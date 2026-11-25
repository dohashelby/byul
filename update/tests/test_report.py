"""7단계: RES → RPT(JSON). 보고서 화면은 BYUL platform의 책임이다."""

import json

from guard.core.report import build_report
from guard.models import SecurityFailureTag, Verdict
from tests.builders import T0, baseline_pack
from tests.report_setup import pipeline

PURPOSE = "요청·대상 접근 권한"


def _purpose_fixture():
    """집계 테스트가 의도한 그룹을 원본 체크리스트의 명칭과 독립적으로 둔다.

    REQ-01의 FAIL·PASS와 같은 목적에 속한 미정 REQ-02를 대조한다.
    실제 원본의 목적 전달은 importer 계약 테스트가 별도로 검증한다.
    """
    pack = baseline_pack()
    for cid in ("REQ-01", "REQ-02"):
        pack.checks[cid] = pack.checks[cid].model_copy(update={"purpose": PURPOSE, "secondary_purposes": []})
    return pack


def test_full_pack_report_structure(tmp_path):
    store, pack, plan = pipeline(tmp_path, pack=_purpose_fixture())
    rpt = build_report(store, pack, plan, as_of=T0)
    store.put(rpt)
    rows = {r.check_id: r for r in rpt.by_check}
    assert len(rows) == 77
    assert rows["REQ-01"].single.security == Verdict.PASS
    assert rows["SUP-01"].single.security == Verdict.FAIL
    assert SecurityFailureTag.NOT_RUN in rows["SUP-01"].single.security_failure_tags
    assert rows["SUP-01"].tested_while_undetermined
    assert len(rpt.manual_required) == 76 and "REQ-01" not in rpt.manual_required
    assert rpt.by_purpose[PURPOSE].security == Verdict.FAIL  # 선택된 REQ-02에 근거가 없다.
    assert rpt.by_purpose[PURPOSE].inventory_complete is False
    assert set(rpt.by_area) == {"SUP", "REQ", "PLT", "LIF", "OBS"}
    refs = {(r.kind, r.id) for r in rpt.inputs_manifest}
    assert ("PLAN", plan.id) in refs and ("PACK", "PACK-BASELINE") in refs
    assert sum(1 for r in rpt.inputs_manifest if r.kind == "RES") == 77


def test_fail_propagates_to_purpose_and_area(tmp_path):
    store, pack, plan = pipeline(tmp_path, scenario="direct_200", include=["REQ-*"], pack=_purpose_fixture())
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.by_purpose[PURPOSE].security == Verdict.FAIL
    assert rpt.by_area["REQ"].security == Verdict.FAIL
    assert rpt.by_check[0].check_id == "REQ-01"  # 미충족이 맨 앞
    assert len(rpt.not_selected) == 56 and all(n.reason == "POLICY_SCOPE" for n in rpt.not_selected)


def test_scope_excluded_checks_not_aggregated(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], pack=_purpose_fixture())
    rpt = build_report(store, pack, plan, as_of=T0)
    assert [r.check_id for r in rpt.by_check if r.selected] == ["REQ-01"]
    # 정책 범위 밖만 있는 영역·목적은 집계하지 않는다(빈 집계로 판정을 만들지 않음)
    assert set(rpt.by_area) == {"REQ"}
    assert set(rpt.by_purpose) == {PURPOSE}
    assert rpt.by_purpose[PURPOSE].security == Verdict.UNKNOWN  # DRAFT 팩 → inventory 미완결


def test_approved_exception_displayed_without_changing_verdict(tmp_path):
    from datetime import timedelta

    exc = [{"check_id": "REQ-01", "reason": "임시", "approved_by": "ciso", "approved_at": T0 - timedelta(days=1),
            "expires_at": T0 + timedelta(days=1)}]
    store, pack, plan = pipeline(tmp_path, scenario="direct_200", include=["REQ-01"], exceptions=exc)
    rpt = build_report(store, pack, plan, as_of=T0)
    row = rpt.by_check[0]
    assert row.single.security == Verdict.FAIL and row.exception.status == "APPROVED"


