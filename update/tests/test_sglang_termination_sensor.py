"""Source-fixture and observer contracts; no engine or CUDA runtime is started."""
from __future__ import annotations

import ast
import asyncio
import dataclasses
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import threading
import time
import types
import weakref
import zipfile

import pytest

from guard.adapters import sglang_request_termination_sensor as sensor

FIXTURE = Path(__file__).parent / 'fixtures/sglang0522-termination-source.zip'
RID = 'source-request-0001'


def source(path):
    with zipfile.ZipFile(FIXTURE) as archive:
        return archive.read(path)


def source_function(path, cls, method, namespace):
    tree = ast.parse(source(path))
    parent = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == cls) if cls else tree
    node = next(node for node in parent.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method)
    node.decorator_list = []
    fragment = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), node], type_ignores=[])
    ast.fix_missing_locations(fragment)
    exec(compile(fragment, path, 'exec'), namespace)
    return namespace[method]


@pytest.fixture
def observed(monkeypatch):
    path = Path(sensor.__file__).with_name('vllm_lifecycle_sensor.py')
    spec = importlib.util.spec_from_file_location('_sglang_test_base8', path)
    base = importlib.util.module_from_spec(spec); spec.loader.exec_module(base)
    sensor.configure(base)
    observer = base.Observer({}, 'a' * 64)
    events = []
    observer.emit = lambda event, payload: events.append((event, payload))
    observer.modules = {name: {'source_sha256': sha, 'code_sha256': 'c' * 64}
                        for name, sha in sensor.SOURCE_SHA.items()}
    base._OBSERVER = observer
    monkeypatch.setattr(sensor, '_BASE', base)
    return observer, events


def test_source_archive_hashes_and_hook_symbols_match_fixed_candidates():
    for module, expected in sensor.SOURCE_SHA.items():
        path = module.removeprefix('sglang.srt.').replace('.', '/') + '.py'
        raw = source(path)
        assert hashlib.sha256(raw).hexdigest() == expected
        tree = ast.parse(raw)
        for cls, methods in sensor.POINTS.get(module, {}).items():
            node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
            names = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert set(methods) <= names
    assert sensor.COMMIT == '5fc09edb6a0bb1d5b6992b543723c66d08f86d7a507e951e87e0d05db4ac766e'


def test_real_init_state_binds_only_hashed_asgi_request(observed):
    observer, events = observed
    class Stats:
        def __init__(self, **_kwargs): pass
        def set_created_time(self, value): self.created = value
    def state(*args): return types.SimpleNamespace(obj=args[3])
    original = source_function('managers/tokenizer_manager.py', 'TokenizerManager', '_init_req_state',
        {'asyncio': asyncio, 'APIServerReqTimeStats': Stats, 'ReqState': state})
    wrapped = observer.wrap('sglang.srt.managers.tokenizer_manager', 'TokenizerManager._init_req_state', original)
    manager = types.SimpleNamespace(enable_trace=False, disaggregation_mode=None, rid_to_state={})
    obj = types.SimpleNamespace(rid=RID, received_time=1, is_single=True, bootstrap_room=None,
                                text='PRIVATE_PROMPT_NEVER_COPY')
    async def app(scope, receive, send):
        wrapped(manager, obj)
        await receive()
        await send({'type': 'http.response.body', 'body': b'ok'})
    sent = []
    async def receive(): return {'type': 'http.disconnect'}
    async def send(value): sent.append(value)
    asyncio.run(sensor.Middleware(app)({'type': 'http', 'method': 'POST', 'path': '/v1/chat/completions',
        'headers': [(b'x-request-id', b'server-owned-id')]}, receive, send))
    selection = next(v for e, v in events if e == 'SG_NATIVE' and v['phase'] == 'RETURN')
    assert selection['asgi_request_sha256'] == hashlib.sha256(b'server-owned-id').hexdigest()
    assert selection['requests'][0]['request_sha256'] == sensor.hash_id(RID)
    assert selection['requests'][0]['state_present'] is True
    assert sent == [{'type': 'http.response.body', 'body': b'ok'}]
    assert any(e == 'SG_ASGI' and v['phase'] == 'DISCONNECT_RECEIVED' for e, v in events)
    assert 'PRIVATE_PROMPT_NEVER_COPY' not in json.dumps(events) and RID not in json.dumps(events)
    assert sensor._REQUEST.get() is None


