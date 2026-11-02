"""Prepared in-pod installer for the source-bound cache and distributed callbacks.

Ships as a self-contained package beside the callback modules it installs. A
target that does not match its pins stays unwrapped and leaves a gap row; the
engine keeps its original behaviour. Rows hold keyed fingerprints only.
"""
from __future__ import annotations

import atexit
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
MODULES = {**cache.TARGETS, **distributed.TARGETS}
# Engine request ids carry generated prefixes and suffixes around this tag.
TAG = re.compile(r'byul-cb-[0-9a-f]{32}')
MAX_FILE_BYTES = 16 * 1024 ** 2


class _Keyed:
    """Share one prepared key so the parent can match its own request tags."""
    def fingerprint(self, value):
        if type(value) is str:
            found = TAG.search(value)
            if found: value = found.group()
        return super().fingerprint(value)


class CacheObserver(_Keyed, cache.CacheCallbackObserver):
    pass


class DistributedObserver(_Keyed, distributed.DistributedCallbackObserver):
    pass


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
    def __init__(self, session, secret, sink, *, context_provider=None, clock=None):
        self.sink = sink
        options = {'sink': sink, 'context_provider': context_provider, 'clock': clock}
        self.cache = CacheObserver(session, **options)
        self.distributed = DistributedObserver(session, **options)
        self.cache.secret = self.distributed.secret = secret

    def instrument(self, module):
        """Validate every point of a module before changing any of them."""
        name = vars(module).get('__name__')
        observer = self.cache if name in cache.TARGETS else self.distributed
        try:
            changes = [(cache._function(module, point), observer.wrap(module, point)) for point in MODULES[name]]
        except Exception:
            self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
            return False
        for (owner, method, _), wrapped in changes:
            setattr(owner, method, wrapped)
        return True

    def gap(self, code, detail=None):
        try:
            self.sink({'schema': SCHEMA, 'phase': 'GAP', 'code': code,
                'detail_sha256': hashlib.sha256(str(detail).encode()).hexdigest()})
        except Exception:
            pass

    def flush_gaps(self):
        for code in sorted(self.cache.gaps | self.distributed.gaps):
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
            context_provider=context_provider)
        sys.meta_path.insert(0, Finder(callbacks))
        atexit.register(callbacks.flush_gaps)
        return True
    except Exception:
        return False
