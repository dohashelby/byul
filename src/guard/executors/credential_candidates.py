"""Bounded credential-path metadata, never credential file contents.

The policy identifies candidate paths; a readable candidate does not establish
that its contents are credentials, nor that a credential was successfully used.
"""
from __future__ import annotations

import json
from guard.credential_policy import MAX_PATHS, MAX_PATH_BYTES, candidate_paths

MAX_OUTPUT_BYTES = 8192
PROBE_TIMEOUT = 10
PROFILE = "credential-candidate-metadata-v1"

# Only /proc metadata is read. Candidate files are lstat/access subjects and
# are never opened; path arguments are JSON argv, never shell interpolation.
METADATA_PROBE = r'''import hashlib,json,os,stat,sys,time
paths=json.loads(sys.argv[1]); deadline=time.monotonic()+8
if (type(paths) is not list or len(paths)>32 or len(set(paths))!=len(paths)
    or any(type(p) is not str or not p.startswith('/') or len(p.encode())>128
           or any(part in ('.','..') for part in p.split('/')) for p in paths)):
    raise ValueError('invalid bounded metadata request')
def budget():
    if time.monotonic()>deadline: raise TimeoutError('metadata budget')
def metadata_text(path):
    budget()
    with open(path,'r',encoding='utf-8') as stream: value=stream.read(8193)
    if len(value)>8192: raise ValueError('metadata limit')
    return value
def identity(pid):
    rows={line.split(':',1)[0]:line.split(':',1)[1].strip()
          for line in metadata_text('/proc/'+str(pid)+'/status').splitlines() if ':' in line}
    root=os.stat('/proc/'+str(pid)+'/root')
    fields={key:rows[key] for key in ('Uid','Gid','Groups','CapEff','CapPrm','NoNewPrivs','Seccomp')}
    fields.update(mount_namespace=os.readlink('/proc/'+str(pid)+'/ns/mnt'),
                  user_namespace=os.readlink('/proc/'+str(pid)+'/ns/user'),
                  root=[root.st_dev,root.st_ino],
                  lsm=metadata_text('/proc/'+str(pid)+'/attr/current').strip())
    if not fields['lsm']: raise ValueError('subject context unavailable')
    start=metadata_text('/proc/'+str(pid)+'/stat').rsplit(')',1)[1].split()[19]
    return fields,start
def subjects():
    budget(); own,_=identity(os.getpid())
    pids=sorted(int(p) for p in os.listdir('/proc') if p.isdigit() and int(p)!=os.getpid())
    if not 0<len(pids)<=128: raise ValueError('process inventory unavailable')
    records=[]; matched=True
    for pid in pids:
        worker,start=identity(pid); matched=matched and worker==own
        records.append([pid,start,worker])
    digest=hashlib.sha256(json.dumps([own,records],sort_keys=True).encode()).hexdigest()
    return matched,len(pids),digest
def mark(info):
    return [info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_gid,info.st_mtime_ns,info.st_ctime_ns]
def path_metadata(path):
    budget(); fd=os.open('/',os.O_PATH|os.O_DIRECTORY); chain=[]
    try:
        parts=path.strip('/').split('/')
        if not parts or parts==['']: return 'unknown','DIRECTORY_SCOPE_UNVERIFIED',chain
        for index,part in enumerate(parts):
            budget()
            try: info=os.stat(part,dir_fd=fd,follow_symlinks=False)
            except FileNotFoundError: return 'absent','PATH_ABSENT',chain
            except PermissionError: return 'unknown','PATH_METADATA_UNAVAILABLE',chain
            chain.append(mark(info))
            if stat.S_ISLNK(info.st_mode): return 'unknown','SYMLINK_NOT_FOLLOWED',chain
            if index<len(parts)-1:
                if not stat.S_ISDIR(info.st_mode): return 'absent','PARENT_NOT_DIRECTORY',chain
                child=os.open(part,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                if mark(os.fstat(child))!=mark(info):
                    os.close(child); return 'unknown','PATH_IDENTITY_CHANGED',chain
                os.close(fd); fd=child
            elif not stat.S_ISREG(info.st_mode):
                return 'unknown','NONREGULAR_SCOPE_UNVERIFIED',chain
            else:
                readable=os.access(part,os.R_OK,dir_fd=fd,effective_ids=True,follow_symlinks=False)
                if mark(os.stat(part,dir_fd=fd,follow_symlinks=False))!=mark(info):
                    return 'unknown','PATH_IDENTITY_CHANGED',chain
                return ('candidate_readable' if readable else 'denied'),'EFFECTIVE_READ_PERMISSION',chain
    except PermissionError: return 'unknown','PATH_METADATA_UNAVAILABLE',chain
    finally: os.close(fd)
results={}; before=None; after=None; errors=[]
try: before=subjects()
except Exception: errors.append('WORKER_SUBJECT_UNVERIFIED')
for path in paths:
    try:
        first=path_metadata(path); second=path_metadata(path)
        state,basis,_=first
        results[path]={'state':state,'basis':basis,'stable':first==second}
    except Exception:
        results[path]={'state':'unknown','basis':'PATH_METADATA_UNAVAILABLE','stable':False}
try: after=subjects()
except Exception: errors.append('WORKER_SUBJECT_UNVERIFIED')
same=before is not None and after is not None and before==after
out={'schema':'guard-credential-metadata/1','paths':results,
     'worker_subject_matches':same and before[0] is True,
     'worker_inventory_stable':same,'workers_examined':before[1] if before else 0,
     'errors':sorted(set(errors))}
encoded=json.dumps(out,separators=(',',':')).encode()
if len(encoded)>8191:
    encoded=json.dumps({'schema':'guard-credential-metadata/1','error':'METADATA_OUTPUT_LIMIT'}).encode()
sys.stdout.buffer.write(encoded+b'\n')
'''


