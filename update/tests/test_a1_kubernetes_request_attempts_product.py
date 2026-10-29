"""REQ12 canonical CLI/native selection/Ref procedure; serving is EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.cli import app
from guard.executors import kubernetes_request_attempts as attempts
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.bounded import capture_pipes
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_kubernetes_request_binding import target as public_target,env as public_env
from tests.test_kubernetes_sampling import World as PodWorld
from tests.test_stream_probe import _server
from tools.build_kubernetes_request_attempts_pack import derive

PRIVATE="EXAMPLE_REQ12_PRIVATE"


class World(BaseWorld):
    def add_attempt(self,rid,session,slot):
        wire=sha256_hex(rid.encode());native_text='native-'+session
        if self.mode=='duplicate-native' and slot in {'disconnect','replacement'}:native_text='native-shared'
        native=sha256_hex(native_text.encode())
        invocation=sha256_hex(('invocation-'+('shared' if self.mode=='shared-invocation' and slot in {'disconnect','replacement'} else session)).encode())
        token=sensor._ROUTE_REQUEST.set({'pid':sensor.os.getpid(),'closed':False,'request':wire,'invocation':invocation,
            'path':sha256_hex(b'/v1/completions'),'root':sha256_hex(b''),'method':sha256_hex(b'POST')})
        try:
            observer=sensor.Observer({'session':self.group.prepared['session']},'b'*64)
            selected=observer.request_selection('InputProcessor.assign_request_id',None,True,
                {'request_id':native_text,'session_id':None if self.mode=='native-unlinked' else session})
        finally:sensor._ROUTE_REQUEST.reset(token)
        def change(rows):
            start=rows[-1]['monotonic_ns'];added=[]
            def put(event,payload,delta):added.append({'event':event,'payload':payload,'monotonic_ns':start+delta})
            module='vllm.v1.engine.output_processor'
            if not any(r['event']=='MODULE' and r['payload']['module']==module for r in rows):
                put('MODULE',{'module':module,'verified':True,'source_sha256':sensor.SOURCE_SHA[module],'code_sha256':'c'*64},0)
            state={'request_sha256':native,'request_present':None,'output_state_present':None,'batch_row_present':None,'status_code':None}
            def payload(point,metadata,states=None):
                source='vllm.v1.engine.input_processor' if point.startswith('InputProcessor') else module
                return {'point':point,'phase':'RETURN','source_sha256':sensor.SOURCE_SHA[source],'requests':[{**state,**(states or {})}],
                    'metadata':metadata,'selection_observed':True,'observation_started_ns':0,'observation_finished_ns':0}
            put('NATIVE',payload('InputProcessor.assign_request_id',{'external_request_sha256':wire,'asgi_request_sha256':wire}),1)
            put('SELECTION',{'point':'InputProcessor.assign_request_id','phase':'RETURN','source_sha256':sensor.SOURCE_SHA['vllm.v1.engine.input_processor'],
                'requests':[],'metadata':{'request_selection':selected},'selection_observed':False,'observation_started_ns':0,'observation_finished_ns':0},2)
            put('NATIVE',payload('OutputProcessor.add_request',{}, {'output_state_present':True}),3)
            if slot=='disconnect':put('ASGI',{'request_sha256':wire,'phase':'DISCONNECT_RECEIVED','more_body':None},4)
            if slot in {'disconnect','replacement'}:
                put('ASGI',{'request_sha256':wire,'phase':'BODY_SEND_RETURN','more_body':True},5)
                put('ASGI',{'request_sha256':wire,'phase':'APP_RETURN','more_body':None},6)
            rows[-1:-1]=added;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],added[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)

    def runner(self,argv,**kwargs):
        if 'exec' not in argv or 'input_bytes' not in kwargs:return super().runner(argv,**kwargs)
        request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request)
        slot=request['id'].removeprefix('req12-');body=request['body']
        assert set(body)=={'model','prompt','max_tokens','temperature','stream','session_id'} and body['prompt']=='hello'
        self.add_attempt(request['headers']['X-Request-Id'],body['session_id'],slot)
        if slot=='after' and self.mode=='after-timeout':raise TimeoutError(PRIVATE)
        closed=slot=='disconnect';text='d' if closed else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE,'finish_reason':'length'}]})
        row={'id':request['id'],'mode':request['mode'],'method':'POST','outcome':200,'body':text,'body_bytes':len(text.encode()),
            'body_sha256':sha256_hex(text.encode()),'body_truncated':False,'utf8_valid':True,'capture_complete':not closed,'eof':not closed,
            'terminal_seen':False,'content_type':'text/event-stream' if closed else 'application/json'}
        if closed:
            row.update(client_closed=True,client_close_ms=3,read_count=1,read_pause_count=0,
                read_observations=[{'seq':0,'started_ms':1,'finished_ms':2,'bytes':1}],stop_reason='client_close',relative_clock='monotonic')
            if self.mode=='close-unverified':row['client_closed']=False
            if self.mode=='wrong-response-id':row['id']='normal-first'
        if self.mode=='before-shape' and slot=='normal-first':row['body']='{}'
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if self.mode=='http-cleanup' and slot=='after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
        return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}


def inputs(tmp_path,group,target,mode):
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision';group.env=env.seal()
    target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode);receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'attempt_environment_sha256':group.env.ref().sha256,'attempt_target_sha256':sha256_hex(canonical_json(world.target)),
        'attempt_validity_start':(now-timedelta(minutes=1)).isoformat(),'attempt_validity_end':(now+timedelta(minutes=1)).isoformat()}
    shutil.copytree(group.manifest_path.parent,tmp_path/'prepared')
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','native-unlinked','shared-invocation','duplicate-native','native-one-missing','native-partial',
    'before-shape','after-timeout','close-unverified','wrong-response-id','engine-replaced','seal-failure','attachment','http-cleanup','actual-lab-false'])
def test_existing_plugin_canonical_cli_attempts_and_exact_refs(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['REQ-12']},values={'REQ-12':values})
    for name,data in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(data))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequest_termination="kubernetes"\n[adapter]\nname="vllm"\n[request_termination]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(i for i in plan.items if i.check_id=='REQ-12').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,a).value=='UNKNOWN' for a in ('security','functional','performance'))
    for key in ('late_delivery_verified','automatic_retry_verified','request_attempt_scope_complete'):assert e.observations[key].value is False
    trials=e.observations['request_attempt_trials'].value;bindings=e.observations['request_attempt_native_bindings'].value
    linked=mode in {'normal','shared-invocation','native-one-missing','after-timeout','seal-failure','attachment','http-cleanup'}
    assert e.observations['native_attempt_binding_observed'].value is linked
    assert bool(e.observations['request_attempt_conflict_candidates'].value)==(mode=='shared-invocation')
    assert all(c['confirmed_violation'] is False for c in e.observations['request_attempt_conflict_candidates'].value)
    assert all(row['attempt_case_id'] is None for row in e.observations['request_attempt_unbound_asgi_events'].value)
    assert len(world.sent)=={'actual-lab-false':0,'before-shape':3,'engine-replaced':1,'wrong-response-id':3,'close-unverified':4}.get(mode,5)
    if linked:
        assert len(bindings)==5
        byid={r['case_id']:r for r in trials}
        assert byid['req12-normal-first']['external_request_sha256']==byid['req12-normal-repeat']['external_request_sha256']
        assert byid['req12-disconnect']['external_request_sha256']==byid['req12-replacement']['external_request_sha256']
        assert len({t['auxiliary_session_sha256'] for t in trials})==5
    if mode=='normal':assert e.execution.status.value=='COMPLETED'
    if mode in {'native-one-missing','native-partial','after-timeout','attachment','seal-failure','http-cleanup','before-shape','close-unverified','wrong-response-id','engine-replaced','actual-lab-false'}:
        assert e.execution.status.value=='ABORTED'
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [o.ref() for o in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_preserves_both_branches_and_other_seventy_six_definition_bytes(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_REQ12_complete']
    for key in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['REQ-12'],key)==getattr(pack.checks['REQ-12'],key)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/REQ-12.yaml'}:
            assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


def request(transport,case,token='ab'*16):
    group='normal' if case.startswith('normal-') else 'shared' if case in {'disconnect','replacement'} else 'after'
    closed=case=='disconnect'
    value={'id':'req12-'+case,'method':'POST','mode':'sse' if closed else 'json','url':transport.endpoint.geturl()+'/v1/completions',
        'headers':{'X-Request-Id':'byul-req12-'+token+'-'+group},'body':{'model':'fixture-model','prompt':'hello','max_tokens':8 if closed else 1,
            'temperature':0,'stream':closed,'session_id':'byul-req12-'+token+'-'+case}}
    if closed:value['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
    return value


@pytest.mark.parametrize('damage',['session','sid-type','bool-count','stream-type','wrong-header','authorization','wrong-mode','other-path','interval','replacement-first','over-time','wrong-token'])
def test_closed_attempt_profile_invalid_request_never_reaches_io(damage):
    target=public_target();world=PodWorld(target);transport=http.KubernetesHTTP(target,public_env(),duration=60,runner=world.runner,profile=http.REQUEST_ATTEMPTS_PROFILE)
    value=request(transport,'normal-first');timeout=10
    if damage=='session':value['body']['session_id']+='foreign'
    elif damage=='sid-type':value['body']['session_id']=None
    elif damage=='bool-count':value['body']['max_tokens']=True
    elif damage=='stream-type':value['body']['stream']=0
    elif damage=='wrong-header':value['headers']['X-Request-Id']+='other'
    elif damage=='authorization':value['headers']['Authorization']=PRIVATE
    elif damage=='wrong-mode':value['mode']='sse'
    elif damage=='other-path':value['url']+='/other'
    elif damage=='interval':value['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':0,'close_after_reads':1}
    elif damage=='replacement-first':value=request(transport,'replacement')
    elif damage=='over-time':timeout=31
    elif damage=='wrong-token':value['body']['session_id']=value['body']['session_id'].replace('ab'*16,'ef'*16)
    with pytest.raises(http.KubernetesBindingError):transport('kubernetes-probe',[value],timeout)
    assert not world.calls


def test_real_pinned_probe_repeated_ids_and_distinct_auxiliary_sessions():
    seen=[]
    def handle(conn,raw,index,stop):
        head,_,wire=raw.partition(b'\r\n\r\n');body=json.loads(wire)
        seen.append((next(h for h in head.split(b'\r\n') if h.lower().startswith(b'x-request-id:')),body['session_id']))
        value=b'data: '+PRIVATE.encode()+b'\n\n' if body['stream'] else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        content=b'text/event-stream' if body['stream'] else b'application/json'
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: '+content+b'\r\nContent-Length: '+str(len(value)).encode()+b'\r\nConnection: close\r\n\r\n'+value)
    with _server(handle) as (url,accepted):
        target=public_target();target['engine']['trusted_url']=url;world=PodWorld(target)
        def runner(argv,**kw):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kw)
            return world.runner(argv,**kw)
        transport=http.KubernetesHTTP(target,public_env(),duration=30,runner=runner,profile=http.REQUEST_ATTEMPTS_PROFILE)
        for slot in attempts.CASES:transport('kubernetes-probe',[request(transport,slot)],10)
        assert len(accepted)==5
    assert seen[0][0]==seen[1][0] and seen[2][0]==seen[3][0] and len({s for _,s in seen})==5
    report=transport.report();assert not report['collection_gap'] and not report['native_attempt_delivery_verified']
    assert PRIVATE not in json.dumps(report)
