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
    monkeypatch.setattr(contract, 'SUPPORTED_PRODUCERS', {})  # 등록 전 상태: 형식이 맞아도 complete가 아니다
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
    # 등록된 생산자(GPU 확인 2026-11-05)라 지원 gap이 없다. 위반 후보는 그대로다.
    assert record['completeness']['source_ended'] is True and result['supported'] is True and result['gaps'] == []


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


from guard.adapters import vllm_health_callback as health


def hrow(ns, phase, **fields):
    return {'schema': health.SCHEMA, 'point': 'AsyncLLM.check_health', 'phase': phase, 'entry_qualified_before': True,
            'entry_qualified_after': True, 'operation_started_ns': ns, 'operation_finished_ns': ns, 'fields': fields,
            'process_sha256': 'p' * 64, 'call_sha256': 'c' * 64}


def test_health_observer_reads_only_the_client_flags():
    obs = health.HealthObserver.__new__(health.HealthObserver); obs.gaps = set()
    task = SimpleNamespace(done=lambda: False)
    llm = SimpleNamespace(engine_core=SimpleNamespace(resources=SimpleNamespace(engine_dead=False)), output_handler=task)
    assert obs._snapshot('AsyncLLM.check_health', (llm,), {}) == {'engine_dead': False, 'output_handler_running': True}
    assert obs._snapshot('AsyncLLM.check_health', (llm,), {}, None, True) == {}


def test_health_and_worker_rows_keep_separate_generations_and_no_shared_causal_key():
    api = [hrow(1_000_000, 'CALL', engine_dead=False, output_handler_running=True), hrow(1_000_100, 'RETURN')]
    core = [row(2_000_000, 'Scheduler.add_request', 'RETURN', request_sha256=R1),
            row(3_000_000, 'Scheduler.update_from_output', 'RETURN', output_request_sha256=[R1])]
    record, result = producers.health_worker_record([{'pid': 90, 'start_ticks': 5, 'events': api},
                                                     {'pid': 74, 'start_ticks': 1000, 'events': core}], pod=POD, **SCOPE)
    events = record['events']
    assert [(e['event'], e['state']) for e in events] == [('api_health', 'healthy'), ('request_accepted', 'admitted'), ('worker_state', 'output')]
    assert events[0]['generation'] == 'process-90-5' and events[2]['generation'] == 'process-74-1000'
    assert len({e['causal_sha256'] for e in events}) == 3
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:HEALTH_WORKER_CAUSAL_LINK_ABSENT_IN_ENGINE' in result['gaps']
    dead = producers.health_worker_events([{'pid': 90, 'start_ticks': 5, 'events': [hrow(1, 'CALL', engine_dead=True, output_handler_running=True)]}])[0]
    assert dead[0]['state'] == 'unhealthy'


from guard.adapters import vllm_profiler_callback as profcb


def prow(ns, point, phase, **fields):
    return {'schema': profcb.SCHEMA, 'point': point, 'phase': phase, 'entry_qualified_before': True, 'entry_qualified_after': True,
            'operation_started_ns': ns, 'operation_finished_ns': ns, 'fields': fields, 'process_sha256': 'p' * 64, 'call_sha256': str(ns)}