def test_real_request_pool_release_and_batch_filter_observe_actual_state_changes(observed):
    observer, events = observed
    free = source_function('mem_cache/memory_pool.py', 'ReqToTokenPool', 'free', {})
    pool = types.SimpleNamespace(freed=[])
    pool.free_rows = lambda values: pool.freed.extend(values)
    class KV:
        def __init__(self):
            self.req_pool_idx = 7; self.kv_allocated_len = 4; self.mamba_pool_idx = None
        @property
        def holds_kv(self): return self.req_pool_idx is not None
    req = types.SimpleNamespace(rid=RID, kv=KV())
    wrapped = observer.wrap('sglang.srt.mem_cache.common', 'common.release_kv_cache',
                            lambda value, _cache: free(pool, value))
    wrapped(req, None)
    assert pool.freed == [7] and req.kv.req_pool_idx is None
    rows = [v for e, v in events if e == 'SG_NATIVE']
    assert rows[-1]['requests'][0]['kv_registered'] is False
    assert rows[-1]['requests'][0]['kv_allocated'] == 4  # Pool row alone is not full KV release.
    class Req:
        def __init__(self): self.rid = RID
        def finished(self): return True
    batch = types.SimpleNamespace(reqs=[Req()])
    original = source_function('managers/schedule_batch.py', 'ScheduleBatch', 'filter_batch',
                               {'Req': Req, 'strip_beam_tail': lambda _batch: None,
                                'CaptureHiddenMode': types.SimpleNamespace(NULL=None)})
    observer.wrap('sglang.srt.managers.schedule_batch', 'ScheduleBatch.filter_batch', original)(batch)
    assert batch.reqs == []
    assert events[-1][1]['requests'][0]['state_present'] is False


def test_observer_error_preserves_return_and_original_exception(observed, monkeypatch):
    observer, events = observed
    def observe(*_args): raise RuntimeError('observer-unavailable')
    monkeypatch.setattr(observer, 'observe', observe)
    marker = object()
    good = observer.wrap('sglang.srt.managers.tokenizer_manager', 'TokenizerManager.abort_request', lambda *_args: marker)
    assert good(object(), RID) is marker
    failure = ValueError('PRIVATE_NATIVE_FAILURE')
    def native(*_args): raise failure
    bad = observer.wrap('sglang.srt.managers.tokenizer_manager', 'TokenizerManager.abort_request', native)
    with pytest.raises(ValueError) as error: bad(object(), RID)
    assert error.value is failure
    assert 'PRIVATE_NATIVE_FAILURE' not in json.dumps(events)


def test_live_qualified_configuration_uses_scalar_snapshots_only(observed, monkeypatch):
    observer, _ = observed
    class Parallel:
        __slots__ = ('_config', '_overrides', '_stamp')
    class Context:
        __slots__ = ('_config_bags',)
    parallel = Parallel(); parallel._overrides = {}; parallel._stamp = {}
    parallel._config = types.SimpleNamespace(_fields={'tp_size': 1, 'pp_size': 1, 'dp_size': 1})
    context = Context(); context._config_bags = {'spec': types.SimpleNamespace(speculative_algorithm=None)}
    module = types.ModuleType('sglang.srt.runtime_context'); module._PARALLEL = parallel; module._CONTEXT = context
    monkeypatch.setitem(sys.modules, module.__name__, module)
    owner = types.SimpleNamespace(enable_overlap=False, enable_hisparse=False, dllm_config=None,
        enable_continuous_input_polling=False, disaggregation_mode='null', device='cuda',
        _deferred_input_requests=[], grammar_manager=types.SimpleNamespace(grammar_queue=[]))
    assert sensor.qualified(owner, observer.modules) is True
    parallel._overrides['tp_size'] = 2
    assert sensor.qualified(owner, observer.modules) is False
    parallel._overrides.clear(); owner.enable_overlap = True
    assert sensor.qualified(owner, observer.modules) is False


