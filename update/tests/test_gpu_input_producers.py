"""#196 request-attempt 생산자(P1): 엔진 관측 → 계약 기록. 값은 관측에서만 온다."""
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_attempt_callback as attempt
from guard.collectors import gpu_common_inputs as contract
from guard.collectors import gpu_input_producers as producers

ENV = {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64}
SCOPE = {'environment_ref': ENV, 'target_sha256': 'b' * 64, 'prepared_sha256': 'c' * 64, 'session': 'd' * 32}
POD = {'pod_uid': '10000000-0000-0000-0000-000000000001', 'container_id': 'containerd://' + 'e' * 64, 'restart_count': 0}
R1, R2 = '1' * 64, '2' * 64


def observer():
    value = attempt.AttemptObserver.__new__(attempt.AttemptObserver)
    value.gaps = set(); value.fingerprint = lambda v: ('f' + str(v)).ljust(64, '0')[:64] if v is not None else None
    return value


def test_observer_reads_only_counts_ids_and_states():
    obs = observer()
    request = SimpleNamespace(request_id='req-1', num_preemptions=0, num_computed_tokens=3,
                              status=SimpleNamespace(name='FINISHED_ABORTED'), prompt='secret prompt')
    scheduler = SimpleNamespace(max_num_scheduled_tokens=512)
    output = SimpleNamespace(num_scheduled_tokens={'req-1': 7}, total_num_scheduled_tokens=7)
    row = obs._snapshot('Scheduler.schedule', (scheduler,), {}, output, True)
    assert row['budget_tokens'] == 512 and row['scheduled'][0]['tokens'] == 7 and row['total_tokens'] == 7
    assert obs._snapshot('Scheduler._free_request', (scheduler, request), {})['status'] == 'FINISHED_ABORTED'
    blocks = SimpleNamespace(blocks=([1, 2], [3]))
    kv = obs._snapshot('KVCacheManager.allocate_slots', (object(), request, 16), {}, blocks, True)
    assert kv['allocated'] is True and kv['new_blocks'] == 3 and kv['num_new_tokens'] == 16
    assert obs._snapshot('KVCacheManager.allocate_slots', (object(), request, 16), {}, None, True)['allocated'] is False
    fence = obs._snapshot('Scheduler.update_from_output', (scheduler, None, SimpleNamespace(req_ids=['req-1'])), {}, {}, True)
    assert len(fence['output_request_sha256']) == 1
    assert 'secret prompt' not in str([row, kv, fence])


def row(ns, point, phase, **fields):
    return {'schema': attempt.SCHEMA, 'point': point, 'phase': phase, 'entry_qualified_before': True,
            'entry_qualified_after': True, 'operation_finished_ns': ns, 'fields': fields}


def lifecycle(request=R1, *, preempt=False, abort=False):
    rows = [row(1_000_000, 'Scheduler.add_request', 'RETURN', request_sha256=request),
            row(2_000_000, 'Scheduler.schedule', 'RETURN', budget_tokens=512, scheduled=[{'request_sha256': request, 'tokens': 8}]),
            row(2_500_000, 'KVCacheManager.allocate_slots', 'RETURN', request_sha256=request, allocated=True, new_blocks=2, num_new_tokens=8),
            row(3_000_000, 'Scheduler.update_from_output', 'RETURN', output_request_sha256=[request])]
    if preempt:
        rows += [row(3_500_000, 'Scheduler._preempt_request', 'RETURN', request_sha256=request),
                 row(4_000_000, 'Scheduler.schedule', 'RETURN', budget_tokens=512, scheduled=[{'request_sha256': request, 'tokens': 8}])]
    rows += [row(5_000_000, 'Scheduler._free_request', 'CALL', request_sha256=request, status='FINISHED_ABORTED' if abort else 'FINISHED_STOPPED'),
             row(6_000_000, 'KVCacheManager.free', 'RETURN', request_sha256=request)]
    return rows


def test_complete_lifecycle_becomes_a_valid_record_that_stays_unsupported_until_registration(monkeypatch):
    record, result = producers.request_attempt_record([{'pid': 74, 'start_ticks': 1000, 'events': lifecycle()}], pod=POD, **SCOPE)
    assert [e['event'] for e in record['events']] == ['admitted', 'queued', 'budget', 'dequeued', 'kv_owned', 'gpu_fence', 'finished', 'kv_released']
    assert record['bindings'] == [{**{k: POD[k] for k in ('pod_uid', 'restart_count')}, 'container_id': 'e' * 64,
                                   'worker_pid': 74, 'start_ticks': 1000, 'rank': 0}]
    assert record['completeness']['source_ended'] is True
    assert result['supported'] is False and result['complete'] is False and result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED']
    producer = record['producer']
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (producer['name'], producer['version']), producer['source_sha256'])
    assert contract.validate(record, kind='request-attempt', **SCOPE)['complete'] is True


def test_preemption_is_a_new_attempt_and_abort_is_kept_apart():
    record, result = producers.request_attempt_record([{'pid': 74, 'start_ticks': 1000, 'events': lifecycle(preempt=True, abort=True)}],
                                                      pod=POD, **SCOPE)
    events = record['events']
    retry, = (e for e in events if e['event'] == 'retry')
    assert retry['retry_cause'] == 'preempted' and retry['next_attempt'] == 2
    assert [e['attempt'] for e in events if e['event'] in ('dequeued',)] == [1, 2]
    assert any(e['event'] == 'abort' for e in events) and result['violation_candidates'] == []


def test_unfinished_requests_unknown_processes_and_filters():
    open_rows = lifecycle()[:4]
    record, result = producers.request_attempt_record([{'pid': 74, 'start_ticks': 1000, 'events': open_rows}], pod=POD, **SCOPE)
    assert record['completeness']['source_ended'] is False and 'GPU_INPUT_SOURCE_INCOMPLETE' in result['gaps']
    events, bindings, codes = producers.request_attempt_events([{'pid': 9, 'start_ticks': None, 'events': lifecycle()}])
    assert events == [] and bindings == [] and codes == ['ATTEMPT_PROCESS_START_UNAVAILABLE']
    both = [{'pid': 74, 'start_ticks': 1000, 'events': lifecycle(R1) + lifecycle(R2)}]
    events, _, _ = producers.request_attempt_events(both, requests={R2})
    assert {e['request_sha256'] for e in events} == {R2}
    replaced = [{**r, 'entry_qualified_after': False} for r in lifecycle()]
    assert producers.request_attempt_events([{'pid': 74, 'start_ticks': 1000, 'events': replaced}])[0] == [
        e for e in producers.request_attempt_events([{'pid': 74, 'start_ticks': 1000, 'events': replaced}])[0] if e['event'] == 'finished']
    with pytest.raises(contract.GpuInputError):
        producers.request_attempt_record([], pod=POD, **SCOPE)


def test_producer_identity_binds_both_source_files():
    first = producers.attempt_producer()
    assert first['name'] == 'guard-engine-callback-request-attempt' and len(first['source_sha256']) == 64
    assert first == producers.attempt_producer()
