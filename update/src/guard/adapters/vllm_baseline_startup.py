"""Exact baseline V1 startup candidate hooks; no product profile or registration.

READY is an IPC handle exchange *before* device/model initialization. Local
Worker body/constructor outcomes can be observed; cross-process failure delivery,
GPU completion, full preparation and external service admission remain separate.
"""
from __future__ import annotations

import contextvars
import functools
import hashlib
import os
import secrets
import sys
import time
import types

from . import vllm_initialization_sensor as initialization
from . import vllm_lifecycle_sensor as base
from . import vllm_rank_ipc_sources as sources
from . import vllm_slot_mask as support
from . import vllm_worker_readiness as callback
from . import vllm_worker_startup_contract as contract

SCHEMA = 'guard-baseline-startup-candidate/1'
COMMIT = '5204ff5c3feeb96e8a6eea65dfcb78395f90d4d8'
IMAGE = 'vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8'
MP = 'vllm.v1.executor.multiproc_executor'
WORKER = 'vllm.v1.worker.gpu_worker'
ROWS = {
    MP: {'path': 'vllm/v1/executor/multiproc_executor.py', 'bytes': 14086,
         'sha256': '5db418332360f43e2a9718f62d8ec5d856d554b8d0d27d14f053fb3392d33239',
         'functions': ('MultiprocExecutor._init_executor', 'WorkerProc.make_worker_process',
                       'WorkerProc.wait_for_startup', 'WorkerProc.__init__')},
    WORKER: {'path': 'vllm/v1/worker/gpu_worker.py', 'bytes': 11991,
             'sha256': '7bc08a4e47bac5e1d68e68f540f014e3f9244c0f8d773f0dd7f5b959da5afb7a',
             'functions': ('Worker.init_device', 'Worker.load_model', 'Worker.compile_or_warm_up_model')},
}
STATIC = frozenset({'WorkerProc.make_worker_process', 'WorkerProc.wait_for_startup'})
_PARENT = contextvars.ContextVar('guard_baseline_startup_parent', default=None)


def qualify(module, *, image):
    """Compare current direct callables with exact installed bytes, without imports.

    Code is compiled by the running allowed interpreter. No cross-ABI bytecode
    fingerprint is invented and no original sensor runtime gate is bypassed.
    """
    try:
        runtime = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))
        if (runtime not in {'cpython-3.12.3', 'cpython-3.12.8'} or os.name != 'posix'
                or sys.flags.optimize != 0 or image not in {IMAGE, 'docker.io/' + IMAGE}
                or type(module) is not types.ModuleType):
            raise ValueError
        row = ROWS[vars(module)['__name__']]
        raw = sources._bytes(vars(module)['__file__'], row)
        codes = sources._codes(compile(raw, vars(module)['__file__'], 'exec', dont_inherit=True, optimize=0))
        functions = {}
        for point in row['functions']:
            original = sources._function(module, point)
            cls, name = point.split('.')
            descriptor = vars(vars(module)[cls])[name]
            if (point in STATIC) != (type(descriptor) is staticmethod):
                raise ValueError
            if (original.__qualname__ != point
                    or base.code_digest(sources.relative_code(original.__code__, row['path']))
                    != base.code_digest(sources.relative_code(codes[point], row['path']))):
                raise ValueError
            functions[point] = original
        return {'functions': functions, 'source_sha256': row['sha256'], 'runtime': runtime}
    except (ValueError, OSError, KeyError, TypeError, AttributeError, SyntaxError):
        raise ValueError('BASELINE_STARTUP_SOURCE_UNAVAILABLE') from None


