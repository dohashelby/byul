"""The installed release remains a prerequisite after offline preparation."""
from pathlib import Path
import json
import subprocess

import pytest

from guard import application
from guard.adapters import vllm_initialization_release_sensor as release
from guard.adapters import vllm_initialization_sensor as legacy
from guard.adapters import vllm_ready_endpoint_sensor as endpoint, vllm_worker_generation_sensor as generation
from guard.adapters import vllm_worker_initialization_sensor as joint
from guard.adapters.vllm_lifecycle_release_sensor import COMMIT
from guard.adapters.vllm_release_profiles import release_profile
from guard.collectors.lifecycle import load_initialization_prepared, prepare_assets
from guard.core.execution_recipe import bind_recipe, configuration_sha256
from guard.core.select import select
from guard.executors import run_binding
from guard.executors.kubernetes_initialization import KubernetesInitializationExecutor
from guard.models import ExecStatus, Side, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy
from tests.run_binding_support import run_locked_update_trial
from tests.test_a1_file_read_sensor import scope_value
from tests.test_kubernetes_sampling import inputs as owned_target
from tests.test_lifecycle_capture import REPOSITORY, external  # noqa: F401


def _write(path, value):
    path.write_bytes(canonical_json(value))
    return str(path)


def _forbidden(*args, **kwargs):
    pytest.fail('Offline initialization preparation must not perform a runtime request')


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
            elif change in {'abbreviated', 'wrong-prefix', 'unanchored-prefix'}:
                engine['source_commit'] = 'a' * 9 if change == 'wrong-prefix' else COMMIT[:9]
                engine['source_commit_status'] = 'ABBREVIATED_MATCH'
                if change != 'unanchored-prefix':
                    engine['expected_source_commit'] = COMMIT
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
    ('LIF-09', 'abbreviated', None),
    ('LIF-09', 'version', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'image', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'commit', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'wrong-prefix', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'unanchored-prefix', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'expected-commit', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'mode', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-07', 'duplicate-mode', 'INITIALIZATION_PREPARED_RELEASE_MISMATCH'),
    ('LIF-09', 'asset', 'RUN_BINDING_UNAVAILABLE'),
])
def test_public_preparation_checks_exact_release_assets(external, monkeypatch, check_id, change, error):  # noqa: F811
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    report = application.update_readiness(*_paired_inputs(external, check_id, change))
    inputs = next(row for row in report['checks'] if row['check'] == 'inputs')
    assert inputs['ok'] is (error is None), report
    if error is not None:
        assert error in inputs['detail']
    assert len(report['check_support']) == 2
    assert all(row['runtime_qualified'] is False for row in report['check_support'])


