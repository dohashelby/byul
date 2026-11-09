"""Owned control protocol boundaries and reuse of the source verifier."""
from copy import deepcopy
from contextlib import redirect_stdout
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from guard.executors import lab_activation as actor


def namespace(source):
    values={'__name__':'_fixed_protocol_fixture'}
    exec(compile(source,'fixed_protocol_fixture.py','exec'),values)
    return values


@pytest.fixture
def control(tmp_path):
    path=tmp_path/'control';path.mkdir(mode=0o700)
    reader=namespace((Path(__file__).parent/'fixtures/fault-control/reader-20adc13.py.txt').read_text())
    code=namespace(actor.FAULT_CONTROL)
    now=time.monotonic_ns();ctx={'boot_sha256':'b'*64,'time_namespace':123,'time_offsets_sha256':'c'*64}
    token={'schema':'byul-lab-fault-control/1','nonce':'a'*32,'session':'d'*32,'target_sha256':'e'*64,
        'external_request_sha256':'f'*64,'fault_kind':'delay','rank':0,'hold_timeout_ms':30000,
        'expires_unix_ns':time.time_ns()+30000*1000000}
    ready={'schema':reader['READY_SCHEMA'],**{k:token[k] for k in (
        'nonce','session','target_sha256','external_request_sha256','fault_kind','rank','expires_unix_ns')},
        'native_request_sha256':'1'*64,'pid':107,'start_ticks':1234,'pid_namespace':222,
        'call_sha256':'2'*64,'point':'WorkerProc._execute_worker_rpc','clock_context':ctx,
        'operation_started_ns':now-1000,'ready_ns':now,'barrier_deadline_ns':now+30000*1000000,
        'active':True,'bounded_barrier':True,'observer_runtime':'cpython-3.12.3',
        'observer_code_sha256':'3'*64,'source_module':reader['MP'],'source_sha256':reader['MP_SHA256']}
    target={'pid':107,'start_ticks':1234,'pod':'engine','pod_uid':'owned'}
    state={'start_ticks':1234,'state':'S'};signals=[]
    class OS:
        def __getattr__(self,key):return getattr(os,key)
        def getpid(self):return 99999
        def stat(self,path,*args,**kwargs):
            if path=='/proc/self/ns/pid':return SimpleNamespace(st_ino=222)
            return os.stat(path,*args,**kwargs)
        def pidfd_open(self,pid,flags):
            assert pid==107 and flags==0
            return os.open(os.devnull,os.O_RDONLY)
    def send_signal(descriptor,number,info,flags):
        os.fstat(descriptor);signals.append((107,number));state['state']='T' if number==19 else 'S'
    code['signal']=SimpleNamespace(pidfd_send_signal=send_signal)
    code['os']=OS();code['clock_context']=lambda:dict(ctx);code['process']=lambda _pid:dict(state)
    def call(action):return code['perform']({'action':action,'directory':str(path),'control':token,'target':target},reader)
    call('arm')
    def save_ready():
        p=path/(token['nonce']+'.ready.json');p.write_text(json.dumps(ready));p.chmod(0o600)
    save_ready()
    return SimpleNamespace(path=path,reader=reader,code=code,token=token,ready=ready,target=target,
        signals=signals,state=state,ctx=ctx,call=call,save_ready=save_ready)


def test_signal_requires_owned_ready_and_exact_live_process(control):
    value=control.call('signal')
    assert control.signals==[(107,19)] and value['observed_state']=='T'
    assert value['signal_before_ns']<=value['signal_after_ns']<=value['state_observed_ns']
    assert value['ready_receipt_sha256']==hashlib.sha256(
        (control.path/(control.token['nonce']+'.ready.json')).read_bytes()).hexdigest()
    assert control.call('ready')['native_fault_verified'] is False


@pytest.mark.parametrize('case',['pid-reused','different-clock','expired-barrier','wrong-kind','symlink-ready'])
def test_bad_ready_never_sends_a_signal(control,case):
    if case=='pid-reused':control.state['start_ticks']+=1
    elif case=='different-clock':control.code['clock_context']=lambda:{**control.ctx,'boot_sha256':'9'*64}
    elif case=='expired-barrier':
        control.ready['ready_ns']=time.monotonic_ns()-2000000000
        control.ready['operation_started_ns']=control.ready['ready_ns']-1
        control.ready['barrier_deadline_ns']=time.monotonic_ns()-1000000000;control.save_ready()
    elif case=='wrong-kind':control.ready['fault_kind']='death';control.save_ready()
    else:
        p=control.path/(control.token['nonce']+'.ready.json');p.rename(control.path/'actual.json');p.symlink_to(control.path/'actual.json')
    with pytest.raises((ValueError,OSError)):control.call('signal')
    assert control.signals==[]


def test_expired_lease_does_not_disable_exact_sigcont_cleanup(control):
    control.call('signal')
    real_time=control.code['time']
    control.code['time']=SimpleNamespace(monotonic_ns=real_time.monotonic_ns,
        time_ns=lambda:real_time.time_ns()+60000000000,sleep=real_time.sleep)
    control.call('release')
    assert control.signals==[(107,19),(107,18)]
    action=json.loads((control.path/(control.token['nonce']+'.release.json')).read_text())
    assert set(action)=={'schema','nonce','session','target_sha256','action'} and action['action']=='release'


