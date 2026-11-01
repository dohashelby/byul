"""명시적 Linux/amd64 LIF13 peer·socket epoch·bounded recv 실커널 확인."""
from __future__ import annotations
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import types

SCHEMA = 'byul-lif13-linux-peer-probe/1'
APPROVED_COMMIT = '0ae9cc56dcce903d059fab8ff2d2e12f23cef3ba'
APPROVED_NATIVE_SHA256 = '8548dc812acdc421e313c5532d6cd4d6bd214c19d51b79adc96ffb7b7804c4da'
TIMEOUT = 10


def sha(raw):return hashlib.sha256(raw).hexdigest()


def environment():
    supported=(sys.platform=='linux' and platform.machine().lower() in {'x86_64','amd64'}
        and sys.version_info[:2]==(3,12) and sys.flags.optimize==0 and hasattr(socket,'SO_PEERCRED')
        and all(Path(p).exists() for p in ('/proc/self/stat','/proc/sys/kernel/random/boot_id','/proc/self/ns/pid','/proc/self/ns/mnt')))
    return {'supported':supported,'system':platform.system(),'machine':platform.machine(),
        'python':platform.python_version(),'SO_PEERCRED_available':hasattr(socket,'SO_PEERCRED'),
        'isolated':bool(sys.flags.isolated),'no_site':bool(sys.flags.no_site),'optimize':sys.flags.optimize}


def source_bytes(path):
    path=Path(path).absolute()
    if any(p.is_symlink() for p in (path,*path.parents)):raise ValueError('SOURCE_REGULAR_FILE_REQUIRED')
    selected=path.lstat()
    identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
    if not stat.S_ISREG(selected.st_mode) or not 0<selected.st_size<=1024*1024:raise ValueError('SOURCE_BOUND')
    # FIFO/leaf-symlink 교체에도 open/read가 대기하지 않는다. FD와 경로는 같은 stat API로 비교한다.
    fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=1024*1024:raise ValueError('SOURCE_BOUND')
        if identity(selected)!=identity(before):raise ValueError('SOURCE_CHANGED_DURING_READ')
        raw=stream.read(1024*1024+1);after=os.fstat(stream.fileno());current=path.lstat()
        if (identity(before)!=identity(after) or identity(after)!=identity(current) or len(raw)!=before.st_size
                or not stat.S_ISREG(current.st_mode) or any(p.is_symlink() for p in (path,*path.parents))):
            raise ValueError('SOURCE_CHANGED_DURING_READ')
    return path,raw


def recheck_sources(proof,native_path,probe_path):
    """한 source 재검사 실패로 다른 source/앞선 단계/정리 proof를 잃지 않는다."""
    proof['source_after']={}
    for name,path in (('native',native_path),('probe',probe_path)):
        try:proof['source_after'][name+'_sha256']=sha(source_bytes(path)[1])
        except BaseException as error:proof['cleanup']['errors'].append('source_recheck_'+name+':'+type(error).__name__)
    proof['source_unchanged']=proof['source_before']==proof['source_after']
    return proof['source_unchanged']


def load_native(path,raw):
    # 승인된 원 bytes를 그대로 실행한다. __main__/RPC/GPU 경로는 호출하지 않는다.
    module=types.ModuleType('_lif13_frozen_linux_peer');module.__file__=str(path)
    exec(compile(raw,str(path),'exec',dont_inherit=True,optimize=0),module.__dict__)
    return module


def child_peer(path):
    """소유 CPU fixture의 AF_UNIX peer. 원 vLLM daemon/protocol이 아니다."""
    listener=None
    try:
        selected=Path(path);parent=selected.parent.lstat()
        if (not selected.is_absolute() or selected.parent.parent!=Path('/tmp') or not selected.parent.name.startswith('l13-')
                or selected.name!='p' or selected.exists() or selected.is_symlink() or selected.parent.is_symlink()
                or not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or stat.S_IMODE(parent.st_mode)!=0o700):
            raise ValueError('OWNED_PEER_DIRECTORY_REQUIRED')
        os.umask(0o077)
        listener=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);listener.bind(path);os.chmod(path,0o600);listener.listen(2)
        print(json.dumps({'ready':True}),flush=True)
        with selectors.DefaultSelector() as selector:
            selector.register(listener,selectors.EVENT_READ,'socket');selector.register(sys.stdin,selectors.EVENT_READ,'control')
            while True:
                for key,_ in selector.select(TIMEOUT):
                    if key.data=='control':
                        if sys.stdin.readline().strip()!='stop':return 1
                        return 0
                    with listener.accept()[0] as connection:
                        connection.settimeout(TIMEOUT);tag=connection.recv(1)
                        if tag==b'N':data=b'X';header=0;payload=1
                        elif tag==b'O':data=struct.pack('!Q',1024)+b'XXXXXXXX';header=8;payload=8
                        else:return 1
                        connection.sendall(data)
                    print(json.dumps({'header_bytes_sent':header,'payload_bytes_sent':payload}),flush=True)
    except BaseException:
        print(json.dumps({'error':'OWNED_PEER_FIXTURE_ERROR'}),flush=True);return 1
    finally:
        if listener is not None:listener.close()


