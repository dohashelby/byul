"""An exact PLT-01 product capture is the only input to the passive PLT-08 projection."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.core.execution_recipe import bind_recipe
from guard.core.judge import judge_evidence
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import passive_api_hop as subject
from guard.executors.kubernetes_container_config import KubernetesContainerConfigExecutor
from guard.models import DataOrigin, ExecStatus, Side, canonical_json, load_pack, sha256_hex
from guard.models.objects import Operator
from guard.store import Store
from tests.builders import T0, policy
from tests.test_kubernetes_process_consumers import PERMISSION_VALUES, PRIVATE, ProcessWorld
from tests.test_model_preparation_integration import full_inputs


@pytest.fixture
def paired_original(tmp_path, monkeypatch):
    environment, target = full_inputs()
    target['lab'] = False
    baseline = environment.model_copy(update={'engine': environment.engine.model_copy(update={'version': '0.30.0'})}).seal()
    candidate = environment.model_copy(update={'id': 'ENV-PASSIVE-CANDIDATE'}).seal()
    environments = [baseline, candidate]
    sides = {}
    worlds_by_path = {}
    for side, env in zip((Side.baseline, Side.candidate), environments):
        bound = deepcopy(target)
        bound.update(environment_ref=env.ref().model_dump(mode='json'), observed_version=env.engine.version)
        bound['model_mapping']['environment_ref'] = bound['environment_ref']
        if side == Side.candidate:
            bound['kubernetes']['engine'].update(pod_uid='20000000-0000-0000-0000-000000000001',
                                                 container_id='containerd://' + '3' * 64)
        target_path = tmp_path / (side.value + '.json')
        target_path.write_bytes(canonical_json(bound))
        sides[side.value] = {'container_config': {'target': str(target_path)}}
        worlds_by_path[str(target_path)] = ProcessWorld(bound)
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['PLT-01', 'PLT-08']},
                 values={'PLT-01': PERMISSION_VALUES, 'PLT-08': {'plaintext_forbidden': True}})
    executors = {'container_config': 'kubernetes'}
    config = {'executors': executors, 'adapter': {'name': 'vllm'}, 'sides': sides}
    plan = bind_recipe(select(pack, environments, pol, mode='update'), config, environments)
    store = Store(tmp_path / 'store')
    store.put_many([*environments, pol, plan])
    initialize = KubernetesContainerConfigExecutor.__init__

    def with_runner(self, cfg, **kwargs):
        initialize(self, cfg, **kwargs, runner=worlds_by_path[cfg['target']].runner)

    monkeypatch.setattr(KubernetesContainerConfigExecutor, '__init__', with_runner)
    evds = run_plan(store, pack, plan, RunConfig(executors=executors, adapter='vllm', requested=config,
        side_configs={side: RunConfig(executors=executors, adapter='vllm', raw=values) for side, values in sides.items()}))
    originals = {}
    for source in (evd for evd in evds if evd.check_id == 'PLT-01'):
        assert source.execution.status == ExecStatus.COMPLETED
        assert source.data_origin == DataOrigin.EXAMPLE
        world = worlds_by_path[sides[source.side.value]['container_config']['target']]
        assert len(world.packets) == world.process_reads == 1
        scope = {'plan_ref': plan.ref(), 'side': source.side, 'attempt_id': source.execution.attempt_id,
                 'source_ref': source.ref(), 'target': world.target, 'expected_target_sha256': sha256_hex(canonical_json(world.target))}
        originals[source.side] = (store, source, scope, world)
    assert set(originals) == {Side.baseline, Side.candidate}
    return originals


@pytest.fixture
def original(paired_original):
    return paired_original[Side.baseline]


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


def test_passive_draft_inherits_both_sides_and_never_runs_a_normal_control(paired_original):
    check = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE').checks['PLT-08']
    _, baseline, baseline_scope, _ = paired_original[Side.baseline]
    _, candidate, candidate_scope, _ = paired_original[Side.candidate]
    assert baseline.execution.attempt_id == candidate.execution.attempt_id
    assert baseline.environment != candidate.environment
    assert baseline_scope['expected_target_sha256'] != candidate_scope['expected_target_sha256']
    for store, source, scope, world in paired_original.values():
        draft = subject.draft_passive_api_hop(Store.read_only(store.root), **scope)
        assert draft.status == ExecStatus.COMPLETED
        assert draft.data_origin == source.data_origin == DataOrigin.EXAMPLE
        assert draft.comparability == source.comparability and draft.comparability is not source.comparability
        assert draft.started_at >= source.execution.finished_at and draft.finished_at >= draft.started_at
        assert not draft.normal_control.attempted and draft.normal_control.succeeded is None
        assert not draft.challenges and not draft.artifacts
        recorded, = draft.observations['api_transport_trials'].value
        assert recorded['source_evidence'] == source.ref().model_dump(mode='json')
        assert not draft.observations['forbidden_plaintext_successes'].collected
        assert draft.observations['api_transport_scope_complete'].value is False
        # Exercise the real judge on the caller's projected shape without
        # publishing an integration artifact or changing either original EVD.
        pol = store.resolve(store.resolve(scope['plan_ref']).inputs.policy)
        projected = source.model_copy(update={'check_id': 'PLT-08', 'normal_control': draft.normal_control,
                                             'observations': draft.observations, 'challenges': [], 'artifacts': []}).seal()
        assert judge_evidence(check, projected, pol, T0).verdicts.security.value == 'UNKNOWN'
        assert len(world.packets) == world.process_reads == 1
    store, _, scope, _ = paired_original[Side.candidate]
    with pytest.raises(subject.PassiveApiHopUnavailable, match='PASSIVE_API_HOP_SOURCE_SCOPE_UNVERIFIED'):
        subject.draft_passive_api_hop(Store.read_only(store.root), **{**scope, 'source_ref': baseline.ref()})


@pytest.mark.parametrize('mutation', ['manual', 'other-attempt'])
def test_passive_draft_refuses_non_product_or_stale_sealed_source(original, mutation):
    store, source, scope, world = original
    change = ({'operator': Operator(by='manual', at=T0)} if mutation == 'manual'
              else {'attempt_id': 'ATTEMPT-PRIOR'})
    execution = source.execution.model_copy(update=change)
    alternate = source.model_copy(update={'revision': source.revision + 1, 'execution': execution}).seal()
    store.put(alternate)
    with pytest.raises(subject.PassiveApiHopUnavailable, match='PASSIVE_API_HOP_SOURCE_SCOPE_UNVERIFIED'):
        subject.draft_passive_api_hop(Store.read_only(store.root), **{**scope, 'source_ref': alternate.ref()})
    assert len(world.packets) == world.process_reads == 1
