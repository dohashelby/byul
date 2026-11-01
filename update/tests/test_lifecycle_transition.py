"""Direct transition contracts; CPU fixtures never claim deployment/GPU proof."""
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
from pathlib import Path
import threading

import pytest

from guard.executors import lifecycle_transition as subject
from guard.executors.lifecycle_session import request_index
from guard.judges.rule_table import RuleTableJudge
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from tests.builders import T0, environment, evidence
from tests.judging import REQ01
from tests.test_kubernetes_request_binding import target as http_target, env as http_env
from tests.test_kubernetes_sampling import World as PodWorld, PRIVATE
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory, CONTEXT
from tests.test_lifecycle_session import group, collect as collect_existing
from tests.test_kubernetes_lifecycle import target as native_target


def policy():
    return {'transition_inputs_sha256': 'a' * 64,
        'transition_sample_at': {p: (T0 + timedelta(seconds=n)).isoformat()
            for p, n in zip(subject.PHASES, (1, 3, 5, 7))},
        'transition_phase_generation': dict(zip(subject.PHASES, ('old', 'new', 'new', 'restored')))}


def check(profile='lif05-stopped-redeploy-v1'):
    return SimpleNamespace(id=subject.PROFILES[profile], collection_profile=profile)


def recording(request='d' * 64, configurations=None, *, owner='a', ambiguous=False):
    """Feed the real producer's raw record structure, not a handmade index."""
    configurations = configurations or [('1' * 64, '2' * 64)]
    native = {'point': 'InputProcessor.assign_request_id', 'phase': 'RETURN',
        'metadata': {'external_request_sha256': 'e' * 64, 'asgi_request_sha256': request},
        'requests': [{'request_sha256': 'f' * 64}]}
    rows = [{'event': 'START', 'seq': 0, 'payload': {'context': CONTEXT}},
        {'event': 'NATIVE', 'seq': 1, 'payload': native}]
    for i, (model, revision) in enumerate(configurations, 2):
        rows.append({'event': 'NATIVE', 'seq': i, 'payload': {
            'point': 'GPUModelRunner._model_forward', 'phase': 'CALL',
            'metadata': {'model_selection': {'configured_model_sha256': model, 'configured_revision_sha256': revision}},
            'requests': [{'request_sha256': 'f' * 64}]}})
    if ambiguous:
        other = deepcopy(native); other['requests'][0]['request_sha256'] = '9' * 64
        rows.append({'event': 'NATIVE', 'seq': len(rows), 'payload': other})
    return {'capture': {'records': rows, 'provenance': {'owner': {'container_id': owner * 64},
        'owner_binding_verified': True}, 'process_context_verified': True},
        'session_owner_match': True, 'listed_file_identity_match': True}


def packet(request='d' * 64, configurations=None, **kwargs):
    recordings = [recording(request, configurations, **kwargs)]
    return {'session': '1' * 32, 'recordings': recordings, 'request_index': request_index(recordings),
        'recorded_files_covered': True}


def sample(native, phase='during', generation='old'):
    return {'phase': phase, 'generation': generation, 'native': native}


def judge(projection):
    return RuleTableJudge().judge(REQ01, evidence(observations={
        'conflicts': {'value': projection['request_configuration_conflicts'], 'collected': True},
        'complete': {'value': False, 'collected': True}}),
        {'fail_when_any': [{'obs': 'conflicts', 'op': 'nonempty'}],
            'pass_when_all': [{'obs': 'complete', 'op': 'eq', 'value': True}]}, {})


def test_real_producer_index_exact_join_and_deduplicates_live_samples():
    native = packet()
    assert next(iter(native['request_index']['domains'].values()))['assignments'][0]['asgi_request_sha256'] == 'd' * 64
    result = subject.project([sample(native), sample(native, 'after')], [{'case_id': 'inflight', 'request_id_sha256': 'd' * 64}])
    assert len(result['request_generation_bindings']) == 1
    assert len(result['request_configuration_facts']) == 1
    assert result['request_configuration_conflicts'] == []
    assert result['actual_weights_cache_consumption_verified'] is False
    assert judge(result).verdicts['security'] == 'UNKNOWN'


