"""PLT-01·04 process-device-provider 생산자(P1): 노드 관측·열기 시험·Pod 명세 → #196 계약 기록."""
import json
import os
import subprocess
import sys

import pytest

from guard.collectors import gpu_common_inputs as contract
from guard.collectors import process_device_producer as producers
from guard.collectors import process_device_observer as observer

ENV = {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64}
SCOPE = {'environment_ref': ENV, 'target_sha256': 'b' * 64, 'prepared_sha256': 'c' * 64, 'session': 'd' * 32}
UID = '10000000-0000-0000-0000-000000000001'
CID = 'e' * 64
POD = {'pod_uid': UID, 'container_id': 'containerd://' + CID, 'restart_count': 0}
GPU = 'GPU-12345678-1234-1234-1234-123456789abc'
SECRET_VALUE = 'hf_live_DO_NOT_LEAK_0123456789'


def pod_spec(mount='/var/run/secrets/kubernetes.io/serviceaccount'):
    return {'metadata': {'uid': UID}, 'spec': {
        'serviceAccountName': 'engine',
        'containers': [{'name': 'engine',
                        'env': [{'name': 'HF_TOKEN', 'valueFrom': {'secretKeyRef': {'name': 'hf', 'key': 'token'}}},
                                {'name': 'PLAIN', 'value': SECRET_VALUE}],
                        'envFrom': [{'secretRef': {'name': 'bulk'}}],
                        'volumeMounts': [{'name': 'kube-api', 'mountPath': mount}, {'name': 'models', 'mountPath': '/models'}]}],
        'volumes': [{'name': 'kube-api', 'projected': {'sources': [{'serviceAccountToken': {'path': 'token'}}]}},
                    {'name': 'models', 'hostPath': {'path': '/srv/models'}}]}}


def test_candidates_use_names_and_reference_hashes_only():
    rows = observer.provider_candidates(pod_spec(), 'engine')
    assert [r['kind'] for r in rows] == ['env_secret', 'env_from_secret', 'projected_sa_token']
    assert rows[0]['env_name'] == 'HF_TOKEN' and rows[2]['mount_path'] == '/var/run/secrets/kubernetes.io/serviceaccount'
    assert all(len(r['nonsecret_id_sha256']) == 64 for r in rows)
    assert SECRET_VALUE not in json.dumps(rows) and 'hf' not in json.dumps([r['nonsecret_id_sha256'] for r in rows])


def _proc(root, pid, *, cgroup, nspid, ticks, environ=b'', mountinfo='', fds=()):
    base = root / str(pid)
    (base / 'fd').mkdir(parents=True); (base / 'fdinfo').mkdir()
    fields = ['S'] + ['0'] * 18 + [str(ticks)]
    (base / 'stat').write_text(f'{pid} (python3) ' + ' '.join(fields) + ' 0 0\n')
    (base / 'status').write_text('Name:\tpython3\nUid:\t2000\t2000\t2000\t2000\nNSpid:\t' + '\t'.join(map(str, nspid)) + '\n')
    (base / 'cgroup').write_text(cgroup)
    (base / 'environ').write_bytes(environ)
    (base / 'mountinfo').write_text(mountinfo)
    for number, target, flags in fds:
        os.symlink(target, base / 'fd' / str(number))
        (base / 'fdinfo' / str(number)).write_text(f'pos:\t0\nflags:\t{flags}\n')


def run_observer(spec):
    result = subprocess.run([sys.executable, '-I', '-S', '-', json.dumps(spec)], input=observer.NODE_OBSERVER.encode(),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, check=True)
    return json.loads(result.stdout)


def test_node_observer_selects_the_owned_container_and_keeps_env_names_only(tmp_path):
    root, dev, mount = tmp_path / 'proc', tmp_path / 'dev', tmp_path / 'secrets'
    dev.mkdir(); mount.mkdir(); (mount / 'token').write_text('x')
    cg = f'0::/kubepods.slice/kubepods-pod{UID.replace("-", "_")}.slice/cri-containerd-{CID}.scope\n'
    env = b'HF_TOKEN=' + SECRET_VALUE.encode() + b'\0PATH=/bin\0'
    _proc(root, 4100, cgroup=cg, nspid=[4100, 7], ticks=900, environ=env,
          mountinfo=f'1 0 0:1 / {mount} ro - tmpfs tmpfs ro\n', fds=[(3, str(mount / 'token'), '0100000')])
    _proc(root, 4200, cgroup='0::/kubepods.slice/other.scope\n', nspid=[4200, 1], ticks=901, environ=env)
    candidates = [{'kind': 'env_secret', 'env_name': 'HF_TOKEN', 'mount_path': None, 'nonsecret_id_sha256': 'f' * 64},
                  {'kind': 'env_secret', 'env_name': 'MISSING', 'mount_path': None, 'nonsecret_id_sha256': 'f' * 64},
                  {'kind': 'projected_sa_token', 'env_name': None, 'mount_path': str(mount), 'nonsecret_id_sha256': 'f' * 64}]
    spec = observer.node_spec(pod_uid=UID, container_id='containerd://' + CID, candidates=candidates, proc_root=str(root),
                              dev_root=str(dev), driver_root=str(tmp_path / 'driver'), nvidia_smi=False)
    raw = run_observer(spec)
    assert SECRET_VALUE not in json.dumps(raw) and 'PATH' not in json.dumps(raw)
    observer.decode_node(raw, spec)
    assert [p['pid'] for p in raw['processes']] == [4100] and raw['scanned'] == 2
    process = raw['processes'][0]
    assert process['stable'] and process['nspid'] == [4100, 7] and process['start_ticks'] == 900
    assert process['env_present'] == ['HF_TOKEN'] and process['mounts_present'] == [0] and process['mount_fds'] == [0]
    assert raw['nvml'] == {'available': False, 'rows': []} and 'DRIVER_GPU_INFO_UNAVAILABLE' in raw['codes']


