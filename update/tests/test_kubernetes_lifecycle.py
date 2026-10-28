"""EXAMPLE kubectl file/process protocol, binding gaps and lossless CLI output."""
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.collectors import kubernetes_lifecycle as collector
from guard.executors import lifecycle_session as session, lifecycle_capture as single, owned_capture
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError, supervised_source
from guard.models import DataOrigin
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_binding import validate_capture_target
from tests.test_lifecycle_capture import case, external, IMAGE
from tests.test_lifecycle_sensor import writer_factory, CONTEXT, CANARY
from tests.test_lifecycle_session import group, GroupDocker
from tests.test_kubernetes_sampling import inputs as sampling_inputs

PRIVATE="EXAMPLE_KUBE_CAPTURE_PRIVATE_73CB"


@pytest.fixture
def target(group):
    role=deepcopy(sampling_inputs()["kubernetes"]["engine"])
    role["image"]=IMAGE
    role["image_id"]="sha256:"+"c"*64  # API-reported ID, deliberately not the spec digest/config ID.
    value={"schema":"guard-kubernetes-lifecycle-target/1","lab":True,"run_id":"guard-fixture",
        "environment_ref":group.env.ref().model_dump(mode="json"),
        "kubernetes":{"context":"fixture-context","engine":role}}
    (group.root/"kube-target.json").write_text(json.dumps(value),encoding="utf-8")
    return value


class World:
    def __init__(self,group,target,mode=None):
        self.group,self.target,self.mode=group,target,mode
        self.docker=GroupDocker(group)
        self.calls=[];self.execs=[];self.bad_owner=False;self.files=0;self.inventories=0
    def runner(self,argv,**kwargs):
        assert argv[0]=="kubectl" and argv[1]=="--context=fixture-context"
        assert argv[2].startswith("--request-timeout=") and 0<kwargs["timeout"]<=5
        assert PRIVATE not in " ".join(argv) and kwargs["max_stderr_bytes"]==4096
        self.calls.append(argv)
        r=self.target["kubernetes"]["engine"]
        pipe={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}
        if "exec" in argv:
            assert "-i" not in argv and "-t" not in argv and "--container=engine" in argv
            command=argv[argv.index("--")+1:]
            assert command[:5]==["python3","-I","-S","-B","-c"]
            wrapper=command[5];tree=ast.parse(wrapper)
            compile_call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call)
                and isinstance(n.func,ast.Name) and n.func.id=="compile")
            source=ast.literal_eval(compile_call.args[0])
            assert wrapper==supervised_source(source) and 0<float(command[6])<=5
            program={owned_capture._FILE_READER:"file",single.CONTEXT_READER:"context",session.INVENTORY_READER:"inventory"}[source]
            self.execs.append(program)
            if program=="file":self.files+=1
            if program=="inventory":self.inventories+=1
            if self.mode=="transport" and len(self.execs)==1:
                return {"returncode":1,"stdout_bytes":PRIVATE.encode(),"stderr_bytes":PRIVATE.encode(),"capture":pipe}
            value=self.docker.exec_bounded("engine",["python3","-c",source,*command[7:]],
                timeout=kwargs["timeout"],max_bytes=kwargs["max_stdout_bytes"])
            raw=value.stdout.encode()
            if self.mode=="partial-pipe" and program=="file" and self.files==1:
                pipe.update(complete=False,truncated=True)
            if (self.mode=="late-file-owner" and program=="file" and self.files==1
                    or self.mode=="late-final-inventory" and program=="inventory" and self.inventories==2):
                self.bad_owner=True
            return {"returncode":0,"stdout_bytes":raw,"stderr_bytes":PRIVATE.encode(),"capture":pipe}
        assert "get" in argv and not any(v in argv for v in ("apply","patch","delete","create"))
        if "namespace" in argv:
            value={"kind":"Namespace","metadata":{"name":r["namespace"],"uid":r["namespace_uid"],
                "annotations":{"private":PRIVATE}}}
            if self.mode=="initial-owner":value["metadata"]["uid"]="00000000-0000-0000-0000-000000000099"
        else:
            assert "pod" in argv and r["pod"] in argv
            value={"kind":"Pod","metadata":{"name":r["pod"],"namespace":r["namespace"],"uid":r["pod_uid"],
                "labels":{"guard.run":self.target["run_id"]},"annotations":{"private":PRIVATE}},
                "spec":{"nodeName":r["node"],"containers":[{"name":r["container"],"image":r["image"],
                    "env":[{"name":"PRIVATE","value":PRIVATE}]}]},
                "status":{"phase":"Running","podIP":r["pod_ip"],"conditions":[{"type":"Ready","status":"True"}],
                    "containerStatuses":[{"name":r["container"],"containerID":r["container_id"],"imageID":r["image_id"],
                        "restartCount":r["restart_count"],"ready":True,"state":{"running":{"startedAt":r["started_at"]}}}]}}
            if self.mode=="not-ready":
                value["status"]["conditions"]=[{"type":"Ready","status":"False"}]
                value["status"]["containerStatuses"][0]["ready"]=False
            if self.bad_owner:value["metadata"]["uid"]="10000000-0000-0000-0000-000000000099"
        return {"returncode":0,"stdout_bytes":json.dumps(value).encode(),"stderr_bytes":PRIVATE.encode(),"capture":pipe}


