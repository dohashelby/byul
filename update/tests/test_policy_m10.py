"""M10 bounded submitted-input contract; synthetic observations, no engine runs."""
from copy import deepcopy
import csv
import json

import pytest
import yaml

from guard.collectors.manual import ManualEvidenceError, build_template, import_evidence
from guard.collectors.policy_template import build_policy_template
from guard.core.compare import compare_results
from guard.core.judge import judge_evidence, judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import PolicyValueError, select
from guard.executors import registry
from guard.executors.input_validation import InputValidationExecutor
from guard.input_policy import OBSERVATIONS, POLICIES, contract_state, policy_context
from guard.judges.declared import DeclaredJudge
from guard.judges.rule_table import RuleTableJudge
from guard.models import Evidence
from guard.models.checkdef import ObsSpec
from guard.models.objects import Observation
from guard.report.html import render_html
from guard.store import Store
from tests.builders import PACK_DIR, ROOT, T0, baseline_pack, environment, policy, completion_response
from tests.prepared_fixtures import manual_setup_seed, put_baseline_snapshot
from tests.response_semantics_support import (SyntheticInputAdapter, synthetic_input_response,
                                               with_legacy_check)
from tests.test_policy_m2 import _manual_evidence
from tests.test_policy_m4 import fill, snapshot, write

AXES = ("security", "functional", "performance")
CPU = {"architecture": "EXAMPLE-x86_64", "model": "EXAMPLE-cpu", "system": "EXAMPLE-Linux",
       "kernel": "EXAMPLE-kernel", "threads": 2}


@pytest.fixture(scope="module")
def pack():
    return baseline_pack()


def values(check, **updates):
    return {"max_input_tokens": 8, "rejection_codes": ["400", "413", "422"],
            "allowed_endpoints": [check.functional_probes[0].id], "allowed_input_formats": ["token_ids"], **updates}


def observed(check):
    return {key: Observation(value=value, collected=True) for key, value in {
        "challenge_outcomes": {"token_id": "400", "length": "400"}, "accepted_challenges": [],
        "challenge_rejection_results": {"token_id": "verified", "length": "verified"},
        "challenge_consumption": {"token_id": "unknown", "length": "unknown"},
        "confirmed_input_violations": [],
        "challenges_planned": ["token_id", "length"],
        "functional_probes_planned": ["before", "after_token_id", "after_length"],
        "functional_probe_results": dict.fromkeys(["before", "after_token_id", "after_length"], "pass"),
        "input_endpoints_tested": [check.functional_probes[0].id], "input_formats_tested": ["token_ids"]}.items()}


def fixture_evidence(check, verdicts=None):
    evd = _manual_evidence(check, verdicts or dict.fromkeys(AXES, "PASS"))
    raw = evd.to_dict()
    raw["execution"]["executor"] = {"name": "manual-entry", "version": "0.5.0",
        "config_sha256": "0"*64, "default_judge": "declared"}
    raw["challenges"] = [{"step": s, "attempted": True} for s in check.challenge_steps]
    raw["observations"].update({key: obs.model_dump(mode="json") for key, obs in observed(check).items()})
    return Evidence.model_validate(raw).seal()


