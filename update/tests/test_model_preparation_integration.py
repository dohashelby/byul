"""A-2 helpers through shared requests, real parsers, CLI, Store and replay."""
from copy import deepcopy
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.collectors import kubernetes_lifecycle as collector
from guard.executors import registry
from guard.executors.direct_route import DirectRouteExecutor
from guard.executors.http_common import Ctx, functional_requests, functional_results
from guard.executors.kubernetes_request_binding import KubernetesRequestBindingExecutor
from guard.models import canonical_json, sha256_hex
from guard.runtime.comparison import execution_conditions, model_request_conditions
from guard.store import Store
from tests.builders import baseline_pack, policy, completion_response, T0
from tests.test_model_mapping import declared_mapping
from tests.test_kubernetes_request_binding import World, env, target, VALUES, PRIVATE, judge
from tests.test_kubernetes_lifecycle import (group, target as session_target, World as SessionWorld,
    arguments, case, external, writer_factory)


def full_inputs():
    declaration,mapping=declared_mapping()
    environment_=env().model_copy(update={"model":declaration.model}).seal()
    mapping.update(environment_ref=environment_.ref().model_dump(mode="json"),
        served_model_name="fixture-model",host_snapshot="/srv/EXAMPLE_PRIVATE_HOST_SNAPSHOT")
    topology=target(environment_);topology["model_mapping"]=mapping
    return environment_,topology


class FullWorld(World):
    def __init__(self,topology,*,mount_mode=None,**changes):
        super().__init__(topology,**changes);self.mount_mode=mount_mode
    def runner(self,argv,**kwargs):
        result=super().runner(argv,**kwargs)
        if "get" not in argv:
            if "input_bytes" in kwargs and self.current_label and self.current_label.startswith("tokenize-"):
                rows=json.loads(result["stdout_bytes"])
                for row in rows:
                    row.update(mode="json",method="POST",capture_complete=True,eof=True,utf8_valid=True,
                        body_truncated=False,body_sha256=sha256_hex(row["body"].encode()))
                result["stdout_bytes"]=json.dumps(rows).encode()
            return result
        value=json.loads(result["stdout_bytes"])
        if "namespaces" in argv:
            for item in value["items"]:item["kind"]="Namespace"
        else:
            value["kind"]="Pod"
            if value["metadata"]["name"]==self.target["kubernetes"]["engine"]["pod"]:
                mapping=self.target["model_mapping"];spec=value["spec"]
                spec["containers"][0]["volumeMounts"].append({"name":"model","mountPath":mapping["container_mount"],
                    "readOnly":self.mount_mode!="writable" and not (self.mount_mode=="changed" and self.current_label=="after")})
                spec["volumes"]=[{"name":"model","hostPath":{"path":mapping["host_snapshot"],"type":"Directory"}}]
            if self.mount_mode=="wrong-kind":value["kind"]="ConfigMap"
        result["stdout_bytes"]=json.dumps(value).encode()
        return result


def mapped_draft(tmp_path,*,mode=None,late=None,foreign=None):
    environment_,topology=full_inputs();world=FullWorld(topology,mount_mode=mode,late=late,foreign=foreign)
    path=tmp_path/"target.json";path.write_bytes(canonical_json(topology))
    draft=KubernetesRequestBindingExecutor({"target":str(path)},runner=world.runner,environ={}).run(
        baseline_pack().checks["REQ-11"],environment_,deepcopy(VALUES),VllmAdapter(),tmp_path/"work")
    summary=json.loads(next(a.path for a in draft.artifacts if a.name=="probe_log.jsonl").read_text().splitlines()[-1])
    return draft,summary,world


@pytest.mark.parametrize("response_model,expected",[("fixture-model","pass"),("repository","no_response")])
def test_shared_pair_uses_same_alias_and_preserves_repository(tmp_path,response_model,expected):
    environment_,topology=full_inputs();before=canonical_json(environment_.to_dict())
    topology.update(gateway={"url":"http://gateway:8000"})
    ctx=Ctx(check=baseline_pack().checks["REQ-01"],env=environment_,adapter=VllmAdapter(),target=topology,
        policy_values={},timeout=1,allow_mutating=False,keys={"allowed":None},secrets=[],
        cprobe=None,workdir=tmp_path,started=T0)
    requests,probes=functional_requests(ctx);request,=requests
    assert request["body"]["model"]=="fixture-model"
    response=completion_response(request)
    if response_model=="repository":
        value=json.loads(response["body"]);value["model"]=environment_.model.id;response["body"]=json.dumps(value)
    states,_,normal=functional_results(probes,{request["id"].split("|",1)[1]:response},environment_.model.id)
    assert set(states.values())=={expected} and normal["succeeded"]==(expected=="pass")
    assert canonical_json(environment_.to_dict())==before


