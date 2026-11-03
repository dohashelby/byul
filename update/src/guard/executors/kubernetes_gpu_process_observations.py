"""B-owned passive GPU/process facts for the existing PLT-01/04 consumers.

These facts are deliberately outside the A process-v1 packet and rule-table
observations. An open FD and an NVML PID are not a model-worker roster.
"""
from __future__ import annotations

import re

from guard.models import sha256_hex, utcnow
from guard.runtime.kubernetes_process import PROFILE as PROCESS_PROFILE

PROFILE = "kubernetes-b-gpu-process-passive-v1"
SCHEMA = "guard-b-gpu-process-passive/1"
SOURCE_SHA256 = "c6c0f0703207e73463f02ead1a35610e35f19ca7a307c877f7ae6ecb915f2a8c"

SOURCE = r'''
import hashlib,json,os,re,signal,stat,subprocess,sys,time
if hasattr(signal,'alarm'):signal.alarm(8)
spec=json.loads(sys.argv[1]);root=spec['proc_root']
if type(root) is not str or not os.path.isabs(root) or '..' in root.split(os.sep) or len(root)>256:raise SystemExit(64)
if type(spec['pids']) is not list or len(spec['pids'])>128:raise SystemExit(65)
if type(spec['candidates']) is not list or len(spec['candidates'])>64:raise SystemExit(66)
class ScopeChanged(ValueError):pass
def digest(raw):return hashlib.sha256(raw).hexdigest()
def stat_ticks(pid):
 raw=open(root+'/'+str(pid)+'/stat','rb').read(8192).decode('utf-8','replace')
 return int(raw[raw.rfind(')')+2:].split()[19])
def namespace(pid,name):return os.stat(root+'/'+str(pid)+'/ns/'+name).st_ino
def status(pid):
 raw=open(root+'/'+str(pid)+'/status','rb').read(16384).decode('utf-8','replace')
 vals={}
 for line in raw.splitlines():
  if line.startswith(('NSpid:','Uid:')):
   key,value=line.split(':',1);vals[key]=[int(x) for x in value.split()]
 return vals
def snapshot(pid):
 s=status(pid)
 return {'start_ticks':stat_ticks(pid),'pid_namespace_inode':namespace(pid,'pid'),
         'mount_namespace_inode':namespace(pid,'mnt'),'uids':s.get('Uid'),
         'nspid':s.get('NSpid',[])}
rows=[]
for expected in spec['pids']:
 pid=expected['pid'];row={'pid':pid,'start_ticks':expected['start_ticks'],
  'pid_identity_stable':False,'uid_effective':None,'pid_namespace_inode':None,
  'mount_namespace_inode':None,'nspid':[],'cgroup_sha256':None,
  'device_fds':[],'credential_fds':[],'diagnostic_codes':[]}
 try:
  if type(pid) is not int or not 1<=pid<=2147483647 or pid==os.getpid():raise ValueError()
  before=snapshot(pid)
  if (before['start_ticks']!=expected['start_ticks']
      or before['mount_namespace_inode']!=expected['mount_namespace_inode']
      or before['pid_namespace_inode']!=expected['pid_namespace_inode']
      or not before['uids'] or len(before['uids'])!=4
      or before['uids'][1]!=expected['uid_effective']):raise ScopeChanged()
  row['uid_effective']=before['uids'][1] if before['uids'] and len(before['uids'])==4 else None
  row['pid_namespace_inode']=before['pid_namespace_inode'];row['mount_namespace_inode']=before['mount_namespace_inode']
  row['nspid']=before['nspid'][:8]
  try:row['cgroup_sha256']=digest(open(root+'/'+str(pid)+'/cgroup','rb').read(8192))
  except (OSError,ValueError):row['diagnostic_codes'].append('CGROUP_UNAVAILABLE')
  names=os.listdir(root+'/'+str(pid)+'/fd')
  if len(names)>256:row['diagnostic_codes'].append('FD_SET_BOUND')
  for name in sorted(names,key=lambda x:int(x) if x.isdecimal() else 2**63)[:256]:
   if not re.fullmatch('[0-9]{1,9}',name):continue
   path=root+'/'+str(pid)+'/fd/'+name
   try:
    link=os.readlink(path);st=os.stat(path)
    info=open(root+'/'+str(pid)+'/fdinfo/'+name,'rb').read(4096).decode('ascii','replace')
    flags=next((int(x.split()[1],8) for x in info.splitlines() if x.startswith('flags:')),None)
    if flags is None:raise ValueError()
    after_link=os.readlink(path);after_fd=os.stat(path)
    if link!=after_link or (st.st_dev,st.st_ino,st.st_mode,st.st_rdev)!=(after_fd.st_dev,after_fd.st_ino,after_fd.st_mode,after_fd.st_rdev):raise ValueError()
   except (OSError,ValueError):
    if 'FD_GONE_OR_UNREADABLE' not in row['diagnostic_codes']:row['diagnostic_codes'].append('FD_GONE_OR_UNREADABLE')
    continue
   mode=flags&os.O_ACCMODE
   metadata_only=bool(flags&getattr(os,'O_PATH',0))
   if stat.S_ISCHR(st.st_mode) and re.fullmatch('/dev/(?:nvidia(?:[0-9]+|ctl|-uvm(?:-tools)?|-caps/nvidia-cap[0-9]+)|dri/renderD[0-9]+)',link):
    row['device_fds'].append({'path':link,'major':os.major(st.st_rdev),'minor':os.minor(st.st_rdev),
                              'read_capable':not metadata_only and mode in (os.O_RDONLY,os.O_RDWR),
                              'write_capable':not metadata_only and mode in (os.O_WRONLY,os.O_RDWR),
                              'metadata_only':metadata_only})
   if stat.S_ISREG(st.st_mode):
    for candidate in spec['candidates']:
     if (candidate['device']==st.st_dev and candidate['inode']==st.st_ino
         and candidate['mount_namespace_inode']==before['mount_namespace_inode']):
      row['credential_fds'].append({'path':candidate['path'],
       'read_capable':not metadata_only and mode in (os.O_RDONLY,os.O_RDWR),
       'metadata_only':metadata_only})
  after=snapshot(pid)
  row['pid_identity_stable']=before==after and not row['uid_effective'] is None
  if not row['pid_identity_stable']:
   row['diagnostic_codes'].append('PID_IDENTITY_CHANGED')
   row['device_fds']=[];row['credential_fds']=[]
 except ScopeChanged:
  row['diagnostic_codes'].append('PID_REUSED_OR_SCOPE_CHANGED')
  row['device_fds']=[];row['credential_fds']=[]
 except Exception:
  row['diagnostic_codes'].append('PID_READ_UNAVAILABLE')
  row['device_fds']=[];row['credential_fds']=[]
 rows.append(row)
nvml={'available':False,'rows':[],'diagnostic_code':'NVML_UNAVAILABLE'}
try:
 result=subprocess.run(['nvidia-smi','--query-compute-apps=pid,gpu_uuid','--format=csv,noheader,nounits'],
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=2,check=False)
 raw=result.stdout[:8193]
 if result.returncode==0 and len(raw)<=8192:
  parsed=[]
  for line in raw.decode('ascii').splitlines():
   fields=[x.strip() for x in line.split(',')]
   if len(fields)!=2 or re.fullmatch('[0-9]{1,10}',fields[0]) is None or re.fullmatch('GPU-[0-9a-fA-F-]{8,80}',fields[1]) is None:raise ValueError()
   parsed.append({'pid':int(fields[0]),'gpu_uuid':fields[1]})
  if len(parsed)>64:raise ValueError()
  nvml={'available':True,'rows':parsed,'diagnostic_code':None}
except (OSError,ValueError,subprocess.TimeoutExpired,UnicodeError):pass
print(json.dumps({'schema':'guard-b-gpu-process-passive/1','rows':rows,'nvml':nvml},
 sort_keys=True,separators=(',',':')))
'''.strip()


