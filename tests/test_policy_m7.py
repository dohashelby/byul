"""OBS-11 declared contracts: no engines, external collectors or real-time sleeps."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest
import yaml
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors.manual import ManualEvidenceError, build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.select import PolicyValueError, select
from guard.judges.declared import DeclaredJudge
from guard.manual_audit import BUDGETS, CASES, CLAIMS, CONTRACT, OBSERVATION, POLICY_TYPES, normalize, timestamp
from guard.models.objects import Observation
from guard.report.html import render_html
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, T0, baseline_pack, environment, policy
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import entry, fill, snapshot, write


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def values(items=("EXAMPLE-one", "EXAMPLE-two")):
    numbers = (100, 2, 20, 30)
    return {"required_records": list(items), **{k: dict.fromkeys(items, n) for k, n in zip(BUDGETS, numbers)}}


def iso(ms):
    return (T0 + timedelta(milliseconds=ms)).isoformat(timespec="milliseconds")


def payload(given=None):
    records = []
    for item in (given or values())["required_records"]:
        for index, case in enumerate(CASES):
            t = index * 1000
            obs = dict(trial_id="EXAMPLE-"+case, event_id=f"EXAMPLE-{item}-{case}", target_scope="EXAMPLE model",
                       path_id="EXAMPLE audit path", profile_applicability="confirmed", profile_detail="EXAMPLE all required missing records need alerts",
                       attempted=True, case_detail="EXAMPLE synthetic case only", occurrence="confirmed",
                       occurrence_source="EXAMPLE separate sender", collection_source="EXAMPLE collector",
                       independence_detail="EXAMPLE independent sender and sink reader, self-reported",
                       clock_basis="bounded_common_time", clock_source="EXAMPLE bounded clock", occurred_at=iso(t),
                       time_errors_ms={"occurred_at": 0}, record="received", received_at=iso(t+90), collector="healthy")
            claims = {"profile", "occurrence", "independence", "clock", "collection"}
            if case == "collection_fault":
                obs.update(record="absent", received_at=None, collector="interrupted", fault_method="collector_interrupt",
                           window_start_at=iso(t-20), window_end_at=iso(t+160), collection_window="complete",
                           window_source="EXAMPLE separate sink reader", window_detail="EXAMPLE complete event-correlated query",
                           missing_handling="identified", detected_at=iso(t+110), alert="received", alert_received_at=iso(t+120),
                           alert_receiver="EXAMPLE queue", alert_source="EXAMPLE separate alert reader",
                           alert_window="complete", alert_window_start_at=iso(t-20), alert_window_end_at=iso(t+160),
                           alert_window_detail="EXAMPLE complete receiver query", recovery="restored")
                claims |= {"window", "collector", "detection", "alert", "alert_window", "dependent", "recovery"}
                for key in ("window_start_at", "window_end_at", "detected_at", "alert_received_at", "alert_window_start_at", "alert_window_end_at"):
                    obs["time_errors_ms"][key] = 0
            elif case == "clock_uncertain":
                obs["received_at"] = iso(t+100)
                obs["time_errors_ms"]["occurred_at"] = 1
                claims.add("dependent")
            if case != "collection_fault":
                obs["time_errors_ms"]["received_at"] = 1 if case == "clock_uncertain" else 0
            if case != "normal":
                obs["dependent"] = {"test_id": f"EXAMPLE-dependent-{item}-{case}", "execution_status": "COMPLETED",
                                    "security": "UNKNOWN", "depends_on_record": True, "independent_failure": False}
            records.append({"record_id": item, "case": case, "observations": obs,
                            "evidence": [{"claim": c, "attachment": "EXAMPLE.txt", "locator": "L1-L20", "basis": "EXAMPLE synthetic "+c}
                                         for c in sorted(claims)], "limitation": None})
    return {"contract": CONTRACT, "records": records}


def unit(pack, given=None, verdicts=None):
    check = pack.checks["OBS-11"]
    evd = _manual_evidence(check, verdicts or {a: "PASS" for a in ("security", "functional", "performance")})
    evd.observations[OBSERVATION] = Observation(value=normalize(check, values(), payload(), {"EXAMPLE.txt": "0"*64}), collected=True)
    return evd, values() if given is None else given


def outcome(pack, evd, given):
    return DeclaredJudge().judge(pack.checks["OBS-11"], evd, {}, given)


def row(evd, case="collection_fault", item="EXAMPLE-one"):
    return next(r for r in evd.observations[OBSERVATION].value["records"] if r["case"]==case and r["record_id"]==item)


def setup(tmp_path, pack, mode="audit", scope=("OBS-11", "SUP-03"), items=("EXAMPLE-one", "EXAMPLE-two")):
    st = Store(tmp_path/"store"); envs = [environment()]
    if mode == "update": envs.append(environment(id="ENV-CANDIDATE"))
    given = values(items)
    pol = policy(pack.ref(), values={"OBS-11": given}, scope={"include": list(scope)})
    st.put_many([*envs, pol]); put_pack(st, PACK_DIR)
    plan = select(pack, envs, pol, mode=mode); st.put(plan)
    doc = fill(build_template(st, pack, plan))
    for e in doc["checks"]:
        if e["check_id"] == "OBS-11": e["audit_evidence"] = payload(given)
    return st, plan, doc


def test_policy_declarations_and_blank_form_are_not_execution(pack, tmp_path):
    check = pack.checks["OBS-11"]
    assert {p.key:p.type for p in check.policy_params if p.required} == POLICY_TYPES
    assert check.executor is None and check.judge is None and check.revision == 2
    assert pack.manifest.revision == 15 and not pack.reviewed
    pol = yaml.safe_load(build_policy_template(pack))
    assert pol["values"]["OBS-11"] == {} and pol["status"] == "DRAFT"
    st, plan, _ = setup(tmp_path, pack)
    blank = build_template(st, pack, plan)
    e = entry(blank, "OBS-11")
    assert blank["template"] == "guard-manual-evidence/3"
    assert len(e["audit_evidence"]["records"]) == 6
    assert all(all(v is None for v in r["observations"].values()) and not r["evidence"] for r in e["audit_evidence"]["records"])
    assert "audit_evidence" not in entry(blank,"SUP-03")


@pytest.mark.parametrize("bad", [None, [], {1:2}, {"EXAMPLE-one":True}, {"EXAMPLE-one":"2"}, {"EXAMPLE-one":1.5}])
def test_integer_maps_reject_bad_types_outside_scope(pack, bad):
    pol = policy(pack.ref(), scope={"include":["SUP-03"]}, values={"OBS-11":{"alert_deadline_ms":bad}})
    with pytest.raises(PolicyValueError, match="OBS-11"):
        select(pack,[environment()],pol)


@pytest.mark.parametrize("gap", ["missing", "empty", "partial", "foreign", "blank", "duplicate", "negative", "zero_collection"])
def test_semantic_policy_gaps_never_create_pass_or_not_applicable(pack, gap):
    evd, given = unit(pack)
    if gap=="missing": given.pop("alert_deadline_ms")
    elif gap=="empty": given["required_records"]=[]
    elif gap=="partial": given["alert_deadline_ms"].pop("EXAMPLE-two")
    elif gap=="foreign": given["alert_deadline_ms"]["EXAMPLE-foreign"]=30
    elif gap=="blank": given["required_records"].append(" ")
    elif gap=="duplicate": given["required_records"].append("EXAMPLE-two")
    elif gap=="negative": given["max_clock_uncertainty_ms"]["EXAMPLE-one"]=-1
    else: given["collection_deadline_ms"]["EXAMPLE-one"]=0
    result=outcome(pack,evd,given)
    assert result.verdicts["security"]=="UNKNOWN"
    assert any("POLICY_" in r for r in result.reasons)


def test_complete_declared_bundle_is_pure_and_axis_bounded(pack):
    evd, given=unit(pack); before, pol=evd.to_dict(),deepcopy(given)
    result=outcome(pack,evd,given)
    assert result.verdicts=={"security":"PASS","functional":"UNKNOWN","performance":"UNKNOWN"}
    assert any("UNCERTAINTY_HANDLED" in r for r in result.reasons)
    assert any("REQUIRED_RECORD_MISSING" in r for r in result.reasons)
    assert outcome(pack,evd,given)==result and evd.to_dict()==before and given==pol


@pytest.mark.parametrize("field,value",[("profile_applicability","unknown"),("profile_applicability","unsupported"),
    ("profile_detail",None),("occurrence","unknown"),("independence_detail",None),
    ("occurrence_source","EXAMPLE collector"),("clock_basis","unknown"),("clock_source",None),
    ("attempted",False),("case_detail",None)])
def test_profile_attempt_occurrence_or_clock_missing_is_unknown(pack,field,value):
    evd,given=unit(pack);row(evd)["observations"][field]=value
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"


@pytest.mark.parametrize("field,value",[("window_start_at",None),("window_start_at",iso(1050)),
    ("window_end_at",iso(1090)),("window_end_at",iso(1100)),("collection_window","incomplete"),
    ("window_source","EXAMPLE collector"),("window_detail",None)])
def test_incomplete_or_late_query_cannot_prove_missing_or_fail(pack,field,value):
    evd,given=unit(pack);o=row(evd)["observations"];o.update(missing_handling="treated_normal");o[field]=value
    result=outcome(pack,evd,given)
    assert result.verdicts["security"]=="UNKNOWN"
    assert not any('record="EXAMPLE-one"' in r and "MISSING_TREATED_NORMAL" in r for r in result.reasons)


@pytest.mark.parametrize("offset,errors,expected",[(98,1,"PASS"),(100,0,"PASS"),(101,0,"UNKNOWN"),(100,1,"UNKNOWN"),(-10,0,"UNKNOWN")])
def test_collection_interval_boundaries(pack,offset,errors,expected):
    evd,given=unit(pack);o=row(evd,"normal")["observations"]
    o["received_at"]=iso(offset);o["time_errors_ms"].update(occurred_at=errors,received_at=errors)
    assert outcome(pack,evd,given).verdicts["security"]==expected


def test_actual_clock_error_is_not_clamped_to_policy_and_unknown_case_not_invented(pack):
    evd,given=unit(pack);o=row(evd,"clock_uncertain")["observations"]
    o["time_errors_ms"]["received_at"]=4
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"
    o["time_errors_ms"].pop("received_at")
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"
    o["time_errors_ms"]["received_at"]=1;o["dependent"]["security"]="PASS"
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"  # no new FAIL rule for uncertainty-only


@pytest.mark.parametrize("kind",["normalization","dependent_pass","alert_absent"])
def test_confirmed_protection_failure_survives_other_case_and_policy_gaps(pack,kind):
    evd,given=unit(pack);r=row(evd);o=r["observations"]
    if kind=="normalization": o["missing_handling"]="treated_normal";given.pop("alert_deadline_ms")
    elif kind=="dependent_pass": o["dependent"]["security"]="PASS";given.pop("detection_deadline_ms")
    else: o.update(alert="absent",alert_received_at=None);given.pop("detection_deadline_ms")
    evd.observations[OBSERVATION].value["records"].remove(row(evd,"normal","EXAMPLE-two"))
    result=outcome(pack,evd,given)
    assert result.verdicts["security"]=="FAIL"
    assert any(("REQUIRED_ALERT_ABSENT" if kind=="alert_absent" else "MISSING_TREATED_NORMAL") in r for r in result.reasons)


@pytest.mark.parametrize("field,value",[("alert_window_start_at",None),("alert_window_start_at",iso(1050)),
    ("alert_window_end_at",iso(1120)),("alert_window","unknown"),("alert_receiver",None),
    ("alert_source","EXAMPLE collector"),("alert_window_detail",None)])
def test_missing_alert_requires_its_own_complete_receiver_query(pack,field,value):
    evd,given=unit(pack);o=row(evd)["observations"];o.update(alert="absent",alert_received_at=None);o[field]=value
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"


def test_record_missing_without_proven_bad_handling_is_not_obs_fail(pack):
    evd,given=unit(pack);o=row(evd)["observations"]
    o.update(missing_handling="unknown",alert="unknown");o["dependent"]["security"]=None
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"


def test_same_event_occurrence_conflict_does_not_hide_separate_violation(pack):
    evd,given=unit(pack);a,b=row(evd),row(evd,item="EXAMPLE-two")
    a["observations"]["event_id"]=b["observations"]["event_id"]="EXAMPLE-shared-event"
    b["observations"]["occurred_at"]=iso(1050)
    a["observations"]["missing_handling"]="treated_normal"
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"
    a["observations"]["event_id"]="EXAMPLE-independent-event"
    assert outcome(pack,evd,given).verdicts["security"]=="FAIL"


def test_record_receipt_differences_are_not_shared_event_conflicts(pack):
    evd,given=unit(pack);a,b=row(evd,"normal"),row(evd,"normal","EXAMPLE-two")
    a["observations"]["event_id"]=b["observations"]["event_id"]="EXAMPLE-shared-normal"
    b["observations"]["received_at"]=iso(95)
    assert outcome(pack,evd,given).verdicts["security"]=="PASS"


def test_unconfirmed_other_clock_cannot_erase_supported_failure(pack):
    evd,given=unit(pack);a,b=row(evd),row(evd,item="EXAMPLE-two")
    a["observations"]["event_id"]=b["observations"]["event_id"]="EXAMPLE-shared-event"
    a["observations"]["missing_handling"]="treated_normal"
    b["observations"].update(occurred_at=iso(1050),clock_basis="unknown")
    assert outcome(pack,evd,given).verdicts["security"]=="FAIL"


def test_dependent_conflict_only_blocks_dependent_failure_premise(pack):
    evd,given=unit(pack);a,b=row(evd),row(evd,item="EXAMPLE-two")
    a["observations"]["event_id"]=b["observations"]["event_id"]="EXAMPLE-shared-fault"
    for r in (a,b): r["observations"]["dependent"]["test_id"]="EXAMPLE-shared-dependent"
    a["observations"]["dependent"]["security"]="PASS"
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"
    a["observations"]["missing_handling"]="treated_normal"
    assert outcome(pack,evd,given).verdicts["security"]=="FAIL"


def test_independent_dependent_fail_is_preserved_without_rewriting_other_results(pack):
    evd,given=unit(pack);r=row(evd);r["observations"]["dependent"].update(security="FAIL",independent_failure=True)
    r["evidence"].append({"claim":"dependent_failure","sha256":"0"*64,"locator":"L30","basis":"EXAMPLE independent violation"})
    assert outcome(pack,evd,given).verdicts["security"]=="PASS"
    r["evidence"].pop()
    assert outcome(pack,evd,given).verdicts["security"]=="UNKNOWN"


def test_raw_fail_and_unknown_contract_and_legacy_definition(pack):
    evd,given=unit(pack,verdicts={a:"UNKNOWN" for a in ("security","functional","performance")})
    assert all(v=="UNKNOWN" for v in outcome(pack,evd,given).verdicts.values())
    evd.observations["declared_verdicts"].value=dict.fromkeys(("security","functional","performance"),"FAIL")
    evd.observations[OBSERVATION].value={"contract":"invalid"};evd.artifacts=[]
    assert all(v=="FAIL" for v in outcome(pack,evd,{}).verdicts.values())
    old=pack.checks["OBS-11"].model_copy(update={"policy_params":[]})
    legacy=_manual_evidence(old,{"security":"PASS"})
    assert DeclaredJudge().judge(old,legacy,{},{}).verdicts["security"]=="PASS"


@pytest.mark.parametrize("bad",["2026-10-19T00:00:00","2026-10-19T00:00:00.0001Z","2026-02-30T00:00:00Z",
    "2026-10-19T00:00:00-00:00","2026-10-19T00:00:00+09:99",3,True])
def test_strict_timestamp_shape_never_rounds_or_adds_timezone(bad):
    with pytest.raises(ValueError): timestamp(bad)
    assert timestamp("2026-10-19T09:00:00.123+09:00")==timestamp("2026-10-19T00:00:00.123Z")


@pytest.mark.parametrize("version",["guard-manual-evidence/2","guard-manual-evidence/3"])
@pytest.mark.parametrize("ref",["policy","pack"])
def test_v2_and_v3_keep_full_pinned_refs(tmp_path,pack,version,ref):
    st,plan,doc=setup(tmp_path,pack);doc["template"]=version
    if version.endswith('/2'):entry(doc,"OBS-11").pop("audit_evidence")
    doc[ref]["revision"]+=1
    with pytest.raises(ManualEvidenceError,match="Ref"):
        import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert not list(st.iter_kind("EVD"))


@pytest.mark.parametrize("version",["guard-manual-evidence/1","guard-manual-evidence/2"])
def test_old_formats_cannot_smuggle_audit_even_when_unfilled(tmp_path,pack,version):
    st,plan,doc=setup(tmp_path,pack);doc["template"]=version
    entry(doc,"OBS-11")["verdicts"]={}
    with pytest.raises(ManualEvidenceError,match="v3"):
        import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert not list(st.iter_kind("EVD"))


def test_v2_still_imports_existing_sup_lif_item_evidence(tmp_path,pack):
    from tests.test_policy_m4 import setup as setup_items
    st,plan,doc=setup_items(tmp_path,pack);fill(doc);doc['template']='guard-manual-evidence/2'
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    results={r.check_id:r for r in judge_plan(st,pack,plan,T0)}
    assert results['SUP-14'].verdicts.security==results['LIF-07'].verdicts.security=='PASS'


@pytest.mark.parametrize('version',['guard-manual-evidence/1','guard-manual-evidence/2'])
def test_legacy_payload_without_audit_under_new_definition_is_unknown(tmp_path,pack,version):
    st,plan,doc=setup(tmp_path,pack);doc['template']=version;entry(doc,'OBS-11').pop('audit_evidence')
    if version.endswith('/1'):doc.pop('policy');doc.pop('pack')
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    results={r.check_id:r for r in judge_plan(st,pack,plan,T0)}
    assert results['OBS-11'].verdicts.security=='UNKNOWN' and results['SUP-03'].verdicts.security=='PASS'


@pytest.mark.parametrize("issue",["foreign_check","foreign_null","unfilled_foreign_null","foreign_side","foreign_item","duplicate","foreign_attachment","wrong_enum","wrong_attempt","wrong_time_error","wrong_dependent","reserved_observation"])
def test_invalid_batch_never_partially_imports(tmp_path,pack,issue):
    st,plan,doc=setup(tmp_path,pack);e=entry(doc,"OBS-11");r=e["audit_evidence"]["records"][0]
    if issue=="foreign_check":entry(doc,"SUP-03")["audit_evidence"]=deepcopy(e["audit_evidence"])
    elif issue in ("foreign_null","unfilled_foreign_null"):
        entry(doc,"SUP-03")["audit_evidence"]=None
        if issue=="unfilled_foreign_null":entry(doc,"SUP-03")["verdicts"]={}
    elif issue=="foreign_side": e["side"]="candidate"
    elif issue=="foreign_item":r["record_id"]="EXAMPLE-not-required"
    elif issue=="duplicate": e["audit_evidence"]["records"].append(deepcopy(r))
    elif issue=="foreign_attachment":r["evidence"][0]["attachment"]="other.txt"
    elif issue=="wrong_enum":r["observations"]["record"]="success"
    elif issue=="wrong_attempt":r["observations"]["attempted"]="true"
    elif issue=="wrong_time_error":r["observations"]["time_errors_ms"]["occurred_at"]=True
    elif issue=="wrong_dependent":r["observations"]["dependent"]={"security":"success"}
    else:
        pack=pack.model_copy(deep=True)
        from guard.models.checkdef import ObsSpec
        pack.checks['OBS-11'].required_observations=[ObsSpec(key=OBSERVATION,type='str')]
    with pytest.raises(ManualEvidenceError):import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert not list(st.iter_kind("EVD")) and not list(st.iter_kind("RES"))


def test_v3_cli_report_integrity_idempotence_and_meaningful_hash(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack);p=write(tmp_path,doc);runner=CliRunner()
    def ok(args):
        r=runner.invoke(app,args);assert r.exit_code==0,r.output+repr(r.exception)
    common=['--store',str(st.root),'--pack',str(PACK_DIR)]
    ok(['evidence','import',str(p),*common])
    args=['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]
    ok(args);before=snapshot(st);ok(args);assert snapshot(st)==before
    ok(['report','--plan',plan.id,'--json',str(tmp_path/'r.json'),'--html',str(tmp_path/'r.html'),*common])
    data=json.loads((tmp_path/'r.json').read_text(encoding='utf-8'))
    obs=next(r for r in data['by_check'] if r['check_id']=='OBS-11')
    assert obs['single']['security']=='PASS' and obs['single']['functional']=='UNKNOWN' and not data['official']
    assert 'AUDIT_COLLECTION:' in (tmp_path/'r.html').read_text(encoding='utf-8')
    assert 'EXAMPLE complete event-correlated query' not in (tmp_path/'r.html').read_text(encoding='utf-8')
    assert all(e.execution.executor.version=='0.5.0' and e.data_origin=='DECLARED' for e in st.iter_kind('EVD'))
    assert all(r.judge.version=='0.8.0' for r in st.iter_kind('RES'))
    previous=next(e for e in st.iter_kind('EVD') if e.check_id=='OBS-11')
    entry(doc,'OBS-11')['audit_evidence']['records'][0]['observations']['case_detail']='EXAMPLE edited meaningful claim'
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    latest=next(e for e in st.find_latest('EVD',lambda e:True) if e.check_id=='OBS-11')
    assert latest.revision==2 and latest.execution.executor.config_sha256!=previous.execution.executor.config_sha256
    assert st.resolve(previous.ref()).to_dict()==previous.to_dict() and not st.verify()


def test_update_sides_remain_separate_and_fail_not_hidden(tmp_path,pack):
    st,plan,doc=setup(tmp_path,pack,mode='update')
    e=entry(doc,'OBS-11',side='candidate')
    next(r for r in e['audit_evidence']['records'] if r['case']=='collection_fault')['observations']['missing_handling']='treated_normal'
    import_evidence(st,pack,write(tmp_path,doc),now=T0);judge_plan(st,pack,plan,T0)
    rpt=build_report(st,pack,plan,T0).to_dict();r=next(r for r in rpt['by_check'] if r['check_id']=='OBS-11')
    assert r['baseline']['security']=='PASS' and r['candidate']['security']=='FAIL'
    assert r['comparison']=='NOT_COMPARABLE'
    assert any('OBS-11/candidate:' in reason and 'MISSING_TREATED_NORMAL' in reason for reason in rpt['limitations'])


def test_html_escapes_record_ids_and_hides_raw_locations(tmp_path,pack):
    unsafe='EXAMPLE-<script>alert(1)</script>'
    st,plan,doc=setup(tmp_path,pack,items=(unsafe,))
    import_evidence(st,pack,write(tmp_path,doc),now=T0);judge_plan(st,pack,plan,T0)
    report=build_report(st,pack,plan,T0).to_dict()
    assert unsafe in json.dumps(report,ensure_ascii=False)
    html=render_html(report)
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html
    assert 'L1-L20' not in html and 'EXAMPLE synthetic clock' not in html


@pytest.mark.parametrize('normal,fail',[('failed',False),('not_attempted',False),('failed',True)])
def test_obs_uses_existing_core_pass_gate_and_keeps_fail(tmp_path,pack,normal,fail):
    st,plan,doc=setup(tmp_path,pack);e=entry(doc,'OBS-11');e['normal_control']['result']=normal
    if fail:next(r for r in e['audit_evidence']['records'] if r['case']=='collection_fault')['observations']['missing_handling']='treated_normal'
    import_evidence(st,pack,write(tmp_path,doc),now=T0)
    res=next(r for r in judge_plan(st,pack,plan,T0) if r.check_id=='OBS-11')
    assert res.verdicts.security==('FAIL' if fail else 'UNKNOWN')
