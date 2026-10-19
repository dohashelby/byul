"""INV-11: PASS는 ① 정상 대조 성공 ② 모든 챌린지 시도·기대대로 거부 ③ 필수 관측 전부 수집 — 모두일 때만."""

import pytest

from guard.models import ExecStatus, Verdict
from tests.builders import evidence
from tests.judging import OK, judge, sec


def test_all_conditions_met_is_pass():
    assert sec(evidence(observations=OK)) == Verdict.PASS


@pytest.mark.parametrize(
    "kw",
    [
        {"normal": {"attempted": False, "succeeded": None}},  # ① 정상 대조 미시도
        {"normal": {"attempted": True, "succeeded": None}},  # ① 결과 모름
        {"attempted": [True, True, False]},  # ② 챌린지 일부 미시도
        {"observations": {"direct_route_status": OK["direct_route_status"]}},  # ③ 관측 키 누락
        {"observations": {**OK, "gateway_route_status": {"value": None, "collected": False}}},  # ③ 수집 실패
        {"status": ExecStatus.ABORTED},  # 실행 미완료
    ],
)
def test_any_missing_condition_is_unknown(kw):
    assert sec(evidence(**kw)) == Verdict.UNKNOWN


def test_over_blocking_is_not_pass():
    # 정상 대조 실패(허용 신원도 Gateway에서 403) → 보안 PASS 아님, 기능 FAIL
    evd = evidence(normal={"attempted": True, "succeeded": False},
                   observations={"direct_route_status": {"value": 403, "collected": True},
                                 "gateway_route_status": {"value": 403, "collected": True}})
    res = judge(evd)
    assert res.verdicts.security == Verdict.UNKNOWN
    assert res.verdicts.functional == Verdict.FAIL


def test_check_without_challenge_records_is_not_pass():
    evd = evidence(attempted=[], observations=OK)  # challenges 비어 있음
    assert sec(evd) == Verdict.UNKNOWN
