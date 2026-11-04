"""REQ-19 kernel 범위 수집: 실제 vLLM 0.31.0 입력 준비 함수 코드를 감싸 만든 행을 투영에 넣는다.

Triton kernel은 GPU가 필요하므로 원 kernel의 식을 그대로 옮긴 대역 kernel을 쓴다.
대역은 결함판(padding 미초기화, 잘못된 token 복사)도 만들어 판정 경계를 확인한다.
"""
from __future__ import annotations

from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_kernel_bounds_callback as kb
from guard.executors import engine_callback_projection as projection
from guard.executors import engine_sensor
from guard.models import sha256_hex
from tests.test_engine_callback_slot_v2 import Row, Staged
from tests.test_cache_callback import source_module

FIXTURES = Path(__file__).parent / 'fixtures/cache-callback-native'
SECRET = bytes(range(32))


class T:
    """1·2차원 정수 목록 tensor 대역. kernel 대역이 쓰고 관측기가 읽는 연산만 있다."""
    def __init__(self, data):
        self.data = data
        self.device = SimpleNamespace(type='cpu')

    @property
    def shape(self):
        return (len(self.data),) if not self.data or type(self.data[0]) is not list else (len(self.data), len(self.data[0]))

    def dim(self): return len(self.shape)
    def numel(self): return len(self.data) if self.dim() == 1 else len(self.data) * len(self.data[0])
    def stride(self, axis): return len(self.data[0]) if self.dim() == 2 and axis == 0 else 1
    def detach(self): return self
    def cpu(self): return self
    def tolist(self): return [list(r) for r in self.data] if self.dim() == 2 else list(self.data)

    def __getitem__(self, key):
        if type(key) is tuple:
            row, cols = key
            return T(self.data[row][cols])
        return T(self.data[key]) if type(key) is slice else self.data[key]


def module_functions(name, tmp_path, kernels):
    raw = (FIXTURES / 'input_batch.py.txt').read_bytes()
    assert sha256_hex(raw) == kb.SOURCES[name]
    path = tmp_path / 'input_batch.py'; path.write_bytes(raw)
    module = ModuleType(name); module.__file__ = str(path)
    codes = {}
    def visit(code):
        codes[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType: visit(child)
    visit(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point in kb.TARGETS[name]:
        setattr(module, point, FunctionType(codes[point], vars(module)))
    vars(module).update(kernels, triton=SimpleNamespace(next_power_of_2=lambda n: 1 << max(0, (n - 1).bit_length())))
    return module


class Launch:
    def __init__(self, body): self.body = body
    def __getitem__(self, grid): return lambda *args, **kw: self.body(grid, *args, **kw)


def prefill_kernel(grid, input_ids, nxt, stride, num_lookahead, idx, qsl, tokens, row_stride, prefill, computed, *, BLOCK_SIZE, LOOKAHEAD_BLOCK, wrong=False):
    for b in range(grid[0]):
        slot = idx.data[b]
        if computed.data[slot] >= prefill.data[slot]:
            continue
        start, end = qsl.data[b], qsl.data[b + 1]
        for i in range(end - start):
            if start + i < len(input_ids.data):  # 대역은 범위 밖에 쓰지 않는다. 범위는 관측기가 따로 계산한다.
                input_ids.data[start + i] = tokens.data[slot][computed.data[slot] + i] + (1 if wrong else 0)
        for k in range(num_lookahead):
            pos = computed.data[slot] + (end - start) + k
            nxt.data[k][slot] = tokens.data[slot][pos] if pos < prefill.data[slot] else 0


def pos_kernel(grid, positions, seq_lens, idx, qsl, computed, max_reqs, *, BLOCK_SIZE, skip_padding=False):
    n = grid[0] - 1
    for b in range(n):
        done = computed.data[idx.data[b]]; start, end = qsl.data[b], qsl.data[b + 1]
        seq_lens.data[b] = done + end - start
        for i in range(end - start):
            if start + i < len(positions.data):
                positions.data[start + i] = done + i
    if not skip_padding:
        for b in range(n, max_reqs):
            seq_lens.data[b] = 0


def world(tmp_path, *, wrong=False, skip_padding=False, capacity=4096, kernel_checks=True):
    kernels = {'_prepare_prefill_inputs_kernel': Launch(lambda g, *a, **k: prefill_kernel(g, *a, **k, wrong=wrong)),
               '_prepare_pos_seq_lens_kernel': Launch(lambda g, *a, **k: pos_kernel(g, *a, **k, skip_padding=skip_padding))}
    ib = module_functions(kb.MODULE, tmp_path, kernels)
    states_module = source_module(sensor.SLOT_V2_STATES, tmp_path, targets=sensor.SLOT_V2_TARGETS, sources=sensor.SLOT_V2_SOURCE)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True}, kernel_checks=kernel_checks)
    assert callbacks.instrument(states_module)
    installed = callbacks.instrument(ib)
    states = states_module.RequestState(); size = 4
    vars(states).update(free_indices=list(range(size))[::-1], req_id_to_index={}, index_to_req_id={}, max_seq_len=[0] * size,
        prompt_len=SimpleNamespace(np=[0] * size), prefill_len=SimpleNamespace(np=[0] * size), total_len=Staged(),
        all_token_ids=Staged(), num_computed_prefill_tokens=[0] * size, num_computed_tokens_np=[0] * size,
        num_computed_tokens=Staged(), draft_tokens=[Row() for _ in range(size)])
    buffers = SimpleNamespace(input_ids=T([0] * capacity), positions=T([0] * capacity), seq_lens=T([7] * size),
        tokens=T([[0] * 2048 for _ in range(size)]), nxt=T([[0] * size]), prefill=T([0] * size), computed=T([0] * size))
    return ib, states, buffers, rows, installed


