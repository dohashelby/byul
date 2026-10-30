"""REQ20 B 전용 실객체 관측. 원 연산·동기화·tensor 값에는 접근하지 않는다.

A가 검토한 실제 sourcepoint에서 호출할 callback이며 설치/출처 인증기는 아니다.
버전 변화는 수정 후보일 뿐 CUDA copy 완료나 replay 값 정합을 뜻하지 않는다.
observer를 소유한 수집기가 finally에서 close()한다. 객체는 weakref로만 추적한다.
"""
from __future__ import annotations

from copy import deepcopy
import inspect
import hashlib
from pathlib import Path
import os
import secrets
import threading
import sys
import types
import weakref

from guard.adapters.vllm_slot_mask import _binding, _get, _native_field, tensor_descriptor

SCHEMA = 'byul-v1-graph-buffer-snapshot/1'
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
POINTS = frozenset({
    'GPUModelRunner._prepare_input_ids', 'GPUModelRunner._prepare_inputs',
    'CUDAGraphWrapper.__call__', 'AsyncGPUModelRunnerOutput.__init__',
    'AsyncGPUModelRunnerOutput.get_output',
})
PHASES = frozenset({'CALL', 'RETURN', 'RAISE'})
BUFFER_NAMES = ('input_ids', 'positions', 'inputs_embeds', 'is_token_ids')
MAX_GRAPH_ENTRIES = 16
MAX_OUTPUT_TENSORS = 8
FORWARD_CONTEXT_SHA256 = '8dab5a4dee60e46c4e52821667321c9f05c793578af49cddbdb204cf9324c403'
_MISSING = object()


