"""LIF-11 failed-group-resources 공통 입력(개발 담당, B 의존 반환 7).

실패 전 관측은 k3d 실제 vLLM CPU 엔진 출력이다(2026-11-08, EXAMPLE). 실패·재기동 뒤 관측은 그 출력을 바탕으로 만든
합성 다음 세대다(새 PID·ticks·socket, 공유 메모리 하나는 Pod 볼륨에 남고 하나는 사라짐). 실제 실패 실측이 아니다.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import gpu_common_inputs as contract
from guard.collectors import group_resource_observer as observer
from guard.collectors import group_resource_producer as producer
from guard.runtime.kubernetes_http import (ENGINE_CALLBACK_PROFILE, PROFILE, REQUEST_BINDING_PROFILE, KubernetesBindingError,
                                           KubernetesHTTP)

FIXTURE = Path(__file__).parent / 'fixtures/group_resources_k3d_20261108.json'
POD = {'pod_uid': '11111111-2222-3333-4444-555555555555', 'container_id': 'containerd://' + 'b' * 64, 'restart_count': 0}
NEXT_POD = {**POD, 'container_id': 'containerd://' + 'c' * 64, 'restart_count': 1}
SCOPE = {'environment_ref': {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64},
         'target_sha256': 'c' * 64, 'prepared_sha256': 'd' * 64, 'session': 'e' * 32}


def before():
    return json.loads(FIXTURE.read_text())


def restarted(keep_shm=1):
    value = deepcopy(before())
    value['realtime_ns'] += 30 * 10**9
    value['generation']['pid1_start_ticks'] += 5000
    for i, p in enumerate(value['processes']):
        p['start_ticks'] += 5000
        p['unix_inodes'] = [n + 10**7 for n in p['unix_inodes']]
    value['shm'] = value['shm'][:keep_shm]
    kept = {(s['dev'], s['ino']) for s in value['shm']}
    for p in value['processes']:
        p['shm_refs'] = [r for r in p['shm_refs'] if tuple(r) in kept]
    return value


def trigger_for(value, role='worker'):
    p = next(p for p in value['processes'] if p['role'] == role)
    return {'at_ns': value['realtime_ns'] + 10**9, 'pid': p['pid'], 'start_ticks': p['start_ticks'], 'ack': True}


def build(after=None, **kw):
    first = before()
    kw.setdefault('trigger', trigger_for(first))
    kw.setdefault('normal_after', {'at_ns': first['realtime_ns'] + 40 * 10**9, 'ok': True})
    return producer.record(first, after or restarted(), pod_before=POD, pod_after=kw.pop('pod_after', NEXT_POD), **SCOPE, **kw)


def test_full_lineage_from_trigger_to_next_normal_inference():
    record, result = build()
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED', 'GPU_INPUT_PRODUCER_DIAGNOSTIC:GPU_MEMORY_RELEASE_UNOBSERVED']
    summary = producer.lineage(record)
    assert summary['failure_trigger_at_ms'] is not None and summary['next_normal_inference_at_ms'] > summary['failure_trigger_at_ms']
    assert summary['next_generation'].startswith('c' + 'c' * 12 + '-r1-p')
    # 프로세스·socket은 새 세대에서 모두 바뀌어 반환으로, 남은 공유 메모리 하나는 잔존 후보로 나온다.
    assert summary['released_by_kind']['pid'] == 4 and summary['released_by_kind']['ipc_socket'] > 0
    assert summary['released_by_kind']['shm'] == len(before()['shm']) - 1 and summary['remaining_by_kind'] == {'shm': 1}
    assert [c['reason'] for c in result['violation_candidates']] == ['resource_remaining_after_failure']
    assert summary['gpu_memory_release_observed'] is False


def test_registered_producer_still_withholds_gpu_release(monkeypatch):
    source = producer.producer()
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (source['name'], source['version']), source['source_sha256'])
    _, result = build()
    assert result['supported'] is True and result['complete'] is False
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_DIAGNOSTIC:GPU_MEMORY_RELEASE_UNOBSERVED']


@pytest.mark.parametrize('kw,code', [
    ({'trigger': None}, 'FAILURE_TRIGGER_UNOBSERVED'),
    ({'normal_after': None}, 'NEXT_NORMAL_INFERENCE_UNVERIFIED'),
    ({'normal_after': {'at_ns': 1, 'ok': False}}, 'NEXT_NORMAL_INFERENCE_UNVERIFIED'),
    ({'pod_after': POD}, 'NEXT_GENERATION_NOT_STARTED'),
])
def test_missing_lineage_parts_are_named(kw, code):
    after = before() if kw.get('pod_after') is POD else None
    record, _ = build(after, **kw)
    assert code in record['completeness']['diagnostic_codes']


def test_unacknowledged_or_foreign_trigger_is_not_a_failure_trigger_row():
    first = before()
    record, _ = build(trigger={**trigger_for(first), 'ack': False})
    assert 'FAILURE_TRIGGER_UNACKNOWLEDGED' in record['completeness']['diagnostic_codes']
    record, _ = build(trigger={**trigger_for(first), 'start_ticks': 1})
    assert 'FAILURE_TRIGGER_TARGET_NOT_IN_GROUP' in record['completeness']['diagnostic_codes']
    assert not any(r['event'] == 'failure_trigger' for r in record['events'])


def test_same_generation_retention_is_a_candidate_not_a_release():
    record, result = build(before(), pod_after=POD)
    summary = producer.lineage(record)
    assert summary['released_by_kind'] == {} and summary['next_generation'] is None
    assert result['violation_candidates']  # 같은 세대에서 전부 남음 — 판정은 소비자가 기한으로


def test_unreferenced_shared_memory_and_unstable_processes_are_reported():
    after = restarted()
    for p in after['processes']:
        p['shm_refs'] = []
    after['processes'][0]['stable'] = False
    record, result = build(after)
    codes = record['completeness']['diagnostic_codes']
    assert 'SHM_WITHOUT_PROCESS_REFERENCE' in codes and 'PROCESS_IDENTITY_CHANGED' in codes
    assert record['completeness']['source_ended'] is False and result['complete'] is False


@pytest.mark.parametrize('damage', [
    lambda v: v.update(schema='x'),
    lambda v: v['generation'].update(pid1_start_ticks=-1),
    lambda v: v['shm'][0].update(name_sha256='x'),
    lambda v: v['processes'][0].update(role='root'),
    lambda v: v['processes'][0].update(shm_refs=[[1]]),
    lambda v: v['processes'].append(deepcopy(v['processes'][0])),
])
def test_decode_rejects_malformed_observations(damage):
    value = before(); damage(value)
    with pytest.raises(ValueError, match='GROUP_RESOURCE_OBSERVATION_INVALID'):
        observer.decode(value)


def test_transport_runs_the_observer_only_on_binding_profiles():
    calls = []
    stub = SimpleNamespace(profile=ENGINE_CALLBACK_PROFILE, _verify=lambda: None,
                           binding={'engine': {'namespace': 'n', 'pod': 'p', 'container': 'engine', 'pod_uid': POD['pod_uid'],
                                               'container_id': POD['container_id'], 'restart_count': 0}},
                           _call=lambda argv, **k: calls.append(argv) or before())
    value, pod, stable = KubernetesHTTP.engine_group_resources(stub)
    assert stable and pod == POD and value['schema'] == observer.SCHEMA and calls[0][-1] == observer.OBSERVER
    stub.profile = REQUEST_BINDING_PROFILE; KubernetesHTTP.engine_group_resources(stub)
    stub.profile = PROFILE
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP.engine_group_resources(stub)
