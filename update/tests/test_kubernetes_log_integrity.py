"""OBS-09·OBS-11 Kubernetes 전송: 원 log_integrity 실행기의 요청·로그 분석·판정을 Kubernetes 읽기 경로로 잇는다.

kubectl은 부르지 않는다. 엔진 응답과 vLLM 원 형식 로그 줄은 기존 OBS-09 시험 대역을 쓰고,
요청은 실제 전송 검증기(_log_integrity_request)를 통과해야 한다.
"""
from copy import deepcopy
import json
import re
import sys

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.executors import kubernetes_log_integrity as module
from guard.executors.log_integrity import _ENV
from guard.executors.registry import EXECUTORS
from guard.models import Verdict
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP
from pathlib import Path

from guard.models import load_pack
from tests.builders import environment
from tests.test_log_integrity import IMAGE, VALUES, World, check, judge, target

DIGEST = IMAGE.rsplit('@', 1)[1]


@pytest.fixture(autouse=True)
def no_settle(monkeypatch):
    monkeypatch.setattr(module, 'ATTACH_SETTLE_SECONDS', 0)


def cri(text):
    """containerd CRI 로그 계층: 줄마다 LF 앞 CR 하나를 지운다(k3d 실측 2026-11-06)."""
    return text.replace('\r\n', '\n')


class Stream:
    def __init__(self, world, mode):
        self.world, self.mode = world, mode

    def stop(self):
        text = re.sub(r'(?m)^\S+Z ', '', ''.join(self.world.lines))  # attach에는 시각이 없다
        if self.mode == 'extra':
            text = 'startup line\n' + text + 'idle line\n'
        if self.mode == 'missing':
            text = text.split('\n', 2)[-1]
        complete = self.mode != 'incomplete'
        return {'returncode': 0 if complete else -1, 'stdout_bytes': text.encode(), 'stderr_bytes': b'',
                'capture': {'complete': complete}}


class Transport:
    """Kubernetes 전송 대역. 요청 검증은 실제 KubernetesHTTP 검증기를 쓴다.

    kubectl logs는 실제 CRI처럼 LF 앞 CR 하나가 지워진 줄을, attach는 원 바이트를 준다.
    """
    api_model = 'fixture-model'
    injected = True

    def __init__(self, world, *, identity=None, status=None, attach='exact'):
        self.world = world; self.identity = identity or {}; self.status = status; self.validated = []
        self.attach = attach; self.attached = 0

    def engine_attach(self, cap):
        assert cap == module.MAX_CAPTURE_BYTES
        if self.attach is None:
            raise KubernetesBindingError()
        self.attached += 1
        return Stream(self.world, self.attach)

    def __call__(self, network, requests, timeout):
        assert network == 'kubernetes-probe'
        request, = requests
        KubernetesHTTP._log_integrity_request(None, request)
        self.validated.append(request['id'])
        return self.world.probe('EXAMPLE-trusted', requests, timeout)

    def engine_log_identity(self):
        argv = ['vllm', 'serve', *target()['comparison_config']['launch']['argv']]
        value = {'container_id': 'containerd://EXAMPLE', 'started_at': '2026-11-04T00:00:00Z', 'image': IMAGE,
                 'image_id': 'docker.io/vllm/vllm-openai-cpu@' + DIGEST, 'argv': argv,
                 'env': {**_ENV, 'API_KEY': None}}
        value.update(self.identity)
        return value

    def engine_logs(self, since, cap):
        until = '2099-01-01T00:00:00+00:00'
        result = self.world.logs_window('EXAMPLE-log-engine', since=since, until=until, timeout=5)
        return {'stdout_bytes': cri(result.stdout).encode(), 'stderr_bytes': b'', 'returncode': result.returncode,
                'capture': {'complete': True}}

    def read_collection_status(self, session):
        if self.status is None:
            raise KubernetesBindingError()
        return self.status

    def report(self):
        return {'cleanup_errors': []}


