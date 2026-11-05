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


from guard.adapters import vllm_transition_callback as transition


def trow(ns, point, phase, **fields):
    return {'schema': transition.SCHEMA, 'point': point, 'phase': phase, 'entry_qualified_before': True,
            'entry_qualified_after': True, 'operation_started_ns': ns, 'operation_finished_ns': ns, 'fields': fields}


def sched(ns, *requests):
    return row(ns, 'Scheduler.schedule', 'RETURN', budget_tokens=512, scheduled=[{'request_sha256': r, 'tokens': 1} for r in requests])


def test_transition_observer_keeps_only_names_and_in_flight_fingerprints():
    obs = transition.TransitionObserver.__new__(transition.TransitionObserver)
    obs.gaps = set(); obs.fingerprint = lambda v: ('f' + str(v)).ljust(64, '0')[:64] if v is not None else None
    core = SimpleNamespace(scheduler=SimpleNamespace(requests={'req-1': object()}))
    entry = obs._snapshot('EngineCore.collective_rpc', (core, 'reload_weights'), {'kwargs': {'weights_path': '/secret/path'}})
    assert entry['rpc_transition'] is True and len(entry['in_flight_request_sha256']) == 1 and '/secret/path' not in str(entry)
    other = obs._snapshot('EngineCore.collective_rpc', (core, 'get_kv_cache_spec'), {})
    assert other['rpc_transition'] is False and 'in_flight_request_sha256' not in other
    lora = obs._snapshot('EngineCore.add_lora', (core, SimpleNamespace(lora_name='tenant-a', lora_path='/x')), {}, True, True)
    assert lora['result'] is True and '/x' not in str(lora)


def test_reload_during_a_live_request_shows_in_flight_mixed_consumption_and_the_gate():
    rows = [row(1_000_000, 'Scheduler.add_request', 'RETURN', request_sha256=R1), sched(2_000_000, R1),
            trow(3_000_000, 'EngineCore.collective_rpc', 'CALL', rpc_transition=True, in_flight_request_sha256=[R1]),
            trow(4_000_000, 'EngineCore.collective_rpc', 'RETURN', rpc_transition=True, result=None),
            sched(5_000_000, R1), row(6_000_000, 'Scheduler.add_request', 'RETURN', request_sha256=R2), sched(7_000_000, R2)]
    record, result = producers.generation_transition_record([{'pid': 74, 'start_ticks': 1000, 'events': rows}], pod=POD, **SCOPE)
    kinds = [(e['event'], e['generation']) for e in record['events']]
    assert kinds == [('consumed', 'weights-0'), ('in_flight', 'weights-0'), ('model_loaded', 'weights-1'), ('service_gate', 'service-0'),
                     ('consumed', 'weights-1'), ('consumed', 'weights-1'), ('accepted', 'service-0')]
    assert result['violation_candidates'] == [{'request_sha256': R1, 'component': 'weights', 'reason': 'mixed_generation_consumption'}]
    assert record['completeness']['source_ended'] is True and result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED']


def test_failed_transition_then_recovery_and_paused_gate():
    rows = [sched(1_000_000, R1),
            trow(2_000_000, 'EngineCore.pause_scheduler', 'CALL', mode='wait', in_flight_request_sha256=[R1]),
            trow(3_000_000, 'EngineCore.collective_rpc', 'CALL', rpc_transition=True, in_flight_request_sha256=[]),
            trow(3_500_000, 'EngineCore.collective_rpc', 'RAISE', rpc_transition=True),
            trow(4_000_000, 'EngineCore.collective_rpc', 'CALL', rpc_transition=True, in_flight_request_sha256=[]),
            trow(4_500_000, 'EngineCore.collective_rpc', 'RETURN', rpc_transition=True, result=None),
            trow(5_000_000, 'EngineCore.resume_scheduler', 'RETURN')]
    events, _, _ = producers.generation_transition_events([{'pid': 74, 'start_ticks': 1000, 'events': rows}])
    names = [e['event'] for e in events]
    assert names == ['consumed', 'in_flight', 'drain', 'failure', 'model_loaded', 'recovery', 'routing', 'service_gate']
    assert next(e for e in events if e['event'] == 'drain')['state'] == 'wait'
    ignored = [trow(1, 'EngineCore.collective_rpc', 'CALL', rpc_transition=False), trow(2, 'EngineCore.collective_rpc', 'RETURN', rpc_transition=False)]
    assert producers.generation_transition_events([{'pid': 74, 'start_ticks': 1000, 'events': ignored}])[0] == []


def test_attempt_reader_keeps_only_attempt_transition_and_gap_rows_within_its_budget(tmp_path):
    import json, os, subprocess, sys
    from guard.runtime.kubernetes_http import ATTEMPT_EVENTS_READER
    session = 'a' * 32; root = tmp_path / session; root.mkdir()
    compact = lambda v: json.dumps(v, sort_keys=True, separators=(',', ':'))  # 센서 Sink와 같은 형식
    noise = compact({'schema': 'guard-vllm-cache-callback/1', 'pad': 'x' * 4000})
    keep = compact({'schema': attempt.SCHEMA, 'phase': 'RETURN'}); gap = compact({'phase': 'GAP', 'code': 'X'})
    (root / f'callbacks-{os.getpid()}.jsonl').write_text('\n'.join([noise] * 2000 + [keep, gap, keep]) + '\n' + keep)
    (root / 'other.jsonl').write_text(keep + '\n')
    source = ATTEMPT_EVENTS_READER.replace('"/guard-lifecycle/data/"', repr(str(tmp_path) + '/'))
    done = subprocess.run([sys.executable, '-c', source, session], capture_output=True, text=True, check=True)
    value = json.loads(done.stdout)
    process, = value['processes']
    assert value['truncated'] is False and process['pid'] == os.getpid() and type(process['start_ticks']) in (int, type(None))
    assert [r.get('phase') for r in process['events']] == ['RETURN', 'GAP', 'RETURN']  # 끝 줄바꿈 없는 마지막 행은 쓰는 중이라 뺀다
