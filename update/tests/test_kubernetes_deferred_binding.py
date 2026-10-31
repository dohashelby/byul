"""Exact candidate identity arrives after baseline stops; no future IDs or mutation."""
from copy import deepcopy
import json
import socket
import subprocess

import pytest
from typer.testing import CliRunner

from guard import application
from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import registry
from guard.executors.kubernetes_request_binding import KubernetesRequestBindingExecutor
from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_transition import KubernetesTransition
from guard.store import Store
from tests.builders import baseline_pack, policy, T0
from tests.test_kubernetes_transition import Pair, Clock
from tests.test_kubernetes_request_binding import env, target, VALUES


def late_inputs(tmp_path,pair):
    cfg=pair.configs(tmp_path)
    path=tmp_path/"late-candidate.json"
    cfg["candidate"]["request_binding"]["target"]=str(path)
    return cfg,path


@pytest.mark.parametrize("damage",[None,"missing","malformed","foreign-env","same-pod","wrong-context"])
def test_waits_for_real_candidate_binding_after_stop_and_bounds_failure(tmp_path,damage):
    pair=Pair();clock=Clock();cfg,path=late_inputs(tmp_path,pair)
    target_=deepcopy(pair.worlds[1].target)
    if damage=="foreign-env":target_["environment_ref"]["sha256"]="0"*64
    if damage=="same-pod":target_["kubernetes"]["engine"]["pod_uid"]=pair.worlds[0].target["kubernetes"]["engine"]["pod_uid"]
    if damage=="wrong-context":target_["kubernetes"]["context"]="other-context"
    def sleep(delay):
        clock.sleep(delay)
        if damage!="missing" and not path.exists():
            path.write_bytes(b"{" if damage=="malformed" else canonical_json(target_))
    gate=KubernetesTransition(pair.envs,cfg,runner=pair.runner,clock=clock,sleep=sleep,
        timeout=.04,poll_interval=.01,candidate_binding="after_baseline")
    assert set(gate.transports)=={"baseline"} and not path.exists()
    gate.prepare_baseline()
    clock.value+=100  # Baseline request duration is outside the transition wait budget.
    if damage is None:
        gate.candidate_ready()
        assert gate.report()["events"][-1]=="CANDIDATE_READY_OBSERVED"
        assert gate.report()["binding_sha256"]["candidate"]
    else:
        with pytest.raises(ValueError,match="KUBERNETES_TRANSITION_"):gate.candidate_ready()
        assert gate.report()["events"][-1]=="RECOVERY_REQUIRED" and pair.candidate_polls==0
    assert gate.report()["events"].index("BASELINE_API_TERMINATION_OBSERVED") < gate.report()["events"].index("WAITING_CANDIDATE_BINDING")
    assert not gate.report()["gpu_release_verified"] and all("get" in call for call in pair.calls)


def test_explicit_late_mode_rejects_stale_file_before_any_query(tmp_path):
    pair=Pair();cfg=pair.configs(tmp_path)
    with pytest.raises(ValueError,match="MUST_BE_FRESH"):
        KubernetesTransition(pair.envs,cfg,runner=pair.runner,candidate_binding="after_baseline")
    assert not pair.calls
    with pytest.raises(ValueError,match="MODE_INVALID"):
        KubernetesTransition(pair.envs,cfg,runner=pair.runner,candidate_binding="automatic-discovery")
    assert not pair.calls


def test_selected_target_hash_is_consumed_by_real_registry_before_any_io(tmp_path,monkeypatch):
    environment_=env();target_=target(environment_);path=tmp_path/"target.json"
    expected=sha256_hex(canonical_json(target_))
    path.write_bytes(canonical_json(target_))
    factory=registry.resolve_executor("request_binding","kubernetes")
    executor=factory({"request_binding":{"target":str(path)},"_kubernetes_target_sha256":expected})
    assert executor._expected_target_sha256==expected
    target_["model_mapping"]["api_model"]="replaced-api-name";path.write_bytes(canonical_json(target_))
    def forbidden(*a,**k):raise AssertionError("changed target must fail before process/network")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    with pytest.raises(ValueError,match="KUBERNETES_REQUEST_BINDING_CONFIGURATION_INVALID"):
        executor.run(baseline_pack().checks["REQ-11"],environment_,deepcopy(VALUES),VllmAdapter(),tmp_path/"work")


