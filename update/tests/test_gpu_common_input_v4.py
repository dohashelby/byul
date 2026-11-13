"""공통 입력 /4의 원 행 보존·상한·미관측·미등록 경계 직접 시험."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import gpu_common_inputs as common, gpu_common_input_v4 as v4
from guard.collectors import gpu_input_producers as producers, runner_consumption_v2_producer as v2
from guard.executors.lifecycle_common_input_consumption import consume_record
from tests.test_gpu_common_inputs import record, row, SCOPE, COMPLETE
from tests.test_gpu_input_producers import POD, lifecycle


def encode(events, **kw):
    return v4.encode(record(events), wire_schema=v4.SCHEMA, kv_unit='block', **kw)


def validate(value):
    return common.validate(value, kind=value['kind'], **SCOPE)


def test_long_repetition_keeps_exact_end_rows_and_source_count_in_consumer():
    events = [row(0, 'admitted')]
    events += [row(i, 'stage', stage='decode', cost=7, consumed_by_attempt=1) for i in range(1, 12001)]
    events += [row(12001, 'finished'), row(12002, 'gpu_fence'), row(12003, 'kv_released')]
    value = encode(events)
    assert len(value['events']) == 7
    first, summary, last = value['events'][1:4]
    assert first['at_ms'] == first['source_seq'] == 1 and first['repeat'] is None
    assert last['at_ms'] == last['source_seq'] == 12000 and last['repeat'] is None
    assert summary['repeat'] == {'first_at_ms': 2, 'last_at_ms': 11999, 'first_seq': 2, 'last_seq': 11999,
        'count': 11998, 'max_values': {'attempt': 1, 'cost': 7, 'consumed_by_attempt': 1}}
    assert validate(value)['source_events'] == len(events)
    facts = consume_record('LIF-03', value, **SCOPE)
    assert facts['facts']['event_counts']['stage'] == 12000
    assert not facts['whole_question_complete'] and not facts['producer_authenticated']
    assert 'GPU_INPUT_PRODUCER_UNSUPPORTED' in facts['gaps']
    with pytest.raises(common.GpuInputError, match='GPU_INPUT_EVENTS_INVALID'):
        common.validate(record(events), kind='request-attempt', **SCOPE)


@pytest.mark.parametrize('field,value', [('attempt', 2), ('binding', 1), ('request_sha256', '2'*64),
    ('cost', 9), ('consumed_by_attempt', 3), ('stage', 'prefill'), ('kv_blocks', 4)])
def test_changed_values_are_never_folded_away(field, value):
    events = [row(i, 'stage', cost=7) for i in range(9)]
    events[4][field] = value
    out = encode(events)['events']
    changed = [r for r in out if r['source_seq'] == 4]
    assert len(changed) == 1 and changed[0]['repeat'] is None
    target = 'kv_units' if field == 'kv_blocks' else field
    assert changed[0][target] == value


@pytest.mark.parametrize('damage', ['count', 'first_seq', 'last_seq', 'first_at_ms', 'last_at_ms',
    'max_values', 'bool_max', 'binding', 'missing_first', 'missing_last', 'unknown_field', 'source_seq', 'bool_seq'])
def test_invalid_summary_cannot_hide_lineage_order_or_values(damage):
    value = encode([row(i, 'stage', cost=7) for i in range(8)])
    summary = value['events'][1]
    if damage in {'count', 'first_seq', 'last_seq', 'first_at_ms', 'last_at_ms'}:
        summary['repeat'][damage] = -1
    elif damage == 'max_values':
        summary['repeat']['max_values']['cost'] = 100
    elif damage == 'bool_max':
        summary['repeat']['max_values']['attempt'] = True
    elif damage == 'binding':
        summary['binding'] = 1
    elif damage == 'missing_first':
        value['events'] = value['events'][1:]
    elif damage == 'missing_last':
        value['events'].pop()
    elif damage == 'unknown_field':
        summary['repeat']['secret'] = '미허용 원문'
    elif damage == 'bool_seq':
        summary['seq'] = True
    else:
        value['events'][0]['source_seq'] = 4
    with pytest.raises(common.GpuInputError):
        validate(value)


def test_irreducible_records_keep_the_original_4096_row_limit():
    assert validate(encode([row(i, 'stage', cost=i) for i in range(4096)]))['events'] == 4096
    with pytest.raises(common.GpuInputError, match='GPU_INPUT_EVENTS_INVALID'):
        validate(encode([row(i, 'stage', cost=i) for i in range(4097)]))


@pytest.mark.parametrize('unit,tokens', [('block', None), ('block', 16), ('token_slot', 1)])
def test_native_kv_values_and_known_unit_size_are_not_converted(unit, tokens):
    value = v4.encode(record([row(0, 'kv_owned', kv_blocks=3)]), wire_schema=v4.SCHEMA,
                      kv_unit=unit, kv_unit_tokens=tokens)
    assert value['events'][0]['kv_units'] == 3 and 'kv_blocks' not in value['events'][0]
    validate(value)
    facts = consume_record('LIF-12', value, **SCOPE)['facts']['kv_observations']
    assert facts == [{'seq': 0, 'kv_units': 3, 'kv_unit': unit, 'kv_unit_tokens': tokens}]


def test_unknown_kv_unit_clears_the_numeric_claim_and_keeps_a_gap():
    value = v4.encode(record([row(0, 'kv_owned', kv_blocks=3)]), wire_schema=v4.SCHEMA)
    assert value['events'][0]['kv_units'] is None and value['events'][0]['kv_unit'] is None
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:KV_UNIT_UNOBSERVED' in validate(value)['gaps']


@pytest.mark.parametrize('field,value', [('kv_unit', '추정 단위'), ('kv_unit', []), ('kv_unit_tokens', 0),
    ('kv_unit_tokens', True), ('kv_units', True), ('enforcement_method', '추정 집행')])
def test_invalid_units_and_enforcement_methods_are_rejected(field, value):
    data = encode([row(0, 'limit_enforced')])
    data['events'][0][field] = value
    with pytest.raises(common.GpuInputError):
        validate(data)


def test_new_capture_emits_refusal_and_preemption_as_explicit_enforcement():
    rows = lifecycle(preempt=True)
    refusal = deepcopy(rows[2]); refusal['fields']['allocated'] = False
    rows.insert(3, refusal)
    value, result = producers.request_attempt_record([{'pid': 74, 'start_ticks': 1000, 'events': rows}],
        pod=POD, wire_schema=v4.SCHEMA, **SCOPE)
    limits = [r['enforcement_method'] for r in value['events'] if r['event'] == 'limit_enforced']
    assert limits == ['alloc_refused', 'preempted']
    assert next(r for r in value['events'] if r['event'] == 'retry')['next_attempt'] == 2
    assert result['supported'] is False and result['complete'] is False
    value['events'][next(i for i,r in enumerate(value['events']) if r['event']=='limit_enforced')]['enforcement_method'] = None
    assert 'GPU_INPUT_LIMIT_ENFORCEMENT_METHOD_UNOBSERVED' in validate(value)['gaps']


def _runner_values(bias):
    fields = {key: 0 for key in common.KINDS['runner-consumption']['fields']}
    fields.update(request_sha256='1'*64, stage=None, logit_bias=bias)
    return fields


@pytest.mark.parametrize('observed,changed,gap', [(2,False,False), (1,True,False), (None,False,True)])
def test_v4_compares_only_observed_sampler_logit_bias_and_preserves_v1(observed, changed, gap):
    events = [dict(_runner_values(2), seq=0, event='api_validated', binding=0),
              dict(_runner_values(2), seq=1, event='runner_received', binding=0),
              dict(_runner_values(observed), seq=2, event='sampler_applied', binding=0)]
    original = record(events, kind='runner-consumption')
    old = validate(original)
    assert 'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias' in old['gaps']
    assert not any('logit_bias' in c.get('fields', []) for c in old['violation_candidates'])
    value = v4.encode(original, wire_schema=v4.SCHEMA)
    result = validate(value)
    assert any('logit_bias' in c.get('fields', []) for c in result['violation_candidates']) is changed
    assert ('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias' in result['gaps']) is gap
    assert not result['complete'] and not result['producer_authenticated']


def test_old_registration_is_not_inherited_and_shared_wrapper_bytes_are_in_identity(monkeypatch):
    functions = [producers.attempt_producer, producers.transition_producer, producers.health_producer,
                 producers.profiler_producer, producers.consumption_producer, producers.supply_producer, v2.producer]
    before = [make() for make in functions]
    for producer in before:
        assert common.SUPPORTED_PRODUCERS[(producer['name'], producer['version'])] != producer['source_sha256']
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p.name == 'vllm_cache_callback.py' else b''))
    assert all(make()['source_sha256'] != old['source_sha256'] for make, old in zip(functions, before))


def test_registering_a_receipt_does_not_recursively_change_the_producer_digest(monkeypatch):
    before = producers.attempt_producer()
    original = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: original(p) + (b'\n' if p.name == 'gpu_common_inputs.py' else b''))
    assert producers.attempt_producer() == before


def test_legacy_record_and_new_record_have_the_same_complete_scope_only_with_mock_registration(monkeypatch):
    old = record(COMPLETE)
    producer = old['producer']
    monkeypatch.setitem(common.SUPPORTED_PRODUCERS, (producer['name'],producer['version']), producer['source_sha256'])
    new = encode(COMPLETE)
    assert validate(old)['complete']
    assert not validate(new)['supported'] and not validate(new)['complete']
    monkeypatch.setitem(common.SUPPORTED_V4_PRODUCERS, (producer['name'],producer['version']), producer['source_sha256'])
    assert validate(new)['complete']
    old_facts = consume_record('LIF-03', old, **SCOPE)
    new_facts = consume_record('LIF-03', new, **SCOPE)
    assert old_facts['facts']['event_counts'] == new_facts['facts']['event_counts']
    assert not new_facts['whole_question_complete'] and not new_facts['producer_authenticated']


@pytest.mark.parametrize('bias,changed,gap', [(1,False,False),(0,True,False),(None,False,True)])
def test_v2_source_callback_observation_reaches_v4_comparison(tmp_path, monkeypatch, bias, changed, gap):
    from tests.test_runner_consumption_v2 import engine, Runner, new_request, params, batch, _api_process, TAG, v1
    callbacks, runner, rows = engine(tmp_path, Runner())
    runner.add_requests(SimpleNamespace(scheduled_new_reqs=[new_request('cmpl-'+TAG+'-0', params())]))
    runner.sampler.logits_processors[0].num_logit_bias.np[0] = bias
    runner.sample([[0.0]], batch([0]), None)
    received = next(r for r in rows if r.get('point') == 'GPUModelRunner.add_requests' and r['phase'] == 'CALL')['fields']['received'][0]
    processes = [_api_process(received['request_sha256'], v1.params_summary(params())),
                 {'pid':40,'start_ticks':20,'events':rows}]
    value, result = v2.runner_consumption_v2_record(processes, pod=POD, wire_schema=v4.SCHEMA, **SCOPE)
    assert value['schema'] == v4.SCHEMA
    assert any('logit_bias' in c.get('fields',[]) for c in result['violation_candidates']) is changed
    assert ('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias' in result['gaps']) is gap
    assert not result['supported'] and not result['complete']
    from guard.executors.common_consumption import runner as consume_runner
    requests = {'normal': received['request_sha256']}
    output = consume_runner(value, SCOPE, check_id='REQ-10', requests=requests, required_cases=['normal'])
    assert not output['supported'] and not output['complete']
    source = value['producer']
    # 이 사례 안에서만 새 wire 등록 후 소비 분기를 모의한다. 실제 GPU 등록은 없다.
    monkeypatch.setitem(common.SUPPORTED_V4_PRODUCERS, (source['name'],source['version']),source['source_sha256'])
    output = consume_runner(value, SCOPE, check_id='REQ-10', requests=requests, required_cases=['normal'])
    assert output['supported']
    assert not output['producer_authenticated'] and not output['whole_worker_scope_verified']
    assert ('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias' in output['gaps']) is gap


@pytest.mark.parametrize('kind,check', [('generation-transition','LIF-06'),('health-worker-causal','OBS-02')])
def test_transition_and_health_summaries_keep_causal_and_generation_keys(kind, check):
    if kind == 'generation-transition':
        fields = dict(request_sha256='1'*64, generation='weights-1', component='weights', state='scheduled',
                      error_sha256=None, alias_sha256=None)
        event = 'consumed'
    else:
        fields = dict(generation='worker-1', causal_sha256='2'*64, state='output')
        event = 'worker_state'
    events = [dict(seq=i,event=event,binding=0,at_ms=i,**fields) for i in range(100)]
    value = v4.encode(record(events,kind=kind),wire_schema=v4.SCHEMA)
    assert len(value['events']) == 3 and validate(value)['source_events'] == 100
    assert consume_record(check,value,**SCOPE)['facts']['event_counts'][event] == 100
    if kind == 'health-worker-causal':
        events[50]['causal_sha256'] = '3'*64
    else:
        events[50]['generation'] = 'weights-2'
    changed = v4.encode(record(events,kind=kind),wire_schema=v4.SCHEMA)
    assert any(r['source_seq']==50 and r['repeat'] is None for r in changed['events'])
