"""Contract tests for the bounded extension, not real vLLM measurements."""
import pytest
from guard.executors.sampling_cpu import fixtures, describe, project, judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check


def test_bounded_profile_covers_four_public_request_classes_and_normalization():
    rows = fixtures()
    assert len(rows) == 18 and len({r['id'] for r in rows}) == 18
    assert {r['endpoint'] for r in rows if r['kind'] == 'stop'} == {'completion', 'chat', 'batch', 'responses'}
    assert all(r.get('value', 0) <= 5 for r in rows)


def test_violation_is_not_erased_by_followup_failure_or_missing_other_cases():
    fixture = next(r for r in fixtures() if r['expected'] == 'rejected')
    obs = project([{**describe(fixture), 'outcome': 'accepted', 'after': {'outcome': 'execution_error'}}])
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.FAIL


@pytest.mark.parametrize('outcome', [None, 'execution_error', 'rejected'])
def test_missing_error_rejection_cannot_produce_full_check_pass(outcome):
    fixture = next(r for r in fixtures() if r['expected'] == 'rejected')
    obs = project([{**describe(fixture), 'outcome': outcome}])
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.UNKNOWN


def test_boundary_or_identity_mismatch_stops_projection():
    row = describe(fixtures()[0])
    for rows in [[row, row], [{**row, 'stage': 'runner'}], [{**row, 'fixture_canonical_sha256': '0'*64}]]:
        with pytest.raises(ValueError): project(rows)