def run(tmp_path, monkeypatch, pack, vals, probe=None, missing_target=False):
    target = tmp_path / "target.json"
    if not missing_target:
        target.write_text(json.dumps({"lab": True, "engine": {"trusted_url": "http://EXAMPLE-engine:8000",
            "ready_timeout": 1}, "origins": {"trusted": {"network": "EXAMPLE-inside"}}}), encoding="utf-8")
    calls = []
    def cprobe(net, requests, timeout):
        calls.extend(deepcopy(requests))
        if probe:
            return probe(requests)
        return [synthetic_input_response(r, 200 if r.get("id") == "ready" or r["body"]["prompt"] == [0] else 400)
                for r in requests]
    monkeypatch.setitem(registry.ADAPTERS, "vllm", SyntheticInputAdapter)
    monkeypatch.setitem(registry.EXECUTORS, "input_validation:m10-test", lambda cfg: InputValidationExecutor(
        {"target": str(target), "vocab_size": 100, "vocab_source": "EXAMPLE synthetic model fixture"},
        container_probe=cprobe))
    env = environment(model={"id": "EXAMPLE-model"}, runtime={"gpu_count": 0, "cpu": CPU})
    pol = policy(pack.ref(), scope={"include": ["REQ-09"]}, values={"REQ-09": vals})
    def create(folder):
        st = Store(folder / "store")
        st.put_many([env, pol]); put_baseline_snapshot(st)
        # Modified definitions are only used for run-contract tests, not pack loading.
        plan = select(pack, [env], pol); st.put(plan)
        return st, plan, None

    st, plan, _ = manual_setup_seed("m10-run-inputs", {
        "pack": pack.model_dump(mode="json"), "environment": env.to_dict(), "policy": pol.to_dict(),
    }, tmp_path, create)
    evd, = run_plan(st, pack, plan, RunConfig(executors={"input_validation": "m10-test"}, adapter="vllm"))
    res = judge_evidence(pack.checks["REQ-09"], evd, pol, T0)
    return st, plan, evd, res, calls


def test_registration_is_bounded_and_template_empty(pack):
    check = pack.checks["REQ-09"]
    assert check.revision == 6 and pack.manifest.revision == 22 and not pack.reviewed
    assert contract_state(check) == "complete" and len(check.required_observations) == 10
    assert len(pack.checks) == 77
    assert sum(bool(c.policy_params) for c in pack.active_checks()) == 22
    assert sum(len(c.policy_params) for c in pack.active_checks()) == 50
    assert len([c for c in pack.active_checks() if c.executor]) == 11
    template = yaml.safe_load(build_policy_template(pack))
    assert template["values"]["REQ-09"] == {} and template["status"] == "DRAFT"
    assert template["confirmed_by"] is None and template["confirmed_at"] is None
    rows = list(csv.DictReader((ROOT / "docs/policy_catalog.csv").open(encoding="utf-8", newline="")))
    assert len(rows) == 323 and sum(len(json.loads(r["check_ids"])) for r in rows) == 615
    assert {c: sum(r["classification"] == c for r in rows) for c in "abcd"} == dict(a=47, b=2, c=161, d=113)
    for pid, key in zip(("P-DRAFT-INPUT-ENDPOINTS", "P-DRAFT-INPUT-FORMATS"), POLICIES):
        row = next(r for r in rows if r["proposed_policy_id"] == pid)
        assert row["classification"] == "a" and json.loads(row["current_param_paths"]) == [f"values.REQ-09.{key}"]
    for cid in ("REQ-08",):
        c = pack.checks[cid]
        assert c.revision == 1 and not c.policy_params and c.executor is None and c.judge is None
    template_check = pack.checks["REQ-07"]
    assert template_check.revision == 2 and template_check.executor == "template_validation"
    assert len(template_check.policy_params) == 3 and len(template_check.required_observations) == 13
    sampling = pack.checks["REQ-10"]
    assert sampling.revision == 2 and sampling.executor == "sampling_validation"
    assert [(p.key, p.type, p.required) for p in sampling.policy_params] == [
        ("allowed_sampling_options", "list[str]", True), ("rejection_codes", "list[str]", True)]
    assert sampling.judge.plugin == "rule_table"


@pytest.mark.parametrize("key", POLICIES)
@pytest.mark.parametrize("bad", [None, True, "token_ids", 1, [True], [0], ["token_ids", 1], {}])
def test_types_rejected_even_outside_scope(pack, key, bad):
    pol = policy(pack.ref(), scope={"include": ["SUP-03"]}, values={"REQ-09": values(pack.checks["REQ-09"], **{key:bad})})
    with pytest.raises(PolicyValueError, match="REQ-09"):
        select(pack, [environment()], pol)


