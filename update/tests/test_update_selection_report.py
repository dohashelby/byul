"""U1 보고 계약: 이월은 새 실측이 아니며 도달 관측은 판정을 바꾸지 않는다."""

import json
from datetime import timedelta

import pytest

from guard.core.report import build_report
from guard.core.select import select
from guard.models import DataOrigin, Evidence, Result, Side, Verdict
from guard.models.objects import Change
from guard.store import Store
from tests.builders import T0, baseline_pack, environment, policy


def _setup(tmp_path, include=("REQ-01",)):
    store, pack = Store(tmp_path / "store"), baseline_pack()
    env = environment({"gateway": {"value": True, "origin": "DECLARED", "source": "operator"}}, origin=DataOrigin.DECLARED)
    pol = policy(pack.ref(), data_origin=DataOrigin.DECLARED,
                 scope={"include": list(include), "exclude": []})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    return store, pack, env, pol, plan


def _evidence(store, plan, env, *, check_id="REQ-01", origin=DataOrigin.MEASURED, logs=None,
              registered=("POST /new",), tested=("POST /new",)):
    artifacts = []
    if logs is not None:
        raw = ("\n".join(json.dumps(line) for line in logs) + "\n").encode()
        artifact = store.put_artifact(raw)
        artifacts = [dict(name="probe_log.jsonl", path=artifact.path, sha256=artifact.sha256,
                          bytes=artifact.bytes, collected_at=T0, collector="test")]
    evd = Evidence(id="EVD-CURRENT", created_at=T0, data_origin=origin, refs=[plan.ref(), env.ref()],
                   plan=plan.ref(), environment=env.ref(), check_id=check_id, side=Side.single,
                   execution={"status": "COMPLETED", "started_at": T0, "finished_at": T0},
                   normal_control={"attempted": True, "succeeded": True}, artifacts=artifacts,
                   observations={"routes_registered": {"collected": True, "value": list(registered)},
                                 "routes_tested": {"collected": True, "value": list(tested)}}).seal()
    store.put(evd)
    return evd


def _result(store, evd, *, revision=1, security=Verdict.PASS, created_at=T0):
    result = Result(id="RES-CURRENT", revision=revision, created_at=created_at,
                    data_origin=evd.data_origin, refs=[evd.ref()], evidence=evd.ref(),
                    check_id=evd.check_id, side=evd.side,
                    judge={"name": "rule_table", "version": "1", "params_sha256": "a" * 64},
                    verdicts={"security": security, "functional": "PASS", "performance": "UNKNOWN"},
                    verification_level=evd.data_origin).seal()
    store.put(result)
    return result


def _with_change(store, pack, plan, *, kind="http", category="API_ROUTE", related=("REQ-01",), route="/new",
                 source_path="entrypoint/server.py"):
    change = Change(id="CHG-REPORT", created_at=T0, data_origin=DataOrigin.DECLARED,
                    baseline={"version": "1.0.0", "source_commit": "a" * 40},
                    candidate={"version": "1.0.1", "source_commit": "b" * 40},
                    extractor={"name": "git_diff", "version": "1"}, facts=[
                        {"id": "F-ROUTE", "category": category, "target": source_path,
                         "certainty": "PARTIAL", "related_checks": list(related),
                         "details": {"entrypoint": True, "entrypoint_kind": kind,
                                     "source_path": source_path,
                                     "routes": [{"method": "POST", "path": route}] if route else []}},
                        {"id": "F-UNKNOWN", "category": "OTHER", "target": "unknown.py",
                         "certainty": "UNKNOWN", "unmapped": True},
                        {"id": "F-GAP", "category": "OUTPUT_PARSING", "target": "parser.py",
                         "certainty": "PARTIAL"},
                    ]).seal()
    store.put(change)
    changed = plan.model_copy(update={"id": "PLAN-CHANGE", "inputs": plan.inputs.model_copy(update={"change": change.ref()}),
                                      "refs": [*plan.refs, change.ref()], "change_type": "ENGINE_RELEASE"}).seal()
    store.put(changed)
    return changed


