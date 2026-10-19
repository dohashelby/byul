"""INV-15 (판정 쪽): 판정 플러그인 실패·미완료 실행은 UNKNOWN. PASS·NOT_APPLICABLE로 바꾸지 않는다."""

from guard.judges import registry
from guard.models import ExecStatus, Verdict
from tests.builders import evidence
from tests.judging import OK, REQ01, judge


class Broken:
    name, version = "rule_table", "x"

    def judge(self, *a, **k):
        raise RuntimeError("bad table")


class SaysNA:
    name, version = "rule_table", "x"

    def judge(self, check, evd, params, pv):
        from guard.judges.base import JudgeOutput
        return JudgeOutput(verdicts={"security": "NOT_APPLICABLE", "functional": "PASS", "performance": "PASS"})


def _with(plugin, fn):
    old = registry.JUDGES["rule_table"]
    registry.JUDGES["rule_table"] = plugin
    try:
        return fn()
    finally:
        registry.JUDGES["rule_table"] = old


def test_plugin_exception_is_unknown():
    res = _with(Broken(), lambda: judge(evidence(observations=OK)))
    assert {res.verdicts.security, res.verdicts.functional, res.verdicts.performance} == {Verdict.UNKNOWN}
    assert any("bad table" in r for r in res.reasons)


def test_plugin_cannot_emit_not_applicable():
    res = _with(SaysNA(), lambda: judge(evidence(observations=OK)))
    assert res.verdicts.security == Verdict.UNKNOWN


def test_aborted_and_not_run_evidence_are_unknown():
    for status in (ExecStatus.ABORTED, ExecStatus.NOT_RUN):
        missing = {k: {"value": None, "collected": False} for k in OK}
        res = judge(evidence(status=status, normal={"attempted": False}, attempted=False, observations=missing))
        assert res.verdicts.security == Verdict.UNKNOWN
        assert res.verification_level is None  # INV-12: 근거 없음


def test_check_without_judge_is_unknown_manual():
    from tests.judging import PACK
    res = judge(evidence(check_id="SUP-01", attempted=False, observations={}), check=PACK.checks["SUP-01"])
    assert res.verdicts.security == Verdict.UNKNOWN
    assert any("manual" in r for r in res.reasons)