@pytest.mark.parametrize("key", POLICIES)
def test_missing_is_not_run_and_zero_requests(tmp_path, monkeypatch, pack, key):
    vals = values(pack.checks["REQ-09"]); del vals[key]
    _, _, evd, res, calls = run(tmp_path, monkeypatch, pack, vals, missing_target=True)
    assert evd.execution.status == "NOT_RUN" and "NO_POLICY_VALUE" in evd.execution.abort_reason
    assert res.verdicts.security == res.verdicts.functional == "UNKNOWN" and not calls


@pytest.mark.parametrize("key,bad", [
    ("allowed_endpoints", []), ("allowed_input_formats", []),
    ("allowed_endpoints", [" "]), ("allowed_input_formats", ["token_ids "]),
    ("allowed_endpoints", ["POST /v1/completions", "POST /v1/completions"]),
    ("allowed_endpoints", ["POST /v1/completions", "POST /other"]),
    ("allowed_input_formats", ["token_ids", "text"]), ("allowed_input_formats", ["text"]),
    *[("allowed_endpoints", [s]) for s in ["post /v1/completions", " POST /v1/completions", "POST /v1/completions/",
      "POST /v1/Completions", "POST /v1/completions?a=1", "POST /v1/completions#x", "POST https://example.invalid/x",
      "POST /v1/{x}", "POST /v1/*", "POST /v1/%63ompletions", "POST /v1/../completions", "POST /alias"]]])
def test_unsupported_aborts_before_target_readiness_and_docker(tmp_path, monkeypatch, pack, key, bad):
    import guard.executors.http_common as http
    def forbidden(*args, **kwargs):
        raise AssertionError("readiness/Docker must never be entered")
    monkeypatch.setattr(http, "wait_engine", forbidden); monkeypatch.setattr(http, "Docker", forbidden)
    _, _, evd, res, calls = run(tmp_path, monkeypatch, pack, values(pack.checks["REQ-09"], **{key: bad}),
                                missing_target=True)
    assert evd.execution.status == "ABORTED" and "INPUT_POLICY_UNSUPPORTED" in evd.execution.abort_reason
    assert not calls and all(not o.collected for o in evd.observations.values())
    assert res.verdicts.security == res.verdicts.functional == "UNKNOWN"


@pytest.mark.parametrize("change", ["via", "method", "boolean", "max_tokens", "extra_body", "multiple", "query", "expect"])
def test_invalid_normal_profile_preflight_and_offline(pack, change):
    c = pack.checks["REQ-09"].model_copy(deep=True); fp = c.functional_probes[0]
    if change == "via": fp.via = "gateway"
    elif change == "method": fp.method = "GET"
    elif change == "boolean": fp.body["prompt"] = [False]
    elif change == "max_tokens": fp.body["max_tokens"] = True
    elif change == "extra_body": fp.body["messages"] = []
    elif change == "multiple": c.functional_probes.append(fp.model_copy(deep=True))
    elif change == "query": fp.path += "?x=1"
    else: fp.expect = [500]
    vals = values(c)
    with pytest.raises(ValueError, match="INPUT_NORMAL_PROFILE_UNSUPPORTED"):
        InputValidationExecutor({}).validate_policy(c, vals)
    evd = fixture_evidence(c)
    assert RuleTableJudge().judge(c, evd, c.judge.params, vals).verdicts["security"] == "UNKNOWN"
    assert DeclaredJudge().judge(c, evd, {}, vals).verdicts["security"] == "UNKNOWN"


