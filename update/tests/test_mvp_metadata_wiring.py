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
from tests.test_lifecycle_capture import REPOSITORY, external  # noqa: F401
from tests.test_model_preparation_integration import full_inputs
from tests.test_template_supply_approval import SHA
from tools import mvp_credential_copy_inputs as copy_inputs
from tools import mvp_metadata_wiring as tool
from tools.mvp_runtime_policy import AMENDMENT


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


@pytest.mark.parametrize(('missing', 'admitted'), [('SUP-04', 'PLT-01'), ('PLT-01', 'SUP-04')])
def test_shared_container_checks_require_their_own_policy(tmp_path, sources, monkeypatch, missing, admitted):
    from guard.executors.kubernetes_container_config import KubernetesContainerConfigExecutor

    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {admitted: original.values[admitted], 'PLT-08': {'plaintext_forbidden': True}}
    scope = original.scope.model_copy(update={'include': ['SUP-04', 'PLT-01', 'PLT-08']})
    revised = original.model_copy(update={'values': values, 'scope': scope}).seal()
    _write(policy_path, revised.to_dict())
    store_path = tmp_path / 'store'
    config, _, checks = tool.assemble(sides, policy_path, pack=pack, store=store_path,
                                     node_image_verification='local-containerd-v1', passive_plt08=True)
    assert checks[missing] == {'configuration_enabled': False,
                              'sides': dict.fromkeys(sides, 'MVP_POLICY_VALUE_MISSING')}
    assert checks[admitted]['configuration_enabled'] and checks[admitted]['sides'] == dict.fromkeys(sides)
    assert config['executors']['container_config'] == 'kubernetes'
    assert all(settings['container_config'] == {'target': str(sides[side]['target'])}
               for side, settings in config['sides'].items())
    if admitted == 'PLT-01':
        assert set(config['update']['allowed_checks']) == {'PLT-01', 'PLT-08'}
        assert checks['PLT-08']['configuration_enabled']
    else:
        assert config['update']['allowed_checks'] == ['SUP-04']
        assert checks['PLT-08']['sides'] == dict.fromkeys(sides, 'PASSIVE_API_HOP_SOURCE_NOT_ADMITTED')
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    calls = []

    def collect(self, check, env, policy_values, *_args):
        calls.append((check.id, env.id))
        self.validate_policy(check, policy_values)
        assert policy_values == values[admitted]
        assert self.cfg.get('node_image_verification') == ('local-containerd-v1' if admitted == 'SUP-04' else None)
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None})

    def forbid_io(*_args, **_kwargs):
        pytest.fail('Dispatch regression must use saved inputs and local test doubles only')

    monkeypatch.setattr(KubernetesContainerConfigExecutor, 'run', collect)
    monkeypatch.setattr(socket, 'socket', forbid_io)
    monkeypatch.setattr(subprocess, 'Popen', forbid_io)
    store = Store(store_path)
    application.execute_update(store, pack, envs, revised, config, tmp_path / 'update', utcnow())
    assert calls == [(admitted, env.id) for env in envs]
    rows = {(evd.check_id, evd.environment.id): evd for evd in store.iter_kind('EVD')}
    for env in envs:
        observed = rows[admitted, env.id]
        assert observed.execution.status == ExecStatus.COMPLETED and observed.data_origin == DataOrigin.EXAMPLE
        assert rows[missing, env.id].execution.abort_reason == 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE'
        unobserved = rows['PLT-08', env.id]
        assert unobserved.execution.status == ExecStatus.NOT_RUN
        assert unobserved.execution.abort_reason == ('PASSIVE_API_HOP_ARTIFACT_UNVERIFIED' if admitted == 'PLT-01'
                                                     else 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE')


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


def _bind_cilium_fixture_targets(sides):
    from guard.executors.kubernetes_passive_egress_policy import CILIUM_AGENT_IMAGE, CILIUM_AGENT_IMAGE_ID

    for side in sides.values():
        target = json.loads(side['target'].read_bytes())
        target['cilium_agent'] = {
            'namespace_uid': '00000000-0000-0000-0000-000000000320',
            'daemonset_uid': '00000000-0000-0000-0000-000000000321',
            'pod': 'cilium-fixture', 'pod_uid': '00000000-0000-0000-0000-000000000322',
            'container_id': 'containerd://' + 'c' * 64,
            'image': CILIUM_AGENT_IMAGE, 'image_id': CILIUM_AGENT_IMAGE_ID,
            'node': target['kubernetes']['engine']['node'], 'restart_count': 0,
            'started_at': target['kubernetes']['engine']['started_at'],
        }
        _write(side['target'], target)


def test_plt07_cilium_status_and_obs04_local_mode_use_independent_owned_bindings(tmp_path, sources, monkeypatch):
    from guard.executors import run_binding
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    from guard.executors.kubernetes_passive_egress_policy import KubernetesPassiveEgressPolicyExecutor
    from guard.executors.kubernetes_usage_report import KubernetesUsageReportExecutor

    pack, sides, policy_path = sources
    _bind_cilium_fixture_targets(sides)
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {
        'OBS-04': {'usage_allowed': False, 'usage_settings_fresh_process': True,
                   'usage_report_paths': ['/home/guard/.config/vllm/usage_stats.json']},
        'PLT-07': {'forbidden_protocol': 'tcp', 'forbidden_operation': 'tcp_connect'},
    }
    pol = prior.model_copy(update={'values': values,
        'scope': prior.scope.model_copy(update={'include': ['OBS-04', 'PLT-07', 'OBS-05']})}).seal()
    _write(policy_path, pol.to_dict())
    output, store_path, fresh = (tmp_path / name for name in ('status', 'store', 'fresh-target.json'))
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store_path)
    assert default['update']['allowed_checks'] == ['OBS-04']
    without_agent = deepcopy(sides)
    preliminary = json.loads(sides['candidate']['target'].read_bytes())
    preliminary.pop('cilium_agent')
    without_agent['candidate']['target'] = _write(tmp_path / 'candidate-without-agent.json', preliminary)
    fallback, _, withheld = tool.assemble(without_agent, policy_path, pack=pack, store=store_path,
        cilium_egress_status=True)
    assert fallback['update']['allowed_checks'] == ['OBS-04']
    assert withheld['PLT-07']['sides']['candidate'] == 'CILIUM_EGRESS_STATUS_TARGET_UNAVAILABLE'
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store_path), '--output', str(output),
            '--cilium-egress-status', '--candidate-target-after-baseline', str(fresh)]
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    cfg = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert cfg['executors'] == {'boundary_egress': 'kubernetes'}
    assert cfg['update']['allowed_checks'] == ['OBS-04', 'PLT-07']
    assert cfg['check_config'] == {'OBS-04': {'local_observation_only': True},
                                    'PLT-07': {'cilium_egress_status_only': True}}
    readiness = json.loads((output / 'readiness.json').read_bytes())
    rows = [row for row in readiness['rows'] if row['check_id'] == 'PLT-07']
    assert readiness['active_checks'] == 69 and len(rows) == 2
    assert all(row['file_binding_valid'] and row['cilium_egress_status_only']
               and row['binding_schema'] == run_binding.SCHEMA and not row['engine_executed'] for row in rows), rows
    assert not readiness['metadata_wiring']['PLT-07']['shared_executor_checks']['OBS-05']['baseline']['transport_dispatched']
    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    expected = {env.id: sha256_hex(canonical_json(json.loads(side['target'].read_bytes())))
                for env, side in zip(envs, sides.values())}
    seen = set()

    def draft(check, env):
        seen.add((check.id, env.id))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    def fixture_cilium(self, check, env, received, *_args):
        assert check.id == 'PLT-07' and received == values[check.id]
        assert self._expected_target_sha256 == expected[env.id]
        assert set(self.cfg) == {'target', 'cilium_egress_status_only'}
        return draft(check, env)

    def fixture_local(self, check, env, received, *_args):
        assert check.id == 'OBS-04' and received == values[check.id]
        assert self.expected_target == expected[env.id] and self.cfg['local_observation_only'] is True
        assert set(self.cfg) == {'target', 'local_observation_only'}
        return draft(check, env)

    def forbid_legacy(*_args, **_kwargs):
        raise AssertionError('The active egress collector must not run for a scoped status read')

    monkeypatch.setattr(KubernetesPassiveEgressPolicyExecutor, 'run', fixture_cilium)
    monkeypatch.setattr(KubernetesUsageReportExecutor, 'run', fixture_local)
    monkeypatch.setattr(KubernetesEgressProductExecutor, 'run', forbid_legacy)
    store = Store(store_path)
    report, _ = application.execute_update(store, pack, envs, pol, cfg, tmp_path / 'paired', utcnow())
    assert seen == {(cid, env.id) for cid in values for env in envs}
    row = next(row for row in report.by_check if row.check_id == 'PLT-07')
    assert row.baseline.security == row.candidate.security == 'UNKNOWN'
    for side in (row.baseline, row.candidate):
        evd = store.resolve(store.resolve(side.result).evidence)
        assert evd.execution.status == ExecStatus.COMPLETED and not run_binding.verify(evd.execution.run_binding, evd)
        assert evd.execution.run_binding['files'] == {'egress_target_sha256': expected[evd.environment.id]}
        assert 'values' not in evd.execution.run_binding
    staged = tomllib.loads((output / 'guard.toml').read_text())
    current, pointer = tool._source(sides['candidate']['target'])
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == {
        'boundary_egress': sha256_hex(canonical_json(current))}
    current['cilium_agent']['pod_uid'] = '00000000-0000-0000-0000-000000000323'
    _write(fresh, current)
    with pytest.raises(ValueError, match='CILIUM_AGENT_GENERATION_CHANGED'):
        tool.revalidate_candidate(staged, envs[1], pointer)
    staged['check_config'].pop('OBS-04')
    with pytest.raises(ValueError, match='METADATA_STAGED_EXECUTION_SCOPE_INVALID'):
        tool.revalidate_candidate(staged, envs[1], pointer)


