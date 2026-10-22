"""rule_table 판정기: 관측 키·연산자·기대값 표로 판정한다 (SPEC §7.2).

    params:
      pass_when_all: [{obs, op, value | value_obs, when?}]   # security 축
      fail_when_any: [{obs, op, value | value_obs, when?}]
      axes:                                                  # 다른 축은 같은 형식으로 따로
        functional: {pass_when_all: [...], fail_when_any: [...]}

- fail_when_any 중 하나가 **수집된** 관측으로 성립하면 FAIL.
- 아니고 pass_when_all이 비어 있지 않고 전부 수집된 관측으로 성립하면 PASS 후보.
- 그 밖은 UNKNOWN. 규칙이 없는 축은 UNKNOWN(INV-18).
- `when`: 전제가 성립할 때만 그 규칙을 요구(pass) 또는 적용(fail)한다. 전제 관측이 없으면 미확인.
- `value_obs`: 기대값 대신 다른 관측 값과 비교. 그 관측이 없으면 미확인.
- `value_policy`: 기대값을 정책 값(POL.values[점검])의 이 키에서 읽는다. 키가 없으면 미확인(UNKNOWN).
- `when_policy`: 정책 값(bool)의 이 키가 true일 때만 그 규칙을 요구(pass)·적용(fail). false면 생략, 없으면 미확인.
PASS 관문(정상 대조·챌린지 시도·필수 관측, INV-11)은 판정 코어가 따로 강제한다.
"""

from __future__ import annotations

from typing import Any

from guard.models import CheckDef, Evidence, Verdict
from guard.input_policy import policy_context

from .base import AXES, JudgeOutput

_MISSING = object()