def test_endpoint_comes_from_definition_and_request_not_policy(tmp_path, monkeypatch, pack):
    c = pack.checks["REQ-09"].model_copy(deep=True); c.functional_probes[0].path = "/EXAMPLE-custom"
    p = pack.model_copy(update={"checks": {**pack.checks, "REQ-09": c}})
    st, _, evd, res, calls = run(tmp_path, monkeypatch, p, values(c))
    assert len(calls) == 6 and calls[0]["id"] == "ready"
    assert all(r["url"].endswith("/EXAMPLE-custom") for r in calls[1:])
    assert [r["body"]["prompt"] for r in calls[1:]] == [[0], [100], [0], [0]*9, [0]]
    assert evd.observations["input_endpoints_tested"].value == ["POST /EXAMPLE-custom"]
    assert evd.observations["input_formats_tested"].value == ["token_ids"]
    assert res.verdicts.security == res.verdicts.functional == "PASS"
    assert res.verdicts.performance == "UNKNOWN" and evd.data_origin == "EXAMPLE" and not st.verify()


@pytest.mark.parametrize("boundary,expected", [(400,"PASS"),(200,"FAIL"),(206,"FAIL"),("timeout","UNKNOWN"),(500,"UNKNOWN"),(401,"UNKNOWN")])
def test_boundary_results_keep_existing_meaning(tmp_path, monkeypatch, pack, boundary, expected):
    # These two status-only FAIL cases exercise the immutable historical
    # definition. New definitions never treat raw 2xx as consumption proof.
    if boundary in (200, 206):
        pack = with_legacy_check(pack, "REQ-09")
    def probe(requests):
        return [synthetic_input_response(r, 200 if r.get("id")=="ready" or r["body"]["prompt"]==[0] else boundary) for r in requests]
    _, _, evd, res, _ = run(tmp_path, monkeypatch, pack, values(pack.checks["REQ-09"]), probe)
    assert evd.execution.status == "COMPLETED" and res.verdicts.security == expected
    assert res.verdicts.functional == "PASS" and res.verdicts.performance == "UNKNOWN"


@pytest.mark.parametrize("stage,outcome,security,functional", [
    ("before", 500, "UNKNOWN", "FAIL"), ("before", "timeout", "UNKNOWN", "UNKNOWN"),
    ("after", 500, "FAIL", "FAIL"), ("after", "timeout", "FAIL", "UNKNOWN")])
def test_controls_and_independent_failure(tmp_path, monkeypatch, pack, stage, outcome, security, functional):
    # Independent raw-200 FAIL is a PACK21 contract, retained without changing
    # any case or expected result. Current verified-violation cases are below.
    pack = with_legacy_check(pack, "REQ-09")
    count = 0
    def probe(requests):
        nonlocal count
        result=[]
        for r in requests:
            if r.get("id")=="ready": result.append({"outcome":200}); continue
            count+=1
            is_control=r["body"]["prompt"]==[0]
            code=outcome if is_control and ((stage=="before" and count==1) or (stage=="after" and count>1)) else 200
            result.append(completion_response(r, code))
        return result
    _, _, evd, res, calls = run(tmp_path, monkeypatch, pack, values(pack.checks["REQ-09"]), probe)
    assert res.verdicts.security == security and res.verdicts.functional == functional
    assert len(calls)==(2 if stage=="before" else 6)
    assert all(not c.attempted for c in evd.challenges) if stage=="before" else all(c.attempted for c in evd.challenges)


