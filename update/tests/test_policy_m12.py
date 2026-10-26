"""M12 limited DECLARED cache contract, synthetic fixtures only, no engine/cache execution."""
from copy import deepcopy
import csv
import hashlib
import json

import pytest
import yaml

from guard.collectors.manual import build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.core.select import PolicyValueError, select
from guard.judges.declared import DeclaredJudge
from guard.judges import manual_cache_context as cache
from guard.models.objects import CpuProfile, ExecutorInfo, Observation
from guard.store import Store
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, policy
from tests.prepared_fixtures import manual_setup_seed, put_baseline_snapshot
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import fill, snapshot, write

AXES = ("security", "functional", "performance")
CONTEXTS = ("model_generation", "adapter_content", "media_identity")
ATTACHMENT = b"EXAMPLE synthetic source; no engine executed\n"


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def payload(contexts=("model_generation",), digest="0"*64):
    rows = []
    for context in contexts:
        for case in ("same_context", "changed_context", "post_control"):
            same = case == "same_context"
            rows.append(dict(context_id=context, case=case, trial_id="EXAMPLE-trial",
                event_id=f"EXAMPLE-{context}-{case}", target_scope="EXAMPLE-worker", workload_ref="EXAMPLE-private",
                context_before="EXAMPLE-old", context_after="EXAMPLE-old" if same else "EXAMPLE-new",
                object_change="unchanged" if same else "observed", other_conditions="same",
                key_relation="same" if same else "different",
                cache_effect={"same_context":"before_context_hit", "changed_context":"isolated_after_change",
                              "post_control":"after_context_hit"}[case],
                inference="succeeded", observation_detail=f"EXAMPLE {case}: consumer/object, entry/lookup, output and ordered event linkage",
                evidence=[dict(sha256=digest, locator=f"EXAMPLE-{context}-{case}:L1-L4", basis="EXAMPLE synthetic event sequence")]))
    return dict(version="guard-cache-context-evidence/1", profile="same_authenticated_group_private",
                sharing_group_ref="EXAMPLE-authenticated-group", identity_basis="EXAMPLE identity-to-group attachment mapping", records=rows)


def unit(check, raw="PASS", contexts=("model_generation",)):
    evd = _manual_evidence(check, dict.fromkeys(AXES, raw))
    evd.execution.executor = ExecutorInfo(name="manual-entry", version="0.5.0",
        config_sha256="0"*64, default_judge="declared")
    evd.challenges = [type(evd.challenges[0])(step=s, attempted=True) for s in check.challenge_steps]
    evd.observations[cache.OBSERVATION] = Observation(value=payload(contexts), collected=True)
    return evd.seal(), {cache.POLICY:list(contexts)}


def result(check, evd, vals):
    return DeclaredJudge().judge(check, evd, {}, vals)


def test_registration_catalog_template_and_no_automation(pack):
    c=pack.checks["REQ-16"]
    assert (pack.manifest.revision,c.revision)==(27,3) and not pack.reviewed
    assert [(p.key,p.type,p.required) for p in c.policy_params]==[("required_cache_contexts","list[str]",True)]
    assert [(o.key,o.type) for o in c.required_observations]==[("declared_cache_context_evidence","dict")]
    assert c.executor is c.judge is None and len(pack.checks)==77
    assert sum(bool(c.policy_params) for c in pack.active_checks())== 25
    assert sum(len(c.policy_params) for c in pack.active_checks())== 54
    assert {c.id for c in pack.active_checks() if c.executor}=={"REQ-01", "REQ-03", "PLT-01", "PLT-02", "PLT-04", "REQ-09", "REQ-10", "REQ-13", "OBS-03", "REQ-07", "REQ-11", "OBS-09", "SUP-04", "SUP-03"}
    text=build_policy_template(pack); doc=yaml.safe_load(text)
    assert doc["values"]["REQ-16"]=={} and "required_cache_contexts" in text
    assert doc["status"]=="DRAFT" and doc["confirmed_by"] is None
    assert not pack.checks["REQ-17"].policy_params and pack.checks["REQ-15"].executor is None
    rows=list(csv.DictReader((ROOT/"docs/policy_catalog.csv").open(encoding="utf-8",newline="")))
    assert len(rows)==327 and sum(len(json.loads(r["check_ids"])) for r in rows)==619
    row=next(r for r in rows if r["proposed_policy_id"]=="P-DRAFT-CACHE-CONTEXT")
    assert json.loads(row["check_ids"])==["REQ-15","REQ-16","REQ-17"]
    assert json.loads(row["current_param_paths"])==["values.REQ-16.required_cache_contexts"]
    assert row["classification"]=="a"
    assert next(r for r in rows if r["proposed_policy_id"]=="P-DRAFT-CACHE-PUBLIC-PREFIX")["classification"]=="a"