def run(tmp_path, monkeypatch, *, changes=None, identity=None, definition=None, values=None, status=None, attach='exact'):
    world = World(**(changes or {}))
    data = target(); data['kubernetes'] = {'run_id': 'EXAMPLE-log-run'}
    path = tmp_path / 'target.json'; path.write_text(json.dumps(data), encoding='utf-8')
    fake = Transport(world, identity=identity, status=status, attach=attach)
    monkeypatch.setattr(module, 'KubernetesHTTP', lambda *a, **k: fake)
    executor = module.KubernetesLogIntegrityExecutor({'target': str(path), 'request_timeout': 1})
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': IMAGE}, model={'id': 'fixture-model'})
    draft = executor.run(definition or check(), env, deepcopy(VALUES if values is None else values), VllmAdapter(), tmp_path / 'work')
    return draft, fake, env


def test_real_executor_through_kubernetes_reads_passes_the_original_judge(tmp_path, monkeypatch):
    draft, fake, _ = run(tmp_path, monkeypatch)
    assert fake.validated == ['before', 'multiline', 'cr', 'crlf', 'reserved', 'html', 'after']
    assert draft.observations['log_identity_verified'].value is True
    assert judge(draft).verdicts == {'security': Verdict.PASS, 'functional': Verdict.PASS, 'performance': Verdict.UNKNOWN}


def test_forged_log_metadata_through_kubernetes_is_still_a_failure(tmp_path, monkeypatch):
    draft, _, _ = run(tmp_path, monkeypatch, changes={'forged_metadata': True})
    assert judge(draft).verdicts['security'] == Verdict.FAIL


@pytest.mark.parametrize('identity', [{'env': {'VLLM_LOGGING_LEVEL': 'DEBUG'}}, {'image_id': 'docker.io/x@sha256:' + 'f' * 64},
                                      {'argv': ['vllm', 'serve', '--disable-log-requests']}])
def test_pod_logging_controls_that_differ_leave_identity_unverified(tmp_path, monkeypatch, identity):
    draft, fake, _ = run(tmp_path, monkeypatch, identity=identity)
    assert draft.observations['log_identity_verified'].value is False and fake.validated == []
    assert judge(draft).verdicts['security'] != Verdict.PASS


def test_lines_after_the_window_end_are_dropped(tmp_path, monkeypatch):
    draft, fake, _ = run(tmp_path, monkeypatch)
    record = module.KubernetesLogIntegrityExecutor({'target': 'x'})
    class Late(Transport):
        def engine_logs(self, since, cap):
            text = '2026-11-04T00:00:01.123456789Z a\rkept\n2026-11-04T00:00:09Z b\n'
            return {'stdout_bytes': text.encode(), 'returncode': 0, 'capture': {'complete': True}}
    ctx = type('Ctx', (), {'cprobe': Late(fake.world)})()
    class Raw:
        def stop(self):
            return {'returncode': 0, 'stdout_bytes': b'a\rkept\nb\n', 'stderr_bytes': b'', 'capture': {'complete': True}}
    record._attach = Raw()
    read = record._read(ctx, '2026-11-04T00:00:00Z', '2026-11-04T00:00:05+00:00', float('inf'))
    assert read['text'] == '2026-11-04T00:00:01.123456789Z a\rkept\n' and read['source_complete'] is True
    class Unstamped(Late):
        def engine_logs(self, since, cap):
            return {'stdout_bytes': b'no stamp\n', 'returncode': 0, 'capture': {'complete': True}}
    ctx.cprobe = Unstamped(fake.world)
    assert record._read(ctx, '2026-11-04T00:00:00Z', '2026-11-04T00:00:05+00:00', float('inf'))['source_complete'] is False


def gap_check():
    return load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['OBS-11']


GAP_VALUES = {'required_records': ['request'], 'collection_session': 'a' * 32, 'collection_deadline_ms': {'request': 1000},
              'max_clock_uncertainty_ms': {'request': 100}, 'detection_deadline_ms': {'request': 2000}, 'alert_deadline_ms': {'request': 3000}}


def status(env, rows):
    return {'schema': 'guard-owned-file/1', 'sha256': 'a' * 64,
            'document': {'schema': 'guard-collection-status/1', 'session': 'a' * 32,
                         'environment_ref': env.ref().model_dump(mode='json'), 'events': rows}}