@pytest.mark.parametrize("key", OBSERVATIONS)
@pytest.mark.parametrize("bad", [None, [], "token_ids", False, [True], ["EXAMPLE-wrong"], ["token_ids","text"]])
def test_scope_blocks_both_judges_without_erasing_fail(pack, key, bad):
    c=pack.checks["REQ-09"]; evd=fixture_evidence(c); vals=values(c)
    evd.observations[key]=Observation(value=bad,collected=bad is not None)
    before=evd.to_dict(); vbefore=deepcopy(vals)
    for judge,params in [(RuleTableJudge(),c.judge.params),(DeclaredJudge(),{})]:
        result=judge.judge(c,evd,params,vals)
        assert result.verdicts["security"]==result.verdicts["functional"]=="UNKNOWN"
        assert any(r.startswith("POLICY_CONTEXT:") for r in result.reasons)
    assert evd.to_dict()==before and vals==vbefore
    evd.observations["accepted_challenges"].value=["token_id"]
    evd.observations["confirmed_input_violations"].value=["EXAMPLE-confirmed-token-id-consumption"]
    evd.observations["functional_probe_results"].value["after_token_id"]="fail"
    evd.observations["declared_verdicts"].value=dict(security="FAIL",functional="FAIL",performance="UNKNOWN")
    for judge,params in [(RuleTableJudge(),c.judge.params),(DeclaredJudge(),{})]:
        result=judge.judge(c,evd,params,vals)
        assert result.verdicts["security"]==result.verdicts["functional"]=="FAIL"
        assert result.verdicts["performance"]=="UNKNOWN"


@pytest.mark.parametrize("problem", ["policy_missing","obs_missing","policy_optional","policy_type","obs_type","duplicate","misplaced"])
def test_partial_declaration_never_falls_back_to_legacy_pass(pack, problem):
    c=pack.checks["REQ-09"].model_copy(deep=True)
    if problem=="policy_missing": c.policy_params=[p for p in c.policy_params if p.key!=POLICIES[0]]
    elif problem=="obs_missing": c.required_observations=[o for o in c.required_observations if o.key!=OBSERVATIONS[0]]
    elif problem=="policy_optional": c.policy_params[-1].required=False
    elif problem=="policy_type": c.policy_params[-1].type="str"
    elif problem=="obs_type": next(o for o in c.required_observations if o.key==OBSERVATIONS[-1]).type="str"
    elif problem=="duplicate": c.policy_params.append(c.policy_params[-1].model_copy())
    else: c.required_observations.append(ObsSpec(key=POLICIES[0],type="list[str]"))
    assert contract_state(c)=="partial"
    evd=fixture_evidence(c)
    for judge,params in [(RuleTableJudge(),c.judge.params),(DeclaredJudge(),{})]:
        assert judge.judge(c,evd,params,values(c)).verdicts["security"]=="UNKNOWN"


def test_offline_empty_equals_empty_and_unsupported_policy_cannot_pass(pack):
    c=pack.checks["REQ-09"]; evd=fixture_evidence(c)
    for key in OBSERVATIONS: evd.observations[key].value=[]
    vals=values(c,allowed_endpoints=[],allowed_input_formats=[])
    assert RuleTableJudge().judge(c,evd,c.judge.params,vals).verdicts["security"]=="UNKNOWN"
    assert DeclaredJudge().judge(c,evd,{},vals).verdicts["security"]=="UNKNOWN"
    # Also reject a supported subset of a larger corporate policy in both paths.
    evd=fixture_evidence(c); vals=values(c,allowed_input_formats=["token_ids","text"])
    assert RuleTableJudge().judge(c,evd,c.judge.params,vals).verdicts["security"]=="UNKNOWN"
    assert DeclaredJudge().judge(c,evd,{},vals).verdicts["security"]=="UNKNOWN"


def test_absent_markers_keep_historical_contract_and_other_checks(pack):
    c=pack.checks["REQ-09"].model_copy(deep=True)
    c.policy_params=[p for p in c.policy_params if p.key not in POLICIES]
    c.required_observations=[o for o in c.required_observations if o.key not in OBSERVATIONS]
    params=deepcopy(c.judge.params)
    for table in [params,params["axes"]["functional"]]:
        table["pass_when_all"]=[r for r in table["pass_when_all"] if r["obs"] not in OBSERVATIONS]
    evd=fixture_evidence(c); vals={"max_input_tokens":8,"rejection_codes":["400"]}
    assert contract_state(c)=="legacy" and policy_context(c,vals) is None
    assert RuleTableJudge().judge(c,evd,params,vals).verdicts["security"]=="PASS"
    assert DeclaredJudge().judge(c,evd,{},vals).verdicts["security"]=="PASS"
    assert policy_context(pack.checks["SUP-03"],{}) is None


