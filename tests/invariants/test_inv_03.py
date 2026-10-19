"""INV-03: 시도가 없으면 차단 성공이 아니다. 응답 미수신만으로 PASS가 되지 않는다."""

from guard.models import Verdict
from tests.builders import evidence
from tests.judging import OK, sec


def test_not_attempted_but_rejected_looking_is_not_pass():
    assert sec(evidence(attempted=False, observations=OK)) == Verdict.UNKNOWN


def test_partially_attempted_is_not_pass():
    assert sec(evidence(attempted=[True, False, True], observations=OK)) == Verdict.UNKNOWN


def test_no_response_is_not_pass():
    no_response = {**OK, "direct_route_status": {"value": None, "collected": True}}
    assert sec(evidence(observations=no_response)) == Verdict.UNKNOWN


import pytest  # noqa: E402


@pytest.mark.engine
@pytest.mark.skip(reason="실제 엔진 직통 요청에서 연결 거부·타임아웃이 PASS가 되지 않는지는 5단계 direct_route 실행기와 함께 시험.")
def test_real_connection_refused_is_not_pass():
    raise AssertionError
