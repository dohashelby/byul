"""Exact-source baseline startup candidates; engine/GPU never imported or started."""
from copy import deepcopy
import hashlib
import json
import os
import types
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_baseline_startup as sensor
from guard.adapters import vllm_worker_readiness as callback
from guard.collectors import baseline_startup as consumer

FIXTURE = Path(__file__).parent / 'fixtures/vllm-0.7.0'
BINDING = {'pid': os.getpid(), 'session_id_sha256': 'a' * 64, 'process_start_sha256': 'b' * 64}


def native_module(name, tmp_path):
    """Construct native functions from untouched full-source compiled code.

    No native module body or annotation expression is executed. The exact code
    objects, including flags, locations and nested functions, remain unchanged.
    """
    row = sensor.ROWS[name]
    raw = (FIXTURE / (row["path"] + ".txt")).read_bytes()
    path = tmp_path / (name + ".py")
    path.write_bytes(raw)
    codes = sensor.sources._codes(compile(raw, str(path), "exec", dont_inherit=True, optimize=0))
    module = ModuleType(name)
    module.__file__ = str(path)
    for point in row["functions"]:
        cls_name, method = point.split(".")
        if cls_name not in vars(module):
            vars(module)[cls_name] = type(cls_name, (), {"__module__": name})
        function = types.FunctionType(codes[point], vars(module))
        assert function.__qualname__ == point
        descriptor = staticmethod(function) if point in sensor.STATIC else function
        setattr(vars(module)[cls_name], method, descriptor)
    return module


def test_exact_source_receipts_and_original_observers_are_unchanged():
    provenance = json.loads((FIXTURE / 'PROVENANCE.json').read_text())
    assert provenance['tag_commit'] == sensor.COMMIT
    for row in sensor.ROWS.values():
        raw = (FIXTURE / (row['path'] + '.txt')).read_bytes()
        assert len(raw) == row['bytes']
        assert hashlib.sha256(raw).hexdigest() == row['sha256'] == provenance['files'][row['path'] + '.txt']['sha256']
    root = Path(sensor.__file__).parent
    assert hashlib.sha256((root / 'vllm_initialization_sensor.py').read_bytes()).hexdigest() == '654b22da19bd6ff8cd0e72edb29fa705afa6c01e6fb3616cef9a091e17e9977f'
    assert 'Worker.initialize_cache' not in callback.POINTS
    assert 'Worker.initialize_from_config' not in sensor.ROWS[sensor.WORKER]['functions']


def test_existing_default_and_unsupported_producer_records_reach_consumer():
    observer = callback.WorkerReadinessObserver()
    for point, args in [('Worker.load_model', ()), ('Worker.initialize_cache', (object(),))]:
        record = observer.snapshot(point, args, {}, phase='CALL')
        result = consumer.consume_worker_snapshot(record)
        assert result['status'] == 'unavailable' and result['paired'] is False
        assert result['point'] == (point if point in callback.POINTS else 'unsupported')
        assert result['whole_scope_complete'] is False
    assert observer.close()['pending_calls'] == 0


def test_qualification_is_exact_and_does_not_replace_originals(tmp_path):
    for name in sensor.ROWS:
        module = native_module(name, tmp_path)
        result = sensor.qualify(module, image=sensor.IMAGE)
        assert set(result['functions']) == set(sensor.ROWS[name]['functions'])
        with pytest.raises(ValueError, match='BASELINE_STARTUP_SOURCE_UNAVAILABLE'):
            sensor.qualify(module, image='other-image')
        Path(module.__file__).write_bytes(Path(module.__file__).read_bytes() + b'\n')
        with pytest.raises(ValueError, match='BASELINE_STARTUP_SOURCE_UNAVAILABLE'):
            sensor.qualify(module, image=sensor.IMAGE)


def test_changed_function_is_rejected_without_installing_any_hook(tmp_path):
    module = native_module(sensor.WORKER, tmp_path)
    module.Worker.load_model = lambda self: None
    observer = sensor.StartupCandidate(lambda row: None, binding=BINDING)
    with pytest.raises(ValueError, match='BASELINE_STARTUP_SOURCE_UNAVAILABLE'):
        observer.install(module, image=sensor.IMAGE)
    assert observer.installed == []


