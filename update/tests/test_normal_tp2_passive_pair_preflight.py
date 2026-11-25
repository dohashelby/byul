"""Two independently prepared passive captures must reach pair admission."""
from copy import deepcopy
import json
import socket
import subprocess
import sys
import tomllib

import pytest

from guard import application
from guard.configuration import NORMAL_REQUEST_BINDING_OPTION, NORMAL_REQUEST_BINDING_SCOPE_ERROR
from guard.core.execution_recipe import bind_recipe, configuration_sha256
from guard.core.select import select
from guard.executors.request_binding import SUPPORTED_POLICY as REQUEST_BINDING_POLICY
from guard.models import canonical_json, load_pack, sha256_hex
from tests.builders import T0, policy
from tests.test_gpu_a_normal_tp2_callbacks import _owned_pods, _stage
from tests.test_kubernetes_sampling import APPROVED_NORMAL
from tools import gpu_a_run_inputs as inputs, mvp_metadata_wiring as wiring


@pytest.mark.parametrize('different_observers', (False, True))
def test_passive_tp2_prepares_comparable_product_inputs_only_for_the_same_mode(
        tmp_path, monkeypatch, different_observers):
    def forbidden(*args, **kwargs):
        pytest.fail('Saved declaration preparation cannot dispatch requests or subprocesses')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    pack = load_pack(wiring.readiness.ROOT / 'packs/PACK-BASELINE')
    declared = policy(pack.ref(), status='CONFIRMED', data_origin='DECLARED', confirmed_by='Fixture owner',
                      confirmed_at=T0, values={'REQ-10': APPROVED_NORMAL}, scope={'include': ['REQ-10']})
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(canonical_json(declared.to_dict()))
    modes = ['worker-shards-and-scoped-runner-consumption', 'worker-shards' if different_observers
             else 'worker-shards-and-scoped-runner-consumption']
    sides, envs, targets = {}, [], []
    for label, mode in zip(('baseline', 'candidate'), modes):
        side = tmp_path / label
        side.mkdir()
        prepared, render, _ = _stage(side, label, mode, generation='v2-template')
        namespace, pod, probe = _owned_pods(prepared)
        if label == 'candidate':
            for epoch, candidate in enumerate((pod, probe), 33):
                candidate['metadata']['uid'] = f'00000000-0000-0000-0000-{epoch:012d}'
                candidate['status']['containerStatuses'][0]['containerID'] = f'containerd://{epoch:064x}'
        observed = {'observed_engine_version': render['engine_version']}
        env = inputs.environment(render, inventory='b' * 64, gpu_model='EXAMPLE L4', driver='EXAMPLE driver',
            external_platform='Linux x86_64', engine_pod=pod, namespace=namespace, observed_gpu_count=2,
            data_origin='DECLARED', **observed)
        target = inputs.target(env, render, namespace, pod, probe, context='offline-fixture', **observed)
        env_file, target_file = side / 'env.json', side / 'target.json'
        env_file.write_bytes(canonical_json(env.to_dict()))
        target_file.write_bytes(canonical_json(target))
        sides[label] = {'environment': env_file, 'target': target_file}
        envs.append(env)
        targets.append(target)
    assert len({target['normal_tp2_callbacks']['session_sha256'] for target in targets}) == 2
    store = tmp_path / 'store'
    if different_observers:
        with pytest.raises(ValueError, match='^METADATA_COMPARISON_CONDITIONS_MISMATCH$'):
            wiring.assemble(sides, policy_path, pack=pack, store=store, normal_sampling=True)
        return

    config, bindings, _ = wiring.assemble(sides, policy_path, pack=pack, store=store, normal_sampling=True)
    assert envs[0].runtime == envs[1].runtime
    assert config['update']['allowed_checks'] == ['REQ-10']
    assert {row['side']: row['target']['sha256'] for row in bindings if row['check_id'] == 'REQ-10'} == {
        name: sha256_hex(files['target'].read_bytes()) for name, files in sides.items()}
    readiness = application.update_readiness(pack, envs, declared, config)
    assert next(row for row in readiness['checks'] if row['check'] == 'inputs')['ok']
    assert not store.exists()