def test_carried_ref_is_exact_pass_aggregated_but_no_new_execution(tmp_path):
    store, pack, env, pol, old = _setup(tmp_path)
    evd = _evidence(store, old, env)
    first = _result(store, evd)
    _result(store, evd, revision=2, security=Verdict.FAIL, created_at=T0 + timedelta(hours=1))
    data = old.to_dict()
    item = next(item for item in data["items"] if item["check_id"] == "REQ-01")
    item.update(selected=False, not_selected_reason="CARRIED_OVER", carried_over={
        "result": first.ref().model_dump(mode="json"), "evidence": evd.ref().model_dump(mode="json"),
        "image_digest": "sha256:" + "a" * 64, "judged_at": first.created_at.isoformat(), "reason": "unchanged"})
    data.update(id="PLAN-CARRIED", change_type="INPUT", items=[item])
    data["inputs"]["previous_plan"] = old.ref().model_dump(mode="json")
    data["refs"].append(old.ref().model_dump(mode="json"))
    current = type(old).model_validate(data).seal()
    store.put(current)
    before = [(obj.id, obj.revision) for obj in store.iter_kind("RES")]
    report = build_report(store, pack, current, T0 + timedelta(hours=2))
    row = report.by_check[0]
    assert row.single.result == first.ref() and row.single.security == Verdict.PASS
    assert row.previous_result.security == Verdict.FAIL
    assert row.comparison is None and row.functional_comparison is None
    assert report.carry_counts == {"selected": 0, "executed": 0, "measured": 0, "carried": 1, "not_run": 0, "manual_required": 0}
    assert report.evidence_counts == {}
    assert [(obj.id, obj.revision) for obj in store.iter_kind("RES")] == before
    assert first.ref() in report.inputs_manifest and evd.ref() in report.inputs_manifest
    data = report.to_dict()
    assert data["carry_counts"]["executed"] == 0 and data["carry_counts"]["carried"] == 1
    assert data["by_check"][0]["carried_over"]["result"] == first.ref().model_dump(mode="json")
    assert data["by_check"][0]["single"]["result"] == first.ref().model_dump(mode="json")


def test_readjudged_carried_result_keeps_original_execution_time(tmp_path):
    store, pack, env, pol, old = _setup(tmp_path)
    evd = _evidence(store, old, env)
    first = _result(store, evd)
    second = _result(store, evd, revision=2, created_at=T0 + timedelta(days=1))
    data = old.to_dict()
    data.update(id="PLAN-REJUDGED", change_type="INPUT", items=[data["items"][next(i for i, it in enumerate(old.items) if it.check_id == "REQ-01")]])
    data["items"][0].update(selected=False, not_selected_reason="CARRIED_OVER", carried_over={
        "result": second.ref().model_dump(mode="json"), "evidence": evd.ref().model_dump(mode="json"),
        "image_digest": "sha256:" + "a" * 64, "judged_at": second.created_at.isoformat(), "reason": "readjudged"})
    data["inputs"]["previous_plan"] = old.ref().model_dump(mode="json")
    data["refs"].extend(ref.model_dump(mode="json") for ref in (old.ref(), second.ref(), evd.ref()))
    current = type(old).model_validate(data).seal()
    store.put(current)
    report = build_report(store, pack, current, T0 + timedelta(days=2))
    times = report.change_summary["carried_results"][0]
    assert times["executed_at"] == T0.isoformat()
    assert times["judged_at"] == second.created_at.isoformat()
    assert store.resolve(first.ref()).content_sha256 == first.content_sha256
    assert report.by_check[0].single.result == second.ref()
    assert times["executed_at"] != times["judged_at"]
    assert report.carry_counts["executed"] == report.carry_counts["measured"] == 0
    assert any("이번 실행·실측으로 세지 않음" in note for note in report.limitations)


