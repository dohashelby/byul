"""INV-04: 위반 증거 없음 ≠ 준수 증거 있음. 수집 실패는 UNKNOWN. 확인된 FAIL은 다른 누락으로 지우지 않는다."""

from guard.models import ExecStatus, Verdict
from tests.builders import evidence, req01_obs
from tests.judging import direct, sec


def test_sensor_failure_is_unknown():
    assert sec(evidence(observations=req01_obs(missing=("direct_outcomes", "gateway_outcomes")))) == Verdict.UNKNOWN


def test_no_violation_seen_is_not_pass():
    # 2xx가 아니라는 것만으로는 PASS가 아니다(등록 경로 404는 UNKNOWN)
    assert sec(evidence(observations=req01_obs(direct=direct(invocations=404)))) == Verdict.UNKNOWN


def test_confirmed_fail_survives_other_missing():
    obs = req01_obs(direct=direct(completions=200), missing=("gateway_outcomes", "trusted_origin_reachable", "routes_tested"))
    assert sec(evidence(observations=obs)) == Verdict.FAIL


def test_confirmed_fail_survives_partial_routes():
    obs = req01_obs(tested=["POST /invocations"], direct={"POST /invocations": 200})
    obs["routes_registered"]["value"] = ["POST /v1/completions", "POST /invocations"]
    assert sec(evidence(observations=obs)) == Verdict.FAIL


def test_confirmed_fail_survives_failed_normal_control_and_partial_attempts():
    evd = evidence(normal={"attempted": True, "succeeded": False}, attempted=[True, False, False],
                   observations=req01_obs(direct=direct(invocations=200)))
    assert sec(evd) == Verdict.FAIL


def test_confirmed_fail_survives_aborted_execution():
    evd = evidence(status=ExecStatus.ABORTED, observations=req01_obs(direct=direct(models=204), missing=("gateway_outcomes",)))
    assert sec(evd) == Verdict.FAIL