@pytest.mark.parametrize(('gap', 'reason'), [
    ('missing', 'CILIUM_EGRESS_STATUS_TARGET_UNAVAILABLE'),
    ('changed', 'CILIUM_AGENT_GENERATION_CHANGED'),
])
def test_staged_candidate_cilium_gap_does_not_abort_other_admitted_checks(tmp_path, sources, monkeypatch, gap, reason):
    from guard.core.run import run_plan
    from guard.executors.kubernetes_egress_product import KubernetesEgressProductExecutor
    from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
    from guard.executors.kubernetes_passive_egress_policy import KubernetesPassiveEgressPolicyExecutor
    from guard.executors.kubernetes_usage_report import KubernetesUsageReportExecutor
    from guard.models import Side
    from guard.store.packs import put_pack

    pack, sides, policy_path = sources
    _bind_cilium_fixture_targets(sides)
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    policy_document = json.loads((tool.readiness.ROOT / 'docs/dev-1/mvp-enterprise-policy-20261116.json').read_bytes())
    admitted = {'OBS-04', 'PLT-06', 'PLT-07'}
    scope = admitted | {'OBS-05'}
    values = {}
    for row in policy_document['decisions']:
        if row['check_id'] in scope:
            values.setdefault(row['check_id'], {})[row['key']] = row['value']
    policy = prior.model_copy(update={'values': values,
        'scope': prior.scope.model_copy(update={'include': sorted(scope)})}).seal()
    _write(policy_path, policy.to_dict())
    store_path = tmp_path / 'store'
    config, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store_path, cilium_egress_status=True)
    assert set(config['update']['allowed_checks']) == admitted
    fresh = tmp_path / 'fresh-candidate.json'
    staged = tool.staged_config(config, fresh)
    run = application.run_config(staged)
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    store = Store(store_path)
    put_pack(store, pack.path)
    for value in (*envs, policy):
        store.put(value)
    plan = bind_recipe(select(pack, envs, policy, mode='update'), staged, envs)
    store.put(plan)
    baseline, _ = tool._source(sides['baseline']['target'])
    expected = {envs[0].id: sha256_hex(canonical_json(baseline))}
    run.side_configs['baseline'].raw['_kubernetes_target_sha256_by_executor'] = dict.fromkeys(
        config['executors'], expected[envs[0].id])
    dispatched = []

    def collect(check, env, target_pin):
        dispatched.append((check.id, env.id))
        assert target_pin == expected[env.id]
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None})

    monkeypatch.setattr(KubernetesUsageReportExecutor, 'run',
        lambda self, check, env, *_: collect(check, env, self.expected_target))
    monkeypatch.setattr(KubernetesIPCProductExecutor, 'run',
        lambda self, check, env, *_: collect(check, env, self.expected_target))
    monkeypatch.setattr(KubernetesPassiveEgressPolicyExecutor, 'run',
        lambda self, check, env, *_: collect(check, env, self._expected_target_sha256))
    monkeypatch.setattr(KubernetesEgressProductExecutor, 'run',
        lambda self, check, env, *_: collect(check, env, None))
    baseline_rows = {row.check_id: row for row in run_plan(store, pack, plan, run, only_side=Side.baseline)}
    assert all(baseline_rows[cid].execution.status == ExecStatus.COMPLETED for cid in admitted)

    current, preliminary_ref = tool._source(sides['candidate']['target'])
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-000000000{number}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    if gap == 'missing':
        current.pop('cilium_agent')
    else:
        current['cilium_agent']['pod_uid'] = '00000000-0000-0000-0000-000000000323'
    _write(fresh, current)
    expected[envs[1].id] = sha256_hex(canonical_json(current))
    with pytest.raises(ValueError, match=f'^{reason}$'):
        tool.revalidate_candidate(staged, envs[1], preliminary_ref)
    assert tool.bind_staged_candidate(run, envs[1], preliminary_ref) == {'PLT-07': reason}
    candidate = run.side_configs['candidate']
    assert candidate.raw['_kubernetes_target_sha256_by_executor'] == {'unix_ipc': expected[envs[1].id]}
    assert candidate.raw['_kubernetes_target_sha256_by_check'] == {'OBS-04': expected[envs[1].id], 'PLT-07': None}
    rows = {row.check_id: row for row in run_plan(store, pack, plan, run, only_side=Side.candidate)}
    assert rows['OBS-04'].execution.status == rows['PLT-06'].execution.status == ExecStatus.COMPLETED
    assert rows['OBS-05'].execution.abort_reason == 'UPDATE_CHECK_OUTSIDE_EXECUTION_SCOPE'
    failed = rows['PLT-07']
    assert failed.execution.status == ExecStatus.ABORTED and failed.execution.abort_reason == reason
    assert failed.execution.executor is None and not failed.normal_control.attempted and not failed.artifacts

    candidate.check_abort_reasons.clear()
    without_diagnostic = {row.check_id: row for row in run_plan(store, pack, plan, run, only_side=Side.candidate)}
    assert without_diagnostic['PLT-07'].execution.status == ExecStatus.NOT_RUN
    assert without_diagnostic['PLT-07'].execution.abort_reason == 'RUN_BINDING_PLT07_NORMAL_TARGET_UNAVAILABLE'
    assert set(dispatched) == {(cid, envs[0].id) for cid in admitted} | {
        (cid, envs[1].id) for cid in ('OBS-04', 'PLT-06')}

    current['kubernetes']['context'] = 'fixture-unrelated-context'
    _write(fresh, current)
    with pytest.raises(ValueError, match='METADATA_CANDIDATE_OWNERSHIP_CHANGED'):
        tool.bind_staged_candidate(run, envs[1], preliminary_ref)


def test_plt07_cilium_status_cannot_enable_legacy_or_unmarked_scope(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan

    pack, sides, policy_path = sources
    pol = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    flag = 'cilium_egress_status_only'
    cfg = {'executors': {'boundary_egress': 'kubernetes'}, 'update': {'allowed_checks': ['PLT-07', 'OBS-04']},
           'sides': {side: {'boundary_egress': {'target': str(paths['target'])}} for side, paths in sides.items()},
           'check_config': {'PLT-07': {flag: True}, 'OBS-04': {'local_observation_only': True}}}
    missing, wrong, false_marker, target_override = (deepcopy(cfg) for _ in range(4))
    missing['check_config'].pop('OBS-04')
    wrong['check_config']['OBS-05'] = wrong['check_config'].pop('PLT-07')
    false_marker['sides']['candidate']['check_config'] = {'PLT-07': {flag: False}}
    target_override['check_config']['OBS-04']['target'] = str(tmp_path / 'different-target.json')
    sibling = {**cfg, 'update': {'allowed_checks': ['PLT-07', 'OBS-04', 'OBS-05']}}
    mixed = {**cfg, 'run_binding': {'PLT-07': {'egress_binding': {}}}}
    wrapped = {**cfg, 'lab_activation': {'mode': 'example'}}

    def forbid_prepare(*_args, **_kwargs):
        raise AssertionError('Malformed status scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', forbid_prepare)
    store = Store(tmp_path / 'store')
    for invalid in (missing, wrong, false_marker, target_override, sibling, mixed, wrapped):
        with pytest.raises(ValueError, match='CILIUM_EGRESS_STATUS_EXECUTION_SCOPE_INVALID'):
            application.execute_update(store, pack, envs, pol, invalid, tmp_path / 'run', utcnow())
    assert not (tmp_path / 'run').exists()
    with pytest.raises(ValueError, match='CILIUM_EGRESS_STATUS_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, select(pack, envs[:1], pol, mode='audit'), application.run_config(cfg))


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
    assert cfg['_kubernetes_target_sha256_by_check'] == {
        'OBS-04': sha256_hex(canonical_json(json.loads(original_bytes)))}
    assert configuration_sha256(cfg, envs) != expected_recipe


def test_obs02_normal_readiness_binds_the_same_product_plan_and_owned_targets(tmp_path, sources, monkeypatch):
    from guard.executors import run_binding
    from guard.executors.kubernetes_normal_serving_readiness import KubernetesNormalServingReadinessExecutor

    pack, sides, policy_path = sources
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'observation_state_required_components': ['http_listener', 'request_selection', 'engine_core', 'worker'],
              'profiler_backend_kind': 'torch', 'profiler_required_ranks': ['0', '1']}
    policy = prior.model_copy(update={'values': {'OBS-02': values},
        'scope': prior.scope.model_copy(update={'include': ['OBS-02']})}).seal()
    _write(policy_path, policy.to_dict())
    output, store_path, fresh = (tmp_path / name for name in ('readiness', 'store', 'fresh-target.json'))
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store_path)
    assert 'observation_state' not in default['executors']
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store_path), '--output', str(output),
            '--normal-serving-readiness', '--candidate-target-after-baseline', str(fresh)]
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    prebound = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert prebound['executors'] == {'observation_state': 'kubernetes'}
    assert prebound['update']['allowed_checks'] == ['OBS-02']
    assert prebound['check_config'] == {'OBS-02': {'normal_serving_readiness_only': True}}
    assert all('run_binding' not in side for side in prebound['sides'].values())
    readiness = json.loads((output / 'readiness.json').read_bytes())
    rows = [row for row in readiness['rows'] if row['check_id'] == 'OBS-02']
    assert readiness['active_checks'] == 69 and len(rows) == 2
    for row in rows:
        assert row['file_binding_valid'] and row['normal_serving_readiness_only'], row
        assert row['binding_schema'] == run_binding.SCHEMA
        assert row['blocking_codes'] == ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
        assert not row['engine_executed'] and not row['ready_for_execution']

    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    prepared = application.update_readiness(pack, envs, policy, prebound)
    assert next(row for row in prepared['checks'] if row['check'] == 'inputs')['ok'], prepared
    expected = {env.id: sha256_hex(canonical_json(json.loads(entry['target'].read_bytes())))
                for env, entry in zip(envs, sides.values())}

    def fixture_normal(self, check, env, received, *_args):
        assert check.id == 'OBS-02' and received == values
        assert self._expected_target_sha256 == expected[env.id]
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    monkeypatch.setattr(KubernetesNormalServingReadinessExecutor, 'run', fixture_normal)
    store = Store(store_path)
    report, _ = application.execute_update(store, pack, envs, policy, prebound, tmp_path / 'prebound', utcnow())
    row = next(row for row in report.by_check if row.check_id == 'OBS-02')
    assert row.selected and row.baseline.security == row.candidate.security == 'UNKNOWN'
    for side in ('baseline', 'candidate'):
        evidence = store.resolve(store.resolve(getattr(row, side).result).evidence)
        binding = evidence.execution.run_binding
        assert evidence.execution.status == ExecStatus.COMPLETED and not run_binding.verify(binding, evidence)
        assert binding['schema'] == run_binding.SCHEMA and 'values' not in binding
        assert binding['files'] == {'observation_state_target_sha256': expected[evidence.environment.id]}

    staged = tomllib.loads((output / 'guard.toml').read_text())
    candidate = json.loads(sides['candidate']['target'].read_bytes())
    for number, role in enumerate(('engine', 'probe'), 200):
        candidate['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        candidate['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, candidate)
    handoff = readiness['candidate_handoff']['preliminary_target']
    actual = sha256_hex(canonical_json(candidate))
    assert tool.revalidate_candidate(staged, envs[1], handoff) == {'observation_state': actual}
    settings = {'target': str(fresh), 'normal_serving_readiness_only': True}
    with pytest.raises(run_binding.RunBindingUnavailable, match='RUN_BINDING_OBS02_NORMAL_TARGET_CHANGED'):
        run_binding.effective_values(pack.checks['OBS-02'], settings, envs[1], values,
            side='candidate', attempt_id='fixture', issued_at=utcnow(), expected_target_sha256='f' * 64)
    candidate['synthetic'] = True
    _write(fresh, candidate)
    with pytest.raises(ValueError, match='NORMAL_SERVING_READINESS_TARGET_UNAVAILABLE'):
        tool.revalidate_candidate(staged, envs[1], handoff)


