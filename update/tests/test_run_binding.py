"""guard-run-binding/1·/2: per-run binding computed by guard run (PACK-BASELINE@35 RB, @36 RB2)."""
from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import subprocess

import pytest

from tests.builders import run_under_plan_lock
import yaml

from guard import application
from guard.core.execution_recipe import bind_recipe, configuration_sha256
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry, run_binding
from guard.executors.base import EvidenceDraft
from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
from guard.models import DataOrigin, ExecStatus, Side, canonical_json, load_pack, sha256_hex, utcnow
from guard.models.objects import Execution
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy
from tests.test_kubernetes_gateway_observation import ordinary_gateway_record  # noqa: F401
from tests.test_lifecycle_capture import external  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')


def target_file(tmp_path, value=None):
    path = tmp_path / 'target.json'
    path.write_text(json.dumps(value or {'schema': 'EXAMPLE-target', 'lab': True}))
    return path


def test_pack_37_moves_exactly_the_listed_keys_and_keeps_rules():
    assert PACK.manifest.revision == 39
    for cid in set().union(*run_binding.STAGES):
        check = PACK.checks[cid]
        keys = (run_binding.KEYS_BY_CHECK.get(cid, ()) + run_binding.KEYS_BY_CHECK_2.get(cid, ())
                + run_binding.KEYS_BY_CHECK_3.get(cid, ()) + run_binding.KEYS_BY_CHECK_4.get(cid, ()))
        assert check.collection_profile == run_binding.PROFILE_BY_CHECK[cid]
        assert run_binding.keys_for(check) == keys
        assert not {p.key for p in check.policy_params} & run_binding.owned_keys(check)
    # LIF-11 keeps its validity in policy (its policy declares times inside it); PLT-10/11 take the
    # run window together with the issuer event from stage 3 (RB3).
    for cid, count in (('PLT-10', 0), ('PLT-11', 0), ('LIF-11', 2)):
        declared = {p.key for p in PACK.checks[cid].policy_params}
        assert sum(k.endswith(('validity_start', 'validity_end')) for k in declared) == count


def test_definitions_that_still_declare_the_keys_or_use_another_profile_are_untouched():
    from guard.models.checkdef import PolicyParam
    old = PACK.checks['REQ-12'].model_copy(deep=True)
    old.policy_params = [PolicyParam(key=k, type='str', required=True) for k in run_binding.KEYS_BY_CHECK['REQ-12']]
    assert run_binding.keys_for(old) == ()  # @34 definition: policy values as before
    other = PACK.checks['REQ-18'].model_copy(deep=True)
    other.collection_profile = 'engine-sensor-slot-lifetime-v1-run-binding-v2'
    assert run_binding.keys_for(other) == ()  # sensor /2 packs keep their operator binding
    assert run_binding.keys_for(PACK.checks['REQ-01']) == ()


def test_compute_uses_env_ref_file_bytes_and_a_bounded_run_window(tmp_path):
    env = environment(model={'id': 'm', 'revision': 'r'})
    path = target_file(tmp_path)
    issued = utcnow()
    document, values = run_binding.compute(PACK.checks['REQ-12'], {'target': str(path)}, env,
                                           side='single', attempt_id='ATTEMPT-X', issued_at=issued)
    assert values['attempt_environment_sha256'] == env.ref().sha256
    assert values['attempt_target_sha256'] == sha256_hex(canonical_json(json.loads(path.read_text())))
    start, end = (run_binding.window('request_termination', issued)[i] for i in (0, 1))
    assert (end - start).total_seconds() == 600 and start <= utcnow()
    assert document == {'schema': run_binding.SCHEMA, 'check_id': 'REQ-12', 'side': 'single', 'attempt_id': 'ATTEMPT-X',
                        'environment_sha256': env.ref().sha256,
                        'files': {'attempt_target_sha256': values['attempt_target_sha256']},
                        'validity': {'start': values['attempt_validity_start'], 'end': values['attempt_validity_end'],
                                     'basis': 'run_window'},
                        'issued_at': document['issued_at']}
    # The executor's own policy contract accepts the computed values.
    from guard.executors.kubernetes_request_attempts import normalize_policy
    normalize_policy({k: values[k] for k in run_binding.KEYS_BY_CHECK['REQ-12']})


