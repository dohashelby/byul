"""Read-only Tetragon node state -> atomic snapshot for the runtime dashboard."""
from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import time

from tools.tetragon_capability import classify_capabilities
from tools.tetragon_trial import atomic_json, run
from serving_runtime.enforcement.tetragon import comparable_spec
from serving_runtime.enforcement.recovery import RecoveryTracker

SCHEMA="runtime-tetragon-state/1.0"


def method_from_spec(policy):
    probes=policy.get("spec",{}).get("kprobes",[])
    if len(probes)!=1: return None
    probe=probes[0]
    actions={a.get("action") for s in probe.get("selectors",[]) for a in s.get("matchActions",[])}
    if not actions & {"Override","Sigkill"}: return "monitor"
    if probe.get("call")=="fd_install" and "Sigkill" in actions: return "sigkill_fd_install"
    if probe.get("call")=="security_file_permission" and "Override" in actions:
        return "override_lsm" if "Sigkill" in actions else "override_only"
    if probe.get("call")=="sys_openat" and {"Override","Sigkill"}<=actions: return "override_syscall"
    return None


class StatusCollector:
    def __init__(self,context,output,evidence_dir,*,runner=run,recovery=None):
        self.context=context
        self.output=Path(output)
        self.evidence=Path(evidence_dir)
        self.run=runner
        self.recovery=recovery
        self.capabilities=[]
        self.capabilities_at=0.0
        self.spec_fingerprint=None
        self.spec_changed_at=0.0

    def kube(self,*args,namespace="kube-system"):
        return self.run(["kubectl","--context",self.context,"-n",namespace,*args])

    def node_read(self,pod,*args):
        result=self.kube("exec",pod,"-c","tetragon","--",*args)
        return result.stdout if result.returncode==0 else None

    def collect(self):
        result={"schema_version":SCHEMA,"generated_at":datetime.now(timezone.utc).isoformat(),"context":self.context,
                "state":"ok","error":None,"policies":[],"capabilities":[],"transitions":[],"verifications":[]}
        result["agents"]=[]
        result["operation_checks"]=[]
        try:
            pods=self.kube("get","pods","-l","app.kubernetes.io/name=tetragon","-o","json")
            if pods.returncode:
                raise ValueError("Tetragon pods could not be queried")
            pod_docs=json.loads(pods.stdout).get("items",[])
            if not pod_docs:
                raise ValueError("Tetragon pods are missing")
            specs=self.kube("get","tracingpoliciesnamespaced","-A","-o","json")
            if specs.returncode:
                raise ValueError("Tetragon policy specifications could not be queried")
            spec_docs=json.loads(specs.stdout).get("items",[])
            fingerprint=sorted((p.get("metadata",{}).get("namespace",""),p.get("metadata",{}).get("name",""),
                                p.get("metadata",{}).get("resourceVersion","")) for p in spec_docs)
            fingerprint += sorted(("agent", p.get("metadata",{}).get("uid",""),
                str(next((s.get("restartCount") for s in p.get("status",{}).get("containerStatuses",[])
                          if s.get("name")=="tetragon"),None))) for p in pod_docs)
            if fingerprint != self.spec_fingerprint:
                self.spec_fingerprint=fingerprint
                self.spec_changed_at=time.monotonic()
            if time.monotonic()-self.spec_changed_at<3:
                raise ValueError("정책 변경 감지: 로드 안정화 대기 중")
            methods={(p.get("metadata",{}).get("namespace"),p.get("metadata",{}).get("name")):method_from_spec(p)
                     for p in spec_docs}
            hashes={(p.get("metadata",{}).get("namespace"),p.get("metadata",{}).get("name")):
                hashlib.sha256(json.dumps(comparable_spec(p.get("spec",{})),sort_keys=True,separators=(",",":")).encode()).hexdigest()
                for p in spec_docs}
            refresh=not self.capabilities or time.monotonic()-self.capabilities_at>60
            capabilities=[]
            for pod in pod_docs:
                name=pod["metadata"]["name"]
                node=pod.get("spec",{}).get("nodeName")
                agent=next((c for c in pod.get("status",{}).get("containerStatuses",[]) if c.get("name")=="tetragon"),{})
                if agent.get("ready") is not True:
                    raise ValueError("Tetragon 에이전트 준비 상태 확인 중")
                ended=agent.get("lastState",{}).get("terminated",{})
                message=ended.get("message","")
                summary=("정책 조회 경로 내부 오류 (TotalMemlock/ListTracingPolicies)"
                         if "TotalMemlock" in message and "ListTracingPolicies" in message else None)
                result["agents"].append({"node":node,"restart_count":agent.get("restartCount"),
                    "pod_uid":pod["metadata"].get("uid"),
                    "last_exit_at":ended.get("finishedAt"),"last_exit_code":ended.get("exitCode"),
                    "last_exit_reason":ended.get("reason"),"last_exit_summary":summary})
                raw=self.node_read(name,"tetra","tp","list","-o","json")
                if raw is None:
                    raise ValueError("Tetragon node policy query failed")
                for policy in json.loads(raw).get("policies",[]):
                    mode=policy.get("mode","").removeprefix("TP_MODE_").lower()
                    result["policies"].append({"node":node,"name":policy.get("name"),"namespace":policy.get("namespace"),
                        "id":str(policy.get("id")),"state":policy.get("state","").removeprefix("TP_STATE_").lower(),
                        "mode":mode if mode in {"monitor","enforce","monitor_only"} else "unknown",
                        "method":methods.get((policy.get("namespace"),policy.get("name"))),
                        "spec_sha256":hashes.get((policy.get("namespace"),policy.get("name"))),
                        "load_error":str(policy.get("error", ""))[:500] or None})
                if refresh:
                    kernel=(self.node_read(name,"uname","-r") or "").strip() or None
                    config=self.node_read(name,"cat",f"/boot/config-{kernel}") if kernel else None
                    if config is None: config=self.node_read(name,"zcat","/proc/config.gz")
                    capability=classify_capabilities(kernel=kernel,config=config,
                        lsm=self.node_read(name,"cat","/sys/kernel/security/lsm"),
                        injection_list=self.node_read(name,"cat","/sys/kernel/debug/error_injection/list"),
                        probe=self.node_read(name,"tetra","probe"))
                    capabilities.append({"node":node,"checked_at":result["generated_at"],**capability})
            if refresh:
                self.capabilities=capabilities
                self.capabilities_at=time.monotonic()
            result["capabilities"]=self.capabilities
        except (ValueError,KeyError,TypeError,AttributeError,RecursionError) as error:
            result.update(state="error",error=str(error)[:300])
            # Preserve the previous per-node sample only with an explicit stale state.
            result["capabilities"]=self.capabilities
        try:
            for path in sorted(self.evidence.rglob("transitions.jsonl"),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
                for line in path.read_text().splitlines()[-100:]:
                    try:
                        record=json.loads(line)
                        if record.get("context")==self.context:
                            result["transitions"].append(record)
                    except (ValueError,AttributeError):
                        continue
            result["transitions"] = sorted(result["transitions"],key=lambda item:item.get("at", ""))[-100:]
            for path in sorted(self.evidence.glob("*.verification.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
                raw=path.read_bytes()
                record=json.loads(raw)
                if record.get("context")==self.context and record.get("schema_version")=="runtime-tetragon-verification/1.0":
                    record["source_report_sha256"]=hashlib.sha256(raw).hexdigest()
                    result["verifications"].append(record)
            result["verifications"].sort(key=lambda item: str(item.get("finished_at", "")), reverse=True)
            for path in sorted(self.evidence.glob("*.operations.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:10]:
                record=json.loads(path.read_text())
                if record.get("context")==self.context and record.get("schema_version")=="runtime-tetragon-operations-check/1.0":
                    result["operation_checks"].append(record)
        except (OSError,ValueError,TypeError,AttributeError,RecursionError):
            result.update(state="error",error="Tetragon trial evidence could not be read")
        if self.recovery is not None:
            try:
                result["monitoring"] = self.recovery.update(result)
            except (OSError,ValueError,TypeError,AttributeError,KeyError):
                result["monitoring"] = {"state":"error","reasons":["recovery_evidence_unavailable"]}
        else:
            result["monitoring"] = {"state":"not_configured","reasons":["recovery_check_not_configured"]}
        atomic_json(self.output,result)
        return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--evidence-dir",type=Path,required=True)
    parser.add_argument("--watch",action="store_true")
    parser.add_argument("--interval",type=float,default=5)
    parser.add_argument("--capture-baseline",type=Path,help="현재 정책 spec·모드를 기대값 파일로 저장; 원격 변경 없음")
    parser.add_argument("--expected-policies",type=Path)
    parser.add_argument("--identities",type=Path)
    parser.add_argument("--feed-heartbeat",type=Path)
    parser.add_argument("--observations",type=Path)
    args=parser.parse_args()
    if args.interval<1: parser.error("interval must be at least one second")
    checks=[args.expected_policies,args.identities,args.feed_heartbeat,args.observations]
    if any(checks) and not all(checks):
        parser.error("recovery checks require expected-policies, identities, feed-heartbeat and observations")
    if args.capture_baseline and args.watch:
        parser.error("baseline capture is a one-shot read")
    recovery=(RecoveryTracker(args.context,args.output.with_name(args.output.name+".recovery.json"),
        baseline_path=args.expected_policies,identities_path=args.identities,
        heartbeat_path=args.feed_heartbeat,observations_path=args.observations) if all(checks) else None)
    collector=StatusCollector(args.context,args.output,args.evidence_dir,recovery=recovery)
    while True:
        result=collector.collect()
        if not args.watch and result.get("error")=="정책 변경 감지: 로드 안정화 대기 중":
            time.sleep(3.1)
            result=collector.collect()
        print(json.dumps({"state":result["state"],"policies":len(result["policies"]),"verifications":len(result["verifications"])}),flush=True)
        if args.capture_baseline:
            if result["state"]!="ok" or not result["policies"]:
                return 1
            atomic_json(args.capture_baseline,{"schema_version":"runtime-tetragon-baseline/1.0",
                "context":args.context,"captured_at":result["generated_at"],
                "policies":[{k:p.get(k) for k in ("node","namespace","name","mode","spec_sha256")} for p in result["policies"]]})
        if not args.watch: break
        time.sleep(args.interval)
    return 0 if result["state"]=="ok" else 1

if __name__=="__main__": raise SystemExit(main())