@pytest.mark.parametrize("bad", [None,False,"model_generation",[1],{},[True],1.0])
def test_select_type_error_even_outside_scope(pack,bad):
    pol=policy(pack.ref(),scope={"include":["SUP-05"]},values={"REQ-16":{cache.POLICY:bad}})
    with pytest.raises(PolicyValueError,match="REQ-16"):
        select(pack,[environment()],pol)


@pytest.mark.parametrize("contexts", [(c,) for c in CONTEXTS]+[CONTEXTS,tuple(reversed(CONTEXTS))])
def test_complete_contract_is_pure_and_order_does_not_rewrite_policy(pack,contexts):
    c=pack.checks["REQ-16"]; evd,vals=unit(c,contexts=contexts)
    before=(c.model_dump(mode="json"),evd.to_dict(),deepcopy(vals))
    r=result(c,evd,vals)
    assert r.verdicts==dict(security="PASS",functional="PASS",performance="UNKNOWN")
    assert result(c,evd,vals)==r
    assert (c.model_dump(mode="json"),evd.to_dict(),vals)==before


@pytest.mark.parametrize("bad", [None,False,[],[""],[" model_generation"],["model_generation","model_generation"],
                                ["EXAMPLE-unsupported"],["model_generation","EXAMPLE-unsupported"],[1]])
@pytest.mark.parametrize("raw",["PASS","FAIL","UNKNOWN"])
def test_invalid_policy_never_erases_fail_or_normalizes(pack,bad,raw):
    c=pack.checks["REQ-16"]; evd,vals=unit(c,raw)
    vals[cache.POLICY]=deepcopy(bad); before=deepcopy(vals)
    r=result(c,evd,vals)
    assert all(v==("UNKNOWN" if raw=="PASS" else raw) for v in r.verdicts.values())
    assert vals==before


SCENARIOS=("uncollected","missing","invalid","version","profile","identity","records","case_missing","duplicate",
    "foreign","event","trial","target","workload","pair","actual_object","other_conditions","key_only",
    "miss_only","old_context_reused","inference","link","foreign_hash","locator","basis","enum","no_detail",
    "extra","unknown","unhashable","partial_coverage")


def damage(evd,scenario):
    obs=evd.observations[cache.OBSERVATION]; data=obs.value; rows=data["records"]; row=rows[1]
    if scenario=="uncollected": obs.collected=False; obs.value=None
    elif scenario=="missing": evd.observations.pop(cache.OBSERVATION)
    elif scenario=="invalid": obs.value=[]
    elif scenario=="version": data["version"]="EXAMPLE-unknown"
    elif scenario=="profile": data["profile"]="EXAMPLE-public-or-cross-group"
    elif scenario=="identity": data["identity_basis"]=""
    elif scenario=="records": data["records"]={}
    elif scenario=="case_missing": rows.pop()
    elif scenario=="duplicate": rows.append(deepcopy(rows[0]))
    elif scenario=="foreign": row["context_id"]="EXAMPLE-foreign"
    elif scenario=="event": row["event_id"]=rows[0]["event_id"]
    elif scenario in ("trial","target","workload"): row[{"trial":"trial_id","target":"target_scope","workload":"workload_ref"}[scenario]]="EXAMPLE-other"
    elif scenario=="pair": rows[2]["context_after"]="EXAMPLE-other-generation"
    elif scenario=="actual_object": row["object_change"]="unknown"
    elif scenario=="other_conditions": row["other_conditions"]="changed"
    elif scenario=="key_only": row["cache_effect"]="unknown"
    elif scenario=="miss_only": rows.pop()
    elif scenario=="old_context_reused": row["cache_effect"]="old_context_reused"
    elif scenario=="inference": rows[2]["inference"]="failed"
    elif scenario=="link": row["evidence"]=[]
    elif scenario=="foreign_hash": row["evidence"][0]["sha256"]="a"*64
    elif scenario in ("locator","basis"): row["evidence"][0][scenario]=" "
    elif scenario=="enum": row["cache_effect"]="EXAMPLE-unrecognized"
    elif scenario=="no_detail": row["observation_detail"]=" "
    elif scenario=="extra": row["EXAMPLE-extra"]="EXAMPLE"
    elif scenario=="unknown": row["cache_effect"]="unknown"
    elif scenario=="unhashable": row["event_id"]=[]
    elif scenario=="partial_coverage": data["records"]=rows[:3]