def test_missing_queue_shape_never_proves_absence():
    assert sensor.scheduler_presence(types.SimpleNamespace(waiting_queue=[]), RID) is None
    owner = types.SimpleNamespace(waiting_queue=[], running_batch=types.SimpleNamespace(reqs=[]),
        cur_batch_for_debug=None, last_batch=None, chunked_req=None, enable_continuous_input_polling=False,
        _deferred_input_requests=[], grammar_manager=types.SimpleNamespace(grammar_queue=[]))
    assert sensor.scheduler_presence(owner, RID) is False
    owner.running_batch.reqs.append(types.SimpleNamespace(rid=RID))
    assert sensor.scheduler_presence(owner, RID) is True
    owner.enable_continuous_input_polling = True
    assert sensor.scheduler_presence(owner, RID) is None


@pytest.mark.parametrize('queue', ['grammar', 'deferred'])
def test_other_scheduler_queues_cannot_be_ignored(queue):
    owner = types.SimpleNamespace(waiting_queue=[], running_batch=None, cur_batch_for_debug=None,
        last_batch=None, chunked_req=None, enable_continuous_input_polling=False,
        _deferred_input_requests=[], grammar_manager=types.SimpleNamespace(grammar_queue=[]))
    target = owner.grammar_manager.grammar_queue if queue == 'grammar' else owner._deferred_input_requests
    target.append(types.SimpleNamespace(rid=RID))
    assert sensor.scheduler_presence(owner, RID) is True
    target[:] = [types.SimpleNamespace(rid='other')]
    assert sensor.scheduler_presence(owner, RID) is None


def test_gpu_limit_includes_the_pollers_current_batch_and_factory_reservations(observed):
    observer, events = observed
    entered, released = threading.Event(), threading.Event()
    handles = []
    class Event:
        def __init__(self, **_kwargs): handles.append(self)
        def record(self, **_kwargs): pass
        def query(self):
            if self is handles[0]:
                entered.set()
                assert released.wait(2)
            return False
    cuda = types.ModuleType('torch.cuda'); cuda.Event = Event; cuda.current_stream = lambda: object()
    owner = types.SimpleNamespace(device_module=cuda, forward_stream=object(), copy_stream=object(), schedule_stream=object())
    observer.fences(owner, (RID,), 1)
    assert entered.wait(2)
    try:
        for operation in range(2, 18): observer.fences(owner, (RID,), operation)
        assert len(handles) == sensor.MAX_GPU_EVENTS
        assert len(observer.gpu_pending) + observer.gpu_active + observer.gpu_reserved == sensor.MAX_GPU_EVENTS
        assert any(event == 'SG_GAP' for event, _value in events)
    finally:
        observer.sg_pid = -1
        released.set()
        if observer.gpu_thread is not None: observer.gpu_thread.join(2)


def test_fences_query_without_device_synchronization_and_release_handles(observed):
    observer, events = observed
    streams = {name: object() for name in ('forward_stream', 'copy_stream', 'schedule_stream')}
    class Event:
        def __init__(self, **kwargs): assert kwargs == {'enable_timing': False}
        def record(self, *, stream): assert stream in streams.values()
        def query(self): return True
        def synchronize(self): raise AssertionError('device wait is forbidden')
    cuda = types.ModuleType('torch.cuda'); cuda.Event = Event
    cuda.current_stream = lambda: streams['schedule_stream']
    owner = types.SimpleNamespace(device_module=cuda, **streams)
    observer.fences(owner, (RID,), 7)
    thread = observer.gpu_thread
    if thread is not None: thread.join(timeout=2)
    receipts = [v for e, v in events if e == 'SG_GPU_FENCE']
    assert {r['stream'] for r in receipts} == {'forward_stream', 'copy_stream', 'schedule_stream', 'return_stream'}
    assert all(r['completed'] is True and r['operation'] == 7 for r in receipts)
    assert observer.gpu_pending == [] and observer.gpu_thread is None
    assert RID not in json.dumps(events)


