"""수동 증거 입력(A): guard evidence template / import. 사람의 판정 = 신고된 관측, 판정기가 PASS 관문을 적용한다."""

import json

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.manual import ManualEvidenceError, build_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.models import DataOrigin, ExecStatus, Verdict
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy
from tests.prepared_fixtures import copy_workdir, dump_fixture_yaml, manual_setup_seed

D = {"origin": "DECLARED", "source": "env.yaml"}


def _setup(tmp_path, include=("SUP-01", "REQ-01")):
    pack = baseline_pack()
    env = environment({"gateway": {"value": True, **D}})
    pol = policy(pack.ref(), scope={"include": list(include), "exclude": []},
                 values={"REQ-01": {"allowed_callers": ["allowed"]},
                         "SUP-01": {"approval_record_system": "EXAMPLE approval registry"},
                         "SUP-02": {"approval_record_system": "EXAMPLE approval registry"}})
    config = RunConfig(executors={"direct_route": "fake"}, adapter="fake")

    def create(folder):
        store = Store(folder / ".guard")
        store.put(env); store.put(pol)
        plan = select(pack, [env], pol); store.put(plan)
        run_plan(store, pack, plan, config)
        return store, plan, {}

    store, plan, _ = manual_setup_seed(
        family="manual-evidence",
        parameters={"pack": pack.model_dump(mode="json"),
                    "environment": env.to_dict(), "policy": pol.to_dict(),
                    "mode": "audit", "scope": list(include),
                    "run_config": {"executors": config.executors, "adapter": config.adapter, "raw": config.raw}},
        folder=tmp_path, create=create, store_directory=".guard")
    return store, pack, plan


def _fill(entry, verdict="PASS", attach=True, steps=True, normal="succeeded", by="tester-a", answer=True):
    entry["verdicts"]["security"] = verdict
    entry["verdicts"]["functional"] = "PASS"
    entry["normal_control"]["result"] = normal
    for c in entry["challenges"]:
        c["attempted"] = steps
    for o in entry["observation_notes"]:
        o["answer"] = "확인함: 무해 표식 요청으로 확인" if answer else None
    entry["basis"] = "제출자 계정으로 승인 기록 변경을 시도했고 거부됨"
    entry["entered_by"] = by
    entry["attachments"] = ["evidence/approval-log.txt"] if attach else []
    return entry