def _same(a: Any, b: Any) -> bool:
    """bool과 int를 섞지 않는 같음 (True == 1 방지)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _member(x: Any, values: Any) -> bool:
    return any(_same(x, v) for v in values)


def _elements(actual: Any) -> list[Any]:
    if isinstance(actual, dict):
        return list(actual.values())
    if isinstance(actual, (list, tuple, set)):
        return list(actual)
    return [actual]


def _keys(actual: Any) -> set:
    if isinstance(actual, dict):
        return set(actual)
    return set(actual)


def _number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _between(x: Any, rng: Any) -> bool:
    lo, hi = rng
    return _number(x) and lo <= x <= hi


def _safe(fn):
    def op(actual: Any, expected: Any, other: Any) -> bool:
        try:
            return bool(fn(actual, expected, other))
        except TypeError:
            return False

    return op


OPS = {
    "eq": _safe(lambda a, e, o: _same(a, e)),
    "ne": _safe(lambda a, e, o: a is not None and not _same(a, e)),
    "in": _safe(lambda a, e, o: _member(a, e)),
    "not_in": _safe(lambda a, e, o: a is not None and not _member(a, e)),
    "lt": _safe(lambda a, e, o: _number(a) and a < e),
    "le": _safe(lambda a, e, o: _number(a) and a <= e),
    "gt": _safe(lambda a, e, o: _number(a) and a > e),
    "ge": _safe(lambda a, e, o: _number(a) and a >= e),
    "nonempty": _safe(lambda a, e, o: isinstance(a, (list, dict, tuple, set, str)) and len(a) > 0),
    "all_in": _safe(lambda a, e, o: a is not None and all(_member(x, e) for x in _elements(a))),
    "any_in": _safe(lambda a, e, o: a is not None and any(_member(x, e) for x in _elements(a))),
    "none_in": _safe(lambda a, e, o: a is not None and not any(_member(x, e) for x in _elements(a))),
    "any_between": _safe(lambda a, e, o: a is not None and any(_between(x, e) for x in _elements(a))),
    "all_between": _safe(lambda a, e, o: a is not None and all(_between(x, e) for x in _elements(a))),
    "superset_of": _safe(lambda a, e, o: a is not None and o is not None and _keys(a) >= _keys(o)),
    # 관측 ⊆ 기대(value 또는 value_policy). dict는 키 집합. 반대는 not_subset_of(허용 밖 원소가 있음)
    "subset_of": _safe(lambda a, e, o: a is not None and e is not None and _keys(a) <= _keys(e)),
    "not_subset_of": _safe(lambda a, e, o: a is not None and e is not None and not (_keys(a) <= _keys(e))),
}


def evaluate_rule(rule: dict, actual: Any, other: Any = None) -> bool:
    op = rule.get("op")
    if op not in OPS:
        raise ValueError(f"unknown rule_table op {op!r}")
    return OPS[op](actual, rule.get("value"), other)


def _describe(rule: dict) -> str:
    if "value_obs" in rule:
        rhs = f"<{rule['value_obs']}>"
    elif "value_policy" in rule:
        rhs = f"<policy:{rule['value_policy']}>"
    else:
        rhs = repr(rule.get("value"))
    text = f"{rule['obs']} {rule['op']}" + ("" if rule["op"] == "nonempty" else f" {rhs}")
    if rule.get("when"):
        text += f" (when {_describe(rule['when'])})"
    if rule.get("when_policy"):
        text += f" (when policy:{rule['when_policy']})"
    return text


class RuleTableJudge:
    name = "rule_table"
    version = "0.5.0"

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput:
        tables = {"security": {k: params[k] for k in ("pass_when_all", "fail_when_any") if k in params}}
        for axis, table in (params.get("axes") or {}).items():
            if axis not in AXES or axis == "security":
                raise ValueError(f"unknown or duplicate axis {axis!r}")
            tables[axis] = table

        verdicts: dict[str, Verdict] = {}
        reasons: list[str] = []
        missing: set[str] = set()
        policy_missing: set[str] = set()
        policy_values = policy_values or {}

        def observed(key: str) -> Any:
            obs = evidence.observations.get(key)
            if obs is None or not obs.collected:
                missing.add(key)
                return _MISSING
            return obs.value

        def check_rule(rule: dict) -> bool | None:
            """True/False, 관측(또는 정책 값)이 없어 판단 못 하면 None."""
            if "value_policy" in rule:
                if "value" in rule:
                    raise ValueError(f"rule has both value and value_policy: {rule}")
                key = rule["value_policy"]
                if key not in policy_values:
                    policy_missing.add(key)
                    return None
                rule = {**rule, "value": policy_values[key]}
            v = observed(rule["obs"])
            other = observed(rule["value_obs"]) if "value_obs" in rule else None
            if v is _MISSING or other is _MISSING:
                return None
            return evaluate_rule(rule, v, other)

        def premise(rule: dict) -> bool | None:
            if "when_policy" in rule:
                key = rule["when_policy"]
                if key not in policy_values:
                    policy_missing.add(key)
                    return None
                if policy_values[key] is not True:
                    return False
            return check_rule(rule["when"]) if rule.get("when") else True

        for axis in AXES:
            table = tables.get(axis)
            if not table:
                verdicts[axis] = Verdict.UNKNOWN
                reasons.append(f"{axis}: no rule for this axis")
                continue
            fail_hits = []
            for rule in table.get("fail_when_any", []):
                if premise(rule) is True and check_rule(rule) is True:
                    fail_hits.append(_describe(rule))
            if fail_hits:
                verdicts[axis] = Verdict.FAIL
                reasons.append(f"{axis}: FAIL because {'; '.join(fail_hits)} (observed {_values(evidence, table['fail_when_any'])})")
                continue
            pass_rules = table.get("pass_when_all", [])
            unmet, unobserved = [], []
            for rule in pass_rules:
                pre = premise(rule)
                if pre is None:
                    unobserved.append(_describe(rule))
                    continue
                if pre is False:
                    continue  # 전제가 성립하지 않으면 요구하지 않는다
                ok = check_rule(rule)
                if ok is None:
                    unobserved.append(_describe(rule))
                elif not ok:
                    unmet.append(f"{_describe(rule)} (observed {evidence.observations[rule['obs']].value!r})")
            if pass_rules and not unmet and not unobserved:
                verdicts[axis] = Verdict.PASS
                reasons.append(f"{axis}: all pass rules met: {'; '.join(_describe(r) for r in pass_rules)}")
            else:
                verdicts[axis] = Verdict.UNKNOWN
                detail = []
                if unobserved:
                    detail.append(f"not collected for: {'; '.join(unobserved)}")
                if unmet:
                    detail.append(f"pass rules unmet: {'; '.join(unmet)}")
                if not pass_rules:
                    detail.append("no pass rules")
                reasons.append(f"{axis}: UNKNOWN ({'; '.join(detail)})")
        for key in sorted(policy_missing):
            reasons.append(f"policy value missing: {key}")
        context = policy_context(check, policy_values, evidence.observations)
        if context is not None:
            reasons.extend(context.reasons)
            missing.update(context.missing)
            if context.blocked:
                verdicts = {axis: Verdict.UNKNOWN if verdict == Verdict.PASS else verdict
                            for axis, verdict in verdicts.items()}
        return JudgeOutput(verdicts=verdicts, reasons=reasons, missing_observations=sorted(missing))


def _values(evidence: Evidence, rules: list[dict]) -> str:
    keys = sorted({r["obs"] for r in rules if r["obs"] in evidence.observations})
    return ", ".join(f"{k}={evidence.observations[k].value!r}" for k in keys)