def capture(group,target,mode=None,env=None):
    world=World(group,target,mode)
    value=collector.collect(env or group.env,target,group.prepared,runner=world.runner)
    return value,world


def test_reuses_actual_file_protocol_and_session_parser_without_fabricating_docker_identity(group,target):
    value,world=capture(group,target)
    assert value["live_census_stable"] and value["recorded_files_covered"] and len(value["recordings"])==2
    assert value["transport"]["profile"]=="kubernetes-owned-lifecycle-read-v1"
    assert value["data_origin"]=="EXAMPLE" and value["security"]=="UNKNOWN" and value["whole_run_coverage_verified"] is False
    for row in value["recordings"]:
        observed=row["capture"]
        assert observed["records"]==single.parse_recording(group.raw_by_pid[row["pid"]],group.prepared)[0]
        assert observed["process_context_verified"] and observed["image_digest_verified"] is False
        owner=observed["provenance"]["owner"]
        assert owner["transport"]=="kubernetes" and owner["reported_image_id"]=="sha256:"+"c"*64
        assert "image_id" not in owner and "started_at" not in owner
        image=observed["provenance"]["native_image_reference"]
        assert image["api_binding_verified_before"] and image["api_binding_verified_after"]
        assert image["node_runtime_image_verified"] is image["manifest_ancestry_verified"] is False
    domain,=value["request_index"]["domains"].values()
    assert domain["assignments"][0]["event"]==[0,3]
    assert PRIVATE not in json.dumps(value) and CANARY not in json.dumps(value)
    assert world.execs.count("inventory")==2 and world.execs.count("file")==4 and world.execs.count("context")==4


def test_readonly_capture_can_retain_failure_metadata_from_a_running_not_ready_pod(group,target):
    value,_=capture(group,target,"not-ready")
    assert len(value["recordings"])==2 and all(r["capture"] for r in value["recordings"])
    assert value["security"]=="UNKNOWN" and value["producer_authenticated"] is False


@pytest.mark.parametrize("mode",["late-file-owner","late-final-inventory","partial-pipe","transport","initial-owner"])
def test_generation_and_transport_gaps_preserve_independent_safe_records(group,target,mode):
    value,world=capture(group,target,mode)
    rows=[r for r in value["recordings"] if r["capture"] is not None]
    expected={"late-file-owner":1,"late-final-inventory":2,"partial-pipe":1,"transport":0,"initial-owner":0}[mode]
    assert len(rows)==expected
    for row in rows:
        assert row["capture"]["records"]==single.parse_recording(group.raw_by_pid[row["pid"]],group.prepared)[0]
    assert not value["recorded_files_covered"] and value["transport"]["collection_gap"]
    assert value["whole_run_coverage_verified"] is False and value["security"]=="UNKNOWN"
    if mode=="late-file-owner":
        assert rows[0]["capture"]["process_context_verified"] is False
        assert rows[0]["capture"]["provenance"]["owner_binding_verified"] is False
    if mode=="initial-owner":assert world.execs==[]
    assert PRIVATE not in json.dumps(value) and CANARY not in json.dumps(value)


@pytest.mark.parametrize("origin",[DataOrigin.DECLARED,DataOrigin.MEASURED])
def test_injected_runner_forces_example_even_when_environment_claims_measured(group,target,origin):
    env=group.env.model_copy(update={"data_origin":origin}).seal()
    target["environment_ref"]=env.ref().model_dump(mode="json")
    value,_=capture(group,target,env=env)
    assert value["data_origin"]=="EXAMPLE" and value["record_authenticity_verified"] is False