@pytest.mark.parametrize("mode", ["tcp", "http", "handler", "missing_scope", "example", "no_evidence", "no_trace", "partial_roles"])
def test_five_stages_keep_evidence_scopes_and_do_not_change_verdicts(tmp_path, mode):
    store, pack, env, pol, initial = _setup(tmp_path)
    plan = _with_change(store, pack, initial)
    basis, outcome = ("tcp_connect (inferred)", "refused") if mode == "tcp" else ("http", 401)
    trace = {"via": "direct", "route": "POST /new", "key": "none", "basis": basis, "outcome": outcome}
    if mode in ("handler", "partial_roles"):
        trace["classified"] = "handler_reached"
    if mode != "no_evidence":
        logs = [trace, {"summary": True, "fixture": {"roles": ["none", "admin"] if mode == "partial_roles" else ["none"]}}]
        evd = _evidence(store, plan, env, logs=None if mode == "no_trace" else [] if mode == "missing_scope" else logs,
                        registered=() if mode == "missing_scope" else ("POST /new",),
                        origin=DataOrigin.EXAMPLE if mode == "example" else DataOrigin.MEASURED)
        res = _result(store, evd, security=Verdict.FAIL)
    report = build_report(store, pack, plan, T0)
    stage = report.change_summary["entrypoints"][0]["stages"][0]
    assert stage["selected"] is True
    assert set(stage) >= {"selected", "in_scope", "role_probe", "attempted", "observed", "observation_scope"}
    if mode == "tcp":
        assert stage["observation_scope"] == ["TCP_BLOCKED"]
        assert stage["attempted"] is False and stage["observed"] is False
    elif mode == "http":
        assert stage["observation_scope"] == ["HTTP_RESPONSE"] and not stage["handler_reached"]
    elif mode == "handler":
        assert stage["handler_reached"] is True and "HANDLER_REACHED" in stage["observation_scope"]
        assert not report.change_summary["manual_targets"]
    elif mode == "missing_scope":
        assert stage["in_scope"] is False
        assert "ENTRYPOINT_OUTSIDE_COLLECTED_CHECK_SCOPE" in stage["limitations"]
    elif mode == "partial_roles":
        assert stage["role_probe"] is False and stage["handler_reached"] is True
        assert report.change_summary["manual_targets"]
    else:
        assert stage["attempted"] is None and stage["observed"] is None
    if mode != "no_evidence":
        assert next(row for row in report.by_check if row.check_id == "REQ-01").single.result == res.ref()
        assert next(row for row in report.by_check if row.check_id == "REQ-01").single.security == Verdict.FAIL
    else:
        assert report.carry_counts["measured"] == 0
    summary = report.to_dict()["change_summary"]
    assert any(fact["target"] == "unknown.py" for fact in summary["unmapped"])
    assert any(fact["target"] == "parser.py" for fact in summary["checklist_gaps"])


def test_static_unhandled_entrypoint_stays_manual_and_json_preserves_literal_source(tmp_path):
    store, pack, env, pol, initial = _setup(tmp_path)
    source_path = "<script>alert(1)</script>"
    plan = _with_change(store, pack, initial, kind="grpc", route=None, source_path=source_path)
    report = build_report(store, pack, plan, T0)
    assert report.change_summary["manual_targets"]
    stage = report.change_summary["entrypoints"][0]["stages"][0]
    assert stage["observed"] is None and stage["handler_reached"] is False
    data = report.to_dict()
    assert data["change_summary"]["entrypoints"][0]["source_path"] == source_path
    assert data["change_summary"]["manual_targets"][0]["source_path"] == source_path
    # HTML로 해석하지 않는 화면 검증은 platform/web/tests/report.test.tsx에 유지한다.


def test_corrupt_trace_cannot_confirm_dynamic_reach(tmp_path):
    store, pack, env, pol, initial = _setup(tmp_path)
    plan = _with_change(store, pack, initial)
    evd = _evidence(store, plan, env, logs=[{"via": "direct", "route": "POST /new", "basis": "http", "outcome": 200,
                                           "classified": "handler_reached"}])
    (store.root / evd.artifacts[0].path).write_bytes(b"altered")
    stage = build_report(store, pack, plan, T0).change_summary["entrypoints"][0]["stages"][0]
    assert not stage["handler_reached"] and stage["observed"] is None
    assert "TRACE_INTEGRITY_ERROR" in stage["limitations"]


def test_old_report_optional_fields_are_not_serialized(tmp_path):
    store, pack, env, pol, plan = _setup(tmp_path)
    report = build_report(store, pack, plan, T0).to_dict()
    assert "change_summary" not in report and "carry_counts" not in report
    assert all(not {"carried_over", "previous_result", "triggers"} & row.keys() for row in report["by_check"])
