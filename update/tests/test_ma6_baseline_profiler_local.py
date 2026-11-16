"""Exact 0.7 source bodies with inert queue collaborators; no GPU or HTTP."""
import asyncio
from copy import deepcopy
import os
from pathlib import Path
import sys
import time
import types
from types import ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_baseline_profiler as direct
from guard.adapters import vllm_baseline_profiler_local as local
from guard.adapters import vllm_rank_ipc_sources as sources


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/vllm-070-profiler-local'
SESSION = 'a' * 32
BINDING = direct.process_binding(SESSION, {'pid': os.getpid(), 'test': 'local-profiler'})


def _module(tmp_path, name):
    path, _, _, points = local.SPECS[name]
    raw = (FIXTURE / path).read_bytes()
    installed = tmp_path / path
    installed.parent.mkdir(parents=True, exist_ok=True)
    installed.write_bytes(raw)
    module = ModuleType(name)
    module.__file__ = str(installed)
    codes = sources._codes(compile(raw, str(installed), 'exec', dont_inherit=True, optimize=0))
    for point in points:
        cls_name, method = point.split('.')
        cls = vars(module).get(cls_name)
        if cls is None:
            cls = type(cls_name, (), {'__module__': name})
            setattr(module, cls_name, cls)
        function = types.FunctionType(codes[point], vars(module))
        if point in {'AsyncMPClient.profile_async', 'Executor.profile'}:
            function.__defaults__ = (True,)
        if point == 'MultiprocExecutor.collective_rpc':
            function.__defaults__ = (None, (), None)
        setattr(cls, method, function)
    return module


def world(tmp_path, *, install=True):
    modules = {name: _module(tmp_path, name) for name in local.SPECS}
    # These are only inert collaborators. The observed function bodies are the
    # exact compiled source above, not replacements of the semantic calls.
    client = modules['vllm.v1.engine.core_client']
    engine = modules['vllm.v1.engine.core']
    multiproc = modules['vllm.v1.executor.multiproc_executor']
    request_type = SimpleNamespace(PROFILE='profile')
    profile_type = type('EngineCoreProfile', (), {'__module__': 'vllm.v1.engine'})
    profile_type.__init__ = lambda self, value: vars(self).update(is_start=value)
    client.EngineCoreRequestType = request_type
    client.EngineCoreProfile = engine.EngineCoreProfile = profile_type
    engine.EngineCoreRequest = type('EngineCoreRequest', (), {})
    engine.EngineCoreResetPrefixCache = type('EngineCoreResetPrefixCache', (), {})
    async def send_input(self, kind, value):
        await asyncio.sleep(0)
        if self.failure is not None:
            raise self.failure
        self.sent.append((kind, value.is_start))
    client.AsyncMPClient._send_input = send_input
    multiproc.time = time
    class ResponseStatus:
        SUCCESS = object()
    multiproc.WorkerProc = SimpleNamespace(ResponseStatus=ResponseStatus)
    modules['vllm.v1.executor.abstract'].Executor.collective_rpc = None
    class Executor(multiproc.MultiprocExecutor, modules['vllm.v1.executor.abstract'].Executor):
        pass
    records = []
    observer = local.LocalProfilerObserver(records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING))
    if install:
        observer.install(modules, image=local.IMAGE)
    frontend = modules['vllm.v1.engine.async_llm'].AsyncLLM()
    frontend.engine_core = client.AsyncMPClient()
    frontend.engine_core.failure = None
    frontend.engine_core.sent = []
    queue = SimpleNamespace(messages=[], enqueue=lambda value: queue.messages.append(value))
    response = SimpleNamespace(dequeue=lambda **kwargs: (ResponseStatus.SUCCESS, None))
    backend = Executor()
    backend.rpc_broadcast_mq = queue
    backend.worker_response_mq = response
    backend.world_size = 1
    backend.workers = [SimpleNamespace(rank=0, worker_response_mq=response)]
    core = engine.EngineCore()
    core.model_executor = backend
    return SimpleNamespace(modules=modules, records=records, observer=observer,
        frontend=frontend, core=core, backend=backend, profile_type=profile_type, queue=queue)


