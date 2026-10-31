"""A 공개 sample/report와 B 저장 연결. Kubernetes 전송·응답은 EXAMPLE이며 worker/GPU 실행은 0이다."""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import shutil

import pytest
import yaml

from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime import kubernetes_worker_readiness as public
from tools import b_lif07_readiness as tool
from tools import b_lif07_native_capture as helper
from tests.test_b_lif07_readiness import case, PRIVATE
from tests.test_a1_worker_readiness_http import World as AWorld, PRIVATE as A_PRIVATE

COMMON = 'f277fecd6df1e59505c0ad731032593f6fb22885'
SOURCES = {'collector': 'ab933ca4a869272d37143643f2bec0519c17e52af1a33814ae86f92338d312a6',
    'binding': 'a63c81627e579129c64f2831f56ddc7cb1dfbb9b27cd98110541c10e21050db7',
    'probe': '96df266f30e713e43f13cc1a042896894cd061c5af7b7ffd286e8a13ae91e23d'}


class World(AWorld):
    """원 A metadata fixture 재사용. wire의 fresh RID와 고정 body를 직접 검사한다."""
    def runner(self, argv, **kwargs):
        if 'exec' not in argv:
            return super().runner(argv, **kwargs)
        request, = json.loads(kwargs['input_bytes'])['requests']
        slot = public.SLOTS[len(self.execs)]
        assert re.fullmatch('byul-lif07-http-[0-9a-f]{32}-' + slot, request['headers']['X-Request-Id'])
        assert request['id'] == 'lif07-http-' + slot
        assert request['url'].endswith('/v1/chat/completions' if slot == 'canary' else '/health')
        if slot == 'canary':
            assert request['body'] == {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'hello'}],
                'max_tokens': 1, 'temperature': 0, 'stream': False}
        self.execs.append(deepcopy(request)); self.after_exec = True
        body = json.dumps({'model': 'other' if self.mode == 'canary-wrong-model' else 'fixture-model',
            'choices': [{'message': {'content': A_PRIVATE}}]}) if slot == 'canary' else A_PRIVATE
        row = {'id': request['id'], 'method': request['method'], 'mode': 'json', 'outcome': 200,
            'body': body, 'body_bytes': len(body.encode()), 'body_sha256': sha256_hex(body.encode()),
            'capture_complete': self.mode != 'partial-body', 'body_truncated': self.mode == 'partial-body',
            'eof': True, 'utf8_valid': True}
        pipe = {'complete': True, 'bounded_at_source': True, 'timed_out': False, 'truncated': False}
        if self.mode == 'cleanup-after-canary' and slot == 'canary':
            pipe.update(pipes_closed=False, read_errors=['child_reap'])
        return {'returncode': 1 if self.mode == 'outer-returncode' and slot == 'canary' else 0,
            'stdout_bytes': canonical_json([row]), 'stderr_bytes': A_PRIVATE.encode(), 'capture': pipe}


def configure(case, monkeypatch, mode='normal'):
    case['manifest']['request']['messages'] = [{'role': 'user', 'content': 'hello'}]
    case['args']['manifest'].write_text(json.dumps(case['manifest']))
    world = World(case['target'], mode)
    original = public.KubernetesWorkerReadinessHTTP
    created = []
    def collector(*args, **kwargs):
        value = original(*args, **kwargs, runner=world.runner); created.append(value); return value
    # helper 원파일을 바꾸지 않는 소비기 단독 source 계약 대역이다. 실제 source gate 시험은 root 통합에 분리한다.
    monkeypatch.setattr(helper, 'HTTP_REVIEWED_COMMON_COMMIT', COMMON, raising=False)
    monkeypatch.setattr(helper, 'HTTP_EXPECTED_SOURCES', SOURCES, raising=False)
    monkeypatch.setattr(helper, 'verify_source', lambda: {'reviewed_common_commit': COMMON})
    monkeypatch.setattr(public, 'KubernetesWorkerReadinessHTTP', collector)
    now = utcnow()
    window = {'http_validity_start': (now - timedelta(seconds=30)).isoformat(),
        'http_validity_end': (now + timedelta(minutes=3)).isoformat()}
    return world, created, window


def collect(case, window):
    return tool._collect_http(case['env'], case['target'], case['prepared'], case['manifest'],
        window['http_validity_start'], window['http_validity_end'])


@pytest.mark.parametrize('mode,attempts', [('normal', 3), ('unready', 3), ('engine-replaced-late', 1),
    ('partial-body', 1), ('cleanup-after-canary', 2), ('canary-wrong-model', 3), ('outer-returncode', 2)])
