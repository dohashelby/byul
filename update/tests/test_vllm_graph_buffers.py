"""REQ20 private callback의 CPU/EXAMPLE 회귀. 실제 CUDA·worker 실행은 없다."""
from copy import deepcopy
import gc
import json
import os
from types import SimpleNamespace
import weakref

import pytest

from guard.adapters.vllm_graph_buffers import GraphBufferObserver, SCHEMA


class Object:
    def __init__(self, **values):
        self.__dict__.update(values)


class Tensor:
    __module__ = 'torch'

    def __init__(self, *, device='cuda', shape=(4,), version=1):
        self.device = SimpleNamespace(type=device)
        self.dtype = 'torch.int32'
        self.shape = shape
        self._version = version

    def data_ptr(self):
        raise AssertionError('주소 읽기 금지')

    def cpu(self):
        raise AssertionError('GPU 복사 금지')

    def tolist(self):
        raise AssertionError('값 읽기 금지')


class Event:
    def query(self):
        raise AssertionError('이벤트 조회 금지')

    def synchronize(self):
        raise AssertionError('동기화 금지')


@pytest.fixture
def observer():
    value = GraphBufferObserver()
    yield value
    value.close()


def binding(call='a', **updates):
    return {'call_id_sha256': call * 64, 'session_id_sha256': 'b' * 64,
            'process_start_sha256': 'c' * 64, 'native_request_id_sha256': 'd' * 64,
            'pid': os.getpid(), **updates}


def emit(observer, point, owner, phase, *, call='a', frame=None, result=None, **kw):
    return observer.snapshot(point, (owner,), {}, phase=phase, binding=binding(call),
                             frame_locals=frame, result=result, **kw)


def runner():
    return Object(input_batch=Object(), input_ids=Object(gpu=Tensor()), positions=Tensor(shape=(2, 4)))


def test_actual_preparation_fields_pair_versions_sizes_and_no_values(observer):
    owner = runner()
    args = (owner, Object(), 2, 4, None)
    point = 'GPUModelRunner._prepare_input_ids'
    before = observer.snapshot(point, args, {}, phase='CALL', binding=binding())
    owner.input_ids.gpu._version += 1
    after = observer.snapshot(point, args, {}, phase='RETURN', binding=binding())
    assert after['schema'] == SCHEMA and after['status'] == 'metadata_collected'
    data = after['metadata']
    assert data['sizes'] == {'num_reqs': 2, 'total_num_scheduled_tokens': 4}
    assert data['call_transition'] == {'paired': True, 'same_owner': True,
        'call_seq': before['seq'], 'return_seq': after['seq'], 'changed_input_versions': ['input_ids']}
    desc = data['buffers']['input_ids']['descriptor']
    assert desc == {'status': 'collected', 'device_type': 'cuda', 'dtype': 'torch.int32',
                    'shape': [4], 'values_collected': False}
    assert data['consumer']['value_comparison_verified'] is None
    assert data['consumer']['event_completion_verified'] is None


def test_returned_metadata_mutation_cannot_poison_pending_source_facts(observer):
    owner = runner();point = 'GPUModelRunner._prepare_inputs'
    call = emit(observer, point, owner, 'CALL')
    call['metadata']['buffers']['input_ids']['mutation_version'] = 100
    ret = emit(observer, point, owner, 'RETURN')
    assert ret['metadata']['call_transition']['changed_input_versions'] == []


def test_replaced_tensor_is_different_generation_not_mutation_of_old_buffer(observer):
    owner = runner();point = 'GPUModelRunner._prepare_inputs'
    old = emit(observer, point, owner, 'CALL')['metadata']['buffers']['input_ids']['tensor_token']
    owner.input_ids.gpu = Tensor(version=99)
    ret = emit(observer, point, owner, 'RETURN')
    assert ret['metadata']['buffers']['input_ids']['tensor_token'] != old
    assert ret['metadata']['call_transition']['changed_input_versions'] == []


