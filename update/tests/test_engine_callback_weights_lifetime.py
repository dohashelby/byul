"""LIF-13 weights daemon 수집: 실제 vLLM 0.31.0 daemon·IPC loader 함수 코드를 감싸 만든 행을 투영에 넣는다.

daemon과 소비자는 프로세스 안의 대역 socket으로 잇는다. 원 통신 함수 대신 대역 send/recv가
요청을 원 daemon 처리기에 그대로 넘긴다. CUDA tensor·IPC handle은 쓰지 않는다.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from guard.adapters import vllm_engine_callback_sensor as sensor
from guard.adapters import vllm_weights_lifetime_callback as wl
from guard.executors import engine_callback_projection as projection
from tests.test_cache_callback import source_module

SECRET = bytes(range(32))


class Unavailable(Exception): pass
class Mismatch(Exception): pass
class Unsupported(Exception): pass


class Key:
    """원 WeightCacheKey 대역. 같은 모델이면 차이 필드가 없다."""
    def __init__(self, model='m'): self.model = model
    def mismatched_fields(self, other): return [] if other.model == self.model else ['model']


class Conn:
    """대역 UNIX socket. 요청은 daemon 처리기로 가고 응답은 다음 recv로 돌아온다."""
    def __init__(self, daemon, cred): self.daemon = daemon; self.cred = cred; self.response = None
    def __enter__(self): return self
    def __exit__(self, *exc): return False


def send_msg(conn, obj):
    if 'cmd' in obj:
        (conn.daemon._handle_get_state(conn, obj) if obj['cmd'] == 'get_state' else conn.daemon._handle_release(conn))
    else:
        conn.response = obj


def recv_msg(conn):
    return conn.response


SHARED = {'send_msg': send_msg, 'recv_msg': recv_msg, 'logger': SimpleNamespace(info=lambda *a: None, info_once=lambda *a: None,
          warning=lambda *a: None, exception=lambda *a: None), 'torch': SimpleNamespace(accelerator=SimpleNamespace(empty_cache=lambda: None))}


def world(tmp_path, *, cred=True):
    daemon_module = source_module(wl.DAEMON_MODULE, tmp_path, targets=wl.TARGETS, sources=wl.SOURCES)
    # 원 load_model 코드도 흐름을 위해 클래스에 둔다. 감싸지는 않는다(실제로는 장식자 래퍼라서).
    loader_module = source_module(wl.LOADER_MODULE, tmp_path, sources=wl.SOURCES,
        targets={**wl.TARGETS, wl.LOADER_MODULE: ('IpcModelLoader.load_model', *wl.TARGETS[wl.LOADER_MODULE])})
    vars(daemon_module).update(SHARED, WeightCacheKey=Key, get_current_device_uuid=lambda: 'GPU-0',
        export_entries=lambda model: ({'w': 1, 'b': 2}, {}), export_model_attrs=lambda model: {})
    model_type = type('Model', (), {})
    vars(loader_module).update(SHARED, WeightCacheUnavailableError=Unavailable, CacheConfigMismatchError=Mismatch,
        UnsupportedQuantForIPCError=Unsupported, check_ipc_platform_support=lambda: None,
        WeightCacheState=lambda **kw: SimpleNamespace(**kw),
        DefaultModelLoader=lambda config: SimpleNamespace(load_model=lambda **kw: model_type()))
    rows = []
    callbacks = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'fixture': True})
    callbacks.weights.peer_reader = staticmethod(lambda conn: conn.cred) if cred else staticmethod(lambda conn: None)
    assert callbacks.instrument(daemon_module) and callbacks.instrument(loader_module)
    daemon = daemon_module.WeightCacheDaemon(); vars(daemon).update(model=model_type(), cache_config=Key(), role='target', global_rank=0)
    return SimpleNamespace(daemon=daemon, loader_cls=loader_module.IpcModelLoader, model_type=model_type, rows=rows, callbacks=callbacks)


def consumer(w, pid, *, fallback=True):
    loader = w.loader_cls()
    conn = lambda timeout: Conn(w.daemon, (pid, 1000, 1000))
    vars(loader).update(mode='zero_copy', fallback=fallback, state_timeout_s=1.0, is_draft=False, _connect=conn,
        _check_gpu_uuid=lambda uuid: None, _fetch_entries=lambda config: loader._request_state(Key()),
        _build_model=lambda *a: w.model_type(), _fallback_load_config=lambda: SimpleNamespace(load_format='auto'),
        load_config=SimpleNamespace(load_format='ipc_cache'))
    config = SimpleNamespace(speculative_config=None)
    return loader.load_model(config, SimpleNamespace(), '')


def release(w, *, pid=200, uid=2000):
    send_msg(Conn(w.daemon, (pid, uid, uid)), {'cmd': 'release'})


CASES = [{'case_id': 'b1', 'role': 'before', 'prompt': 'x'}, {'case_id': 'b2', 'role': 'before', 'prompt': 'x'},
         {'case_id': 'r', 'role': 'switch', 'action': 'weights_release', 'variant': 'target'},
         {'case_id': 'a1', 'role': 'after', 'prompt': 'x'}]
SWITCHES = [{'case_id': 'r', 'action': 'weights_release', 'variant': 'target', 'completed': True}]
SAME = {'b1': 'o', 'b2': 'o', 'a1': 'o'}


def scenario(tmp_path, **options):
    w = world(tmp_path, **options)
    consumer(w, 101); consumer(w, 102)
    release(w)
    consumer(w, 103)
    return w


def test_real_daemon_and_loader_code_cover_every_branch_except_release_authorization(tmp_path):
    w = scenario(tmp_path)
    result = projection.project_weights_lifetime(CASES, SAME, w.rows, SWITCHES)
    assert result['consumers'] == 2 and result['post_release'] == {'refused': 1, 'fallback': 1}
    assert result['violation'] is False and result['full_scope'] is False
    assert result['missing'] == ['release_authorization_policy_unavailable']
    assert result['release_actors'][0]['peer_uid_sha256'] is not None
    assert '2000' not in str(result['release_actors'])  # 주체는 keyed 요약만 남는다.


def test_released_generation_served_again_is_a_violation(tmp_path):
    w = world(tmp_path)
    consumer(w, 101); consumer(w, 102)
    kept = w.daemon.model
    release(w)
    w.daemon.model = kept  # 결함 daemon: release 뒤에도 같은 세대를 다시 들고 있다.
    consumer(w, 103)
    result = projection.project_weights_lifetime(CASES, SAME, w.rows, SWITCHES)
    assert result['violation'] is True and {'reason': 'released_generation_served'} in result['violations']


def test_fallback_while_disabled_is_a_violation(tmp_path):
    w = scenario(tmp_path)
    loader = w.loader_cls(); vars(loader).update(fallback=False, _fallback_load_config=lambda: SimpleNamespace(load_format='auto'))
    loader._fallback_load(SimpleNamespace(), SimpleNamespace(), '')
    result = projection.project_weights_lifetime(CASES, SAME, w.rows, SWITCHES)
    assert {'reason': 'fallback_used_while_disabled'} in result['violations']


def test_disabled_fallback_without_daemon_raises_and_leaves_fallback_unobserved(tmp_path):
    w = world(tmp_path)
    consumer(w, 101); consumer(w, 102); release(w)
    with pytest.raises(Unavailable):
        consumer(w, 103, fallback=False)
    result = projection.project_weights_lifetime(CASES, SAME, w.rows, SWITCHES)
    assert result['violation'] is None and 'fallback_consumption_unobserved' in result['missing']


def test_unreadable_peer_credentials_leave_the_release_actor_unobserved(tmp_path):
    w = scenario(tmp_path, cred=False)
    result = projection.project_weights_lifetime(CASES, SAME, w.rows, SWITCHES)
    assert 'release_actor_unobserved' in result['missing'] and 'two_consumer_acquisition_unobserved' in result['missing']
    assert 'WEIGHTS_PEER_CREDENTIAL_UNAVAILABLE' in w.callbacks.weights.gaps


def test_changed_answer_after_release_alone_stays_unknown(tmp_path):
    w = scenario(tmp_path)
    result = projection.project_weights_lifetime(CASES, {'b1': 'o', 'b2': 'o', 'a1': 'p'}, w.rows, SWITCHES)
    assert result['violation'] is None and 'post_release_output_changed' in result['missing']


def test_release_switch_is_only_for_lif13_and_needs_a_declared_daemon_socket():
    projection.validate_cases('LIF-13', CASES)
    with pytest.raises(ValueError):
        projection.validate_cases('REQ-16', CASES)
    spec = {'weights_daemon': {'variants': {'target': '/guard-weights-daemon/target.sock'}}}
    assert projection.validate_lab_contexts(spec, CASES) == spec
    with pytest.raises(ValueError):
        projection.validate_lab_contexts({'weights_daemon': {'variants': {'target': '/tmp/x.sock'}}}, CASES)


def test_decorated_load_model_is_not_a_target(tmp_path):
    # 실제 vLLM의 load_model은 @instrument 장식자 래퍼라 원 코드 자격을 얻을 수 없다.
    assert all(not p.endswith('.load_model') for p in wl.TARGETS[wl.LOADER_MODULE])


def test_peer_reader_returns_none_without_unix_credentials():
    assert wl.peer(object()) is None


def test_transport_sends_one_release_only_to_the_declared_daemon_socket():
    import json
    from guard.runtime.kubernetes_http import WEIGHTS_RELEASE, KubernetesBindingError
    from tests.test_req16_lab_contexts import LabWorld, transport

    class ReleaseWorld(LabWorld):
        def __init__(self, status='ok'):
            super().__init__(); self.releases = []; self.status = status
        def runner(self, argv, **kwargs):
            if 'exec' in argv and WEIGHTS_RELEASE in argv:
                self.releases.append(argv[-1])
                value = {'schema': 'guard-lab-weights-release/1', 'status': self.status}
                return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
            return super().runner(argv, **kwargs)

    spec = {'weights_daemon': {'variants': {'target': '/guard-weights-daemon/target.sock'}}}
    world = ReleaseWorld(); http = transport(world)
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('weights_release', 'target')  # 선언 전
    http.declare_callback_lab(spec)
    assert http.engine_callback_switch('weights_release', 'target') == {'daemon_status': 'ok'}
    assert world.releases == ['/guard-weights-daemon/target.sock'] and not world.posts
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('weights_release', 'draft')
    refused = ReleaseWorld('error'); http = transport(refused); http.declare_callback_lab(spec)
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('weights_release', 'target')


def test_daemon_and_consumer_rows_from_separate_processes_are_not_mixed(tmp_path):
    # daemon과 소비자는 실제로 다른 프로세스다. 호출 번호가 겹쳐도 행이 서로 덮이면 안 된다.
    daemon_module = source_module(wl.DAEMON_MODULE, tmp_path, targets=wl.TARGETS, sources=wl.SOURCES)
    loader_module = source_module(wl.LOADER_MODULE, tmp_path, sources=wl.SOURCES,
        targets={**wl.TARGETS, wl.LOADER_MODULE: ('IpcModelLoader.load_model', *wl.TARGETS[wl.LOADER_MODULE])})
    rows = []
    daemon_side = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'process': 'daemon'})
    consumer_side = sensor.EngineCallbacks('a' * 32, SECRET, rows.append, context_provider=lambda: {'process': 'consumer'})
    daemon_side.weights.peer_reader = staticmethod(lambda conn: conn.cred)
    assert daemon_side.instrument(daemon_module) and consumer_side.instrument(loader_module)
    model_type = type('Model', (), {})
    vars(daemon_module).update(SHARED, WeightCacheKey=Key, get_current_device_uuid=lambda: 'GPU-0',
        export_entries=lambda model: ({'w': 1}, {}), export_model_attrs=lambda model: {})
    vars(loader_module).update(SHARED, WeightCacheUnavailableError=Unavailable, CacheConfigMismatchError=Mismatch,
        UnsupportedQuantForIPCError=Unsupported, check_ipc_platform_support=lambda: None,
        WeightCacheState=lambda **kw: SimpleNamespace(**kw),
        DefaultModelLoader=lambda config: SimpleNamespace(load_model=lambda **kw: model_type()))
    daemon = daemon_module.WeightCacheDaemon(); vars(daemon).update(model=model_type(), cache_config=Key(), role='target', global_rank=0)
    w = SimpleNamespace(daemon=daemon, loader_cls=loader_module.IpcModelLoader, model_type=model_type)
    consumer(w, 101); consumer(w, 102); release(w); consumer(w, 103)
    result = projection.project_weights_lifetime(CASES, SAME, rows, SWITCHES)
    assert len({r['process_sha256'] for r in rows}) == 2
    assert result['consumers'] == 2 and result['post_release'] == {'refused': 1, 'fallback': 1} and result['violation'] is False