def test_actual_public_HTTP_complete_partial_and_late_facts_reach_B_projection(case, monkeypatch, mode, attempts):
    world, created, window = configure(case, monkeypatch, mode)
    observation = collect(case, window)
    packet = observation['snapshot']; assert len(created) == 1 and observation['report_validated']
    assert len(world.execs) == attempts and sum(row['attempted'] for row in packet['requests']) == attempts
    assert packet['requests'][0]['outcome'] == 200
    assert observation['collection_gap'] == (mode in {'engine-replaced-late', 'partial-body', 'cleanup-after-canary', 'outer-returncode'})
    if mode == 'unready': assert packet['requests'][0]['engine_readiness_before']['pod_ready'] is False
    if mode == 'canary-wrong-model': assert packet['requests'][1]['normal_shape_verified'] is False
    if mode == 'cleanup-after-canary': assert packet['cleanup_errors'] and packet['requests'][1]['outcome'] == 200
    record = tool.project(tool._empty(case['env'], case['target'], case['prepared'], case['manifest']),
        case['env'], case['target'], case['prepared'], case['manifest'])
    record = tool._attach_http(record, observation)
    assert record['requests'] == record['http_requests_attempted'] == attempts
    assert record['target_io_attempted'] is True and record['cleanup']['owned_host_complete'] is None
    assert record['pre_ready_admission'] is record['post_ready_canary'] is record['ready_send_observed'] is None
    assert record['confirmed_violations'] == [] and record['full_scope_coverage'] is False
    assert all(packet[key] is False for key in public.FLAGS)
    assert PRIVATE not in json.dumps(record) and A_PRIVATE not in json.dumps(record)


@pytest.mark.parametrize('fault', ['exception', 'private_packet'])
def test_late_report_failure_preserves_validated_sample_without_raw_echo(case, monkeypatch, fault):
    _, created, window = configure(case, monkeypatch)
    original_report = public.KubernetesWorkerReadinessHTTP
    # 실제 sample 메서드는 그대로 실행하고 report 경계만 실패시킨다.
    def collector(*args, **kwargs):
        value = original_report(*args, **kwargs)
        if fault == 'exception':
            value.report = lambda: (_ for _ in ()).throw(ValueError(PRIVATE))
        else:
            report = value.report
            def invalid():
                packet = report(); packet['body'] = PRIVATE; return packet
            value.report = invalid
        return value
    monkeypatch.setattr(public, 'KubernetesWorkerReadinessHTTP', collector)
    observation = collect(case, window)
    assert len(created) == 1 and observation['snapshot']['sampling_complete']
    assert observation['snapshot']['requests'][1]['outcome'] == 200
    assert observation['collection_gap'] and observation['gaps'] == ['HTTP_REPORT_UNAVAILABLE']
    assert not observation['report_validated'] and PRIVATE not in json.dumps(observation)


@pytest.mark.parametrize('fault', ['missing_window', 'expired_window', 'long_window', 'hello', 'source_error', 'wrong_common'])
def test_HTTP_input_and_source_gate_close_before_public_collector_or_IO(case, monkeypatch, fault):
    world, created, window = configure(case, monkeypatch)
    if fault == 'missing_window': window['http_validity_start'] = None
    elif fault == 'expired_window': window['http_validity_end'] = (utcnow() - timedelta(seconds=1)).isoformat()
    elif fault == 'long_window': window['http_validity_end'] = (utcnow() + timedelta(minutes=20)).isoformat()
    elif fault == 'hello': case['manifest']['request']['messages'][0]['content'] = PRIVATE
    elif fault == 'source_error': monkeypatch.setattr(helper, 'verify_source', lambda: (_ for _ in ()).throw(ValueError(PRIVATE)))
    else: monkeypatch.setattr(helper, 'verify_source', lambda: {'reviewed_common_commit': '0' * 40})
    if fault in {'source_error', 'wrong_common'}:
        result = collect(case, window)
        assert result['snapshot'] is None and result['gaps'] == ['HTTP_SOURCE_UNAVAILABLE']
        assert result['sample_attempted'] is False and PRIVATE not in json.dumps(result)
    else:
        with pytest.raises(ValueError, match='LIF07_HTTP_'): collect(case, window)
    assert created == [] and not world.calls and not world.execs


@pytest.mark.parametrize('other', ['execute', 'collect_native', 'import_capture', '_example_capture', 'window_without_mode'])
def test_HTTP_mode_is_explicitly_exclusive_before_collection(case, monkeypatch, other):
    world, created, window = configure(case, monkeypatch)
    options = {'collect_http': True, **window}
    if other == 'window_without_mode': options['collect_http'] = False
    else: options[other] = 'unused.json' if other == 'import_capture' else (lambda *a: None) if other == '_example_capture' else True
    with pytest.raises(ValueError, match='MODES_EXCLUSIVE|HTTP_WINDOW_REQUIRES_HTTP_MODE'):
        tool.run(**case['args'], **options)
    assert not created and not world.calls and not case['args']['output'].exists()


