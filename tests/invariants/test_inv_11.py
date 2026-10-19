"""INV-11: PASS는 ① 정상 대조 성공 ② 모든 챌린지 시도·기대대로 거부 ③ 필수 관측 전부 수집 — 모두일 때만."""

import pytest

from guard.models import ExecStatus, Verdict
from tests.builders import evidence, req01_obs
from tests.judging import OK, judge, sec


def test_all_conditions_met_is_pass():
    assert sec(evidence(observations=OK)) == Verdict.PASS


_ONLY_SOME = {k: v for k, v in req01_obs().items() if k != "trusted_origin_reachable"}


@pytest.mark.parametrize(
    "kw",
    [
        {"normal": {"attempted": False, "succeeded": None}},  # ① 정상 대조 미시도
        {"normal": {"attempted": True, "succeeded": None}},  # ① 결과 모름
        {"attempted": [True, True, False]},  # ② 챌린지 일부 미시도
        {"observations": _ONLY_SOME},  # ③ 관측 키 누락 (판정표가 안 쓰는 키여도)
        {"observations": req01_obs(missing=("gateway_outcomes",))},  # ③ 수집 실패
        {"observations": req01_obs(registered=[], tested=[])},  # 등록 경로 없음 = 시험 범위 없음
        {"status": ExecStatus.ABORTED},  # 실행 미완료
    ],
)
def test_any_missing_condition_is_unknown(kw):
    assert sec(evidence(**kw)) == Verdict.UNKNOWN


def test_over_blocking_is_not_pass():
    # 정상 대조 실패(허용 키도 Gateway에서 403) → 보안 PASS 아님, 기능 FAIL
    evd = evidence(normal={"attempted": True, "succeeded": False},
                   observations=req01_obs(gateway={"/v1/models": 403, "/v1/completions": 403, "/invocations": 403}))
    res = judge(evd)
    assert res.verdicts.security == Verdict.UNKNOWN
    assert res.verdicts.functional == Verdict.FAIL


def test_check_without_challenge_records_is_not_pass():
    assert sec(evidence(attempted=[], observations=OK)) == Verdict.UNKNOWN
