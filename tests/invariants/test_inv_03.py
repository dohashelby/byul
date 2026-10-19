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
