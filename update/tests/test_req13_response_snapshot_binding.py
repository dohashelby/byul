"""The retained REQ-13 mode is bound to the paired PLAN before dispatch."""

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import socket
import subprocess
import sys
import tomllib

import pytest
from typer.testing import CliRunner

from guard import application
from guard.cli import app as guard_cli
from guard.configuration import RESPONSE_SNAPSHOT_INPUT, RESPONSE_SNAPSHOT_OPTION, RESPONSE_SNAPSHOT_SCOPE_ERROR
from guard.core import execution_recipe, run as core_run
from guard.core.execution_recipe import bind_recipe, configuration_sha256
from guard.core.select import select
from guard.executors import kubernetes_response_snapshot as snapshot
from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
from guard.models import DataOrigin, ExecStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import T0, policy
from tests.test_kubernetes_response_snapshot import VALUES, snapshot_inputs
from tests.test_mvp_gateway_preparation import NOW, gateway_pair, sources  # noqa: F401
from tools import mvp_live_readiness, mvp_metadata_wiring
from tools.j02_run_config import write_config


def _forbidden(*args, **kwargs):
    pytest.fail('The retained normal response must never dispatch an HTTP request, process or legacy caller')


def _paired(tmp_path, gateway_pair, *, valid_from=None, valid_until=None):
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    pol = policy(pack.ref(), scope={'include': ['REQ-13']}, values={'REQ-13': VALUES},
                 valid_from=valid_from, valid_until=valid_until)
    assert (snapshot.OPTION, snapshot.INPUT) == (RESPONSE_SNAPSHOT_OPTION, RESPONSE_SNAPSHOT_INPUT)
    config = {
        'adapter': {'name': 'vllm'},
        'update': {'mode': 'external', 'allowed_checks': ['REQ-13'], 'kubernetes_candidate_binding': 'prebound'},
        'executors': {'response_content': 'kubernetes'},
        'check_config': {'REQ-13': {RESPONSE_SNAPSHOT_OPTION: True}},
        'sides': {},
    }
    envs, expected = [], {}
    for side in ('baseline', 'candidate'):
        env, settings, pins = snapshot_inputs(tmp_path, gateway_pair, side, pol.ref().sha256)
        envs.append(env)
        expected[side] = pins
        config['sides'][side] = {'response_content': {
            'target': settings['target'], RESPONSE_SNAPSHOT_INPUT: settings[RESPONSE_SNAPSHOT_INPUT],
        }}
    return pack, pol, envs, config, expected


def _seal(store, pack, pol, envs, config):
    put_pack(store, pack.path)
    store.put_many([*envs, pol])
    plan = bind_recipe(select(pack, envs, pol, mode='update'), config, envs)
    store.put(plan)
    return plan


def _block_legacy_io(monkeypatch):
    monkeypatch.setattr(socket, 'socket', _forbidden)
    monkeypatch.setattr(subprocess, 'Popen', _forbidden)
    monkeypatch.setattr(KubernetesHTTP, '__call__', _forbidden)
    monkeypatch.setattr(KubernetesResponseContentExecutor, 'prepared_gateway', _forbidden)
    monkeypatch.setattr(KubernetesResponseContentExecutor, '_gateway_transport', _forbidden)
    monkeypatch.setattr(KubernetesResponseContentExecutor, 'keys', _forbidden)
    construct = KubernetesResponseContentExecutor.__init__

    def use_inert_runner(self, cfg, **kwargs):
        construct(self, cfg, runner=_forbidden, **kwargs)

    monkeypatch.setattr(KubernetesResponseContentExecutor, '__init__', use_inert_runner)


def _file(path):
    return {'path': str(path), 'sha256': sha256_hex(path.read_bytes())}


