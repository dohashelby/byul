"""One small real loopback group; separate cap regressions cover product limits."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import hashlib
import os
import subprocess
import sys
import threading
import time
from uuid import uuid4
import pytest
from tools import selected_gateway_mixed_budget as tool


def config():
    return {'schema':'byul-gateway-mixed-budget/1','expected_scope':{'policy_sha256':'policy','target_id':'target','run_id':'run','target_instance_id':'generation'},
            'principal_a':'client-a','principal_b':'client-b','normal_tokens':8,'long_tokens':32,'baseline_count':1,'rounds':1,
            'global_concurrency_limit':2,'expected_connection_limit':64,'normal_slo_ms':1000,'required_underload_successes':1,
            'request_timeout_seconds':2,'max_group_seconds':15,'observe_load_probes':25,'observe_interval_ms':5,
            'recovery_probe_count':1,'recovery_interval_ms':1,'caps_refs':[],'lif010_refs':[]}


@contextmanager
def server(starve=False):
    lock=threading.Lock();state={'active':0,'records':{},'posts':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,value,**headers):
            raw=json.dumps(value).encode();self.send_response(status);self.send_header('Content-Length',str(len(raw)))
            for key,val in headers.items():self.send_header(key,val)
            self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            if self.path=='/api/status':
                with lock:active=state['active']
                value={'policy':{'sha256':'policy','target_id':'target'},'run_id':'run',
                       'target':{'instance':{'current':{'instance_id':'generation'}}},'auth':{'protected_routes':['GET /api/*']},
                       'concurrency':{'limit':2,'in_flight':active},'connections':{'limit':64}}
            elif self.path=='/api/policy':value={'current':{'sha256':'policy','target_id':'target','output_max_tokens':256}}
            else:value={'gateway_records':state['records'].get(self.path.rsplit('/',1)[1],[])}
            self.send(200,value)
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));rid=uuid4().hex
            principal='client-a' if self.headers['Authorization']=='Bearer EXAMPLE_A' else 'client-b'
            long=body['max_tokens']==32
            with lock:
                active=state['active'];denied=active>=2 or starve and not long and active>0
                if not denied:state['active']+=1
                state['posts'].append({'principal':principal,'tokens':body['max_tokens'],'active_before':active,'denied':denied})
            base={'request_id':rid,'run_id':'run','target_id':'target','target':{'instance_id':'generation'},'policy':{'sha256':'policy'},
                  'auth':{'state':'authenticated','principal_id':principal},'decision':{'result':'DENIED' if denied else 'ALLOWED',
                  'http_status':429 if denied else 200,'reason_code':'CONCURRENCY_LIMIT' if denied else 'ALLOWED'},'delivery':{'attempted':not denied}}
            state['records'][rid]=[{**base,'phase':'started','observation_session_id':'session',
                                     'recorded_at':datetime.now(timezone.utc).isoformat()}]
            try:
                if denied:self.send(429,{'error':{'code':'CONCURRENCY_LIMIT'}},**{'X-Request-ID':rid})
                elif body.get('stream'):
                    self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('X-Request-ID',rid);self.end_headers()
                    if long:time.sleep(.15)
                    # Model one event per generated token plus role/finish/DONE.
                    wire=(b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                          + b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'*body['max_tokens']
                          + b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n\ndata: [DONE]\n\n')
                    self.wfile.write(wire);self.wfile.flush()
                else:self.send(200,{'choices':[{'message':{'content':'hello'}}]},**{'X-Request-ID':rid})
            finally:
                with lock:
                    if not denied:state['active']-=1
                state['records'][rid].append({**base,'phase':'final','final_outcome':'completed',
                                             'observation_session_id':'session',
                                             'recorded_at':datetime.now(timezone.utc).isoformat()})
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);http.daemon_threads=True
    thread=threading.Thread(target=http.serve_forever);thread.start()
    try:yield 'http://127.0.0.1:'+str(http.server_port),state
    finally:http.shutdown();http.server_close();thread.join()


def results(doc):return {c['id']:c['result'] for c in doc['cases']}


def test_actual_loopback_mixed_principals_saturation_and_recovery():
    with server() as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    cases=results(doc)
    assert cases['LIF003-BASELINE']=='passed'
    assert cases['LIF003-NORMAL-OPPORTUNITY']=='passed'
    assert cases['LIF003-SATURATION']=='passed'
    assert cases['LIF003-RECOVERY']=='passed'
    assert len(state['posts'])==doc['declared_total_request_bound']==8
    assert all(p['tokens'] in (8,32) for p in state['posts'])
    long=[c for c in doc['cases']if c['id'].startswith('LIF003-LONG-')]
    assert all(c['result']=='passed'for c in long)
    assert all(r['event_count']==35 and r['stop_reason']=='eof'for c in long for r in c['facts']['stream_captures'])
    assert doc['declared_output_token_upper_bound']==136
    assert doc['lif010_connection']['state']=='not_measured_by_this_tool'
    assert cases['LIF003-LIF010-LINK']=='unknown'
    assert doc['security_verdict'] is None and 'conditional' in doc['request_gpu_accounting']
    assert 'EXAMPLE_A' not in json.dumps(doc) and 'EXAMPLE_B' not in json.dumps(doc)


def test_normal_principal_starvation_never_passes_opportunity():
    with server(starve=True) as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    assert results(doc)['LIF003-NORMAL-OPPORTUNITY']=='failed'
    assert results(doc)['LIF003-SATURATION']=='passed'
    assert results(doc)['LIF003-RECOVERY']=='passed'


def test_reference_tamper_rejected_before_network(tmp_path,monkeypatch):
    cfg=config();path=tmp_path/'cap.json';path.write_text('{}')
    cfg['caps_refs']=[{'path':str(path),'sha256':'0'*64,'scope':'existing caps matrix'}]
    monkeypatch.setattr(tool,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    with pytest.raises(ValueError):tool.run('http://127.0.0.1:1','m','a','b','v',cfg)


@pytest.mark.parametrize('kind', ['fifo','symlink','oversize'])
def test_reference_regular_file_boundary_before_any_post(tmp_path,monkeypatch,kind):
    cfg=config();path=tmp_path/'cap.json'
    if kind=='fifo':os.mkfifo(path)
    elif kind=='symlink':
        target=tmp_path/'original';target.write_text('{}');path.symlink_to(target)
    else:
        with path.open('wb') as f:f.truncate(8*1024*1024+1)
    cfg['caps_refs']=[{'path':str(path),'sha256':'0'*64,'scope':'existing cap result'}]
    if kind=='fifo':
        script="""import json,sys