def test_prebound_req11_uses_both_sealed_normal_targets_without_transition(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Offline request binding preparation attempted a dispatch or transition')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(application, '_kubernetes_transition', forbidden)
    pack = load_pack(wiring.readiness.ROOT / 'packs/PACK-BASELINE')
    values = {'REQ-11': REQUEST_BINDING_POLICY}
    scope = {'include': ['REQ-11']}
    declared = policy(pack.ref(), status='CONFIRMED', data_origin='DECLARED', confirmed_by='Fixture owner',
                      confirmed_at=T0, values=values, scope=scope)
    policy_path = tmp_path / 'policy.json'
    policy_path.write_bytes(canonical_json(declared.to_dict()))

    output = tmp_path / 'wiring'
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--output', str(output),
            '--store', str(tmp_path / 'store'), '--normal-request-binding']
    from tests.test_gpu_a_run_inputs import deployed

    envs, targets = [], {}
    for number, (side, release) in enumerate(wiring.versions().items(), 1):
        folder = tmp_path / side
        folder.mkdir()
        pods, render = deployed(folder, 'v2-template', run_id='example-' + side, release_launch=True, normal_tp2=True,
                                engine_version=release['version'], engine_image=release['image'])
        namespace = json.loads((folder / 'namespace.json').read_bytes())
        for index, pod in enumerate(pods.values(), 10 * number):
            pod['kind'] = 'Pod'
            pod['metadata']['uid'] = f'00000000-0000-0000-0000-{index:012d}'
            pod['status']['containerStatuses'][0]['containerID'] = f'containerd://{index:064x}'
        observed = {'observed_engine_version': release['version']}
        env = inputs.environment(render, inventory='b' * 64, gpu_model='EXAMPLE L4', driver='EXAMPLE driver',
            external_platform='Linux x86_64', engine_pod=pods['engine'], namespace=namespace, observed_gpu_count=2,
            data_origin='DECLARED', **observed)
        target = inputs.target(env, render, namespace, pods['engine'], pods['probe'], context='offline-fixture', **observed)
        env_file, target_file = folder / 'env.json', folder / 'target.json'
        env_file.write_bytes(canonical_json(env.to_dict()))
        target_file.write_bytes(canonical_json(target) + b'\n')
        args.extend([f'--{side}-environment', str(env_file), f'--{side}-target', str(target_file)])
        envs.append(env)
        targets[side] = target_file
    monkeypatch.setattr(sys, 'argv', args)
    wiring.main()

    config = tomllib.loads((output / 'guard.toml').read_bytes().decode('utf-8'))
    index = output / 'input-sources.json'
    review = wiring.readiness.audit(index, pack=pack)
    rows = {row['side']: row for row in review['rows'] if row['check_id'] == 'REQ-11'}
    assert config['update']['allowed_checks'] == ['REQ-11']
    assert config['check_config']['REQ-11'] == {NORMAL_REQUEST_BINDING_OPTION: True}
    for side, row in rows.items():
        assert row['file_binding_valid'] and row[NORMAL_REQUEST_BINDING_OPTION]
        assert row['files']['target'] == {'path': str(targets[side]), 'sha256': sha256_hex(targets[side].read_bytes())}
        assert 'LIVE_EXECUTOR_PREFLIGHT_REQUIRED' in row['blocking_codes']
    assert set(rows) == set(targets)
    assert review['actual_results'] is None
    prepared = application.update_readiness(pack, envs, declared, config)
    assert prepared['execution_scope']['static_preparation_ready'], prepared
    assert not prepared['execution_ready']

    unpaired = deepcopy(config)
    del unpaired['check_config']['REQ-11']
    unpaired['sides']['baseline']['check_config'] = {'REQ-11': {NORMAL_REQUEST_BINDING_OPTION: True}}
    staged = deepcopy(config)
    staged['update']['kubernetes_candidate_binding'] = 'after_baseline'
    for unusable in (unpaired, staged):
        blocked = application.update_readiness(pack, envs, declared, unusable)
        assert next(row for row in blocked['checks'] if row['check'] == 'inputs')['detail'] == (
            NORMAL_REQUEST_BINDING_SCOPE_ERROR)
    draft = policy(pack.ref(), values=values, scope=scope)
    blocked = application.update_readiness(pack, envs, draft, config)
    assert next(row for row in blocked['checks'] if row['check'] == 'inputs')['detail'] == (
        'NORMAL_REQUEST_BINDING_CONFIRMED_POLICY_REQUIRED')

    plan = bind_recipe(select(pack, envs, declared, mode='update'), config, envs)
    captured = dict.fromkeys(targets.values())
    assert configuration_sha256(config, envs, plan=plan, target_snapshots=captured) == plan.inputs.execution_recipe_sha256
    assert captured == {path: path.read_bytes() for path in targets.values()}
    candidate = targets['candidate']
    candidate.write_bytes(candidate.read_bytes() + b'\n')
    assert configuration_sha256(config, envs, plan=plan) != plan.inputs.execution_recipe_sha256
    stale = wiring.readiness.audit(index, pack=pack)
    stale_rows = {row['side']: row for row in stale['rows'] if row['check_id'] == 'REQ-11'}
    assert stale_rows['baseline']['file_binding_valid']
    assert stale_rows['candidate']['blocking_codes'] == ['MVP_INPUT_HASH_MISMATCH']
