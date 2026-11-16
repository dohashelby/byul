"""Request-free, source-bound local call facts for the 0.7 V1 profiler path.

This candidate does not connect the API process to the core or worker process.
It never inspects request IDs, headers, serialized messages, or profiler output.
The existing /32 Worker.profile observer remains the direct backend observer.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import os
import re
import secrets
import sys
import threading
import time
import types
import weakref

from . import vllm_lifecycle_sensor as base
from . import vllm_rank_ipc_sources as sources


SCHEMA = 'guard-baseline-profiler-local-candidate/1'
IMAGE = 'vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8'
RUNTIMES = frozenset({'cpython-3.12.3', 'cpython-3.12.8'})
SPECS = {
    'vllm.v1.engine.async_llm': ('vllm/v1/engine/async_llm.py', 14054,
        'c6386700282f55a46b1f103331020e5713d59acf06b12a3e64b0eca40a3aa9b7',
        ('AsyncLLM.start_profile', 'AsyncLLM.stop_profile')),
    'vllm.v1.engine.core_client': ('vllm/v1/engine/core_client.py', 10790,
        'e8fcc24c3b8ff15dc5aed40ce28ac3ebeee9e298e62855ffbf4f781bc00e9cf6',
        ('AsyncMPClient.profile_async',)),
    'vllm.v1.engine.core': ('vllm/v1/engine/core.py', 11486,
        '091b574c562202ccc60139e3d1a9cdd29f0fd33cbffa286ca4ae76f460ee3a16',
        ('EngineCore._handle_client_request',)),
    'vllm.v1.executor.abstract': ('vllm/v1/executor/abstract.py', 3585,
        '6e0c838ef1e866b15f8176da0af117dc4661efe387fbcca20d521020c7b1970a',
        ('Executor.profile',)),
    'vllm.v1.executor.multiproc_executor': ('vllm/v1/executor/multiproc_executor.py', 14086,
        '5db418332360f43e2a9718f62d8ec5d856d554b8d0d27d14f053fb3392d33239',
        ('MultiprocExecutor.collective_rpc',)),
}
POINTS = {point: (module, path, digest) for module, (path, _, digest, names) in SPECS.items()
          for point in names}
SEGMENTS = {
    'api': frozenset({'vllm.v1.engine.async_llm', 'vllm.v1.engine.core_client'}),
    'core': frozenset({'vllm.v1.engine.core', 'vllm.v1.executor.abstract',
                      'vllm.v1.executor.multiproc_executor'}),
}
PARENTS = {
    'AsyncMPClient.profile_async': frozenset({'AsyncLLM.start_profile', 'AsyncLLM.stop_profile'}),
    'Executor.profile': frozenset({'EngineCore._handle_client_request'}),
    'MultiprocExecutor.collective_rpc': frozenset({'Executor.profile'}),
}
FIELDS = frozenset({'schema', 'point', 'phase', 'binding', 'runtime', 'source_sha256',
    'call_sha256', 'owner_sha256', 'parent_call_sha256', 'parent_frame_verified',
    'is_start', 'started_ns', 'finished_ns', 'exception_kind', 'original_invoked',
    'cross_process_rpc_verified', 'producer_authenticated', 'whole_scope_complete'})
_CURRENT = contextvars.ContextVar('baseline_profiler_local_call', default=None)
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _runtime():
    return sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))


def qualify(modules, *, image):
    """Validate one process-local segment completely before wrapping any hook."""
    try:
        if (image not in {IMAGE, 'docker.io/' + IMAGE} or _runtime() not in RUNTIMES
                or sys.flags.optimize != 0 or os.name != 'posix' or type(modules) is not dict
                or frozenset(modules) not in (SEGMENTS['api'], SEGMENTS['core'], frozenset(SPECS))):
            raise ValueError
        functions = {}
        for name in modules:
            path, size, digest, points = SPECS[name]
            module = modules[name]
            if type(module) is not types.ModuleType or vars(module).get('__name__') != name:
                raise ValueError
            raw = sources._bytes(vars(module)['__file__'], {'bytes': size, 'sha256': digest})
            compiled = sources._codes(compile(raw, vars(module)['__file__'], 'exec', dont_inherit=True, optimize=0))
            for point in points:
                original = sources._function(module, point)
                if (original.__qualname__ != point or
                    base.code_digest(sources.relative_code(original.__code__, path)) !=
                    base.code_digest(sources.relative_code(compiled[point], path))):
                    raise ValueError
                functions[point] = (module, original)
        return functions
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SyntaxError):
        raise ValueError('BASELINE_PROFILER_LOCAL_SOURCE_UNAVAILABLE') from None


class LocalProfilerObserver:
    def __init__(self, emit, *, session, binding_factory):
        if (not callable(emit) or not callable(binding_factory) or type(session) is not str
                or re.fullmatch('[0-9a-f]{32}', session) is None):
            raise ValueError('BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE')
        self.emit, self.session, self.binding_factory = emit, session, binding_factory
        self.pid, self.disabled, self.installed = None, False, []
        self._process()

    def _process(self):
        if self.pid != os.getpid():
            binding = self.binding_factory()
            if (type(binding) is not dict or set(binding) != {'pid', 'session_id_sha256', 'process_start_sha256'}
                    or type(binding['pid']) is not int or binding['pid'] != os.getpid()
                    or any(type(binding[k]) is not str
                    or _SHA.fullmatch(binding[k]) is None for k in ('session_id_sha256', 'process_start_sha256'))):
                raise ValueError('BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE')
            if binding['session_id_sha256'] != hashlib.sha256(self.session.encode()).hexdigest():
                raise ValueError('BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE')
            self.binding, self.pid = dict(binding), os.getpid()
            self.owners = weakref.WeakKeyDictionary()
            self.lock = threading.RLock()

    def _owner(self, value):
        with self.lock:
            try:
                if value not in self.owners:
                    if len(self.owners) >= 256:
                        return None
                    self.owners[value] = secrets.token_hex(32)
                return self.owners[value]
            except TypeError:
                return None

    @staticmethod
    def _parent_frame(parent):
        frame = inspect.currentframe()
        try:
            for _ in range(24):
                frame = frame.f_back if frame is not None else None
                if frame is None:
                    return False
                if (frame.f_code is parent['original'].__code__
                        and frame.f_globals is parent['original'].__globals__
                        and frame.f_locals.get('self') is parent['owner']):
                    return True
            return False
        finally:
            del frame

    def _begin(self, point, original, args, kwargs):
        self._process()
        owner = args[0] if args else kwargs.get('self')
        owner_id = self._owner(owner)
        if owner_id is None:
            return None
        flag = ({'AsyncLLM.start_profile': True, 'AsyncLLM.stop_profile': False}
                .get(point))
        if point in {'AsyncMPClient.profile_async', 'Executor.profile'}:
            raw = args[1] if len(args) > 1 else kwargs.get('is_start', True)
            flag = raw if type(raw) is bool else None
        if point == 'EngineCore._handle_client_request':
            request = args[1] if len(args) > 1 else kwargs.get('request')
            # The pinned native branch uses isinstance(request, EngineCoreProfile).
            # Exact class identity is narrower and cannot mislabel an abort/list
            # branch whose class merely copies the name and module strings.
            if type(request) is not original.__globals__.get('EngineCoreProfile'):
                return None
            raw = vars(request).get('is_start')
            flag = raw if type(raw) is bool else None
        if point == 'MultiprocExecutor.collective_rpc':
            method = args[1] if len(args) > 1 else kwargs.get('method')
            values = args[3] if len(args) > 3 else kwargs.get('args', ())
            if type(method) is not str or method != 'profile' or type(values) is not tuple or len(values) != 1:
                return None
            flag = values[0] if type(values[0]) is bool else None
        if flag is None:
            return None
        parent = _CURRENT.get()
        linked = False
        if (parent is not None and parent['observer'] is self
                and parent['point'] in PARENTS.get(point, ())
                and parent['pid'] == self.pid and parent['thread'] == threading.get_ident()
                and self._parent_frame(parent)):
            expected = (vars(parent['owner']).get('engine_core') is owner
                        if point == 'AsyncMPClient.profile_async' else
                        vars(parent['owner']).get('model_executor') is owner
                        if point == 'Executor.profile' else parent['owner'] is owner)
            linked = bool(expected and parent['flag'] is flag)
        call = secrets.token_hex(32)
        value = {'schema': SCHEMA, 'point': point, 'phase': 'CALL', 'binding': dict(self.binding),
            'runtime': _runtime(), 'source_sha256': POINTS[point][2], 'call_sha256': call,
            'owner_sha256': owner_id, 'parent_call_sha256': parent['call'] if linked else None,
            'parent_frame_verified': linked, 'is_start': flag, 'started_ns': time.monotonic_ns(),
            'finished_ns': None, 'exception_kind': None, 'original_invoked': False,
            'cross_process_rpc_verified': False, 'producer_authenticated': False,
            'whole_scope_complete': False}
        return value, {'observer': self, 'point': point, 'original': original, 'owner': owner, 'call': call,
            'pid': self.pid, 'thread': threading.get_ident(), 'flag': flag}

    def _emit(self, value):
        try:
            self.emit(value)
        except BaseException:
            self.disabled = True

    def _wrap(self, point, original, module):
        observer = self
        path = POINTS[point][1]
        expected = base.code_digest(sources.relative_code(original.__code__, path))
        def begin(args, kwargs):
            if observer.disabled:
                return None
            if (getattr(observer, 'require_fresh_install', False)
                    and observer.pid != os.getpid()):
                return None  # inherited hook is inert until the router reinstalls
            try:
                name = POINTS[point][0]
                _, size, digest, _ = SPECS[name]
                sources._bytes(vars(module)['__file__'], {'bytes': size, 'sha256': digest})
                if base.code_digest(sources.relative_code(original.__code__, path)) != expected:
                    observer.disabled = True
                    return None
                value = observer._begin(point, original, args, kwargs)
                if value is not None:
                    observer._emit(value[0])
                return value if not observer.disabled else None
            except BaseException:
                observer.disabled = True
                return None
        def finish(value, phase, error):
            try:
                name = POINTS[point][0]
                _, size, digest, _ = SPECS[name]
                sources._bytes(vars(module)['__file__'], {'bytes': size, 'sha256': digest})
                if base.code_digest(sources.relative_code(original.__code__, path)) != expected:
                    raise ValueError
                observer._emit({**value, 'phase': phase, 'finished_ns': time.monotonic_ns(),
                    'exception_kind': None if error is None else
                        'exception' if isinstance(error, Exception) else 'base-exception',
                    'original_invoked': True})
            except BaseException:
                observer.disabled = True
        if inspect.iscoroutinefunction(original):
            @functools.wraps(original)
            async def wrapped(*args, **kwargs):
                value = begin(args, kwargs)
                if value is None:
                    return await original(*args, **kwargs)
                row, parent = value
                token = _CURRENT.set(parent)
                phase, error = 'RAISE', None
                try:
                    result = await original(*args, **kwargs)
                    phase = 'RETURN'
                    return result
                except BaseException as caught:
                    error = caught
                    raise
                finally:
                    finish(row, phase, error)
                    try:
                        _CURRENT.reset(token)
                    except BaseException:
                        observer.disabled = True
        else:
            @functools.wraps(original)
            def wrapped(*args, **kwargs):
                value = begin(args, kwargs)
                if value is None:
                    return original(*args, **kwargs)
                row, parent = value
                token = _CURRENT.set(parent)
                phase, error = 'RAISE', None
                try:
                    result = original(*args, **kwargs)
                    phase = 'RETURN'
                    return result
                except BaseException as caught:
                    error = caught
                    raise
                finally:
                    finish(row, phase, error)
                    try:
                        _CURRENT.reset(token)
                    except BaseException:
                        observer.disabled = True
        return wrapped

    def install(self, modules, *, image):
        functions = qualify(modules, image=image)
        for point, (module, original) in functions.items():
            owner = vars(module)[point.split('.')[0]]
            wrapped = self._wrap(point, original, module)
            setattr(owner, point.split('.')[1], wrapped)
            self.installed.append((owner, point.split('.')[1], original, wrapped))
        return {'hooks_installed': len(self.installed), 'producer_registered': False}

    def close(self):
        self.disabled = True
        restored = True
        for owner, name, original, wrapped in reversed(self.installed):
            if vars(owner).get(name) is wrapped:
                setattr(owner, name, original)
            else:
                restored = False
        self.installed.clear()
        self.owners.clear()
        return {'restored': restored, 'producer_authenticated': False}


class LocalProfilerRouter:
    """Stage only already-loaded modules, then install one exact local segment.

    ``observe_loaded`` never imports engine modules. The caller owns the raw
    record sink, session, process-start binding factory, image and opt-in flag.
    The factory is called again in each process; a stale inherited binding is
    rejected by LocalProfilerObserver before a new hook is installed.
    """

    def __init__(self, emit, *, session, binding_factory, image, enabled=False):
        if (not callable(emit) or not callable(binding_factory) or type(session) is not str
                or re.fullmatch('[0-9a-f]{32}', session) is None or type(image) is not str
                or type(enabled) is not bool):
            raise ValueError('BASELINE_PROFILER_LOCAL_ROUTER_INVALID')
        self.emit, self.session, self.binding_factory = emit, session, binding_factory
        self.image, self.enabled = image, enabled
        self.pid = os.getpid()
        self.pending = {name: {} for name in SEGMENTS}
        self.observers = {}
        self.installed_modules = {}
        self.failed = set()
        self.previous_start_sha256 = set()
        self.last_diagnostic = 'DISABLED' if not enabled else 'WAITING_FOR_SEGMENT'

    def _reset_after_fork(self):
        if self.pid == os.getpid():
            return
        self.previous_start_sha256 = {
            observer.binding['process_start_sha256'] for observer in self.observers.values()}
        for observer in self.observers.values():
            try:
                if observer.close()['restored'] is not True:
                    raise ValueError
            except BaseException:
                self.last_diagnostic = 'INHERITED_HOOK_RESTORE_FAILED'
                self.enabled = False
                return
        self.pid = os.getpid()
        self.pending = {name: {} for name in SEGMENTS}
        self.observers = {}
        self.installed_modules = {}
        self.failed.clear()
        self.last_diagnostic = 'WAITING_FOR_SEGMENT'

    def observe_loaded(self, module):
        """Return True only on a fresh complete installation in this call."""
        self._reset_after_fork()
        if not self.enabled:
            if self.last_diagnostic != 'INHERITED_HOOK_RESTORE_FAILED':
                self.last_diagnostic = 'DISABLED'
            return False
        if type(module) is not types.ModuleType:
            self.last_diagnostic = 'UNSUPPORTED_MODULE'
            return False
        name = vars(module).get('__name__')
        segment = next((part for part, names in SEGMENTS.items() if name in names), None)
        if segment is None:
            self.last_diagnostic = 'UNSUPPORTED_MODULE'
            return False
        if sys.modules.get(name) is not module:
            self.last_diagnostic = 'MODULE_NOT_LOADED'
            return False
        if segment in self.observers:
            self.last_diagnostic = ('ALREADY_INSTALLED'
                if all(sys.modules.get(part) is candidate
                    for part, candidate in self.installed_modules[segment].items())
                else 'MODULE_REPLACED')
            return False
        if segment in self.failed:
            self.last_diagnostic = 'SEGMENT_FAILED'
            return False
        staged = self.pending[segment]
        if name in staged and staged[name] is not module:
            self.failed.add(segment)
            self.last_diagnostic = 'MODULE_REPLACED'
            return False
        staged[name] = module
        if frozenset(staged) != SEGMENTS[segment]:
            self.last_diagnostic = 'PARTIAL_SEGMENT'
            return False
        if any(sys.modules.get(part) is not candidate for part, candidate in staged.items()):
            self.failed.add(segment)
            self.last_diagnostic = 'MODULE_REPLACED'
            return False
        observer = None
        try:
            observer = LocalProfilerObserver(self.emit, session=self.session,
                binding_factory=self.binding_factory)
            if observer.binding['process_start_sha256'] in self.previous_start_sha256:
                raise ValueError('BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE')
            observer.require_fresh_install = True
            observer.install(dict(staged), image=self.image)
        except BaseException as error:
            if observer is not None:
                try:
                    if observer.close()['restored'] is not True:
                        raise ValueError
                except BaseException:
                    self.enabled = False
                    self.last_diagnostic = 'HOOK_RESTORE_FAILED'
                    return False
            self.failed.add(segment)
            self.last_diagnostic = ('BINDING_UNAVAILABLE' if isinstance(error, ValueError)
                and str(error) == 'BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE'
                else 'SOURCE_UNAVAILABLE')
            return False
        self.observers[segment] = observer
        self.installed_modules[segment] = dict(staged)
        self.pending[segment] = {}
        self.last_diagnostic = 'INSTALLED'
        return True

    def close(self):
        self.enabled = False
        restored = True
        for observer in self.observers.values():
            try:
                restored = observer.close()['restored'] and restored
            except BaseException:
                restored = False
        self.pending = {name: {} for name in SEGMENTS}
        self.observers = {}
        self.installed_modules = {}
        self.last_diagnostic = 'CLOSED' if restored else 'HOOK_RESTORE_FAILED'
        return {'restored': restored, 'producer_authenticated': False}


def project_local(rows, *, binding):
    """Validate local pairs only. No API-to-core or core-to-worker join is made."""
    try:
        if type(rows) is not list or len(rows) > 1024 or type(binding) is not dict or set(binding) != {
                'pid', 'session_id_sha256', 'process_start_sha256'}:
            raise ValueError
        if (type(binding['pid']) is not int or binding['pid'] <= 0
                or any(type(binding[k]) is not str or _SHA.fullmatch(binding[k]) is None
                       for k in ('session_id_sha256', 'process_start_sha256'))):
            raise ValueError
        calls, terminals = {}, {}
        for row in rows:
            if (type(row) is not dict or set(row) != FIELDS or row['schema'] != SCHEMA
                    or row['point'] not in POINTS or row['binding'] != binding
                    or row['runtime'] not in RUNTIMES or row['source_sha256'] != POINTS[row['point']][2]
                    or any(type(row[k]) is not str or _SHA.fullmatch(row[k]) is None
                        for k in ('call_sha256', 'owner_sha256'))
                    or type(row['is_start']) is not bool or type(row['started_ns']) is not int
                    or row['started_ns'] < 0 or any(row[k] is not False for k in
                        ('cross_process_rpc_verified', 'producer_authenticated', 'whole_scope_complete'))):
                raise ValueError
            parent = row['parent_call_sha256']
            if (type(row['parent_frame_verified']) is not bool
                    or (parent is None) != (row['parent_frame_verified'] is False)
                    or parent is not None and (type(parent) is not str or _SHA.fullmatch(parent) is None)):
                raise ValueError
            call = row['call_sha256']
            if row['phase'] == 'CALL':
                if (call in calls or row['finished_ns'] is not None or row['exception_kind'] is not None
                        or row['original_invoked'] is not False):
                    raise ValueError
                if parent is not None:
                    preceding = calls.get(parent)
                    if (preceding is None or parent in terminals
                            or preceding['point'] not in PARENTS.get(row['point'], ())
                            or preceding['is_start'] is not row['is_start']
                            or preceding['started_ns'] > row['started_ns']):
                        raise ValueError
                calls[call] = row
            elif row['phase'] in {'RETURN', 'RAISE'}:
                prior = calls.get(call)
                if (prior is None or call in terminals
                        or any(row[k] != prior[k] for k in FIELDS - {
                            'phase', 'finished_ns', 'exception_kind', 'original_invoked'})
                        or type(row['finished_ns']) is not int
                        or row['finished_ns'] < row['started_ns']
                        or row['original_invoked'] is not True
                        or (row['phase'] == 'RETURN') != (row['exception_kind'] is None)
                        or row['exception_kind'] not in {None, 'exception', 'base-exception'}):
                    raise ValueError
                if (prior['parent_call_sha256'] in terminals
                        or any(child['parent_call_sha256'] == call and
                            (child_call not in terminals or
                             terminals[child_call]['finished_ns'] > row['finished_ns'])
                            for child_call, child in calls.items())):
                    raise ValueError
                terminals[call] = row
            else:
                raise ValueError
        if set(calls) != set(terminals):
            raise ValueError
        return {'local_complete_calls': len(calls),
            'local_parent_pairs': sum(row['parent_frame_verified'] for row in calls.values()),
            'api_to_core_verified': False, 'core_to_worker_verified': False,
            'cross_process_rpc_verified': False, 'producer_authenticated': False,
            'whole_scope_complete': False}
    except (TypeError, ValueError, KeyError):
        raise ValueError('BASELINE_PROFILER_LOCAL_RECORD_INVALID') from None
