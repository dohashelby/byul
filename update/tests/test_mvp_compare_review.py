"""Read-only work-order counts from real product object graphs with synthetic evidence."""
from copy import deepcopy
import json

import pytest

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
    assert result['reports'] == [{'verified': False, 'reason': 'EVIDENCE_CONTRACT_INVALID'}]
    assert result['rows']['REQ-01']['status'] == 'ORIGINAL_ROW_MISSING'
    assert not result['unknown_security_limit_met']


def test_config_difference_keeps_real_verdict_and_exact_blocker():
    case = builders.build_pure_update_case(candidate_fail=True, different_config=True)
    result = tool.review([case], pack=case[2], expected=_expected(case))
    row = result['rows']['REQ-01']
    assert row['candidate']['verdicts']['security'] == 'FAIL'
    assert row['comparison'] == 'NOT_COMPARABLE'
    assert row['comparison_reasons'] == ['config_sha256']
    assert row['blocking_codes'] == ['COMPARISON_UNRESOLVED']
    assert 'REQ-01' in result['unresolved_check_ids']


def test_repeated_original_rows_never_choose_a_result_implicitly():
    case = builders.build_pure_update_case()
    result = tool.review([case, case], pack=case[2], expected=_expected(case))
    assert result['rows']['REQ-01']['status'] == 'ORIGINAL_ROW_AMBIGUOUS'
    assert len(result['rows']['REQ-01']['reports']) == 2
    assert not result['comparison_complete']


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