def node(*, nvml_pid=4100, fds=True, env=('HF_TOKEN',), mounts=(0,), mount_fds=()):
    process = {'pid': 4100, 'stable': True, 'nspid': [4100, 7], 'start_ticks': 900, 'uid_effective': 2000,
               'cgroup_sha256': '1' * 64, 'env_present': list(env), 'mounts_present': list(mounts), 'mount_fds': list(mount_fds),
               'device_fds': [{'path': '/dev/nvidia0', 'read_capable': True, 'write_capable': True},
                              {'path': '/dev/nvidiactl', 'read_capable': True, 'write_capable': True}] if fds else [],
               'codes': []}
    api = dict(process, pid=4090, nspid=[4090, 1], start_ticks=880, device_fds=[])
    return {'schema': observer.NODE_SCHEMA, 'scanned': 300, 'processes': [api, process], 'codes': [],
            'devices': [{'path': '/dev/nvidia0', 'minor': 0, 'gpu_uuid': GPU}, {'path': '/dev/nvidiactl', 'minor': 255, 'gpu_uuid': None},
                        {'path': '/dev/nvidia-modeset', 'minor': 254, 'gpu_uuid': None}],
            'nvml': {'available': True, 'rows': [{'pid': nvml_pid, 'gpu_uuid': GPU}]}}


OPENED = {'schema': observer.OPEN_SCHEMA, 'uid_effective': 2000,
          'devices': [{'path': '/dev/nvidia0', 'result': 'allowed', 'errno': None},
                      {'path': '/dev/nvidiactl', 'result': 'allowed', 'errno': None}]}
CANDIDATES = observer.provider_candidates(pod_spec(), 'engine')


@pytest.fixture(autouse=True)
def unregistered_synthetic_producer(monkeypatch):
    # 실제 GPU 확인 뒤 등록된 소스와 별개로 미지원 기록의 거부 경계를 계속 시험한다.
    monkeypatch.delitem(contract.SUPPORTED_PRODUCERS,
                        (producers.DEVICE_PRODUCER_NAME, producers.DEVICE_PRODUCER_VERSION), raising=False)


def record(value, opened=OPENED, **kwargs):
    return producers.process_device_record(value, opened, CANDIDATES, pod=POD, container='engine', **SCOPE, **kwargs)


def test_nvml_host_pid_joins_only_through_the_namespace_chain():
    rec, result = record(node())
    gpu = [e for e in rec['events'] if e['event'] == 'gpu_process']
    assert len(gpu) == 1 and gpu[0]['nvml_host_pid'] == 4100 and gpu[0]['namespace_pid'] == 7
    assert rec['bindings'][gpu[0]['binding']]['worker_pid'] == 7 and rec['bindings'][gpu[0]['binding']]['start_ticks'] == 900
    assert gpu[0]['gpu_uuid_sha256'] != GPU and GPU not in json.dumps(rec)
    assert rec['completeness']['source_ended'] is True
    # envFrom 후보는 이름을 모르므로 진단으로 남는다(Pod 명세 예시에 있다).
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED', 'GPU_INPUT_PRODUCER_DIAGNOSTIC:PROVIDER_ENVFROM_NAMES_UNOBSERVED']
    assert result['complete'] is False
    other, _ = record(node(nvml_pid=9999))
    assert not [e for e in other['events'] if e['event'] == 'gpu_process']
    assert 'NVML_PROCESS_OUTSIDE_CONTAINER' in other['completeness']['diagnostic_codes']


def test_device_rows_separate_held_tested_and_unexposed_nodes():
    rec, _ = record(node())
    rows = {(e['binding'], e['nonsecret_id_sha256']): e['result'] for e in rec['events'] if e['event'] == 'device_access'}
    sha = lambda p: __import__('hashlib').sha256(p.encode()).hexdigest()
    worker = 1
    assert rows[(worker, sha('/dev/nvidia0'))] == 'allowed' and rows[(worker, sha('/dev/nvidia-modeset'))] == 'unknown'
    assert rows[(0, sha('/dev/nvidia0'))] == 'allowed'  # API 프로세스도 같은 컨테이너 cgroup 열기 시험 결과
    denied = dict(OPENED, devices=[{'path': '/dev/nvidia0', 'result': 'denied', 'errno': 'EPERM'}])
    rec2, _ = record(node(fds=False), opened=denied)
    assert {e['result'] for e in rec2['events'] if e['event'] == 'device_access' and e['nonsecret_id_sha256'] == sha('/dev/nvidia0')} == {'denied'}
    rec3, result3 = record(node(), opened=None)
    assert rec3['completeness']['source_ended'] is False and 'DEVICE_OPEN_TEST_UNAVAILABLE' in rec3['completeness']['diagnostic_codes']
    assert 'GPU_INPUT_SOURCE_INCOMPLETE' in result3['gaps']


