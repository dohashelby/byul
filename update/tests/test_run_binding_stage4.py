"""RB3-Q3/Q4: exact policy rows and run files, without self-approval or self references."""
from copy import deepcopy
from pathlib import Path
import shutil

import pytest
import yaml

from guard.executors import run_binding as binding
from guard.executors import credential_copy_contract as copies, retry_record_contract as retries
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from tests.builders import environment

ROOT = Path(__file__).resolve().parents[1]
PACK = load_pack(ROOT / 'packs/PACK-BASELINE')
SESSION = '5' * 32


def inputs(tmp_path, check_id):
    target = tmp_path / 'target.json'
    target.write_bytes(b'{ "lab": true }\n')
    producer = tmp_path / 'producer.py'
    producer.write_bytes(b'# EXAMPLE producer\r\n')
    cfg = {'target': str(target)}
    run = {'record_session': SESSION} if check_id in binding._SESSION_KEY else {}
    for key in binding.PRODUCERS_3.get(check_id, {}):
        run[key] = str(producer)
    if check_id == 'OBS-01':
        run['test_actors'] = {'allowed': 'a' * 64, 'denied': 'b' * 64}
    if check_id in binding.RUN_FILE_KEYS:
        path, _ = binding._FILE_OVERRIDES[binding.RUN_FILE_KEYS[check_id]]
        file = tmp_path / (path[0] + '.json')
        file.write_bytes(b'{ "example": true }\n')
        cfg[path[0]] = str(file)
    return cfg, run or None


