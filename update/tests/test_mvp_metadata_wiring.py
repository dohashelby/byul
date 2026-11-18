"""Exact paired metadata config through the same side/check merge used at runtime."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tomllib

import pytest

from guard.configuration import for_check, merge_config, update_allowed_checks
from guard import application
from guard.core.execution_recipe import bind_recipe, configuration_sha256
from guard.core.select import select
from guard.executors import kubernetes_template_supply as template_executor, registry
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex, utcnow
from guard.store import Store
from tests.builders import T0, policy
from tests.test_model_preparation_integration import full_inputs
from tests.test_template_supply_approval import SHA
from tools import mvp_metadata_wiring as tool


def _write(path, obj):
    path.write_bytes(canonical_json(obj))
    return path


@pytest.fixture
def sources(tmp_path):
    pack = load_pack(tool.readiness.ROOT / 'packs/PACK-BASELINE')
    release = tool.versions()
    seed_env, original_target = full_inputs()
    sides = {}
    for side, identity in release.items():
        env = seed_env.model_copy(update={
            'id': f'ENV-EXAMPLE-{side.upper()}', 'data_origin': DataOrigin.DECLARED,
            'engine': seed_env.engine.model_copy(update={'name': identity['engine'], 'version': identity['version'],
                                                         'image_digest': identity['image']}),
            'runtime': seed_env.runtime.model_copy(update={'gpu_count': 1, 'gpu_model': 'Fixture GPU',
                                                           'driver': 'Fixture driver', 'tp_size': 1}),
            'model': seed_env.model.model_copy(update={'inventory_sha256': 'a' * 64}),
        }).seal()
        target = deepcopy(original_target)
        target['environment_ref'] = env.ref().model_dump(mode='json')
        target['model_mapping'].update(environment_ref=target['environment_ref'], inventory_sha256=env.model.inventory_sha256)
        target['engine']['image_digest'] = identity['image']
        target['kubernetes']['engine'].update(image=identity['image'], image_id=identity['image'])
        target.update(lab=False, observed_version=identity['version'])
        sides[side] = {'environment': _write(tmp_path / f'{side}-env.json', env.to_dict()),
                       'target': _write(tmp_path / f'{side}-target.json', target)}
    confirmed = policy(pack.ref(), status='CONFIRMED', data_origin='DECLARED',
        confirmed_by='Fixture owner', confirmed_at=T0, values={
            'SUP-04': {'approved_image_digests': [d['image'] for d in release.values()]},
            'PLT-01': {'allowed_capabilities': [], 'allowed_mounts': [], 'allowed_devices': [],
                       'require_non_root': True, 'allowed_exec_writable_paths': []},
            'PLT-04': {'forbidden_env_names': ['FIXTURE_NAME'], 'forbidden_mount_paths': ['/fixture'],
                       'credential_candidate_paths': ['/fixture/candidate']},
            'SUP-03': {'allowed_template_sha256': [SHA]},
            'REQ-07': {'allowed_template_sha256': [SHA], 'max_rendered_input_tokens': 128,
                       'request_template_override_allowed': False},
            'SUP-13': {'forbidden_template_attributes': []},
        })
    return pack, sides, _write(tmp_path / 'policy.json', confirmed.to_dict())


@pytest.mark.parametrize('missing_template', [False, True])
def test_offline_cli_emits_product_config_and_all_138_rows(tmp_path, monkeypatch, sources, missing_template):
    pack, sides, policy_path = sources
    if missing_template:
        target_path = sides['candidate']['target']
        target = json.loads(target_path.read_bytes())
        target['comparison_config'].pop('launch')
        _write(target_path, target)
    def forbidden(*args, **kwargs):
        raise AssertionError('No network or subprocess during file assembly')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    output, store = tmp_path / 'assembled', tmp_path / 'store'
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store), '--output', str(output),
            '--node-image-verification', 'local-k3s-containerd-v1']
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    assert not store.exists()
    config = tomllib.loads((output / 'guard.toml').read_text())
    assert config['executors'] == {
        'container_config': 'kubernetes', 'credential_exposure': 'kubernetes',
        **({} if missing_template else {'template_validation': 'kubernetes'}),
    }
    for side, paths in sides.items():
        merged = merge_config(config, config['sides'][side])
        assert for_check(merged, 'credential_exposure', 'PLT-04')['credential_exposure'] == {'target': str(paths['target'])}
        assert for_check(merged, 'container_config', 'SUP-04')['container_config']['node_image_verification'] == 'local-k3s-containerd-v1'
        assert 'node_image_verification' not in for_check(merged, 'container_config', 'PLT-01')['container_config']
        assert not ({'lab_activation', 'record_attestation', '_runtime_environ'} & set(merged))
    readiness = json.loads((output / 'readiness.json').read_bytes())
    assert readiness['sides'] == 138 and readiness['active_checks'] == 69
    assert readiness['file_binding_valid_sides'] == (6 if missing_template else 10)
    assert readiness['ready_for_execution'] is False and readiness['actual_results'] is None
    assert all(not row['engine_executed'] and row['actual_results'] is None for row in readiness['rows'])
    assert readiness['metadata_wiring']['SUP-03']['sides']['candidate'] == (
        'CONFIGURED_TEMPLATE_TARGET_REQUIRED' if missing_template else None)
    assert config['check_config'].get('REQ-07') == (None if missing_template else {'req07_normal_only': True})


def test_a_target_from_the_other_release_cannot_be_reused(tmp_path, sources):
    pack, sides, policy_path = sources
    sides['candidate']['target'] = sides['baseline']['target']
    with pytest.raises(ValueError, match='METADATA_TARGET_BINDING_INVALID'):
        tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')


def test_shared_template_siblings_stop_before_any_target_or_transport_access(tmp_path, monkeypatch, sources):
    pack, sides, policy_path = sources
    config, _, checks = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    assert config['executors']['template_validation'] == 'kubernetes'
    assert checks['SUP-03']['configuration_enabled']
    for side, paths in sides.items():
        env = tool.Environment.model_validate_json(paths['environment'].read_bytes())
        executor = registry.build_executor('template_validation', 'kubernetes',
            merge_config(config, config['sides'][side]), engine=env.engine.name, version=env.engine.version)
        assert type(executor) is template_executor.KubernetesTemplateSupplyExecutor
        def forbidden(*args, **kwargs):
            raise AssertionError('unsupported siblings may never access or dispatch to a target')
        with monkeypatch.context() as guard:
            guard.setattr(template_executor.Path, 'open', forbidden)
            guard.setattr(template_executor, 'KubernetesHTTP', forbidden)
            decision = checks['SUP-03']['shared_executor_checks']['SUP-13'][side]
            assert decision == {'expected_if_invoked': 'NOT_RUN', 'reason': 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE',
                                'transport_dispatched': False}
            for check_id in ('REQ-07', 'SUP-13'):
                with pytest.raises(ValueError, match='KUBERNETES_TEMPLATE_PROFILE_UNSUPPORTED'):
                    executor.run(pack.checks[check_id], env, {}, object(), tmp_path / 'unused')


def test_staged_configuration_requires_a_fresh_candidate_before_hash_binding(tmp_path, sources):
    pack, sides, policy_path = sources
    config, _, _ = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    path = sides['candidate']['target']
    preliminary, pointer = tool._source(path)
    env = tool.Environment.model_validate_json(sides['candidate']['environment'].read_bytes())
    with pytest.raises(ValueError, match='METADATA_FRESH_CANDIDATE_PATH_REQUIRED'):
        tool.staged_config(config, path)
    fresh = tmp_path / 'fresh-candidate.json'
    staged = tool.staged_config(config, fresh)
    assert staged['update']['kubernetes_candidate_binding'] == 'after_baseline'
    assert config['sides']['candidate']['credential_exposure']['target'] == str(path)
    assert staged['sides']['candidate']['credential_exposure']['target'] == str(fresh)
    _write(fresh, preliminary)
    with pytest.raises(ValueError, match='METADATA_CANDIDATE_GENERATION_NOT_FRESH'):
        tool.revalidate_candidate(staged, env, pointer)
    current = deepcopy(preliminary)
    for number, role in enumerate(('engine', 'probe'), 100):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, env, pointer) == dict.fromkeys(
        config['executors'], sha256_hex(canonical_json(current)))
    assert json.loads(path.read_bytes()) == preliminary
    for ownership_change in ('context', 'namespace_uid'):
        moved = deepcopy(current)
        if ownership_change == 'context':
            moved['kubernetes']['context'] = 'fixture-unrelated-context'
        else:
            moved['kubernetes']['probe']['namespace_uid'] = '00000000-0000-0000-0000-000000000120'
        _write(fresh, moved)
        with pytest.raises(ValueError, match='METADATA_CANDIDATE_OWNERSHIP_CHANGED'):
            tool.revalidate_candidate(staged, env, pointer)
    current['comparison_config']['launch']['max_model_len'] += 1
    _write(fresh, current)
    with pytest.raises(ValueError, match='METADATA_CANDIDATE_CONDITIONS_CHANGED'):
        tool.revalidate_candidate(staged, env, pointer)


def test_template_wiring_requires_the_configured_hash_in_approved_policy(tmp_path, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {**original.values, 'SUP-03': {'allowed_template_sha256': []},
              'REQ-07': {**original.values['REQ-07'], 'allowed_template_sha256': ['f' * 64]}}
    _write(policy_path, original.model_copy(update={'values': values}).seal().to_dict())
    config, _, checks = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    assert 'template_validation' not in config['executors']
    assert set(checks['SUP-03']['sides'].values()) == {'CONFIGURED_TEMPLATE_POLICY_APPROVAL_MISSING'}


def test_missing_shared_policy_blocks_both_container_checks_without_filling_values(tmp_path, sources):
    pack, sides, policy_path = sources
    document, _ = tool._source(policy_path)
    original = tool.Policy.model_validate(document)
    values = {key: value for key, value in original.values.items() if key != 'SUP-04'}
    revised = original.model_copy(update={'values': values}).seal()
    _write(policy_path, revised.to_dict())
    config, _, checks = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    assert 'container_config' not in config['executors']
    assert checks['SUP-04']['sides']['baseline'] == 'MVP_POLICY_VALUE_MISSING'
    assert checks['PLT-01']['sides']['baseline'] == 'SHARED_CONTAINER_CHECK_INPUT_UNAVAILABLE'


def test_opt_in_plt08_seals_the_same_plt01_target_in_prebound_and_staged_inputs(tmp_path, monkeypatch, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {**original.values, 'PLT-08': {'plaintext_forbidden': True}}
    _write(policy_path, original.model_copy(update={'values': values}).seal().to_dict())
    store = tmp_path / 'store'
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store)
    assert 'PLT-08' not in default['update']['allowed_checks']
    assert 'api_transport' not in default['executors']

    output, fresh = tmp_path / 'passive-wiring', tmp_path / 'fresh-target.json'
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store), '--output', str(output),
            '--passive-plt08', '--candidate-target-after-baseline', str(fresh)]
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    prebound = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert prebound['check_config']['PLT-08'] == {'passive_from_plt01': True}
    assert {'PLT-01', 'PLT-08'} <= set(prebound['update']['allowed_checks'])
    assert 'PLT-09' not in prebound['update']['allowed_checks']
    for side, settings in prebound['sides'].items():
        assert settings['api_transport'] == settings['container_config'] == {'target': str(sides[side]['target'])}
    readiness = json.loads((output / 'readiness.json').read_bytes())
    rows = [row for row in readiness['rows'] if row['check_id'] == 'PLT-08']
    assert len(rows) == 2
    for row in rows:
        assert row['file_binding_valid'] and row['executor_policy_valid']
        assert 'SAME_ATTEMPT_PLT01_CONTROL_REQUIRED' in row['blocking_codes']
        assert not row['ready_for_execution'] and not row['engine_executed']

    staged = tomllib.loads((output / 'guard.toml').read_text())
    target = json.loads(sides['candidate']['target'].read_bytes())
    for number, role in enumerate(('engine', 'probe'), 100):
        target['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        target['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, target)
    env = tool.Environment.model_validate_json(sides['candidate']['environment'].read_bytes())
    hashes = tool.revalidate_candidate(staged, env, readiness['candidate_handoff']['preliminary_target'])
    assert hashes['api_transport'] == hashes['container_config'] == sha256_hex(canonical_json(target))

    values.pop('PLT-01')
    _write(policy_path, original.model_copy(update={'values': values}).seal().to_dict())
    blocked, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=store, passive_plt08=True)
    assert 'api_transport' not in blocked['executors']
    assert set(decisions['PLT-08']['sides'].values()) == {'PASSIVE_API_HOP_SOURCE_NOT_ADMITTED'}


def test_local_metadata_scope_pins_public_update_targets_and_withholds_shared_siblings(tmp_path, monkeypatch, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    policy_document = json.loads((tool.readiness.ROOT / 'docs/dev-1/mvp-enterprise-policy-20261116.json').read_bytes())
    related = ['OBS-04', 'OBS-05', 'PLT-07', 'PLT-06', 'PLT-02']
    values = deepcopy(original.values)
    for row in policy_document['decisions']:
        if row['check_id'] in related:
            values.setdefault(row['check_id'], {})[row['key']] = row['value']
    pol = original.model_copy(update={'values': values, 'scope': original.scope.model_copy(update={'include': related})}).seal()
    _write(policy_path, pol.to_dict())
    config, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    assert set(config['update']['allowed_checks']) == {'OBS-04', 'PLT-06'}
    assert update_allowed_checks(config, pack) == {'OBS-04', 'PLT-06'}
    mixed = deepcopy(config)
    mixed['sides']['candidate']['update'] = {'allowed_checks': related}
    with pytest.raises(ValueError, match='UPDATE_ALLOWED_CHECKS_MUST_BE_COMMON'):
        update_allowed_checks(mixed, pack)
    assert config['check_config'] == {'OBS-04': {'local_observation_only': True},
                                      'PLT-06': {'worker_trust_metadata_only': True}}
    for anchor, siblings in (('OBS-04', ('OBS-05', 'PLT-07')), ('PLT-06', ('PLT-02',))):
        for check_id in siblings:
            for decision in decisions[anchor]['shared_executor_checks'][check_id].values():
                assert decision == {'expected_if_invoked': 'NOT_RUN', 'reason': 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE',
                                    'transport_dispatched': False}

    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    readiness = application.update_readiness(pack, envs, pol, config)
    assert readiness['ready'] is False
    assert any(row['check'] == 'execution-scope' and not row['ok'] for row in readiness['checks'])
    assert all('UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE' in row['reasons'] for row in readiness['check_support']
               if row['check_id'] in {'OBS-05', 'PLT-07', 'PLT-02'})
    expected = {env.id: sha256_hex(canonical_json(json.loads(sides[side]['target'].read_bytes())))
                for side, env in zip(sides, envs)}
    seen = []

    class Example:
        version = '1'

        def __init__(self, name, raw):
            self.name = name
            self.target_hash = raw['_kubernetes_target_sha256']

        def run(self, check, env, *_args):
            assert check.id in {'OBS-04', 'PLT-06'} and self.target_hash == expected[env.id]
            seen.append((check.id, env.id))
            return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                                 normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    for name in config['executors']:
        monkeypatch.setitem(registry.EXECUTORS, f'{name}:kubernetes', lambda raw, name=name: Example(name, raw))
    store = Store(tmp_path / 'store')
    report, _ = application.execute_update(store, pack, envs, pol, config, tmp_path / 'update', utcnow())
    assert set(seen) == {(cid, env.id) for cid in ('OBS-04', 'PLT-06') for env in envs}
    assert all(next(row for row in report.by_check if row.check_id == cid).selected for cid in related)
    for row in store.iter_kind('EVD'):
        if row.check_id in {'OBS-05', 'PLT-07', 'PLT-02'}:
            assert row.execution.status == ExecStatus.NOT_RUN
            assert row.execution.abort_reason == 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE'


def test_bounded_prebound_target_identity_uses_the_recipe_snapshot(monkeypatch, sources):
    pack, sides, policy_path = sources
    target_path = sides['baseline']['target']
    original_bytes = target_path.read_bytes()
    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    pol = tool.Policy.model_validate_json(policy_path.read_bytes())
    cfg = {'update': {'allowed_checks': ['OBS-04']}, 'executors': {'boundary_egress': 'kubernetes'},
           'boundary_egress': {'target': str(target_path)},
           'check_config': {'OBS-04': {'target': str(target_path), 'local_observation_only': True}}}
    plan = select(pack, envs, pol, mode='update')
    expected_recipe = configuration_sha256(cfg, envs)
    paths = application._bounded_kubernetes_targets(cfg, {'OBS-04': 'boundary_egress'})
    snapshots = {target_path: None}
    reads = []
    read_bytes = Path.read_bytes

    def counted(path):
        if path == target_path:
            reads.append(path)
        return read_bytes(path)

    with monkeypatch.context() as guard:
        guard.setattr(Path, 'read_bytes', counted)
        bound = bind_recipe(plan, cfg, envs, target_snapshots=snapshots)
        assert reads == [target_path]
        assert snapshots[target_path] == original_bytes
        assert bound.inputs.execution_recipe_sha256 == expected_recipe
        _write(target_path, {'schema': 'replaced-source-fixture'})
        application._pin_prebound_kubernetes_targets(cfg, envs[0], paths, snapshots)
        assert reads == [target_path]
    assert cfg['_kubernetes_target_sha256_by_executor'] == {
        'boundary_egress': sha256_hex(canonical_json(json.loads(original_bytes)))}
    assert configuration_sha256(cfg, envs) != expected_recipe
