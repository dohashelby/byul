"""Exact source producer→consumer; all tensor/array/backend collaborators EXAMPLE."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as NS

import pytest

from guard.adapters import vllm_baseline_attention_metadata as attention
from guard.adapters import vllm_cache_callback as cache
from tests.test_ma6_baseline_slot_sampling import source


class Array:
    """EXAMPLE host arithmetic only; no NumPy/native interface qualification."""
    def __init__(self, values, *, owner=None, indices=None):
        self.values = list(values) if owner is None else owner.values
        self.indices = list(range(len(self.values))) if indices is None else indices
    def read(self):return [self.values[i] for i in self.indices]
    def __getitem__(self, index):
        if isinstance(index, slice):return Array([], owner=self, indices=self.indices[index])
        if isinstance(index, Array):return Array([self.values[self.indices[i]] for i in index.read()])
        return self.values[self.indices[index]]
    def __setitem__(self, index, value):
        selected = self.indices[index]
        if isinstance(index, slice):
            for target, item in zip(selected, value.read()):self.values[target] = item
        else:self.values[selected] = value
    def operation(self, other, fn):
        rhs = other.read() if isinstance(other, Array) else [other] * len(self.indices)
        return Array([fn(a, b) for a, b in zip(self.read(), rhs)])
    def __add__(self, other):return self.operation(other, lambda a, b:a + b)
    def __sub__(self, other):return self.operation(other, lambda a, b:a - b)
    def __mul__(self, other):return self.operation(other, lambda a, b:a * b)
    def __floordiv__(self, other):return self.operation(other, lambda a, b:a // b)
    def __mod__(self, other):return self.operation(other, lambda a, b:a % b)
    def min(self):return min(self.read())
    def max(self):return max(self.read())


class Tensor:
    """EXAMPLE opaque tensor identity; data access is only inert source plumbing."""
    def __init__(self, array=None):self.array = array;self.copy_count = 0
    def __getitem__(self, index):
        return Tensor(self.array[index] if self.array is not None else None)
    def __sub__(self, value):return Tensor()
    def flatten(self):return self
    def numpy(self):return self.array
    def long(self):return self
    def to(self, *args, **kwargs):return self
    def copy_(self, other, **kwargs):self.copy_count += 1;return self
    def unbind(self, axis):return Tensor(), Tensor()
    def tolist(self):raise AssertionError('observer read tensor values')
    def data_ptr(self):raise AssertionError('observer read pointer')
    def __int__(self):raise AssertionError('observer converted tensor')


class Metadata:
    def __init__(self, **kwargs):self.__dict__.update(num_input_tokens=0, **kwargs)


def repeat(values, counts):
    return Array([v for v, count in zip(values.read(), counts.read()) for _ in range(count)])


def cumsum(values):
    total = 0;result = []
    for value in values.read():total += value;result.append(total)
    return Array(result)


def add(left, right, *, out):out[:] = left + right


def setup(tmp_path, *, install=True):
    rows = []
    def sink(row):
        assert len(rows) < 256
        rows.append(row)
    observer = attention.AttentionMetadataObserver('a' * 32, sink=sink,
        context_provider=lambda: {'pid': 11, 'start_ticks': 22})
    modules = {name: source(name, points, attention.SOURCES[name], tmp_path) for name, points in attention.TARGETS.items()}
    runner_module, backend_module = modules[attention.RUNNER], modules[attention.ATTENTION]
    selected = []
    torch = NS(index_select=lambda *args, **kwargs: None, from_numpy=lambda array:Tensor(array),
        tensor=lambda *args, **kwargs:Tensor(), int32='EXAMPLE-int32',
        ops=NS(_C_cache_ops=NS(reshape_and_cache_flash=lambda *args:selected.append('cache'))))
    runner_module.np = NS(int32='EXAMPLE-int32', array=lambda values, **kwargs:Array(values), repeat=repeat, cumsum=cumsum, add=add)
    runner_module.torch = backend_module.torch = torch
    runner_module.FlashAttentionMetadata = Metadata
    runner_module.FlashAttentionBackend = NS(use_cascade_attention=lambda **kwargs:True)
    backend_module.flash_attn_varlen_func = lambda **kwargs:selected.append('regular')
    backend_module.cascade_attention = lambda *args, **kwargs:selected.append('cascade')
    backend_module.FlashAttentionImpl.forward.__defaults__ = (None,)
    if install:
        for name, points in attention.TARGETS.items():
            for point in points:
                cls, method = point.split('.')
                setattr(vars(modules[name])[cls], method, observer.wrap(modules[name], point))
    batch = Metadata(num_reqs=2, req_ids=['low-entropy-A', 'low-entropy-B'], req_id_to_index={'low-entropy-A':0, 'low-entropy-B':1},
        num_computed_tokens_cpu=Array([4, 4]), token_ids_cpu=NS(shape=(2, 16)), token_ids_cpu_tensor=Tensor())
    block_cpu = Tensor(Array([0, 1, 2, 3, 4, 5, 6, 7]));block_device = Tensor()
    batch.block_table = NS(commit=lambda count:None, get_cpu_tensor=lambda:block_cpu, get_device_tensor=lambda:block_device)
    runner = runner_module.GPUModelRunner()
    runner.__dict__.update(input_batch=batch, model_config=NS(uses_mrope=False), arange_np=Array(range(32)),
        max_num_tokens=32, max_num_reqs=4, max_model_len=16, max_num_blocks_per_req=4, block_size=2,
        device='EXAMPLE-device', num_query_heads=2, num_kv_heads=1, sliding_window=None, num_sms=1)
    for name in ('positions_np', 'slot_mapping_np', 'query_start_loc_np', 'seq_lens_np'):setattr(runner, name, Array([0]*32))
    for name in ('input_ids_cpu', 'input_ids', 'positions', 'positions_cpu', 'query_start_loc_cpu', 'seq_lens_cpu', 'slot_mapping_cpu'):setattr(runner, name, Tensor())
    impl = backend_module.FlashAttentionImpl()
    impl.__dict__.update(kv_cache_dtype='EXAMPLE-auto', scale=1.0, alibi_slopes=None, sliding_window=None, logits_soft_cap=0.0, fa_version=2)
    scheduler = NS(total_num_scheduled_tokens=3, num_scheduled_tokens={'low-entropy-A':2,'low-entropy-B':1}, num_common_prefix_blocks=0)
    return NS(rows=rows, observer=observer, modules=modules, runner=runner, batch=batch, impl=impl, scheduler=scheduler, selected=selected)


def prepare(world):return world.runner._prepare_inputs(world.scheduler)


def forward(world, metadata, output=None):
    output = Tensor() if output is None else output
    return output, world.impl.forward(NS(_k_scale=1.0, _v_scale=1.0), Tensor(), Tensor(), Tensor(), Tensor(), metadata, output)


def project(world, rows=None):
    return attention.project(world.rows if rows is None else rows,
        session_sha256=cache.base.sha(world.observer.session.encode()), process_sha256=world.observer.process_sha256)


@pytest.mark.parametrize('cascade', [False, True])
def test_actual_prepare_tuple_transfers_to_actual_forward_and_preserves_return(tmp_path, cascade):
    world = setup(tmp_path)
    world.scheduler.num_common_prefix_blocks = 2 if cascade else 0
    metadata, logits = prepare(world)
    # execute_model sets this padded count after _prepare_inputs; it is not a stable count.
    metadata.num_input_tokens = 8
    provided, returned = forward(world, metadata)
    assert provided is returned and isinstance(logits, Tensor)
    result = project(world)
    chain, = result['metadata_chains']
    assert len(result['source_calls']) == 2
    assert chain['branch'] == ('cascade' if cascade else 'regular')
    assert world.selected == ['cache', chain['branch']]
    assert chain['same_metadata_object_observed'] is True
    assert chain['metadata_scalar_values_stable_observed'] is True
    assert chain['returned_provided_output_observed'] is True
    for field in ('scheduled_counts_match_declared_total', 'scheduled_total_matches_metadata',
                  'declared_token_capacity_relation', 'declared_request_capacity_relation'):
        assert chain[field] is True
    assert all(chain[field] is False for field in attention.TRUTH)
    assert result['producer_authenticated'] is False
    encoded = json.dumps(world.rows)
    assert 'low-entropy' not in encoded
    assert hashlib.sha256(b'low-entropy-A').hexdigest() not in encoded
    assert 'installed_native_cache_op_identity_unverified' in result['missing']
    assert 'installed_flashattention_dependency_source_unverified' in result['missing']


def test_profiling_none_is_actual_no_backend_path(tmp_path):
    world = setup(tmp_path)
    provided, returned = forward(world, None)
    chain, = project(world)['metadata_chains']
    assert provided is returned and world.selected == []
    assert chain['branch'] == 'profiling' and chain['profiling_returned_provided_output_observed'] is True
    assert chain['same_metadata_object_observed'] is False
    assert all(chain[field] is False for field in attention.TRUTH)


class UnsupportedScalar:
    def __int__(self):raise AssertionError('unqualified scalar conversion')
    def item(self):raise AssertionError('unqualified native scalar interface')


def test_unsupported_scalar_actual_rows_keep_gap_and_never_convert(tmp_path):
    world = setup(tmp_path)
    metadata, _ = prepare(world)
    metadata.max_seq_len = UnsupportedScalar()
    forward(world, metadata)
    result = project(world)
    chain, = result['metadata_chains']
    assert 'UNQUALIFIED_INTEGER_SCALAR' in result['missing']
    assert chain['metadata_counts_at_forward']['max_seq_len'] is None
    assert chain['metadata_scalar_values_stable_observed'] is False
    assert chain['whole_question_complete'] is False


def test_property_fields_are_not_invoked(tmp_path):
    world = setup(tmp_path)
    class Unsupported:
        @property
        def use_cascade(self):raise AssertionError('property executed')
        @property
        def max_seq_len(self):raise AssertionError('property executed')
    snapshot = world.observer._snapshot(attention.FORWARD, (world.impl, None, None, None, None, None, Unsupported(), Tensor()), {})
    assert snapshot['branch'] == 'unsupported' and snapshot['metadata']['counts']['max_seq_len'] is None


@pytest.mark.parametrize('mutation', ['process', 'source', 'owner', 'time', 'extra', 'metadata', 'branch'])
def test_tampered_source_process_transfer_and_fields_cannot_make_chain(tmp_path, mutation):
    world = setup(tmp_path)
    metadata, _ = prepare(world);forward(world, metadata)
    rows = deepcopy(world.rows)
    if mutation == 'process':rows[2]['process_sha256'] = '0' * 64
    elif mutation == 'source':rows[2]['source_file_sha256'] = '0' * 64
    elif mutation == 'owner':rows[3]['fields']['implementation_sha256'] = '0' * 64
    elif mutation == 'time':
        rows[2]['operation_started_ns'] = rows[3]['operation_started_ns'] = 0
    elif mutation == 'extra':rows[2]['fields']['raw_secret'] = 'must-reject'
    elif mutation == 'metadata':
        for row in rows[2:]:row['fields']['metadata']['metadata_sha256'] = '0' * 64
    elif mutation == 'branch':rows[3]['fields']['branch'] = 'cascade'
    assert project(world, rows)['metadata_chains'] == []


def test_source_changed_and_process_changed_leave_original_behavior(tmp_path):
    world = setup(tmp_path)
    module = world.modules[attention.ATTENTION]
    with open(module.__file__, 'ab') as stream:stream.write(b'\n# EXAMPLE tampering\n')
    provided, returned = forward(world, None)
    assert provided is returned and project(world)['metadata_chains'] == []
    assert world.observer.report()['missing']
    other = setup(tmp_path)
    other.observer.pid = -1
    provided, returned = forward(other, None)
    assert provided is returned and project(other)['metadata_chains'] == []


def test_actual_backend_exception_is_unchanged_and_no_successful_chain(tmp_path):
    world = setup(tmp_path)
    metadata, _ = prepare(world)
    failure = RuntimeError('EXAMPLE-private-backend-error')
    def fail(**kwargs):raise failure
    world.modules[attention.ATTENTION].flash_attn_varlen_func = fail
    with pytest.raises(RuntimeError) as caught:forward(world, metadata)
    assert caught.value is failure
    result = project(world)
    assert result['metadata_chains'] == []
    assert result['source_calls'][-1]['phase'] == 'RAISE'
    assert 'EXAMPLE-private-backend-error' not in json.dumps(world.rows)


def test_metadata_substitution_and_missing_terminal_do_not_make_transfer(tmp_path):
    world = setup(tmp_path)
    metadata, _ = prepare(world)
    replacement = Metadata(**{key:value for key,value in metadata.__dict__.items() if key != 'num_input_tokens'})
    forward(world, replacement)
    assert project(world)['metadata_chains'] == []
    forward(world, metadata)
    assert project(world)['metadata_chains']
    assert project(world, world.rows[:-1])['metadata_chains'] == []


def test_explicit_wrapper_restoration_retains_original_source_behavior(tmp_path):
    world = setup(tmp_path)
    cls = world.modules[attention.ATTENTION].FlashAttentionImpl
    original = cls.forward.__wrapped__
    cls.forward = original
    before = len(world.rows)
    provided, returned = forward(world, None)
    assert returned is provided and len(world.rows) == before
    cls.forward = world.observer.wrap(world.modules[attention.ATTENTION], attention.FORWARD)
    provided, returned = forward(world, None)
    assert returned is provided
    assert project(world)['metadata_chains'][0]['profiling_returned_provided_output_observed'] is True


@pytest.mark.parametrize('enabled', [False, True])
def test_prepared_callback_path_keeps_attention_observation_explicit(tmp_path, enabled):
    from guard.adapters import vllm_engine_callback_sensor as sensor
    from guard.collectors import engine_callbacks
    world = setup(tmp_path, install=False)
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, b'EXAMPLE-attention-shared-key-01', rows.append,
        context_provider=lambda: {'pid': 11, 'start_ticks': 22}, baseline_attention_metadata=enabled)
    originals = {point: vars(vars(world.modules[name])[point.split('.')[0]])[point.split('.')[1]]
        for name, points in attention.TARGETS.items() for point in points}
    for module in world.modules.values():
        assert callbacks.instrument(module) is enabled
    metadata, _ = prepare(world)
    forward(world, metadata)
    if enabled:
        assert callbacks.attention_metadata.secret == b'EXAMPLE-attention-shared-key-01'
        assert callbacks.attention_metadata.report()['events'] == []
        result = attention.project(rows, session_sha256=cache.base.sha('a'.encode() * 32),
            process_sha256=callbacks.attention_metadata.process_sha256)
        assert len(result['source_calls']) == 2 and len(result['metadata_chains']) == 1
        assert result['whole_question_complete'] is False
    else:
        assert callbacks.attention_metadata is None and rows == []
        for name, points in attention.TARGETS.items():
            for point in points:
                assert vars(vars(world.modules[name])[point.split('.')[0]])[point.split('.')[1]] is originals[point]
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', baseline_attention_metadata=enabled)
    assert prepared['baseline_attention_metadata'] is enabled and prepared['engine_started'] is False
    assert 'guard_engine_callbacks/vllm_baseline_attention_metadata.py' in prepared['assets']
    assert json.loads((tmp_path / 'assets/settings.json').read_bytes())['baseline_attention_metadata'] is enabled
    assert engine_callbacks.load_prepared(tmp_path / 'assets') == prepared
