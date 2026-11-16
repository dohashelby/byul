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


def world(tmp_path):
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
