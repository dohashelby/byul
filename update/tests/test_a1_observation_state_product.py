"""Same registered OBS02 CLI and native boundaries; all serving/API I/O is EXAMPLE."""
import ast
import asyncio
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
from guard.adapters.vllm_observation_state import COMPONENTS,project
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.bounded import capture_pipes
from guard.executors import kubernetes_observation_state as state
from guard.executors.session_seal import SESSION_SEALER
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_kubernetes_request_termination_product import World as BaseWorld
from tests.test_a1_native_attempt_binding import world as producer10,invoke as invoke10
from tests.test_a1_native_output_binding import world as producer11,invoke as invoke11
from tests.test_a1_attempt_binding_product import owned_loop
from tests.test_kubernetes_request_binding import env as public_env,target as public_target
from tests.test_kubernetes_sampling import World as PodWorld
from tests.test_stream_probe import _server
from tools.build_kubernetes_observation_state_pack import derive

PRIVATE='EXAMPLE_OBS02_PRIVATE'


class World(BaseWorld):
    def add_native(self,rid):
        if hasattr(self,'producer'):
            invoke=invoke11 if self.native_version==11 else invoke10
            self.loop.run_until_complete(invoke(self.producer,label=rid,receive_disconnect=rid.endswith('-disconnect')))
            self.group.raw_by_pid[42]=bytes(self.producer.fake.data)
        else:super().add_native(rid)
        if self.mode=='selection-missing':
            def remove(rows):
                rows[:]=[r for r in rows if not(r['event']=='SELECTION' and r['payload']['metadata']['request_selection'].get('asgi_request_sha256')==sha256_hex(rid.encode()))]
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],remove)
        if self.mode=='shared-native':
            def share(rows):
                for row in rows:
                    if row['event']=='SELECTION':row['payload']['metadata']['request_selection']['selected_request_id_sha256']='9'*64
                    if row['event']=='NATIVE':
                        for item in row['payload']['requests']:item['request_sha256']='9'*64
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],share)

    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request)
            slot=request['id'].removeprefix('obs02-');health=slot.startswith('health-');close=slot=='disconnect'
            if not health:self.add_native(request['headers']['X-Request-Id'])
            assert request['body']==(None if health else {'model':'fixture-model','prompt':'hello','max_tokens':8 if close else 1,'temperature':0,'stream':close})
            if self.mode=='after-timeout' and slot=='normal-after':raise TimeoutError(PRIVATE)
            body='d' if close else '' if health else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]})
            row={'id':request['id'],'mode':request['mode'],'method':request['method'],'outcome':200,
                'body':body,'body_bytes':len(body.encode()),'body_sha256':sha256_hex(body.encode()),
                'capture_complete':not close,'eof':not close,'body_truncated':False,'utf8_valid':True,'terminal_seen':False,
                'content_type':'text/event-stream' if close else 'application/json'}
            if close:
                row.update(client_closed=True,client_close_ms=3,read_count=1,read_pause_count=0,
                    read_observations=[{'seq':0,'started_ms':1,'finished_ms':2,'bytes':1}],stop_reason='client_close',relative_clock='monotonic')
            if self.mode=='before-failure' and slot=='normal-before':row['outcome']=503
            if self.mode=='before-shape' and slot=='normal-before':row.update(body='{}',body_bytes=2,body_sha256=sha256_hex(b'{}'))
            if self.mode=='health-partial' and slot=='health-after':row.update(capture_complete=False,eof=False)
            if self.mode=='wrong-response-id' and close:row['id']='foreign'
            if self.mode=='wrong-response-method' and slot=='health-before':row['method']='POST'
            if self.mode=='close-unverified' and close:row['client_closed']=False
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup' and slot=='health-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'exec' in argv and hasattr(self,'producer'):
            command=argv[argv.index('--')+1:]
            call=next(n for n in ast.walk(ast.parse(command[5])) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='compile')
            if ast.literal_eval(call.args[0])==SESSION_SEALER and not self.producer.writer.closed:
                self.producer.writer.close('SEALED');self.group.raw_by_pid[42]=bytes(self.producer.fake.data)
        return super().runner(argv,**kwargs)


