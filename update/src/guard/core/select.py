"""select: ENV + POL (+ CHG) + 팩 → PLAN (SPEC §4.4). 시험을 실행하지 않는다."""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatchcase
from typing import Any
from typing import TYPE_CHECKING

from guard.models import (
    Applicability,
    CheckDef,
    CondResult,
    Environment,
    Gap,
    Mode,
    NotSelectedReason,
    Pack,
    Plan,
    Policy,
    new_id,
    weakest_origin,
)
from guard.models.objects import Change, ConditionEval, FactValue, PlanInputs, PlanItem

if TYPE_CHECKING:
    from .change_selection import SelectionContext

ENV_COUNT = {Mode.audit: 1, Mode.update: 2}

# 정책 값 타입: 지금 팩이 쓰는 것만. 범용 정책 언어는 만들지 않는다(DECISIONS Step 1-C)
SUPPORTED_POLICY_TYPES = ("str", "int", "bool", "list[str]", "dict[str,int]")


class PolicyValueError(ValueError):
    """정책 값이 CheckDef.policy_params의 타입과 맞지 않음(실행 전에 거부)."""


def check_policy_value(check_id: str, key: str, type_: str, value: Any) -> None:
    if type_ not in SUPPORTED_POLICY_TYPES:
        raise PolicyValueError(f"{check_id}.{key}: unsupported policy type {type_!r} (supported: {', '.join(SUPPORTED_POLICY_TYPES)})")
    ok = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
        "list[str]": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
        "dict[str,int]": lambda v: isinstance(v, dict) and all(isinstance(k, str) and type(x) is int for k, x in v.items()),
    }[type_](value)
    if not ok:
        got = "null" if value is None else f"{type(value).__name__} {value!r}"
        raise PolicyValueError(f"{check_id}.{key}: expected {type_}, got {got}")


def evaluate_condition(fact: str, expected: Any, env: Environment) -> ConditionEval:
    fv: FactValue | None = env.facts.get(fact)

    def out(result: CondResult, note: str, actual: Any = None) -> ConditionEval:
        return ConditionEval(
            environment=env.id,
            fact=fact,
            expected=expected,
            actual=actual,
            origin=fv.origin if fv else None,
            result=result,
            note=note,
        )

    if fv is None:
        return out(CondResult.NULL, "fact not provided")
    if fv.conflict:
        return out(CondResult.CONFLICT, f"sources conflict: {fv.conflict}")
    if fv.value is None:
        return out(CondResult.NULL, "value unknown")
    if fv.origin is None or not fv.source:
        return out(CondResult.NULL, "no origin/source for value", fv.value)
    if type(fv.value) is not type(expected):
        return out(CondResult.NULL, f"type mismatch: {type(fv.value).__name__}", fv.value)
    if fv.value is False and not fv.absence_evidence:
        # INV-06: 근거 없는 false는 null
        return out(CondResult.NULL, "false without absence_evidence treated as null", fv.value)
    if fv.value == expected:
        return out(CondResult.TRUE, "matches", fv.value)
    return out(CondResult.FALSE, "evidenced mismatch", fv.value)


def _combine(conds: list[ConditionEval], always: bool) -> tuple[Applicability, str]:
    if not conds:
        if always:
            return Applicability.APPLICABLE, "applies_when.always"
        return Applicability.UNDETERMINED, "no structured conditions (prose only); empty list is not always-true"
    results = [c.result for c in conds]
    if CondResult.FALSE in results:
        false = [c.fact for c in conds if c.result == CondResult.FALSE]
        return Applicability.NOT_APPLICABLE, f"evidenced FALSE: {', '.join(false)}"
    if all(r == CondResult.TRUE for r in results):
        return Applicability.APPLICABLE, "all conditions evidenced TRUE"
    if CondResult.CONFLICT in results:
        conflict = [c.fact for c in conds if c.result == CondResult.CONFLICT]
        return Applicability.CONFLICT, f"conflicting values: {', '.join(conflict)}"
    unknown = [c.fact for c in conds if c.result == CondResult.NULL]
    return Applicability.UNDETERMINED, f"no evidence: {', '.join(unknown)}"


# 여러 환경(update 모드)에서는 어느 한쪽에서라도 적용되면 시험한다(추가 방향으로만 합침)
_INCLUSIVE = [Applicability.APPLICABLE, Applicability.CONFLICT, Applicability.UNDETERMINED, Applicability.NOT_APPLICABLE]


def evaluate_check(check: CheckDef, envs: list[Environment]) -> tuple[Applicability, list[ConditionEval], str]:
    per_env: list[tuple[Applicability, str]] = []
    all_conds: list[ConditionEval] = []
    for env in envs:
        conds = [evaluate_condition(c.fact, c.eq, env) for c in check.applies_when.all]
        per_env.append(_combine(conds, check.applies_when.always))
        all_conds.extend(conds)
    best = min(per_env, key=lambda x: _INCLUSIVE.index(x[0]))
    if len(envs) > 1:
        reason = "; ".join(f"{e.id}: {a.value} ({r})" for e, (a, r) in zip(envs, per_env))
        return best[0], all_conds, reason
    return best[0], all_conds, best[1]


