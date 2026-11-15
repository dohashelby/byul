"""Baseline Worker.profile: direct Torch backend outcomes, never wrapper state.

Standalone candidate source. The composition supplies exact private original
helpers; this file imports no engine, package, GPU dependency or HTTP handler.
"""
from __future__ import annotations
import functools
import hashlib
import inspect
import json
import os
import re
import secrets
import sys
import threading
import time
import types
import weakref

SCHEMA = 'guard-baseline-profiler-candidate/1'
WORKER = 'vllm.v1.worker.gpu_worker'
SOURCE = {'sha256': '7bc08a4e47bac5e1d68e68f540f014e3f9244c0f8d773f0dd7f5b959da5afb7a',
          'bytes': 11991, 'path': 'vllm/v1/worker/gpu_worker.py'}
POINT = 'Worker.profile'
FIELDS = frozenset({'schema', 'point', 'phase', 'binding', 'source_sha256', 'runtime',
    'call_sha256', 'worker_sha256', 'is_start', 'rank', 'local_rank', 'started_ns', 'finished_ns',
    'exception_kind', 'backend_before', 'backend_after', 'backend_generation_sha256',
    'backend_probe_qualified', 'trace_before', 'trace_after', 'callback_directory_binding_verified',
    'cross_process_rpc_verified', 'producer_authenticated', 'whole_scope_complete'})