def test_obs11_record_from_the_probe_pod_reuses_the_original_evaluation(tmp_path, monkeypatch):
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': IMAGE}, model={'id': 'fixture-model'})
    late = {'record': 'request', 'event_at_ms': 0, 'arrived_at_ms': None, 'gap_marked_at_ms': 5000, 'alert_at_ms': None,
            'dependent_result': 'PASS', 'clock_uncertainty_ms': 10}
    draft, _, _ = run(tmp_path, monkeypatch, definition=gap_check(), values=GAP_VALUES, status=status(env, [late]))
    reasons = {v['reason'] for v in draft.observations['collection_gap_violations'].value}
    assert reasons == {'gap_not_marked_in_time', 'alert_not_raised_in_time', 'dependent_test_passed_without_record'}


def test_obs11_unreadable_record_stays_uncollected(tmp_path, monkeypatch):
    draft, _, _ = run(tmp_path, monkeypatch, definition=gap_check(), values=GAP_VALUES, status=None)
    assert draft.observations['collection_gap_violations'].collected is False


def test_transport_accepts_only_the_seven_fixed_body_ids():
    good = {'id': 'cr', 'method': 'POST', 'url': 'http://x/v1/completions', 'headers': {},
            'body': {'model': 'm', 'prompt': 'guard log control', 'max_tokens': 1, 'temperature': 0, 'request_id': 'G0123456789ab_cr\rGUARD_CR'}}
    KubernetesHTTP._log_integrity_request(None, good)
    for change in ({'id': 'other'}, {'headers': {'X-Request-Id': 'x'}}, {'body': {**good['body'], 'request_id': 'G0123456789ab_cr'}},
                   {'body': {**good['body'], 'prompt': 'free'}}, {'body': {**good['body'], 'extra': 1}}):
        with pytest.raises(KubernetesBindingError):
            KubernetesHTTP._log_integrity_request(None, {**good, **change})


def test_executor_is_registered_and_rejects_unknown_settings(tmp_path):
    assert 'log_integrity:kubernetes' in EXECUTORS
    executor = module.KubernetesLogIntegrityExecutor({'target': str(tmp_path / 'x.json'), 'docker_binary': 'docker'})
    with pytest.raises(ValueError, match='KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID'):
        executor.run(check(), environment(), VALUES, VllmAdapter(), tmp_path / 'w')


def test_pod_env_without_value_is_the_empty_string_and_secret_refs_stay_unread():
    from types import SimpleNamespace
    pod = {'spec': {'containers': [{'name': 'engine', 'image': IMAGE, 'command': ['vllm', 'serve'], 'args': ['/m'],
                    'env': [{'name': 'VLLM_LOGGING_PREFIX'}, {'name': 'VLLM_LOGGING_LEVEL', 'value': 'INFO'},
                            {'name': 'API_KEY', 'valueFrom': {'secretKeyRef': {'name': 's', 'key': 'k'}}}]}]},
           'status': {'containerStatuses': [{'name': 'engine', 'containerID': 'c', 'imageID': 'i',
                      'state': {'running': {'startedAt': 't'}}}]}}
    stub = SimpleNamespace(profile='kubernetes-owned-log-integrity-http-v1', binding={'engine': {'container': 'engine'}},
                           _verify=lambda: {'engine': pod})
    identity = KubernetesHTTP.engine_log_identity(stub)
    assert identity['env'] == {'VLLM_LOGGING_PREFIX': '', 'VLLM_LOGGING_LEVEL': 'INFO', 'API_KEY': None}


def test_cri_alone_loses_the_native_continuation_and_stays_unknown(tmp_path, monkeypatch):
    """11-05 GPU 실행의 모양: attach가 없으면 CRI 줄만으로는 multiline·crlf 경계를 확인할 수 없다."""
    draft, fake, _ = run(tmp_path, monkeypatch, attach=None)
    gaps = draft.observations['log_coverage_gaps'].value
    assert any(g.startswith('multiline:') for g in gaps) and any(g.startswith('crlf:') for g in gaps)
    assert draft.observations['log_trials_reached'].value == ['before', 'cr', 'reserved', 'html', 'after']
    assert draft.observations['log_security_violations'].value == []
    assert judge(draft).verdicts['security'] == Verdict.UNKNOWN


