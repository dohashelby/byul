"""Read-only work-order counts from real product object graphs with synthetic evidence."""
from copy import deepcopy
import json

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import passive_api_hop, run_binding
from guard.models import DataOrigin, canonical_json, load_pack, sha256_hex, utcnow
from guard.models.artifacts import model_closure_sha256
from guard.models.objects import Execution, object_from_dict
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from guard.update_acceptance import assess_update
from tests import update_builders as builders
from tests import test_passive_api_hop as passive_fixtures
from tests.builders import T0
from tests.test_update_acceptance import patch_case
from tests.test_passive_api_hop import paired_controls
from tools import mvp_compare_review as tool


def _expected(case):
    _, objects, _ = case
    envs = {obj['id']: obj for obj in objects if obj['kind'] == 'ENV'}
    plan, = [obj for obj in objects if obj['kind'] == 'PLAN']
    expected = {}
    for side, ref in zip(tool.SIDES, plan['inputs']['environments'], strict=True):
        engine = envs[ref['id']]['engine']
        expected[side] = {'engine': engine['name'], 'version': engine['version'], 'image': engine['image_digest']}
    return expected


def test_original_unknown_security_is_counted_once_per_id_across_sides_and_replayed_store(tmp_path):
    def missing_control(cid, side, raw):
        if cid == 'REQ-01' or (cid, side) == ('REQ-03', 'baseline'):
            raw['normal_control']['succeeded'] = False

    case = builders.build_update_case(tmp_path, mutate_evidence=missing_control)
    report, _, pack = case
    bundle, restored, rpt_file = (tmp_path / name for name in ('evidence.zip', 'restored', 'report.json'))
    rpt_file.write_text(json.dumps(report), encoding='utf-8')
    export_bundle(tmp_path / 'store', bundle)
    restore_bundle(bundle, restored)
    before = {path: path.read_bytes() for path in restored.rglob('*') if path.is_file()}
    runs = list(tool._read_runs([(restored, rpt_file)]))
    strict = assess_update(*runs[0], profile='plan-scope/2')
    assert strict['evidence_contract_valid'] and not strict['accepted']
    result = tool.review(runs, pack=pack, expected=_expected(case))
    assert result['unknown_security_check_ids'] == ['REQ-01', 'REQ-03']
    assert result['unknown_security_count'] == 2
    assert result['rows']['REQ-01']['security_unknown_sides'] == ['baseline', 'candidate']
    assert result['rows']['REQ-03']['security_unknown_sides'] == ['baseline']
    assert result['rows']['REQ-01']['baseline']['evidence'] == strict['checks']['REQ-01']['baseline']['evidence']
    assert result['rows']['REQ-02']['status'] == 'ORIGINAL_ROW_MISSING'
    assert result['comparison_complete'] is result['unknown_security_limit_met'] is False
    assert not result['paired_execution_complete']
    assert 'REQ-02' in result['execution_coverage']['candidate']['unverified']
    assert 'REQ-02' not in result['execution_coverage']['candidate']['not_run']
    assert {path: path.read_bytes() for path in restored.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('damage', ['missing-side', 'duplicate-row', 'displayed-verdict'])
def test_resealed_incomplete_or_duplicate_report_is_refused(damage):
    case = builders.build_pure_update_case()

    def mutate(report):
        row = next(row for row in report['by_check'] if row['check_id'] == 'REQ-01')
        if damage == 'missing-side':
            row['baseline'] = None
        elif damage == 'displayed-verdict':
            row['baseline']['security'] = 'UNKNOWN'
        else:
            report['by_check'].append(deepcopy(row))

    damaged = patch_case(case, 'RPT', mutate)
    result = tool.review([damaged], pack=case[2], expected=_expected(case))
    row = result['rows']['REQ-01']
    if damage == 'duplicate-row':
        assert result['reports'] == [{'verified': False, 'reason': 'ORIGINAL_CHECK_ROWS_MISMATCH'}]
        assert row['status'] == 'ORIGINAL_ROW_MISSING'
    else:
        assert result['reports'][0]['verified']
        assert row['status'] == 'ORIGINAL_ROW_UNRESOLVED'
        expected_blocker = ('ORIGINAL_RPT_SIDE_MISSING' if damage == 'missing-side'
                            else 'ORIGINAL_RESULT_REPLAY_MISMATCH')
        assert row['baseline']['blocking_codes'] == [expected_blocker]
        assert row['baseline']['reported_security'] is None
        assert 'REQ-01' not in result['unknown_security_check_ids']
        assert row['candidate']['verdicts']['security'] == 'PASS'
    assert not result['unknown_security_limit_met']


def test_executor_specific_record_artifact_survives_export_restore_and_rejudgment(tmp_path):
    def record_only(cid, side, raw):
        if cid == 'PLT-04':
            raw['artifacts'][0]['name'] = 'worker-security-record.json'

    case = builders.build_update_case(tmp_path, mutate_evidence=record_only)
    report, _, pack = case
    rpt_path = tmp_path / 'report.json'
    rpt_path.write_text(json.dumps(report), encoding='utf-8')
    bundle, restored = tmp_path / 'evidence.zip', tmp_path / 'restored'
    export_bundle(tmp_path / 'store', bundle)
    restore_bundle(bundle, restored)
    runs = list(tool._read_runs([(restored, rpt_path)]))
    result = tool.review(runs, pack=pack, expected=_expected(case))
    assert not assess_update(*runs[0], profile='plan-scope/2')['evidence_contract_valid']
    assert result['reports'][0]['verified']
    row = result['rows']['PLT-04']
    assert row['status'] == 'MEASURED_ROWS_VERIFIED'
    assert all(row[side]['verdicts']['security'] == 'PASS' for side in tool.SIDES)


def test_two_original_reports_from_one_restored_store_keep_exact_plan_refs(tmp_path, monkeypatch):
    first = builders.build_update_case(tmp_path)
    original_evidence = builders.Evidence

    def next_run_evidence(**raw):
        raw['id'] += '-SECOND'
        return original_evidence(**raw)

    monkeypatch.setattr(builders, 'Evidence', next_run_evidence)
    second = builders.build_update_case(tmp_path, candidate_fail=True)
    reports = [tmp_path / 'first-report.json', tmp_path / 'second-report.json']
    for path, case in zip(reports, (first, second), strict=True):
        path.write_text(json.dumps(case[0]), encoding='utf-8')
    bundle, restored = tmp_path / 'evidence.zip', tmp_path / 'restored'
    export_bundle(tmp_path / 'store', bundle)
    restore_bundle(bundle, restored)
    runs = list(tool._read_runs((restored, path) for path in reports))
    assert all(len([obj for obj in run[1] if obj['kind'] == 'PLAN']) == 2 for run in runs)
    authoritative = object_from_dict(second[0]).ref().model_dump(mode='json')
    result = tool.review(runs, pack=first[2], expected=_expected(first), assignments={'REQ-01': authoritative})
    assert len({entry['refs']['plan']['sha256'] for entry in result['reports']}) == 2
    assert result['rows']['REQ-01']['candidate']['verdicts']['security'] == 'FAIL'
    assert result['rows']['REQ-01']['report'] == authoritative


def test_config_difference_keeps_real_verdict_and_exact_blocker():
    case = builders.build_pure_update_case(candidate_fail=True, different_config=True)
    result = tool.review([case], pack=case[2], expected=_expected(case))
    row = result['rows']['REQ-01']
    assert row['candidate']['verdicts']['security'] == 'FAIL'
    assert row['comparison'] == 'NOT_COMPARABLE'
    assert row['comparison_reasons'] == ['config_sha256']
    assert row['blocking_codes'] == ['COMPARISON_UNRESOLVED']
    assert 'REQ-01' in result['unresolved_check_ids']


@pytest.mark.parametrize('status', ['NOT_RUN', 'ABORTED'])
def test_unfinished_execution_retains_exact_refs_as_an_unresolved_side(status):
    def unfinished(cid, side, raw):
        if (cid, side) == ('REQ-01', 'baseline'):
            raw['execution']['status'] = status
            raw['data_origin'] = 'DECLARED'

    case = builders.build_pure_update_case(mutate_evidence=unfinished)
    result = tool.review([case], pack=case[2], expected=_expected(case))
    row = result['rows']['REQ-01']
    assert row['status'] == 'ORIGINAL_ROW_UNRESOLVED'
    assert row['baseline']['evidence']['kind'] == 'EVD'
    assert row['baseline']['result']['kind'] == 'RES'
    assert 'EXECUTION_' + status in row['baseline']['blocking_codes']
    assert row['baseline']['verdicts'] is None
    assert row['baseline']['reported_security'] == 'UNKNOWN'
    assert row['security_unknown_sides'] == ['baseline']
    assert result['unknown_security_check_ids'] == ['REQ-01']
    assert row['candidate']['verdicts']['security'] == 'PASS'
    assert row['baseline']['execution_status'] == status
    assert result['execution_coverage']['baseline'][status.lower()] == ['REQ-01']
    assert 'REQ-01' in result['execution_coverage']['candidate']['verified_measured']
    assert not result['paired_execution_complete']


def test_repeated_original_rows_never_choose_a_result_implicitly():
    case = builders.build_pure_update_case()
    result = tool.review([case, case], pack=case[2], expected=_expected(case))
    assert result['rows']['REQ-01']['status'] == 'ORIGINAL_ROW_AMBIGUOUS'
    assert len(result['rows']['REQ-01']['reports']) == 2
    assert not result['comparison_complete']


def test_explicit_exact_original_report_ref_resolves_overlapping_plans():
    def unfinished(cid, side, raw):
        if (cid, side) == ('REQ-01', 'candidate'):
            raw['execution']['status'] = 'NOT_RUN'
            raw['data_origin'] = 'DECLARED'

    complete = builders.build_pure_update_case()
    partial = builders.build_pure_update_case(mutate_evidence=unfinished)
    pack, expected = complete[2], _expected(complete)
    complete_ref = object_from_dict(complete[0]).ref().model_dump(mode='json')
    partial_ref = object_from_dict(partial[0]).ref().model_dump(mode='json')
    cases = [partial, complete]
    ambiguous = tool.review(cases, pack=pack, expected=expected)
    assert ambiguous['rows']['REQ-01']['status'] == 'ORIGINAL_ROW_AMBIGUOUS'
    selected = tool.review(cases, pack=pack, expected=expected, assignments={'REQ-01': complete_ref})
    assert selected['assigned_reports'] == {'REQ-01': complete_ref}
    assert selected['policy_ref'] is not None
    assert selected['rows']['REQ-01']['report'] == complete_ref
    assert selected['rows']['REQ-01']['status'] == 'MEASURED_ROWS_VERIFIED'
    assert {entry['refs']['report']['sha256'] for entry in selected['reports']} == {
        partial_ref['sha256'], complete_ref['sha256']}
    incomplete = tool.review(cases, pack=pack, expected=expected, assignments={'REQ-01': partial_ref})
    assert incomplete['rows']['REQ-01']['candidate']['blocking_codes'][0] == 'EXECUTION_NOT_RUN'
    wrong = {**complete_ref, 'sha256': '0' * 64}
    missing = tool.review(cases, pack=pack, expected=expected, assignments={'REQ-01': wrong})
    assert missing['rows']['REQ-01']['status'] == 'ASSIGNED_ORIGINAL_ROW_UNAVAILABLE'


def test_product_model_closure_hash_includes_the_tokenizer_identity():
    case = builders.build_pure_update_case()
    modified = patch_case(case, 'ENV', lambda env: env['model'].update(tokenizer_revision='SYNTHETIC-tokenizer'))
    env = next(object_from_dict(obj) for obj in modified[1] if obj['kind'] == 'ENV')
    closure = model_closure_sha256(env.model)
    modified = patch_case(modified, 'EVD', lambda evd: evd['comparability'].update(model_inventory_sha256=closure))
    result = tool.review([modified], pack=case[2], expected=_expected(case))
    assert result['rows']['REQ-01']['status'] == 'MEASURED_ROWS_VERIFIED'


def test_recorded_run_binding_must_match_the_original_evidence_subject():
    def wrong_binding(cid, side, raw):
        if (cid, side) != ('REQ-01', 'baseline'):
            return
        raw['execution']['run_binding'] = {
            'schema': run_binding.SCHEMA, 'attempt_id': None, 'check_id': 'REQ-03', 'side': side,
            'environment_sha256': raw['environment'].sha256,
        }

    case = builders.build_pure_update_case(mutate_evidence=wrong_binding)
    reviewed = tool.review([case], pack=case[2], expected=_expected(case))
    baseline = reviewed['rows']['REQ-01']['baseline']
    assert baseline['blocking_codes'] == ['RUN_BINDING_SUBJECT_MISMATCH']
    assert baseline['verdicts'] is None


def test_not_applicable_requires_two_original_environment_conditions(monkeypatch):
    monkeypatch.setattr(builders, 'CHECKS', ('PLT-01', 'PLT-04', 'REQ-09'))
    original_env, original_policy = builders.environment, builders.policy

    def environment_with_absence(**kwargs):
        kwargs['facts']['gateway'].update(value=False, absence_evidence='Synthetic absence proof')
        return original_env(**kwargs)

    def policy_including_conditional_check(*args, **kwargs):
        kwargs['scope']['include'].append('REQ-03')
        return original_policy(*args, **kwargs)

    monkeypatch.setattr(builders, 'environment', environment_with_absence)
    monkeypatch.setattr(builders, 'policy', policy_including_conditional_check)
    case = builders.build_pure_update_case()
    result = tool.review([case], pack=case[2], expected=_expected(case))
    row = result['rows']['REQ-03']
    assert row['status'] == 'SELECTION_VERIFIED'
    for side in tool.SIDES:
        assert row[side]['conditions'] == [{'fact': 'gateway', 'origin': 'DECLARED', 'result': 'FALSE'}]
    assert result['rows']['REQ-01']['status'] == 'ORIGINAL_ROW_MISSING'

    wrong = patch_case(case, 'ENV', lambda env: env['facts']['gateway'].update(value=True),
                       choose=lambda env: env['id'].endswith('CANDIDATE'))
    result = tool.review([wrong], pack=case[2], expected=_expected(case))
    assert result['rows']['REQ-03']['status'] == 'ORIGINAL_ROW_MISSING'


@pytest.mark.parametrize('damage', [None, 'different-rpt-original', 'changed-digest', 'changed-trial', 'malformed-ref', 'changed-files'])
def test_passive_hop_requires_original_same_report_source_and_attachment(tmp_path, monkeypatch, damage):
    originals = paired_controls(tmp_path, monkeypatch, review_fixture=True)
    pack_path = tool.ROOT / 'packs/PACK-BASELINE'
    pack = load_pack(pack_path)
    for side, (store, source, scope, _) in originals.items():
        plan = store.resolve(scope['plan_ref'])
        prepared = next(evd for evd in store.iter_kind('EVD') if evd.check_id == 'PLT-08' and evd.side == side)
        draft = passive_api_hop.draft_passive_api_hop(store, **scope)
        trial, = draft.observations['api_transport_trials'].value
        values = {key: trial[key] for key in ('profile', 'source_evidence', 'source_artifact_sha256', 'target_sha256')}
        if side == 'baseline':
            if damage == 'different-rpt-original':
                store.put(source.model_copy(update={'revision': source.revision + 1}).seal())
            elif damage == 'changed-digest':
                values['source_artifact_sha256'] = '0' * 64
                trial['source_artifact_sha256'] = values['source_artifact_sha256']
            elif damage == 'changed-trial':
                trial['hop_policy_adjudicated'] = True
            elif damage == 'malformed-ref':
                values['source_evidence'] = {'kind': 'EVD', 'id': source.id, 'revision': []}
        files = {key: values[key] for key in ('target_sha256', 'source_artifact_sha256')}
        if side == 'baseline' and damage == 'changed-files':
            files['target_sha256'] = '0' * 64
        binding = {'schema': run_binding.SCHEMA_2, 'check_id': 'PLT-08', 'side': side.value,
                   'attempt_id': scope['attempt_id'], 'environment_sha256': source.environment.sha256,
                   'files': files, 'validity': None, 'issued_at': draft.started_at.isoformat(), 'values': values}
        execution = Execution(status=draft.status, executor={'name': 'api_transport', 'version': '0.1.0+passive-api-hop',
                              'config_sha256': sha256_hex(canonical_json(draft.config))}, adapter=source.execution.adapter,
                              started_at=draft.started_at, finished_at=draft.finished_at,
                              attempt_id=scope['attempt_id'], run_binding=binding)
        store.put(prepared.model_copy(update={'revision': prepared.revision + 1, 'refs': [*prepared.refs, source.ref()],
            'data_origin': draft.data_origin, 'execution': execution, 'normal_control': draft.normal_control,
            'observations': draft.observations, 'comparability': draft.comparability}).seal())

    put_pack(store, pack_path)
    as_of = utcnow()
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    store.put(report)
    report_path = tmp_path / 'report.json'
    report_path.write_bytes(canonical_json(report.to_dict()))
    bundle, restored = tmp_path / 'evidence.zip', tmp_path / 'restored'
    export_bundle(store.root, bundle)
    restore_bundle(bundle, restored)
    runs = list(tool._read_runs([(restored, report_path)], include_artifact_store=True))
    result = tool.review(runs, pack=pack, expected=_expected(runs[0][:3]))
    if damage == 'malformed-ref':
        assert result['reports'] == [{'verified': False, 'reason': 'ORIGINAL_EVIDENCE_BINDING_MISMATCH'}]
        assert result['rows']['PLT-08']['status'] == 'ORIGINAL_ROW_MISSING'
        return
    assert result['reports'][0]['verified']
    row = result['rows']['PLT-08']
    assert row['candidate']['verdicts']['security'] == 'UNKNOWN'
    assert (row['baseline']['verdicts'] or {}).get('security') == ('UNKNOWN' if damage is None else None)
    assert ('PASSIVE_API_HOP_PROVENANCE_INVALID' in row['baseline']['blocking_codes']) is (damage is not None)
    if damage is None:
        assert row['status'] == 'MEASURED_ROWS_VERIFIED'
        assert all(not row[side]['blocking_codes'] for side in tool.SIDES)
    assert not assess_update(*runs[0][:3], profile='plan-scope/2')['evidence_contract_valid']


@pytest.mark.parametrize('damage,code', [
    (None, None),
    ('raw-pin', 'PLT08_ARCHIVED_TARGET_FILE_CHANGED'),
    ('foreign-target', 'PASSIVE_API_HOP_TARGET_UNVERIFIED'),
])
def test_archived_plt01_targets_replay_without_a_completed_plt08_row(tmp_path, monkeypatch, damage, code):
    build_policy = passive_fixtures.policy

    def confirmed_policy(*args, **kwargs):
        return build_policy(*args, **kwargs, data_origin=DataOrigin.DECLARED, status='CONFIRMED',
                            confirmed_by='EXAMPLE owner', confirmed_at=T0)

    monkeypatch.setattr(passive_fixtures, 'policy', confirmed_policy)
    originals = paired_controls(tmp_path, monkeypatch, review_fixture=True)
    pack_path = tool.ROOT / 'packs/PACK-BASELINE'
    pack = load_pack(pack_path)
    store, _, scope, _ = next(iter(originals.values()))
    plan = store.resolve(scope['plan_ref'])
    put_pack(store, pack_path)
    as_of = utcnow()
    judge_plan(store, pack, plan, as_of)
    report = build_report(store, pack, plan, as_of)
    store.put(report)
    report_path = tmp_path / 'original-report.json'
    report_path.write_bytes(canonical_json(report.to_dict()))
    runs = list(tool._read_runs([(store.root, report_path)], include_artifact_store=True))
    checked = tool.review(runs, pack=pack, expected=_expected(runs[0][:3]))
    assert checked['reports'][0]['verified'] and checked['reports'][0]['official']
    assert all(checked['rows']['PLT-08'][side]['execution_status'] == 'NOT_RUN' for side in tool.SIDES)

    targets = {}
    for side, (_, source, bound, _) in originals.items():
        target_path = tmp_path / f'{side.value}-pinned-target.json'
        raw = canonical_json(bound['target']) + b'\n'
        target_path.write_bytes(raw)
        targets[side.value] = (target_path, sha256_hex(raw))
        assert sha256_hex(raw) != bound['expected_target_sha256']
        assert checked['rows']['PLT-01'][side.value]['evidence'] == source.ref().model_dump(mode='json')
    if damage == 'raw-pin':
        path, _ = targets['candidate']
        targets['candidate'] = (path, '0' * 64)
    elif damage == 'foreign-target':
        targets['candidate'] = targets['baseline']
    if code:
        with pytest.raises(ValueError, match=f'^{code}$'):
            tool._replay_archived_plt08(checked, runs[0][3], targets)
        return

    original_db = sha256_hex(store.db_path.read_bytes())
    snapshot = deepcopy(checked)
    receipt = tool._replay_archived_plt08(checked, runs[0][3], targets)
    assert checked == snapshot and sha256_hex(store.db_path.read_bytes()) == original_db
    assert receipt['report_ref'] == report.ref().model_dump(mode='json')
    assert receipt['plan_ref'] == plan.ref().model_dump(mode='json')
    assert receipt['attempt_id'] == scope['attempt_id']
    for side, (_, source, bound, _) in originals.items():
        assert receipt['sides'][side.value] == {
            'environment_ref': source.environment.model_dump(mode='json'),
            'source_evidence': source.ref().model_dump(mode='json'),
            'source_artifact_sha256': source.artifacts[0].sha256,
            'target_file_sha256': targets[side.value][1],
            'target_sha256': bound['expected_target_sha256'],
            'observations': passive_api_hop.read_passive_api_hop(runs[0][3], **bound),
        }