def test_obs02_normal_marker_cannot_dispatch_legacy_or_unpaired_paths(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan
    from guard.executors.kubernetes_normal_serving_readiness import KubernetesNormalServingReadinessExecutor

    pack, sides, policy_path = sources
    policy = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    base = {'executors': {'observation_state': 'kubernetes'}, 'update': {'allowed_checks': ['OBS-02']},
            'sides': {side: {'observation_state': {'target': str(paths['target'])}} for side, paths in sides.items()}}
    option = 'normal_serving_readiness_only'
    half = deepcopy(base)
    half['sides']['baseline']['check_config'] = {'OBS-02': {option: True}}
    scoped = {**base, 'check_config': {'OBS-02': {option: True}}}
    denied = {**base, 'check_config': {'OBS-02': {option: False}}}
    misplaced = {**base, 'check_config': {'OBS-01': {option: True}}}
    wrong_table = {**base, 'executors': {**base['executors'], option: True}}
    unscoped = {**scoped, 'update': {}}
    mixed = {**scoped, 'run_binding': {'OBS-02': {}}}
    wrapped = {**scoped, 'lab_activation': {'mode': 'example'}}

    def no_preparation(*_args, **_kwargs):
        raise AssertionError('Unsafe or malformed scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', no_preparation)
    store = Store(tmp_path / 'store')
    for invalid in (half, denied, misplaced, wrong_table, unscoped, mixed, wrapped):
        with pytest.raises(ValueError, match='NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID'):
            application.execute_update(store, pack, envs, policy, invalid, tmp_path / 'update', utcnow())
    assert not (tmp_path / 'update').exists()
    with pytest.raises(ValueError, match='NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID'):
        application._validation_inputs(envs[0], scoped)
    with pytest.raises(ValueError, match='NORMAL_SERVING_READINESS_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, select(pack, envs[:1], policy, mode='audit'), application.run_config(scoped))

    cfg = {'observation_state': {'target': str(sides['baseline']['target']), option: False}}
    executor = registry.build_executor('observation_state', 'kubernetes', cfg,
        engine=envs[0].engine.name, version=envs[0].engine.version)
    assert type(executor) is KubernetesNormalServingReadinessExecutor
    with pytest.raises(ValueError, match='OBS02_NORMAL_SCOPE_INVALID'):
        executor.validate_policy(pack.checks['OBS-02'], {})


def test_req09_pair_checks_owned_targets_and_withholds_legacy_siblings(tmp_path, sources, monkeypatch):
    from guard.executors.kubernetes_normal_token_input import KubernetesNormalTokenInputExecutor

    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    pol = original.model_copy(update={
        'scope': original.scope.model_copy(update={'include': ['REQ-08', 'REQ-09', 'SUP-08']}),
        'values': {'REQ-09': {'allowed_endpoints': ['POST /v1/completions', 'POST /v1/chat/completions'],
                              'allowed_input_formats': ['token_ids', 'text'],
                              'allowed_input_pairs': ['POST /v1/completions|token_ids',
                                                      'POST /v1/chat/completions|text'],
                              'max_input_tokens': 127, 'rejection_codes': ['400', '422']}},
    }).seal()
    _write(policy_path, pol.to_dict())
    output, store = tmp_path / 'token-input', tmp_path / 'store'
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store)
    assert 'input_validation' not in default['executors']

    def no_transition(*_args, **_kwargs):
        raise AssertionError('A scoped normal input does not admit legacy lifecycle collection')

    monkeypatch.setattr(application, 'KubernetesTransition', no_transition)
    fresh = tmp_path / 'fresh-token-target.json'
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store), '--output', str(output),
            '--normal-token-input', '--candidate-target-after-baseline', str(fresh)]
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()

    cfg = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert cfg['executors'] == {'input_validation': 'kubernetes'}
    assert cfg['check_config'] == {'REQ-09': {'normal_token_input_only': True}}
    assert cfg['update']['allowed_checks'] == ['REQ-09']
    assert all(settings == {'input_validation': {'target': str(sides[side]['target'])}}
               for side, settings in cfg['sides'].items())
    readiness = json.loads((output / 'readiness.json').read_bytes())
    rows = [row for row in readiness['rows'] if row['check_id'] == 'REQ-09']
    assert readiness['active_checks'] == 69 and len(rows) == 2
    for row in rows:
        assert row['file_binding_valid'] and row['normal_token_input_only'], row
        assert row['blocking_codes'] == ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
        assert not row['engine_executed'] and not row['ready_for_execution']
    shared = readiness['metadata_wiring']['REQ-09']['shared_executor_checks']
    assert set(shared) == {'REQ-08', 'SUP-08'}
    assert all(not row['transport_dispatched'] for sibling in shared.values() for row in sibling.values())

    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    pinned = {env.id: sha256_hex(canonical_json(json.loads(entry['target'].read_bytes())))
              for env, entry in zip(envs, sides.values())}
    seen = set()

    def fixture_normal(self, check, env, *_args):
        assert check.id == 'REQ-09' and self._expected_target_sha256 == pinned[env.id]
        seen.add(env.id)
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    monkeypatch.setattr(KubernetesNormalTokenInputExecutor, 'run', fixture_normal)
    report, _ = application.execute_update(Store(store), pack, envs, pol, cfg, tmp_path / 'prebound', utcnow())
    assert seen == set(pinned)
    assert next(row for row in report.by_check if row.check_id == 'REQ-09').selected

    staged = tomllib.loads((output / 'guard.toml').read_text())
    previous = readiness['candidate_handoff']['preliminary_target']
    current = json.loads(sides['candidate']['target'].read_bytes())
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, current)
    expected = sha256_hex(canonical_json(current))
    assert tool.revalidate_candidate(staged, envs[1], previous) == {'input_validation': expected}
    merged = merge_config(staged, staged['sides']['candidate'])
    executor = registry.build_executor('input_validation', 'kubernetes',
        {**for_check(merged, 'input_validation', 'REQ-09'),
         '_kubernetes_target_sha256_by_executor': {'input_validation': expected}},
        engine=envs[1].engine.name, version=envs[1].engine.version)
    assert type(executor) is KubernetesNormalTokenInputExecutor
    assert executor._expected_target_sha256 == expected
    current['comparison_config']['launch']['max_model_len'] = True
    _write(fresh, current)
    with pytest.raises(ValueError, match='NORMAL_TOKEN_INPUT_TARGET_UNAVAILABLE'):
        tool.revalidate_candidate(staged, envs[1], previous)
    _write(sides['candidate']['target'], current)
    invalid = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in invalid['checks'] if row['check'] == 'inputs')['detail'] == (
        'KUBERNETES_SCOPED_TARGET_INVALID')


