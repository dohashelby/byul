"""INV-04: 위반 증거 없음 ≠ 준수 증거 있음. 수집 실패는 UNKNOWN. 확인된 FAIL은 다른 누락으로 지우지 않는다."""

from guard.models import ExecStatus, Verdict
from tests.builders import evidence
from tests.judging import OK, sec

DIRECT_200 = {"value": 200, "collected": True}
MISSING = {"value": None, "collected": False}


def test_sensor_failure_is_unknown():
    assert sec(evidence(observations={"direct_route_status": MISSING, "gateway_route_status": MISSING})) == Verdict.UNKNOWN


def test_no_violation_seen_is_not_pass():
    # 직통 200이 아니라는 것만으로는 PASS가 아니다(401/403 거부가 관측돼야 함)
    assert sec(evidence(observations={**OK, "direct_route_status": {"value": 404, "collected": True}})) == Verdict.UNKNOWN


def test_confirmed_fail_survives_other_missing():
    evd = evidence(observations={"direct_route_status": DIRECT_200, "gateway_route_status": MISSING})
    assert sec(evd) == Verdict.FAIL


def test_confirmed_fail_survives_failed_normal_control_and_partial_attempts():
    evd = evidence(normal={"attempted": True, "succeeded": False}, attempted=[True, False, False],
                   observations={**OK, "direct_route_status": DIRECT_200})
    assert sec(evd) == Verdict.FAIL


def test_confirmed_fail_survives_aborted_execution():
    evd = evidence(status=ExecStatus.ABORTED, observations={"direct_route_status": DIRECT_200, "gateway_route_status": MISSING})
    assert sec(evd) == Verdict.FAIL
