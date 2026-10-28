"""Contracts for native online-render gate observations; not engine results."""
import json
import pytest
from guard.executors.online_input_cpu import fixtures,describe,project,judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check


def test_online_cases_separate_reuse_template_and_default_mm_guard():
    rows=fixtures();assert len(rows)==16 and len({r['id'] for r in rows})==16
    assert {r['check_id'] for r in rows}=={'REQ-07','REQ-08','SUP-08'}
    assert next(r for r in rows if r['id']=='code-revision-opt-in-text')['expected'] is None


@pytest.mark.parametrize('check_id',['REQ-07','REQ-08','SUP-08'])
def test_verified_boundary_violation_survives_missing_other_cases_and_followup_failure(check_id):
    case=next(r for r in fixtures() if r['check_id']==check_id and r['expected']=='rejected')
    obs=project([{**describe(case),'outcome':'accepted','after':{'outcome':'execution_error'}}])[check_id]
    assert RuleTableJudge().judge(check(),evidence(observations=obs),judge_params(),{}).verdicts['security']==Verdict.FAIL


@pytest.mark.parametrize('outcome',['rejected','execution_error',None])
def test_error_missing_and_rejection_do_not_become_full_check_pass(outcome):
    obs=project([{**describe(fixtures()[1]),'outcome':outcome}])['REQ-08']
    assert RuleTableJudge().judge(check(),evidence(observations=obs),judge_params(),{}).verdicts['security']==Verdict.UNKNOWN


def test_wrong_boundary_or_check_cannot_be_substituted():
    row=describe(fixtures()[0])
    for key,value in [('stage','runner_consumption'),('check_id','REQ-13'),('fixture_canonical_sha256','0'*64)]:
        with pytest.raises(ValueError):project([{**row,key:value}])


def test_fixture_description_does_not_persist_requests_tokens_or_revision():
    for r in fixtures():
        value=json.dumps(describe(r));assert 'hello' not in value and 'b1-local-fixture' not in value