@pytest.mark.parametrize("mode",["normal","missing","swapped"])
def test_existing_update_cli_delayed_identity_or_abort_retains_baseline(tmp_path,monkeypatch,mode):
    pair=Pair();clock=Clock();cfg,path=late_inputs(tmp_path,pair)
    def sleep(delay):
        clock.sleep(delay)
        if mode!="missing" and not path.exists():path.write_bytes(canonical_json(pair.worlds[1].target))
    original=application.KubernetesTransition
    def gate(*args,**kwargs):
        result=original(*args,**kwargs,runner=pair.runner,clock=clock,sleep=sleep)
        ready=result.candidate_ready
        def selected():
            ready()
            if mode=="swapped":
                changed=deepcopy(pair.worlds[1].target);changed["model_mapping"]["api_model"]="swapped"
                path.write_bytes(canonical_json(changed))
        result.candidate_ready=selected
        return result
    monkeypatch.setattr(application,"KubernetesTransition",gate)
    monkeypatch.setitem(registry.EXECUTORS,"request_binding:kubernetes",lambda raw:KubernetesRequestBindingExecutor(
        raw["request_binding"],runner=pair.runner,expected_target_sha256=raw.get("_kubernetes_target_sha256")))
    def forbidden(*a,**k):raise AssertionError("no actual infrastructure")
    monkeypatch.setattr(subprocess,"Popen",forbidden);monkeypatch.setattr(socket,"socket",forbidden)
    pack=baseline_pack();pol=policy(pack.ref(),scope={"include":["REQ-11"]},values={"REQ-11":VALUES})
    env_paths=[]
    for index,environment_ in enumerate(pair.envs):
        p=tmp_path/(str(index)+"-env.json");p.write_bytes(canonical_json(environment_.to_dict()));env_paths.append(p)
    policy_path=tmp_path/"policy.json";policy_path.write_bytes(canonical_json(pol.to_dict()))
    config=tmp_path/"guard.toml"
    config.write_text('[executors]\nrequest_binding="kubernetes"\n[adapter]\nname="vllm"\n[update]\nmode="external"\nkubernetes_candidate_binding="after_baseline"\ntransition_timeout=0.04\ntransition_poll_interval=0.01\n'+
        ''.join('[sides.'+side+'.request_binding]\ntarget='+json.dumps(cfg[side]["request_binding"]["target"].replace('\\','/'))+'\n' for side in cfg))
    store_path=tmp_path/"store"
    result=CliRunner().invoke(app,["update","--pack",str(pack.path),"--baseline",str(env_paths[0]),"--candidate",str(env_paths[1]),
        "--policy",str(policy_path),"--config",str(config),"--store",str(store_path),"--output",str(tmp_path/"updates"),"--as-of",T0.isoformat()])
    assert result.exit_code==(0 if mode=="normal" else 1),(result.output,repr(result.exception))
    store=Store(store_path);evds=list(store.find_latest("EVD",lambda e:True));rpt,=store.find_latest("RPT",lambda e:True)
    base=next(e for e in evds if e.side.value=="baseline");candidate=next(e for e in evds if e.side.value=="candidate")
    assert base.execution.status.value=="COMPLETED" and base.artifacts and base.ref() in rpt.inputs_manifest
    assert candidate.execution.status.value==("COMPLETED" if mode=="normal" else "ABORTED")
    assert candidate.ref() in rpt.inputs_manifest and len(list(store.iter_kind("RES")))==2
    if mode!="normal":assert not pair.worlds[1].packets
