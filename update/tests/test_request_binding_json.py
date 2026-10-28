"""EXAMPLE duplicate JSON cannot fabricate or erase consumer correspondence."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.executors import request_binding as binding
from guard.executors.content_common import MAX_SAMPLE_BYTES
from guard.models import Verdict
from tests import test_request_binding as req

CANARY="EXAMPLE_PRIVATE_DUPLICATE_RESPONSE"
EXPECTED={"tokens":[1],"text":"EXAMPLE-origin"}
FOREIGN={"tokens":[2],"text":"EXAMPLE-other"}


def frame(mode="json"):
    choice={"index":0,"token_ids":[55],"finish_reason":"length"}
    choice.update({"message":{"role":"assistant","content":"EXAMPLE-output"}} if mode=="json"
                  else {"delta":{"content":"EXAMPLE-output"}})
    return {"id":"chatcmpl-EXAMPLE","model":"fixture-model",
        "object":"chat.completion" if mode=="json" else "chat.completion.chunk",
        "prompt_token_ids":[1],"prompt_text":"EXAMPLE-origin","choices":[choice],
        "usage":{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2}}


def duplicate(raw,key,first,second):
    value=json.loads(raw)
    before=json.dumps(key)+": "+json.dumps(value[key])
    after=json.dumps(key)+": "+json.dumps(first)+", "+json.dumps(key)+": "+json.dumps(second)
    assert before in raw
    return raw.replace(before,after,1)


def transport(raw,mode="json",extra=()):
    data=raw if mode=="json" else "".join("data: "+part+"\n\n" for part in (raw,*extra,"[DONE]"))
    value={"outcome":200,"capture_complete":True,"body_truncated":False,"utf8_valid":True,"eof":True,
        "body":data,"body_bytes":len(data.encode()),"body_sha256":hashlib.sha256(data.encode()).hexdigest(),
        "content_type":"application/json" if mode=="json" else "text/event-stream"}
    if mode=="sse":
        value.update(terminal_seen=True,events=[{"seq":i,"data":part} for i,part in enumerate((raw,*extra,"[DONE]"))])
    return value


def analyze(response,mode="json"):
    return binding.analyze_response(response,expected=EXPECTED,foreign=FOREIGN,
        model="fixture-model",max_output=8,mode=mode)


@pytest.mark.parametrize("mode",["json","sse"])
def test_unambiguous_normal_wire_shape_is_unchanged(mode):
    result=analyze(transport(json.dumps(frame(mode)),mode),mode)
    assert result["verified"] and result["violations"]==[] and result["gaps"]==[]


@pytest.mark.parametrize("mode",["json","sse"])
@pytest.mark.parametrize("key",["id","model","prompt_token_ids","prompt_text","choices","usage"])
def test_duplicate_even_identical_fields_are_gaps_not_correspondence_or_violation(mode,key):
    value=frame(mode);raw=duplicate(json.dumps(value),key,value[key],value[key])
    result=analyze(transport(raw,mode),mode)
    assert not result["verified"] and result["gaps"] and result["violations"]==[]


@pytest.mark.parametrize("mode",["json","sse"])
@pytest.mark.parametrize("foreign_last",[False,True])
def test_duplicate_consumer_field_order_cannot_fabricate_or_conceal_a_violation(mode,foreign_last):
    raw=json.dumps(frame(mode))
    first,last=(EXPECTED,FOREIGN) if foreign_last else (FOREIGN,EXPECTED)
    raw=duplicate(raw,"prompt_token_ids",first["tokens"],last["tokens"])
    result=analyze(transport(raw,mode),mode)
    assert not result["verified"] and result["gaps"] and result["violations"]==[]


@pytest.mark.parametrize("mode",["json","sse"])
def test_nested_duplicate_keys_are_not_a_valid_choice(mode):
    raw=json.dumps(frame(mode)).replace('"index": 0','"index": 0, "index": 0',1)
    result=analyze(transport(raw,mode),mode)
    assert not result["verified"] and result["violations"]==[]


def test_unambiguous_foreign_consumer_before_bad_frame_remains_independent_violation():
    value=frame("sse");value.update(prompt_token_ids=FOREIGN["tokens"],prompt_text=FOREIGN["text"])
    malformed=duplicate(json.dumps(frame("sse")),"id","chatcmpl-EXAMPLE","chatcmpl-untrusted")
    result=analyze(transport(json.dumps(value),"sse",(malformed,)),"sse")
    assert not result["verified"] and result["gaps"]
    assert set(result["violations"])=={
        "the other connection's exact prompt token IDs were consumed",
        "the other connection's exact rendered prompt was returned"}
    assert "API request ID changed within one response stream" not in result["violations"]


def test_existing_body_bound_prevents_strict_parse_without_creating_a_new_limit(monkeypatch):
    raw=json.dumps(frame());response=transport(raw)
    response["body_bytes"]=MAX_SAMPLE_BYTES
    monkeypatch.setattr(binding,"strict_json",lambda *a,**k:pytest.fail("bounded body must not be decoded"))
    result=analyze(response)
    assert not result["verified"] and result["violations"]==[]
    assert "response reached the bounded capture limit" in result["gaps"]


@pytest.mark.parametrize("mode",["json","sse"])
def test_ambiguous_private_fields_never_become_diagnostic_text(mode):
    raw=json.dumps(frame(mode))[:-1]+', "unused": "'+CANARY+'", "unused": 0}'
    result=analyze(transport(raw,mode),mode)
    assert not result["verified"] and result["violations"]==[] and CANARY not in json.dumps(result)


class DuplicateWorld(req.World):
    def __init__(self,mode="json",foreign_last=True,*,prior_foreign=False,tokenizer_key=None):
        super().__init__(**({"foreign":mode+"-generated-B","raises":"after"} if prior_foreign else {}))
        self.mode,self.foreign_last,self.tokenizer_key=mode,foreign_last,tokenizer_key

    def probe(self,network,requests,timeout):
        results=super().probe(network,requests,timeout)
        if self.tokenizer_key:
            for row in results:
                value=json.loads(row["body"]);key=self.tokenizer_key
                row["body"]=duplicate(row["body"],key,value[key],value[key])
                row["body_bytes"]=len(row["body"].encode())
        return results

    def stream(self,network,requests,timeout):
        results=super().stream(network,requests,timeout)
        for row in results:
            if row["id"]!=self.mode+"-generated-A":continue
            own=requests[0]["body"];other=requests[1]["body"]
            first,last=(own,other) if self.foreign_last else (other,own)
            if self.mode=="json":
                raw=duplicate(row["body"],"prompt_token_ids",self.tokens(first),self.tokens(last))
                raw=raw[:-1]+', "unused": "'+CANARY+'", "unused": 0}'
            else:
                events=deepcopy(row["events"])
                events[0]["data"]=duplicate(events[0]["data"],"prompt_token_ids",self.tokens(first),self.tokens(last))
                events[0]["data"]=events[0]["data"][:-1]+', "unused": "'+CANARY+'", "unused": 0}'
                row["events"]=events
                raw="".join("data: "+event["data"]+"\n\n" for event in events)
            row.update(body=raw,body_bytes=len(raw.encode()),body_sha256=hashlib.sha256(raw.encode()).hexdigest())
        return results


@pytest.mark.parametrize("mode",["json","sse"])
@pytest.mark.parametrize("foreign_last",[False,True])
def test_collector_and_existing_judge_do_not_turn_duplicate_order_into_a_finding(tmp_path,mode,foreign_last):
    draft,_=req.run(tmp_path,DuplicateWorld(mode,foreign_last))
    assert req.judge(draft).verdicts["security"]==Verdict.UNKNOWN
    assert draft.observations["security_violations"].value==[]
    assert not draft.observations["collection_complete"].value
    assert mode+":generated" not in draft.observations["cases_verified"].value


@pytest.mark.parametrize("mode",["json","sse"])
def test_other_connection_foreign_input_survives_ambiguous_response_and_later_failure(tmp_path,mode):
    draft,_=req.run(tmp_path,DuplicateWorld(mode,prior_foreign=True))
    assert req.judge(draft).verdicts["security"]==Verdict.FAIL
    assert draft.observations["security_violations"].value
    assert not draft.observations["collection_complete"].value


@pytest.mark.parametrize("key",["tokens","count","max_model_len"])
def test_duplicate_tokenizer_fields_do_not_bootstrap_verified_input(tmp_path,key):
    draft,world=req.run(tmp_path,DuplicateWorld(tokenizer_key=key))
    assert req.judge(draft).verdicts["security"]==Verdict.UNKNOWN
    assert not draft.observations["input_projection_verified"].value
    assert draft.observations["cases_attempted"].value==[]
    assert all(request["url"].endswith("/tokenize") for request in world.calls)


@pytest.mark.parametrize("mode,expected",[("ambiguous",Verdict.UNKNOWN),("independent",Verdict.FAIL),
    ("later-artifact-failure",Verdict.FAIL)])
def test_product_storage_report_restore_and_exact_rejudge_keep_packet_meaning(tmp_path,monkeypatch,mode,expected):
    from guard.core.run import RunConfig,run_plan
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors import registry
    from guard.models import canonical_json,load_pack,new_id
    from guard.store import Store
    from guard.store.packs import put_pack
    from guard.store.bundle import export_bundle,restore_bundle
    from tests.builders import PACK_DIR,environment,policy,T0

    world=DuplicateWorld("sse",prior_foreign=mode!="ambiguous")
    path=tmp_path/"target.json";path.write_text(json.dumps(req.target()),encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS,"request_binding:json-sim",lambda cfg:
        binding.RequestBindingExecutor({"target":str(path)},docker=world,container_probe=world.probe,
            stream_probe=world.stream,environ={}))
    pack=load_pack(PACK_DIR);store=Store(tmp_path/"store");put_pack(store,PACK_DIR)
    env=environment(id=new_id("ENV"),engine={"name":"vllm","version":"0.31.0","image_digest":req.IMAGE},
        model={"id":"fixture-model"})
    pol=policy(pack.ref(),id=new_id("POL"),scope={"include":["REQ-11"],"exclude":[]},
        values={"REQ-11":deepcopy(req.VALUES)})
    store.put_many([env,pol]);plan=select(pack,[env],pol);store.put(plan)
    with monkeypatch.context() as later:
        if mode=="later-artifact-failure":
            def unavailable(*args,**kwargs):raise OSError(CANARY)
            later.setattr(Store,"put_artifact",unavailable)
        evd,=run_plan(store,pack,plan,RunConfig(executors={"request_binding":"json-sim"},adapter="vllm"))
    result,=judge_plan(store,pack,plan,as_of=T0)
    assert result.verdicts.security==expected and evd.data_origin.value=="EXAMPLE"
    assert evd.execution.executor.version=="0.3.1"
    assert not evd.observations["collection_complete"].value
    assert bool(evd.observations["security_violations"].value) is (expected==Verdict.FAIL)
    if mode=="later-artifact-failure":
        assert evd.execution.status.value=="ABORTED" and not evd.artifacts
        assert "ARTIFACT_PERSISTENCE_INCOMPLETE" in evd.execution.abort_reason
    for artifact in evd.artifacts:assert CANARY not in (store.root/artifact.path).read_text(encoding="utf-8")
    report=build_report(store,pack,plan,as_of=T0);store.put(report)
    original=canonical_json(evd.to_dict());assert CANARY not in original.decode()
    export_bundle(store.root,tmp_path/"evidence.zip")
    restore_bundle(tmp_path/"evidence.zip",tmp_path/"restored")
    restored=Store(tmp_path/"restored")
    assert store.verify()==restored.verify()==[]
    assert canonical_json(restored.resolve(evd.ref()).to_dict())==original
    assert restored.resolve(report.ref()).to_dict()==report.to_dict()
    replayed,=judge_plan(restored,pack,restored.resolve(plan.ref()),as_of=T0)
    assert replayed.ref()==result.ref()


