"""Offline paired lifecycle input materialization through the existing readiness consumer."""

from functools import partial
import json
from pathlib import Path
import socket
import subprocess
import tomllib

import pytest

from guard.executors import lifecycle_transition as transition
from guard.models import DataOrigin, Environment, canonical_json, sha256_hex
from tests import test_redeployment_preflight as fixture
from tests.builders import T0, policy
from tests.test_lifecycle_capture import external
from tools import mvp_lifecycle_transition_inputs as materializer, mvp_live_readiness as readiness


def _pointer(path):
    path = Path(path)
    return {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}


@pytest.mark.parametrize(('check_id', 'case', 'error'), [
    ('LIF-05', 'ordinary', None),
    ('LIF-16', 'same-pod-restart', None),
    ('LIF-05', 'selection-changed', 'MVP_TRANSITION_SELECTION_CHANGED'),
    ('LIF-16', 'raw-target-changed', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
    ('LIF-05', 'lab-target', 'MVP_TRANSITION_GENERATION_TARGET_MISMATCH'),
    ('LIF-05', 'wrong-before-env', 'MVP_TRANSITION_BEFORE_ENVIRONMENT_MISMATCH'),
    ('LIF-16', 'other-release', 'MVP_TRANSITION_GENERATION_ENVIRONMENT_MISMATCH'),
    ('LIF-16', 'other-target-release', 'MVP_TRANSITION_GENERATION_TARGET_MISMATCH'),
    ('LIF-05', 'cross-side-owner', 'TRANSITION_GENERATION_SIDE_REUSE'),
    ('LIF-16', 'duplicate-offset', 'TRANSITION_POLICY_INVALID'),
    ('LIF-05', 'manifest-rewritten', 'MVP_TRANSITION_DECLARATION_FILE_CHANGED'),
])
def test_materializes_one_paired_redeployment_check_without_collection(external, monkeypatch, check_id, case, error):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline lifecycle input materialization attempted to launch or contact a target')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(transition.KubernetesHTTP, '_verify', forbidden)
    monkeypatch.setattr(transition.KubernetesHTTP, '__call__', forbidden)
    monkeypatch.setattr(transition.LifecycleTransitionExecutor, 'run', forbidden)
    monkeypatch.setattr(fixture, 'environment', partial(fixture.environment, origin=DataOrigin.DECLARED))
    pack, envs, _, cfg = fixture._configuration(external, check_id, change=case, mvp_inputs=True,
                                               lab=case == 'lab-target')
    confirmed = policy(pack.ref(), scope={'include': [check_id]}, values={}, data_origin=DataOrigin.DECLARED,
                       status='CONFIRMED', confirmed_by='EXAMPLE operator', confirmed_at=T0)
    policy_path = external / 'policy.json'
    policy_path.write_bytes(canonical_json(confirmed.to_dict()))
    binding = cfg['run_binding'][check_id]
    sides = {}
    for side, env in zip(readiness.SIDES, envs, strict=True):
        rows = cfg['sides'][side]['check_config'][check_id]['generations']
        sides[side] = {'environment_ref': env.ref().model_dump(mode='json'),
                       'generations': [{key: value if key == 'id' else _pointer(value) for key, value in row.items()}
                                       for row in rows],
                       'phase_generation': dict(binding['phase_generation']),
                       'sample_offsets_ms': dict(binding['sample_offsets_ms'])}

    candidate = sides['candidate']
    candidate['sample_offsets_ms']['recovered'] += 100
    after = candidate['generations'][1]
    target = Path(after['target']['path'])
    if case == 'raw-target-changed':
        target.write_bytes(target.read_bytes() + b'\n')
    elif case == 'other-target-release':
        declaration = json.loads(target.read_bytes())
        target.write_bytes(canonical_json({**declaration, 'observed_version': '0.30.0'}))
        after['target'] = _pointer(target)
    elif case == 'wrong-before-env':
        candidate['environment_ref'] = Environment.model_validate_json(
            Path(after['environment']['path']).read_bytes()).ref().model_dump(mode='json')
    elif case == 'duplicate-offset':
        candidate['sample_offsets_ms']['during'] = candidate['sample_offsets_ms']['before']
    elif case == 'manifest-rewritten':
        manifest = Path(after['prepared']['path'])
        original = transition.LifecycleTransitionExecutor.prepared_generations

        def rewrite_after_read(executor, check, env, values):
            prepared = original(executor, check, env, values)
            if env.ref() == envs[1].ref():
                manifest.write_bytes(manifest.read_bytes() + b'\n')
            return prepared

        monkeypatch.setattr(transition.LifecycleTransitionExecutor, 'prepared_generations', rewrite_after_read)

    selection_path = external / 'selection.json'
    selection_path.write_bytes(canonical_json({'schema': materializer.SELECTION_SCHEMA, 'check_id': check_id,
                                               'policy_ref': confirmed.ref().model_dump(mode='json'), 'sides': sides}))
    selection_pin = '0' * 64 if case == 'selection-changed' else sha256_hex(selection_path.read_bytes())
    output = external / 'assembled'
    if error is not None:
        with pytest.raises(ValueError, match=f'^{error}$'):
            materializer.prepare(policy_path, selection_path, selection_pin, output, pack=pack)
        assert not output.exists()
        return

    assembled, index = materializer.prepare(policy_path, selection_path, selection_pin, output, pack=pack)
    config_path = output / materializer.CONFIG_NAME
    index_path = output / materializer.INDEX_NAME
    assert tomllib.loads(config_path.read_text(encoding='utf-8')) == assembled
    assert json.loads(index_path.read_bytes()) == index
    assert assembled['update']['allowed_checks'] == [check_id]
    assert assembled['executors'] == {'lifecycle_transition': 'kubernetes'}
    assert [item['side'] for item in index['inputs']] == list(readiness.SIDES)
    for item in index['inputs']:
        side = item['side']
        expected = cfg['sides'][side]['check_config'][check_id]
        digest = sha256_hex(canonical_json(transition.inputs_manifest(expected)))
        assert assembled['sides'][side]['check_config'] == {
            check_id: {**expected, transition.GENERATION_MANIFEST_PIN: digest}}
        assert assembled['sides'][side]['run_binding'] == {
            check_id: {key: sides[side][key] for key in binding}}
        assert item['configuration'] == _pointer(config_path)
        assert item['environment'] == sides[side]['generations'][0]['environment']

    report = readiness.audit(index_path, pack=pack)
    selected = {row['side']: row for row in report['rows'] if row['check_id'] == check_id}
    assert set(selected) == set(readiness.SIDES)
    for row in selected.values():
        assert row['file_binding_valid'] and row['binding_schema'] == 'guard-run-binding/2'
        assert row['blocking_codes'] == ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
        assert not row['engine_executed'] and not row['ready_for_execution'] and row['actual_results'] is None

    target_data = json.loads(target.read_bytes())
    target_data['kubernetes']['probe']['pod_uid'] = '2187f050-0a54-41ea-8c12-f03c406400cc'
    target.write_bytes(canonical_json(target_data))
    report = readiness.audit(index_path, pack=pack)
    changed = next(row for row in report['rows'] if row['check_id'] == check_id and row['side'] == 'candidate')
    assert not changed['file_binding_valid']
