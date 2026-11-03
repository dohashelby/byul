"""PR206 B process contracts: positive facts survive partial/optional failures."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.executors import container_config, credential_exposure, ipc_trust_scope
from guard.executors import kubernetes_gpu_process_observations as helper
from guard.models import Verdict
from tests.builders import T0, baseline_pack, evidence, policy
from guard.core.judge import judge_evidence
from tests.test_b_kubernetes_gpu_process_observations import v1_record
from tests.test_b_plt06_worker_scope import row
from tests.test_credential_environment_collection import NameDocker, VALUES
from tests.test_credential_mount_semantics import context, IMAGE
from tests.builders import environment


class NoTransportAccess:
    def __getattribute__(self, name):
        raise AssertionError('No public GPU metadata execution contract exists yet')


def test_public_contract_pending_does_not_touch_transport_or_silently_drop_symlink():
    record = v1_record()
    record['packet']['candidate_metadata'][0].update(state='symlink_not_followed', metadata=None,
        metadata_identity_stable=False)
    result = helper.collect(record, NoTransportAccess())
    assert result['binding_stable'] is False and result['rows'] == []
    assert result['diagnostic_codes'] == ['B_GPU_PROCESS_PUBLIC_CONTRACT_UNAVAILABLE',
        'CREDENTIAL_CANDIDATE_SYMLINK_UNRESOLVED']
    assert result['unresolved_credential_candidates'] == [{'path': '/EXAMPLE/key',
        'state': 'symlink_not_followed', 'diagnostic_code': 'CREDENTIAL_CANDIDATE_SYMLINK_UNRESOLVED'}]
    assert result['selected_process_count'] == 1 and result['metadata_candidate_count'] == 0
    assert all(result[key] is False for key in ('worker_subject_verified', 'worker_inventory_complete',
        'device_access_tested', 'candidate_read_tested', 'provider_scope_verified'))


@pytest.mark.parametrize('check_id', ['PLT-01', 'PLT-04'])
def test_mandatory_process_and_normal_control_precede_optional_and_report_error_preserves_facts(monkeypatch, check_id):
    events = []; clock = SimpleNamespace(now=0)
    record = v1_record()
    record['packet']['processes'][0].update(uid_effective=0, cap_eff='200000')
    def read(*args):
        events.append('process'); return record
    def control(ctx):
        events.append('normal'); assert clock.now == 0
        return {'functional_probe_results': {'value': {'EXAMPLE': 'ok'}, 'collected': True}}, {'attempted': True, 'succeeded': True}
    def optional(*args):
        events.append('optional'); clock.now = 1000
        return helper._empty('B_GPU_PROCESS_PUBLIC_CONTRACT_UNAVAILABLE')
    def broken_report():
        raise ValueError('PRIVATE_REPORT_VALUE_NOT_EXPORTED')
    module = container_config if check_id == 'PLT-01' else credential_exposure
    monkeypatch.setattr(module, 'read_kubernetes_process_observations', read)
    monkeypatch.setattr(module, 'kubernetes_process_control', control)
    monkeypatch.setattr(helper, 'collect', optional)
    ctx = SimpleNamespace(check=SimpleNamespace(id=check_id, challenge_steps=[], functional_probes=[]),
        policy_values={'credential_candidate_paths': []}, cprobe=SimpleNamespace(report=broken_report))
    collect = module.collect_kubernetes_process_permissions if check_id == 'PLT-01' else module.collect_kubernetes_credential_exposure
    observations, normal, _, summary = collect(ctx)
    assert events == ['process', 'normal', 'optional']
    assert normal['succeeded'] is True and observations['functional_probe_results']['value'] == {'EXAMPLE': 'ok'}
    assert summary['kubernetes'] == {'diagnostic_codes': ['KUBERNETES_TRANSPORT_REPORT_UNAVAILABLE']}
    assert 'PRIVATE_REPORT_VALUE_NOT_EXPORTED' not in json.dumps(summary)
    if check_id == 'PLT-01':
        assert observations['root_uid_present'] == {'value': True, 'collected': True}
        assert observations['capabilities_effective']['value'] == ['SYS_ADMIN']
    else:
        assert summary['container_environment_names_observed'] == ['EXAMPLE_NAME']
        assert observations['worker_env_names'] == {'value': None, 'collected': False}
        assert summary['provider_scope_verified'] is False


@pytest.mark.parametrize('later_error', [False, True])
def test_gpu_forbidden_subject_name_keeps_existing_fail_even_after_normal_error(later_error):
    ctx = context(VALUES)
    ctx.env = environment(engine={'name': 'engine-x', 'version': '1.0.0', 'image_digest': IMAGE},
        model={'id': 'test'}, runtime={'gpu_count': 1})
    ctx.target['environment_ref'] = ctx.env.ref().model_dump(mode='json')
    if later_error:
        def broken(*args):raise OSError('PRIVATE_NORMAL_ERROR_NOT_EXPORTED')
        ctx.cprobe = broken
    obs, normal, _, summary = credential_exposure.CredentialExposureExecutor({}, docker=NameDocker(
        'PID 19\nFORBIDDEN_CANARY\nGUARD_READ_OK\nPID -1\nUNATTRIBUTED\nGUARD_READ_OK\n')).collect(ctx)
    assert obs['worker_env_names'] == {'value': ['FORBIDDEN_CANARY'], 'collected': True}
    assert obs['process_scan_complete']['value'] is False
    assert summary['observed_environment_subjects'] == [{'pid': 19, 'environment_names': ['FORBIDDEN_CANARY'], 'read_complete': True}]
    assert summary['worker_subject_verified'] is summary['worker_inventory_complete'] is summary['provider_scope_verified'] is False
    pack = baseline_pack()
    result = judge_evidence(pack.checks['PLT-04'], evidence(check_id='PLT-04', observations=obs, normal=normal),
        policy(pack.ref(), values={'PLT-04': VALUES}), T0)
    assert result.verdicts.security == Verdict.FAIL
    assert 'PRIVATE_NORMAL_ERROR_NOT_EXPORTED' not in json.dumps(summary)


def test_duplicate_pid_ambiguity_is_explicit_without_self_pair_or_erasing_clear_violation():
    values = {'worker_isolation_requirement': 'independent-uid', 'worker_trust_group_markers': ['Core', 'Worker']}
    projected = ipc_trust_scope.project({'processes': [row(10, 'Core'), row(10, 'Worker'),
        row(11, 'Core'), row(12, 'Worker')]}, values)
    assert projected['ambiguous_processes'] == [{'pid': 10, 'matching_markers': ['Core', 'Worker'],
        'reported_uids': ['1000'], 'resources_sha256': [helper.sha256_hex(b'/memfd:EXAMPLE')],
        'reasons': ['duplicate_pid_records']}]
    assert [item['pids'] for item in projected['mapping']] == [[11], [12]]
    assert projected['violations'] == [{'groups': ['Core', 'Worker'], 'shared_uid': ['1000']}]
    only = ipc_trust_scope.project({'processes': [row(10, 'Core'), row(10, 'Worker')]}, values)
    assert only['mapping'] == [] and only['violations'] == [] and only['ambiguous_processes']


def test_saved_complete_list_definition_cannot_turn_partial_gpu_declarations_into_pass():
    from tests.test_container_target_binding import context as permission_context, World
    ctx = permission_context()
    ctx.env.runtime.gpu_count = 1; ctx.env = ctx.env.seal()
    ctx.target['environment_ref'] = ctx.env.ref().model_dump(mode='json')
    ctx.check = SimpleNamespace(collection_profile=None, required_observations=[],
        functional_probes=ctx.check.functional_probes, challenge_steps=ctx.check.challenge_steps)
    class DeclaredGpu(World):
        def inspect_json(self, container, *, timeout):
            value = super().inspect_json(container, timeout=timeout)
            value['HostConfig']['Devices'] = [{'PathOnHost': '/dev/nvidia0',
                'PathInContainer': '/dev/nvidia0', 'CgroupPermissions': 'rwm'}]
            return value
    obs, _, _, summary = container_config.ContainerConfigExecutor({}, docker=DeclaredGpu()).collect(ctx)
    assert obs['devices']['collected'] is False
    assert summary['devices'] == ['/dev/nvidia0:/dev/nvidia0:rwm']
    assert summary['gpu_effective_device_access_unverified'] is True
    assert 'permission_scan_complete' not in obs


@pytest.mark.parametrize('replacement', [False, True])
@pytest.mark.parametrize('metadata_only', [False, True])
def test_actual_source_packet_and_decoder_agree_on_caps_device_and_scope_changed(tmp_path, monkeypatch, capsys, replacement, metadata_only):
    # Synthetic proc/FD metadata only, not MIG or GPU hardware measurement.
    pid = os.getpid() + 10000; root = tmp_path / str(pid)
    (root / 'ns').mkdir(parents=True); (root / 'fd').mkdir(); (root / 'fdinfo').mkdir()
    for name in ('pid', 'mnt'):(root / 'ns' / name).write_text('EXAMPLE')
    (root / 'stat').write_text(f'{pid} (python) ' + '0 ' * 19 + '42 0\n')
    (root / 'status').write_text(f'Uid:\t1000\t1000\t1000\t1000\nNSpid:\t{pid}\n')
    (root / 'cgroup').write_text('0::/EXAMPLE\n')
    fd = root / 'fd/7'; fd.write_bytes(b'PRIVATE_FD_CONTENT_MUST_NOT_BE_READ')
    flags = 2 | (0x200000 if metadata_only else 0)
    (root / 'fdinfo/7').write_text('flags:\t' + format(flags, 'o') + '\n')
    (root / 'fd/8').write_bytes(b'EXAMPLE_GONE_METADATA')
    expected = {'pid': pid, 'start_ticks': 43 if replacement else 42, 'uid_effective': 1000,
        'pid_namespace_inode': (root / 'ns/pid').stat().st_ino,
        'mount_namespace_inode': (root / 'ns/mnt').stat().st_ino}
    original_stat = os.stat
    def metadata(path, *args, **kwargs):
        if Path(path) == fd:return SimpleNamespace(st_mode=stat.S_IFCHR | 0o600, st_dev=11, st_ino=22, st_rdev=0)
        return original_stat(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'stat', metadata)
        patch.setattr(os, 'readlink', lambda path: '/dev/nvidia-caps/nvidia-cap7')
        patch.setattr(os, 'major', lambda value: 195, raising=False)
        patch.setattr(os, 'minor', lambda value: 7, raising=False)
        patch.setattr(os, 'O_ACCMODE', 3, raising=False)
        patch.setattr(os, 'O_PATH', 0x200000, raising=False)
        patch.setattr(signal, 'alarm', lambda *args: None, raising=False)
        patch.setattr(subprocess, 'run', lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b''))
        later_missing = {**expected, 'pid': pid + 1}
        patch.setattr(sys, 'argv', ['EXAMPLE-source', json.dumps({'proc_root': str(tmp_path), 'pids': [expected, later_missing], 'candidates': []})])
        exec(compile(helper.SOURCE, '<fixed-source-EXAMPLE-metadata-only>', 'exec'), {'__name__': '__main__'})
    output = capsys.readouterr().out
    assert 'PRIVATE_FD_CONTENT_MUST_NOT_BE_READ' not in output
    rows, nvml = helper._decode(json.loads(output), [expected, later_missing], [])
    assert nvml['available'] is False
    assert rows[1]['diagnostic_codes'] == ['PID_READ_UNAVAILABLE']
    assert rows[1]['device_fds'] == [] and rows[1]['pid_identity_stable'] is False
    if replacement:
        assert rows[0]['diagnostic_codes'] == ['PID_REUSED_OR_SCOPE_CHANGED']
        assert rows[0]['device_fds'] == [] and rows[0]['pid_identity_stable'] is False
    else:
        assert rows[0]['device_fds'] == [{'path': '/dev/nvidia-caps/nvidia-cap7', 'major': 195, 'minor': 7,
            'read_capable': not metadata_only, 'write_capable': not metadata_only, 'metadata_only': metadata_only}]
        assert rows[0]['pid_identity_stable'] is True
        assert rows[0]['diagnostic_codes'] == ['FD_GONE_OR_UNREADABLE']
