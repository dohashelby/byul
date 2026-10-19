"""INV-12: 여러 근거를 집계한 verification_level·data_origin은 필수 근거 중 하나라도 null이면 null."""

from guard.models import DataOrigin as O
from guard.models import weakest_origin


def test_any_null_makes_null():
    assert weakest_origin([O.MEASURED, None]) is None
    assert weakest_origin([None, O.EXAMPLE]) is None


def test_empty_is_null_not_measured():
    assert weakest_origin([]) is None


def test_weakest_wins_without_dropping():
    assert weakest_origin([O.MEASURED, O.MEASURED]) == O.MEASURED
    assert weakest_origin([O.MEASURED, O.DECLARED]) == O.DECLARED
    assert weakest_origin([O.DECLARED, O.EXAMPLE, O.MEASURED]) == O.EXAMPLE


def test_result_verification_level_null_if_any_basis_null():
    from tests.builders import evidence
    from tests.judging import OK, judge

    evd = evidence(observations=OK)
    assert judge(evd, origins=[O.DECLARED]).verification_level == O.EXAMPLE
    assert judge(evd, origins=[O.DECLARED, None]).verification_level is None