@pytest.mark.parametrize("field",["environment_ref","repository_id","revision","inventory_sha256","missing","null"])
def test_explicit_bad_mapping_fails_before_readiness_or_probe(tmp_path,field,monkeypatch):
    environment_,topology=full_inputs()
    if field=="missing":del topology["model_mapping"]["served_model_name"]
    elif field=="null":topology["model_mapping"]=None
    elif field=="environment_ref":topology["model_mapping"][field]["sha256"]="0"*64
    elif field=="repository_id":topology["model_mapping"][field]="other/repository"
    elif field=="revision":topology["model_mapping"][field]="b"*40
    else:topology["model_mapping"][field]="b"*64
    path=tmp_path/"target.json";path.write_bytes(canonical_json(topology))
    def forbidden(*a,**k):raise AssertionError("no transport for bad mapping")
    monkeypatch.setattr("guard.executors.http_common.wait_engine",forbidden)
    with pytest.raises(ValueError,match="MODEL_MAPPING_INVALID"):
        DirectRouteExecutor({"target":str(path),"roles":{"allowed":"KEY"}},container_probe=forbidden,environ={}).run(
            baseline_pack().checks["REQ-01"],environment_,{"allowed_callers":["allowed"]},VllmAdapter(),tmp_path/"work")


def test_absent_mapping_comparison_unchanged_explicit_mapping_hashed_without_paths():
    _,topology=full_inputs();mapping=topology.pop("model_mapping")
    original=execution_conditions(topology,{},[]);assert "model_mapping_sha256" not in original
    topology["model_mapping"]=mapping;mapped=execution_conditions(topology,{},[])
    assert mapped.pop("model_mapping_sha256")==sha256_hex(canonical_json(model_request_conditions(mapping))) and mapped==original
    first=execution_conditions(topology,{},[])
    assert "EXAMPLE_PRIVATE_HOST_SNAPSHOT" not in json.dumps(first)
    other=deepcopy(topology);other["model_mapping"]["environment_ref"]["id"]="ENV-OTHER"
    other["model_mapping"]["host_snapshot"]="/other/snapshot"
    assert execution_conditions(other,{},[])==first
    other["model_mapping"]["served_model_name"]="other-alias"
    assert execution_conditions(other,{},[])!=first


@pytest.mark.parametrize("mode,collected,read_only",[(None,True,True),("writable",True,False),("changed",False,None),("wrong-kind",False,None)])
def test_mount_is_bound_projection_in_existing_attachment(tmp_path,mode,collected,read_only):
    draft,summary,world=mapped_draft(tmp_path,mode=mode);result=summary["model_mount"]
    assert result["collected"] is collected
    assert result["worker_access_verified"] is result["producer_authenticated"] is result["whole_run_coverage_verified"] is False
    if collected:assert result["observations"]=={"declared_read_only":read_only,"host_snapshot_matches":True}
    else:assert result["diagnostic_codes"]==["MODEL_MOUNT_SOURCE_UNVERIFIED"]
    assert judge(draft).verdicts["security"].value=="UNKNOWN"
    blob=json.dumps(draft.model_dump(mode="json"))+"".join(a.path.read_text() for a in draft.artifacts)
    assert "EXAMPLE_PRIVATE_HOST_SNAPSHOT" not in blob and PRIVATE not in blob
    assert all(r["body"]["model"]=="fixture-model" for batch in world.packets for r in batch)


