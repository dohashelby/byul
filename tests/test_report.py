"""7단계: RES → RPT(JSON) → HTML."""

import json

from guard.core.report import build_report
from guard.models import Verdict
from guard.report.html import render_html
from tests.builders import T0
from tests.report_setup import pipeline

PURPOSE = "요청·대상 접근 권한"


def test_full_pack_report_structure(tmp_path):
    store, pack, plan = pipeline(tmp_path)
    rpt = build_report(store, pack, plan, as_of=T0)
    store.put(rpt)
    rows = {r.check_id: r for r in rpt.by_check}
    assert len(rows) == 77
    assert rows["REQ-01"].single.security == Verdict.PASS
    assert rows["SUP-01"].single.security == Verdict.UNKNOWN
    assert rows["SUP-01"].tested_while_undetermined
    assert len(rpt.manual_required) == 76 and "REQ-01" not in rpt.manual_required
    # DRAFT 팩 → inventory 미완결 → 목적·영역은 PASS가 될 수 없다 (INV-13)
    assert rpt.by_purpose[PURPOSE].security == Verdict.UNKNOWN
    assert rpt.by_purpose[PURPOSE].inventory_complete is False
    assert set(rpt.by_area) == {"SUP", "REQ", "PLT", "LIF", "OBS"}
    refs = {(r.kind, r.id) for r in rpt.inputs_manifest}
    assert ("PLAN", plan.id) in refs and ("PACK", "PACK-BASELINE") in refs
    assert sum(1 for r in rpt.inputs_manifest if r.kind == "RES") == 77


def test_fail_propagates_to_purpose_and_area(tmp_path):
    store, pack, plan = pipeline(tmp_path, scenario="direct_200", include=["REQ-*"])
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.by_purpose[PURPOSE].security == Verdict.FAIL
    assert rpt.by_area["REQ"].security == Verdict.FAIL
    assert rpt.by_check[0].check_id == "REQ-01"  # 미충족이 맨 앞
    assert len(rpt.not_selected) == 56 and all(n.reason == "POLICY_SCOPE" for n in rpt.not_selected)


def test_scope_excluded_checks_not_aggregated(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"])
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
    html = render_html(rpt.to_dict())
    assert "예외 승인됨" in html


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


def test_html_is_rendered_only_from_json(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"])
    data = json.loads(json.dumps(build_report(store, pack, plan, as_of=T0).to_dict()))
    html = render_html(data)
    assert html == render_html(data)  # 결정적
    assert "REQ-01" in html and "충족" in html and "EXAMPLE" in html
    assert "<script" not in html.lower()
