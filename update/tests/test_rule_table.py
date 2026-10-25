"""rule_table 연산자 (SPEC §7.2)."""

import pytest

from guard.judges.rule_table import evaluate_rule


@pytest.mark.parametrize(
    "op, value, actual, ok",
    [
        ("eq", 200, 200, True), ("eq", 200, 201, False), ("ne", 200, 201, True),
        ("in", [401, 403], 403, True), ("in", [401, 403], 200, False), ("not_in", [200], 401, True),
        ("lt", 5, 4, True), ("le", 5, 5, True), ("gt", 5, 6, True), ("ge", 5, 4, False),
        ("lt", 5, None, False), ("eq", 200, None, False),
        ("eq", True, 1, False), ("eq", 1, True, False),  # bool과 int를 섞지 않는다
        # 모음
        ("nonempty", None, ["/a"], True), ("nonempty", None, [], False), ("nonempty", None, {}, False),
        ("nonempty", None, None, False),
        ("all_in", [401, "refused"], {"/a": 401, "/b": "refused"}, True),
        ("all_in", [401, "refused"], {"/a": 401, "/b": 404}, False),
        ("all_in", [401], [401, 401], True),
        ("all_in", [1], [True], False),
        ("any_in", ["refused", "timeout"], {"/a": 401, "/b": "timeout"}, True),
        ("any_in", ["refused"], {"/a": 401}, False),
        ("any_between", [200, 299], {"/a": "refused", "/b": 204}, True),
        ("any_between", [200, 299], {"/a": "refused", "/b": 401}, False),
        ("any_between", [200, 299], ["200"], False),  # 문자열은 숫자가 아님
        ("all_between", [200, 299], [200, 201], True),
        ("all_between", [200, 299], [200, "timeout"], False),
    ],
)
def test_ops(op, value, actual, ok):
    assert evaluate_rule({"obs": "x", "op": op, "value": value}, actual) is ok


@pytest.mark.parametrize(
    "actual, other, ok",
    [
        (["/a", "/b"], ["/a"], True),
        (["/a"], ["/a", "/b"], False),
        ({"/a": 401, "/b": 401}, ["/a", "/b"], True),  # dict는 키 집합
        ({"/a": 401}, ["/a", "/b"], False),
        ([], [], True),
        (None, ["/a"], False),
    ],
)
def test_superset_of(actual, other, ok):
    assert evaluate_rule({"obs": "x", "op": "superset_of", "value_obs": "y"}, actual, other) is ok


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        evaluate_rule({"obs": "x", "op": "regex", "value": "."}, "a")


def _evd(obs):
    from tests.builders import evidence

    return evidence(observations={k: {"value": v, "collected": True} if v is not ... else {"value": None, "collected": False}
                                  for k, v in obs.items()})


def _judge(params, obs):
    from guard.judges.rule_table import RuleTableJudge
    from tests.judging import REQ01

    return RuleTableJudge().judge(REQ01, _evd(obs), params, {})


WHEN = {"pass_when_all": [{"obs": "b", "op": "eq", "value": True, "when": {"obs": "a", "op": "any_in", "value": ["refused"]}}]}


def test_when_false_means_rule_not_required():
    assert _judge(WHEN, {"a": [401], "b": False}).verdicts["security"] == "PASS"


def test_when_true_requires_rule():
    assert _judge(WHEN, {"a": ["refused"], "b": False}).verdicts["security"] == "UNKNOWN"
    assert _judge(WHEN, {"a": ["refused"], "b": True}).verdicts["security"] == "PASS"


def test_when_obs_uncollected_is_unknown():
    out = _judge(WHEN, {"a": ..., "b": True})
    assert out.verdicts["security"] == "UNKNOWN" and "a" in out.missing_observations


def test_value_obs_uncollected_is_unknown():
    params = {"pass_when_all": [{"obs": "t", "op": "superset_of", "value_obs": "r"}]}
    out = _judge(params, {"t": ["/a"], "r": ...})
    assert out.verdicts["security"] == "UNKNOWN" and "r" in out.missing_observations


# --- Step 1-D: subset_of, value_policy -------------------------------------------------


@pytest.mark.parametrize(
    "actual, expected, ok",
    [
        (["a"], ["a", "b"], True), ([], ["a"], True), (["a", "c"], ["a", "b"], False),
        ({"a": 1}, ["a", "b"], True),  # dict는 키 집합
        (None, ["a"], False), (["a"], None, False),
    ],
)
def test_subset_of(actual, expected, ok):
    assert evaluate_rule({"obs": "x", "op": "subset_of", "value": expected}, actual) is ok


def _judge_policy(params, obs, policy_values):
    from guard.judges.rule_table import RuleTableJudge
    from tests.judging import REQ01

    return RuleTableJudge().judge(REQ01, _evd(obs), params, policy_values)


CAPS = {"pass_when_all": [{"obs": "caps", "op": "subset_of", "value_policy": "allowed_caps"}],
        "fail_when_any": [{"obs": "caps", "op": "not_subset_of", "value_policy": "allowed_caps"}]}


def test_value_policy_reads_expected_from_policy():
    obs = {"caps": ["CHOWN"]}
    assert _judge_policy(CAPS, obs, {"allowed_caps": ["CHOWN", "KILL"]}).verdicts["security"] == "PASS"
    # 같은 증거, 정책 값만 다름 → 판정이 바뀐다
    assert _judge_policy(CAPS, obs, {"allowed_caps": []}).verdicts["security"] == "FAIL"


def test_value_policy_missing_key_is_unknown_not_pass():
    out = _judge_policy({"pass_when_all": CAPS["pass_when_all"]}, {"caps": []}, {})
    assert out.verdicts["security"] == "UNKNOWN"
    assert any("policy value missing: allowed_caps" in r for r in out.reasons)


def test_value_and_value_policy_together_is_error():
    with pytest.raises(ValueError):
        _judge_policy({"pass_when_all": [{"obs": "caps", "op": "subset_of", "value": [], "value_policy": "k"}]},
                      {"caps": []}, {"k": []})
