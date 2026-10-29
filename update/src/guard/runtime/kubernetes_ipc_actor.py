"""Standalone owned Linux IPC actor supervisor; public Kube transport is separate.

Only this helper's fork children change credentials or receive signals. The
immutable A2 probe sends no payload. No engine, Pod, RBAC or worker is modified.
This module has no runtime side effects on import and needs only the stdlib.
"""
from __future__ import annotations
import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import PurePosixPath
import re
import select
import signal
import socket
import stat
import sys
import time
import types

SCHEMA='guard-owned-unix-ipc-actor/1'
PROBE_SHA256='f096e9d95aed61315b4d129369cd273786fa9dc106ac264d48c670e004269ca2'
MAX_BYTES=65536
PROBE_SOURCE_B64=None  # Filled by the local reviewed program builder, never target input.
COMPILE_PROVENANCE=None


class Unavailable(Exception):pass
class Deadline(BaseException):pass


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode()


def pairs(items):
    result={}
    for key,value in items:
        if key in result:raise Unavailable()
        result[key]=value
    return result


def identity(item):
    return {'device':item.st_dev,'inode':item.st_ino,'uid':item.st_uid,'gid':item.st_gid,
        'mode':stat.S_IMODE(item.st_mode),'ctime_ns':item.st_ctime_ns}


def context():
    def read(name,limit):
        with open('/proc/self/'+name,'rb') as stream:raw=stream.read(limit+1)
        if len(raw)>limit:raise Unavailable()
        return raw
    fields=read('stat',4096).rsplit(b')',1)[1].split()
    lines=read('status',65536).decode('ascii').splitlines();data={}
    for name in ('Uid','Gid','Groups','CapEff','CapPrm','CapInh','CapAmb','NoNewPrivs'):
        row=next(line for line in lines if line.startswith(name+':')).split()[1:]
        data[name]=([int(v) for v in row] if name in {'Uid','Gid','Groups'} else int(row[0],16 if name.startswith('Cap') else 10))
    if len(data['Uid'])!=4 or len(data['Gid'])!=4 or len(data['Groups'])>128:raise Unavailable()
    namespaces={name:os.readlink('/proc/self/ns/'+name) for name in ('mnt','net','pid','user','time')}
    if any(re.fullmatch(re.escape(k)+r':\[[0-9]{1,20}\]',v) is None for k,v in namespaces.items()):raise Unavailable()
    with open('/proc/sys/kernel/random/boot_id','rb') as stream:boot=stream.read(129)
    if len(boot)>128:raise Unavailable()
    return {'pid':os.getpid(),'start_ticks':int(fields[19]),'uids':data['Uid'],'gids':data['Gid'],'groups':sorted(data['Groups']),
        'cap_effective':data['CapEff'],'cap_permitted':data['CapPrm'],'cap_inheritable':data['CapInh'],'cap_ambient':data['CapAmb'],
        'no_new_privs':data['NoNewPrivs'],'namespaces':namespaces,'boot_sha256':hashlib.sha256(boot).hexdigest(),
        'time_offsets_sha256':hashlib.sha256(read('timens_offsets',4096)).hexdigest()}


def prctl(option,value):
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(ctypes.c_int(option),ctypes.c_ulong(value),ctypes.c_ulong(0),ctypes.c_ulong(0),ctypes.c_ulong(0))!=0:raise Unavailable()


def restrict_actor(actor,parent_pid):
    before=context()
    if before['uids']!=[os.geteuid()]*4 or before['gids']!=[os.getegid()]*4:raise Unavailable()
    uid,gid,groups=actor['uid'],actor['gid'],actor['groups']
    if os.geteuid()!=0:
        if before['uids']!=[uid]*4 or before['gids']!=[gid]*4 or before['groups']!=groups:raise Unavailable()
    else:
        if before['groups']!=groups:os.setgroups(groups)
        if before['gids']!=[gid]*4:os.setresgid(gid,gid,gid)
        if before['uids']!=[uid]*4:os.setresuid(uid,uid,uid)
    # Credential changes can clear PDEATHSIG: set it after the last change.
    prctl(1,signal.SIGKILL);prctl(38,1)
    if os.getppid()!=parent_pid:raise Unavailable()
    after=context()
    if after['uids']!=[uid]*4 or after['gids']!=[gid]*4 or after['groups']!=groups or after['no_new_privs']!=1:raise Unavailable()
    if uid!=0 and any(after[k]!=0 for k in ('cap_effective','cap_permitted','cap_inheritable','cap_ambient')):raise Unavailable()
    return after