def test_req09_rejects_mixed_legacy_dispatch_before_any_executor(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan
    from guard.executors.kubernetes_input_matrix import KubernetesMatrixInputValidationExecutor
    from guard.executors.kubernetes_normal_token_input import KubernetesNormalTokenInputExecutor
    from guard.store.packs import put_pack

    pack, sides, policy_path = sources
    pol = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    settings = {'target': str(sides['baseline']['target'])}
    engine, version = envs[0].engine.name, envs[0].engine.version
    legacy = registry.build_executor('input_validation', 'kubernetes', {'input_validation': settings},
                                     engine=engine, version=version)
    opted_out = registry.build_executor('input_validation', 'kubernetes',
        {'input_validation': {**settings, 'normal_token_input_only': False}}, engine=engine, version=version)
    assert type(legacy) is KubernetesMatrixInputValidationExecutor
    assert type(opted_out) is KubernetesNormalTokenInputExecutor
    with pytest.raises(ValueError, match='KUBERNETES_INPUT_NORMAL_SCOPE_INVALID'):
        opted_out.validate_configuration(pack.checks['REQ-09'])
    base = {'executors': {'input_validation': 'kubernetes'}, 'update': {'allowed_checks': ['REQ-09']},
            'sides': {side: {'input_validation': {'target': str(entry['target'])}} for side, entry in sides.items()}}

    def no_dispatch(*_args, **_kwargs):
        raise AssertionError('A missing or misplaced marker must never activate legacy input validation')

    monkeypatch.setattr(application, 'KubernetesTransition', no_dispatch)
    half = deepcopy(base)
    half['sides']['baseline']['check_config'] = {'REQ-09': {'normal_token_input_only': True}}
    wrong = {**base, 'check_config': {'SUP-08': {'normal_token_input_only': True}}}
    scoped = {**base, 'check_config': {'REQ-09': {'normal_token_input_only': True}}}
    unscoped = {**scoped, 'update': {}}
    with_sibling = {**scoped, 'update': {'allowed_checks': ['REQ-08', 'REQ-09']}}
    store = Store(tmp_path / 'store')
    for invalid in (half, wrong, unscoped, with_sibling):
        ready = application.update_readiness(pack, envs, pol, invalid)
        assert next(row for row in ready['checks'] if row['check'] == 'inputs')['detail'] == (
            'NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID')
        with pytest.raises(ValueError, match='NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID'):
            application.execute_update(store, pack, envs, pol, invalid, tmp_path / 'update', utcnow())
    assert not (tmp_path / 'update').exists()

    put_pack(store, pack.path)
    for item in (*envs, pol):
        store.put(item)
    plan = bind_recipe(select(pack, envs, pol, mode='update'), scoped, envs)
    store.put(plan)
    monkeypatch.setattr(registry, 'build_executor', no_dispatch)
    run = application.run_config(scoped)
    run.side_configs['candidate'].raw['check_config'].pop('REQ-09')
    with pytest.raises(ValueError, match='NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, plan, run)
    run.side_configs['baseline'].raw['check_config'].pop('REQ-09')
    with pytest.raises(ValueError, match='OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID'):
        run_plan(store, pack, plan, run)

    audit = select(pack, envs[:1], pol, mode='audit')
    with pytest.raises(ValueError, match='NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, audit, application.run_config(scoped))
    with pytest.raises(ValueError, match='NORMAL_TOKEN_INPUT_EXECUTION_SCOPE_INVALID'):
        application._validation_inputs(envs[0], scoped)

    mixed = deepcopy(scoped)
    mixed['executors']['request_binding'] = 'kubernetes'
    requested = application.run_config(mixed)
    transition, names = application._kubernetes_transition_configurations(
        {side: config.raw for side, config in requested.side_configs.items()})
    assert names == ('request_binding',)
    assert all(side['executors'] == {'request_binding': 'kubernetes'} for side in transition.values())
    assert all(set(config.raw['executors']) == {'request_binding', 'input_validation'}
               for config in requested.side_configs.values())
    for side in requested.side_configs.values():
        side.raw.pop('check_config')
    _, legacy = application._kubernetes_transition_configurations(
        {side: config.raw for side, config in requested.side_configs.items()})
    assert legacy == ('request_binding', 'input_validation')


def test_req10_pair_preserves_policy_scope_and_pins_the_staged_candidate(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan
    from guard.store.packs import put_pack

    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'REQ-10': {'allowed_sampling_options': ['temperature', 'top_p', 'max_tokens', 'min_tokens'],
                         'rejection_codes': ['400', '422']}}
    pol = original.model_copy(update={'values': values,
        'scope': original.scope.model_copy(update={'include': ['REQ-10']})}).seal()
    _write(policy_path, pol.to_dict())
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store')
    assert 'sampling_validation' not in default['executors']
    cfg, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store', normal_sampling=True)
    assert cfg['update']['allowed_checks'] == ['REQ-10'], decisions
    assert cfg['check_config'] == {'REQ-10': {'normal_sampling_only': True}}
    assert all('run_binding' not in side for side in cfg['sides'].values())
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    unscoped = deepcopy(cfg)
    unscoped['update'].pop('allowed_checks')
    blocked = application.update_readiness(pack, envs, pol, unscoped)
    assert next(row for row in blocked['checks'] if row['check'] == 'inputs')['detail'] == (
        'NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID')

    fresh = tmp_path / 'fresh-sampling-target.json'
    staged = tool.staged_config(cfg, fresh)
    preliminary, previous = tool._source(sides['candidate']['target'])
    with pytest.raises(FileNotFoundError):
        tool.revalidate_candidate(staged, envs[1], previous)
    for number, role in enumerate(('engine', 'probe'), 200):
        preliminary['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        preliminary['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, preliminary)
    hashes = tool.revalidate_candidate(staged, envs[1], previous)
    assert hashes == {'sampling_validation': sha256_hex(canonical_json(preliminary))}
    effective = for_check(merge_config(staged, staged['sides']['candidate']), 'sampling_validation', 'REQ-10')
    executor = registry.build_executor('sampling_validation', 'kubernetes',
        {**effective, '_kubernetes_target_sha256_by_executor': hashes},
        engine=envs[1].engine.name, version=envs[1].engine.version)
    assert executor._expected_target_sha256 == hashes['sampling_validation']

    store = Store(tmp_path / 'store')
    put_pack(store, pack.path)
    for item in (*envs, pol):
        store.put(item)
    plan = bind_recipe(select(pack, envs, pol, mode='update'), staged, envs)
    store.put(plan)

    def no_dispatch(*_args, **_kwargs):
        raise AssertionError('An omitted normal scope cannot dispatch the original sampling executor')

    monkeypatch.setattr(registry, 'build_executor', no_dispatch)
    run = application.run_config(staged)
    run.side_configs['candidate'].raw['check_config'].pop('REQ-10')
    with pytest.raises(ValueError, match='NORMAL_SAMPLING_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, plan, run)
    run.side_configs['baseline'].raw['check_config'].pop('REQ-10')
    with pytest.raises(ValueError, match='OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID'):
        run_plan(store, pack, plan, run)


def test_lif04_normal_preparation_binds_actual_target_without_enabling_stream_sibling(tmp_path, sources):
    from tests.test_lif04_normal_stream import configuration

    pack, sides, policy_path = sources
    _, _, _, full_values = configuration(tmp_path)
    lif04 = {row.key: full_values[row.key] for row in pack.checks['LIF-04'].policy_params}
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    pol = original.model_copy(update={'values': {'LIF-04': lif04},
        'scope': original.scope.model_copy(update={'include': ['LIF-03', 'LIF-04']})}).seal()
    _write(policy_path, pol.to_dict())
    cfg, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store',
                                       normal_stream_buffer=True)
    assert cfg['update']['allowed_checks'] == ['LIF-04']
    assert cfg['check_config'] == {'LIF-04': {'normal_stream_only': True}}
    assert decisions['LIF-04']['shared_executor_checks']['LIF-03']['baseline']['transport_dispatched'] is False
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    staged = tool.staged_config(cfg, tmp_path / 'fresh-stream-target.json')
    assert staged['sides']['candidate']['stream_buffer'] == {'target': str(tmp_path / 'fresh-stream-target.json')}
    altered = deepcopy(cfg)
    altered['update']['allowed_checks'].append('LIF-03')
    invalid = application.update_readiness(pack, envs, pol, altered)
    assert next(row for row in invalid['checks'] if row['check'] == 'inputs')['detail'] == (
        'NORMAL_STREAM_EXECUTION_SCOPE_INVALID')


def test_paired_lif03_and_lif04_keep_independent_executors_on_the_same_owned_target(tmp_path, sources, monkeypatch):
    from guard.executors import run_binding
    from guard.executors.kubernetes_normal_request_latency import KubernetesNormalRequestLatencyExecutor
    from guard.executors.kubernetes_stream_buffer import KubernetesStreamBufferExecutor
    from tests.test_lif04_normal_stream import configuration

    pack, sides, policy_path = sources
    _, _, _, full = configuration(tmp_path)
    expected_options = {'LIF-03': {'normal_request_latency_only': True}, 'LIF-04': {'normal_stream_only': True}}
    values = {cid: {row.key: full[row.key] for row in pack.checks[cid].policy_params} for cid in expected_options}
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    policy = original.model_copy(update={'values': values,
        'scope': original.scope.model_copy(update={'include': list(expected_options)})}).seal()
    _write(policy_path, policy.to_dict())
    store_path = tmp_path / 'store'
    cfg, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=store_path,
        normal_request_latency=True, normal_stream_buffer=True)
    assert cfg['update']['allowed_checks'] == ['LIF-03', 'LIF-04'], decisions
    assert cfg['check_config'] == expected_options
    assert cfg['executors'] == {'stream_buffer': 'kubernetes'}
    assert all(side == {'stream_buffer': {'target': str(sides[name]['target'])}}
               for name, side in cfg['sides'].items())
    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    ready = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    pinned = {env.id: sha256_hex(canonical_json(json.loads(side['target'].read_bytes())))
              for env, side in zip(envs, sides.values())}
    seen = set()

    def draft(check, env):
        seen.add((check.id, env.id))
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    def fixture_latency(self, check, env, policy_values, *_args):
        assert check.id == 'LIF-03' and self._expected_target_sha256 == pinned[env.id]
        assert set(self.cfg) == {'target', 'normal_request_latency_only'} and policy_values == values[check.id]
        return draft(check, env)

    def fixture_stream(self, check, env, policy_values, *_args):
        assert check.id == 'LIF-04' and self.normal_only and self.expected_target == pinned[env.id]
        assert set(self.cfg) == {'target', 'normal_stream_only'} and 'buffer_prepared_sha256' not in policy_values
        assert policy_values['buffer_target_sha256'] == pinned[env.id]
        assert policy_values['buffer_environment_sha256'] == env.ref().sha256
        return draft(check, env)

    default = registry.build_executor('stream_buffer', 'kubernetes', {'stream_buffer': {}},
        engine=envs[0].engine.name, version=envs[0].engine.version)
    assert type(default) is KubernetesStreamBufferExecutor and not default.normal_only
    monkeypatch.setattr(KubernetesNormalRequestLatencyExecutor, 'run', fixture_latency)
    monkeypatch.setattr(KubernetesStreamBufferExecutor, 'run', fixture_stream)
    store = Store(store_path)
    report, _ = application.execute_update(store, pack, envs, policy, cfg, tmp_path / 'paired', utcnow())
    assert seen == {(cid, env.id) for cid in values for env in envs}
    for row in report.by_check:
        if row.check_id not in values:
            continue
        for side in (row.baseline, row.candidate):
            evidence = store.resolve(store.resolve(side.result).evidence)
            binding = evidence.execution.run_binding
            assert evidence.execution.status == ExecStatus.COMPLETED and not run_binding.verify(binding, evidence)
            assert binding['files'] == {'buffer_target_sha256': pinned[evidence.environment.id]}
            assert (binding['validity'] is None) == (row.check_id == 'LIF-03')

    fresh = tmp_path / 'fresh-stream-target.json'
    staged = tool.staged_config(cfg, fresh)
    current, pointer = tool._source(sides['candidate']['target'])
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == {
        'stream_buffer': sha256_hex(canonical_json(current))}
    changed = deepcopy(staged)
    changed['check_config'].pop('LIF-03')
    with pytest.raises(ValueError, match='METADATA_STAGED_EXECUTION_SCOPE_INVALID'):
        tool.revalidate_candidate(changed, envs[1], pointer)


def test_lif03_scope_rejects_combined_or_incomplete_flags_before_preparation(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan

    pack, sides, policy_path = sources
    policy = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(side['environment'].read_bytes()) for side in sides.values()]
    marker = 'normal_request_latency_only'
    cfg = {'executors': {'stream_buffer': 'kubernetes'}, 'update': {'allowed_checks': ['LIF-03', 'LIF-04']},
           'sides': {side: {'stream_buffer': {'target': str(paths['target'])}} for side, paths in sides.items()},
           'check_config': {'LIF-03': {marker: True}, 'LIF-04': {'normal_stream_only': True}}}

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Invalid stream scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', forbidden)
    single_marker, false_marker, combined, shared_marker, override_target, prepared = (
        deepcopy(cfg) for _ in range(6))
    single_marker['check_config'].pop('LIF-04')
    false_marker['sides']['candidate']['check_config'] = {'LIF-03': {marker: False}}
    combined['check_config']['LIF-03']['normal_stream_only'] = True
    shared_marker['sides']['baseline']['stream_buffer'][marker] = True
    override_target['check_config']['LIF-03']['target'] = str(tmp_path / 'other.json')
    prepared['sides']['candidate']['stream_buffer']['prepared'] = '/fixture/unsupported'
    mixed_binding = {**cfg, 'run_binding': {'LIF-04': {}}}
    wrapped = {**cfg, 'record_attestation': {'mode': 'example'}}
    store = Store(tmp_path / 'store')
    for invalid in (single_marker, false_marker, combined, shared_marker, override_target, prepared, mixed_binding, wrapped):
        with pytest.raises(ValueError, match='NORMAL_REQUEST_LATENCY_EXECUTION_SCOPE_INVALID'):
            application.execute_update(store, pack, envs, policy, invalid, tmp_path / 'run', utcnow())
    with pytest.raises(ValueError, match='NORMAL_REQUEST_LATENCY_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, select(pack, envs[:1], policy, mode='audit'), application.run_config(cfg))
    assert not (tmp_path / 'run').exists()


def test_gateway_audit_wiring_is_prebound_and_requires_two_distinct_owned_requests(tmp_path, sources, monkeypatch):
    from tools import mvp_gateway_preparation as preparation

    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'OBS-01': {'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
                         'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256'],
                         'observation_max_delay_ms': 5000}}
    pol = original.model_copy(update={'values': values,
        'scope': original.scope.model_copy(update={'include': ['OBS-01', 'PLT-12']})}).seal()
    _write(policy_path, pol.to_dict())
    producer = tmp_path / 'selected-producer.py'
    producer.write_bytes(b'# fixture producer\n')
    monkeypatch.setattr(preparation, 'PRODUCER_SHA256', sha256_hex(producer.read_bytes()))
    gateway_inputs = {}
    for number, (side, paths) in enumerate(sides.items(), 1):
        target_doc = json.loads(paths['target'].read_bytes())
        roles = target_doc['kubernetes']
        roles['probe'].update(namespace=roles['engine']['namespace'], namespace_uid=roles['engine']['namespace_uid'])
        target_doc['kubernetes_gateway'] = {**roles['engine'], 'pod': f'gateway-{side}', 'container': 'gateway',
            'pod_uid': f'00000000-0000-0000-0001-{number:012d}', 'container_id': f'containerd://{90 + number:064x}',
            'pod_ip': f'10.2.3.{number}', 'image': preparation.GATEWAY_IMAGE, 'image_id': preparation.GATEWAY_IMAGE}
        _write(paths['target'], target_doc)
        gateway_inputs[side] = {'record_session': f'{number:032x}', 'record_producer': str(producer),
            'test_actors': {'allowed': 'a' * 64, 'denied': 'b' * 64},
            'gateway_record_source': {'environment': preparation.GATEWAY_ENV, 'target_id': 'fixture-target',
                'runtime_policy_sha256': 'f' * 64, 'request_sha256': f'{number:064x}'}}
    cfg, bindings, decisions = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store',
                                             gateway_record_inputs=gateway_inputs)
    assert cfg['update']['allowed_checks'] == ['OBS-01'], decisions
    assert cfg['check_config']['OBS-01'] == {'record_snapshot_only': True, 'gateway_record_snapshot_only': True}
    assert decisions['OBS-01']['shared_executor_checks']['PLT-12']['baseline']['transport_dispatched'] is False
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready

    def forbid_io(*_args, **_kwargs):
        pytest.fail('The Gateway receipt preflight must not contact a running service')

    monkeypatch.setattr(socket, 'socket', forbid_io)
    monkeypatch.setattr(subprocess, 'Popen', forbid_io)
    configuration = tmp_path / 'guard.toml'
    tool.write_config(cfg, configuration)
    config_ref = {'path': str(configuration), 'sha256': sha256_hex(configuration.read_bytes())}
    index = _write(tmp_path / 'inputs.json', {**tool.readiness.template(pack),
        'inputs': [{**binding, 'configuration': config_ref} for binding in bindings]})

    def gateway_rows():
        return {row['side']: row for row in tool.readiness.audit(index, pack=pack)['rows']
                if row['check_id'] == 'OBS-01'}

    rows = gateway_rows()
    assert all(row['file_binding_valid'] and row['executor_policy_valid']
               and not row['ready_for_execution'] and not row['engine_executed']
               and row['actual_results'] is None for row in rows.values())
    baseline = sides['baseline']['target']
    original = baseline.read_bytes()
    raw = merge_config(cfg, cfg['sides']['baseline'])
    paths = application._bounded_kubernetes_targets(raw, {'OBS-01': 'required_event_record'})
    assert paths == {'OBS-01': baseline}
    application._pin_prebound_kubernetes_targets(raw, envs[0], paths, {baseline: original})
    assert raw['_kubernetes_target_sha256_by_check'] == {'OBS-01': sha256_hex(canonical_json(json.loads(original)))}

    for problem in ('missing', 'wrong_namespace'):
        malformed = json.loads(original)
        if problem == 'missing':
            malformed.pop('kubernetes_gateway')
        else:
            malformed['kubernetes_gateway']['namespace_uid'] = '00000000-0000-0000-0002-000000000001'
        _write(baseline, malformed)
        rows = gateway_rows()
        assert not rows['baseline']['file_binding_valid']
        assert 'KUBERNETES_SCOPED_TARGET_INVALID' in rows['baseline']['blocking_codes']
        assert rows['candidate']['file_binding_valid'] and not rows['candidate']['engine_executed']
        application._pin_prebound_kubernetes_targets(raw, envs[0], paths, {baseline: baseline.read_bytes()})
        assert raw['_kubernetes_target_sha256_by_check'] == {'OBS-01': None}
    baseline.write_bytes(original)
    with pytest.raises(ValueError, match='GATEWAY_RECORDS_REQUIRE_PREBOUND_PAIR'):
        tool.staged_config(cfg, tmp_path / 'unbound-candidate.json')
    bad_mode = deepcopy(cfg)
    bad_mode['sides']['candidate']['check_config']['OBS-01']['gateway_record_snapshot_only'] = False
    invalid = application.update_readiness(pack, envs, pol, bad_mode)
    assert next(row for row in invalid['checks'] if row['check'] == 'inputs')['detail'] == (
        'OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID')
    gateway_inputs['candidate']['gateway_record_source']['request_sha256'] = (
        gateway_inputs['baseline']['gateway_record_source']['request_sha256'])
    with pytest.raises(ValueError, match='GATEWAY_DISTINCT_PAIRED_RECORDS_REQUIRED'):
        tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store', gateway_record_inputs=gateway_inputs)