def test_graph_selected_frame_descriptor_mode_entry_and_output_are_actual_fields(observer):
    descriptor = Object(num_tokens=4, num_reqs=2, uniform=False, has_lora=False, num_active_loras=0)
    entry = Object(cudagraph=Object(), output=Tensor())
    # 실제 dict의 key는 hash 가능한 실객체이며 문자열·주소를 표시하지 않는다.
    wrapper = Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={descriptor: entry})
    frame = {'batch_descriptor': descriptor, 'entry': entry, 'cudagraph_runtime_mode': Object(_name_='FULL'),
             'secret': 'PRIVATE_FRAME_SECRET'}
    call = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'CALL', frame=frame)
    ret = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'RETURN', frame=frame, result=entry.output)
    data = ret['metadata']
    assert data['runtime_mode'] == data['selected_runtime_mode'] == 'FULL'
    assert data['descriptor']['num_tokens'] == 4 and data['descriptor']['has_lora'] is False
    assert data['entry_token'] == call['metadata']['entry_token']
    assert data['graph_token'] == data['known_entries'][0]['graph_token']
    assert data['output']['tensor_items'][0]['tensor_token'] == call['metadata']['output']['tensor_items'][0]['tensor_token']
    assert data['replay_verified'] is None
    assert 'PRIVATE' not in json.dumps(ret)


def test_absent_graph_frame_does_not_claim_replay_or_mode_selection(observer):
    wrapper = Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={})
    ret = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'RETURN')
    assert ret['metadata']['runtime_mode'] == 'FULL'
    assert ret['metadata']['selected_runtime_mode'] is None
    assert ret['metadata']['replay_verified'] is None
    assert 'GRAPH_FRAME_UNAVAILABLE' in ret['gaps']


def test_async_original_event_retained_reference_and_consumer_boundaries(observer):
    owner = Object();point = 'AsyncGPUModelRunnerOutput.__init__'
    tensor, event, stream = Tensor(), Event(), Object()
    args = (owner, Object(), tensor, None, [], stream)
    observer.snapshot(point, args, {}, phase='CALL', binding=binding())
    owner._sampled_token_ids = tensor;owner.async_copy_ready_event = event
    owner.sampled_token_ids_cpu = Tensor(device='cpu', shape=(2, 1))
    initialized = observer.snapshot(point, args, {}, phase='RETURN', binding=binding())
    assert initialized['metadata']['consumer']['copy_enqueue_boundary_returned'] is True
    tensor._version += 1
    before = emit(observer, 'AsyncGPUModelRunnerOutput.get_output', owner, 'CALL', call='e')
    assert before['metadata']['consumer']['pending_output_version_changed'] is True
    assert before['metadata']['consumer']['event_completion_verified'] is None
    del owner._sampled_token_ids
    result = Object(sampled_token_ids=[[999]], private='PRIVATE_OUTPUT')
    returned = emit(observer, 'AsyncGPUModelRunnerOutput.get_output', owner, 'RETURN', call='e', result=result)
    state = returned['metadata']['consumer']
    assert state['get_output_boundary_returned'] is True and state['retained_reference_released'] is True
    assert state['event_completion_verified'] is state['value_comparison_verified'] is None
    assert returned['metadata']['event_token'] == initialized['metadata']['event_token']
    assert returned['metadata']['stream_token'] == initialized['metadata']['stream_token']
    assert '999' not in json.dumps(returned) and 'PRIVATE' not in json.dumps(returned)
    assert observer.close()['collection_gap'] is False


def test_changed_copy_event_or_different_request_never_joins_consumer(observer):
    owner = Object(_sampled_token_ids=Tensor(), async_copy_ready_event=Event())
    emit(observer, 'AsyncGPUModelRunnerOutput.__init__', owner, 'CALL')
    emit(observer, 'AsyncGPUModelRunnerOutput.__init__', owner, 'RETURN')
    owner.async_copy_ready_event = Event()
    state = emit(observer, 'AsyncGPUModelRunnerOutput.get_output', owner, 'CALL', call='e')
    assert 'COPY_EVENT_CHANGED' in state['gaps']
    foreign = observer.snapshot('AsyncGPUModelRunnerOutput.get_output', (owner,), {}, phase='CALL',
        binding=binding('f', native_request_id_sha256='f' * 64))
    assert 'OUTPUT_LINEAGE_UNAVAILABLE' in foreign['gaps']


