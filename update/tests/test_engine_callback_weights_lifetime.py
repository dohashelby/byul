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


# ---------------------------------------------------------------------------
# 2026-11-06: 세대 범위 결합. GPU(2026-11-05)에서 daemon은 경계 요청을 받지 않고, 소비자는 기동 때(경계 요청 전) 취득해
# window()가 모든 가중치 행을 버렸다(소비자 0·release 미관측, ENGINE_SENSOR_CALLBACK_SCOPE_INCOMPLETE).
# ---------------------------------------------------------------------------
BARRIER = 'byul-cb-' + 'f' * 32
CONSUMER_CASES = [{'case_id': 'b1', 'role': 'before', 'prompt': 'x'},
                  {'case_id': 'c1', 'role': 'switch', 'action': 'weights_consumer_start', 'variant': 'target'},
                  {'case_id': 'r', 'role': 'switch', 'action': 'weights_release', 'variant': 'target'},
                  {'case_id': 'c2', 'role': 'switch', 'action': 'weights_consumer_start', 'variant': 'target'},
                  {'case_id': 'a1', 'role': 'after', 'prompt': 'x'}]


def barrier_row(at_ns):
    """엔진 프로세스가 경계 요청을 처음 본 행(캐시 관측). 표식은 경계 태그의 keyed 지문이다."""
    return {'schema': 'guard-vllm-cache-callback/1', 'point': 'KVCacheManager.get_computed_blocks', 'phase': 'CALL',
            'process_sha256': 'engine', 'call_sha256': 'barrier', 'operation_started_ns': at_ns, 'entry_qualified_before': True,
            'fields': {'request_sha256': projection.tag_digest(SECRET, BARRIER)}}


def timed(w):
    """행의 시작 시각(같은 노드 monotonic)으로 기동 취득 두 번 → release → 재취득 사이에 경계 요청을 둔다."""
    starts = sorted({r['operation_started_ns'] for r in w.rows if r.get('point') == projection.DAEMON_RELEASE})
    return starts[0] - 1, min(r['operation_started_ns'] for r in w.rows) - 10


def test_daemon_and_startup_acquisitions_join_the_run_by_container_generation(tmp_path):
    w = scenario(tmp_path)
    anchor, container = timed(w)
    events = [barrier_row(anchor), *reversed(w.rows)]  # 읽기 순서(파일 이름 순)와 무관하게 시각으로 정렬한다.
    old = projection.project_weights_lifetime(CASES, SAME, projection.window(events, SECRET, BARRIER), SWITCHES)
    assert old['consumers'] == 0 and 'release_unobserved' in old['missing']  # 원 GPU 실패와 같은 모양
    kept, scope = projection.weights_window(events, SECRET, BARRIER, container)
    assert scope == {'anchor_ns': anchor, 'container_start_known': True, 'rows_outside_container': 0}
    result = projection.project_weights_lifetime(CASES, SAME, kept, SWITCHES, scope)
    assert result['consumers'] == 2 and result['post_release'] == {'refused': 1, 'fallback': 1}
    assert result['violation'] is False and result['missing'] == ['release_authorization_policy_unavailable']


def test_rows_from_an_earlier_container_and_a_release_before_the_run_are_not_this_run(tmp_path):
    w = scenario(tmp_path)
    anchor, container = timed(w)
    stale = {'schema': wl.SCHEMA, 'point': projection.DAEMON_RELEASE, 'phase': 'CALL', 'process_sha256': 'old-daemon',
             'call_sha256': 'x', 'operation_started_ns': container - 5, 'entry_qualified_before': True, 'fields': {}}
    kept, scope = projection.weights_window([stale, barrier_row(anchor), *w.rows], SECRET, BARRIER, container)
    assert scope['rows_outside_container'] == 1 and stale not in kept
    # 같은 세대를 다시 시험한 경우: 이번 경계 요청이 release 뒤에 있으면 그 release는 이번 실행의 것이 아니다.
    late = max(r['operation_started_ns'] for r in w.rows) + 1
    kept, scope = projection.weights_window([barrier_row(late), *w.rows], SECRET, BARRIER, container)
    result = projection.project_weights_lifetime(CASES, SAME, kept, SWITCHES, scope)
    assert 'release_before_run' in result['missing'] and result['violation'] is None