def test_provider_rows_report_env_presence_mount_use_and_unknown_envfrom():
    rec, _ = record(node(env=(), mounts=(0,), mount_fds=()))
    rows = [e for e in rec['events'] if e['event'] == 'provider_access' and e['binding'] == 1]
    assert [(r['provider_kind'], r['result']) for r in rows] == [('env_secret', 'denied'), ('env_from_secret', 'unknown'),
                                                                ('projected_sa_token', 'unknown')]
    assert rows[0]['env_name'] == 'HF_TOKEN' and rows[0]['credential_generation'] is None and len(rows[0]['receiver_sha256']) == 64
    assert 'PROVIDER_ENVFROM_NAMES_UNOBSERVED' in rec['completeness']['diagnostic_codes']
    used, _ = record(node(mount_fds=(0,)))
    assert [r['result'] for r in used['events'] if r['event'] == 'provider_access' and r['binding'] == 1][::2] == ['allowed', 'allowed']
    assert SECRET_VALUE not in json.dumps(used)


def test_unstable_identity_binding_change_and_missing_nvml_keep_the_record_open():
    value = node(); value['processes'][0] = dict(value['processes'][0], stable=False, codes=['PID_IDENTITY_CHANGED'])
    rec, _ = record(value)
    assert len(rec['bindings']) == 1 and rec['completeness']['source_ended'] is False
    assert 'PROCESS_IDENTITY_CHANGED' in rec['completeness']['diagnostic_codes']
    rec2, _ = record(node(), scope_stable=False)
    assert 'PROCESS_DEVICE_BINDING_CHANGED' in rec2['completeness']['diagnostic_codes'] and not rec2['completeness']['source_ended']
    value = node(); value['nvml'] = {'available': False, 'rows': []}
    rec3, result3 = record(value)
    assert 'GPU_INPUT_REQUIRED_EVENT_MISSING:gpu_process' in result3['gaps'] and 'NVML_UNAVAILABLE' in rec3['completeness']['diagnostic_codes']


def test_decoder_rejects_names_outside_the_candidates_and_foreign_device_paths():
    bad = node(); bad['processes'][1]['env_present'] = ['PATH']
    with pytest.raises(observer.ObserverOutputError):
        record(bad)
    bad = node(); bad['devices'].append({'path': '/dev/sda', 'minor': 0, 'gpu_uuid': None})
    with pytest.raises(observer.ObserverOutputError):
        record(bad)
    with pytest.raises(observer.ObserverOutputError):
        record(node(), opened=dict(OPENED, devices=[{'path': '/dev/nvidia0', 'result': 'allowed', 'errno': 'EPERM'}]))


def test_no_owned_process_is_a_contract_error_not_an_empty_record():
    value = node(); value['processes'] = []
    with pytest.raises(contract.GpuInputError):
        record(value)


@pytest.mark.parametrize('code', ['ENVIRON_UNAVAILABLE', 'ENVIRON_TRUNCATED', 'MOUNTINFO_UNAVAILABLE', 'MOUNTINFO_TRUNCATED'])
def test_unread_provider_state_stays_unknown_instead_of_denied(code):
    value = node(env=(), mounts=())
    value['processes'][1]['codes'] = [code]
    rec, result = record(value)
    kind = 'env_secret' if code.startswith('ENVIRON') else 'projected_sa_token'
    event = next(e for e in rec['events'] if e['event'] == 'provider_access' and e['binding'] == 1 and e['provider_kind'] == kind)
    assert event['result'] == 'unknown'
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:' + code in result['gaps']


def test_open_test_does_not_infer_permissions_for_another_uid():
    value = node()
    value['processes'][0]['uid_effective'] = 0
    rec, result = record(value)
    assert {e['result'] for e in rec['events'] if e['event'] == 'device_access' and e['binding'] == 0} == {'unknown'}
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:DEVICE_OPEN_TEST_UID_MISMATCH' in result['gaps']
    assert any(e['result'] == 'allowed' for e in rec['events'] if e['event'] == 'device_access' and e['binding'] == 1)


def test_provider_candidate_limit_is_explicit_instead_of_silent_omission():
    pod = pod_spec()
    pod['spec']['containers'][0]['envFrom'] *= observer.MAX_CANDIDATES
    with pytest.raises(observer.ObserverOutputError, match='PROVIDER_CANDIDATES_BOUND'):
        observer.provider_candidates(pod, 'engine')
