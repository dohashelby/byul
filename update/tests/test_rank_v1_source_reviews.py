"""현재 V1의 실제 태그 함수 본문·오류 경계를 대조한다. 엔진은 import하지 않는다."""
from copy import deepcopy
from contextlib import contextmanager
from enum import Enum
from hashlib import sha256
import json
import pickle
from pathlib import Path
import sys
import time
import types

import pytest

from guard.adapters import vllm_rank_ipc_sources as sources
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = sys.implementation.name + '-' + '.'.join(map(str, sys.version_info[:3]))


def reviewed(side):
    image = versions()[side]['image']
    rows = json.loads(sources.V1_REVIEWS.read_bytes())['profiles']
    row, = [r for r in rows if r['image'] == image and r['runtime'] == RUNTIME]
    return row


def module_body(tmp_path, side, name):
    """컴파일한 원 함수 code만 시험 namespace에 배치한다. import/장식자 실행은 없다."""
    row = reviewed(side)
    spec = row['sources'][name]
    fixture = ROOT / 'tests/fixtures' / ('vllm-' + versions()[side]['version']) / (spec['path'] + '.txt')
    raw = fixture.read_bytes()
    path = tmp_path / (side + '-' + name + '.py')
    path.write_bytes(raw)
    module = types.ModuleType(name)
    module.__file__ = str(path)
    codes = sources._codes(compile(raw, str(path), 'exec', dont_inherit=True, optimize=0))
    for point in spec['functions']:
        cls_name, method = point.split('.')
        if cls_name not in vars(module):
            vars(module)[cls_name] = type(cls_name, (), {'__module__': name})
        assert all(name == '__class__' for name in codes[point].co_freevars)
        closure = tuple(types.CellType(vars(module)[cls_name]) for name in codes[point].co_freevars) or None
        fn = types.FunctionType(codes[point], vars(module), method, closure=closure)
        if codes[point].co_varnames[:1] != ('self',):
            fn = staticmethod(fn)
        setattr(vars(module)[cls_name], method, fn)
    return module, row, spec


@pytest.mark.parametrize('side', ['baseline', 'candidate'])
def test_exact_current_source_body_review_never_installs_or_registers(tmp_path, side):
    row = reviewed(side)
    for name in row['sources']:
        module, _, spec = module_body(tmp_path, side, name)
        originals = {p: sources._function(module, p) for p in spec['functions']}
        result = sources.qualify_current_v1(module, image=row['image'])
        assert result['profile'] == row['id']
        assert result['source_sha256'] == spec['sha256']
        assert result['functions'] == originals
        assert result['body_code_sha256'] == spec['body_code_sha256']
        assert result['engine_group_verified'] is result['observer_installed'] is result['producer_registered'] is False
        assert result['pin_status'] == 'candidate_pin'
        assert result['review_scope'] == 'offline_source_body_only' and result['image_runtime_verified'] is False
        assert {p: sources._function(module, p) for p in spec['functions']} == originals


def test_installed_source_rows_keep_actual_tag_bytes_and_runtime_body_pins():
    rows = json.loads(sources.V1_REVIEWS.read_bytes())['profiles']
    assert len(rows) == 4
    assert {(r['image'], r['runtime']) for r in rows} == {
        (v['image'], runtime) for v in versions().values()
        for runtime in ('cpython-3.12.3', 'cpython-3.12.8')}
    for side in ('baseline', 'candidate'):
        fixture = ROOT / 'tests/fixtures' / ('vllm-' + versions()[side]['version'])
        provenance = json.loads((fixture / 'PROVENANCE.json').read_bytes())
        for spec in reviewed(side)['sources'].values():
            raw = (fixture / (spec['path'] + '.txt')).read_bytes()
            assert len(raw) == spec['bytes']
            assert sha256(raw).hexdigest() == spec['sha256'] == provenance['files'][spec['path'] + '.txt']['sha256']
            assert set(spec['body_code_sha256']) == set(spec['functions']) == set(spec['function_lines'])
    initial = reviewed('baseline')['sources']['vllm.v1.executor.multiproc_executor']['functions']
    later = reviewed('candidate')['sources']['vllm.v1.executor.multiproc_executor']['functions']
    assert 'WorkerProc._execute_worker_rpc' not in initial
    assert 'WorkerProc._execute_worker_rpc' in later


@pytest.mark.parametrize('damage', ['bytes', 'body', 'namespace', 'property', 'symlink', 'image',
                                   'runtime', 'pin', 'duplicate', 'mode', 'unknown_module'])
