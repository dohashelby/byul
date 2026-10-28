"""Embedding fixture and existing judge contract; real loader runs separately."""
import pytest
from guard.executors.embedding_cpu import fixtures, describe, project, judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check


def test_small_loader_fixtures_cover_actual_dimension_dtype_and_feature_rules():
    rows = fixtures()
    assert len(rows) == 10 and len({r['id'] for r in rows}) == 10
    assert next(r for r in rows if r['id'] == 'floating-cast')['expected'] == 'accepted'
    assert next(r for r in rows if r['id'] == 'hidden-mismatch')['expected'] == 'rejected'
    assert all(max(r['shape']) <= 5 for r in rows)


def test_known_violation_survives_followup_failure_and_missing_other_cases():
    row = next(r for r in fixtures() if r['expected'] == 'rejected')
    obs = project([{**describe(row), 'outcome': 'accepted', 'after': {'outcome': 'execution_error'}}])
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.FAIL


@pytest.mark.parametrize('outcome', [None, 'execution_error', 'rejected'])
def test_no_full_security_pass_for_rejection_or_error(outcome):
    obs = project([{**describe(fixtures()[2]), 'outcome': outcome}])
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.UNKNOWN


def test_mismatched_identity_or_stage_stops_projection():
    row = describe(fixtures()[0])
    for rows in [[row, row], [{**row, 'stage': 'model'}], [{**row, 'fixture_canonical_sha256': '0'*64}]]:
        with pytest.raises(ValueError): project(rows)