def test_unknown_container_start_or_missing_anchor_keeps_the_scope_open(tmp_path):
    w = scenario(tmp_path)
    anchor, _ = timed(w)
    kept, scope = projection.weights_window([barrier_row(anchor), *w.rows], SECRET, BARRIER, None)
    result = projection.project_weights_lifetime(CASES, SAME, kept, SWITCHES, scope)
    assert scope['container_start_known'] is False and result['consumers'] == 0
    assert {'container_generation_unverified', 'release_unobserved'} <= set(result['missing']) and result['violation'] is None
    kept, scope = projection.weights_window(list(w.rows), SECRET, BARRIER, timed(w)[1])
    result = projection.project_weights_lifetime(CASES, SAME, kept, SWITCHES, scope)
    assert 'run_anchor_unobserved' in result['missing'] and result['violation'] is None


def test_consumer_start_switch_is_lif13_only_and_bound_to_the_declared_daemon():
    projection.validate_cases('LIF-13', CONSUMER_CASES)
    with pytest.raises(ValueError):
        projection.validate_cases('REQ-21', CONSUMER_CASES)
    spec = {'weights_daemon': {'variants': {'target': '/guard-weights-daemon/target.sock'}}}
    assert projection.validate_lab_contexts(spec, CONSUMER_CASES) == spec


def test_transport_starts_numbered_consumers_and_stops_only_the_ones_it_started():
    import json
    from guard.runtime.kubernetes_http import WEIGHTS_CONSUMER_START, WEIGHTS_CONSUMERS_STOP, KubernetesBindingError
    from tests.test_req16_lab_contexts import LabWorld, transport

    class ConsumerWorld(LabWorld):
        def __init__(self, status='ok'):
            super().__init__(); self.ports = []; self.stops = []; self.status = status
        def runner(self, argv, **kwargs):
            if 'exec' in argv and WEIGHTS_CONSUMER_START in argv:
                self.ports.append(argv[-1])
                value = {'schema': 'guard-lab-weights-consumer/1', 'status': self.status, 'pid': 400 + len(self.ports), 'start_ticks': 9}
                return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
            if 'exec' in argv and WEIGHTS_CONSUMERS_STOP in argv:
                self.stops.append(json.loads(argv[-1]))
                value = {'schema': 'guard-lab-weights-consumers-stop/1', 'status': 'ok'}
                return {'returncode': 0, 'stdout_bytes': json.dumps(value).encode(), 'stderr_bytes': b'', 'capture': {'complete': True}}
            return super().runner(argv, **kwargs)

    spec = {'weights_daemon': {'variants': {'target': '/guard-weights-daemon/target.sock'}}}
    world = ConsumerWorld(); http = transport(world)
    assert http.stop_lab_consumers() is True and world.stops == []  # 띄운 것이 없으면 아무것도 하지 않는다
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('weights_consumer_start', 'target')  # 선언 전
    http.declare_callback_lab(spec)
    assert http.engine_callback_switch('weights_consumer_start', 'target') == {'consumer_status': 'ok', 'consumer_port': 8001}
    assert http.engine_callback_switch('weights_consumer_start', 'target')['consumer_port'] == 8002
    assert world.ports == ['8001', '8002'] and not world.posts
    assert http.stop_lab_consumers() is True
    assert world.stops == [[{'pid': 401, 'start_ticks': 9}, {'pid': 402, 'start_ticks': 9}]]
    # 준비되지 않은 소비자도 정리 대상에 남기고, 동작은 실패로 돌려준다.
    slow = ConsumerWorld('timeout'); http = transport(slow); http.declare_callback_lab(spec)
    with pytest.raises(KubernetesBindingError):
        http.engine_callback_switch('weights_consumer_start', 'target')
    http.stop_lab_consumers()
    assert slow.stops == [[{'pid': 401, 'start_ticks': 9}]]