def test_source_exception_is_not_stringified_replaced_or_saved(observer):
    class Error(Exception):
        def __str__(self):
            raise AssertionError('원 예외 읽기 금지')
    error = Error('PRIVATE_EXCEPTION');owner = runner()
    emit(observer, 'GPUModelRunner._prepare_inputs', owner, 'CALL')
    out = emit(observer, 'GPUModelRunner._prepare_inputs', owner, 'RAISE', exception=error)
    assert 'SOURCE_CALL_RAISED' in out['gaps'] and out['metadata']['call_transition']['paired']
    assert 'PRIVATE' not in json.dumps(out) and error.args == ('PRIVATE_EXCEPTION',)


def test_safe_field_reads_never_call_properties_repr_or_custom_iterators(observer):
    class Hostile:
        @property
        def input_ids(self):
            raise AssertionError('속성 실행 금지')
        def __repr__(self):
            raise AssertionError('원문 표시 금지')
    output = emit(observer, 'GPUModelRunner._prepare_inputs', Hostile(), 'CALL')
    assert output['status'] == 'metadata_collected'
    assert output['metadata']['buffers']['input_ids']['descriptor']['status'] == 'unavailable'


def test_missing_foreign_pid_and_mismatched_owner_remain_unqualified(observer):
    owner = runner();point = 'GPUModelRunner._prepare_inputs'
    bad = observer.snapshot(point, (owner,), {}, phase='CALL', binding=binding(pid=os.getpid() + 1))
    assert 'CALL_BINDING_UNAVAILABLE' in bad['gaps']
    emit(observer, point, owner, 'CALL')
    changed = emit(observer, point, runner(), 'RETURN')
    assert changed['metadata']['call_transition']['paired'] is False
    assert 'CALL_OWNER_CHANGED' in changed['gaps']
    assert observer.snapshot(point, (owner,), {}, phase='RETURN')['metadata']['call_transition']['paired'] is False


def test_weak_identity_does_not_retain_tensor_or_owner(observer):
    owner = runner();tensor = owner.input_ids.gpu;ref = weakref.ref(tensor)
    emit(observer, 'GPUModelRunner._prepare_inputs', owner, 'CALL')
    del tensor, owner;gc.collect()
    assert ref() is None
    assert observer.close()['pending_calls'] == 1


def test_duplicate_call_bounds_and_busy_observer_do_not_block_or_run_work():
    observer = GraphBufferObserver(max_objects=2, max_pending=1)
    owner = runner();point = 'GPUModelRunner._prepare_inputs'
    first = emit(observer, point, owner, 'CALL')
    assert 'OBJECT_BOUND' in first['gaps']
    second = emit(observer, point, owner, 'CALL')
    assert 'DUPLICATE_CALL' in second['gaps']
    other = emit(observer, point, owner, 'CALL', call='f')
    assert 'CALL_BOUND' in other['gaps']
    observer._lock.acquire()
    try:
        assert emit(observer, point, owner, 'RETURN')['gaps'] == ['OBSERVER_BUSY']
        assert observer.close()['complete'] is False
    finally:
        observer._lock.release()
    assert observer.close()['complete'] is True
    assert emit(observer, point, owner, 'RETURN')['gaps'] == ['OBSERVER_CLOSED']


def test_bad_inputs_and_fork_generation_are_gaps(observer):
    assert observer.snapshot([], (), {}, phase='CALL')['gaps'] == ['CALL_CONTRACT_UNAVAILABLE']
    owner = runner();observer._pid = -1
    out = emit(observer, 'GPUModelRunner._prepare_inputs', owner, 'CALL')
    assert out['gaps'] == ['PROCESS_CHANGED'] and out['metadata'] == {}
    out = emit(observer, 'GPUModelRunner._prepare_inputs', owner, 'CALL')
    assert out['seq'] is not None


