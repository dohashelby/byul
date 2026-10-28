"""EXAMPLE harness for target/input origin ceilings; no engine, API or cluster."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from guard.collectors import lifecycle, native_weight_files, kubernetes_lifecycle
from guard.executors import owned_capture, weight_file_capture, kubernetes_lifecycle_capture
from guard.models import DataOrigin, canonical_json
from tests.test_lifecycle_capture import case, external, Docker, IMAGE
from tests.test_lifecycle_sensor import writer_factory
from tests.test_lifecycle_session import group, GroupDocker
from tests.test_native_weight_files import inputs, Reader, packet, ROOT, sha
from tests.test_kubernetes_lifecycle import target, World


def environment(value,grade):
    env=value.model_copy(deep=True);env.data_origin=DataOrigin(grade)
    return env.seal()


@pytest.mark.parametrize("grade",["EXAMPLE","DECLARED","MEASURED"])
@pytest.mark.parametrize("lab",[True,False,None])
@pytest.mark.parametrize("mode",["recording","session"])
def test_docker_stub_origin_cannot_be_promoted_by_environment_grade(group,grade,lab,mode):
    env=environment(group.env,grade)
    selected={**group.target,"environment_ref":env.ref().model_dump(mode="json")}
    if lab is not None:selected["lab"]=lab
    if mode=="recording":
        value=lifecycle.collect(Docker(group),env,selected,group.source,group.prepared)
        assert value["records"] and value["process_context_verified"]
    else:
        value=lifecycle.collect_session(GroupDocker(group),env,selected,group.source["container"],IMAGE,group.prepared)
        assert value["recordings"] and value["recorded_files_covered"]
    assert value["data_origin"]==("EXAMPLE" if lab is True or grade=="EXAMPLE" else "DECLARED")
    coverage=value["summary"]["whole_run_coverage_verified"] if mode=="recording" else value["whole_run_coverage_verified"]
    assert value["security"]=="UNKNOWN" and coverage is False


def weight_inputs(inputs,grade,lab,transport="docker",kube_target=None):
    env=environment(inputs.value["env"],grade);ref=env.ref().model_dump(mode="json")
    inputs.paths[0].write_bytes(canonical_json(env.to_dict()))
    selected=deepcopy(kube_target) if kube_target else json.loads(inputs.paths[1].read_bytes())
    selected.update(lab=lab,environment_ref=ref)
    inputs.paths[1].write_bytes(canonical_json(selected))
    native=json.loads(inputs.paths[5].read_bytes());native.update(data_origin=grade,environment_ref=ref)
    raw=canonical_json(native);inputs.paths[5].write_bytes(raw)
    context=json.loads(inputs.paths[3].read_bytes());context.update(transport=transport,environment_ref=ref,native_session_sha256=sha(raw))
    inputs.paths[3].write_bytes(canonical_json(context))
    return native_weight_files.load_inputs(*inputs.paths)


@pytest.mark.parametrize("grade",["EXAMPLE","DECLARED","MEASURED"])
@pytest.mark.parametrize("lab",[True,False])
def test_weight_file_origin_respects_known_docker_stub_even_without_injected_reader_flag(inputs,monkeypatch,grade,lab):
    value=weight_inputs(inputs,grade,lab)
    monkeypatch.setattr(owned_capture,"create_capture_client",lambda *args:object())
    monkeypatch.setattr(weight_file_capture,"DockerWeightReader",lambda *a,**kw:SimpleNamespace(read=Reader(inputs.owner).read))
    result=native_weight_files.collect(value,max_file_bytes=1024)
    assert len(result["files"])==2 and all(r["capture_verified"] for r in result["files"])
    assert result["data_origin"]==("EXAMPLE" if lab or grade=="EXAMPLE" else "DECLARED")
    assert result["security"]=="UNKNOWN" and not result["actual_consumption_verified"]


@pytest.mark.parametrize("grade",["EXAMPLE","DECLARED","MEASURED"])
def test_kubernetes_lab_declaration_keeps_its_non_stub_meaning(group,target,monkeypatch,grade):
    env=environment(group.env,grade);selected={**target,"environment_ref":env.ref().model_dump(mode="json")}
    world=World(group,selected)
    monkeypatch.setattr(kubernetes_lifecycle_capture,"capture_pipes",world.runner)
    result=kubernetes_lifecycle.collect(env,selected,group.prepared)
    assert selected["lab"] is True and result["recorded_files_covered"] and result["recordings"]
    assert result["data_origin"]==("EXAMPLE" if grade=="EXAMPLE" else "DECLARED")
    assert result["security"]=="UNKNOWN" and not result["whole_run_coverage_verified"]


@pytest.mark.parametrize("grade",["EXAMPLE","DECLARED","MEASURED"])
def test_weight_kubernetes_lab_declaration_does_not_mislabel_owned_source_as_stub(inputs,group,target,monkeypatch,grade):
    value=weight_inputs(inputs,grade,True,"kubernetes",target)
    world=World(group,value["target"])
    def runner(argv,**kwargs):
        if "exec" not in argv:return world.runner(argv,**kwargs)
        command=argv[argv.index("--")+1:]
        assert command[:5]==["python3","-I","-S","-B","-c"]
        assert command[5]==kubernetes_lifecycle_capture.supervised_source(weight_file_capture.HASH_READER)
        return {"returncode":0,"stdout_bytes":json.dumps(packet(ROOT+"/"+command[-2])).encode(),"stderr_bytes":b"",
            "capture":{"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False}}
    monkeypatch.setattr(kubernetes_lifecycle_capture,"capture_pipes",runner)
    result=native_weight_files.collect(value,max_file_bytes=1024)
    assert len(result["files"])==2 and all(r["capture"] and r["owner_binding_verified"] for r in result["files"])
    # The fixture's native files came from a Docker owner. Never silently join that identity to a Pod.
    assert all(not r["native_owner_match"] and not r["capture_verified"] for r in result["files"])
    assert result["data_origin"]==("EXAMPLE" if grade=="EXAMPLE" else "DECLARED")
    assert result["collection_gap"] and result["security"]=="UNKNOWN" and not result["actual_consumption_verified"]