def process_binding(session, context):
    return {'pid': context['pid'], 'session_id_sha256': hashlib.sha256(session.encode()).hexdigest(),
        'process_start_sha256': hashlib.sha256(json.dumps(context, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()}


def qualify(module, *, image, base, sources):
    try:
        runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
        if (runtime not in {'cpython-3.12.3', 'cpython-3.12.8'} or os.name != 'posix' or sys.flags.optimize != 0
                or image not in {'vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8',
                    'docker.io/vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8'}
                or type(module) is not types.ModuleType or vars(module).get('__name__') != WORKER):
            raise ValueError
        raw = sources._bytes(vars(module)['__file__'], SOURCE)
        compiled = sources._codes(compile(raw, vars(module)['__file__'], 'exec', dont_inherit=True, optimize=0))[POINT]
        original = sources._function(module, POINT)
        cls = vars(module)['Worker']
        if (vars(cls).get('profile') is not original or original.__qualname__ != POINT
                or base.code_digest(sources.relative_code(original.__code__, SOURCE['path']))
                    != base.code_digest(sources.relative_code(compiled, SOURCE['path']))):
            raise ValueError
        return original, runtime
    except (ValueError, OSError, KeyError, TypeError, AttributeError, SyntaxError):
        raise ValueError('BASELINE_PROFILER_SOURCE_UNAVAILABLE') from None


class ProfilerCandidate:
    def __init__(self, emit, *, session, binding_factory, base, sources, profiler):
        if (not callable(emit) or not callable(binding_factory) or type(session) is not str
                or re.fullmatch('[0-9a-f]{32}', session) is None):
            raise ValueError('BASELINE_PROFILER_BINDING_UNAVAILABLE')
        self.emit, self.session, self.binding_factory = emit, session, binding_factory
        self.base, self.sources, self.profiler = base, sources, profiler
        self.pid = None
        self.disabled = False
        self.installed = []
        self._process()

    def _process(self):
        if self.pid != os.getpid():
            binding = self.binding_factory()
            if (type(binding) is not dict or binding.get('pid') != os.getpid()
                    or set(binding) != {'pid', 'session_id_sha256', 'process_start_sha256'}
                    or any(type(binding[k]) is not str or re.fullmatch('[0-9a-f]{64}', binding[k]) is None
                           for k in ('session_id_sha256', 'process_start_sha256'))):
                raise ValueError('BASELINE_PROFILER_BINDING_UNAVAILABLE')
            self.binding, self.pid = binding, os.getpid()
            self.objects = weakref.WeakKeyDictionary()
            self.generations = {}
            self.lock = threading.RLock()

    def _nonce(self, value=None):
        with self.lock:
            if value is not None and value in self.objects:
                return self.objects[value]
            if value is not None and len(self.objects) >= 256:
                return None
            token = hashlib.sha256(secrets.token_bytes(32)).hexdigest()
            if value is not None:
                self.objects[value] = token
            return token

    def _backend(self, owner):
        result = {'value': None, 'collected': False, 'backend_sha256': None, 'thread_id': None}
        torch = sys.modules.get('torch')
        backend = self.profiler.attrs(owner).get('profiler')
        try:
            if type(torch) is not types.ModuleType or type(backend) is not torch.profiler.profile:
                return result
            result['backend_sha256'] = self._nonce(backend)
            probe = torch.autograd._profiler_enabled
            if (not inspect.isbuiltin(probe) or probe is not torch._C._autograd._profiler_enabled
                    or probe.__module__ != 'torch._C._autograd'):
                return result
            value = probe()
            if type(value) is bool:
                result.update(value=value, collected=True, thread_id=threading.get_native_id())
        except BaseException:
            pass
        return result

    def _inventory(self):
        # This is an explicitly declared owned directory, not a synthesized
        # Worker.profiler_config or evidence of the consumed Torch callback path.
        scope = types.SimpleNamespace(profiler_config=types.SimpleNamespace(
            torch_profiler_dir='/guard-profiler-traces/' + self.session))
        return self.profiler.trace_inventory(scope, self.session)

    def _begin(self, args, kwargs, runtime):
        self._process()
        owner = args[0] if args else kwargs.get('self')
        if type(owner).__module__ != WORKER or type(owner).__name__ != 'Worker':
            return None
        flag = args[1] if len(args) > 1 else kwargs.get('is_start', True)
        data = self.profiler.attrs(owner)
        rank = self.profiler.uint(data.get('rank'))
        local = self.profiler.uint(data.get('local_rank'))
        call_id, worker_id = self._nonce(), self._nonce(owner)
        if call_id is None or worker_id is None:
            return None
        return {'schema': SCHEMA, 'point': POINT, 'binding': dict(self.binding),
            'source_sha256': SOURCE['sha256'], 'runtime': runtime, 'call_sha256': call_id,
            'worker_sha256': worker_id, 'is_start': flag if type(flag) is bool else None,
            'rank': rank if rank is not None and rank < 64 else None,
            'local_rank': local if local is not None and local < 64 else None,
            'started_ns': time.monotonic_ns(), 'backend_before': self._backend(owner),
            'trace_before': self._inventory(), 'callback_directory_binding_verified': False,
            'cross_process_rpc_verified': False, 'producer_authenticated': False, 'whole_scope_complete': False}

    def _record(self, info, phase, owner, error=None):
        try:
            after = self._backend(owner) if phase != 'CALL' else None
            generation = None
            qualified = False
            if after is not None:
                before = info['backend_before']
                key = (info['worker_sha256'], before['thread_id'])
                same = bool(before['collected'] and after['collected']
                    and before['thread_id'] == after['thread_id']
                    and before['backend_sha256'] == after['backend_sha256'] and after['backend_sha256'] is not None)
                with self.lock:
                    if info['is_start'] is True and phase == 'RETURN' and same and before['value'] is False and after['value'] is True:
                        self.generations.pop(key, None)
                        if len(self.generations) < 256:
                            created = self._nonce()
                            if created is not None:
                                self.generations[key] = {'generation': created, 'backend': after['backend_sha256']}
                    lease = self.generations.get(key)
                    if (same and lease and lease['generation'] is not None and lease['backend'] == after['backend_sha256']
                            and (info['is_start'] is True and phase == 'RETURN' and before['value'] is False and after['value'] is True
                                 or info['is_start'] is False and before['value'] is True)):
                        generation, qualified = lease['generation'], True
                    if info['is_start'] is False and same and after['value'] is False:
                        self.generations.pop(key, None)
            self.emit({**info, 'phase': phase, 'finished_ns': time.monotonic_ns() if phase != 'CALL' else None,
                'exception_kind': None if error is None else 'exception' if isinstance(error, Exception) else 'base-exception',
                'backend_after': after, 'backend_generation_sha256': generation, 'backend_probe_qualified': qualified,
                'trace_after': self._inventory() if phase != 'CALL' else None})
        except BaseException:
            self.disabled = True

    def install(self, module, *, image):
        self._process()
        original, runtime = qualify(module, image=image, base=self.base, sources=self.sources)
        expected = self.base.code_digest(original.__code__)
        observer = self
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            info = None
            try:
                if not observer.disabled and observer.base.code_digest(original.__code__) == expected:
                    info = observer._begin(args, kwargs, runtime)
                    if info is not None:
                        observer._record(info, 'CALL', args[0] if args else kwargs.get('self'))
            except BaseException:
                observer.disabled = True
            if info is None:
                return original(*args, **kwargs)
            result = error = None
            phase = 'RAISE'
            try:
                result = original(*args, **kwargs)
                phase = 'RETURN'
                return result
            except BaseException as caught:
                error = caught
                raise
            finally:
                observer._record(info, phase, args[0] if args else kwargs.get('self'), error)
        cls = vars(module)['Worker']
        cls.profile = wrapped
        self.installed.append((cls, original, wrapped))
        return {'hooks_installed': 1, 'producer_registered': False}

    def close(self):
        self.disabled = True
        restored = True
        for cls, original, wrapped in reversed(self.installed):
            if vars(cls).get('profile') is wrapped:
                cls.profile = original
            else:
                restored = False
        self.installed.clear()
        if self.pid == os.getpid():
            self.objects.clear()
            self.generations.clear()
        return {'restored': restored, 'producer_authenticated': False}