def worker_world(tmp_path, *, fail=False):
    module = native_module(sensor.WORKER, tmp_path)
    worker = module.Worker.__new__(module.Worker)
    worker.rank = worker.local_rank = 0
    worker.model_config = SimpleNamespace(enforce_eager=True)
    worker.vllm_config = SimpleNamespace(model_config=SimpleNamespace(enable_sleep_mode=False),
        compilation_config=SimpleNamespace(cudagraph_mode=None))
    class Failure(RuntimeError):
        pass
    error = Failure('must never be recorded')
    def load():
        if fail:
            raise error
    worker.model_runner = SimpleNamespace(load_model=load)
    records = []
    observer = sensor.StartupCandidate(records.append, binding=BINDING)
    observer.install(module, image=sensor.IMAGE)
    return module, worker, observer, records, error


def test_actual_qualified_worker_load_body_and_callback_pair_are_consumed(tmp_path):
    module, worker, observer, records, error = worker_world(tmp_path)
    original = observer.installed[1][2]
    assert worker.load_model() is None
    projected = consumer.project(records, binding=BINDING)
    fact, = projected['preparation_calls']
    assert fact['point'] == 'Worker.load_model' and fact['paired'] is True and fact['returned'] is True
    assert fact['rank'] == 0 and fact['owner_token']
    assert fact['constructor_interval_observed'] is False  # no constructor was observed
    assert projected['service_gate_verified'] is False
    assert 'DUMMY_FLAG_UNAVAILABLE' in fact['gaps']  # baseline has no dummy parameter
    assert observer.close()['restored'] is True
    assert vars(module.Worker)['load_model'] is original


def test_actual_body_exception_is_preserved_and_observation_sink_failure_is_passive(tmp_path):
    module, worker, observer, records, error = worker_world(tmp_path, fail=True)
    with pytest.raises(type(error)) as raised:
        worker.load_model()
    assert raised.value is error
    result = consumer.project(records, binding=BINDING)
    assert result['preparation_calls'][0]['returned'] is False
    assert 'must never be recorded' not in json.dumps(records)
    observer.close()
    def broken_sink(row):
        raise RuntimeError('sink failed')
    observer = sensor.StartupCandidate(broken_sink, binding=BINDING)
    observer.install(module, image=sensor.IMAGE)
    with pytest.raises(type(error)) as raised:
        worker.load_model()
    assert raised.value is error
    assert observer.disabled is True
    observer.close()


def test_tampered_scope_source_and_callback_are_rejected(tmp_path):
    module, worker, observer, records, error = worker_world(tmp_path)
    worker.load_model()
    for mutate in (lambda r: r[0].update(source_sha256='0' * 64),
                   lambda r: r[0]['binding'].update(process_start_sha256='c' * 64),
                   lambda r: r[1]['worker_snapshot']['metadata'].update(source_qualified=True)):
        changed = deepcopy(records)
        mutate(changed)
        with pytest.raises(ValueError, match='BASELINE_STARTUP_RECORD_INVALID'):
            consumer.project(changed, binding=BINDING)
    observer.close()


def inert_child_boundaries(mp, worker):
    import pickle
    import sys
    from contextlib import contextmanager
    sent = []
    worker.init_device = lambda: None  # no GPU, and no init-device claim
    worker.parallel_config = SimpleNamespace(world_size=1)
    wrapper = SimpleNamespace(worker=worker, init_worker=lambda kwargs: None)
    class Queue:
        def __init__(self, *args):
            pass
        def export_handle(self):
            return 'example-transport-handle'
        @staticmethod
        def create_from_handle(*args):
            return Queue()
    @contextmanager
    def socket(*args):
        yield SimpleNamespace(send_string=sent.append, send=lambda payload: None)
    vars(mp).update(os=os, sys=sys, pickle=pickle, WorkerWrapperBase=lambda **kwargs: wrapper,
        _add_prefix=lambda *args: None, MessageQueue=Queue, zmq_socket_ctx=socket,
        zmq=SimpleNamespace(constants=SimpleNamespace(PUSH=1)))
    mp.WorkerProc.READY_STR = 'READY'
    # Every actual load invocation must occur after the native constructor sent
    # transport READY; this is not a model-readiness observation.
    load = worker.model_runner.load_model
    def checked_load():
        assert sent == ['READY']
        return load()
    worker.model_runner.load_model = checked_load
    return SimpleNamespace(parallel_config=worker.parallel_config)


