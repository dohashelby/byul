"""정확 source 후보의 원 호출 앞뒤에서 IPC 메타데이터만 읽는 설치 후보.

함수 인자/반환값·엔진 객체를 바꾸지 않는다. 관측 실패는 원 호출을 막지 않으며
원 함수의 예외는 그대로 전달한다. 공통 입력/판정/생산자 등록에는 연결하지 않는다.
"""
import functools
import hmac
from hashlib import sha256
import inspect
import json
import os
import threading
import time
import types
import weakref

from . import vllm_lifecycle_sensor as base
from . import vllm_rank_ipc_identity as identity
from . import vllm_rank_ipc_sources as sources

MISSING = object()


def _plain(value, key):
    """일반 인스턴스 dict 값만 받는다. descriptor/사용자 getter는 실행하지 않는다."""
    if (inspect.getattr_static(type(value), '__getattribute__', None) is not object.__getattribute__
            or type(inspect.getattr_static(value, '__dict__', None)) is not types.GetSetDescriptorType):
        return MISSING
    descriptor = inspect.getattr_static(type(value), key, MISSING)
    if descriptor is not MISSING and inspect.getattr_static(type(descriptor), '__get__', None) is not None:
        return MISSING
    data = object.__getattribute__(value, '__dict__')
    return data.get(key, MISSING) if type(data) is dict else MISSING


def _path(value, path):
    for key in path:
        if type(key) is not str or value is MISSING:
            return MISSING
        value = _plain(value, key)
    return value


