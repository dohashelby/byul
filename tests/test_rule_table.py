"""rule_table 연산자."""

import pytest

from guard.judges.rule_table import evaluate_rule


@pytest.mark.parametrize(
    "op, value, actual, ok",
    [
        ("eq", 200, 200, True), ("eq", 200, 201, False), ("ne", 200, 201, True),
        ("in", [401, 403], 403, True), ("in", [401, 403], 200, False), ("not_in", [200], 401, True),
        ("lt", 5, 4, True), ("le", 5, 5, True), ("gt", 5, 6, True), ("ge", 5, 4, False),
        ("lt", 5, None, False), ("eq", 200, None, False),
    ],
)
def test_ops(op, value, actual, ok):
    assert evaluate_rule({"obs": "x", "op": op, "value": value}, actual) is ok


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        evaluate_rule({"obs": "x", "op": "regex", "value": "."}, "a")