@pytest.mark.parametrize('fail', [False, True])
def test_actual_child_constructor_load_failure_correspondence_and_ready_precedes_load(tmp_path, fail):
    """Execute pinned constructor + load body with inert native I/O boundaries."""
    module, worker, observer, records, error = worker_world(tmp_path, fail=fail)
    mp = native_module(sensor.MP, tmp_path)
    config = inert_child_boundaries(mp, worker)
    observer.install(mp, image=sensor.IMAGE)
    if fail:
        with pytest.raises(type(error)) as raised:
            mp.WorkerProc(vllm_config=config, local_rank=0, rank=0,
                distributed_init_method='example', input_shm_handle=None, ready_path='example')
        assert raised.value is error
    else:
        mp.WorkerProc(vllm_config=config, local_rank=0, rank=0,
            distributed_init_method='example', input_shm_handle=None, ready_path='example')
    result = consumer.project(records, binding=BINDING)
    preparation, = result['preparation_calls']
    assert preparation['constructor_interval_observed'] is True
    assert preparation['python_body_return_observed'] is (not fail)
    if fail:
        propagation, = result['local_failure_propagation']
        assert propagation['component_failure_constructor_failure_observed'] is True
        assert propagation['parent_process_aggregate_verified'] is False
        assert propagation['confirmed_violation'] is False
    else:
        assert result['local_failure_propagation'] == []
    assert result['whole_scope_complete'] is False
    observer.close()


def test_wait_for_startup_return_is_transport_only_and_does_not_read_handle(tmp_path):
    from contextlib import contextmanager
    mp = native_module(sensor.MP, tmp_path)
    class Opaque:
        def __getattribute__(self, key):
            raise AssertionError('native handle must not be inspected')
    handle = Opaque()
    @contextmanager
    def socket(*args):
        yield SimpleNamespace(poll=lambda **kwargs: 1, recv_string=lambda: 'READY',
            recv=lambda **kwargs: SimpleNamespace(buffer=b'example'))
    vars(mp).update(zmq_socket_ctx=socket, zmq=SimpleNamespace(constants=SimpleNamespace(PULL=1)),
        POLLING_TIMEOUT_MS=5000, pickle=SimpleNamespace(loads=lambda frame: handle))
    mp.WorkerProc.READY_STR = 'READY'
    records = []
    observer = sensor.StartupCandidate(records.append, binding=BINDING)
    observer.install(mp, image=sensor.IMAGE)
    assert mp.WorkerProc.wait_for_startup(object(), 'example') is handle
    projected = consumer.project(records, binding=BINDING)
    fact, = projected['transport_handshakes']
    assert fact['transport_handshake_return_observed'] is True
    assert fact['model_ready_verified'] is False
    assert projected['preparation_calls'] == []
    observer.close()


def test_restore_does_not_overwrite_a_later_function_change(tmp_path):
    module, worker, observer, records, error = worker_world(tmp_path)
    replacement = lambda self: 'later-owner'
    module.Worker.load_model = replacement
    result = observer.close()
    assert result['restored'] is False
    assert vars(module.Worker)['load_model'] is replacement


def test_changed_original_code_is_not_observed_as_qualified(tmp_path):
    module, worker, observer, records, error = worker_world(tmp_path)
    original = observer.installed[1][2]
    original.__code__ = (lambda self: 'changed').__code__
    assert worker.load_model() == 'changed'
    assert records == [] and observer.disabled is True
    observer.close()


# /31 reuses the prepared lifecycle assets, private original helpers, Writer and
# capture parser. These remain synthetic process/native boundaries, never GPU.
from guard.adapters import vllm_lifecycle_catalog as catalog
from guard.adapters import vllm_lifecycle_sensor as legacy
from guard.adapters import vllm_baseline_startup_composition as composition
from guard.collectors import lifecycle
from guard.executors import lifecycle_capture as capture
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_lifecycle_sensor import CONTEXT, FakeOS, SilentThread
import threading

