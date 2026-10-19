"""버전 비교 규칙 (SPEC §4.7, INV-09)."""

from __future__ import annotations

from guard.models import Comparison, Verdict
from guard.models.objects import Comparability

_TABLE: dict[tuple[Verdict, Verdict], Comparison] = {
    (Verdict.PASS, Verdict.PASS): Comparison.NO_CHANGE,
    (Verdict.PASS, Verdict.FAIL): Comparison.REGRESSED,
    (Verdict.FAIL, Verdict.PASS): Comparison.FIXED,
    (Verdict.FAIL, Verdict.FAIL): Comparison.STILL_FAILING,
}
_COMPARABLE_VERDICTS = {Verdict.PASS, Verdict.FAIL, Verdict.UNKNOWN}


def comparable(a: Comparability, b: Comparability) -> bool:
    """모든 비교 조건이 양쪽에서 null이 아니고 같아야 한다."""
    da, db = a.model_dump(), b.model_dump()
    return all(da[k] is not None and da[k] == db[k] for k in da)


def compare(baseline: Verdict, candidate: Verdict, comparable: bool) -> Comparison:
    baseline, candidate = Verdict(baseline), Verdict(candidate)
    if not comparable or baseline not in _COMPARABLE_VERDICTS or candidate not in _COMPARABLE_VERDICTS:
        return Comparison.NOT_COMPARABLE
    return _TABLE.get((baseline, candidate), Comparison.UNKNOWN)


def compare_results(base: Verdict, base_cmp: Comparability, cand: Verdict, cand_cmp: Comparability) -> Comparison:
    return compare(base, cand, comparable(base_cmp, cand_cmp))