def test_sup10_installed_inventory_uses_exact_pair_and_empty_policy(tmp_path, sources, monkeypatch):
    from guard.executors import run_binding
    from guard.executors.kubernetes_installed_plugin_inventory import KubernetesInstalledPluginInventoryExecutor
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor

    pack, sides, policy_path = sources
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    policy = prior.model_copy(update={'values': {},
        'scope': prior.scope.model_copy(update={'include': ['SUP-10', 'SUP-11']})}).seal()
    _write(policy_path, policy.to_dict())
    output, store_path, fresh = (tmp_path / name for name in ('inventory', 'store', 'fresh-target.json'))
    default, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store_path)
    assert 'supply_boot' not in default['executors']
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store_path), '--output', str(output),
            '--installed-plugin-inventory', '--candidate-target-after-baseline', str(fresh)]
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    cfg = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert cfg['executors'] == {'supply_boot': 'kubernetes'}
    assert cfg['update']['allowed_checks'] == ['SUP-10']
    assert cfg['check_config'] == {'SUP-10': {'installed_plugin_inventory_only': True}}
    assert all(side == {'supply_boot': {'target': str(sides[name]['target'])}}
               for name, side in cfg['sides'].items())
    readiness = json.loads((output / 'readiness.json').read_bytes())
    rows = [row for row in readiness['rows'] if row['check_id'] == 'SUP-10']
    assert len(rows) == 2 and readiness['active_checks'] == 69
    for row in rows:
        assert row['file_binding_valid'] and row['installed_plugin_inventory_only'], row
        assert row['binding_schema'] == run_binding.SCHEMA and not row['engine_executed']
    siblings = readiness['metadata_wiring']['SUP-10']['shared_executor_checks']
    assert not siblings['SUP-11']['baseline']['transport_dispatched']

    envs = [tool.Environment.model_validate_json(settings['environment'].read_bytes()) for settings in sides.values()]
    ready = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    legacy = registry.build_executor('supply_boot', 'kubernetes', {'supply_boot': {}},
        engine=envs[0].engine.name, version=envs[0].engine.version)
    assert type(legacy) is KubernetesSupplyBootExecutor
    expected = {env.id: sha256_hex(canonical_json(json.loads(side['target'].read_bytes())))
                for env, side in zip(envs, sides.values())}

    def fixture_inventory(self, check, env, values, *_args):
        assert check.id == 'SUP-10' and values == {}
        assert self._expected_target_sha256 == expected[env.id]
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    monkeypatch.setattr(KubernetesInstalledPluginInventoryExecutor, 'run', fixture_inventory)
    store = Store(store_path)
    report, _ = application.execute_update(store, pack, envs, policy, cfg, tmp_path / 'paired', utcnow())
    row = next(row for row in report.by_check if row.check_id == 'SUP-10')
    assert row.baseline.security == row.candidate.security == 'UNKNOWN'
    for side in (row.baseline, row.candidate):
        evidence = store.resolve(store.resolve(side.result).evidence)
        binding = evidence.execution.run_binding
        assert evidence.execution.status == ExecStatus.COMPLETED and not run_binding.verify(binding, evidence)
        assert binding['files'] == {'supply_target_sha256': expected[evidence.environment.id]}
        assert 'values' not in binding

    staged = tomllib.loads((output / 'guard.toml').read_text())
    current, pointer = tool._source(sides['candidate']['target'])
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == {
        'supply_boot': sha256_hex(canonical_json(current))}
    staged['check_config'].clear()
    with pytest.raises(ValueError, match='METADATA_STAGED_EXECUTION_SCOPE_INVALID'):
        tool.revalidate_candidate(staged, envs[1], pointer)


def test_sup10_installed_inventory_scope_cannot_fall_through_to_supply_boot(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan

    pack, sides, policy_path = sources
    policy = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    option = 'installed_plugin_inventory_only'
    base = {'executors': {'supply_boot': 'kubernetes'}, 'update': {'allowed_checks': ['SUP-10']},
            'sides': {side: {'supply_boot': {'target': str(paths['target'])}} for side, paths in sides.items()}}
    cfg = {**base, 'check_config': {'SUP-10': {option: True}}}
    half = deepcopy(base)
    half['sides']['baseline']['check_config'] = cfg['check_config']
    wrong_check = {**base, 'check_config': {'SUP-11': {option: True}}}
    false_marker = {**base, 'check_config': {'SUP-10': {option: False}}}
    sibling = {**cfg, 'update': {'allowed_checks': ['SUP-10', 'SUP-11']}}
    mixed = {**cfg, 'run_binding': {'SUP-10': {}}}
    prepared = {**cfg, 'supply_boot': {'normal': {'prepared': '/fixture/unused'}}}
    wrapped = {**cfg, 'record_attestation': {'mode': 'example'}}

    def no_preparation(*_args, **_kwargs):
        raise AssertionError('Invalid installed inventory scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', no_preparation)
    store = Store(tmp_path / 'store')
    for invalid in (half, wrong_check, false_marker, sibling, mixed, prepared, wrapped):
        with pytest.raises(ValueError, match='INSTALLED_PLUGIN_INVENTORY_EXECUTION_SCOPE_INVALID'):
            application.execute_update(store, pack, envs, policy, invalid, tmp_path / 'run', utcnow())
    assert not (tmp_path / 'run').exists()
    with pytest.raises(ValueError, match='INSTALLED_PLUGIN_INVENTORY_EXECUTION_SCOPE_INVALID'):
        run_plan(store, pack, select(pack, envs[:1], policy, mode='audit'), application.run_config(cfg))


def test_sup05_cli_binds_both_saved_preparations_and_keeps_the_ordinary_sibling(tmp_path, external, monkeypatch):
    from guard.executors.kubernetes_sup05_normal_preparation import prepare
    from tests.test_kubernetes_request_binding import target as ordinary_target
    from tests.test_sup05_normal_preparation import _case

    def forbid_io(*_args, **_kwargs):
        pytest.fail('Saved declarations must not contact or launch the workload')

    monkeypatch.setattr(socket, 'socket', forbid_io)
    monkeypatch.setattr(subprocess, 'Popen', forbid_io)
    pack = load_pack(tool.readiness.ROOT / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['SUP-05', 'SUP-10', 'SUP-11']}, values={},
                 status='CONFIRMED', data_origin=DataOrigin.DECLARED, confirmed_by='Fixture owner', confirmed_at=T0)
    policy_path = _write(tmp_path / 'policy.json', pol.to_dict())
    inputs, references, outputs, envs = {}, {}, {}, []
    for index, (side, row) in enumerate(tool.versions().items(), 1):
        source = external / side
        source.mkdir()
        case = _case(source, row['version'])
        runtime = case.env.runtime.model_copy(update={'gpu_model': 'Fixture GPU', 'driver': 'Fixture driver'})
        env = case.env.model_copy(update={'id': f'ENV-EXAMPLE-SUP05-{side.upper()}',
                                          'data_origin': DataOrigin.DECLARED, 'runtime': runtime}).seal()
        envs.append(env)
        args = case.args
        _write(args['environment'], env.to_dict())
        lifecycle = json.loads(args['target'].read_bytes())
        lifecycle['environment_ref'] = env.ref().model_dump(mode='json')
        engine = lifecycle['kubernetes']['engine']
        engine['container_id'] = f'containerd://{index:064x}'
        engine['image_id'] = row['image']
        case.snapshot['pod']['status']['containerStatuses'][0].update(
            containerID=engine['container_id'], imageID=engine['image_id'])
        args['owner_snapshot_sha256'] = sha256_hex(_write(args['owner_snapshot'], case.snapshot).read_bytes())
        _write(args['target'], lifecycle)
        outputs[side] = prepare(case.output, REPOSITORY, **args)
        path = case.output / 'preparation.json'
        references[side] = {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}

        declaration = ordinary_target(env)
        declaration.update(lab=False, observed_version=row['version'], kubernetes={
            **declaration['kubernetes'], 'engine': engine})
        declaration['engine']['image_digest'] = row['image']
        inputs[side] = {'environment': args['environment'], 'target': _write(source / 'ordinary-target.json', declaration)}

    options = {'pack': pack, 'store': tmp_path / 'store', 'installed_plugin_inventory': True}
    default, _, decisions = tool.assemble(inputs, policy_path, **options)
    assert default['update']['allowed_checks'] == ['SUP-10'] and 'SUP-05' not in decisions
    with pytest.raises(ValueError, match='^SUP05_PAIRED_NORMAL_PREPARATIONS_REQUIRED$'):
        tool.assemble(inputs, policy_path, sup05_normal_preparations={'baseline': references['baseline']}, **options)
    for candidate in (references['baseline'], {**references['candidate'], 'sha256': '0' * 64}):
        invalid = {**references, 'candidate': candidate}
        fallback, _, decisions = tool.assemble(inputs, policy_path, sup05_normal_preparations=invalid, **options)
        assert fallback == default
        assert decisions['SUP-05'] == {'configuration_enabled': False,
            'sides': {'baseline': None, 'candidate': 'SUP05_PREPARATION_RECEIPT_INVALID'}}

    index_path = _write(tmp_path / 'sup05-preparations.json', references)
    argv = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(options['store']),
            '--sup05-normal-preparations', str(index_path), '--installed-plugin-inventory']
    for side, paths in inputs.items():
        for role, path in paths.items():
            argv.extend([f'--{side}-{role}', str(path)])
    output = tmp_path / 'assembled'
    monkeypatch.setattr(sys, 'argv', [*argv, '--output', str(output)])
    tool.main()
    config = tomllib.loads((output / 'guard.toml').read_text())
    assert config['update']['allowed_checks'] == ['SUP-10', 'SUP-05']
    assert config['check_config']['SUP-05'] == {'normal_file_reads_only': True}
    for side in inputs:
        assert config['sides'][side] == {'check_config': {
            'SUP-05': {'normal': outputs[side]['normal'], 'preparation_receipt': references[side]},
            'SUP-10': {'target': str(inputs[side]['target'])},
        }}
    ready = application.update_readiness(pack, envs, pol, config)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    audit = json.loads((output / 'readiness.json').read_bytes())
    assert audit['active_checks'] == 69 and audit['file_binding_valid_sides'] == 4
    assert audit['actual_results'] is None and not audit['ready_for_execution']
    index = output / 'input-sources.json'
    assert audit['source_index_sha256'] == sha256_hex(index.read_bytes())
    published_config = {'path': str(output / 'guard.toml'), 'sha256': sha256_hex((output / 'guard.toml').read_bytes())}
    for row in audit['rows']:
        if row['file_binding_valid']:
            assert row['files']['configuration'] == published_config
        if row['check_id'] == 'SUP-05':
            assert row['sup05_normal_preparation'] == references[row['side']]
    assert all(row['configuration'] == published_config for row in json.loads(index.read_bytes())['inputs'])
    with monkeypatch.context() as replay_environment:
        replay_environment.chdir(external)
        replayed = tool.readiness.audit(index, pack=pack)
    assert replayed['rows'] == audit['rows']
    conflicting = {**references, 'candidate': {**references['candidate'], 'sha256': '0' * 64}}
    mismatch = tool.readiness.audit(index, pack=pack, sup05_normal_preparations=conflicting)
    assert mismatch['file_binding_valid_sides'] == 3
    candidate = next(row for row in mismatch['rows'] if row['check_id'] == 'SUP-05' and row['side'] == 'candidate')
    assert candidate['blocking_codes'] == ['SUP05_NORMAL_PREPARATION_REFERENCE_MISMATCH']
    misplaced = deepcopy(config)
    misplaced['sides']['candidate']['check_config']['SUP-10']['preparation_receipt'] = references['candidate']
    with pytest.raises(ValueError, match='^NORMAL_FILE_READS_EXECUTION_SCOPE_INVALID$'):
        application._validate_requested_normal_scopes(misplaced, pack, update_allowed_checks(misplaced, pack))
    sibling = audit['metadata_wiring']['SUP-05']['shared_executor_checks']['SUP-11']
    assert all(not row['transport_dispatched'] for row in sibling.values())
    with pytest.raises(ValueError, match='^SUP05_NORMAL_PREPARATIONS_REQUIRE_PREBOUND_PAIR$'):
        tool.staged_config(config, tmp_path / 'fresh-candidate.json')

    nested = Path(references['baseline']['path']).parent / 'cannot-overwrite-preparation'
    monkeypatch.setattr(sys, 'argv', [*argv, '--output', str(nested)])
    with pytest.raises(ValueError, match='outside source inputs'):
        tool.main()
    assert not nested.exists()

    prepared = Path(outputs['candidate']['normal']['prepared'])
    original_bytes = prepared.read_bytes()
    assemble = tool.assemble

    def change_after_assembly(*args, **kwargs):
        result = assemble(*args, **kwargs)
        assert result[2]['SUP-05']['configuration_enabled']
        prepared.write_bytes(original_bytes + b'\n')
        return result

    declined = tmp_path / 'declined'
    with monkeypatch.context() as late:
        late.setattr(tool, 'assemble', change_after_assembly)
        late.setattr(sys, 'argv', [*argv, '--output', str(declined)])
        with pytest.raises(ValueError, match='^SUP05_NORMAL_PREPARATION_PUBLICATION_UNVERIFIED$'):
            tool.main()
    assert declined.is_dir() and not any(declined.iterdir())
    assert prepared.read_bytes() == original_bytes + b'\n'
    stale = tool.readiness.audit(index, pack=pack)
    assert stale['file_binding_valid_sides'] == 3
    candidate = next(row for row in stale['rows'] if row['check_id'] == 'SUP-05' and row['side'] == 'candidate')
    assert candidate['blocking_codes'] == ['SUP05_PREPARATION_RECEIPT_INVALID']
    blocked = application.update_readiness(pack, envs, pol, config)
    inputs_check = next(row for row in blocked['checks'] if row['check'] == 'inputs')
    assert not inputs_check['ok'] and inputs_check['detail'] == 'SUP05_PREPARATION_RECEIPT_INVALID'


