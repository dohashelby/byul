"""버전 비교 규칙 표 전체 (SPEC §4.7)."""

import pytest

from guard.core.compare import compare, security_comparison_verdict
from guard.models import Comparison as C
from guard.models import SecurityFailureTag as Tag
from guard.models import Verdict as V

TABLE = {
    (V.PASS, V.PASS): C.NO_CHANGE, (V.PASS, V.FAIL): C.REGRESSED, (V.PASS, V.UNKNOWN): C.UNKNOWN,
    (V.FAIL, V.PASS): C.FIXED, (V.FAIL, V.FAIL): C.STILL_FAILING, (V.FAIL, V.UNKNOWN): C.UNKNOWN,
    (V.UNKNOWN, V.PASS): C.UNKNOWN, (V.UNKNOWN, V.FAIL): C.UNKNOWN, (V.UNKNOWN, V.UNKNOWN): C.UNKNOWN,
}


@pytest.mark.parametrize("pair", list(TABLE), ids=lambda p: f"{p[0].value}->{p[1].value}")
def test_table_cell(pair):
    assert compare(*pair, comparable=True) == TABLE[pair]


@pytest.mark.parametrize("pair", list(TABLE), ids=lambda p: f"{p[0].value}->{p[1].value}")
def test_not_comparable_overrides_table(pair):
    assert compare(*pair, comparable=False) == C.NOT_COMPARABLE


def test_not_applicable_side_is_not_comparable():
    assert compare(V.NOT_APPLICABLE, V.PASS, comparable=True) == C.NOT_COMPARABLE


@pytest.mark.parametrize("tags,expected", [
    ([], V.FAIL),
    ([Tag.CONFIRMED_VIOLATION], V.FAIL),
    ([Tag.CONFIRMED_VIOLATION, Tag.ABORTED], V.FAIL),
    ([Tag.CONDITIONS_UNMET], V.UNKNOWN),
    ([Tag.NOT_RUN, Tag.INSUFFICIENT_EVIDENCE], V.UNKNOWN),
    ([Tag.NO_PASS_RULE], V.UNKNOWN),
])
def test_security_comparison_discriminates_between_unproven_and_confirmed_fail(tags, expected):
    candidate = security_comparison_verdict(V.FAIL, tags)
    assert candidate == expected
    assert compare(V.PASS, candidate, comparable=True) == TABLE[V.PASS, expected]
    assert compare(candidate, V.PASS, comparable=False) == C.NOT_COMPARABLE
