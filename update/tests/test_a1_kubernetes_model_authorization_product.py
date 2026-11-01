"""REQ05 정식 probe/native/Store 경계. kubectl와 serving은 EXAMPLE 대역이다."""
import ast
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.cli import app
from guard.executors import kubernetes_model_authorization as model
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.executors.session_seal import SESSION_SEALER
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_kubernetes_lifecycle import group,target as native_target,World as NativeWorld
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_kubernetes_sampling import World as PodWorld,inputs as pod_inputs
from tests.test_model_selection_sensor import metadata,model_payload
from tools.build_kubernetes_model_authorization_pack import derive

PRIVATE="EXAMPLE_REQ05_PRIVATE"
ALLOWED_KEY="EXAMPLE_allowed_credential_05"
DENIED_KEY="EXAMPLE_denied_credential_05"
BASE="vllm.model_executor.model_loader.base_loader"
GPU="vllm.v1.worker.gpu_model_runner"


class World:
    def __init__(self,group,target,mode):
        self.group,self.mode=group,mode
        self.target=pod_inputs()
        self.target.update(schema="guard-kubernetes-request-binding-target/1",lab=mode!="actual-lab-false",
            environment_ref=group.env.ref().model_dump(mode="json"),
            model_mapping={"repository_id":group.env.model.id,"revision":group.env.model.revision,"api_model":"fixture-model"})
        self.target["kubernetes"]["engine"]=deepcopy(target["kubernetes"]["engine"])
        self.target["engine"].update(image_digest=group.env.engine.image_digest)
        self.pods=PodWorld(self.target)
        self.native=NativeWorld(group,target,mode="partial-pipe" if mode=="native-partial" else None)
        self.sent=[];self.native_ops=[]
        if mode=="native-one-missing":self.native.docker.group_mode="bad-43"
        if mode=="native-census-gap":self.native.docker.group_mode="new-file"

    def add_native(self,rid):
        request=hashlib.sha256(rid.encode()).hexdigest()
        native=hashlib.sha256(("native-"+rid).encode()).hexdigest()
        def change(rows):
            additions=[]
            ready={r["payload"].get("module") for r in rows if r["event"]=="MODULE"}
            for module_ in (BASE,GPU):
                if module_ not in ready:
                    additions.append({"event":"MODULE","monotonic_ns":rows[-1]["monotonic_ns"],
                        "payload":{"module":module_,"verified":True,"source_sha256":sensor.SOURCE_SHA[module_],"code_sha256":"c"*64}})
            if BASE not in ready:
                payload=model_payload("BaseModelLoader.load_model",model={**metadata(),"returned_model_sha256":"9"*64,
                    "configured_model_sha256":hashlib.sha256(self.group.env.model.id.encode()).hexdigest(),
                    "configured_revision_sha256":hashlib.sha256(self.group.env.model.revision.encode()).hexdigest()})
                additions.append({"event":"NATIVE","monotonic_ns":rows[-1]["monotonic_ns"],"payload":payload})
            assigned={"point":"InputProcessor.assign_request_id","phase":"RETURN","source_sha256":sensor.SOURCE_SHA["vllm.v1.engine.input_processor"],
                "requests":[{"request_sha256":native,"request_present":None,"output_state_present":None,"batch_row_present":None,"status_code":None}],
                "metadata":{"external_request_sha256":request,"asgi_request_sha256":request},"selection_observed":True,
                "observation_started_ns":0,"observation_finished_ns":0}
            forward=model_payload(model={**metadata(),"owner_model_sha256":"9"*64})
            forward["requests"]=deepcopy(assigned["requests"])
            if self.mode=="native-unlinked":assigned["metadata"]["asgi_request_sha256"]="0"*64
            additions.extend({"event":"NATIVE","monotonic_ns":rows[-1]["monotonic_ns"],"payload":p} for p in (assigned,forward))
            rows[-1:-1]=additions
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)

    def runner(self,argv,**kwargs):
        if "exec" in argv and "input_bytes" in kwargs:
            spec=json.loads(kwargs["input_bytes"]);request,=spec["requests"];self.sent.append(request)
            assert request["body"]=={"model":"fixture-model","messages":[{"role":"user","content":"hello"}],"max_tokens":1,"stream":False}
            self.add_native(request["headers"]["X-Request-Id"])
            slot=request["id"].removeprefix("req05-model-")
            assert request["headers"]["Authorization"]=="Bearer "+(DENIED_KEY if slot=="challenge" else ALLOWED_KEY)
            body=json.dumps({"model":"fixture-model","choices":[{"message":{"role":"assistant","content":PRIVATE},"finish_reason":"length"}]})
            row={"id":request["id"],"mode":"json","method":"POST","outcome":403 if self.mode=="denied-response" and slot=="challenge" else 200,
                "body":body,"body_sha256":sha256_hex(body.encode()),"body_bytes":len(body.encode()),"capture_complete":True,
                "utf8_valid":True,"eof":True,"body_truncated":False}
            pipe={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}
            if self.mode=="before-shape" and slot=="before":row["body"]="{}"
            if self.mode=="after-timeout" and slot=="after":raise TimeoutError(PRIVATE)
            if self.mode=="http-cleanup" and slot=="after":pipe.update(pipes_closed=False,read_errors=["child_reap"])
            if self.mode=="wrong-response-id" and slot=="challenge":row["id"]="before"
            return {"returncode":0,"stdout_bytes":canonical_json([row]),"stderr_bytes":PRIVATE.encode(),"capture":pipe}
        if "exec" in argv:
            command=argv[argv.index("--")+1:]
            tree=ast.parse(command[5])
            call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=="compile")
            source=ast.literal_eval(call.args[0])
            if source==SESSION_SEALER:
                assert command[5]==supervised_source(SESSION_SEALER) and command[7:]==[self.group.prepared["session"]]
                self.native_ops.append("seal")
                value={"schema":"guard-session-seal/1","session":self.group.prepared["session"],"sealed":self.mode!="seal-failure",
                    "recordings_closed":2,"terminal_inventory_sha256":"c"*64}
                return {"returncode":0,"stdout_bytes":canonical_json(value),"stderr_bytes":PRIVATE.encode(),
                    "capture":{"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}}
            self.native_ops.append("read")
            return self.native.runner(argv,**kwargs)
        if "namespace" in argv:
            return self.native.runner(argv,**kwargs)
        result=self.pods.runner(argv,**kwargs)
        value=json.loads(result["stdout_bytes"])
        if "namespaces" not in argv:value["kind"]="Pod"
        result["stdout_bytes"]=canonical_json(value)
        result["capture"].update(bounded_at_source=True,timed_out=False,truncated=False)
        if self.mode=="engine-replaced" and self.sent and "pod" in argv:
            value["status"]["containerStatuses"][0]["restartCount"]+=1
            result["stdout_bytes"]=canonical_json(value)
        return result


def inputs(tmp_path,group,target,mode):
    # This owned test ENV explicitly supplies the model identity absent from the
    # older generic lifecycle fixture; production inputs never infer it.
    environment=group.env.model_copy(deep=True)
    environment.model.id="EXAMPLE-model";environment.model.revision="EXAMPLE-revision"
    group.env=environment.seal()
    target=deepcopy(target);target["environment_ref"]=group.env.ref().model_dump(mode="json")
    world=World(group,target,mode)
    receipt=derive(baseline_pack().path,tmp_path/"pack");pack=load_pack(tmp_path/"pack")
    now=utcnow()
    identity=(hashlib.sha256(group.env.model.id.encode()).hexdigest(),hashlib.sha256(group.env.model.revision.encode()).hexdigest())
    values={"model_environment_sha256":group.env.ref().sha256,"model_target_sha256":sha256_hex(canonical_json(world.target)),
        "allowed_principal_sha256":"1"*64,"denied_principal_sha256":"2"*64,
        "selected_model_grants":[":".join(("1"*64,*identity))],
        "retired_model_revisions":[":".join((*identity,(now-timedelta(seconds=2)).isoformat()))] if mode=="retired" else [],
        "model_validity_start":(now-timedelta(minutes=1)).isoformat(),"model_validity_end":(now+timedelta(minutes=1)).isoformat()}
    shutil.copytree(group.manifest_path.parent,tmp_path/"prepared")
    return world,values,pack,receipt


@pytest.mark.parametrize("mode",["normal","denied-response","retired","native-unlinked","native-one-missing",
    "native-partial","native-census-gap","before-shape","after-timeout","http-cleanup","wrong-response-id",
    "engine-replaced","seal-failure","attachment","actual-lab-false"])
def test_registered_cli_owned_probe_native_and_exact_refs(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setenv("REQ05_ALLOWED_KEY",ALLOWED_KEY);monkeypatch.setenv("REQ05_DENIED_KEY",DENIED_KEY)
    monkeypatch.setattr(http,"capture_pipes",world.runner)
    monkeypatch.setattr("guard.executors.kubernetes_lifecycle_capture.capture_pipes",world.runner)
    def forbidden(*a,**kw):raise AssertionError("unexpected external runtime")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    if mode=="attachment":
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=="probe_log.jsonl" and a and a[0]=="w":raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,"open",open_file)
    pol=policy(pack.ref(),scope={"include":["REQ-05"]},values={"REQ-05":values})
    for name,value in (("environment",group.env.to_dict()),("policy",pol.to_dict()),("target",world.target)):
        (tmp_path/(name+".json")).write_bytes(canonical_json(value))
    cfg=tmp_path/"guard.toml"
    cfg.write_text('[executors]\nmodel_authorization="kubernetes"\n[adapter]\nname="vllm"\n[model_authorization]\ntarget='
        +json.dumps(str(tmp_path/"target.json"))+'\nprepared='+json.dumps(str(tmp_path/"prepared/manifest.json"))
        +'\n[model_authorization.roles]\nallowed="REQ05_ALLOWED_KEY"\ndenied="REQ05_DENIED_KEY"\n',encoding="utf-8")
    runner=CliRunner();common=["--store",tmp_path/"store","--config",cfg]
    def cli(args,expected=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==expected,(args,result.output,result.exception)
        assert all(s not in result.output for s in (PRIVATE,ALLOWED_KEY,DENIED_KEY))
    cli(["select","--pack",pack.path,"--env",tmp_path/"environment.json","--policy",tmp_path/"policy.json",*common])
    plan,=Store.read_only(tmp_path/"store").iter_kind("PLAN")
    assert next(x for x in plan.items if x.check_id=="REQ-05").selected
    cli(["run","--plan",plan.id,"--pack",pack.path,*common],1 if mode=="http-cleanup" else 0)
    cli(["judge","--plan",plan.id,"--as-of",T0.isoformat(),*common])
    cli(["report","--plan",plan.id,"--as-of",T0.isoformat(),"--json",tmp_path/"report.json",*common]);cli(["verify",*common])
    store=Store.read_only(tmp_path/"store");e,=store.iter_kind("EVD");res,=store.iter_kind("RES")
    assert e.data_origin.value=="EXAMPLE" and all(getattr(res.verdicts,a).value=="UNKNOWN" for a in ("security","functional","performance"))
    assert not e.observations["model_authorization_scope_complete"].value
    assert not e.observations["model_retirement_scope_complete"].value and not e.observations["final_model_identity_verified"].value
    trials=e.observations["model_use_trials"].value
    if mode=="actual-lab-false":assert not world.sent and not trials and e.execution.status.value=="ABORTED"
    else:
        assert world.sent and world.native_ops.count("seal")== (0 if mode=="engine-replaced" else 1)
        assert len(world.sent)=={"before-shape":2,"engine-replaced":1,"wrong-response-id":2}.get(mode,3)
        assert trials and all(not t["final_model_identity_verified"] for t in trials)
    if mode in {"normal","denied-response","retired","attachment","http-cleanup","seal-failure","after-timeout"}:
        assert any(t["native_request_linked"] for t in trials)
        assert e.observations["configured_model_policy_conflicts"].value
    if mode=="retired":
        assert e.observations["configured_retired_model_uses"].value
        assert all(not v["confirmed_violation"] for v in e.observations["configured_retired_model_uses"].value)
    if mode=="native-unlinked":assert not any(t["native_request_linked"] for t in trials)
    assert bool(e.execution.cleanup_errors) is (mode=="http-cleanup")
    for artifact in e.artifacts:
        raw=(tmp_path/"store"/artifact.path).read_bytes()
        assert all(s.encode() not in raw for s in (PRIVATE,ALLOWED_KEY,DENIED_KEY))
    assert all(s not in canonical_json(e.to_dict()).decode() for s in (PRIVATE,ALLOWED_KEY,DENIED_KEY))
    objects=[x for kind in ("ENV","POL","PLAN","EVD","RES","RPT") for x in store.iter_kind(kind)];assert len(objects)==6
    cli(["evidence","export","--store",tmp_path/"store","--output",tmp_path/"bundle.zip"])
    cli(["evidence","restore","--bundle",tmp_path/"bundle.zip","--destination",tmp_path/"restored"])
    restored=Store.read_only(tmp_path/"restored");assert all(restored.resolve(x.ref()).to_dict()==x.to_dict() for x in objects)
    cli(["judge","--plan",plan.id,"--as-of",T0.isoformat(),"--store",tmp_path/"restored","--config",cfg])
    assert [r.ref() for r in Store.read_only(tmp_path/"restored").iter_kind("RES")]==[res.ref()]
    cli(["verify","--store",tmp_path/"restored","--config",cfg])


def test_pack_keeps_three_branches_two_required_results_and_original_criteria(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/"pack");pack=load_pack(tmp_path/"pack")
    assert receipt["unchanged_definition_bytes"]==76 and not receipt["full_REQ05_complete"] and not receipt["required_subresults_complete"]
    for path in seed.path.rglob("*"):
        if path.is_file() and path.relative_to(seed.path).as_posix() not in {"pack.yaml","checks/REQ-05.yaml"}:
            assert path.read_bytes()==(pack.path/path.relative_to(seed.path)).read_bytes()
    for field in ("question","satisfied_if","fail_criteria","unknown_criteria","normal_control","challenge_steps","legacy"):
        assert getattr(pack.checks["REQ-05"],field)==getattr(seed.checks["REQ-05"],field)
    assert pack.checks["REQ-05"].judge.params=={"pass_when_all":[],"fail_when_any":[]}
    assert len(pack.checks["REQ-05"].legacy.scenario_ids)==3


@pytest.mark.parametrize("damage",["extra","same-principal","bad-grant","duplicate-grant","naive","retired-hash","duplicate-retired"])
def test_explicit_policy_closes_unsupported_values_before_io(tmp_path,group,native_target,damage):
    _,values,_,_=inputs(tmp_path,group,native_target,"normal")
    if damage=="extra":values["producer_verified"]=True
    elif damage=="same-principal":values["denied_principal_sha256"]=values["allowed_principal_sha256"]
    elif damage=="bad-grant":values["selected_model_grants"]=["runtime-auto-approved"]
    elif damage=="duplicate-grant":values["selected_model_grants"]*=2
    elif damage=="naive":values["model_validity_start"]="2026-10-29T00:00:00"
    elif damage=="retired-hash":values["retired_model_revisions"]=["runtime:model:2026-10-29T00:00:00Z"]
    else:
        entry="a"*64+":"+"b"*64+":2026-10-29T00:00:00Z";values["retired_model_revisions"]=[entry,entry]
    with pytest.raises(ValueError,match="MODEL_AUTHORIZATION_POLICY_INVALID"):model.normalize_policy(values)


@pytest.mark.parametrize("damage",["id","path","header","credential","model","stream","max-tokens","message",
    "method","mode","delay","rid","want-body","network","timeout","initial-challenge"])
def test_model_http_profile_is_closed_before_remote_io(tmp_path,group,native_target,damage):
    world,_,_,_=inputs(tmp_path,group,native_target,"normal")
    transport=http.KubernetesHTTP(world.target,group.env,duration=60,runner=world.runner,profile=http.MODEL_AUTHORIZATION_PROFILE)
    slot="challenge" if damage=="initial-challenge" else "before"
    req={"id":"req05-model-"+slot,"method":"POST","mode":"json","url":world.target["engine"]["trusted_url"]+"/v1/chat/completions",
        "headers":{"X-Request-Id":"byul-req05-model-"+"ab"*16+"-"+slot,"Authorization":"Bearer "+ALLOWED_KEY},
        "body":{"model":"fixture-model","messages":[{"role":"user","content":"hello"}],"max_tokens":1,"stream":False}}
    network="kubernetes-probe";timeout=10
    if damage=="id":req["id"]="before"
    elif damage=="path":req["url"]=world.target["engine"]["trusted_url"]+"/tokenize"
    elif damage=="header":req["headers"]["Secret"]=PRIVATE
    elif damage=="credential":req["headers"]["Authorization"]="Bearer private\r\nInjected: value"
    elif damage=="model":req["body"]["model"]=group.env.model.id
    elif damage=="stream":req["body"]["stream"]=True
    elif damage=="max-tokens":req["body"]["max_tokens"]=True
    elif damage=="message":req["body"]["messages"][0]["content"]=PRIVATE
    elif damage=="method":req["method"]="GET"
    elif damage=="mode":req["mode"]="sse"
    elif damage=="delay":req["read_delay_ms"]=1
    elif damage=="rid":req["headers"]["X-Request-Id"]+="-other"
    elif damage=="want-body":req["want_body"]=False
    elif damage=="network":network="other"
    elif damage=="timeout":timeout=121
    with pytest.raises(http.KubernetesBindingError):transport(network,[req],timeout)
    assert not world.sent and not world.native_ops
    assert all(secret not in json.dumps(transport.report()) for secret in (PRIVATE,ALLOWED_KEY,DENIED_KEY))


@pytest.mark.parametrize("profile",[http.PROFILE,http.REQUEST_BINDING_PROFILE,http.VALIDATION_ERROR_PROFILE])
def test_model_profile_does_not_reuse_another_checks_ids(tmp_path,group,native_target,profile):
    world,_,_,_=inputs(tmp_path,group,native_target,"normal")
    transport=http.KubernetesHTTP(world.target,group.env,duration=60,runner=world.runner,profile=profile)
    req={"id":"req05-model-before","method":"POST","mode":"json","url":world.target["engine"]["trusted_url"]+"/v1/chat/completions",
        "headers":{"X-Request-Id":"byul-req05-model-"+"ab"*16+"-before","Authorization":"Bearer "+ALLOWED_KEY},
        "body":{"model":"fixture-model","messages":[{"role":"user","content":"hello"}],"max_tokens":1,"stream":False}}
    with pytest.raises(http.KubernetesBindingError):transport("kubernetes-probe",[req],10)
    assert not world.sent and not world.native_ops