def _in_scope(check_id: str, policy: Policy) -> bool:
    inc, exc = policy.scope.include, policy.scope.exclude
    if any(fnmatchcase(check_id, p) for p in exc):
        return False
    return not inc or any(fnmatchcase(check_id, p) for p in inc)


def _purposes(check: CheckDef) -> list[str]:
    return [check.purpose, *check.secondary_purposes]


def select(
    pack: Pack,
    envs: list[Environment],
    policy: Policy,
    change: Change | None = None,
    mode: Mode | str = Mode.audit,
    *,
    context: SelectionContext | None = None,
) -> Plan:
    mode = Mode(mode)
    if len(envs) != ENV_COUNT[mode]:
        raise ValueError(f"{mode.value} mode needs {ENV_COUNT[mode]} environment(s), got {len(envs)}")
    if change is not None and mode != Mode.update and context is None:
        raise ValueError("change analysis is only used in update mode")
    pack_ref = pack.ref()
    if policy.pack != pack_ref:
        raise ValueError(
            f"policy is for {policy.pack.id} rev {policy.pack.revision}, not {pack_ref.id} rev {pack_ref.revision}"
        )

    triggers: dict[str, list[str]] = defaultdict(list)
    for f in change.facts if change else []:
        for cid in f.related_checks:
            triggers[cid].append(f.id)

    items: list[PlanItem] = []
    judged_by_purpose: dict[str, list[bool]] = defaultdict(list)
    for cid in pack.manifest.checks:
        check = pack.checks[cid]
        if check.retired:
            items.append(
                PlanItem(
                    check_id=cid, check_revision=check.revision, applicability=Applicability.UNDETERMINED,
                    reason="retired check", selected=False, not_selected_reason=NotSelectedReason.RETIRED,
                    priority=check.review_order,
                )
            )
            continue

        applicability, conds, reason = evaluate_check(check, envs)
        for p in _purposes(check):
            judged_by_purpose[p].append(applicability in (Applicability.APPLICABLE, Applicability.NOT_APPLICABLE))

        trig = triggers.get(cid, [])
        not_selected: NotSelectedReason | None = None
        if not _in_scope(cid, policy):
            not_selected = NotSelectedReason.POLICY_SCOPE
        elif applicability == Applicability.NOT_APPLICABLE and (context is not None or not trig):
            not_selected = NotSelectedReason.NOT_APPLICABLE
        if trig and not_selected is None and applicability == Applicability.NOT_APPLICABLE:
            reason += f"; selected because change facts {trig} relate to it"

        # 정책 값이 있으면 타입을 실행 전에 확인(없으면 아래 NO_POLICY_VALUE 공백)
        given = policy.values.get(cid, {})
        for param in check.policy_params:
            if param.type not in SUPPORTED_POLICY_TYPES or param.key in given:
                check_policy_value(cid, param.key, param.type, given.get(param.key))

        gaps: list[Gap] = []
        if not_selected is None:
            if check.executor is None:
                gaps.append(Gap.NO_EXECUTOR)
            values = policy.values.get(cid, {})
            if any(p.required and p.key not in values for p in check.policy_params):
                gaps.append(Gap.NO_POLICY_VALUE)

        carried = None
        item_triggers = []
        if context is not None:
            item_triggers = context.triggers.get(cid, [])
            if not_selected is None and not context.full_scope and cid not in context.triggers:
                carried = context.carried_over.get(cid)
                if carried is not None:
                    not_selected = NotSelectedReason.CARRIED_OVER
                    gaps = []
                    reason += "; prior security PASS carried by exact evidence/result references"
            if context.full_scope:
                reason += f"; U1 full scope: {context.reason or context.change_type}"

        items.append(
            PlanItem(
                check_id=cid,
                check_revision=check.revision,
                applicability=applicability,
                conditions=conds,
                reason=reason,
                triggered_by_change=trig,
                selected=not_selected is None,
                not_selected_reason=not_selected,
                gaps=gaps,
                priority=check.review_order,
                executor=check.executor,
                triggers=item_triggers,
                carried_over=carried,
            )
        )

    # INV-13: 팩 REVIEWED이고 목적의 모든 점검이 적용 판단을 받았을 때만 true
    inventory = {p: pack.reviewed and all(v) for p, v in sorted(judged_by_purpose.items())}

    refs = [pack_ref, *(e.ref() for e in envs), policy.ref()]
    if change is not None:
        refs.append(change.ref())
    if context is not None:
        if context.previous_plan is not None:
            refs.append(context.previous_plan)
        for carried in context.carried_over.values():
            for ref in (carried.result, carried.evidence):
                if ref not in refs:
                    refs.append(ref)
    origin = weakest_origin([*(e.data_origin for e in envs), policy.data_origin, *([change.data_origin] if change else [])])
    return Plan(
        id=new_id("PLAN"),
        data_origin=origin,
        refs=refs,
        mode=mode,
        inputs=PlanInputs(
            pack=pack_ref,
            environments=[e.ref() for e in envs],
            policy=policy.ref(),
            change=change.ref() if change else None,
            previous_plan=context.previous_plan if context is not None else None,
        ),
        change_type=context.change_type if context is not None else None,
        items=items,
        inventory_complete=inventory,
    ).seal()