def test_actual_source_default_repeated_calls_and_local_pairs(tmp_path):
    w = world(tmp_path)
    asyncio.run(w.frontend.start_profile())
    asyncio.run(w.frontend.stop_profile())
    asyncio.run(w.frontend.start_profile())
    w.core._handle_client_request(w.profile_type(True))
    w.core._handle_client_request(w.profile_type(False))
    result = local.project_local(w.records, binding=BINDING)
    assert result['local_complete_calls'] == 12
    assert result['local_parent_pairs'] == 7
    assert result['cross_process_rpc_verified'] is False
    assert result['whole_scope_complete'] is False
    assert w.frontend.engine_core.sent == [('profile', True), ('profile', False), ('profile', True)]
    assert w.queue.messages == [('profile', (True,), {}), ('profile', (False,), {})]
    assert [row['is_start'] for row in w.records if row['point'] == 'AsyncLLM.start_profile'
            and row['phase'] == 'RETURN'] == [True, True]
    assert w.observer.close()['restored'] is True


def test_await_failure_and_cancellation_record_raise_after_await(tmp_path):
    w = world(tmp_path)
    w.frontend.engine_core.failure = ValueError('EXAMPLE')
    with pytest.raises(ValueError, match='EXAMPLE'):
        asyncio.run(w.frontend.start_profile())
    assert local.project_local(w.records, binding=BINDING)['local_complete_calls'] == 2
    assert all(row['phase'] != 'RETURN' for row in w.records)
    assert [row['exception_kind'] for row in w.records if row['phase'] == 'RAISE'] == ['exception'] * 2
    w.frontend.engine_core.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w.frontend.stop_profile())
    assert local.project_local(w.records, binding=BINDING)['local_complete_calls'] == 4
    assert [row['exception_kind'] for row in w.records if row['phase'] == 'RAISE'][-2:] == ['base-exception'] * 2
    assert w.observer.close()['restored'] is True


def test_unsupported_operation_and_source_tamper_do_not_install(tmp_path):
    w = world(tmp_path)
    w.core._handle_client_request(w.profile_type(True))
    before = len(w.records)
    assert w.backend.collective_rpc('health') == [None]
    assert len(w.records) == before
    spoof = type('EngineCoreProfile', (list,), {'__module__': 'vllm.v1.engine'})
    forged = spoof()
    forged.is_start = True
    w.core.abort_requests = lambda request: None
    w.core._handle_client_request(forged)
    assert len(w.records) == before  # native abort/list branch is not profiler
    assert w.observer.close()['restored'] is True
    modules = {name: _module(tmp_path / 'changed', name) for name in local.SPECS}
    path = Path(modules['vllm.v1.engine.core'].__file__)
    path.write_bytes(path.read_bytes() + b'\n# mutation\n')
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_SOURCE_UNAVAILABLE'):
        local.LocalProfilerObserver(lambda value: None, session=SESSION,
            binding_factory=lambda: dict(BINDING)).install(modules, image=local.IMAGE)
    assert vars(modules['vllm.v1.engine.async_llm'].AsyncLLM)['start_profile'].__name__ == 'start_profile'


def test_default_standalone_call_and_post_install_source_mutation(tmp_path):
    w = world(tmp_path)
    w.backend.profile()
    result = local.project_local(w.records, binding=BINDING)
    assert result['local_complete_calls'] == 2 and result['local_parent_pairs'] == 1
    assert all(row['parent_call_sha256'] is None for row in w.records
               if row['point'] == 'Executor.profile')
    assert w.queue.messages == [('profile', (True,), {})]
    before = len(w.records)
    path = Path(w.modules['vllm.v1.engine.async_llm'].__file__)
    path.write_bytes(path.read_bytes() + b'\n# post-install mutation\n')
    asyncio.run(w.frontend.start_profile())
    assert len(w.records) == before
    assert w.frontend.engine_core.sent == [('profile', True)]
    assert w.observer.close()['restored'] is True


def test_each_process_segment_qualifies_without_foreign_modules(tmp_path):
    for segment in local.SEGMENTS.values():
        modules = {name: _module(tmp_path / str(len(segment)), name) for name in segment}
        assert set(local.qualify(modules, image=local.IMAGE)) == {
            point for name in segment for point in local.SPECS[name][3]}
        observer = local.LocalProfilerObserver(lambda value: None, session=SESSION,
            binding_factory=lambda: dict(BINDING))
        assert observer.install(modules, image=local.IMAGE)['hooks_installed'] == 3
        assert observer.close()['restored'] is True


def test_observation_clock_failure_preserves_native_return_and_exception(tmp_path, monkeypatch):
    w = world(tmp_path)
    original_clock = local.time.monotonic_ns
    calls = 0
    def broken_terminal_clock():
        nonlocal calls
        calls += 1
        if calls > 2:
            raise RuntimeError('EXAMPLE observer clock failure')
        return original_clock()
    monkeypatch.setattr(local.time, 'monotonic_ns', broken_terminal_clock)
    assert w.backend.profile() is None
    assert w.queue.messages == [('profile', (True,), {})]
    assert w.observer.disabled is True
    assert w.observer.close()['restored'] is True
    w2 = world(tmp_path / 'exception')
    calls = 0
    def bad_native(self, *args, **kwargs):
        raise ValueError('EXAMPLE native error')
    w2.backend.rpc_broadcast_mq.enqueue = bad_native.__get__(w2.backend.rpc_broadcast_mq)
    with pytest.raises(ValueError, match='EXAMPLE native error'):
        w2.backend.profile()
    assert w2.observer.disabled is True
    assert w2.observer.close()['restored'] is True


