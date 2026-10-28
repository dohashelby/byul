"""EXAMPLE source-to-projection equivalence and saved product replay; no engine."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.executors import log_analysis as collector
from guard.executors.log_analysis_contract import STRICT_JSON_PROFILE, analyze_log_captures, events
from guard.executors.log_projection import (PROFILE, PUBLIC_MARKERS, FieldProjection, fingerprint,
    project_log_captures, protect_analysis, validate_projection)
from guard.log_evidence import analyze_saved_logs
from guard.models import canonical_json, sha256_hex
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import get_pack, put_pack
from guard.validation_evidence import inventory_tree
from tests.builders import PACK_DIR, T0, baseline_pack
from tests import test_log_analysis as source

INPUT, OUTPUT, KEY = "EXAMPLE_PRIVATE_LOG_INPUT", "EXAMPLE_PRIVATE_LOG_OUTPUT", "EXAMPLE_PRIVATE_LOG_KEY"
MARKS = {"caller_input":INPUT, "private_system":INPUT+"_SYSTEM", "error_input":INPUT+"_ERROR"}
CORRELATION = {"normal":"EXAMPLE-real-normal", "error":"EXAMPLE-real-error"}
WINDOW = {"since":"2026-10-24T00:00:00Z", "until":"2026-10-25T00:00:00Z"}


def captures(event, *, suffix="", stderr="", **changes):
    rows = [{"request_id":CORRELATION["normal"], "event":"normal"},
            {"request_id":CORRELATION["error"], "exception":"EXAMPLE traceback " + OUTPUT},event]
    return [{"source":{"id":"engine", "kind":"docker_logs", "container":"engine"},
             "identity":{"container_id":"EXAMPLE-id", "image_id":"sha256:EXAMPLE", "started_at":"2026-10-24T00:00:00Z"},
             "window":dict(WINDOW),"returncode":0,"identity_stable":True,"source_complete":True,
             "text":"\n".join(json.dumps(row,ensure_ascii=False) for row in rows)+suffix,
             "stderr_text":stderr,**changes}]


def sample(projection, values=None):
    return {"schema":"guard-log-analysis-capture/4","analysis_profile":STRICT_JSON_PROFILE,
            "projection_profile":PROFILE,**projection,"requests":[{"case":"normal","trial":"normal",
                "request_id_sha256":projection["correlation_fingerprints"]["normal"],"request_fixture_sha256":"a"*64,"outcome":200}],
            "initial_gaps":[],"coverage_gaps":projection["analysis"]["log_coverage_gaps"],
            "controls":{"before":"pass","after":"pass"},"policy_values":values or source.VALUES,
            "source_target":"engine","window":dict(WINDOW)}


@pytest.mark.parametrize("key", ["extra",KEY,"a.b","a[]","",'a"b',"line\nkey","key\ufffd","中文",
    "request_id","ExCePtIoN","GF_private","nested","credential","prompt"])
@pytest.mark.parametrize("value", [None,123456,False,"", " \t", OUTPUT, MARKS["caller_input"],
    "prefix_"+MARKS["caller_input"], [0,987654,876543], {"request_id":CORRELATION["error"],"traceback":OUTPUT}])
def test_projection_keeps_original_strict_analyzer_predicates_for_paths_types_and_markers(key,value):
    event={"request_id":CORRELATION["normal"],key:value}
    original=captures(event)
    before=deepcopy(original)
    expected=analyze_log_captures(original,source.VALUES,MARKS,CORRELATION,profile=STRICT_JSON_PROFILE)
    projected=project_log_captures(original,source.VALUES,MARKS,CORRELATION,analysis=expected)
    assert projected["analysis"]==protect_analysis(expected,source.VALUES)
    old_fields,new_fields=set(expected["log_fields_seen"]),set(projected["analysis"]["log_fields_seen"])
    allowed,forbidden=set(source.VALUES["allowed_log_fields"]),set(source.VALUES["forbidden_log_fields"])
    assert (old_fields<=allowed)==(new_fields<=allowed)
    assert bool(old_fields&forbidden)==bool(new_fields&forbidden)
    validate_projection(sample(projected),source.VALUES)
    assert original==before
    text=json.dumps(projected,ensure_ascii=True)
    assert OUTPUT not in text and INPUT not in text and KEY not in text


@pytest.mark.parametrize("bad", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e400}',
    '{"x":{"a":1,"a":2}}','[1,2]','not JSON '+OUTPUT,'{"x":"'+OUTPUT+'"',
    '{"x":"\ufffd","x":1}'])
@pytest.mark.parametrize("leak",[False,True])
def test_ambiguous_or_truncated_neighbor_keeps_gap_and_independent_fail(bad,leak):
    event={"request_id":CORRELATION["normal"],"event":"normal"}
    if leak:event["prompt"]=MARKS["caller_input"]
    projected=project_log_captures(captures(event,suffix="\n"+bad),source.VALUES,MARKS,CORRELATION)
    assert not projected["analysis"]["log_coverage_complete"]
    assert bool(projected["analysis"]["log_violations"]) is leak
    validate_projection(sample(projected),source.VALUES)
    assert OUTPUT not in json.dumps(projected)


@pytest.mark.parametrize("stamp",[None,"2026-10-24T12:00:00Z","2026-10-23T12:00:00Z",
    "2026-10-24TINVALID"+OUTPUT,"2026-10-24TINVALID\ufffd"])
def test_timestamp_and_decoding_replacement_semantics_are_retained_without_bad_timestamp_text(stamp):
    original=captures({"request_id":CORRELATION["normal"],"event":"normal"})
    event=json.dumps({"request_id":CORRELATION["error"],"exception":OUTPUT})
    original[0]["text"]="\n".join(original[0]["text"].splitlines()[:1]+[(stamp+" " if stamp else "")+event])
    projected=project_log_captures(original,source.VALUES,MARKS,CORRELATION)
    validate_projection(sample(projected),source.VALUES)
    assert OUTPUT not in json.dumps(projected)


@pytest.mark.parametrize("value", ["literal\ufffd","literal\\ufffd","literal\u2028line","literal\x85line"])
def test_json_escaping_does_not_change_decoding_signal(value):
    original=captures({"request_id":CORRELATION["normal"],"exception":value})
    projected=project_log_captures(original,source.VALUES,MARKS,CORRELATION)
    validate_projection(sample(projected),source.VALUES)


def test_alias_namespace_cannot_turn_unknown_field_into_a_policy_allowed_field():
    values={**source.VALUES,"allowed_log_fields":[*source.VALUES["allowed_log_fields"],"GF_"+"a"*64]}
    rules=FieldProjection(values)
    assert rules.namespace!="GF_"
    projected=project_log_captures(captures({"request_id":CORRELATION["normal"],KEY:OUTPUT}),values,MARKS,CORRELATION)
    assert projected["analysis"]["log_violations"]
    assert rules.path(KEY) in projected["analysis"]["log_fields_seen"]


@pytest.mark.parametrize("key",["x.request_id","x.y"])
@pytest.mark.parametrize("declared",[False,True])
def test_literal_quoted_paths_cannot_fabricate_a_missing_error_branch(key,declared):
    event={"request_id":CORRELATION["normal"],key:CORRELATION["error"] if key=="x.request_id" else
           {"request_id":CORRELATION["error"],"traceback":OUTPUT}}
    values=deepcopy(source.VALUES)
    if declared:
        from guard.executors.content_common import leaves
        values["allowed_log_fields"]=list(leaves(event))
    original=captures(event)
    original[0]["text"]=json.dumps(event)
    projected=project_log_captures(original,values,MARKS,CORRELATION)
    assert projected["analysis"]["log_sources_tested"]["engine"]==["normal"]
    assert not projected["analysis"]["log_coverage_complete"]


@pytest.fixture
def product(tmp_path_factory,monkeypatch):
    def run(mode="normal",*,fault=None):
        root=tmp_path_factory.mktemp("log-privacy")
        obj=source.AmbiguousSource("request",leak=True) if mode=="ambiguous" else source.Source(
            leak=mode in {"leak","leak-late"},dump=mode!="unknown")
        base=obj.probe
        sent=[]
        def probe(network,requests,timeout):
            sent.extend(deepcopy(requests))
            if mode in {"late","leak-late"} and requests[0]["id"]=="after":
                raise type(OUTPUT,(RuntimeError,),{})(OUTPUT)
            rows=base(network,requests,timeout)
            for row in rows:
                body=json.loads(row["body"])
                if "error" in body:body["error"]["message"]=OUTPUT
                else:body["choices"][0]["text"]=OUTPUT
                row.update(body=json.dumps(body),body_preview=OUTPUT,detail=OUTPUT,phase=OUTPUT,
                           headers={"Authorization":OUTPUT},elapsed_ms=OUTPUT,method=OUTPUT)
                if mode=="no-control" and requests[0]["id"]=="normal":row["outcome"]=503
                if mode=="bad-error-status" and requests[0]["id"]=="safe_error":row["outcome"]=500
            if mode=="extra-key":obj.events[-1][KEY]={"payload":[987654,876543],"message":OUTPUT}
            if mode=="credential":obj.events[-1]["credential"]=MARKS["caller_input"]
            return rows
        obj.probe=probe
        if mode=="source-exception":obj.inspect_json=lambda *a,**k:(_ for _ in ()).throw(type(OUTPUT,(RuntimeError,),{})(OUTPUT))
        original_write=Path.write_bytes
        def write(path,data):
            if path.name=="log_samples.json" and fault=="write":
                original_write(path,b"EXAMPLE-partial")
                raise PermissionError(13,OUTPUT,str(path))
            return original_write(path,data)
        with monkeypatch.context() as patch:
            patch.setattr(collector,"markers",lambda:dict(MARKS))
            patch.setattr(Path,"write_bytes",write)
            if fault=="project":patch.setattr(collector,"project_log_captures",lambda *a,**k:(_ for _ in ()).throw(RuntimeError(OUTPUT)))
            if fault=="budget":patch.setattr(collector,"MAX_ARTIFACT_BYTES",1)
            cfg=[{"id":"engine","kind":"container_file","container":"engine","path":"/logs/"+KEY}] if mode=="file" else None
            store,evd,res=source.run_log(root,patch,obj,source_cfg=cfg)
        serialized=canonical_json(evd.to_dict())+canonical_json(res.to_dict())
        for artifact in evd.artifacts:serialized+=(store.root/artifact.path).read_bytes()
        assert all(value.encode() not in serialized for value in (*MARKS.values(),OUTPUT,KEY))
        assert store.verify()==[]
        item=next((a for a in evd.artifacts if a.name=="log_samples.json"),None)
        saved=json.loads((store.root/item.path).read_bytes())["capture"] if item else None
        return SimpleNamespace(store=store,evd=evd,res=res,sample=saved,source=obj,root=root,sent=sent)
    return run


@pytest.mark.parametrize("mode,verdict",[("normal","PASS"),("leak","FAIL"),("unknown","UNKNOWN"),
    ("ambiguous","FAIL"),("extra-key","FAIL"),("source-exception","UNKNOWN"),("late","UNKNOWN"),
    ("leak-late","FAIL"),("file","PASS"),("no-control","UNKNOWN"),("bad-error-status","UNKNOWN"),("credential","FAIL")])
def test_real_collector_path_preserves_verdicts_and_omits_raw_values(product,mode,verdict):
    case=product(mode)
    assert case.res.verdicts.security.value==verdict
    assert case.evd.execution.status.value=="COMPLETED" and case.evd.data_origin.value=="EXAMPLE"
    assert case.evd.execution.executor.version=="0.3.0"
    assert case.sample["projection_profile"]==PROFILE and case.sample["markers"]==PUBLIC_MARKERS
    validate_projection(case.sample,source.VALUES)
    for saved,submitted in zip(case.sample["requests"],case.sent,strict=True):
        assert saved["request_fixture_sha256"]==sha256_hex(canonical_json(submitted["body"]))
        assert saved["request_id_sha256"]==fingerprint(submitted["headers"]["X-Request-Id"])
    log=next(a for a in case.evd.artifacts if a.name=="probe_log.jsonl")
    records=[json.loads(line) for line in (case.store.root/log.path).read_text(encoding="utf-8").splitlines()[:-1]]
    assert records and all(row["method"]=="POST" and row["detail"]=="RAW_INPUT_RESPONSE_OMITTED" for row in records)
    assert all(row["body_preview"] is None and row["elapsed_ms"] is None for row in records)


@pytest.mark.parametrize("mode",["normal","leak","unknown","ambiguous","extra-key","source-exception","late","file","no-control","bad-error-status","credential"])
def test_saved_projection_audit_bundle_and_ref_replay_are_read_only_and_labelled(product,mode):
    case=product(mode)
    pack=baseline_pack()
    put_pack(case.store,PACK_DIR)
    plan=case.store.resolve(case.evd.plan)
    rpt=build_report(case.store,pack,plan,as_of=T0)
    case.store.put(rpt)
    before=inventory_tree(case.store.root)
    audit=analyze_saved_logs(case.store.root)
    assert audit["accepted"] and audit["original_inventory_preserved"]
    row,=audit["checks"]
    assert row["status"]=="PROJECTION_REPLAY_MATCH" and row["replay_basis"]=="stored_predicate_projection"
    assert row["raw_source_replayed"] is False and row["verdicts"]==case.res.verdicts.model_dump(mode="json")
    assert inventory_tree(case.store.root)==before
    export_bundle(case.store.root,case.root/"evidence.zip")
    restore_bundle(case.root/"evidence.zip",case.root/"restored")
    restored=Store(case.root/"restored")
    replayed,=judge_plan(restored,get_pack(restored,pack.ref()),restored.resolve(plan.ref()),as_of=T0)
    assert replayed.ref()==case.res.ref() and restored.resolve(rpt.ref()).to_dict()==rpt.to_dict()
    assert restored.verify()==[]


@pytest.mark.parametrize("fault",["project","write","budget"])
@pytest.mark.parametrize("mode",["normal","leak"])
def test_projection_or_capture_failure_keeps_original_safe_facts_and_independent_fail(product,fault,mode):
    case=product(mode,fault=fault)
    assert case.evd.execution.status.value=="ABORTED" and case.evd.execution.abort_reason=="ARTIFACT_CAPTURE_INCOMPLETE"
    assert case.res.verdicts.security.value==("FAIL" if mode=="leak" else "UNKNOWN")
    assert case.evd.normal_control.succeeded and case.evd.observations["log_fields_seen"].collected
    assert case.sample is None and [a.name for a in case.evd.artifacts]==["probe_log.jsonl"]


@pytest.mark.parametrize("change",["raw-scalar","raw-key","fingerprint","extra-metadata","wrong-profile","raw-number","rejected-count"])
def test_projection_grammar_rejects_resealed_raw_or_wrong_inputs(change):
    saved=sample(project_log_captures(captures({"request_id":CORRELATION["normal"],"event":"normal"}),
                                    source.VALUES,MARKS,CORRELATION))
    capture=saved["captures"][0]
    if change in {"raw-scalar","raw-key","raw-number"}:
        rows,_=events(capture["text"],profile=STRICT_JSON_PROFILE)
        if change=="raw-key":rows[0]["event"][KEY]=None
        else:rows[0]["event"]["event"]=OUTPUT if change=="raw-scalar" else 987654
        capture["text"]="\n".join(json.dumps(row["event"]) for row in rows)
        capture["text_projection_sha256"]=fingerprint(capture["text"])
        capture["text_projection_bytes"]=len(capture["text"].encode())
    elif change=="fingerprint":capture["text_projection_sha256"]="a"*64
    elif change=="extra-metadata":capture["body"]=OUTPUT
    elif change=="rejected-count":capture["rejected_json_lines"]["text"]=1
    else:saved["projection_profile"]="future"
    with pytest.raises(ValueError,match="saved log projection"):
        validate_projection(saved,source.VALUES)


def test_fixture_sha_matches_prior_validated_original_contract(product):
    case=product()
    assert case.evd.comparability.fixture_sha256=="e5793c0307334602b159afdd409df2edaf76af3b3f99516faa3def0f577ab5f0"


@pytest.mark.parametrize("change",["scalar","source","request","analysis","metadata","duplicate-envelope"])
def test_resealed_projection_mismatch_does_not_rewrite_original_result_or_disclose_text(product,change):
    case=product()
    pack=baseline_pack()
    put_pack(case.store,PACK_DIR)
    plan=case.store.resolve(case.evd.plan)
    original=build_report(case.store,pack,plan,as_of=T0)
    case.store.put(original)
    artifact=next(a for a in case.evd.artifacts if a.name=="log_samples.json")
    envelope=json.loads((case.store.root/artifact.path).read_bytes())
    value=envelope["capture"]
    if change=="scalar":
        cap=value["captures"][0]
        rows,_=events(cap["text"],profile=STRICT_JSON_PROFILE)
        rows[0]["event"]["event"]=OUTPUT
        cap["text"]="\n".join(json.dumps(row["event"]) for row in rows)
        cap["text_projection_sha256"]=fingerprint(cap["text"])
        cap["text_projection_bytes"]=len(cap["text"].encode())
    elif change=="source":value["captures"][0]["source"]["container"]="EXAMPLE-other-engine"
    elif change=="request":value["requests"][0]["request_id_sha256"]="f"*64
    elif change=="analysis":value["analysis"]["log_fields_seen"]=[]
    elif change=="metadata":value["captures"][0]["headers"]={"Authorization":OUTPUT}
    raw=json.dumps(envelope).encode()
    if change=="duplicate-envelope":raw=raw[:-1]+b',"encoding":"EXAMPLE duplicate"}'
    saved=case.store.put_artifact(raw)
    updated=artifact.model_copy(update={"path":saved.path,"sha256":saved.sha256,"bytes":saved.bytes})
    newer=case.evd.model_copy(update={"revision":2,"artifacts":[updated if a==artifact else a for a in case.evd.artifacts]}).seal()
    case.store.put(newer)
    judge_plan(case.store,pack,plan,as_of=T0)
    report=build_report(case.store,pack,plan,as_of=T0)
    case.store.put(report)
    before=inventory_tree(case.store.root)
    audit=analyze_saved_logs(case.store.root,f"{report.id}@{report.revision}")
    assert not audit["accepted"] and audit["problems"]
    assert OUTPUT not in json.dumps(audit)
    assert analyze_saved_logs(case.store.root,f"{original.id}@{original.revision}")["accepted"]
    assert inventory_tree(case.store.root)==before