def test_window_follows_the_executor_limit_and_policy_validity():
    now = utcnow()
    assert (lambda s, e, b: (e - s).total_seconds())(*run_binding.window('supply_boot', now)) == 3600
    start, end, basis = run_binding.window('request_termination', now, valid_until=now + timedelta(seconds=90))
    assert basis == 'run_window_and_policy' and 0 < (end - start).total_seconds() <= 90
    with pytest.raises(run_binding.RunBindingUnavailable, match='OUTSIDE_POLICY_VALIDITY'):
        run_binding.window('request_termination', now, valid_from=now + timedelta(minutes=5))
    with pytest.raises(run_binding.RunBindingUnavailable, match='OUTSIDE_POLICY_VALIDITY'):
        run_binding.window('request_termination', now, valid_until=now - timedelta(seconds=1))


def test_policy_must_not_carry_run_binding_keys_and_missing_files_are_unavailable(tmp_path):
    env = environment(model={'id': 'm', 'revision': 'r'})
    check = PACK.checks['REQ-12']
    with pytest.raises(run_binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
        run_binding.effective_values(check, {'target': str(target_file(tmp_path))}, env,
            {'attempt_target_sha256': 'a' * 64}, side='single', attempt_id=None, issued_at=utcnow())
    with pytest.raises(run_binding.RunBindingUnavailable, match='RUN_BINDING_UNAVAILABLE'):
        run_binding.effective_values(check, {'target': str(tmp_path / 'absent.json')}, env, {},
            side='single', attempt_id=None, issued_at=utcnow())
    with pytest.raises(run_binding.RunBindingUnavailable, match='FILE_UNAVAILABLE'):
        run_binding.effective_values(check, {'target': ''}, env, {}, side='single', attempt_id=None, issued_at=utcnow())


def test_empty_run_binding_is_not_serialized_so_past_evidence_hashes_hold():
    assert 'run_binding' not in Execution(status=ExecStatus.NOT_RUN).model_dump(mode='json')
    assert Execution(status=ExecStatus.NOT_RUN, run_binding={'schema': run_binding.SCHEMA}).model_dump(mode='json')['run_binding']


class Recording:
    name, version = 'request_termination', '0.0-EXAMPLE'

    def __init__(self, seen):
        self.seen = seen

    def run(self, check, env, values, adapter, workdir):
        self.seen.append(dict(values))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False}, challenges=[], observations={}, config={'profile': 'EXAMPLE'})


def run_req12(tmp_path, values, cfg):
    store = Store(tmp_path / 'store')
    env = environment(model={'id': 'm', 'revision': 'r'})
    ref = put_pack(store, PACK.path)
    pol = policy(ref, scope={'include': ['REQ-12']}, values={'REQ-12': values})
    store.put_many([env, pol])
    plan = select(PACK, [env], pol)
    store.put(plan)
    seen = []
    registry.EXECUTORS['request_termination:sim'] = lambda raw: Recording(seen)
    try:
        evd, = run_plan(store, PACK, plan, RunConfig(executors={'request_termination': 'sim'}, adapter='vllm', raw=cfg))
    finally:
        registry.EXECUTORS.pop('request_termination:sim')
    return env, evd, seen


def test_guard_run_binds_without_policy_values_and_records_the_binding(tmp_path):
    path = target_file(tmp_path)
    env, evd, seen = run_req12(tmp_path, {}, {'request_termination': {'target': str(path)}})
    assert evd.execution.status == ExecStatus.COMPLETED
    values, = seen
    assert values['attempt_environment_sha256'] == env.ref().sha256 == evd.environment.sha256
    assert values['attempt_target_sha256'] == sha256_hex(canonical_json(json.loads(path.read_text())))
    document = evd.execution.run_binding
    assert document['files'] == {'attempt_target_sha256': values['attempt_target_sha256']}
    assert document['validity']['start'] == values['attempt_validity_start']
    assert run_binding.verify(document, evd) == [] and evd.hash_ok()
    # The binding is not part of the executor configuration hash.
    assert evd.execution.executor.config_sha256 == sha256_hex(canonical_json({'profile': 'EXAMPLE'}))


def test_unbindable_run_is_not_run_with_a_fixed_code_and_keeps_unknown_semantics(tmp_path):
    _, evd, seen = run_req12(tmp_path, {}, {'request_termination': {'target': str(tmp_path / 'absent.json')}})
    assert seen == [] and evd.execution.status == ExecStatus.NOT_RUN
    assert evd.execution.abort_reason == 'RUN_BINDING_UNAVAILABLE'
    assert not evd.normal_control.attempted