def test_attach_bytes_restore_the_cr_removed_by_cri(tmp_path, monkeypatch):
    draft, fake, _ = run(tmp_path, monkeypatch, attach='extra')
    assert fake.attached == 1
    assert draft.observations['log_trials_reached'].value == ['before', 'multiline', 'cr', 'crlf', 'reserved', 'html', 'after']
    assert judge(draft).verdicts['security'] == Verdict.PASS


@pytest.mark.parametrize('mode', ['missing', 'incomplete'])
def test_attach_that_does_not_cover_the_window_is_not_used(tmp_path, monkeypatch, mode):
    draft, _, _ = run(tmp_path, monkeypatch, attach=mode)
    assert draft.observations['log_collection_complete'].value is False
    assert judge(draft).verdicts['security'] != Verdict.PASS


def test_alignment_requires_one_contiguous_exact_run():
    lines = ['2026-11-06T00:00:01Z A1\n', '2026-11-06T00:00:01Z A2\n']
    merged, diag = module.align_attach(lines, b'x\nA1\r\nA2\ny\n')
    assert merged == '2026-11-06T00:00:01Z A1\r\n2026-11-06T00:00:01Z A2\n' and diag['cr_restored_lines'] == 1
    assert module.align_attach(lines, b'A1\r\nA2\nA1\nA2\n')[0] is None  # 두 위치
    assert module.align_attach(lines, b'A1\r\r\nA2\n')[0] is None        # CR 둘은 CRI 줄 A1\r과 같아야 한다
    assert module.align_attach(lines, b'A1\r\nA2')[0] is None              # 끝나지 않은 줄
    assert module.align_attach(lines, b'A1\nX\nA2\n')[0] is None           # 끊긴 연속
    assert module.align_attach([], b'A1\n')[0] is None


def test_attach_stream_stops_only_its_child_and_reports_bytes():
    from guard.runtime.kubernetes_http import AttachStream
    import time
    code = "import sys,time;sys.stdout.buffer.write(b'A1\\r\\nA2\\n');sys.stdout.flush();time.sleep(30)"
    stream = AttachStream([sys.executable, '-c', code], cap=1024, timeout=20)
    time.sleep(1.0)
    result = stream.stop()
    assert result['stdout_bytes'] == b'A1\r\nA2\n' and result['returncode'] == 0 and result['capture']['complete'] is True
    early = AttachStream([sys.executable, '-c', 'print(1)'], cap=1024, timeout=20)
    time.sleep(1.0)
    assert early.stop()['capture']['complete'] is False  # 스스로 끝난 attach는 연결 끊김이다
    small = AttachStream([sys.executable, '-c', code], cap=2, timeout=20)
    time.sleep(1.0)
    assert small.stop()['capture']['truncated'] is True


def test_transport_attach_is_log_profile_only_and_quiet():
    from types import SimpleNamespace
    from guard.runtime import kubernetes_http as http
    seen = {}
    class Fake:
        def __init__(self, argv, **kwargs):
            seen['argv'] = argv; self.stdout = self.stderr = __import__('io').BytesIO(b'')
        def poll(self): return 0
        def kill(self): pass
        def wait(self, timeout=None): return 0
        returncode = 0
    stub = SimpleNamespace(profile=http.LOG_INTEGRITY_PROFILE, binary='kubectl', deadline=100, clock=lambda: 0,
                           binding={'context': 'c', 'engine': {'namespace': 'n', 'pod': 'p', 'container': 'engine'}},
                           _verify=lambda: None)
    http.KubernetesHTTP.engine_attach(stub, 1024, popen=Fake).stop()
    assert seen['argv'][-4:] == ['attach', 'p', '--container=engine', '--quiet'] and '-i' not in seen['argv']
    with pytest.raises(KubernetesBindingError):
        http.KubernetesHTTP.engine_attach(SimpleNamespace(**{**vars(stub), 'profile': http.PROFILE}), 1024, popen=Fake)
