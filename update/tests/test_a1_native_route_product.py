"""REQ02 real registry/CLI; existing reviewed native bodies and I/O are EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import ast,asyncio,hashlib,json,shutil,socket,subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.executors.session_seal import SESSION_SEALER
from guard.collectors.lifecycle import load_prepared
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory,CANARY
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_route_auth_sensor import begin,application,scope,invoke,AUTH
from tests.test_registered_route_handler import Router
from tests.test_completion_handler_sensor import application as completion_app,MODULE as COMPLETION
from tests.test_a1_attempt_binding_product import owned_loop
from guard.adapters import vllm_lifecycle_sensor as sensor
from tools.build_kubernetes_native_route_pack import derive

PRIVATE='EXAMPLE_REQ02_NATIVE_PRIVATE'

class World(BaseWorld):
    def setup(self,writer_factory,monkeypatch,loop):
        self.loop=loop
        self.observer,self.writer,self.fake=begin(self.group,writer_factory,monkeypatch)
        self.writer.write('MODULE',{'module':COMPLETION,'verified':True,'source_sha256':sensor.SOURCE_SHA[COMPLETION],'code_sha256':'c'*64})
        self.get_app,_,_=application(self.observer,monkeypatch,router_factory=Router)
        self.post_app,*_=completion_app(self.observer,monkeypatch)
        if self.mode=='rewrite':
            original=self.get_app.app.app
            async def rewrite(value,receive,send):
                value['path']='/EXAMPLE-rewritten/v1/models';value['root_path']='/EXAMPLE-rewritten'
                return await original(value,receive,send)
            self.get_app.app.app=rewrite

    def add_route(self,request):
        rid=request['headers']['X-Request-Id'];completion=request['method']=='POST'
        role='missing' if 'Authorization' not in request['headers'] else 'wrong' if self.mode=='denied' else 'valid'
        value=scope(role,header=rid,path='/v1/completions' if completion else '/v1/models');value['method']=request['method']
        selected=self.post_app if completion else self.get_app
        self.loop.run_until_complete(invoke(selected,value))
        if self.mode=='duplicate' and request['id']=='req02-models-before':self.loop.run_until_complete(invoke(selected,deepcopy(value)))
        self.group.raw_by_pid[42]=bytes(self.fake.data)
        if self.mode=='source-module-missing':
            self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],lambda rows:rows.__setitem__(slice(None),[r for r in rows if not(r['event']=='MODULE' and r['payload']['module']==AUTH)]))

    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request);self.add_route(request)
            if self.mode=='after-timeout' and request['id']=='req02-models-after':raise TimeoutError(PRIVATE)
            completion=request['method']=='POST'
            body=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]} if completion else {'object':'list','data':[{'id':'fixture-model'}]})
            row={'id':request['id'],'method':request['method'],'mode':'json','outcome':401 if self.mode in {'denied','missing-key'} else 200,
                'body':body,'body_bytes':len(body.encode()),'body_sha256':sha256_hex(body.encode()),'capture_complete':True,'eof':True,
                'utf8_valid':True,'body_truncated':False,'terminal_seen':False}
            if self.mode=='wrong-response-method' and request['id']=='req02-models-before':row['method']='POST'
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup' and request['id']=='req02-models-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'exec' in argv:
            command=argv[argv.index('--')+1:]
            call=next(n for n in ast.walk(ast.parse(command[5])) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='compile')
            if ast.literal_eval(call.args[0])==SESSION_SEALER and not self.writer.closed:
                if self.mode!='unclosed':self.writer.close('SEALED')
                self.group.raw_by_pid[42]=bytes(self.fake.data)
                if self.mode=='source-module-missing':
                    self.group.raw_by_pid[42]=revised(self.group.raw_by_pid[42],lambda rows:rows.__setitem__(slice(None),[r for r in rows if not(r['event']=='MODULE' and r['payload']['module']==AUTH)]))
        return super().runner(argv,**kwargs)

def inputs(tmp_path,group,target,monkeypatch,writer_factory,mode,loop):
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision';group.env=env.seal()
    target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode);world.setup(writer_factory,monkeypatch,loop)
    shutil.copytree(group.manifest_path.parent,tmp_path/'prepared')
    derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'route_native_environment_sha256':group.env.ref().sha256,'route_native_target_sha256':sha256_hex(canonical_json(world.target)),
        'route_native_prepared_sha256':sha256_hex(canonical_json(load_prepared(tmp_path/'prepared/manifest.json'))),
        'route_native_validity_start':(now-timedelta(minutes=1)).isoformat(),'route_native_validity_end':(now+timedelta(minutes=2)).isoformat()}
    return world,values,pack

@pytest.mark.parametrize('mode',['normal','denied','missing-key','rewrite','duplicate','source-module-missing','after-timeout','wrong-response-method',
    'native-partial','native-one-missing','engine-replaced','seal-failure','unclosed','attachment','http-cleanup'])
def test_registered_product_keeps_observations_unknown_and_exact_refs(tmp_path,group,native_target,monkeypatch,writer_factory,mode,owned_loop):
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,writer_factory,mode,owned_loop)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected external execution')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*args,**kw):
            if path.name=='probe_log.jsonl' and args and args[0]=='w':raise OSError(PRIVATE)
            return original(path,*args,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    key=CANARY+('-wrong' if mode=='denied' else '-credential');monkeypatch.setenv('BYUL_EXAMPLE_REQ02_KEY',key)
    pol=policy(pack.ref(),scope={'include':['REQ-02']},values={'REQ-02':values})
    for name,data in [('environment',group.env.to_dict()),('target',world.target),('policy',pol.to_dict())]:(tmp_path/(name+'.json')).write_bytes(canonical_json(data))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nnative_route="kubernetes"\n[adapter]\nname="vllm"\n[native_route]\ntarget='+json.dumps(str(tmp_path/'target.json'))
        +'\nprepared='+json.dumps(str(tmp_path/'prepared/manifest.json'))+('\nprobe_key_env="BYUL_EXAMPLE_REQ02_KEY"' if mode!='missing-key' else '')+'\n',encoding='utf-8')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==0,(result.output,result.exception)
        assert PRIVATE not in result.output and key not in result.output
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',pack.path,*common]);cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and e.normal_control.succeeded is None
    if mode in {'normal','denied','missing-key','rewrite','duplicate'}:assert e.execution.status.value=='COMPLETED'
    else:assert e.execution.status.value=='ABORTED'
    assert all(getattr(res.verdicts,a).value=='UNKNOWN' for a in ('functional','security','performance'))
    assert e.observations['native_route_authorization_verified'].value is e.observations['native_route_scope_complete'].value is False
    associations=e.observations['native_route_associations'].value
    if mode in {'normal','rewrite','duplicate','source-module-missing','after-timeout','seal-failure','unclosed','attachment','http-cleanup','native-one-missing'}:assert associations
    if mode=='normal':assert len(associations)==3 and all(r['same_recording_association_observed'] and r['registered_handler_observed'] for r in associations)
    if mode=='rewrite':assert any(r['path_change_observed'] is True for r in associations)
    if mode in {'denied','missing-key'}:assert associations and not any(r['registered_handler_observed'] for r in associations)
    if mode=='duplicate':assert any(r['request_hash_ambiguous'] and not r['same_recording_association_observed'] for r in associations)
    if mode in {'source-module-missing','unclosed'}:assert not any(r['same_recording_association_observed'] for r in associations)
    text=canonical_json(e.to_dict()).decode();assert PRIVATE not in text and key not in text and hashlib.sha256(key.encode()).hexdigest() not in text
    for a in e.artifacts:
        raw=(tmp_path/'store'/a.path).read_bytes();assert PRIVATE.encode() not in raw and key.encode() not in raw and hashlib.sha256(key.encode()).hexdigest().encode() not in raw
    objects=[r for k in ('ENV','POL','PLAN','EVD','RES','RPT') for r in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(r.ref()).to_dict()==r.to_dict() for r in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])

def test_pack_retains_question_criteria_and_seventy_six_exact_definitions(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');assert receipt['unchanged_definition_bytes']==76
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(pack.checks['REQ-02'],name)==getattr(seed.checks['REQ-02'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/REQ-02.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()

@pytest.mark.parametrize('bad',['foreign-id','other-token','wrong-order','header','raw-key','get-body','method','path','foreign-host','query','mode','read-profile','batch','old-profile'])
def test_closed_own_profile_rejects_bad_request_before_io(bad):
    from tests.test_model_preparation_integration import full_inputs
    env,target=full_inputs();calls=[]
    def forbidden(*a,**kw):calls.append(True);raise AssertionError('no external I/O')
    profile=http.REQUEST_BINDING_PROFILE if bad=='old-profile' else http.ROUTE_AUTHENTICATION_PROFILE
    transport=http.KubernetesHTTP(target,env,duration=30,runner=forbidden,profile=profile)
    request={'id':'req02-models-before','method':'GET','mode':'json','url':target['engine']['trusted_url']+'/v1/models',
        'headers':{'X-Request-Id':'byul-req02-'+'ab'*16+'-models-before'},'body':None}
    if bad=='foreign-id':request['id']='obs02-health-before'
    if bad=='other-token':request['headers']['X-Request-Id']='byul-obs02-'+'ab'*16+'-models-before'
    if bad=='wrong-order':request['id']='req02-models-after';request['headers']['X-Request-Id']='byul-req02-'+'ab'*16+'-models-after'
    if bad=='header':request['headers']['X-External-Caller']=PRIVATE
    if bad=='raw-key':request['headers']['Authorization']='Bearer '+PRIVATE+'\r\nX-Other: 1'
    if bad=='get-body':request['body']={'model':'fixture-model'}
    if bad=='method':request['method']='DELETE'
    if bad=='path':request['url']+='/other'
    if bad=='foreign-host':request['url']='http://EXAMPLE-foreign/v1/models'
    if bad=='query':request['url']+='?EXAMPLE=1'
    if bad=='mode':request['mode']='sse'
    if bad=='read-profile':request['read_profile']={'profile':'slow-reader-v1'}
    with pytest.raises(http.KubernetesBindingError):transport('kubernetes-probe',[request]*2 if bad=='batch' else [request],10)
    assert calls==[]

@pytest.mark.parametrize('bad',['prepared-pin','lab-false','native032','key-env','key-shape','unknown-config'])
def test_preflight_keeps_source_lab_profile_and_explicit_key_boundaries(tmp_path,group,native_target,monkeypatch,writer_factory,bad,owned_loop):
    from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor
    world,values,pack=inputs(tmp_path,group,native_target,monkeypatch,writer_factory,'normal',owned_loop)
    cfg={'target':str(tmp_path/'target.json'),'prepared':str(tmp_path/'prepared/manifest.json')};environ={}
    if bad=='prepared-pin':values['route_native_prepared_sha256']='0'*64
    if bad=='lab-false':world.target['lab']=False;values['route_native_target_sha256']=sha256_hex(canonical_json(world.target))
    if bad=='native032':env=group.env.model_copy(deep=True);env.engine.version='0.32.0';group.env=env.seal()
    if bad in {'key-env','key-shape'}:cfg['probe_key_env']='BYUL_EXAMPLE_REQ02_KEY'
    if bad=='key-shape':environ['BYUL_EXAMPLE_REQ02_KEY']=PRIVATE+'\r\nother'
    if bad=='unknown-config':cfg['implicit_key']=PRIVATE
    (tmp_path/'target.json').write_bytes(canonical_json(world.target));calls=[]
    def forbidden(*a,**kw):calls.append(True);raise AssertionError('no external I/O')
    executor=KubernetesNativeRouteExecutor(cfg,runner=forbidden,environ=environ)
    with pytest.raises((ValueError,http.KubernetesBindingError)):executor.run(pack.checks['REQ-02'],group.env,values,object(),tmp_path/'work')
    assert calls==[]