def test_tampered_binding_document_is_reported_on_replay(tmp_path):
    _, evd, _ = run_req12(tmp_path, {}, {'request_termination': {'target': str(target_file(tmp_path))}})
    forged = {**evd.execution.run_binding, 'environment_sha256': 'f' * 64, 'check_id': 'REQ-01'}
    assert set(run_binding.verify(forged, evd)) == {'RUN_BINDING_ENVIRONMENT_MISMATCH', 'RUN_BINDING_SUBJECT_MISMATCH'}


def test_file_changed_after_binding_still_fails_the_executor_comparison(tmp_path):
    from guard.collectors import engine_callbacks
    from guard.collectors.file import load_environment
    from guard.executors import engine_sensor
    from tests.test_gpu_a_run_inputs import build, check
    build(tmp_path, 'v1-callback')
    out = check(tmp_path, 'REQ-18', 'req18-slot', 'run')
    env = load_environment(tmp_path / 'env.json')
    cfg = {'target': str(out / 'target.json'), 'prepared': str(tmp_path / 'prepared/manifest.json'),
           'manifest': str(out / 'manifest.json'), 'max_duration_seconds': 300}
    values = yaml.safe_load((out / 'policy.yaml').read_text())['values']['REQ-18']
    _, values = run_binding.effective_values(PACK.checks['REQ-18'], cfg, env, values, side='single', attempt_id=None,
                                             issued_at=utcnow())
    manifest = json.loads((out / 'manifest.json').read_text())
    manifest['cases'] = manifest['cases'][:-1]
    (out / 'manifest.json').write_text(json.dumps(manifest))
    executor = engine_sensor.EngineSensorExecutor(cfg)
    executor.transport = None
    unit = PACK.checks['REQ-18'].model_copy(deep=True)
    with pytest.raises(ValueError, match='ENGINE_SENSOR_NATIVE_BINDING_MISMATCH'):
        executor.run(unit, env, values, None, tmp_path / 'work')
    assert engine_callbacks.load_prepared(cfg['prepared'])


def test_policy_migration_drops_only_run_binding_keys():
    values = {'REQ-12': {'attempt_target_sha256': 'a' * 64}, 'REQ-01': {'allowed_callers': ['x']},
              'PLT-10': {'revocation_target_sha256': 'b' * 64, 'revocation_validity_start': 'T'}}
    assert run_binding.migrate_policy_values(values) == {'REQ-12': {}, 'REQ-01': {'allowed_callers': ['x']},
                                                         'PLT-10': {'revocation_validity_start': 'T'}}


def test_pack_migration_is_a_minimal_text_change(tmp_path):
    seed = tmp_path / 'pack34'
    import shutil
    shutil.copytree(PACK.path, seed)
    # Rebuild an @34-shaped REQ-12 from the current file, then migrate it again.
    path = seed / 'checks/REQ-12.yaml'
    text = path.read_text(encoding='utf-8')
    entries = ''.join(f'- key: {k}\n  type: str\n  required: true\n' for k in run_binding.KEYS_BY_CHECK['REQ-12'])
    text = text.replace('policy_params: []\n', 'policy_params:\n' + entries)
    path.write_text(text, encoding='utf-8')
    manifest = (seed / 'pack.yaml').read_text(encoding='utf-8')
    import re
    from hashlib import sha256
    manifest = re.sub(r'(- path: checks/REQ-12.yaml\n +sha256: )[0-9a-f]{64}',
                      lambda m: m.group(1) + sha256(path.read_bytes()).hexdigest(), manifest)
    (seed / 'pack.yaml').write_text(manifest, encoding='utf-8')
    migrated = run_binding.migrate_pack(seed, tmp_path / 'pack36')
    assert migrated.manifest.revision == PACK.manifest.revision + 1 == 40 and not migrated.reviewed
    assert migrated.checks['REQ-12'].revision == PACK.checks['REQ-12'].revision + 1
    assert (tmp_path / 'pack36/checks/REQ-12.yaml').read_text(encoding='utf-8').replace(
        f"revision: {PACK.checks['REQ-12'].revision + 1}\n", f"revision: {PACK.checks['REQ-12'].revision}\n", 1) == \
        (PACK.path / 'checks/REQ-12.yaml').read_text(encoding='utf-8')
    with pytest.raises(ValueError, match='ALREADY_APPLIED'):
        run_binding.migrate_pack(PACK.path, tmp_path / 'again')


