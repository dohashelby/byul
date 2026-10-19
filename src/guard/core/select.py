"""select: ENV + POL (+ CHG) + 팩 → PLAN (SPEC §4.4). 시험을 실행하지 않는다."""

from __future__ import annotations

from collections import defaultdict
from fnmatch import fnmatchcase
from typing import Any

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

ENV_COUNT = {Mode.audit: 1, Mode.update: 2}


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
) -> Plan:
    mode = Mode(mode)
    if len(envs) != ENV_COUNT[mode]:
        raise ValueError(f"{mode.value} mode needs {ENV_COUNT[mode]} environment(s), got {len(envs)}")
    if change is not None and mode != Mode.update:
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
        elif applicability == Applicability.NOT_APPLICABLE and not trig:
            not_selected = NotSelectedReason.NOT_APPLICABLE
        if trig and not_selected is None and applicability == Applicability.NOT_APPLICABLE:
            reason += f"; selected because change facts {trig} relate to it"

        gaps: list[Gap] = []
        if not_selected is None:
            if check.executor is None:
                gaps.append(Gap.NO_EXECUTOR)
            values = policy.values.get(cid, {})
            if any(p.required and p.key not in values for p in check.policy_params):
                gaps.append(Gap.NO_POLICY_VALUE)

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
            )
        )

    # INV-13: 팩 REVIEWED이고 목적의 모든 점검이 적용 판단을 받았을 때만 true
    inventory = {p: pack.reviewed and all(v) for p, v in sorted(judged_by_purpose.items())}

    refs = [pack_ref, *(e.ref() for e in envs), policy.ref()]
    if change is not None:
        refs.append(change.ref())
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
        ),
        items=items,
        inventory_complete=inventory,
    ).seal()
