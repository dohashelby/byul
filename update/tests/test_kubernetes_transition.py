"""Read-only API gates and the existing update CLI; no workload or GPU changes."""
from copy import deepcopy
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard import application
from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_request_binding import KubernetesRequestBindingExecutor
from guard.models import canonical_json
from guard.runtime.kubernetes_http import KubernetesBindingError
from guard.runtime.kubernetes_transition import KubernetesTransition
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_kubernetes_request_binding import World, env, target, VALUES
from tools.j02_run_config import build_config, dumps_toml, j02_checks


class Clock:
    def __init__(self):self.value=0
    def __call__(self):return self.value
    def sleep(self,delay):self.value+=delay


class Pair:
    def __init__(self,*,stop=True,pending=False,replace=False):
        base=env().model_copy(update={"id":"ENV-KUBE-BASE"}).seal()
        candidate=env().model_copy(update={"id":"ENV-KUBE-CANDIDATE"}).seal()
        targets=[target(base),target(candidate)]
        targets[1]["kubernetes"]["engine"].update(pod="candidate-pod",pod_uid="00000000-0000-0000-0000-000000000099",
            container_id="containerd://"+"e"*64,pod_ip="127.0.0.3")
        targets[1]["engine"]["trusted_url"]="http://127.0.0.3:8000"
        self.envs=[base,candidate];self.worlds=[World(t) for t in targets]
        self.stop=stop;self.pending=pending;self.replace=replace;self.candidate_polls=0;self.calls=[]

    def runner(self,argv,**kwargs):
        self.calls.append(argv)
        if "namespace" in argv:
            expected=self.worlds[0].target["kubernetes"]["engine"]
            return {"returncode":0,"stdout_bytes":json.dumps({"metadata":{"name":expected["namespace"],"uid":expected["namespace_uid"]}}).encode(),"stderr_bytes":b"","capture":{"complete":True}}
        candidate="candidate-pod" in argv
        if "input_bytes" in kwargs:
            candidate="127.0.0.3" in json.loads(kwargs["input_bytes"])["requests"][0]["url"]
        world=self.worlds[int(candidate)]
        result=world.runner(argv,**kwargs)
        if "get" in argv and "pod" in argv:
            value=json.loads(result["stdout_bytes"])
            if "--ignore-not-found" in argv and self.stop:
                value["status"]["phase"]="Succeeded"
                state=value["status"]["containerStatuses"][0]
                started=state["state"]["running"]["startedAt"]
                state["state"]={"terminated":{"startedAt":started,"finishedAt":"2026-10-26T00:01:00Z","exitCode":0}}
                state["ready"]=False
                if self.replace:value["metadata"]["uid"]="00000000-0000-0000-0000-000000000088"
            if candidate:
                self.candidate_polls+=1
                if self.pending and self.candidate_polls==1:
                    value["status"]["conditions"]=[{"type":"Ready","status":"False"}]
                    value["status"]["containerStatuses"][0]["ready"]=False
            result["stdout_bytes"]=json.dumps(value).encode()
        return result

    def configs(self,tmp_path):
        rows={}
        for side,world in zip(("baseline","candidate"),self.worlds):
            path=tmp_path/(side+".json");path.write_text(json.dumps(world.target),encoding="utf-8")
            rows[side]={"executors":{"request_binding":"kubernetes"},"adapter":{"name":"vllm"},"request_binding":{"target":str(path)}}
        return rows


def test_prepare_stop_wait_candidate_ready_is_read_only_and_not_gpu_release(tmp_path):
    pair=Pair(pending=True);clock=Clock()
    gate=KubernetesTransition(pair.envs,pair.configs(tmp_path),runner=pair.runner,timeout=1,poll_interval=.01,clock=clock,sleep=clock.sleep)
    gate.prepare_baseline();gate.candidate_ready();report=gate.report()
    assert report["events"]==["BASELINE_READY_OBSERVED","WAITING_BASELINE_API_TERMINATION","BASELINE_API_TERMINATION_OBSERVED","WAITING_CANDIDATE_READY","CANDIDATE_READY_OBSERVED"]
    assert not report["gpu_release_verified"] and not report["workloads_mutated"] and not report["rollback_performed"]
    assert all("get" in call and not any(op in call for op in ("apply","delete","patch","exec")) for call in pair.calls)


