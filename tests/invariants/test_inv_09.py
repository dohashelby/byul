"""INV-09: 비교 단위는 엔진 이미지. 모델 inventory·시험 자료·설정·GPU·드라이버가 같아야 비교. 다르거나 null이면 NOT_COMPARABLE."""

import pytest

from guard.core.compare import comparable, compare_results
from guard.models import Comparison, Verdict
from guard.models.objects import Comparability

FULL = dict(model_inventory_sha256="m" * 64, fixture_sha256="f" * 64, config_sha256="c" * 64, gpu="L4", driver="550")


def test_identical_conditions_are_comparable():
    assert comparable(Comparability(**FULL), Comparability(**FULL))


@pytest.mark.parametrize("field", list(FULL))
def test_any_difference_is_not_comparable(field):
    other = {**FULL, field: "different"}
    assert not comparable(Comparability(**FULL), Comparability(**other))


@pytest.mark.parametrize("field", list(FULL))
def test_any_null_is_not_comparable(field):
    nulled = {**FULL, field: None}
    assert not comparable(Comparability(**nulled), Comparability(**nulled))


def test_compare_results_uses_comparability():
    a, b = Comparability(**FULL), Comparability(**{**FULL, "driver": "560"})
    assert compare_results(Verdict.PASS, a, Verdict.FAIL, a) == Comparison.REGRESSED
    assert compare_results(Verdict.PASS, a, Verdict.FAIL, b) == Comparison.NOT_COMPARABLE


@pytest.mark.engine
@pytest.mark.skip(reason="두 엔진 이미지로 같은 점검을 실제 실행하는 guard update는 8단계(실제 엔진 필요). 비교 함수는 위에서 검증.")
def test_update_mode_with_two_real_images():
    raise AssertionError