def test_independent_conflict_fact_survives_later_missing_capture():
    native = packet(configurations=[('1' * 64, '2' * 64), ('3' * 64, '4' * 64)])
    result = subject.project([sample(native), sample(None, 'recovered')], [{'case_id': 'inflight', 'request_id_sha256': 'd' * 64}])
    assert len(result['request_configuration_conflicts']) == 1
    assert 'recovered:old:native_unavailable' in result['missing']
    assert judge(result).verdicts['security'] == 'FAIL'


@pytest.mark.parametrize('change', ['session', 'owner', 'generation'])
def test_same_hash_nearby_time_does_not_join_separate_session_owner_or_target(change):
    old = packet(); new = packet(configurations=[('3' * 64, '4' * 64)], owner='b' if change == 'owner' else 'a')
    if change == 'session': new['session'] = '2' * 32
    result = subject.project([sample(old), sample(new, generation='new' if change == 'generation' else 'old')],
        [{'case_id': 'inflight', 'request_id_sha256': 'd' * 64}])
    assert len(result['request_generation_bindings']) == 2
    assert result['request_configuration_conflicts'] == []


@pytest.mark.parametrize('gap', ['ambiguous', 'owner', 'file', 'process', 'other-id'])
def test_ambiguous_or_unbound_record_cannot_supply_join(gap):
    native = packet(ambiguous=gap == 'ambiguous')
    if gap == 'owner': native['recordings'][0]['session_owner_match'] = False
    if gap == 'file': native['recordings'][0]['listed_file_identity_match'] = False
    if gap == 'process': native['recordings'][0]['capture']['process_context_verified'] = False
    result = subject.project([sample(native)], [{'case_id': 'inflight', 'request_id_sha256': ('c' if gap == 'other-id' else 'd') * 64}])
    assert not result['request_generation_bindings'] and not result['request_configuration_facts']
    assert result['missing'] == ['inflight:native_request_generation_unlinked']


def test_existing_capture_producer_unsupported_model_branch_not_fabricated(group):
    native = collect_existing(group)
    result = subject.project([sample(native)], [{'case_id': 'inflight', 'request_id_sha256': 'd' * 64}])
    # Legacy producer has no header-context mapping. Fingerprint proximity is insufficient.
    assert not result['request_generation_bindings'] and not result['request_configuration_facts']
    assert 'inflight:native_request_generation_unlinked' in result['missing']


class Clock:
    def __init__(self): self.value = T0; self.lock = threading.Lock()
    def now(self):
        with self.lock: return self.value
    def wait(self, seconds):
        with self.lock: self.value += timedelta(seconds=seconds)


def harness(monkeypatch, *, failure=None):
    clock = Clock(); entered = threading.Event(); release = threading.Event(); log = []; ids = {}
    env = environment()
    generations = {key: subject.Generation(key, env, {'lab': True}, {}, {'session': str(i) * 32})
        for i, key in enumerate(('old', 'new', 'restored'), 1)}
    monkeypatch.setattr(subject, 'load_generations', lambda *a: (generations, []))
    executor = subject.LifecycleTransitionExecutor({}, runner=lambda *a: None, clock=clock.now, wait=clock.wait)
    def reader(generation):
        log.append(('reader', generation.key))
        def seal():
            log.append(('seal', generation.key))
            if failure == 'cleanup': raise OSError(PRIVATE)
            return {'sealed': True}
        return SimpleNamespace(seal_session=seal, report=lambda: {})
    def probe(generation, phase, request, timeout):
        log.append(('probe', phase)); ids[phase] = sha256_hex(request.encode())
        if phase == 'inflight':
            entered.set(); assert release.wait(3)
        if failure == 'recovery' and phase == 'recovered': raise OSError(PRIVATE)
        return {'normal_shape_verified': True, 'transport': {'cleanup_errors': []}}
    def capture(generation):
        log.append(('capture', generation.key))
        if 'inflight' in ids:
            if generation.key == 'old' and clock.now() < T0 + timedelta(seconds=3): assert entered.wait(3)
            if clock.now() >= T0 + timedelta(seconds=5): release.set()
            if failure == 'replacement' and generation.key == 'old' and clock.now() >= T0 + timedelta(seconds=5):
                raise OSError(PRIVATE)
            return packet(ids['inflight'], [('1' * 64, '2' * 64), ('3' * 64, '4' * 64)])
        return packet()
    monkeypatch.setattr(executor, '_reader', reader); monkeypatch.setattr(executor, '_probe', probe)
    monkeypatch.setattr(executor, '_capture', capture)
    return executor, env, log