@pytest.mark.parametrize("mode",["still-running","replaced"])
def test_failed_stop_or_replacement_requires_operator_recovery(tmp_path,mode):
    pair=Pair(stop=mode!="still-running",replace=mode=="replaced");clock=Clock()
    gate=KubernetesTransition(pair.envs,pair.configs(tmp_path),runner=pair.runner,timeout=.03,poll_interval=.01,clock=clock,sleep=clock.sleep)
    gate.prepare_baseline()
    with pytest.raises(ValueError,match="KUBERNETES_TRANSITION_"):gate.candidate_ready()
    assert gate.report()["events"][-1]=="RECOVERY_REQUIRED" and pair.candidate_polls==0


@pytest.mark.parametrize("stop",[True,False])
def test_update_cli_sides_evidence_report_and_failed_candidate_preserve_baseline(tmp_path,monkeypatch,stop):
    _update_cli(tmp_path,monkeypatch,stop,mixed=False)


@pytest.mark.parametrize("stop",[True,False])
def test_full_j02_config_reaches_update_evidence_and_report(tmp_path,monkeypatch,stop):
    _update_cli(tmp_path,monkeypatch,stop,mixed=True)


def _update_cli(tmp_path,monkeypatch,stop,*,mixed):
    pair=Pair(stop=stop);clock=Clock();configs=pair.configs(tmp_path)
    original=application.KubernetesTransition
    monkeypatch.setattr(application,"KubernetesTransition",lambda envs,sides,**kwargs:original(envs,sides,**kwargs,runner=pair.runner,clock=clock,sleep=clock.sleep))
    monkeypatch.setitem(registry.EXECUTORS,"request_binding:kubernetes",lambda raw:KubernetesRequestBindingExecutor(raw["request_binding"],runner=pair.runner))
    def forbidden(*a,**k):raise AssertionError("no actual process/network")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    pack=baseline_pack();pol=policy(pack.ref(),scope={"include":["REQ-11"]},values={"REQ-11":VALUES})
    env_paths=[]
    for index,environment_ in enumerate(pair.envs):
        path=tmp_path/(str(index)+"-env.json");path.write_bytes(canonical_json(environment_.to_dict()));env_paths.append(path)
    policy_path=tmp_path/"policy.json";policy_path.write_bytes(canonical_json(pol.to_dict()))
    config=tmp_path/"guard.toml"
    implementations={"request_binding":"kubernetes"}
    if mixed:
        implementations.update({pack.checks[cid].executor:"kubernetes" for cid in j02_checks()
                                if pack.checks[cid].executor is not None})
    generated=build_config({'executors':implementations,
        'shared':{'update':{'mode':'external','transition_timeout':0.03,'transition_poll_interval':0.01}}},
        {side:row['request_binding']['target'] for side,row in configs.items()})
    config.write_text(dumps_toml(generated),encoding="utf-8")
    store_path=tmp_path/"store";output=tmp_path/"updates"
    result=CliRunner().invoke(app,["update","--pack",str(pack.path),"--baseline",str(env_paths[0]),"--candidate",str(env_paths[1]),
        "--policy",str(policy_path),"--config",str(config),"--store",str(store_path),"--output",str(output),"--as-of",T0.isoformat()])
    assert result.exit_code==(0 if stop else 1),(result.output,repr(result.exception))
    store=Store(store_path);evidence=list(store.find_latest("EVD",lambda e:True));report,=store.find_latest("RPT",lambda e:True)
    assert len(evidence)==2 and len(list(store.iter_kind("RES")))==2 and not report.official
    base=next(e for e in evidence if e.side.value=="baseline");candidate=next(e for e in evidence if e.side.value=="candidate")
    assert base.execution.status.value=="COMPLETED" and base.artifacts
    assert candidate.execution.status.value==("COMPLETED" if stop else "ABORTED")
    if not stop:assert not pair.worlds[1].packets
    assert base.ref() in report.inputs_manifest and candidate.ref() in report.inputs_manifest


