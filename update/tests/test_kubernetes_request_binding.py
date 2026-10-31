"""Same kubectl/stdin interface and existing REQ11 analyzer; no cluster or GPU."""
from copy import deepcopy
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_request_binding import KubernetesRequestBindingExecutor
from guard.models import canonical_json
from guard.store import Store
from tests.builders import baseline_pack, environment, policy, T0
from tests.test_kubernetes_sampling import World as PodWorld, inputs, PRIVATE
from tests.test_request_binding import World as RequestWorld, IMAGE, SHA, TEMPLATE_PATH, VALUES, target as docker_target, judge


def env():
    return environment(engine={"name":"vllm","version":"0.31.0","image_digest":IMAGE},
        model={"id":"EXAMPLE-repository-id","revision":"EXAMPLE-revision"})


def target(environment_=None):
    environment_=environment_ or env()
    value=inputs();value.update(schema="guard-kubernetes-request-binding-target/1",
        environment_ref=environment_.ref().model_dump(mode="json"),observed_version="0.31.0",
        model_mapping={"repository_id":environment_.model.id,"revision":environment_.model.revision,"api_model":"fixture-model"},
        comparison_config=deepcopy(docker_target()["comparison_config"]))
    value["engine"].update(image_digest=IMAGE,command=["vllm","serve"])
    value["kubernetes"]["engine"].update(image=IMAGE,image_id=IMAGE)
    return value


class World(PodWorld):
    def __init__(self,target_=None,*,late=None,**changes):
        super().__init__(target_ or target())
        self.backend=RequestWorld(**changes);self.late=late;self.current_label=None
        self.template_reads=0;self.replaced=False;self.packets=[]

    def runner(self,argv,**kwargs):
        if "exec" in argv and "input_bytes" not in kwargs:
            assert "-i" not in argv and argv[-1]==TEMPLATE_PATH
            self.template_reads+=1
            value={"sha256":SHA,"bytes":100}
            result={"returncode":0,"stdout_bytes":json.dumps(value).encode(),"stderr_bytes":PRIVATE.encode(),"capture":{"complete":True}}
        elif "exec" in argv:
            spec=json.loads(kwargs["input_bytes"]);requests=spec["requests"]
            self.current_label=requests[0]["id"];self.packets.append(deepcopy(requests))
            self.after_exec=True
            if self.late=="exception" and self.current_label=="after":raise OSError(PRIVATE)
            rows=(self.backend.probe("EXAMPLE-trusted",requests,spec["timeout"]) if self.current_label.startswith("tokenize-")
                else self.backend.stream("EXAMPLE-trusted",requests,spec["timeout"]))
            result={"returncode":0,"stdout_bytes":json.dumps(rows).encode(),"stderr_bytes":PRIVATE.encode(),"capture":{"complete":True}}
            if self.late=="cleanup" and self.current_label=="after":
                result["capture"].update(complete=False,pipes_closed=False,read_errors=["child_reap"])
            if self.late=="duplicate" and self.current_label=="after":
                result["stdout_bytes"]=b'[{"id":"after","id":"after"}]'
            if self.late=="truncated" and self.current_label=="after":result["capture"]["complete"]=False
            if self.late=="restart" and self.current_label.startswith("sse-"):self.replaced=True
        else:
            engine_name=self.target["kubernetes"]["engine"]["pod"]
            result=super().runner(["engine-pod" if arg==engine_name else arg for arg in argv],**kwargs)
            value=json.loads(result["stdout_bytes"])
            if value.get("kind")!="List" and "spec" in value:
                if value["metadata"]["name"]==self.target["kubernetes"]["engine"]["pod"]:
                    value["spec"]["containers"][0].update(command=self.target["engine"]["command"],
                        args=self.target["comparison_config"]["launch"]["argv"],volumeMounts=[{"name":"template","mountPath":TEMPLATE_PATH,"readOnly":True}])
                    if self.replaced:value["status"]["containerStatuses"][0]["restartCount"]+=1
                result["stdout_bytes"]=json.dumps(value).encode()
        return result


def run(tmp_path,world=None):
    world=world or World();path=tmp_path/"target.json";path.write_text(json.dumps(world.target),encoding="utf-8")
    executor=KubernetesRequestBindingExecutor({"target":str(path)},runner=world.runner,environ={})
    return executor.run(baseline_pack().checks["REQ-11"],env(),deepcopy(VALUES),VllmAdapter(),tmp_path/"work"),world


def test_same_cases_analyzer_and_declared_model_mapping_without_docker(tmp_path,monkeypatch):
    from guard.executors import request_binding
    monkeypatch.setattr(request_binding,"Docker",lambda *a:(_ for _ in ()).throw(AssertionError("no Docker")))
    draft,world=run(tmp_path)
    assert draft.data_origin.value=="EXAMPLE" and draft.status.value=="COMPLETED"
    assert draft.observations["input_projection_verified"].value is True
    assert draft.observations["output_binding_verified"].value is False
    assert judge(draft).verdicts["security"].value=="UNKNOWN" and judge(draft).verdicts["functional"].value=="PASS"
    assert world.template_reads==2 and sum(map(len,world.packets))==20
    assert all(request["body"]["model"]=="fixture-model" for batch in world.packets for request in batch)
    for artifact in draft.artifacts:
        text=artifact.path.read_text(encoding="utf-8")
        assert PRIVATE not in text and "Guard attribution A" not in text and "EXAMPLE-generated-without-input-marker" not in text


