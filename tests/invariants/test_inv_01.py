"""INV-01: UNKNOWN을 PASS로 올리지 않는다. 확인 부족과 위반 확인을 구별한다."""

from guard.models import Verdict
from tests.builders import evidence
from tests.judging import OK, judge, sec


def test_missing_observation_is_unknown_not_pass():
    evd = evidence(observations={**OK, "gateway_route_status": {"value": None, "collected": False}})
    assert sec(evd) == Verdict.UNKNOWN


def test_unexpected_value_is_unknown_not_pass_or_fail():
    # 401/403도 200도 아닌 응답(예: 500)은 확인 부족이다
    evd = evidence(observations={**OK, "direct_route_status": {"value": 500, "collected": True}})
    res = judge(evd)
    assert res.verdicts.security == Verdict.UNKNOWN
    assert res.reasons


def test_unknown_and_fail_are_distinct():
    fail = evidence(observations={**OK, "direct_route_status": {"value": 200, "collected": True}})
    unknown = evidence(observations={**OK, "direct_route_status": {"value": None, "collected": False}})
    assert sec(fail) == Verdict.FAIL and sec(unknown) == Verdict.UNKNOWN
    assert judge(unknown).missing_observations == ["direct_route_status"]
    assert judge(fail).missing_observations == []
