"""rule_table 판정기: 관측 키·연산자·기대값 표로 판정한다 (SPEC §7.2).

    params:
      pass_when_all: [{obs, op, value}]   # security 축
      fail_when_any: [{obs, op, value}]
      axes:                                # 다른 축은 같은 형식으로 따로
        functional: {pass_when_all: [...], fail_when_any: [...]}

- fail_when_any 중 하나가 **수집된** 관측으로 성립하면 FAIL.
- 아니고 pass_when_all이 비어 있지 않고 전부 수집된 관측으로 성립하면 PASS 후보.
- 그 밖은 UNKNOWN. 규칙이 없는 축은 UNKNOWN(INV-18).
PASS 관문(정상 대조·챌린지 시도·필수 관측, INV-11)은 판정 코어가 따로 강제한다.
"""

from __future__ import annotations

from typing import Any

from guard.models import CheckDef, Evidence, Verdict

from .base import AXES, JudgeOutput

_MISSING = object()


def _cmp(fn):
    def op(actual: Any, expected: Any) -> bool:
        try:
            return bool(fn(actual, expected))
        except TypeError:
            return False

    return op


OPS = {
    "eq": _cmp(lambda a, e: a == e),
    "ne": _cmp(lambda a, e: a is not None and a != e),
    "in": _cmp(lambda a, e: a in e),
    "not_in": _cmp(lambda a, e: a is not None and a not in e),
    "lt": _cmp(lambda a, e: a < e),
    "le": _cmp(lambda a, e: a <= e),
    "gt": _cmp(lambda a, e: a > e),
    "ge": _cmp(lambda a, e: a >= e),
}


def evaluate_rule(rule: dict, actual: Any) -> bool:
    op = rule.get("op")
    if op not in OPS:
        raise ValueError(f"unknown rule_table op {op!r}")
    return OPS[op](actual, rule.get("value"))


def _describe(rule: dict) -> str:
    return f"{rule['obs']} {rule['op']} {rule.get('value')!r}"


class RuleTableJudge:
    name = "rule_table"
    version = "0.1.0"

    def judge(self, check: CheckDef, evidence: Evidence, params: dict, policy_values: dict) -> JudgeOutput:
        tables = {"security": {k: params[k] for k in ("pass_when_all", "fail_when_any") if k in params}}
        for axis, table in (params.get("axes") or {}).items():
            if axis not in AXES or axis == "security":
                raise ValueError(f"unknown or duplicate axis {axis!r}")
            tables[axis] = table

        verdicts: dict[str, Verdict] = {}
        reasons: list[str] = []
        missing: set[str] = set()

        def observed(key: str) -> Any:
            obs = evidence.observations.get(key)
            if obs is None or not obs.collected:
                missing.add(key)
                return _MISSING
            return obs.value

        for axis in AXES:
            table = tables.get(axis)
            if not table:
                verdicts[axis] = Verdict.UNKNOWN
                reasons.append(f"{axis}: no rule for this axis")
                continue
            fail_hits = []
            for rule in table.get("fail_when_any", []):
                v = observed(rule["obs"])
                if v is not _MISSING and evaluate_rule(rule, v):
                    fail_hits.append(_describe(rule))
            if fail_hits:
                verdicts[axis] = Verdict.FAIL
                reasons.append(f"{axis}: FAIL because {'; '.join(fail_hits)} (observed {_values(evidence, fail_hits)})")
                continue
            pass_rules = table.get("pass_when_all", [])
            unmet, unobserved = [], []
            for rule in pass_rules:
                v = observed(rule["obs"])
                if v is _MISSING:
                    unobserved.append(rule["obs"])
                elif not evaluate_rule(rule, v):
                    unmet.append(f"{_describe(rule)} (observed {v!r})")
            if pass_rules and not unmet and not unobserved:
                verdicts[axis] = Verdict.PASS
                reasons.append(f"{axis}: all pass rules met: {'; '.join(_describe(r) for r in pass_rules)}")
            else:
                verdicts[axis] = Verdict.UNKNOWN
                detail = []
                if unobserved:
                    detail.append(f"not collected: {', '.join(unobserved)}")
                if unmet:
                    detail.append(f"pass rules unmet: {'; '.join(unmet)}")
                if not pass_rules:
                    detail.append("no pass rules")
                reasons.append(f"{axis}: UNKNOWN ({'; '.join(detail)})")
        return JudgeOutput(verdicts=verdicts, reasons=reasons, missing_observations=sorted(missing))


def _values(evidence: Evidence, described: list[str]) -> str:
    keys = sorted({d.split(" ", 1)[0] for d in described})
    return ", ".join(f"{k}={evidence.observations[k].value!r}" for k in keys)