@pytest.mark.parametrize('kwargs', [{'max_objects': True}, {'max_objects': 257}, {'max_pending': 0}])
def test_invalid_bounds_raise_before_observation(kwargs):
    with pytest.raises(ValueError):
        GraphBufferObserver(**kwargs)


def context_module(tmp_path, monkeypatch):
    """EXAMPLE 공개 함수 fixture. 원 vLLM import·실제 engine 실행은 하지 않는다."""
    import hashlib
    import sys
    import types
    from guard.adapters import vllm_graph_buffers as graph
    source = '_forward_context = None\ndef is_forward_context_available():\n    return _forward_context is not None\ndef get_forward_context():\n    assert _forward_context is not None\n    return _forward_context\n'
    path = tmp_path / 'forward_context.py';path.write_text(source)
    module = types.ModuleType('vllm.forward_context');module.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), vars(module))
    monkeypatch.setitem(sys.modules, 'vllm.forward_context', module)
    monkeypatch.setattr(graph, 'FORWARD_CONTEXT_SHA256', hashlib.sha256(source.encode()).hexdigest())
    return graph, module, path


def test_public_pinned_context_reads_actual_key_without_custom_hash_or_equality(tmp_path, monkeypatch, observer):
    graph, module, path = context_module(tmp_path, monkeypatch)
    descriptor = Object(num_tokens=4, num_reqs=2, uniform=True, has_lora=False, num_active_loras=0)
    module._forward_context = Object(batch_descriptor=descriptor, cudagraph_runtime_mode=Object(_name_='FULL'))
    entry = Object(cudagraph=Object(), output=Tensor())
    wrapper = Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={descriptor: entry})
    reads = []
    original = graph.Path.read_bytes
    def read(p):
        reads.append(p)
        return original(p)
    monkeypatch.setattr(graph.Path, 'read_bytes', read)
    a = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'CALL')
    b = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'RETURN', result=entry.output)
    assert a['metadata']['forward_context_source_verified'] is True
    assert b['metadata']['entry_token'] == b['metadata']['known_entries'][0]['entry_token']
    assert b['metadata']['graph_key_fields_matched'] is True
    assert b['metadata']['graph_key_identity_matched'] is True
    assert b['metadata']['selected_runtime_mode'] == 'FULL'
    assert reads == [path]  # 같은 module/file/function identity에서는 재해시·재compile하지 않는다.
    assert observer._context_cache
    assert observer.close()['complete'] is True and observer._context_cache == {}


def test_public_context_absence_is_eager_and_pinned_function_replacement_is_unavailable(tmp_path, monkeypatch, observer):
    graph, module, path = context_module(tmp_path, monkeypatch)
    wrapper = Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={})
    a = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'CALL')
    assert a['metadata']['forward_context_state'] == 'absent'
    assert a['metadata']['execution_path'] == 'eager_no_context'
    assert 'GRAPH_FRAME_UNAVAILABLE' not in a['gaps']
    touched = []
    def replaced():
        touched.append(True)
        return True
    module.is_forward_context_available = replaced
    b = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'RETURN')
    assert b['metadata']['forward_context_state'] == 'unavailable' and touched == []
    assert observer._context_cache == {}


def test_modified_context_source_is_not_reapproved_and_no_context_is_set(tmp_path, monkeypatch, observer):
    graph, module, path = context_module(tmp_path, monkeypatch)
    wrapper = Object(runtime_mode=Object(_name_='FULL'), concrete_cudagraph_entries={})
    emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'CALL')
    path.write_text(path.read_text() + '\n# changed source\n')
    out = emit(observer, 'CUDAGraphWrapper.__call__', wrapper, 'RETURN')
    assert out['metadata']['forward_context_state'] == 'unavailable'
    assert module._forward_context is None and observer._context_cache == {}