@pytest.mark.parametrize("scenario",SCENARIOS)
@pytest.mark.parametrize("raw",["PASS","FAIL","UNKNOWN"])
def test_malformed_missing_conflicting_evidence_only_caps_pass(pack,scenario,raw):
    c=pack.checks["REQ-16"]; evd,vals=unit(c,raw,contexts=CONTEXTS)
    damage(evd,scenario); before=evd.to_dict()
    r=result(c,evd,vals)
    expected="UNKNOWN" if raw=="PASS" else raw
    assert r.verdicts["functional"]==expected
    # A failed inference does not itself disprove separately declared isolation.
    assert r.verdicts["security"]==("PASS" if raw=="PASS" and scenario=="inference" else expected)
    assert r.verdicts["performance"]==expected
    assert evd.to_dict()==before
    assert any(reason.startswith("POLICY_CONTEXT: CACHE_CONTEXT_") for reason in r.reasons)
    if scenario=="old_context_reused":
        assert any("CACHE_CONTEXT_CONTRADICTION" in reason for reason in r.reasons)


@pytest.mark.parametrize("raw",["PASS","FAIL","UNKNOWN"])
def test_dependency_error_and_no_io_preserve_fail(pack,monkeypatch,raw):
    c=pack.checks["REQ-16"]; evd,vals=unit(c,raw)
    def forbidden(*args,**kwargs): raise RuntimeError("EXAMPLE-secret-must-not-leak")
    # Evaluation must not read a file or perform a network request.
    monkeypatch.setattr("builtins.open",forbidden)
    monkeypatch.setattr("socket.socket",forbidden)
    assert result(c,evd,vals).verdicts["security"]==raw
    monkeypatch.setattr(cache,"evaluate",forbidden)
    r=result(c,evd,vals)
    assert all(v==("UNKNOWN" if raw=="PASS" else raw) for v in r.verdicts.values())
    assert "EXAMPLE-secret" not in " ".join(r.reasons)


@pytest.mark.parametrize("kind",["legacy","partial_policy","partial_observation","wrong_type","other"])
def test_contract_markers_and_existing_checks(pack,kind):
    c=pack.checks["REQ-16"].model_copy(deep=True); evd,vals=unit(c)
    if kind=="legacy": c.policy_params=[]; c.required_observations=[]
    elif kind=="partial_policy": c.required_observations=[]
    elif kind=="partial_observation": c.policy_params=[]
    elif kind=="wrong_type": c.required_observations[0].type="str"
    else: c=pack.checks["SUP-05"]; evd=_manual_evidence(c,dict.fromkeys(AXES,"PASS")); vals={}
    r=result(c,evd,vals)
    expected="PASS" if kind in ("legacy","other") else "UNKNOWN"
    assert all(v==expected for v in r.verdicts.values())


