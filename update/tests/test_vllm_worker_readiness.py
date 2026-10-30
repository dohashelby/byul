"""LIF07 callback의 표준 라이브러리 EXAMPLE 회귀. 실제 worker/GPU 실행은 없다."""
import gc
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import weakref

import pytest

from guard.adapters import vllm_worker_readiness as native


class Object:
    def __init__(self, **values):
        self.__dict__.update(values)


def owner(**updates):
    values = dict(rank=0, local_rank=0, model_config=Object(enforce_eager=True),
        vllm_config=Object(compilation_config=Object(cudagraph_mode=Object(_name_='NONE'))))
    values.update(updates)
    return Object(**values)


def binding(call='a', **updates):
    return {'call_id_sha256':call * 64, 'session_id_sha256':'b' * 64,
        'process_start_sha256':'c' * 64, 'pid':os.getpid(), **updates}


def emit(obs, worker, point='Worker.load_model', phase='CALL', *, call='a', given=None, **kwargs):
    return obs.snapshot(point, (worker,), kwargs, phase=phase,
        binding=binding(call) if given is None else given)


@pytest.fixture
def observer():
    value = native.WorkerReadinessObserver()
    yield value
    value.close()


@pytest.mark.parametrize('point', sorted(native.POINTS))
def test_actual_snapshot_scalar_boundary_and_unknown_qualification(observer, point):
    worker = owner()
    first = emit(observer, worker, point, load_dummy_weights=False)
    last = emit(observer, worker, point, 'RETURN', load_dummy_weights=False)
    data = last['metadata']
    assert first['schema'] == last['schema'] == native.SCHEMA
    assert last['status'] == 'metadata_collected' and last['seq'] > first['seq']
    assert data['rank'] == data['local_rank'] == 0
    assert data['enforce_eager'] is True and data['configured_graph_mode'] == 'NONE'
    assert data['explicit_load_dummy_weights'] is (False if point == 'Worker.load_model' else None)
    assert data['call_transition'] == {'paired': True, 'call_seq': first['seq'],
        'end_seq': last['seq'], 'same_owner': True, 'same_thread': True, 'returned': True}
    assert data['owner_token'] == first['metadata']['owner_token']
    assert data['thread_token'] == first['metadata']['thread_token']
    assert data['source_qualified'] is data['actual_worker_observed'] is False
    assert all(data[k] is None for k in ('gpu_completion_verified', 'all_model_ready_verified',
        'ready_send_verified', 'service_gate_verified'))
    assert set(last['gaps']) <= native.GAPS
    assert 'SOURCE_POINT_PROVENANCE_REQUIRED' in last['gaps']


def test_raise_pairs_original_call_without_exception_text_or_ready_claim(observer):
    worker = owner()
    call = emit(observer, worker)
    row = observer.snapshot('Worker.load_model', (worker,), {}, phase='RAISE',
        exception=RuntimeError('PRIVATE_EXCEPTION_MODEL_PATH'), result=Object(secret='PRIVATE_RESULT'), binding=binding())
    assert row['metadata']['call_transition']['call_seq'] == call['seq']
    assert row['metadata']['call_transition']['paired'] is True
    assert row['metadata']['call_transition']['returned'] is False
    assert 'SOURCE_CALL_RAISED' in row['gaps']
    assert 'PRIVATE' not in json.dumps(row)


def test_default_dummy_flag_unobserved_and_explicit_flag_preserved(observer):
    worker = owner()
    missing = emit(observer, worker)
    assert missing['metadata']['explicit_load_dummy_weights'] is None
    assert 'DUMMY_FLAG_UNAVAILABLE' in missing['gaps']
    explicit = emit(observer, worker, call='d', load_dummy_weights=True)
    assert explicit['metadata']['explicit_load_dummy_weights'] is True


def test_safe_getters_skip_custom_properties_and_never_read_tensor_model_values(observer, monkeypatch):
    class Unsafe:
        rank = 0
        local_rank = 0
        @property
        def model_config(self):
            raise AssertionError('property 실행 금지')
        def __getattr__(self, key):
            raise AssertionError('사용자 fallback 실행 금지')
    def no_io(*args, **kwargs):
        raise AssertionError('외부 파일 I/O 금지')
    monkeypatch.setattr('builtins.open', no_io)
    monkeypatch.setattr(Path, 'read_bytes', no_io)
    worker = Unsafe()
    row = emit(observer, worker)
    assert row['metadata']['rank'] == 0
    assert row['metadata']['enforce_eager'] is None
    assert row['metadata']['configured_graph_mode'] is None
    assert 'CONFIG_UNAVAILABLE' in row['gaps']


