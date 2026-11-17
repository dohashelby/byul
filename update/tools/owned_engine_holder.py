"""Keep an owned lab PID namespace observable after its engine child exits.

The holder never restarts the engine, sends worker signals or removes resources.
It must be launched as PID 1 in a freshly owned lab container. Its records do
not establish engine/GPU resource recovery; census and next-launch tests do that.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

ROOT=Path('/guard-rank-ipc/data')
MAX_BINDING=32768


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def _identity(status):
    return status.st_dev,status.st_ino,status.st_mode,status.st_uid,status.st_size,status.st_mtime_ns,status.st_ctime_ns


def read_launch(path,expected_pod_uid,*,root=ROOT):
    path=Path(path);root=Path(root)
    if (path.name!='engine-launch.json' or path.parent.parent!=root
            or re.fullmatch('[0-9a-f]{32}',path.parent.name)is None):
        raise ValueError('ENGINE_HOLDER_BINDING_PATH_INVALID')
    parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    directory=None;fd=None
    try:
        directory=os.open(path.parent.name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent)
        holder=os.fstat(directory)
        if holder.st_uid!=os.geteuid() or stat.S_IMODE(holder.st_mode)!=0o700:
            raise ValueError('ENGINE_HOLDER_DIRECTORY_INVALID')
        fd=os.open(path.name,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW,dir_fd=directory)
        before=os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.geteuid()
                or stat.S_IMODE(before.st_mode)!=0o600 or not 1<=before.st_size<=MAX_BINDING):
            raise ValueError('ENGINE_HOLDER_BINDING_FILE_INVALID')
        raw=os.read(fd,MAX_BINDING+1)
        after=os.fstat(fd);current=os.stat(path.name,dir_fd=directory,follow_symlinks=False)
        if len(raw)!=before.st_size or len({_identity(s)for s in(before,after,current)})!=1:
            raise ValueError('ENGINE_HOLDER_BINDING_CHANGED')
    finally:
        if fd is not None:os.close(fd)
        if directory is not None:os.close(directory)
        os.close(parent)
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('ENGINE_HOLDER_BINDING_DUPLICATE')
            result[key]=value
        return result
    value=json.loads(raw,object_pairs_hook=pairs)
    if (type(value)is not dict or set(value)!={'pod_uid','container_id','restart_count','argv'}
            or type(expected_pod_uid)is not str or not expected_pod_uid or value['pod_uid']!=expected_pod_uid
            or type(value['container_id'])is not str or re.fullmatch('[0-9a-f]{64}',value['container_id'])is None
            or type(value['restart_count'])is not int or not 0<=value['restart_count']<2**31
            or type(value['argv'])is not list or not 2<=len(value['argv'])<=96
            or value['argv'][:2]!=['vllm','serve']
            or any(type(arg)is not str or not arg or len(arg)>4096 or '\0'in arg for arg in value['argv'])):
        raise ValueError('ENGINE_HOLDER_BINDING_INVALID')
    return value,hashlib.sha256(raw).hexdigest()


def publish(directory,name,value):
    directory=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    backing='.holder-'+os.urandom(16).hex()+'.bin';fd=None;published=False
    try:
        fd=os.open(backing,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory)
        raw=canonical(value)
        if os.write(fd,raw)!=len(raw):raise OSError('ENGINE_HOLDER_PARTIAL_WRITE')
        os.fsync(fd);os.close(fd);fd=None
        os.link(backing,name,src_dir_fd=directory,dst_dir_fd=directory,follow_symlinks=False)
        published=True
    finally:
        if fd is not None:os.close(fd)
        if not published:
            try:os.unlink(backing,dir_fd=directory)
            except FileNotFoundError:pass
        os.close(directory)


def run(path,*,root=ROOT,max_seconds=7200,binding_wait_seconds=180):
    if type(max_seconds)is not int or not 1<=max_seconds<=7200:
        raise ValueError('ENGINE_HOLDER_LIFETIME_INVALID')
    if type(binding_wait_seconds)is not int or not 1<=binding_wait_seconds<=180:
        raise ValueError('ENGINE_HOLDER_BINDING_DEADLINE_INVALID')
    path=Path(path);started=time.monotonic();value=None
    while time.monotonic()-started<min(binding_wait_seconds,max_seconds):
        try:value,binding_sha=read_launch(path,os.environ.get('GUARD_POD_UID'),root=root);break
        except FileNotFoundError:time.sleep(.05)
    if value is None:raise ValueError('ENGINE_HOLDER_BINDING_TIMEOUT')
    stop=[]
    signal.signal(signal.SIGTERM,lambda number,frame:stop.append(number))
    signal.signal(signal.SIGINT,lambda number,frame:stop.append(number))
    environment={**os.environ,'GUARD_CONTAINER_ID':value['container_id'],'GUARD_RESTART_COUNT':str(value['restart_count'])}
    child=subprocess.Popen(value['argv'],env=environment,start_new_session=True)
    try:
        raw=Path('/proc/'+str(child.pid)+'/stat').read_bytes()
        engine_start_ticks=int(raw[raw.rfind(b')')+2:].split()[19])
    except (OSError,ValueError,IndexError):engine_start_ticks=None
    common={'schema':'guard-owned-engine-holder/1','binding_sha256':binding_sha,'holder_pid':os.getpid(),
        'engine_pid':child.pid,'engine_start_ticks':engine_start_ticks,
        'engine_restarted':False,'worker_signals_sent':False,'resources_removed':False,
        'resource_recovery_verified':False,'scope_complete':False}
    publish(path.parent,'holder-started.json',{**common,'observed_wall_ns':time.time_ns(),'observed_monotonic_ns':time.monotonic_ns()})
    recorded=False
    while not stop and time.monotonic()-started<max_seconds:
        code=child.poll()
        if code is not None and not recorded:
            publish(path.parent,'holder-engine-exit.json',{**common,'returncode':code,
                'observed_wall_ns':time.time_ns(),'observed_monotonic_ns':time.monotonic_ns()})
            recorded=True
        if recorded:
            # Only reap dead adopted children; never force cleanup of live
            # workers, sockets, SHM or GPU resources before the census.
            while True:
                try:
                    if os.waitpid(-1,os.WNOHANG)[0]==0:break
                except ChildProcessError:break
        time.sleep(.05)
    publish(path.parent,'holder-stopped.json',{**common,'reason':'operator_signal'if stop else'lifetime_expired',
        'engine_exit_observed':recorded,'observed_wall_ns':time.time_ns(),'observed_monotonic_ns':time.monotonic_ns()})
    # As container PID 1, exiting lets the container runtime dispose of the
    # namespace. That operator cleanup must not be counted as native recovery.
    return 0


if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise ValueError('ENGINE_HOLDER_ARGUMENTS_INVALID')
        raise SystemExit(run(Path(sys.argv[1])))
    except Exception:
        raise SystemExit('ENGINE_HOLDER_UNAVAILABLE')from None
