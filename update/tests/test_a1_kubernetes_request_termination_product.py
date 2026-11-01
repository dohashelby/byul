"""LIF10 product/HTTP/native/Ref contracts; kubectl and serving are EXAMPLE."""
import ast
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
from guard.adapters.vllm_request_termination import SCOPES, project_native
from guard.cli import app
from guard.executors import kubernetes_request_termination as termination
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.bounded import capture_pipes
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_stream_probe import _server
from tests.test_kubernetes_request_binding import env as http_env,target as http_target
from tests.test_kubernetes_sampling import World as PodWorld
from tools.build_kubernetes_request_termination_pack import derive

PRIVATE="EXAMPLE_LIF10_PRIVATE"


class World(BaseWorld):
    def add_native(self,rid):
        request=sha256_hex(rid.encode());native_text="native-"+rid;native=sha256_hex(native_text.encode())
        token=sensor._ROUTE_REQUEST.set({"pid":sensor.os.getpid(),"closed":False,"request":request,
            "invocation":sha256_hex(("invocation-"+rid).encode()),"path":sha256_hex(b'/v1/completions'),
            "root":sha256_hex(b''),"method":sha256_hex(b'POST')})
        try:
            observer=sensor.Observer({"session":self.group.prepared['session']},'b'*64)
            selection=observer.request_selection('InputProcessor.assign_request_id',None,True,{'request_id':native_text})
        finally:sensor._ROUTE_REQUEST.reset(token)
        def change(rows):
            start=rows[-1]['monotonic_ns'];added=[]
            def put(event,payload,delta):added.append({'event':event,'payload':payload,'monotonic_ns':start+delta})
            ready={r['payload'].get('module') for r in rows if r['event']=='MODULE'}
            for module in ('vllm.v1.engine.output_processor','vllm.v1.worker.gpu_model_runner'):
                if module not in ready:put('MODULE',{'module':module,'verified':True,'source_sha256':sensor.SOURCE_SHA[module],'code_sha256':'c'*64},0)
            state={'request_sha256':native,'request_present':None,'output_state_present':None,'batch_row_present':None,'status_code':None}
            def native_payload(point,metadata=None,**states):
                module='vllm.v1.engine.input_processor' if point.startswith('InputProcessor') else 'vllm.v1.engine.output_processor' if point.startswith('OutputProcessor') else 'vllm.v1.worker.gpu_model_runner'
                return {'point':point,'phase':'RETURN','source_sha256':sensor.SOURCE_SHA[module],'requests':[{**state,**states}],
                    'metadata':metadata or {},'selection_observed':True,'observation_started_ns':0,'observation_finished_ns':0}
            put('NATIVE',native_payload('InputProcessor.assign_request_id',{'external_request_sha256':request,'asgi_request_sha256':request}),10)
            put('SELECTION',{'point':'InputProcessor.assign_request_id','phase':'RETURN',
                'source_sha256':sensor.SOURCE_SHA['vllm.v1.engine.input_processor'],'requests':[],
                'metadata':{'request_selection':selection},'selection_observed':False,'observation_started_ns':0,'observation_finished_ns':0},11)
            if rid.endswith('-disconnect') and self.mode!='native-unlinked':
                if self.mode=='already-finished':put('ASGI',{'request_sha256':request,'phase':'APP_RETURN','more_body':None},12)
                put('ASGI',{'request_sha256':request,'phase':'DISCONNECT_RECEIVED','more_body':None},20)
                put('NATIVE',native_payload('OutputProcessor._finish_request',output_state_present=False),30)
                put('NATIVE',native_payload('GPUModelRunner._update_states',batch_row_present=True),50_000_020)
                put('ASGI',{'request_sha256':request,'phase':'APP_RETURN','more_body':None},50_000_030)
            rows[-1:-1]=added;rows[-1]['monotonic_ns']=max(rows[-1]['monotonic_ns'],added[-1]['monotonic_ns'])
        self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],change)

    def runner(self,argv,**kwargs):
        if 'exec' not in argv or 'input_bytes' not in kwargs:return super().runner(argv,**kwargs)
        spec=json.loads(kwargs['input_bytes']);request,=spec['requests'];self.sent.append(request)
        slot=request['id'].removeprefix('lif10-');self.add_native(request['headers']['X-Request-Id'])
        assert request['body']=={'model':'fixture-model','prompt':'hello','max_tokens':8 if slot=='disconnect' else 1,'temperature':0,'stream':slot=='disconnect'}
        if slot=='after' and self.mode=='after-timeout':raise TimeoutError(PRIVATE)
        body='d' if slot=='disconnect' else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE,'finish_reason':'length'}]})
        row={'id':request['id'],'mode':request['mode'],'method':'POST','outcome':200,'body':body,'body_bytes':len(body.encode()),
            'body_sha256':sha256_hex(body.encode()),'capture_complete':slot!='disconnect','eof':slot!='disconnect',
            'terminal_seen':False,'body_truncated':False,'utf8_valid':True,'content_type':'text/event-stream' if slot=='disconnect' else 'application/json'}
        if slot=='disconnect':
            assert request['read_profile']=={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
            row.update(client_closed=True,client_close_ms=3,read_count=1,read_pause_count=0,
                read_observations=[{'seq':0,'started_ms':1,'finished_ms':2,'bytes':1}],stop_reason='client_close',relative_clock='monotonic')
            if self.mode=='close-unverified':row['client_closed']=False
            if self.mode=='wrong-response-id':row['id']='before'
        if self.mode=='before-shape' and slot=='before':row['body']='{}'
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if self.mode=='http-cleanup' and slot=='after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
        return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}