def test_invalid_scalars_and_arbitrary_graph_name_are_not_emitted(observer):
    worker = owner(rank=True, local_rank=-1, model_config=Object(enforce_eager=1),
        vllm_config=Object(compilation_config=Object(cudagraph_mode=Object(_name_='PRIVATE_GRAPH_PATH'))))
    row = emit(observer, worker, load_dummy_weights='PRIVATE_SECRET')
    assert all(row['metadata'][key] is None for key in ('rank', 'local_rank',
        'enforce_eager', 'configured_graph_mode', 'explicit_load_dummy_weights'))
    assert 'PRIVATE' not in json.dumps(row)


def test_complete_binding_required_and_invalid_pid_not_self_certified(observer):
    worker = owner()
    missing = emit(observer, worker, given={})
    wrong_pid = emit(observer, worker, 'Worker.load_model', 'RETURN', given=binding(pid=os.getpid() + 1))
    for row in (missing, wrong_pid):
        assert row['metadata']['call_transition']['paired'] is False
        assert 'CALL_BINDING_UNAVAILABLE' in row['gaps']
    assert observer.close()['pending_calls'] == 0


def test_changed_optional_binding_hash_cannot_pair(observer):
    worker = owner()
    emit(observer, worker, given=binding(asgi_request_id_sha256='d' * 64))
    row = emit(observer, worker, phase='RETURN', given=binding(asgi_request_id_sha256='e' * 64))
    assert row['metadata']['call_transition']['paired'] is False
    assert observer.close()['pending_calls'] == 1


def test_session_and_process_start_switch_clear_old_pending_and_weak_tokens(observer):
    worker = owner()
    first = emit(observer, worker)
    changed = emit(observer, worker, phase='RETURN', given=binding(session_id_sha256='d' * 64))
    assert 'SESSION_CHANGED' in changed['gaps']
    assert changed['metadata']['call_transition']['paired'] is False
    assert changed['metadata']['owner_token'] != first['metadata']['owner_token']
    next_scope = emit(observer, worker, given=binding(session_id_sha256='d' * 64, process_start_sha256='e' * 64))
    assert 'SESSION_CHANGED' in next_scope['gaps']


def test_same_thread_nested_calls_pair_only_in_lifo_order(observer):
    worker = owner()
    outer = emit(observer, worker, 'Worker.init_device')
    inner = emit(observer, worker, 'Worker.load_model', call='d')
    inner_end = emit(observer, worker, 'Worker.load_model', 'RETURN', call='d')
    outer_end = emit(observer, worker, 'Worker.init_device', 'RETURN')
    assert inner_end['metadata']['call_transition']['call_seq'] == inner['seq']
    assert outer_end['metadata']['call_transition']['call_seq'] == outer['seq']
    assert observer.close()['collection_gap'] is False


def test_out_of_order_nested_return_invalidates_remaining_pairs(observer):
    worker = owner()
    emit(observer, worker, 'Worker.init_device')
    emit(observer, worker, 'Worker.load_model', call='d')
    outer = emit(observer, worker, 'Worker.init_device', 'RETURN')
    inner = emit(observer, worker, 'Worker.load_model', 'RETURN', call='d')
    assert 'CALL_ORDER_UNAVAILABLE' in outer['gaps']
    assert not outer['metadata']['call_transition']['paired']
    assert not inner['metadata']['call_transition']['paired']


def test_duplicate_call_id_is_ambiguous_and_bounded(observer):
    worker = owner()
    emit(observer, worker)
    duplicate = emit(observer, worker)
    returned = emit(observer, worker, phase='RETURN')
    assert 'DUPLICATE_CALL' in duplicate['gaps']
    assert not returned['metadata']['call_transition']['paired']
    assert observer.close()['pending_calls'] == 0


def test_different_owner_cannot_complete_original_call(observer):
    worker, replacement = owner(), owner()
    emit(observer, worker)
    row = emit(observer, replacement, phase='RETURN')
    assert 'OWNER_CHANGED' in row['gaps']
    assert row['metadata']['call_transition']['same_owner'] is False
    assert not row['metadata']['call_transition']['paired']


def test_cross_thread_return_cannot_pair_or_erase_original_pending(observer):
    worker = owner()
    rows = []
    child = threading.Thread(target=lambda: rows.append(emit(observer, worker)))
    child.start(); child.join(timeout=2)
    assert not child.is_alive()
    row = emit(observer, worker, phase='RETURN')
    assert rows[0]['metadata']['thread_token'] != row['metadata']['thread_token']
    assert not row['metadata']['call_transition']['paired']
    assert observer.close()['collection_gap'] is True


