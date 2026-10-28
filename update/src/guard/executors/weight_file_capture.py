"""Bounded owned regular-file hashing; no model/tensor import or file mutation."""
from __future__ import annotations

import hashlib
import re
import time

from .owned_capture import identity, strict_json
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader, KubernetesCaptureError, supervised_source

MAX_FILE_BYTES=1024**3
MAX_PACKET_BYTES=8192
HASH_READER=r'''
import hashlib,json,os,stat,sys
from pathlib import Path,PurePosixPath
root,relative,limit=sys.argv[1:];limit=int(limit)
parts=PurePosixPath(relative).parts
if (not root.startswith('/') or root=='/' or PurePosixPath(root).as_posix()!=root
 or '..' in PurePosixPath(root).parts or not parts or relative.startswith('/')
 or PurePosixPath(relative).as_posix()!=relative or any(p in {'.','..'} for p in parts)
 or not 1<=limit<=1073741824):raise ValueError()
def directory(path):
 fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
 try:
  for part in PurePosixPath(path).parts[1:]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd);os.close(fd);fd=nxt
  return fd
 except BaseException:os.close(fd);raise
def info(value):
 return dict(device=value.st_dev,inode=value.st_ino,size=value.st_size,mtime_ns=value.st_mtime_ns,ctime_ns=value.st_ctime_ns)
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
root_fd=directory(root)
try:
 logical=Path(root)/relative;logical_before=info(logical.lstat())
 resolved=logical.resolve(strict=True);selected=resolved.relative_to(Path(root))
 resolved_before=info(resolved.stat());root_before=info(os.stat(root));root_opened=info(os.fstat(root_fd))
 parent=os.dup(root_fd)
 try:
  for part in selected.parts[:-1]:
   nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=nxt
  fd=os.open(selected.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent)
 finally:os.close(parent)
 try:
  before=info(os.fstat(fd))
  if not stat.S_ISREG(os.fstat(fd).st_mode) or before['size']>limit:raise ValueError()
  h=hashlib.sha256();count=0
  while True:
   chunk=os.read(fd,min(1048576,limit-count+1))
   if not chunk:break
   count+=len(chunk)
   if count>limit:raise ValueError()
   h.update(chunk)
  try:after=info(os.fstat(fd))
  except OSError:after=None
 finally:os.close(fd)
 binding=False
 try:binding=(logical.resolve(strict=True)==resolved and info(logical.lstat())==logical_before
  and info(resolved.stat())==resolved_before==before==after
  and info(os.stat(root))==root_before==root_opened)
 except (OSError,ValueError,RuntimeError):pass
 packet=dict(schema='guard-weight-file-hash/1',path_sha256=digest(str(logical)),resolved_path_sha256=digest(str(resolved)),
  content_sha256=h.hexdigest(),bytes_read=count,file_before=before,file_after=after,
  regular_file=True,nofollow_resolved_components=True,source_complete=True,
  file_identity_stable=before==after and count==before['size'],logical_binding_stable=binding)
 print(json.dumps(packet,sort_keys=True,separators=(',',':')))
finally:os.close(root_fd)
'''


def validate_packet(result,expected_path_sha,max_bytes):
    pipe=result.capture or {}
    if (result.returncode!=0 or pipe.get("complete") is not True or pipe.get("bounded_at_source") is not True
            or pipe.get("timed_out") is not False or pipe.get("truncated") is not False):
        raise ValueError("WEIGHT_FILE_TRANSPORT_INCOMPLETE")
    row=strict_json(result.stdout.encode(),limit=MAX_PACKET_BYTES)
    return validate_packet_body(row,expected_path_sha,max_bytes)


