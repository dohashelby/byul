"""Read-only baseline→candidate gate. It never deploys, deletes or claims GPU release."""
import math
from pathlib import Path
import time

from .bounded import strict_json
from .kubernetes_binding import verify_owned_pod
from .kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE


class KubernetesTransition:
    def __init__(self,envs,side_configs,*,timeout=300,poll_interval=0.25,runner=None,clock=None,sleep=None):
        if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=300
                or type(poll_interval) not in (int,float) or not math.isfinite(poll_interval) or not 0.01<=poll_interval<=5):
            raise ValueError("KUBERNETES_TRANSITION_CONFIGURATION_INVALID")
        self.clock=clock or time.monotonic;self.sleep=sleep or time.sleep
        self.deadline=self.clock()+timeout;self.interval=poll_interval
        self.transports={};self.events=[]
        for side,env in zip(("baseline","candidate"),envs):
            cfg=side_configs[side]
            if cfg.get("executors")!={"request_binding":"kubernetes"}:
                raise ValueError("KUBERNETES_TRANSITION_SCOPE_UNSUPPORTED")
            target=strict_json(Path(cfg["request_binding"]["target"]).read_bytes())
            self.transports[side]=KubernetesHTTP(target,env,duration=timeout,
                binary=cfg["request_binding"].get("kubectl_binary","kubectl"),runner=runner,profile=REQUEST_BINDING_PROFILE,clock=self.clock)
        a,b=[self.transports[s].binding for s in ("baseline","candidate")]
        if a["context"]!=b["context"] or a["engine"]["pod_uid"]==b["engine"]["pod_uid"] or a["engine"]["container_id"]==b["engine"]["container_id"]:
            raise ValueError("KUBERNETES_TRANSITION_TARGETS_UNVERIFIED")

    def prepare_baseline(self):
        self.transports["baseline"]._verify()
        self.events.append("BASELINE_READY_OBSERVED")

    def candidate_ready(self):
        transport=self.transports["baseline"];expected=transport.binding["engine"]
        self.events.append("WAITING_BASELINE_API_TERMINATION")
        while self.clock()<self.deadline:
            try:
                ns=transport._call(["get","namespace",expected["namespace"],"-o","json"])
                meta=ns["metadata"]
                if meta.get("uid")!=expected["namespace_uid"] or meta.get("deletionTimestamp"):raise ValueError
                pod=transport._call([f"--namespace={expected['namespace']}","get","pod",expected["pod"],"--ignore-not-found","-o","json"],allow_empty=True)
                stopped=pod is None
                if pod is not None:
                    if pod["metadata"].get("uid")!=expected["pod_uid"]:raise ValueError
                    try:
                        verify_owned_pod(pod,meta,expected,transport.binding["run_id"],require_ready=False,terminated=True)
                        stopped=True
                    except ValueError:
                        verify_owned_pod(pod,meta,expected,transport.binding["run_id"],require_ready=False)
                if stopped:
                    if "BASELINE_API_TERMINATION_OBSERVED" not in self.events:self.events.append("BASELINE_API_TERMINATION_OBSERVED")
                    candidate=self.transports["candidate"]
                    pods=candidate._verify(require_ready=False)
                    ready=all(any(row.get("type")=="Ready" and row.get("status")=="True" for row in pod["status"].get("conditions",[]))
                        and next(row for row in pod["status"]["containerStatuses"] if row["name"]==candidate.binding[role]["container"]).get("ready") is True
                        for role,pod in pods.items())
                    if ready:
                        candidate._verify()
                        self.events.append("CANDIDATE_READY_OBSERVED")
                        return
                    if "WAITING_CANDIDATE_READY" not in self.events:self.events.append("WAITING_CANDIDATE_READY")
            except Exception:
                self.events.append("RECOVERY_REQUIRED")
                raise ValueError("KUBERNETES_TRANSITION_UNVERIFIED") from None
            self.sleep(min(self.interval,max(0,self.deadline-self.clock())))
        self.events.append("RECOVERY_REQUIRED")
        raise ValueError("KUBERNETES_TRANSITION_TIMEOUT")

    def report(self):
        return {"profile":"kubernetes-owned-transition/1","events":list(self.events),
            "workloads_mutated":False,"gpu_release_verified":False,"rollback_performed":False,
            "binding_sha256":{side:value.report()["binding_sha256"] for side,value in self.transports.items()},
            "limitations":["API termination/absence and candidate Ready do not prove node-side execution ended or GPU was released.",
                "Operator performs deployment/termination/recovery; this gate sends read-only queries only."]}