@pytest.mark.parametrize('failure', ['missing-stream', 'record-raises'])
def test_partial_factory_releases_handles_and_traceback_before_reservation(observed, failure):
    observer, _ = observed
    gap, release_gap = threading.Event(), threading.Event()
    query, release_query = threading.Event(), threading.Event()
    live = weakref.WeakSet()
    class Event:
        def __init__(self, **_kwargs): live.add(self)
        def record(self, *, stream):
            if failure == 'record-raises' and threading.current_thread().name == 'bad-partial':
                raise RuntimeError('record failed')
        def query(self):
            query.set()
            assert release_query.wait(2)
            return False
    cuda = types.ModuleType('torch.cuda'); cuda.Event = Event
    cuda.current_stream = lambda: (None if failure == 'missing-stream'
        and threading.current_thread().name == 'bad-partial' else object())
    owner = types.SimpleNamespace(device_module=cuda, forward_stream=object(),
        copy_stream=object(), schedule_stream=object())
    original = observer.emit
    def emit(event, payload):
        if event == 'SG_GAP' and threading.current_thread().name == 'bad-partial':
            gap.set()
            assert release_gap.wait(2)
        original(event, payload)
    observer.emit = emit
    bad = threading.Thread(target=lambda: observer.fences(owner, (RID,), 1), name='bad-partial')
    bad.start()
    assert gap.wait(2)
    try:
        assert len(live) == 0
        assert observer.gpu_reserved == 0
        for operation in range(2, 18): observer.fences(owner, (RID,), operation)
        assert query.wait(2)
        assert len(live) <= sensor.MAX_GPU_EVENTS
    finally:
        observer.sg_pid = -1
        release_query.set(); release_gap.set(); bad.join(2)
        if observer.gpu_thread is not None: observer.gpu_thread.join(2)
        observer.gpu_pending.clear()


def test_immutable_base_asset_rejects_symlink_and_change(tmp_path):
    original = Path(sensor.__file__).with_name('vllm_lifecycle_sensor.py')
    link = tmp_path / 'base.py'; link.symlink_to(original)
    with pytest.raises((ValueError, OSError)): sensor.artifact(link, sensor.BASE8_SHA256)
    path = tmp_path / 'changed.py'; path.write_bytes(original.read_bytes() + b'\n')
    with pytest.raises(ValueError): sensor.artifact(path, sensor.BASE8_SHA256)


def test_pinned_ipc_slots_bind_frontend_output_and_scheduler_abort(observed, monkeypatch):
    observer, events = observed
    module = types.ModuleType('sglang.srt.managers.io_struct')
    class BaseBatchReq:
        __slots__ = ('rids',)
    class BatchStrOutput(BaseBatchReq):
        __slots__ = ()
    class BaseReq:
        __slots__ = ('rid',)
    class AbortReq(BaseReq):
        __slots__ = ()
    for cls in (BatchStrOutput, AbortReq):
        cls.__module__ = module.__name__; setattr(module, cls.__name__, cls)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    batch=BatchStrOutput();batch.rids=[RID]
    abort=AbortReq();abort.rid=RID
    assert sensor.fields(batch)=={}  # Exact actual msgspec storage mismatch.
    assert sensor.selected_ids('TokenizerManager._handle_batch_output',(None,batch),{},observer.modules)==(RID,)
    assert sensor.selected_ids('Scheduler.abort_request',(None,abort),{},observer.modules)==(RID,)
    manager=types.SimpleNamespace(rid_to_state={RID:object()})
    async def original(self,recv):
        self.rid_to_state.pop(recv.rids[0])
    wrapped=observer.wrap(module.__name__,'TokenizerManager._handle_batch_output',original)
    asyncio.run(wrapped(manager,batch))
    rows=[p for e,p in events if e=='SG_NATIVE']
    assert [p['requests'][0]['state_present']for p in rows]==[True,False]
    assert all(p['requests'][0]['request_sha256']==sensor.hash_id(RID)for p in rows)
    assert sensor.selected_ids('Scheduler.abort_request',(None,abort),{},{} )==()
    module.AbortReq=object
    assert sensor.selected_ids('Scheduler.abort_request',(None,abort),{},observer.modules)==()


