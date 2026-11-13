"""#196 공개 입력 계약: 형식·세대 결합·지원 생산자·완료 주장 거부 조건."""
from copy import deepcopy

import pytest

from guard.collectors import gpu_common_inputs as contract

ENV = {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64}
SCOPE = {'environment_ref': ENV, 'target_sha256': 'b' * 64, 'prepared_sha256': 'c' * 64, 'session': 'd' * 32}
PRODUCER = {'name': 'guard-gpu-attempt', 'version': '1', 'source_sha256': 'e' * 64}
R1, R2 = '1' * 64, '2' * 64


def binding(pid=101):
    return {'pod_uid': 'pod-1', 'container_id': 'containerd-1', 'restart_count': 0, 'worker_pid': pid, 'start_ticks': 5, 'rank': 0}


def row(seq, event, **values):
    base = {'request_sha256': R1, 'attempt': 1, 'at_ms': seq, 'stage': None, 'cost': None, 'kv_blocks': None, 'gpu_bytes': None,
            'retry_cause': None, 'next_attempt': None, 'consumed_by_attempt': None}
    return {'seq': seq, 'event': event, 'binding': 0, **base, **values}


def record(events, *, kind='request-attempt', ended=True, dropped=0, codes=()):
    return {'schema': contract.SCHEMA, 'kind': kind, 'producer': PRODUCER, **SCOPE, 'bindings': [binding()],
            'events': events, 'completeness': {'source_ended': ended, 'dropped': dropped, 'diagnostic_codes': list(codes)}}


COMPLETE = [row(0, 'admitted'), row(1, 'finished'), row(2, 'gpu_fence'), row(3, 'kv_released')]


def test_well_formed_record_from_an_unregistered_producer_is_never_complete(monkeypatch):
    result = contract.validate(record(COMPLETE), kind='request-attempt', **SCOPE)
    assert result['supported'] is False and result['complete'] is False and result['producer_authenticated'] is False
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED'] and ('guard-gpu-attempt', '1') not in contract.SUPPORTED_PRODUCERS
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, ('guard-gpu-attempt', '1'), 'e' * 64)
    assert contract.validate(record(COMPLETE), kind='request-attempt', **SCOPE)['complete'] is True
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, ('guard-gpu-attempt', '1'), 'f' * 64)
    assert contract.validate(record(COMPLETE), kind='request-attempt', **SCOPE)['supported'] is False


def test_missing_required_stages_incomplete_sources_and_diagnostics_are_gaps_not_violations(monkeypatch):
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, ('guard-gpu-attempt', '1'), 'e' * 64)
    result = contract.validate(record(COMPLETE[:2], ended=False, codes=['READ_TIMEOUT']), kind='request-attempt', **SCOPE)
    assert result['complete'] is False and result['violation_candidates'] == []
    assert set(result['gaps']) == {'GPU_INPUT_SOURCE_INCOMPLETE', 'GPU_INPUT_REQUIRED_EVENT_MISSING:gpu_fence',
                                   'GPU_INPUT_REQUIRED_EVENT_MISSING:kv_released', 'GPU_INPUT_PRODUCER_DIAGNOSTIC:READ_TIMEOUT'}


def test_violation_candidates_survive_later_collection_errors():
    events = [*COMPLETE, row(4, 'retry', retry_cause='timeout'), row(5, 'stage', attempt=2, consumed_by_attempt=1, stage='decode')]
    result = contract.validate(record(events, ended=False), kind='request-attempt', **SCOPE)
    assert [c['reason'] for c in result['violation_candidates']] == ['retry_without_new_attempt', 'consumption_by_finished_attempt']
    assert 'GPU_INPUT_SOURCE_INCOMPLETE' in result['gaps']


