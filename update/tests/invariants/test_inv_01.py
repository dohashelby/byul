"""INV-01: UNKNOWN을 PASS로 올리지 않는다. 확인 부족과 위반 확인을 구별한다."""

from guard.models import Verdict
from tests.builders import evidence, req01_obs
from tests.judging import direct, judge, sec


def test_missing_observation_is_unknown_not_pass():
    assert sec(evidence(observations=req01_obs(missing=("gateway_outcomes",)))) == Verdict.UNKNOWN


def test_unexpected_value_is_unknown_not_pass_or_fail():
    # 401/403·무응답도 2xx도 아닌 응답(예: 500)은 확인 부족이다
    res = judge(evidence(observations=req01_obs(direct=direct(completions=500))))
    assert res.verdicts.security == Verdict.UNKNOWN
    assert res.reasons


def test_partial_routes_is_unknown():
    obs = req01_obs(tested=["POST /v1/completions"])
    obs["routes_registered"]["value"] = ["GET /v1/models", "POST /v1/completions", "POST /invocations"]
    assert sec(evidence(observations=obs)) == Verdict.UNKNOWN


def test_unknown_and_fail_are_distinct():
    fail = evidence(observations=req01_obs(direct=direct(completions=200)))
    unknown = evidence(observations=req01_obs(missing=("direct_outcomes",)))
    assert sec(fail) == Verdict.FAIL and sec(unknown) == Verdict.UNKNOWN
    assert judge(unknown).missing_observations == ["direct_outcomes"]
    assert judge(fail).missing_observations == []
