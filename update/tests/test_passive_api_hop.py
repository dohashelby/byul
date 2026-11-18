"""An exact PLT-01 product capture is the only input to the passive PLT-08 projection."""
import json
from pathlib import Path

import pytest

from guard.core.execution_recipe import bind_recipe
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import passive_api_hop as subject
from guard.executors.kubernetes_container_config import KubernetesContainerConfigExecutor
from guard.models import DataOrigin, ExecStatus, Side, canonical_json, load_pack, sha256_hex
from guard.store import Store
from tests.builders import policy
from tests.test_kubernetes_process_consumers import PERMISSION_VALUES, PRIVATE, ProcessWorld
from tests.test_model_preparation_integration import full_inputs


@pytest.fixture
def original(tmp_path, monkeypatch):
    environment, target = full_inputs()
    target['lab'] = False
    candidate = environment.model_copy(update={'id': 'ENV-PASSIVE-OTHER'}).seal()
    world = ProcessWorld(target)
    target_path = tmp_path / 'target.json'
    target_path.write_bytes(canonical_json(target))
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['PLT-01', 'PLT-08']},
                 values={'PLT-01': PERMISSION_VALUES, 'PLT-08': {'plaintext_forbidden': True}})
    config = {'container_config': {'target': str(target_path)}}
    environments = [environment, candidate]
    plan = bind_recipe(select(pack, environments, pol, mode='update'), config, environments)
    store = Store(tmp_path / 'store')
    store.put_many([*environments, pol, plan])
    initialize = KubernetesContainerConfigExecutor.__init__

    def with_runner(self, cfg, **kwargs):
        initialize(self, cfg, **kwargs, runner=world.runner)

    monkeypatch.setattr(KubernetesContainerConfigExecutor, '__init__', with_runner)
    evds = run_plan(store, pack, plan, RunConfig(executors={'container_config': 'kubernetes'},
                                               adapter='vllm', raw=config), only_side=Side.baseline)
    source, = [evd for evd in evds if evd.check_id == 'PLT-01']
    assert source.execution.status == ExecStatus.COMPLETED
    assert source.data_origin == DataOrigin.EXAMPLE
    assert len(world.packets) == world.process_reads == 1
    scope = {'plan_ref': plan.ref(), 'side': Side.baseline, 'attempt_id': source.execution.attempt_id,
             'source_ref': source.ref(), 'target': target, 'expected_target_sha256': sha256_hex(canonical_json(target))}
    return store, source, scope, world


def test_sealed_product_control_projects_one_hop_without_new_calls(original):
    store, source, scope, world = original
    projected = subject.read_passive_api_hop(Store.read_only(store.root), **scope)
    row, = projected['api_transport_trials']['value']
    assert row['transport'] == 'plain_http' and row['outcome'] == 200
    assert row['capture_complete'] and row['owner_binding_verified_before_after']
    assert row['original_source_data_origin'] == 'EXAMPLE'
    assert row['source_evidence'] == source.ref().model_dump(mode='json')
    assert row['source_artifact_sha256'] == source.artifacts[0].sha256
    assert row['global_plaintext_forbidden'] and not row['hop_policy_adjudicated']
    assert not projected['forbidden_plaintext_successes']['collected']
    assert not projected['api_transport_scope_complete']['value']
    assert len(world.packets) == world.process_reads == 1
    body = json.dumps(projected)
    assert scope['target']['engine']['trusted_url'] not in body and PRIVATE not in body


@pytest.mark.parametrize('mutation', ['attempt', 'side', 'ref', 'target', 'artifact', 'symlink'])
def test_scope_or_artifact_changes_fail_closed_without_dispatch(original, mutation):
    store, source, scope, world = original
    archive = store.root / source.artifacts[0].path
    if mutation == 'attempt':
        scope['attempt_id'] = 'ATTEMPT-DIFFERENT'
    elif mutation == 'side':
        scope['side'] = Side.candidate
    elif mutation == 'ref':
        scope['source_ref'] = scope['source_ref'].model_copy(update={'sha256': 'f' * 64})
    elif mutation == 'target':
        scope['target']['lab'] = True
        scope['expected_target_sha256'] = sha256_hex(canonical_json(scope['target']))
    elif mutation == 'artifact':
        data = archive.read_bytes()
        archive.write_bytes(data[:-1] + b'?')
    elif mutation == 'symlink':
        sibling = archive.with_name('other')
        sibling.write_bytes(archive.read_bytes())
        archive.unlink()
        archive.symlink_to(sibling.name)
    with pytest.raises(subject.PassiveApiHopUnavailable, match='PASSIVE_API_HOP_'):
        subject.read_passive_api_hop(Store.read_only(store.root), **scope)
    assert len(world.packets) == world.process_reads == 1


@pytest.mark.parametrize('mutation', ['source_target', 'request', 'time'])
def test_even_sealed_original_requires_consistent_bound_control_metadata(original, mutation):
    store, source, scope, _ = original
    old, = source.artifacts
    lines = [json.loads(line) for line in (store.root / old.path).read_bytes().splitlines()]
    summary = lines[-1]
    if mutation == 'source_target':
        summary['process_observation']['target_sha256'] = 'f' * 64
    elif mutation == 'request':
        summary['kubernetes']['requests'][0]['binding_verified_before_after'] = False
    else:
        summary['kubernetes']['requests'][0]['finished_at'] = '2099-01-01T00:00:00+00:00'
    artifact = store.put_artifact(b'\n'.join(canonical_json(line) for line in lines) + b'\n')
    updated = old.model_copy(update={'path': artifact.path, 'sha256': artifact.sha256, 'bytes': artifact.bytes})
    alternate = source.model_copy(update={'revision': source.revision + 1, 'artifacts': [updated]}).seal()
    store.put(alternate)
    scope['source_ref'] = alternate.ref()
    with pytest.raises(subject.PassiveApiHopUnavailable, match='PASSIVE_API_HOP_CONTROL_UNVERIFIED'):
        subject.read_passive_api_hop(Store.read_only(store.root), **scope)
