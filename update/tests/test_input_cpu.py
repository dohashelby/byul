"""Fixture/projection contracts; actual CPU validation is recorded separately."""
import json
import pytest
from guard.executors.input_cpu import fixtures, describe, project, judge_params
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check


def test_scoped_fixtures_do_not_invent_shape_policy():
    rows = fixtures()
    assert len(rows) == 26 and len({r['id'] for r in rows}) == 26
    assert {r['check_id'] for r in rows} == {'REQ-07', 'REQ-08', 'REQ-09'}
    assert all(r['expected'] is None for r in rows if r['kind'] == 'shape')
    rows[0]['value'].clear()
    assert fixtures()[0]['value']


def test_descriptions_omit_raw_inputs():
    for row in fixtures():
        value = describe(row)
        assert set(value) == {'case_id', 'check_id', 'stage', 'expected', 'fixture_canonical_sha256'}
        assert len(value['fixture_canonical_sha256']) == 64 and 'hello' not in json.dumps(value)


@pytest.mark.parametrize('check_id', ['REQ-07', 'REQ-08', 'REQ-09'])
def test_violation_survives_other_missing_rows_and_followup_error(check_id):
    fixture = next(r for r in fixtures() if r['check_id'] == check_id and r['expected'] == 'rejected')
    row = {**describe(fixture), 'outcome': 'accepted', 'after': {'outcome': 'execution_error'}}
    obs = project([row])[check_id]
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.FAIL
    for other in {'REQ-07', 'REQ-08', 'REQ-09'} - {check_id}:
        assert not project([row])[other]['input_cpu_confirmed_violations']['collected']


@pytest.mark.parametrize('outcome', ['rejected', 'execution_error', None])
def test_rejection_error_missing_do_not_produce_full_check_pass(outcome):
    fixture = next(r for r in fixtures() if r['expected'] == 'rejected')
    obs = project([{**describe(fixture), 'outcome': outcome}])['REQ-08']
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.UNKNOWN


def test_unknown_shape_rule_is_not_a_confirmed_violation():
    fixture = next(r for r in fixtures() if r['kind'] == 'shape')
    assert project([{**describe(fixture), 'outcome': 'accepted'}])['REQ-09']['input_cpu_confirmed_violations']['value'] == []


@pytest.mark.parametrize('field,value', [('check_id', 'REQ-13'), ('stage', 'model_consumption'), ('fixture_canonical_sha256', '0'*64)])
def test_wrong_attribution_or_boundary_stops_projection(field, value):
    with pytest.raises(ValueError): project([{**describe(fixtures()[0]), field: value}])


def test_duplicate_unknown_rows_stop_projection():
    row = describe(fixtures()[0])
    with pytest.raises(ValueError): project([row, row])
    with pytest.raises(ValueError): project([{**row, 'case_id': 'unknown'}])


def test_observed_truncation_mismatch_is_independent_violation_but_missing_value_is_unknown():
    from guard.executors.input_cpu import digest
    case = next(r for r in fixtures() if r['kind'] == 'truncate')
    row = {**describe(case), 'outcome': 'accepted'}
    assert not project([row])['REQ-08']['input_cpu_confirmed_violations']['value']
    row['admitted_metadata'] = {'token_ids_canonical_sha256': digest([0])}
    assert project([row])['REQ-08']['input_cpu_confirmed_violations']['value'] == [case['id']]
    row['admitted_metadata']['token_ids_canonical_sha256'] = digest(case['expected_admitted_ids'])
    assert not project([row])['REQ-08']['input_cpu_confirmed_violations']['value']
