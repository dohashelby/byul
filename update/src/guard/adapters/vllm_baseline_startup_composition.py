"""Pinned startup extension appended only to the baseline /31 composition.

The configured /27 template and original /14+/19 helpers remain byte-preserved.
Private dependency imports resolve explicit reviewed modules without modifying
sys.modules, the global importer or the source bytes being executed.
"""
import builtins
import hashlib
import json
import os
import stat
import types

STARTUP_PROFILE = 'vllm-native-lifecycle-metadata/31'
STARTUP_SCHEMA = 'guard-native-lifecycle-events/31'
STARTUP_DIRECTORY = '/guard-lifecycle'
DEPENDENCIES = {
    'vllm_initialization_sensor': ('guard_initialization.py', '654b22da19bd6ff8cd0e72edb29fa705afa6c01e6fb3616cef9a091e17e9977f'),
    'vllm_slot_mask': ('guard_worker_readiness_support.py', '659d615f133cdc96ef54163678753a60e6b8ee51aa0e78e0560e0d308f7f50da'),
    'vllm_worker_readiness': ('guard_worker_readiness_callback.py', 'a583d32f31d74def618256557ee9e7aee801d5fe1ff55e27b6221d7f2420a965'),
    'vllm_worker_generation_contract': ('guard_worker_generation_contract.py', 'f0c37215577292ce7b89b535cf147553ac22f5ae6ef6a08708cf055f4d4eb27a'),
    'vllm_rank_ipc_sources': ('guard_startup_sources.py', 'd54a79846796d78dcfebb59e21627781e2ffee02ca97f1d041d79f7a9e4c9648'),
    'vllm_worker_startup_contract': ('guard_worker_startup_contract.py', '6788df2f4c6f04f46e681eb5eab48631e2ba037754d26166bb02341e4b592950'),
    'vllm_baseline_startup': ('guard_baseline_startup.py', 'eae41581ae04d9edf571c458824840f0d8877ef31bf79e08d218116a588ec842'),
}


def _startup_read(name, digest, directory_path='/guard-lifecycle'):
    directory = os.open(directory_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fd = None
    try:
        before_path = os.stat(name, dir_fd=directory, follow_symlinks=False)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 262144:
            raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(262145)
        after = os.fstat(fd)
        after_path = os.stat(name, dir_fd=directory, follow_symlinks=False)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) != before.st_size or len({identity(s) for s in (before_path, before, after, after_path)}) != 1
                or hashlib.sha256(raw).hexdigest() != digest):
            raise ValueError('BASELINE_STARTUP_DEPENDENCY_UNAVAILABLE')
        return raw
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)


def _startup_modules(base):
    selected = {'vllm_lifecycle_sensor': base}
    namespace = types.ModuleType('_guard_startup_private_dependencies')
    namespace.vllm_lifecycle_sensor = base
    original_import = builtins.__import__
    def private_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level == 1:
            if not name:
                if not fromlist or any(part not in selected for part in fromlist):
                    raise ImportError('BASELINE_STARTUP_PRIVATE_IMPORT_UNAVAILABLE')
                return namespace
            if name not in selected:
                raise ImportError('BASELINE_STARTUP_PRIVATE_IMPORT_UNAVAILABLE')
            return selected[name]
        if name.startswith('guard.'):
            prefix = 'guard.adapters.'
            if not name.startswith(prefix) or name[len(prefix):] not in selected or not fromlist:
                raise ImportError('BASELINE_STARTUP_PRIVATE_IMPORT_UNAVAILABLE')
            return selected[name[len(prefix):]]
        return original_import(name, globals, locals, fromlist, level)
    for logical, (filename, digest) in DEPENDENCIES.items():
        raw = _startup_read(filename, digest, directory_path=STARTUP_DIRECTORY)
        module = types.ModuleType('guard.adapters.' + logical)
        module.__package__ = 'guard.adapters'
        module.__file__ = STARTUP_DIRECTORY + '/' + filename
        module.__dict__['__builtins__'] = {**vars(builtins), '__import__': private_import}
        exec(compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0), module.__dict__)
        selected[logical] = module
        setattr(namespace, logical, module)
    return selected['vllm_baseline_startup']


def _startup_configure(base):
    candidate = _startup_modules(base)
    if (SPEC['profile'] != STARTUP_PROFILE or SPEC['schema'] != STARTUP_SCHEMA
            or set(SPEC['sources']) != set(candidate.ROWS) or SPEC['void_return_argument_ids'] != []
            or any(SPEC['sources'].get(name, {}).get('sha256') != row['sha256']
                   or SPEC['sources'][name]['classes'] != {}
                   for name, row in candidate.ROWS.items())):
        raise ValueError('BASELINE_STARTUP_COMPOSITION_INVALID')
    original = base.Observer
    class StartupObserver(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.startup_candidate = None

        def _startup_binding(self):
            return candidate.process_binding(self.config['session'], self._writer().context)

        def _startup_emit(self, record):
            if self.active():
                self.emit('BASELINE_STARTUP', record)

        def instrument(self, module):
            if vars(module).get('__name__') not in candidate.ROWS:
                return super().instrument(module)
            if self.startup_candidate is None:
                self.startup_candidate = candidate.StartupCandidate(self._startup_emit,
                    binding=self._startup_binding(), binding_factory=self._startup_binding)
            self.startup_candidate.install(module, image=SPEC['startup_image'])

        def close(self):
            try:
                if self.startup_candidate is not None:
                    self.startup_candidate.close()
            finally:
                super().close()
    base.Observer = StartupObserver
    class StartupMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            # Startup-only profile has no request-ID/admission observation.
            # In particular, never read a header or invoke the legacy ID hash.
            return await self.app(scope, receive, send)
    base.Middleware = StartupMiddleware
    return base


# This file is also imported by the catalog for its pinned dependency metadata.
# The composition entry is changed only when appended to the configured template.
if '_configure' in globals():
    _startup_previous_configure = _configure
    def _configure(base):
        return _startup_configure(_startup_previous_configure(base))
