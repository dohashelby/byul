"""태그 bytes 후보 자격·반환 형식·sampler 배치를 모의 자료로 확인한다."""
from pathlib import Path
from types import CodeType, FunctionType, ModuleType, SimpleNamespace
import threading

import pytest

from guard.adapters import vllm_cache_callback as cache
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_distributed_callback as distributed
from guard.adapters import vllm_scoped_consumption_callback as scoped
from guard.adapters import vllm_kernel_bounds_callback as kernel
from guard.adapters import vllm_graph_replay_callback as graph
from guard.adapters import vllm_weights_lifetime_callback as weights
from guard.adapters import vllm_worker_shard_callback as shards
from guard.adapters.observer_runtime import QualifiedObserver
from guard.adapters.vllm_observer_pin_rows import ROWS, layout

FIXTURES = Path(__file__).parent / 'fixtures/vllm-0.30.0'
OBSERVERS = (cache.CacheCallbackObserver, distributed.DistributedCallbackObserver,
    scoped.ScopedConsumptionObserver, scoped.ScopedConsumptionV2Observer,
    sensor.SlotObserver, sensor.SlotV2Observer, kernel.KernelBoundsObserver,
    graph.GraphReplayObserver, weights.WeightsLifetimeObserver)
CASES = [(observer, name) for observer in OBSERVERS for name in observer.targets if name in ROWS]


@pytest.mark.parametrize('observer,name', CASES, ids=lambda value: getattr(value, '__name__', value))
def test_candidate_bytes_qualify_only_exact_registered_row(observer, name, tmp_path):
    raw = (FIXTURES / (ROWS[name]['path'] + '.txt')).read_bytes()
    path = tmp_path / 'source.py'; path.write_bytes(raw)
    module = ModuleType(name); module.__file__ = str(path)
    codes = cache._codes(module, observer.targets, observer.sources)
    assert {point: codes[point] for point in observer.targets[name]} == {
        point: ROWS[name]['points'][point] for point in observer.targets[name]}
    assert cache.source_pin(module, observer.sources) == ROWS[name]['sha256']
    path.write_bytes(raw + b'\n')
    with pytest.raises(ValueError):
        cache._codes(module, observer.targets, observer.sources)


@pytest.mark.parametrize('name,fixture,pin', [
    (shards.LOADER, 'vllm-0.30.0/vllm/model_executor/model_loader/default_loader.py.txt',
     '9c9d54b1b650bf5affc924ebb8b8c73711e187ae7486cadd9f737bb1279cc7b8'),
    (shards.MP, 'vllm-0.30.0/vllm/v1/executor/multiproc_executor.py.txt', ROWS[shards.MP]['sha256']),
    (shards.LOADER, 'weight-iteration-native/default_loader.py.txt',
     'd17ab54969489de2338bea88190b4af78c8a1f66e866796f612ff554eea3cca9'),
    (shards.MP, 'vllm-mp-031/multiproc_executor.py',
     '11a1554380d1efa516bb481416c798798d5839f38ac3b0f379ef4b8b547f4d1b'),
])
def test_worker_shard_callables_keep_exact_paired_source_pins(tmp_path, name, fixture, pin):
    raw = (FIXTURES.parent / fixture).read_bytes()
    path = tmp_path / 'source.py'
    path.write_bytes(raw)
    module = ModuleType(name)
    module.__file__ = str(path)
    source = compile(raw, str(path), 'exec', dont_inherit=True, optimize=0)
    observer = shards.WorkerShardObserver('a' * 32, context_provider=lambda: {'fixture': True})

    for point in observer.targets[name]:
        owner, method = point.split('.')
        owner_code = next(code for code in source.co_consts if type(code) is CodeType and code.co_name == owner)
        code = next(code for code in owner_code.co_consts if type(code) is CodeType and code.co_name == method)
        original = FunctionType(code, vars(module))
        if not hasattr(module, owner):
            setattr(module, owner, type(owner, (), {}))
        setattr(getattr(module, owner), method, original)
        assert observer.wrap(module, point).__wrapped__ is original
        assert observer._source_pins[name] == pin

    path.write_bytes(raw + b'\n')
    with pytest.raises(ValueError):
        observer.wrap(module, point)


def test_qualified_observer_postcheck_uses_the_matched_pin(tmp_path):
    name = next(iter(cache.TARGETS))
    path = tmp_path / 'source.py'; path.write_bytes((FIXTURES / (ROWS[name]['path'] + '.txt')).read_bytes())
    module = ModuleType(name); module.__file__ = str(path)
    observer = QualifiedObserver(); observer.lock = threading.RLock(); observer.gaps = set()
    observer._qualified_codes(module, cache.TARGETS, cache.SOURCES)
    observer.verify_sources()
    assert observer.gaps == set()
    assert observer._source_pins[name] == ROWS[name]['sha256']
    path.write_bytes(path.read_bytes() + b'\n')
    observer.verify_sources()
    assert observer.gaps == {'OBSERVER_SOURCE_POSTCHECK_FAILED'}


def test_candidate_sampler_parameters_and_state_keep_their_meaning():
    observer = sensor.SlotV2Observer('a'*32, context_provider=lambda: {'fixture': True})
    observer._source_pins = {sensor.SLOT_V2_SAMPLER: ROWS[sensor.SLOT_V2_SAMPLER]['sha256']}
    sampler = SimpleNamespace(req_states=SimpleNamespace(index_to_req_id={2: 'fixture-request'}),
        logit_bias_state=SimpleNamespace(num_allowed_token_ids=SimpleNamespace(np=[0, 0, 3]),
                                        use_logit_bias=[False, False, True]),
        needs_logits_processing=[False, False, True])
    params = SimpleNamespace(allowed_token_ids=[1, 2, 3])
    row = observer._snapshot('Sampler.add_request', (sampler, 2, 40, params), {}, returned=True)
    assert row['restricted'] is True and row['allowed_token_count'] == 3
    assert row['logit_bias_in_use'] is True and row['needs_logits_processing'] is True
    observer._source_pins = {}
    assert layout(observer, sensor.SLOT_V2_SAMPLER) == {}


def test_candidate_weight_state_tuple_is_a_count_not_a_tensor_read():
    observer = weights.WeightsLifetimeObserver('a'*32, context_provider=lambda: {'fixture': True})
    observer._source_pins = {weights.LOADER_MODULE: ROWS[weights.LOADER_MODULE]['sha256']}
    row = observer._snapshot('IpcModelLoader._request_state',
        (SimpleNamespace(mode='copy', fallback=False),), {}, result=({'a': object(), 'b': object()}, {}), returned=True)
    assert row['entries'] == 2 and row['served'] is True
    assert row['fallback_enabled'] is False


def test_baseline_only_slot_targets_never_expand_candidate_target_contract(tmp_path):
    name = sensor.SLOT_MODULE
    assert sensor.SlotObserver.targets == sensor.SLOT_TARGETS
    raw = (FIXTURES / (ROWS[name]['path'] + '.txt')).read_bytes()
    path = tmp_path / 'source.py'
    path.write_bytes(raw)
    module = ModuleType(name)
    module.__file__ = str(path)
    observer = sensor.SlotObserver('a' * 32, context_provider=lambda: {'fixture': True})
    for point in sensor.BASELINE_SLOT_EXTRA:
        with pytest.raises(ValueError, match='SLOT_BASELINE_SOURCE_UNSUPPORTED'):
            observer.wrap(module, point)
    assert observer.targets == sensor.SLOT_TARGETS
    assert sensor.SlotObserver.targets == sensor.SLOT_TARGETS