def _empty(code: str) -> dict:
    return {"profile": PROFILE, "schema": SCHEMA, "source_sha256": SOURCE_SHA256,
            "started_at": utcnow().isoformat(), "finished_at": utcnow().isoformat(),
            "binding_stable": False, "rows": [], "nvml": {"available": False, "rows": [],
            "diagnostic_code": "NVML_UNAVAILABLE"}, "nvml_joins": [],
            "diagnostic_codes": [code], "worker_subject_verified": False,
            "worker_inventory_complete": False, "device_access_tested": False,
            "candidate_read_tested": False, "provider_scope_verified": False,
            "scope": "selected container processes; no model-worker classification or full GPU access scope"}


def _inputs(record: dict) -> tuple[list[dict], list[dict]]:
    packet = record.get("packet")
    if (record.get("profile") != PROCESS_PROFILE or record.get("available") is not True
            or record.get("binding_verified_before_after") is not True
            or type(packet) is not dict or packet.get("worker_subject_verified") is not False
            or packet.get("worker_inventory_complete") is not False
            or packet.get("capture_process_excluded") is not True
            or packet.get("capture_subject", {}).get("identity_stable") is not True):
        raise ValueError("B_GPU_PROCESS_V1_INPUT_UNAVAILABLE")
    subject = packet["capture_subject"]
    rows = [{"pid": row["pid"], "start_ticks": row["start_ticks"],
             "uid_effective": row["uid_effective"],
             "mount_namespace_inode": row["mount_namespace_inode"],
             "pid_namespace_inode": subject["pid_namespace_inode"]}
            for row in packet["processes"] if row["pid_identity_stable"]
            and row["start_ticks"] is not None and row["uid_effective"] is not None
            and row["mount_namespace_inode"] is not None]
    candidates = []
    for row in packet["candidate_metadata"]:
        meta = row["metadata"]
        if row["state"] == "metadata" and row["metadata_identity_stable"] and meta is not None:
            candidates.append({"path": row["path"], "device": meta["device"], "inode": meta["inode"],
                               "mount_namespace_inode": subject["mount_namespace_inode"]})
    return rows, candidates