def inputs(tmp_path,group,target,monkeypatch,mode,native_version=8,loop=None):
    if native_version!=8:
        location=tmp_path/'producer';location.mkdir()
        producer=(producer11 if native_version==11 else producer10).__wrapped__(location,monkeypatch)
        group.prepared=producer.prepared;group.manifest_path=producer.root/'assets/manifest.json'
        def header(rows):
            rows[0]['payload'].update(profile=producer.prepared['profile'],schema='guard-native-lifecycle-events/'+str(native_version),
                session=producer.prepared['session'],sensor_sha256=producer.prepared['assets']['guard_lifecycle_sensor.py']['sha256'],
                settings_sha256=producer.prepared['assets']['settings.json']['sha256'])
        group.raw_by_pid[43]=revised(group.raw_by_pid[43],header);group.raw_by_pid[42]=bytes(producer.fake.data)
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision';group.env=env.seal()
    target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode)
    if native_version!=8:world.producer=producer;world.native_version=native_version;world.loop=loop
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'observation_state_environment_sha256':group.env.ref().sha256,
        'observation_state_target_sha256':sha256_hex(canonical_json(world.target)),
        'observation_state_validity_start':(now-timedelta(minutes=1)).isoformat(),
        'observation_state_validity_end':(now+timedelta(minutes=1)).isoformat(),
        'observation_state_required_components':sorted(COMPONENTS)}
    shutil.copytree(group.manifest_path.parent,tmp_path/'prepared')
    return world,values,pack,receipt


def run_cli(tmp_path,world,values,pack,monkeypatch):
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if world.mode=='attachment':
        original=Path.open
        def broken(path,*args,**kw):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kw)
        monkeypatch.setattr(Path,'open',broken)
    pol=policy(pack.ref(),scope={'include':['OBS-02']},values={'OBS-02':values})
    for name,value in [('environment',world.group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nobservation_state="kubernetes"\n[adapter]\nname="vllm"\n[observation_state]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,expected=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==expected,(args,result.output,result.exception)
        assert PRIVATE not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN');assert next(i for i in plan.items if i.check_id=='OBS-02').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if world.mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common])
    cli(['verify',*common]);store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,k).value=='UNKNOWN' for k in ('security','functional','performance'))
    for k in ('state_scope_complete','independent_worker_state_verified','state_semantics_verified'):assert e.observations[k].value is False
    assert e.normal_control.succeeded is None
    assert PRIVATE not in canonical_json(e.to_dict()).decode()
    for a in e.artifacts:assert PRIVATE.encode() not in (tmp_path/'store'/a.path).read_bytes()
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg])
    assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])
    return e


@pytest.mark.parametrize('mode',['normal','before-failure','before-shape','after-timeout','health-partial','seal-failure','native-one-missing',
    'native-partial','native-census-gap','selection-missing','shared-native','http-cleanup','attachment','wrong-response-id','wrong-response-method','close-unverified','engine-replaced'])
def test_registered_cli_state_facts_partial_failure_and_exact_refs(tmp_path,group,native_target,monkeypatch,mode):
    world,values,pack,_=inputs(tmp_path,group,native_target,monkeypatch,mode)
    e=run_cli(tmp_path,world,values,pack,monkeypatch)
    trials=e.observations['state_http_trials'].value;byid={t['case_id']:t for t in trials}
    if mode=='normal':
        assert len(world.sent)==5 and e.execution.status.value=='COMPLETED'
        assert len(e.observations['state_native_bindings'].value)==3 and e.observations['state_native_events'].value
        assert byid['obs02-disconnect']['expected_client_close']
        assert all(x['case_id'] is None for x in e.observations['state_asgi_events'].value)  # legacy ASGI lacks invocation identity
    if mode in {'before-failure','before-shape'}:
        assert len(world.sent)==4 and 'obs02-disconnect' not in byid
        assert len(e.observations['state_conflict_candidates'].value)==1
    if mode=='selection-missing':assert not e.observations['state_native_bindings'].value
    if mode=='shared-native':
        assert len(e.observations['state_conflict_candidates'].value)==3
        assert all(r['case_id'] is None for r in e.observations['state_native_events'].value)
    if mode not in {'normal','selection-missing','shared-native'}:assert e.execution.status.value=='ABORTED'
    if mode not in {'engine-replaced','wrong-response-method'}:
        assert byid['obs02-health-before']['http_available'] is True
    assert all(x['confirmed_violation'] is False for x in e.observations['state_conflict_candidates'].value)


