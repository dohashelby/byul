"""Opt-in kind check of operational attribution and observed agent recovery."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.tetragon import render_manifests
from serving_runtime.enforcement.recovery import RecoveryTracker
from serving_runtime.mvp.observation_feed import ObservationFeed
from tools.tetragon_trial import KindTrial, ROOT, PROBE_PATH, SERVER, CLIENT, atomic_json, now
from tools.tetragon_identities import IdentityCollector
from tools.tetragon_stream import TetragonStream
from tools.tetragon_status import StatusCollector


def check(output: Path, *, restart_agent=False):
    fixture=KindTrial(output,"sigkill_fd_install")
    report={"schema_version":"runtime-tetragon-operations-check/1.0","id":fixture.namespace,
            "context":fixture.context,"namespace":fixture.namespace,"started_at":now(),
            "data_origin":"real","restart_requested":restart_agent,"facts":{}}
    directory=output/fixture.namespace
    directory.mkdir(parents=True,exist_ok=True)
    stream=None
    child=None
    baseline=fixture.document("get","tracingpoliciesnamespaced","-A","-o","json")
    original={(p["metadata"]["namespace"],p["metadata"]["name"]):p["spec"] for p in baseline.get("items",[])}
    try:
        fixture.apply("namespace",{"apiVersion":"v1","kind":"Namespace","metadata":{"name":fixture.namespace,
            "labels":{"serving-runtime.local/purpose":"tetragon-operations-check"}}},create=True)
        fixture.created=True
        for name in ("probe","other"):
            fixture.apply(name,{"apiVersion":"v1","kind":"Pod","metadata":{"name":name,"namespace":fixture.namespace,
                "labels":{"app":"byul-operations-probe"}},"spec":{"restartPolicy":"Always","terminationGracePeriodSeconds":1,
                "automountServiceAccountToken":False,"securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},
                "volumes":[{"name":"file","emptyDir":{}}],"initContainers":[{"name":"fake-file","image":fixture.image,
                    "imagePullPolicy":"IfNotPresent","command":["python","-c",f"from pathlib import Path; Path({PROBE_PATH!r}).write_text('FAKE-ONLY')"],
                    "volumeMounts":[{"name":"file","mountPath":"/run/byul-probe"}]}],"containers":[{"name":"probe","image":fixture.image,
                    "imagePullPolicy":"IfNotPresent","command":["python","-u","-c",SERVER],
                    "volumeMounts":[{"name":"file","mountPath":"/run/byul-probe","readOnly":True}]}]}})
        ready=fixture.kube("wait","pods","--all","--for=condition=Ready","--timeout=60s",timeout=65)
        if ready.returncode:
            raise RuntimeError("operational fixture pods did not become ready")
        intent=json.loads((ROOT/"deploy/lab/intent.lab-v1.1.json").read_text())
        intent["namespace"]=fixture.namespace
        intent["engine"]["labels"]={"app":"byul-operations-probe"}
        intent["protected_paths"]["secret_paths"]=[PROBE_PATH]
        intent["tetragon_kernel_identity"]=True
        intent.pop("tetragon_rule_modes",None)
        intent.pop("tetragon_enforce_method",None)
        policy=render_manifests(parse_intent(intent))["tetragon-tg-secret-01.json"]
        policy["metadata"]["name"]=fixture.policy
        fixture.apply("policy",policy)
        time.sleep(4)
        stream=TetragonStream(fixture.context,fixture.namespace,[fixture.policy],directory/"feed")
        stream.start()
        identities=IdentityCollector(fixture.context,fixture.namespace,"app=byul-operations-probe",directory/"identities.json")
        feed=ObservationFeed(kind="tetragon",path=directory/"feed/tetragon.jsonl",data_dir=directory/"gateway",
            data_origin="real",environment="kind-runtime-lab",run_id=fixture.namespace,
            heartbeat_path=directory/"feed/tetragon.heartbeat.json",identity_path=directory/"identities.json",kube_context=fixture.context)
        collector=StatusCollector(fixture.context,directory/"status.json",directory/"no-trials")
        child_code=(f"import time,threading\ndef read_loop():\n for _ in range(240):\n  with open({PROBE_PATH!r},'rb') as f: f.read(1)\n  time.sleep(.5)\n"
                    "threading.Thread(target=read_loop,daemon=True).start()\ntime.sleep(120)")
        child=subprocess.Popen(["kubectl","--context",fixture.context,"-n",fixture.namespace,"exec","probe","-c","probe","--",
                                "python","-c",child_code],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        history=[]
        tracker=None
        last_ids=0

        def tick():
            nonlocal tracker,last_ids
            stream.tick()
            if time.monotonic()-last_ids>3:
                identities.collect()
                last_ids=time.monotonic()
            fixture.request("/probe")
            fixture.kube("exec","other","-c","probe","--","python","-c",CLIENT,"/probe",timeout=10)
            try: feed.collect()
            except (OSError,ValueError): pass
            sample=collector.collect()
            if tracker is None and sample["state"]=="ok":
                atomic_json(directory/"baseline.json",{"schema_version":"runtime-tetragon-baseline/1.0","context":fixture.context,
                    "policies":[{k:p.get(k) for k in ("node","namespace","name","mode","spec_sha256")} for p in sample["policies"]]})
                tracker=RecoveryTracker(fixture.context,directory/"recovery.json",baseline_path=directory/"baseline.json",
                    identities_path=directory/"identities.json",heartbeat_path=directory/"feed/tetragon.heartbeat.json",
                    observations_path=directory/"gateway/observations.jsonl")
                collector.recovery=tracker
            if tracker is not None and "monitoring" in sample:
                history.append({"at":now(),"monitoring":sample["monitoring"],"agents":sample["agents"]})
            return sample

        deadline=time.monotonic()+100
        sample={}
        while time.monotonic()<deadline:
            sample=tick()
            events=[]
            if (directory/"gateway/observations.jsonl").exists():
                events=[json.loads(l) for l in (directory/"gateway/observations.jsonl").read_text().splitlines()]
            bound=[e for e in events if e.get("identity",{}).get("state")=="confirmed"]
            report["facts"].update(main_process_identified=any(e["identity"].get("ns_pid")==1 for e in bound),
                child_process_identified=any(e["identity"].get("ns_pid",0)>1 for e in bound),
                threaded_child_identified=any(e["identity"].get("ns_pid",0)>1 for e in bound),
                distinct_pods_identified=len({e["identity"]["pod_uid"] for e in bound})==2)
            if (all(report["facts"].values()) and sample.get("monitoring",{}).get("state")=="healthy"):
                break
            time.sleep(1)
        else:
            raise RuntimeError("operational attribution or initial recovery conditions did not verify")
        report["agent_before"]=sample["agents"]
        report["facts"]["initial_monitoring_verified"]=True
        if restart_agent:
            report["restart_requested_at"]=now()
            restarted=fixture.kube("rollout","restart","daemonset/tetragon",namespace="kube-system")
            if restarted.returncode:
                raise RuntimeError("kind agent restart failed")
            observed_gap=False
            deadline=time.monotonic()+150
            while time.monotonic()<deadline:
                sample=tick()
                monitor=sample.get("monitoring",{})
                changed=sample["agents"]!=report["agent_before"]
                if monitor.get("state")!="healthy": observed_gap=True
                if changed and observed_gap and monitor.get("state")=="healthy":
                    report["facts"].update(agent_change_detected=True,gap_detected=True,recovery_verified=True)
                    break
                time.sleep(1)
            else:
                raise RuntimeError("post-restart policies and attributed event recovery did not verify")
        report["agent_after"]=sample["agents"]
        report["monitoring"]=sample.get("monitoring")
        atomic_json(directory/"recovery-samples.json",history)
        report["status"]="passed"
    except Exception as error:
        report.update(status="failed",error=str(error)[:500])
    finally:
        if child is not None:
            child.terminate()
            try: child.wait(timeout=3)
            except subprocess.TimeoutExpired: child.kill();child.wait(timeout=3)
        if stream is not None: stream.close()
        if fixture.created:
            cleaned=fixture.kube("delete","namespace",fixture.namespace,"--wait=true","--timeout=45s",timeout=50)
            report["facts"]["cleanup_ok"]=cleaned.returncode==0
        final=fixture.document("get","tracingpoliciesnamespaced","-A","-o","json")
        actual={(p["metadata"]["namespace"],p["metadata"]["name"]):p["spec"] for p in final.get("items",[])}
        report["facts"]["original_policies_preserved"]=original==actual
        if not report["facts"].get("cleanup_ok") or not report["facts"]["original_policies_preserved"]:
            report["status"]="failed"
        report["finished_at"]=now()
        report["artifact_sha256"]={}
        for path in (directory/"policy.json",directory/"recovery-samples.json",directory/"feed/tetragon.gaps.jsonl",directory/"gateway/observations.jsonl"):
            if path.exists(): report["artifact_sha256"][str(path.relative_to(directory))]=hashlib.sha256(path.read_bytes()).hexdigest()
        atomic_json(output/(fixture.namespace+".operations.json"),report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--allow-isolated-kind-check",action="store_true",required=True)
    parser.add_argument("--restart-agent",action="store_true",help="kind 에이전트 1회 교체; 실제 관측 공백 시험")
    args=parser.parse_args()
    result=check(args.output,restart_agent=args.restart_agent)
    print(json.dumps({k:result.get(k) for k in ("id","status","facts","error")},ensure_ascii=False))
    return 0 if result["status"]=="passed" else 2


if __name__=="__main__": raise SystemExit(main())
