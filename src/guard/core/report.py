"""report: PLAN + RES → RPT. 새 판정을 하지 않는다. approval은 채우지 않는다(INV-07)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from guard.models import (
    Applicability,
    DataOrigin,
    Evidence,
    ExceptionStatus,
    ExecStatus,
    Gap,
    Mode,
    NotSelectedReason,
    Pack,
    Plan,
    PolicyStatus,
    Ref,
    Result,
    ReviewStatus,
    Side,
    Verdict,
    new_id,
    weakest_origin,
)
from guard.models.objects import (
    AxisSummary,
    CheckRow,
    EvidenceCount,
    ExceptionDisplay,
    Replaced,
    FunctionalScope,
    NotSelected,
    Policy,
    Report,
    SideResult,
)
from guard.store import Store

from .aggregate import aggregate
from .compare import compare_results, comparison_differences

_IMPACT_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, None: 3}
_REVIEW_ORDER_RANK = {"reference_baseline_first": 0, "implementation_path_first": 1, "conditional_when_selected": 2}


def _exception_display(check_id: str, policy: Policy, as_of: datetime) -> tuple[ExceptionDisplay | None, list[str]]:
    shown, notes = None, []
    for exc in policy.exceptions:
        if exc.check_id != check_id:
            continue
        if not (exc.approved_by and exc.approved_at):
            notes.append(f"{check_id}: 정책 예외 미승인 — 표시하지 않음 ({exc.reason})")
        elif exc.expires_at is None or exc.expires_at <= as_of:
            notes.append(f"{check_id}: 정책 예외 만료 — 표시하지 않음 ({exc.reason})")
        else:
            shown = ExceptionDisplay(status=ExceptionStatus.APPROVED, reason=exc.reason, expires_at=exc.expires_at)
    return shown, notes


def _side(res: Result | None) -> SideResult:
    if res is None:
        return SideResult(result=None, security=Verdict.UNKNOWN)
    return SideResult(
        result=res.ref(),
        security=res.verdicts.security,
        functional=res.verdicts.functional,
        performance=res.verdicts.performance,
        verification_level=res.verification_level,
    )


def _source(evd: Evidence | None) -> str | None:
    """증거 출처: 사람이 입력(operator 있음) → manual, 실행 완료 → automated, 그 밖 → None(미실행·중단)."""
    if evd is None or evd.execution.status != ExecStatus.COMPLETED:
        return None
    return "manual" if evd.execution.operator is not None else "automated"


def _replaced(store: Store, latest: Evidence | None, src: str | None) -> Replaced | None:
    """최신 증거가 출처가 다른 이전 COMPLETED revision을 대체했는가(append-only 이력에서 찾는다)."""
    if latest is None or src is None:
        return None
    for rev in sorted(store.revisions(latest.id), reverse=True):
        if rev >= latest.revision:
            continue
        old = store.get(latest.id, rev)
        old_src = _source(old)
        if old_src is not None and old_src != src:
            reason = latest.execution.override_reason if src == "manual" else "자동 실행이 수동 증거를 대체(--replace-manual)"
            return Replaced(source=old_src, revision=rev,
                            executor=old.execution.executor.name if old.execution.executor else None, reason=reason)
    return None


def _fill_policy(note: str, values: dict) -> str:
    """점검 한계 문장의 {키}·{len:키}를 정책 값으로 채운다(값이 없으면 '정책 값 없음')."""
    import json as _json
    import re as _re

    def sub(m):
        key = m.group(2)
        if key not in values:
            return "(정책 값 없음)"
        v = values[key]
        return str(len(v)) if m.group(1) else _json.dumps(v, ensure_ascii=False)

    return _re.sub(r"\{(len:)?([A-Za-z_][A-Za-z0-9_]*)\}", sub, note)


def _functional_scope(check, evd: Evidence | None) -> FunctionalScope | None:
    """CheckDef.functional_scope가 가리키는 관측으로 "기능 확인 범위: N / 등록 경로 M (표본)"을 만든다."""
    spec = getattr(check, "functional_scope", None)
    if spec is None or evd is None:
        return None
    probed = evd.observations.get(spec.probed_obs)
    of = evd.observations.get(spec.of_obs)
    n = len(probed.value) if probed and probed.collected and probed.value is not None else 0
    m = len(of.value) if of and of.collected and of.value is not None else None
    return FunctionalScope(probed=n, of=m, label=f"기능 확인 범위: {n} / 등록 경로 {m if m is not None else '?'} (표본)")


def build_report(store: Store, pack: Pack, plan: Plan, as_of: datetime) -> Report:
    if plan.inputs.pack != pack.ref():
        raise ValueError("plan was made with a different pack revision")
    policy: Policy = store.resolve(plan.inputs.policy)
    envs = [store.resolve(r) for r in plan.inputs.environments]
    plan_ref = plan.ref()

    evds: dict[tuple[str, Side], Evidence] = {
        (e.check_id, e.side): e for e in store.find_latest("EVD", lambda e: e.plan == plan_ref)
    }
    evd_keys = {(e.id, e.revision, e.content_sha256) for e in evds.values()}
    results: dict[tuple[str, Side], Result] = {}
    for r in store.find_latest("RES", lambda r: (r.evidence.id, r.evidence.revision, r.evidence.sha256) in evd_keys):
        results[(r.check_id, r.side)] = r  # 최신 EVD revision을 가리키는 최신 RES

    sides = [Side.single] if plan.mode == Mode.audit else [Side.baseline, Side.candidate]
    limitations: list[str] = []
    rows: list[CheckRow] = []
    not_selected: list[NotSelected] = []
    manual: list[str] = []
    by_purpose: dict[str, list[Verdict]] = defaultdict(list)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"manual": 0, "automated": 0, "not_run": 0})
    by_area: dict[str, list[Verdict]] = defaultdict(list)
    area_purposes: dict[str, set[str]] = defaultdict(set)

    for item in plan.items:
        if item.not_selected_reason == NotSelectedReason.RETIRED:
            continue
        check = pack.checks[item.check_id]
        row = dict(
            check_id=check.id, purpose=check.purpose, area=check.area, question=check.question,
            review_order=check.review_order, impact=check.impact, applicability=item.applicability,
            selected=item.selected, gaps=item.gaps,
        )
        if not item.selected:
            not_selected.append(NotSelected(check_id=check.id, reason=item.not_selected_reason.value))
            rows.append(CheckRow(**row))
            if item.not_selected_reason == NotSelectedReason.NOT_APPLICABLE:
                verdict = Verdict.NOT_APPLICABLE
            else:
                continue  # 정책 범위 밖: 집계하지 않는다
        else:
            row["tested_while_undetermined"] = item.applicability in (Applicability.UNDETERMINED, Applicability.CONFLICT)
            side_results = {s: results.get((check.id, s)) for s in sides}
            for s, res in side_results.items():
                row[s.value] = _side(res)
            if plan.mode == Mode.update:
                b, c = evds.get((check.id, Side.baseline)), evds.get((check.id, Side.candidate))
                rb, rc = row["baseline"], row["candidate"]
                if b and c:
                    row["comparison"] = compare_results(rb.security, b.comparability, rc.security, c.comparability)
                    row["functional_comparison"] = compare_results(rb.functional or Verdict.UNKNOWN, b.comparability,
                                                                   rc.functional or Verdict.UNKNOWN, c.comparability)
                    row["comparison_reasons"] = comparison_differences(b.comparability, c.comparability)
            exc, notes = _exception_display(check.id, policy, as_of)
            row["exception"] = exc
            limitations.extend(notes)
            not_run = any(e.execution.status == ExecStatus.NOT_RUN for (cid, _), e in evds.items() if cid == check.id)
            if Gap.NO_EXECUTOR in item.gaps or not_run:
                manual.append(check.id)
            row["functional_scope"] = _functional_scope(check, evds.get((check.id, sides[-1])))
            latest = evds.get((check.id, sides[-1]))
            src = _source(latest)
            row["evidence_source"] = src
            row["replaced"] = _replaced(store, latest, src)
            counts[check.area][src or "not_run"] += 1
            rows.append(CheckRow(**row))
            # update 모드의 목적·영역 집계는 후보(candidate) 기준
            verdict = row[sides[-1].value].security
        for p in [check.purpose, *check.secondary_purposes]:
            by_purpose[p].append(verdict)
            area_purposes[check.area].add(p)
        by_area[check.area].append(verdict)

    inv = plan.inventory_complete
    purpose_summary = {
        p: AxisSummary(security=aggregate(vs, inv.get(p, False)), inventory_complete=inv.get(p, False))
        for p, vs in sorted(by_purpose.items())
    }
    area_summary = {}
    for a, vs in sorted(by_area.items()):
        complete = all(inv.get(p, False) for p in area_purposes[a])
        area_summary[a] = AxisSummary(security=aggregate(vs, complete), inventory_complete=complete)

    # 미충족 먼저: impact → review_order → id. 나머지는 PLAN 순서
    def is_fail(r: CheckRow) -> bool:
        return any(getattr(r, s.value) and getattr(r, s.value).security == Verdict.FAIL for s in sides)

    fails = sorted((r for r in rows if is_fail(r)),
                   key=lambda r: (_IMPACT_RANK.get(r.impact, 3), _REVIEW_ORDER_RANK.get(r.review_order, 9), r.check_id))
    rows = fails + [r for r in rows if not is_fail(r)]

    inputs: list[Ref] = [pack.ref(), *(e.ref() for e in envs), policy.ref(), plan_ref]
    if plan.inputs.change:
        inputs.append(plan.inputs.change)
    inputs += sorted((e.ref() for e in evds.values()), key=lambda r: r.id)
    inputs += sorted((r.ref() for r in results.values()), key=lambda r: r.id)

    origins = [e.data_origin for e in envs] + [policy.data_origin, plan.data_origin]
    origins += [e.data_origin for e in evds.values()] + [r.data_origin for r in results.values()]
    data_origin = weakest_origin(origins) or DataOrigin.EXAMPLE

    # INV-17 및 한계
    for e in evds.values():
        for error in e.execution.cleanup_errors:
            limitations.append(f"{e.check_id}/{e.side.value}: {error}")
    if policy.status != PolicyStatus.CONFIRMED:
        limitations.insert(0, "정책 DRAFT: 공식 아님")
    if not pack.reviewed:
        limitations.insert(0, "검토 전 팩(DRAFT): 공식 아님")
    if getattr(pack, "source", "dir") == "store":
        limitations.append(f"팩 보존본 사용(revision {pack.manifest.revision}): 현재 팩 폴더와 다른, select 시점의 팩으로 보고서를 만들었다")
    for e in envs:
        if e.runtime.platform:
            limitations.append(f"실행 환경({e.id}): {e.runtime.platform}")
        status = e.engine.source_commit_status
        if status and status != "MATCH":
            label = {"ABBREVIATED_MATCH": "축약 hash만 일치, 전체 commit 미확인", "MISMATCH": "불일치",
                     "UNVERIFIED": "일치 미확인"}[status]
            limitations.append(
                f"엔진 소스 commit({e.id}): {label} — 관측 {e.engine.source_commit or '없음'}, "
                f"고정 {e.engine.expected_source_commit or '미지정'}")
    for r in rows:
        if r.selected:
            for note in getattr(pack.checks[r.check_id], "limitations", []) or []:
                limitations.append(f"{r.check_id}: {_fill_policy(note, policy.values.get(r.check_id, {}))}")
    if DataOrigin.EXAMPLE in origins:
        limitations.append("EXAMPLE 자료 사용: 공식 판정 근거가 될 수 없음")
    undetermined = [r.check_id for r in rows if r.tested_while_undetermined]
    if undetermined:
        limitations.append(f"적용 미정 상태로 시험함: {len(undetermined)}개 점검")
    if manual:
        limitations.append(f"자동 실행 불가(수동 시험 필요): {len(manual)}개 점검")
    no_level = [k[0] for k, r in results.items() if r.verification_level is None]
    if no_level:
        limitations.append(f"검증 수준 없음(근거 누락): {len(set(no_level))}개 점검")
    incomplete = [p for p, s in purpose_summary.items() if not s.inventory_complete]
    if incomplete:
        limitations.append(f"점검 목록 미완결 보호 목적: {len(incomplete)}개 — 집계 PASS 불가")
    scope_out = [n for n in not_selected if n.reason == NotSelectedReason.POLICY_SCOPE.value]
    if scope_out:
        limitations.append(f"정책 범위 밖으로 제외: {len(scope_out)}개 점검")

    manual_n = sum(c["manual"] for c in counts.values())
    if manual_n:
        limitations.append(f"수동 증거 {manual_n}건(DECLARED): 수동 입력자는 인증되지 않은 자기 신고다(입력자 이름을 확인하지 않음)")
    replaced_rows = [r.check_id for r in rows if r.replaced is not None]
    if replaced_rows:
        limitations.append(f"출처가 다른 이전 증거를 대체한 점검 {len(replaced_rows)}건: {', '.join(replaced_rows)} (행의 '대체됨' 참고)")
    official = (
        policy.status == PolicyStatus.CONFIRMED and pack.reviewed and DataOrigin.EXAMPLE not in origins
    )
    return Report(
        id=new_id("RPT"),
        data_origin=data_origin,
        refs=[r for r in inputs if r.kind != "PACK"] + [pack.ref()],
        mode=plan.mode,
        official=official,
        inputs_manifest=inputs,
        pack_review_status=ReviewStatus.REVIEWED if pack.reviewed else ReviewStatus.DRAFT,
        policy_status=policy.status,
        by_check=rows,
        by_purpose=purpose_summary,
        by_area=area_summary,
        not_selected=not_selected,
        manual_required=manual,
        limitations=limitations,
        approval=None,
        evidence_counts={a: EvidenceCount(**c) for a, c in sorted(counts.items())},
    ).seal()