@pytest.mark.parametrize('version',[10,11])
def test_product_current_native_hook_and_same_cli_refs(tmp_path,group,native_target,monkeypatch,owned_loop,version):
    world,values,pack,_=inputs(tmp_path,group,native_target,monkeypatch,'normal',version,owned_loop)
    e=run_cli(tmp_path,world,values,pack,monkeypatch)
    assert e.execution.status.value=='COMPLETED' and len(e.observations['state_native_bindings'].value)==3
    events=e.observations['state_asgi_events'].value
    assert {x['case_id'] for x in events if x['invocation_binding_observed']}=={'obs02-normal-before','obs02-disconnect','obs02-normal-after'}
    if version==11:
        assert e.observations['state_output_events'].value and e.observations['state_output_links'].value
        assert {x['origin_case_id'] for x in e.observations['state_output_links'].value if x['origin_binding_observed']}=={'obs02-normal-before','obs02-disconnect','obs02-normal-after'}
        assert all(not x['client_receipt_verified'] and not x['confirmed_violation'] for x in e.observations['state_output_links'].value)


def request(transport,slot,token='ab'*16):
    health=slot.startswith('health-');close=slot=='disconnect'
    r={'id':'obs02-'+slot,'method':'GET' if health else 'POST','mode':'sse' if close else 'json',
        'url':transport.endpoint.geturl()+('/health' if health else '/v1/completions'),
        'headers':{'X-Request-Id':'byul-obs02-'+token+'-'+slot},'body':None if health else
            {'model':'fixture-model','prompt':'hello','max_tokens':8 if close else 1,'temperature':0,'stream':close}}
    if close:r['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
    return r


@pytest.mark.parametrize('damage',['wrong-first','method','mode','path','host','query','auth','header','token','body','extra','timeout','none','private','reader'])
def test_closed_public_request_rejection_before_any_io(damage):
    target=public_target();world=PodWorld(target)
    t=http.KubernetesHTTP(target,public_env(),duration=30,runner=world.runner,profile=http.OBSERVATION_STATE_PROFILE)
    r=request(t,'health-before');timeout=10
    if damage=='wrong-first':r=request(t,'normal-after')
    elif damage=='method':r['method']='POST'
    elif damage=='mode':r['mode']='sse'
    elif damage=='path':r['url']+='/other'
    elif damage=='host':r['url']=r['url'].replace(t.endpoint.hostname,'127.0.0.2')
    elif damage=='query':r['url']+='?private='+PRIVATE
    elif damage=='auth':r['headers']['Authorization']=PRIVATE
    elif damage=='header':r['headers']['X-Other']=PRIVATE
    elif damage=='token':r['headers']['X-Request-Id']='foreign'
    elif damage=='body':r['body']={}
    elif damage=='extra':r['private']=PRIVATE
    elif damage=='timeout':timeout=11
    elif damage=='none':r=None
    elif damage=='private':r['id']=PRIVATE
    elif damage=='reader':r['read_profile']={'profile':'other'}
    with pytest.raises(http.KubernetesBindingError):t('kubernetes-probe',[r],timeout)
    assert not world.calls and PRIVATE not in canonical_json(t.report()).decode()


def test_pack_preserves_three_branches_other_definitions_and_seed(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_OBS02_complete']
    for key in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['OBS-02'],key)==getattr(pack.checks['OBS-02'],key)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/OBS-02.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


def test_real_pinned_probe_health_and_completion_wire_contract():
    seen=[]
    def handle(conn,raw,index,stop):
        head,_,wire=raw.partition(b'\r\n\r\n');seen.append(head.split(b'\r\n')[0])
        health=head.startswith(b'GET ')
        body=None if health else json.loads(wire)
        assert body is None if health else body['max_tokens']==(8 if body['stream'] else 1)
        value=b'' if health else b'data: '+PRIVATE.encode()+b'\n\n' if body['stream'] else json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        content=b'text/event-stream' if body and body['stream'] else b'application/json'
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: '+content+b'\r\nContent-Length: '+str(len(value)).encode()+b'\r\nConnection: close\r\n\r\n'+value)
    with _server(handle) as (url,accepted):
        target=public_target();target['engine']['trusted_url']=url;world=PodWorld(target)
        def runner(argv,**kw):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kw)
            return world.runner(argv,**kw)
        t=http.KubernetesHTTP(target,public_env(),duration=30,runner=runner,profile=http.OBSERVATION_STATE_PROFILE)
        for slot in state.SLOTS:t('kubernetes-probe',[request(t,slot)],10)
        assert len(accepted)==5 and not t.report()['collection_gap']
    assert seen[0]==seen[-1]==b'GET /health HTTP/1.1'
    assert PRIVATE not in canonical_json(t.report()).decode()


