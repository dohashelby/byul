"""Initial V1 real source producer→consumer; tensor/backend collaborators EXAMPLE."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_baseline_slot_sampling as sampling
from guard.collectors import engine_callbacks
from guard.executors import engine_callback_projection as projection
from tests.test_ma6_baseline_cache_slot import batch, request, SECRET, slot_projection

FIXTURE = Path(__file__).parent / 'fixtures/vllm-0.7.0'


def source(name, points, digest, tmp_path):
    raw = (FIXTURE / (name.replace('.', '/') + '.py.txt')).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == digest
    tree = ast.parse(raw)
    functions = {}
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and cls.name + '.' + fn.name in points:
                    assert fn.decorator_list == []  # the exact reviewed direct functions, no unwrapping
    module = ModuleType(name)
    path = tmp_path / (name + '.py')
    path.write_bytes(raw)
    module.__file__ = str(path)
    def visit(code):
        functions[code.co_qualname] = code
        for child in code.co_consts:
            if type(child) is CodeType:visit(child)
    visit(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point in points:
        cls, method = point.split('.')
        owner = vars(module).setdefault(cls, type(cls, (), {'__module__': name}))
        fn = FunctionType(functions[point], vars(module))
        if point == 'InputBatch.make_sampling_metadata':fn.__defaults__ = (False,)
        setattr(owner, method, fn)
    return module


class Metadata:
    def __init__(self, **kwargs):self.__dict__.update(kwargs)


class OpaqueTensor:
    def __init__(self):self.copies = 0
    def __getitem__(self, index):return self
    def copy_(self, other, **kwargs):self.copies += 1;return self
    def to(self, *args, **kwargs):return self
    def __lt__(self, other):return False
    def unsqueeze(self, **kwargs):return self
    def div_(self, other):return self
    def __iter__(self):raise AssertionError('observer must not read tensor values')
    def tolist(self):raise AssertionError('observer must not read tensor values')
    def data_ptr(self):raise AssertionError('observer must not read pointers')


def setup(tmp_path, *, enabled=True):
    rows = []
    def bounded_sink(row):
        assert len(rows) < 256
        rows.append(row)
    callbacks = sensor.EngineCallbacks('f' * 32, SECRET, bounded_sink,
        context_provider=lambda: {'pid': 1, 'start_ticks': 2}, baseline_slot_sampling=enabled)
    batch_points = sensor.SLOT_TARGETS[sensor.SLOT_MODULE] + sensor.BASELINE_SLOT_EXTRA + sampling.TARGETS[sampling.BATCH]
    modules = {sampling.BATCH: source(sampling.BATCH, batch_points, sampling.SOURCES[sampling.BATCH], tmp_path),
        sampling.RUNNER: source(sampling.RUNNER, sampling.TARGETS[sampling.RUNNER], sampling.SOURCES[sampling.RUNNER], tmp_path),
        sampling.QWEN: source(sampling.QWEN, sampling.TARGETS[sampling.QWEN], sampling.SOURCES[sampling.QWEN], tmp_path),
        sampling.SAMPLER: source(sampling.SAMPLER, sampling.TARGETS[sampling.SAMPLER] + ('Sampler.apply_penalties',), sampling.SOURCES[sampling.SAMPLER], tmp_path)}
    modules[sampling.BATCH].SamplingType = SimpleNamespace(GREEDY='greedy')
    modules[sampling.BATCH].SamplingMetadata = Metadata
    modules[sampling.SAMPLER].torch = SimpleNamespace(float32='EXAMPLE-float', int32='EXAMPLE-int', where=lambda cond, a, b: b)
    modules[sampling.SAMPLER]._SAMPLING_EPS = 1e-5
    modules[sampling.SAMPLER].apply_min_token_penalties = lambda *args: None
    modules[sampling.SAMPLER].Sampler.__call__ = lambda self, logits, metadata: self.forward(logits, metadata)
    modules[sampling.SAMPLER].SamplerOutput = lambda **kwargs: SimpleNamespace(**kwargs)
    for module in modules.values():callbacks.instrument(module)
    value = batch(modules[sampling.BATCH])
    for name in ('temperature', 'top_p', 'top_k', 'frequency_penalties', 'presence_penalties', 'repetition_penalties',
                 'temperature_cpu_tensor', 'top_p_cpu_tensor', 'top_k_cpu_tensor'):
        setattr(value, name, OpaqueTensor())
    value.max_num_logprobs = 0
    value.prompt_token_ids = None
    sampler = modules[sampling.SAMPLER].Sampler()
    sampler.topk_topp_sampler = lambda *args: OpaqueTensor()  # inert backend, no GPU/value claim
    model = modules[sampling.QWEN].Qwen2ForCausalLM()
    model.sampler = sampler
    runner = modules[sampling.RUNNER].GPUModelRunner()
    runner.input_batch, runner.model, runner.requests = value, model, {}
    return SimpleNamespace(callbacks=callbacks, modules=modules, batch=value, runner=runner, model=model,
        sampler=sampler, rows=rows)


def add(w, tag, *, restricted=False, slot=0):
    item = request(tag, None)
    item.sampling_params.__dict__.update(sampling.STIMULI['restricted' if restricted else 'reused'])
    item.sampling_params.sampling_type = 'random'
    w.runner.requests[tag] = item
    w.batch.add_request(item, slot)
    return item


def prepare(w, *, skip=False):
    value = w.batch
    value.num_reqs = len(value.req_id_to_index)
    value.all_greedy, value.all_random = False, True
    value.no_top_k, value.no_top_p, value.no_penalties = not value.top_k_reqs, not value.top_p_reqs, True
    scheduler = SimpleNamespace(finished_req_ids=[] if skip else ['EXAMPLE-finished'], preempted_req_ids=[],
        scheduled_new_reqs=[], scheduled_resumed_reqs=[])
    return w.runner._prepare_sampling(scheduler)


def sample(w):
    metadata = prepare(w)
    return metadata, w.model.sample(OpaqueTensor(), metadata)


def test_supported_limit_source_metadata_reaches_actual_final_sampler_and_existing_slot_consumer(tmp_path):
    w = setup(tmp_path)
    restricted, reused = 'byul-cb-' + '1' * 32, 'byul-cb-' + '2' * 32
    add(w, restricted, restricted=True)
    sample(w)
    w.batch.remove_request(restricted)
    w.runner.requests.pop(restricted)
    add(w, reused)
    metadata, result = sample(w)
    result = slot_projection(w.rows, restricted, reused)
    chains = result['sampling_metadata_lineage']['chains']
    assert len(chains) == 2
    chain = next(c for c in chains if c['request_slots'][0]['request_sha256'] == w.callbacks.slot.fingerprint(reused))
    assert chain['same_metadata_object_observed'] is True
    assert chain['temperature_argument_same_tensor_observed'] is True
    assert chain['selection_same_metadata_observed'] is True
    assert chain['metadata_flags_stable_observed'] is True and chain['metadata_tensor_identities_stable_observed'] is True
    assert chain['declared_restrictions'][0]['restrictions']['top_k'] == 0
    assert chain['declared_restrictions'][0]['restrictions']['allowed_token_ids_supported'] is False
    assert chain['gpu_values_verified'] is chain['copy_completion_verified'] is False
    assert result['rows'][0]['slot_index_match_candidate'] is True
    assert result['rows'][0]['sampling_metadata_lineage_observed'] is True
    assert result['rows'][0]['slot_reused'] is None and result['violation'] is None and not result['full_scope']
    assert 'byul-cb-' not in json.dumps(w.rows)
    assert all('data_ptr' not in json.dumps(row) for row in w.rows)


def test_default_disabled_mode_keeps_sampling_points_untouched(tmp_path):
    w = setup(tmp_path, enabled=False)
    add(w, 'byul-cb-' + '3' * 32)
    sample(w)
    assert not any((row.get('fields') or {}).get('sampling_stage') for row in w.rows)
    assert projection._slot_sampling_lineage(w.rows)['chains'] == []


def test_unsupported_metadata_and_missing_native_model_transfer_remain_partial(tmp_path):
    w = setup(tmp_path)
    add(w, 'byul-cb-' + '4' * 32)
    metadata = prepare(w)
    # Direct sampler use bypasses the qualified model.sample boundary.
    w.sampler(OpaqueTensor(), metadata)
    assert projection._slot_sampling_lineage(w.rows)['chains'] == []
    fields = sampling.snapshot(w.callbacks.slot, 'Sampler.forward', (w.sampler, OpaqueTensor(), None), {})
    assert fields['sampling_metadata']['metadata_sha256'] is None
    assert fields['sampling_metadata']['gpu_values_verified'] is False
    assert sampling.snapshot(w.callbacks.slot, 'unsupported', (), {})['sampling_stage'] == 'unsupported'


def test_source_process_and_object_transfer_tampering_never_forms_a_chain(tmp_path):
    w = setup(tmp_path)
    add(w, 'byul-cb-' + '5' * 32)
    sample(w)
    assert len(projection._slot_sampling_lineage(w.rows)['chains']) == 1
    for damage in ('process', 'source', 'metadata', 'parent'):
        rows = deepcopy(w.rows)
        for row in rows:
            if row.get('point') != 'Sampler.forward':continue
            if damage == 'process':row['process_sha256'] = 'e' * 64
            elif damage == 'source':row['source_file_sha256'] = '0' * 64
            elif damage == 'metadata':row['fields']['sampling_metadata']['metadata_sha256'] = 'a' * 64
            else:row['parent_call_sha256'] = 'b' * 64
        assert projection._slot_sampling_lineage(rows)['chains'] == []
    path = Path(w.modules[sampling.SAMPLER].__file__)
    path.write_bytes(path.read_bytes() + b'\n')
    prior = len(w.rows)
    sample(w)
    assert projection._slot_sampling_lineage(w.rows[prior:])['chains'] == []


def test_stimulus_uses_the_actual_native_argument_contract_without_tolerance(tmp_path):
    params_module = source('vllm.sampling_params', ('SamplingParams._verify_args',),
        'c43343b437b675d4ed5dd16479636db1f621a3a408a30b81817923913419aa62', tmp_path)
    for values in sampling.STIMULI.values():
        params = SimpleNamespace(n=1, presence_penalty=0.0, frequency_penalty=0.0, repetition_penalty=1.0,
            min_p=0.0, max_tokens=8, min_tokens=0, logprobs=None, prompt_logprobs=None,
            truncate_prompt_tokens=None, stop=[], best_of=1, _real_n=1, **values)
        assert params_module.SamplingParams._verify_args(params) is None
    params.top_k = 0
    with pytest.raises(ValueError, match='top_k must be -1'):
        params_module.SamplingParams._verify_args(params)
    precise = SimpleNamespace(temperature=0.0004, top_p=0.999999, top_k=-1, allowed_token_ids=None)
    assert sampling.restricted(precise) is True
    assert sampling.restrictions(precise)['top_p_hex'] != float(1.0).hex()


def test_real_condense_then_sampling_retains_supported_b_metadata_and_copy_gap(tmp_path):
    w = setup(tmp_path)
    restricted, reused = 'byul-cb-' + '6' * 32, 'byul-cb-' + '7' * 32
    add(w, restricted, restricted=True, slot=0)
    add(w, reused, slot=1)
    w.batch.remove_request(restricted)
    w.runner.requests.pop(restricted)
    w.batch.num_reqs = 1
    w.batch.condense([0])
    sample(w)
    result = slot_projection(w.rows, restricted, reused)
    assert result['observed_slot_moves'] == 1
    assert result['rows'][0]['slot_index_match_candidate'] is True
    assert result['rows'][0]['sampling_metadata_lineage_observed'] is True
    assert result['rows'][0]['sampler_restriction_values_verified'] is False
    chain, = result['sampling_metadata_lineage']['chains']
    assert chain['declared_restrictions'][0]['restrictions']['top_k'] == 0
    assert chain['sampler_flags']['no_top_k'] is True
    assert chain['scope_complete'] is chain['copy_completion_verified'] is False
    assert result['violation'] is None and not result['full_scope']


def test_skip_copy_and_llama_model_transfer_are_separate_observed_facts(tmp_path):
    w = setup(tmp_path)
    tag = 'byul-cb-' + '8' * 32
    add(w, tag)
    llama = source(sampling.LLAMA, sampling.TARGETS[sampling.LLAMA], sampling.SOURCES[sampling.LLAMA], tmp_path)
    assert w.callbacks.instrument(llama) is True
    model = llama.LlamaForCausalLM()
    model.sampler = w.sampler
    w.runner.model = model
    metadata = prepare(w, skip=True)
    assert w.batch.top_k.copies == 0  # native skip branch, not a GPU completion assertion
    model.sample(OpaqueTensor(), metadata)
    result = projection._slot_sampling_lineage(w.rows)
    assert len(result['chains']) == 1
    assert result['chains'][0]['selection_same_metadata_observed'] is True
    batch_row = next(row for row in w.rows if row['point'] == sampling.BATCH_POINT and row['phase'] == 'RETURN')
    assert batch_row['fields']['skip_copy'] is True
    assert result['chains'][0]['copy_completion_verified'] is False


def test_default_unsupported_properties_are_not_executed_and_assets_are_opt_in(tmp_path):
    class Trap:
        @property
        def top_p(self):raise AssertionError('property must not run')
        @property
        def temperature(self):raise AssertionError('property must not run')
    assert sampling.restrictions(Trap())['temperature_milli'] is None
    assert sampling.restricted(Trap()) is None
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', baseline_slot_sampling=True)
    assert prepared['baseline_slot_sampling'] is True and prepared['engine_started'] is False
    assert engine_callbacks.load_prepared(tmp_path / 'assets') == prepared
    with pytest.raises(ValueError, match='SLOT_SAMPLING_EXCLUSIVE'):
        engine_callbacks.prepare_assets(tmp_path / 'other', baseline_slot_sampling=True, runner_consumption=True)