def test_policy_migration_tool_keeps_enterprise_values_and_never_overwrites(tmp_path):
    from tools.migrate_policy_run_binding import main, migrate
    source = {'data_origin': 'DECLARED', 'status': 'CONFIRMED', 'confirmed_by': 'EXAMPLE-owner', 'confirmed_at': 'T',
              'pack': {'id': 'PACK-BASELINE', 'revision': 34},
              'values': {'REQ-12': {'attempt_environment_sha256': 'a' * 64, 'attempt_target_sha256': 'b' * 64},
                         'LIF-11': {'recovery_target_sha256': 'c' * 64, 'recovery_validity_start': 'S',
                                    'recovery_branch': 'runtime-failure'}}}
    out, removed = migrate(source, PACK)
    assert removed == {'REQ-12': ['attempt_environment_sha256', 'attempt_target_sha256'], 'LIF-11': ['recovery_target_sha256']}
    assert out['values'] == {'REQ-12': {}, 'LIF-11': {'recovery_validity_start': 'S', 'recovery_branch': 'runtime-failure'}}
    assert out['pack'] == {'id': 'PACK-BASELINE', 'revision': 39}
    assert out['status'] == 'DRAFT' and out['confirmed_by'] is None and source['status'] == 'CONFIRMED'
    path = tmp_path / 'policy.yaml'
    path.write_text(yaml.safe_dump(source))
    main([str(path), '--out', str(tmp_path / 'new.yaml')])
    assert yaml.safe_load((tmp_path / 'new.yaml').read_text()) == out
    with pytest.raises(SystemExit):
        main([str(path), '--out', str(tmp_path / 'new.yaml')])
    with pytest.raises(ValueError, match='SEALED_POLICY'):
        migrate({'kind': 'POL'}, PACK)


@pytest.mark.parametrize('change', [
    'missing', 'create-before-run', 'delete-before-run', 'create-after-recipe', 'delete-after-recipe',
])
def test_paired_req02_preserves_owned_record_for_recipe_bound_missing_native_preparation(
        tmp_path, external, monkeypatch, ordinary_gateway_record, change):  # noqa: F811
    import socket

    from guard.collectors.lifecycle import prepare_assets
    from guard.executors import kubernetes_route_record as route
    from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor
    from tests.test_a1_kubernetes_route_product import SESSION, World, stored_gateway_route
    from tests.test_kubernetes_request_binding import env
    from tools.mvp_versions import versions

    candidate = env()
    baseline_version = versions()['baseline']
    baseline = environment(id='ENV-ROUTE-BASELINE', model=candidate.model,
        engine={'name': baseline_version['engine'], 'version': baseline_version['version'],
                'image_digest': baseline_version['image']})
    world = World(candidate, 'normal')
    gateway_raw, _, _ = ordinary_gateway_record
    stored_gateway_route(world, candidate, gateway_raw)
    target = tmp_path / 'target.json'
    target.write_bytes(canonical_json(world.target))
    prepared = external / 'native-assets' / 'manifest.json'
    if change.startswith('delete-'):
        prepare_assets(prepared.parent, ROOT.parent)
    settings = {'target': str(target),
                'native_route': {'target': str(target), 'prepared': str(prepared)}}
    run_cfg = {'record_session': SESSION}
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'route_record': 'kubernetes'},
           'update': {'allowed_checks': ['REQ-02']},
           'sides': {'candidate': {'route_record': settings, 'run_binding': {'REQ-02': run_cfg}}}}
    envs = [baseline, candidate]
    pol = policy(PACK.ref(), scope={'include': ['REQ-02']}, values={})
    store = Store(tmp_path / 'store')
    put_pack(store, PACK.path)
    store.put_many([*envs, pol])
    selected = select(PACK, envs, pol, mode='update')
    plan = bind_recipe(selected, cfg, envs)
    store.put(plan)
    if change == 'missing':
        # The default offline preflight requires complete inputs.
        with pytest.raises(run_binding.RunBindingUnavailable, match='^RUN_BINDING_UNAVAILABLE$'):
            run_binding.effective_values(PACK.checks['REQ-02'], settings, candidate, {}, side='candidate',
                                         attempt_id=None, issued_at=utcnow(), run_cfg=run_cfg)
    if change == 'create-before-run':
        prepare_assets(prepared.parent, ROOT.parent)
    elif change == 'delete-before-run':
        prepared.unlink()
    elif change.endswith('-after-recipe'):
        original = run_binding.effective_values

        def change_after_recipe(*args, **kwargs):
            assert kwargs.get('native_route_prepared_absent', False) is change.startswith('create-')
            if change == 'create-after-recipe':
                prepare_assets(prepared.parent, ROOT.parent)
            else:
                prepared.unlink()
            return original(*args, **kwargs)

        monkeypatch.setattr(run_binding, 'effective_values', change_after_recipe)

    def forbid_runtime(*args, **kwargs):
        pytest.fail('REQ-02 retained record must not dispatch an external or native request')

    monkeypatch.setattr(subprocess, 'Popen', forbid_runtime)
    monkeypatch.setattr(socket, 'socket', forbid_runtime)
    monkeypatch.setattr(KubernetesNativeRouteExecutor, '__init__', forbid_runtime)
    monkeypatch.setattr(route, 'capture_pipes', world.runner)
    monkeypatch.setattr('guard.runtime.kubernetes_http.capture_pipes', world.runner)

    run_settings = application.run_config(cfg)
    if change.endswith('-before-run'):
        with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
            run_under_plan_lock(store, PACK, plan, run_settings, only_side=Side.candidate)
        assert not list(store.iter_kind('EVD')) and world.reads == 0
        return

    evd, = run_under_plan_lock(store, PACK, plan, run_settings, only_side=Side.candidate)
    if change == 'delete-after-recipe':
        assert evd.execution.status == ExecStatus.NOT_RUN
        assert evd.execution.abort_reason == 'RUN_BINDING_UNAVAILABLE'
        assert not evd.artifacts and world.reads == 0
        return

    assert world.reads == 1
    assert evd.execution.status == ExecStatus.ABORTED and evd.data_origin == DataOrigin.EXAMPLE
    assert evd.observations['route_record_collected'].value is True
    assert evd.observations['route_producer_authenticated'].value is False
    assert evd.observations['native_route_trials'].collected is False
    assert evd.observations['composite_subcollections'].value['failed'] == {'native_route': 'SUBCOLLECTION_UNAVAILABLE'}
    binding = evd.execution.run_binding
    assert binding['files'] == {'route_target_sha256': sha256_hex(target.read_bytes())}
    assert binding['values'] == {'route_session': SESSION} and run_binding.verify(binding, evd) == []
    artifact, = evd.artifacts
    record = json.loads((store.root / artifact.path).read_bytes().splitlines()[-1])['record']
    assert record['file_bytes_sha256'] == sha256_hex(world.raw)
    assert record['owner_binding_verified'] is True
    assert store.resolve(evd.ref()).to_dict() == evd.to_dict()