def test_native_event_guard_never_initializes_cuda_or_calls_cpu_dummy_query(monkeypatch):
    import sys
    import types
    from guard.adapters.vllm_graph_buffers import _event_query
    touched = []
    class FakeEvent:
        @property
        def device(self):
            touched.append('device')
            raise AssertionError('임의 property 실행 금지')
        def query(self):
            touched.append('query')
            return True
    torch = types.ModuleType('torch');cuda = types.ModuleType('torch.cuda')
    streams = types.ModuleType('torch.cuda.streams');native = types.ModuleType('torch._C')
    torch._C = native;streams.Event = FakeEvent;native._CudaEventBase = Object
    cuda._initialized = False
    cuda.init = lambda: touched.append('init')
    for name, module in [('torch', torch), ('torch.cuda', cuda), ('torch.cuda.streams', streams), ('torch._C', native)]:
        monkeypatch.setitem(sys.modules, name, module)
    assert _event_query(FakeEvent())['complete'] is None
    cuda._initialized = True
    assert _event_query(FakeEvent())['collected'] is False
    assert touched == []


def test_event_query_uncollected_is_not_false_pending_and_no_return_invents_completion(observer):
    owner = Object(_sampled_token_ids=Tensor(), async_copy_ready_event=Event())
    emit(observer, 'AsyncGPUModelRunnerOutput.__init__', owner, 'CALL')
    after = emit(observer, 'AsyncGPUModelRunnerOutput.__init__', owner, 'RETURN')
    assert after['metadata']['event_query']['collected'] is False
    assert after['metadata']['event_query']['complete'] is None
    emit(observer, 'AsyncGPUModelRunnerOutput.get_output', owner, 'CALL', call='e')
    del owner._sampled_token_ids
    out = emit(observer, 'AsyncGPUModelRunnerOutput.get_output', owner, 'RETURN', call='e', result=Object())
    assert out['metadata']['consumer']['get_output_boundary_returned'] is True
    assert out['metadata']['consumer']['event_query_collected'] is False
    assert out['metadata']['consumer']['event_completion_verified'] is None


@pytest.mark.parametrize('complete', [False, True])
def test_example_native_probe_preserves_false_pending_separately_from_missing(complete):
    # 진짜 CUDA 타입 검사는 _event_query가 맡는다. 이 시험은 검증 뒤 조회 순서의 EXAMPLE이다.
    from guard.adapters.vllm_graph_buffers import _query_native_event
    Device = type('device', (), {'__module__': 'torch'})
    device = Device();device.type = 'cuda';device.index = 0
    calls = []
    getter = SimpleNamespace(__get__=lambda *a: device)
    handle = SimpleNamespace(__get__=lambda *a: 1)
    def query(event):
        calls.append('query')
        return complete
    result = _query_native_event(Object(), Object, query, getter, handle, lambda: False, lambda: 0)
    assert result['collected'] is True and result['complete'] is complete and calls == ['query']
    assert result['reason'] == 'NATIVE_EVENT_QUERY'
    assert 'cuda_event' not in result


@pytest.mark.parametrize('case,reason', [
    ('uninitialized', 'CUDA_EVENT_UNINITIALIZED'),
    ('capture', 'CUDA_CAPTURE_ACTIVE'),
    ('device', 'CUDA_EVENT_DEVICE_MISMATCH'),
    ('exception', 'CUDA_EVENT_QUERY_UNAVAILABLE'),
])
def test_example_native_probe_skips_capture_uninitialized_and_device_switch_and_redacts_failure(case, reason):
    from guard.adapters.vllm_graph_buffers import _query_native_event
    Device = type('device', (), {'__module__': 'torch'})
    device = Device();device.type = 'cuda';device.index = 0
    calls = []
    getter = SimpleNamespace(__get__=lambda *a: device)
    handle = SimpleNamespace(__get__=lambda *a: 0 if case == 'uninitialized' else 1)
    def query(event):
        calls.append('query')
        raise RuntimeError('PRIVATE_NATIVE_ERROR')
    result = _query_native_event(Object(), Object, query, getter, handle,
                                 lambda: case == 'capture', lambda: 1 if case == 'device' else 0)
    assert result['collected'] is False and result['complete'] is None
    assert result['reason'] == reason
    assert calls == (['query'] if case == 'exception' else [])
    assert 'PRIVATE' not in json.dumps(result)
