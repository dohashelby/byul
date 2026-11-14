"""두 묶음 원 표본의 귀속·완결성·독립 FAIL 보존. 모든 자료는 EXAMPLE이다."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.collectors import engine_callbacks
from guard.executors import cache_timing_group as group, engine_callback_projection as projection, engine_sensor
from guard.models import canonical_json, sha256_hex, DataOrigin
from tests.builders import baseline_pack
from tests.test_gpu_a_run_inputs import build as input_fixture
from guard.collectors.file import load_environment
from tools import gpu_a_req17_cohort as cohort, gpu_a_run_inputs as inputs

POLICY = {'cache_timing_min_samples': 200, 'cache_timing_max_auc_percent': 55}


def packet(tmp_path, *, prompt_chars=100):
    _, cohorts = cohort.write_split(tmp_path / 'cases', 26111401, prompt_chars=prompt_chars)
    lists = cohort.load_split(tmp_path / 'cases/group.json')
    base = {'schema': projection.SCHEMA, 'session': 'a' * 32,
            'target_sha256': 'b' * 64, 'environment_ref': {'id': 'EXAMPLE', 'sha256': 'c' * 64}}
    parts = [{**base, 'cases': cases} for cases in lists]
    manifest = {k: v for k, v in base.items() if k != 'schema'}
    manifest.update(schema=group.SCHEMA, parts=[])
    for index, part in enumerate(parts, 1):
        raw = canonical_json(part)
        (tmp_path / f'part-{index}.json').write_bytes(raw)
        manifest['parts'].append({'index': index, 'file': f'part-{index}.json',
            'file_sha256': sha256_hex(raw), 'manifest_sha256': sha256_hex(raw)})
    scope = {'runtime_binding': {'engine': {'container_id': 'EXAMPLE'}},
             'environment_ref': base['environment_ref'], 'policy_values_sha256': sha256_hex(canonical_json(POLICY)),
             'target_sha256': base['target_sha256'], 'prepared_sha256': 'e' * 64,
             'group_sha256': sha256_hex(canonical_json(manifest))}
    return manifest, parts, scope


def capture(index, parts, scope, *, present_hit=False, offset=0, cross=None):
    start = datetime(2026, 11, 14, tzinfo=timezone.utc) + timedelta(seconds=index)
    samples = []
    for case in parts[index - 1]['cases']:
        role = case['role']
        elapsed = 1 if role == 'control-present' else 10
        if role == 'present' and cross is not None:
            elapsed = cross[0]
        if role == 'absent' and cross is not None:
            elapsed = cross[1]
        samples.append({'case_id': case['case_id'], 'role': role,
            'request_sha256': sha256_hex(f'{index}-{case["case_id"]}'.encode()),
            'elapsed_ms': elapsed + offset,
            'clock': {'outcome': 200, 'capture_complete': True, 'elapsed_ms': elapsed + offset},
            'native_tokens': [16 if role == 'control-present' or role == 'present' and present_hit else 0]})
    return group.seal_capture({'schema': group.CAPTURE_SCHEMA, 'index': index,
        'part_sha256': sha256_hex(canonical_json(parts[index - 1])), 'scope': deepcopy(scope),
        'runtime_binding': scope['runtime_binding'], 'actors': ['d' * 64],
        'started_at': start.isoformat(), 'finished_at': (start + timedelta(milliseconds=100)).isoformat(),
        'data_origin': 'EXAMPLE', 'capture_complete': True, 'normal_control_succeeded': True, 'samples': samples})


def test_split_files_keep_512_bound_and_200_per_role(tmp_path):
    manifest, parts, scope = packet(tmp_path, prompt_chars=1500)
    assert group.load_parts(manifest, tmp_path) == parts
    assert all(len(p['cases']) == 504 <= projection.MAX_CASES == 512 for p in parts)
    assert Counter(c['role'] for p in parts for c in p['cases']) == {
        'prime': 208, **dict.fromkeys(group.ROLES, 200)}
    assert all((tmp_path / f'part-{i}.json').stat().st_size > 262144 for i in (1, 2))
    with pytest.raises(ValueError):
        projection.validate_cases('REQ-17', parts[0]['cases'] + parts[1]['cases'])
    result = group.aggregate(manifest, parts, [capture(i, parts, scope) for i in (1, 2)], POLICY, scope)
    assert result['samples'] == dict.fromkeys(group.ROLES, 200)
    assert result['control_auc_percent'] == 100 and result['cross_auc_percent'] == 50
    assert result['violation'] is False and result['full_scope'] is True
    assert result['producer_authenticated'] is False and result['data_origin'] == 'EXAMPLE'
    assert projection.project_cache_timing(parts[0]['cases'], {}, {}, POLICY)['violation'] is None


def test_uses_pooled_raw_samples_instead_of_mean_of_aucs(tmp_path):
    manifest, parts, scope = packet(tmp_path)
    # 두 묶음의 분포 위치가 다르다. 개별 AUC100·0의 평균50과 달리 원 표본 집계는75다.
    first = capture(1, parts, scope, present_hit=True, cross=(1, 20))
    second = capture(2, parts, scope, present_hit=True, cross=(11, 10))
    result = group.aggregate(manifest, parts, [first, second], POLICY, scope)
    assert result['cross_auc_percent'] == 75
    assert result['violation'] is True
    assert 75 != (100 + 0) / 2


@pytest.mark.parametrize('damage', ['scope', 'model', 'generation', 'actors', 'time', 'origin',
    'duplicate_request', 'missing_sample', 'negative', 'boolean', 'nan', 'infinite', 'clock_missing', 'native_missing',
    'valid_latency_tamper', 'bad_clock_basis'])
def test_invalid_or_incomplete_source_never_becomes_clean(tmp_path, damage):
    manifest, parts, scope = packet(tmp_path)
    captures = [capture(i, parts, scope) for i in (1, 2)]
    value = captures[1].value()
    sample = next(s for s in value['samples'] if s['role'] == 'present')
    if damage == 'scope': value['scope']['policy_values_sha256'] = 'f' * 64
    elif damage == 'model': value['scope']['environment_ref'] = {'sha256': 'f' * 64}
    elif damage == 'generation': value['runtime_binding'] = {'engine': {'container_id': 'changed'}}
    elif damage == 'actors': value['actors'] = ['f' * 64]
    elif damage == 'time': value['started_at'] = captures[0].value()['started_at']
    elif damage == 'origin': value['data_origin'] = 'MEASURED'
    elif damage == 'duplicate_request': sample['request_sha256'] = captures[0].value()['samples'][0]['request_sha256']
    elif damage == 'missing_sample': value['samples'].pop()
    elif damage == 'native_missing': sample['native_tokens'] = []
    elif damage == 'clock_missing': sample['elapsed_ms'] = None
    elif damage == 'valid_latency_tamper': sample['elapsed_ms'] += 1
    elif damage == 'bad_clock_basis': sample['clock']['timing_basis'] = 'unknown'
    elif damage == 'negative': sample['elapsed_ms'] = -1
    elif damage == 'boolean': sample['elapsed_ms'] = True
    elif damage == 'nan': sample['elapsed_ms'] = float('nan')
    elif damage == 'infinite': sample['elapsed_ms'] = float('inf')
    if damage in ('nan', 'infinite'):
        with pytest.raises(ValueError): group.seal_capture(value)
        return
    if damage in ('negative', 'boolean'):
        with pytest.raises(ValueError): group.seal_capture(value)
        raw = canonical_json(value)
        captures[1] = group.Capture(raw, sha256_hex(raw))
        assert group.aggregate(manifest, parts, captures, POLICY, scope)['violation'] is None
        return
    captures[1] = group.seal_capture(value)
    result = group.aggregate(manifest, parts, captures, POLICY, scope)
    assert result['violation'] is None and not result['full_scope']
    assert result['data_origin'] == 'EXAMPLE'


def test_late_gap_does_not_hide_confirmed_group_violation(tmp_path):
    manifest, parts, scope = packet(tmp_path)
    captures = [capture(i, parts, scope, present_hit=True, cross=(1, 10)) for i in (1, 2)]
    value = captures[1].value(); value['capture_complete'] = False
    captures[1] = group.seal_capture(value)
    result = group.aggregate(manifest, parts, captures, POLICY, scope)
    assert result['violation'] is True and not result['full_scope']
    assert result['missing'] == ['timing_group_collection_incomplete']


def test_independent_part_violation_survives_missing_other_part(tmp_path):
    manifest, parts, scope = packet(tmp_path)
    example_policy = {**POLICY, 'cache_timing_min_samples': 100}
    scope['policy_values_sha256'] = sha256_hex(canonical_json(example_policy))
    result = group.aggregate(manifest, parts,
        [capture(1, parts, scope, present_hit=True, cross=(1, 10))], example_policy, scope)
    assert result['violation'] is True and not result['full_scope']
    assert 'timing_group_parts_incomplete' in result['missing']
    assert POLICY['cache_timing_min_samples'] == 200


def test_changed_capture_bytes_and_duplicate_cohort_are_rejected(tmp_path):
    manifest, parts, scope = packet(tmp_path)
    first = capture(1, parts, scope)
    bad = group.Capture(first.raw.replace(b'EXAMPLE', b'CHANGED'), first.sha256)
    assert group.aggregate(manifest, parts, [bad, capture(2, parts, scope)], POLICY, scope)['violation'] is None
    with pytest.raises(ValueError, match='DUPLICATE_COHORT'):
        group.validate_case_parts([parts[0]['cases'], parts[0]['cases']])
    (tmp_path / 'part-1.json').write_bytes(b'{}')
    with pytest.raises(ValueError, match='FILE_CHANGED'):
        group.load_parts(manifest, tmp_path)


@pytest.mark.parametrize('damage', ['path', 'symlink', 'extra_part', 'part_identity', 'part_size'])
def test_bad_group_is_rejected_before_collection(tmp_path, damage):
    manifest, parts, scope = packet(tmp_path)
    if damage == 'path': manifest['parts'][0]['file'] = '../part-1.json'
    elif damage == 'symlink':
        p = tmp_path / 'part-1.json'; p.rename(tmp_path / 'saved.json'); p.symlink_to(tmp_path / 'saved.json')
    elif damage == 'extra_part': manifest['parts'].append(deepcopy(manifest['parts'][0]))
    elif damage == 'part_identity':
        parts[0]['session'] = 'f' * 32
        raw = canonical_json(parts[0]); (tmp_path / 'part-1.json').write_bytes(raw)
        manifest['parts'][0].update(file_sha256=sha256_hex(raw), manifest_sha256=sha256_hex(raw))
    else:
        raw = b' ' * (group.MAX_PART_BYTES + 1)
        (tmp_path / 'part-1.json').write_bytes(raw); manifest['parts'][0]['file_sha256'] = sha256_hex(raw)
    with pytest.raises(ValueError): group.load_parts(manifest, tmp_path)


class TimingTransport:
    process_observation_origin = 'EXAMPLE'
    endpoint = SimpleNamespace(scheme='http', netloc='engine:8000')
    api_model = 'EXAMPLE-model'

    def __init__(self, secret, cohorts, binding, *, hit=False, changed_actor=False, fail_second=False, fail_first=False):
        self.secret, self.cohorts, self.binding = secret, cohorts, deepcopy(binding)
        self.hit, self.changed_actor = hit, changed_actor
        self.fail_second, self.fail_first = fail_second, fail_first
        self.events, self.part, self.calls = [], 0, []

    def begin_callback_trial(self, session):
        self.events = []; self.part += 1
        if self.fail_second and self.part == 2:
            raise OSError('EXAMPLE 수집 중단')

    def __call__(self, network, requests, timeout):
        request, = requests
        self.calls.append(request['id'])
        if self.fail_first:
            from guard.runtime.engine_http import KubernetesBindingError
            raise KubernetesBindingError()
        barrier = request['id'] == 'cb-9999'
        case = None if barrier else self.cohorts[self.part - 1][int(request['id'].split('-')[1])]
        role = 'prime' if barrier else case['role']
        tokens = 16 if role == 'control-present' or role == 'present' and self.hit else 0
        self.events.append({'point': projection.COMPUTED, 'phase': 'RETURN',
            'entry_qualified_before': True, 'entry_qualified_after': True,
            'process_sha256': ('e' if self.changed_actor and self.part == 2 else 'd') * 64,
            'fields': {'request_sha256': projection.tag_digest(self.secret, request['headers']['X-Request-Id']),
                       'num_computed_tokens': tokens}})
        elapsed = 1 if role == 'control-present' or role == 'present' and self.hit else 10
        return [{'outcome': 200, 'capture_complete': True, 'elapsed_ms': elapsed, 'body': '{}'}]

    def engine_callback_events(self, session):
        return {'events': self.events, 'truncated': False}

    def report(self):
        return {'binding': deepcopy(self.binding)}


def run_group(tmp_path, *, hit=False, changed_actor=False, fail_second=False, fail_first=False, policy_values=None):
    input_fixture(tmp_path, 'v1-callback')
    env = load_environment(tmp_path / 'env.json')
    target = json.loads((tmp_path / 'target.json').read_bytes())
    cohort.write_split(tmp_path / 'cohorts', 26111401)
    cohorts = cohort.load_split(tmp_path / 'cohorts/group.json')
    draft_policy = {'pack': {'id': 'PACK-BASELINE', 'revision': 34},
                    'values': {'REQ-17': POLICY if policy_values is None else policy_values}}
    manifest, policy, config, files = inputs.timing_group_inputs(env, target, tmp_path / 'prepared', cohorts, draft_policy, root=tmp_path)
    for name, raw in files.items(): (tmp_path / name).write_bytes(raw)
    (tmp_path / 'manifest.json').write_bytes(canonical_json(manifest))
    executor = engine_sensor.EngineSensorExecutor({'target': str(tmp_path / 'target.json'),
        'prepared': str(tmp_path / 'prepared/manifest.json'), 'manifest': str(tmp_path / 'manifest.json'),
        'max_duration_seconds': 300}, implementation='kubernetes')
    transport = TimingTransport(engine_callbacks.load_secret(tmp_path / 'prepared'), cohorts, target['kubernetes'],
                                hit=hit, changed_actor=changed_actor, fail_second=fail_second, fail_first=fail_first)
    executor.transport = transport
    check = baseline_pack().checks['REQ-17'].model_copy(deep=True)
    check.executor = 'engine_sensor'; check.collection_profile = engine_sensor.PROFILES['REQ-17']
    result = executor.run(check, env, policy['values']['REQ-17'], None, tmp_path / 'work')
    return result, transport


@pytest.mark.parametrize('hit,changed_actor,expected', [(False, False, False), (True, False, True), (False, True, None)])
def test_actual_executor_collects_two_bound_parts_and_preserves_example_origin(tmp_path, hit, changed_actor, expected):
    draft, transport = run_group(tmp_path, hit=hit, changed_actor=changed_actor)
    assert len(transport.calls) == 1010
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.observations['engine_sensor_violation'].value is expected
    assert draft.status.value == ('ABORTED' if expected is None else 'COMPLETED')
    assert len({a.name for a in draft.artifacts}) == len(draft.artifacts)
    for artifact in draft.artifacts:
        if artifact.name.startswith('part-') and artifact.path.name == 'engine-sensor.json':
            value = json.loads(artifact.path.read_bytes())
            assert len(value['timing_samples']['samples']) == 504
            assert not any(k in value['timing_samples'] for k in ('secret', 'prompt', 'token'))


def test_cli_assembles_exact_part_bytes_without_running_engine(tmp_path):
    input_fixture(tmp_path, 'v1-callback')
    cohort.main(['--seed', '26111401', '--split', '--out', str(tmp_path / 'cohorts')])
    inputs.main(['check', '--check', 'REQ-17', '--env', str(tmp_path / 'env.json'),
        '--target', str(tmp_path / 'target.json'), '--prepared', str(tmp_path / 'prepared'),
        '--cases', str(tmp_path / 'cohorts/group.json'), '--out', str(tmp_path / 'assembled')])
    manifest = json.loads((tmp_path / 'assembled/manifest.json').read_bytes())
    assert len(group.load_parts(manifest, tmp_path / 'assembled')) == 2
    with pytest.raises(ValueError, match='NEW_OUTPUT_REQUIRED'):
        inputs.main(['check', '--check', 'REQ-17', '--env', str(tmp_path / 'env.json'),
            '--target', str(tmp_path / 'target.json'), '--prepared', str(tmp_path / 'prepared'),
            '--cases', str(tmp_path / 'cohorts/group.json'), '--out', str(tmp_path / 'assembled')])


def test_late_artifact_failure_keeps_confirmed_violation_and_aborted_status(tmp_path, monkeypatch):
    original = Path.open
    def fail(path, *args, **kwargs):
        if path.name == 'engine-sensor.json' and path.parent.name == 'part-2' and args and args[0] == 'xb':
            raise OSError('EXAMPLE 기록 실패')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail)
    draft, _ = run_group(tmp_path, hit=True)
    assert draft.observations['engine_sensor_violation'].value is True
    assert not draft.observations['engine_sensor_full_scope'].value
    assert draft.status.value == 'ABORTED' and draft.data_origin != DataOrigin.MEASURED
    assert 'ENGINE_SENSOR_TIMING_PART_ARTIFACT_INCOMPLETE' in draft.cleanup_errors
    from guard.judges.rule_table import RuleTableJudge
    from tests.builders import evidence
    evd = evidence(status=draft.status, check_id='REQ-17', observations={
        key: value.model_dump() for key, value in draft.observations.items()})
    result = RuleTableJudge().judge(baseline_pack().checks['REQ-17'], evd,
        {'fail_when_any': [{'obs': 'engine_sensor_violation', 'op': 'eq', 'value': True}]}, {})
    assert result.verdicts['security'].value == 'FAIL'


def test_policy_cannot_change_after_captures_were_bound(tmp_path):
    manifest, parts, scope = packet(tmp_path)
    with pytest.raises(ValueError, match='SCOPE_INVALID'):
        group.aggregate(manifest, parts, [capture(i, parts, scope) for i in (1, 2)],
                        {**POLICY, 'cache_timing_max_auc_percent': 100}, scope)


def test_independent_violation_survives_second_collection_failure(tmp_path):
    draft, transport = run_group(tmp_path, hit=True, fail_second=True,
        policy_values={**POLICY, 'cache_timing_min_samples': 100})  # 단독 FAIL 경로용 EXAMPLE 정책
    assert len(transport.calls) == 505
    assert draft.observations['engine_sensor_violation'].value is True
    assert draft.status.value == 'ABORTED' and draft.data_origin == DataOrigin.EXAMPLE
    assert 'ENGINE_SENSOR_TIMING_PART_COLLECTION_UNAVAILABLE' in draft.cleanup_errors
    assert POLICY['cache_timing_min_samples'] == 200


def test_incomplete_first_collection_does_not_start_second(tmp_path):
    draft, transport = run_group(tmp_path, fail_first=True)
    assert transport.part == 1
    assert draft.status.value == 'ABORTED' and draft.observations['engine_sensor_violation'].value is None
    assert draft.data_origin != DataOrigin.MEASURED