@pytest.mark.parametrize(('side', 'check_id', 'change', 'error'), [
    ('baseline', 'LIF-09', None, None),
    ('candidate', 'LIF-07', None, None),
    ('baseline', 'LIF-09', 'not-owned-lab', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('candidate', 'LIF-07', 'wrong-model', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('candidate', 'LIF-09', 'aliased-owner', 'KUBERNETES_HTTP_BINDING_UNAVAILABLE'),
    ('baseline', 'LIF-07', 'other-profile', 'INITIALIZATION_CONFIGURATION_INVALID'),
    ('candidate', 'LIF-07', generation.PROFILE, None),
    ('candidate', 'LIF-09', endpoint.PROFILE, 'INITIALIZATION_NORMAL_NODE_IMAGE_PROOF_CONFIGURATION_REQUIRED'),
    ('candidate', 'LIF-09', 'endpoint-with-node-proof', None),
    ('baseline', 'LIF-07', 'cross-release-generation', 'INITIALIZATION_NORMAL_WORKER_RELEASE_UNSUPPORTED'),
    ('candidate', 'LIF-09', 'incomplete-activation', 'INITIALIZATION_CONFIGURATION_INVALID'),
    ('candidate', 'LIF-07', 'non-normal-phase', 'INITIALIZATION_CONFIGURATION_INVALID'),
    ('candidate', 'LIF-09', 'scope-mismatch', 'MODEL_READ_SCOPE_TARGET_MISMATCH'),
])
def test_executor_prepared_inputs_reuse_run_checks_without_owner_reads(
        external, tmp_path, monkeypatch, side, check_id, change, error):  # noqa: F811
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    pack, envs, pol, cfg = _paired_inputs(external, check_id, None)
    check = pack.checks[check_id]
    env = envs[side == 'candidate']
    settings = cfg['sides'][side]['initialization']
    target_path = Path(settings['target'])
    target = json.loads(target_path.read_bytes())
    if change == 'not-owned-lab':
        target['lab'] = False
    elif change == 'wrong-model':
        target['model_mapping']['repository_id'] = 'EXAMPLE-other-model'
    elif change == 'aliased-owner':
        target['kubernetes']['probe']['container_id'] = target['kubernetes']['engine']['container_id']
    target_path.write_bytes(canonical_json(target))
    if change == 'other-profile':
        assets = external / 'other-profile'
        prepare_assets(assets, REPOSITORY)
        settings['prepared'] = str(assets / 'manifest.json')
    native_profile = {
        'incomplete-activation': generation.PROFILE,
        'non-normal-phase': generation.PROFILE,
        'cross-release-generation': generation.PROFILE,
        'endpoint-with-node-proof': endpoint.PROFILE,
    }.get(change, change)
    if native_profile in {generation.PROFILE, endpoint.PROFILE, 'scope-mismatch'}:
        native = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': True,
            'run_id': target['kubernetes']['run_id'], 'environment_ref': target['environment_ref'],
            'kubernetes': {key: target['kubernetes'][key] for key in ('context', 'engine')}}
        scope = scope_value(external / 'public.safetensors', env=env, target=native)
        if change == 'scope-mismatch':
            scope['target_sha256'] = '0' * 64
        scope_path = _write(external / 'model-scope.json', scope)
        assets = external / 'model-profile'
        prepare_assets(assets, REPOSITORY, profile=joint.PROFILE if change == 'scope-mismatch' else native_profile,
                       model_read_scope=scope_path)
        settings['prepared'] = str(assets / 'manifest.json')
        if change == 'endpoint-with-node-proof':
            settings['node_image_verification'] = 'local-containerd-v1'
    if change == 'incomplete-activation':
        check = check.model_copy(update={'required_observations': [
            item for item in check.required_observations if item.key != 'activation_scope_complete']})
    policy_values = dict(pol.values[check_id])
    if change == 'non-normal-phase':
        policy_values['init_phase'] = 'partial-failure'
    _, values = run_binding.effective_values(check, settings, env, policy_values, side=side,
        attempt_id=None, issued_at=utcnow(), run_cfg=cfg['run_binding'][check_id])
    executor = KubernetesInitializationExecutor(settings, runner=_forbidden)
    if error is None:
        bound_target, prepared, transport = executor.prepared_inputs(check, env, values)
        assert bound_target == target
        expected_profile = native_profile or (release.PROFILE if side == 'baseline' else legacy.PROFILE)
        assert prepared['profile'] == expected_profile
        assert transport.binding == target['kubernetes']
    else:
        with pytest.raises(ValueError, match=f'^{error}$'):
            executor.prepared_inputs(check, env, values)
        if change == 'scope-mismatch':
            draft = executor.run(check, env, values, object(), tmp_path / 'runtime-gap')
            assert draft.status is ExecStatus.ABORTED and draft.abort_reason is not None
            assert 'INITIALIZATION_PREFLIGHT_UNAVAILABLE' in draft.abort_reason
        else:
            with pytest.raises((RuntimeError, ValueError), match=f'^{error}$'):
                executor.run(check, env, values, object(), tmp_path / 'invalid-runtime')


def _sealed_plan(store, pack, envs, pol, cfg):
    put_pack(store, pack.path)
    for item in [*envs, pol]:
        store.put(item)
    plan = bind_recipe(select(pack, envs, pol, mode='update'), cfg, envs)
    store.put(plan)
    return plan


@pytest.mark.parametrize(('side', 'check_id'), [('baseline', 'LIF-07'), ('candidate', 'LIF-09')])
def test_initialization_rejects_same_content_target_symlink_after_preparation(
        external, monkeypatch, side, check_id):  # noqa: F811
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    pack, envs, pol, cfg = _paired_inputs(external, check_id, None)
    check = pack.checks[check_id]
    assert run_binding.stages_for(check) == (1, 2)
    env = envs[side == 'candidate']
    settings = cfg['sides'][side]['initialization']
    _, values = run_binding.effective_values(check, settings, env, pol.values[check_id], side=side,
        attempt_id=None, issued_at=utcnow(), run_cfg=cfg['run_binding'][check_id])
    executor = KubernetesInitializationExecutor(settings, runner=_forbidden,
        expected_target_sha256=values['init_target_sha256'])
    executor.prepared_inputs(check, env, values)

    target = Path(settings['target'])
    original = target.with_name('original-target.json')
    target.rename(original)
    target.symlink_to(original.name)
    monkeypatch.setattr('guard.executors.kubernetes_initialization.KubernetesInitializationHTTP', _forbidden)
    with pytest.raises(ValueError, match='^INITIALIZATION_CONFIGURATION_INVALID$'):
        executor.prepared_inputs(check, env, values)
    with pytest.raises(ValueError, match='^INITIALIZATION_CONFIGURATION_INVALID$'):
        executor.run(check, env, values, object(), external / 'not-dispatched')


def test_recipe_pins_raw_files_and_preserves_canonical_run_binding(external, tmp_path):  # noqa: F811
    check_id = 'LIF-07'
    pack, envs, pol, cfg = _paired_inputs(external, check_id, None)
    run_cfg = cfg['run_binding'][check_id]
    scenario = Path(run_cfg['scenario'])
    scenario.write_bytes(b'{\n  "case": "EXAMPLE normal startup"\n}\n')
    plan = _sealed_plan(Store(tmp_path / 'store'), pack, envs, pol, cfg)
    files = {}
    assert configuration_sha256(cfg, envs, plan=plan, initialization_digests=files) == plan.inputs.execution_recipe_sha256
    assert len(files) == 2
    for side, env in zip(('baseline', 'candidate'), envs):
        settings = cfg['sides'][side]['initialization']
        manifest = Path(settings['prepared'])
        exact = files[side + ':' + check_id]
        assert exact == {'prepared': sha256_hex(manifest.read_bytes()), 'scenario': sha256_hex(scenario.read_bytes())}
        doc, _ = run_binding.effective_values(pack.checks[check_id], settings, env, pol.values[check_id],
            side=side, attempt_id=None, issued_at=utcnow(), run_cfg=run_cfg, expected_initialization_digests=exact)
        assert doc['schema'] == run_binding.SCHEMA_2
        assert doc['files']['init_prepared_sha256'] == sha256_hex(canonical_json(json.loads(manifest.read_bytes())))
        assert doc['files']['init_scenario_sha256'] == sha256_hex(canonical_json(json.loads(scenario.read_bytes())))
        assert doc['files']['init_scenario_sha256'] != exact['scenario']


@pytest.mark.parametrize('late', [False, True])
@pytest.mark.parametrize(('field', 'side', 'check_id'), [
    ('prepared', Side.baseline, 'LIF-07'), ('scenario', Side.candidate, 'LIF-09'),
])
def test_recipe_rejects_a_valid_substitute_without_dispatch(external, tmp_path, monkeypatch, field, side, check_id, late):  # noqa: F811
    from guard.core import run as core_run

    pack, envs, pol, cfg = _paired_inputs(external, check_id, None)
    store = Store(tmp_path / 'store')
    plan = _sealed_plan(store, pack, envs, pol, cfg)
    if field == 'prepared':
        manifest = Path(cfg['sides'][side.value]['initialization']['prepared'])
        other = external / 'replacement'
        original_prepared = load_initialization_prepared(manifest, envs[0])
        prepare_assets(other, REPOSITORY, profile=original_prepared['profile'])

        def replace():
            for file in other.iterdir():
                (manifest.parent / file.name).write_bytes(file.read_bytes())
            assert load_initialization_prepared(manifest, envs[0])['session'] != original_prepared['session']
    else:
        scenario = Path(cfg['run_binding'][check_id]['scenario'])

        def replace():
            _write(scenario, {'case': 'EXAMPLE same path, different valid input'})

    monkeypatch.setattr(KubernetesInitializationExecutor, 'run', _forbidden)
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    if late:
        original_run = core_run._run_one

        def replace_after_recipe(*args, **kwargs):
            replace()
            return original_run(*args, **kwargs)

        monkeypatch.setattr(core_run, '_run_one', replace_after_recipe)
        evidence, = run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=side)
        assert evidence.execution.status == ExecStatus.NOT_RUN
        assert evidence.execution.abort_reason == f'RUN_BINDING_INITIALIZATION_{field.upper()}_CHANGED'
        assert evidence.execution.run_binding is None
    else:
        replace()
        with pytest.raises(ValueError, match='EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED'):
            run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=side)
        assert not store.find_latest('EVD', lambda row: row.plan == plan.ref())