def test_binding_requires_actual_private_session_and_bounded_consumer():
    wrong = {**BINDING, 'session_id_sha256': 'f' * 64}
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_BINDING_UNAVAILABLE'):
        local.LocalProfilerObserver(lambda value: None, session=SESSION,
            binding_factory=lambda: wrong)
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local([], binding={**BINDING, 'pid': '1'})
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local([{}] * 1025, binding=BINDING)


def _loaded(monkeypatch, modules):
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)


def test_router_default_off_then_exact_api_and_core_fragments(tmp_path, monkeypatch):
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    before_modules = {name for name in sys.modules if name.startswith('vllm.')}
    off = local.LocalProfilerRouter(w.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE)
    assert off.observe_loaded(w.modules['vllm.v1.engine.async_llm']) is False
    assert off.last_diagnostic == 'DISABLED'
    asyncio.run(w.frontend.start_profile())
    assert w.records == [] and w.frontend.engine_core.sent == [('profile', True)]
    router = local.LocalProfilerRouter(w.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE, enabled=True)
    assert router.observe_loaded(w.modules['vllm.v1.engine.async_llm']) is False
    assert router.last_diagnostic == 'PARTIAL_SEGMENT'
    assert router.observe_loaded(w.modules['vllm.v1.engine.core_client']) is True
    assert router.last_diagnostic == 'INSTALLED'
    asyncio.run(w.frontend.stop_profile())
    assert local.project_local(w.records, binding=BINDING)['local_complete_calls'] == 2
    api_name = 'vllm.v1.engine.async_llm'
    monkeypatch.setitem(sys.modules, api_name, ModuleType(api_name))
    assert router.observe_loaded(w.modules['vllm.v1.engine.core_client']) is False
    assert router.last_diagnostic == 'MODULE_REPLACED'
    assert router.observe_loaded(sys.modules[api_name]) is False
    assert router.last_diagnostic == 'MODULE_REPLACED'
    monkeypatch.setitem(sys.modules, api_name, w.modules[api_name])
    assert router.observe_loaded(w.modules['vllm.v1.engine.core']) is False
    assert router.observe_loaded(w.modules['vllm.v1.executor.abstract']) is False
    assert router.observe_loaded(w.modules['vllm.v1.executor.multiproc_executor']) is True
    w.core._handle_client_request(w.profile_type(True))
    result = local.project_local(w.records, binding=BINDING)
    assert result['local_complete_calls'] == 5 and result['local_parent_pairs'] == 3
    assert result['cross_process_rpc_verified'] is False
    assert {name for name in sys.modules if name.startswith('vllm.')} == before_modules
    assert router.observe_loaded(w.modules['vllm.v1.engine.async_llm']) is False
    assert router.last_diagnostic == 'ALREADY_INSTALLED'
    assert router.close()['restored'] is True
    assert off.close()['restored'] is True


def test_router_partial_foreign_unloaded_source_failure_and_hook_owner(tmp_path, monkeypatch):
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    router = local.LocalProfilerRouter(w.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE, enabled=True)
    assert router.observe_loaded(ModuleType('not.vllm')) is False
    assert router.last_diagnostic == 'UNSUPPORTED_MODULE'
    impostor = ModuleType('vllm.v1.engine.async_llm')
    assert router.observe_loaded(impostor) is False
    assert router.last_diagnostic == 'MODULE_NOT_LOADED'
    assert router.observe_loaded(w.modules['vllm.v1.engine.async_llm']) is False
    assert router.last_diagnostic == 'PARTIAL_SEGMENT'
    assert w.records == []
    path = Path(w.modules['vllm.v1.engine.core_client'].__file__)
    path.write_bytes(path.read_bytes() + b'\n# source mutation\n')
    assert router.observe_loaded(w.modules['vllm.v1.engine.core_client']) is False
    assert router.last_diagnostic == 'SOURCE_UNAVAILABLE'
    asyncio.run(w.frontend.start_profile())
    assert w.records == [] and w.frontend.engine_core.sent == [('profile', True)]
    assert router.close()['restored'] is True

    other = world(tmp_path / 'owned', install=False)
    _loaded(monkeypatch, other.modules)
    api = {name: other.modules[name] for name in local.SEGMENTS['api']}
    owner = local.LocalProfilerObserver(other.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING))
    owner.install(api, image=local.IMAGE)
    wrapped = vars(other.modules['vllm.v1.engine.async_llm'].AsyncLLM)['start_profile']
    router2 = local.LocalProfilerRouter(other.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE, enabled=True)
    assert router2.observe_loaded(other.modules['vllm.v1.engine.async_llm']) is False
    assert router2.observe_loaded(other.modules['vllm.v1.engine.core_client']) is False
    assert router2.last_diagnostic == 'SOURCE_UNAVAILABLE'
    assert vars(other.modules['vllm.v1.engine.async_llm'].AsyncLLM)['start_profile'] is wrapped
    assert router2.close()['restored'] is True
    assert owner.close()['restored'] is True


