"""P2 runner-consumption V2 runner 경로: 실제 vLLM 0.31.0 V2 `model_runner.py` 함수 코드를 감싸 만든 행 → 계약 기록."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from guard.adapters import vllm_consumption_callback as v1
from guard.adapters import vllm_consumption_v2_callback as v2
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.collectors import runner_consumption_v2_producer as producer
from tests.test_cache_callback import source_module

SECRET = bytes(range(32))
TAG = 'byul-cb-' + '1' * 32
ENV = {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'a' * 64}
SCOPE = {'environment_ref': ENV, 'target_sha256': 'b' * 64, 'prepared_sha256': 'c' * 64, 'session': 'd' * 32}
POD = {'pod_uid': '10000000-0000-0000-0000-000000000001', 'container_id': 'containerd://' + 'e' * 64, 'restart_count': 0}
VOCAB = 1000


def arr(size, fill=0):
    return SimpleNamespace(np=[fill] * size)


def params(**values):
    base = {'temperature': 0.7, 'top_p': 0.9, 'top_k': 5, 'max_tokens': 8, 'logprobs': 2, 'allowed_token_ids': [3, 4, 4],
            'bad_words': ['x', 'y'], 'logit_bias': {7: 1.0}}
    base.update(values)
    return SimpleNamespace(**base)


class Runner:
    """V2 GPUModelRunner의 add_requests·sample이 부르는 속성만 흉내 낸다. sampler 상태는 vLLM처럼 자리별 배열이다."""
    def __init__(self, size=4, *, drop_bias=False):
        self.size = size; self.drop_bias = drop_bias
        self.req_states = SimpleNamespace(req_id_to_index={}, index_to_req_id={}, add_request=self._add_state,
                                          apply_staged_writes=lambda: None)
        states = SimpleNamespace(vocab_size=VOCAB, temperature=arr(size, 0.0), top_p=arr(size, 1.0), top_k=arr(size, VOCAB),
                                 num_logprobs=[-1] * size)
        self.bias = SimpleNamespace(num_allowed_token_ids=arr(size), num_logit_bias=arr(size))
        self.bad = SimpleNamespace(num_bad_words=arr(size))
        self.sampler = SimpleNamespace(sampling_states=states, logits_processors=[self.bias, object(), self.bad],
                                       add_request=self._add_sampler, apply_staged_writes=lambda: None)
        self.adaptive_verification = self.pooling_runner = self.encoder_cache = self.batch_sharder = None
        self.rejection_sampler = None
        self.model_state = SimpleNamespace(add_request=lambda *a: None, apply_staged_writes=lambda: None)
        self.block_tables = SimpleNamespace(append_block_ids=lambda *a, **k: None)
        self.lora_state = SimpleNamespace(add_request=lambda *a: None)
        self.prompt_logprobs_worker = SimpleNamespace(add_request=lambda *a: None)
        self.is_last_pp_rank = True
        self.model = SimpleNamespace(compute_logits=lambda hidden: 'logits')

    def _remove_request(self, req_id):
        return False

    def _add_state(self, *, req_id, **_):
        slot = len(self.req_states.req_id_to_index)
        self.req_states.req_id_to_index[req_id] = slot; self.req_states.index_to_req_id[slot] = req_id

    def _add_sampler(self, slot, p):
        s = self.sampler.sampling_states
        s.temperature.np[slot] = p.temperature; s.top_p.np[slot] = p.top_p
        s.top_k.np[slot] = VOCAB if p.top_k <= 0 else p.top_k
        s.num_logprobs[slot] = -1 if p.logprobs is None else VOCAB if p.logprobs == -1 else p.logprobs
        self.bias.num_allowed_token_ids.np[slot] = 0 if self.drop_bias else len(p.allowed_token_ids or ())
        self.bias.num_logit_bias.np[slot] = len(p.logit_bias or {})
        self.bad.num_bad_words.np[slot] = len(p.bad_words or ())


class Sampled:
    num_sampled = 'n'; num_rejected = 'r'


def engine(tmp_path, runner):
    module = source_module(v2.RUNNER, tmp_path, targets=v2.TARGETS, sources=v2.SOURCES)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True}, runner_consumption=True)
    assert callbacks.instrument(module)
    cls = module.GPUModelRunner
    instance = cls.__new__(cls); vars(instance).update(vars(runner)); instance._remove_request = runner._remove_request
    # sample()은 self.sampler(logits, input_batch)를 부른다. 같은 상태 배열을 가진 호출 가능한 객체로 바꾼다.
    class CallableSampler(SimpleNamespace):
        def __call__(self, logits, batch):
            return Sampled()
    instance.sampler = CallableSampler(**vars(runner.sampler))
    return callbacks, instance, rows


def new_request(req_id, p, prompt=(1, 2, 9)):
    return SimpleNamespace(req_id=req_id, prompt_token_ids=list(prompt), prefill_token_ids=list(prompt), prompt_len=len(prompt),
                           num_computed_tokens=0, sampling_params=p, pooling_params=None, mm_features=[], block_ids=([],),
                           lora_request=None)


def batch(slots):
    return SimpleNamespace(idx_mapping_np=list(slots), logits_indices=slice(None), num_reqs=len(slots), num_draft_tokens=0)


def test_real_v2_add_requests_and_sample_are_wrapped_and_read_slot_arrays(tmp_path):
    callbacks, runner, rows = engine(tmp_path, Runner())
    request = 'cmpl-' + TAG + '-0'
    runner.add_requests(SimpleNamespace(scheduled_new_reqs=[new_request(request, params())]))
    out = runner.sample([[0.0]], batch([0]), None)
    assert out[1:] == ('n', 'r')
    mine = [r for r in rows if r.get('schema') == v2.SCHEMA]
    assert {r['point'] for r in mine} == {'GPUModelRunner.add_requests', 'GPUModelRunner.sample'}
    assert all(r['entry_qualified_before'] for r in mine)
    received = next(r for r in mine if r['point'] == 'GPUModelRunner.add_requests' and r['phase'] == 'CALL')['fields']['received'][0]
    applied = next(r for r in mine if r['point'] == 'GPUModelRunner.sample' and r['phase'] == 'CALL')['fields']['applied'][0]
    assert received['prompt_tokens'] == 3 and received['max_token_id'] == 9 and received['params']['allowed_tokens'] == 2
    assert applied['request_sha256'] == received['request_sha256']
    assert applied == {'request_sha256': received['request_sha256'], 'temperature_milli': 700, 'top_p_milli': 900, 'top_k': 5,
                       'logprobs': 2, 'allowed_tokens': 2, 'bad_words': 2, 'logit_bias': 1}
    assert request not in str(rows) and TAG not in str(rows)


def _api_process(fingerprint, values):
    def row(point, phase, seq, **fields):
        # process_inputs(c1)가 두 검증을 감싼다: 그 RETURN은 검증들보다 늦게 끝난다.
        finished = 9_000_000 if seq == 1 else seq * 1_000_000 + 1
        return {'schema': v1.SCHEMA, 'point': point, 'phase': phase, 'entry_qualified_before': True, 'entry_qualified_after': True,
                'process_sha256': 'p', 'call_sha256': f'c{seq}', 'operation_started_ns': seq * 1_000_000,
                'operation_finished_ns': finished, 'fields': fields}
    return {'pid': 1, 'start_ticks': 10, 'events': [
        row('InputProcessor.process_inputs', 'CALL', 1, request_sha256=fingerprint),
        row('InputProcessor._validate_model_input', 'CALL', 2, request_sha256=fingerprint, prompt_tokens=3, max_token_id=9),
        row('InputProcessor._validate_model_input', 'RETURN', 2),
        row('InputProcessor._validate_params', 'CALL', 3, request_sha256=fingerprint, params=values),
        row('InputProcessor._validate_params', 'RETURN', 3),
        row('InputProcessor.process_inputs', 'RETURN', 1)]}


def test_v2_record_matches_api_values_including_logit_bias(tmp_path):
    callbacks, runner, rows = engine(tmp_path, Runner())
    runner.add_requests(SimpleNamespace(scheduled_new_reqs=[new_request('cmpl-' + TAG + '-0', params())]))
    runner.sample([[0.0]], batch([0]), None)
    received = next(r for r in rows if r.get('point') == 'GPUModelRunner.add_requests' and r['phase'] == 'CALL')['fields']['received'][0]
    processes = [_api_process(received['request_sha256'], v1.params_summary(params())),
                 {'pid': 40, 'start_ticks': 20, 'events': rows}]
    record, result = producer.runner_consumption_v2_record(processes, pod=POD, **SCOPE)
    assert [e['event'] for e in record['events']] == ['api_validated', 'runner_received', 'sampler_applied']
    assert record['events'][2]['logit_bias'] == 1 and record['completeness']['source_ended'] is True
    # V2 sampler 단계의 logit bias 개수는 기록에 있다. 공통 계약은 V1 한계로 이 gap을 무조건 붙인다(계약 변경은 별도 결정).
    assert result['violation_candidates'] == []
    assert set(result['gaps']) == {'GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:sampler_applied:logit_bias', 'GPU_INPUT_PRODUCER_UNSUPPORTED'}
    assert record['producer']['name'] == 'guard-engine-callback-runner-consumption-v2'


def test_lost_allowed_list_at_the_sampler_is_a_candidate_not_hidden_by_dedup(tmp_path):
    callbacks, runner, rows = engine(tmp_path, Runner(drop_bias=True))
    runner.add_requests(SimpleNamespace(scheduled_new_reqs=[new_request('cmpl-' + TAG + '-0', params())]))
    runner.sample([[0.0]], batch([0]), None)
    received = next(r for r in rows if r.get('point') == 'GPUModelRunner.add_requests' and r['phase'] == 'CALL')['fields']['received'][0]
    processes = [_api_process(received['request_sha256'], v1.params_summary(params())), {'pid': 40, 'start_ticks': 20, 'events': rows}]
    record, result = producer.runner_consumption_v2_record(processes, pod=POD, **SCOPE)
    assert record['events'][2]['allowed_tokens'] == 0
    assert result['violation_candidates']


def test_mixed_v1_runner_rows_are_dropped_and_missing_v2_rows_are_diagnosed():
    fingerprint = '1' * 64
    stray = {'schema': v1.SCHEMA, 'point': 'GPUModelRunner._sample', 'phase': 'CALL', 'entry_qualified_before': True,
             'process_sha256': 'q', 'call_sha256': 'x', 'operation_started_ns': 9_000_000, 'fields': {'applied': []}}
    processes = [_api_process(fingerprint, v1.params_summary(params())), {'pid': 40, 'start_ticks': 20, 'events': [stray]}]
    record, result = producer.runner_consumption_v2_record(processes, pod=POD, **SCOPE)
    codes = record['completeness']['diagnostic_codes']
    assert 'CONSUMPTION_V1_RUNNER_ROWS_PRESENT' in codes and 'CONSUMPTION_V2_RUNNER_UNOBSERVED' in codes
    assert record['completeness']['source_ended'] is False


def test_sessions_without_runner_consumption_leave_the_v2_runner_untouched(tmp_path):
    module = source_module(v2.RUNNER, tmp_path, targets=v2.TARGETS, sources=v2.SOURCES)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True}, runner_consumption=False)
    original = vars(module.GPUModelRunner)['add_requests']
    assert callbacks.instrument(module) is False and vars(module.GPUModelRunner)['add_requests'] is original


@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf'), 2.5, 2**63])
def test_invalid_sampler_count_stays_unread_without_losing_the_other_fields(tmp_path, number):
    callbacks, runner, rows = engine(tmp_path, Runner())
    runner.add_requests(SimpleNamespace(scheduled_new_reqs=[new_request('cmpl-' + TAG + '-0', params())]))
    runner.sampler.sampling_states.top_k.np[0] = number
    runner.sample([[0.0]], batch([0]), None)
    applied = next(r for r in rows if r.get('point') == 'GPUModelRunner.sample' and r['phase'] == 'CALL')['fields']['applied'][0]
    assert applied['top_k'] is None and applied['temperature_milli'] == 700
