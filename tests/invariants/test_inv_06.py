"""INV-06: 환경 사실 false에는 비사용·비노출 근거가 필요하다. 근거 없는 false는 null. 빈 조건 목록은 항상 참이 아니다."""

from guard.core.select import evaluate_check
from guard.models import Applicability, CheckDef, CondResult
from tests.builders import environment

D = {"origin": "DECLARED", "source": "env.yaml"}


def _check(conds, always=False):
    return CheckDef.model_validate(dict(
        id="REQ-91", revision=1, area="REQ", purpose="P", question="q", review_order="x",
        applies_when={"all": conds, "always": always}, normal_control="n", challenge_steps=[],
        satisfied_if="s", fail_criteria="f", unknown_criteria="u"))


def test_false_without_absence_evidence_is_null():
    app, conds, _ = evaluate_check(_check([{"fact": "gateway", "eq": True}]),
                                   [environment({"gateway": {"value": False, **D}})])
    assert conds[0].result == CondResult.NULL
    assert app == Applicability.UNDETERMINED  # NOT_APPLICABLE로 빼지 않는다


def test_false_with_absence_evidence_counts():
    app, conds, _ = evaluate_check(_check([{"fact": "gateway", "eq": True}]),
                                   [environment({"gateway": {"value": False, **D, "absence_evidence": "EVD-1#x"}})])
    assert conds[0].result == CondResult.FALSE and app == Applicability.NOT_APPLICABLE


def test_unevidenced_false_cannot_satisfy_eq_false_either():
    app, conds, _ = evaluate_check(_check([{"fact": "prefix_cache", "eq": False}]),
                                   [environment({"prefix_cache": {"value": False, **D}})])
    assert conds[0].result == CondResult.NULL and app == Applicability.UNDETERMINED


def test_empty_conditions_not_always_true():
    app, _, _ = evaluate_check(_check([]), [environment({})])
    assert app == Applicability.UNDETERMINED
