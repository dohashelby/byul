"""OBS-09·OBS-11 Kubernetes 전송: 원 log_integrity 실행기의 요청·로그 분석·판정을 Kubernetes 읽기 경로로 잇는다.

kubectl은 부르지 않는다. 엔진 응답과 vLLM 원 형식 로그 줄은 기존 OBS-09 시험 대역을 쓰고,
요청은 실제 전송 검증기(_log_integrity_request)를 통과해야 한다.
"""
from copy import deepcopy
from contextlib import redirect_stdout
import io
import json
import logging
import os
import re
import sys
from time import monotonic
from types import SimpleNamespace

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.configuration import COLLECTION_STATUS_SNAPSHOT_OPTION
from guard.executors import kubernetes_log_integrity as module
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.executors.log_integrity import _ENV
from guard.executors.registry import EXECUTORS
from guard.models import Verdict, canonical_json, sha256_hex
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP
from pathlib import Path

from guard.models import load_pack
from tests.builders import environment
from tests.test_log_integrity import IMAGE, VALUES, World, check, judge, target
from tests.test_log_integrity_parser import PREFIX

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

    def report(self):
        return {'cleanup_errors': []}


def run(tmp_path, monkeypatch, *, changes=None, identity=None, definition=None, values=None, status=None, attach='exact'):
    world = World(**(changes or {}))
    data = target(); data['kubernetes'] = {'run_id': 'EXAMPLE-log-run'}
    path = tmp_path / 'target.json'; path.write_text(json.dumps(data), encoding='utf-8')
    fake = Transport(world, identity=identity, status=status, attach=attach)
    monkeypatch.setattr(module, 'KubernetesHTTP', lambda *a, **k: fake)
    def read_status(ctx, session):
        assert ctx.cprobe is fake
        if status is None:
            raise KubernetesBindingError()
        return status, _status_capture(canonical_json(status))
    monkeypatch.setattr(module, '_read_collection_status', read_status)
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


@pytest.mark.parametrize('inside_attach', [True, False])
def test_owned_writer_window_clips_both_sides_before_attach_alignment(inside_attach):
    from guard.executors.log_integrity_parser import parse_native_info

    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(logging.Formatter(PREFIX + '%(message)s'))
    writer = logging.Logger('EXAMPLE-ordinary-log', level=logging.INFO)
    writer.addHandler(handler)
    for message in ('EXAMPLE before window', 'EXAMPLE inclusive start', 'EXAMPLE inclusive end', 'EXAMPLE after window'):
        writer.info(message)
    physical = output.getvalue().split('\n')[:-1]
    stamps = ('2026-11-04T00:00:01.099999999Z', '2026-11-04T00:00:01.100000000Z',
              '2026-11-04T00:00:05.200000000Z', '2026-11-04T00:00:05.200000001Z')
    lines = [f'{stamp} {line}\n' for stamp, line in zip(stamps, physical, strict=True)]
    raw = ''.join(lines).encode()
    stream_lines = physical[1:3] if inside_attach else [physical[0], physical[3]]
    attach = ''.join(line + '\n' for line in stream_lines).encode()
    state = {'complete': True}
    record = module.KubernetesLogIntegrityExecutor({'target': 'unused'})
    record._attach = SimpleNamespace(stop=lambda: {'returncode': 0, 'stdout_bytes': attach, 'capture': state})
    calls = []

    def engine_logs(since, cap):
        calls.append((since, cap))
        return {'returncode': 0, 'stdout_bytes': raw, 'capture': state}

    since, until = '2026-11-04T09:00:01.100000+09:00', '2026-11-04T00:00:05.200000+00:00'
    ctx = SimpleNamespace(cprobe=SimpleNamespace(engine_logs=engine_logs))
    read = record._read(ctx, since, until, float('inf'))
    assert calls == [(since, module.MAX_CAPTURE_BYTES)]
    assert read['text'] == ''.join(lines[1:3]) and read['source_complete'] is inside_attach
    assert read['window'] == {'since': since, 'until': until}
    assert read['raw_retained_sha256'] == sha256_hex(attach if inside_attach else raw)
    assert record._source['cri_raw_sha256'] == sha256_hex(raw)
    assert record._source['alignment']['aligned'] is inside_attach
    parsed = parse_native_info(read['text'])
    assert [row['message'] for row in parsed['events']] == ['EXAMPLE inclusive start', 'EXAMPLE inclusive end']


def gap_check():
    return load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['OBS-11']


