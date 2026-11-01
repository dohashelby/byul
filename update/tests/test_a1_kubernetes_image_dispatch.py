"""Existing registry/CLI consumes A-2's immutable API-only SUP04 implementation."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors.kubernetes_container_config import KubernetesContainerConfigExecutor
from guard.executors.reuse import preview_inputs
from guard.models import canonical_json, sha256_hex
from guard.runtime import kubernetes_http as kube
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_model_preparation_integration import full_inputs, FullWorld

PRIVATE="EXAMPLE_A1_API_DISPATCH_PRIVATE_829a"


class World(FullWorld):
    def __init__(self,target,mode=None):
        super().__init__(target)
        self.round=0
        self.mode=mode

    def runner(self,argv,**kwargs):
        assert "exec" not in argv and "input_bytes" not in kwargs
        if "namespaces" in argv:self.round+=1
        result=super().runner(argv,**kwargs)
        value=json.loads(result["stdout_bytes"])
        if "namespaces" not in argv:
            value["metadata"]["annotations"]={"private":PRIVATE}
            value["spec"]["containers"][0].update(env=[{"name":"PRIVATE","value":PRIVATE}],args=[PRIVATE])
            if self.round==2 and value["metadata"]["name"]==self.target["kubernetes"]["engine"]["pod"]:
                if self.mode=="replacement":value["metadata"]["uid"]="other"
                elif self.mode=="restart":value["status"]["containerStatuses"][0]["restartCount"]+=1
                elif self.mode=="not-ready":value["status"]["containerStatuses"][0]["ready"]=False
                elif self.mode in {"incomplete","cleanup"}:
                    result["capture"]["complete"]=False
                    if self.mode=="cleanup":result["capture"].update(pipes_closed=False,read_errors=["child_reap"])
                elif self.mode=="exception":raise OSError(PRIVATE)
        result["stdout_bytes"]=json.dumps(value).encode()
        return result


def inputs(tmp_path,mode=None,approved=None):
    env,target=full_inputs()
    path=tmp_path/"target.json"
    path.write_bytes(canonical_json(target))
    world=World(target,mode)
    cfg={"target":str(path)}
    values={"approved_image_digests":[target["engine"]["image_digest"]] if approved is None else approved}
    return env,target,world,cfg,values


@pytest.mark.parametrize("approved",[None,[],["other.invalid/image@sha256:"+"f"*64]])
def test_api_identity_never_establishes_image_approval_or_sends_inference(tmp_path,approved):
    env,target,world,cfg,values=inputs(tmp_path,approved=approved)
    target["lab"]=False
    Path(cfg["target"]).write_bytes(canonical_json(target))
    draft=KubernetesContainerConfigExecutor(cfg,runner=world.runner).run(
        baseline_pack().checks["SUP-04"],env,values,VllmAdapter(),tmp_path/"work")
    assert draft.data_origin.value=="EXAMPLE" and draft.status.value=="COMPLETED"
    assert draft.observations["running_image_identity_verified"].value is False
    assert not draft.observations["running_image_approved"].collected
    assert not draft.observations["running_image_unapproved"].collected
    assert not draft.normal_control.attempted
    summary=json.loads(draft.artifacts[0].path.read_text(encoding="utf-8"))
    assert summary["kubernetes_image"]["api_identity_stable"]
    assert summary["collection_errors"]==["RUNNING_IMAGE_RUNTIME_RELATION_UNAVAILABLE"]
    assert summary["dispatch"]=={"connected":True,"scope":"api-metadata-only",
        "runtime_image_approval_connected":False,"whole_run_coverage_verified":False}
    reads=summary["kubernetes"]["snapshot_reads"]
    assert len(reads)==2 and all(r["collected"] and r["started_at"]<=r["finished_at"] for r in reads)
    assert all(r["source_file_sha256"]==sha256_hex(Path(kube.__file__).read_bytes()) for r in reads)
    assert PRIVATE not in json.dumps(summary) and "EXAMPLE_PRIVATE_HOST_SNAPSHOT" not in json.dumps(summary)
    assert all("get" in command and "exec" not in command for command in world.calls)


@pytest.mark.parametrize("mode",["replacement","restart","not-ready","incomplete","cleanup","exception"])
def test_later_snapshot_gap_keeps_first_identity_and_fixed_diagnostics(tmp_path,mode):
    env,_,world,cfg,values=inputs(tmp_path,mode)
    draft=KubernetesContainerConfigExecutor(cfg,runner=world.runner).run(
        baseline_pack().checks["SUP-04"],env,values,VllmAdapter(),tmp_path/"work")
    summary=json.loads(draft.artifacts[0].path.read_text(encoding="utf-8"))
    analysis=summary["kubernetes_image"]
    assert analysis["identity_before"] and analysis["identity_after"] is None
    assert analysis["diagnostic_codes"]==["RUNNING_IMAGE_SOURCE_CHANGED"]
    assert summary["kubernetes"]["snapshot_reads"][0]["collected"]
    assert not summary["kubernetes"]["snapshot_reads"][1]["collected"]
    assert summary["kubernetes"]["collection_gap"] and PRIVATE not in json.dumps(summary)
    if mode=="cleanup":assert draft.cleanup_errors==["KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"]


@pytest.mark.parametrize("damage",["profile","policy","target-pin","extra-config"])
def test_invalid_profile_or_binding_rejected_before_api(tmp_path,damage):
    env,_,_,cfg,values=inputs(tmp_path)
    check=baseline_pack().checks["SUP-04"]
    if damage=="profile":check=baseline_pack().checks["PLT-01"]
    elif damage=="policy":values={"approved_image_digests":None}
    elif damage=="extra-config":cfg["allow_mutating_routes"]=True
    def forbidden(*args,**kwargs):pytest.fail("must reject before API")
    executor=KubernetesContainerConfigExecutor(cfg,runner=forbidden,
        expected_target_sha256="0"*64 if damage=="target-pin" else None)
    with pytest.raises(ValueError):executor.run(check,env,values,VllmAdapter(),tmp_path/"work")


def test_static_reuse_cannot_confirm_runtime_identity(tmp_path,monkeypatch):
    env,target,_,cfg,values=inputs(tmp_path)
    target["lab"]=False
    Path(cfg["target"]).write_bytes(canonical_json(target))
    def forbidden(*args,**kwargs):pytest.fail("preview must not read API")
    monkeypatch.setattr(kube,"capture_pipes",forbidden)
    raw={"executors":{"container_config":"kubernetes"},"container_config":cfg,"adapter":{"name":"vllm"}}
    assert preview_inputs(baseline_pack().checks["SUP-04"],env,values,raw) is None


@pytest.mark.parametrize("mode",[None,"replacement","attachment","missing-policy"])
def test_real_registry_cli_exact_refs_restore_and_no_fake_executor(tmp_path,monkeypatch,mode):
    env,target,world,cfg,values=inputs(tmp_path,"replacement" if mode=="replacement" else None)
    pack=baseline_pack()
    pol=policy(pack.ref(),scope={"include":["SUP-04"]},values={} if mode=="missing-policy" else {"SUP-04":values})
    paths={}
    for name,value in (("env",env.to_dict()),("policy",pol.to_dict())):
        paths[name]=tmp_path/(name+".json");paths[name].write_bytes(canonical_json(value))
    config=tmp_path/"guard.toml"
    config.write_text('[executors]\ncontainer_config="kubernetes"\n[adapter]\nname="vllm"\n[container_config]\ntarget='+json.dumps(cfg["target"])+"\n",encoding="utf-8")
    monkeypatch.setattr(kube,"capture_pipes",world.runner)
    def forbidden(*args,**kwargs):raise AssertionError("no Docker, subprocess or network")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    runner=CliRunner();common=["--config",str(config),"--store",str(tmp_path/"store")]
    def cli(args):
        result=runner.invoke(app,args);assert result.exit_code==0,(args,result.output,repr(result.exception));return result.output
    plan=cli(["select","--pack",str(pack.path),"--env",str(paths["env"]),"--policy",str(paths["policy"]),*common]).strip().splitlines()[-1]
    with monkeypatch.context() as capture:
        if mode=="attachment":
            def unavailable(*a,**k):raise OSError(PRIVATE)
            capture.setattr(Store,"put_artifact",unavailable)
        cli(["run","--plan",plan,"--pack",str(pack.path),*common])
    cli(["judge","--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),*common])
    cli(["report","--plan",plan,"--pack",str(pack.path),"--json",str(tmp_path/"report.json"),"--as-of",T0.isoformat(),*common])
    cli(["verify",*common])
    store=Store.read_only(tmp_path/"store")
    evd,=store.find_latest("EVD",lambda e:True);res,=store.find_latest("RES",lambda r:True)
    assert res.verdicts.security.value=="UNKNOWN"
    if mode=="missing-policy":assert evd.execution.executor is None
    else:assert evd.execution.executor.name=="container_config"
    assert evd.data_origin.value=="EXAMPLE" and PRIVATE not in canonical_json(evd.to_dict()).decode()
    if mode=="attachment":assert evd.execution.status.value=="ABORTED"
    if mode=="missing-policy":assert not world.calls and evd.execution.status.value=="NOT_RUN"
    else:assert all("exec" not in command for command in world.calls)
    bundle=tmp_path/"bundle.zip";restored_path=tmp_path/"restored"
    cli(["evidence","export","--store",str(tmp_path/"store"),"--output",str(bundle)])
    cli(["evidence","restore","--bundle",str(bundle),"--destination",str(restored_path)])
    restored=Store.read_only(restored_path)
    assert restored.resolve(evd.ref()).to_dict()==evd.to_dict()
    cli(["judge","--plan",plan,"--pack",str(pack.path),"--as-of",T0.isoformat(),"--store",str(restored_path),"--config",str(config)])
    assert restored.find_latest("RES",lambda r:True)[0].ref()==res.ref()
