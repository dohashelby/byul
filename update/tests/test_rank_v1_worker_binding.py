"""원 태그 loop/queue 본문과 EXAMPLE 협력 객체의 /28→/2 연결. 엔진/GPU 실행 아님."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
import enum
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

from guard.adapters import vllm_rank_v1_worker_binding as binding
from guard.adapters import vllm_rank_v1_worker_runtime as runtime
from guard.adapters import vllm_rank_v1_worker_capture as capture
from guard.adapters.vllm_rank_ipc_capture import canonical
from tools import rank_v1_worker_inputs as inputs
from tools.mvp_versions import versions
from tests.test_rank_v1_source_reviews import module_body, EndLoop, reviewed
from tests.test_fresh_request_binding import make_world, program, run_request, recording, SECRET
from tests.test_lifecycle_capture import external  # noqa: F401
from tests.test_lifecycle_sensor import CONTEXT


class Opaque:
    def __str__(self):
        raise AssertionError('출력 원문을 읽었다')

    __repr__ = __str__


@pytest.fixture
def world(external, monkeypatch):  # noqa: F811
    fresh = make_world(external)
    prepared = external / 'worker'
    manifest = inputs.prepare(prepared, image=versions()['baseline']['image'], fresh_prepared=fresh.folder)
    settings = json.loads((prepared / 'settings.json').read_bytes())
    key = bytes.fromhex(settings['key'])
    modules = {}
    for name in reviewed('baseline')['sources']:
        module, _, _ = module_body(external, 'baseline', name)
        modules[name] = module
        monkeypatch.setitem(__import__('sys').modules, name, module)
    loop = modules['vllm.v1.executor.multiproc_executor']
    queues = modules['vllm.distributed.device_communicators.shm_broadcast']
    scheduler = modules['vllm.v1.core.scheduler']
    # EXAMPLE module/type/IPC 협력 객체. 원 함수 code/except/return은 바꾸지 않는다.
    scheduler.SchedulerOutput = type('SchedulerOutput', (), {'__module__': scheduler.__name__})
    loop.WorkerProc.ResponseStatus = enum.Enum('ResponseStatus', ['SUCCESS', 'FAILURE'],
                                               module=loop.__name__, qualname='WorkerProc.ResponseStatus')
    loop.logger = types.SimpleNamespace(exception=lambda *a, **k: None)
    monkeypatch.setattr(binding.ipc.base, '_context', lambda: dict(CONTEXT))
    written = []
    codec = types.SimpleNamespace(HIGHEST_PROTOCOL=5)
    codec.dumps = lambda obj, protocol: written.append(obj) or b'EXAMPLE-opaque-wire'
    queues.pickle = codec
    queues.MessageQueue.enqueue.__defaults__ = (None,)
    queues.MessageQueue.dequeue.__defaults__ = (None,)

    @contextmanager
    def acquire_read(timeout):
        yield bytearray(b'\0EXAMPLE-opaque-wire')

    @contextmanager
    def acquire_write(timeout):
        yield bytearray(4096)

    inbound, outbound = object.__new__(queues.MessageQueue), object.__new__(queues.MessageQueue)
    inbound._is_local_reader = True; inbound.acquire_read = acquire_read
    outbound._is_writer = True; outbound.n_local_reader = 1; outbound.n_remote_reader = 0
    outbound.buffer = types.SimpleNamespace(max_chunk_bytes=4096); outbound.acquire_write = acquire_write
    worker = object.__new__(loop.WorkerProc); worker.rank = 1
    worker.rpc_broadcast_mq, worker.worker_response_mq = inbound, outbound

    class Model:
        def execute_model(self, value):
            return output

    worker.worker = Model(); output = Opaque()
    saved = []
    writer_settings = settings | {'output_dir': str(prepared / 'data' / settings['session'])}
    writer = runtime.SnapshotWriter(writer_settings, manifest['settings_sha256'])

    def emit(value):
        saved.append(value)
        writer(value)

    # verify_observer는 EXAMPLE source namespace 시험에서만 대역이다.
    # SDK 자체 동결은 아래 별도 시험에서 원 함수로 확인한다.
    observer = binding.WorkerObserver(image=manifest['image'], key=key, session=settings['session'],
        fresh_prepared_sha256=manifest['fresh_prepared_sha256'], binding_key_asset_sha256=manifest['binding_key_asset_sha256'],
        emit=emit, verify_observer=lambda: None)
    for module in modules.values(): observer.attach(module)
    state = program(fresh)
    return types.SimpleNamespace(fresh=fresh, prepared=prepared, manifest=manifest, settings=settings, key=key,
        modules=modules, loop=loop, queues=queues, worker=worker, output=output, written=written,
        codec=codec, saved=saved, observer=observer, state=state, scheduler=scheduler,
        capture=prepared / 'data' / settings['session'])


def execute(world, *, fail=False, mutate=False, idle=False):
    original = world.state.engine.engine_core.add_request_async
    failure = ValueError('EXAMPLE-private-failure-text')
    body_calls = []

    def model(value):
        body_calls.append(value)
        if mutate: value.num_scheduled_tokens = {'EXAMPLE-changed-request': 1}
        if fail: raise failure
        return world.output

    world.worker.worker.execute_model = model

    async def core(req):
        value = world.scheduler.SchedulerOutput()
        value.num_scheduled_tokens = {} if idle else {req.request_id: 1}
        value.total_num_scheduled_tokens = 0 if idle else 1
        queued = iter([('execute_model', (value,), {})])

        def loads(_):
            try: return next(queued)
            except StopIteration: raise EndLoop from None

        world.codec.loads = loads
        try: world.worker.worker_busy_loop()
        except EndLoop: pass
        await original(req)

    world.state.engine.engine_core.add_request_async = core

    async def app(scope, receive, send):
        raw = types.SimpleNamespace(scope=scope, headers={'X-Request-Id': SECRET}, state=types.SimpleNamespace())
        return await world.state.handler.create_completion(world.state.request, raw)

    assert run_request(world.fresh, app) is world.state.result
    (_, _), raw = recording(world.fresh)
    fresh_recording = world.fresh.root / 'fresh.jsonl'; fresh_recording.write_bytes(raw)
    result = capture.verify_capture(world.prepared, world.capture, fresh_prepared=world.fresh.folder,
        fresh_recording=fresh_recording, expected_source_manifest_sha256=world.manifest['source_manifest_sha256'])
    return result, failure, body_calls, fresh_recording


@pytest.mark.parametrize('fail', [False, True])
def test_actual_tag_worker_loop_and_queue_have_one_fresh_link_without_reading_output(world, fail):
    result, failure, calls, _ = execute(world, fail=fail)
    assert len(calls) == 1 and len(world.written) == 1
    assert world.written[0][1] is (failure if fail else world.output)
    assert result['worker_receives'] == result['worker_responses'] == len(result['fresh_worker_links']) == 1
    assert result['gaps'] == [] and result['fresh_worker_links'][0]['response_ref'] is not None
    facts = [r for r in world.saved if r['schema'] == binding.SCHEMA]
    assert [r['event'] for r in facts] == ['worker-rpc-received', 'worker-response-published']
    assert facts[1]['response_status'] == ('failure' if fail else 'success')
    assert facts[0]['request_binding_hmac_sha256'] == facts[1]['request_binding_hmac_sha256']
    assert facts[0]['request_hmac_sha256'] != facts[0]['request_binding_hmac_sha256']
    assert all(result[field] is False for field in binding.FALSE_FIELDS)
    public = canonical(result)
    assert all(secret not in public for secret in (SECRET.encode(), world.key.hex().encode(),
        b'EXAMPLE-private-prompt', b'EXAMPLE-private-output', b'EXAMPLE-private-failure-text'))


def test_changed_input_keeps_original_model_call_and_received_fact_but_withholds_response_link(world):
    result, _, calls, _ = execute(world, mutate=True)
    assert len(calls) == len(world.written) == 1
    assert result['worker_receives'] == 1 and result['worker_responses'] == 0
    assert 'RANK_V1_BINDING_UNAVAILABLE' in result['gaps'] and 'RANK_V1_RESPONSE_UNOBSERVED' in result['gaps']
    assert result['fresh_worker_links'][0]['response_ref'] is None
    assert all(result[field] is False for field in binding.FALSE_FIELDS)


def test_zero_scheduled_work_is_not_fabricated_as_request_bound_work(world):
    result, _, calls, _ = execute(world, idle=True)
    assert len(calls) == len(world.written) == 1
    assert result['worker_receives'] == result['worker_responses'] == len(result['fresh_worker_links']) == 0
    assert 'RANK_V1_RECEIVE_UNOBSERVED' in result['gaps']


def test_source_drift_does_not_change_native_call_or_return(world):
    module = world.modules['vllm.v1.executor.multiproc_executor']
    Path(module.__file__).write_bytes(Path(module.__file__).read_bytes() + b'\n')
    result, _, calls, _ = execute(world)
    assert len(calls) == len(world.written) == 1
    assert result['worker_receives'] == result['worker_responses'] == 0
    assert 'RANK_V1_BINDING_UNAVAILABLE' in result['gaps']


def test_direct_queue_call_without_actual_loop_frame_has_no_binding_fact(world):
    world.codec.loads = lambda _: ('execute_model', (object(),), {})
    result = world.worker.rpc_broadcast_mq.dequeue()
    assert result[0] == 'execute_model' and world.saved == []


@pytest.mark.parametrize('change', ['late', 'clock', 'unmatched-id'])
def test_valid_worker_facts_outside_fresh_scope_are_preserved_as_unbound(world, change):
    _, _, _, fresh_recording = execute(world)
    for path in world.capture.glob('pid-*.json'):
        record = json.loads(path.read_bytes()); snapshot = record['snapshot']; payload = snapshot['payload']
        if change == 'late':
            payload['observation_ns'] += 10**12; payload['operation_started_ns'] += 10**12
        elif change == 'clock':
            snapshot['context']['time_namespace'] += 1; payload['context']['time_namespace'] += 1
        else:
            payload['request_binding_hmac_sha256'] = ['c' * 64]
        record['hmac_sha256'] = hmac.new(world.key, canonical(snapshot), sha256).hexdigest()
        path.write_bytes(canonical(record))
    result = capture.verify_capture(world.prepared, world.capture, fresh_prepared=world.fresh.folder,
        fresh_recording=fresh_recording, expected_source_manifest_sha256=world.manifest['source_manifest_sha256'])
    assert result['worker_receives'] == result['worker_responses'] == 1
    assert result['fresh_worker_links'] == [] and result['gaps']
    assert all(result[field] is False for field in binding.FALSE_FIELDS)


def test_original_dequeue_exception_is_preserved_in_actual_loop(world):
    failure = OSError('EXAMPLE-private-original-error')

    def loads(_): raise failure

    world.codec.loads = loads
    with pytest.raises(OSError) as caught:
        world.worker.worker_busy_loop()
    assert caught.value is failure and world.saved == []


def test_original_response_enqueue_exception_does_not_create_publication(world):
    failure = OSError('EXAMPLE-private-original-error')

    def dumps(*a, **kw): raise failure

    world.codec.dumps = dumps
    with pytest.raises(OSError) as caught:
        execute(world)
    assert caught.value is failure
    assert [r['event'] for r in world.saved if r['schema'] == binding.SCHEMA] == ['worker-rpc-received']


def test_observer_sdk_failure_keeps_native_execution_without_claiming_binding(world):
    def unavailable(): raise ValueError('EXAMPLE source 관문 실패')

    world.observer.verify_observer = unavailable
    result, _, calls, _ = execute(world)
    assert len(calls) == len(world.written) == 1
    assert result['worker_receives'] == result['worker_responses'] == 0
    assert 'RANK_V1_BINDING_UNAVAILABLE' in result['gaps']


@pytest.mark.parametrize('change', ['hmac', 'status', 'owner', 'request', 'claim', 'extra', 'duplicate-call', 'point-type'])
def test_changed_closed_snapshot_or_overclaim_is_rejected_even_with_test_owned_key(world, change):
    _, _, _, fresh_recording = execute(world)
    paths = sorted(world.capture.glob('pid-*.json'))
    path = paths[-1]; record = json.loads(path.read_bytes()); payload = record['snapshot']['payload']
    if change == 'hmac': record['hmac_sha256'] = '0' * 64
    else:
        if change == 'status': payload['response_status'] = 'unknown'
        elif change == 'owner': payload['owner_object_hmac_sha256'] = 'a' * 64
        elif change == 'request': payload['request_binding_hmac_sha256'] = ['b' * 64]
        elif change == 'claim': payload['scope_complete'] = True
        elif change == 'extra': payload['output'] = 'EXAMPLE-private-output'
        elif change == 'point-type': payload['point'] = []
        else: payload['call_hmac_sha256'] = json.loads(paths[0].read_bytes())['snapshot']['payload']['call_hmac_sha256']
        record['hmac_sha256'] = hmac.new(world.key, canonical(record['snapshot']), sha256).hexdigest()
    path.write_bytes(canonical(record))
    with pytest.raises(ValueError):
        capture.verify_capture(world.prepared, world.capture, fresh_prepared=world.fresh.folder,
            fresh_recording=fresh_recording, expected_source_manifest_sha256=world.manifest['source_manifest_sha256'])


@pytest.mark.parametrize('change', ['settings-extra', 'settings-key', 'settings-mode', 'source', 'fresh-hash', 'asset', 'key-mode', 'key-symlink'])
def test_prepared_key_asset_or_scope_changes_are_rejected(world, change):
    _, _, _, fresh_recording = execute(world)
    path = world.prepared / 'settings.json'
    if change.startswith('settings-'):
        value = json.loads(path.read_bytes())
        if change == 'settings-extra': value['extra'] = True
        elif change == 'settings-key': value['key'] = '0' * 64
        else: path.chmod(0o644)
        if change != 'settings-mode': path.write_bytes(canonical(value))
    elif change == 'asset':
        path = world.prepared / 'guard/adapters/vllm_rank_v1_worker_binding.py'
        path.write_bytes(path.read_bytes() + b'\n')
    elif change in ('key-mode', 'key-symlink'):
        path = world.fresh.folder / 'request-binding-key.json'
        if change == 'key-mode': path.chmod(0o644)
        else:
            renamed = path.with_name('owned-key.json'); path.rename(renamed); path.symlink_to(renamed)
    else:
        path = world.prepared / 'manifest.json'; value = json.loads(path.read_bytes())
        value['source_manifest_sha256' if change == 'source' else 'fresh_prepared_sha256'] = '0' * 64
        path.write_bytes(canonical(value))
    with pytest.raises((ValueError, OSError)):
        capture.verify_capture(world.prepared, world.capture, fresh_prepared=world.fresh.folder,
            fresh_recording=fresh_recording, expected_source_manifest_sha256=world.manifest['source_manifest_sha256'])


def test_prepared_secret_is_private_and_public_manifest_does_not_contain_key(world):
    assert (world.prepared / 'settings.json').stat().st_mode & 0o777 == 0o600
    assert world.key.hex().encode() not in canonical(world.manifest)
    for path in world.prepared.rglob('*'):
        if path.is_file(): assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='OUTPUT_EXISTS'):
        inputs.prepare(world.prepared, image=world.manifest['image'], fresh_prepared=world.fresh.folder)


def test_sdk_code_freeze_and_private_key_reader_use_actual_unchanged_sources(external, monkeypatch):  # noqa: F811
    from guard.collectors import lifecycle
    fresh = external / 'fresh'
    original = lifecycle.prepare_assets(fresh, inputs.ROOT.parent, profile='vllm-native-lifecycle-metadata/28')
    prepared = external / 'worker'
    manifest = inputs.prepare(prepared, image=versions()['baseline']['image'], fresh_prepared=fresh)
    value = json.loads((prepared / 'settings.json').read_bytes())
    monkeypatch.setattr(runtime.common, 'ROOT', prepared)
    monkeypatch.setattr(runtime, 'ROOT', prepared)
    runtime.freeze(value)
    key = runtime.private_key(fresh / 'request-binding-key.json',
        expected_sha256=original['assets']['request-binding-key.json']['sha256'], session=original['session'])
    assert key.hex() == value['key'] and not manifest['observer_installed']
    monkeypatch.setattr(binding, '_native_ids', lambda *a: [])
    with pytest.raises(ValueError, match='CODE_UNAVAILABLE'):
        runtime.freeze(value)


def test_isolated_stdlib_sdk_installation_has_no_fault_control_or_engine_import(external):  # noqa: F811
    from guard.collectors import lifecycle
    fresh = external / 'fresh'
    lifecycle.prepare_assets(fresh, inputs.ROOT.parent, profile='vllm-native-lifecycle-metadata/28')
    prepared = external / 'worker'
    inputs.prepare(prepared, image=versions()['baseline']['image'], fresh_prepared=fresh)
    code = '''import sys,os,importlib,importlib.util,json,types
from pathlib import Path
root,fresh=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('_byul_rank_ipc',root/'guard/__init__.py',submodule_search_locations=[str(root/'guard')])
package=importlib.util.module_from_spec(spec);sys.modules['_byul_rank_ipc']=package;spec.loader.exec_module(package)
runtime=importlib.import_module('_byul_rank_ipc.adapters.vllm_rank_v1_worker_runtime')
runtime.ROOT=runtime.common.ROOT=root
# EXAMPLE filesystem root mapping only. 원 SDK 함수/bytes/키/세션은 보존한다.
path=root/'settings.json';value=json.loads(path.read_bytes());value['output_dir']=str(root/'data'/value['session'])
path.write_text(json.dumps(value,sort_keys=True,separators=(',',':')))
opened=os.open
runtime.common.base.os=types.SimpleNamespace(**(vars(os)|{'open':lambda p,f,*a,**kw:opened(str(fresh) if p in ('/guard-lifecycle','guard-lifecycle') else p,f,*a,**kw)}))
before=len(sys.meta_path);result=runtime.install()
assert result['observer_installed'] and len(sys.meta_path)==before+1
assert all(result[k] is False for k in ('scope_complete','source_authenticated','engine_group_verified','producer_registered'))
observer,finder=runtime._INSTALLED
assert observer.modules=={} and observer.installed=={} and not hasattr(observer,'control')
assert not any(name=='vllm' or name.startswith('vllm.') for name in sys.modules)
try:runtime.install()
except ValueError:pass
else:raise AssertionError('중복 설치를 받았다')
print('EXAMPLE 읽기 전용 SDK 설치 확인')
'''
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(prepared), str(fresh)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'EXAMPLE 읽기 전용 SDK 설치 확인'