def setup(tmp_path,pack,mode="audit"):
    envs=[environment()]
    if mode=="update": envs.append(environment(id="ENV-CANDIDATE"))
    pol=policy(pack.ref(),scope={"include":["REQ-16"]},values={"REQ-16":{cache.POLICY:list(CONTEXTS)}})
    def create(folder):
        st=Store(folder/"store")
        st.put_many([*envs,pol]);put_baseline_snapshot(st)
        plan=select(pack,envs,pol,mode=mode);st.put(plan)
        doc=fill(build_template(st,pack,plan))
        for entry in doc["checks"]:
            entry["required_observations"][0].update(value=payload(CONTEXTS,hashlib.sha256(ATTACHMENT).hexdigest()),collected=True)
        return st,plan,doc

    st,plan,doc=manual_setup_seed("m12", {
        "pack":pack.model_dump(mode="json"),"environments":[e.to_dict() for e in envs],
        "policy":pol.to_dict(),"mode":mode,"attachment":ATTACHMENT.hex(),
    },tmp_path,create)
    assert plan.item("REQ-16").gaps==["NO_EXECUTOR"]
    return st,plan,doc


def write_input(tmp_path,doc):
    path=write(tmp_path,doc)
    # Hash the exact attached bytes; text-mode CRLF conversion is not the payload's LF hash.
    (tmp_path/"EXAMPLE.txt").write_bytes(ATTACHMENT)
    return path


@pytest.mark.parametrize("version,mode",[(1,"audit"),(2,"audit"),(3,"audit"),(3,"update")])
def test_actual_import_rejudge_history_hash_sides_and_report(tmp_path,pack,version,mode):
    st,plan,doc=setup(tmp_path,pack,mode)
    doc["template"]=f"guard-manual-evidence/{version}"
    if version==1: doc.pop("policy");doc.pop("pack")
    path=write_input(tmp_path,doc);import_evidence(st,pack,path,now=T0)
    good=judge_plan(st,pack,plan,T0)
    assert all(r.verdicts.security==r.verdicts.functional=="PASS" and r.verdicts.performance=="UNKNOWN" for r in good)
    before=snapshot(st)
    side="candidate" if mode=="update" else "single"
    previous=next(e for e in st.find_latest("EVD",lambda e:e.side==side))
    entry=next(e for e in doc["checks"] if e["side"]==side)
    entry["required_observations"][0]["value"]["records"][1]["cache_effect"]="old_context_reused"
    import_evidence(st,pack,write_input(tmp_path,doc),now=T0)
    latest=next(e for e in st.find_latest("EVD",lambda e:e.side==side))
    assert latest.content_sha256!=previous.content_sha256
    assert latest.execution.executor.config_sha256==previous.execution.executor.config_sha256
    results=judge_plan(st,pack,plan,T0)
    for r in results:
        assert r.verdicts.security==("UNKNOWN" if r.side==side else "PASS")
    rpt=build_report(st,pack,plan,T0); text=json.dumps(rpt.to_dict(),ensure_ascii=False)
    assert not rpt.official and "CACHE_CONTEXT_CONTRADICTION" in text
    assert next(r for r in rpt.by_check if r.check_id=="REQ-16").evidence_source=="manual"
    assert "REQ15" in text and "REQ17" in text
    for obj in before.values(): assert st.get(obj["id"],obj["revision"]).to_dict()==obj
    after=snapshot(st);judge_plan(st,pack,plan,T0)
    assert snapshot(st)==after and not st.verify()


def test_core_gates_missing_policy_and_cpu_remain(pack):
    c=pack.checks["REQ-16"]; evd,vals=unit(c)
    missing=select(pack,[environment()],policy(pack.ref(),scope={"include":["REQ-16"]},values={}))
    assert set(missing.item("REQ-16").gaps)=={"NO_POLICY_VALUE","NO_EXECUTOR"}
    assert result(c,evd,{}).verdicts["security"]=="UNKNOWN"
    pol=policy(pack.ref(),values={"REQ-16":vals})
    evd.normal_control.succeeded=False
    r=judge_evidence(c,evd.seal(),pol,T0)
    assert r.verdicts.security==r.verdicts.functional=="UNKNOWN"
    evd.observations["declared_verdicts"].value=dict.fromkeys(AXES,"FAIL")
    evd.comparability.cpu=CpuProfile(architecture="EXAMPLE-x86",model="EXAMPLE-cpu",
        system="EXAMPLE-system",kernel="EXAMPLE-kernel",threads=1)
    r=judge_evidence(c,evd.seal(),pol,T0)
    assert r.verdicts.security==r.verdicts.functional=="FAIL" and r.verdicts.performance=="UNKNOWN"
