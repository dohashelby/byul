"""Opt-in isolated trials; read denial and main-process death are distinct results."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import re
import subprocess
import time
from uuid import uuid4

from serving_runtime.enforcement.intent import parse_intent
from serving_runtime.enforcement.tetragon import render_manifests, comparable_spec
from serving_runtime.enforcement.verification import KERNEL_IDENTITY_DATA, match_probe_events, reassess_record
from tools.tetragon_mode import switch_mode

ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = "/run/byul-probe/secret.txt"
METHODS = ("override_lsm", "override_syscall", "sigkill_fd_install")
SERVER = r'''
import errno,json,os
from http.server import BaseHTTPRequestHandler,HTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        answer={"ready":True,"pid":os.getpid()}
        if self.path=="/probe":
            try:
                fd=os.open("/run/byul-probe/secret.txt",os.O_RDONLY)
                os.read(fd,1)
                os.close(fd)
                answer={"read":"ok","pid":os.getpid()}
            except OSError as error:
                answer={"read":"denied","errno":error.errno,"pid":os.getpid()}
        raw=json.dumps(answer).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(raw)
HTTPServer(("127.0.0.1",8099),Handler).serve_forever()
'''
CLIENT = r'''
import json,sys,urllib.request
try:
    with urllib.request.urlopen("http://127.0.0.1:8099"+sys.argv[1],timeout=3) as response:
        print(response.read().decode())
except Exception as error:
    print(json.dumps({"transport_error":type(error).__name__}))
    raise SystemExit(2)
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def run(command, *, timeout=30):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command,124,"","command timed out")


def atomic_json(path, value):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+".next")
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
    temporary.replace(path)


class KindDockerAccess:
    def __init__(self, runner):
        self.run=runner

    def validate(self, node):
        pass

    def inspect(self, node, cid):
        return self.run(["docker","exec",node,"crictl","inspect",cid])

    def sample(self, node, pid):
        # Preserve the original kind command byte for byte.
        return self.run(["docker","exec",node,"sh","-c",
                        f"cat /proc/{pid}/stat && readlink /proc/{pid}/ns/pid && "
                        f"grep '^NSpid:' /proc/{pid}/status && getconf CLK_TCK"])

    def kernel(self, node):
        return self.run(["docker","exec",node,"uname","-r"])


class LocalSudoAccess:
    """Only fixed executables/arguments; never pass CRI output to a shell."""
    def __init__(self, runner, context):
        self.run=runner
        self.context=context

    def validate(self, node):
        message="local host machine ID does not match probe Pod node or cannot be verified"
        if not node:
            raise RuntimeError(message)
        try:
            remote=self.run(["kubectl","--context",self.context,"get","node",node,
                             "-o","jsonpath={.status.nodeInfo.machineID}"],timeout=30)
            local=self.run(["sudo","-n","cat","/etc/machine-id"])
        except OSError:
            raise RuntimeError(message) from None
        if remote.returncode or local.returncode:
            raise RuntimeError(message)
        remote_id,local_id=remote.stdout.strip(),local.stdout.strip()
        if (not re.fullmatch(r"[a-f0-9]{32}",remote_id)
            or not re.fullmatch(r"[a-f0-9]{32}",local_id) or remote_id!=local_id):
            raise RuntimeError(message)

    def inspect(self, node, cid):
        return self.run(["sudo","-n","crictl","inspect",cid])

    def sample(self, node, pid):
        lines=[]
        for command in (["cat",f"/proc/{pid}/stat"], ["readlink",f"/proc/{pid}/ns/pid"],
                        ["grep","^NSpid:",f"/proc/{pid}/status"], ["getconf","CLK_TCK"]):
            result=self.run(["sudo","-n",*command])
            if result.returncode:
                return subprocess.CompletedProcess([],result.returncode,"","")
            lines.append(result.stdout.rstrip("\n"))
        return subprocess.CompletedProcess([],0,"\n".join(lines)+"\n","")

    def kernel(self, node):
        self.validate(node)
        return self.run(["sudo","-n","uname","-r"])


class KindTrial:
    # Historical name retained for callers of the kind-only tool.
    def __init__(self, output: Path, method: str, *, image="serving-runtime-lab:dev", override_only=False,
                 context="kind-runtime-lab", node_access="kind-docker", image_pull_policy="IfNotPresent", runner=None):
        if node_access not in {"kind-docker","local-sudo"}:
            raise ValueError("unknown node access method")
        if image_pull_policy not in {"IfNotPresent","Never"}:
            raise ValueError("unsupported image pull policy")
        self.run=runner or run
        self.node_access=(KindDockerAccess(self.run) if node_access=="kind-docker"
                          else LocalSudoAccess(self.run,context))
        self.output=output
        self.method=method
        self.image=image
        self.image_pull_policy=image_pull_policy
        self.override_only=override_only
        self.context=context
        self.namespace="byul-proof-"+uuid4().hex[:10]
        self.policy="runtime-tg-secret-01-"+self.namespace
        self.pod="probe"
        self.history=output/"transitions.jsonl"
        self.report={"schema_version":"runtime-tetragon-verification/1.0", "id":self.namespace,
                     "context":self.context,"namespace":self.namespace,"policy":self.policy,
                     "method":method,"data_origin":"real","started_at":now(),"facts":{},"events":[],"steps":[]}
        self.report["variant"]="override_only_diagnostic" if override_only else "combined"
        self.report["probe_path"]=PROBE_PATH
        self.created=False
        self.loaded=False
        self.stream=None
        self.stream_file=None
        self.stream_error=None

    def kube(self,*args,namespace=None,timeout=30):
        return self.run(["kubectl","--context",self.context,"-n",namespace or self.namespace,*args],timeout=timeout)

    def document(self,*args):
        result=self.kube(*args)
        return json.loads(result.stdout) if result.returncode==0 else {}

    def apply(self,name,value,*,create=False):
        path=self.output/self.namespace/(name+".json")
        atomic_json(path,value)
        result=self.kube("create" if create else "apply","-f",str(path))
        if result.returncode:
            raise RuntimeError("trial resource could not be applied")

    def state(self):
        result=self.kube("exec","daemonset/tetragon","-c","tetragon","--","tetra","tp","list","-o","json",namespace="kube-system")
        if result.returncode:
            return None
        return next((p for p in json.loads(result.stdout).get("policies",[]) if p.get("name")==self.policy and p.get("namespace")==self.namespace),None)

    def mode(self,target):
        result=switch_mode(self.context,self.namespace,self.policy,target,run=self.run,history_path=self.history)
        self.report["steps"].append({"at":now(),"target_mode":target,**result})
        return result

    def request(self,path):
        result=self.kube("exec",self.pod,"-c","probe","--","python","-c",CLIENT,path,timeout=10)
        try: value=json.loads(result.stdout)
        except ValueError: value={}
        return result.returncode,value

    def pod_state(self):
        document=self.document("get","pod",self.pod,"-o","json")
        statuses=document.get("status",{}).get("containerStatuses",[])
        return (document.get("metadata",{}).get("uid"),statuses[0] if statuses else {})

    def agent_state(self):
        result=self.kube("get","pods","-l","app.kubernetes.io/name=tetragon","-o","json",namespace="kube-system")
        pods=json.loads(result.stdout).get("items",[]) if result.returncode==0 else []
        if len(pods)!=1:
            raise RuntimeError("isolated trial requires exactly one Tetragon agent")
        statuses=pods[0].get("status",{}).get("containerStatuses",[])
        agent=next((s for s in statuses if s.get("name")=="tetragon"),{})
        if not agent.get("ready"):
            raise RuntimeError("Tetragon agent is not ready")
        return {"pod_uid":pods[0]["metadata"]["uid"],"restart_count":agent.get("restartCount")}

    def task_identity(self, pod, node):
        """Read only CRI's exact container init; never infer identity from a PID alone."""
        import re
        if pod.get("spec",{}).get("nodeName")!=node:
            raise RuntimeError("probe Pod node changed during trial")
        self.node_access.validate(node)
        statuses=pod.get("status",{}).get("containerStatuses",[])
        container=next(s for s in statuses if s.get("name")=="probe")
        cid=container["containerID"].removeprefix("containerd://")
        if not re.fullmatch(r"[a-f0-9]{64}",cid):
            raise RuntimeError("unexpected CRI container identity")
        inspected=self.node_access.inspect(node,cid)
        if inspected.returncode:
            raise RuntimeError("cannot inspect disposable container init")
        init_pid=json.loads(inspected.stdout)["info"]["pid"]
        if type(init_pid) is not int or init_pid <= 0:
            raise RuntimeError("invalid CRI init PID")
        sample=self.node_access.sample(node,init_pid)
        if sample.returncode:
            raise RuntimeError("cannot bind disposable container init to procfs")
        stat,pidns,nspid,hz=sample.stdout.splitlines()
        identity={"pod_uid":pod["metadata"]["uid"],"namespace":self.namespace,
                  "container_id":cid,"node_pid":init_pid,"ns_pid":int(nspid.split()[-1]),
                  "pidns":int(pidns.removeprefix("pid:[").removesuffix("]")),
                  "start_ticks":int(stat.rsplit(")",1)[1].split()[19]),"clock_ticks":int(hz)}
        if identity["ns_pid"] != 1:
            raise RuntimeError("CRI probe process is not container init")
        return identity

    def wait_read(self,path="/health",seconds=60):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            code,value=self.request(path)
            if code==0 and value.get("ready" if path=="/health" else "read") in (True,"ok"):
                return True
            time.sleep(1)
        return False

    def execute(self):
        facts=self.report["facts"]
        facts.update(main_process=True,cleanup_ok=None,collector_complete=False)
        try:
            self.report["agent_before"]=self.agent_state()
            self.apply("namespace",{"apiVersion":"v1","kind":"Namespace","metadata":{"name":self.namespace,"labels":{"serving-runtime.local/purpose":"tetragon-proof"}}},create=True)
            self.created=True
            self.apply("pod",{"apiVersion":"v1","kind":"Pod","metadata":{"name":self.pod,"namespace":self.namespace,"labels":{"app":"byul-tetragon-proof"}},
                "spec":{"restartPolicy":"Always","terminationGracePeriodSeconds":1,"automountServiceAccountToken":False,"securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},
                    "volumes":[{"name":"file","emptyDir":{}}],
                    "initContainers":[{"name":"prepare-fake-file","image":self.image,"imagePullPolicy":self.image_pull_policy,"command":["python","-c","from pathlib import Path; Path('/run/byul-probe/secret.txt').write_text('FAKE-PROBE-ONLY')"],"volumeMounts":[{"name":"file","mountPath":"/run/byul-probe"}]}],
                    "containers":[{"name":"probe","image":self.image,"imagePullPolicy":self.image_pull_policy,"command":["python","-u","-c",SERVER],"volumeMounts":[{"name":"file","mountPath":"/run/byul-probe","readOnly":True}]}]}})
            ready=self.kube("wait","pod/"+self.pod,"--for=condition=Ready","--timeout=60s",timeout=65)
            if ready.returncode or not self.wait_read():
                raise RuntimeError("disposable main-process probe did not become ready")
            uid,before_container=self.pod_state()
            self.report["pod_uid"]=uid
            pod_doc=self.document("get","pod",self.pod,"-o","json")
            node=pod_doc["spec"]["nodeName"]
            self.report["node"]=node
            identity=self.task_identity(pod_doc,node)
            self.report["task_identity"]=identity
            kernel=self.node_access.kernel(node)
            self.report["kernel"]=kernel.stdout.strip() if kernel.returncode==0 else None
            if not self.report["kernel"]:
                raise RuntimeError("cannot read trial node kernel")
            source=json.loads((ROOT/"deploy/lab/intent.lab-v1.1.json").read_text())
            source["namespace"]=self.namespace
            source["engine"]["labels"]={"app":"byul-tetragon-proof"}
            source["protected_paths"]["secret_paths"]=[PROBE_PATH]
            source["protected_paths"]["enforce_exact_files"]=[PROBE_PATH]
            source["tetragon_rule_modes"]={"TG-SECRET-01":"enforce"}
            source["tetragon_enforce_method"]=self.method
            if self.method=="override_syscall": source["tetragon_openat_symbol"]="__x64_sys_openat"
            policy=render_manifests(parse_intent(source))["tetragon-tg-secret-01.json"]
            policy["metadata"]["name"]=self.policy
            # Proof-only metadata reads; the candidate's hooks, selectors and
            # enforcement actions remain identical to the rendered policy.
            policy["spec"]["kprobes"][0]["data"]=KERNEL_IDENTITY_DATA
            self.report["identity_probe_data"]=KERNEL_IDENTITY_DATA
            if self.override_only:
                if self.method=="sigkill_fd_install":
                    raise ValueError("Override diagnostic requires an Override method")
                for selector in policy["spec"]["kprobes"][0]["selectors"]:
                    selector["matchActions"]=[a for a in selector["matchActions"] if a["action"]!="Sigkill"]
                policy["metadata"]["annotations"]["serving-runtime.local/trial-variant"]="override-only-diagnostic"
            self.apply("policy",policy)
            installed=self.document("get","tracingpolicynamespaced",self.policy,"-o","json")
            atomic_json(self.output/self.namespace/"installed-spec.json",installed.get("spec",{}))
            if comparable_spec(installed.get("spec", {})) != comparable_spec(policy["spec"]):
                raise RuntimeError("loaded policy spec differs from reviewed candidate")
            self.report["policy_actions"]=policy["spec"]["kprobes"][0]["selectors"][0]["matchActions"]
            self.report["policy_spec_sha256"]=hashlib.sha256(json.dumps(comparable_spec(installed["spec"]),sort_keys=True,separators=(",",":")).encode()).hexdigest()
            # 1.7.1 ListTracingPolicies can race with sensor loading. Let the
            # controller settle before querying; still fence agent restarts.
            time.sleep(3)
            deadline=time.monotonic()+20
            while time.monotonic()<deadline:
                state=self.state()
                if state and state.get("state") in ("TP_STATE_ENABLED","TP_STATE_LOAD_ERROR"):
                    break
                time.sleep(1)
            self.report["load"]=state
            if not state or state.get("state")!="TP_STATE_ENABLED":
                facts["unsupported"]=bool(state and state.get("state")=="TP_STATE_LOAD_ERROR")
                raise RuntimeError("candidate policy did not reach enabled; see load evidence")
            self.loaded=True
            self.report["policy_id"]=str(state["id"])
            raw_path=self.output/self.namespace/"probe-events.jsonl"
            self.stream_file=raw_path.open("w")
            self.stream_error=(raw_path.parent/"probe-stream.stderr").open("w")
            self.stream=subprocess.Popen(["kubectl","--context",self.context,"-n","kube-system",
                "exec","daemonset/tetragon","-c","tetragon","--","timeout","120","tetra","getevents",
                "--policy-names",self.policy,"-o","json"],
                stdout=self.stream_file,stderr=self.stream_error)
            time.sleep(3)
            monitor_started=now()
            monitor_read=self.request("/probe")[1]
            self.report["monitor_read_result"]=monitor_read
            facts["monitor_read_ok"]=monitor_read.get("read")=="ok" and monitor_read.get("pid")==1
            main_pid=None
            # Keep incomplete events for diagnosis; namespace/Pod/PID checks
            # remain mandatory in match_probe_events. The agent may retry Pod
            # enrichment for 15 * 2 seconds before delivering an event.
            for _ in range(40):
                if self.stream.poll() is not None:
                    raise RuntimeError("probe event stream ended before mode switch")
                monitor_events=match_probe_events(raw_path.read_text().splitlines(),policy=self.policy,
                    namespace=self.namespace,pod_uid=uid,path=PROBE_PATH,since=monitor_started,main_process=True,task_identity=identity)
                pids={e["pid"] for e in monitor_events if isinstance(e.get("pid"),int)}
                if len(pids)==1:
                    main_pid=pids.pop()
                    self.report["monitor_events"]=monitor_events
                    break
                self.request("/probe")
                time.sleep(1)
            if main_pid is None:
                raise RuntimeError("monitor read could not be bound to the container main process")
            if self.task_identity(self.document("get","pod",self.pod,"-o","json"),node) != identity:
                raise RuntimeError("probe container identity changed before enforcement")
            self.report["main_pid"]=main_pid
            switched=self.mode("enforce")
            if not switched["ok"]:
                raise RuntimeError("enforce mode could not be verified")
            facts["enforce_active"]=True
            started=now()
            code,read=self.request("/probe")
            self.report["read_result"]=read
            facts["read_succeeded"]=read.get("read")=="ok"
            facts["read_denied"]=code==0 and read.get("read")=="denied" and read.get("errno") in (1,13)
            facts["service_unavailable"]=self.request("/health")[0]!=0
            facts["container_restarted"]=False
            facts["process_terminated"]=False
            for _ in range(15):
                if facts["read_denied"]: break
                current_uid,container=self.pod_state()
                terminated=container.get("lastState",{}).get("terminated",{})
                restarted=(current_uid==uid and container.get("restartCount",0)>before_container.get("restartCount",0))
                if restarted:
                    self.report["termination"]={key:terminated.get(key) for key in ("exitCode","signal","reason","finishedAt")}
                    finished=terminated.get("finishedAt")
                    # Compare times at whole-second precision because Kubernetes truncates them.
                    in_window=bool(finished and datetime.fromisoformat(finished.replace('Z','+00:00')).timestamp()>=datetime.fromisoformat(started).timestamp()-1)
                    facts["container_restarted"]=True
                    facts["process_terminated"]=terminated.get("exitCode")==137 and terminated.get("reason")!="OOMKilled" and in_window
                    break
                time.sleep(1)
            until=now()
            events=[]
            for _ in range(8):
                raw_lines=raw_path.read_text().splitlines()
                events=match_probe_events(raw_lines,policy=self.policy,namespace=self.namespace,pod_uid=uid,path=PROBE_PATH,since=started,until=until,main_process=True,pid=main_pid,task_identity=identity)
                if events: break
                time.sleep(1)
            self.report["events"]=events
            facts["collector_complete"]=self.stream.poll() is None and not any('lost_event' in line for line in raw_lines)
            facts["event_bound"]=bool(events)
            facts["override_event"]=any(e["action"]=="KPROBE_ACTION_OVERRIDE" for e in events)
            facts["sigkill_event"]=any(e["action"]=="KPROBE_ACTION_SIGKILL" for e in events)
            restored=self.mode("monitor")
            facts["monitor_mode_restored"]=restored["ok"]
            facts["same_policy_id"]=restored["ok"] and restored["after"]["id"]==str(state["id"])
            facts["service_recovered"]=self.wait_read()
            facts["restore_read_ok"]=self.wait_read("/probe")
            self.report["restore_read_result"]=self.request("/probe")[1]
        except Exception as error:
            self.report["error"]=str(error)[:1200]
        finally:
            if self.loaded:
                recovery=self.mode("monitor")
                facts["monitor_mode_restored"]=recovery["ok"]
            if self.stream is not None:
                self.stream.terminate()
                try: self.stream.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.stream.kill()
                    self.stream.wait(timeout=5)
            if self.stream_file is not None: self.stream_file.close()
            if self.stream_error is not None: self.stream_error.close()
            # Closing kubectl does not always stop the remote exec process.
            listing=self.kube("exec","daemonset/tetragon","-c","tetragon","--","ps","-o","pid,args",namespace="kube-system")
            for line in listing.stdout.splitlines():
                fields=line.split(maxsplit=1)
                if (len(fields)==2 and fields[0].isdigit() and "tetra getevents" in fields[1]
                    and f"--policy-names {self.policy} " in fields[1]):
                    self.kube("exec","daemonset/tetragon","-c","tetragon","--","kill","-TERM",fields[0],namespace="kube-system")
            if self.created:
                deleted=self.kube("delete","namespace",self.namespace,"--wait=true","--timeout=45s",timeout=50)
                facts["cleanup_ok"]=deleted.returncode==0 and (not self.loaded or facts.get("monitor_mode_restored") is True)
            try:
                self.report["agent_after"]=self.agent_state()
                facts["agent_changed"]=self.report.get("agent_before")!=self.report["agent_after"]
            except Exception:
                facts["agent_changed"]=True
            if facts["agent_changed"]:
                facts["collector_complete"]=False
                self.report["error"]="Tetragon agent restarted or became unavailable during trial; evidence incomplete"
            self.finish()
        return self.report

    def finish(self):
        self.report["finished_at"]=now()
        for filename,key in (("policy.json","manifest_sha256"),("probe-events.jsonl","raw_event_sha256")):
            artifact=self.output/self.namespace/filename
            if artifact.exists():
                self.report[key]=hashlib.sha256(artifact.read_bytes()).hexdigest()
        self.report=reassess_record(self.report)
        atomic_json(self.output/(self.namespace+".verification.json"),self.report)
        return self.report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--methods",nargs="+",choices=METHODS,default=METHODS)
    parser.add_argument("--node-access",choices=("kind-docker","local-sudo"),default="kind-docker")
    parser.add_argument("--context",default="kind-runtime-lab")
    parser.add_argument("--image",default="serving-runtime-lab:dev",help="Python을 포함한 시험용 이미지")
    parser.add_argument("--image-pull-policy",choices=("IfNotPresent","Never"),default="IfNotPresent")
    parser.add_argument("--allow-isolated-trials","--allow-isolated-kind-trials",dest="allow_isolated_trials",action="store_true",required=True)
    parser.add_argument("--override-only-diagnostic",action="store_true",help="시험 전용으로 Sigkill을 제거해 Override의 errno를 분리 측정")
    args=parser.parse_args()
    exit_code=0
    for method in args.methods:
        if args.override_only_diagnostic and method=="sigkill_fd_install":
            parser.error("Override diagnostic requires an Override method")
        report=KindTrial(args.output,method,override_only=args.override_only_diagnostic,
                         node_access=args.node_access,context=args.context,image=args.image,
                         image_pull_policy=args.image_pull_policy).execute()
        print(json.dumps({"id":report["id"],"method":method,"verification":report["verification"],"load_state":(report.get("load") or {}).get("state"),"error":report.get("error")},ensure_ascii=False),flush=True)
        if report["verification"]["status"]!="passed": exit_code=2
        if report["facts"].get("agent_changed"): break
    return exit_code

if __name__=="__main__": raise SystemExit(main())
