"""EXAMPLE response-content proof is transient; saved evidence contains no body."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.executors import response_content as content
from guard.models import canonical_json, sha256_hex
from tests import test_response_content as source

INPUT = "EXAMPLE_PRIVATE_RESPONSE_INPUT"
OUTPUT = "EXAMPLE_PRIVATE_RESPONSE_OUTPUT_AND_DIAGNOSTIC"
MARKS = {"caller_input":INPUT+"-U", "private_system":INPUT+"-S", "error_input":INPUT+"-E"}


@pytest.fixture(autouse=True)
def fixed_markers(monkeypatch):
    monkeypatch.setattr(content,"markers",lambda:dict(MARKS))


def transport(mode, sent, captures, *, bad_outcome=None, change_outcome=False):
    base = source.response_probe(leak=mode in {"leak","leak-late","exception"},own_quote=mode=="own-quote",
                                 extra=mode=="field",option_outcome=200 if mode in {"ignored","tokens"} else 400,
                                 failed_after=mode=="leak-late")
    exception = type(OUTPUT,(Exception,),{})
    def probe(network, requests, timeout):
        sent.extend(deepcopy(requests))
        if mode=="exception" and requests[0]["id"]=="after": raise exception(OUTPUT)
        rows = base(network,requests,timeout)
        for request,row in zip(requests,rows):
            value = json.loads(row["body"])
            for choice in value.get("choices",[]):
                field = choice.get("message",choice)
                key = "content" if "message" in choice else "text"
                if not any(marker in field.get(key,"") for marker in MARKS.values()): field[key]=OUTPUT
            if "error" in value: value["error"]["message"]=OUTPUT
            if mode=="tokens" and request["id"]=="option:return_token_ids": value["prompt_token_ids"]=[987654,876543]
            raw = ("not JSON "+MARKS["private_system"]+OUTPUT if mode=="unparsed" and request["id"]=="private_system" else json.dumps(value))
            row.update(body=raw,body_bytes=len(raw.encode()),body_preview=OUTPUT,detail=OUTPUT,
                       phase=OUTPUT,elapsed_ms=OUTPUT,method=OUTPUT,headers={"Authorization":OUTPUT})
            if change_outcome and request["id"]=="option:return_prompt_text": row["outcome"]=bad_outcome
            captures.append(deepcopy(row))
        return rows
    return probe


def payload(store,evd):
    ref = next(a for a in evd.artifacts if a.name=="response_samples.json")
    return json.loads((store.root/ref.path).read_text(encoding="utf-8"))["capture"]


def assert_no_content(store,evd,res):
    serialized = canonical_json(evd.to_dict())+canonical_json(res.to_dict())
    assert INPUT.encode() not in serialized and OUTPUT.encode() not in serialized
    for artifact in evd.artifacts:
        text = (store.root/artifact.path).read_text(encoding="utf-8")
        assert INPUT not in text and OUTPUT not in text
        for key in ("body","prompt","messages","request","response","headers","prompt_token_ids","tokens"):
            assert json.dumps(key)+":" not in text
    assert store.verify()==[]


@pytest.mark.parametrize("mode",["normal","own-quote","leak","leak-late","field","ignored","tokens","unparsed","exception","completion"])
def test_original_response_policy_uses_complete_transient_data_without_saving_it(tmp_path,monkeypatch,mode):
    sent,captures=[],[]
    store,evd,res=source.run_content(tmp_path,monkeypatch,transport(mode,sent,captures),
                                   endpoint="/v1/completions" if mode=="completion" else "/v1/chat/completions")
    assert_no_content(store,evd,res)
    expected = "FAIL" if mode in {"leak","leak-late","field","tokens","unparsed","exception"} else (
        "UNKNOWN" if mode in {"ignored","completion"} else "PASS")
    assert res.verdicts.security.value==expected
    assert evd.normal_control.succeeded is (mode not in {"leak-late","exception"})
    assert evd.execution.executor.version=="0.2.0" and evd.data_origin.value=="EXAMPLE"
    data=payload(store,evd)
    assert data["artifact_profile"]==content.ARTIFACT_PROFILE and len(data["samples"])==len(sent)
    first=data["samples"][0]
    assert first["request_fixture_sha256"]==sha256_hex(canonical_json(sent[0]["body"]))
    assert first["capture"]["original_body_sha256"]==hashlib.sha256(captures[0]["body"].encode()).hexdigest()
    assert first["capture"]["original_body_bytes"]==len(captures[0]["body"].encode())
    assert first["capture"]["stored_body_is_verbatim"] is False
    assert data["marker_sha256"]=={role:sha256_hex(canonical_json(value)) for role,value in MARKS.items()}
    if mode=="tokens": assert "return_token_ids" in evd.observations["response_options_returned"].value
    if mode=="unparsed":
        sample=next(item for item in data["samples"] if item["case"]=="private_system")
        assert sample["capture"]["original_json_state"]=="invalid_or_nonfinite"
        assert "engine:private_system:<unparsed>" in evd.observations["private_markers_returned"].value
    log_ref=next(a for a in evd.artifacts if a.name=="probe_log.jsonl")
    records=[json.loads(line) for line in (store.root/log_ref.path).read_text(encoding="utf-8").splitlines()[:-1]]
    assert records and all(row["method"]=="POST" and row["detail"]=="RAW_INPUT_RESPONSE_OMITTED" for row in records)
    assert all(row["body_preview"] is None and row["elapsed_ms"] is None for row in records)


@pytest.mark.parametrize("outcome",[True,200.0,99,600,OUTPUT,{"code":OUTPUT},[OUTPUT],None],
                         ids=["bool","float","low","high","text","object","array","null"])
@pytest.mark.parametrize("leak",[False,True],ids=["no-leak","earlier-leak"])
def test_malformed_outcome_is_a_safe_gap_and_cannot_erase_earlier_leak(tmp_path,monkeypatch,outcome,leak):
    store,evd,res=source.run_content(tmp_path,monkeypatch,transport("leak" if leak else "normal",[],[],
                                  bad_outcome=outcome,change_outcome=True))
    assert_no_content(store,evd,res)
    assert res.verdicts.security.value==("FAIL" if leak else "UNKNOWN")
    row=next(item for item in payload(store,evd)["samples"] if item["case"]=="option:return_prompt_text")
    assert row["outcome"]=="probe_error" and evd.observations["response_coverage_complete"].value is False


@pytest.mark.parametrize("mode",["normal","leak-late"])
def test_saved_product_report_bundle_and_ref_replay_keep_original_verdicts(tmp_path,monkeypatch,mode):
    from guard.core.report import build_report
    from guard.core.judge import judge_plan
    from guard.store import Store
    from guard.store.bundle import export_bundle,restore_bundle
    from guard.store.packs import put_pack,get_pack
    from tests.builders import PACK_DIR,T0,baseline_pack
    store,evd,res=source.run_content(tmp_path,monkeypatch,transport(mode,[],[]))
    assert_no_content(store,evd,res)
    pack=baseline_pack()
    put_pack(store,PACK_DIR)
    plan=store.resolve(evd.plan)
    rpt=build_report(store,pack,plan,as_of=T0)
    store.put(rpt)
    export_bundle(store.root,tmp_path/"evidence.zip")
    restore_bundle(tmp_path/"evidence.zip",tmp_path/"restored")
    restored=Store(tmp_path/"restored")
    saved_pack=get_pack(restored,pack.ref())
    (replayed,)=judge_plan(restored,saved_pack,restored.resolve(plan.ref()),as_of=T0)
    assert replayed.ref()==res.ref() and restored.resolve(evd.ref()).to_dict()==evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict()==rpt.to_dict() and restored.verify()==[]


def test_fixture_fingerprint_matches_the_pinned_original_contract(tmp_path,monkeypatch):
    _,evd,_=source.run_content(tmp_path,monkeypatch,transport("normal",[],[]))
    assert evd.comparability.fixture_sha256=="cf89d7034a2cec8b22b3129bd564f0363babcaeab497aefd9ca44beb42770ee7"