def test_public_HTTP_to_actual_CLI_manual_Store_exact_ref_restore(case, monkeypatch, capsys):
    world, created, window = configure(case, monkeypatch, 'cleanup-after-canary')
    before = tool._inventory(case['source'].root)
    argv = ['--collect-http']
    for key, value in {**case['args'], **window}.items():
        argv += ['--' + ('plan' if key == 'plan_id' else key.replace('_', '-')), str(value)]
    tool.main(argv); stdout = capsys.readouterr().out; summary = json.loads(stdout)
    output = case['args']['output']; record = json.loads((output / 'worker-readiness.json').read_text())
    assert len(created) == 1 and len(world.execs) == summary['requests'] == summary['http_requests_attempted'] == 2
    assert summary['exact_refs_restored'] and summary['verdicts'] == {key: 'UNKNOWN' for key in ('security', 'functional', 'performance')}
    assert summary['capture_origin'] == 'EXAMPLE' and summary['actual_target_executed'] is False
    assert record['http_observation']['snapshot']['cleanup_errors'] and record['http_observation']['snapshot']['requests'][1]['outcome'] == 200
    entry, = yaml.safe_load((output / 'manual-evidence.yaml').read_text())['checks']
    assert entry['normal_control']['result'] == 'not_attempted' and all(not row['attempted'] for row in entry['challenges'])
    assert 'HTTP 결과' in entry['basis'] and 'HTTP 요청·Pod 변경·지연 0' not in json.dumps(entry, ensure_ascii=False)
    assert tool._inventory(case['source'].root) == before
    assert PRIVATE not in json.dumps(record) and A_PRIVATE not in json.dumps(record)
    # 명시 보존 회차만 fresh 외부 경로로 옮긴다. 기본 pytest는 자체 임시 정리를 유지한다.
    durable = os.environ.get('BYUL_LIF07_HTTP_CONTRACT_EVIDENCE')
    if durable:
        dest = Path(durable); assert not dest.exists(); shutil.copytree(output, dest)
        (dest / 'actual-argv.json').write_text(json.dumps(argv, ensure_ascii=False, indent=2))
        (dest / 'actual-stdout.json').write_text(stdout)


def test_HTTP_post_collection_input_mutation_preserves_safe_facts_without_EVD(case, monkeypatch):
    world, _, window = configure(case, monkeypatch)
    original = public.KubernetesWorkerReadinessHTTP
    def collector(*args, **kwargs):
        value = original(*args, **kwargs); report = value.report
        def mutate_after_report():
            packet = report(); case['args']['manifest'].write_text('{}'); return packet
        value.report = mutate_after_report
        return value
    monkeypatch.setattr(public, 'KubernetesWorkerReadinessHTTP', collector)
    with pytest.raises(tool.EvidencePreservationError) as error:
        tool.run(**case['args'], collect_http=True, **window)
    assert len(world.execs) == 3 and error.value.safe_facts['http_requests_attempted'] == 3
    assert error.value.safe_facts['http_observation']['snapshot']['requests'][1]['outcome'] == 200
    assert 'ORIGINAL_INPUT_CHANGED' in error.value.recovery['issues']
    assert not case['args']['output'].exists() and PRIVATE not in json.dumps(error.value.safe_facts)


@pytest.mark.parametrize('sample_attempted', [False, True])
def test_HTTP_no_valid_packet_keeps_target_IO_unknown_unless_not_invoked(case, sample_attempted):
    record = tool.project(tool._empty(case['env'], case['target'], case['prepared'], case['manifest']),
        case['env'], case['target'], case['prepared'], case['manifest'])
    observation = {'snapshot': None, 'sample_attempted': sample_attempted, 'collection_gap': True,
        'report_attempted': sample_attempted, 'report_validated': False, 'gaps': ['HTTP_SAMPLE_UNAVAILABLE']}
    value = tool._attach_http(record, observation)
    assert value['requests'] is (None if sample_attempted else 0)
    assert value['http_requests_attempted'] is value['target_http_requests'] is value['requests']
    assert value['http_collection_attempted'] is sample_attempted
    assert value['target_io_attempted'] is (None if sample_attempted else False)
    assert value['cleanup']['owned_host_complete'] is None


@pytest.mark.parametrize('api_collected,attempted', [(True, False), (False, False), (False, True)])
def test_HTTP_valid_partial_counts_and_API_read_are_independently_preserved(case, api_collected, attempted):
    record = tool.project(tool._empty(case['env'], case['target'], case['prepared'], case['manifest']),
        case['env'], case['target'], case['prepared'], case['manifest'])
    packet = {'requests': [{'attempted': attempted}], 'api_readiness': [{'collected': api_collected}],
        'started_at': record['started_at'], 'finished_at': record['finished_at'], 'data_origin': 'EXAMPLE'}
    value = tool._attach_http(record, {'snapshot': packet, 'sample_attempted': True, 'gaps': []})
    assert value['requests'] == value['http_requests_attempted'] == value['target_http_requests'] == int(attempted)
    assert value['target_io_attempted'] is (True if attempted or api_collected else None)