@pytest.mark.parametrize("late",["exception","restart","cleanup","truncated"])
def test_mount_gap_preserves_prior_violation_and_artifacts(tmp_path,late):
    draft,summary,_=mapped_draft(tmp_path,late=late,foreign="json-distinct_body-A")
    assert draft.observations["security_violations"].value and judge(draft).verdicts["security"].value=="FAIL"
    assert not summary["model_mount"]["collected"] and len(draft.artifacts)>=2
    if late=="cleanup":assert draft.cleanup_errors==["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]


@pytest.mark.parametrize("attachment_failure",[False,True])
def test_full_mapping_cli_fail_refs_artifact_preservation_and_restore(tmp_path,monkeypatch,attachment_failure):
    environment_,topology=full_inputs();world=FullWorld(topology,foreign="json-distinct_body-A")
    pack=baseline_pack();pol=policy(pack.ref(),scope={"include":["REQ-11"]},values={"REQ-11":VALUES});paths={}
    for name,value in (("env",environment_.to_dict()),("policy",pol.to_dict()),("target",topology)):
        paths[name]=tmp_path/(name+".json");paths[name].write_bytes(canonical_json(value))
    config=tmp_path/"guard.toml"
    config.write_text('[executors]\nrequest_binding="kubernetes"\n[adapter]\nname="vllm"\n[request_binding]\ntarget='+json.dumps(paths["target"].as_posix())+'\n')
    monkeypatch.setitem(registry.EXECUTORS,"request_binding:kubernetes",lambda raw:KubernetesRequestBindingExecutor(raw["request_binding"],runner=world.runner,environ={}))
    def forbidden(*a,**k):raise AssertionError("no actual cluster/network")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    cli_runner=CliRunner();common=["--config",str(config),"--store",str(tmp_path/"store")]
    def cli(args):
        result=cli_runner.invoke(app,args);assert result.exit_code==0,(result.output,repr(result.exception));return result.output
    plan=cli(["select","--pack",str(pack.path),"--env",str(paths["env"]),"--policy",str(paths["policy"]),*common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if attachment_failure:
            original=Store.put_artifact;calls=[]
            def partial(self,*a,**k):
                calls.append(1)
                if len(calls)==2:raise OSError(PRIVATE)
                return original(self,*a,**k)
            capture.setattr(Store,"put_artifact",partial)
        cli(["run","--plan",plan,"--pack",str(pack.path),*common])
    for command in ("judge","report"):
        cli([command,"--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),*common,
             *(["--json",str(tmp_path/"report.json")] if command=="report" else [])])
    store=Store(tmp_path/"store");evd,=store.find_latest("EVD",lambda e:True);res,=store.find_latest("RES",lambda e:True);rpt,=store.find_latest("RPT",lambda e:True)
    assert res.verdicts.security.value=="FAIL" and store.resolve(evd.environment).ref()==environment_.ref()
    assert evd.execution.status.value==("ABORTED" if attachment_failure else "COMPLETED")
    if attachment_failure:assert len(evd.artifacts)==1
    assert evd.ref() in rpt.inputs_manifest
    for artifact in evd.artifacts:
        text=(store.root/artifact.path).read_text()
        assert "EXAMPLE_PRIVATE_HOST_SNAPSHOT" not in text and PRIVATE not in text
    cli(["verify",*common]);archive=tmp_path/"evidence.zip";restored=tmp_path/"restored"
    cli(["evidence","export","--store",str(store.root),"--output",str(archive)])
    cli(["evidence","restore","--bundle",str(archive),"--destination",str(restored)])
    assert Store(restored).resolve(evd.ref()).to_dict()==evd.to_dict()
    cli(["judge","--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),"--config",str(config),"--store",str(restored)])
    assert Store(restored).find_latest("RES",lambda e:True)[0].ref()==res.ref()


@pytest.mark.parametrize("late",[False,True])
def test_capture_cli_consumes_snapshot_pair_retains_session_on_late_gap(group,session_target,monkeypatch,late):
    declaration,mapping=declared_mapping();group.env=group.env.model_copy(update={"model":declaration.model}).seal()
    mapping["environment_ref"]=group.env.ref().model_dump(mode="json");session_target["environment_ref"]=mapping["environment_ref"]
    (group.root/"env.json").write_bytes(canonical_json(group.env.to_dict()))
    (group.root/"kube-target.json").write_bytes(canonical_json(session_target))
    mapping_path=group.root/"mapping.json";mapping_path.write_bytes(canonical_json(mapping))
    world=SessionWorld(group,session_target,mode="late-final-inventory" if late else None);original=world.runner
    def runner(argv,**kwargs):
        result=original(argv,**kwargs)
        if "get" in argv and "pod" in argv:
            value=json.loads(result["stdout_bytes"]);spec=value["spec"]
            spec["containers"][0]["volumeMounts"]=[{"name":"model","mountPath":mapping["container_mount"],"readOnly":True}]
            spec["volumes"]=[{"name":"model","hostPath":{"path":mapping["host_snapshot"],"type":"Directory"}}]
            result["stdout_bytes"]=json.dumps(value).encode()
        return result
    real=collector.collect;monkeypatch.setattr(collector,"collect",lambda *a,**k:real(*a,**k,runner=runner))
    output=group.root/"session-model.json"
    result=CliRunner().invoke(app,arguments(group,output)+["--model-mapping",str(mapping_path),"--execute"])
    assert result.exit_code==0,(result.output,repr(result.exception))
    value=json.loads(output.read_bytes())
    assert len(value["recordings"])==2 and value["model_mount"]["collected"] is not late
    assert not value["model_mount"]["worker_access_verified"] and value["data_origin"]=="EXAMPLE"
    assert PRIVATE not in output.read_text() and mapping["host_snapshot"] not in output.read_text()