def _index(tmp_path, pack, pol, envs, config):
    index = mvp_live_readiness.template(pack)
    policy_path, config_path = tmp_path / 'policy.json', tmp_path / 'guard.toml'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    write_config(config, config_path)
    pinned_files = {}
    for side, env in zip(('baseline', 'candidate'), envs, strict=True):
        env_path = tmp_path / (side + '-retained-env.json')
        env_path.write_bytes(canonical_json(env.to_dict()))
        settings = config['sides'][side]['response_content']
        files = {'environment': _file(env_path), 'policy': _file(policy_path), 'configuration': _file(config_path),
                 'target': _file(Path(settings['target'])),
                 RESPONSE_SNAPSHOT_INPUT: _file(Path(settings[RESPONSE_SNAPSHOT_INPUT]['path']))}
        index['inputs'].append({'check_id': 'REQ-13', 'side': side, **files})
        pinned_files[side] = files

    index_path = tmp_path / 'index.json'
    index_path.write_bytes(canonical_json(index))
    return index, index_path, pinned_files


@pytest.mark.parametrize('damage', [
    None, 'separate-targets', 'foreign-probe', 'one-side', 'raw-target', 'selection-policy', 'staged', 'late-manifest',
])
def test_metadata_handoff_publishes_exact_prebound_response_pair(tmp_path, gateway_pair, monkeypatch, damage):
    pack = load_pack(Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE')
    separate_targets = damage in {'separate-targets', 'foreign-probe'}
    policy_values = {'REQ-13': VALUES}
    if separate_targets:
        policy_values['SUP-04'] = {'approved_image_digests': [row['image'] for row in mvp_live_readiness.versions().values()]}
    pol = policy(pack.ref(), scope={'include': list(policy_values)}, values=policy_values,
                 status='CONFIRMED', data_origin='DECLARED', confirmed_by='Fixture owner', confirmed_at=T0)
    policy_path, output, store = tmp_path / 'req13-policy.json', tmp_path / 'metadata', tmp_path / 'store'
    policy_path.write_bytes(canonical_json(pol.to_dict()))
    selections = {}
    manifests = {}
    env_paths = {}
    ordinary_targets, response_targets = {}, {}
    args = ['mvp_metadata_wiring', '--policy', str(policy_path), '--output', str(output), '--store', str(store)]
    for side in ('baseline', 'candidate'):
        env, settings, pins = snapshot_inputs(tmp_path, gateway_pair, side, pol.ref().sha256)
        env_path = tmp_path / (side + '-metadata-env.json')
        env_path.write_bytes(canonical_json(env.to_dict()))
        env_paths[side] = env_path
        target = Path(settings['target'])
        response_targets[side] = target
        ordinary = Path(gateway_pair[0][side]['target']) if separate_targets else target
        ordinary_targets[side] = ordinary
        if damage == 'foreign-probe' and side == 'candidate':
            data = json.loads(ordinary.read_bytes())
            data['kubernetes']['probe']['pod_uid'] = '00000000-0000-0000-0000-000000000099'
            ordinary.write_bytes(canonical_json(data))
        manifest = settings[RESPONSE_SNAPSHOT_INPUT]
        manifests[side] = {key: manifest[key] for key in ('path', 'sha256')}
        if damage == 'selection-policy' and side == 'candidate':
            manifest['guard_policy_sha256'] = '0' * 64
        selection_path = target.parent / 'selection.json'
        selection_path.write_bytes(canonical_json(settings))
        selections[side] = {**_file(selection_path), 'target_file_sha256': pins['expected_target_file_sha256']}
        args.extend([f'--{side}-environment', str(env_path), f'--{side}-target', str(ordinary)])
        if damage == 'raw-target' and side == 'candidate':
            target.write_bytes(target.read_bytes() + b'\n')
    _block_legacy_io(monkeypatch)
    selections_path = tmp_path / 'selections.json'
    if damage == 'one-side':
        selections.pop('candidate')
    selections_path.write_bytes(canonical_json(selections))
    args.extend(['--gateway-response-selections', str(selections_path)])
    if damage == 'staged':
        args.extend(['--candidate-target-after-baseline', str(tmp_path / 'staged.json')])
    monkeypatch.setattr(sys, 'argv', args)

    if damage == 'staged':
        with pytest.raises(SystemExit):
            mvp_metadata_wiring.main()
        assert not output.exists()
        return
    if damage in {'one-side', 'raw-target', 'selection-policy', 'foreign-probe'}:
        with pytest.raises(ValueError, match='^GATEWAY_RESPONSE_SELECTIONS_UNVERIFIED$'):
            mvp_metadata_wiring.main()
        assert not output.exists()
        return
    if damage == 'late-manifest':
        audit = mvp_live_readiness.audit

        def change_manifest_then_audit(*args, **kwargs):
            path = Path(manifests['candidate']['path'])
            path.write_bytes(path.read_bytes() + b'\n')
            return audit(*args, **kwargs)

        monkeypatch.setattr(mvp_live_readiness, 'audit', change_manifest_then_audit)
        with pytest.raises(ValueError, match='^GATEWAY_RESPONSE_SELECTIONS_PUBLICATION_UNVERIFIED$'):
            mvp_metadata_wiring.main()
        assert not list(output.iterdir())
        return

    mvp_metadata_wiring.main()
    assert not store.exists()
    config = tomllib.loads((output / 'guard.toml').read_text())
    expected_checks = ['SUP-04', 'REQ-13'] if separate_targets else ['REQ-13']
    assert config['update'] == {'mode': 'external', 'kubernetes_candidate_binding': 'prebound',
                                'allowed_checks': expected_checks}
    assert config['check_config'] == {'REQ-13': {RESPONSE_SNAPSHOT_OPTION: True}}
    executors = {'response_content': 'kubernetes'}
    if separate_targets:
        executors['container_config'] = 'kubernetes'
    assert config['executors'] == executors
    index = json.loads((output / 'input-sources.json').read_bytes())
    assert len(index['inputs']) == 2 * len(expected_checks)
    for row in index['inputs']:
        side = row['side']
        if row['check_id'] == 'SUP-04':
            assert row['target'] == _file(ordinary_targets[side])
            assert config['sides'][side]['container_config']['target'] == str(ordinary_targets[side])
            continue
        assert row['check_id'] == 'REQ-13' and row[RESPONSE_SNAPSHOT_INPUT] == manifests[side]
        assert row['target']['sha256'] == selections[side]['target_file_sha256']
        settings = config['sides'][side]['response_content']
        assert row['target']['path'] == settings['target'] == str(response_targets[side])
        assert row['target']['sha256'] == settings[RESPONSE_SNAPSHOT_INPUT]['target_file_sha256']
    result = json.loads((output / 'readiness.json').read_bytes())
    assert result['metadata_wiring']['REQ-13']['selection_refs'] == selections
    assert result['file_binding_valid_sides'] == 2 * len(expected_checks) and not result['ready_for_execution']
    prepare = ['prepare-update', '--pack', str(pack.path), '--policy', str(policy_path),
               '--baseline', str(env_paths['baseline']), '--candidate', str(env_paths['candidate']),
               '--config', str(output / 'guard.toml')]
    runner = CliRunner()
    prepared = runner.invoke(guard_cli, prepare)
    assert prepared.exit_code == 0, prepared.output
    ready = json.loads(prepared.stdout)
    assert ready['execution_scope']['static_preparation_ready'] and not ready['execution_ready']

    candidate_target = Path(config['sides']['candidate']['response_content']['target'])
    candidate_target.write_bytes(candidate_target.read_bytes() + b'\n')
    changed = runner.invoke(guard_cli, prepare)
    assert changed.exit_code == 1, changed.output
    ready = json.loads(changed.stdout)
    assert not ready['execution_scope']['static_preparation_ready']
    assert next(row for row in ready['checks'] if row['check'] == 'inputs')['detail'] == snapshot.INVALID


def test_prebound_saved_plan_prepares_and_reads_the_exact_paired_gateway_snapshots(tmp_path, gateway_pair, monkeypatch):
    pack, pol, envs, config, expected = _paired(tmp_path, gateway_pair)
    _block_legacy_io(monkeypatch)
    ready = application.update_readiness(pack, envs, pol, config)
    assert ready['execution_scope']['static_preparation_ready'], ready
    assert all(not row['runtime_qualified'] for row in ready['check_support'])

    index, index_path, pinned_files = _index(tmp_path, pack, pol, envs, config)
    audited = mvp_live_readiness.audit(index_path, pack=pack)
    assert audited['sides'] == 138 and audited['file_binding_valid_sides'] == 2
    for row in audited['rows']:
        if row['check_id'] == 'REQ-13':
            assert row['file_binding_valid'] and not row['engine_executed']
            assert row['files'] == pinned_files[row['side']]
    index['inputs'][1].pop(RESPONSE_SNAPSHOT_INPUT)
    index_path.write_bytes(canonical_json(index))
    audited = mvp_live_readiness.audit(index_path, pack=pack)
    assert audited['file_binding_valid_sides'] == 1
    candidate = next(row for row in audited['rows'] if row['check_id'] == 'REQ-13' and row['side'] == 'candidate')
    assert candidate['blocking_codes'] == ['MVP_RESPONSE_SNAPSHOT_REFERENCE_SCOPE_INVALID']

    store = Store(tmp_path / 'store')
    plan = _seal(store, pack, pol, envs, config)
    bindings = {}
    assert configuration_sha256(config, envs, plan=plan, response_snapshot_digests=bindings) == (
        plan.inputs.execution_recipe_sha256)
    assert set(bindings) == set(expected)
    for side, pins in expected.items():
        assert bindings[side] == {
            'target_file_sha256': pins['expected_target_file_sha256'],
            'target_sha256': pins['expected_target_sha256'],
            'manifest_sha256': pins['expected_manifest_sha256'],
            'policy_ref': pol.ref().model_dump(mode='json'),
        }

    evidence, failed = application.execute_saved_update(store, pack, plan, config, tmp_path / 'run')
    assert not failed
    assert {row.side.value for row in evidence} == {'baseline', 'candidate'}
    for row in evidence:
        assert row.check_id == 'REQ-13' and row.execution.status is ExecStatus.COMPLETED
        assert row.data_origin is DataOrigin.EXAMPLE
        assert row.observations['response_coverage_complete'].value is False
        assert not any(trial.attempted for trial in row.challenges)
        assert row.observations['response_cases_tested'].value == ['gateway:normal']
        report, = row.artifacts
        proof = json.loads((store.root / report.path).read_bytes())['snapshot']
        assert proof['guard_policy_sha256'] == plan.inputs.policy.sha256
        assert proof['manifest_sha256'] == expected[row.side.value]['expected_manifest_sha256']

    with monkeypatch.context() as guard:
        guard.setattr(snapshot, 'snapshot_manifest_bytes', _forbidden)
        other = policy(pack.ref(), scope={'include': ['SUP-10']}, values={})
        unselected = select(pack, envs, other, mode='update')
        unrelated = {}
        configuration_sha256(config, envs, plan=unselected, response_snapshot_digests=unrelated)
        assert not unrelated

    manifest = Path(config['sides']['candidate']['response_content'][RESPONSE_SNAPSHOT_INPUT]['path'])
    manifest.write_bytes(manifest.read_bytes() + b'\n')
    monkeypatch.setattr(KubernetesResponseContentExecutor, 'run', _forbidden)
    with pytest.raises(ValueError, match='EXECUTION_RECIPE_CHANGED_RESELECT_REQUIRED'):
        application.execute_saved_update(store, pack, plan, config, tmp_path / 'changed')


@pytest.mark.parametrize('state', ['staged', 'masked-stage', 'partial-option', 'legacy-key', 'wrong-policy', 'late-target'])
def test_retained_gateway_scope_does_not_fall_back_on_invalid_input(tmp_path, gateway_pair, monkeypatch, state):
    pack, pol, envs, config, _ = _paired(tmp_path, gateway_pair)
    _block_legacy_io(monkeypatch)
    if state in {'staged', 'masked-stage'}:
        config['update']['kubernetes_candidate_binding'] = 'after_baseline'
        if state == 'masked-stage':
            for side in config['sides'].values():
                side['update'] = {'kubernetes_candidate_binding': 'prebound'}
    elif state == 'partial-option':
        config['check_config'].clear()
    elif state == 'legacy-key':
        config['sides']['candidate']['response_content']['allowed_key_env'] = 'EXAMPLE_UNUSED'
    elif state == 'wrong-policy':
        config['sides']['candidate']['response_content'][RESPONSE_SNAPSHOT_INPUT]['guard_policy_sha256'] = '0' * 64
    if state in {'staged', 'masked-stage', 'partial-option', 'legacy-key'}:
        ready = application.update_readiness(pack, envs, pol, config)
        assert next(row for row in ready['checks'] if row['check'] == 'inputs')['detail'] == RESPONSE_SNAPSHOT_SCOPE_ERROR
        with pytest.raises(ValueError, match=RESPONSE_SNAPSHOT_SCOPE_ERROR):
            application._validation_inputs(envs[0], deepcopy(config))
        return

    store = Store(tmp_path / 'store')
    plan = _seal(store, pack, pol, envs, config)
    if state == 'late-target':
        path = Path(config['sides']['candidate']['response_content']['target'])
        dispatch = core_run._run_one

        def rewrite_then_dispatch(store, check, item, env, *args, **kwargs):
            if env == envs[1]:
                path.write_bytes(path.read_bytes() + b'\n')
            return dispatch(store, check, item, env, *args, **kwargs)

        monkeypatch.setattr(core_run, '_run_one', rewrite_then_dispatch)
    evds, failed = application.execute_saved_update(store, pack, plan, config, tmp_path / 'run')
    rows = {row.side.value: row for row in evds}
    assert failed and rows['baseline'].execution.status is ExecStatus.COMPLETED
    assert rows['candidate'].execution.status is ExecStatus.ABORTED
    assert not rows['candidate'].artifacts


def test_current_guard_policy_cannot_qualify_a_response_captured_before_it_applied(tmp_path, gateway_pair, monkeypatch):
    pack, pol, envs, config, _ = _paired(
        tmp_path, gateway_pair, valid_from=NOW + timedelta(seconds=2), valid_until=NOW + timedelta(minutes=1),
    )
    _block_legacy_io(monkeypatch)
    checked_at = NOW + timedelta(seconds=10)
    monkeypatch.setattr(execution_recipe, 'utcnow', lambda: checked_at)
    assert pol.valid_from < checked_at < pol.valid_until
    execution_recipe.validate_policy_time(pol)

    ready = application.update_readiness(pack, envs, pol, config)
    assert not ready['execution_scope']['static_preparation_ready'], ready
    errors = {row['check']: row['detail'] for row in ready['checks'] if not row['ok']}
    assert {label: detail for label, detail in errors.items() if label.startswith('inputs:')} == {
        'inputs:baseline/REQ-13': snapshot.INVALID,
        'inputs:candidate/REQ-13': snapshot.INVALID,
    }

    _, index_path, _ = _index(tmp_path, pack, pol, envs, config)
    audited = mvp_live_readiness.audit(index_path, pack=pack, issued_at=checked_at)
    rows = [row for row in audited['rows'] if row['check_id'] == 'REQ-13']
    assert len(rows) == 2 and all(not row['file_binding_valid'] and not row['engine_executed'] for row in rows)
    assert all(row['blocking_codes'] == [snapshot.INVALID] for row in rows)

    store = Store(tmp_path / 'store')
    plan = _seal(store, pack, pol, envs, config)
    evds, failed = application.execute_saved_update(store, pack, plan, config, tmp_path / 'run')
    assert failed and {row.side.value for row in evds} == {'baseline', 'candidate'}
    baseline = next(row for row in evds if row.side.value == 'baseline')
    assert baseline.execution.status is ExecStatus.ABORTED and baseline.execution.abort_reason == 'EXECUTOR_FAILURE'
    assert all(not row.artifacts and not row.observations['response_fields_seen'].collected for row in evds)


@pytest.mark.parametrize("mode", ["probe", "loopback", "mixed"])
def test_response_only_pair_reaches_the_public_update_without_a_journal(tmp_path, gateway_pair, monkeypatch, mode):
    from tests.test_mvp_req13_gateway_inputs import _sources as direct_sources
    from tools import mvp_lifecycle_transition_inputs, mvp_req13_gateway_inputs

    pack = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
    sides, selection_refs, envs, paired = {}, {}, [], {}
    policies = []
    originals = {}
    ordinary_targets = {}
    for side in ("baseline", "candidate"):
        directory = tmp_path / side
        directory.mkdir()
        loopback = mode == "loopback" or mode == "mixed" and side == "baseline"
        env, pol, inputs, body = direct_sources(directory, gateway_pair, pack, monkeypatch, None,
                                                side=side, response_only=True, loopback=loopback)
        retained = directory / "retained"
        selected = mvp_req13_gateway_inputs.prepare(inputs["path"], inputs["sha256"], retained, pack=pack)
        manifest = selected[RESPONSE_SNAPSHOT_INPUT]
        paired[side] = {"target": selected["target"], RESPONSE_SNAPSHOT_INPUT: manifest}
        ordinary = json.loads(Path(selected["target"]).read_bytes())
        ordinary.pop("kubernetes_gateway")
        ordinary_path = directory / "ordinary-target.json"
        ordinary_path.write_bytes(canonical_json(ordinary))
        ordinary_targets[side] = _file(ordinary_path)
        sides[side] = (env, ordinary, None, None, None)
        selection_refs[side] = {**_file(retained / "selection.json"),
                                "target_file_sha256": manifest["target_file_sha256"]}
        policies.append(pol)
        envs.append(env)
        originals[side] = (manifest["sha256"], body)
        assert "journal" not in json.loads(Path(manifest["path"]).read_bytes())
    assert policies[0].ref() == policies[1].ref()

    pol = policies[0]
    _block_legacy_io(monkeypatch)
    monkeypatch.setattr(execution_recipe, "utcnow", lambda: NOW)
    if mode == "mixed":
        with pytest.raises(ValueError, match="^GATEWAY_RESPONSE_SELECTIONS_UNVERIFIED$"):
            mvp_metadata_wiring._gateway_response_settings(selection_refs, sides, pack.checks["REQ-13"], pol)
    else:
        assert mvp_metadata_wiring._gateway_response_settings(
            selection_refs, sides, pack.checks["REQ-13"], pol) == paired
    config = {
        "adapter": {"name": "vllm"},
        "update": {"mode": "external", "allowed_checks": ["REQ-13"], "kubernetes_candidate_binding": "prebound"},
        "executors": {"response_content": "kubernetes"},
        "check_config": {"REQ-13": {RESPONSE_SNAPSHOT_OPTION: True}},
        "sides": {side: {"response_content": settings} for side, settings in paired.items()},
    }
    index, index_path, _ = _index(tmp_path, pack, pol, envs, config)
    declaration = {"index": _file(index_path), "targets": ordinary_targets}
    refs = {side: env.ref().model_dump(mode="json")
            for side, env in zip(mvp_live_readiness.SIDES, envs, strict=True)}
    if mode == "mixed":
        with pytest.raises(ValueError, match="^MVP_TRANSITION_METADATA_INPUT_INVALID$"):
            mvp_lifecycle_transition_inputs._prebound_metadata(declaration, pack, pol, refs, captured=[])
        return
    combined, pinned_rows, _, _ = mvp_lifecycle_transition_inputs._prebound_metadata(
        declaration, pack, pol, refs, captured=[])
    assert combined == config
    assert pinned_rows == [{role: pointer for role, pointer in row.items() if role != "configuration"}
                           for row in index["inputs"]]
    report = mvp_live_readiness.audit(index_path, pack=pack)
    assert report["file_binding_valid_sides"] == 2 and report["actual_results"] is None
    config = combined
    assert application.update_readiness(pack, envs, pol, config)["execution_scope"]["static_preparation_ready"]
    store = Store(tmp_path / "store")
    plan = _seal(store, pack, pol, envs, config)
    evidence, failed = application.execute_saved_update(store, pack, plan, config, tmp_path / "run")
    assert not failed and {row.side.value for row in evidence} == {"baseline", "candidate"}
    for row in evidence:
        assert row.check_id == "REQ-13" and row.execution.status is ExecStatus.COMPLETED
        assert row.data_origin is DataOrigin.EXAMPLE and row.normal_control.succeeded is True
        assert row.observations["response_coverage_complete"].value is False
        assert not any(trial.attempted for trial in row.challenges)
        artifact, = row.artifacts
        raw = (store.root / artifact.path).read_bytes()
        proven = json.loads(raw)["snapshot"]
        manifest_sha, response_body = originals[row.side.value]
        assert proven["profile"] == snapshot.RESPONSE_ONLY_PROFILE and proven["manifest_sha256"] == manifest_sha
        assert proven.get("transport_origin") == (
            snapshot.GATEWAY_LOOPBACK_RESPONSE_ORIGIN if mode == "loopback" else None)
        assert "journal_sha256" not in proven and response_body not in raw
