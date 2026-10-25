"""M13 valid unknown is distinct from malformed shape; all fixtures synthetic."""
from copy import deepcopy
import json
import hashlib

import pytest
from guard.collectors.manual import import_evidence
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.judges import manual_cache_context as cache
from guard.judges.declared import DeclaredJudge
from guard.models import Verdict
from tests.builders import T0, policy
from tests.test_policy_m4 import snapshot
from tests.test_policy_m12 import AXES, CONTEXTS, baseline_pack, setup, unit, write_input


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


VALID = [
    ("object_change", "unchanged", False, False), ("object_change", "observed", True, True),
    ("object_change", "unknown", False, False),
    ("other_conditions", "same", True, True), ("other_conditions", "changed", False, False),
    ("other_conditions", "unknown", False, False),
    ("key_relation", "same", False, False), ("key_relation", "different", True, True),
    ("key_relation", "unknown", False, False),
    ("cache_effect", "before_context_hit", False, False), ("cache_effect", "isolated_after_change", True, True),
    ("cache_effect", "after_context_hit", False, False), ("cache_effect", "old_context_reused", False, False),
    ("cache_effect", "unknown", False, False),
    ("inference", "succeeded", True, True), ("inference", "failed", True, False),
    ("inference", "not_attempted", True, False), ("inference", "unknown", True, False),
]


@pytest.mark.parametrize("field,value,secure,functional", VALID)
@pytest.mark.parametrize("raw", ["PASS", "FAIL", "UNKNOWN"])
def test_all_allowed_state_values_and_raw_axis_ceiling(pack,field,value,secure,functional,raw):
    check=pack.checks["REQ-16"];evd,vals=unit(check,raw,CONTEXTS)
    evd.observations[cache.OBSERVATION].value["records"][1][field]=value
    before=(evd.to_dict(),deepcopy(vals),check.model_dump(mode="json"))
    r=DeclaredJudge().judge(check,evd,{},vals)
    expected={a: raw if raw!="PASS" else ("PASS" if good else "UNKNOWN")
              for a,good in (("security",secure),("functional",functional),("performance",False))}
    assert r.verdicts==expected
    assert not any("RECORD_INCOMPLETE" in s or "ENUM_INVALID" in s for s in r.reasons)
    assert DeclaredJudge().judge(check,evd,{},vals)==r
    assert (evd.to_dict(),vals,check.model_dump(mode="json"))==before
    if field=="inference" and value!="succeeded":
        assert any("INFERENCE_UNRESOLVED" in s for s in r.reasons)


@pytest.mark.parametrize("field", list(cache.ENUMS))
@pytest.mark.parametrize("bad", [None,False,[],0,""," "," unknown","EXAMPLE-invalid"])
def test_bad_enum_shape_remains_blocked_and_not_echoed(pack,field,bad):
    check=pack.checks["REQ-16"];evd,vals=unit(check)
    evd.observations[cache.OBSERVATION].value["records"][1][field]=bad
    r=DeclaredJudge().judge(check,evd,{},vals)
    assert all(v=="UNKNOWN" for v in r.verdicts.values())
    code="ENUM_INVALID" if bad=="EXAMPLE-invalid" else "RECORD_INCOMPLETE"
    assert any(code in s for s in r.reasons)
    assert "EXAMPLE-invalid" not in " ".join(r.reasons)


@pytest.mark.parametrize("field", ["trial_id","event_id","target_scope","workload_ref","context_before",
                                   "context_after","observation_detail","identity_basis","sharing_group_ref","locator","basis"])
def test_unknown_identifier_or_basis_is_not_a_valid_evidence_state(pack,field):
    check=pack.checks["REQ-16"];evd,vals=unit(check)
    p=evd.observations[cache.OBSERVATION].value
    if field in ("identity_basis","sharing_group_ref"):p[field]="unknown"
    elif field in ("locator","basis"):p["records"][1]["evidence"][0][field]="unknown"
    else:p["records"][1][field]="unknown"
    r=DeclaredJudge().judge(check,evd,{},vals)
    assert all(v=="UNKNOWN" for v in r.verdicts.values())


