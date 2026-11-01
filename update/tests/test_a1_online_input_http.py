"""공개 Online HTTP /1. kubectl/serving은 EXAMPLE, 실제 stdlib wire는 별도 loopback이다."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sys
import threading

import pytest
from guard.models import DataOrigin,canonical_json
from guard.runtime import kubernetes_http as kube
from guard.runtime.bounded import capture_pipes
from tests.test_kubernetes_sampling import World as PodWorld,PRIVATE
from tests.test_kubernetes_request_binding import env,target
from tests.test_stream_probe import _server

TOKEN="ab"*16


class World(PodWorld):
    def __init__(self,value=None,damage=None):
        super().__init__(value or target());self.damage=damage
        self.primary_started=None;self.primary_release=None
    def runner(self,argv,**kwargs):
        if "exec" not in argv:return super().runner(argv,**kwargs)
        spec=json.loads(kwargs["input_bytes"]);request,=spec["requests"]
        self.execs.append(deepcopy(request));self.after_exec=True
        assert 0<spec["timeout"]<=30 and "--container=probe" in argv
        if self.primary_started is not None and request["id"].endswith("-primary"):
            self.primary_started.set();assert self.primary_release.wait(2)
        if self.damage=="timeout" and request["id"].endswith("-after"):raise TimeoutError(PRIVATE)
        model=self.target["model_mapping"]["api_model"]
        body=json.dumps({"model":model,"choices":[{"message":{"role":"assistant","content":PRIVATE},"finish_reason":"length"}]})
        row={"id":request["id"],"method":"POST","mode":"json","outcome":400 if self.damage=="error-status" else 200,
             "body":body,"body_sha256":hashlib.sha256(body.encode()).hexdigest(),"body_bytes":len(body.encode()),
             "capture_complete":self.damage!="partial","eof":True,"utf8_valid":True,"body_truncated":False}
        pipe={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}
        if self.damage=="wrong-id":row["id"]="before"
        elif self.damage=="cleanup" and request["id"].endswith("-after"):pipe.update(pipes_closed=False,read_errors=["child_reap"])
        elif self.damage=="oversized":row["body"]=PRIVATE*20000
        return {"returncode":0,"stdout_bytes":canonical_json([row]),"stderr_bytes":PRIVATE.encode(),"capture":pipe}


def setup(world=None,environment=None):
    world=world or World()
    value=kube.KubernetesHTTP(world.target,environment or env(),duration=60,runner=world.runner,profile=kube.ONLINE_INPUT_PROFILE)
    return value,world


def request(value,case="normal",phase="primary",token=TOKEN,**extra):
    return {"id":"online-"+case+"-"+phase,"method":"POST","mode":"json",
        "url":value.endpoint.geturl()+"/v1/chat/completions",
        "headers":{"X-Request-Id":"byul-online-"+token+"-"+case+"-"+phase},"read_delay_ms":0,
        "body":{"model":value.api_model,"messages":[{"role":"user","content":"hello"}],"max_tokens":1,"stream":False,**extra}}


def send(value,case="normal",phase="primary",**extra):
    return value("kubernetes-probe",[request(value,case,phase,**extra)],30)


@pytest.mark.parametrize("fields",[
    {},{"kv_transfer_params":{}},{"kv_transfer_params":None},{"kv_transfer_params":{"prompt_token_ids":[0,32,-1]}},
    {"echo":True},{"echo":None},{"chat_template":"EXAMPLE {{ messages }}"},{"chat_template":None},
    {"chat_template_kwargs":{"suffix":"안녕","nested":{"enabled":True,"count":2}}},{"chat_template_kwargs":None},
    {"mm_processor_kwargs":{"code_revision":"EXAMPLE_revision"}},{"media_io_kwargs":{"EXAMPLE":{"frames":2}}},
    {"echo":False,"chat_template":"EXAMPLE","chat_template_kwargs":{},"mm_processor_kwargs":{},"media_io_kwargs":None,
     "kv_transfer_params":{"prompt_token_ids":[]}},
])
def test_primary_field_shapes_and_normal_after_are_body_free(fields):
    value,world=setup();before=deepcopy(fields)
    primary,=send(value,**fields);after,=send(value,phase="after")
    assert fields==before and [primary["outcome"],after["outcome"]]==[200,200]
    assert len(world.execs)==2 and all(r["body"]["model"]==value.api_model for r in world.execs)
    assert value.api_model!=env().model.id
    report=value.report()
    assert report["profile"]=="kubernetes-owned-online-input-http-v1" and report["input_contract_version"]==1
    assert report["data_origin"]=="EXAMPLE" and not report["native_consumption_verified"]
    assert not report["collection_gap"] and not report["cleanup_errors"]
    assert all(row["binding_verified_before_after"] for row in report["requests"])
    assert PRIVATE not in json.dumps(report) and TOKEN not in json.dumps(report) and "body" not in json.dumps(report)


@pytest.mark.parametrize("damage",["id","case-long","method","path","origin","query","header","rid","token","mode",
    "body-key","repository-model","max-bool","max-two","stream","message-role","message-url","message-image",
    "message-count","echo","template","kwargs","kwargs-url","kv-remote","kv-nonlist","kv-bool","kv-big",
    "want-body","delay","bool-delay","batch","timeout","network","depth","nodes","nan","bytes","object"])
def test_unsupported_envelope_closes_before_any_remote_io(damage):
    value,world=setup();req=request(value);timeout=30;network="kubernetes-probe"
    if damage=="id":req["id"]="json-generated-A"
    elif damage=="case-long":req=request(value,case="a"*61)
    elif damage=="method":req["method"]="GET"
    elif damage=="path":req["url"]=value.endpoint.geturl()+"/tokenize"
    elif damage=="origin":req["url"]="http://127.0.0.99:8000/v1/chat/completions"
    elif damage=="query":req["url"]+="?private=1"
    elif damage=="header":req["headers"]["Authorization"]=PRIVATE
    elif damage=="rid":req["headers"]["X-Request-Id"]+="-other"
    elif damage=="token":req["headers"]["X-Request-Id"]="byul-online-invalid-normal-primary"
    elif damage=="mode":req["mode"]="sse"
    elif damage=="body-key":req["body"]["tools"]=[]
    elif damage=="repository-model":req["body"]["model"]=env().model.id
    elif damage=="max-bool":req["body"]["max_tokens"]=True
    elif damage=="max-two":req["body"]["max_tokens"]=2
    elif damage=="stream":req["body"]["stream"]=True
    elif damage=="message-role":req["body"]["messages"][0]["role"]="tool"
    elif damage=="message-url":req["body"]["messages"][0]["content"]="https://private.invalid/"
    elif damage=="message-image":req["body"]["messages"][0]["content"]=[{"type":"image_url","url":"PRIVATE"}]
    elif damage=="message-count":req["body"]["messages"]*=17
    elif damage=="echo":req["body"]["echo"]=1
    elif damage=="template":req["body"]["chat_template"]={}
    elif damage=="kwargs":req["body"]["mm_processor_kwargs"]=[]
    elif damage=="kwargs-url":req["body"]["media_io_kwargs"]={"url":"http://private.invalid/"}
    elif damage=="kv-remote":req["body"]["kv_transfer_params"]={"remote_engine_id":"PRIVATE"}
    elif damage=="kv-nonlist":req["body"]["kv_transfer_params"]={"prompt_token_ids":"0,1"}
    elif damage=="kv-bool":req["body"]["kv_transfer_params"]={"prompt_token_ids":[True]}
    elif damage=="kv-big":req["body"]["kv_transfer_params"]={"prompt_token_ids":[2**63]}
    elif damage=="want-body":req["want_body"]=False
    elif damage=="delay":req["read_delay_ms"]=1
    elif damage=="bool-delay":req["read_delay_ms"]=False
    elif damage=="timeout":timeout=31
    elif damage=="network":network="other"
    elif damage=="depth":
        nested={}
        for _ in range(15):nested={"a":nested}
        req["body"]["chat_template_kwargs"]=nested
    elif damage=="nodes":req["body"]["chat_template_kwargs"]={"many":[0]*4096}
    elif damage=="nan":req["body"]["chat_template_kwargs"]={"value":float("nan")}
    elif damage=="bytes":req["body"]["chat_template_kwargs"]={"a":"가"*4096,"b":"가"*4096}
    elif damage=="object":req["body"]["chat_template_kwargs"]={"value":object()}
    with pytest.raises(kube.KubernetesBindingError):
        value(network,[req,deepcopy(req)] if damage=="batch" else [req],timeout)
    assert not world.calls and not world.execs
    assert value.report()["collection_gap"] and PRIVATE not in json.dumps(value.report())


def test_normal_after_cannot_carry_protected_fields():
    value,world=setup();send(value)
    with pytest.raises(kube.KubernetesBindingError):send(value,phase="after",echo=False)
    assert len(world.execs)==1


def test_missing_normal_after_is_an_explicit_collection_gap():
    value,world=setup();send(value)
    assert value.report()["collection_gap"]
    send(value,phase="after")
    assert not value.report()["collection_gap"] and len(world.execs)==2


@pytest.mark.parametrize("origin",[DataOrigin.EXAMPLE,DataOrigin.DECLARED,DataOrigin.MEASURED])
def test_uninjected_report_retains_environment_origin_ceiling_without_io(origin):
    environment=env().model_copy(update={"data_origin":origin}).seal();selected=target()
    selected["environment_ref"]=environment.ref().model_dump(mode="json");selected["lab"]=False
    value=kube.KubernetesHTTP(selected,environment,duration=60,profile=kube.ONLINE_INPUT_PROFILE)
    assert value.report()["data_origin"]==origin.value and not value.report()["native_consumption_verified"]


def test_sixteen_paired_cases_then_reject_before_remote():
    value,world=setup()
    for i in range(16):
        send(value,case="case-"+str(i));send(value,case="case-"+str(i),phase="after")
    assert len(world.execs)==32 and not value.report()["collection_gap"]
    with pytest.raises(kube.KubernetesBindingError):send(value,case="case-seventeen")
    assert len(world.execs)==32


@pytest.mark.parametrize("damage",["after-first","repeat-primary","interleave","token","repeat-case"])
def test_pair_sequence_and_token_are_not_reused(damage):
    value,world=setup()
    if damage!="after-first":send(value,case="first")
    if damage=="after-first":req=request(value,case="first",phase="after")
    elif damage=="repeat-primary":req=request(value,case="first")
    elif damage=="interleave":req=request(value,case="second")
    elif damage=="token":req=request(value,case="first",phase="after",token="cd"*16)
    else:
        send(value,case="first",phase="after");req=request(value,case="first")
    count=len(world.execs)
    with pytest.raises(kube.KubernetesBindingError):value("kubernetes-probe",[req],30)
    assert len(world.execs)==count


@pytest.mark.parametrize("profile",[kube.PROFILE,kube.REQUEST_BINDING_PROFILE,kube.VALIDATION_ERROR_PROFILE,kube.MODEL_AUTHORIZATION_PROFILE])
def test_online_ids_are_not_routed_through_other_profiles(profile):
    online,world=setup()
    other=kube.KubernetesHTTP(world.target,env(),duration=60,runner=world.runner,profile=profile)
    with pytest.raises(kube.KubernetesBindingError):other("kubernetes-probe",[request(online)],30)
    assert not world.calls and not world.execs


@pytest.mark.parametrize("role",["engine","probe"])
@pytest.mark.parametrize("field",["pod_uid","container_id","image_id","restart_count","started_at"])
def test_selected_generation_change_rejects_response(role,field):
    value,world=setup()
    def mutate(which,row,after):
        if which!=role or not after:return
        if field=="pod_uid":row["metadata"]["uid"]="replaced"
        elif field=="started_at":row["status"]["containerStatuses"][0]["state"]["running"]["startedAt"]="replaced"
        else:row["status"]["containerStatuses"][0][{"container_id":"containerID","image_id":"imageID","restart_count":"restartCount"}[field]]=1
    world.mutate=mutate
    with pytest.raises(kube.KubernetesBindingError):send(value)
    assert len(world.execs)==1 and not value.report()["requests"][0]["binding_verified_before_after"]


@pytest.mark.parametrize("damage",["wrong-id","oversized"])
def test_response_transport_gaps_are_fixed_and_sanitized(damage):
    value,world=setup(World(damage=damage))
    with pytest.raises(kube.KubernetesBindingError):send(value)
    assert value.report()["collection_gap"] and PRIVATE not in json.dumps(value.report())


@pytest.mark.parametrize("damage",["timeout","cleanup","partial","error-status"])
def test_known_primary_remains_available_after_followup_gap(damage):
    value,world=setup()
    primary,=send(value)
    world.damage=damage
    if damage=="timeout":
        with pytest.raises(kube.KubernetesBindingError):send(value,phase="after")
    else:
        after,=send(value,phase="after")
        assert after["outcome"]==(400 if damage=="error-status" else 200)
    assert primary["outcome"]==200 and primary["capture_complete"]
    report=value.report()
    if damage=="cleanup":assert report["cleanup_errors"]==["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]
    if damage in {"timeout","partial"}:assert report["collection_gap"]
    assert PRIVATE not in json.dumps(report)


def test_online_calls_are_serial_even_when_after_is_invoked_before_primary_returns():
    world=World();world.primary_started=threading.Event();world.primary_release=threading.Event()
    value,_=setup(world)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(send,value)
        assert world.primary_started.wait(2)
        after=pool.submit(send,value,phase="after")
        assert len(world.execs)==1
        world.primary_release.set()
        assert first.result(timeout=3)[0]["outcome"]==200
        assert after.result(timeout=3)[0]["outcome"]==200
    assert len(world.execs)==2


@pytest.mark.parametrize("origin",[DataOrigin.EXAMPLE,DataOrigin.DECLARED,DataOrigin.MEASURED])
def test_injected_observer_ceiling_and_unsupported_engine(origin):
    environment=env().model_copy(update={"data_origin":origin}).seal();world=World()
    world.target["environment_ref"]=environment.ref().model_dump(mode="json");world.target["lab"]=False
    value,_=setup(world,environment)
    assert value.report()["data_origin"]=="EXAMPLE"
    environment.engine.version="0.32.0";environment=environment.seal()
    world.target["environment_ref"]=environment.ref().model_dump(mode="json")
    with pytest.raises(kube.KubernetesBindingError):
        kube.KubernetesHTTP(world.target,environment,duration=60,runner=world.runner,profile=kube.ONLINE_INPUT_PROFILE)
    assert not world.calls


def test_selected_url_shaped_alias_is_not_mistaken_for_a_remote_media_address():
    world=World();world.target["model_mapping"]["api_model"]="alias://owned-model"
    value,_=setup(world);send(value);send(value,phase="after")
    assert len(world.execs)==2 and all(r["body"]["model"]=="alias://owned-model" for r in world.execs)


def test_exact_pinned_probe_live_loopback_unicode_and_typed_error():
    def handle(conn,raw,index,stop):
        _,_,payload=raw.partition(b"\r\n\r\n");body=json.loads(payload)
        assert body["model"]=="fixture-model" and body["max_tokens"]==1
        primary="echo" in body
        data=json.dumps({"error":{"message":"EXAMPLE 오류"}} if primary else {"model":"fixture-model","choices":[]},ensure_ascii=False).encode()
        status=b"400 Bad Request" if primary else b"200 OK"
        conn.sendall(b"HTTP/1.1 "+status+b"\r\nContent-Type: application/json\r\nContent-Length: "+str(len(data)).encode()+b"\r\nConnection: close\r\n\r\n"+data)
    with _server(handle) as (url,accepted):
        value_target=target();value_target["engine"]["trusted_url"]=url;world=World(value_target)
        def runner(argv,**kwargs):
            if "exec" in argv:return capture_pipes([sys.executable,"-c",argv[-2],argv[-1]],**kwargs)
            return world.runner(argv,**kwargs)
        value=kube.KubernetesHTTP(value_target,env(),duration=30,runner=runner,profile=kube.ONLINE_INPUT_PROFILE)
        first,=send(value,echo=True,chat_template_kwargs={"suffix":"안녕"})
        after,=send(value,phase="after")
        assert len(accepted)==2
    assert [first["outcome"],after["outcome"]]==[400,200]
    assert all(row["capture_complete"] and row["eof"] and row["utf8_valid"] for row in (first,after))
    assert first["body_sha256"]==hashlib.sha256(first["body"].encode()).hexdigest()
    assert "오류" not in json.dumps(value.report(),ensure_ascii=False) and not value.report()["native_consumption_verified"]
