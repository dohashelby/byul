"""Owned Kubernetes metadata reads through fixed stdlib programs only."""
from __future__ import annotations

import hashlib
import math
import re
import time
from types import SimpleNamespace

from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_binding import validate_capture_target, verify_owned_pod
from .owned_capture import _FILE_READER, MAX_BYTES, strict_json, validate_file_result, validate_owned_source
from .lifecycle_capture import CONTEXT_READER
from .lifecycle_session import INVENTORY_READER

PROFILE = "kubernetes-owned-lifecycle-read-v1"


def validate_target(target,env):
    """Keep runtime binding access inside the executor boundary, including dry runs."""
    return validate_capture_target(target,env)


class KubernetesCaptureError(ValueError):
    def __init__(self):
        super().__init__("KUBERNETES_CAPTURE_BINDING_UNAVAILABLE")


def supervised_source(source):
    # -I -S -B is supplied by the caller: no site hooks or bytecode writes.
    # os._exit affects this new exec helper only, never the engine or another PID.
    return ("import math,os,sys,threading\n"
        "limit=float(sys.argv.pop(1))\n"
        "if not math.isfinite(limit) or not 0 < limit <= 5: os._exit(124)\n"
        "watchdog=threading.Timer(limit,lambda:os._exit(124))\n"
        "watchdog.daemon=True\nwatchdog.start()\n"
        + "exec(compile("+repr(source)+",'guard-owned-read','exec'),{'__name__':'__main__'})\n")