def read_control(process):
    deadline=time.monotonic()+TIMEOUT;raw=bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout,selectors.EVENT_READ)
        while b'\n' not in raw:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not selector.select(remaining):raise TimeoutError('OWNED_PEER_CONTROL_TIMEOUT')
            chunk=os.read(process.stdout.fileno(),1024)
            if not chunk:raise ValueError('OWNED_PEER_CONTROL_EOF')
            raw.extend(chunk)
            if len(raw)>4096:raise ValueError('OWNED_PEER_CONTROL_BOUND')
    value=json.loads(raw.decode())
    if type(value) is not dict or value.get('error'):raise ValueError('OWNED_PEER_FIXTURE_ERROR')
    return value


class CountedSocket:
    """본물 socket recv 호출 수만 잰다. 반환 bytes/peer 정보는 대체하지 않는다."""
    def __init__(self,raw):self.raw=raw;self.recv_calls=0;self.received_bytes=0
    def settimeout(self,value):self.raw.settimeout(value)
    def sendall(self,value):self.raw.sendall(value)
    def recv(self,count):
        self.recv_calls+=1;value=self.raw.recv(count);self.received_bytes+=len(value);return value


def reject(call,code):
    try:call()
    except ValueError as error:
        if error.args!=(code,):raise AssertionError('UNEXPECTED_GUARD_REASON') from None
        return code
    raise AssertionError('EXPECTED_GUARD_REJECTION_MISSING')


def exact_header(bounded):
    raw=bytearray()
    while len(raw)<8:
        value=bounded.recv(8-len(raw))
        if not value:raise AssertionError('FIXTURE_HEADER_INCOMPLETE')
        raw.extend(value)
    return struct.unpack('!Q',raw)[0]


