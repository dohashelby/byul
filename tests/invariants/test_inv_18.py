"""INV-18: 보안·기능·성능 판정은 따로 기록한다. 한 축의 결과로 다른 축을 채우지 않는다."""

import pytest
from pydantic import ValidationError

from guard.models import Verdicts


def test_all_three_axes_required_separately():
    with pytest.raises(ValidationError):
        Verdicts(security="PASS")  # 다른 축을 자동으로 채우지 않는다
    v = Verdicts(security="PASS", functional="UNKNOWN", performance="UNKNOWN")
    assert (v.security, v.functional, v.performance) == ("PASS", "UNKNOWN", "UNKNOWN")


def test_axes_judged_independently():
    from tests.builders import evidence
    from tests.judging import OK, judge

    # 보안 FAIL(직통 200)이어도 기능은 Gateway 관측으로 따로 PASS, 성능은 규칙이 없어 UNKNOWN
    res = judge(evidence(observations={**OK, "direct_route_status": {"value": 200, "collected": True}}))
    assert (res.verdicts.security, res.verdicts.functional, res.verdicts.performance) == ("FAIL", "PASS", "UNKNOWN")
    res = judge(evidence(observations=OK))
    assert res.verdicts.performance == "UNKNOWN"  # 보안 PASS를 성능에 복사하지 않는다
