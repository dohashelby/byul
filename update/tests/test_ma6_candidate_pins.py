"""태그 bytes 후보 자격·반환 형식·sampler 배치를 모의 자료로 확인한다."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import threading

import pytest

from guard.adapters import vllm_cache_callback as cache
from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_distributed_callback as distributed
from guard.adapters import vllm_scoped_consumption_callback as scoped
from guard.adapters import vllm_kernel_bounds_callback as kernel
from guard.adapters import vllm_graph_replay_callback as graph
from guard.adapters import vllm_weights_lifetime_callback as weights
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
