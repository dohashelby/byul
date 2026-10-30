"""LIF07의 읽기 전용 Worker 함수 경계 관측. 설치·출처 자격은 A가 제공한다.

원 함수 RETURN은 Python 경계 정상 반환이다. CUDA 완료·모델 준비·READY
전송·서비스 접수 gate는 이 callback의 관측 범위에 포함하지 않는다.
"""
from __future__ import annotations

import os
import secrets
import threading
import weakref

from guard.adapters.vllm_slot_mask import BINDING_HASHES, _binding, _get

SCHEMA = 'byul-v1-worker-readiness-snapshot/1'
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
POINTS = frozenset({'Worker.init_device', 'Worker.load_model',
    'Worker.initialize_from_config', 'Worker.compile_or_warm_up_model'})
PHASES = frozenset({'CALL', 'RETURN', 'RAISE'})
STATUSES = frozenset({'unavailable', 'metadata_collected'})
GRAPH_MODES = frozenset({'NONE', 'FULL', 'PIECEWISE', 'FULL_AND_PIECEWISE'})
GAPS = frozenset({'PROCESS_CHANGED', 'OBSERVER_BUSY', 'OBSERVER_CLOSED',
    'CALL_CONTRACT_UNAVAILABLE', 'OWNER_UNAVAILABLE', 'OBJECT_BOUND',
    'OBJECT_IDENTITY_UNAVAILABLE', 'CALL_BINDING_UNAVAILABLE', 'SESSION_CHANGED',
    'DUPLICATE_CALL', 'CALL_BOUND', 'CALL_PAIR_UNAVAILABLE', 'CALL_ORDER_UNAVAILABLE',
    'OWNER_CHANGED', 'RANK_UNAVAILABLE', 'CONFIG_UNAVAILABLE',
    'DUMMY_FLAG_UNAVAILABLE', 'SOURCE_CALL_RAISED', 'SOURCE_POINT_PROVENANCE_REQUIRED',
    'GPU_COMPLETION_UNOBSERVED', 'READY_SEND_UNOBSERVED', 'SERVICE_GATE_UNOBSERVED',
    'OBSERVER_READ_UNAVAILABLE', 'UNFINISHED_SOURCE_CALLS'})