@pytest.mark.parametrize("change",[
    lambda t:t.update(schema="other"),lambda t:t.update(lab=False),lambda t:t.update(secret=PRIVATE),
    lambda t:t["environment_ref"].update(sha256="0"*64),lambda t:t.update(run_id="-flag"),
    lambda t:t["kubernetes"].update(context="fixture context"),lambda t:t["kubernetes"].update(probe={}),
    lambda t:t["kubernetes"]["engine"].update(pod_uid="other"),lambda t:t["kubernetes"]["engine"].update(container_id="docker://"+"a"*64),
    lambda t:t["kubernetes"]["engine"].update(image="example/image:latest"),lambda t:t["kubernetes"]["engine"].update(restart_count=True),
    lambda t:t["kubernetes"]["engine"].update(started_at="2026-10-27"),lambda t:t["kubernetes"]["engine"].update(pod_ip="::1"),
])
def test_explicit_target_rejects_wrong_reference_identity_or_extra_fields_before_runner(group,target,change):
    change(target)
    with pytest.raises(ValueError):
        KubernetesLifecycleReader(target,group.env,group.prepared,runner=lambda *a,**k:(_ for _ in ()).throw(AssertionError("runner touched")))


@pytest.mark.parametrize("mode",["namespace_uid","pod_uid","container_id","image","image_id","restart","started","label","host","deleted","container"])
def test_api_objects_cannot_substitute_another_generation_before_exec(group,target,mode):
    world=World(group,target);original=world.runner
    def runner(argv,**kwargs):
        result=original(argv,**kwargs)
        if "exec" in argv:return result
        value=json.loads(result["stdout_bytes"])
        if value["kind"]=="Namespace" and mode=="namespace_uid":value["metadata"]["uid"]="00000000-0000-0000-0000-000000000099"
        if value["kind"]=="Pod":
            state=value["status"]["containerStatuses"][0]
            if mode=="pod_uid":value["metadata"]["uid"]="10000000-0000-0000-0000-000000000099"
            if mode=="container_id":state["containerID"]="containerd://"+"f"*64
            if mode=="image":value["spec"]["containers"][0]["image"]="different@sha256:"+"f"*64
            if mode=="image_id":state["imageID"]="sha256:"+"f"*64
            if mode=="restart":state["restartCount"]+=1
            if mode=="started":state["state"]["running"]["startedAt"]="2026-10-27T01:00:00Z"
            if mode=="label":value["metadata"]["labels"]["guard.run"]="another-owner"
            if mode=="host":value["spec"]["hostPID"]=True
            if mode=="deleted":value["metadata"]["deletionTimestamp"]="now"
            if mode=="container":value["status"]["containerStatuses"]=[]
        result["stdout_bytes"]=json.dumps(value).encode();return result
    value=collector.collect(group.env,target,group.prepared,runner=runner)
    assert world.execs==[] and value["recordings"]==[] and not value["live_census_stable"]
    assert PRIVATE not in json.dumps(value)


@pytest.mark.parametrize("command",[
    ["python3","-c","print('arbitrary')","a"*32],
    ["python3","-c",session.INVENTORY_READER,"0"*32],
    ["python3","-c",single.CONTEXT_READER,"-1"],
    ["python3","-c",owned_capture._FILE_READER,"/proc/1/environ","262144"],
    ["python3","-c",owned_capture._FILE_READER,"/guard-lifecycle/data/../../secret","262144"],
])
def test_only_fixed_read_programs_and_session_paths_can_reach_kubectl(group,target,command):
    world=World(group,target)
    client=KubernetesLifecycleReader(target,group.env,group.prepared,runner=world.runner)
    cap=524288 if command[2]==owned_capture._FILE_READER else 4096 if command[2]==single.CONTEXT_READER else 65536
    with pytest.raises(ValueError):client.exec_bounded("engine",command,timeout=5,max_bytes=cap)
    assert world.calls==[] and not hasattr(client,"inspect_json") and not hasattr(client,"image_info")


@pytest.mark.parametrize("budget",[True,0,65535,262145])
def test_file_budget_is_rejected_before_any_kubernetes_request(group,target,budget):
    world=World(group,target);client=KubernetesLifecycleReader(target,group.env,group.prepared,runner=world.runner)
    path=f"/guard-lifecycle/data/{group.prepared['session']}/pid-42-99.jsonl"
    with pytest.raises(ValueError):
        client.read_file(client,target,container="engine",image=IMAGE,path=path,max_bytes=budget)
    assert world.calls==[]


