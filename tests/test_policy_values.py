"""정책값 검증 (Step 1-C): select가 null·잘못된 타입·알 수 없는 타입을 실행 전에 거부한다."""

import pytest

from guard.core.select import PolicyValueError, check_policy_value, select
from guard.models import CheckDef
from tests.builders import baseline_pack, environment, policy

D = {"origin": "DECLARED", "source": "env.yaml"}


@pytest.mark.parametrize(
    "type_, value, ok",
    [
        ("str", "a", True), ("str", 1, False), ("str", None, False),
        ("int", 3, True), ("int", True, False), ("int", "3", False),
        ("bool", True, True), ("bool", 1, False),
        ("list[str]", ["a", "b"], True), ("list[str]", [], True), ("list[str]", "a", False),
        ("list[str]", ["a", 1], False), ("list[str]", None, False),
    ],
)
def test_check_policy_value(type_, value, ok):
    if ok:
        check_policy_value("X-01", "k", type_, value)
    else:
        with pytest.raises(PolicyValueError) as e:
            check_policy_value("X-01", "k", type_, value)
        assert "X-01" in str(e.value) and "k" in str(e.value) and type_ in str(e.value)


def test_unknown_type_rejected():
    with pytest.raises(PolicyValueError, match="unsupported policy type"):
        check_policy_value("X-01", "k", "dict[str,int]", {})


def _plan(values):
    pack = baseline_pack()
    return select(pack, [environment({"gateway": {"value": True, **D}})],
                  policy(pack.ref(), values=values, scope={"include": ["REQ-01", "REQ-03"], "exclude": []}))


@pytest.mark.parametrize("bad", [None, "gateway-sa", ["ok", 3]])
def test_select_rejects_bad_value_before_running(bad):
    with pytest.raises(PolicyValueError) as e:
        _plan({"REQ-01": {"allowed_callers": bad}, "REQ-03": {"general_allowed_kinds": ["inference"]}})
    msg = str(e.value)
    assert "REQ-01" in msg and "allowed_callers" in msg and "list[str]" in msg


def test_select_accepts_valid_values_and_missing_is_still_gap():
    plan = _plan({"REQ-01": {"allowed_callers": ["allowed"]}, "REQ-03": {"general_allowed_kinds": ["inference"]}})
    assert plan.item("REQ-01").gaps == [] and plan.item("REQ-03").gaps == []
    plan = _plan({"REQ-03": {"general_allowed_kinds": ["inference"]}})
    assert plan.item("REQ-01").gaps == ["NO_POLICY_VALUE"]  # 값이 없으면 기존처럼 공백(실행 안 함)


def test_pack_declares_only_supported_types():
    from guard.core.select import SUPPORTED_POLICY_TYPES

    for c in baseline_pack().active_checks():
        for p in c.policy_params:
            assert p.type in SUPPORTED_POLICY_TYPES, (c.id, p.key, p.type)