@pytest.mark.parametrize('damage',['missing','extra','bool-hash','hash','naive','reverse','window','components-unknown','components-bool','components-empty','components-duplicate'])
def test_invalid_policy_never_calls_runtime(damage):
    now=utcnow();v={'observation_state_environment_sha256':'a'*64,'observation_state_target_sha256':'b'*64,
        'observation_state_validity_start':now.isoformat(),'observation_state_validity_end':(now+timedelta(seconds=60)).isoformat(),
        'observation_state_required_components':['http_listener']}
    if damage=='missing':del v['observation_state_target_sha256']
    elif damage=='extra':v['private']=PRIVATE
    elif damage=='bool-hash':v['observation_state_environment_sha256']=True
    elif damage=='hash':v['observation_state_target_sha256']='not-a-hash'
    elif damage=='naive':v['observation_state_validity_start']=now.replace(tzinfo=None).isoformat()
    elif damage=='reverse':v['observation_state_validity_end']=(now-timedelta(seconds=1)).isoformat()
    elif damage=='window':v['observation_state_validity_end']=(now+timedelta(seconds=601)).isoformat()
    elif damage=='components-unknown':v['observation_state_required_components']=['profiler']
    elif damage=='components-bool':v['observation_state_required_components']=[True]
    elif damage=='components-empty':v['observation_state_required_components']=[]
    elif damage=='components-duplicate':v['observation_state_required_components']=['http_listener']*2
    with pytest.raises(ValueError,match='OBSERVATION_STATE_POLICY_INVALID'):state.normalize_policy(v)


@pytest.mark.parametrize('damage',['duplicate','token-switch','after-first','terminal'])
def test_serial_case_and_uuid_contract_cannot_be_reused(damage):
    target=public_target();world=PodWorld(target);t=http.KubernetesHTTP(target,public_env(),duration=30,runner=world.runner,profile=http.OBSERVATION_STATE_PROFILE)
    t._state_request(request(t,'health-before'),10)
    if damage=='duplicate':r=request(t,'health-before')
    elif damage=='token-switch':r=request(t,'normal-before',token='cd'*16)
    elif damage=='after-first':r=request(t,'health-after')
    else:
        for slot in ('normal-before','normal-after','health-after'):t._state_request(request(t,slot),10)
        r=request(t,'disconnect')
    with pytest.raises(http.KubernetesBindingError):t._state_request(r,10)
    assert not world.calls


@pytest.mark.parametrize('damage',['lab','engine','version'])
def test_actual_or_unreviewed_engine_cannot_open_state_profile(damage):
    target=public_target();env=public_env();world=PodWorld(target)
    if damage=='lab':target['lab']=False
    else:
        env=env.model_copy(deep=True)
        if damage=='engine':env.engine.name='other'
        else:env.engine.version='0.32.0'
        env=env.seal()
    with pytest.raises(http.KubernetesBindingError):http.KubernetesHTTP(target,env,duration=30,runner=world.runner,profile=http.OBSERVATION_STATE_PROFILE)
    assert not world.calls