def test_profiler_chain_across_three_processes_with_output_and_next_collection():
    api = [prow(1_000_000, 'AsyncLLM.start_profile', 'CALL', is_start=True), prow(9_000_000, 'AsyncLLM.stop_profile', 'CALL', is_start=False)]
    core = [prow(2_000_000, 'EngineCore.profile', 'RETURN', is_start=True), prow(10_000_000, 'EngineCore.profile', 'RETURN', is_start=False)]
    worker = [prow(1_500_000, 'TorchProfilerWrapper._start', 'RETURN', is_start=True), prow(1_800_000, 'Worker.profile', 'RETURN', is_start=True, rank=0),
              prow(9_500_000, 'TorchProfilerWrapper._stop', 'RETURN', is_start=False, output_files=[{'name_sha256': 'n' * 64, 'bytes': 10}]),
              prow(9_800_000, 'Worker.profile', 'RETURN', is_start=False, rank=0),
              prow(11_000_000, 'TorchProfilerWrapper._start', 'RETURN', is_start=True)]
    record, result = producers.profiler_control_record([{'pid': 1, 'start_ticks': 1, 'events': api}, {'pid': 74, 'start_ticks': 2, 'events': core},
                                                        {'pid': 111, 'start_ticks': 3, 'events': worker}], pod=POD, **SCOPE)
    assert [(e['event'], e['state']) for e in record['events']] == [
        ('api_call', 'start'), ('backend_state', 'started'), ('worker_control', 'start'), ('rpc', 'start'), ('api_call', 'stop'),
        ('backend_state', 'stopped'), ('output_complete', 'files'), ('worker_control', 'stop'), ('rpc', 'stop'), ('next_collection', 'started')]
    assert record['completeness']['source_ended'] is True and len(record['bindings']) == 3
    # 등록 뒤에도 공유 인과 키 부재 진단은 남아 complete가 아니다.
    assert set(result['gaps']) == {'GPU_INPUT_PRODUCER_DIAGNOSTIC:PROFILER_CAUSAL_ID_ABSENT_IN_ENGINE'} and result['complete'] is False


def test_profiler_stop_failure_and_missing_output_stay_visible():
    worker = [prow(1, 'TorchProfilerWrapper._stop', 'RAISE', is_start=False), prow(2, 'Worker.profile', 'RAISE', is_start=False),
              prow(3, 'TorchProfilerWrapper._stop', 'RETURN', is_start=False, output_files=[])]
    events, _, codes = producers.profiler_control_events([{'pid': 111, 'start_ticks': 3, 'events': worker}])
    assert [e['event'] for e in events] == ['stop_failed', 'stop_failed', 'backend_state'] and codes == ['PROFILER_OUTPUT_UNOBSERVED']


from guard.adapters import vllm_consumption_callback as cons


def crow(ns, point, phase, call='1', **fields):
    return {'schema': cons.SCHEMA, 'point': point, 'phase': phase, 'entry_qualified_before': True, 'entry_qualified_after': True,
            'operation_started_ns': ns, 'operation_finished_ns': ns, 'fields': fields, 'process_sha256': 'p' * 64, 'call_sha256': call}


PARAMS = {'temperature_milli': 0, 'top_p_milli': 1000, 'top_k': 0, 'max_tokens': 4, 'logprobs': 0, 'allowed_tokens': 3, 'bad_words': 1, 'logit_bias': 0}


def api_rows(request=R1, *, reject=False):
    return [crow(1, 'InputProcessor.process_inputs', 'CALL', call='a', request_sha256=request),
            crow(2, 'InputProcessor._validate_params', 'CALL', call='b', request_sha256=request, params=PARAMS),
            crow(3, 'InputProcessor._validate_params', 'RAISE' if reject else 'RETURN', call='b'),
            crow(4, 'InputProcessor._validate_model_input', 'CALL', call='c', request_sha256=request, prompt_tokens=5, max_token_id=99),
            crow(5, 'InputProcessor._validate_model_input', 'RETURN', call='c'),
            crow(6, 'InputProcessor.process_inputs', 'RAISE' if reject else 'RETURN', call='a')]


def test_consumption_observer_reads_counts_not_strings():
    params = SimpleNamespace(temperature=0.7, top_p=0.9, top_k=-1, max_tokens=8, logprobs=None, allowed_token_ids=[1, 2],
                             bad_words=['secret-word'], logit_bias={5: 1.0})
    summary = cons.params_summary(params)
    assert summary == {'temperature_milli': 700, 'top_p_milli': 900, 'top_k': 0, 'max_tokens': 8, 'logprobs': 0,
                       'allowed_tokens': 2, 'bad_words': 1, 'logit_bias': 1}
    assert 'secret-word' not in str(summary)