def run_probe(native_path,output):
    began=time.monotonic();started=datetime.now(timezone.utc).isoformat();host=environment()
    native_path,native_raw=source_bytes(native_path);probe_path,probe_raw=source_bytes(__file__)
    if sha(native_raw)!=APPROVED_NATIVE_SHA256:raise ValueError('FROZEN_NATIVE_PIN_MISMATCH')
    output=Path(output).absolute();repo=probe_path.parents[3]
    if (output.exists() or output.is_symlink() or not output.parent.is_dir()
            or any(p.is_symlink() for p in output.parents) or output.resolve().is_relative_to(repo.resolve())):
        raise ValueError('FRESH_EXTERNAL_OUTPUT_REQUIRED')
    output.mkdir(mode=0o700)
    proof={'schema':SCHEMA,'status':'UNSUPPORTED' if not host['supported'] else 'RUNNING','environment':host,
        'scope':'guarded_native_peer_epoch_and_bounded_socket_linux_cpu_only','fixture_data_origin':'EXAMPLE',
        'measurement_kind':'actual_linux_kernel_only' if host['supported'] else 'unsupported_environment_guard_only',
        'started_at':started,'stages':[],'kernel_cases_executed':0,'kernel_cases_passed':0,'owned_peer_children_created':0,
        'actual_weight_cache_daemon':False,'actual_vllm_protocol_recv_msg_called':False,'RPC_release_called':False,
        'Torch_vllm_GPU_used':False,'tcp_udp_port_listener_created':False,'source_python_module_executed':False,
        'original_lif13_verdicts':{k:'UNKNOWN' for k in ('security','functional','performance')},
        'approved_native_commit':APPROVED_COMMIT,'approved_native_sha256':APPROVED_NATIVE_SHA256,
        'command':{'argv':list(sys.orig_argv),'entry_argv':[sys.executable,*sys.argv],'argv_basis':'CPython sys.orig_argv','dependencies':'Python 3.12 stdlib only'},
        'source_before':{'native_sha256':sha(native_raw),'probe_sha256':sha(probe_raw)},
        'cleanup':{'owned_sockets_closed':True,'owned_peer_reaped':True,'owned_streams_closed':True,
            'owned_fixture_directory_created':False,'owned_fixture_directory_removed':True,'socket_path_removed':True,'forced_child_termination':False,'errors':[]}}
    if not host['supported']:
        proof['reason']='PYTHON_OPTIMIZATION_UNSUPPORTED' if host['optimize'] else 'LINUX_X86_64_PYTHON312_PROC_SO_PEERCRED_REQUIRED'
        unchanged=recheck_sources(proof,native_path,probe_path)
        proof['finished_at']=datetime.now(timezone.utc).isoformat();proof['wall_seconds']=time.monotonic()-began
        if not unchanged:proof['status']='FAILED'
        with (output/'proof.json').open('x') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2)
        code=2 if unchanged else 1
        print(json.dumps({'status':proof['status'],'returncode':code,'kernel_cases_executed':0,'proof':str(output/'proof.json')}))
        return code
    fixture=None;process=None;owned=[];address=None;failure=None;active=None
    try:
        native=load_native(native_path,native_raw);proof['source_python_module_executed']=True
        fixture=tempfile.TemporaryDirectory(prefix='l13-',dir='/tmp');directory=Path(fixture.name);os.chmod(directory,0o700);address=directory/'p';proof['cleanup']['owned_fixture_directory_created']=True
        process=subprocess.Popen([sys.executable,'-I','-S','-B',str(probe_path),'--_peer',str(address)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
        proof['owned_peer_children_created']=1;proof['cleanup']['owned_peer_pid']=process.pid
        if read_control(process)!={'ready':True}:raise AssertionError('OWNED_PEER_NOT_READY')
        active={'stage':'peer_identity_and_mismatch','status':'RUNNING'};proof['stages'].append(active);proof['kernel_cases_executed']+=1
        actor=native._proc_identity(process.pid);config={'daemon':{**actor,'socket_path':str(address)}}
        identity=native._socket_identity(config);epoch=native._daemon_epoch(config,identity)
        socket_mode=stat.S_IMODE(address.lstat().st_mode);directory_mode=stat.S_IMODE(directory.lstat().st_mode)
        assert socket_mode==0o600 and directory_mode==0o700
        raw=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);owned.append(raw);raw.settimeout(TIMEOUT);raw.connect(str(address))
        active.update(peer_identity=actor,socket_device_inode_uid=list(identity),generation_sha256=epoch)
        observed_epoch=native._check_peer(raw,config,identity)
        creds=struct.unpack('3i',raw.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        assert observed_epoch==epoch and creds[:2]==(actor['pid'],actor['uid'])
        counted=CountedSocket(raw);bounded=native.BoundedSocket(counted,limit=16,deadline=time.monotonic()+TIMEOUT,timeout=TIMEOUT,check=lambda:native._check_peer(raw,config,identity))
        bounded.sendall(b'N');value=bounded.recv(1);assert len(value)==1 and read_control(process)=={'header_bytes_sent':0,'payload_bytes_sent':1}
        wrong={'daemon':{**native._proc_identity(os.getpid()),'socket_path':str(address)}}
        native._daemon_epoch(wrong,identity)  # /proc epoch 자체는 유효해 PID peer 검사가 가려지지 않는다.
        wrong_peer=reject(lambda:native._check_peer(raw,wrong,identity),'LIF13_PEER_CHANGED')
        changed=deepcopy(config);changed['daemon']['start_ticks']+=1
        wrong_epoch=reject(lambda:native._check_peer(raw,changed,identity),'LIF13_PEER_CHANGED')
        active.update({'stage':'peer_identity_and_mismatch','status':'PASS','peer_identity':actor,
            'kernel_peer_pid_uid_gid':list(creds),'socket_device_inode_uid':list(identity),'socket_mode':format(socket_mode,'04o'),'directory_mode':format(directory_mode,'04o'),
            'same_uid_different_pid_rejected':wrong_peer,'wrong_expected_process_epoch_valid':True,'expected_start_tick_mismatch_rejected':wrong_epoch,
            'actual_process_restart_executed':False,'generation_sha256':epoch,'received_bytes':counted.received_bytes})
        raw.close();proof['kernel_cases_passed']+=1
        active={'stage':'oversize_before_payload_decoder','status':'RUNNING'};proof['stages'].append(active);proof['kernel_cases_executed']+=1
        raw=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);owned.append(raw);raw.settimeout(TIMEOUT);raw.connect(str(address));native._check_peer(raw,config,identity)
        counted=CountedSocket(raw);bounded=native.BoundedSocket(counted,limit=16,deadline=time.monotonic()+TIMEOUT,timeout=TIMEOUT,check=lambda:native._check_peer(raw,config,identity))
        bounded.sendall(b'O');length=exact_header(bounded);assert length==1024
        calls=counted.recv_calls;received=counted.received_bytes;decoder_called=False
        def payload_then_decode():
            nonlocal decoder_called
            payload=bounded.recv(length);decoder_called=True;return json.loads(payload)
        reason=reject(payload_then_decode,'LIF13_RESPONSE_BOUND')
        assert counted.recv_calls==calls and counted.received_bytes==received==8 and not decoder_called
        assert read_control(process)=={'header_bytes_sent':8,'payload_bytes_sent':8}
        active.update({'stage':'oversize_before_payload_decoder','status':'PASS','fixture_frame':'u64_network_order_not_vllm_protocol',
            'advertised_bytes':length,'response_limit_bytes':16,'header_bytes_received':received,'underlying_recv_calls_before':calls,
            'underlying_recv_calls_after':counted.recv_calls,'payload_recv_calls_after_bound':0,'decoder_called':decoder_called,
            'guard_reason':reason,'peer_fixture_payload_bytes_sent':8})
        raw.close();proof['kernel_cases_passed']+=1
        active={'stage':'socket_replacement_epoch','status':'RUNNING'};proof['stages'].append(active);proof['kernel_cases_executed']+=1
        address.unlink();replacement=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);owned.append(replacement);replacement.bind(str(address));os.chmod(address,0o600);replacement.listen(1)
        replacement_identity=native._socket_identity(config);assert replacement_identity!=identity
        reason=reject(lambda:native._daemon_epoch(config,identity),'LIF13_SOCKET_CHANGED')
        active.update({'stage':'socket_replacement_epoch','status':'PASS','original_device_inode_uid':list(identity),
            'replacement_device_inode_uid':list(replacement_identity),'guard_reason':reason,'selected_peer_still_alive':process.poll() is None})
        proof['kernel_cases_passed']+=1
    except BaseException as error:
        failure=type(error).__name__;proof['status']='FAILED';proof['failure_class']=failure
        if active is not None and active['status']=='RUNNING':active.update(status='FAIL',error_class=failure)
    finally:
        cleanup=proof['cleanup']
        for item in owned:
            try:item.close()
            except BaseException as error:cleanup['errors'].append('socket_close:'+type(error).__name__)
        cleanup['owned_sockets_closed']=all(item.fileno()==-1 for item in owned)
        if process is not None:
            try:
                if process.poll() is None:
                    process.stdin.write(b'stop\n');process.stdin.flush()
            except BaseException as error:cleanup['errors'].append('peer_stop:'+type(error).__name__)
            try:
                try:process.wait(timeout=TIMEOUT)
                except subprocess.TimeoutExpired:
                    cleanup['forced_child_termination']=True;process.terminate()
                    try:process.wait(timeout=TIMEOUT)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=TIMEOUT)
                cleanup['owned_peer_returncode']=process.returncode;cleanup['owned_peer_reaped']=process.returncode is not None
            except BaseException as error:
                cleanup['owned_peer_reaped']=False;cleanup['errors'].append('peer_wait:'+type(error).__name__)
            for stream in (process.stdin,process.stdout,process.stderr):
                try:stream.close()
                except BaseException as error:cleanup['errors'].append('stream_close:'+type(error).__name__)
            cleanup['owned_streams_closed']=all(stream.closed for stream in (process.stdin,process.stdout,process.stderr))
        if fixture is not None:
            try:fixture.cleanup()
            except BaseException as error:cleanup['errors'].append('directory_cleanup:'+type(error).__name__)
            cleanup['owned_fixture_directory_removed']=not Path(fixture.name).exists();cleanup['socket_path_removed']=not address.exists()
        recheck_sources(proof,native_path,probe_path)
        success=(failure is None and proof['kernel_cases_executed']==proof['kernel_cases_passed']==3 and proof['source_unchanged']
            and all(cleanup[k] for k in ('owned_sockets_closed','owned_peer_reaped','owned_streams_closed','owned_fixture_directory_removed','socket_path_removed'))
            and not cleanup['forced_child_termination'] and not cleanup['errors'] and cleanup.get('owned_peer_returncode')==0)
        proof['status']='VERIFIED_LINUX_KERNEL_FIXTURE' if success else 'FAILED'
        proof['finished_at']=datetime.now(timezone.utc).isoformat();proof['wall_seconds']=time.monotonic()-began
        with (output/'proof.json').open('x') as stream:json.dump(proof,stream,ensure_ascii=False,indent=2)
    print(json.dumps({'status':proof['status'],'returncode':0 if success else 1,'kernel_cases_executed':proof['kernel_cases_executed'],'proof':str(output/'proof.json')}))
    return 0 if success else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--native-script',type=Path);parser.add_argument('--output',type=Path);parser.add_argument('--_peer',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args._peer:
        if not environment()['supported']:return 2
        return child_peer(args._peer)
    if args.native_script is None or args.output is None:parser.error('--native-script and --output are required')
    try:return run_probe(args.native_script,args.output)
    except BaseException as error:
        print(json.dumps({'status':'FAILED','error_class':type(error).__name__,'kernel_cases_executed':0}),file=sys.stderr);return 1

if __name__=='__main__':raise SystemExit(main())