def _integer(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _argument(args, kwargs, index, name):
    return kwargs.get(name) if name in kwargs else args[index] if len(args) > index else None


def _mode(value):
    name = _get(value, '_name_')
    return name if type(name) is str and name in ('NONE', 'FULL', 'PIECEWISE', 'FULL_AND_PIECEWISE') else None


def _public_forward_context(cache):
    """검토한 0.31 원본 public context 함수만 읽기 호출한다. import/설정은 하지 않는다."""
    module = sys.modules.get('vllm.forward_context')
    if type(module) is not types.ModuleType or sys.flags.optimize:
        return None, 'unavailable'
    try:
        namespace = vars(module)
        filename = namespace.get('__file__')
        if type(filename) is not str:
            return None, 'unavailable'
        path = Path(filename)
        stat = path.stat()
        if path.is_symlink() or path.suffix != '.py' or stat.st_size > 1024 * 1024:
            return None, 'unavailable'
        identity = (filename, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        names = ('is_forward_context_available', 'get_forward_context')
        cached = (cache.get('identity') == identity and cache.get('module', lambda: None)() is module
                  and all(cache.get(name, lambda: None)() is namespace.get(name) for name in names))
        if cached:
            expected_codes = cache['codes']
        else:
            cache.clear()
            source = path.read_bytes()
            if hashlib.sha256(source).hexdigest() != FORWARD_CONTEXT_SHA256:
                return None, 'unavailable'
            compiled = compile(source, filename, 'exec', dont_inherit=True, optimize=0)
            expected_codes = {name: next((item for item in compiled.co_consts
                if type(item) is types.CodeType and item.co_name == name), None) for name in names}
        for name in names:
            fn = namespace.get(name)
            if (type(fn) is not types.FunctionType or fn.__module__ != 'vllm.forward_context'
                    or fn.__globals__ is not namespace or fn.__code__ != expected_codes[name]):
                cache.clear()
                return None, 'unavailable'
        if not cached:
            cache.update(identity=identity, module=weakref.ref(module), codes=expected_codes,
                         **{name: weakref.ref(namespace[name]) for name in names})
        present = namespace['is_forward_context_available']()
        if type(present) is not bool:
            return None, 'unavailable'
        if not present:
            return None, 'absent'
        context = namespace['get_forward_context']()
        return (context, 'available') if context is not None else (None, 'unavailable')
    except Exception:
        return None, 'unavailable'


def _event_query(event):
    """이미 초기화된 실제 native CUDA Event만 비차단 조회한다.

    임의 property/Python query는 실행하지 않는다. native query는 원 event를
    조회할 뿐 record/wait/synchronize/device 선택·event 생성을 수행하지 않는다.
    """
    value = {'collected': False, 'complete': None, 'initialized': None,
             'capture_active': None, 'device_type': None, 'device_index': None,
             'reason': 'NATIVE_CUDA_EVENT_UNAVAILABLE'}
    torch = sys.modules.get('torch')
    cuda = sys.modules.get('torch.cuda')
    streams = sys.modules.get('torch.cuda.streams')
    native = sys.modules.get('torch._C')
    if any(type(module) is not types.ModuleType for module in (torch, cuda, streams, native)):
        return value
    if vars(cuda).get('_initialized') is not True or vars(torch).get('_C') is not native:
        return value
    event_type = vars(streams).get('Event')
    base = vars(native).get('_CudaEventBase')
    capture = vars(native).get('_cuda_isCurrentStreamCapturing')
    current_device = vars(native).get('_cuda_getDevice')
    if (type(event_type) is not type or type(base) is not type or type(event) is not event_type
            or event_type.__module__ != 'torch.cuda.streams' or event_type.__name__ != 'Event'
            or base.__module__ != 'torch._C' or base.__name__ != '_CudaEventBase'
            or base not in event_type.__bases__
            or type(capture) is not types.BuiltinFunctionType
            or capture.__module__ != 'torch._C' or capture.__name__ != '_cuda_isCurrentStreamCapturing'
            or type(current_device) is not types.BuiltinFunctionType
            or current_device.__module__ != 'torch._C' or current_device.__name__ != '_cuda_getDevice'):
        return value
    query = inspect.getattr_static(base, 'query', None)
    device_getter = inspect.getattr_static(event_type, 'device', None)
    handle_getter = inspect.getattr_static(event_type, 'cuda_event', None)
    if (type(query) is not types.MethodDescriptorType or query.__objclass__ is not base
            or type(device_getter) is not types.GetSetDescriptorType or device_getter.__objclass__ is not base
            or type(handle_getter) is not types.GetSetDescriptorType or handle_getter.__objclass__ is not base):
        return value
    return _query_native_event(event, event_type, query, device_getter, handle_getter, capture, current_device)


def _query_native_event(event, event_type, query, device_getter, handle_getter, capture, current_device):
    """검증된 native API의 조회 순서. 테스트는 이 경계만 EXAMPLE로 대조한다."""
    value = {'collected': False, 'complete': None, 'initialized': None,
             'capture_active': None, 'device_type': None, 'device_index': None,
             'reason': 'NATIVE_CUDA_EVENT_UNAVAILABLE'}
    try:
        device = device_getter.__get__(event, event_type)
        # native handle은 초기화 bool 판정 직후 폐기하며 값·hash를 출력하지 않는다.
        initialized = bool(handle_getter.__get__(event, event_type))
        value['initialized'] = initialized
        if not initialized or device is None:
            value['reason'] = 'CUDA_EVENT_UNINITIALIZED'
            return value
        if type(device).__module__ != 'torch' or type(device).__name__ != 'device':
            return value
        if _native_field(device, 'type') != 'cuda':
            return value
        value['device_type'] = 'cuda'
        value['device_index'] = _integer(_native_field(device, 'index'))
        if value['device_index'] is None or current_device() != value['device_index']:
            value['reason'] = 'CUDA_EVENT_DEVICE_MISMATCH'
            return value
        capturing = capture()
        if type(capturing) is not bool:
            return value
        value['capture_active'] = capturing
        if capturing:
            value['reason'] = 'CUDA_CAPTURE_ACTIVE'
            return value
        complete = query(event)
        if type(complete) is bool:
            value.update(collected=True, complete=complete, reason='NATIVE_EVENT_QUERY')
    except Exception:
        value.update(collected=False, complete=None, reason='CUDA_EVENT_QUERY_UNAVAILABLE')
    return value


class GraphBufferObserver:
    """세션 소유의 bounded, non-blocking callback. GPU 참조를 강하게 보관하지 않는다."""

    def __init__(self, *, max_objects=256, max_pending=128):
        if type(max_objects) is not int or not 1 <= max_objects <= 256:
            raise ValueError('GRAPH_OBJECT_BOUND_INVALID')
        if type(max_pending) is not int or not 1 <= max_pending <= 128:
            raise ValueError('GRAPH_PENDING_BOUND_INVALID')
        self._max_objects, self._max_pending = max_objects, max_pending
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._objects = []
        self._context_cache = {}
        self._calls = {}
        self._consumers = {}
        self._seq = 0
        self._closed = False

    def _token(self, value, gaps):
        if value is None or value is _MISSING:
            return None
        self._objects[:] = [(ref, token) for ref, token in self._objects if ref() is not None]
        for ref, token in self._objects:
            if ref() is value:
                return token
        if len(self._objects) >= self._max_objects:
            gaps.add('OBJECT_BOUND')
            return None
        try:
            ref = weakref.ref(value)
        except TypeError:
            gaps.add('OBJECT_IDENTITY_UNAVAILABLE')
            return None
        token = secrets.token_hex(32)
        self._objects.append((ref, token))
        return token

    def _tensor(self, value, gaps):
        descriptor = tensor_descriptor(value)
        collected = descriptor.get('status') == 'collected'
        # 기존 B native metadata getter만 사용한다. 주소·버전 값의 hash는 만들지 않는다.
        version = _integer(_native_field(value, '_version')) if collected else None
        return {'tensor_token': self._token(value, gaps) if collected else None,
                'descriptor': descriptor, 'mutation_version': version}

    def _output(self, value, gaps):
        if value is None:
            return {'tensor_items': [], 'complete': None}
        if type(value) in (tuple, list):
            if len(value) > MAX_OUTPUT_TENSORS:
                gaps.add('OUTPUT_BOUND')
            items = value[:MAX_OUTPUT_TENSORS]
        else:
            items = (value,)
        tensors = [self._tensor(item, gaps) for item in items]
        complete = all(item['descriptor'].get('status') == 'collected' for item in tensors)
        if not complete:
            gaps.add('OUTPUT_METADATA_UNAVAILABLE')
        return {'tensor_items': tensors, 'complete': complete}

    def _descriptor(self, value, gaps):
        return {'token': self._token(value, gaps),
                **{name: _integer(_get(value, name)) for name in ('num_tokens', 'num_reqs', 'num_active_loras')},
                **{name: _get(value, name) if type(_get(value, name)) is bool else None
                   for name in ('uniform', 'has_lora')}}

    def _metadata(self, point, args, kwargs, result, phase, frame, gaps):
        owner = _argument(args, kwargs, 0, 'self')
        data = {'owner_token': self._token(owner, gaps)}
        if point.startswith('GPUModelRunner.'):
            data['input_batch_token'] = self._token(_get(owner, 'input_batch'), gaps)
            buffers = {}
            for name in BUFFER_NAMES:
                buffer = _get(owner, name)
                gpu = _get(buffer, 'gpu', _MISSING)
                # positions가 tensor인 판본/경로도 metadata만 읽는다.
                tensor = buffer if gpu is _MISSING else gpu
                buffers[name] = {'buffer_token': self._token(buffer, gaps), **self._tensor(tensor, gaps)}
            data['buffers'] = buffers
            data['sizes'] = {name: _integer(frame.get(name)) for name in ('num_reqs', 'total_num_scheduled_tokens')}
            if point.endswith('_prepare_input_ids'):
                data['sizes'] = {
                    'num_reqs': _integer(_argument(args, kwargs, 2, 'num_reqs')),
                    'total_num_scheduled_tokens': _integer(_argument(args, kwargs, 3, 'total_num_scheduled_tokens'))}
        elif point == 'CUDAGraphWrapper.__call__':
            data['runtime_mode'] = _mode(_get(owner, 'runtime_mode'))
            data['actual_graph_stream_token'] = None
            gaps.add('GRAPH_STREAM_UNOBSERVED')
            context, context_state = _public_forward_context(self._context_cache)
            data['forward_context_state'] = context_state
            data['forward_context_source_verified'] = context_state in ('available', 'absent')
            descriptor = _get(context, 'batch_descriptor') if context_state == 'available' else frame.get('batch_descriptor')
            selected_mode = _get(context, 'cudagraph_runtime_mode') if context_state == 'available' else frame.get('cudagraph_runtime_mode')
            data['selected_runtime_mode'] = _mode(selected_mode)
            data['descriptor'] = self._descriptor(descriptor, gaps)
            entry = frame.get('entry')
            data['graph_key_identity_matched'] = None
            data['graph_key_fields_matched'] = None
            entries = _get(owner, 'concrete_cudagraph_entries')
            if context_state == 'available' and type(entries) is dict and len(entries) <= MAX_GRAPH_ENTRIES:
                fields = ('num_tokens', 'num_reqs', 'uniform', 'has_lora', 'num_active_loras')
                selected = {name: data['descriptor'][name] for name in fields}
                matches = []
                for key, candidate in entries.items():
                    actual = self._descriptor(key, gaps)
                    if all(actual[name] == selected[name] for name in fields) and selected['num_tokens'] is not None:
                        matches.append((key, candidate))
                if len(matches) == 1:
                    key, entry = matches[0]
                    data['graph_key_fields_matched'] = True
                    data['actual_graph_key_token'] = self._token(key, gaps)
                    data['graph_key_identity_matched'] = key is descriptor
            data['execution_path'] = ('eager_no_context' if context_state == 'absent'
                else 'eager_mode_none' if data['selected_runtime_mode'] == 'NONE'
                else 'eager_mode_mismatch' if data['selected_runtime_mode'] is not None
                    and data['runtime_mode'] is not None and data['selected_runtime_mode'] != data['runtime_mode']
                else 'graph_context_candidate' if data['selected_runtime_mode'] is not None
                else 'unavailable')
            data['entry_token'] = self._token(entry, gaps)
            data['graph_token'] = self._token(_get(entry, 'cudagraph'), gaps)
            data['output'] = self._output(result if phase == 'RETURN' else _get(entry, 'output'), gaps)
            data['argument_tensors'] = [self._tensor(item, gaps) for item in args[1:MAX_OUTPUT_TENSORS + 1]
                                        if tensor_descriptor(item).get('status') == 'collected']
            entries = _get(owner, 'concrete_cudagraph_entries')
            data['known_entries'] = []
            if type(entries) is dict and len(entries) <= MAX_GRAPH_ENTRIES:
                for descriptor, candidate in entries.items():
                    data['known_entries'].append({'entry_token': self._token(candidate, gaps),
                        'descriptor': self._descriptor(descriptor, gaps),
                        'graph_token': self._token(_get(candidate, 'cudagraph'), gaps)})
            else:
                gaps.add('GRAPH_ENTRIES_UNAVAILABLE')
            if context_state == 'unavailable':
                gaps.add('FORWARD_CONTEXT_PROVENANCE_UNAVAILABLE')
            if context_state != 'absent' and (entry is None or descriptor is None):
                gaps.add('GRAPH_FRAME_UNAVAILABLE')
            data['wrapper_return_boundary'] = phase == 'RETURN'
            data['replay_verified'] = None
        else:
            event = _get(owner, 'async_copy_ready_event')
            retained = _get(owner, '_sampled_token_ids', _MISSING)
            data.update(event_token=self._token(event, gaps), event_query=_event_query(event),
                retained_device_output_token=self._token(retained, gaps),
                retained_output=self._tensor(retained, gaps),
                copy_host_output=self._tensor(_get(owner, 'sampled_token_ids_cpu'), gaps))
            data['retained_reference_present'] = retained is not _MISSING and retained is not None
            stream = (_argument(args, kwargs, 5, 'async_output_copy_stream')
                      if point.endswith('__init__') else frame.get('async_output_copy_stream'))
            data['stream_token'] = self._token(stream, gaps)
            data['producer_stream_token'] = self._token(frame.get('default_stream'), gaps)
            if phase == 'RETURN' and point.endswith('get_output'):
                data['returned_output_token'] = self._token(result, gaps)
        return data

    @staticmethod
    def _scope(binding):
        if binding.get('pid') != os.getpid() or not all(binding.get(k) for k in ('session_id_sha256', 'process_start_sha256', 'call_id_sha256')):
            return None
        return tuple(binding.get(k) for k in ('session_id_sha256', 'process_start_sha256',
                      'asgi_request_id_sha256', 'native_request_id_sha256', 'pid'))

    def _transition(self, point, phase, binding, data, seq, gaps):
        scope = self._scope(binding)
        call = binding.get('call_id_sha256')
        owner = data.get('owner_token')
        transition = {'paired': False, 'call_seq': None, 'return_seq': None,
                      'same_owner': None, 'changed_input_versions': []}
        if scope is None or owner is None:
            gaps.add('CALL_BINDING_UNAVAILABLE')
            return transition
        key = (scope, call, point)
        if phase == 'CALL':
            if key in self._calls:
                self._calls[key] = None
                gaps.add('DUPLICATE_CALL')
            elif len(self._calls) < self._max_pending:
                self._calls[key] = (seq, deepcopy(data))
            else:
                gaps.add('CALL_BOUND')
            return transition
        previous = self._calls.pop(key, None)
        if previous is None:
            gaps.add('CALL_PAIR_UNAVAILABLE')
            return transition
        call_seq, before = previous
        same = before.get('owner_token') == owner
        transition.update(paired=same, call_seq=call_seq, return_seq=seq, same_owner=same)
        if not same:
            gaps.add('CALL_OWNER_CHANGED')
            return transition
        for name in BUFFER_NAMES:
            old = before.get('buffers', {}).get(name, {})
            new = data.get('buffers', {}).get(name, {})
            a, b = old.get('mutation_version'), new.get('mutation_version')
            if (old.get('tensor_token') is not None and old.get('tensor_token') == new.get('tensor_token')
                    and a is not None and b is not None and a != b):
                transition['changed_input_versions'].append(name)
        return transition

    def _consumer(self, point, phase, binding, data, transition, seq, gaps):
        state = {'copy_enqueue_boundary_returned': None, 'get_output_boundary_returned': None,
                 'paired_init_call_seq': None, 'pending_output_version_changed': None,
                 'retained_reference_released': None, 'event_completion_verified': None, 'event_query_collected': False,
                 'value_comparison_verified': None}
        scope = self._scope(binding)
        owner = data.get('owner_token')
        if not point.startswith('AsyncGPUModelRunnerOutput.') or scope is None or owner is None:
            return state
        key = (scope, owner)
        if point.endswith('__init__') and phase == 'RETURN' and transition['paired']:
            state['copy_enqueue_boundary_returned'] = True
            query = data.get('event_query', {})
            if data.get('event_token') is not None and query.get('collected') is True:
                state['event_query_collected'] = True
                state['event_completion_verified'] = query['complete']
            if len(self._consumers) >= self._max_pending and key not in self._consumers:
                gaps.add('CONSUMER_BOUND')
            else:
                self._consumers[key] = {'call_seq': transition['call_seq'], 'event_token': data.get('event_token'),
                    'stream_token': data.get('stream_token'), 'tensor_token': data.get('retained_device_output_token'),
                    'version': data.get('retained_output', {}).get('mutation_version')}
        elif point.endswith('get_output'):
            before = self._consumers.get(key)
            if before is None:
                gaps.add('OUTPUT_LINEAGE_UNAVAILABLE')
                return state
            state['paired_init_call_seq'] = before['call_seq']
            data['stream_token'] = before['stream_token']
            if before['event_token'] is None or before['event_token'] != data.get('event_token'):
                gaps.add('COPY_EVENT_CHANGED')
                return state
            query = data.get('event_query', {})
            if query.get('collected') is True:
                state['event_query_collected'] = True
                state['event_completion_verified'] = query['complete']
            token = data.get('retained_device_output_token')
            version = data.get('retained_output', {}).get('mutation_version')
            if phase == 'CALL' and token == before['tensor_token'] and token is not None:
                if before['version'] is not None and version is not None:
                    state['pending_output_version_changed'] = before['version'] != version
            elif phase == 'RETURN' and transition['paired']:
                state['get_output_boundary_returned'] = True
                state['retained_reference_released'] = data.get('retained_reference_present') is False
                self._consumers.pop(key, None)
            elif phase == 'RAISE':
                gaps.add('OUTPUT_CONSUMER_RAISED')
        return state

    def snapshot(self, point, args, kwargs, *, phase, result=None, exception=None, binding=None, frame_locals=None):
        """호출 인자와 고정 선정 locals를 읽는다. 값·원 예외는 출력하지 않는다."""
        output = {'schema': SCHEMA, 'point': point if type(point) is str and point in POINTS else 'unsupported',
            'phase': phase if type(phase) is str and phase in PHASES else 'unsupported',
            'status': 'unavailable', 'seq': None, 'binding': _binding(binding), 'metadata': {}, 'gaps': []}
        if self._pid != os.getpid():
            self._objects.clear(); self._calls.clear(); self._consumers.clear()
            self._context_cache.clear()
            self._pid = os.getpid(); self._lock = threading.Lock()
            output['gaps'] = ['PROCESS_CHANGED']
            return output
        if not self._lock.acquire(blocking=False):
            output['gaps'] = ['OBSERVER_BUSY']
            return output
        gaps = set()
        try:
            if self._closed:
                output['gaps'] = ['OBSERVER_CLOSED']
                return output
            if (output['point'] == 'unsupported' or output['phase'] == 'unsupported'
                    or type(args) not in (tuple, list) or type(kwargs) is not dict
                    or (frame_locals is not None and type(frame_locals) is not dict)):
                output['gaps'] = ['CALL_CONTRACT_UNAVAILABLE']
                return output
            self._seq += 1; output['seq'] = self._seq
            data = self._metadata(point, args, kwargs, result, phase, frame_locals or {}, gaps)
            transition = self._transition(point, phase, output['binding'], data, self._seq, gaps)
            data['call_transition'] = transition
            data['consumer'] = self._consumer(point, phase, output['binding'], data, transition, self._seq, gaps)
            if phase == 'RAISE':
                gaps.add('SOURCE_CALL_RAISED')
            gaps.add('SOURCE_POINT_PROVENANCE_REQUIRED')
            gaps.add('GPU_VALUES_UNOBSERVED')
            if data.get('event_query', {}).get('collected') is not True:
                gaps.add('GPU_COMPLETION_UNOBSERVED')
            output.update(status='metadata_collected', metadata=data, gaps=sorted(gaps))
            return output
        except Exception:
            output['gaps'] = ['OBSERVER_READ_UNAVAILABLE']
            return output
        finally:
            self._lock.release()

    def close(self):
        """소유한 weakref·metadata만 해제한다. CUDA 연산을 호출하지 않는다."""
        if not self._lock.acquire(blocking=False):
            return {'complete': False, 'pending_calls': None, 'pending_consumers': None,
                    'collection_gap': True, 'gaps': ['OBSERVER_BUSY']}
        try:
            calls, consumers = len(self._calls), len(self._consumers)
            self._objects.clear(); self._calls.clear(); self._consumers.clear(); self._context_cache.clear(); self._closed = True
            return {'complete': True, 'pending_calls': calls, 'pending_consumers': consumers,
                    'collection_gap': bool(calls or consumers),
                    'gaps': ['UNFINISHED_SOURCE_CALLS'] if calls or consumers else []}
        finally:
            self._lock.release()
