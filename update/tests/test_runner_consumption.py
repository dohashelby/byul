"""P2 runner metadata: real producer output, missing reads, and later changes."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_consumption_callback as callback
from guard.collectors import gpu_common_inputs as contract
from guard.collectors import gpu_input_producers as producers

from tests.test_gpu_input_producers import POD, R1, R2, SCOPE, api_rows, crow, PARAMS


def observer():
    value = callback.ConsumptionObserver.__new__(callback.ConsumptionObserver)
    value.gaps = set()
    value.fingerprint = lambda request: {'request-1': R1, 'request-2': R2}.get(request)
    return value


def batch(**overrides):
    return SimpleNamespace(**{
        '_req_ids': ['request-1'], 'vocab_size': 100,
        'temperature_cpu': [0.7], 'top_p_cpu': [0.9], 'top_k_cpu': [5],
        'num_logprobs': {'request-1': 2}, 'bad_words_token_ids': {0: [[5], [6]]},
        'allowed_token_ids_mask_cpu_tensor': None, 'has_allowed_token_ids': set(), **overrides,
    })


def worker_rows(**values):
    return [crow(10, 'GPUModelRunner._update_states', 'CALL', received=[{
                'request_sha256': R1, 'prompt_tokens': 5, 'max_token_id': 99, 'params': PARAMS}]),
            crow(11, 'GPUModelRunner._sample', 'CALL', applied=[{
                'request_sha256': R1, 'temperature_milli': 0, 'top_p_milli': 1000,
                'top_k': 0, 'logprobs': 0, 'allowed_tokens': 3, 'bad_words': 2, **values}])]


def produce(worker, api=None, **options):
    return producers.runner_consumption_record([
        {'pid': 1, 'start_ticks': 1, 'events': api_rows() if api is None else api},
        {'pid': 112, 'start_ticks': 2, 'events': worker}], pod=POD, **SCOPE, **options)


def register(monkeypatch, record):
    producer = record['producer']
    monkeypatch.setitem(contract.SUPPORTED_PRODUCERS, (producer['name'], producer['version']), producer['source_sha256'])


def test_sampler_reads_fields_through_the_native_fields_accessor():
    rows = observer()._snapshot('GPUModelRunner._sample', (SimpleNamespace(input_batch=batch()),), {})['applied']
    assert rows == [{'request_sha256': R1, 'temperature_milli': 700, 'top_p_milli': 900, 'top_k': 5,
                     'logprobs': 2, 'allowed_tokens': 0, 'bad_words': 2}]


@pytest.mark.parametrize(('stored', 'expected'), [(5, 5), (100, 0), (None, None)])
def test_batch_top_k_distinguishes_disabled_from_unread(stored, expected):
    rows = observer()._batch(SimpleNamespace(input_batch=batch(top_k_cpu=[stored])))
    assert rows[0]['top_k'] == expected


def test_missing_cpu_arrays_stay_null_and_never_become_disabled_top_k():
    obs = observer()
    rows = obs._batch(SimpleNamespace(input_batch=batch(temperature_cpu=None, top_p_cpu=[], top_k_cpu=None)))
    assert all(rows[0][key] is None for key in ('temperature_milli', 'top_p_milli', 'top_k'))


def test_retained_allowed_token_mask_does_not_restrict_a_later_unrestricted_request():
    class Row:
        def __init__(self, count):
            self.count = count
        def __invert__(self):
            return self
        def sum(self):
            return self
        def item(self):
            return self.count

    mask = [Row(100)]
    obs = observer()
    assert obs._batch(SimpleNamespace(input_batch=batch(allowed_token_ids_mask_cpu_tensor=mask)))[0]['allowed_tokens'] == 0
    active = batch(allowed_token_ids_mask_cpu_tensor=[Row(3)], has_allowed_token_ids={'request-1'})
    assert obs._batch(SimpleNamespace(input_batch=active))[0]['allowed_tokens'] == 3
    missing = batch(has_allowed_token_ids={'request-1'})
    assert obs._batch(SimpleNamespace(input_batch=missing))[0]['allowed_tokens'] is None


def test_duplicate_allowed_ids_and_unbounded_logprobs_are_not_misreported():
    params = SimpleNamespace(temperature=0.7, top_p=0.9, top_k=-1, max_tokens=8, logprobs=-1,
                             allowed_token_ids=[1, 1, 2], bad_words=[], logit_bias=None)
    summary = callback.params_summary(params)
    assert summary['allowed_tokens'] == 2 and summary['top_k'] == 0 and summary['logprobs'] is None


def test_real_producer_record_is_consumable_but_unregistered_until_gpu_review(monkeypatch):
    monkeypatch.setattr(contract, 'SUPPORTED_PRODUCERS', {})  # 등록 전 상태를 따로 확인한다
    record, result = produce(worker_rows())
    assert result['gaps'] == ['GPU_INPUT_PRODUCER_UNSUPPORTED'] and result['complete'] is False
    register(monkeypatch, record)
    result = contract.validate(record, kind='runner-consumption', **SCOPE)
    assert result['complete'] is True and result['producer_authenticated'] is False


@pytest.mark.parametrize('field', ['temperature_milli', 'top_p_milli', 'top_k', 'allowed_tokens', 'bad_words'])
def test_unread_sampler_field_is_a_gap_even_for_a_supported_producer(monkeypatch, field):
    record, result = produce(worker_rows(**{field: None}))
    assert result['violation_candidates'] == []
    register(monkeypatch, record)
    result = contract.validate(record, kind='runner-consumption', **SCOPE)
    assert result['complete'] is False
    assert result['gaps'] == [f'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:{field}']


def test_each_validated_request_needs_both_runner_and_sampler_stages(monkeypatch):
    api = [*api_rows(), *api_rows(R2)]
    record, _ = produce(worker_rows(), api)
    register(monkeypatch, record)
    result = contract.validate(record, kind='runner-consumption', **SCOPE)
    assert record['completeness']['source_ended'] is False and result['complete'] is False
    assert {'GPU_INPUT_CONSUMPTION_STAGE_MISSING:runner_received',
            'GPU_INPUT_CONSUMPTION_STAGE_MISSING:sampler_applied'} <= set(result['gaps'])


def test_later_sampler_change_survives_deduplication_and_truncation():
    rows = worker_rows()
    same = deepcopy(rows[-1]); same['operation_started_ns'] = 12
    changed = deepcopy(rows[-1]); changed['operation_started_ns'] = 13
    changed['fields']['applied'][0]['allowed_tokens'] = 0
    record, result = produce([*rows, same, changed], truncated=True)
    samples = [event for event in record['events'] if event['event'] == 'sampler_applied']
    assert [event['stage'] for event in samples] == ['first_sample', 'sample_changed']
    assert result['violation_candidates'][0]['fields'] == ['allowed_tokens']
    assert 'GPU_INPUT_SOURCE_INCOMPLETE' in result['gaps']


def test_unseen_selected_request_is_a_diagnostic():
    record, result = produce(worker_rows(), requests={R1, R2})
    assert record['completeness']['diagnostic_codes'] == ['CONSUMPTION_SELECTED_REQUEST_UNOBSERVED']
    assert 'GPU_INPUT_PRODUCER_DIAGNOSTIC:CONSUMPTION_SELECTED_REQUEST_UNOBSERVED' in result['gaps']


def test_nondefault_logit_bias_needs_a_sampler_observation():
    api = deepcopy(api_rows())
    api[1]['fields']['params']['logit_bias'] = 1
    worker = worker_rows()
    worker[0]['fields']['received'][0]['params'] = {**PARAMS, 'logit_bias': 1}
    _, result = produce(worker, api)
    assert 'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias' in result['gaps']


def test_unrequested_bad_words_at_the_sampler_are_a_candidate():
    api = deepcopy(api_rows())
    api[1]['fields']['params']['bad_words'] = 0
    worker = worker_rows()
    worker[0]['fields']['received'][0]['params'] = {**PARAMS, 'bad_words': 0}
    _, result = produce(worker, api)
    assert result['violation_candidates'][0]['fields'] == ['bad_words']