def _startup_declaration(paths, *, source_commit=None):
    env = tool.Environment.model_validate_json(paths['environment'].read_bytes())
    env = env.model_copy(update={'runtime': env.runtime.model_copy(update={'pp_size': 1, 'dp_size': 1})})
    if source_commit is not None:
        env = env.model_copy(update={'engine': env.engine.model_copy(update={'source_commit': source_commit})})
    env = env.seal()
    target = json.loads(paths['target'].read_bytes())
    target['environment_ref'] = env.ref().model_dump(mode='json')
    target['model_mapping']['environment_ref'] = target['environment_ref']
    started = target['kubernetes']['engine']['started_at'] = '2026-11-18T08:00:00Z'
    target['startup_log_prefix'] = {'byte_count': 400, 'sha256': 'a' * 64, 'container_started_at': started,
                                    'ready_at': '2026-11-18T08:00:01Z'}
    _write(paths['environment'], env.to_dict())
    _write(paths['target'], target)
    return env, target


def test_startup_tools_check_exact_declared_provider_source(tmp_path, sources, monkeypatch):
    pack, sides, policy_path = sources
    envs = [_startup_declaration(paths)[0] for paths in sides.values()]
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'LIF-01': {'supply_stage_budget_ms': {'load': 600000},
                         'supply_stage_budget_bytes': {'load': 17179869184}, 'supply_total_budget_ms': 1800000}}
    required = {'LIF-01': 'NORMAL_STARTUP_LOAD_SOURCE_UNAVAILABLE',
                'SUP-07': 'NORMAL_COMPILE_CACHE_SOURCE_UNAVAILABLE'}
    pol = prior.model_copy(update={'values': values,
        'scope': prior.scope.model_copy(update={'include': [*required, 'SUP-10']})}).seal()
    _write(policy_path, pol.to_dict())

    def forbid_io(*_args, **_kwargs):
        pytest.fail('Startup declaration checks must not contact the workload')

    monkeypatch.setattr(socket, 'socket', forbid_io)
    monkeypatch.setattr(subprocess, 'Popen', forbid_io)
    options = {'pack': pack, 'store': tmp_path / 'store', 'normal_startup_load': True,
               'normal_compile_cache': True, 'installed_plugin_inventory': True}
    cfg, bindings, _ = tool.assemble(sides, policy_path, **options)
    assert cfg['update']['allowed_checks'] == [*required, 'SUP-10']

    envs[1], target = _startup_declaration(sides['candidate'], source_commit='f' * 40)
    for _, transport, _ in tool.readiness.STARTUP_OBSERVERS.values():
        transport(target, envs[1], duration=1, runner=forbid_io)
    fallback, _, decisions = tool.assemble(sides, policy_path, **options)
    assert fallback['update']['allowed_checks'] == ['SUP-10']
    for check_id, reason in required.items():
        assert decisions[check_id]['sides'] == {'baseline': None, 'candidate': reason}
    prepared = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in prepared['checks'] if row['check'] == 'inputs')['detail'] == 'KUBERNETES_SCOPED_TARGET_INVALID'

    configuration = tmp_path / 'guard.toml'
    tool.write_config(cfg, configuration)
    config_ref = {'path': str(configuration), 'sha256': sha256_hex(configuration.read_bytes())}
    for binding in bindings:
        _, binding['environment'] = tool._source(sides[binding['side']]['environment'])
    index = _write(tmp_path / 'sources.json', {**tool.readiness.template(pack),
        'inputs': [{**binding, 'configuration': config_ref} for binding in bindings]})
    report = tool.readiness.audit(index, pack=pack)
    assert report['file_binding_valid_sides'] == 4
    for row in report['rows']:
        if row['check_id'] in required and row['side'] == 'candidate':
            assert row['blocking_codes'] == [required[row['check_id']]]
        assert not row['engine_executed'] and row['actual_results'] is None