REPO = Path(__file__).resolve().parents[2]
STARTUP_PROFILE = composition.STARTUP_PROFILE


def composed_world(external):
    prepared = lifecycle.prepare_assets(external / 'assets', REPO, profile=STARTUP_PROFILE)
    module = ModuleType('_baseline_startup_composition_test')
    module.__file__ = str(external / 'assets/guard_lifecycle_sensor.py')
    exec(compile(catalog.render_source(STARTUP_PROFILE), module.__file__, 'exec', dont_inherit=True, optimize=0), vars(module))
    module._dependency = lambda: Path(legacy.__file__).read_bytes()
    module.STARTUP_DIRECTORY = str(external / 'assets')
    base = module._private_base()  # verifies base ABI and every exact private dependency
    fake = FakeOS()
    fake.getpid = os.getpid
    context = {**CONTEXT, 'pid': os.getpid()}
    base.os = fake
    base._context = lambda: dict(context)
    base._open_directory = lambda path: 10
    base.threading = SimpleNamespace(RLock=threading.RLock, Thread=SilentThread, Event=threading.Event)
    settings = json.loads((external / 'assets/settings.json').read_bytes())
    observer = base.Observer(settings, prepared['assets']['settings.json']['sha256'])
    return SimpleNamespace(prepared=prepared, module=module, base=base, fake=fake,
        context=context, observer=observer, root=external)


def attach_native(world, module):
    # Same post-source-load installation entry used by immutable CheckedLoader.
    world.observer.instrument(module)
    name = module.__name__
    info = {'source_sha256': world.base.SOURCE_SHA[name], 'code_sha256': 'c' * 64}
    world.observer.modules[name] = info
    world.observer.emit('MODULE', {'module': name, 'verified': True, **info})


def rechain(rows):
    previous = '0' * 64
    output = b''
    for seq, row in enumerate(rows):
        row['seq'], row['previous_sha256'] = seq, previous
        line = capture.canonical_row(row)
        output += line
        previous = hashlib.sha256(line).hexdigest()
    return output


def test_startup_prepared_roundtrip_exact_private_dependencies_and_legacy_bytes(external):  # noqa: F811
    world = composed_world(external)
    assert lifecycle.load_prepared(external / 'assets/manifest.json') == world.prepared
    assert set(world.prepared['assets']) == {'guard_lifecycle_sensor.py', 'guard_lifecycle_base.py',
        'sitecustomize.py', 'settings.json', *catalog.startup_assets(STARTUP_PROFILE)}
    assert len(world.base.SOURCE_SHA) == 10
    assert legacy.PROFILE == 'vllm-native-lifecycle-metadata/8'
    assert catalog.row('vllm-native-lifecycle-metadata/27')['sources'].keys() < world.base.SOURCE_SHA.keys()
    for logical, (filename, digest) in composition.DEPENDENCIES.items():
        assert world.prepared['assets'][filename]['sha256'] == digest
    assert world.prepared['product_connected'] is False
    # The old profile has no extension or newly inherited startup targets.
    old = catalog.render_source('vllm-native-lifecycle-metadata/27')
    assert b'BASELINE_STARTUP' not in old and b'guard_baseline_startup.py' not in old
    world.observer.close()