def test_ipc_identity_reader_never_calls_properties(observed, monkeypatch):
    observer,_=observed
    module=types.ModuleType('sglang.srt.managers.io_struct')
    class BatchStrOutput:
        __slots__=()
        @property
        def rids(self):raise AssertionError('property executed')
    BatchStrOutput.__module__=module.__name__;module.BatchStrOutput=BatchStrOutput
    monkeypatch.setitem(sys.modules,module.__name__,module)
    assert sensor.selected_ids('TokenizerManager._handle_batch_output',(None,BatchStrOutput()),{},observer.modules)==()


def test_actual_req_kv_info_slots_observe_retained_then_released(observed,monkeypatch):
    observer,events=observed
    tree=ast.parse(source('managers/schedule_batch.py'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef)and n.name=='ReqKvInfo')
    # Compile the exact pinned dataclass fields/methods with deferred annotations;
    # only its default dict factory needs a runtime definition, no torch imported.
    module=types.ModuleType('sglang.srt.managers.schedule_batch')
    module.dataclasses=dataclasses
    monkeypatch.setitem(sys.modules,module.__name__,module)
    fragment=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),node],type_ignores=[])
    ast.fix_missing_locations(fragment);exec(compile(fragment,'actual_schedule_batch.py','exec'),vars(module))
    kv=module.ReqKvInfo(req_pool_idx=3,kv_allocated_len=16,mamba_pool_idx=None)
    assert sensor.fields(kv)=={}
    owner=types.SimpleNamespace(rid=RID,kv=kv)
    def original(req,cache,*,checkpoint):
        req.kv.req_pool_idx=None
        req.kv.mark_kv_released()
    wrapped=observer.wrap('sglang.srt.mem_cache.common','common.release_kv_cache',original)
    wrapped(owner,None,checkpoint=False)
    states=[p['requests'][0]for event,p in events if event=='SG_NATIVE']
    assert [(r['kv_registered'],r['kv_allocated'],r['mamba_present'])for r in states]==[(True,16,False),(False,0,False)]
    assert sensor.kv_fields(kv,{})=={}
    # Missing/uninitialized storage stays unknown rather than becoming zero.
    del kv.kv_allocated_len
    row=sensor.snapshot('common.release_kv_cache',(owner,None),(RID,),observer.modules)[0]
    assert row['kv_allocated']is None and row['kv_registered']is None


def test_kv_slot_reader_rejects_properties_and_same_named_impostors(observed,monkeypatch):
    observer,_=observed
    module=types.ModuleType('sglang.srt.managers.schedule_batch')
    class ReqKvInfo:
        __slots__=()
        @property
        def req_pool_idx(self):raise AssertionError('property executed')
    ReqKvInfo.__module__=module.__name__;module.ReqKvInfo=ReqKvInfo
    monkeypatch.setitem(sys.modules,module.__name__,module)
    assert sensor.kv_fields(ReqKvInfo(),observer.modules)=={}
    assert sensor.kv_fields(types.SimpleNamespace(kv_allocated_len=0),observer.modules)=={}