class KubernetesLifecycleReader:
    def __init__(self,target,env,prepared,*,binary="kubectl",duration=180,runner=None):
        self.binding=validate_target(target,env)
        if (type(binary) is not str or not binary.strip() or type(duration) not in (int,float)
                or not math.isfinite(duration) or not 0<duration<=300
                or type(prepared.get("session")) is not str or re.fullmatch(r"[0-9a-f]{32}",prepared["session"]) is None):
            raise KubernetesCaptureError()
        self.target_sha256=sha256_hex(canonical_json(target))
        self.session=prepared["session"]
        self.binary=binary;self.deadline=time.monotonic()+duration
        self.runner=runner or capture_pipes;self.injected=runner is not None
        self.programs={"file":_FILE_READER,"context":CONTEXT_READER,"inventory":INVENTORY_READER}
        self.wrapped={key:supervised_source(value) for key,value in self.programs.items()}
        self.rows=[];self.gap=False

    def _remaining(self,deadline):
        value=min(self.deadline,deadline)-time.monotonic()
        if value<=0:raise KubernetesCaptureError()
        return value

    def _request(self,args,*,deadline,cap):
        remaining=self._remaining(deadline)
        argv=[self.binary,f"--context={self.binding['context']}",f"--request-timeout={remaining:.3f}s",*args]
        try:
            result=self.runner(argv,timeout=remaining,env_extra={},max_stdout_bytes=cap,max_stderr_bytes=4096)
            pipe=result["capture"];raw=result["stdout_bytes"]
            if (result["returncode"]!=0 or pipe.get("complete") is not True
                    or pipe.get("bounded_at_source") is not True or pipe.get("timed_out") is not False
                    or pipe.get("truncated") is not False or type(raw) is not bytes or len(raw)>cap):
                raise KubernetesCaptureError()
            return raw
        except Exception:
            self.gap=True
            raise KubernetesCaptureError() from None

    def _owner(self,timeout):
        deadline=time.monotonic()+timeout;expected=self.binding["engine"]
        try:
            namespace=strict_json(self._request(["get","namespace",expected["namespace"],"-o","json"],
                deadline=deadline,cap=262144),limit=262144)
            if (namespace.get("kind")!="Namespace" or namespace["metadata"].get("name")!=expected["namespace"]):
                raise KubernetesCaptureError()
            pod=strict_json(self._request([f"--namespace={expected['namespace']}","get","pod",expected["pod"],"-o","json"],
                deadline=deadline,cap=262144),limit=262144)
            if pod.get("kind")!="Pod":raise KubernetesCaptureError()
            return verify_owned_pod(pod,namespace["metadata"],expected,self.binding["run_id"],require_ready=False)
        except Exception:
            self.gap=True
            raise KubernetesCaptureError() from None

    def _selected(self,client,target,container,image):
        expected=self.binding["engine"]
        if (client is not self or sha256_hex(canonical_json(target))!=self.target_sha256
                or container!=expected["container"] or image!=expected["image"]):
            raise KubernetesCaptureError()

    def owner_identity(self,client,target,container,image,timeout):
        self._selected(client,target,container,image)
        return self._owner(timeout)

    def _program(self,container,argv,max_bytes):
        if (container!=self.binding["engine"]["container"] or type(argv) is not list
                or len(argv) not in (4,5) or any(type(v) is not str for v in argv)
                or argv[:2]!=["python3","-c"] or type(max_bytes) is not int):
            raise KubernetesCaptureError()
        if argv[2]==_FILE_READER and len(argv)==5:
            if not re.fullmatch(r"[1-9][0-9]{4,5}",argv[4]) and argv[4]!="262144":
                raise KubernetesCaptureError()
            budget=int(argv[4])
            if not MAX_BYTES<=budget<=262144 or str(budget)!=argv[4] or max_bytes!=2*budget:
                raise KubernetesCaptureError()
            prefix=re.escape("/guard-lifecycle/data/"+self.session+"/")
            if re.fullmatch(prefix+r"pid-[1-9][0-9]{0,9}-[1-9][0-9]{0,19}\.jsonl",argv[3]) is None:
                raise KubernetesCaptureError()
            return "file"
        if argv[2]==CONTEXT_READER and len(argv)==4 and max_bytes==4096 and re.fullmatch(r"[1-9][0-9]{0,9}",argv[3]):
            return "context"
        if argv[2]==INVENTORY_READER and len(argv)==4 and max_bytes==65536 and argv[3]==self.session:
            return "inventory"
        raise KubernetesCaptureError()

    def exec_bounded(self,container,argv,*,timeout,max_bytes):
        key=self._program(container,argv,max_bytes)
        if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=5:
            raise KubernetesCaptureError()
        deadline=min(self.deadline,time.monotonic()+timeout)
        before=self._owner(self._remaining(deadline))
        role=self.binding["engine"]
        row={"program":key,"owner_before_after_verified":False,"transport_complete":False,
             "diagnostic_codes":[]}
        try:
            budget=self._remaining(deadline)
            raw=self._request([f"--namespace={role['namespace']}","exec",role["pod"],f"--container={role['container']}",
                "--","python3","-I","-S","-B","-c",self.wrapped[key],repr(budget),*argv[3:]],
                deadline=deadline,cap=max_bytes)
            text=raw.decode("utf-8","strict")
            row["transport_complete"]=True
        except Exception:
            row["diagnostic_codes"].append("KUBERNETES_READ_TRANSPORT_UNAVAILABLE")
            self.gap=True
            self.rows.append(row)
            raise KubernetesCaptureError() from None
        try:row["owner_before_after_verified"]=self._owner(self._remaining(deadline))==before
        except Exception:row["diagnostic_codes"].append("KUBERNETES_READ_OWNER_RECHECK_UNAVAILABLE")
        if not row["owner_before_after_verified"]:self.gap=True
        self.rows.append(row)
        return SimpleNamespace(returncode=0,stdout=text,stderr="",owner_before=before,
            owner_binding_verified=row["owner_before_after_verified"],
            capture={"complete":True,"bounded_at_source":True,"timed_out":False,"truncated":False,
                     "owner_binding_verified":row["owner_before_after_verified"]})

    def read_file(self,client,target,*,container,image,path,timeout=5,max_bytes=MAX_BYTES):
        self._selected(client,target,container,image)
        validate_owned_source(container=container,image=image,path=path)
        if type(max_bytes) is not int or not MAX_BYTES<=max_bytes<=262144:raise KubernetesCaptureError()
        result=self.exec_bounded(container,["python3","-c",_FILE_READER,path,str(max_bytes)],timeout=timeout,max_bytes=2*max_bytes)
        raw,file_identity=validate_file_result(result,max_bytes=max_bytes)
        role=self.binding["engine"]
        return raw,{"capture_sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw),"owner":result.owner_before,
            "file_identity":file_identity,"producer_authenticated":False,"record_authenticity_verified":False,
            "owner_binding_verified":result.owner_binding_verified,
            "native_image_reference":{"declared_image":role["image"],"reported_image_id":role["image_id"],
                "api_binding_verified_before":True,"api_binding_verified_after":result.owner_binding_verified,
                "node_runtime_image_verified":False,
                "manifest_ancestry_verified":False,"transport":PROFILE}}

    @staticmethod
    def image_check(provenance,source):
        # Pod spec/status matching is not the Docker RepoDigests proof used by this flag.
        return False

    def report(self):
        return {"profile":PROFILE,"target_binding_sha256":sha256_hex(canonical_json(self.binding)),
            "context_sha256":hashlib.sha256(self.binding["context"].encode()).hexdigest(),
            "programs":{k:{"source_sha256":hashlib.sha256(v.encode()).hexdigest(),
                "supervisor_sha256":hashlib.sha256(self.wrapped[k].encode()).hexdigest()} for k,v in self.programs.items()},
            "reads":self.rows,"collection_gap":self.gap,"node_runtime_image_verified":False,
            "producer_authenticated":False,"engine_started":False,"injected_runner":self.injected}
