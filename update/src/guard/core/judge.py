"""judge: EVD → RES. 엔진에 접속하지 않는다. 저장된 증거만으로 언제든 다시 판정한다(재판정).

코어가 플러그인과 별개로 강제하는 것:
- INV-11 PASS 관문: 실행 완료, 정상 대조 성공, 모든 챌린지 시도, 필수 관측 전부 수집.
- INV-04 확인된 FAIL은 관문과 무관하게 유지.
- INV-15 플러그인 실패·이상 출력은 증거 부족으로 남긴다.
- INV-08 예외는 판정을 바꾸지 않고 표시만. 만료·미승인은 표시하지 않고 사유를 남긴다.
- 계획에 따른 보안 판정은 PASS/FAIL과 구조화된 실패 사유를 쓴다. 과거 판정 재생에는 기존 의미를 유지한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from guard.judges import registry
from guard.judges.base import AXES
from guard.models import (
    CheckDef,
    DataOrigin,
    Evidence,
    ExecStatus,
    Mode,
    Pack,
    Plan,
    Policy,
    Result,
    SecurityFailureTag,
    Verdict,
    canonical_json,
    new_id,
    sha256_hex,
    weakest_origin,
)
from guard.models.objects import ExceptionMark, JudgeInfo, Verdicts
from guard.store import Store

_ALLOWED = {Verdict.PASS, Verdict.FAIL, Verdict.UNKNOWN}
_PLUGIN_FAILURE_TAGS = {
    SecurityFailureTag.CONDITIONS_UNMET,
    SecurityFailureTag.INSUFFICIENT_EVIDENCE,
    SecurityFailureTag.NO_PASS_RULE,
}
_INCOMPLETE_EXECUTION_TAGS = {
    ExecStatus.NOT_RUN: SecurityFailureTag.NOT_RUN,
    ExecStatus.ABORTED: SecurityFailureTag.ABORTED,
}


def pass_gate(check: CheckDef, evd: Evidence) -> list[str]:
    """INV-11: PASS를 막는 사유 목록. 비어 있으면 관문 통과."""
    return _pass_gate(check, evd)[0]


def _pass_gate(check: CheckDef, evd: Evidence) -> tuple[list[str], set[SecurityFailureTag]]:
    blocks: list[str] = []
    tags: set[SecurityFailureTag] = set()
    if evd.execution.status != ExecStatus.COMPLETED:
        blocks.append(f"execution {evd.execution.status.value}: {evd.execution.abort_reason or ''}".rstrip(": "))
        tags.add(_INCOMPLETE_EXECUTION_TAGS.get(evd.execution.status, SecurityFailureTag.INSUFFICIENT_EVIDENCE))
    nc = evd.normal_control
    if not nc.attempted:
        blocks.append("normal control not attempted")
        tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)
    elif nc.succeeded is not True:
        blocks.append("normal control did not succeed (over-blocking or unknown)")
        tags.add(SecurityFailureTag.CONDITIONS_UNMET if nc.succeeded is False
                 else SecurityFailureTag.INSUFFICIENT_EVIDENCE)
    steps = {c.step: c for c in evd.challenges}
    not_attempted = [s for s in check.challenge_steps if s not in steps or not steps[s].attempted]
    if not_attempted:
        blocks.append(f"challenges not attempted: {not_attempted}")
        tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)
    if not evd.challenges and check.challenge_steps:
        blocks.append("no challenge records")
    missing = [o.key for o in check.required_observations if not (o.key in evd.observations and evd.observations[o.key].collected)]
    if missing:
        blocks.append(f"required observations not collected: {missing}")
        tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)
    return blocks, tags


def _exception(check_id: str, policy: Policy, as_of: datetime) -> tuple[ExceptionMark | None, list[str]]:
    mark, reasons = None, []
    for exc in policy.exceptions:
        if exc.check_id != check_id:
            continue
        reason = exc.unavailable_reason(as_of)
        if reason is not None:
            detail = {"unapproved": "not approved", "expired": f"expired at {exc.expires_at}"}.get(reason, reason)
            reasons.append(f"policy exception not shown: {detail} ({exc.reason})")
        elif exc.expires_at is not None:
            mark = ExceptionMark(check_id=check_id, expires_at=exc.expires_at)
    return mark, reasons


def judge_evidence(
    check: CheckDef,
    evd: Evidence,
    policy: Policy,
    as_of: datetime,
    condition_origins: Iterable[DataOrigin | None] | None = None,
    plan: Plan | None = None,
    *,
    carryover: bool = False,
    binary_security: bool | None = None,
) -> Result:
    if evd.check_id != check.id:
        raise ValueError(f"evidence is for {evd.check_id}, not {check.id}")
    if as_of.utcoffset() is None:
        raise ValueError("JUDGMENT_TIMEZONE_REQUIRED")
    as_of = as_of.astimezone(timezone.utc)
    if binary_security is None:
        binary_security = plan is not None

    reasons: list[str] = []
    missing = sorted(
        o.key for o in check.required_observations if not (o.key in evd.observations and evd.observations[o.key].collected)
    )
    verdicts = {axis: Verdict.UNKNOWN for axis in AXES}
    blocks, security_tags = _pass_gate(check, evd)
    judge_info = JudgeInfo(name="none", version="0", params_sha256=sha256_hex(canonical_json({})))

    # 판정기: 증거에 기록된 실행기의 기본 판정기가 있으면 그것, 없으면 CheckDef.judge
    executor = evd.execution.executor
    if executor is not None and executor.default_judge:
        plugin_name, params = executor.default_judge, {}
    elif check.judge is not None:
        plugin_name, params = check.judge.plugin, check.judge.params
    else:
        plugin_name, params = None, {}

    if plugin_name is None:
        security_tags.add(SecurityFailureTag.NO_PASS_RULE)
        reasons.append("no judge defined: manual judgment required")
        if evd.execution.status != ExecStatus.COMPLETED:
            reasons.append(f"execution {evd.execution.status.value}: {evd.execution.abort_reason}")
    else:
        judge_info = JudgeInfo(
            name=plugin_name, version="?", params_sha256=sha256_hex(canonical_json(params))
        )
        plugin = registry.JUDGES.get(plugin_name)
        if plugin is None:
            security_tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)
            reasons.append(f"judge plugin {plugin_name!r} not available")
        else:
            judge_info = judge_info.model_copy(update={"name": plugin.name, "version": plugin.version})
            try:
                out = plugin.judge(check, evd, params, policy.values.get(check.id, {}))
                for axis in AXES:
                    v = Verdict(out.verdicts.get(axis, Verdict.UNKNOWN))
                    if v not in _ALLOWED:
                        reasons.append(f"{axis}: plugin returned {v.value}; treated as UNKNOWN")
                        v = Verdict.UNKNOWN
                    verdicts[axis] = v
                    if binary_security and axis == "security":
                        if v == Verdict.FAIL:
                            confirmed = getattr(out, "security_violation_confirmed", None)
                            security_tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE if confirmed is False
                                              else SecurityFailureTag.CONFIRMED_VIOLATION)
                        security_tags.update(tag for tag in getattr(out, "security_failure_tags", ())
                                             if tag in _PLUGIN_FAILURE_TAGS)
                reasons.extend(out.reasons)
                missing = sorted(set(missing) | set(out.missing_observations))
            except Exception:  # noqa: BLE001 - INV-15
                # Never serialize arbitrary plugin exceptions into evidence reports.
                reasons.append("JUDGE_PLUGIN_FAILURE")
                security_tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)
                # A later invalid field must not leave an earlier PASS in place.
                # Already obtained FAIL remains independent of that collection gap.
                for axis in AXES:
                    if verdicts[axis] == Verdict.PASS:
                        verdicts[axis] = Verdict.UNKNOWN

        for axis in AXES:
            if verdicts[axis] == Verdict.PASS and blocks:
                verdicts[axis] = Verdict.UNKNOWN
                reasons.append(f"{axis}: PASS withheld: {'; '.join(blocks)}")
            elif verdicts[axis] == Verdict.FAIL and blocks:
                reasons.append(f"{axis}: FAIL kept despite: {'; '.join(blocks)}")

    if binary_security and (verdicts["security"] != Verdict.PASS or security_tags):
        if verdicts["security"] != Verdict.FAIL:
            detail = f" ({', '.join(blocks)})" if blocks else ""
            reasons.append(f"security: FAIL because PASS requirements were not established{detail}")
        verdicts["security"] = Verdict.FAIL
        if not security_tags:
            security_tags.add(SecurityFailureTag.INSUFFICIENT_EVIDENCE)

    if evd.comparability.cpu is not None:
        verdicts["performance"] = Verdict.UNKNOWN
        reasons.append("performance: explicit CPU comparison covers security/function only; performance unverified")

    mark, exc_reasons = _exception(check.id, policy, as_of)
    reasons.extend(exc_reasons)

    executed = evd.execution.status == ExecStatus.COMPLETED
    origins = [evd.data_origin if executed else None, *(condition_origins or [])]
    level = weakest_origin(origins)
    data_origin = weakest_origin([evd.data_origin, policy.data_origin, *([plan.data_origin] if plan else [])])

    refs = [evd.ref(), policy.ref()]
    return Result(
        id=new_id("RES"),
        data_origin=data_origin,
        refs=refs,
        evidence=evd.ref(),
        check_id=check.id,
        side=evd.side,
        judge=judge_info,
        verdicts=Verdicts(**verdicts),
        reasons=reasons,
        missing_observations=missing,
        exception=mark,
        verification_level=level,
        as_of=as_of,
        evaluation_mode="carryover" if carryover else None,
        security_verdict_version="binary-v1" if binary_security else None,
        security_failure_tags=sorted(security_tags) if binary_security else [],
    ).seal()


_VOLATILE = {"id", "revision", "created_at", "content_sha256"}


def _same_content(a: Result, b: Result) -> bool:
    da = {k: v for k, v in a.to_dict().items() if k not in _VOLATILE}
    db = {k: v for k, v in b.to_dict().items() if k not in _VOLATILE}
    return da == db


def plan_evidence(store: Store, plan: Plan, attempt_id: str | None = None) -> tuple[str | None, list[Evidence]]:
    plan_ref = plan.ref()
    if attempt_id is not None:
        reports = store.find_latest("RPT", lambda r: r.execution_attempt_id == attempt_id and plan_ref in r.inputs_manifest)
        if reports:
            # The first report records the selected snapshot, including DECLARED
            # manual evidence intentionally retained by the run.
            original = min(reports, key=lambda r: (r.created_at, r.id))
            store.validate_object(original)
            evds = [store.resolve(ref) for ref in original.inputs_manifest if ref.kind == "EVD"]
            return attempt_id, [evd for evd in evds if evd.plan == plan_ref]
        evidence = store.find_latest("EVD", lambda e: e.plan == plan_ref and e.execution.attempt_id == attempt_id)
        if not evidence:
            raise ValueError("ATTEMPT_NOT_FOUND")
        return attempt_id, evidence
    evidence = store.find_latest("EVD", lambda e: e.plan == plan_ref)
    if plan.mode == Mode.update:
        attempted = [e for e in evidence if e.execution.attempt_id is not None]
        if attempted:
            latest = max(attempted, key=lambda e: (e.created_at, e.execution.attempt_id))
            attempt_id = latest.execution.attempt_id
            evidence = [e for e in evidence if e.execution.attempt_id == attempt_id or e.execution.operator is not None]
    return attempt_id, evidence


def result_for_policy(result, policy_ref) -> bool:
    policies = [ref for ref in result.refs if ref.kind == "POL"]
    return result.evaluation_mode is None and (not policies or policies == [policy_ref])


def judge_plan(store: Store, pack: Pack, plan: Plan, as_of: datetime, *,
               attempt_id: str | None = None, evidence: Iterable[Evidence] | None = None,
               binary_security: bool = True) -> list[Result]:
    """Judge one coherent evidence snapshot while protecting its result revisions."""
    from guard.core.execution_recipe import execution_lock
    with execution_lock(store.root, plan.id):
        return judge_plan_locked(store, pack, plan, as_of, attempt_id=attempt_id, evidence=evidence,
                                 binary_security=binary_security)


def judge_plan_locked(store: Store, pack: Pack, plan: Plan, as_of: datetime, *,
                      attempt_id: str | None = None, evidence: Iterable[Evidence] | None = None,
                      binary_security: bool = True) -> list[Result]:
    if plan.inputs.pack != pack.ref():
        raise ValueError("plan was made with a different pack revision")
    policy = store.resolve(plan.inputs.policy)
    evds = list(evidence) if evidence is not None else plan_evidence(store, plan, attempt_id)[1]
    plan_ref = plan.ref()
    if len({(evd.check_id, evd.side) for evd in evds}) != len(evds):
        raise ValueError("EVIDENCE_SLOT_AMBIGUOUS")
    for evd in evds:
        if evd.plan != plan_ref or not plan.item(evd.check_id).selected:
            raise ValueError("EVIDENCE_PLAN_SCOPE_MISMATCH")
        store.validate_object(evd)
    evd_refs = {evd.id: evd.ref() for evd in evds}
    prior: dict[str, Result] = {}
    same_time: dict[str, Result] = {}
    security_version = "binary-v1" if binary_security else None
    for result in store.iter_kind("RES") if evd_refs else ():
        eid = result.evidence.id
        if eid not in evd_refs or not result_for_policy(result, plan.inputs.policy):
            continue
        if eid not in prior or result.revision > prior[eid].revision:
            prior[eid] = result
        if (result.evidence == evd_refs[eid] and result.as_of == as_of
                and result.security_verdict_version == security_version):
            if eid not in same_time or result.revision > same_time[eid].revision:
                same_time[eid] = result
    out = []
    for evd in sorted(evds, key=lambda e: (e.check_id, e.side.value)):
        check = pack.checks[evd.check_id]
        item = plan.item(evd.check_id)
        res = judge_evidence(
            check, evd, policy, as_of, condition_origins=[c.origin for c in item.conditions], plan=plan,
            binary_security=binary_security,
        )
        recorded = same_time.get(evd.id)
        if recorded is not None and _same_content(recorded, res):
            store.validate_object(recorded)
            out.append(recorded)
            continue
        prev = prior.get(evd.id)
        if prev is not None:
            res = res.model_copy(update={"id": prev.id, "revision": prev.revision + 1}).seal()
        store.put(res)
        out.append(res)
    return out