def test_report_uses_latest_result_revision_pinned(tmp_path):
    from guard.core.judge import judge_plan
    from guard.core.run import RunConfig, run_plan

    store, pack, plan = pipeline(tmp_path, include=["REQ-01"])
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}, raw={"fake": {"scenario": "direct_200"}}))
    judge_plan(store, pack, plan, as_of=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    row = rpt.by_check[0]
    assert row.single.security == Verdict.FAIL
    assert store.resolve(row.single.result).verdicts.security == Verdict.FAIL


def test_functional_scope_is_displayed(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"])
    rpt = build_report(store, pack, plan, as_of=T0)
    (row,) = [r for r in rpt.by_check if r.check_id == "REQ-01"]
    assert row.functional_scope.probed == 1 and row.functional_scope.of == 3
    assert row.functional_scope.label == "기능 확인 범위: 1 / 등록 경로 3 (표본)"
    # 기능 범위 정의가 없는 점검에는 표시하지 않는다
    store2, pack2, plan2 = pipeline(tmp_path / "b", include=["SUP-01"])
    (sup,) = [r for r in build_report(store2, pack2, plan2, as_of=T0).by_check if r.check_id == "SUP-01"]
    assert sup.functional_scope is None


def test_fail_and_unknown_reasons_are_copied_from_result(tmp_path):
    """FAIL·UNKNOWN 축의 판정 이유는 RES.reasons 원문 그대로 보고서 행에 나온다(PASS 축 이유는 뺀다)."""
    store, pack, plan = pipeline(tmp_path, scenario="direct_200", include=["REQ-01"])
    rpt = build_report(store, pack, plan, as_of=T0)
    row = rpt.by_check[0]
    res = store.resolve(row.single.result)
    assert row.single.security == Verdict.FAIL
    passed = tuple(f"{axis}:" for axis in ("security", "functional", "performance")
                   if getattr(res.verdicts, axis) not in (Verdict.FAIL, Verdict.UNKNOWN))
    assert row.single.reasons == [r for r in res.reasons if not r.startswith(passed)]
    assert any(r.startswith("security: FAIL because") for r in row.single.reasons)
    assert json.loads(json.dumps(rpt.to_dict()))["by_check"][0]["single"]["reasons"] == row.single.reasons


def test_reasons_omitted_when_every_axis_settled():
    from types import SimpleNamespace

    from guard.core.report import _verdict_reasons
    from guard.models.objects import SideResult

    settled = SimpleNamespace(verdicts=SimpleNamespace(security=Verdict.PASS, functional=Verdict.PASS,
                                                       performance=Verdict.NOT_APPLICABLE),
                              reasons=["security: all pass rules met: x eq True"])
    assert _verdict_reasons(settled) is None
    mixed = SimpleNamespace(verdicts=SimpleNamespace(security=Verdict.PASS, functional=Verdict.PASS,
                                                     performance=Verdict.UNKNOWN),
                            reasons=["security: all pass rules met: x eq True", "performance: no rule for this axis",
                                     "execution ABORTED: target lost"])
    assert _verdict_reasons(mixed) == ["performance: no rule for this axis", "execution ABORTED: target lost"]
    # 이유가 없으면 직렬화에서 빠져 기존 RPT 해시가 그대로다
    assert "reasons" not in SideResult(security=Verdict.PASS).model_dump(mode="json")


def test_missing_evd_and_res_close_selected_update_sides_as_fail(tmp_path):
    from guard.core.select import select
    from guard.store import Store
    from tests.builders import environment, policy

    store = Store(tmp_path / ".guard")
    pack = baseline_pack()
    envs = [
        environment({"gateway": {"value": True, "origin": "DECLARED", "source": "test"}},
                    id=f"ENV-SYNTHETIC-{side}", engine={"name": "vllm", "version": f"test-{side}"})
        for side in ("baseline", "candidate")
    ]
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]})
    for obj in [*envs, pol]:
        store.put(obj)
    plan = select(pack, envs, pol, mode="update")
    store.put(plan)

    report = build_report(store, pack, plan, as_of=T0)
    store.put(report)
    (row,) = [r for r in report.by_check if r.selected]
    assert report.security_verdict_version == "binary-v1"
    assert row.comparison == "NOT_COMPARABLE"
    assert row.functional_comparison is None
    for side in (row.baseline, row.candidate):
        assert side.security == Verdict.FAIL
        assert side.security_failure_tags == [SecurityFailureTag.INSUFFICIENT_EVIDENCE]
        assert side.result is None and side.evidence_ref is None and side.execution_status is None
    assert report.evidence_counts["REQ"].not_run == 1
    assert report.by_area["REQ"].security == Verdict.FAIL

    legacy = build_report(store, pack, plan, as_of=T0, binary_security=False)
    store.put(legacy)
    (old_row,) = [r for r in legacy.by_check if r.selected]
    assert "security_verdict_version" not in legacy.to_dict()
    assert old_row.comparison is None
    assert old_row.baseline.security == old_row.candidate.security == Verdict.UNKNOWN
    assert "security_failure_tags" not in old_row.candidate.model_dump(mode="json")