class IPCObserver:
    def __init__(self, *, image, key, emit):
        if type(key) is not bytes or len(key) != 32 or not callable(emit):
            raise ValueError('RANK_IPC_HOOK_SETTINGS_INVALID')
        data = json.loads(sources.PROFILES.read_bytes())
        matches = [p for p in data['profiles'] if p['image'] == image]
        if len(matches) != 1:
            raise ValueError('RANK_IPC_HOOK_SETTINGS_INVALID')
        self.profile, = matches
        self.image, self.key, self.emit = image, key, emit
        self.pid, self.serial, self.objects = os.getpid(), 0, {}
        self.lock = threading.RLock()
        self.modules = {}

    def _name(self, value, context):
        if value is MISSING:
            return None
        if self.pid != os.getpid():
            self.pid, self.serial, self.objects = os.getpid(), 0, {}
            self.lock = threading.RLock()
        with self.lock:
            entry = self.objects.get(id(value))
            if entry is not None and entry[0]() is value:
                return entry[1]
            if len(self.objects) >= 256:
                raise ValueError('RANK_IPC_OBJECT_BOUND')
            reference = weakref.ref(value)
            self.serial += 1
            raw = json.dumps([context, self.serial], sort_keys=True, separators=(',', ':')).encode()
            name = hmac.new(self.key, raw, sha256).hexdigest()
            self.objects[id(value)] = reference, name
            return name

    @staticmethod
    def _argument(original, args, kwargs, name):
        code = original.__code__
        positional = code.co_varnames[:code.co_argcount]
        if name in positional:
            index = positional.index(name)
            return args[index] if index < len(args) else kwargs.get(name, MISSING)
        return kwargs.get(name, MISSING) if name in code.co_varnames[code.co_argcount:code.co_argcount + code.co_kwonlyargcount] else MISSING

    def _fields(self, spec, roots, context, gaps):
        def select(item):
            return _path(roots.get(item.get('root', 'item'), MISSING), item['path'])
        result = {'scalars': {}, 'endpoints': {}, 'processes': {}}
        for name, item in spec.get('scalars', {}).items():
            value = select(item)
            if type(value) is not int or not 0 <= value < 2**63:
                result['scalars'][name] = None
                gaps.append('RANK_IPC_SCALAR_UNAVAILABLE:' + name)
            else:
                result['scalars'][name] = value
        for name, item in spec.get('endpoints', {}).items():
            value = select(item)
            try:
                capture = {'pipe': identity.pipe_endpoint, 'shared-memory': identity.shared_memory_endpoint}[item['kind']]
                result['endpoints'][name] = capture(value, key=self.key)
            except (ValueError, KeyError, TypeError):
                gaps.append('RANK_IPC_ENDPOINT_UNAVAILABLE:' + name)
        for name, item in spec.get('processes', {}).items():
            try:
                result['processes'][name] = identity.process_context(select(item))
            except ValueError:
                gaps.append('RANK_IPC_PROCESS_UNAVAILABLE:' + name)
        return result

    def _capture(self, module, point, original, spec, args, kwargs, expected, body):
        started = time.monotonic_ns()
        # 감싸기 뒤 원 함수/설치 파일이 바뀌어도 이전 자격을 그대로 쓰지 않는다.
        sources._bytes(vars(module)['__file__'], expected)
        if sources.code_digest(sources.relative_code(original.__code__, expected['path'])) != body:
            raise ValueError('RANK_IPC_SOURCE_DRIFT')
        context = base._context()
        roots = {name: self._argument(original, args, kwargs, name)
                 for name in original.__code__.co_varnames[:original.__code__.co_argcount + original.__code__.co_kwonlyargcount]}
        gaps = []
        required = spec.get('source_modules', list(self.profile['sources']))
        if (type(required) is not list or not required or len(required) != len(set(required))
                or not set(required) <= set(self.profile['sources'])):
            raise ValueError('RANK_IPC_SOURCE_GRAPH_INVALID')
        if not set(required) <= set(self.modules):
            gaps.append('RANK_IPC_SOURCE_GRAPH_INCOMPLETE')
        result = {'schema': 'guard-rank-ipc-hook-candidate/1', 'image': self.image,
                  'profile': self.profile['id'], 'point': point, 'phase': spec['phase'],
                  'role': spec['role'], 'source_module': vars(module)['__name__'],
                  'source_sha256': expected['sha256'], 'body_code_sha256': body,
                  'source_graph': dict(self.modules), 'context': context,
                  'observation_started_ns': started,
                  'object_hmac_sha256': self._name(roots.get('self', MISSING), context),
                  'source_authenticated': False, 'engine_group_verified': False,
                  'producer_registered': False, 'gaps': gaps}
        result.update(self._fields(spec, roots, context, gaps))
        if 'members' in spec:
            member_spec = spec['members']
            values = _path(roots.get(member_spec['root'], MISSING), member_spec['path'])
            result['members'] = []
            if type(values) is not list or not 1 <= len(values) <= 64:
                gaps.append('RANK_IPC_MEMBERS_UNAVAILABLE')
            else:
                before = tuple(values)
                for value in before:
                    item = self._fields(member_spec, {'item': value}, context, gaps)
                    item['object_hmac_sha256'] = self._name(value, context)
                    result['members'].append(item)
                if tuple(map(id, before)) != tuple(map(id, values)):
                    gaps.append('RANK_IPC_MEMBERS_CHANGED')
        result['observation_finished_ns'] = time.monotonic_ns()
        self.emit(result)

    def _wrap(self, module, point, original, spec, expected, body):
        @functools.wraps(original)
        def observed(*args, **kwargs):
            def capture():
                try:
                    self._capture(module, point, original, spec, args, kwargs, expected, body)
                except Exception:
                    try:
                        self.emit({'schema': 'guard-rank-ipc-hook-candidate/1', 'point': point,
                                   'source_authenticated': False, 'engine_group_verified': False,
                                   'producer_registered': False, 'gaps': ['RANK_IPC_CAPTURE_UNAVAILABLE']})
                    except Exception:
                        pass
            if spec['phase'] == 'before-original':
                capture()
            result = original(*args, **kwargs)
            if spec['phase'] == 'after-original':
                capture()
            return result
        return observed

    def attach(self, module):
        """모든 대상 본문을 먼저 대조한 뒤 명시된 호출 경계만 감싼다."""
        qualified = sources.qualify(module, image=self.image)
        name = vars(module)['__name__']
        expected = self.profile['sources'][name]
        changes = []
        for point, spec in expected.get('hooks', {}).items():
            if spec['phase'] not in {'before-original', 'after-original'}:
                raise ValueError('RANK_IPC_HOOK_SETTINGS_INVALID')
            original = qualified['functions'][point]
            parts = point.split('.')
            owner = module if len(parts) == 1 else vars(module)[parts[0]]
            member = vars(owner)[parts[-1]]
            wrapped = self._wrap(module, point, original, spec, expected, qualified['body_code_sha256'][point])
            replacement = staticmethod(wrapped) if type(member) is staticmethod else classmethod(wrapped) if type(member) is classmethod else wrapped
            changes.append((owner, parts[-1], member, replacement))
        applied = []
        try:
            for owner, attribute, member, replacement in changes:
                setattr(owner, attribute, replacement)
                applied.append((owner, attribute, member))
        except Exception:
            for owner, attribute, member in reversed(applied):
                setattr(owner, attribute, member)
            raise
        self.modules[name] = qualified['source_sha256']
        return {'hooks_installed': len(applied), 'engine_group_verified': False, 'producer_registered': False}