@pytest.mark.parametrize(('defect', 'absent'), [
    ('missing-asset', False), ('malformed', False), ('dangling-symlink', False),
    ('missing-native-target', True), ('malformed-native-target', True), ('foreign-environment', True),
])
def test_req02_record_only_binding_requires_the_declared_preparation_itself_to_be_absent(
        external, defect, absent):  # noqa: F811
    from guard.collectors.lifecycle import prepare_assets
    from tests.test_kubernetes_request_binding import env, target

    selected_env = env()
    record_target = target_file(external, target(selected_env))
    prepared = external / 'assets' / 'manifest.json'
    if defect == 'missing-asset':
        prepare_assets(prepared.parent, ROOT.parent)
        (prepared.parent / 'sitecustomize.py').unlink()
    elif defect == 'malformed':
        prepared.parent.mkdir()
        prepared.write_bytes(b'{')
    elif defect == 'dangling-symlink':
        prepared.parent.mkdir()
        prepared.symlink_to('missing.json')
    native_target = external / 'native-target.json'
    if defect == 'malformed-native-target':
        native_target.write_bytes(b'[]')
    elif defect == 'foreign-environment':
        native_target.write_bytes(canonical_json({**target(selected_env), 'environment_ref': {}}))
    elif defect != 'missing-native-target':
        native_target.write_bytes(record_target.read_bytes())
    settings = {'target': str(record_target),
                'native_route': {'target': str(native_target), 'prepared': str(prepared)}}
    assert run_binding.native_route_preparation_missing(settings) is absent
    with pytest.raises(run_binding.RunBindingUnavailable, match='^RUN_BINDING_UNAVAILABLE$'):
        run_binding.compute(PACK.checks['REQ-02'], settings, selected_env, side='candidate', attempt_id='attempt',
                            issued_at=utcnow(), run_cfg={'record_session': 'ab' * 16},
                            native_route_prepared_absent=absent)