def _uint(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


def _bool(value):
    return value if type(value) is bool else None


def _mode(value):
    # Enum의 정해진 이름만 읽고 사용자 __str__/property는 호출하지 않는다.
    name = _get(value, '_name_')
    return name if type(name) is str and name in GRAPH_MODES else None


class WorkerReadinessObserver:
    """세션 소유 bounded callback. Worker·모델·tensor의 강한 참조를 보관하지 않는다."""

    def __init__(self, *, max_objects=128, max_pending=64):
        if type(max_objects) is not int or not 1 <= max_objects <= 128:
            raise ValueError('WORKER_READINESS_OBJECT_BOUND_INVALID')
        if type(max_pending) is not int or not 1 <= max_pending <= 64:
            raise ValueError('WORKER_READINESS_PENDING_BOUND_INVALID')
        self._max_objects, self._max_pending = max_objects, max_pending
        self._pid = os.getpid()
        self._lock = threading.Lock()
        self._objects = []
        self._calls = {}
        self._stacks = {}
        self._scope = None
        self._seq = 0
        self._closed = False

    def _clear(self):
        self._objects.clear()
        self._calls.clear()
        self._stacks.clear()
        self._scope = None

    def _token(self, value, gaps):
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

    @staticmethod
    def _binding_scope(binding):
        if binding.get('pid') != os.getpid() or not all(binding.get(k) for k in (
                'session_id_sha256', 'process_start_sha256', 'call_id_sha256')):
            return None
        return tuple(binding[k] for k in ('session_id_sha256', 'process_start_sha256', 'pid'))

    def _metadata(self, point, owner, kwargs, gaps):
        rank, local = _uint(_get(owner, 'rank')), _uint(_get(owner, 'local_rank'))
        if rank is None or local is None:
            gaps.add('RANK_UNAVAILABLE')
        # 정해진 scalar만 읽는다. 모델 신원과 model_runner는 읽지 않는다.
        model_config = _get(owner, 'model_config')
        config = _get(owner, 'vllm_config')
        compilation = _get(config, 'compilation_config')
        eager = _bool(_get(model_config, 'enforce_eager'))
        mode = _mode(_get(compilation, 'cudagraph_mode'))
        if eager is None or mode is None:
            gaps.add('CONFIG_UNAVAILABLE')
        dummy = _bool(kwargs.get('load_dummy_weights')) if point == 'Worker.load_model' else None
        if point == 'Worker.load_model' and dummy is None:
            gaps.add('DUMMY_FLAG_UNAVAILABLE')
        return {'owner_token': self._token(owner, gaps),
            'thread_token': self._token(threading.current_thread(), gaps),
            'rank': rank, 'local_rank': local, 'enforce_eager': eager,
            'configured_graph_mode': mode, 'explicit_load_dummy_weights': dummy,
            'source_qualified': False, 'actual_worker_observed': False,
            'gpu_completion_verified': None, 'all_model_ready_verified': None,
            'ready_send_verified': None, 'service_gate_verified': None}

    def _transition(self, point, phase, binding, data, seq, scope, gaps):
        value = {'paired': False, 'call_seq': None, 'end_seq': None,
            'same_owner': None, 'same_thread': None, 'returned': None}
        thread, owner = data['thread_token'], data['owner_token']
        if scope is None or thread is None or owner is None:
            gaps.add('CALL_BINDING_UNAVAILABLE')
            return value
        key = (scope, thread, tuple(binding.get(k) for k in BINDING_HASHES), point)
        if phase == 'CALL':
            if key in self._calls:
                # 같은 키의 중복은 어느 호출에도 연결하지 않고 추가 보관하지 않는다.
                self._calls[key] = None
                gaps.add('DUPLICATE_CALL')
            elif len(self._calls) >= self._max_pending:
                gaps.add('CALL_BOUND')
            else:
                self._calls[key] = (seq, owner)
                self._stacks.setdefault(thread, []).append(key)
            return value
        if key not in self._calls:
            gaps.add('CALL_PAIR_UNAVAILABLE')
            return value
        stack = self._stacks.get(thread, [])
        if not stack or stack[-1] != key:
            # 순서가 불명확해진 thread의 pending은 후속 RETURN에도 재사용하지 않는다.
            for pending in stack:
                self._calls.pop(pending, None)
            self._stacks.pop(thread, None)
            gaps.add('CALL_ORDER_UNAVAILABLE')
            return value
        stack.pop()
        if not stack:
            self._stacks.pop(thread, None)
        previous = self._calls.pop(key)
        if previous is None:
            gaps.add('CALL_PAIR_UNAVAILABLE')
            return value
        call_seq, before_owner = previous
        same = owner == before_owner
        value.update(call_seq=call_seq, end_seq=seq, same_owner=same, same_thread=True)
        if not same:
            gaps.add('OWNER_CHANGED')
            return value
        value.update(paired=True, returned=phase == 'RETURN')
        return value

    def snapshot(self, point, args, kwargs, *, phase, result=None, exception=None, binding=None):
        """원 args의 고정 속성만 읽는다. result/exception의 본문·repr는 소비하지 않는다."""
        output = {'schema': SCHEMA, 'point': point if type(point) is str and point in POINTS else 'unsupported',
            'phase': phase if type(phase) is str and phase in PHASES else 'unsupported',
            'status': 'unavailable', 'seq': None, 'binding': _binding(binding), 'metadata': {}, 'gaps': []}
        if self._pid != os.getpid():
            self._clear()
            self._pid = os.getpid()
            self._seq = 0
            self._lock = threading.Lock()
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
                    or type(args) not in (tuple, list) or type(kwargs) is not dict):
                output['gaps'] = ['CALL_CONTRACT_UNAVAILABLE']
                return output
            if not args or args[0] is None:
                output['gaps'] = ['OWNER_UNAVAILABLE']
                return output
            scope = self._binding_scope(output['binding'])
            if scope is not None:
                if self._scope is not None and self._scope != scope:
                    self._clear()
                    gaps.add('SESSION_CHANGED')
                self._scope = scope
            self._seq += 1
            output['seq'] = self._seq
            data = self._metadata(point, args[0], kwargs, gaps)
            data['call_transition'] = self._transition(point, phase, output['binding'], data, self._seq, scope, gaps)
            if phase == 'RAISE':
                gaps.add('SOURCE_CALL_RAISED')
            gaps.update({'SOURCE_POINT_PROVENANCE_REQUIRED', 'GPU_COMPLETION_UNOBSERVED',
                         'READY_SEND_UNOBSERVED', 'SERVICE_GATE_UNOBSERVED'})
            output.update(status='metadata_collected', metadata=data, gaps=sorted(gaps))
            return output
        except Exception:
            output['gaps'] = ['OBSERVER_READ_UNAVAILABLE']
            return output
        finally:
            self._lock.release()

    def close(self):
        """이 observer가 소유한 약한 참조와 pending만 해제한다."""
        if self._pid != os.getpid():
            self._clear()
            self._pid = os.getpid()
            self._lock = threading.Lock()
        if not self._lock.acquire(blocking=False):
            return {'complete': False, 'pending_calls': None, 'collection_gap': True, 'gaps': ['OBSERVER_BUSY']}
        try:
            pending = len(self._calls)
            self._clear()
            self._closed = True
            return {'complete': True, 'pending_calls': pending, 'collection_gap': bool(pending),
                'gaps': ['UNFINISHED_SOURCE_CALLS'] if pending else []}
        finally:
            self._lock.release()