@pytest.mark.parametrize("late",["exception","restart","cleanup","duplicate","truncated"])
def test_independently_bound_fail_survives_later_collection_or_cleanup_gap(tmp_path,late):
    draft,_=run(tmp_path,World(late=late,foreign="json-distinct_body-A"))
    assert draft.observations["security_violations"].value
    assert judge(draft).verdicts["security"].value=="FAIL"
    assert draft.observations["collection_complete"].value is False
    if late=="cleanup":assert draft.cleanup_errors==["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]


@pytest.mark.parametrize("damage",["ref","seal","model","revision","image","endpoint","schema"])
def test_invalid_binding_rejected_before_any_kubectl_or_request(tmp_path,damage):
    world=World();environment_=env()
    if damage=="ref":world.target["environment_ref"]["sha256"]="0"*64
    elif damage=="seal":environment_=environment_.model_copy(update={"content_sha256":"0"*64})
    elif damage in {"model","revision"}:world.target["model_mapping"]["repository_id" if damage=="model" else "revision"]="other"
    elif damage=="image":world.target["engine"]["image_digest"]="other@sha256:"+"f"*64
    elif damage=="endpoint":world.target["engine"]["trusted_url"]="https://service.invalid"
    else:world.target["schema"]="unknown"
    path=tmp_path/"target.json";path.write_text(json.dumps(world.target))
    def forbidden(*a,**k):raise AssertionError("must reject before transport")
    executor=KubernetesRequestBindingExecutor({"target":str(path)},runner=forbidden)
    with pytest.raises(RuntimeError):executor.run(baseline_pack().checks["REQ-11"],environment_,VALUES,VllmAdapter(),tmp_path/"work")


@pytest.mark.parametrize("role",["engine","probe"])
@pytest.mark.parametrize("field",["pod_uid","container_id","image_id","restart_count"])
def test_new_profile_checks_both_selected_generations_after_dispatch(tmp_path,role,field):
    world=World()
    def mutate(which,value,after):
        if which!=role or not after:return
        if field=="pod_uid":value["metadata"]["uid"]="replacement"
        else:value["status"]["containerStatuses"][0][{"container_id":"containerID","image_id":"imageID","restart_count":"restartCount"}[field]]=1
    world.mutate=mutate
    draft,_=run(tmp_path,world)
    assert draft.observations["input_projection_verified"].value is False
    assert judge(draft).verdicts["security"].value=="UNKNOWN"
    assert len(world.packets)==1


def test_unreviewed_gpu_image_does_not_gain_cpu_consumer_support(tmp_path):
    environment_=env().model_copy(deep=True)
    environment_.engine.image_digest="example.invalid/gpu@sha256:"+"f"*64;environment_=environment_.seal()
    value=target(environment_)
    value["engine"]["image_digest"]=environment_.engine.image_digest
    value["kubernetes"]["engine"].update(image=environment_.engine.image_digest,image_id=environment_.engine.image_digest)
    world=World(value);path=tmp_path/"target.json";path.write_text(json.dumps(value))
    executor=KubernetesRequestBindingExecutor({"target":str(path)},runner=world.runner)
    draft=executor.run(baseline_pack().checks["REQ-11"],environment_,VALUES,VllmAdapter(),tmp_path/"work")
    assert not world.packets and world.template_reads==0
    assert draft.observations["source_contract_verified"].value is False
    assert draft.observations["profile_supported"].value is False


def test_exact_shipped_probe_source_parses_real_cpu_loopback_json_and_sse():
    import sys
    import threading
    from guard.runtime.bounded import capture_pipes
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    from tests.test_stream_probe import _server, _response
    barrier=threading.Barrier(2)
    def handle(conn,request,index,stop):
        body=json.loads(request.partition(b"\r\n\r\n")[2]);barrier.wait(timeout=2)
        if body["stream"]:
            _response(conn,b'data: {"marker":"EXAMPLE-stream"}\n\ndata: [DONE]\n\n',content_type="text/event-stream")
        else:_response(conn,b'{"marker":"EXAMPLE-json"}')
    with _server(handle) as (url,accepted):
        value=target();value["engine"]["trusted_url"]=url;world=World(value)
        def runner(argv,**kwargs):
            if "exec" in argv and "input_bytes" in kwargs:
                return capture_pipes([sys.executable,"-c",argv[-2],argv[-1]],**kwargs)
            return world.runner(argv,**kwargs)
        transport=KubernetesHTTP(value,env(),duration=10,runner=runner,profile=REQUEST_BINDING_PROFILE)
        requests=[{"id":"json-generated-A" if mode=="json" else "sse-generated-B","method":"POST","mode":mode,
            "url":url+"/v1/chat/completions","body":{"model":"fixture-model","stream":mode=="sse"}} for mode in ("json","sse")]
        responses=transport("kubernetes-probe",requests,5)
    assert len(accepted)==2 and all(row["capture_complete"] and row["eof"] for row in responses)
    assert responses[1]["terminal_seen"] is True and len(responses[1]["events"])==2
    assert len({row["connection_id"] for row in responses})==2
    assert all(row["binding_verified_before_after"] for row in transport.report()["requests"])
    assert "EXAMPLE-stream" not in json.dumps(transport.report()) and "EXAMPLE-json" not in json.dumps(transport.report())


@pytest.mark.parametrize("change",[{"missing_done":"sse-generated-A"},{"incomplete":"json-generated-A"},{"after_error":"after"}])
def test_missing_partial_or_rejected_response_keeps_unknown_without_invented_fail(tmp_path,change):
    draft,_=run(tmp_path,World(**change))
    assert draft.observations["collection_complete"].value is False
    assert not draft.observations["security_violations"].value
    assert judge(draft).verdicts["security"].value=="UNKNOWN"


@pytest.mark.parametrize("mode,expected",[("normal","UNKNOWN"),("foreign","FAIL"),("attachment","FAIL"),("wrong-owner","UNKNOWN")])
def test_existing_cli_evd_res_rpt_refs_export_restore_and_rejudge(tmp_path,monkeypatch,mode,expected):
    world=World(foreign="json-distinct_body-A" if mode in {"foreign","attachment"} else None)
    if mode=="wrong-owner":world.mutate=lambda which,value,after:value["metadata"]["labels"].update({"guard.run":"other"}) if which=="engine" else None
    pack=baseline_pack();environment_=env()
    pol=policy(pack.ref(),scope={"include":["REQ-11"]},values={"REQ-11":VALUES})
    paths={}
    for key,value in (("env",environment_.to_dict()),("policy",pol.to_dict()),("target",world.target)):
        paths[key]=tmp_path/(key+".json");paths[key].write_bytes(canonical_json(value))
    config=tmp_path/"guard.toml"
    config.write_text('[executors]\nrequest_binding="kubernetes"\n[adapter]\nname="vllm"\n[request_binding]\ntarget='+json.dumps(paths["target"].as_posix())+'\n')
    monkeypatch.setitem(registry.EXECUTORS,"request_binding:kubernetes",lambda raw:KubernetesRequestBindingExecutor(raw["request_binding"],runner=world.runner,environ={}))
    def unavailable(*a,**k):raise OSError(PRIVATE)
    monkeypatch.setattr(subprocess,"Popen",unavailable);monkeypatch.setattr(socket,"socket",unavailable)
    runner=CliRunner();store_path=tmp_path/"store";common=["--config",str(config),"--store",str(store_path)]
    def cli(args):
        result=runner.invoke(app,args);assert result.exit_code==0,(args,result.output,repr(result.exception));return result.output
    plan=cli(["select","--pack",str(pack.path),"--env",str(paths["env"]),"--policy",str(paths["policy"]),*common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if mode=="attachment":
            original=Store.put_artifact;calls=[]
            def partial(self,*a,**k):
                calls.append(1)
                if len(calls)==2:raise OSError(PRIVATE)
                return original(self,*a,**k)
            capture.setattr(Store,"put_artifact",partial)
        cli(["run","--plan",plan,"--pack",str(pack.path),*common])
    cli(["judge","--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),*common])
    cli(["report","--plan",plan,"--pack",str(pack.path),"--json",str(tmp_path/"report.json"),"--as-of",T0.isoformat(),*common])
    cli(["verify",*common]);store=Store(store_path)
    evd,=store.find_latest("EVD",lambda e:True);res,=store.find_latest("RES",lambda e:True);rpt,=store.find_latest("RPT",lambda e:True)
    assert res.verdicts.security.value==expected and evd.data_origin.value=="EXAMPLE" and not rpt.official
    assert store.resolve(evd.environment).model.id=="EXAMPLE-repository-id"
    if mode=="attachment":assert evd.execution.status.value=="ABORTED" and len(evd.artifacts)==1
    assert PRIVATE not in canonical_json(evd.to_dict()).decode()
    bundle=tmp_path/"bundle.zip";restored_path=tmp_path/"restored"
    cli(["evidence","export","--store",str(store_path),"--output",str(bundle)])
    cli(["evidence","restore","--bundle",str(bundle),"--destination",str(restored_path)])
    restored=Store(restored_path)
    assert restored.resolve(evd.ref()).to_dict()==evd.to_dict() and restored.resolve(rpt.ref()).to_dict()==rpt.to_dict()
    cli(["judge","--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),"--store",str(restored_path),"--config",str(config)])
    assert restored.find_latest("RES",lambda e:True)[0].ref()==res.ref()