def test_router_rejects_member_replaced_between_stages(tmp_path, monkeypatch):
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    router = local.LocalProfilerRouter(w.records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE, enabled=True)
    api_name = 'vllm.v1.engine.async_llm'
    assert router.observe_loaded(w.modules[api_name]) is False
    monkeypatch.setitem(sys.modules, api_name, ModuleType(api_name))
    assert router.observe_loaded(w.modules['vllm.v1.engine.core_client']) is False
    assert router.last_diagnostic == 'MODULE_REPLACED'
    asyncio.run(w.frontend.start_profile())
    assert w.records == [] and w.frontend.engine_core.sent == [('profile', True)]
    assert router.close()['restored'] is True


def test_router_sink_failure_preserves_return_exception_and_cancellation(tmp_path, monkeypatch):
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    def unavailable_sink(row):
        raise RuntimeError('EXAMPLE sink unavailable')
    router = local.LocalProfilerRouter(unavailable_sink, session=SESSION,
        binding_factory=lambda: dict(BINDING), image=local.IMAGE, enabled=True)
    assert router.observe_loaded(w.modules['vllm.v1.engine.async_llm']) is False
    assert router.observe_loaded(w.modules['vllm.v1.engine.core_client']) is True
    assert asyncio.run(w.frontend.start_profile()) is None
    assert w.frontend.engine_core.sent == [('profile', True)]
    w.frontend.engine_core.failure = ValueError('EXAMPLE native failure')
    with pytest.raises(ValueError, match='EXAMPLE native failure'):
        asyncio.run(w.frontend.start_profile())
    w.frontend.engine_core.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(w.frontend.stop_profile())
    assert router.observers['api'].disabled is True
    assert router.close()['restored'] is True


def test_router_rebinds_after_fork_without_inherited_hook_reuse(tmp_path, monkeypatch):
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    epoch = [1]
    factory = lambda: direct.process_binding(SESSION,
        {'pid': os.getpid(), 'start_epoch': epoch[0]})
    router = local.LocalProfilerRouter(w.records.append, session=SESSION,
        binding_factory=factory, image=local.IMAGE, enabled=True)
    api = w.modules['vllm.v1.engine.async_llm']
    client = w.modules['vllm.v1.engine.core_client']
    assert router.observe_loaded(api) is False
    assert router.observe_loaded(client) is True
    prior = router.observers['api'].binding
    real_pid = os.getpid()
    epoch[0] = 2
    monkeypatch.setattr(local.os, 'getpid', lambda: real_pid + 1)
    asyncio.run(w.frontend.start_profile())
    assert w.records == []  # inherited hooks are inert before router restaging
    assert router.observe_loaded(api) is False
    assert router.last_diagnostic == 'PARTIAL_SEGMENT'
    assert router.observe_loaded(client) is True
    fresh = router.observers['api'].binding
    assert fresh['pid'] == real_pid + 1
    assert fresh['process_start_sha256'] != prior['process_start_sha256']
    asyncio.run(w.frontend.start_profile())
    assert local.project_local(w.records, binding=fresh)['local_complete_calls'] == 2
    assert router.close()['restored'] is True


