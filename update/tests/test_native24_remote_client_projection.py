"""Actual CLIENT HTTP bytes -> Lab.requests -> controlled actor -> real projector.

Loopback HTTP500 and EXAMPLE native packets; no engine fault, GPU or AWS.
"""
from copy import deepcopy
import hashlib,json,os,pathlib,subprocess,sys,threading,time
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
import pytest
from guard.executors import lab_activation as actor
from guard.adapters import vllm_fault_control as control
from tests.test_fault_projection24 import case,rehash,B,CLOCK,EXT,NONCE,SESSION,TARGET,OWNER
from tests.test_lab_activation_control_workflow import BASE

@pytest.mark.skipif(sys.platform!='linux',reason='actual production requester clock reads Linux procfs')
@pytest.mark.parametrize('kind,number',[('death',9),('delay',19),('exception',None),('communication-stall',None)])
@pytest.mark.parametrize('response_mode',['http-error','http-success','incomplete-error'])
def test_actual_remote_row_reaches_real_fault_projection(monkeypatch,kind,number,response_mode):
    received=threading.Event();release=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']));received.set()
            assert release.wait(10)
            body=b'{"error":{"message":"CPU fixture"}}'
            self.send_response(200 if response_mode=='http-success'else 500);self.send_header('Content-Length',str(len(body)+(10 if response_mode=='incomplete-error'else 0)));self.end_headers();self.wfile.write(body);self.close_connection=True
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    lab=actor.Lab({**BASE,'service_url':'http://127.0.0.1:'+str(server.server_port),'fault_control_session':SESSION,'fault_control_target_sha256':TARGET})
    ctx={'module':control,'binding':{'engine':dict(OWNER)},'env':SimpleNamespace(model_dump=lambda **_:{}),'target':{},'prepared':{}}
    monkeypatch.setattr(actor,'_control_context',lambda _:ctx)
    monkeypatch.setattr(lab,'normal_request',lambda _: {'outcome':200,'complete':True})
    monkeypatch.setattr(lab,'restore',lambda:True)
    def kubectl(*args,stdin=None,**kwargs):
        assert args[-1]==actor.CLIENT
        done=subprocess.run([sys.executable,'-B','-c',args[-1]],input=stdin,capture_output=True,check=True,timeout=15)
        return done.stdout
    monkeypatch.setattr(lab,'_kubectl',kubectl)
    fixture={};count=[0]
    def capture(_lab,_ctx,seal=False):
        count[0]+=1
        if count[0]==1:return {'packet':{},'transport':{'collection_gap':False},'sealed':False}
        return {'packet':deepcopy(fixture['args'][0] if seal else fixture['args'][2]['pre_signal_packet']),'transport':{'collection_gap':False},'sealed':seal}
    monkeypatch.setattr(actor,'_control_capture',capture)
    monkeypatch.setattr(actor,'_control_seed',lambda *_:{'pid':33,'start_ticks':100})
    def rpc(_lab,_ctx,action,token,target):
        if action=='ready':
            assert received.wait(10)
            clock={'boot_sha256':hashlib.sha256(pathlib.Path('/proc/sys/kernel/random/boot_id').read_bytes()).hexdigest(),'time_namespace':os.stat('/proc/self/ns/time').st_ino,'time_offsets_sha256':hashlib.sha256(pathlib.Path('/proc/self/timens_offsets').read_bytes()).hexdigest()}
            delta=time.monotonic_ns()-B
            def bind(v):
                if type(v)is dict:
                    result={k:bind(x) for k,x in v.items()}
                    if set(CLOCK)<=set(v):result.update(clock)
                    return result
                if type(v)is list:return [bind(x) for x in v]
                if type(v)is str:return token['external_request_sha256'] if v==EXT else token['nonce'] if v==NONCE else v
                if type(v)is int and B<=v<=B+31_000_000_000:return v+delta
                return v
            args=tuple(bind(v) for v in case(kind))
            for packet in (args[0],args[2]['pre_signal_packet']):
                for item in packet['recordings']:rehash(item)
            fixture['args']=args;return {'ready':True}
        if action in ('signal','activate'):
            release.set();return {k:v for k,v in fixture['args'][2].items() if k!='pre_signal_packet'}
        if action=='release':release.set()
        return {}
    monkeypatch.setattr(actor,'_control_rpc',rpc)
    try:
        actor._controlled_fault(lab,kind,5000,number,notification_ms=1000)
        document=lab.fault_evidence[-1]['document'];row=document['client'];projection=document['projection']
        assert document['error_kind'] is None,document
        assert row['connection_established']is True and row['request_sent']is True
        assert 'connection' not in row and 'requestSent' not in row
        assert row['clock_context']==fixture['args'][1]['clock_context']
        assert row['external_request_sha256']==document['expected_scope']['external_request_sha256']
        assert row['fault_nonce']==document['nonce']
        assert row['outcome']==(200 if response_mode=='http-success'else 500)
        assert row['complete']is (response_mode!='incomplete-error')
        assert projection['clock_verified'] and projection['request_fault_overlap_verified']
        if response_mode=='http-success':
            assert not projection['affected_request_error_verified'] and not projection['whole_kind_complete']
            assert 'exact_affected_request_error_response'in projection['missing']
        else:
            assert projection['affected_request_error_verified'] and projection['whole_kind_complete'],projection
            assert lab.missing==[]
        assert lab.violations==[]
    finally:
        release.set();server.shutdown();server.server_close();thread.join()

@pytest.mark.parametrize('field,legacy',[('connection_established','connection'),('request_sent','requestSent')])
@pytest.mark.parametrize('value',[False,None,0,1,'true'])
def test_conflicting_or_untyped_transport_fact_cannot_create_overlap(field,legacy,value):
    from guard.adapters.vllm_fault_projection import validate_fault_case
    args=case();args[1][field]=value
    assert args[1][legacy]is True
    result=validate_fault_case(*args)
    assert not result['request_fault_overlap_verified'] and not result['whole_kind_complete']