def test_output_mutation_does_not_poison_internal_call_pair(observer):
    worker = owner()
    first = emit(observer, worker)
    first['metadata']['owner_token'] = 'PRIVATE_MUTATION'
    first['binding']['call_id_sha256'] = 'f' * 64
    row = emit(observer, worker, phase='RETURN')
    assert row['metadata']['call_transition']['paired'] is True
    assert 'PRIVATE_MUTATION' not in json.dumps(row)


def test_no_strong_worker_or_model_reference_survives_callback(observer):
    worker = owner(model_runner=Object(tensor=Object()))
    reference = weakref.ref(worker)
    emit(observer, worker)
    del worker
    gc.collect()
    assert reference() is None
    assert observer.close()['pending_calls'] == 1


def test_bounds_and_close_keep_partial_metadata_and_pending_count():
    worker = owner()
    obs = native.WorkerReadinessObserver(max_pending=1)
    emit(obs, worker)
    row = emit(obs, worker, call='d')
    assert 'CALL_BOUND' in row['gaps'] and row['status'] == 'metadata_collected'
    assert obs.close() == {'complete': True, 'pending_calls': 1,
        'collection_gap': True, 'gaps': ['UNFINISHED_SOURCE_CALLS']}
    assert emit(obs, worker)['gaps'] == ['OBSERVER_CLOSED']
    tiny = native.WorkerReadinessObserver(max_objects=1)
    row = emit(tiny, worker)
    assert 'OBJECT_BOUND' in row['gaps']
    assert row['metadata']['call_transition']['paired'] is False
    tiny.close()


def test_busy_callback_and_close_do_not_block(observer):
    observer._lock.acquire()
    try:
        assert emit(observer, owner())['gaps'] == ['OBSERVER_BUSY']
        assert observer.close()['complete'] is False
    finally:
        observer._lock.release()


def test_simulated_fork_resets_weak_objects_pending_lock_and_sequence(observer, monkeypatch):
    worker = owner()
    emit(observer, worker)
    pid = os.getpid()
    monkeypatch.setattr(native, 'os', SimpleNamespace(getpid=lambda: pid + 1))
    changed = emit(observer, worker)
    assert changed['status'] == 'unavailable' and changed['gaps'] == ['PROCESS_CHANGED']
    row = emit(observer, worker, given=binding(pid=pid + 1))
    assert row['seq'] == 1
    assert observer.close()['pending_calls'] == 1


@pytest.mark.parametrize('point,phase,args,kwargs', [
    ('EngineCoreProc._perform_handshakes', 'RETURN', (Object(),), {}),
    ('Worker.init_device', 'YIELD', (Object(),), {}),
    ('Worker.init_device', 'CALL', (), {}),
    ('Worker.init_device', 'CALL', (Object(),), None),
])
def test_unsupported_generator_or_contract_cannot_claim_ready(observer, point, phase, args, kwargs):
    row = observer.snapshot(point, args, kwargs, phase=phase, binding=binding())
    assert row['status'] == 'unavailable' and row['metadata'] == {} and row['seq'] is None
    assert set(row['gaps']) <= native.GAPS


@pytest.mark.parametrize('key,value', [('max_objects', True), ('max_objects', 129),
    ('max_pending', 0), ('max_pending', 65)])
def test_constructor_limits_are_explicit(key, value):
    with pytest.raises(ValueError):
        native.WorkerReadinessObserver(**{key: value})


@pytest.mark.parametrize('phase', ['RETURN', 'RAISE'])
def test_real_minimal_startup_binding_needs_no_request_or_external_call_id(observer, phase):
    worker = owner()
    startup = {'session_id_sha256': 'b' * 64, 'process_start_sha256': 'c' * 64,
        'pid': os.getpid()}
    first = emit(observer, worker, given=startup)
    last = emit(observer, worker, phase=phase, given=startup)
    assert first['binding'] == last['binding'] == startup
    assert not any(key in last['binding'] for key in (
        'call_id_sha256', 'asgi_request_id_sha256', 'native_request_id_sha256'))
    transition = last['metadata']['call_transition']
    assert transition['paired'] is True and transition['call_seq'] == first['seq']
    assert transition['same_owner'] is transition['same_thread'] is True
    assert transition['returned'] is (phase == 'RETURN')
    assert last['metadata']['source_qualified'] is False
    assert last['metadata']['ready_send_verified'] is None
    assert observer.close()['pending_calls'] == 0
