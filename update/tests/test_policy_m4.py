"""M4 contracts: declared item evidence is not engine execution or policy approval."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.manual import ManualEvidenceError, build_template, import_evidence
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import select
from guard.judges.declared import DeclaredJudge
from guard.manual_policy import CASES, OBSERVATION, PARAMETERS, blank_evidence, normalize
from guard.models import Evidence
from guard.store import IntegrityError, Store
from tests.builders import PACK_DIR, T0, baseline_pack, environment, evidence, policy
from tests.prepared_fixtures import dump_fixture_yaml, manual_setup_seed, put_baseline_snapshot

runner = CliRunner()


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def values(items=("EXAMPLE-one", "EXAMPLE-two")):
    return {cid: {key: list(items)} for cid, key in PARAMETERS.items()}


def setup(tmp_path, pack, given=None, mode="audit"):
    envs = [environment()]
    if mode == "update":
        envs.append(environment(id="ENV-CANDIDATE"))
    pol = policy(pack.ref(), values=values() if given is None else given,
                 scope={"include": ["SUP-14", "LIF-07", "SUP-03"]})
    def create(folder):
        st = Store(folder / "store")
        st.put_many([*envs, pol])
        put_baseline_snapshot(st)
        plan = select(pack, envs, pol, mode=mode)
        st.put(plan)
        return st, plan, build_template(st, pack, plan)

    return manual_setup_seed("m4", {
        "pack": pack.model_dump(mode="json"), "environments": [e.to_dict() for e in envs],
        "policy": pol.to_dict(), "mode": mode,
    }, tmp_path, create)


def fill(doc):
    for entry in doc["checks"]:
        entry.update(verdicts={"security":"PASS","functional":"PASS","performance":"PASS"},
                     attachments=["EXAMPLE.txt"], entered_by="EXAMPLE operator",
                     entered_at=T0.isoformat(), basis="EXAMPLE synthetic contract")
        entry["normal_control"]["result"] = "succeeded"
        for step in entry["challenges"]:
            step["attempted"] = True
        for note in entry["observation_notes"]:
            note["answer"] = "EXAMPLE synthetic observation"
        cid = entry["check_id"]
        for row in entry.get("policy_evidence", {}).get("records", []):
            case = row["case"]
            obs = {"trial_id":f"EXAMPLE-{cid}-{case}", "target_scope":"EXAMPLE rank/model/worker"}
            if cid == "SUP-14":
                obs.update(check_outcome="succeeded" if case=="normal" else case,
                           gate_outcome="advanced" if case=="normal" else "blocked",
                           exception_assessment="none", transition_detail="EXAMPLE transition + exception review")
            else:
                obs.update(item_state=case, request_outcome="accepted" if case=="ready" else "blocked",
                           inference_outcome="succeeded" if case=="ready" else "not_attempted",
                           order_basis="same_clock", order_detail="EXAMPLE state at admission; ordered events")
            row.update(observations=obs, evidence=[{"attachment":"EXAMPLE.txt","locator":"L1-L4",
                                                   "basis":"EXAMPLE synthetic event linkage"}])
    return doc


def entry(doc, cid="SUP-14", side="single"):
    return next(e for e in doc["checks"] if e["check_id"]==cid and e["side"]==side)


def row(doc, cid, case, item="EXAMPLE-one", side="single"):
    return next(r for r in entry(doc,cid,side)["policy_evidence"]["records"]
                if r["item_id"]==item and r["case"]==case)


def write(tmp_path, doc):
    (tmp_path/"EXAMPLE.txt").write_text("EXAMPLE synthetic source; no engine executed\n",encoding="utf-8")
    path = tmp_path/"manual.yaml"
    path.write_text(dump_fixture_yaml(doc,allow_unicode=True,sort_keys=False),encoding="utf-8")
    return path


def judge(st, pack, plan):
    return {r.check_id:r for r in judge_plan(st,pack,plan,T0)}


def snapshot(st):
    return {o.ref().sha256:o.to_dict() for k in ("EVD","RES") for o in st.iter_kind(k)}


def unit(check, supplied=None, verdicts=None):
    doc = fill({"checks":[{"check_id":check.id,"side":"single","verdicts":{},
        "normal_control":{},"challenges":[],"observation_notes":[],
        "policy_evidence":blank_evidence(check, values()[check.id])}]})
    data = normalize(check, values()[check.id], entry(doc,check.id)["policy_evidence"], {"EXAMPLE.txt":"a"*64})
    evd = evidence(check_id=check.id, observations={
        "declared_verdicts":{"value":verdicts or {"security":"PASS","functional":"PASS","performance":"PASS"},"collected":True},
        "declared_observation_notes":{"value":{p:"EXAMPLE note" for p in check.required_observations_prose},"collected":True},
        OBSERVATION:{"value":data,"collected":True}},
        artifacts=[{"name":"EXAMPLE.txt","path":"unit-only","sha256":"a"*64,"bytes":1,
                    "collected_at":T0,"collector":"EXAMPLE"}])
    return evd, values()[check.id] if supplied is None else supplied


def result(check, evd, vals):
    return DeclaredJudge().judge(check,evd,{},vals)


def prior_fail_setup(tmp_path, pack):
    """Clone the fixed prior state; each invalid import still runs below."""
    env = environment()
    pol = policy(pack.ref(), values=values(), scope={"include": ["SUP-14", "LIF-07", "SUP-03"]})

    def create(folder):
        st, plan, doc = setup(folder, pack)
        fill(doc)
        entry(doc)["verdicts"]["security"] = "FAIL"
        import_evidence(st, pack, write(folder, doc), now=T0)
        judge(st, pack, plan)
        return st, plan, doc

    return manual_setup_seed("m4-prior-fail", {
        "pack": pack.model_dump(mode="json"), "environment": env.to_dict(), "policy": pol.to_dict(),
        "mode": "audit", "prior_security": "FAIL",
        "attachment": b"EXAMPLE synthetic source; no engine executed\n".hex(),
    }, tmp_path, create)


def test_v2_uses_pinned_refs_and_never_populates_observation_results(tmp_path, pack):
    st,plan,doc = setup(tmp_path,pack)
    assert doc["template"]=="guard-manual-evidence/3"
    assert doc["policy"]==plan.inputs.policy.model_dump(mode="json")
    assert doc["pack"]==plan.inputs.pack.model_dump(mode="json")
    for cid,key in PARAMETERS.items():
        e=entry(doc,cid)
        assert e["policy_evidence"]["parameter"]==key
        assert len(e["policy_evidence"]["records"])==2*len(CASES[cid])
        assert all(all(v is None for v in r["observations"].values()) and not r["evidence"]
                   for r in e["policy_evidence"]["records"])
    assert "policy_evidence" not in entry(doc,"SUP-03")
    # A new revision with the same ID is not the PLAN's policy.
    original=st.resolve(plan.inputs.policy)
    st.put(original.model_copy(update={"revision":2,"values":values(["EXAMPLE-new"])}).seal())
    assert build_template(st,pack,plan)==doc


def test_v2_end_to_end_report_and_rejudge(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack)
    import_evidence(st,pack,write(tmp_path,fill(doc)),now=T0)
    outcomes=judge(st,pack,plan)
    assert outcomes["SUP-14"].verdicts.model_dump()=={"security":"PASS","functional":"UNKNOWN","performance":"UNKNOWN"}
    assert outcomes["LIF-07"].verdicts.model_dump()=={"security":"PASS","functional":"PASS","performance":"UNKNOWN"}
    assert outcomes["SUP-03"].verdicts.security=="PASS"
    for evd in st.iter_kind("EVD"):
        assert evd.data_origin=="DECLARED" and evd.execution.executor.version=="0.5.0"
        if evd.check_id in PARAMETERS:
            payload=evd.observations[OBSERVATION].value
            assert payload["version"]=="guard-policy-evidence/1"
            assert "attachment" not in payload["records"][0]["evidence"][0]
            assert payload["records"][0]["evidence"][0]["sha256"]==evd.artifacts[0].sha256
    before=snapshot(st)
    judge(st,pack,plan)
    assert snapshot(st)==before and not st.verify()
    rpt=build_report(st,pack,plan,T0).to_dict()
    assert rpt["official"] is False
    assert any("SUP-14/single: POLICY_ITEMS:" in s and "EXAMPLE-one" in s for s in rpt["limitations"])
    assert any("LIF-07/single: POLICY_ITEMS:" in s for s in rpt["limitations"])
    assert not any("SUP-03/single: POLICY_ITEMS:" in s for s in rpt["limitations"])
    assert "EXAMPLE synthetic event linkage" not in json.dumps(rpt)  # no raw basis leakage


@pytest.mark.parametrize("issue",[
    "policy_revision","policy_hash","pack_revision","pack_hash","plan_hash","reference_kind","missing_ref",
    "duplicate","foreign_item","wrong_parameter","wrong_case","wrong_observation_type","unknown_field",
    "wrong_side","not_selected","foreign_attachment","bad_locator","bad_limitation","bad_records",
    "legacy_smuggling","non_target_payload"])
def test_invalid_forms_reject_entire_batch_and_preserve_prior_fail(tmp_path,pack,issue):
    st,plan,doc=prior_fail_setup(tmp_path,pack)
    before,blobs=snapshot(st),set((st.root/"artifacts").iterdir())
    bad=deepcopy(doc); e=entry(bad); r=e["policy_evidence"]["records"][0]
    if issue in ("policy_revision","pack_revision"): bad[issue.split("_")[0]]["revision"]+=1
    elif issue in ("policy_hash","pack_hash"): bad[issue.split("_")[0]]["sha256"]="0"*64
    elif issue=="plan_hash": bad["plan"]["sha256"]="0"*64
    elif issue=="reference_kind": bad["policy"]["kind"]="ENV"
    elif issue=="missing_ref": bad.pop("policy")
    elif issue=="duplicate": e["policy_evidence"]["records"].append(deepcopy(r))
    elif issue=="foreign_item": r["item_id"]="EXAMPLE-not-required"
    elif issue=="wrong_parameter": e["policy_evidence"]["parameter"]="wrong"
    elif issue=="wrong_case": r["case"]="guessed_success"
    elif issue=="wrong_observation_type": r["observations"]["gate_outcome"]=True
    elif issue=="unknown_field": r["observations"]["gate_otucome"]="advanced"
    elif issue=="wrong_side": e["side"]="candidate"
    elif issue=="not_selected": e["check_id"]="SUP-02"
    elif issue=="foreign_attachment": r["evidence"][0]["attachment"]="other-entry-only.txt"
    elif issue=="bad_locator": r["evidence"][0]["locator"]=9
    elif issue=="bad_limitation": r["limitation"]=[]
    elif issue=="bad_records": e["policy_evidence"]["records"]={}
    elif issue=="legacy_smuggling": bad["template"]="guard-manual-evidence/1"
    elif issue=="non_target_payload": entry(bad,"SUP-03")["policy_evidence"]=deepcopy(e["policy_evidence"])
    with pytest.raises((ManualEvidenceError,ValueError,IntegrityError)):
        import_evidence(st,pack,write(tmp_path,bad),now=T0)
    assert snapshot(st)==before and set((st.root/"artifacts").iterdir())==blobs


@pytest.mark.parametrize("cid",list(PARAMETERS))
@pytest.mark.parametrize("gap",["missing_case","empty_refs","missing_locator","unknown_observation","limitation","different_trial"])
def test_partial_evidence_never_passes(pack,cid,gap):
    check=pack.checks[cid]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]; r=rows[-1]
    if gap=="missing_case": rows.pop()
    elif gap=="empty_refs": r["evidence"]=[]
    elif gap=="missing_locator": r["evidence"][0]["locator"]=None
    elif gap=="unknown_observation": r["observations"]["trial_id"]=None
    elif gap=="limitation": r["limitation"]="EXAMPLE unresolved scope"
    elif gap=="different_trial":
        positive="normal" if cid=="SUP-14" else "ready"
        next(r for r in rows if r["item_id"]=="EXAMPLE-two" and r["case"]==positive)["observations"]["trial_id"]="EXAMPLE-other-trial"
    out=result(check,evd,vals)
    assert out.verdicts["security"]=="UNKNOWN" and out.missing_observations


@pytest.mark.parametrize("cid",list(PARAMETERS))
@pytest.mark.parametrize("items",[[],[""],[" EXAMPLE-one"],["EXAMPLE-one","EXAMPLE-one"]])
def test_empty_or_ambiguous_policy_is_unknown_not_type_error(tmp_path,pack,cid,items):
    given={cid:{PARAMETERS[cid]:items}}
    st,plan,doc=setup(tmp_path,pack,given)
    assert "NO_POLICY_VALUE" not in plan.item(cid).gaps
    assert "NO_EXECUTOR" in plan.item(cid).gaps
    fill(doc); import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert judge(st,pack,plan)[cid].verdicts.security=="UNKNOWN"


@pytest.mark.parametrize("cid",list(PARAMETERS))
def test_unknown_is_not_promoted_and_fail_is_preserved(pack,cid):
    check=pack.checks[cid]
    evd,vals=unit(check,verdicts={"security":"UNKNOWN","functional":"UNKNOWN","performance":"UNKNOWN"})
    assert all(v=="UNKNOWN" for v in result(check,evd,vals).verdicts.values())
    evd.observations["declared_verdicts"].value={"security":"FAIL","functional":"FAIL","performance":"FAIL"}
    evd.observations[OBSERVATION].value={"version":"invalid"}
    evd.artifacts=[]
    assert all(v=="FAIL" for v in result(check,evd,{}).verdicts.values())


@pytest.mark.parametrize("case",["failed","error","unsupported","skipped"])
def test_sup_incomplete_advanced_is_fail_not_hidden_by_other_missing(pack,case):
    check=pack.checks["SUP-14"]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]
    r=next(r for r in rows if r["item_id"]=="EXAMPLE-one" and r["case"]==case)
    r["observations"]["gate_outcome"]="advanced"
    r["observations"]["trial_id"]="EXAMPLE-independent-violation"
    rows.remove(next(r for r in rows if r["item_id"]=="EXAMPLE-two" and r["case"]=="normal"))
    out=result(check,evd,vals)
    assert out.verdicts["security"]=="FAIL"
    assert any("SUP_REQUIRED_INCOMPLETE_ADVANCED" in r for r in out.reasons)


def test_sup_exception_is_not_check_success(pack):
    check=pack.checks["SUP-14"]; evd,vals=unit(check)
    r=evd.observations[OBSERVATION].value["records"][1]
    r["observations"].update(gate_outcome="advanced",exception_assessment="reported")
    assert result(check,evd,vals).verdicts["security"]=="UNKNOWN"


def test_unresolved_other_policy_item_does_not_hide_known_required_violation(pack):
    check=pack.checks["SUP-14"]; evd,vals=unit(check)
    vals["required_checks"].append("")
    r=evd.observations[OBSERVATION].value["records"][1]
    r["observations"].update(gate_outcome="advanced",trial_id="EXAMPLE-independent")
    assert result(check,evd,vals).verdicts["security"]=="FAIL"


@pytest.mark.parametrize("cid",list(PARAMETERS))
def test_item_judgment_is_pure_and_repeatable(pack,cid):
    check=pack.checks[cid]; evd,vals=unit(check)
    before,pol=evd.to_dict(),deepcopy(vals)
    first=result(check,evd,vals).model_dump(mode="json")
    assert result(check,evd,vals).model_dump(mode="json")==first
    assert evd.to_dict()==before and vals==pol


def test_conflicting_event_does_not_hide_independent_violation(pack):
    check=pack.checks["SUP-14"]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]
    next(r for r in rows if r["item_id"]=="EXAMPLE-one" and r["case"]=="failed")["observations"]["gate_outcome"]="advanced"
    assert result(check,evd,vals).verdicts["security"]=="UNKNOWN"  # other item reports same event blocked
    next(r for r in rows if r["item_id"]=="EXAMPLE-two" and r["case"]=="error")["observations"].update(
        gate_outcome="advanced",trial_id="EXAMPLE-independent")
    assert result(check,evd,vals).verdicts["security"]=="FAIL"


def test_lif_early_admission_survives_unknown_inference_and_other_gap(pack):
    check=pack.checks["LIF-07"]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]
    rows[0]["observations"].update(request_outcome="accepted",inference_outcome="unknown",trial_id="EXAMPLE-early")
    rows.pop()
    out=result(check,evd,vals)
    assert out.verdicts["security"]=="FAIL" and out.verdicts["functional"]=="UNKNOWN"


def test_lif_unknown_order_is_not_proven_early_admission(pack):
    check=pack.checks["LIF-07"]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]
    rows[0]["observations"].update(request_outcome="accepted",order_basis="unknown",trial_id="EXAMPLE-early")
    assert result(check,evd,vals).verdicts["security"]=="UNKNOWN"


def test_lif_function_failure_requires_ready_bundle(pack):
    check=pack.checks["LIF-07"]; evd,vals=unit(check)
    rows=evd.observations[OBSERVATION].value["records"]
    for r in rows:
        if r["case"]=="ready": r["observations"]["inference_outcome"]="failed"
    out=result(check,evd,vals)
    assert out.verdicts["functional"]=="FAIL" and out.verdicts["security"]=="UNKNOWN"
    rows.remove(next(r for r in rows if r["item_id"]=="EXAMPLE-two" and r["case"]=="ready"))
    assert result(check,evd,vals).verdicts["functional"]=="UNKNOWN"


@pytest.mark.parametrize("normal,steps", [("failed",True),("not_attempted",True),("succeeded",False)])
def test_existing_core_pass_gate_is_preserved(tmp_path,pack,normal,steps):
    st,plan,doc=setup(tmp_path,pack); fill(doc)
    for e in doc["checks"]:
        e["normal_control"]["result"]=normal
        for c in e["challenges"]: c["attempted"]=steps
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert all(r.verdicts.security=="UNKNOWN" for r in judge(st,pack,plan).values())


def test_v1_import_and_old_definition_compatibility(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack); fill(doc)
    doc["template"]="guard-manual-evidence/1"
    doc.pop("policy"); doc.pop("pack")
    for e in doc["checks"]: e.pop("policy_evidence",None)
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    out=judge(st,pack,plan)
    assert out["SUP-14"].verdicts.security=="UNKNOWN" and out["LIF-07"].verdicts.security=="UNKNOWN"
    assert out["SUP-03"].verdicts.security=="PASS"
    old=pack.checks["SUP-14"].model_copy(update={"policy_params":[]})
    evd,vals=unit(pack.checks["SUP-14"])
    del evd.observations[OBSERVATION]
    assert result(old,evd,{}).verdicts["security"]=="PASS"


def test_meaningful_new_observations_change_config_hash_without_overwrite(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack); fill(doc)
    path=write(tmp_path,doc); import_evidence(st,pack,path,now=T0)
    before={e.check_id:e for e in st.iter_kind("EVD")}
    row(doc,"SUP-14","failed")["observations"]["trial_id"]="EXAMPLE-edited-trial"
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    current={e.check_id:e for e in st.find_latest("EVD",lambda e: e.plan==plan.ref())}
    assert current["SUP-14"].execution.executor.config_sha256 != before["SUP-14"].execution.executor.config_sha256
    assert current["SUP-14"].revision==2 and st.resolve(before["SUP-14"].ref()).to_dict()==before["SUP-14"].to_dict()
    assert current["LIF-07"].execution.executor.config_sha256==before["LIF-07"].execution.executor.config_sha256


def test_update_sides_have_separate_records_and_remain_not_comparable(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack,mode="update"); fill(doc)
    row(doc,"LIF-07","not_ready",side="candidate")["observations"].update(
        request_outcome="accepted",trial_id="EXAMPLE-candidate-early")
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    judge_plan(st,pack,plan,T0)
    report=build_report(st,pack,plan,T0).to_dict()
    lif=next(r for r in report["by_check"] if r["check_id"]=="LIF-07")
    assert lif["baseline"]["security"]=="PASS" and lif["candidate"]["security"]=="FAIL"
    assert lif["comparison"]=="NOT_COMPARABLE"
    assert any("LIF-07/candidate:" in s and "LIF_ADMITTED_BEFORE_READY" in s for s in report["limitations"])


def test_report_keeps_identifiers_as_data_and_omits_raw_source_details(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack,values(["EXAMPLE-<script>alert(1)</script>"])); fill(doc)
    row_id=entry(doc)["policy_evidence"]["records"][0]["item_id"]
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    judge(st,pack,plan)
    report=build_report(st,pack,plan,T0).to_dict(); text=json.dumps(report,ensure_ascii=False)
    assert row_id in json.dumps(report,ensure_ascii=False)
    assert "EXAMPLE synthetic event linkage" not in text and "L1-L4" not in text


def test_cli_v2_to_json_and_idempotent_rejudge(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack); fill(doc)
    path=write(tmp_path,doc)
    def ok(args):
        r=runner.invoke(app,args)
        assert r.exit_code==0,r.output+repr(r.exception)
        return r.output
    common=["--store",str(st.root),"--pack",str(PACK_DIR)]
    ok(["evidence","import",str(path),*common])
    args=["judge","--plan",plan.id,"--as-of",T0.isoformat(),*common]
    ok(args); before=snapshot(st); ok(args); assert snapshot(st)==before
    ok(["report","--plan",plan.id,"--json",str(tmp_path/"report.json"),*common])
    assert "POLICY_ITEMS:" in (tmp_path/"report.json").read_text(encoding="utf-8")
    assert not st.verify()