def inputs(tmp_path,group,target,mode):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision'
    group.env=environment.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode)
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'termination_environment_sha256':group.env.ref().sha256,'termination_target_sha256':sha256_hex(canonical_json(world.target)),
        'termination_validity_start':(now-timedelta(minutes=1)).isoformat(),'termination_validity_end':(now+timedelta(minutes=1)).isoformat(),
        'termination_max_delay_ms':20,'termination_required_scopes':sorted(SCOPES)}
    shutil.copytree(group.manifest_path.parent,tmp_path/'prepared')
    return world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','after-timeout','seal-failure','http-cleanup','attachment','native-unlinked',
    'native-one-missing','native-partial','before-shape','already-finished','wrong-response-id','close-unverified','engine-replaced','actual-lab-false'])
def test_registered_cli_native_deadline_candidates_and_exact_ref_roundtrip(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,mode)
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['LIF-10']},values={'LIF-10':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nrequest_termination="kubernetes"\n[adapter]\nname="vllm"\n[request_termination]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(x for x in plan.items if x.check_id=='LIF-10').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,a).value=='UNKNOWN' for a in ('security','functional','performance'))
    assert e.observations['termination_scope_complete'].value is False and e.observations['worker_gpu_release_verified'].value is False
    trials=e.observations['termination_attempts'].value;events=e.observations['termination_native_events'].value
    candidates=e.observations['termination_deadline_conflict_candidates'].value
    if mode=='actual-lab-false':assert not world.sent and not trials
    else:
        assert len(world.sent)=={'before-shape':2,'engine-replaced':1,'wrong-response-id':2}.get(mode,3)
        assert world.native_ops.count('seal')==(0 if mode=='engine-replaced' else 1)
    linked=mode in {'normal','after-timeout','seal-failure','http-cleanup','attachment','wrong-response-id','close-unverified','native-one-missing'}
    assert bool(e.observations['termination_binding_observed'].value)==linked
    if linked:
        assert events and candidates and all(c['confirmed_violation'] is False for c in candidates)
        assert all(c['scope']=='worker_batch' and c['declared_elapsed_ns']>20_000_000 for c in candidates)
    else:assert not candidates
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_keeps_original_scope_criteria_and_seventy_six_bytes(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_LIF10_complete']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['LIF-10'],name)==getattr(pack.checks['LIF-10'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/LIF-10.yaml'}:
            assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('damage',['max-bool','temperature-bool','prompt','path','header','mode','delay','read-profile','chunk','interval','read-bool','timeout','after-first'])