def test_consumer_rejects_false_parent_duplicate_and_reordered_pair(tmp_path):
    w = world(tmp_path)
    asyncio.run(w.frontend.start_profile())
    records = deepcopy(w.records)
    assert local.project_local(records, binding=BINDING)['local_parent_pairs'] == 1
    altered = deepcopy(records)
    for child in altered:
        if child['point'] == 'AsyncMPClient.profile_async':
            child['parent_call_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local(altered, binding=BINDING)
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local(records + [deepcopy(records[-1])], binding=BINDING)
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local(list(reversed(records)), binding=BINDING)
    parent_first = [records[0], records[-1], records[1], records[2]]
    with pytest.raises(ValueError, match='BASELINE_PROFILER_LOCAL_RECORD_INVALID'):
        local.project_local(parent_first, binding=BINDING)
    assert w.observer.close()['restored'] is True


@pytest.mark.parametrize('enabled', [False, True])
def test_owned_callback_installer_stages_local_fragments_without_foreign_imports(tmp_path, monkeypatch, enabled):
    from guard.adapters import vllm_engine_callback_sensor as sensor
    from guard.collectors import engine_callbacks
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    rows = []
    expected_image = local.IMAGE if enabled else None
    context = lambda: {'pid': os.getpid(), 'test': 'owned-profiler'}
    callbacks = sensor.EngineCallbacks(SESSION, b'EXAMPLE-local-profiler-key-01', rows.append,
        context_provider=context, baseline_profiler_local=enabled,
        baseline_profiler_local_image=expected_image)
    registry = {name for name in sys.modules if name.startswith('vllm.')}
    for module in w.modules.values():
        callbacks.instrument(module)
    asyncio.run(w.frontend.start_profile())
    w.core._handle_client_request(w.profile_type(True))
    actual = [row for row in rows if row.get('schema') == local.SCHEMA]
    if enabled:
        assert set(callbacks.local_profiler.observers) == {'api', 'core'}
        binding = direct.process_binding(SESSION, context())
        result = local.project_local(actual, binding=binding)
        assert result['local_complete_calls'] == 5 and result['local_parent_pairs'] == 3
        assert result['whole_scope_complete'] is False and result['cross_process_rpc_verified'] is False
        assert callbacks.local_profiler.close()['restored'] is True
    else:
        assert callbacks.local_profiler is None and actual == []
    assert {name for name in sys.modules if name.startswith('vllm.')} == registry
    prepared = engine_callbacks.prepare_assets(tmp_path / 'assets', baseline_profiler_local=enabled,
        baseline_profiler_local_image=expected_image)
    assert prepared['baseline_profiler_local'] is enabled and prepared['engine_started'] is False
    assert prepared['baseline_profiler_local_image'] == expected_image
    assert 'guard_engine_callbacks/vllm_baseline_profiler_local.py' in prepared['assets']
    assert 'guard_engine_callbacks/vllm_rank_ipc_sources.py' in prepared['assets']
    assert engine_callbacks.load_prepared(tmp_path / 'assets') == prepared


def test_local_callback_preparation_requires_explicit_image_and_separate_mode(tmp_path):
    from guard.collectors import engine_callbacks
    with pytest.raises(ValueError, match='PROFILER_LOCAL_IMAGE_REQUIRED'):
        engine_callbacks.prepare_assets(tmp_path / 'missing', baseline_profiler_local=True)
    with pytest.raises(ValueError, match='PROFILER_LOCAL_FLAG_REQUIRED'):
        engine_callbacks.prepare_assets(tmp_path / 'off', baseline_profiler_local_image=local.IMAGE)
    with pytest.raises(ValueError, match='PROFILER_LOCAL_EXCLUSIVE'):
        engine_callbacks.prepare_assets(tmp_path / 'mixed', baseline_profiler_local=True,
            baseline_profiler_local_image=local.IMAGE, profiler_control=True)


def test_inherited_callback_does_not_restage_profiler_into_parent_sink(tmp_path, monkeypatch):
    from guard.adapters import vllm_engine_callback_sensor as sensor
    w = world(tmp_path, install=False)
    _loaded(monkeypatch, w.modules)
    rows = []
    callbacks = sensor.EngineCallbacks(SESSION, b'EXAMPLE-local-profiler-key-01', rows.append,
        context_provider=lambda: {'pid': os.getpid(), 'test': 'owned-profiler'},
        baseline_profiler_local=True, baseline_profiler_local_image=local.IMAGE)
    for name in local.SEGMENTS['api']:
        callbacks.instrument(w.modules[name])
    asyncio.run(w.frontend.start_profile())
    count = sum(row.get('schema') == local.SCHEMA for row in rows)
    parent_pid = os.getpid()
    with monkeypatch.context() as child:
        child.setattr(local.os, 'getpid', lambda: parent_pid + 1)
        callbacks.instrument(w.modules['vllm.v1.engine.async_llm'])
        asyncio.run(w.frontend.stop_profile())
        assert sum(row.get('schema') == local.SCHEMA for row in rows) == count
        assert w.frontend.engine_core.sent == [('profile', True), ('profile', False)]
    assert callbacks.local_profiler.close()['restored'] is True