@pytest.mark.parametrize('defect', [None, 'undeclared', 'corrupt-manifest', 'changed-manifest', 'wrong-record-target'])
def test_prepare_update_checks_only_the_declared_req02_record_pair(
        tmp_path, monkeypatch, ordinary_gateway_record, defect):
    import socket

    from typer.testing import CliRunner
    from guard.cli import app
    from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor
    from tests.test_a1_kubernetes_route_product import SESSION, World, stored_gateway_route
    from tests.test_kubernetes_request_binding import env
    from tools.j02_run_config import write_config
    from tools.mvp_versions import versions

    parsed_candidate = env()
    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'route_record': 'kubernetes'},
           'update': {'mode': 'external', 'kubernetes_candidate_binding': 'prebound',
                      'allowed_checks': ['REQ-02']}, 'sides': {}}
    worlds, environments = {}, {}
    gateway_raw, _, _ = ordinary_gateway_record
    for side, version in versions().items():
        selected = environment(id='ENV-ROUTE-' + side.upper(), model=parsed_candidate.model,
            engine={'name': version['engine'], 'version': version['version'], 'image_digest': version['image']})
        world = worlds[side] = World(selected, 'normal')
        world.target['observed_version'] = selected.engine.version
        world.target['engine']['image_digest'] = selected.engine.image_digest
        world.target['kubernetes']['engine'].update(image=selected.engine.image_digest,
                                                    image_id=selected.engine.image_digest)
        stored_gateway_route(world, selected, gateway_raw)
        environment_file = environments[side] = tmp_path / (side + '-env.json')
        environment_file.write_bytes(canonical_json(selected.to_dict()))
        target = tmp_path / (side + '-target.json')
        target.write_bytes(canonical_json(world.target))
        cfg['sides'][side] = {'route_record': {'target': str(target),
            'native_route': {'target': str(target), 'prepared': str(tmp_path / (side + '-native.json'))}},
            'run_binding': {'REQ-02': {'record_session': SESSION}}}

    candidate = cfg['sides']['candidate']['route_record']
    manifest = Path(candidate['native_route']['prepared'])
    if defect == 'undeclared':
        candidate.pop('native_route')
    elif defect == 'corrupt-manifest':
        manifest.write_bytes(b'{')
    elif defect == 'changed-manifest':
        effective_values = run_binding.effective_values

        def change_after_binding(*args, **kwargs):
            result = effective_values(*args, **kwargs)
            if kwargs.get('side') == 'candidate':
                manifest.write_bytes(b'{')
            return result

        monkeypatch.setattr(run_binding, 'effective_values', change_after_binding)
    elif defect == 'wrong-record-target':
        candidate['target'] = cfg['sides']['baseline']['route_record']['target']
    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    pol = policy(PACK.ref(), scope={'include': ['REQ-02']}, values={})
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(cfg, config_path)

    def forbidden(*args, **kwargs):
        pytest.fail('Static REQ-02 preparation must not dispatch or construct a native collector')

    monkeypatch.setattr(KubernetesNativeRouteExecutor, '__init__', forbidden)
    monkeypatch.setattr(application, 'Docker', forbidden)
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = CliRunner().invoke(app, ['prepare-update', '--pack', str(PACK.path),
        '--baseline', str(environments['baseline']), '--candidate', str(environments['candidate']),
        '--policy', str(policy_path), '--config', str(config_path)])
    ready = json.loads(result.stdout)
    assert result.exit_code == (0 if defect is None else 1), result.output
    assert ready['execution_scope']['static_preparation_ready'] is (defect is None)
    assert all(world.reads == 0 for world in worlds.values())


def _supply_pair(external, check_id):
    from tests.test_sup15_owned_record_admission import _prepare_roles
    from tests.test_supply_boot_record_access import VALUES as example_policy_values
    from tools.mvp_versions import versions

    cfg = {'adapter': {'name': 'vllm'}, 'executors': {'supply_boot': 'kubernetes'},
           'update': {'allowed_checks': [check_id]}, 'sides': {}}
    envs = []
    for index, (side, version) in enumerate(versions().items()):
        root = external / side
        root.mkdir()
        env = environment(id=f'ENV-SUPPLY-RECIPE-{side.upper()}',
            engine={'name': version['engine'], 'version': version['version'], 'image_digest': version['image']},
            runtime={'gpu_count': 1, 'gpu_model': 'EXAMPLE GPU', 'driver': 'EXAMPLE driver'})
        envs.append(env)
        settings, _, _, _ = _prepare_roles(root, env, late_owner_change=False, first_owner=1 + 2 * index)
        producer = root / 'exact-producer.py'
        producer.write_bytes(b'# EXAMPLE identity only\n')
        cfg['sides'][side] = {'check_config': {check_id: settings},
                             'run_binding': {check_id: {'record_producer': str(producer)}}}
    row = {param.key: example_policy_values[param.key] for param in PACK.checks[check_id].policy_params}
    values = {check_id: row} if row else {}
    return envs, policy(PACK.ref(), scope={'include': [check_id]}, values=values), cfg