def test_reused_pid_is_not_resumed_during_cleanup(control):
    control.call('signal');control.state['start_ticks']+=1
    with pytest.raises(ValueError):control.call('release')
    assert control.signals==[(107,19)]


def test_ipc_activation_never_substitutes_process_stop(control):
    control.token['fault_kind']='communication-stall';control.ready['fault_kind']='communication-stall'
    control.ready['point']='WorkerProc.enqueue_output'
    (control.path/(control.token['nonce']+'.arm.json')).write_text(json.dumps(control.token))
    control.save_ready();value=control.call('activate')
    assert control.signals==[] and value['action']=='activate'


def test_cleanup_does_not_unlink_foreign_nonce_file(control):
    p=control.path/(control.token['nonce']+'.ready.json')
    p.write_text(json.dumps({**control.ready,'nonce':'9'*32}))
    with pytest.raises(ValueError):control.call('clear')
    assert p.exists() and (control.path/(control.token['nonce']+'.arm.json')).exists()


def test_control_arm_does_not_overwrite_existing_transaction(control):
    with pytest.raises(FileExistsError):control.call('arm')


def run_client(port):
    header='byul-lif08-'+('a'*32)
    spec={'url':f'http://127.0.0.1:{port}','requests':[{'method':'POST','path':'/v1/chat/completions',
        'body':{'model':'m'},'headers':{'X-Request-Id':header},'fault_nonce':'a'*32,'timeout':2}]}
    result=subprocess.run([sys.executable,'-I','-S','-B','-c',actor.CLIENT],input=json.dumps(spec),
        capture_output=True,text=True,timeout=10,check=True)
    row,=json.loads(result.stdout)
    assert row['fault_nonce']=='a'*32 and row['external_request_sha256']==hashlib.sha256(header.encode()).hexdigest()
    assert row['request_started_ns']<=row['response_finished_ns']
    return row


def test_client_distinguishes_connect_refusal_from_sent_request():
    # The OS may time out rather than refuse a bound-but-unlistened socket.
    # Inject the transport exception at its actual boundary, not a timer guess.
    class Refused:
        def __init__(self,*_args,**_kwargs):pass
        def connect(self):raise ConnectionRefusedError('test connection refused')
        def request(self,*_args,**_kwargs):raise AssertionError('no request bytes may be sent')
        def close(self):pass
    source=io.StringIO(json.dumps({'url':'http://127.0.0.1:8000','requests':[
        {'method':'POST','path':'/v1/chat/completions','body':{'model':'m'}}]}));output=io.StringIO()
    with patch('sys.stdin',source),patch('http.client.HTTPConnection',Refused),redirect_stdout(output):
        exec(compile(actor.CLIENT,'original-client.py','exec'),{'__name__':'_client_boundary'})
    row,=json.loads(output.getvalue())
    assert row['outcome']=='error' and row['error_kind']=='connection-refused' and row['error_stage']=='connect'
    assert row['connection_established'] is False and row['request_sent'] is False


def test_client_preserves_complete_http_failure_and_own_clock_context():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            raw=b'{"error":{"code":"engine-failed"}}'
            self.send_response(503);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def log_message(self,*_args):pass
    with http.server.HTTPServer(('127.0.0.1',0),Handler) as server:
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:row=run_client(server.server_port)
        finally:server.shutdown();thread.join()
    assert row['outcome']==503 and row['complete'] and row['connection_established'] and row['request_sent']
    assert row['error_kind'] is None
    if row['clock_context'] is not None:
        assert set(row['clock_context'])=={'boot_sha256','time_namespace','time_offsets_sha256'}


def test_slow_identity_read_expires_barrier_before_signal_sink(control):
    original=control.code['process']
    real_time=control.code['time'];expired=[False]
    def process(pid):
        value=original(pid);expired[0]=True;return value
    control.code['process']=process
    control.code['time']=SimpleNamespace(time_ns=real_time.time_ns,sleep=real_time.sleep,
        monotonic_ns=lambda:control.ready['barrier_deadline_ns']+1 if expired[0] else control.ready['ready_ns']+1)
    with pytest.raises(ValueError,match='BARRIER_EXPIRED'):control.call('signal')
    assert control.signals==[]


def test_pid_reuse_while_opening_pidfd_never_sends_to_replacement(control):
    original=control.code['os'].pidfd_open
    def opened(pid,flags):
        descriptor=original(pid,flags);control.state['start_ticks']+=1;return descriptor
    control.code['os'].pidfd_open=opened
    with pytest.raises(ValueError,match='PROCESS_CHANGED'):control.call('signal')
    assert control.signals==[]


def test_unavailable_pidfd_fails_closed_without_numeric_signal_fallback(control):
    control.code['os'].pidfd_open=lambda *_:(_ for _ in ()).throw(OSError('unsupported pidfd'))
    with pytest.raises(OSError):control.call('signal')
    assert control.signals==[]