def test_transport_accepts_the_lif13_duration_only_for_an_owned_lab_callback_target():
    """실행기가 LIF-13에 900초를 주면 전송도 받아야 한다(11-06 GPU 준비 중 발견한 불일치)."""
    from tests.test_req16_lab_contexts import LabWorld
    from tests.test_kubernetes_request_binding import World as BindingWorld, env as binding_env, target as binding_target
    from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, REQUEST_BINDING_PROFILE, KubernetesBindingError, KubernetesHTTP
    lab = LabWorld()
    KubernetesHTTP(lab.target, binding_env(), duration=900, runner=lab.runner, profile=ENGINE_CALLBACK_PROFILE)
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP(lab.target, binding_env(), duration=901, runner=lab.runner, profile=ENGINE_CALLBACK_PROFILE)
    value = binding_target(); value['lab'] = False
    plain = BindingWorld(value)
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP(plain.target, binding_env(), duration=900, runner=plain.runner, profile=ENGINE_CALLBACK_PROFILE)
    with pytest.raises(KubernetesBindingError):
        KubernetesHTTP(lab.target, binding_env(), duration=900, runner=lab.runner, profile=REQUEST_BINDING_PROFILE)


@pytest.mark.parametrize('cmdline,supported', [
    ([b'/opt/venv/bin/python', b'/opt/venv/bin/vllm', b'serve', b'/models/opt-125m'], True),  # k3d 실측 형태
    ([b'vllm', b'serve', b'/models/opt-125m'], True),
    ([b'/opt/venv/bin/python', b'-c', b'serve', b'x'], False),
    ([b'/opt/venv/bin/python', b'/opt/venv/bin/vllm', b'bench', b'x'], False),
    ([b'vllm', b'serve', b'/m', b'--port', b'8000'], False)])
def test_consumer_start_reads_the_console_script_form_of_pid1(cmdline, supported, capsys):
    import io, json as _json, sys as _sys, types
    from guard.runtime import kubernetes_http as http
    started = []
    class Child:
        pid = 4242
        def poll(self): return None
    fake_subprocess = types.SimpleNamespace(DEVNULL=-3, STDOUT=-2, Popen=lambda argv, **kw: started.append(argv) or Child())
    def fake_open(path, mode='r'):
        if path == '/proc/1/cmdline': return io.BytesIO(b'\0'.join(cmdline) + b'\0')
        if path.startswith('/proc/4242/stat'): return io.StringIO('4242 (python) S ' + ' '.join(['0'] * 17) + ' 777 0')
        return io.BytesIO()
    class Resp: status = 200
    fake_urllib = types.SimpleNamespace(request=types.SimpleNamespace(urlopen=lambda *a, **k: Resp()))
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *args, **kwargs):
        if name == 'subprocess': return fake_subprocess
        if name.startswith('urllib'): return fake_urllib
        return real_import(name, *args, **kwargs)
    scope = {'__name__': '__main__', '__builtins__': {**builtins.__dict__, '__import__': fake_import, 'open': fake_open}}
    argv = _sys.argv
    try:
        _sys.argv = ['x', '8001']
        with pytest.raises(SystemExit) if not supported else __import__('contextlib').nullcontext():
            exec(compile(http.WEIGHTS_CONSUMER_START, 'consumer', 'exec'), scope)
    finally:
        _sys.argv = argv
    result = _json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    if supported:
        assert result['status'] == 'ok' and started == [[a.decode() for a in cmdline] + ['--port', '8001']]
    else:
        assert result['status'] == 'unsupported' and not started
