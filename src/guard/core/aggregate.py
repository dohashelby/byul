"""집계 규칙 (SPEC §4.7): 점검 → 보호 목적 → 영역. 순서를 바꾸지 않는다."""

from __future__ import annotations

from typing import Iterable

from guard.models import Verdict


def aggregate(verdicts: Iterable[Verdict], inventory_complete: bool) -> Verdict:
    vs = [Verdict(v) for v in verdicts]
    if Verdict.FAIL in vs:
        return Verdict.FAIL
    if Verdict.UNKNOWN in vs or not inventory_complete:
        return Verdict.UNKNOWN
    if Verdict.PASS in vs:
        return Verdict.PASS
    return Verdict.NOT_APPLICABLE
