"""PLT-02 ipc-ownership 공통 입력(개발 담당, B 의존 반환 6).

관측 자료는 k3d 실제 vLLM CPU 엔진에서 관측기를 돌린 출력이다(2026-11-08, EXAMPLE·arm64·검토 계약 밖 이미지).
API 서버·EngineCore·Worker의 listener와 상대 socket이 모두 컨테이너 안에서 풀린 모양이다.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import gpu_common_inputs as contract
from guard.collectors import ipc_ownership_observer as observer
from guard.collectors import ipc_ownership_producer as producer
from guard.runtime.kubernetes_http import (ENGINE_CALLBACK_PROFILE, PROFILE, REQUEST_BINDING_PROFILE, KubernetesBindingError,
                                           KubernetesHTTP)

FIXTURE = Path(__file__).parent / 'fixtures/ipc_ownership_k3d_20261108.json'
POD = {'pod_uid': '11111111-2222-3333-4444-555555555555', 'container_id': 'containerd://' + 'b' * 64, 'restart_count': 0}
SCOPE = {'environment_ref': {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64},
         'target_sha256': 'c' * 64, 'prepared_sha256': 'd' * 64, 'session': 'e' * 32}


def observed():
    return json.loads(FIXTURE.read_text())


def build(value=None, **kw):
    return producer.record(value or observed(), pod=POD, **SCOPE, **kw)


def test_real_engine_roster_and_directions_are_recovered():
    record, result = build()
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED']  # 등록은 GPU 확인 뒤
    assert record['completeness']['source_ended'] is True and result['violation_candidates'] == []
    summary = producer.summarize(record)
    assert summary['listeners'] == {'api_server': 2, 'engine_core': 2, 'worker': 2}
    assert summary['connections'] == {'engine_core->api_server': 2, 'engine_core->worker': 2, 'worker->engine_core': 2}
    assert summary['unresolved_peers'] == 0 and 'utility' in summary['roles']
    # 바인딩은 실제 프로세스의 PID·시작 ticks다. 유틸리티를 worker로 부르지 않는다.
    roles = {r['binding']: r['role'] for r in record['events'] if r['event'] == 'process'}
    assert sorted(roles.values()) == ['api_server', 'engine_core', 'utility', 'worker']
    assert all(b['worker_pid'] >= 1 and b['start_ticks'] > 0 for b in record['bindings'])


def test_registered_producer_makes_the_record_complete(monkeypatch):
    source = producer.producer()
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (source['name'], source['version']), source['source_sha256'])
    _, result = build()
    assert result['supported'] is True and result['complete'] is True and result['producer_authenticated'] is False


def test_socket_inode_and_path_file_identity_stay_separate():
    record, _ = build()
    rows = [r for r in record['events'] if r['event'] == 'unix_socket' and r['path_sha256']]
    assert rows and all(r['path_file_sha256'] and len(r['path_file_sha256']) == 64 for r in rows)
    # 같은 경로의 listener와 accept한 socket은 커널 inode가 다르고 경로 파일 신원은 같다.
    by_path = {}
    for r in rows:
        by_path.setdefault(r['path_sha256'], []).append(r)
    pair = next(v for v in by_path.values() if {r['state'] for r in v} == {'listen', 'accepted'})
    assert len({r['socket_inode'] for r in pair}) == 2 and len({r['path_file_sha256'] for r in pair}) == 1


@pytest.mark.parametrize('change,code', [
    (lambda v: v.update(sock_diag_complete=False), 'SOCK_DIAG_INCOMPLETE'),
    (lambda v: v['processes'][0].update(stable=False), 'PROCESS_IDENTITY_CHANGED'),
    (lambda v: v['codes'].append('PROCESS_SET_BOUND'), 'PROCESS_SET_BOUND'),
])
def test_incomplete_observation_never_ends_the_source(change, code):
    value = observed(); change(value)
    record, result = build(value)
    assert record['completeness']['source_ended'] is False and code in record['completeness']['diagnostic_codes']
    assert result['complete'] is False


def test_binding_change_is_recorded():
    record, _ = build(scope_stable=False)
    assert 'IPC_OWNERSHIP_BINDING_CHANGED' in record['completeness']['diagnostic_codes']
    assert record['completeness']['source_ended'] is False


def test_peer_outside_the_roster_and_utility_listener_are_candidates_only():
    value = observed()
    worker = next(p for p in value['processes'] if p['role'] == 'worker')
    accepted = next(s for s in worker['sockets'] if value['unix'].get(str(s['inode']), {}).get('state') == 1
                    and value['unix'][str(s['inode'])]['path_sha256'])
    value['unix'][str(accepted['inode'])]['peer'] = 999999999  # 컨테이너 밖 상대
    utility = next(p for p in value['processes'] if p['role'] == 'utility')
    utility['sockets'].append({'fd': 900, 'inode': 888888888})
    value['unix']['888888888'] = {'type': 1, 'state': 10, 'path_sha256': 'f' * 64, 'abstract': False, 'vfs': [1, 2], 'peer': None}
    record, result = build(value)
    reasons = sorted(c['reason'] for c in result['violation_candidates'])
    assert reasons == ['listener_owned_outside_serving_roles', 'peer_outside_container_roster']
    assert 'UNIX_PEER_UNRESOLVED' in record['completeness']['diagnostic_codes']
    assert producer.summarize(record)['unresolved_peers'] == 1


def test_no_stable_process_makes_no_record():
    value = observed()
    for p in value['processes']:
        p['stable'] = False
    with pytest.raises(ValueError, match='IPC_OWNERSHIP_NO_STABLE_PROCESS'):
        build(value)


@pytest.mark.parametrize('damage', [
    lambda v: v.update(schema='other'),
    lambda v: v['processes'][0].update(role='root'),
    lambda v: v['processes'][0].update(title_sha256='x'),
    lambda v: v['unix'].update({'abc': next(iter(v['unix'].values()))}),
    lambda v: next(iter(v['unix'].values())).update(vfs=[1]),
    lambda v: v['processes'].append(deepcopy(v['processes'][0])),
])
def test_decode_rejects_malformed_observations(damage):
    value = observed(); damage(value)
    with pytest.raises(ValueError, match='IPC_OWNERSHIP_OBSERVATION_INVALID'):
        observer.decode(value)


def test_non_unix_sockets_are_out_of_scope():
    value = observed()
    api = next(p for p in value['processes'] if p['role'] == 'api_server')
    api['sockets'].append({'fd': 901, 'inode': 777777777})  # sock_diag AF_UNIX에 없는 inode(TCP 등)
    record, _ = build(value)
    assert 777777777 not in {r['socket_inode'] for r in record['events']}


def test_transport_runs_the_observer_only_on_binding_profiles():
    calls = []
    stub = SimpleNamespace(profile=REQUEST_BINDING_PROFILE, _verify=lambda: None,
                           binding={'engine': {'namespace': 'n', 'pod': 'p', 'container': 'engine', 'pod_uid': POD['pod_uid'],
                                               'container_id': POD['container_id'], 'restart_count': 0}},
                           _call=lambda argv, **k: calls.append(argv) or observed())
    value, pod, stable = KubernetesHTTP.engine_ipc_ownership(stub)
    assert stable is True and pod == POD and value['schema'] == observer.SCHEMA
    assert calls[0][-2:] == ['-c', observer.OBSERVER] and 'exec' in calls[0]
    for profile in (ENGINE_CALLBACK_PROFILE,):
        stub.profile = profile; KubernetesHTTP.engine_ipc_ownership(stub)
    stub.profile = PROFILE
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP.engine_ipc_ownership(stub)
    stub.profile = REQUEST_BINDING_PROFILE; stub._call = lambda argv, **k: {'schema': 'bad'}
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP.engine_ipc_ownership(stub)