def test_current_v1_exact_review_rejects_drift_without_opening_legacy_profiles(tmp_path, monkeypatch, damage):
    module, row, spec = module_body(tmp_path, 'baseline', 'vllm.v1.executor.multiproc_executor')
    image = row['image']
    if damage == 'bytes':
        Path(module.__file__).write_bytes(Path(module.__file__).read_bytes() + b'\n')
    elif damage == 'body':
        module.WorkerProc.worker_busy_loop = lambda self: None
    elif damage == 'namespace':
        original = module.WorkerProc.worker_busy_loop
        module.WorkerProc.worker_busy_loop = types.FunctionType(original.__code__, {})
    elif damage == 'property':
        def forbidden(_):
            raise AssertionError('property를 실행했다')
        module.WorkerProc.worker_busy_loop = property(forbidden)
    elif damage == 'symlink':
        alias = tmp_path / 'alias.py'
        alias.symlink_to(module.__file__)
        module.__file__ = str(alias)
    elif damage == 'image':
        image = 'example.invalid/other@sha256:' + '0' * 64
    elif damage == 'unknown_module':
        module.__name__ = 'unknown.module'
    else:
        data = deepcopy(json.loads(sources.V1_REVIEWS.read_bytes()))
        chosen, = [r for r in data['profiles'] if r['image'] == image and r['runtime'] == RUNTIME]
        if damage == 'runtime':
            chosen['runtime'] = 'cpython-unreviewed'
        elif damage == 'pin':
            chosen['sources'][module.__name__]['body_code_sha256']['WorkerProc.worker_busy_loop'] = '0' * 64
        elif damage == 'duplicate':
            data['profiles'].append(deepcopy(chosen))
        else:
            chosen['engine_mode'] = 'V0'
        path = tmp_path / 'reviews.json'
        path.write_text(json.dumps(data))
        monkeypatch.setattr(sources, 'V1_REVIEWS', path)
    with pytest.raises(ValueError, match='RANK_V1_SOURCE_UNAVAILABLE'):
        sources.qualify_current_v1(module, image=image)


class EndLoop(BaseException):
    """무한 원 루프를 시험에서만 끝내는 대역. 원 Exception 경계를 바꾸지 않는다."""


class Queue:
    def __init__(self, *items):
        self.items = iter(items)
        self.sent = []

    def dequeue(self, **kwargs):
        try:
            item = next(self.items)
        except StopIteration:
            raise EndLoop from None
        if isinstance(item, BaseException):
            raise item
        return item

    def enqueue(self, item):
        self.sent.append(item)


def runtime_module(tmp_path):
    module, row, _ = module_body(tmp_path, 'baseline', 'vllm.v1.executor.multiproc_executor')
    # 협력 객체는 EXAMPLE이다. 원 메서드 code/except/return은 바꾸지 않는다.
    module.WorkerProc.ResponseStatus = Enum('ResponseStatus', ['SUCCESS', 'FAILURE'])
    module.time = time
    module.logger = types.SimpleNamespace(exception=lambda *a, **k: None)
    assert not sources.qualify_current_v1(module, image=row['image'])['observer_installed']
    return module


def test_initial_worker_loop_sends_original_result_and_caught_exception_without_propagating_it(tmp_path):
    module = runtime_module(tmp_path)
    worker = object.__new__(module.WorkerProc)  # 루프 시험의 EXAMPLE 상태. __init__은 별도 시험한다.
    result, failure, arg = object(), ValueError('EXAMPLE 원 오류'), object()
    observed = []

    def execute(value):
        observed.append(value)
        if len(observed) == 2:
            raise failure
        return result

    worker.worker = types.SimpleNamespace(execute_model=execute)
    worker.rpc_broadcast_mq = Queue(('execute_model', (arg,), {}), ('execute_model', (arg,), {}))
    worker.worker_response_mq = Queue()
    with pytest.raises(EndLoop):
        worker.worker_busy_loop()
    assert observed == [arg, arg]
    status = module.WorkerProc.ResponseStatus
    assert worker.worker_response_mq.sent == [(status.SUCCESS, result), (status.FAILURE, failure)]


@pytest.mark.parametrize('failure_rank', [None, 0, 1])
def test_initial_parent_waits_for_all_rank_responses_and_preserves_exact_worker_exception(tmp_path, failure_rank):
    module = runtime_module(tmp_path)
    status = module.WorkerProc.ResponseStatus
    parent = module.MultiprocExecutor()
    parent.world_size, parent.rpc_broadcast_mq = 2, Queue()
    request, first, second = object(), object(), object()
    failure = ValueError('EXAMPLE worker 오류')
    parent.workers = [types.SimpleNamespace(rank=rank, worker_response_mq=Queue(
        (status.FAILURE, failure) if rank == failure_rank else (status.SUCCESS, item)))
        for rank, item in enumerate((first, second))]
    if failure_rank is None:
        assert parent.collective_rpc('execute_model', timeout=None, args=(request,), kwargs=None) == [first, second]
    else:
        with pytest.raises(ValueError) as caught:
            parent.collective_rpc('execute_model', timeout=None, args=(request,), kwargs=None)
        assert caught.value is failure
    assert parent.rpc_broadcast_mq.sent == [('execute_model', (request,), {})]


