"""Exact initial V1 profiler source; all backend/I/O collaborators are EXAMPLE."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import types
from types import ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_baseline_profiler as native
from guard.adapters import vllm_baseline_profiler_composition as composition
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_sensor as base
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_profiler_sensor as original
from guard.adapters import vllm_profiler_metadata as consumer
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_lifecycle_sensor import CONTEXT, FakeOS, SilentThread
from tests.test_ma6_baseline_startup import rechain

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SESSION = 'a' * 32
BINDING = native.process_binding(SESSION, {**CONTEXT, 'pid': os.getpid()})


def worker_module(tmp_path):
    raw = (ROOT / 'tests/fixtures/vllm-0.7.0/vllm/v1/worker/gpu_worker.py.txt').read_bytes()
    path = tmp_path / 'gpu_worker.py'
    path.write_bytes(raw)
    codes = sources._codes(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    module = ModuleType(native.WORKER)
    module.__file__ = str(path)
    method = types.FunctionType(codes[native.POINT], vars(module))
    method.__defaults__ = (True,)  # exact source default, evaluated outside function code
    module.Worker = type('Worker', (), {'__module__': native.WORKER, 'profile': method})
    return module


class ExampleBackend:
    def __init__(self):
        self.active = False
        self.calls = []
        self.stop_error = None
    def start(self):
        self.calls.append('start')
        self.active = True
    def stop(self):
        self.calls.append('stop')
        if self.stop_error is not None:
            raise self.stop_error
        self.active = False


def world(tmp_path, *, backend=None):
    module = worker_module(tmp_path)
    worker = module.Worker()
    worker.rank = worker.local_rank = 0
    worker.profiler = ExampleBackend() if backend is None else backend
    records = []
    observer = native.ProfilerCandidate(records.append, session=SESSION,
        binding_factory=lambda: dict(BINDING), base=base, sources=sources, profiler=original)
    observer.install(module, image='vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8')
    return SimpleNamespace(module=module, worker=worker, records=records, observer=observer)


def example_probe(observer):
    """EXAMPLE deterministic state-machine inputs, never a real C-probe claim."""
    def snapshot(owner):
        backend = vars(owner)['profiler']
        return {'value': backend.active, 'collected': True,
                'backend_sha256': observer._nonce(backend), 'thread_id': 11}
    observer._backend = snapshot


def test_exact_source_default_and_unsupported_records_reach_existing_consumer(tmp_path):
    w = world(tmp_path)
    assert w.worker.profile() is None
    assert w.worker.profile(1) is None  # native truthiness preserved; no fabricated bool
    result = consumer.native_backend_project(w.records, binding=BINDING)
    assert result['complete_calls'] == 2 and result['backend_cycles'] == []
    assert w.records[0]['is_start'] is True and w.records[2]['is_start'] is None
    assert all(row['backend_before']['collected'] is False for row in w.records)
    assert all(row['backend_probe_qualified'] is False for row in w.records)
    assert w.worker.profiler.calls == ['start', 'start']
    assert result['scope_complete'] is False
    assert w.observer.close()['restored'] is True


def test_python_probe_even_with_forged_module_name_is_not_independent_builtin(tmp_path, monkeypatch):
    w = world(tmp_path)
    torch = ModuleType('torch')
    probe = lambda: True
    probe.__module__ = 'torch._C._autograd'
    torch.profiler = SimpleNamespace(profile=ExampleBackend)
    torch.autograd = SimpleNamespace(_profiler_enabled=probe)
    torch._C = SimpleNamespace(_autograd=SimpleNamespace(_profiler_enabled=probe))
    monkeypatch.setitem(sys.modules, 'torch', torch)
    w.worker.profile(True)
    assert w.records[-1]['backend_before']['backend_sha256']
    assert w.records[-1]['backend_before']['collected'] is False
    assert w.records[-1]['backend_after']['collected'] is False
    assert w.records[-1]['backend_generation_sha256'] is None
    consumer.native_backend_project(w.records, binding=BINDING)
    w.observer.close()


@pytest.mark.parametrize('stop_fails', [False, True])
def test_generation_created_only_by_successful_start_and_stop_outcome_preserved(tmp_path, stop_fails):
    w = world(tmp_path)
    example_probe(w.observer)
    error = RuntimeError('PRIVATE_EXAMPLE_STOP_ERROR')
    w.worker.profiler.stop_error = error if stop_fails else None
    w.worker.profile(True)
    if stop_fails:
        with pytest.raises(RuntimeError) as raised:
            w.worker.profile(False)
        assert raised.value is error
    else:
        w.worker.profile(False)
    result = consumer.native_backend_project(w.records, binding=BINDING)
    cycle, = result['backend_cycles']
    assert cycle['backend_collection_stopped_observed'] is (not stop_fails)
    assert cycle['backend_collection_continued_observed'] is stop_fails
    assert cycle['stop_phase'] == ('RAISE' if stop_fails else 'RETURN')
    assert cycle['worker_sha256'] != cycle['backend_sha256']
    assert cycle['trace_output_completed_verified'] is False
    assert cycle['cross_process_rpc_verified'] is False
    assert 'wrapper_sha256' not in cycle
    assert 'PRIVATE_EXAMPLE_STOP_ERROR' not in json.dumps(w.records)
    for mutate in (lambda r: r[0]['binding'].update(process_start_sha256='f' * 64),
                   lambda r: r[0].update(source_sha256='0' * 64),
                   lambda r: r[1].update(callback_directory_binding_verified=True)):
        changed = deepcopy(w.records)
        mutate(changed)
        with pytest.raises(ValueError):consumer.native_backend_project(changed, binding=BINDING)
    w.observer.close()


def test_stop_without_successful_start_has_no_generation_or_backend_cycle(tmp_path):
    w = world(tmp_path)
    example_probe(w.observer)
    w.worker.profiler.active = True
    w.worker.profile(False)
    assert w.records[-1]['backend_generation_sha256'] is None
    assert consumer.native_backend_project(w.records, binding=BINDING)['backend_cycles'] == []
    w.observer.close()


def test_source_mismatch_sink_failure_and_later_owner_change_remain_passive(tmp_path):
    w = world(tmp_path)
    w.observer.emit = lambda row: (_ for _ in ()).throw(RuntimeError('PRIVATE_SINK'))
    w.worker.profile(True)
    assert w.worker.profiler.calls == ['start'] and w.observer.disabled is True
    replacement = lambda self, is_start=True: 'later'
    w.module.Worker.profile = replacement
    assert w.observer.close()['restored'] is False
    assert w.module.Worker.profile is replacement
    with pytest.raises(ValueError, match='BASELINE_PROFILER_SOURCE_UNAVAILABLE'):
        native.qualify(w.module, image='vllm/vllm-openai@sha256:a43963ed149a7b8b6c8c9dd028d4ab2be9fe804761d41b11cc07043a1edb61a8', base=base, sources=sources)


def composed(external):
    directory = external / 'assets'
    prepared = lifecycle.prepare_assets(directory, REPO, profile=composition.PROFILER_PROFILE)
    module = ModuleType('_baseline_profiler_composition_test')
    module.__file__ = str(directory / 'guard_lifecycle_sensor.py')
    exec(compile(catalog.render_source(composition.PROFILER_PROFILE), module.__file__, 'exec', dont_inherit=True, optimize=0), vars(module))
    module._dependency = lambda: Path(base.__file__).read_bytes()
    module.STARTUP_DIRECTORY = str(directory)
    selected = module._private_base()
    fake = FakeOS()
    fake.getpid = os.getpid
    context = {**CONTEXT, 'pid': os.getpid()}
    selected.os = fake
    selected._context = lambda: dict(context)
    selected._open_directory = lambda path: 10
    selected.threading = SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event)
    settings = json.loads((directory / 'settings.json').read_bytes())
    observer = selected.Observer(settings, prepared['assets']['settings.json']['sha256'])
    return SimpleNamespace(prepared=prepared, module=module, base=selected, fake=fake,
        context=context, observer=observer, directory=directory)


def test_prepared_composition_and_existing_capture_retain_actual_native_outcome(external, tmp_path):  # noqa: F811
    import asyncio
    w = composed(external)
    assert lifecycle.load_prepared(w.directory / 'manifest.json') == w.prepared
    assert w.base.SOURCE_SHA == {native.WORKER: native.SOURCE['sha256']}
    assert w.base.POINTS == {native.WORKER: {}} and w.base.FUNCTION_POINTS == w.base.HANDLER_POINTS == {}
    module = worker_module(tmp_path)
    worker = module.Worker()
    worker.rank = worker.local_rank = 0
    worker.profiler = ExampleBackend()
    w.observer.instrument(module)
    info = {'source_sha256': native.SOURCE['sha256'], 'code_sha256': 'c' * 64}
    w.observer.modules[native.WORKER] = info
    w.observer.emit('MODULE', {'module': native.WORKER, 'verified': True, **info})
    private_id = 'profile-request-7'
    scope = {'type': 'http', 'headers': [(b'x-request-id', private_id.encode())]}
    async def untouched_app(actual_scope, receive, send):
        assert actual_scope is scope
        return 'unchanged'
    assert asyncio.run(w.base.Middleware(untouched_app)(scope, None, None)) == 'unchanged'
    worker.profile(True)
    worker.profile(False)
    w.observer.close()
    rows, summary = capture.parse_recording(bytes(w.fake.data), w.prepared)
    assert private_id.encode() not in w.fake.data
    assert hashlib.sha256(private_id.encode()).hexdigest().encode() not in w.fake.data
    assert summary['baseline_profiler']['complete_calls'] == 2
    assert summary['baseline_profiler']['backend_cycles'] == []  # Python EXAMPLE backend has no qualified builtin
    assert summary['baseline_profiler']['scope_complete'] is False
    for event in ('ASGI', 'NATIVE', 'SELECTION', 'PROFILER'):
        changed = deepcopy(rows)
        changed.insert(-1, {'seq': 0, 'event': event, 'monotonic_ns': rows[-1]['monotonic_ns'],
            'previous_sha256': '0' * 64, 'payload': {}})
        with pytest.raises(ValueError):capture.parse_recording(rechain(changed), w.prepared)
    changed = deepcopy(rows)
    next(row for row in changed if row['event'] == 'BASELINE_PROFILER')['payload']['binding']['process_start_sha256'] = 'e' * 64
    with pytest.raises(ValueError):capture.parse_recording(rechain(changed), w.prepared)


@pytest.mark.parametrize('damage', ['bytes', 'rehash', 'symlink'])
def test_private_profiler_assets_require_exact_nofollow_bytes(external, damage):  # noqa: F811
    w = composed(external)
    path = w.directory / 'guard_baseline_profiler.py'
    if damage == 'symlink':
        path.unlink()
        path.symlink_to(native.__file__)
    else:
        path.write_bytes(path.read_bytes() + b'\n')
        if damage == 'rehash':
            manifest = json.loads((w.directory / 'manifest.json').read_bytes())
            manifest['assets'][path.name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            (w.directory / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError):lifecycle.load_prepared(w.directory / 'manifest.json')
    with pytest.raises((ValueError, OSError)):w.module._private_base()
    w.observer.close()


def test_declared_owned_trace_inventory_is_not_consumed_callback_or_output_truth(tmp_path, monkeypatch):
    w = world(tmp_path)
    example_probe(w.observer)
    root = tmp_path / 'owned'
    root.mkdir(mode=0o700)
    folder = root / SESSION
    folder.mkdir(mode=0o700)
    real_open = original.os.open
    def opened(path, flags, *args, **kwargs):
        return real_open(root if path == '/guard-profiler-traces' else path, flags, *args, **kwargs)
    monkeypatch.setattr(original.os, 'open', opened)
    w.worker.profile(True)
    stop = w.worker.profiler.stop
    def write_trace():
        (folder / 'EXAMPLE.pt.trace.json').write_text(json.dumps({'traceEvents': [{'name': 'PRIVATE_TRACE_BODY'}]}))
        stop()
    w.worker.profiler.stop = write_trace
    w.worker.profile(False)
    result = consumer.native_backend_project(w.records, binding=BINDING)
    cycle, = result['backend_cycles']
    trace, = cycle['new_declared_directory_trace_facts']
    assert trace['trace_events'] == 1 and trace['kind'] == 'trace'
    assert cycle['callback_directory_binding_verified'] is False
    assert cycle['trace_output_completed_verified'] is False
    assert 'PRIVATE_TRACE_BODY' not in json.dumps(w.records)
    assert 'EXAMPLE.pt.trace.json' not in json.dumps(w.records)
    w.observer.close()


def test_native_disabled_profiler_failure_is_preserved_as_component_outcome(tmp_path):
    w = world(tmp_path)
    w.worker.profiler = None
    with pytest.raises(RuntimeError) as error:
        w.worker.profile()
    assert str(error.value) == 'Profiler is not enabled.'
    result = consumer.native_backend_project(w.records, binding=BINDING)
    assert result['complete_calls'] == 1 and result['backend_cycles'] == []
    assert w.records[-1]['phase'] == 'RAISE' and w.records[-1]['backend_after']['collected'] is False
    assert 'Profiler is not enabled.' not in json.dumps(w.records)
    w.observer.close()