@pytest.mark.parametrize(('check_id', 'candidate_issue', 'reason'), [
    *((check_id, None, None) for check_id, check in PACK.checks.items()
      if check.executor == 'supply_boot' and not check.retired),
    ('SUP-06', 'foreign-environment', 'SUPPLY_BOOT_INPUT_UNAVAILABLE'),
    ('SUP-09', 'foreign-environment', 'SUPPLY_BOOT_INPUT_UNAVAILABLE'),
    ('SUP-11', 'same-record-location', 'SUPPLY_BOOT_DISTINCT_ROLE_INPUTS_REQUIRED'),
])
def test_offline_readiness_validates_nested_supply_roles(external, monkeypatch, check_id, candidate_issue, reason):  # noqa: F811
    from tools import mvp_live_readiness
    from tools.j02_run_config import write_config

    def forbid_runtime_request(*args, **kwargs):
        pytest.fail('supply file readiness must not make a runtime request')

    monkeypatch.setattr(subprocess, 'Popen', forbid_runtime_request)
    envs, pol, cfg = _supply_pair(external, check_id)
    roles = cfg['sides']['candidate']['check_config'][check_id]
    if candidate_issue == 'foreign-environment':
        path = Path(roles['challenge']['target'])
        target = json.loads(path.read_bytes())
        target['environment_ref'] = envs[0].ref().model_dump(mode='json')
        path.write_bytes(canonical_json(target))
    elif candidate_issue == 'same-record-location':
        roles['challenge'] = dict(roles['normal'])

    ready = application.update_readiness(PACK, envs, pol, cfg)
    inputs_result = next(row for row in ready['checks'] if row['check'] == 'inputs')
    assert inputs_result['ok'] is (reason is None), ready
    if reason:
        assert inputs_result['detail'] == reason
    config_path, policy_path = external / 'guard.toml', external / 'policy.json'
    write_config(cfg, config_path)
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    index = mvp_live_readiness.template(PACK)
    for side, env in zip(cfg['sides'], envs):
        env_path = external / side / 'env.json'
        env_path.write_bytes(canonical_json(env.to_dict()))
        inputs = {role: {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}
                  for role, path in {'configuration': config_path, 'policy': policy_path, 'environment': env_path}.items()}
        index['inputs'].append({'check_id': check_id, 'side': side, **inputs})
    index_path = external / 'index.json'
    index_path.write_bytes(canonical_json(index))
    report = mvp_live_readiness.audit(index_path, pack=PACK)
    assert report['sides'] == 138 and report['file_binding_valid_sides'] == (1 if reason else 2)
    for row in report['rows']:
        if row['check_id'] == check_id:
            if reason and row['side'] == 'candidate':
                assert not row['file_binding_valid'], row
                assert row['blocking_codes'] == [reason]
            else:
                assert row['file_binding_valid'], row
            assert row['live_target_verified'] is False
            assert row['engine_executed'] is row['ready_for_execution'] is False


def test_paired_supply_readiness_does_not_reinterpret_normal_legacy_or_unpaired_settings():
    from guard.configuration import KUBERNETES_BOUND_CHECK_OPTIONS
    from guard.models.checkdef import PolicyParam

    roles = {'normal': {}, 'challenge': {}}
    for check in PACK.checks.values():
        if check.executor == 'supply_boot' and (option := KUBERNETES_BOUND_CHECK_OPTIONS.get(check.id)):
            assert run_binding.is_paired_supply_role_preflight(check, roles, {})
            for value in (True, False):
                assert not run_binding.is_paired_supply_role_preflight(check, {**roles, option: value}, {})
    check = PACK.checks['SUP-06']
    old = check.model_copy(update={'policy_params': [PolicyParam(key='supply_environment_sha256', type='str')]})
    assert not run_binding.is_paired_supply_role_preflight(old, roles, {})
    assert not run_binding.is_paired_supply_role_preflight(check, {'target': 'unused'}, {})
    assert not run_binding.is_paired_supply_role_preflight(check, roles, {'kubernetes_candidate_binding': 'after_baseline'})
    assert not run_binding.is_paired_supply_role_preflight(check, roles, {'mode': 'managed'})


