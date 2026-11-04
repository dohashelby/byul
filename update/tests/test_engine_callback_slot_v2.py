"""REQ-18 V2 runner 자리 재사용 수집: 실제 vLLM 0.31.0 함수 코드를 감싸 만든 행을 투영에 넣는다."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.executors import engine_callback_projection as projection
from tests.test_cache_callback import source_module

SECRET = bytes(range(32))
TAGS = {case: 'byul-cb-' + str(n) * 32 for n, case in enumerate('abcd', 1)}
CASES = [{'case_id': 'a', 'role': 'standalone', 'prompt': 'x'}, {'case_id': 'b', 'role': 'standalone', 'prompt': 'x'},
         {'case_id': 'c', 'role': 'restricted', 'prompt': 'y', 'allowed_token_ids': [1]}, {'case_id': 'd', 'role': 'reused', 'prompt': 'x'}]


class Staged:
    def stage_write(self, *args): pass
    def stage_write_elem(self, *args): pass


class Row:
    def zero_(self): pass


class LogitBias:
    """LogitBiasState.add_request의 허용 개수 갱신만 흉내 낸다. reset=False는 결함 엔진."""
    def __init__(self, size, *, reset=True):
        self.num_allowed_token_ids = SimpleNamespace(np=[0] * size)
        self.use_logit_bias = [False] * size
        self.reset = reset

    def add_request(self, slot, params):
        if params.allowed_token_ids:
            self.num_allowed_token_ids.np[slot] = len(params.allowed_token_ids); self.use_logit_bias[slot] = True
        elif self.reset:
            self.num_allowed_token_ids.np[slot] = 0; self.use_logit_bias[slot] = False
        return self.use_logit_bias[slot]

    def __getattr__(self, name):  # apply_staged_writes 등 다른 호출은 하는 일이 없다.
        if name.startswith('__'):
            raise AttributeError(name)
        return lambda *a, **k: False


def engine(tmp_path, *, reset=True, size=4):
    states_module = source_module(sensor.SLOT_V2_STATES, tmp_path, targets=sensor.SLOT_V2_TARGETS, sources=sensor.SLOT_V2_SOURCE)
    sampler_module = source_module(sensor.SLOT_V2_SAMPLER, tmp_path, targets=sensor.SLOT_V2_TARGETS, sources=sensor.SLOT_V2_SOURCE)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True})
    assert callbacks.instrument(states_module) and callbacks.instrument(sampler_module)
    states = states_module.RequestState()
    vars(states).update(free_indices=list(range(size))[::-1], req_id_to_index={}, index_to_req_id={},
        max_seq_len=[0] * size, prompt_len=SimpleNamespace(np=[0] * size), prefill_len=SimpleNamespace(np=[0] * size),
        total_len=Staged(), all_token_ids=Staged(), num_computed_prefill_tokens=[0] * size,
        num_computed_tokens_np=[0] * size, num_computed_tokens=Staged(), draft_tokens=[Row() for _ in range(size)])
    sampler = sampler_module.Sampler()
    bias = LogitBias(size, reset=reset)
    nothing = SimpleNamespace(add_request=lambda *a: False)
    vars(sampler).update(req_states=states, sampling_states=nothing, thinking_budget_state=nothing,
        logits_processors=[bias], needs_logits_processing=[False] * size, logprob_token_ids_state=nothing, trace_replay_state=None)
    return states, sampler, rows


def serve(states, sampler, case):
    """한 요청: 자리 배정 → sampler 등록 → 샘플링 진입 → 회수. 엔진 req_id에는 접두·접미가 붙는다."""
    req_id = 'cmpl-' + TAGS[case['case_id']] + '-0'
    states.add_request(req_id, 1, [7], 0, 1)
    slot = states.req_id_to_index[req_id]
    sampler.add_request(slot, SimpleNamespace(allowed_token_ids=case.get('allowed_token_ids')))
    batch = SimpleNamespace(idx_mapping_np=[slot], expanded_idx_mapping=None, idx_mapping=None, cu_num_logits_np=None,
        expanded_local_pos=None, positions=None, logits_indices=None, input_ids=None, seq_lens_cpu_upper_bound=None)
    with pytest.raises(TypeError):  # 대역 batch에는 GPU tensor가 없어 원 코드가 진입 뒤 멈춘다.
        sampler(None, batch)
    assert states.remove_request(req_id) == slot


def run(tmp_path, outputs, *, reset=True):
    states, sampler, rows = engine(tmp_path, reset=reset)
    for case in CASES:
        serve(states, sampler, case)
    return projection.project_slot(CASES, outputs, rows, SECRET, TAGS), rows


def test_real_v2_code_reuses_the_slot_and_clean_state_completes_scope(tmp_path):
    result, rows = run(tmp_path, {'a': 'o', 'b': 'o', 'd': 'o'})
    assert result['runner'] == 'v2'
    assert result['rows'] == [{'slot_reused': True, 'output_matches_standalone': True,
                               'sampler_state_observed': True, 'stale_restriction_at_sampling': False}]
    assert result['violation'] is False and result['full_scope'] is True
    assert all(r['entry_qualified_before'] for r in rows if r['phase'] == 'CALL')
    # 허용 token 값 자체는 남지 않고 개수·플래그만 남는다.
    assert all(set(r['fields']) <= {'slot', 'request_sha256', 'restricted', 'allowed_token_count', 'logit_bias_in_use',
                                    'needs_logits_processing'} for r in rows if r['point'] == 'Sampler.add_request')


def test_stale_restriction_at_sampling_is_a_violation_even_when_outputs_match(tmp_path):
    result, _ = run(tmp_path, {'a': 'o', 'b': 'o', 'd': 'o'}, reset=False)
    assert result['rows'][0]['stale_restriction_at_sampling'] is True
    assert result['violation'] is True and result['full_scope'] is False


def test_stale_restriction_stays_a_violation_when_controls_are_unstable(tmp_path):
    result, _ = run(tmp_path, {'a': 'o', 'b': 'p', 'd': 'q'}, reset=False)
    assert 'standalone_control_not_deterministic' in result['missing'] and result['violation'] is True


def test_changed_output_on_reused_slot_with_clean_state_is_still_a_violation(tmp_path):
    result, _ = run(tmp_path, {'a': 'o', 'b': 'o', 'd': 'other'})
    assert result['violation'] is True


def test_missing_sampler_rows_leave_v2_scope_uncollected(tmp_path):
    _, rows = run(tmp_path, {'a': 'o', 'b': 'o', 'd': 'o'})
    kept = [r for r in rows if r['point'] != 'Sampler.__call__']
    result = projection.project_slot(CASES, {'a': 'o', 'b': 'o', 'd': 'o'}, kept, SECRET, TAGS)
    assert result['violation'] is None and 'sampler_state_not_observed' in result['missing']


def test_unqualified_entry_rows_are_not_evidence(tmp_path):
    _, rows = run(tmp_path, {'a': 'o', 'b': 'o', 'd': 'o'}, reset=False)
    for row in rows:
        if row['point'] == 'Sampler.__call__':
            row['entry_qualified_before'] = False
    result = projection.project_slot(CASES, {'a': 'o', 'b': 'o', 'd': 'o'}, rows, SECRET, TAGS)
    assert result['violation'] is None


def test_changed_source_is_refused_and_the_module_stays_unwrapped(tmp_path):
    module = source_module(sensor.SLOT_V2_STATES, tmp_path, targets=sensor.SLOT_V2_TARGETS, sources=sensor.SLOT_V2_SOURCE)
    original = module.RequestState.add_request
    (tmp_path / 'states.py').write_bytes(b'# changed\n')
    gaps = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, gaps.append, context_provider=lambda: {'fixture': True})
    assert callbacks.instrument(module) is False
    assert module.RequestState.add_request is original
    assert gaps and gaps[0]['code'] == 'ENGINE_CALLBACK_INSTALL_UNAVAILABLE'


def test_oversized_or_non_integer_sampled_slots_record_a_gap():
    observer = sensor.SlotV2Observer('a' * 32, sink=[].append, context_provider=lambda: {})
    batch = SimpleNamespace(idx_mapping_np=[0.5])
    assert observer._snapshot('Sampler.__call__', (None, None, batch), {}) == {'sampled': None}
    assert 'SLOT_V2_SAMPLED_SLOTS_UNAVAILABLE' in observer.gaps


def test_v1_rows_keep_the_previous_projection():
    events = [{'point': 'InputBatch.add_request', 'phase': 'RETURN', 'entry_qualified_before': True, 'entry_qualified_after': True,
               'fields': {'request_sha256': projection.tag_digest(SECRET, TAGS[c]), 'slot': 0, 'restricted': c == 'c'}} for c in 'cd']
    result = projection.project_slot(CASES, {'a': 'o', 'b': 'o', 'd': 'o'}, events, SECRET, TAGS)
    assert result['runner'] == 'v1' and result['full_scope'] is True and 'sampler_state_observed' not in result['rows'][0]
