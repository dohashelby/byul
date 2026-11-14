"""정확 RPC 안의 동일 요청·worker 연결을 읽는 미등록 후보.

원 함수/예외를 보존하고 초기 IPC 관측과 같은 객체 HMAC을 사용한다.
이 구성요소 자체는 장애를 주입하거나 생산자를 등록하지 않는다.
"""
from contextvars import ContextVar
import functools
from hashlib import sha256
import hmac
import json
import os
import sys
import threading
import time
import types

from . import vllm_rank_ipc_hooks as ipc
from . import vllm_rank_ipc_sources as sources
from . import vllm_rank_request_identity as request_identity


class RPCObserver:
    def __init__(self, *, image, key, emit, identity_observer):
        self.image, self.key, self.emit = image, key, emit
        self.identity_observer = identity_observer
        self.profile = identity_observer.profile
        self.sources = self.profile.get('rpc_sources', {})
        self.modules, self.functions, self.installed = {}, {}, {}
        self.current = ContextVar('guard_rank_rpc_candidate', default=None)
        self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()

    def _call(self, context):
        if self.pid != os.getpid():
            self.pid, self.serial, self.lock = os.getpid(), 0, threading.RLock()
        with self.lock:
            self.serial += 1
            if self.serial > 4096:
                raise ValueError('RANK_RPC_CALL_BOUND')
            raw = json.dumps(['rpc-call', context, self.serial], sort_keys=True, separators=(',', ':')).encode()
            return hmac.new(self.key, raw, sha256).hexdigest()

    def _fresh(self, required):
        if not set(required) <= set(self.modules):
            raise ValueError('RANK_RPC_SOURCE_GRAPH_INCOMPLETE')
        for name in required:
            module, expected = self.modules[name], self.sources[name]
            sources._bytes(vars(module)['__file__'], expected)
            for point, original in self.functions[name].items():
                if (original.__globals__ is not vars(module)
                        or original.__qualname__ != point
                        or sources.code_digest(sources.relative_code(original.__code__, expected['path']))
                           != expected['body_code_sha256'][point]):
                    raise ValueError('RANK_RPC_SOURCE_DRIFT')
                binding = self.installed.get((name, point))
                if binding is not None and vars(binding[0]).get(binding[1]) is not binding[2]:
                    raise ValueError('RANK_RPC_HOOK_REPLACED')

    @staticmethod
    def _root(original, args, kwargs, selector):
        value = ipc.IPCObserver._argument(original, args, kwargs, selector['root'])
        for index in selector.get('indexes', []):
            if type(index) is not int or type(value) is not tuple or not 0 <= index < len(value):
                return ipc.MISSING
            value = value[index]
        return value

    def _requests(self, original, args, kwargs, spec):
        if 'rpc_envelope' in spec:
            envelope = self._root(original, args, kwargs, spec['rpc_envelope'])
            if type(envelope) is not tuple or len(envelope) != 4:
                return None
            method, rpc_args, rpc_kwargs, output_rank = envelope
            if type(method) is not str or method != spec['rpc_method']:
                return None
            if (type(rpc_args) is not tuple or len(rpc_args) != 1
                    or type(rpc_kwargs) is not dict or rpc_kwargs
                    or not (output_rank is None or type(output_rank) is int and output_rank == 0)):
                raise ValueError('RANK_RPC_REQUEST_UNAVAILABLE')
        value = self._root(original, args, kwargs, spec['requests'])
        if value is None:
            return None
        layout = self.profile['request_layouts'][spec['requests']['layout']]
        return request_identity.requests(value, layout=layout, key=self.key)['request_hmac_sha256']

    @staticmethod
    def _parent_frame(parent):
        frame = sys._getframe(1)
        try:
            for _ in range(20):
                if frame is None:
                    return False
                if (frame.f_code is parent['original'].__code__
                        and frame.f_globals is parent['original'].__globals__
                        and frame.f_locals.get('self') is parent['owner']):
                    return True
                frame = frame.f_back
            return False
        finally:
            frame = None

    def _metadata(self, module_name, point, spec, context, owner, ids, call, event, started,
                  *, parent=None, result=ipc.MISSING):
        self._fresh(spec['source_modules'])
        expected = self.sources[module_name]
        value = {'schema': 'guard-rank-rpc-hook-candidate/1', 'image': self.image,
                 'profile': self.profile['id'], 'source_module': module_name,
                 'source_sha256': expected['sha256'], 'body_code_sha256': expected['body_code_sha256'][point],
                 'point': point, 'phase': spec['phase'], 'role': spec['role'], 'event': event,
                 'context': context, 'call_hmac_sha256': call,
                 'operation_started_ns': started, 'observation_ns': time.monotonic_ns(),
                 'request_hmac_sha256': ids, 'object_hmac_sha256': self.identity_observer._name(owner, context),
                 'source_graph': {name: self.sources[name]['sha256'] for name in spec['source_modules']},
                 'rpc_parent_call_hmac_sha256': None, 'rpc_parent_frame_verified': False,
                 'rpc_parent_worker_verified': False, 'source_authenticated': False,
                 'engine_group_verified': False, 'producer_registered': False, 'gaps': []}
        parent_spec = spec.get('parent')
        if parent_spec is not None:
            valid = (type(parent) is dict and parent['point'] == parent_spec['point']
                     and parent['context'] == context and parent['ids'] == ids
                     and ipc._path(parent['owner'], parent_spec['worker_path']) is owner
                     and self._parent_frame(parent))
            if valid:
                self._fresh(parent['source_modules'])
                value.update(rpc_parent_call_hmac_sha256=parent['call'],
                             rpc_parent_frame_verified=True, rpc_parent_worker_verified=True)
            else:
                value['gaps'].append('RANK_RPC_ACTIVE_PARENT_UNVERIFIED')
        fields = self.identity_observer._fields(spec, {'self': owner}, context, value['gaps'])
        value.update(fields)
        if spec.get('kind') == 'assignment' and event == 'RETURN':
            value['assignment'] = request_identity.assignment(
                result, expected_type=self.profile['assignment_type'], key=self.key)
        elif spec.get('kind') == 'external-id' and event == 'RETURN':
            value['external_request_hmac_sha256'] = request_identity._fingerprints([result], self.key)[0]
        self.emit(value)
        return value

    def _gap(self, point):
        try:
            self.emit({'schema': 'guard-rank-rpc-hook-candidate/1', 'point': point,
                       'source_authenticated': False, 'engine_group_verified': False,
                       'producer_registered': False, 'gaps': ['RANK_RPC_CAPTURE_UNAVAILABLE']})
        except BaseException:
            pass

    def _wrap(self, module_name, point, original, spec):
        @functools.wraps(original)
        def observed(*args, **kwargs):
            owner = ipc.IPCObserver._argument(original, args, kwargs, 'self')
            context, call, ids, token, parent, started = None, None, [], None, None, time.monotonic_ns()
            armed = False
            try:
                context = ipc.base._context()
                if spec['kind'] == 'rpc':
                    ids = self._requests(original, args, kwargs, spec)
                    if ids is not None:
                        call, parent = self._call(context), self.current.get()
                        self._metadata(module_name, point, spec, context, owner, ids, call, 'CALL', started, parent=parent)
                        token = self.current.set({'point': point, 'original': original, 'owner': owner,
                            'context': context, 'ids': ids, 'call': call, 'source_modules': spec['source_modules']})
                        armed = True
                else:
                    call, armed = self._call(context), True
            except BaseException:
                self._gap(point)
            try:
                result = original(*args, **kwargs)
            except BaseException:
                if armed:
                    try:
                        self._metadata(module_name, point, spec, context, owner, ids, call, 'RAISE', started, parent=parent)
                    except BaseException:
                        self._gap(point)
                raise
            else:
                if armed:
                    try:
                        selected = result
                        if spec['kind'] == 'assignment':
                            selected = self._root(original, args, kwargs, spec['assignment'])
                        self._metadata(module_name, point, spec, context, owner, ids, call, 'RETURN', started,
                                       parent=parent, result=selected)
                    except BaseException:
                        self._gap(point)
                return result
            finally:
                if token is not None:
                    self.current.reset(token)
        return observed

    def attach(self, module):
        qualified = sources.qualify(module, image=self.image, scope='rpc_sources')
        name = vars(module)['__name__']
        expected, changes = self.sources[name], []
        for point, spec in expected.get('hooks', {}).items():
            if spec['kind'] not in {'rpc', 'assignment', 'external-id'} or spec['phase'] != 'around-original':
                raise ValueError('RANK_RPC_HOOK_SETTINGS_INVALID')
            original = qualified['functions'][point]
            parts = point.split('.')
            owner = module if len(parts) == 1 else vars(module)[parts[0]]
            member = vars(owner)[parts[-1]]
            wrapped = self._wrap(name, point, original, spec)
            replacement = staticmethod(wrapped) if type(member) is staticmethod else classmethod(wrapped) if type(member) is classmethod else wrapped
            changes.append((owner, parts[-1], member, replacement, point))
        applied = []
        try:
            for owner, attribute, member, replacement, point in changes:
                setattr(owner, attribute, replacement)
                applied.append((owner, attribute, member))
                self.installed[name, point] = owner, attribute, replacement
        except Exception:
            for owner, attribute, member in reversed(applied):
                setattr(owner, attribute, member)
            raise
        self.modules[name], self.functions[name] = module, qualified['functions']
        return {'hooks_installed': len(applied), 'engine_group_verified': False, 'producer_registered': False}