def shadowed_paths(paths, destinations):
    """Conservatively withhold conclusions where nested mounts hide a tree."""
    def contains(parent, child):
        return parent == child or child.startswith(parent.rstrip("/") + "/")

    return {path for path in paths
            if any(left != right and contains(left, right)
                   and (contains(right, path) or contains(path, right))
                   for left in destinations for right in destinations)}


def decode_metadata(result, paths):
    """Accept only complete bounded metadata from the fixed read-only probe."""
    unknown = {path: "unknown" for path in paths}
    detail = {"profile": PROFILE, "code": "CANDIDATE_METADATA_UNAVAILABLE"}
    capture = getattr(result, "capture", None)
    raw = getattr(result, "stdout_bytes", None)
    if (result.returncode != 0 or type(capture) is not dict
            or capture.get("bounded_at_source") is not True or capture.get("complete") is not True
            or capture.get("truncated") is not False or capture.get("timed_out") is not False
            or type(raw) is not bytes or len(raw) > MAX_OUTPUT_BYTES):
        return unknown, detail
    try:
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError):
        return unknown, detail
    if (type(value) is not dict or value.get("schema") != "guard-credential-metadata/1"
            or value.get("worker_subject_matches") is not True
            or value.get("worker_inventory_stable") is not True
            or type(value.get("workers_examined")) is not int or not 1 <= value["workers_examined"] <= 128
            or value.get("errors") != [] or type(value.get("paths")) is not dict
            or set(value["paths"]) != set(paths)):
        return unknown, detail
    observed, proofs = {}, {}
    allowed = {"absent": {"PATH_ABSENT", "PARENT_NOT_DIRECTORY"},
               "denied": {"EFFECTIVE_READ_PERMISSION"},
               "candidate_readable": {"EFFECTIVE_READ_PERMISSION"},
               "unknown": {"DIRECTORY_SCOPE_UNVERIFIED", "SYMLINK_NOT_FOLLOWED",
                           "PATH_IDENTITY_CHANGED", "NONREGULAR_SCOPE_UNVERIFIED",
                           "PATH_METADATA_UNAVAILABLE"}}
    for path, record in value["paths"].items():
        if (type(record) is not dict or record.get("stable") is not True
                or type(record.get("state")) is not str or record["state"] not in allowed
                or type(record.get("basis")) is not str or record["basis"] not in allowed[record["state"]]):
            observed[path], proofs[path] = "unknown", "PATH_METADATA_UNVERIFIED"
        else:
            observed[path], proofs[path] = record["state"], record["basis"]
    return observed, {"profile": PROFILE, "code": "WORKER_SUBJECT_METADATA_VERIFIED",
                      "workers_examined": value["workers_examined"], "path_basis": proofs,
                      "scope": "effective read permission only; no file content, credential validity or use observed"}
