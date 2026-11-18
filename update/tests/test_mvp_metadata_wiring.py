"""Exact paired metadata config through the same side/check merge used at runtime."""
from copy import deepcopy
import json
import socket
import subprocess
import sys
import tomllib

import pytest

from guard.configuration import for_check, merge_config
from guard.models import DataOrigin, canonical_json, load_pack
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
