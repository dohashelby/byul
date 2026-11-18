"""Exact paired metadata config through the same side/check merge used at runtime."""
from copy import deepcopy
import json
import socket
import subprocess
import sys
import tomllib

import pytest

from guard.configuration import for_check, merge_config
from guard.executors import kubernetes_template_supply as template_executor, registry
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex
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
    assert readiness['file_binding_valid_sides'] == (6 if missing_template else 8)
    assert readiness['ready_for_execution'] is False and readiness['actual_results'] is None
    assert all(not row['engine_executed'] and row['actual_results'] is None for row in readiness['rows'])
    assert readiness['metadata_wiring']['SUP-03']['sides']['candidate'] == (
        'CONFIGURED_TEMPLATE_TARGET_REQUIRED' if missing_template else None)


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
            for check_id in ('REQ-07', 'SUP-13'):
                decision = checks['SUP-03']['shared_executor_checks'][check_id][side]
                assert decision == {'expected_if_invoked': 'ABORTED', 'reason': 'EXECUTOR_FAILURE',
                                    'transport_dispatched': False}
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
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, env, pointer) == dict.fromkeys(
        config['executors'], sha256_hex(canonical_json(current)))
    assert json.loads(path.read_bytes()) == preliminary
    current['comparison_config']['launch']['max_model_len'] += 1
    _write(fresh, current)
    with pytest.raises(ValueError, match='METADATA_CANDIDATE_CONDITIONS_CHANGED'):
        tool.revalidate_candidate(staged, env, pointer)


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
