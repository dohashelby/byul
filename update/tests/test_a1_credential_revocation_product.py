"""PLT10 canonical product with exact auth/models bodies; transport is EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import ast,asyncio,hashlib,json,socket,subprocess,sys
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.cli import app
from guard.executors.credential_revocation_contract import normalize_policy,SLOTS
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_credential_revocation import KubernetesCredentialRevocationHTTP
from guard.runtime.bounded import capture_pipes
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_kubernetes_model_authorization_product import World as BaseWorld
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tests import test_route_auth_sensor as auth
from tests.test_registered_route_handler import Router
from tests.test_stream_probe import _server
from tools.build_kubernetes_credential_revocation_pack import derive

RETIRED='EXAMPLE_PLT10_RETIRED_CREDENTIAL'
CONTROL='EXAMPLE_PLT10_CONTROL_CREDENTIAL'
PRIVATE='EXAMPLE_PLT10_PRIVATE_RESPONSE'
REPO=Path(__file__).resolve().parents[2]


class ModelList:
    def __init__(self):self.data=[]
    def model_dump(self):return {'object':'list','data':[{'id':'fixture-model'}]}


class OwnedResponse:
    def __init__(self,*,content,status_code=200):self.content=content;self.status=status_code
    async def __call__(self,scope,receive,send):
        await send({'type':'http.response.start','status':self.status})
        await send({'type':'http.response.body','body':canonical_json(self.content)})


class World(BaseWorld):
    def __init__(self,group,target,mode,factory,patch):
        super().__init__(group,target,mode);self.observer,self.writer,self.fake=auth.begin(group,factory,patch);self.sealed=False
        patch.setattr(auth,'Response',OwnedResponse)
        async def registry():return ModelList()
        self.app,_,_=auth.application(self.observer,patch,registry=registry,router_factory=Router)
    def native_response(self,rid,role,slot):
        accepted=role=='control' or self.mode in {'accepted','conditional','before-deadline','attachment','seal-failure','native-one-missing',
            'native-partial','after-timeout','http-cleanup','late-protocol','native-unbound','native-duplicate'}
        tokens=[CONTROL,RETIRED] if accepted else [CONTROL]
        self.app.app=type(self.app.app)(self.app.app.app,tokens)
        value={'type':'http','method':'GET','path':'/v1/models','root_path':'',
            'headers':[(b'x-request-id',(rid+'-other' if self.mode=='native-unbound' else rid).encode()),
                (b'authorization',('Bearer '+(RETIRED if role=='retired' else CONTROL)).encode())]}
        response=asyncio.run(auth.invoke(self.app,value))
        if self.mode=='native-duplicate':asyncio.run(auth.invoke(self.app,value))
        self.group.raw_by_pid[42]=bytes(self.fake.data)
        return response[0]['status'],b''.join(m.get('body',b'') for m in response).decode()
    def close_native(self):
        if not self.sealed:
            self.writer.close('SEALED');self.sealed=True;self.group.raw_by_pid[42]=bytes(self.fake.data)
    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            request,=json.loads(kwargs['input_bytes'])['requests'];self.sent.append(request)
            slot=request['id'].removeprefix('plt10-');role='retired' if slot.startswith('retired-') else 'control'
            assert slot in SLOTS and request['method']=='GET' and request['url'].endswith('/v1/models') and 'body' not in request
            assert request['headers']['Authorization']=='Bearer '+(RETIRED if role=='retired' else CONTROL)
            if self.mode=='after-timeout' and slot=='control-after':raise TimeoutError(PRIVATE)
            status,body=self.native_response(request['headers']['X-Request-Id'],role,slot)
            if self.mode=='before-shape' and slot=='control-before':body='{}'
            if status in {401,403}:body=json.dumps({'error':PRIVATE,'credential':RETIRED})
            row={'id':request['id'],'method':'GET','mode':'json','outcome':status,'body':body,
                'body_sha256':sha256_hex(body.encode()),'body_bytes':len(body.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False}
            if self.mode=='late-protocol' and slot=='retired-second':row['id']='other'
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup' and slot=='control-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'exec' in argv and any('guard-session-seal/1' in value for value in argv):self.close_native()
        result=super().runner(argv,**kwargs)
        if 'namespaces' in argv:
            value=json.loads(result['stdout_bytes'])
            for item in value['items']:item['kind']='Namespace'
            result['stdout_bytes']=canonical_json(value)
        return result


def inputs(tmp_path,group,target,mode,factory,patch):
    environment=group.env.model_copy(deep=True);environment.model.id='EXAMPLE-model';environment.model.revision='EXAMPLE-revision';environment.model.inventory_sha256='b'*64
    if mode=='version032':environment.engine.version='0.32.0'
    group.env=environment.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode,factory,patch);derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    deadline=now+timedelta(seconds=30) if mode=='before-deadline' else now-timedelta(seconds=30)
    values={'revocation_environment_sha256':group.env.ref().sha256,'revocation_target_sha256':sha256_hex(canonical_json(world.target)),
        'revocation_prepared_sha256':sha256_hex(canonical_json(group.prepared)),'revocation_validity_start':(now-timedelta(seconds=120)).isoformat(),
        'revocation_validity_end':(now+timedelta(seconds=120)).isoformat(),'revocation_issuer_id':'issuer-example','revocation_event_id':'event-example',
        'revocation_event_at':(deadline-timedelta(seconds=1)).isoformat(),'revocation_deny_after':deadline.isoformat(),
        'revocation_deadline_basis':'after-verified-issuer-event' if mode=='conditional' else 'absolute-unconditional',
        'revocation_propagation_limit_ms':1000,'revocation_max_clock_skew_ms':100,'revocation_subject_label':'retired','revocation_max_new_access_samples':3}
    return world,values,pack


@pytest.mark.parametrize('mode',['denied','accepted','conditional','before-deadline','before-shape','native-unbound','native-duplicate',
    'engine-replaced','seal-failure','native-one-missing','native-partial','attachment','after-timeout','http-cleanup','late-protocol',
    'missing-credential','same-credential','actual-lab-false','version032'])
def test_registered_cli_auth_samples_absolute_constraint_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,mode):
    world,values,pack=inputs(tmp_path,group,native_target,mode,writer_factory,monkeypatch)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-10']},values={'PLT-10':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\ncredential_revocation="kubernetes"\n[adapter]\nname="vllm"\n[credential_revocation]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\nretired_key_env="BYUL_PLT10_RETIRED"\ncontrol_key_env="BYUL_PLT10_CONTROL"\n',encoding='utf-8')
    monkeypatch.setenv('BYUL_PLT10_RETIRED',RETIRED);monkeypatch.setenv('BYUL_PLT10_CONTROL',RETIRED if mode=='same-credential' else CONTROL)
    if mode=='missing-credential':monkeypatch.delenv('BYUL_PLT10_RETIRED')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert all(secret not in result.output for secret in (RETIRED,CONTROL,PRIVATE))
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='PLT-10').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    fail=mode in {'accepted','seal-failure','native-one-missing','attachment','after-timeout','http-cleanup','late-protocol'}
    assert e.data_origin.value=='EXAMPLE' and res.verdicts.functional.value==('FAIL' if fail else 'UNKNOWN')
    assert res.verdicts.security.value==res.verdicts.performance.value=='UNKNOWN' and e.normal_control.succeeded is None
    if mode in {'actual-lab-false','version032','missing-credential','same-credential'}:
        assert not world.sent and e.observations['revocation_absolute_denial_constraint_violated'].value is None
    else:
        assert e.observations['revocation_absolute_denial_constraint_violated'].value==fail
        assert all(e.observations[k].value is False for k in ('revocation_issuer_event_verified','revocation_provider_propagation_verified',
            'revocation_normal_before_issuer_verified','revocation_new_connection_receipt_verified','revocation_scope_complete'))
        assert len(world.sent)=={'before-shape':2,'engine-replaced':1,'late-protocol':3}.get(mode,5)
    if mode in {'native-unbound','native-duplicate','engine-replaced','native-partial','attachment','after-timeout','http-cleanup','late-protocol',
        'actual-lab-false','version032','missing-credential','same-credential'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    private=(RETIRED,CONTROL,PRIVATE,hashlib.sha256(RETIRED.encode()).hexdigest(),hashlib.sha256(CONTROL.encode()).hexdigest())
    assert all(secret not in canonical_json(e.to_dict()).decode() for secret in private)
    for artifact in e.artifacts:assert all(secret.encode() not in (tmp_path/'store'/artifact.path).read_bytes() for secret in private)
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_keeps_original_issuer_new_access_criteria_and_other76(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_PLT10_complete']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['PLT-10'],name)==getattr(pack.checks['PLT-10'],name)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/PLT-10.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('key,value',[('revocation_deadline_basis','auto'),('revocation_max_new_access_samples',4),('revocation_max_clock_skew_ms',True),
    ('revocation_subject_label','unknown-key'),('revocation_issuer_id',''),('revocation_event_at','2026-10-30T00:00:00')])
def test_policy_rejects_inferred_subject_deadline_or_extra_calls(tmp_path,group,native_target,writer_factory,monkeypatch,key,value):
    _,values,_=inputs(tmp_path,group,native_target,'denied',writer_factory,monkeypatch);values[key]=value
    with pytest.raises(ValueError,match='CREDENTIAL_REVOCATION_POLICY_INVALID'):normalize_policy(values)


def test_public_five_fixed_gets_use_owned_fresh_connections_and_do_not_persist_credentials():
    from tests.test_kubernetes_request_binding import env,target
    from tests.test_kubernetes_sampling import World as PodWorld
    def handle(conn,raw,index,stop):
        head,_,body=raw.partition(b'\r\n\r\n');assert head.startswith(b'GET /v1/models HTTP/1.1') and not body
        role='retired' if b'Authorization: Bearer '+RETIRED.encode() in head else 'control'
        assert b'Authorization: Bearer '+(RETIRED if role=='retired' else CONTROL).encode() in head
        data=canonical_json({'object':'list','data':[{'id':'fixture-model'}]}) if role=='control' else canonical_json({'error':PRIVATE,'credential':RETIRED})
        status=b'200 OK' if role=='control' else b'401 Unauthorized'
        conn.sendall(b'HTTP/1.1 '+status+b'\r\nContent-Type: application/json\r\nContent-Length: '+str(len(data)).encode()+b'\r\nConnection: close\r\n\r\n'+data)
    with _server(handle) as (url,accepted):
        selected=target();selected['engine']['trusted_url']=url;pods=PodWorld(selected)
        def runner(argv,**kwargs):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kwargs)
            result=pods.runner(argv,**kwargs);value=json.loads(result['stdout_bytes'])
            if 'namespaces' in argv:
                for item in value['items']:item['kind']='Namespace'
            else:value['kind']='Pod'
            result['stdout_bytes']=canonical_json(value);return result
        transport=KubernetesCredentialRevocationHTTP(selected,env(),{'retired':RETIRED,'control':CONTROL},runner=runner,duration=60)
        replies=[transport.perform(slot,'byul-plt10-'+'ab'*16+'-'+slot) for slot in SLOTS]
        assert len(accepted)==5
        assert [r['outcome'] for r in replies]==[200,401,401,401,200]
        assert all(not r['new_connection_receipt_verified'] and not r['issuer_event_verified'] for r in replies)
    report=transport.report();assert not report['collection_gap'] and not report['issuer_mutated'] and not report['credentials_written']
    assert all(value not in json.dumps(report) for value in (RETIRED,CONTROL,PRIVATE,sha256_hex(RETIRED.encode()),sha256_hex(CONTROL.encode())))
