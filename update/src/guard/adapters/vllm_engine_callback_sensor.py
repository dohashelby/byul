"""Prepared in-pod installer for the source-bound cache and distributed callbacks.

Ships as a self-contained package beside the callback modules it installs. A
target that does not match its pins stays unwrapped and leaves a gap row; the
engine keeps its original behaviour. Rows hold keyed fingerprints only.
"""
from __future__ import annotations

import atexit
import functools
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import sys
import threading

from . import vllm_cache_callback as cache
from . import vllm_distributed_callback as distributed

PROFILE = 'vllm-engine-callback-metadata/1'
SCHEMA = 'guard-engine-callback-events/1'
ROOT = '/guard-lifecycle/engine-callbacks'
DATA_ROOT = '/guard-lifecycle/data'
SLOT_MODULE = 'vllm.v1.worker.gpu_input_batch'
SLOT_TARGETS = {SLOT_MODULE: ('InputBatch.add_request', 'InputBatch.remove_request')}
SLOT_SOURCE = {SLOT_MODULE: 'e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d'}
# Block-level lookups feed no projection. Wrapping them made every cache hit
# slower in proportion to its length and multiplied the session rows.
CACHE_TARGETS = {'vllm.v1.core.kv_cache_manager': cache.TARGETS['vllm.v1.core.kv_cache_manager']}
MODULES = {**CACHE_TARGETS, **distributed.TARGETS, **SLOT_TARGETS}
# Engine request ids carry generated prefixes and suffixes around this tag.
TAG = re.compile(r'byul-cb-[0-9a-f]{32}')
MAX_FILE_BYTES = 16 * 1024 ** 2
FAULT_POINT = '_execute_worker_rpc'
FAULT_FILE = 'lab-fault-exception'


class _Keyed:
    """Share one prepared key so the parent can match its own request tags."""
    def fingerprint(self, value):
        if type(value) is str:
            found = TAG.search(value)
            if found: value = found.group()
        return super().fingerprint(value)

    def _emit(self, row):
        """오래 도는 엔진에서 메모리 상한이 기록을 끊지 않게 행을 파일로만 보낸다."""
        try:
            self.sink(row)
        except BaseException:
            with self.lock:
                self.gaps.add('CACHE_CALLBACK_SINK_UNAVAILABLE')


class CacheObserver(_Keyed, cache.CacheCallbackObserver):
    targets = CACHE_TARGETS


class DistributedObserver(_Keyed, distributed.DistributedCallbackObserver):
    pass


class SlotObserver(_Keyed, cache.CacheCallbackObserver):
    """요청이 받은 batch 자리와 제한 여부만 남긴다. 값 자체는 남기지 않는다."""
    targets = SLOT_TARGETS
    sources = SLOT_SOURCE
    schema = 'guard-vllm-slot-callback/1'
    profile = 'source-bound-slot-callback-candidate-v1'

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        value = args[1] if len(args) > 1 else kwargs.get('request', kwargs.get('req_id'))
        if point == 'InputBatch.add_request':
            fields = cache.base.attrs(value)
            allowed = cache.base.attrs(fields.get('sampling_params')).get('allowed_token_ids')
            row = {'request_sha256': self.fingerprint(fields.get('req_id')), 'restricted': type(allowed) is list and bool(allowed)}
        else:
            row = {'request_sha256': self.fingerprint(value)}
        if returned:
            row['slot'] = cache._uint(result)
        return row


class Sink:
    def __init__(self, directory):
        self.path = Path(directory) / f'callbacks-{os.getpid()}.jsonl'
        self.size = 0; self.fd = None; self.lock = threading.Lock()

    def __call__(self, row):
        raw = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with self.lock:
            if self.size + len(raw) > MAX_FILE_BYTES:
                raise ValueError('ENGINE_CALLBACK_FILE_LIMIT')
            if self.fd is None:
                self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            os.write(self.fd, raw); self.size += len(raw)


class EngineCallbacks:
    def __init__(self, session, secret, sink, *, context_provider=None, clock=None, fault_file=None):
        self.sink = sink; self.fault_file = fault_file
        options = {'sink': sink, 'context_provider': context_provider, 'clock': clock}
        self.cache = CacheObserver(session, **options)
        self.distributed = DistributedObserver(session, **options)
        self.slot = SlotObserver(session, **options)
        self.cache.secret = self.distributed.secret = self.slot.secret = secret

    def instrument(self, module):
        """Validate every point of a module before changing any of them."""
        name = vars(module).get('__name__')
        observer = self.cache if name in CACHE_TARGETS else self.slot if name in SLOT_TARGETS else self.distributed
        try:
            changes = [(cache._function(module, point), observer.wrap(module, point)) for point in MODULES[name]]
        except Exception:
            self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
            return False
        for (owner, method, _), wrapped in changes:
            setattr(owner, method, self._fault(wrapped) if method == FAULT_POINT and self.fault_file else wrapped)
        return True

    def _fault(self, wrapped):
        """Lab only: raise once in the worker when the parent placed the fault file."""
        path = Path(self.fault_file)
        @functools.wraps(wrapped)
        def faulted(*args, **kwargs):
            try:
                path.unlink()
            except OSError:
                return wrapped(*args, **kwargs)
            raise RuntimeError('GUARD_LAB_FAULT')
        wrapped.guard_outer = faulted
        return faulted

    def gap(self, code, detail=None):
        try:
            self.sink({'schema': SCHEMA, 'phase': 'GAP', 'code': code, 'process_sha256': self.cache.process_sha256,
                'detail_sha256': hashlib.sha256(str(detail).encode()).hexdigest()})
        except Exception:
            pass

    def flush_gaps(self):
        for code in sorted(self.cache.gaps | self.distributed.gaps | self.slot.gaps):
            self.gap(code)


class _Loader:
    def __init__(self, loader, callbacks):
        self.loader = loader; self.callbacks = callbacks

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        self.callbacks.instrument(module)

    def __getattr__(self, name):
        return getattr(self.loader, name)


class Finder(importlib.abc.MetaPathFinder):
    def __init__(self, callbacks):
        self.callbacks = callbacks

    def find_spec(self, name, path=None, target=None):
        if name not in MODULES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader, self.callbacks)
        return spec


def install(root=ROOT, data_root=DATA_ROOT, *, context_provider=None):
    """Never let a missing or broken sensor stop the engine from starting."""
    try:
        settings = json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8'))
        directory = Path(data_root) / settings['session']
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        callbacks = EngineCallbacks(settings['session'], bytes.fromhex(settings['secret']), Sink(directory),
            context_provider=context_provider,
            fault_file=str(directory / FAULT_FILE) if settings.get('lab_faults') is True else None)
        sys.meta_path.insert(0, Finder(callbacks))
        atexit.register(callbacks.flush_gaps)
        return True
    except Exception:
        return False