def _write(tmp_path, doc):
    (tmp_path / "evidence").mkdir(exist_ok=True)
    (tmp_path / "evidence/approval-log.txt").write_text("2026-10-19 submitter write denied\n", encoding="utf-8")
    p = tmp_path / "filled.yaml"
    p.write_text(dump_fixture_yaml(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return p


def _entry(doc, cid):
    return next(e for e in doc["checks"] if e["check_id"] == cid)


def test_template_lists_manual_checks_with_prose(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    ids = [e["check_id"] for e in doc["checks"]]
    assert ids == ["SUP-01"]  # 자동 실행기가 있는 REQ-01은 기본 제외
    e = _entry(doc, "SUP-01")
    assert e["normal_control"]["description"] == pack.checks["SUP-01"].normal_control
    assert [c["step"] for c in e["challenges"]] == pack.checks["SUP-01"].challenge_steps
    assert [o["item"] for o in e["observation_notes"]] == pack.checks["SUP-01"].required_observations_prose
    assert e["verdicts"] == {"security": None, "functional": None, "performance": None}
    assert doc["plan"] == {"id": plan.id, "revision": plan.revision, "sha256": plan.content_sha256}
    with_auto = build_template(store, pack, plan, include_automated=True)
    assert _entry(with_auto, "REQ-01")["automated"] is True


def test_import_creates_declared_evidence_and_pass(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    _fill(_entry(doc, "SUP-01"))
    summary = import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    assert summary["imported"] == ["SUP-01"]
    evds = [e for e in store.iter_kind("EVD") if e.check_id == "SUP-01"]
    latest = max(evds, key=lambda e: e.revision)
    assert latest.revision == 2 and min(e.revision for e in evds) == 1  # NOT_RUN 기록 위에 새 revision
    assert latest.data_origin == DataOrigin.DECLARED  # INV-02
    assert latest.execution.operator.by == "tester-a" and latest.execution.operator.at == T0
    assert latest.execution.executor.default_judge == "declared"
    assert len(latest.artifacts) == 1
    (res,) = [r for r in judge_plan(store, pack, plan, as_of=T0) if r.check_id == "SUP-01"]
    assert res.verdicts.security == Verdict.PASS and res.verification_level == DataOrigin.DECLARED


@pytest.mark.parametrize("kw, reason", [
    ({"attach": False}, "attachment"),             # (a) PASS인데 첨부 증거 없음
    ({"steps": False}, "challenges not attempted"),  # 관문: 챌린지 미시도
    ({"normal": "failed"}, "normal control"),     # 관문: 정상 대조 실패
    ({"answer": False}, "observation"),           # 필수 관측 설명 미기입
])
def test_declared_pass_is_downgraded_without_basis(tmp_path, kw, reason):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    _fill(_entry(doc, "SUP-01"), **kw)
    import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    (res,) = [r for r in judge_plan(store, pack, plan, as_of=T0) if r.check_id == "SUP-01"]
    assert res.verdicts.security == Verdict.UNKNOWN
    assert any(reason in r for r in res.reasons), res.reasons


def test_declared_fail_kept_even_without_attachment(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    _fill(_entry(doc, "SUP-01"), verdict="FAIL", attach=False, steps=False)
    import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    (res,) = [r for r in judge_plan(store, pack, plan, as_of=T0) if r.check_id == "SUP-01"]
    assert res.verdicts.security == Verdict.FAIL  # 확인된 FAIL은 다른 누락으로 지우지 않는다(INV-04)


def test_import_validation_is_all_or_nothing(tmp_path):
    store, pack, plan = _setup(tmp_path)
    before = len(list(store.iter_kind("EVD")))
    doc = build_template(store, pack, plan, include_automated=True)
    _fill(_entry(doc, "SUP-01"))
    _fill(_entry(doc, "REQ-01"))  # 자동 실행기가 있는데 override_reason 없음
    with pytest.raises(ManualEvidenceError, match="REQ-01.*override"):
        import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    assert len(list(store.iter_kind("EVD"))) == before  # 하나도 쓰지 않음


@pytest.mark.parametrize("mutate, match", [
    (lambda e: e.update(entered_by=""), "entered_by"),
    (lambda e: e["verdicts"].update(security="MAYBE"), "verdict"),
    (lambda e: e["challenges"].append({"step": "만든 단계", "attempted": True}), "challenge"),
    (lambda e: e.update(attachments=["evidence/none.txt"]), "attachment"),
    (lambda e: e.update(check_id="OBS-12"), "not selected"),  # INV-10
])
def test_import_rejects_bad_entries(tmp_path, mutate, match):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    e = _fill(_entry(doc, "SUP-01"))
    mutate(e)
    with pytest.raises(ManualEvidenceError, match=match):
        import_evidence(store, pack, _write(tmp_path, doc), now=T0)


def test_unfilled_entries_are_skipped(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    summary = import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    assert summary["imported"] == [] and summary["skipped"] == ["SUP-01"]


def test_override_replaces_automated_and_report_marks_it(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan, include_automated=True)
    e = _fill(_entry(doc, "REQ-01"), verdict="FAIL")
    e["override_reason"] = "실험실 밖 운영 Gateway에서 수동 확인"
    for o in e["required_observations"]:
        o["collected"] = False
    import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    judge_plan(store, pack, plan, as_of=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    (row,) = [r for r in rpt.by_check if r.check_id == "REQ-01"]
    assert row.evidence_source == "manual" and row.replaced.source == "automated"
    assert row.replaced.reason == "실험실 밖 운영 Gateway에서 수동 확인"
    assert row.single.security == Verdict.FAIL


def test_run_does_not_silently_overwrite_manual_evidence(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    _fill(_entry(doc, "SUP-01"))
    import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}, adapter="fake"))  # 다시 실행
    latest = max((e for e in store.iter_kind("EVD") if e.check_id == "SUP-01"), key=lambda e: e.revision)
    assert latest.execution.operator is not None  # 수동 증거 유지(NOT_RUN으로 덮지 않음)
    run_plan(store, pack, plan, RunConfig(executors={"direct_route": "fake"}, adapter="fake", replace_manual=True))
    latest = max((e for e in store.iter_kind("EVD") if e.check_id == "SUP-01"), key=lambda e: e.revision)
    assert latest.execution.status == ExecStatus.NOT_RUN  # 명시적으로 요청했을 때만 대체


def test_report_counts_and_limitations(tmp_path):
    store, pack, plan = _setup(tmp_path)
    doc = build_template(store, pack, plan)
    _fill(_entry(doc, "SUP-01"))
    import_evidence(store, pack, _write(tmp_path, doc), now=T0)
    judge_plan(store, pack, plan, as_of=T0)
    rpt = build_report(store, pack, plan, as_of=T0)
    assert rpt.evidence_counts["SUP"].manual == 1 and rpt.evidence_counts["REQ"].automated == 1
    assert any("인증되지 않은 자기 신고" in l for l in rpt.limitations)
    from guard.report.html import render_html

    html = render_html(rpt.to_dict())
    assert "수동 입력 1건 / 자동 측정 0건" in html and "수동 입력 0건 / 자동 측정 1건" in html


def test_cli_template_and_import(tmp_path, monkeypatch):
    copy_workdir(tmp_path)
    policy_path = tmp_path / "examples/policy.yaml"
    fixture = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    fixture.setdefault("values", {})["SUP-01"] = {"approval_record_system": "EXAMPLE approval registry"}
    policy_path.write_text(yaml.safe_dump(fixture), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    out = runner.invoke(app, ["select", "--pack", "packs/PACK-BASELINE", "--env", "examples/env.yaml",
                              "--policy", "examples/policy.yaml"]).output
    plan = out.strip().splitlines()[-1]
    assert runner.invoke(app, ["run", "--plan", plan]).exit_code == 0
    r = runner.invoke(app, ["evidence", "template", "--plan", plan, "--out", "manual.yaml"])
    assert r.exit_code == 0, r.output
    doc = yaml.safe_load((tmp_path / "manual.yaml").read_text(encoding="utf-8"))
    assert {entry['check_id'] for entry in doc['checks']} == {
        check.id for check in baseline_pack().active_checks() if check.executor is None}
    _fill(_entry(doc, "SUP-01"))
    p = _write(tmp_path, doc)
    r = runner.invoke(app, ["evidence", "import", str(p)])
    assert r.exit_code == 0, r.output
    assert runner.invoke(app, ["judge", "--plan", plan]).exit_code == 0
    assert runner.invoke(app, ["report", "--plan", plan, "--json", "r.json", "--html", "r.html"]).exit_code == 0
    data = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    (row,) = [x for x in data["by_check"] if x["check_id"] == "SUP-01"]
    assert row["single"]["security"] == "PASS" and row["evidence_source"] == "manual"