from tools import selected_gateway_mixed_budget as tool
tool.request=lambda *a,**k: (_ for _ in ()).throw(AssertionError('must not dispatch'))
try:tool.run('http://127.0.0.1:1','m','a','b','v',json.load(sys.stdin))
except (ValueError,OSError):sys.exit(0)
sys.exit(1)
"""
        result=subprocess.run([sys.executable,'-c',script],input=json.dumps(cfg),text=True,capture_output=True,timeout=2)
        assert result.returncode==0
        return
    monkeypatch.setattr(tool,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    with pytest.raises((ValueError,OSError)):tool.run('http://127.0.0.1:1','m','a','b','v',cfg)


def test_reference_growth_after_fstat_keeps_bounded_read_and_rejects(tmp_path,monkeypatch):
    from tools import selected_acceptance_run as source
    path=tmp_path/'cap.json';path.write_text('{}');cfg=config()
    cfg['caps_refs']=[{'path':str(path),'sha256':'0'*64,'scope':'existing cap result'}]
    original=source.os.fstat;calls=[0]
    def grow(fd):
        before=original(fd);calls[0]+=1
        if calls[0]==1:
            with path.open('ab') as f:f.truncate(8*1024*1024+1)
        return before
    monkeypatch.setattr(source.os,'fstat',grow)
    monkeypatch.setattr(tool,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    with pytest.raises(ValueError):tool.run('http://127.0.0.1:1','m','a','b','v',cfg)


def test_regular_reference_keeps_exact_approved_hash(tmp_path):
    path=tmp_path/'cap.json';raw=b'{"state":"EXAMPLE"}\n';path.write_bytes(raw)
    cfg=config();row={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'scope':'existing cap result'}
    cfg['caps_refs']=[row]
    assert tool.validate(cfg)['caps_refs']==[row] and path.read_bytes()==raw


def test_cli_config_fifo_rejected_without_waiting_for_writer(tmp_path):
    path=tmp_path/'config';os.mkfifo(path)
    result=subprocess.run([sys.executable,tool.__file__,'--gateway','http://127.0.0.1:1','--model','m',
        '--config',str(path),'--inference-a-env','MISSING_TEST_A','--inference-b-env','MISSING_TEST_B',
        '--viewer-env','MISSING_TEST_VIEWER','--out',str(tmp_path/'new')],text=True,capture_output=True,timeout=2)
    assert result.returncode==2 and json.loads(result.stdout)['error_type']=='ValueError'


def test_unapproved_connection_cap_never_dispatches():
    cfg=config();cfg['expected_connection_limit']=65
    with server() as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',cfg)
    assert doc['execution']=='not_run' and not state['posts']


def test_scope_change_stops_group_and_preserves_prior_result(monkeypatch):
    original=tool.status_scope;count=[0]
    def changed(value):
        count[0]+=1;scope=original(value)
        if count[0]>1:scope['run_id']='other-run'
        return scope
    monkeypatch.setattr(tool,'status_scope',changed)
    with server() as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    assert results(doc)['LIF003-BASELINE']=='passed'
    assert results(doc)['LIF003-GROUP-INCOMPLETE']=='unknown'
    assert len(state['posts'])<=2


@pytest.mark.parametrize('key,value',[('rounds',3),('long_tokens',128),('long_tokens',256),('normal_tokens',9),('max_group_seconds',181),('recovery_probe_count',4)])
def test_budget_bounds_fail_before_dispatch(key,value,monkeypatch):
    cfg=config();cfg[key]=value
    monkeypatch.setattr(tool,'request',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not dispatch')))
    with pytest.raises(ValueError):tool.run('http://127.0.0.1:1','m','a','b','v',cfg)


def test_delayed_b_after_a_completion_is_unknown(monkeypatch):
    original=tool.request;posts=[0]
    def delayed(method,url,**kwargs):
        if method=='POST' and kwargs.get('token')=='EXAMPLE_B':
            posts[0]+=1
            if posts[0]==2:time.sleep(.3)
        return original(method,url,**kwargs)
    monkeypatch.setattr(tool,'request',delayed)
    with server() as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    assert state['posts'][2]['active_before']==0
    assert results(doc)['LIF003-NORMAL-OPPORTUNITY']=='unknown'
    assert doc['mixed_observations'][0]['overlap']['gateway_interval_overlap']['verified'] is False


@pytest.mark.parametrize('kind',['missing','naive','reversed','session','scope','query_id'])
def test_unqualified_intervals_never_pass_opportunity(monkeypatch,kind):
    original=tool.request
    def altered(method,url,**kwargs):
        reply=original(method,url,**kwargs)
        if method=='GET' and '/api/requests/' in url:
            doc=json.loads(reply.body);rows=doc.get('gateway_records',[])
            for row in rows:
                if kind=='missing':row.pop('recorded_at',None)
                elif kind=='naive':row['recorded_at']='2026-11-08T00:00:00'
                elif kind=='reversed':row['recorded_at']='2026-11-09T00:00:00+00:00' if row['phase']=='started' else '2026-11-08T00:00:00+00:00'
                elif kind=='session':row['observation_session_id']=row['auth']['principal_id']
                elif kind=='scope':row['run_id']='other-run'
                else:row['request_id']='f'*32
            return type(reply)(reply.status,reply.headers,json.dumps(doc).encode())
        return reply
    monkeypatch.setattr(tool,'request',altered)
    with server() as (url,state):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    assert results(doc).get('LIF003-NORMAL-OPPORTUNITY')!='passed'


def test_original_128_token_stream_exceeds_unchanged_collector_event_budget():
    from guard.executors import stream_probe
    assert stream_probe.MAX_EVENTS==64
    with server() as (url,_):
        row,=stream_probe.collect([{'id':'original128','mode':'sse','method':'POST','url':url+'/v1/chat/completions',
            'headers':{'Authorization':'Bearer EXAMPLE_A'},'body':{'model':'test-model','messages':[{'role':'user','content':'fixed synthetic'}],
            'stream':True,'max_tokens':128}}],2)
    assert row['stop_reason']=='events_limit' and row['body_truncated']
    assert not row['capture_complete'] and not row['terminal_seen'] and len(row['events'])==64


def test_capture_limit_diagnostic_is_preserved_and_never_passes_load(monkeypatch):
    original=tool.collect
    def capped(specs,*args,**kwargs):
        rows=original(specs,*args,**kwargs)
        if specs[0]['body']['max_tokens']==32:
            for row in rows:row.update(stop_reason='events_limit',capture_complete=False,body_truncated=True,terminal_seen=False)
        return rows
    monkeypatch.setattr(tool,'collect',capped)
    with server() as (url,_):doc=tool.run(url,'test-model','EXAMPLE_A','EXAMPLE_B','EXAMPLE_VIEWER',config())
    long=[c for c in doc['cases']if c['id'].startswith('LIF003-LONG-')]
    assert all(c['result']=='failed'for c in long)
    assert all(r['stop_reason']=='events_limit'and r['body_truncated']for c in long for r in c['facts']['stream_captures'])
    assert results(doc)['LIF003-NORMAL-OPPORTUNITY']=='unknown'