def _join_nvml(rows: list[dict], nvml: dict) -> list[dict]:
    joins = []
    for item in nvml["rows"]:
        # NVML may report host PIDs while /proc is container-local. Equal
        # numbers in different namespaces can collide. Require the kernel's
        # explicit ancestor->container NSpid chain; a single PID is unjoined.
        matching = [row for row in rows if row["pid_identity_stable"]
                    and len(row["nspid"]) >= 2 and row["nspid"][0] == item["pid"]
                    and row["nspid"][-1] == row["pid"]]
        joins.append({"nvml_pid": item["pid"], "gpu_uuid": item["gpu_uuid"],
                      "process_pid": matching[0]["pid"] if len(matching) == 1 else None,
                      "joined": len(matching) == 1,
                      "namespace_chain_verified": len(matching) == 1})
    return joins


def _decode(raw: dict, pids: list[dict], candidates: list[dict]) -> tuple[list[dict], dict]:
    if (type(raw) is not dict or set(raw) != {"schema", "rows", "nvml"}
            or raw["schema"] != SCHEMA or type(raw["rows"]) is not list
            or len(raw["rows"]) != len(pids) or type(raw["nvml"]) is not dict):
        raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
    allowed_diagnostics = {"CGROUP_UNAVAILABLE", "FD_SET_BOUND", "FD_GONE_OR_UNREADABLE",
                           "PID_IDENTITY_CHANGED", "PID_READ_UNAVAILABLE", "PID_REUSED_OR_SCOPE_CHANGED"}
    candidate_paths = {row["path"] for row in candidates}
    rows = []
    for expected, row in zip(pids, raw["rows"]):
        if (type(row) is not dict or set(row) != {"pid", "start_ticks", "pid_identity_stable",
                "uid_effective", "pid_namespace_inode", "mount_namespace_inode", "nspid",
                "cgroup_sha256", "device_fds", "credential_fds", "diagnostic_codes"}
                or row["pid"] != expected["pid"] or row["start_ticks"] != expected["start_ticks"]
                or type(row["pid_identity_stable"]) is not bool
                or row["uid_effective"] not in (None, expected["uid_effective"])
                or row["pid_namespace_inode"] not in (None, expected["pid_namespace_inode"])
                or row["mount_namespace_inode"] not in (None, expected["mount_namespace_inode"])
                or type(row["nspid"]) is not list or len(row["nspid"]) > 8
                or any(type(pid) is not int or not 1 <= pid <= 2147483647 for pid in row["nspid"])
                or row["cgroup_sha256"] is not None and
                (type(row["cgroup_sha256"]) is not str or re.fullmatch("[0-9a-f]{64}", row["cgroup_sha256"]) is None)
                or type(row["device_fds"]) is not list or len(row["device_fds"]) > 256
                or type(row["credential_fds"]) is not list or len(row["credential_fds"]) > 256
                or type(row["diagnostic_codes"]) is not list
                or len(set(row["diagnostic_codes"])) != len(row["diagnostic_codes"])
                or any(code not in allowed_diagnostics for code in row["diagnostic_codes"])):
            raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
        if row["pid_identity_stable"] is False and (row["device_fds"] or row["credential_fds"]):
            raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
        if row["pid_identity_stable"] is True and (
                row["uid_effective"] != expected["uid_effective"]
                or row["pid_namespace_inode"] != expected["pid_namespace_inode"]
                or row["mount_namespace_inode"] != expected["mount_namespace_inode"]):
            raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
        for fd in row["device_fds"]:
            if (type(fd) is not dict or set(fd) != {"path", "major", "minor", "read_capable", "write_capable",
                                                  "metadata_only"}
                    or type(fd["path"]) is not str or re.fullmatch(
                        r"/dev/(?:nvidia(?:[0-9]+|ctl|-uvm(?:-tools)?|-caps/nvidia-cap[0-9]+)|dri/renderD[0-9]+)", fd["path"]) is None
                    or any(type(fd[key]) is not int or fd[key] < 0 for key in ("major", "minor"))
                    or any(type(fd[key]) is not bool for key in ("read_capable", "write_capable", "metadata_only"))
                    or fd["metadata_only"] and (fd["read_capable"] or fd["write_capable"])):
                raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
        for fd in row["credential_fds"]:
            if (type(fd) is not dict or set(fd) != {"path", "read_capable", "metadata_only"}
                    or fd["path"] not in candidate_paths
                    or type(fd["read_capable"]) is not bool or type(fd["metadata_only"]) is not bool
                    or fd["metadata_only"] and fd["read_capable"]):
                raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
        rows.append(row)
    nvml = raw["nvml"]
    if (set(nvml) != {"available", "rows", "diagnostic_code"}
            or type(nvml["available"]) is not bool or type(nvml["rows"]) is not list
            or len(nvml["rows"]) > 64 or nvml["diagnostic_code"] not in (None, "NVML_UNAVAILABLE")
            or (nvml["available"] is False) != (nvml["diagnostic_code"] == "NVML_UNAVAILABLE")
            or nvml["available"] is False and nvml["rows"]):
        raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
    for item in nvml["rows"]:
        if (type(item) is not dict or set(item) != {"pid", "gpu_uuid"}
                or type(item["pid"]) is not int or not 1 <= item["pid"] <= 2147483647
                or type(item["gpu_uuid"]) is not str
                or re.fullmatch(r"GPU-[0-9a-fA-F-]{8,80}", item["gpu_uuid"]) is None):
            raise ValueError("B_GPU_PROCESS_PACKET_INVALID")
    return rows, nvml