def test_initial_parent_timeout_is_rpc_timeout_with_original_cause_not_worker_detection(tmp_path):
    module = runtime_module(tmp_path)
    parent = module.MultiprocExecutor()
    failure = TimeoutError('EXAMPLE response queue timeout')
    parent.world_size, parent.rpc_broadcast_mq = 2, Queue()
    parent.workers = [types.SimpleNamespace(rank=0, worker_response_mq=Queue(failure))]
    with pytest.raises(TimeoutError, match='RPC call to execute_model timed out') as caught:
        parent.collective_rpc('execute_model', timeout=1, args=(object(),), kwargs=None)
    assert caught.value is not failure and caught.value.__cause__ is failure


def test_initial_abstract_execute_model_returns_rank_zero_only_after_collective_returns(tmp_path):
    module, row, _ = module_body(tmp_path, 'baseline', 'vllm.v1.executor.abstract')
    assert not sources.qualify_current_v1(module, image=row['image'])['observer_installed']
    executor = module.Executor()
    request, first, second, calls = object(), object(), object(), []

    def collective(method, args):
        calls.append((method, args))
        return [first, second]

    executor.collective_rpc = collective
    assert executor.execute_model(request) is first
    assert calls == [('execute_model', (request,))]


def test_initial_ready_is_sent_before_device_initialization_and_model_load(tmp_path):
    module = runtime_module(tmp_path)
    events = []
    native_worker = types.SimpleNamespace(rank=1, init_device=lambda: events.append('init-device'),
                                         load_model=lambda: events.append('load-model'))

    class Wrapper:
        def __init__(self, **kwargs):
            self.worker = native_worker

        def init_worker(self, kwargs):
            assert kwargs[1]['rank'] == 1 and len(kwargs) == 2

    class MessageQueue:
        def __init__(self, *args):
            pass

        def export_handle(self):
            return b'EXAMPLE-response-handle'

        @staticmethod
        def create_from_handle(handle, rank):
            assert handle == b'EXAMPLE-input-handle' and rank == 1
            return MessageQueue()

    @contextmanager
    def socket(path, kind):
        assert path == 'EXAMPLE-ready-path' and kind == 'EXAMPLE-PUSH'
        yield types.SimpleNamespace(send_string=lambda value: events.append(('ready', value)),
                                    send=lambda value: events.append(('handle', pickle.loads(value))))

    module.WorkerWrapperBase, module.MessageQueue = Wrapper, MessageQueue
    module.zmq_socket_ctx, module.pickle = socket, pickle
    module.zmq = types.SimpleNamespace(constants=types.SimpleNamespace(PUSH='EXAMPLE-PUSH'))
    module.os, module.sys = types.SimpleNamespace(getpid=lambda: 123), types.SimpleNamespace(stdout=None, stderr=None)
    module._add_prefix = lambda *args: None
    module.WorkerProc.READY_STR = 'READY'
    config = types.SimpleNamespace(parallel_config=types.SimpleNamespace(world_size=2))
    worker = module.WorkerProc(config, 1, 1, 'EXAMPLE-init', b'EXAMPLE-input-handle', 'EXAMPLE-ready-path')
    assert worker.worker is native_worker
    assert events == [('ready', 'READY'), ('handle', b'EXAMPLE-response-handle'), 'init-device', 'load-model']


def test_new_source_review_does_not_change_existing_active_profile_selection():
    legacy = json.loads(sources.PROFILES.read_bytes())
    assert {p['id'] for p in legacy['profiles']} == {'v0-task-queue', 'v1-shm-broadcast'}
    assert len(legacy['profiles']) == 2
    for row in json.loads(sources.V1_REVIEWS.read_bytes())['profiles']:
        assert not row['observer_installed'] and not row['engine_group_verified'] and not row['producer_registered']
        assert all('hooks' not in spec for spec in row['sources'].values())


def test_current_candidate_super_class_cell_must_match_exact_selected_owner(tmp_path):
    name='vllm.v1.executor.multiproc_executor'
    module,row,spec=module_body(tmp_path,'candidate',name)
    point='FutureWrapper.__init__'
    assert point in spec['functions']
    original=sources._function(module,point)
    assert original.__code__.co_freevars==('__class__',)
    assert original.__closure__[0].cell_contents is module.FutureWrapper
    foreign=type('FutureWrapper',(),{'__module__':name})
    replacement=types.FunctionType(original.__code__,vars(module),original.__name__,
        closure=(types.CellType(foreign),))
    module.FutureWrapper.__init__=replacement
    with pytest.raises(ValueError,match='RANK_V1_SOURCE_UNAVAILABLE'):
        sources.qualify_current_v1(module,image=row['image'])
