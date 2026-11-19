"""The installed release remains a prerequisite after offline preparation."""
from pathlib import Path
import subprocess

import pytest

from guard import application
from guard.adapters import vllm_initialization_release_sensor as release
from guard.adapters import vllm_initialization_sensor as legacy
from guard.adapters.vllm_lifecycle_release_sensor import COMMIT
from guard.adapters.vllm_release_profiles import release_profile
from guard.collectors.lifecycle import prepare_assets
from guard.models import canonical_json, load_pack
from tests.builders import environment, policy
from tests.test_kubernetes_sampling import inputs as owned_target
from tests.test_lifecycle_capture import REPOSITORY, external  # noqa: F401


def _write(path, value):
    path.write_bytes(canonical_json(value))
    return str(path)


def _paired_inputs(external, check_id, change):
    pack = load_pack(REPOSITORY / 'update' / 'packs' / 'PACK-BASELINE')
    values = {'init_executor_backend': 'mp', 'init_expected_ranks': ['0'], 'init_phase': 'normal',
              'init_required_stages': ['worker-ready', 'model-load', 'admission'],
              'init_failure_rank': 0, 'init_failure_stage': 'model-load',
              'init_preparation_deadline_ms': 10000, 'init_recovery_deadline_ms': 10000}
    if check_id == 'LIF-07':
        values['required_readiness_items'] = ['http_listener']
    pol = policy(pack.ref(), scope={'include': [check_id]}, values={check_id: values})
    scenario = _write(external / 'scenario.json', {'case': 'EXAMPLE normal startup'})
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'initialization': 'kubernetes'},
           'update': {'allowed_checks': [check_id]}, 'run_binding': {check_id: {'scenario': scenario}}, 'sides': {}}
    envs = []
    for side, version, profile in (('baseline', '0.30.0', release.PROFILE),
                                   ('candidate', '0.31.0', legacy.PROFILE)):
        engine = {'name': 'vllm', 'version': version, 'image_digest': release_profile(version)['images'][0]}
        runtime = {}
        if side == 'baseline':
            if change == 'version':
                engine['version'] = '0.31.0'
            elif change == 'image':
                engine['image_digest'] = 'example/other@sha256:' + 'a' * 64
            elif change == 'commit':
                engine['source_commit'] = 'a' * 40
            elif change == 'expected-commit':
                engine['expected_source_commit'] = 'a' * 40
            elif change == 'mode':
                runtime['platform'] = 'GPU; engine_requested=V0'
            elif change == 'duplicate-mode':
                runtime['platform'] = 'GPU; engine_requested=V1; engine_requested=V1'
            elif change == 'explicit-v1':
                engine['source_commit'] = COMMIT
                runtime['platform'] = 'GPU; engine_requested=V1'
        env = environment(id=f'ENV-{side.upper()}', engine=engine, runtime=runtime,
                          model={'id': 'EXAMPLE-model', 'revision': 'EXAMPLE-revision', 'inventory_sha256': 'c' * 64})
        envs.append(env)
        target = owned_target()
        target['engine']['image_digest'] = engine['image_digest']
        target['kubernetes']['engine'].update(image=engine['image_digest'], image_id=engine['image_digest'])
        target.update(schema='guard-kubernetes-request-binding-target/1', observed_version=engine['version'],
                      environment_ref=env.ref().model_dump(mode='json'), comparison_config={'fixture': 'startup'},
                      model_mapping={'repository_id': env.model.id, 'revision': env.model.revision,
                                     'api_model': 'EXAMPLE-serving'})
        assets = external / side
        prepared = prepare_assets(assets, REPOSITORY, profile=profile)
        assert prepared['image_contents_verified'] is False and prepared['engine_started'] is False
        if side == 'baseline' and change == 'asset':
            asset = assets / 'guard_initialization.py'
            asset.write_bytes(asset.read_bytes() + b'\n# changed after preparation\n')
        cfg['sides'][side] = {'initialization': {'target': _write(external / f'{side}.json', target),
                                                 'prepared': str(assets / 'manifest.json')}}
    return pack, envs, pol, cfg


@pytest.mark.parametrize(('check_id', 'change', 'error'), [
    ('LIF-09', None, None),
    ('LIF-07', 'explicit-v1', None),
    ('LIF-09', 'version', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'image', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'commit', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'expected-commit', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'mode', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'duplicate-mode', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'asset', 'RUN_BINDING_UNAVAILABLE'),
])
def test_public_preparation_checks_exact_release_assets(external, monkeypatch, check_id, change, error):  # noqa: F811
    def forbidden(*args, **kwargs):
        pytest.fail('Offline initialization preparation must not perform a runtime request')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    report = application.update_readiness(*_paired_inputs(external, check_id, change))
    inputs = next(row for row in report['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is (error is None), report
    if error is not None:
        assert error in inputs['detail']
    assert len(report['check_support']) == 2
    assert all(row['runtime_qualified'] is False for row in report['check_support'])
