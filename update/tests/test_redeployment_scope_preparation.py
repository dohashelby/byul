"""Offline model reference preparation for the selected redeployment generation."""

from functools import partial
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.lifecycle import load_prepared, prepare_assets
from guard.collectors.model_read_scope import validate_binding
from guard.executors import lifecycle_transition as transition, run_binding
from guard.executors.lifecycle_transition_scope import prepare_scope
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex
from tests import test_redeployment_preflight as fixture
from tests.builders import T0
from tests.test_lifecycle_capture import REPOSITORY, external


@pytest.fixture
def declared_generation(external, monkeypatch):
    monkeypatch.setattr(fixture, 'environment', partial(fixture.environment, origin=DataOrigin.DECLARED))
    monkeypatch.setattr(transition.KubernetesHTTP, '_verify', fixture._forbidden)
    monkeypatch.setattr(transition.KubernetesHTTP, '__call__', fixture._forbidden)
    monkeypatch.setattr(transition.LifecycleTransitionExecutor, 'run', fixture._forbidden)
    env, generation = fixture._generation(external / 'old', 'old', 100, version='0.31.0', mvp_inputs=True, lab=False)
    env_path, target_path = (Path(generation[key]) for key in ('environment', 'target'))
    reference = external / 'old' / 'reference.json'
    return env, generation, env_path, target_path, reference


def _scope_call(env, target, reference, destination):
    return ['prepare-model-read-scope', '--env', str(env), '--target', str(target), '--model-manifest', str(reference),
            '--model-root', '/models', '--output', str(destination), '--nonsecret-model-weights']


def test_public_pinned_scope_produces_a_preparable_generation(external, declared_generation):
    env, before, env_path, target_path, reference = declared_generation
    output = external / 'scope.json'
    target_pin, reference_pin = (sha256_hex(path.read_bytes()) for path in (target_path, reference))
    result = CliRunner().invoke(app, [*_scope_call(env_path, target_path, reference, output),
        '--target-sha256', target_pin, '--model-manifest-sha256', reference_pin])
    assert result.exit_code == 0, (result.output, result.exception)
    assert json.loads(result.output) == {'prepared': True, 'model_files_read': False, 'approval_verified': False}
    scope = json.loads(output.read_bytes())
    target = json.loads(target_path.read_bytes())
    native = transition.generation_native_target(target, env, require_lab=False)
    assert scope['environment_ref'] == env.ref().model_dump(mode='json')
    assert scope['manifest_bytes_sha256'] == reference_pin and scope['inventory_sha256'] == env.model.inventory_sha256
    assert scope['target_sha256'] == sha256_hex(canonical_json(native))
    assert scope['hash_model_bytes'] is False and scope['data_origin'] == 'DECLARED'
    assert scope['session'] is None and scope['input_scope_sha256'] is None

    assets = external / 'selected-prepared'
    prepare_assets(assets, REPOSITORY, profile=transition.generation_sensor.PROFILE, model_read_scope=output)
    manifest_path = assets / 'manifest.json'
    prepared = load_prepared(manifest_path)
    assert prepared['engine_started'] is False and prepared['product_connected'] is False
    validate_binding(prepared, env, native)
    assert prepared['model_read_scope']['manifest_bytes_sha256'] == reference_pin
    deployed_scope = json.loads((assets / 'model-read-scope.json').read_bytes())
    assert deployed_scope['session'] == prepared['session']
    assert deployed_scope['input_scope_sha256'] == sha256_hex(output.read_bytes())

    pack = load_pack(REPOSITORY / 'update' / 'packs' / 'PACK-BASELINE')
    check = pack.checks['LIF-05']
    _, after = fixture._generation(external / 'after', 'after', 101, version='0.31.0', mvp_inputs=True, lab=False)
    settings = {'generations': [{**before, 'prepared': str(manifest_path)}, after]}
    digest = sha256_hex(canonical_json(transition.inputs_manifest(settings)))
    settings[transition.GENERATION_MANIFEST_PIN] = digest
    run_cfg = {'phase_generation': {'before': 'old', 'during': 'old', 'after': 'after', 'recovered': 'after'},
               'sample_offsets_ms': {'before': 1000, 'during': 2000, 'after': 3000, 'recovered': 4000}}
    _, values = run_binding.compute(check, settings, env, side='candidate', attempt_id='EXAMPLE-prepare-scope',
                                   issued_at=T0, run_cfg=run_cfg)
    executor = transition.LifecycleTransitionExecutor(settings, runner=fixture._forbidden)
    generations, verified_manifest = executor.prepared_generations(check, env, values)
    assert generations['old'].prepared == prepared and generations['old'].native_target == native
    assert sha256_hex(canonical_json(verified_manifest)) == digest