@pytest.mark.parametrize('check_id', binding.POLICY_DOCUMENT_KEYS)
def test_policy_hash_names_exact_pol_row_and_never_run_file_contents(tmp_path, check_id):
    cfg, run = inputs(tmp_path, check_id)
    row = {'enterprise': {'decision': '고정', 'limit': 7}}
    before = deepcopy(row)
    document, effective = binding.effective_values(PACK.checks[check_id], cfg, environment(), row,
        side='single', attempt_id='stage4-example', issued_at=utcnow(), run_cfg=run)
    key = binding.POLICY_DOCUMENT_KEYS[check_id]
    assert effective[key] == document['values'][key] == sha256_hex(canonical_json(row))
    assert row == before
    with pytest.raises(binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
        binding.effective_values(PACK.checks[check_id], cfg, environment(), {**row, key: 'c' * 64},
            side='single', attempt_id=None, issued_at=utcnow(), run_cfg=run)
    if check_id in binding.RUN_FILE_KEYS:
        file_key = binding.RUN_FILE_KEYS[check_id]
        path, loader = binding._FILE_OVERRIDES[file_key]
        raw = Path(cfg[path[0]]).read_bytes()
        expected = sha256_hex(raw) if loader == 'bytes' else sha256_hex(canonical_json({'example': True}))
        assert document['files'][file_key] == effective[file_key] == expected


def test_obs06_binds_current_sealed_policy_outside_its_own_values(tmp_path):
    from guard.executors.kubernetes_record_access_product import normalize_policy
    from tests.builders import policy
    check = PACK.checks['OBS-06']
    assert not check.policy_params
    cfg, run = inputs(tmp_path, check.id)
    pol = policy(PACK.ref(), values={'OBS-06': {}})
    document, values = binding.effective_values(check, cfg, environment(), {}, side='single',
        attempt_id=None, issued_at=utcnow(), run_cfg=run, policy_ref=pol.ref())
    assert values['record_access_policy_ref'] == pol.ref().model_dump(mode='json')
    assert document['values']['record_access_policy_ref'] == values['record_access_policy_ref']
    normalize_policy(values)
    with pytest.raises(binding.RunBindingUnavailable, match='POLICY_REF_UNAVAILABLE'):
        binding.effective_values(check, cfg, environment(), {}, side='single', attempt_id=None,
            issued_at=utcnow(), run_cfg=run)
    with pytest.raises(binding.RunBindingUnavailable, match='BELONGS_TO_EXECUTION'):
        binding.effective_values(check, cfg, environment(), {'record_access_policy_ref': values['record_access_policy_ref']},
            side='single', attempt_id=None, issued_at=utcnow(), run_cfg=run, policy_ref=pol.ref())


def test_stage4_migration_reproduces_exact_pack38_checkdefs_and_old_pack_replays(tmp_path):
    # Later input-matrix definitions are not a stage-4 run-binding migration.
    # Preserve the exact original full comparison against published @38.
    expected_path=tmp_path/'published-pack38';shutil.copytree(PACK.path,expected_path)
    published=ROOT/'tests/fixtures/input-matrix-pack38'
    for path in published.rglob('*.yaml'):
        shutil.copyfile(path,expected_path/path.relative_to(published))
    expected=load_pack(expected_path)
    assert PACK.manifest.revision==39 and expected.ref().sha256=='22dee30a0df2e9e79defd6d28010f3d16e202512d67269e831d3c5fa95113d80'
    historical = tmp_path / 'pack37'
    shutil.copytree(expected.path, historical)
    fixture = ROOT / 'tests/fixtures/run-binding-pack37'
    for path in fixture.rglob('*.yaml'):
        shutil.copyfile(path, historical / path.relative_to(fixture))
    old = load_pack(historical)
    assert old.manifest.revision == 37
    for cid in binding.KEYS_BY_CHECK_4:
        assert 4 not in binding.stages_for(old.checks[cid])
    migrated = binding.migrate_pack(historical, tmp_path / 'pack38', stage=4)
    assert migrated.manifest.revision == expected.manifest.revision == 38
    for cid, check in expected.checks.items():
        assert migrated.checks[cid].model_dump(mode='json') == check.model_dump(mode='json')
    assert migrated.manifest.review.status.value == 'DRAFT'
    assert old.manifest.revision == load_pack(historical).manifest.revision
    cfg, run = inputs(tmp_path, 'REQ-04')
    _, values = binding.effective_values(old.checks['REQ-04'], cfg, environment(),
        {'forwarded_identity_policy_sha256': 'c' * 64}, side='single', attempt_id=None,
        issued_at=utcnow(), run_cfg=run)
    assert values['forwarded_identity_policy_sha256'] == 'c' * 64


@pytest.mark.parametrize('field,value', [('max_attempts', True), ('max_attempts', 0), ('extra', 1)])
def test_malformed_retry_decisions_are_rejected_during_policy_validation(field, value):
    from tests.test_a1_retry_record_contract import example
    scope, values, _, _ = example()
    values['retry_limits'] = {**scope['limits'], field: value}
    with pytest.raises(ValueError, match='RETRY_POLICY_INVALID'):
        retries.normalize_policy(values)


def test_copy_decisions_have_the_inventory_closed_shape_and_do_not_accept_secrets():
    from tests.test_a1_credential_copy_contract import example
    inventory, values, _, _ = example()
    values['copy_treatments'] = copies.treatments(inventory)
    copies.normalize_policy(values)
    decision = next(iter(values['copy_treatments'].values()))
    decision['credential'] = 'EXAMPLE forbidden field'
    with pytest.raises(ValueError):
        copies.normalize_policy(values)


def test_recovery_policy_cannot_claim_absence_of_unselected_kinds():
    from guard.executors import resource_recovery_contract as recovery
    # Validate the cross-file coverage independently of transport, keeping original policy checks.
    from tests.test_a1_resource_recovery import selection, values as original_values
    chosen = selection()
    assert 'gpu-memory' not in {r['kind'] for r in chosen['resources']}
    # Closed policy validation is exercised by the existing recovery contract suite;
    # this new case changes only the required kind while preserving the original fields.
    values = original_values(chosen)
    values['recovery_require_absent_kinds'] = ['gpu-memory']
    with pytest.raises(ValueError, match='RESOURCE_SELECTED_REQUIRED_KIND_MISSING'):
        recovery.validate_selected_policy(chosen, values)


def test_guard_run_passes_the_plan_policy_ref_to_obs06(tmp_path, monkeypatch):
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.store import Store
    from guard.store.packs import put_pack
    from tests.builders import policy
    from tests.test_run_binding_stage3 import Recording
    cfg, run = inputs(tmp_path, 'OBS-06')
    store = Store(tmp_path / 'store')
    env = environment()
    pol = policy(put_pack(store, PACK.path), scope={'include': ['OBS-06']}, values={'OBS-06': {}})
    store.put_many([env, pol])
    plan = select(PACK, [env], pol)
    store.put(plan)
    seen = []
    monkeypatch.setitem(registry.EXECUTORS, 'record_access:sim', lambda raw: Recording(seen))
    evd, = run_plan(store, PACK, plan, RunConfig(executors={'record_access': 'sim'}, adapter='vllm', raw={
        'record_access': cfg, 'run_binding': {'OBS-06': run}}))
    assert seen[0]['record_access_policy_ref'] == plan.inputs.policy.model_dump(mode='json') == pol.ref().model_dump(mode='json')
    assert evd.execution.run_binding['values']['record_access_policy_ref'] == seen[0]['record_access_policy_ref']
    assert binding.verify(evd.execution.run_binding, evd) == [] and evd.hash_ok()


def test_migration_reports_missing_enterprise_decisions_without_inventing_them(tmp_path, capsys):
    from tools.migrate_policy_run_binding import main
    source = tmp_path / 'old.yaml'
    source.write_text(yaml.safe_dump({'pack': {'id': 'PACK-BASELINE', 'revision': 37}, 'status': 'CONFIRMED',
        'values': {'LIF-12': {'retry_scope_sha256': 'a' * 64, 'retry_policy_sha256': 'b' * 64}}}))
    output = tmp_path / 'new.yaml'
    main([str(source), '--out', str(output), '--stage', '4'])
    result = yaml.safe_load(output.read_text())
    report = yaml.safe_load(capsys.readouterr().out)
    assert result['status'] == 'DRAFT' and result['confirmed_by'] is None and result['values']['LIF-12'] == {}
    assert report['required_policy_values']['LIF-12'] == ['retry_limits']
    assert report['ready_for_execution'] is False
