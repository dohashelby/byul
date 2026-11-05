"""REQ-20 graph replay 수집: 실제 vLLM 0.31.0 replay·sample 함수 코드를 감싸 만든 행을 투영에 넣는다.

graph replay 자체는 GPU가 필요하므로 원 `CudaGraphManager.run_fullgraph`(부모)만 대역으로
두고, 하위 `ModelCudaGraphManager.run_fullgraph`와 `GPUModelRunner.sample`은 원 코드를 쓴다.
위치 입력 기록은 실제 `prepare_pos_seq_lens` 감싸기(REQ-19 관측기)가 만든다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_graph_replay_callback as gr
from guard.adapters import vllm_kernel_bounds_callback as kb
from guard.executors import engine_callback_projection as projection
from guard.models import sha256_hex
from tests.test_engine_callback_kernel_bounds import Launch, T, module_functions, pos_kernel

FIXTURES = Path(__file__).parent / 'fixtures/cache-callback-native'
SECRET = bytes(range(32))
TAGS = {'p': 'byul-cb-' + '1' * 32, 'q': 'byul-cb-' + '2' * 32}
CASES = [{'case_id': 'p', 'role': 'replay', 'prompt': 'x'}, {'case_id': 'q', 'role': 'replay', 'prompt': 'y'}]


class Buffer:
    """같은 저장소를 공유하는 1차원 view. data_ptr은 저장소 시작 + offset."""
    def __init__(self, storage, start=0, end=None):
        self.storage = storage; self.start = start; self.end = len(storage) if end is None else end
        self.device = SimpleNamespace(type='cpu')

    @property
    def shape(self): return (self.end - self.start,)
    def data_ptr(self): return id(self.storage) + self.start
    def detach(self): return self
    def cpu(self): return self
    def tolist(self): return self.storage[self.start:self.end]

    def __getitem__(self, key):
        if type(key) is slice:
            a, b, _ = key.indices(self.end - self.start)
            return Buffer(self.storage, self.start + a, self.start + b)
        return [self.storage[self.start + i] for i in key]


def load(name, points, tmp_path, namespace):
    raw = (FIXTURES / (name.rsplit('.', 1)[-1] + '.py.txt')).read_bytes()
    assert sha256_hex(raw) == gr.SOURCES[name]
    path = tmp_path / (name.rsplit('.', 1)[-1] + '.py'); path.write_bytes(raw)
    module = ModuleType(name); module.__file__ = str(path); vars(module).update(namespace)
    codes = {}
    def visit(code):
        codes[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType: visit(child)
    visit(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point, cls in points.items():
        code = codes[point]
        cell = (lambda c: (lambda: c).__closure__[0])(cls) if '__class__' in code.co_freevars else None
        setattr(cls, point.split('.')[1], FunctionType(code, vars(module), closure=(cell,) if cell else None))
        setattr(module, cls.__name__, cls)
    return module


@dataclass(frozen=True)
class Desc:
    """원 BatchExecutionDescriptor처럼 값으로 비교·해시되는 graph key."""
    cg_mode: object
    num_tokens: int
    num_reqs: int
    uniform_token_count: int | None = None
    num_active_loras: int = 0
    num_ubatches: int = 1


FULL = type('CUDAGraphMode', (), {'name': 'FULL'})()


class Stop(Exception):
    """원 sample이 진입 기록 뒤 대역 model에서 멈추게 한다."""


def world(tmp_path):
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True}, kernel_checks=True)
    ib = module_functions(kb.MODULE, tmp_path, {'_prepare_pos_seq_lens_kernel': Launch(pos_kernel),
                                                '_prepare_prefill_inputs_kernel': Launch(lambda *a, **k: None)})
    assert callbacks.instrument(ib)
    hidden = [0] * 64

    class CudaGraphManager:
        def run_fullgraph(self, desc):  # 원 부모의 replay 대역: 위치 입력에서 출력 buffer를 다시 쓴다.
            for i in range(desc.num_tokens):
                hidden[i] = self.positions.data[i] * 10 + 1

    class ModelCudaGraphManager(CudaGraphManager):
        pass

    class GPUModelRunner:
        pass

    graph_module = load(gr.GRAPH_MODULE, {'ModelCudaGraphManager.run_fullgraph': ModelCudaGraphManager}, tmp_path, {})
    runner_module = load(gr.RUNNER_MODULE, {'GPUModelRunner.sample': GPUModelRunner}, tmp_path, {})
    assert callbacks.instrument(graph_module) and callbacks.instrument(runner_module)
    positions = T([0] * 64)
    manager = ModelCudaGraphManager()
    desc = Desc(FULL, 4, 2)
    vars(manager).update(graphs={desc: object()}, is_last_pp_rank=True, hidden_states=Buffer(hidden),
                         use_aux_hidden_state_outputs=False, positions=positions)
    def compute_logits(_):
        raise Stop
    runner = GPUModelRunner(); vars(runner).update(batch_sharder=None, model=SimpleNamespace(compute_logits=compute_logits))
    return SimpleNamespace(ib=ib, manager=manager, runner=runner, desc=desc, positions=positions, rows=rows, hidden=hidden)


def step(w, cases, *, done=0, tamper_inputs=False, tamper_output=False, consume=True):
    idx = T(list(range(len(cases)))); qsl = T([0, 2, 4][:len(cases) + 1]); computed = T([done] * 4)
    w.ib.prepare_pos_seq_lens(idx, qsl, computed, w.positions, T([0] * 4))
    if tamper_inputs:
        w.positions.data[0] += 99
    output = w.manager.run_fullgraph(w.desc)
    if tamper_output:
        w.hidden[0] += 1
    if consume:
        batch = SimpleNamespace(req_ids=['cmpl-' + TAGS[c] + '-0' for c in cases], logits_indices=[1, 3])
        with pytest.raises(Stop):
            w.runner.sample(output, batch, None)
    return output


def project(w, outputs=None, references=None):
    return projection.project_graph_lifetime(CASES, outputs or {}, w.rows, SECRET, TAGS, references)


def test_real_replay_and_sample_code_bind_consumption_to_requests(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q']); step(w, ['p', 'q'], done=2)
    result = project(w, {'p': 'a', 'q': 'b'}, {'p': 'a', 'q': 'b'})
    assert result['covered_cases'] == ['p', 'q'] and result['replays'] == 2
    assert result['violation'] is False and result['full_scope'] is True
    replay_calls = [r for r in w.rows if r['point'] == gr.TARGETS[gr.GRAPH_MODULE][0] and r['phase'] == 'CALL']
    assert all(r['entry_qualified_before'] and r['fields']['input_refresh'] is True for r in replay_calls)


def test_replay_before_the_previous_output_is_consumed_is_a_violation(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q'], consume=False); step(w, ['p', 'q'], done=2)
    result = project(w, {'p': 'a', 'q': 'b'}, {'p': 'a', 'q': 'b'})
    assert result['violation'] is True and {'reason': 'replay_before_output_consumed'} in result['violations']


def test_output_changed_before_consumption_is_a_violation(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q'], tamper_output=True)
    assert {'reason': 'graph_output_changed_before_consumption'} in project(w)['violations']


def test_stale_input_buffer_at_replay_is_a_violation(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q'], tamper_inputs=True)
    assert {'reason': 'stale_graph_input'} in project(w)['violations']


def test_answer_differing_from_the_eager_reference_alone_stays_unknown(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q'])
    result = project(w, {'p': 'a', 'q': 'b'}, {'p': 'a', 'q': 'z'})
    assert result['violation'] is None and 'reference_output_mismatch' in result['missing']


def test_without_an_eager_reference_scope_stays_incomplete(tmp_path):
    w = world(tmp_path)
    step(w, ['p', 'q'])
    result = project(w, {'p': 'a', 'q': 'b'})
    assert result['violation'] is None and result['missing'] == ['independent_output_control_unavailable']


def test_reference_outputs_must_be_hashes_of_replay_cases():
    good = {'p': 'a' * 64}
    assert projection.validate_reference_outputs(CASES, good) == good
    assert projection.validate_reference_outputs(CASES, None) is None
    for bad in ({}, {'p': 'x'}, {'other': 'a' * 64}):
        with pytest.raises(ValueError, match='REFERENCE_OUTPUTS_INVALID'):
            projection.validate_reference_outputs(CASES, bad)


def test_graph_observer_stays_off_without_the_lab_setting(tmp_path):
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, [].append, context_provider=lambda: {'fixture': True})
    module = load(gr.GRAPH_MODULE, {'ModelCudaGraphManager.run_fullgraph': type('ModelCudaGraphManager', (), {})}, tmp_path, {})
    assert callbacks.graph is None and callbacks.instrument(module) is False