@pytest.mark.parametrize('failure', ['artifact', 'original_input'])
def test_HTTP_unvalidated_after_sample_retains_null_in_storage_and_recovery(case, monkeypatch, failure):
    world, _, window = configure(case, monkeypatch)
    original = public.KubernetesWorkerReadinessHTTP
    def collector(*args, **kwargs):
        value = original(*args, **kwargs); sample, report = value.sample, value.report
        def invalid_sample(*args, **kwargs):
            packet = sample(*args, **kwargs); packet['body'] = PRIVATE; return packet
        def invalid_report():
            packet = report(); packet['body'] = PRIVATE
            if failure == 'original_input': case['args']['manifest'].write_text('{}')
            return packet
        value.sample, value.report = invalid_sample, invalid_report
        return value
    monkeypatch.setattr(public, 'KubernetesWorkerReadinessHTTP', collector)
    if failure == 'artifact':
        write = tool._write
        def unavailable(path, value):
            if path.name == 'worker-readiness.json': raise OSError(PRIVATE)
            return write(path, value)
        monkeypatch.setattr(tool, '_write', unavailable)
        summary = tool.run(**case['args'], collect_http=True, **window)
        facts = summary['safe_facts']
        assert summary['requests'] is summary['http_requests_attempted'] is None
        entry, = yaml.safe_load((case['args']['output'] / 'manual-evidence.yaml').read_text())['checks']
        note = entry['observation_notes'][0]['answer']
        assert '선정 HTTP 시도=미수집' in note and '선정 HTTP 시도=None' not in note
        embedded = json.loads(note.split('LIF07_SAFE_FACTS=', 1)[1])
        assert embedded['requests'] is embedded['http_requests_attempted'] is embedded['target_http_requests'] is None
        assert summary['exact_refs_restored']
    else:
        with pytest.raises(tool.EvidencePreservationError) as error:
            tool.run(**case['args'], collect_http=True, **window)
        facts = error.value.safe_facts
        assert 'ORIGINAL_INPUT_CHANGED' in error.value.recovery['issues']
        assert not case['args']['output'].exists()
    assert len(world.execs) == 3 and facts['http_observation']['snapshot'] is None
    assert facts['requests'] is facts['http_requests_attempted'] is facts['target_http_requests'] is None
    assert facts['target_io_attempted'] is None and PRIVATE not in json.dumps(facts)


@pytest.mark.parametrize('reviewed,mode', [
    (COMMON, 'normal'),
    ('df6255e96a877ebfbe185230ed87320aa84d2c30', 'cleanup-after-canary'),
    ('062af9d1e0bc76a9c6f180af40e2b9fe52ffeb89', 'normal'),
    ('9d8987a5a306ffce28fcb735e012c400589303f2', 'normal'),
    (PRIVATE, 'normal'), (None, 'normal'), ([], 'normal'),
])
def test_HTTP_approved_feature_scope_preserves_selected_common_and_closes_others(case, monkeypatch, reviewed, mode):
    """source gate의 확정 proof/set만 EXAMPLE 주입하고 실제 공개 sample/report/validator를 소비한다."""
    world, created, window = configure(case, monkeypatch, mode)
    approved = frozenset({COMMON, 'df6255e96a877ebfbe185230ed87320aa84d2c30'})
    monkeypatch.setattr(helper, 'HTTP_APPROVED_REVIEWED_COMMON_COMMITS', approved, raising=False)
    monkeypatch.setattr(helper, 'verify_source', lambda: {'reviewed_common_commit': reviewed})
    observation = collect(case, window)
    if type(reviewed) is str and reviewed in approved:
        assert observation['source_validation'] == {'reviewed_common_commit': reviewed,
            'runtime_hash_approval': False, 'producer_authenticated': False}
        packet = observation['snapshot']; assert len(created) == 1 and observation['report_validated']
        assert packet['requests'][0]['outcome'] == 200
        assert all(packet[key] is False for key in public.FLAGS)
        if mode == 'cleanup-after-canary':
            assert len(world.execs) == 2 and packet['cleanup_errors'] and observation['collection_gap']
            assert packet['requests'][1]['outcome'] == 200
        else:
            assert len(world.execs) == 3 and packet['sampling_complete']
    else:
        assert observation['source_validation'] is observation['snapshot'] is None
        assert observation['gaps'] == ['HTTP_SOURCE_UNAVAILABLE'] and not observation['sample_attempted']
        assert created == [] and not world.calls and not world.execs
    assert PRIVATE not in json.dumps(observation) and A_PRIVATE not in json.dumps(observation)