def test_unselected_initialization_files_do_not_block_other_checks(external, tmp_path, monkeypatch):  # noqa: F811
    pack, envs, _, cfg = _paired_inputs(external, 'LIF-07', None)
    del cfg['update']['allowed_checks']
    pol = policy(pack.ref(), scope={'include': ['SUP-10']}, values={})
    store = Store(tmp_path / 'store')
    plan = _sealed_plan(store, pack, envs, pol, cfg)
    Path(cfg['run_binding']['LIF-07']['scenario']).unlink()
    Path(cfg['sides']['baseline']['initialization']['prepared']).unlink()
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
        run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=Side.baseline)
    assert not store.find_latest('EVD', lambda row: row.plan == plan.ref())

    # Selection with missing, unused files is valid; removing already committed inputs requires re-selection.
    current = _sealed_plan(store, pack, envs, pol, cfg)
    evidence, = run_locked_update_trial(store, pack, current, application.run_config(cfg), only_side=Side.baseline)
    assert evidence.check_id == 'SUP-10'
    assert evidence.execution.abort_reason == "executor 'supply_boot' not configured in guard.toml [executors]"


def test_executor_rejects_manifest_rewrite_after_run_binding(external, tmp_path, monkeypatch):  # noqa: F811
    check_id = 'LIF-09'
    pack, envs, pol, cfg = _paired_inputs(external, check_id, None)
    store = Store(tmp_path / 'store')
    plan = _sealed_plan(store, pack, envs, pol, cfg)
    path = Path(cfg['sides']['candidate']['initialization']['prepared'])
    declared = json.loads(path.read_bytes())
    effective_values = run_binding.effective_values

    def rewrite_after_binding(*args, **kwargs):
        result = effective_values(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b'\n')
        assert json.loads(path.read_bytes()) == declared
        return result

    monkeypatch.setattr(run_binding, 'effective_values', rewrite_after_binding)
    monkeypatch.setattr('guard.executors.kubernetes_initialization.KubernetesInitializationHTTP', _forbidden)
    evidence, = run_locked_update_trial(store, pack, plan, application.run_config(cfg), only_side=Side.candidate)
    assert evidence.execution.status == ExecStatus.ABORTED
    assert evidence.execution.abort_reason == 'EXECUTOR_FAILURE'
    assert evidence.execution.run_binding['files']['init_prepared_sha256'] == sha256_hex(canonical_json(declared))
