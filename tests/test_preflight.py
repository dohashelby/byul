import pytest
from guard.core.preflight import validate_pack
from tests.builders import baseline_pack


def test_pack_rules_resolve_to_declared_keys():
    validate_pack(baseline_pack())


@pytest.mark.parametrize("rule", [
    {"obs":"typo","op":"eq","value":True},
    {"obs":"routes_registered","op":"typo"},
    {"obs":"routes_registered","op":"eq","value_policy":"typo"},
])
def test_bad_rules_are_rejected_before_running(rule):
    pack = baseline_pack()
    check = pack.checks["REQ-01"]
    check.judge.params = {"pass_when_all":[rule]}
    with pytest.raises(ValueError):
        validate_pack(pack)
