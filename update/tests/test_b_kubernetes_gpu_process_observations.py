"""B-only passive GPU/FD facts never promote the A v1 worker/access claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from guard.models import sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.runtime.kubernetes_process import PROFILE as PROCESS_PROFILE
from guard.executors import kubernetes_gpu_process_observations as helper
from guard.executors import container_config, credential_exposure

UUID = 'GPU-' + 'a' * 32
TARGET_SHA = 'b' * 64


def v1_record():
    return {'profile': PROCESS_PROFILE, 'target_sha256': TARGET_SHA, 'program_sha256': 'a' * 64,
        'available': True,
        'binding_verified_before_after': True,
        'packet': {'worker_subject_verified': False, 'worker_inventory_complete': False,
            'capture_process_excluded': True,
            'capture_subject': {'pid_namespace_inode': 51, 'mount_namespace_inode': 52,
                                'identity_stable': True},
            'processes': [{'pid': 123, 'start_ticks': 456, 'pid_identity_stable': True,
                           'uid_effective': 1000, 'mount_namespace_inode': 52,
                           'cap_eff': '0', 'environment_names': ['EXAMPLE_NAME']}],
            'candidate_metadata': [{'path': '/EXAMPLE/key', 'state': 'metadata',
                'metadata_identity_stable': True,
                'metadata': {'device': 11, 'inode': 22}}]}}


def remote_row(*, stable=True, device=True, credential=True, diagnostics=None):
    return {'pid': 123, 'start_ticks': 456, 'pid_identity_stable': stable,
        'uid_effective': 1000, 'pid_namespace_inode': 51, 'mount_namespace_inode': 52,
        'nspid': [123], 'cgroup_sha256': 'c' * 64,
        'device_fds': ([{'path': '/dev/nvidia0', 'major': 195, 'minor': 0,
                        'read_capable': True, 'write_capable': True,
                        'metadata_only': False}] if device else []),
        'credential_fds': ([{'path': '/EXAMPLE/key', 'read_capable': True,
                            'metadata_only': False}] if credential else []),
        'diagnostic_codes': diagnostics or []}


def remote_packet(*, row=None, nvml_pid=123, nvml=True):
    return {'schema': helper.SCHEMA, 'rows': [row or remote_row()],
            'nvml': {'available': nvml, 'rows': ([{'pid': nvml_pid, 'gpu_uuid': UUID}] if nvml else []),
                     'diagnostic_code': None if nvml else 'NVML_UNAVAILABLE'}}


def transport_for(packet, *, replace=False, later_error=False):
    transport = object.__new__(KubernetesHTTP)
    transport.profile = REQUEST_BINDING_PROFILE
    transport.injected = False
    transport.target_sha256 = TARGET_SHA
    transport.clock = time.monotonic
    transport.deadline = time.monotonic() + 30
    transport.binding = {'engine': {'namespace': 'EXAMPLE-ns', 'pod': 'EXAMPLE-pod',
                                     'container': 'engine'}}
    transport._checks = 0
    def verify(**kwargs):
        transport._checks += 1
        if replace and transport._checks == 2:
            raise ValueError('EXAMPLE-replaced')
    def call(*args, **kwargs):
        if later_error:
            raise ValueError('EXAMPLE-remote-error')
        return packet
    transport._verify = verify
    transport._call = call
    return transport


def test_real_remote_source_reports_stable_pid_and_unsupported_nvml_without_secrets():
    assert sha256_hex(helper.SOURCE.encode()) == helper.SOURCE_SHA256
    compile(helper.SOURCE, 'B-passive-remote', 'exec')
    with TemporaryDirectory() as folder:
        root = Path(folder)
        pid = 12345
        proc = root / str(pid)
        (proc / 'ns').mkdir(parents=True)
        (proc / 'fd').mkdir()
        (proc / 'fdinfo').mkdir()
        (proc / 'ns/pid').write_text('EXAMPLE')
        (proc / 'ns/mnt').write_text('EXAMPLE')
        (proc / 'stat').write_text(f'{pid} (python) ' + '0 ' * 19 + '42 0\n')
        (proc / 'status').write_text(f'Uid:\t1000\t1000\t1000\t1000\nNSpid:\t{pid}\n')
        (proc / 'cgroup').write_text('0::/EXAMPLE\n')
        expected = {'pid': pid, 'start_ticks': 42, 'uid_effective': 1000,
                    'pid_namespace_inode': (proc / 'ns/pid').stat().st_ino,
                    'mount_namespace_inode': (proc / 'ns/mnt').stat().st_ino}
        spec = {'proc_root': str(root), 'pids': [expected], 'candidates': []}
        env = {**os.environ, 'PATH': str(root / 'no-binaries')}
        result = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', helper.SOURCE,
                                 json.dumps(spec)], capture_output=True, timeout=15, env=env)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
        packet = json.loads(result.stdout)
        row = packet['rows'][0]
        assert row['pid_identity_stable'] is True and row['uid_effective'] == 1000
        assert row['device_fds'] == row['credential_fds'] == []
        assert packet['nvml'] == {'available': False, 'rows': [], 'diagnostic_code': 'NVML_UNAVAILABLE'}
        (proc / 'fd/7').write_text('EXAMPLE gone FD')
        spec['pids'][0]['start_ticks'] = 43
        changed = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', helper.SOURCE,
                                  json.dumps(spec)], capture_output=True, timeout=15, env=env)
        assert changed.returncode == 0
        changed_row = json.loads(changed.stdout)['rows'][0]
        assert changed_row['pid_identity_stable'] is False
        assert changed_row['device_fds'] == changed_row['credential_fds'] == []
        spec['pids'][0]['start_ticks'] = 42
        gone = subprocess.run([sys.executable, '-I', '-S', '-B', '-c', helper.SOURCE,
                               json.dumps(spec)], capture_output=True, timeout=15, env=env)
        assert gone.returncode == 0
        gone_row = json.loads(gone.stdout)['rows'][0]
        assert gone_row['pid_identity_stable'] is True
        assert 'FD_GONE_OR_UNREADABLE' in gone_row['diagnostic_codes']
        assert gone_row['credential_fds'] == []


def test_passive_consumer_keeps_only_process_facts_and_unique_nvml_join():
    record = v1_record()
    joined_row = remote_row()
    joined_row['nspid'] = [999, 123]
    transport = transport_for(remote_packet(row=joined_row, nvml_pid=999))
    observed = helper.collect(record, transport)
    assert transport._checks == 2 and observed['binding_stable'] is True
    assert observed['rows'][0]['device_fds'][0]['major'] == 195
    assert observed['rows'][0]['credential_fds'] == [{'path': '/EXAMPLE/key', 'read_capable': True,
                                                      'metadata_only': False}]
    assert observed['nvml_joins'] == [{'nvml_pid': 999, 'gpu_uuid': UUID,
                                        'process_pid': 123, 'joined': True,
                                        'namespace_chain_verified': True}]
    assert observed['worker_subject_verified'] is False and observed['worker_inventory_complete'] is False
    mismatch = helper.collect(record, transport_for(remote_packet(nvml_pid=123)))
    assert mismatch['nvml_joins'][0]['joined'] is False
    assert 'B_GPU_NVML_PID_UNJOINED' in mismatch['diagnostic_codes']


def test_metadata_only_fd_is_not_readable_or_writable_and_contradiction_is_rejected():
    row = remote_row()
    row['device_fds'][0].update(read_capable=False, write_capable=False, metadata_only=True)
    row['credential_fds'][0].update(read_capable=False, metadata_only=True)
    observed = helper.collect(v1_record(), transport_for(remote_packet(row=row, nvml=False)))
    assert observed['binding_stable'] is True
    assert observed['rows'][0]['device_fds'][0]['metadata_only'] is True
    assert observed['rows'][0]['device_fds'][0]['read_capable'] is False
    assert observed['rows'][0]['credential_fds'][0] == {
        'path': '/EXAMPLE/key', 'read_capable': False, 'metadata_only': True}
    row['credential_fds'][0]['read_capable'] = True
    rejected = helper.collect(v1_record(), transport_for(remote_packet(row=row, nvml=False)))
    assert rejected['binding_stable'] is False and rejected['rows'] == []
    row['credential_fds'][0]['read_capable'] = False
    row['device_fds'][0]['write_capable'] = True
    rejected_device = helper.collect(v1_record(), transport_for(remote_packet(row=row, nvml=False)))
    assert rejected_device['binding_stable'] is False and rejected_device['rows'] == []


@pytest.mark.parametrize('path,accepted', [
    ('/dev/nvidia-uvm', True),
    ('/dev/nvidia-uvm-tools', True),
    ('/dev/nvidiauvm', False),
    ('/dev/nvidiauvm-tools', False),
])
def test_device_fd_decoder_uses_exact_kernel_device_names(path, accepted):
    row = remote_row()
    row['device_fds'][0]['path'] = path
    observed = helper.collect(v1_record(), transport_for(remote_packet(row=row, nvml=False)))
    assert observed['binding_stable'] is accepted
    assert bool(observed['rows']) is accepted
    assert observed['worker_subject_verified'] is False
    assert observed['device_access_tested'] is False


@pytest.mark.parametrize('damage', ['pid-reuse', 'pod-replace', 'fd-gone', 'remote-error', 'injected', 'budget-low'])
def test_failure_paths_do_not_forge_worker_or_access(damage):
    record = v1_record()
    row = remote_row(stable=False, device=False, credential=False,
                     diagnostics=['PID_READ_UNAVAILABLE']) if damage == 'pid-reuse' else remote_row(
                     device=False, credential=False, diagnostics=['FD_GONE_OR_UNREADABLE']) if damage == 'fd-gone' else remote_row()
    transport = transport_for(remote_packet(row=row), replace=damage == 'pod-replace',
                              later_error=damage == 'remote-error')
    if damage == 'injected':
        transport.injected = True
    if damage == 'budget-low':
        transport.deadline = time.monotonic() + 5
    result = helper.collect(record, transport)
    assert result['worker_subject_verified'] is False and result['worker_inventory_complete'] is False
    if damage in {'pod-replace', 'remote-error', 'injected', 'budget-low'}:
        assert not result['binding_stable'] and result['rows'] == []
    else:
        assert result['binding_stable'] and 'B_GPU_PROCESS_PARTIAL' in result['diagnostic_codes']
        assert result['rows'][0]['credential_fds'] == []


def test_later_auxiliary_error_keeps_existing_independent_root_and_capability(monkeypatch):
    record = v1_record()
    record['packet']['processes'][0]['uid_effective'] = 0
    record['packet']['processes'][0]['cap_eff'] = '200000'
    record['packet']['processes'][0]['environment_names'] = ['FORBIDDEN_EXAMPLE']
    monkeypatch.setattr(container_config, 'read_kubernetes_process_observations', lambda ctx, *a: record)
    monkeypatch.setattr(container_config, 'kubernetes_process_control', lambda ctx: ({}, {'attempted': False}))
    monkeypatch.setattr(credential_exposure, 'read_kubernetes_process_observations', lambda ctx, *a: record)
    monkeypatch.setattr(credential_exposure, 'kubernetes_process_control', lambda ctx: ({}, {'attempted': False}))
    ctx = SimpleNamespace(check=SimpleNamespace(id='PLT-01', challenge_steps=[], functional_probes=[]),
                          cprobe=transport_for(remote_packet(), replace=True),
                          policy_values={'credential_candidate_paths': ['/EXAMPLE/key']})
    ctx.cprobe.report = lambda: {}
    observations, _, _, summary = container_config.collect_kubernetes_process_permissions(ctx)
    assert observations['root_uid_present'] == {'value': True, 'collected': True}
    assert observations['capabilities_effective']['collected'] is True
    assert summary['passive_gpu_process_observation']['binding_stable'] is False
    ctx.check = SimpleNamespace(id='PLT-04', challenge_steps=[], functional_probes=[])
    observations, _, _, summary = credential_exposure.collect_kubernetes_credential_exposure(ctx)
    assert observations['worker_env_names'] == {'value': None, 'collected': False}
    assert observations['credential_candidate_results']['value'] == {'/EXAMPLE/key': 'unknown'}
    assert summary['passive_gpu_process_observation']['worker_subject_verified'] is False