@pytest.mark.parametrize('check_id', ['SUP-15', 'SUP-16'])
@pytest.mark.parametrize(('phase', 'field', 'reason'), [
    ('before-recipe', 'challenge', None),
    ('before-recipe', 'record_producer', None),
    ('before-binding', 'challenge', 'RUN_BINDING_SUPPLY_CHALLENGE_PREPARED_CHANGED'),
    ('before-binding', 'record_producer', 'RUN_BINDING_SUPPLY_RECORD_PRODUCER_CHANGED'),
    ('after-binding', 'challenge', None),
])
def test_paired_recipe_binds_raw_supply_role_files_until_consumed(
        external, tmp_path, monkeypatch, check_id, phase, field, reason):  # noqa: F811
    from guard.core import run as core_run

    def forbid_runtime_request(*args, **kwargs):
        pytest.fail('The supply role fixture must not make a runtime request')

    envs, pol, cfg = _supply_pair(external, check_id)
    store = Store(tmp_path / 'store')
    put_pack(store, PACK.path)
    store.put_many([*envs, pol])
    plan = bind_recipe(select(PACK, envs, pol, mode='update'), cfg, envs)
    store.put(plan)
    digests = {}
    assert configuration_sha256(cfg, envs, plan=plan, supply_record_digests=digests) == plan.inputs.execution_recipe_sha256
    assert set(digests) == {f'baseline:{check_id}', f'candidate:{check_id}'}
    candidate = cfg['sides']['candidate']
    settings = candidate['check_config'][check_id]
    path = Path(candidate['run_binding'][check_id]['record_producer']
                if field == 'record_producer' else settings[field]['prepared'])
    assert digests[f'candidate:{check_id}'][field] == sha256_hex(path.read_bytes())
    unchanged_document = json.loads(path.read_bytes()) if field != 'record_producer' else None

    def rewrite():
        path.write_bytes(path.read_bytes() + b'\n')
        if unchanged_document is not None:
            assert json.loads(path.read_bytes()) == unchanged_document

    monkeypatch.setattr(subprocess, 'Popen', forbid_runtime_request)
    seen = []

    def role_reader(executor, target, env, prepared):
        seen.append(target['run_id'])
        raise ValueError('EXAMPLE detached reader')

    monkeypatch.setattr(KubernetesSupplyBootExecutor, '_role_reader', role_reader)
    run_cfg = application.run_config(cfg)
    if phase == 'before-recipe':
        rewrite()
        with pytest.raises(ValueError, match='^EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED$'):
            run_under_plan_lock(store, PACK, plan, run_cfg, only_side=Side.candidate)
        assert seen == []
        return
    if phase == 'before-binding':
        run_one = core_run._run_one

        def rewrite_before_binding(*args, **kwargs):
            rewrite()
            return run_one(*args, **kwargs)

        monkeypatch.setattr(core_run, '_run_one', rewrite_before_binding)
    else:
        binding = run_binding.effective_values

        def rewrite_after_binding(*args, **kwargs):
            result = binding(*args, **kwargs)
            rewrite()
            return result

        monkeypatch.setattr(run_binding, 'effective_values', rewrite_after_binding)
    evd, = run_under_plan_lock(store, PACK, plan, run_cfg, only_side=Side.candidate)
    if phase == 'before-binding':
        assert seen == []
        assert evd.execution.status == ExecStatus.NOT_RUN and evd.execution.abort_reason == reason
        assert evd.execution.run_binding is None
    else:
        assert seen == ['guard-normal']
        assert evd.execution.status == ExecStatus.ABORTED
        assert evd.execution.run_binding['files']['supply_challenge_prepared_sha256'] == sha256_hex(canonical_json(unchanged_document))
        normal, challenge = evd.observations['supply_boot_sessions'].value
        assert normal['diagnostic_codes'] == ['SUPPLY_BOOT_PREFLIGHT_UNAVAILABLE']
        assert challenge['diagnostic_codes'] == ['SUPPLY_BOOT_INPUT_UNAVAILABLE']