@pytest.mark.parametrize('damage', ['bytes', 'symlink', 'manifest-rehash'])
def test_startup_prepared_rejects_changed_dependency_even_after_manifest_rehash(external, damage):  # noqa: F811
    world = composed_world(external)
    path = external / 'assets/guard_baseline_startup.py'
    if damage == 'symlink':
        path.unlink()
        path.symlink_to(sensor.__file__)
    else:
        path.write_bytes(path.read_bytes() + b'\n')
        if damage == 'manifest-rehash':
            manifest_path = external / 'assets/manifest.json'
            manifest = json.loads(manifest_path.read_bytes())
            manifest['assets'][path.name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        lifecycle.load_prepared(external / 'assets/manifest.json')
    with pytest.raises((ValueError, OSError)):
        world.module._private_base()
    world.observer.close()


def test_composed_worker_producer_writer_and_capture_consumer_keep_actual_failure(external, tmp_path):  # noqa: F811
    world = composed_world(external)
    module, worker, previous, unused, error = worker_world(tmp_path, fail=True)
    previous.close()
    attach_native(world, module)
    with pytest.raises(type(error)) as raised:
        worker.load_model()
    assert raised.value is error
    world.observer.close()
    rows, summary = capture.parse_recording(bytes(world.fake.data), world.prepared)
    projected = summary['baseline_startup']
    assert projected['paired_calls'] == 1
    fact, = projected['preparation_calls']
    assert fact['point'] == 'Worker.load_model' and fact['returned'] is False
    assert fact['paired'] is True and fact['constructor_interval_observed'] is False
    assert projected['service_gate_verified'] is False and projected['whole_scope_complete'] is False
    assert 'must never be recorded' not in bytes(world.fake.data).decode()
    assert summary['recording_closed'] is True
    startup_rows = [row for row in rows if row['event'] == 'BASELINE_STARTUP']
    assert startup_rows[0]['payload']['binding'] == sensor.process_binding(world.prepared['session'], world.context)
    for mutate in (lambda r: r['payload']['binding'].update(process_start_sha256='f' * 64),
                   lambda r: r['payload'].update(source_sha256='0' * 64),
                   lambda r: r['payload'].update(runtime='cpython-3.12.99')):
        changed = deepcopy(rows)
        mutate(next(row for row in changed if row['event'] == 'BASELINE_STARTUP'))
        with pytest.raises(ValueError):
            capture.parse_recording(rechain(changed), world.prepared)
    changed = deepcopy(rows)
    next(row for row in changed if row['event'] == 'MODULE')['payload']['verified'] = False
    with pytest.raises(ValueError, match='BASELINE_STARTUP_SOURCE_PROVENANCE_UNAVAILABLE'):
        capture.parse_recording(rechain(changed), world.prepared)


def test_process_factory_uses_new_context_and_discards_inherited_parent_interval(tmp_path, monkeypatch):
    module = native_module(sensor.WORKER, tmp_path)
    parent_binding = dict(BINDING)
    current = [os.getpid()]
    next_binding = {**parent_binding, 'pid': os.getpid() + 1, 'process_start_sha256': 'c' * 64}
    observer = sensor.StartupCandidate(lambda record: None, binding=parent_binding, binding_factory=lambda: next_binding)
    observer.install(module, image=sensor.IMAGE)
    old_callback = observer.callback
    sensor._PARENT.set('d' * 64)
    monkeypatch.setattr(sensor, 'os', SimpleNamespace(getpid=lambda: current[0], name='posix'))
    current[0] = next_binding['pid']
    assert observer._current_process() is True
    assert observer.binding == next_binding and observer.callback is not old_callback
    assert sensor._PARENT.get() is None
    observer.close()


def test_composed_constructor_failure_reaches_existing_capture_summary(external, tmp_path):  # noqa: F811
    world = composed_world(external)
    module, worker, previous, unused, error = worker_world(tmp_path, fail=True)
    previous.close()
    mp = native_module(sensor.MP, tmp_path)
    config = inert_child_boundaries(mp, worker)
    attach_native(world, mp)
    attach_native(world, module)
    with pytest.raises(type(error)) as raised:
        mp.WorkerProc(vllm_config=config, local_rank=0, rank=0,
            distributed_init_method='example', input_shm_handle=None, ready_path='example')
    assert raised.value is error
    world.observer.close()
    rows, summary = capture.parse_recording(bytes(world.fake.data), world.prepared)
    fact, = summary['baseline_startup']['local_failure_propagation']
    assert fact['point'] == 'Worker.load_model' and fact['rank'] == 0
    assert fact['component_failure_constructor_failure_observed'] is True
    assert fact['parent_process_aggregate_verified'] is False
    assert fact['confirmed_violation'] is False
    assert summary['baseline_startup']['paired_calls'] == 2
    assert summary['whole_run_coverage_verified'] is False