def manual_setup(tmp_path, pack, mode="audit"):
    envs=[environment()]
    if mode=="update": envs.append(environment(id="ENV-CANDIDATE"))
    pol=policy(pack.ref(),scope={"include":["REQ-09","SUP-03"]},values={"REQ-09":values(pack.checks["REQ-09"])})
    def create(folder):
        st=Store(folder/"store")
        st.put_many([*envs,pol]); put_baseline_snapshot(st)
        plan=select(pack,envs,pol,mode=mode); st.put(plan)
        doc=fill(build_template(st,pack,plan,include_automated=True))
        for e in doc["checks"]:
            if e["check_id"]=="REQ-09":
                e["override_reason"]="EXAMPLE bounded synthetic manual assessment"
                for o in e["required_observations"]:
                    o.update(value=observed(pack.checks["REQ-09"])[o["key"]].value,collected=True)
        return st,plan,doc

    st,plan,doc=manual_setup_seed("m10-manual", {
        "pack":pack.model_dump(mode="json"),"environments":[e.to_dict() for e in envs],
        "policy":pol.to_dict(),"mode":mode,
    },tmp_path,create)
    assert all(e["check_id"]!="REQ-09" for e in build_template(st,pack,plan)["checks"])
    return st,plan,doc


@pytest.mark.parametrize("version,mode", [(1,"audit"),(2,"audit"),(3,"audit"),(3,"update")])
@pytest.mark.parametrize("scope_edit", ["value", "collected"])
def test_manual_versions_hash_history_sides_and_report(tmp_path, pack, version, mode, scope_edit):
    st,plan,doc=manual_setup(tmp_path,pack,mode)
    doc["template"]=f"guard-manual-evidence/{version}"
    if version==1: doc.pop("policy"); doc.pop("pack")
    path=write(tmp_path,doc); import_evidence(st,pack,path,now=T0)
    old=snapshot(st); results=judge_plan(st,pack,plan,T0)
    assert all(r.verdicts.security=="PASS" for r in results)
    assert all(e.data_origin=="DECLARED" for e in st.iter_kind("EVD"))
    rpt=build_report(st,pack,plan,T0)
    assert not rpt.official and any("INPUT_SCOPE_MATCHED" in note for note in rpt.limitations)
    assert "token_ids" in render_html(rpt.to_dict()) and all(r.evidence_source == "manual" for r in rpt.by_check if r.selected)
    previous={ (e.check_id,e.side):e for e in st.find_latest("EVD",lambda e:True)}
    for e in doc["checks"]:
        if e["check_id"]=="REQ-09":
            next(o for o in e["required_observations"] if o["key"]=="input_formats_tested").update(
                value=["text"] if scope_edit=="value" else None, collected=scope_edit=="value")
    path=write(tmp_path,doc); import_evidence(st,pack,path,now=T0)
    latest={ (e.check_id,e.side):e for e in st.find_latest("EVD",lambda e:True)}
    for key,e in latest.items():
        changed=e.execution.executor.config_sha256 != previous[key].execution.executor.config_sha256
        assert changed == (e.check_id=="REQ-09")
    for sha,data in old.items():
        assert st.get(data["id"],data["revision"]).to_dict()==data
    results=judge_plan(st,pack,plan,T0)
    assert all(r.verdicts.security=="UNKNOWN" for r in results if r.check_id=="REQ-09")
    after=snapshot(st); judge_plan(st,pack,plan,T0)
    assert snapshot(st)==after and not st.verify()


def test_manual_override_required_and_atomic(tmp_path, pack):
    st,plan,doc=manual_setup(tmp_path,pack)
    e=next(e for e in doc["checks"] if e["check_id"]=="REQ-09"); e["override_reason"]=" "
    before=snapshot(st)
    with pytest.raises(ManualEvidenceError): import_evidence(st,pack,write(tmp_path,doc),now=T0)
    assert snapshot(st)==before


