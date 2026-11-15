"""Exact-source baseline startup candidates; engine/GPU never imported or started."""
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from guard.adapters import vllm_baseline_startup as sensor
from guard.adapters import vllm_worker_readiness as callback
from guard.collectors import baseline_startup as consumer

FIXTURE = Path(__file__).parent / 'fixtures/vllm-0.7.0'
BINDING = {'pid': os.getpid(), 'session_id_sha256': 'a' * 64, 'process_start_sha256': 'b' * 64}


def native_module(name, tmp_path):
    """Compile exact function nodes, without native imports or startup side effects.

    Annotation evaluation and unrelated class bases are omitted outside function
    bodies; source qualification still compares every function's complete code.
    """
    row = sensor.ROWS[name]
    raw = (FIXTURE / (row['path'] + '.txt')).read_bytes()
    path = tmp_path / (name + '.py')
    path.write_bytes(raw)
    parsed = ast.parse(raw)
    classes = []
    for cls in parsed.body:
        if not isinstance(cls, ast.ClassDef):
            continue
        methods = [fn for fn in cls.body if isinstance(fn, ast.FunctionDef)
                   and cls.name + '.' + fn.name in row['functions']]
        if not methods:
            continue
        for fn in methods:
            fn.returns = None
            for arg in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs):
                arg.annotation = None
            if fn.args.vararg:
                fn.args.vararg.annotation = None
            if fn.args.kwarg:
                fn.args.kwarg.annotation = None
        cls.bases, cls.keywords, cls.decorator_list, cls.body = [], [], [], methods
        classes.append(cls)
    module = ModuleType(name)
    module.__file__ = str(path)
    exec(compile(ast.Module(body=classes, type_ignores=[]), str(path), 'exec', dont_inherit=True, optimize=0), vars(module))
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


@pytest.mark.parametrize('fail', [False, True])
def test_actual_child_constructor_load_failure_correspondence_and_ready_precedes_load(tmp_path, fail):
    """Execute pinned constructor + load body with inert native I/O boundaries."""
    import pickle
    import sys
    from contextlib import contextmanager
    module, worker, observer, records, error = worker_world(tmp_path, fail=fail)
    mp = native_module(sensor.MP, tmp_path)
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
    observer.install(mp, image=sensor.IMAGE)
    # Every actual load invocation must occur after the native constructor sent
    # transport READY; this is not a model-readiness observation.
    load = worker.model_runner.load_model
    def checked_load():
        assert sent == ['READY']
        return load()
    worker.model_runner.load_model = checked_load
    config = SimpleNamespace(parallel_config=worker.parallel_config)
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