@pytest.mark.parametrize('damage',['other-target','foreign-env','missing-target','different-http-scope'])
def test_mixed_transition_checks_every_http_target_before_queries(tmp_path,damage):
    pair=Pair();configs=pair.configs(tmp_path)
    for side,cfg in configs.items():
        cfg['executors'].update(input_validation='kubernetes',container_config='kubernetes')
        cfg['input_validation']=dict(cfg['request_binding'])
    other=deepcopy(pair.worlds[0].target)
    if damage=='other-target':other['model_mapping']['api_model']='other-model-alias'
    if damage=='foreign-env':other['environment_ref']['sha256']='0'*64
    if damage=='different-http-scope':configs['candidate']['executors'].pop('input_validation')
    else:
        path=tmp_path/'input-validation.json'
        if damage!='missing-target':path.write_bytes(canonical_json(other))
        configs['baseline']['input_validation']['target']=str(path)
    expected=KubernetesBindingError if damage=='foreign-env' else FileNotFoundError if damage=='missing-target' else ValueError
    with pytest.raises(expected):
        KubernetesTransition(pair.envs,configs,runner=pair.runner)
    assert not pair.calls


def test_mixed_transition_accepts_matching_targets_in_separate_files(tmp_path):
    pair=Pair();clock=Clock();configs=pair.configs(tmp_path)
    for side,cfg in configs.items():
        cfg['executors'].update(input_validation='kubernetes',container_config='kubernetes')
        path=tmp_path/(side+'-input.json')
        path.write_bytes(canonical_json(pair.worlds[side=='candidate'].target))
        cfg['input_validation']={'target':str(path)}
    gate=KubernetesTransition(pair.envs,configs,runner=pair.runner,clock=clock,sleep=clock.sleep)
    gate.prepare_baseline();gate.candidate_ready()
    assert gate.report()['events'][-1]=='CANDIDATE_READY_OBSERVED'
    assert all('get' in call and 'exec' not in call for call in pair.calls)
    assert not gate.report()['gpu_release_verified']


def test_transition_target_pin_does_not_replace_other_collectors_targets():
    raw={'_kubernetes_target_sha256':'b'*64,
         '_kubernetes_target_sha256_by_executor':{'request_binding':'a'*64,'input_validation':'a'*64}}
    for name in ('request_binding','input_validation'):
        assert registry.build_executor(name,'kubernetes',raw)._expected_target_sha256=='a'*64
    assert registry.build_executor('container_config','kubernetes',raw)._expected_target_sha256 is None
    assert raw['_kubernetes_target_sha256']=='b'*64


def test_prepare_update_static_kube_path_does_not_require_docker_or_claim_actual_cluster(tmp_path,monkeypatch):
    pair=Pair();configs=pair.configs(tmp_path);pack=baseline_pack()
    pol=policy(pack.ref(),scope={"include":["REQ-11"]},values={"REQ-11":VALUES})
    monkeypatch.setattr(application,"Docker",lambda *a:(_ for _ in ()).throw(AssertionError("no Docker readiness")))
    config={"executors":{"request_binding":"kubernetes"},"adapter":{"name":"vllm"},"update":{"mode":"external"},"sides":configs}
    result=application.update_readiness(pack,pair.envs,pol,config)
    assert all(row["check"]!="docker" for row in result["checks"])
    assert any(row["check"]=="kubernetes-static-binding" and row["ok"] for row in result["checks"])
    assert not result["ready"]  # Same image is a CPU transition contract, not a two-image validation.
    assert any(row["check"]=="image-pair" and not row["ok"] for row in result["checks"])