def validate_packet_body(row,expected_path_sha,max_bytes):
    fields={"schema","path_sha256","resolved_path_sha256","content_sha256","bytes_read","file_before","file_after",
        "regular_file","nofollow_resolved_components","source_complete","file_identity_stable","logical_binding_stable"}
    if type(row) is not dict or set(row)!=fields or row["schema"]!="guard-weight-file-hash/1":raise ValueError("WEIGHT_FILE_PACKET_INVALID")
    if row["path_sha256"]!=expected_path_sha or any(type(row[k]) is not str or re.fullmatch(r"[0-9a-f]{64}",row[k]) is None
            for k in ("path_sha256","resolved_path_sha256","content_sha256")):
        raise ValueError("WEIGHT_FILE_PACKET_HASH_INVALID")
    stat_fields={"device","inode","size","mtime_ns","ctime_ns"}
    for key in ("file_before","file_after"):
        value=row[key]
        if key=="file_after" and value is None:continue
        if type(value) is not dict or set(value)!=stat_fields or any(type(v) is not int or v<0 or v>=2**63 for v in value.values()):
            raise ValueError("WEIGHT_FILE_STAT_INVALID")
    if (type(row["bytes_read"]) is not int or not 0<=row["bytes_read"]<=max_bytes or row["file_before"]["size"]>max_bytes
            or any(type(row[k]) is not bool for k in ("regular_file","nofollow_resolved_components","source_complete","file_identity_stable","logical_binding_stable"))
            or not row["regular_file"] or not row["nofollow_resolved_components"] or not row["source_complete"]
            or row["file_identity_stable"] and (row["file_before"]!=row["file_after"] or row["bytes_read"]!=row["file_before"]["size"])):
        raise ValueError("WEIGHT_FILE_PACKET_INCONSISTENT")
    return row


class DockerWeightReader:
    def __init__(self,client,target,*,duration=180):
        self.client,self.target=client,target
        self.deadline=time.monotonic()+duration

    def read(self,container,image,root,relative,max_bytes):
        deadline=min(self.deadline,time.monotonic()+5)
        def remaining():
            value=deadline-time.monotonic()
            if value<=0:raise ValueError("WEIGHT_FILE_DEADLINE")
            return value
        owner=identity(self.client,self.target,container,image,remaining())
        result=self.client.exec_bounded(container,["python3","-I","-S","-B","-c",supervised_source(HASH_READER),
            repr(remaining()),root,relative,str(max_bytes)],timeout=remaining(),max_bytes=MAX_PACKET_BYTES)
        packet=validate_packet(result,hashlib.sha256((root+"/"+relative).encode()).hexdigest(),max_bytes)
        try:stable=identity(self.client,self.target,container,image,remaining())==owner
        except Exception:stable=False
        return packet,owner,stable


class KubernetesWeightReader(KubernetesLifecycleReader):
    def __init__(self,target,env,prepared,root,files,max_bytes,**kwargs):
        super().__init__(target,env,prepared,**kwargs)
        self.root,self.files,self.max_bytes=root,frozenset(files),max_bytes
        self.programs={"weight_file":HASH_READER}
        self.wrapped={"weight_file":supervised_source(HASH_READER)}

    def _program(self,container,argv,max_bytes):
        if (container!=self.binding["engine"]["container"] or type(argv) is not list or len(argv)!=6
                or any(type(v) is not str for v in argv)
                or argv[:3]!=["python3","-c",HASH_READER] or argv[3]!=self.root or argv[4] not in self.files
                or argv[5]!=str(self.max_bytes) or max_bytes!=MAX_PACKET_BYTES):
            raise KubernetesCaptureError()
        return "weight_file"

    def read(self,container,image,root,relative,max_bytes):
        if root!=self.root or max_bytes!=self.max_bytes or image!=self.binding["engine"]["image"]:raise KubernetesCaptureError()
        result=self.exec_bounded(container,["python3","-c",HASH_READER,root,relative,str(max_bytes)],timeout=5,max_bytes=MAX_PACKET_BYTES)
        packet=validate_packet(result,hashlib.sha256((root+"/"+relative).encode()).hexdigest(),max_bytes)
        return packet,result.owner_before,result.owner_binding_verified

    def report(self):
        return {**super().report(),"profile":"kubernetes-owned-weight-file-read-v1"}
