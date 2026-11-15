"""Request-free /32 composition over the exact existing private lifecycle loader."""
import hashlib
import types

PROFILER_PROFILE = 'vllm-native-lifecycle-metadata/32'
PROFILER_SCHEMA = 'guard-native-lifecycle-events/32'
EXTRA_ASSETS = {
    'guard_profiler.py': ('vllm_profiler_sensor', '97f920da0ca9ab9ed41b814ab0e08b3af99833cb3a680686c9ffc07d552e1291'),
    'guard_baseline_profiler.py': ('vllm_baseline_profiler', '888628708434c0a6ba78e9d711f20da40a38d4aaead0a343a8c6042d420961a3'),
}


def _profiler_module(filename):
    raw = _startup_read(filename, EXTRA_ASSETS[filename][1], directory_path=STARTUP_DIRECTORY)
    module = types.ModuleType('_guard_' + filename[:-3])
    module.__file__ = STARTUP_DIRECTORY + '/' + filename
    exec(compile(raw, module.__file__, 'exec', dont_inherit=True, optimize=0), vars(module))
    return module


def _profiler_configure(base):
    helper = _startup_modules(base)  # exact original qualification/identity helpers; no startup hook installed
    profiler = _profiler_module('guard_profiler.py')
    candidate = _profiler_module('guard_baseline_profiler.py')
    if (SPEC['profile'] != PROFILER_PROFILE or SPEC['schema'] != PROFILER_SCHEMA
            or SPEC['sources'] != {candidate.WORKER: {'sha256': candidate.SOURCE['sha256'],
                'bytes': candidate.SOURCE['bytes'], 'classes': {}}}
            or SPEC['void_return_argument_ids'] != []):
        raise ValueError('BASELINE_PROFILER_COMPOSITION_INVALID')
    original = base.Observer
    class DirectProfilerObserver(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.direct_profiler = None

        def _profiler_binding(self):
            return candidate.process_binding(self.config['session'], self._writer().context)

        def _profiler_emit(self, record):
            if self.active():
                self.emit('BASELINE_PROFILER', record)

        def instrument(self, module):
            if vars(module).get('__name__') != candidate.WORKER:
                return super().instrument(module)
            if self.direct_profiler is None:
                self.direct_profiler = candidate.ProfilerCandidate(self._profiler_emit,
                    session=self.config['session'], binding_factory=self._profiler_binding,
                    base=base, sources=helper.sources, profiler=profiler)
            self.direct_profiler.install(module, image=SPEC['profiler_image'])

        def close(self):
            try:
                if self.direct_profiler is not None:
                    self.direct_profiler.close()
            finally:
                super().close()
    class DirectProfilerMiddleware:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            return await self.app(scope, receive, send)
    base.Observer, base.Middleware = DirectProfilerObserver, DirectProfilerMiddleware
    return base


if '_startup_previous_configure' in globals():
    # Call the unchanged configured template, not /31's startup observer. The
    # earlier extension supplies only its pinned private reader/module loader.
    def _configure(base):
        return _profiler_configure(_startup_previous_configure(base))