def arguments(group,output):
    return ["capture-kubernetes-lifecycle-session","--env",str(group.root/"env.json"),
        "--target",str(group.root/"kube-target.json"),"--prepared",str(group.manifest_path),"--output",str(output)]


def test_product_default_never_constructs_a_capture_client_or_output(group,target,monkeypatch):
    monkeypatch.setattr(collector,"collect",lambda *a,**k:(_ for _ in ()).throw(AssertionError("client created")))
    output=group.root/"kube-session.json"
    result=CliRunner().invoke(app,arguments(group,output))
    assert result.exit_code==0,result.output
    assert json.loads(result.output)["capture_executed"] is False and not output.exists()


def test_product_execute_saves_exact_safe_bytes_and_does_not_overwrite(group,target,monkeypatch):
    world=World(group,target);real=collector.collect
    monkeypatch.setattr(collector,"collect",lambda *a,**k:real(*a,**k,runner=world.runner))
    output=group.root/"kube-session.json"
    result=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert result.exit_code==0,result.output
    raw=output.read_bytes();summary=json.loads(result.output)
    assert summary["sha256"]==hashlib.sha256(raw).hexdigest() and summary["bytes"]==len(raw)
    assert summary["artifact_saved"] and len(json.loads(raw)["recordings"])==2
    count=len(world.calls)
    second=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert second.exit_code==2 and output.read_bytes()==raw and len(world.calls)==count
    assert PRIVATE.encode() not in raw and CANARY.encode() not in raw


@pytest.mark.parametrize("failure",["open","write","late-existing"])
def test_kubernetes_output_failure_preserves_collected_metadata_and_existing_files(group,target,monkeypatch,failure):
    output=group.root/"kube-session.json";real=collector.collect;opened=Path.open
    def collected(*args,**kwargs):
        if failure=="late-existing":output.write_bytes(b"EXAMPLE-existing-artifact")
        return real(*args,**kwargs,runner=World(group,target).runner)
    monkeypatch.setattr(collector,"collect",collected)
    class Broken:
        def __enter__(self):return self
        def __exit__(self,*a):return False
        def write(self,data):raise OSError(PRIVATE)
    def opening(path,*args,**kwargs):
        if path==output and args and args[0]=="x":
            if failure=="open":raise OSError(PRIVATE)
            if failure=="write":return Broken()
        return opened(path,*args,**kwargs)
    monkeypatch.setattr(Path,"open",opening)
    result=CliRunner().invoke(app,arguments(group,output)+["--execute"])
    assert result.exit_code==1,result.output
    value=json.loads(result.output)
    assert value["capture_executed"] and not value["artifact_saved"] and len(value["capture"]["recordings"])==2
    assert all(r["capture"]["records"]==single.parse_recording(group.raw_by_pid[r["pid"]],group.prepared)[0]
        for r in value["capture"]["recordings"])
    assert PRIVATE not in result.output and CANARY not in result.output
    if failure=="late-existing":assert output.read_bytes()==b"EXAMPLE-existing-artifact"


@pytest.mark.parametrize("option",[["--max-duration","0"],["--max-duration","nan"],["--max-duration","301"],["--kubectl-binary"," "]])
def test_bad_transport_options_fail_even_in_default_validation_mode(group,target,monkeypatch,option):
    monkeypatch.setattr(collector,"collect",lambda *a,**k:(_ for _ in ()).throw(AssertionError("client created")))
    result=CliRunner().invoke(app,arguments(group,group.root/"kube-session.json")+option)
    assert result.exit_code==2 and result.output.strip()=="KUBERNETES_LIFECYCLE_CAPTURE_FAILED"


def test_remote_supervisor_preserves_argv_and_exits_only_its_own_process():
    source="import json,sys;print(json.dumps(sys.argv[1:]))"
    result=capture_pipes([sys.executable,"-I","-S","-B","-c",supervised_source(source),"5","EXAMPLE"],
        timeout=10,env_extra={},max_stdout_bytes=4096,max_stderr_bytes=4096)
    assert result["returncode"]==0 and json.loads(result["stdout_bytes"])==["EXAMPLE"]
    stalled=capture_pipes([sys.executable,"-I","-S","-B","-c",supervised_source("import time;time.sleep(10)"),"0.15"],
        timeout=3,env_extra={},max_stdout_bytes=4096,max_stderr_bytes=4096)
    assert stalled["returncode"]==124 and stalled["capture"]["complete"] and stalled["capture"]["timed_out"] is False