def close_inherited(keep):
    names=os.listdir('/proc/self/fd')
    if len(names)>4096:raise Unavailable()
    for name in names:
        if name.isdigit() and int(name)>2 and int(name) not in keep:
            try:os.close(int(name))
            except OSError:pass


def endpoint(path):
    """Open every component as this actor; no inherited root directory handle."""
    parts=PurePosixPath(path).parts;directory=os.open('/',os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW)
    chain=[identity(os.fstat(directory))]
    try:
        for name in parts[1:-1]:
            child=os.open(name,os.O_PATH|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
            os.close(directory);directory=child;chain.append(identity(os.fstat(directory)))
        leaf=os.open(parts[-1],os.O_PATH|os.O_NOFOLLOW,dir_fd=directory)
        try:
            item=os.fstat(leaf)
            if not stat.S_ISSOCK(item.st_mode):raise Unavailable()
            return leaf,identity(item),hashlib.sha256(encoded(chain)).hexdigest()
        except BaseException:os.close(leaf);raise
    finally:os.close(directory)


def socket_proxy(paths,anchors):
    actual_socket=socket.socket
    class Peer:
        def __init__(self,*args,**kwargs):
            if args!=(socket.AF_UNIX,socket.SOCK_STREAM) or kwargs:raise ValueError('IPC_SOCKET_KIND_UNAVAILABLE')
            self.peer=actual_socket(*args);self.leaf=None
        def __enter__(self):return self
        def __exit__(self,*args):
            try:self.peer.close()
            finally:
                if self.leaf is not None:os.close(self.leaf);self.leaf=None
        def settimeout(self,value):return self.peer.settimeout(value)
        def getsockopt(self,*args):return self.peer.getsockopt(*args)
        def connect(self,path):
            row={'index':paths.index(path),'identity':None,'directory_chain_sha256':None,
                'started_monotonic_ns':time.monotonic_ns(),'finished_monotonic_ns':None,
                'metadata_complete':False,'kernel_connect_attempted':False,'path_binding_stable':False}
            anchors.append(row)
            try:self.leaf,item,chain=endpoint(path)
            except Exception:raise ValueError('IPC_ACTOR_ENDPOINT_UNAVAILABLE') from None
            row.update(identity=item,directory_chain_sha256=chain,metadata_complete=True,kernel_connect_attempted=True)
            try:
                # O_PATH pins exactly this actor-resolved endpoint, including
                # when another process races pathname replacement at connect.
                return self.peer.connect('/proc/self/fd/'+str(self.leaf))
            finally:
                current=None
                try:
                    current,after,after_chain=endpoint(path)
                    row['path_binding_stable']=(after==item and after_chain==chain and identity(os.fstat(self.leaf))==item)
                except Exception:pass
                finally:
                    row['finished_monotonic_ns']=time.monotonic_ns()
                    if current is not None:os.close(current)
    return types.SimpleNamespace(socket=Peer,AF_UNIX=socket.AF_UNIX,SOCK_STREAM=socket.SOCK_STREAM,SOL_SOCKET=socket.SOL_SOCKET,SO_PEERCRED=socket.SO_PEERCRED)


def child_result(probe,config,actor,parent_pid,write_fd,deadline):
    result={'requested':actor,'context_before':None,'context_after':None,'connect_packet':None,'path_anchors':[],
        'credential_match_observed':False,'context_stable':False,'diagnostics':[]}
    def emit():
        raw=encoded(result)+b'\n'
        if len(raw)>8192:raise Unavailable()
        offset=0
        while offset<len(raw):
            written=os.write(write_fd,raw[offset:])
            if written<=0:raise Unavailable()
            offset+=written
    try:
        close_inherited({write_fd});prctl(1,signal.SIGKILL)
        if os.getppid()!=parent_pid:raise Unavailable()
        signal.setitimer(signal.ITIMER_REAL,max(0.001,deadline-time.monotonic()))
        result['context_before']=restrict_actor(actor,parent_pid)
        probe.socket=socket_proxy(config['paths'],result['path_anchors'])
        emit()
        for index,path in enumerate(config['paths']):
            previous=encoded(result)
            packet=probe.connect_paths([path],config['connect_timeout'])
            packet['rows'][0]['index']=index
            if result['connect_packet'] is None:
                result['connect_packet']={**packet,'rows':[]}
            if any(packet[key]!=result['connect_packet'][key] for key in ('schema','mode','uid','groups','namespaces')):raise Unavailable()
            result['connect_packet']['rows'].extend(packet['rows'])
            result['context_after']=context()
            result['credential_match_observed']=True
            result['context_stable']=result['context_before']==result['context_after']
            if len(encoded(result))+128>8191:
                result=json.loads(previous)
                result['diagnostics'].append('IPC_ACTOR_OUTPUT_BOUND')
                break
            emit()
    except Deadline:result['diagnostics'].append('IPC_ACTOR_DEADLINE')
    except BaseException:result['diagnostics'].append('IPC_ACTOR_UNAVAILABLE')
    try:emit()
    except BaseException:pass
    os._exit(0)


def reap_owned(pid,*,terminate):
    child,status=os.waitpid(pid,os.WNOHANG)
    if child==pid:return True,status==0
    if terminate:
        try:os.kill(pid,signal.SIGKILL)
        except ProcessLookupError:pass
    end=time.monotonic()+1
    while time.monotonic()<end:
        child,status=os.waitpid(pid,os.WNOHANG)
        if child==pid:return True,status==0
        time.sleep(0.01)
    return False,False


def invoke_actor(probe,config,actor,deadline):
    read_fd,write_fd=os.pipe();pid=None;raw=b'';count=0;done=False;exit_ok=False
    value={'requested':actor,'context_before':None,'context_after':None,'connect_packet':None,'path_anchors':[],
        'credential_match_observed':False,'context_stable':False,'diagnostics':[]}
    try:
        parent_pid=os.getpid();pid=os.fork()
        if pid==0:
            try:
                os.close(read_fd);child_result(probe,config,actor,parent_pid,write_fd,deadline)
            finally:os._exit(2)
        os.close(write_fd);write_fd=None
        while True:
            remaining=deadline-time.monotonic()
            if remaining<=0:raise Deadline()
            if not select.select([read_fd],[],[],remaining)[0]:raise Deadline()
            piece=os.read(read_fd,8192)
            if not piece:break
            count+=len(piece);raw+=piece
            if count>81920:raise Unavailable()
            while b'\n' in raw:
                frame,raw=raw.split(b'\n',1)
                if len(frame)>8191:raise Unavailable()
                item=json.loads(frame,object_pairs_hook=pairs)
                if type(item) is not dict or item.get('requested')!=actor:raise Unavailable()
                value=item
            if len(raw)>8191:raise Unavailable()
        if raw:raise Unavailable()
        done,exit_ok=reap_owned(pid,terminate=False);pid=None if done else pid
        if not done or not exit_ok:raise Unavailable()
    except Deadline:
        value['diagnostics'].append('IPC_ACTOR_DEADLINE')
    except BaseException:
        value['diagnostics'].append('IPC_ACTOR_OUTPUT_UNAVAILABLE')
    finally:
        os.close(read_fd)
        if write_fd is not None:os.close(write_fd)
        if pid is not None:
            try:done,exit_ok=reap_owned(pid,terminate=True)
            except BaseException:pass
    value['diagnostics']=sorted(set(value['diagnostics']))
    return {**value,'child_reaped':done,'child_exit_ok':exit_ok}


def bounded_inspect(probe,paths,target,collector):
    packet={'schema':'guard-unix-ipc-probe/1','mode':'inspect','uid':collector['uids'][1],
        'namespaces':{key:collector['namespaces'][key] for key in ('mnt','net','pid','user')},'rows':[]}
    target.update(packet=packet,output_bound_reached=False)
    for index,path in enumerate(paths):
        item=probe.inspect_paths([path]);row=item['rows'][0];row['index']=index
        if any(item[key]!=packet[key] for key in ('schema','mode','uid','namespaces')):raise Unavailable()
        packet['rows'].append(row)
        if len(encoded(packet))>12288:
            packet['rows'][-1]={'index':index,'complete':False,'error':'IPC_METADATA_UNAVAILABLE'}
            target['output_bound_reached']=True


def snapshot(probe,config):
    value={'schema':SCHEMA,'probe_source_sha256':PROBE_SHA256,'collector_before':None,'collector_after':None,
        'inspect_before':None,'inspect_after':None,'actors':[],'diagnostics':[],
        'compile_provenance':COMPILE_PROVENANCE,
        'producer_authenticated':False,'worker_identity_verified':False,'whole_scope_complete':False}
    started=time.monotonic_ns();deadline=time.monotonic()+config['total_timeout']
    old_handler=signal.getsignal(signal.SIGALRM);old_timer=signal.getitimer(signal.ITIMER_REAL)
    if old_timer!=(0.0,0.0):raise Unavailable()
    def alarm(signum,frame):raise Deadline()
    signal.signal(signal.SIGALRM,alarm);signal.setitimer(signal.ITIMER_REAL,config['total_timeout'])
    try:
        value['collector_before']=context();value['inspect_before']={}
        bounded_inspect(probe,config['paths'],value['inspect_before'],value['collector_before'])
        for actor in config['actors']:
            if time.monotonic()>=deadline:raise Deadline()
            value['actors'].append(invoke_actor(probe,config,actor,deadline))
        value['inspect_after']={}
        bounded_inspect(probe,config['paths'],value['inspect_after'],value['collector_before'])
        value['collector_after']=context()
    except Deadline:value['diagnostics'].append('IPC_SNAPSHOT_DEADLINE')
    except BaseException:value['diagnostics'].append('IPC_SNAPSHOT_UNAVAILABLE')
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,old_handler)
    value.update(started_monotonic_ns=started,finished_monotonic_ns=time.monotonic_ns())
    return value