def serve(ib, states, buffers, tag, length, *, start=0):
    req_id = 'cmpl-' + tag + '-0'
    states.add_request(req_id, length, [1] * length, 0, 1)
    slot = states.req_id_to_index[req_id]
    buffers.tokens.data[slot][:length] = [(slot * 7 + i) % 97 + 1 for i in range(length)]
    buffers.prefill.data[slot] = length; buffers.computed.data[slot] = 0
    idx = T([slot]); qsl = T([start, start + length])
    ib.prepare_prefill_inputs(buffers.input_ids, buffers.nxt, idx, qsl, buffers.tokens, buffers.prefill, buffers.computed)
    ib.prepare_pos_seq_lens(idx, qsl, buffers.computed, buffers.positions, buffers.seq_lens)
    states.remove_request(req_id)


CASES = [{'case_id': 'short', 'role': 'boundary', 'prompt': 'a'}, {'case_id': 'long', 'role': 'boundary', 'prompt': 'a'}]
TAGS = {'short': 'byul-cb-' + '1' * 32, 'long': 'byul-cb-' + '2' * 32}


def run(tmp_path, *, lengths=(1023, 1025), starts=(0, 0), **options):
    ib, states, buffers, rows, installed = world(tmp_path, **options)
    assert installed
    for case, length, start in zip(CASES, lengths, starts):
        serve(ib, states, buffers, TAGS[case['case_id']], length, start=start)
    return projection.project_kernel_bounds(CASES, rows, SECRET, TAGS), rows


def test_real_input_preparation_code_across_the_block_boundary_completes_scope(tmp_path):
    result, rows = run(tmp_path)
    assert result['covered_cases'] == ['long', 'short'] and result['query_lengths'] == [1023, 1025]
    assert result['violation'] is False and result['full_scope'] is True and result['hardware_memory_checker'] is False
    calls = [r for r in rows if r['point'] in kb.TARGETS[kb.MODULE] and r['phase'] == 'CALL']
    assert len(calls) == 4 and all(r['entry_qualified_before'] for r in calls)
    assert 'reference' not in str(calls)  # 비교용 원 범위는 엔진 밖으로 나가지 않는다.


def test_uninitialized_padding_is_a_violation(tmp_path):
    result, _ = run(tmp_path, skip_padding=True)
    assert result['violation'] is True and {'point': 'prepare_pos_seq_lens', 'reason': 'padding_not_initialized'} in result['violations']


def test_write_range_beyond_the_buffer_is_a_violation(tmp_path):
    result, _ = run(tmp_path, capacity=2048, starts=(0, 1500))
    assert result['violation'] is True and any(v['reason'] == 'access_range' for v in result['violations'])


def test_wrong_output_alone_stays_unknown_instead_of_a_memory_finding(tmp_path):
    result, _ = run(tmp_path, wrong=True)
    assert result['violation'] is None and 'reference_output_mismatch' in result['missing']


def test_lengths_on_one_side_of_the_block_do_not_complete_scope(tmp_path):
    result, _ = run(tmp_path, lengths=(10, 1000))
    assert result['violation'] is None and 'block_boundary_not_crossed' in result['missing']


def test_kernel_checks_stay_off_unless_the_lab_enables_them(tmp_path):
    ib, _, _, rows, installed = world(tmp_path, kernel_checks=False)
    assert installed is False and not hasattr(ib.prepare_prefill_inputs, '__wrapped__') and not rows


def test_changed_source_leaves_the_functions_unwrapped(tmp_path):
    ib, _, _, _, _ = world(tmp_path, kernel_checks=False)
    (tmp_path / 'input_batch.py').write_bytes(b'# changed\n')
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True}, kernel_checks=True)
    assert callbacks.instrument(ib) is False and not hasattr(ib.prepare_prefill_inputs, '__wrapped__')
    assert rows and rows[0]['code'] == 'ENGINE_CALLBACK_INSTALL_UNAVAILABLE'


def test_req19_is_a_callback_check_now():
    assert engine_sensor.native_capability('REQ-19') == {'implemented': True, 'reason': None}
    projection.validate_cases('REQ-19', CASES)