@pytest.mark.parametrize('case,error', [
    ('target-bytes', 'FILE_READ_DEPENDENCY_UNAVAILABLE'),
    ('reference-bytes', 'FILE_READ_DEPENDENCY_UNAVAILABLE'),
    ('symlink', 'symlinks or junctions'),
    ('unsealed-env', 'TRANSITION_SCOPE_SEALED_ENV_REQUIRED'),
    ('example-env', 'TRANSITION_SCOPE_SEALED_ENV_REQUIRED'),
    ('lab', 'TRANSITION_SCOPE_GENERATION_TARGET_INVALID'),
    ('synthetic', 'TRANSITION_SCOPE_GENERATION_TARGET_INVALID'),
    ('wrong-env', 'TRANSITION_SCOPE_GENERATION_TARGET_INVALID'),
    ('wrong-version', 'TRANSITION_SCOPE_GENERATION_TARGET_INVALID'),
    ('wrong-probe', 'TRANSITION_GENERATION_HTTP_TARGET_INVALID'),
    ('wrong-model', 'MODEL_READ_SCOPE_MANIFEST_BINDING_INVALID'),
])
def test_pinned_scope_rejects_changed_or_unbound_sources(external, declared_generation, case, error):
    env, _, env_path, target_path, reference = declared_generation
    target_pin, reference_pin = (sha256_hex(path.read_bytes()) for path in (target_path, reference))
    if case in {'target-bytes', 'reference-bytes'}:
        path = target_path if case == 'target-bytes' else reference
        path.write_bytes(path.read_bytes() + b'\n')
    elif case == 'symlink':
        link = external / 'linked-target.json'
        link.symlink_to(target_path)
        target_path = link
    elif case == 'unsealed-env':
        env_path.write_bytes(canonical_json({**env.to_dict(), 'content_sha256': ''}))
    elif case == 'wrong-model':
        document = json.loads(reference.read_bytes())
        reference.write_bytes(canonical_json({**document, 'model_id': 'EXAMPLE unrelated model'}))
        reference_pin = sha256_hex(reference.read_bytes())
    else:
        target = json.loads(target_path.read_bytes())
        if case == 'example-env':
            env = env.model_copy(update={'data_origin': DataOrigin.EXAMPLE}).seal()
            env_path.write_bytes(canonical_json(env.to_dict()))
            target['environment_ref'] = env.ref().model_dump(mode='json')
        elif case == 'lab':
            target['lab'] = True
        elif case == 'synthetic':
            target['synthetic'] = True
        elif case == 'wrong-env':
            target['environment_ref']['sha256'] = '0' * 64
        elif case == 'wrong-version':
            target['observed_version'] = '0.30.0'
        else:
            target['kubernetes']['probe']['pod_uid'] = target['kubernetes']['engine']['pod_uid']
        target_path.write_bytes(canonical_json(target))
        target_pin = sha256_hex(target_path.read_bytes())
    with pytest.raises(ValueError, match=error):
        prepare_scope(env_path, target_path, reference, '/models', expected_target_sha256=target_pin,
                      approved_manifest_sha256=reference_pin, nonsecret_model_weights=True)


def test_public_pinned_scope_rejects_one_pin(external, declared_generation):
    _, _, env_path, target_path, reference = declared_generation
    output = external / 'scope.json'
    result = CliRunner().invoke(app, [*_scope_call(env_path, target_path, reference, output),
        '--target-sha256', sha256_hex(target_path.read_bytes())])
    assert result.exit_code == 2 and 'MODEL_READ_SCOPE_PREPARATION_FAILED' in result.output
    assert not output.exists()