def main():
    try:
        if sys.platform!='linux' or sys.implementation.name!='cpython' or sys.version_info[:2]!=(3,12):raise Unavailable()
        # The standalone builder embeds the same reviewed contract source too.
        config_raw=sys.stdin.buffer.read(16385)
        if len(config_raw)>16384:raise Unavailable()
        supplied=json.loads(config_raw,object_pairs_hook=pairs)
        if type(supplied) is not dict or set(supplied)!={'policy_values','actor_specs','connect_timeout','total_timeout'}:raise Unavailable()
        config=normalize_inputs(**supplied)
        raw=base64.b64decode(PROBE_SOURCE_B64,validate=True)
        if hashlib.sha256(raw).hexdigest()!=PROBE_SHA256:raise Unavailable()
        probe=types.ModuleType('_guard_owned_ipc_probe');probe.__file__='guard-owned-ipc-probe.py'
        code=compile(raw,probe.__file__,'exec',dont_inherit=True,optimize=0)
        import marshal
        COMPILE_PROVENANCE['code_sha256']['probe']=hashlib.sha256(marshal.dumps(code)).hexdigest()
        exec(code,probe.__dict__)
        value=snapshot(probe,config);result=encoded(value)
        if len(result)>MAX_BYTES:raise Unavailable()
        sys.stdout.buffer.write(result+b'\n');return 0
    except BaseException:
        sys.stdout.write('{"schema":"guard-owned-unix-ipc-actor/1","diagnostics":["IPC_PROGRAM_UNAVAILABLE"]}\n');return 2


if __name__=='__main__':raise SystemExit(main())