@pytest.mark.parametrize(('compile_enabled', 'reader_enabled', 'graph_enabled'), [
    (False, False, False), (True, False, False), (True, True, False), (True, True, True),
])
def test_startup_checks_prepare_separate_owned_bindings(
        tmp_path, sources, monkeypatch, compile_enabled, reader_enabled, graph_enabled):
    from guard.adapters.engine_provider import get_provider
    from guard.core.run import run_plan
    from guard.executors import run_binding
    from guard.executors.engine_sensor import EngineSensorExecutor
    from guard.executors.kubernetes_installed_plugin_inventory import KubernetesInstalledPluginInventoryExecutor
    from guard.executors.kubernetes_normal_compile_cache import KubernetesNormalCompileCacheExecutor
    from guard.executors.kubernetes_normal_graph_capture import KubernetesNormalGraphCaptureExecutor
    from guard.executors.kubernetes_normal_safetensors_reader import KubernetesNormalSafetensorsReaderExecutor
    from guard.executors.kubernetes_normal_startup_load import KubernetesNormalStartupLoadExecutor
    from guard.executors.kubernetes_supply_boot import KubernetesSupplyBootExecutor
    from guard.models import Side
    from guard.store.packs import put_pack

    pack, sides, policy_path = sources
    provider = get_provider('vllm')
    envs = []
    pins = {}
    lines_by_side = ((5416, 1094, (895, 918, 430), 6929), (5354, 1090, (1063, 1086, 484), 6854))
    for paths, (load_line, cache_line, reader_lines, graph_line) in zip(sides.values(), lines_by_side):
        env, target = _startup_declaration(paths)
        envs.append(env)
        pins[env.id] = sha256_hex(canonical_json(target))
        profile = provider.evidence_contract('startup_load_source', env)
        assert profile['logger_call_linenumber'] == load_line and len(profile['file_sha256']) == 5
        assert profile['required_env'] == {'VLLM_USE_V2_MODEL_RUNNER': '0'}
        cache = provider.evidence_contract('compile_cache_source', env)
        assert cache['directory_call_linenumber'] == cache_line and cache['completion_call_linenumber'] == 53
        assert cache['cache_root'] == '/cache/vllm' and cache['artifact_name'] == 'vllm_compile_cache.py'
        reader = provider.evidence_contract('safetensors_reader_source', env)
        assert (reader['filesystem_call_linenumber'], reader['default_strategy_call_linenumber'],
                reader['loader_completion_call_linenumber']) == reader_lines
        assert provider.evidence_contract('graph_capture_source', env) == {
            'capture_completion_call_linenumber': graph_line}
        wrong = env.model_copy(update={'engine': env.engine.model_copy(update={'image_digest': None})})
        with pytest.raises(ValueError, match='LIF01_BOOT_SOURCE_UNQUALIFIED'):
            provider.evidence_contract('startup_load_source', wrong)
        with pytest.raises(ValueError, match='SUP07_COMPILE_CACHE_SOURCE_UNQUALIFIED'):
            provider.evidence_contract('compile_cache_source', wrong)
        with pytest.raises(ValueError, match='SUP12_SAFETENSORS_READER_SOURCE_UNQUALIFIED'):
            provider.evidence_contract('safetensors_reader_source', wrong)
        with pytest.raises(ValueError, match='REQ20_CAPTURE_SOURCE_UNQUALIFIED'):
            provider.evidence_contract('graph_capture_source', wrong)

    budgets = {'supply_stage_budget_ms': {'load': 600000}, 'supply_stage_budget_bytes': {'load': 17179869184},
               'supply_total_budget_ms': 1800000}
    options = {'LIF-01': 'normal_startup_load_only', 'SUP-10': 'installed_plugin_inventory_only'}
    values_by_check = {'LIF-01': budgets}
    if compile_enabled:
        options['SUP-07'] = 'normal_compile_cache_only'
    if reader_enabled:
        options['SUP-12'] = 'normal_safetensors_reader_only'
        values_by_check['SUP-12'] = {'supply_allowed_readers': ['safetensors']}
    if graph_enabled:
        options['REQ-20'] = 'normal_graph_capture_only'
    prior = tool.Policy.model_validate_json(policy_path.read_bytes())
    policy = prior.model_copy(update={'values': values_by_check,
        'scope': prior.scope.model_copy(update={'include': [*sorted(options), 'SUP-11', 'REQ-19']})}).seal()
    _write(policy_path, policy.to_dict())
    output, store_path, fresh = (tmp_path / name for name in ('startup', 'store', 'fresh-target.json'))
    empty, _, _ = tool.assemble(sides, policy_path, pack=pack, store=store_path)
    assert not empty['executors']
    without_prefix = deepcopy(sides)
    incomplete = json.loads(sides['candidate']['target'].read_bytes())
    incomplete.pop('startup_log_prefix')
    without_prefix['candidate']['target'] = _write(tmp_path / 'candidate-no-prefix.json', incomplete)
    fallback, _, decisions = tool.assemble(without_prefix, policy_path, pack=pack, store=store_path,
        normal_startup_load=True, installed_plugin_inventory=True, normal_compile_cache=compile_enabled,
        normal_safetensors_reader=reader_enabled, normal_graph_capture=graph_enabled)
    assert fallback['update']['allowed_checks'] == ['SUP-10']
    assert decisions['LIF-01']['sides']['candidate'] == 'NORMAL_STARTUP_LOAD_TARGET_UNAVAILABLE'
    if compile_enabled:
        assert decisions['SUP-07']['sides']['candidate'] == 'NORMAL_COMPILE_CACHE_TARGET_UNAVAILABLE'
    if graph_enabled:
        assert decisions['REQ-20']['sides']['candidate'] == 'NORMAL_GRAPH_CAPTURE_TARGET_UNAVAILABLE'
    if reader_enabled:
        assert decisions['SUP-12']['sides']['candidate'] == 'NORMAL_SAFETENSORS_READER_TARGET_UNAVAILABLE'
        unsupported_values = {**values_by_check, 'SUP-12': {'supply_allowed_readers': ['safetensors', 'other']}}
        unsupported = policy.model_copy(update={'values': unsupported_values}).seal()
        unsupported_path = _write(tmp_path / 'unsupported-reader-policy.json', unsupported.to_dict())
        partial, _, decisions = tool.assemble(sides, unsupported_path, pack=pack, store=store_path,
            normal_startup_load=True, installed_plugin_inventory=True, normal_compile_cache=compile_enabled,
            normal_safetensors_reader=True, normal_graph_capture=graph_enabled)
        assert partial['update']['allowed_checks'] == sorted(set(options) - {'SUP-12'})
        assert not decisions['SUP-12']['configuration_enabled']
        assert set(decisions['SUP-12']['sides'].values()) == {'METADATA_EXECUTOR_OR_POLICY_UNAVAILABLE'}
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--store', str(store_path), '--output', str(output),
            '--normal-startup-load', '--installed-plugin-inventory', '--candidate-target-after-baseline', str(fresh)]
    if compile_enabled:
        args.append('--normal-compile-cache')
    if reader_enabled:
        args.append('--normal-safetensors-reader')
    if graph_enabled:
        args.append('--normal-graph-capture')
    for side, paths in sides.items():
        for role, path in paths.items():
            args.extend([f'--{side}-{role}', str(path)])
    monkeypatch.setattr(sys, 'argv', args)
    tool.main()
    cfg = tomllib.loads((output / 'preliminary-guard.toml').read_text())
    assert cfg['executors'] == {pack.checks[cid].executor: 'kubernetes' for cid in options}
    assert cfg['update']['allowed_checks'] == sorted(options)
    assert cfg['check_config'] == {check: {option: True} for check, option in options.items()}
    preflight = json.loads((output / 'readiness.json').read_bytes())
    origins = [row for row in preflight['rows'] if row['check_id'] in options]
    assert len(origins) == 2 * len(options) and preflight['active_checks'] == 69
    assert all(row['file_binding_valid'] and row[options[row['check_id']]]
               and row['binding_schema'] == run_binding.SCHEMA and not row['engine_executed'] for row in origins), origins
    prepared = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in prepared['checks'] if row['check'] == 'inputs')['ok'], prepared
    def forbid_preflight_io(*_args, **_kwargs):
        pytest.fail('Startup declaration checks must not contact the workload')

    for check_id, override, reason in (
        ('SUP-12', '--safetensors-load-strategy=eager', 'SUP12_READER_TARGET_OR_SOURCE_UNVERIFIED'),
        ('REQ-20', '--enforce-eager', 'REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED'),
    ):
        if check_id not in options:
            continue
        path = sides['candidate']['target']
        original = path.read_bytes()
        declaration = json.loads(original)
        declaration['comparison_config']['launch']['argv'].append(override)
        try:
            _write(path, declaration)
            with monkeypatch.context() as pure:
                pure.setattr(socket, 'socket', forbid_preflight_io)
                pure.setattr(subprocess, 'Popen', forbid_preflight_io)
                prepared = application.update_readiness(pack, envs, policy, cfg)
                rows = tool.readiness.audit(output / 'input-sources.json', pack=pack)['rows']
            check = next(row for row in prepared['checks'] if row['check'] == 'inputs')
            assert not check['ok'] and check['detail'] == reason
            checked = [row for row in rows if row['check_id'] in options]
            rejected, = [row for row in checked if not row['file_binding_valid']]
            assert (rejected['check_id'], rejected['side'], rejected['blocking_codes']) == (check_id, 'candidate', [reason])
            assert all(not row['engine_executed'] and row['actual_results'] is None for row in checked)
        finally:
            path.write_bytes(original)

    def sample(check, env, values, executor, option):
        assert values == values_by_check.get(check.id, {})
        assert executor._expected_target_sha256 == pins[env.id]
        assert set(executor.cfg) == {'target', option}
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                             normal_control={'attempted': False, 'succeeded': None, 'detail': ''})

    reader_run = KubernetesNormalSafetensorsReaderExecutor.run
    graph_run = KubernetesNormalGraphCaptureExecutor.run
    monkeypatch.setattr(KubernetesNormalStartupLoadExecutor, 'run',
        lambda self, check, env, values, *_: sample(check, env, values, self, 'normal_startup_load_only'))
    monkeypatch.setattr(KubernetesInstalledPluginInventoryExecutor, 'run',
        lambda self, check, env, values, *_: sample(check, env, values, self, 'installed_plugin_inventory_only'))
    monkeypatch.setattr(KubernetesNormalCompileCacheExecutor, 'run',
        lambda self, check, env, values, *_: sample(check, env, values, self, 'normal_compile_cache_only'))
    monkeypatch.setattr(KubernetesNormalSafetensorsReaderExecutor, 'run',
        lambda self, check, env, values, *_: sample(check, env, values, self, 'normal_safetensors_reader_only'))
    monkeypatch.setattr(KubernetesNormalGraphCaptureExecutor, 'run',
        lambda self, check, env, values, *_: sample(check, env, values, self, 'normal_graph_capture_only'))

    def forbid_legacy(*_args, **_kwargs):
        raise AssertionError('The prepared boot or challenge collector must stay outside the paired scope')

    monkeypatch.setattr(KubernetesSupplyBootExecutor, 'run', forbid_legacy)
    monkeypatch.setattr(EngineSensorExecutor, 'run', forbid_legacy)
    store = Store(store_path)
    report, _ = application.execute_update(store, pack, envs, policy, cfg, tmp_path / 'paired', utcnow())
    for check_id in options:
        row = next(row for row in report.by_check if row.check_id == check_id)
        assert row.baseline.security == row.candidate.security == 'UNKNOWN'
        for side in (row.baseline, row.candidate):
            evidence = store.resolve(store.resolve(side.result).evidence)
            binding = evidence.execution.run_binding
            assert evidence.execution.status == ExecStatus.COMPLETED and not run_binding.verify(binding, evidence)
            target_field = 'sensor_target_sha256' if check_id == 'REQ-20' else 'supply_target_sha256'
            assert binding['files'] == {target_field: pins[evidence.environment.id]}
            assert 'values' not in binding

    staged = tomllib.loads((output / 'guard.toml').read_text())
    current, pointer = tool._source(sides['candidate']['target'])
    for number, role in enumerate(('engine', 'probe'), 200):
        current['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        current['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    current['kubernetes']['engine']['started_at'] = '2026-11-18T08:02:00Z'
    current['startup_log_prefix'].update(
        container_started_at='2026-11-18T08:02:00Z', ready_at='2026-11-18T08:02:01Z', sha256='b' * 64)
    _write(fresh, current)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == dict.fromkeys(
        cfg['executors'], sha256_hex(canonical_json(current)))
    current.pop('startup_log_prefix')
    _write(fresh, current)
    pins[envs[1].id] = sha256_hex(canonical_json(current))
    run = application.run_config(staged)
    with pytest.raises(ValueError, match='^NORMAL_STARTUP_LOAD_TARGET_UNAVAILABLE$'):
        tool.revalidate_candidate(staged, envs[1], pointer)
    startup_gaps = {'LIF-01': 'NORMAL_STARTUP_LOAD_TARGET_UNAVAILABLE'}
    if compile_enabled:
        startup_gaps['SUP-07'] = 'NORMAL_COMPILE_CACHE_TARGET_UNAVAILABLE'
    assert tool.bind_staged_candidate(run, envs[1], pointer) == startup_gaps
    raw_candidate = run.side_configs['candidate'].raw
    assert raw_candidate['_kubernetes_target_sha256_by_executor'] == dict.fromkeys(
        cfg['executors'].keys() - {'supply_boot'}, pins[envs[1].id])
    assert raw_candidate['_kubernetes_target_sha256_by_check'] == {
        cid: None if cid in startup_gaps else pins[envs[1].id]
        for cid in options if pack.checks[cid].executor == 'supply_boot'}
    staged_store = Store(tmp_path / 'staged-store')
    put_pack(staged_store, pack.path)
    for obj in (*envs, policy):
        staged_store.put(obj)
    plan = bind_recipe(select(pack, envs, policy, mode='update'), staged, envs)
    staged_store.put(plan)
    monkeypatch.setattr(KubernetesNormalStartupLoadExecutor, 'run', forbid_legacy)
    monkeypatch.setattr(KubernetesNormalCompileCacheExecutor, 'run', forbid_legacy)
    monkeypatch.setattr(KubernetesNormalSafetensorsReaderExecutor, 'run', reader_run)
    monkeypatch.setattr(KubernetesNormalGraphCaptureExecutor, 'run', graph_run)
    actual = {row.check_id: row for row in run_plan(staged_store, pack, plan, run, only_side=Side.candidate)}
    for check_id, reason in startup_gaps.items():
        evidence = actual[check_id]
        assert evidence.execution.status == ExecStatus.ABORTED and evidence.execution.abort_reason == reason
        assert evidence.execution.executor is None and not evidence.normal_control.attempted and not evidence.artifacts
    if reader_enabled:
        assert actual['SUP-12'].execution.status == ExecStatus.ABORTED
    if graph_enabled:
        assert actual['REQ-20'].execution.status == ExecStatus.ABORTED
    assert actual['REQ-19'].execution.status == ExecStatus.NOT_RUN
    assert actual['SUP-10'].execution.status == ExecStatus.COMPLETED
    assert actual['SUP-11'].execution.status == ExecStatus.NOT_RUN


@pytest.mark.parametrize(('check_id', 'marker', 'scope_error'), [
    ('LIF-01', 'normal_startup_load_only', 'NORMAL_STARTUP_LOAD_EXECUTION_SCOPE_INVALID'),
    ('SUP-07', 'normal_compile_cache_only', 'NORMAL_COMPILE_CACHE_EXECUTION_SCOPE_INVALID'),
    ('SUP-12', 'normal_safetensors_reader_only', 'NORMAL_SAFETENSORS_READER_EXECUTION_SCOPE_INVALID'),
])
def test_startup_supply_checks_cannot_enable_legacy_dispatch(tmp_path, sources, monkeypatch, check_id, marker, scope_error):
    from guard.core.run import run_plan

    pack, sides, policy_path = sources
    policy = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(paths['environment'].read_bytes()) for paths in sides.values()]
    config = {'executors': {'supply_boot': 'kubernetes'}, 'update': {'allowed_checks': [check_id, 'SUP-10']},
              'sides': {side: {'supply_boot': {'target': str(paths['target'])}} for side, paths in sides.items()},
              'check_config': {check_id: {marker: True}, 'SUP-10': {'installed_plugin_inventory_only': True}}}
    missing, wrong, false_marker, target_override = (deepcopy(config) for _ in range(4))
    missing['check_config'].pop('SUP-10')
    wrong['check_config']['SUP-11'] = wrong['check_config'].pop(check_id)
    false_marker['sides']['candidate']['check_config'] = {check_id: {marker: False}}
    target_override['check_config']['SUP-10']['target'] = str(tmp_path / 'different-target.json')
    sibling = {**config, 'update': {'allowed_checks': [check_id, 'SUP-10', 'SUP-11']}}
    mixed = {**config, 'run_binding': {check_id: {}}}
    wrapped = {**config, 'record_attestation': {'mode': 'example'}}
    custom_module = deepcopy(config)
    custom_module['check_config'][check_id]['reader_module'] = 'other'

    def forbid_prepare(*_args, **_kwargs):
        raise AssertionError('Invalid startup scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', forbid_prepare)
    store = Store(tmp_path / 'scope-store')
    for invalid in (missing, wrong, false_marker, target_override, sibling, mixed, wrapped, custom_module):
        with pytest.raises(ValueError, match=f'^{scope_error}$'):
            application.execute_update(store, pack, envs, policy, invalid, tmp_path / 'run', utcnow())
    audit = select(pack, envs[:1], policy, mode='audit')
    startup_only = {**missing, 'update': {'allowed_checks': [check_id]}}
    with pytest.raises(ValueError, match=f'^{scope_error}$'):
        run_plan(store, pack, audit, application.run_config(startup_only))
    with pytest.raises(ValueError, match='^INSTALLED_PLUGIN_INVENTORY_EXECUTION_SCOPE_INVALID$'):
        run_plan(store, pack, audit, application.run_config(config))


def test_req20_capture_scope_rejects_native_siblings_before_target_preparation(tmp_path, sources, monkeypatch):
    from guard.core.run import run_plan
    from guard.executors.engine_sensor import EngineSensorExecutor

    pack, sides, policy_path = sources
    policy = tool.Policy.model_validate_json(policy_path.read_bytes())
    envs = [tool.Environment.model_validate_json(paths['environment'].read_bytes()) for paths in sides.values()]
    marker = 'normal_graph_capture_only'
    scope_error = '^NORMAL_GRAPH_CAPTURE_EXECUTION_SCOPE_INVALID$'
    config = {'executors': {'engine_sensor': 'kubernetes'}, 'update': {'allowed_checks': ['REQ-20']},
              'sides': {side: {'engine_sensor': {'target': str(paths['target'])}} for side, paths in sides.items()},
              'check_config': {'REQ-20': {marker: True}}}
    legacy = registry.build_executor('engine_sensor', 'kubernetes', {'engine_sensor': {}},
        engine=envs[0].engine.name, version=envs[0].engine.version)
    assert isinstance(legacy, EngineSensorExecutor)
    half, wrong, disabled, native_inputs = (deepcopy(config) for _ in range(4))
    half['sides']['baseline']['check_config'] = half.pop('check_config')
    wrong['check_config']['REQ-19'] = wrong['check_config'].pop('REQ-20')
    disabled['sides']['candidate']['check_config'] = {'REQ-20': {marker: False}}
    native_inputs['check_config']['REQ-20']['prepared'] = str(tmp_path / 'prepared.json')
    sibling = {**config, 'update': {'allowed_checks': ['REQ-19', 'REQ-20']}}
    wrapped = {**config, 'record_attestation': {'mode': 'example'}}
    managed = {**config, 'update': {**config['update'], 'mode': 'managed'}}
    binding = {**config, 'run_binding': {'REQ-20': {}}}

    def forbid_prepare(*_args, **_kwargs):
        raise AssertionError('Invalid graph capture scope reached target preparation')

    monkeypatch.setattr(application, 'update_inputs', forbid_prepare)
    store = Store(tmp_path / 'scope-store')
    for invalid in (half, wrong, disabled, native_inputs, sibling, wrapped, managed, binding):
        with pytest.raises(ValueError, match=scope_error):
            application.execute_update(store, pack, envs, policy, invalid, tmp_path / 'run', utcnow())
    with pytest.raises(ValueError, match=scope_error):
        run_plan(store, pack, select(pack, envs[:1], policy, mode='audit'), application.run_config(config))


