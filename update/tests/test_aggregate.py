"""집계 규칙 (SPEC §4.7): 점검 → 보호 목적 → 영역."""

import pytest

from guard.core.aggregate import aggregate
from guard.models import Verdict as V

P, F, U, NA = V.PASS, V.FAIL, V.UNKNOWN, V.NOT_APPLICABLE


@pytest.mark.parametrize(
    "verdicts, complete, expected",
    [
        ([P, F, U, NA], True, F),  # 1. FAIL 하나라도
        ([F], False, F),  # FAIL은 inventory 미완결보다 우선
        ([P, U], True, U),  # 2. UNKNOWN
        ([P, P], False, U),  # 2. inventory_complete false
        ([P, NA], True, P),  # 3. PASS 하나 이상
        ([P], True, P),
        ([NA, NA], True, NA),  # 4. 전부 NOT_APPLICABLE
        ([NA], False, U),
        ([], True, NA),
        ([], False, U),
    ],
)
def test_aggregate_rules(verdicts, complete, expected):
    assert aggregate(verdicts, inventory_complete=complete) == expected
