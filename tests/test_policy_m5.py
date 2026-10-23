"""M5: confirmed failures survive unrelated conflicts; approval names are context."""
from copy import deepcopy
import csv
import json

import pytest
import yaml

from guard.collectors.manual import build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import PolicyValueError, select
from guard.judges.declared import DeclaredJudge
from guard.manual_policy import OBSERVATION
from guard.report.html import render_html
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, policy
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import fill, unit, write


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def early(pack):
    check = pack.checks["LIF-07"]
    evd, values = unit(check)
    negative = [r for r in evd.observations[OBSERVATION].value["records"] if r["case"] == "not_ready"]
    for r in negative:
        r["observations"].update(request_outcome="accepted", inference_outcome="failed")
    return check, evd, values, negative


@pytest.mark.parametrize("conflict", ["inference", "order_method", "both", "unknown_inference"])
def test_independent_early_admission_fail_survives_unrelated_conflicts(pack, conflict):
    check, evd, values, negative = early(pack)
    if conflict in ("inference", "both"):
        negative[1]["observations"]["inference_outcome"] = "succeeded"
    if conflict in ("order_method", "both"):
        negative[1]["observations"]["order_basis"] = "independent_order"
    if conflict == "unknown_inference":
        negative[0]["observations"]["inference_outcome"] = "unknown"
    before, pol = evd.to_dict(), deepcopy(values)
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts == {"security": "FAIL", "functional": "UNKNOWN", "performance": "UNKNOWN"}
    assert any("LIF_ADMITTED_BEFORE_READY" in r for r in result.reasons)
    assert DeclaredJudge().judge(check, evd, {}, values) == result
    assert evd.to_dict() == before and values == pol


@pytest.mark.parametrize("gap", ["admission_conflict", "unknown_order", "blank_order", "unlinked"])
def test_unconfirmed_early_admission_is_unknown(pack, gap):
    check, evd, values, negative = early(pack)
    if gap == "admission_conflict":
        negative[1]["observations"]["request_outcome"] = "blocked"
    else:
        for r in negative:
            if gap == "unknown_order": r["observations"]["order_basis"] = "unknown"
            elif gap == "blank_order": r["observations"]["order_detail"] = " "
            else: r["evidence"] = []
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == "UNKNOWN"
    assert not any("LIF_ADMITTED_BEFORE_READY" in r for r in result.reasons)


def test_lif_independent_event_failure_survives_admission_conflict(pack):
    check, evd, values, negative = early(pack)
    negative[1]["observations"].update(request_outcome="blocked")
    assert DeclaredJudge().judge(check, evd, {}, values).verdicts["security"] == "UNKNOWN"
    negative[0]["observations"]["trial_id"] = "EXAMPLE-independent-early-event"
    assert DeclaredJudge().judge(check, evd, {}, values).verdicts["security"] == "FAIL"


def test_inference_conflict_still_prevents_unproved_pass(pack):
    check, evd, values, negative = early(pack)
    for r in negative: r["observations"].update(request_outcome="blocked", inference_outcome="not_attempted")
    negative[1]["observations"]["inference_outcome"] = "failed"
    result = DeclaredJudge().judge(check, evd, {}, values)
    assert result.verdicts["security"] == result.verdicts["functional"] == "UNKNOWN"
    assert any("EVENT_CONFLICT" in r for r in result.reasons)


def test_approval_declarations_are_two_scalar_paths_not_item_evidence(pack):
    assert pack.manifest.revision == 19 and not pack.reviewed
    for cid in ("SUP-01", "SUP-02"):
        c = pack.checks[cid]
        assert c.revision == 2
        assert [(p.key, p.type, p.required) for p in c.policy_params] == [("approval_record_system", "str", True)]
        assert c.executor is None and c.judge is None
    template = build_policy_template(pack)
    data = yaml.safe_load(template)
    assert data["values"]["SUP-01"] == data["values"]["SUP-02"] == {}
    assert data["status"] == "DRAFT" and data["data_origin"] == "DECLARED"
    rows = list(csv.DictReader((ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="")))
    row = next(r for r in rows if r["proposed_policy_id"] == "P-DRAFT-SUP-GOV-TOOL")
    assert row["classification"] == "a" and len(json.loads(row["current_param_paths"])) == 2
    assert len(rows) == 322 and sum(len(json.loads(r["check_ids"])) for r in rows) == 614