@pytest.mark.parametrize("failed_axis", AXES)
@pytest.mark.parametrize("scenario", ["unknown","missing_other_context","conflict","dependency"])
def test_independent_declared_fail_survives_unresolved_other_axes(pack,monkeypatch,failed_axis,scenario):
    check=pack.checks["REQ-16"];evd,vals=unit(check,contexts=CONTEXTS)
    evd.observations["declared_verdicts"].value={"security":"PASS","functional":"UNKNOWN","performance":"PASS"}
    evd.observations["declared_verdicts"].value[failed_axis]="FAIL"
    rows=evd.observations[cache.OBSERVATION].value["records"]
    if scenario=="unknown":rows[1]["inference"]="unknown"
    elif scenario=="missing_other_context":rows.pop()
    elif scenario=="conflict":rows[1]["cache_effect"]="old_context_reused"
    else:
        def error(*args,**kwargs):raise RuntimeError("EXAMPLE-sensitive-detail")
        monkeypatch.setattr(cache,"evaluate",error)
    r=DeclaredJudge().judge(check,evd,{},vals)
    assert r.verdicts[failed_axis]=="FAIL"
    if failed_axis!="functional":assert r.verdicts["functional"]=="UNKNOWN"
    assert "EXAMPLE-sensitive-detail" not in " ".join(r.reasons)


@pytest.mark.parametrize("gate", ["normal","challenge","attachment","notes","cpu","legacy","partial"])
def test_unknown_inference_does_not_bypass_existing_contract_gates(pack,gate):
    check=pack.checks["REQ-16"].model_copy(deep=True);evd,vals=unit(check)
    evd.observations[cache.OBSERVATION].value["records"][1]["inference"]="unknown"
    if gate=="normal":evd.normal_control.succeeded=False
    elif gate=="challenge":evd.challenges[0].attempted=False
    elif gate=="attachment":evd.artifacts=[]
    elif gate=="notes":evd.observations["declared_observation_notes"].value={}
    elif gate=="cpu":
        from guard.models.objects import CpuProfile
        evd.comparability.cpu=CpuProfile(architecture="EXAMPLE-x86",model="EXAMPLE-cpu",system="EXAMPLE-system",kernel="EXAMPLE-kernel",threads=1)
    elif gate=="legacy":check.policy_params=[];check.required_observations=[]
    else:check.required_observations=[]
    pol=policy(pack.ref(),values={"REQ-16":vals})
    r=judge_evidence(check,evd.seal(),pol,T0)
    if gate=="cpu":
        assert r.verdicts.security=="PASS" and r.verdicts.functional==r.verdicts.performance=="UNKNOWN"
    elif gate=="legacy":assert r.verdicts.security==r.verdicts.functional==r.verdicts.performance=="PASS"
    else:assert r.verdicts.security==r.verdicts.functional=="UNKNOWN"


@pytest.mark.parametrize("version,mode",[(1,"audit"),(2,"audit"),(3,"audit"),(3,"update")])
@pytest.mark.parametrize("newline",[b"\n",b"\r\n"])
def test_real_import_unknown_state_reports_refs_sides_history_and_bytes(tmp_path,pack,version,mode,newline):
    st,plan,doc=setup(tmp_path,pack,mode)
    doc["template"]=f"guard-manual-evidence/{version}"
    if version==1:doc.pop("policy");doc.pop("pack")
    data=b"EXAMPLE line one"+newline+b"EXAMPLE line two"+newline
    digest=hashlib.sha256(data).hexdigest()
    for entry in doc["checks"]:
        for row in entry["required_observations"][0]["value"]["records"]:
            row["evidence"][0]["sha256"]=digest
        if entry["side"]==("candidate" if mode=="update" else "single"):
            entry["required_observations"][0]["value"]["records"][1]["inference"]="unknown"
    path=write_input(tmp_path,doc);(tmp_path/"EXAMPLE.txt").write_bytes(data)
    import_evidence(st,pack,path,now=T0)
    results=judge_plan(st,pack,plan,T0)
    for r in results:
        assert r.verdicts.security=="PASS"
        assert r.verdicts.functional==("UNKNOWN" if r.side==("candidate" if mode=="update" else "single") else "PASS")
        assert r.verdicts.performance=="UNKNOWN"
        assert not any("RECORD_INCOMPLETE" in s for s in r.reasons)
    evds=list(st.iter_kind("EVD"))
    assert all(e.artifacts[0].sha256==digest and e.artifacts[0].bytes==len(data) for e in evds)
    before=snapshot(st);judge_plan(st,pack,plan,T0)
    assert snapshot(st)==before and not st.verify()
    rpt=build_report(st,pack,plan,T0)
    assert not rpt.official and "CACHE_CONTEXT_INFERENCE_UNRESOLVED" in json.dumps(rpt.to_dict(),ensure_ascii=False)
    row=next(r for r in rpt.by_check if r.check_id=="REQ-16")
    assert row.evidence_source=="manual"
    if mode=="update":assert row.functional_comparison=="NOT_COMPARABLE"