@pytest.mark.parametrize("gap", ["attachment","notes","normal","challenge","structured","execution"])
def test_new_manual_contract_cannot_bypass_existing_core_gates(pack, gap):
    c=pack.checks["REQ-09"]; evd=fixture_evidence(c); vals=values(c)
    pol=policy(pack.ref(),values={"REQ-09":vals})
    assert judge_evidence(c,evd,pol,T0).verdicts.security=="PASS"
    raw=evd.to_dict()
    if gap=="attachment": raw["artifacts"]=[]
    elif gap=="notes": raw["observations"]["declared_observation_notes"]["value"]={}
    elif gap=="normal": raw["normal_control"]["succeeded"]=False
    elif gap=="challenge": raw["challenges"][0]["attempted"]=False
    elif gap=="structured": raw["observations"]["challenge_outcomes"]={"value":None,"collected":False}
    else: raw["execution"]["status"]="NOT_RUN"
    result=judge_evidence(c,Evidence.model_validate(raw).seal(),pol,T0)
    assert result.verdicts.security=="UNKNOWN"
    raw["observations"]["declared_verdicts"]["value"]["security"]="FAIL"
    assert judge_evidence(c,Evidence.model_validate(raw).seal(),pol,T0).verdicts.security=="FAIL"


def test_invalid_policy_keeps_declared_fail_and_unknown(pack):
    c=pack.checks["REQ-09"]; evd=fixture_evidence(c,dict(security="FAIL",functional="UNKNOWN",performance="PASS"))
    result=DeclaredJudge().judge(c,evd,{},values(c,allowed_input_formats=["text"]))
    assert result.verdicts==dict(security="FAIL",functional="UNKNOWN",performance="UNKNOWN")


def test_automatic_report_forwards_only_safe_context_and_escapes(tmp_path,monkeypatch,pack):
    st,plan,evd,res,_=run(tmp_path,monkeypatch,pack,values(pack.checks["REQ-09"]))
    st.put(res)
    rpt=build_report(st,pack,plan,T0)
    assert not rpt.official and any("INPUT_SCOPE_MATCHED" in note for note in rpt.limitations)
    raw=rpt.model_dump(mode="json")
    assert "token_ids" in json.dumps(raw) and "INPUT_SCOPE_MATCHED" in render_html(rpt.to_dict())
    res.reasons.extend(["POLICY_ITEMS: EXAMPLE must not forward", "AUDIT_COLLECTION: EXAMPLE must not forward"])
    res.revision+=1; st.put(res.seal())
    rpt=build_report(st,pack,plan,T0)
    assert not any("EXAMPLE must not forward" in n for n in rpt.limitations)
    rpt.limitations.append('<script>EXAMPLE</script>')
    html=render_html(rpt.to_dict())
    assert '<script>EXAMPLE</script>' not in html and '&lt;script&gt;EXAMPLE&lt;/script&gt;' in html


def test_comparison_keeps_conditions_and_cpu_performance(tmp_path,monkeypatch,pack):
    _,_,evd,res,_=run(tmp_path,monkeypatch,pack,values(pack.checks["REQ-09"]))
    comp=evd.comparability.model_copy(update={"fixture_sha256":"a"*64,"config_sha256":"b"*64,
        "model_inventory_sha256":"c"*64})
    assert compare_results(res.verdicts.security,comp,res.verdicts.security,comp)=="NO_CHANGE"
    assert compare_results(res.verdicts.security,comp,res.verdicts.security,comp.model_copy(update={"fixture_sha256":"d"*64}))=="NOT_COMPARABLE"
    assert compare_results(res.verdicts.security,comp,res.verdicts.security,comp.model_copy(update={"cpu":None}))=="NOT_COMPARABLE"
    assert res.verdicts.performance=="UNKNOWN"