@pytest.mark.parametrize("cid", ["SUP-01", "SUP-02"])
@pytest.mark.parametrize("bad", [None, [], {}, 1, False])
def test_approval_invalid_type_rejected_even_outside_selected_scope(pack, cid, bad):
    pol = policy(pack.ref(), scope={"include": ["SUP-03"]}, values={cid: {"approval_record_system": bad}})
    with pytest.raises(PolicyValueError, match=cid):
        select(pack, [environment()], pol)


@pytest.mark.parametrize("cid", ["SUP-01", "SUP-02"])
@pytest.mark.parametrize("value", [None, "", " \t ", "EXAMPLE approval registry"])
def test_approval_context_holds_only_pass_and_never_promotes_unknown(pack, cid, value):
    check = pack.checks[cid]
    vals = {} if value is None else {"approval_record_system": value}
    plan = select(pack, [environment()], policy(pack.ref(), scope={"include": [cid]}, values={cid: vals}))
    assert ("NO_POLICY_VALUE" in plan.item(cid).gaps) == (value is None)
    assert "NO_EXECUTOR" in plan.item(cid).gaps
    evd = _manual_evidence(check, {"security": "PASS", "functional": "UNKNOWN", "performance": "FAIL"})
    result = DeclaredJudge().judge(check, evd, {}, vals)
    assert result.verdicts == {"security": "PASS" if value and value.strip() else "UNKNOWN",
                               "functional": "UNKNOWN", "performance": "FAIL"}
    assert any(r.startswith("POLICY_CONTEXT:") for r in result.reasons)
    if value is None:
        assert any("NO_POLICY_VALUE" in r for r in result.reasons)
    # With no declaration the targeted new guard must not affect an old pack.
    old_definition = check.model_copy(update={"revision": 1, "policy_params": []})
    old = DeclaredJudge().judge(old_definition, evd, {}, {})
    assert old.verdicts["security"] == "PASS"
    assert not any("POLICY_CONTEXT:" in r for r in old.reasons)


def test_approval_policies_are_independent_not_copied(pack):
    pol = policy(pack.ref(), scope={"include": ["SUP-01", "SUP-02"]},
                 values={"SUP-01": {"approval_record_system": "EXAMPLE one"}})
    plan = select(pack, [environment()], pol)
    assert "NO_POLICY_VALUE" not in plan.item("SUP-01").gaps
    assert "NO_POLICY_VALUE" in plan.item("SUP-02").gaps


@pytest.mark.parametrize("version", [1, 2])
def test_manual_approval_to_report_preserves_history_and_escapes_context(tmp_path, pack, version):
    st = Store(tmp_path / "store")
    env = environment()
    unsafe = "EXAMPLE <script>alert(1)</script> registry"
    pol = policy(pack.ref(), scope={"include": ["SUP-01", "SUP-02"]}, values={
        "SUP-01": {"approval_record_system": unsafe}, "SUP-02": {"approval_record_system": " "}})
    st.put_many([env, pol]); put_pack(st, PACK_DIR)
    plan = select(pack, [env], pol); st.put(plan)
    doc = fill(build_template(st, pack, plan))
    assert all("policy_evidence" not in e for e in doc["checks"])
    if version == 1:
        doc["template"] = "guard-manual-evidence/1"
        doc.pop("policy"); doc.pop("pack")
    import_evidence(st, pack, write(tmp_path, doc), now=T0)
    results = {r.check_id: r for r in judge_plan(st, pack, plan, T0)}
    assert results["SUP-01"].verdicts.security == "PASS"
    assert results["SUP-02"].verdicts.security == "UNKNOWN"
    # The evidence is declared; the result keeps the weaker EXAMPLE ENV/POL
    # provenance rather than promoting this synthetic contract to real data.
    assert all(r.judge.version == "0.12.0" and r.data_origin == "EXAMPLE" for r in results.values())
    assert all(e.data_origin == "DECLARED" for e in st.iter_kind("EVD"))
    saved = {o.ref().sha256: o.to_dict() for k in ("EVD", "RES") for o in st.iter_kind(k)}
    judge_plan(st, pack, plan, T0)
    assert {o.ref().sha256: o.to_dict() for k in ("EVD", "RES") for o in st.iter_kind(k)} == saved
    report = build_report(st, pack, plan, T0).to_dict()
    assert report["official"] is False
    assert any("SUP-02/single: POLICY_CONTEXT:" in s for s in report["limitations"])
    assert unsafe in json.dumps(report, ensure_ascii=False)
    html = render_html(report)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "POLICY_CONTEXT:" in html and not st.verify()
