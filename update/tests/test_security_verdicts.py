import pytest

from guard.core.judge import judge_evidence
from guard.models import Result, SecurityFailureTag, SideResult, Verdict
from tests.builders import T0, evidence, policy, req01_obs
from tests.judging import PACK, REQ01


def _binary(evd, check=REQ01):
    return judge_evidence(check, evd, policy(PACK.ref()), T0, binary_security=True)


def _check_with_rules(*, pass_rules, fail_rules=()):
    params = {**REQ01.judge.params, "pass_when_all": list(pass_rules), "fail_when_any": list(fail_rules)}
    return REQ01.model_copy(update={"judge": REQ01.judge.model_copy(update={"params": params})})


def test_binary_pass_requires_the_rule_and_gate_and_keeps_other_axes():
    result = _binary(evidence(observations=req01_obs()))
    assert result.verdicts.security == Verdict.PASS
    assert result.verdicts.functional == Verdict.PASS
    assert result.verdicts.performance == Verdict.UNKNOWN
    assert result.security_verdict_version == "binary-v1"
    assert "security_failure_tags" not in result.to_dict()
    assert Result.model_validate(result.to_dict()).hash_ok()


@pytest.mark.parametrize("normal_outcome, tag", [(False, SecurityFailureTag.CONDITIONS_UNMET),
                                                  (None, SecurityFailureTag.INSUFFICIENT_EVIDENCE)])
def test_failed_and_unrecorded_normal_control_have_distinct_tags(normal_outcome, tag):
    result = _binary(evidence(normal={"attempted": True, "succeeded": normal_outcome}))
    assert result.verdicts.security == Verdict.FAIL
    assert result.security_failure_tags == [tag]


def test_observed_mismatch_and_missing_observation_are_independent_tags():
    check = _check_with_rules(pass_rules=[{"obs": "trusted_origin_reachable", "op": "eq", "value": True},
                                          {"obs": "routes_registered", "op": "nonempty"}])
    result = _binary(evidence(observations=req01_obs(trusted=False, missing=("routes_registered",))), check)
    assert result.verdicts.security == Verdict.FAIL
    assert set(result.security_failure_tags) == {SecurityFailureTag.CONDITIONS_UNMET,
                                                 SecurityFailureTag.INSUFFICIENT_EVIDENCE}
    assert Result.model_validate(result.to_dict()).hash_ok()


def test_confirmed_rule_hit_survives_incomplete_gate_and_undefined_pass_rule():
    check = _check_with_rules(pass_rules=[], fail_rules=[{"obs": "trusted_origin_reachable", "op": "eq", "value": False}])
    result = _binary(evidence(attempted=False, observations=req01_obs(trusted=False)), check)
    assert result.verdicts.security == Verdict.FAIL
    assert set(result.security_failure_tags) == {SecurityFailureTag.CONFIRMED_VIOLATION,
                                                 SecurityFailureTag.INSUFFICIENT_EVIDENCE,
                                                 SecurityFailureTag.NO_PASS_RULE}


def test_missing_check_judge_records_no_pass_rule():
    result = _binary(evidence(), REQ01.model_copy(update={"judge": None}))
    assert result.verdicts.security == Verdict.FAIL
    assert result.security_failure_tags == [SecurityFailureTag.NO_PASS_RULE]


def test_unverified_declaration_keeps_fail_without_confirming_violation():
    executor = {"name": "manual-entry", "version": "1", "config_sha256": "0" * 64, "default_judge": "declared"}
    observations = {**req01_obs(), "declared_verdicts": {"value": {"security": "FAIL"}, "collected": True}}
    evd = evidence(execution={"status": "COMPLETED", "executor": executor, "operator": {"by": "EXAMPLE", "at": T0}},
                   observations=observations)
    result = _binary(evd)
    assert result.verdicts.security == Verdict.FAIL
    assert result.security_failure_tags == [SecurityFailureTag.INSUFFICIENT_EVIDENCE]


def test_models_refuse_binary_fail_without_tags_and_pass_with_tags():
    result = _binary(evidence()).to_dict()
    result["verdicts"]["security"] = "FAIL"
    with pytest.raises(ValueError, match="security FAIL requires unique tags"):
        Result.model_validate(result)
    with pytest.raises(ValueError, match="security failure tags must be unique"):
        SideResult(security=Verdict.PASS, security_failure_tags=[SecurityFailureTag.INSUFFICIENT_EVIDENCE])