@pytest.mark.parametrize('profile', list(subject.PROFILES))
def test_normal_transition_ongoing_request_and_recovery_all_profiles(monkeypatch, tmp_path, profile):
    executor, env, log = harness(monkeypatch)
    draft = executor.run(check(profile), env, policy(), None, tmp_path)
    assert draft.data_origin == DataOrigin.EXAMPLE
    assert draft.normal_control.succeeded is True
    assert len(draft.observations['transition_trials'].value) == 5
    assert log.index(('capture', 'old')) < log.index(('probe', 'before')) < log.index(('probe', 'inflight'))
    assert log.index(('probe', 'recovered')) < log.index(('seal', 'old'))
    assert draft.observations['transition_scope_complete'].value is False
    assert draft.observations['transition_actual_consumption_verified'].value is False
    assert 'actual_transition_activation_unverified' in draft.observations['transition_missing'].value
    if check(profile).id == 'LIF-16':
        assert 'tamper_activation_and_rejection_unverified' in draft.observations['transition_missing'].value
    assert PRIVATE not in draft.artifacts[0].path.read_text(encoding='utf-8')


@pytest.mark.parametrize('failure', ['cleanup', 'recovery', 'replacement'])
def test_later_error_keeps_independent_facts_and_other_generation(monkeypatch, tmp_path, failure):
    executor, env, log = harness(monkeypatch, failure=failure)
    draft = executor.run(check(), env, policy(), None, tmp_path)
    assert draft.status == ExecStatus.ABORTED
    assert draft.observations['transition_request_configuration_conflicts'].value
    assert any(f['generation'] == 'new' for f in draft.observations['transition_request_configuration_facts'].value)
    if failure == 'cleanup': assert draft.cleanup_errors
    if failure == 'recovery': assert draft.normal_control.succeeded is False
    assert ('probe', 'recovered') in log


@pytest.mark.parametrize('change', ['naive', 'order', 'hash', 'phase'])
def test_policy_rejects_missing_or_ambiguous_schedule(change):
    values = policy()
    if change == 'naive': values['transition_sample_at']['before'] = '2026-10-19T00:00:00'
    if change == 'order': values['transition_sample_at']['after'] = values['transition_sample_at']['before']
    if change == 'hash': values['transition_inputs_sha256'] = True
    if change == 'phase': values['transition_phase_generation'].pop('during')
    with pytest.raises(ValueError, match='TRANSITION_POLICY_INVALID'): subject.normalize_policy(values)


def test_unsupported_snapshot_profile_is_explicit_not_unknown_wrapper():
    with pytest.raises(ValueError, match='PROFILE_UNSUPPORTED'):
        subject.LifecycleTransitionExecutor({}).validate_policy(SimpleNamespace(id='LIF-16', collection_profile='snapshot'), policy())


class HTTPWorld(PodWorld):
    def runner(self, argv, **kwargs):
        result = super().runner(argv, **kwargs)
        if 'exec' in argv:
            request, = json.loads(kwargs['input_bytes'])['requests']
            body = json.dumps({'model': 'fixture-model', 'choices': [{'message': {'content': PRIVATE}}]})
            result['stdout_bytes'] = canonical_json([{'id': request['id'], 'method': 'POST', 'mode': 'json',
                'outcome': 200, 'body': body, 'body_bytes': len(body.encode()), 'body_sha256': sha256_hex(body.encode()),
                'capture_complete': True, 'eof': True, 'utf8_valid': True, 'body_truncated': False}])
        return result


@pytest.mark.parametrize('replacement', [False, True])
def test_real_http_transport_closed_safe_request_and_replacement_binding(replacement):
    env = http_env(); target = http_target(env); world = HTTPWorld(target)
    if replacement:
        def mutate(role, value, after):
            if role == 'engine' and after: value['metadata']['uid'] = 'other'
        world.mutate = mutate
    generation = subject.Generation('old', env, target, {}, {})
    result = subject.LifecycleTransitionExecutor({}, runner=world.runner)._probe(generation, 'before', 'example-request', 3)
    assert result['normal_shape_verified'] is (not replacement)
    assert len(world.execs) == 1
    assert world.execs[0]['body']['max_tokens'] == 1
    assert world.execs[0]['headers'] == {'X-Request-Id': 'example-request'}
    assert PRIVATE not in json.dumps(result)
    assert all(not any(a in argv for a in ('apply', 'create', 'delete', 'patch', 'rollout')) for argv in world.calls)