def collect(record: dict, transport) -> dict:
    """Keep the public-contract dependency explicit; never bypass A's registry.

    The fixed SOURCE and decoder remain directly CPU-testable. Live execution
    requires an A-owned public bounded metadata contract, not private transport
    fields or mutation of another consumer's model_snapshots.
    """
    result = _empty("B_GPU_PROCESS_PUBLIC_CONTRACT_UNAVAILABLE")
    result["unresolved_credential_candidates"] = []
    result["availability_reason"] = "A_PUBLIC_BOUNDED_PROCESS_METADATA_CONTRACT_PENDING"
    try:
        pids, candidates = _inputs(record)
        result["selected_process_count"] = len(pids)
        result["metadata_candidate_count"] = len(candidates)
        result["unresolved_credential_candidates"] = [
            {"path": row["path"], "state": "symlink_not_followed",
             "diagnostic_code": "CREDENTIAL_CANDIDATE_SYMLINK_UNRESOLVED"}
            for row in record["packet"]["candidate_metadata"]
            if row["state"] == "symlink_not_followed"]
        if result["unresolved_credential_candidates"]:
            result["diagnostic_codes"].append("CREDENTIAL_CANDIDATE_SYMLINK_UNRESOLVED")
    except (KeyError, TypeError, ValueError):
        result["diagnostic_codes"].append("B_GPU_PROCESS_V1_INPUT_UNAVAILABLE")
    result["finished_at"] = utcnow().isoformat()
    return result

__all__ = ["PROFILE", "SCHEMA", "SOURCE_SHA256", "collect"]