def test_closed_profile_rejects_invalid_envelope_before_any_io(tmp_path,group,native_target,damage):
    world,_,_,_=inputs(tmp_path,group,native_target,'normal')
    transport=http.KubernetesHTTP(world.target,group.env,duration=60,runner=world.runner,profile=http.TERMINATION_PROFILE)
    request={'id':'lif10-before','method':'POST','mode':'json','url':world.target['engine']['trusted_url']+'/v1/completions',
        'headers':{'X-Request-Id':'byul-lif10-'+'ab'*16+'-before'},
        'body':{'model':'fixture-model','prompt':'hello','max_tokens':1,'temperature':0,'stream':False}}
    timeout=10
    if damage=='max-bool':request['body']['max_tokens']=True
    elif damage=='temperature-bool':request['body']['temperature']=False
    elif damage=='prompt':request['body']['prompt']=PRIVATE
    elif damage=='path':request['url']+='/other'
    elif damage=='header':request['headers']['Authorization']=PRIVATE
    elif damage=='mode':request['mode']='sse'
    elif damage=='delay':request['read_delay_ms']=True
    elif damage=='timeout':timeout=31
    elif damage=='after-first':request['id']='lif10-after';request['headers']['X-Request-Id']=request['headers']['X-Request-Id'].removesuffix('before')+'after'
    else:
        request['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
        if damage=='chunk':request['read_profile']['chunk_bytes']=2
        elif damage=='interval':request['read_profile']['interval_ms']=0
        elif damage=='read-bool':request['read_profile']['close_after_reads']=True
    with pytest.raises(http.KubernetesBindingError):transport('kubernetes-probe',[request],timeout)
    assert not world.sent and not world.native_ops and not world.pods.calls


@pytest.mark.parametrize('profile',[http.PROFILE,http.REQUEST_BINDING_PROFILE,http.VALIDATION_ERROR_PROFILE,http.MODEL_AUTHORIZATION_PROFILE,http.ONLINE_INPUT_PROFILE])
def test_no_other_profile_admits_lif10_ids(tmp_path,group,native_target,profile):
    world,_,_,_=inputs(tmp_path,group,native_target,'normal')
    transport=http.KubernetesHTTP(world.target,group.env,duration=60,runner=world.runner,profile=profile)
    with pytest.raises(http.KubernetesBindingError):transport('kubernetes-probe',[{'id':'lif10-before'}],10)
    assert not world.sent and not world.native_ops


@pytest.mark.parametrize('key,value',[('termination_max_delay_ms',True),('termination_max_delay_ms',-1),('termination_required_scopes',None),
    ('termination_required_scopes',['gpu_transfers','gpu_transfers']),('termination_required_scopes',['unknown']),('termination_target_sha256','bad')])
def test_policy_types_are_required_and_not_derived_from_observation(tmp_path,group,native_target,key,value):
    _,values,_,_=inputs(tmp_path,group,native_target,'normal');values[key]=value
    with pytest.raises(ValueError):termination.normalize_policy(values)


def test_missing_selected_request_cannot_match_null_native_context():
    result=project_native({'recordings':[],'recorded_files_covered':True},None,1,[])
    assert not result['binding_observed'] and result['diagnostic_codes']==['TERMINATION_SELECTED_REQUEST_UNAVAILABLE']


def test_exact_pinned_stdlib_probe_closes_only_selected_client_and_keeps_normal_controls():
    def handle(conn,raw,index,stop):
        head,_,body=raw.partition(b'\r\n\r\n');value=json.loads(body)
        assert head.startswith(b'POST /v1/completions HTTP/1.1')
        assert value=={'model':'fixture-model','prompt':'hello','max_tokens':8 if index==1 else 1,'temperature':0,'stream':index==1}
        data=b'data: '+PRIVATE.encode()+b'\n\n' if index==1 else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        content=b'text/event-stream' if index==1 else b'application/json'
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: '+content+b'\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data)
    with _server(handle) as (url,accepted):
        target=http_target();target['engine']['trusted_url']=url;world=PodWorld(target)
        def runner(argv,**kwargs):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
            return world.runner(argv,**kwargs)
        transport=http.KubernetesHTTP(target,http_env(),duration=30,runner=runner,profile=http.TERMINATION_PROFILE)
        replies=[]
        for slot in ('before','disconnect','after'):
            request={'id':'lif10-'+slot,'method':'POST','mode':'sse' if slot=='disconnect' else 'json',
                'url':url+'/v1/completions','headers':{'X-Request-Id':'byul-lif10-'+'ab'*16+'-'+slot},
                'body':{'model':'fixture-model','prompt':'hello','max_tokens':8 if slot=='disconnect' else 1,'temperature':0,'stream':slot=='disconnect'}}
            if slot=='disconnect':request['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
            reply,=transport('kubernetes-probe',[request],10);replies.append(reply)
        assert len(accepted)==3
    before,closed,after=replies
    assert before['capture_complete'] and after['capture_complete']
    assert closed['body']=='d' and closed['client_closed'] and not closed['capture_complete']
    report=transport.report()
    assert not report['collection_gap'] and not report['cleanup_errors'] and not report['native_termination_verified']
    assert next(r for r in report['requests'] if r['id']=='lif10-disconnect')['expected_client_close']
    assert PRIVATE not in json.dumps(report)


def projection_packet():
    request,native='a'*64,'b'*64
    def row(event,payload,seq,stamp):return {'event':event,'payload':payload,'seq':seq,'monotonic_ns':stamp}
    context={'pid':42,'start_ticks':100,'boot_sha256':'c'*64,'time_namespace':'time:[1]','time_offsets_sha256':'d'*64}
    selection={'asgi_request_sha256':request,'asgi_invocation_sha256':'e'*64,'selected_request_id_sha256':native,
        'request_id_observed':True,'entry_path_sha256':sha256_hex(b'/v1/completions'),'entry_method_sha256':sha256_hex(b'POST')}
    assignment={'point':'InputProcessor.assign_request_id','phase':'RETURN','requests':[{'request_sha256':native}],
        'metadata':{'external_request_sha256':request,'asgi_request_sha256':request}}
    rows=[row('START',{'context':context},0,0),row('NATIVE',assignment,1,1),
        row('SELECTION',{'point':'InputProcessor.assign_request_id','phase':'RETURN','metadata':{'request_selection':selection}},2,2),
        row('ASGI',{'request_sha256':request,'phase':'DISCONNECT_RECEIVED'},3,3),
        row('NATIVE',{'point':'GPUModelRunner._update_states','phase':'RETURN','requests':[{'request_sha256':native,'batch_row_present':True}],'metadata':{}},4,50_000_003)]
    recording={'session_owner_match':True,'listed_file_identity_match':True,'capture':{'records':rows,'process_context_verified':True,'image_digest_verified':False}}
    return {'recordings':[recording],'recorded_files_covered':True},request


@pytest.mark.parametrize('damage',['none','missing-invocation','wrong-path','duplicate-binding','prior-return','other-clock','unverified-process'])
def test_binding_and_clock_gaps_never_create_reclaim_or_independent_fail(damage):
    packet,request=projection_packet();recording=packet['recordings'][0];rows=recording['capture']['records']
    selection=rows[2]['payload']['metadata']['request_selection']
    if damage=='missing-invocation':selection['asgi_invocation_sha256']=None
    elif damage=='wrong-path':selection['entry_path_sha256']='0'*64
    elif damage=='duplicate-binding':packet['recordings'].append(deepcopy(recording))
    elif damage=='prior-return':rows.insert(3,{'event':'ASGI','payload':{'request_sha256':request,'phase':'APP_RETURN'},'seq':2.5,'monotonic_ns':2})
    elif damage in {'other-clock','unverified-process'}:
        other=deepcopy(recording);other['capture']['records']=[deepcopy(rows[0]),rows.pop()]
        if damage=='other-clock':other['capture']['records'][0]['payload']['context']['time_namespace']='time:[2]'
        else:other['capture']['process_context_verified']=False
        packet['recordings'].append(other)
    result=project_native(packet,request,20,['worker_batch'])
    assert result['binding_observed']==(damage in {'none','other-clock','unverified-process'})
    assert bool(result['deadline_conflict_candidates'])==(damage=='none')
    assert not result['whole_scope_complete'] and not result['worker_gpu_release_verified']
    assert all(c['confirmed_violation'] is False for c in result['deadline_conflict_candidates'])
    if damage in {'other-clock','unverified-process'}:
        assert result['events'] and result['events'][0]['declared_elapsed_ns'] is None