def test_api_runner_and_sampler_values_match_and_complete_the_record():
    worker = [crow(10, 'GPUModelRunner._update_states', 'CALL', received=[{'request_sha256': R1, 'prompt_tokens': 5, 'max_token_id': 99, 'params': PARAMS}]),
              crow(11, 'GPUModelRunner._sample', 'CALL', applied=[{'request_sha256': R1, 'temperature_milli': 0, 'top_p_milli': 1000, 'top_k': 0,
                                                                    'logprobs': 0, 'allowed_tokens': 3, 'bad_words': 2}]),
              crow(12, 'GPUModelRunner._sample', 'CALL', applied=[{'request_sha256': R1, 'temperature_milli': 0, 'top_p_milli': 1000, 'top_k': 0,
                                                                    'logprobs': 0, 'allowed_tokens': 3, 'bad_words': 2}])]
    record, result = producers.runner_consumption_record([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
                                                          {'pid': 112, 'start_ticks': 2, 'events': worker}], pod=POD, **SCOPE)
    assert [e['event'] for e in record['events']] == ['api_validated', 'runner_received', 'sampler_applied']
    assert record['completeness']['source_ended'] is True and result['violation_candidates'] == []
    assert result['gaps'] == [] and result['supported'] is True and result['producer_authenticated'] is False


def test_restriction_lost_before_sampler_and_rejected_input_consumed_are_candidates():
    lost = [crow(10, 'GPUModelRunner._sample', 'CALL', applied=[{'request_sha256': R1, 'temperature_milli': 0, 'top_p_milli': 1000, 'top_k': 0,
                                                                  'logprobs': 0, 'allowed_tokens': 0, 'bad_words': 0}])]
    _, result = producers.runner_consumption_record([{'pid': 1, 'start_ticks': 1, 'events': api_rows()},
                                                     {'pid': 112, 'start_ticks': 2, 'events': lost}], pod=POD, **SCOPE)
    candidate, = result['violation_candidates']
    assert candidate['stage'] == 'sampler_applied' and candidate['fields'] == ['allowed_tokens', 'bad_words']
    consumed = [crow(10, 'GPUModelRunner._update_states', 'CALL', received=[{'request_sha256': R1, 'prompt_tokens': 5, 'max_token_id': 99, 'params': PARAMS}])]
    _, result = producers.runner_consumption_record([{'pid': 1, 'start_ticks': 1, 'events': api_rows(reject=True)},
                                                     {'pid': 112, 'start_ticks': 2, 'events': consumed}], pod=POD, **SCOPE)
    assert {c['reason'] for c in result['violation_candidates']} == {'rejected_input_consumed'}


from guard.adapters import vllm_supply_read_callback as supply


def test_audit_hook_records_only_declared_roots_once_with_access_kind(tmp_path):
    root = tmp_path / 'models'; root.mkdir(); (root / 'config.json').write_text('{}')
    rows = []
    observer = supply.SupplyReadObserver([str(root)], sink=rows.append, process_sha256='p' * 64, clock=lambda: 5)
    observer.hook('open', (str(root / 'config.json'), 'r', 0))
    observer.hook('open', (str(root / 'config.json'), 'r', 0))
    observer.hook('open', (str(root / 'new.bin'), 'wb', 0))
    observer.hook('open', (str(tmp_path / 'outside.txt'), 'r', 0))
    observer.hook('os.listdir', (str(root),))
    assert [(r['relative'], r['write']) for r in rows] == [('config.json', False), ('new.bin', True)]
    assert rows[0]['size'] == 2 and rows[0]['regular'] is True and observer.seen[(0, 'config.json', False)] == 2


