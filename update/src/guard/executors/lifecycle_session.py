"""Read-only bounded session census and per-process capture; no lifecycle verdict."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from copy import copy
import json
import re

from guard.adapters import vllm_lifecycle_links as links
from guard.adapters import vllm_route_authentication
from .lifecycle_capture import capture
from .owned_capture import identity, strict_json, validate_owned_source

MAX_FILES = 32
MAX_PROCESSES = 128
MAX_INDEX_IDS = 256
MAX_INDEX_EVENTS = 4096
MAX_MODEL_EVENTS = 256

# No argv/environ, engine import, signal, directory creation or native cleanup.
INVENTORY_READER = r'''
import hashlib,json,os,re,stat,sys
from pathlib import Path,PurePosixPath
session=sys.argv[1]
if re.fullmatch(r"[0-9a-f]{32}",session) is None:raise ValueError()
def read(path,limit):
 with path.open('rb') as stream:data=stream.read(limit+1)
 if len(data)>limit:raise ValueError()
 return data
def directory(path):
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
 try:
  for part in PurePosixPath(path).parts[1:]:
   child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
   os.close(fd);fd=child
  return fd
 except BaseException:
  os.close(fd);raise
def info(value):
 return dict(device=value.st_dev,inode=value.st_ino,uid=value.st_uid,mode=stat.S_IMODE(value.st_mode),mtime_ns=value.st_mtime_ns)
root=directory('/guard-lifecycle/data/'+session)
try:
 first=info(os.fstat(root))
 if first['uid']!=os.geteuid() or first['mode']&0o077:raise ValueError()
 files=[];unknown=0;files_truncated=False;sealed=False;seen=0
 with os.scandir(root) as entries:
  for entry in entries:
   seen+=1
   if seen>64:files_truncated=True;break
   match=re.fullmatch(r'pid-([1-9][0-9]{0,9})-([1-9][0-9]{0,19})\.jsonl',entry.name)
   if entry.name=='SEALED':
    control=entry.stat(follow_symlinks=False)
    sealed=stat.S_ISREG(control.st_mode) and control.st_uid==first['uid'] and not control.st_mode&0o077
    if not sealed:unknown+=1
   elif match:
    if len(files)>=32:files_truncated=True;continue
    try:
     value=entry.stat(follow_symlinks=False)
     row=dict(pid=int(match[1]),start_ticks=int(match[2]),bytes=value.st_size,uid=value.st_uid,
       mode=stat.S_IMODE(value.st_mode),device=value.st_dev,inode=value.st_ino,mtime_ns=value.st_mtime_ns,ctime_ns=value.st_ctime_ns,
       regular=stat.S_ISREG(value.st_mode))
    except OSError:
     row=dict(pid=int(match[1]),start_ticks=int(match[2]),bytes=None,uid=None,mode=None,device=None,inode=None,mtime_ns=None,ctime_ns=None,regular=False)
    files.append(row)
   else:unknown+=1
 last=info(os.fstat(root))
finally:os.close(root)
boot=hashlib.sha256(read(Path('/proc/sys/kernel/random/boot_id'),128)).hexdigest()
def process(pid):
 p=Path('/proc')/str(pid)
 def core():
  data=read(p/'stat',8192).decode('utf-8');values=data[data.rfind(')')+2:].split()
  uid=int(next(v for v in read(p/'status',16384).decode('ascii').splitlines() if v.startswith('Uid:')).split()[2])
  return dict(pid=pid,start_ticks=int(values[19]),parent_pid=int(values[1]),uid=uid,
   boot_sha256=boot,pid_namespace=os.stat(p/'ns/pid').st_ino,time_namespace=os.stat(p/'ns/time').st_ino,
   time_offsets_sha256=hashlib.sha256(read(p/'timens_offsets',1024)).hexdigest())
 before=core();same=os.path.samefile(p/'exe','/proc/self/exe');after=core()
 if before!=after:raise ValueError()
 return dict(before,same_executable=same)
pids=[];processes_truncated=False
with os.scandir('/proc') as entries:
 for entry in entries:
  if re.fullmatch(r'[1-9][0-9]{0,9}',entry.name) and int(entry.name)!=os.getpid():
   if len(pids)>=128:processes_truncated=True;break
   pids.append(int(entry.name))
processes=[];unreadable=[]
for pid in sorted(pids):
 try:processes.append(process(pid))
 except Exception:unreadable.append(pid)
print(json.dumps(dict(schema='guard-lifecycle-session-inventory/1',session=session,
 directory_before=first,directory_after=last,directory_stable=first==last,sealed=sealed,
 files=sorted(files,key=lambda v:(v['pid'],v['start_ticks'])),unknown_entries=unknown,
 files_truncated=files_truncated,processes=processes,unreadable_pids=unreadable,
 processes_truncated=processes_truncated),sort_keys=True,separators=(',',':')))
'''


def _integer(value, minimum=0):
    # Linux filesystem identifiers may use the high bit of an unsigned u64.
    return type(value) is int and minimum <= value < 2**64


def _sha(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()


def safe_owner(owner):
    value = dict(owner)
    value["started_at_sha256"] = hashlib.sha256(value.pop("started_at").encode("utf-8","surrogatepass")).hexdigest()
    return value


def parse_inventory(raw, session):
    value = strict_json(raw)
    keys = {"schema","session","directory_before","directory_after","directory_stable","sealed","files",
            "unknown_entries","files_truncated","processes","unreadable_pids","processes_truncated"}
    if (type(value) is not dict or set(value)!=keys or value["schema"]!="guard-lifecycle-session-inventory/1"
            or value["session"]!=session
            or any(type(value[k]) is not bool for k in ("directory_stable","sealed","files_truncated","processes_truncated"))
            or not _integer(value["unknown_entries"]) or value["unknown_entries"]>64):
        raise ValueError("LIFECYCLE_INVENTORY_INVALID")
    for key in ("directory_before","directory_after"):
        row=value[key]
        if (type(row) is not dict or set(row)!={"device","inode","uid","mode","mtime_ns"}
                or any(not _integer(v) for v in row.values()) or row["mode"]>0o7777 or row["mode"]&0o077):
            raise ValueError("LIFECYCLE_INVENTORY_DIRECTORY_INVALID")
    if value["directory_stable"] is not (value["directory_before"]==value["directory_after"]):
        raise ValueError("LIFECYCLE_INVENTORY_STABILITY_INVALID")
    files=value["files"]
    if type(files) is not list or len(files)>MAX_FILES:
        raise ValueError("LIFECYCLE_INVENTORY_FILE_BOUND")
    seen=set()
    for row in files:
        if (type(row) is not dict or set(row)!={"pid","start_ticks","bytes","uid","mode","device","inode","mtime_ns","ctime_ns","regular"}
                or not _integer(row["pid"],1) or row["pid"]>=10**10 or not _integer(row["start_ticks"],1)
                or type(row["regular"]) is not bool
                or any(v is not None and not _integer(v) for k,v in row.items() if k not in {"regular","pid","start_ticks"})
                or row["mode"] is not None and row["mode"]>0o7777):
            raise ValueError("LIFECYCLE_INVENTORY_FILE_INVALID")
        key=(row["pid"],row["start_ticks"])
        if key in seen:raise ValueError("LIFECYCLE_INVENTORY_DUPLICATE_FILE")
        seen.add(key)
    processes=value["processes"]
    unreadable=value["unreadable_pids"]
    if (type(processes) is not list or len(processes)>MAX_PROCESSES or type(unreadable) is not list
            or len(unreadable)>MAX_PROCESSES or any(not _integer(p,1) or p>=10**10 for p in unreadable)):
        raise ValueError("LIFECYCLE_INVENTORY_PROCESS_BOUND")
    pids=set(unreadable)
    if len(pids)!=len(unreadable):raise ValueError("LIFECYCLE_INVENTORY_DUPLICATE_PID")
    for row in processes:
        if (type(row) is not dict or set(row)!={"pid","start_ticks","parent_pid","uid","boot_sha256",
                "pid_namespace","time_namespace","time_offsets_sha256","same_executable"}
                or any(not _integer(row[k],0 if k in {"uid","parent_pid"} else 1)
                    for k in ("pid","start_ticks","parent_pid","uid","pid_namespace","time_namespace"))
                or row["pid"]>=10**10 or any(not _sha(row[k]) for k in ("boot_sha256","time_offsets_sha256"))
                or type(row["same_executable"]) is not bool or row["pid"] in pids):
            raise ValueError("LIFECYCLE_INVENTORY_PROCESS_INVALID")
        pids.add(row["pid"])
    if len(pids)>MAX_PROCESSES:raise ValueError("LIFECYCLE_INVENTORY_PROCESS_BOUND")
    # Normalize lists before the second census comparison; never reorder events.
    value["files"]=sorted(files,key=lambda r:(r["pid"],r["start_ticks"]))
    value["processes"]=sorted(processes,key=lambda r:r["pid"])
    value["unreadable_pids"]=sorted(unreadable)
    return value


def inventory(docker,target,*,container,image,session,owner_identity=None,rolling=False):
    validate_owned_source(container=container,image=image,path="/guard-lifecycle/data/"+session)
    if re.fullmatch(r"[0-9a-f]{32}",session) is None:raise ValueError("LIFECYCLE_SESSION_INVALID")
    owner=identity if owner_identity is None else owner_identity
    before=owner(docker,target,container,image,5)
    program=INVENTORY_READER
    if rolling:
        from .lifecycle_rolling import inventory_reader
        program=inventory_reader(program)
    result=docker.exec_bounded(container,["python3","-c",program,session],timeout=5,max_bytes=131072 if rolling else 65536)
    pipe=result.capture or {}
    if (result.returncode!=0 or pipe.get("complete") is not True or pipe.get("bounded_at_source") is not True
            or pipe.get("timed_out") is not False or pipe.get("truncated") is not False):
        raise ValueError("LIFECYCLE_INVENTORY_TRANSPORT_INVALID")
    raw=result.stdout.encode()
    if rolling:
        from .lifecycle_rolling import parse_inventory as parse_rolling
        value=parse_rolling(raw,session)
    else:value=parse_inventory(raw,session)
    try:stable=(owner(docker,target,container,image,5)==before and pipe.get("owner_binding_verified",True) is True)
    except Exception:stable=False
    return {"metadata":value,"owner":safe_owner(before),"owner_stable":stable,
            "capture_sha256":hashlib.sha256(raw).hexdigest(),"bytes":len(raw)}


def _complete(value):
    row=value["metadata"]
    return (value["owner_stable"] and row["directory_stable"] and not row["unknown_entries"]
        and not row["files_truncated"] and not row["processes_truncated"] and not row["unreadable_pids"])


def request_index(recordings):
    domains={};ids_count=0;events_count=0;model_count=0;complete=True
    for capture_index,recording in enumerate(recordings):
        if recording["capture"] is None:continue
        value=recording["capture"];context=value["records"][0]["payload"]["context"]
        clock={k:context[k] for k in ("boot_sha256","pid_namespace","time_namespace","time_offsets_sha256")}
        domain={"owner_sha256":_digest(value["provenance"]["owner"]),"clock":clock}
        key=_digest(domain)
        group=domains.setdefault(key,{**domain,"fingerprints":{},"assignments":[],"model_observations":[]})
        for row in value["records"]:
            for fingerprint in links.fingerprints(row):
                if fingerprint not in group["fingerprints"]:
                    if ids_count>=MAX_INDEX_IDS:complete=False;continue
                    group["fingerprints"][fingerprint]=[];ids_count+=1
                if events_count>=MAX_INDEX_EVENTS:complete=False;continue
                group["fingerprints"][fingerprint].append([capture_index,row["seq"]]);events_count+=1
            pair=links.assignment(row)
            if pair:
                if len(group["assignments"])>=MAX_INDEX_IDS:complete=False;continue
                entry={"external_sha256":pair[0],"internal_sha256":pair[1],"event":[capture_index,row["seq"]]}
                asgi=links.asgi_assignment(row)
                if asgi is not None:entry["asgi_request_sha256"]=asgi
                group["assignments"].append(entry)
            if row["event"]=="NATIVE" and "model_selection" in row["payload"]["metadata"]:
                if model_count>=MAX_MODEL_EVENTS:complete=False;continue
                group["model_observations"].append({"event":[capture_index,row["seq"]],
                    "point":row["payload"]["point"],"phase":row["payload"]["phase"],
                    "request_sha256":[r["request_sha256"] for r in row["payload"]["requests"]],
                    "metadata":row["payload"]["metadata"]["model_selection"]})
                model_count+=1
    for group in domains.values():
        values={}
        for row in group["assignments"]:
            values.setdefault(row["external_sha256"],set()).add(row["internal_sha256"])
        group["ambiguous_external_sha256"]=sorted(k for k,v in values.items() if len(v)>1)
        asgi_values={}
        for row in group["assignments"]:
            if "asgi_request_sha256" in row:
                asgi_values.setdefault(row["asgi_request_sha256"],set()).add(row["internal_sha256"])
        group["ambiguous_asgi_request_sha256"]=sorted(k for k,v in asgi_values.items() if len(v)>1)
    return {"domains":domains,"stored_record_index_complete":complete,"producer_authenticated":False,
            "final_model_verified":False,"actual_consumption_verified":False,"weights_revision_verified":False,
            "whole_run_coverage_verified":False,"scope":"declared fingerprint equality, ASGI header-context association and native assignment events; no stop duration or reclamation verdict"}


def capture_session(docker,target,*,container,image,prepared,owner_identity=None,read_file=None,image_check=None,checkpoint=False,rolling=False):
    result={"schema":"guard-lifecycle-session-capture/1","session":prepared["session"],"before":None,"after":None,
        "recordings":[],"diagnostic_codes":[],"live_census_stable":False,"recorded_files_covered":False,
        "python_processes_without_recording":[],"producer_authenticated":False,"record_authenticity_verified":False,
        "whole_run_coverage_verified":False,"product_connected":False,"security":"UNKNOWN"}
    if checkpoint:result["capture_mode"]="live_checkpoint_rolling" if rolling else "live_checkpoint"
    arguments={"container":container,"image":image,"session":prepared["session"]}
    if owner_identity is not None:arguments["owner_identity"]=owner_identity
    if rolling:arguments["rolling"]=True
    try:result["before"]=inventory(docker,target,**arguments)
    except Exception:result["diagnostic_codes"].append("LIFECYCLE_INITIAL_INVENTORY_UNAVAILABLE")
    before=result["before"]
    if before is not None:
        def read_recording(item):
            file, reader = item
            source={"container":container,"image":image,
                "path":f"/guard-lifecycle/data/{prepared['session']}/pid-{file['pid']}-{file['start_ticks']}"+(f"-segment-{file['segment']}" if rolling and file['segment'] is not None else '')+'.jsonl'}
            row={"pid":file["pid"],"start_ticks":file["start_ticks"],"capture":None,"session_owner_match":False,
                 "listed_file_identity_match":False,"diagnostic_codes":[]}
            if rolling:row["segment"]=file["segment"]
            try:
                if (not file["regular"] or file["uid"]!=before["metadata"]["directory_before"]["uid"]
                        or file["mode"]&0o077):raise ValueError("LIFECYCLE_SESSION_FILE_UNVERIFIED")
                options={}
                if read_file is not None:options["read_file"]=reader.read_file if reader is not docker else read_file
                if image_check is not None:options["image_check"]=reader.image_check if reader is not docker else image_check
                if checkpoint:options["checkpoint"]=not rolling or file["segment"] is None
                if rolling:options["segment"]=file["segment"];options["rolling"]=True
                row["capture"]=capture(reader,target,source=source,prepared=prepared,**options)
                row["session_owner_match"]=row["capture"]["provenance"]["owner"]==before["owner"]
                if not row["session_owner_match"]:row["diagnostic_codes"].append("LIFECYCLE_SESSION_OWNER_CHANGED")
                expected={k:file["bytes" if k=="size" else k] for k in ("device","inode","size","mtime_ns","ctime_ns")}
                row["listed_file_identity_match"]=row["capture"]["provenance"]["file_identity"]==expected
                if checkpoint and row['capture'].get('checkpoint_prefix') is not None:
                    observed=row['capture']['provenance']['file_identity']
                    row['listed_file_identity_match']=all(observed.get(k)==expected[k] for k in ('device','inode'))
                if not row["listed_file_identity_match"]:row["diagnostic_codes"].append("LIFECYCLE_LISTED_FILE_CHANGED")
            except Exception:row["diagnostic_codes"].append("LIFECYCLE_PROCESS_RECORDING_UNAVAILABLE")
            return row, reader
        files=before['metadata']['files']
        # Only the production reader's stdlib subprocess runner is independently
        # callable. Custom/injected runners keep their existing serial contract.
        from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
        from guard.runtime.bounded import capture_pipes
        parallel=(type(docker) is KubernetesLifecycleReader and not docker.injected
            and docker.runner is capture_pipes and read_file==docker.read_file
            and image_check==docker.image_check and len(files)>1
            and len(docker.node_image_receipts)+len(files)<=64)
        if parallel:
            readers=[]
            for _ in files:
                reader=copy(docker)
                # Immutable binding/programs/deadline remain identical. Every
                # per-file owner/image/context/second-read still runs in full.
                reader.rows=[];reader.gap=False;reader._parent_cleanup_failures=0
                reader.node_image_receipts=[];reader.node_image_proof_failures=0
                reader.node_image_failure_codes=set()
                readers.append(reader)
            with ThreadPoolExecutor(max_workers=min(4,len(files))) as pool:
                captured=list(pool.map(read_recording,zip(files,readers)))
            # Input order controls records, references and transport diagnostics,
            # regardless of worker completion order. Census runs after all joins.
            for row,reader in captured:
                result['recordings'].append(row)
                docker.rows.extend(reader.rows);docker.gap|=reader.gap
                docker._parent_cleanup_failures+=reader._parent_cleanup_failures
                docker.node_image_receipts.extend(reader.node_image_receipts)
                docker.node_image_proof_failures+=reader.node_image_proof_failures
                codes=getattr(docker,'node_image_failure_codes',set())
                codes.update(reader.node_image_failure_codes);docker.node_image_failure_codes=codes
        else:
            result['recordings']=[read_recording((file,docker))[0] for file in files]
    try:result["after"]=inventory(docker,target,**arguments)
    except Exception:result["diagnostic_codes"].append("LIFECYCLE_FINAL_INVENTORY_UNAVAILABLE")
    after=result["after"]
    if before is not None and after is not None:
        result["live_census_stable"]=(bool(_complete(before) and _complete(after))
            and before["owner"]==after["owner"] and before["metadata"]==after["metadata"])
        if checkpoint:
            from .lifecycle_checkpoint import census
            result['live_census_stable']=(bool(_complete(before) and _complete(after))
                and before['owner']==after['owner'] and census(before['metadata'])==census(after['metadata']))
    if not result["live_census_stable"]:result["diagnostic_codes"].append("LIFECYCLE_SESSION_CENSUS_UNVERIFIED")
    result["recorded_files_covered"]=(result["live_census_stable"] and bool(result["recordings"])
        and all(r["capture"] is not None and r["session_owner_match"] and r["listed_file_identity_match"] for r in result["recordings"]))
    if before is not None:
        recorded={(r["pid"],r["start_ticks"]) for r in result["recordings"] if r["capture"] is not None}
        result["python_processes_without_recording"]=[{"pid":p["pid"],"start_ticks":p["start_ticks"]}
            for p in before["metadata"]["processes"] if p["same_executable"] and (p["pid"],p["start_ticks"]) not in recorded]
    if rolling:
        from .lifecycle_rolling import active_for
        result['rolling_history_evicted']=any(
            (row.get('segment') or 0)>0 for row in result['recordings']) and not any(row.get('segment')==0 for row in result['recordings'])
    result["request_index"]=request_index(result["recordings"])
    result["route_authentication_index"]=vllm_route_authentication.index(result["recordings"])
    result["route_handler_index"]=vllm_route_authentication.handler_index(result["recordings"])
    result["request_selection_index"]=vllm_route_authentication.selection_index(result["recordings"])
    return result
