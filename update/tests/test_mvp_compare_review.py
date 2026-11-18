"""Read-only work-order counts from real product object graphs with synthetic evidence."""
from copy import deepcopy
import json

import pytest

from guard.executors import run_binding
from guard.models.artifacts import model_closure_sha256
from guard.models.objects import object_from_dict
from guard.store.bundle import export_bundle, restore_bundle
from guard.update_acceptance import assess_update
from tests import update_builders as builders
from tests.test_update_acceptance import patch_case
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
    assert {path: path.read_bytes() for path in restored.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('damage', ['missing-side', 'duplicate-row'])
def test_resealed_incomplete_or_duplicate_report_is_refused(damage):
    case = builders.build_pure_update_case()

    def mutate(report):
        row = next(row for row in report['by_check'] if row['check_id'] == 'REQ-01')
        if damage == 'missing-side':
            row['baseline'] = None
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
        assert row['baseline']['blocking_codes'] == ['ORIGINAL_RPT_SIDE_MISSING']
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
    assert row['candidate']['verdicts']['security'] == 'PASS'


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