GAP_VALUES = {'required_records': ['request'], 'collection_session': 'a' * 32, 'collection_deadline_ms': {'request': 1000},
              'max_clock_uncertainty_ms': {'request': 100}, 'detection_deadline_ms': {'request': 2000}, 'alert_deadline_ms': {'request': 3000}}


def status(env, rows):
    return {'schema': 'guard-collection-status/1', 'session': 'a' * 32,
            'environment_ref': env.ref().model_dump(mode='json'), 'events': rows}


def _status_capture(raw):
    return {'file_sha256': sha256_hex(raw), 'file_bytes': len(raw), 'owner_binding_verified': True}


def _saved_status_target(side):
    from tests.test_kubernetes_request_binding import target as owned_target
    from tests.test_model_mapping import declared_mapping
    from tools.mvp_versions import versions

    release = versions()[side]
    model_env, mapping = declared_mapping()
    env = environment(engine={'name': release['engine'], 'version': release['version'], 'image_digest': release['image']},
                      model=model_env.model)
    declaration = owned_target(env)
    declaration['model_mapping'] = {**mapping, 'environment_ref': env.ref().model_dump(mode='json'),
                                    'served_model_name': 'fixture-model'}
    declaration['observed_version'] = release['version']
    declaration['engine']['image_digest'] = release['image']
    declaration['kubernetes']['engine'].update(image=release['image'], image_id=release['image'])
    return env, declaration