def test_supply_record_matches_opens_to_hashes_and_flags_writes_and_changes():
    opened = [{'schema': supply.SCHEMA, 'phase': 'OPEN', 'root': 0, 'relative': 'config.json', 'write': False, 'regular': True, 'size': 2, 'opened_ns': 1},
              {'schema': supply.SCHEMA, 'phase': 'OPEN', 'root': 0, 'relative': 'tokenizer.json', 'write': True, 'regular': True, 'size': 5, 'opened_ns': 2}]
    processes = [{'pid': 1, 'start_ticks': 1, 'events': opened}]
    assert producers.supply_open_files(processes) == [{'root': 0, 'relative': 'config.json'}, {'root': 0, 'relative': 'tokenizer.json'}]
    hashes = [{'root': 0, 'relative': 'config.json', 'sha256': 'a' * 64, 'size': 2, 'state': 'hashed'},
              {'root': 0, 'relative': 'tokenizer.json', 'sha256': 'b' * 64, 'size': 9, 'state': 'hashed'}]
    record, result = producers.supply_read_record(processes, hashes, pod=POD, **SCOPE)
    assert record['completeness']['source_ended'] is True
    assert record['events'][0]['path_sha256'] == producers.path_sha256('config.json')
    assert {c['reason'] for c in result['violation_candidates']} == {'supply_file_written', 'supply_file_changed_after_open'}
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:SUPPLY_NATIVE_READS_UNOBSERVED' in result['gaps']
    _, partial = producers.supply_read_record(processes, hashes[:1], pod=POD, **SCOPE)
    assert 'GPU_INPUT_SOURCE_INCOMPLETE' in partial['gaps']


def test_inotify_events_become_unattributed_rows_once_and_overflow_is_a_gap(tmp_path):
    import struct
    root = tmp_path / 'models'; (root / 'sub').mkdir(parents=True)
    rows = []
    watcher = supply.SupplyWatcher([str(root)], sink=rows.append, process_sha256='p' * 64, clock=lambda: 7)
    watcher.watches = {1: (0, '', None), 2: (0, 'sub/', None), 3: (0, '', '.')}
    def event(wd, mask, name):
        raw = name.encode() + b'\0' * (16 - len(name) % 16)
        return struct.pack('iIII', wd, mask, 0, len(raw)) + raw
    watcher.handle(event(1, supply.IN_OPEN, 'vocab.json') + event(1, supply.IN_ACCESS, 'vocab.json')
                   + event(2, supply.IN_OPEN, 'merges.txt') + event(1, supply.IN_OPEN | supply.IN_ISDIR, 'sub')
                   + event(-1, supply.IN_Q_OVERFLOW, '') + struct.pack('iIII', 3, supply.IN_OPEN, 0, 0))
    observed = [r for r in rows if r['phase'] == 'INOTIFY']
    assert [r['relative'] for r in observed] == ['vocab.json', 'sub/merges.txt', '.'] and observed[0]['process_attributed'] is False
    assert [r['code'] for r in rows if r['phase'] == 'GAP'] == ['SUPPLY_INOTIFY_OVERFLOW'] and 'SUPPLY_INOTIFY_OVERFLOW' in watcher.gaps


def test_inotify_rows_join_the_record_and_replace_the_unobserved_diagnostic():
    rows = [{'schema': supply.SCHEMA, 'phase': 'OPEN', 'root': 0, 'relative': 'config.json', 'write': False, 'regular': True, 'size': 2, 'opened_ns': 1},
            {'schema': supply.SCHEMA, 'phase': 'INOTIFY', 'root': 0, 'relative': 'vocab.json', 'opened_ns': 2, 'process_attributed': False}]
    processes = [{'pid': 1, 'start_ticks': 1, 'events': rows}]
    files = producers.supply_open_files(processes)
    assert {'root': 0, 'relative': 'vocab.json'} in files
    hashes = [{**f, 'sha256': 'a' * 64, 'size': 1, 'state': 'hashed'} for f in files]
    record, result = producers.supply_read_record(processes, hashes, pod=POD, **SCOPE)
    assert [e['event'] for e in record['events']] == ['file_opened', 'file_observed', 'file_hashed', 'file_hashed']
    assert record['completeness']['source_ended'] is True
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED' in result['gaps']
    assert not any('UNOBSERVED' in g for g in result['gaps'])