def test_actual_prepared_legacy_profile_rejected_before_execution(case, tmp_path):
    values = policy(); values['transition_phase_generation'] = dict.fromkeys(subject.PHASES, 'old')
    cfg = {'generations': [{'id': 'old', 'environment': str(case.root / 'env.json'),
        'target': str(case.root / 'target.json'), 'prepared': str(case.manifest_path)}]}
    with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
        subject.load_generations(cfg, case.env, values)


@pytest.mark.parametrize('profile', [subject.generation_sensor.PROFILE, subject.endpoint_sensor.PROFILE])
@pytest.mark.parametrize('change', [None, 'reuse-session', 'scope-target', 'manifest-pin', 'real-target'])
def test_actual_preparation_to_loader_exact_scope_sessions_and_transport(tmp_path, group, native_target, profile, change):
    from guard.collectors.lifecycle import prepare_assets, load_prepared
    from guard.collectors.model_read_scope import build_scope
    from tests.test_a1_model_read_scope import reference
    env, native, model_manifest = reference(tmp_path, group, native_target)
    scope = build_scope(env, native, model_manifest, '/models/UNMOUNTED', nonsecret_model_weights=True)
    scope_path = tmp_path / 'scope.json'; scope_path.write_bytes(canonical_json(scope))
    folder = group.root / 'transition-prepared'
    prepare_assets(folder, Path(__file__).resolve().parents[2], profile=profile, model_read_scope=scope_path)
    prepared = load_prepared(folder / 'manifest.json')
    target = http_target(env); target['kubernetes']['engine'] = native['kubernetes']['engine']
    target['engine']['image_digest'] = env.engine.image_digest
    if change == 'scope-target': target['kubernetes']['context'] = 'other-context'
    if change == 'real-target': target['lab'] = False
    env_path = tmp_path / 'environment.json'; env_path.write_bytes(canonical_json(env.to_dict()))
    target_path = tmp_path / 'target.json'; target_path.write_bytes(canonical_json(target))
    rows = [{'id': 'old', 'target': str(target_path), 'prepared': str(folder / 'manifest.json'), 'environment': str(env_path)}]
    if change == 'reuse-session': rows.append({**rows[0], 'id': 'new'})
    manifest = [{'id': row['id'], 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': sha256_hex(canonical_json(target)), 'prepared_sha256': sha256_hex(canonical_json(prepared))} for row in rows]
    values = policy(); values['transition_inputs_sha256'] = sha256_hex(canonical_json(manifest))
    values['transition_phase_generation'] = dict.fromkeys(subject.PHASES, 'old')
    if change == 'manifest-pin': values['transition_inputs_sha256'] = '0' * 64
    if change:
        with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
            subject.load_generations({'generations': rows}, env, values)
    else:
        generations, actual = subject.load_generations({'generations': rows}, env, values)
        assert actual == manifest
        assert generations['old'].native_target == native
        assert generations['old'].prepared == prepared


@pytest.mark.parametrize('profile', [subject.generation_sensor.PROFILE, subject.endpoint_sensor.PROFILE])
def test_capture_consumes_profile_projector_default_and_preserves_raw_on_projection_failure(monkeypatch, profile):
    reader = SimpleNamespace(binding={'engine': {'container': 'engine', 'image': 'image'}},
        owner_identity=None, read_file=None, image_check=None)
    generation = subject.Generation('old', None, {}, {}, {'profile': profile, 'session': '1' * 32}, reader)
    native = {'session': '1' * 32, 'recordings': [], 'diagnostic_codes': []}
    monkeypatch.setattr(subject, 'capture_session', lambda *a, **k: deepcopy(native))
    executor = subject.LifecycleTransitionExecutor({})
    result = executor._capture(generation)
    projected = result['transition_generation_projection']
    assert projected['generation_events'] == [] and projected['missing']
    assert projected['recording_profile'] == profile
    module = subject.endpoint_metadata if profile == subject.endpoint_sensor.PROFILE else subject.generation_metadata
    def broken(*a): raise ValueError(PRIVATE)
    monkeypatch.setattr(module, 'project', broken)
    result = executor._capture(generation)
    assert result['recordings'] == native['recordings']
    assert result['transition_generation_projection'] is None
    assert result['diagnostic_codes'] == ['TRANSITION_GENERATION_PROJECTION_UNAVAILABLE']
    assert PRIVATE not in json.dumps(result)