def test_obs01_preparation_preserves_side_run_bindings_and_holds_plt12(tmp_path, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'OBS-01': {'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
                         'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256'],
                         'observation_max_delay_ms': 5000}}
    pol = original.model_copy(update={'values': values,
        'scope': original.scope.model_copy(update={'include': ['OBS-01', 'PLT-12']})}).seal()
    _write(policy_path, pol.to_dict())
    obs_bindings = {}
    for number, side in enumerate(sides, 1):
        producer = tmp_path / f'{side}-approved-producer.py'
        producer.write_text(f'# producer fixture for {side}\n')
        obs_bindings[side] = {'record_session': f'{number:032x}', 'record_producer': str(producer),
                              'test_actors': {'allowed': 'a' * 64, 'denied': 'b' * 64}}
    cfg, _, decisions = tool.assemble(
        sides, policy_path, pack=pack, store=tmp_path / 'store', observation_run_bindings=obs_bindings)
    assert cfg['update']['allowed_checks'] == ['OBS-01']
    assert cfg['check_config'] == {'OBS-01': {'record_snapshot_only': True}}
    assert cfg['executors'] == {'required_event_record': 'kubernetes'}
    assert decisions['OBS-01']['shared_executor_checks']['PLT-12']['baseline']['expected_if_invoked'] == 'NOT_RUN'
    for side in sides:
        assert cfg['sides'][side]['run_binding'] == {'OBS-01': obs_bindings[side]}
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    ready = application.update_readiness(pack, envs, pol, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready
    bad_scope = deepcopy(cfg)
    bad_scope['update']['allowed_checks'].append('PLT-12')
    invalid = application.update_readiness(pack, envs, pol, bad_scope)
    assert next(row for row in invalid['checks'] if row['check'] == 'inputs')['detail'] == (
        'OBSERVATION_RECORD_SNAPSHOT_SCOPE_INVALID')
    fresh = tmp_path / 'fresh-record-target.json'
    staged = tool.staged_config(cfg, fresh)
    assert staged['sides']['candidate']['run_binding'] == cfg['sides']['candidate']['run_binding']
    assert staged['sides']['candidate']['required_event_record']['target'] == str(fresh)
    _, pointer = tool._source(sides['candidate']['target'])
    target = json.loads(sides['candidate']['target'].read_bytes())
    for number, role in enumerate(('engine', 'probe'), 100):
        target['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        target['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, target)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == {
        'required_event_record': sha256_hex(canonical_json(target))}
    recipe = configuration_sha256(staged, envs)
    Path(obs_bindings['candidate']['record_producer']).write_text('# changed producer bytes\n')
    assert configuration_sha256(staged, envs) != recipe


def test_copy_and_observation_share_executor_with_separate_pinned_record_inputs(tmp_path, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    env_by_side = {side: tool.Environment.model_validate_json(settings['environment'].read_bytes())
                   for side, settings in sides.items()}
    base = original.model_copy(update={
        'values': {'OBS-01': {'observation_required_events': ['ACCESS_ALLOWED', 'ACCESS_DENIED'],
                             'observation_required_fields': ['actor_sha256', 'target_sha256', 'run_sha256', 'policy_sha256'],
                             'observation_max_delay_ms': 5000}, 'PLT-12': {'copy_max_record_delay_ms': 5000}},
        'source_document_sha256': sha256_hex(AMENDMENT.read_bytes()),
        'scope': original.scope.model_copy(update={'include': ['OBS-01', 'PLT-12']}),
    }).seal()
    policy, inventory = copy_inputs.materialize(base, pack, env_by_side, {
        'schema': copy_inputs.SELECTION_SCHEMA, 'base_policy_ref': base.ref().model_dump(mode='json'),
        'environments': {side: env.ref().model_dump(mode='json') for side, env in env_by_side.items()},
        'started_at': '2026-11-19T03:00:00.125+09:00', 'credential_family_id': '1' * 32,
        'copies': [{'copy_id': '2' * 32, 'generation': 1, 'location_id': '3' * 32,
                    'location_kind': 'FILE', 'recipient_id': '4' * 32}],
    })
    _write(policy_path, policy.to_dict())
    observation, copies = {}, {}
    for number, side in enumerate(sides, 1):
        obs_source = tmp_path / f'{side}-observation.py'
        obs_source.write_text('# observation fixture\n')
        copy_source = tmp_path / f'{side}-copy.py'
        copy_source.write_text('# copy fixture\n')
        observation[side] = {'record_session': f'{number:032x}', 'record_producer': str(obs_source),
                             'test_actors': {'allowed': 'a' * 64, 'denied': 'b' * 64}}
        copies[side] = {'record_session': f'{number + 10:032x}', 'record_producer': str(copy_source),
                        'copy_inventory': str(_write(tmp_path / f'{side}-copy-inventory.json', inventory))}
    cfg, _, decisions = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store',
                                      observation_run_bindings=observation, copy_record_inputs=copies)
    assert cfg['update']['allowed_checks'] == ['OBS-01', 'PLT-12']
    assert cfg['executors'] == {'required_event_record': 'kubernetes'}
    assert all(decisions[cid]['configuration_enabled'] for cid in ('OBS-01', 'PLT-12'))
    for side in sides:
        settings = merge_config(cfg, cfg['sides'][side])
        copy_cfg = for_check(settings, 'required_event_record', 'PLT-12')['required_event_record']
        audit_cfg = for_check(settings, 'required_event_record', 'OBS-01')['required_event_record']
        assert copy_cfg == {'target': str(sides[side]['target']), 'copy_record_snapshot_only': True,
                            'copy_inventory': copies[side]['copy_inventory']}
        assert audit_cfg == {'target': str(sides[side]['target']), 'record_snapshot_only': True}
        assert settings['run_binding']['OBS-01'] == observation[side]
        assert settings['run_binding']['PLT-12'] == {key: copies[side][key] for key in ('record_session', 'record_producer')}
    envs = list(env_by_side.values())
    ready = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['ok'], ready

    overlap = deepcopy(cfg)
    row = overlap['sides']['candidate']['run_binding']
    row['PLT-12']['record_session'] = row['OBS-01']['record_session']
    blocked = application.update_readiness(pack, envs, policy, overlap)
    assert next(row for row in blocked['checks'] if row['check'] == 'inputs')['detail'] == (
        'OBSERVATION_RECORD_SNAPSHOT_SESSIONS_MUST_DIFFER')

    fresh = tmp_path / 'fresh-dual-record-target.json'
    staged = tool.staged_config(cfg, fresh)
    assert staged['sides']['candidate']['run_binding'] == cfg['sides']['candidate']['run_binding']
    assert staged['sides']['candidate']['check_config'] == cfg['sides']['candidate']['check_config']
    _, pointer = tool._source(sides['candidate']['target'])
    target = json.loads(sides['candidate']['target'].read_bytes())
    for number, role in enumerate(('engine', 'probe'), 100):
        target['kubernetes'][role]['pod_uid'] = f'00000000-0000-0000-0000-{number:012d}'
        target['kubernetes'][role]['container_id'] = f'containerd://{number:064x}'
    _write(fresh, target)
    assert tool.revalidate_candidate(staged, envs[1], pointer) == {
        'required_event_record': sha256_hex(canonical_json(target))}

    recipe = configuration_sha256(staged, envs)
    _write(Path(copies['candidate']['copy_inventory']), {**inventory, 'policy_sha256': '0' * 64})
    assert configuration_sha256(staged, envs) != recipe
    invalid = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in invalid['checks'] if row['check'] == 'inputs')['detail'] == 'COPY_CONFIGURATION_INVALID'


def test_obs11_canonical_prepare_uses_exact_side_sessions_without_default_activation(sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    approved = json.loads((tool.readiness.ROOT / 'docs/dev-1/mvp-enterprise-policy-20261116.json').read_bytes())
    values = {row['key']: row['value'] for row in approved['decisions'] if row['check_id'] == 'OBS-11'}
    policy = original.model_copy(update={'values': {'OBS-11': values},
        'scope': original.scope.model_copy(update={'include': ['OBS-11']})}).seal()
    cfg = {'update': {'mode': 'external', 'allowed_checks': ['OBS-11']},
           'adapter': {'name': 'vllm'}, 'executors': {'log_integrity': 'kubernetes'},
           'sides': {side: {'log_integrity': {'target': str(entry['target'])},
                            'run_binding': {'OBS-11': {'record_session': f'{number:032x}'}}}
                     for number, (side, entry) in enumerate(sides.items(), 1)}}
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    assert tool.run_binding.config_keys(pack.checks['OBS-11']) == {'record_session'}
    readiness = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in readiness['checks'] if row['check'] == 'inputs')['ok'], readiness
    assert not any(row['runtime_qualified'] for row in readiness['check_support'])
    cfg['sides']['candidate']['run_binding'] = {}
    blocked = application.update_readiness(pack, envs, policy, cfg)
    assert next(row for row in blocked['checks'] if row['check'] == 'inputs')['detail'] == 'RUN_BINDING_CONFIG_INVALID'


def test_shared_ipc_checks_keep_distinct_recipe_pinned_targets(tmp_path, monkeypatch, sources):
    pack, sides, policy_path = sources
    original = tool.Policy.model_validate_json(policy_path.read_bytes())
    values = {'PLT-02': {'protected_unix_paths': ['/run/fixture/engine.sock'], 'allowed_ipc_uids': ['1000']}}
    approved = json.loads((tool.readiness.ROOT / 'docs/dev-1/mvp-enterprise-policy-20261116.json').read_bytes())
    values['PLT-06'] = {row['key']: row['value'] for row in approved['decisions'] if row['check_id'] == 'PLT-06'}
    pol = original.model_copy(update={'values': values,
        'scope': original.scope.model_copy(update={'include': ['PLT-02', 'PLT-06']})}).seal()
    _write(policy_path, pol.to_dict())
    cfg, _, _ = tool.assemble(sides, policy_path, pack=pack, store=tmp_path / 'store', protected_ipc_metadata=True)
    assert set(cfg['update']['allowed_checks']) == {'PLT-02', 'PLT-06'}
    envs = [tool.Environment.model_validate_json(entry['environment'].read_bytes()) for entry in sides.values()]
    expected = {}
    for side, env in zip(sides, envs):
        target = json.loads(sides[side]['target'].read_bytes())
        expected['PLT-06', env.id] = sha256_hex(canonical_json(target))
        target['kubernetes']['engine']['pod_uid'] = '00000000-0000-0000-0000-000000000157'
        alternate = _write(tmp_path / f'{side}-ipc-target.json', target)
        expected['PLT-02', env.id] = sha256_hex(canonical_json(target))
        cfg['sides'][side]['check_config'] = {'PLT-02': {'target': str(alternate)}}

    class Example:
        name, version = 'unix_ipc', '1'

        def __init__(self, raw):
            self.target_hash = raw['_kubernetes_target_sha256']

        def run(self, check, env, *_args):
            assert self.target_hash == expected.pop((check.id, env.id))
            return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
                                 normal_control={'attempted': False, 'succeeded': None})

    monkeypatch.setitem(registry.EXECUTORS, 'unix_ipc:kubernetes', Example)
    store = Store(tmp_path / 'store')
    application.execute_update(store, pack, envs, pol, cfg, tmp_path / 'update', utcnow())
    assert not expected
    assert all(row.execution.status == ExecStatus.COMPLETED for row in store.iter_kind('EVD'))
