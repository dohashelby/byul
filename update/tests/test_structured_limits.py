"""EXAMPLE harness contracts; real reference results are recorded separately."""
import json
import sys
import pytest
from guard.executors.structured_limits import fixtures, nested_schema, describe, observe_call, project, judge_params, run_reference
from guard.judges.rule_table import RuleTableJudge
from guard.models import Verdict
from tests.builders import evidence
from tests.test_sampling_validation import check

class Rejected(Exception):
    pass


def test_finite_identities_and_engine_depth_not_enterprise_size_policy():
    rows = fixtures()
    assert len(rows) == 15 and len({r['id'] for r in rows}) == 15
    assert all(describe(r)['input_canonical_bytes'] < 20000 for r in rows)
    assert all(r['expected'] is None for r in rows if r['rule'].endswith('observation_only'))
    assert {r['expected'] for r in rows if r['id'].startswith('depth-65')} == {'rejected'}
    rows[0]['value'].clear()
    assert fixtures()[0]['value']

@pytest.mark.parametrize('value', [True, 0, -1, 202, 1.2])
def test_bad_fixture_depth_stops_before_execution(value):
    with pytest.raises(ValueError): nested_schema(value)


def test_entry_monitor_restores_profile_and_omits_exception_text():
    def call(): raise Rejected('private fixture content')
    row = observe_call(call, Rejected, [('entry', call)])
    assert row['outcome'] == 'rejected' and row['events'] == ['entry']
    assert sys.getprofile() is None and 'private' not in json.dumps(row)


def test_unexpected_error_and_existing_profile_are_not_rejection():
    def fail(): raise RuntimeError('private')
    assert observe_call(fail, Rejected)['outcome'] == 'execution_error'
    old = lambda *args: None
    sys.setprofile(old)
    try:
        assert observe_call(fail, Rejected)['outcome'] == 'execution_error'
        assert sys.getprofile() is old
    finally: sys.setprofile(None)


def test_independent_violation_survives_followup_error_and_missing_cases():
    spec = next(c for c in fixtures() if c['id'] == 'depth-65-dict')
    row = {**describe(spec), 'outcome': 'accepted', 'after': {'outcome': 'execution_error'}}
    obs = project([row])
    result = RuleTableJudge().judge(check(), evidence(check_id='REQ-10', observations=obs), judge_params(), {})
    assert result.verdicts['security'] == Verdict.FAIL
    assert not obs['structured_limits_cases_complete']['value']

@pytest.mark.parametrize('outcome', ['execution_error', 'rejected', None])
def test_error_missing_and_rejection_cannot_produce_security_pass(outcome):
    obs = project([{**describe(fixtures()[4]), 'outcome': outcome}])
    assert not obs['structured_limits_confirmed_violations']['value']
    assert RuleTableJudge().judge(check(), evidence(observations=obs), judge_params(), {}).verdicts['security'] == Verdict.UNKNOWN


def test_duplicate_unknown_and_mismatched_fixtures_stop_projection():
    row = {**describe(fixtures()[0]), 'outcome': 'accepted'}
    for rows in [[row, row], [{**row, 'case_id': 'other'}], [{**row, 'input_canonical_sha256': '0'*64}]]:
        with pytest.raises(ValueError): project(rows)


def test_harness_keeps_boundary_results_and_runs_followups():
    seen = []
    class Params:
        def __init__(self, **kwargs): self.__dict__.update(kwargs)
    class Request:
        @staticmethod
        def model_validate(body):
            seen.append('request')
            raise Rejected()
    def backend(params):
        seen.append('backend')
        assert isinstance(params.structured_outputs, Params)
    rows = run_reference(Request, Params, Params, backend, Rejected)
    assert len(rows) == 15 and all(r['after']['outcome'] == 'accepted' for r in rows)
    assert seen.count('request') == 4 and seen.count('backend') == 26