def test_mixed_generation_and_remaining_resources_are_candidates():
    gen = lambda seq, event, generation, component='weights': {'seq': seq, 'event': event, 'binding': 0, 'at_ms': seq,
        'request_sha256': R2, 'generation': generation, 'component': component, 'state': None, 'error_sha256': None, 'alias_sha256': None}
    events = [gen(0, 'in_flight', 'g1'), gen(1, 'consumed', 'g1'), gen(2, 'consumed', 'g2'), gen(3, 'service_gate', 'g2', 'service')]
    result = contract.validate(record(events, kind='generation-transition'), kind='generation-transition', **SCOPE)
    assert result['violation_candidates'] == [{'request_sha256': R2, 'component': 'weights', 'reason': 'mixed_generation_consumption'}]
    res = lambda seq, event, item: {'seq': seq, 'event': event, 'binding': 0, 'at_ms': seq, 'resource': 'shm',
                                    'resource_sha256': item, 'owner_group_sha256': 'f' * 64, 'next_generation': None}
    events = [res(0, 'inventory_before', R1), res(1, 'inventory_before', R2), res(2, 'inventory_after', R2), res(3, 'released', R1)]
    result = contract.validate(record(events, kind='failed-group-resources'), kind='failed-group-resources', **SCOPE)
    assert result['violation_candidates'] == [{'resource_sha256': R2, 'reason': 'resource_remaining_after_failure'}]


@pytest.mark.parametrize('damage', ['scope', 'binding', 'duplicate-binding', 'event-field', 'event-kind', 'seq', 'secret', 'kind'])
def test_closed_format_rejects_unbound_or_open_records(damage):
    value, kind = record(deepcopy(COMPLETE)), 'request-attempt'
    if damage == 'scope':
        value['session'] = 'e' * 32
    elif damage == 'binding':
        value['bindings'][0]['worker_pid'] = 0
    elif damage == 'duplicate-binding':
        value['bindings'].append(binding())
    elif damage == 'event-field':
        value['events'][0]['extra'] = 1
    elif damage == 'event-kind':
        value['events'][0]['event'] = 'gpu_process'
    elif damage == 'seq':
        value['events'][1]['seq'] = 7
    elif damage == 'secret':
        value['events'][0]['stage'] = 'sk-live secret value'
    else:
        kind = 'rank-fault'
    with pytest.raises(contract.GpuInputError):
        contract.validate(value, kind=kind, **SCOPE)


def test_every_kind_names_its_consumers_and_required_events():
    consumers = {c for spec in contract.KINDS.values() for c in spec['checks']}
    assert consumers == {'LIF-03', 'LIF-05', 'LIF-06', 'LIF-07', 'LIF-08', 'LIF-09', 'LIF-10', 'LIF-11', 'LIF-12',
                         'LIF-14', 'PLT-01', 'PLT-02', 'PLT-04', 'OBS-02', 'OBS-03', 'REQ-08', 'REQ-09', 'REQ-10', 'SUP-03',
                         'SUP-06'}
    assert all(set(spec['complete_requires']) <= set(spec['events']) for spec in contract.KINDS.values())


def test_historical_gpu_registration_is_preserved_and_changed_sources_remain_unregistered():
    """과거 등록은 보존하고 의존성이 바뀐 일곱 생산자에게 이월하지 않는다."""
    from guard.collectors.process_device_producer import device_producer
    from guard.collectors.runner_consumption_v2_producer import producer as v2_producer
    from guard.collectors import gpu_input_producers as producers
    current = [f() for f in (producers.attempt_producer, producers.transition_producer, producers.health_producer,
                             producers.profiler_producer, producers.consumption_producer, producers.supply_producer)]
    current.extend([device_producer(), v2_producer()])
    import json
    from pathlib import Path
    historical = json.loads((Path(__file__).parent / 'fixtures/gpu-registered-producers-20261105.json').read_bytes())
    assert contract.SUPPORTED_PRODUCERS == {(p['name'], p['version']): p['source_sha256'] for p in historical['rows']}
    assert len(current) == len(contract.SUPPORTED_PRODUCERS) == 8
    unchanged = {p['name'] for p in current if contract.SUPPORTED_PRODUCERS[(p['name'], p['version'])] == p['source_sha256']}
    assert unchanged == {'guard-node-process-device-provider'}
    changed = [p for p in current if p['name'] not in unchanged]
    assert len(changed) == 7
    assert all(contract.SUPPORTED_PRODUCERS[(p['name'], p['version'])] != p['source_sha256'] for p in changed)
