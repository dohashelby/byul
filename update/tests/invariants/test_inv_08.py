"""INV-08: 정책 예외는 판정을 바꾸지 않는다. 만료·미승인 예외는 표시하지 않고 사유를 남긴다."""

from datetime import timedelta

from guard.models import Verdict
from tests.builders import T0, evidence, policy
from tests.builders import req01_obs
from tests.judging import OK, PACK, direct, judge

FAIL_EVD = evidence(observations=req01_obs(direct=direct(completions=200)))


def _pol(**exc):
    base = {"check_id": "REQ-01", "reason": "임시 허용", "approved_by": "ciso", "approved_at": T0 - timedelta(days=1),
            "expires_at": T0 + timedelta(days=30)}
    base.update(exc)
    return policy(PACK.ref(), exceptions=[base])


def test_approved_exception_keeps_fail_and_is_marked():
    res = judge(FAIL_EVD, pol=_pol())
    assert res.verdicts.security == Verdict.FAIL
    assert res.exception is not None and res.exception.check_id == "REQ-01"


def test_expired_exception_not_marked_with_reason():
    res = judge(FAIL_EVD, pol=_pol(expires_at=T0 - timedelta(seconds=1)))
    assert res.verdicts.security == Verdict.FAIL and res.exception is None
    assert any("expired" in r for r in res.reasons)


def test_unapproved_exception_not_marked_with_reason():
    res = judge(FAIL_EVD, pol=_pol(approved_by=None, approved_at=None))
    assert res.exception is None
    assert any("not approved" in r for r in res.reasons)


def test_exception_does_not_change_pass_either():
    res = judge(evidence(observations=OK), pol=_pol())
    assert res.verdicts.security == Verdict.PASS