@pytest.mark.parametrize(('case', 'error'), [
    ('baseline', None),
    ('candidate', None),
    ('owner-alias', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('configured-budget', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('request-timeout', 'KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID'),
    ('offline-digest', 'KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID'),
    ('executor-digest', 'KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID'),
    ('non-object', 'KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID'),
    ('unbound-session', 'COLLECTION_GAP_POLICY_INVALID'),
    ('legacy-model-mapping', 'MODEL_MAPPING_INVALID'),
])
def test_obs11_preparation_checks_declarations_without_collecting_status(tmp_path, monkeypatch, case, error):
    def forbidden(*args, **kwargs):
        pytest.fail('OBS-11 preparation must not contact the target or load its status file')

    monkeypatch.setattr(KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(module, '_read_collection_status', forbidden)
    env, target = _saved_status_target('baseline' if case == 'baseline' else 'candidate')
    if case == 'owner-alias':
        roles = target['kubernetes']
        roles['probe']['pod_uid'] = roles['engine']['pod_uid']
    elif case == 'non-object':
        target = []
    elif case == 'legacy-model-mapping':
        target['model_mapping'] = {'repository_id': env.model.id, 'revision': env.model.revision,
                                   'api_model': target['model_mapping']['served_model_name']}
    raw = canonical_json(target)
    path = tmp_path / 'saved-target.json'
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    settings = {'target': str(path), 'max_duration_seconds': 301 if case == 'configured-budget' else 30}
    if case == 'request-timeout':
        settings['request_timeout'] = 'invalid'
    executor = module.KubernetesLogIntegrityExecutor(
        settings, runner=forbidden, expected_target_sha256='0' * 64 if case == 'executor-digest' else digest)
    values = {key: value for key, value in GAP_VALUES.items()
              if case != 'unbound-session' or key != 'collection_session'}
    expected = 'f' * 64 if case == 'offline-digest' else digest
    if error is None:
        executor.prepared_collection_status(gap_check(), env, values, expected_target_sha256=expected)
    else:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_collection_status(gap_check(), env, values, expected_target_sha256=expected)
    if case == 'legacy-model-mapping':
        with pytest.raises(ValueError, match='^MODEL_MAPPING_INVALID$'):
            executor.run(gap_check(), env, values, VllmAdapter(), tmp_path / 'work')


@pytest.mark.parametrize(('change', 'snapshot'), [
    (None, False), (None, True), ('changed-owner', False), ('duplicate-field', False), ('oversize', False),
])
def test_obs11_run_rechecks_the_prepared_target_before_reading_status(tmp_path, monkeypatch, change, snapshot):
    env, declaration = _saved_status_target('candidate')
    if snapshot:
        declaration['lab'] = False
    path = tmp_path / 'saved-target.json'
    raw = canonical_json(declaration)
    path.write_bytes(raw)
    digest = sha256_hex(raw)

    def forbidden(*args, **kwargs):
        pytest.fail('OBS-11 fixture dispatched runtime I/O')

    settings = {'target': str(path)}
    if snapshot:
        settings[COLLECTION_STATUS_SNAPSHOT_OPTION] = True
        assert module.snapshot_target_sha256(raw, env) == digest
        with pytest.raises(ValueError):
            module.snapshot_target_sha256(raw.decode(), env)
    executor = module.KubernetesLogIntegrityExecutor(settings, runner=forbidden, expected_target_sha256=digest)
    monkeypatch.setattr(module, '_read_collection_status', forbidden)
    definition = gap_check()
    executor.prepared_collection_status(definition, env, GAP_VALUES, expected_target_sha256=None if snapshot else digest)
    if change is not None:
        if change == 'changed-owner':
            declaration['kubernetes']['probe']['pod_uid'] = '00000000-0000-0000-0000-000000000003'
            path.write_bytes(canonical_json(declaration))
        elif change == 'duplicate-field':
            path.write_bytes(b'{"lab":true,' + raw[1:])
        else:
            path.write_bytes(raw + b' ' * 65536)
        if change != 'changed-owner':
            assert canonical_json(json.loads(path.read_bytes())) == raw
        monkeypatch.setattr(executor, '_transport', forbidden)
        with pytest.raises(ValueError, match='^KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID$'):
            executor.run(definition, env, GAP_VALUES, VllmAdapter(), tmp_path / 'work')
        return

    row = {'record': 'request', 'event_at_ms': 10, 'arrived_at_ms': 11, 'gap_marked_at_ms': None,
           'alert_at_ms': None, 'dependent_result': None, 'clock_uncertainty_ms': 1}
    document = status(env, [row])
    record_bytes = canonical_json(document) + b'\n'
    read_sessions = []

    def read_status(ctx, session):
        assert ctx._collection_target == declaration and ctx.cprobe.target_sha256 == digest
        assert ctx.env.ref() == env.ref()
        read_sessions.append(session)
        return document, _status_capture(record_bytes)

    monkeypatch.setattr(module, '_read_collection_status', read_status)
    draft = executor.run(definition, env, GAP_VALUES, VllmAdapter(), tmp_path / 'work')
    assert read_sessions == [GAP_VALUES['collection_session']]
    assert draft.status.value == 'COMPLETED' and draft.data_origin.value == 'EXAMPLE'
    assert draft.observations['collection_normal_arrivals'].value == ['request']
    summary = json.loads(draft.artifacts[0].path.read_text(encoding='utf-8').splitlines()[-1])
    assert summary['file_sha256'] == sha256_hex(record_bytes)
    assert summary['capture_receipt'] == _status_capture(record_bytes)
    assert summary['record_sha256'] == sha256_hex(canonical_json(document))
    assert summary['selected_record_timeline'] == [row] and not summary['producer_authenticated']


@pytest.mark.parametrize('case', [
    'missing-pin', 'preparation-pin-only', 'wrong-pin', 'conflicting-pins', 'lab', 'wrong-environment',
    'nonliteral-option', 'legacy-check', 'other-executor', 'obs09',
])
def test_obs11_snapshot_rejects_unbound_or_out_of_scope_inputs_before_transport(tmp_path, monkeypatch, case):
    env, declaration = _saved_status_target('candidate')
    declaration['lab'] = case == 'lab'
    if case == 'wrong-environment':
        declaration['environment_ref']['sha256'] = 'f' * 64
    raw = canonical_json(declaration)
    if case in {'lab', 'wrong-environment'}:
        with pytest.raises(ValueError, match='^KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID$'):
            module.snapshot_target_sha256(raw, env)
    path = tmp_path / 'saved-target.json'
    path.write_bytes(raw)
    digest = sha256_hex(raw)
    definition = gap_check()
    if case == 'legacy-check':
        root = Path(__file__).resolve().parents[1]
        definition = load_pack(root / 'reference/pack-seeds/PACK-BASELINE-33').checks['OBS-11']
    elif case == 'other-executor':
        definition = definition.model_copy(update={'executor': 'observation_state'})
    elif case == 'obs09':
        definition = check()

    def forbidden(*args, **kwargs):
        pytest.fail('Unbound OBS-11 snapshot fixture attempted collection')

    constructor_pin = {'missing-pin': None, 'preparation-pin-only': None, 'wrong-pin': '0' * 64}.get(case, digest)
    preparation_pin = {'preparation-pin-only': digest, 'conflicting-pins': '0' * 64}.get(case)
    settings = {'target': str(path), COLLECTION_STATUS_SNAPSHOT_OPTION: 1 if case == 'nonliteral-option' else True}
    executor = module.KubernetesLogIntegrityExecutor(settings, runner=forbidden, expected_target_sha256=constructor_pin)
    monkeypatch.setattr(module, '_read_collection_status', forbidden)
    if case == 'preparation-pin-only':
        executor.prepared_collection_status(definition, env, GAP_VALUES, expected_target_sha256=preparation_pin)
    monkeypatch.setattr(executor, '_transport', forbidden)
    if case != 'preparation-pin-only':
        with pytest.raises(ValueError, match='^KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID$'):
            executor.prepared_collection_status(definition, env, GAP_VALUES, expected_target_sha256=preparation_pin)
    if case != 'conflicting-pins':
        with pytest.raises(ValueError, match='^KUBERNETES_LOG_INTEGRITY_CONFIGURATION_INVALID$'):
            executor.run(definition, env, GAP_VALUES, VllmAdapter(), tmp_path / 'work')


def test_obs11_record_from_the_probe_pod_reuses_the_original_evaluation(tmp_path, monkeypatch):
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': IMAGE}, model={'id': 'fixture-model'})
    late = {'record': 'request', 'event_at_ms': 0, 'arrived_at_ms': None, 'gap_marked_at_ms': 5000, 'alert_at_ms': None,
            'dependent_result': 'PASS', 'clock_uncertainty_ms': 10}
    unrelated = {**late, 'record': 'private-unselected-record-name'}
    draft, observer, _ = run(tmp_path, monkeypatch, definition=gap_check(), values=GAP_VALUES, status=status(env, [late, unrelated]))
    reasons = {v['reason'] for v in draft.observations['collection_gap_violations'].value}
    assert reasons == {'gap_not_marked_in_time', 'alert_not_raised_in_time', 'dependent_test_passed_without_record'}
    assert draft.status.value=='COMPLETED' and not observer.validated and observer.attached==0
    assert draft.artifacts[0].collector.endswith('+k8s.2')
    summary=json.loads(draft.artifacts[0].path.read_text(encoding='utf-8').splitlines()[-1])
    assert summary['selected_record_timeline']==[late] and summary['producer_authenticated'] is False
    assert 'private-unselected-record-name' not in json.dumps(summary)


def test_obs11_unreadable_record_stays_uncollected(tmp_path, monkeypatch):
    draft, observer, _ = run(tmp_path, monkeypatch, definition=gap_check(), values=GAP_VALUES, status=None)
    assert draft.observations['collection_gap_violations'].collected is False
    assert draft.status.value=='ABORTED' and draft.abort_reason=='COLLECTION_STATUS_UNAVAILABLE'
    assert draft.data_origin.value=='EXAMPLE' and not observer.validated and observer.attached==0


@pytest.mark.parametrize('fault', [None, 'wrong-env', 'wrong-session', 'duplicate', 'symlink', 'owner-changed', 'file-changed'])
def test_obs11_bound_reader_retains_only_stable_scope_validated_receipts(tmp_path, monkeypatch, fault):
    session = 'a' * 32
    selected = tmp_path / 'guard-collection/data'
    selected.mkdir(parents=True)
    directory = selected / session
    if fault == 'symlink':
        alternate = tmp_path / 'another-owner'
        alternate.mkdir()
        directory.symlink_to(alternate, target_is_directory=True)
    else:
        directory.mkdir()
    env = environment(engine={'name': 'vllm', 'version': '0.31.0', 'image_digest': IMAGE})
    normal = {'record': 'request', 'event_at_ms': 50, 'arrived_at_ms': 60, 'gap_marked_at_ms': None,
              'alert_at_ms': None, 'dependent_result': None, 'clock_uncertainty_ms': 1}
    document = status(env, [normal])
    if fault == 'wrong-env':
        document['environment_ref']['sha256'] = '0' * 64
    elif fault == 'wrong-session':
        document['session'] = 'b' * 32
    raw = canonical_json(document) + b'\n'
    if fault == 'duplicate':
        raw = raw.replace(b'"arrived_at_ms":60', b'"arrived_at_ms":60,"arrived_at_ms":2000')
    receipt = directory / 'collection-status.json'
    receipt.write_bytes(raw)
    topology = {'lab': False}
    probe = {'namespace': 'fixture', 'pod': 'fixture-probe', 'container': 'probe', 'image': IMAGE, 'image_id': IMAGE}
    ownership, calls = [], []

    def runner(argv, **_kwargs):
        calls.append(argv)
        assert argv[3:13] == ['--namespace=fixture', 'exec', 'fixture-probe', '--container=probe', '--',
                              'python3', '-I', '-S', '-B', '-c']
        assert argv[13] == supervised_source(module._FILE_READER)
        assert argv[15:] == [f'/guard-collection/data/{session}/collection-status.json', str(module._STATUS_CAP)]
        original_os_open = os.open
        original_os_read = os.read

        def sandbox_open(name, *args, **kwargs):
            return original_os_open(tmp_path if name == '/' else name, *args, **kwargs)

        reads = []
        def concurrent_write(fd, limit):
            data = original_os_read(fd, limit)
            if not reads:
                receipt.write_bytes(raw + b' ')
            reads.append(True)
            return data

        output = io.StringIO()
        with monkeypatch.context() as patch, redirect_stdout(output):
            patch.setattr(os, 'open', sandbox_open)
            if fault == 'file-changed':
                patch.setattr(os, 'read', concurrent_write)
            patch.setattr(sys, 'argv', ['reader', *argv[15:]])
            exec(compile(module._FILE_READER, 'owned file reader', 'exec'), {})
        return {'returncode': 0, 'stdout_bytes': output.getvalue().encode(),
                'capture': {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}}

    class BoundTransport(SimpleNamespace):
        def _verify(self):
            assert 0 < self.deadline - self.clock() <= 5
            ownership.append(True)
            if fault == 'owner-changed' and len(ownership) == 2:
                raise KubernetesBindingError()

    transport = BoundTransport(profile=module.LOG_INTEGRITY_PROFILE, target_sha256=sha256_hex(canonical_json(topology)),
        binding={'context': 'fixture', 'run_id': 'fixture-run', 'probe': probe}, binary='kubectl',
        runner=runner, clock=monotonic, deadline=monotonic() + 30)
    ctx = SimpleNamespace(cprobe=transport, _collection_target=topology, _collection_status_cleanup=[],
                          env=env, check=gap_check(), policy_values=GAP_VALUES)
    executor = module.KubernetesLogIntegrityExecutor({'target': 'EXAMPLE'})
    observations, _, _, summary = executor.collect(ctx)
    assert not summary['producer_authenticated'] and not summary['independent_event_verified']
    if fault:
        assert summary['diagnostic_codes'] == ['COLLECTION_STATUS_UNAVAILABLE']
        assert summary['capture_receipt'] is None and summary['record_sha256'] is None
        assert summary['file_sha256'] == (sha256_hex(raw) if fault in {'wrong-env', 'wrong-session'} else None)
        assert not observations['collection_normal_arrivals']['collected']
    else:
        assert not summary['diagnostic_codes']
        assert summary['capture_receipt'] == _status_capture(raw)
        assert summary['file_sha256'] == sha256_hex(raw)
        assert summary['record_sha256'] == sha256_hex(canonical_json(document))
        assert summary['selected_record_timeline'] == [normal]
        assert observations['collection_normal_arrivals'] == {'value': ['request'], 'collected': True}
    assert len(calls) == 1 and len(ownership) == 2 and not ctx._collection_status_cleanup
    assert transport.deadline - transport.clock() > 5


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


def test_kubernetes_projection_attachments_are_written_and_execution_completes(tmp_path, monkeypatch):
    """11-05·11-06 GPU OBS-09 ABORTED 원인: 투영 재생이 CRI image ID(repo@sha256)를 거부했다."""
    draft, _, _ = run(tmp_path, monkeypatch)
    names = {artifact.name for artifact in draft.artifacts}
    assert {'log_integrity_samples.json', 'log_integrity_events.json', 'log_integrity_display.html'} <= names
    assert getattr(draft, 'abort_reason', None) is None


def test_projection_replay_accepts_only_the_same_digest_cri_image_id(tmp_path, monkeypatch):
    from copy import deepcopy as _copy
    from guard.executors import log_integrity as li, native_projection as projection
    seen = []
    real = projection.replay_native
    monkeypatch.setattr(li, 'replay_native', lambda sample: (seen.append(_copy(sample)), real(sample))[1])
    run(tmp_path, monkeypatch)
    sample, = seen
    identity = sample['source_identity_before']
    assert identity['image_id'].endswith('@' + identity['image'].rsplit('@', 1)[1])
    real(_copy(sample))  # 실제 CRI 형식 그대로 통과
    for bad in ('docker.io/x@sha256:' + 'f' * 64, 'not-an-id'):
        changed = _copy(sample); changed['source_identity_before']['image_id'] = bad
        with pytest.raises(ValueError):
            real(changed)