class StartupCandidate:
    """Opt-in candidate installation into already loaded, qualified native modules.

    emit owns the bounded journal and its process context. No startup, import hook,
    fork/spawn installation, failure injection or lifecycle verdict is performed.
    """
    def __init__(self, emit, *, binding):
        scope = support._binding(binding)
        if (not callable(emit) or scope.get('pid') != os.getpid()
                or not all(scope.get(k) for k in ('session_id_sha256', 'process_start_sha256'))):
            raise ValueError('BASELINE_STARTUP_BINDING_UNAVAILABLE')
        contract.callback_code_contract(callback, support, base)
        self.emit, self.binding = emit, scope
        self.pid = os.getpid()
        self.callback = callback.WorkerReadinessObserver()
        self.installed = []
        self.disabled = False

    def _record(self, point, phase, args, kwargs, info, *, result=None, error=None):
        try:
            worker_snapshot = None
            if point in ROWS[WORKER]['functions']:
                worker_snapshot = self.callback.snapshot(point, args, kwargs, phase=phase,
                    result=result, exception=error, binding={**self.binding, 'call_id_sha256': info['call_sha256']})
                contract.closed_callback(worker_snapshot, callback)
            owner = args[0] if args and point == 'MultiprocExecutor._init_executor' else None
            workers = initialization.handles(initialization.attrs(owner).get('workers')) if phase != 'CALL' else None
            returned = initialization.handles([result]) if point == 'WorkerProc.make_worker_process' and phase == 'RETURN' else None
            rank = None
            if point in {'WorkerProc.make_worker_process', 'WorkerProc.__init__'}:
                offset = 1 if point == 'WorkerProc.__init__' else 0
                rank = initialization.uint(kwargs.get('rank', args[offset + 2] if len(args) > offset + 2 else None))
                if rank is not None and rank > 63:
                    rank = None
            self.emit({'schema': SCHEMA, **info, 'point': point, 'phase': phase,
                'binding': dict(self.binding), 'finished_ns': time.monotonic_ns() if phase != 'CALL' else None,
                'exception_kind': None if error is None else 'exception' if isinstance(error, Exception) else 'base-exception',
                'rank': rank, 'workers': workers, 'returned_worker': returned,
                'worker_snapshot': worker_snapshot, 'producer_authenticated': False,
                'whole_scope_complete': False, 'service_gate_verified': False})
        except BaseException:
            self.disabled = True

    def _wrap(self, point, original, qualified):
        expected = base.code_digest(original.__code__)
        observer = self
        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            try:
                if observer.disabled or observer.pid != os.getpid():
                    observe = False
                else:
                    observe = base.code_digest(original.__code__) == expected
                    if not observe:
                        observer.disabled = True
                if observe:
                    info = {'call_sha256': hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                            'parent_call_sha256': _PARENT.get(), 'started_ns': time.monotonic_ns(),
                            'source_sha256': qualified['source_sha256'], 'runtime': qualified['runtime']}
                    observer._record(point, 'CALL', args, kwargs, info)
                    token = _PARENT.set(info['call_sha256'])
            except BaseException:
                observer.disabled = True
                observe = False
            if not observe:
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
                observer._record(point, phase, args, kwargs, info, result=result, error=error)
                try:
                    _PARENT.reset(token)
                except BaseException:
                    observer.disabled = True
        return wrapped

    def install(self, module, *, image):
        if self.disabled or self.pid != os.getpid():
            raise ValueError('BASELINE_STARTUP_OBSERVER_UNAVAILABLE')
        qualified = qualify(module, image=image)
        changes = []
        for point, original in qualified['functions'].items():
            cls, name = point.split('.')
            owner = vars(module)[cls]
            descriptor = vars(owner)[name]
            wrapped = self._wrap(point, original, qualified)
            changes.append((owner, name, descriptor, staticmethod(wrapped) if point in STATIC else wrapped))
        applied = []
        try:
            for owner, name, previous, wrapped in changes:
                setattr(owner, name, wrapped)
                applied.append((owner, name, previous, wrapped))
        except BaseException:
            for owner, name, previous, wrapped in reversed(applied):
                if vars(owner).get(name) is wrapped:
                    setattr(owner, name, previous)
            raise
        self.installed.extend(applied)
        return {'hooks_installed': len(applied), 'producer_registered': False, 'whole_scope_complete': False}

    def close(self):
        self.disabled = True
        restored = True
        for owner, name, previous, wrapped in reversed(self.installed):
            if vars(owner).get(name) is wrapped:
                setattr(owner, name, previous)
            else:
                restored = False
        self.installed.clear()
        return {'restored': restored, 'callback': self.callback.close(), 'producer_authenticated': False}
