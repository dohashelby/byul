"""PLT11 canonical CLI; probe/native boundary is explicitly EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
import hashlib,json,socket,subprocess,sys
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.executors.persistent_session_probe import SCHEMA,SLOTS
from guard.executors.session_revocation_contract import normalize_policy,epoch_ns
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_a1_credential_revocation_product import World as AuthWorld,RETIRED,CONTROL,PRIVATE,serving_loop
from tests.test_kubernetes_lifecycle import group,target as native_target
from tests.test_lifecycle_capture import case,external,revised
from tests.test_lifecycle_sensor import writer_factory
from tools.build_kubernetes_session_revocation_pack import derive


class World(AuthWorld):
    def native_response(self,rid,role,slot):
        old=self.mode
        self.mode=old if old in {'native-unbound','native-duplicate'} else ('denied' if old=='denied' and slot in {'retired-session-reuse','retired-new'} else 'accepted')
        try:return super().native_response(rid,role,slot)
        finally:self.mode=old
    def runner(self,argv,**kwargs):
        if 'exec' in argv and 'input_bytes' in kwargs:
            spec=json.loads(kwargs['input_bytes']);assert spec['schema']==SCHEMA and spec['keys']=={'retired':RETIRED,'control':CONTROL}
            assert spec['url']==self.target['engine']['trusted_url'] and spec['api_model']=='fixture-model'
            rows=[];identity={'probe_pid':90,'descriptor':5,'device':1,'inode':91}
            for number,slot in enumerate(SLOTS):
                role='retired' if slot.startswith('retired-') else 'control';rid='byul-plt11-'+spec['request_token']+'-'+slot
                self.sent.append({'id':'plt11-'+slot})
                status,body=self.native_response(rid,role,slot)
                when=spec['open_before_ns']-10**7+number*10**6 if number<2 else spec['reuse_after_ns']+spec['max_clock_skew_ms']*10**6+number*10**6
                connection=identity if slot in {'retired-session-open','retired-session-reuse'} else {**identity,'inode':100+number}
                same=slot in {'retired-session-open','retired-session-reuse'}
                if self.mode=='reconnected' and slot=='retired-session-reuse':connection={**connection,'inode':92};same=False
                row={'case_id':'plt11-'+slot,'request_id_sha256':sha256_hex(rid.encode()),'credential_label':role,'attempted':True,
                    'outcome':status,'normal_shape_verified':status==200,'denial_response_observed':status in {401,403},'capture_complete':True,
                    'started_wall_ns':when,'finished_wall_ns':when+1000,'started_monotonic_ns':number*10**7+100,'finished_monotonic_ns':number*10**7+1100,
                    'clock_consistent':True,'probe_socket_before':connection,'probe_socket_after':connection,
                    'selected_existing_socket_verified':same,'new_distinct_probe_connection_verified':slot=='retired-new',
                    'response_allows_keepalive':True,'diagnostic':None,'issuer_event_verified':False,'serving_session_receipt_verified':False}
                if self.mode=='before-shape' and slot=='control-before':row['normal_shape_verified']=False
                if self.mode=='clock-gap' and slot=='retired-session-reuse':row['clock_consistent']=False
                if self.mode=='after-timeout' and slot=='control-after':row.update(outcome=None,capture_complete=False,normal_shape_verified=False,diagnostic='SESSION_PROBE_SELECTED_ACCESS_UNAVAILABLE')
                if self.mode=='wrong-response-id' and slot=='retired-session-reuse':row['request_id_sha256']='0'*64
                rows.append(row)
            packet={'schema':SCHEMA,'rows':rows,'cleanup_errors':[],'issuer_mutated':False,'credential_bytes_or_hashes_recorded':False,'probe_process_only':True,
                'producer_authenticated':False,'serving_session_receipt_verified':False,'application_session_or_handle_verified':False}
            pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
            if self.mode=='http-cleanup':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='late-capture-gap':pipe['complete']=False
            return {'returncode':0,'stdout_bytes':canonical_json(packet),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        return super().runner(argv,**kwargs)


def inputs(tmp_path,group,target,mode,factory,patch,loop):
    env=group.env.model_copy(deep=True);env.model.id='EXAMPLE-model';env.model.revision='EXAMPLE-revision';env.model.inventory_sha256='b'*64
    if mode=='version032':env.engine.version='0.32.0'
    group.env=env.seal();target=deepcopy(target);target['environment_ref']=group.env.ref().model_dump(mode='json')
    world=World(group,target,mode,factory,patch,loop);derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack');now=utcnow()
    values={'session_environment_sha256':group.env.ref().sha256,'session_target_sha256':sha256_hex(canonical_json(world.target)),
        'session_prepared_sha256':sha256_hex(canonical_json(group.prepared)),'session_validity_start':(now-timedelta(seconds=60)).isoformat(),
        'session_validity_end':(now+timedelta(seconds=60)).isoformat(),'session_issuer_id':'issuer-example','session_event_id':'event-example',
        'session_event_at':(now+timedelta(seconds=2)).isoformat(),'session_open_before':(now+timedelta(seconds=1)).isoformat(),
        'session_deny_after':(now+timedelta(seconds=3)).isoformat(),'session_deadline_basis':'after-verified-issuer-event' if mode=='conditional' else 'absolute-unconditional',
        'session_propagation_limit_ms':1000,'session_max_clock_skew_ms':100,'session_max_wait_seconds':5,'session_residual_policy':'deny-existing-after-deadline'}
    return world,values,pack


@pytest.mark.parametrize('mode',['denied','accepted','conditional','reconnected','before-shape','clock-gap','native-unbound','native-duplicate',
    'engine-replaced','seal-failure','native-one-missing','native-partial','attachment','after-timeout','http-cleanup','late-capture-gap',
    'wrong-response-id','missing-credential','same-credential','actual-lab-false','version032'])
def test_registered_cli_existing_connection_constraints_and_exact_refs(tmp_path,group,native_target,writer_factory,monkeypatch,serving_loop,mode):
    world,values,pack=inputs(tmp_path,group,native_target,mode,writer_factory,monkeypatch,serving_loop)
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr('guard.executors.kubernetes_lifecycle_capture.capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def open_file(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',open_file)
    pol=policy(pack.ref(),scope={'include':['PLT-11']},values={'PLT-11':values})
    for name,value in [('environment',group.env.to_dict()),('policy',pol.to_dict()),('target',world.target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nsession_revocation="kubernetes"\n[adapter]\nname="vllm"\n[session_revocation]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nprepared='+json.dumps(str(group.manifest_path))+'\nretired_key_env="BYUL_PLT11_RETIRED"\ncontrol_key_env="BYUL_PLT11_CONTROL"\n',encoding='utf-8')
    monkeypatch.setenv('BYUL_PLT11_RETIRED',RETIRED);monkeypatch.setenv('BYUL_PLT11_CONTROL',RETIRED if mode=='same-credential' else CONTROL)
    if mode=='missing-credential':monkeypatch.delenv('BYUL_PLT11_RETIRED')
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,code=0):
        result=runner.invoke(app,list(map(str,args)));assert result.exit_code==code,(args,result.output,result.exception)
        assert all(secret not in result.output for secret in (RETIRED,CONTROL,PRIVATE))
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(item for item in plan.items if item.check_id=='PLT-11').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='http-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    fail=mode in {'accepted','seal-failure','native-one-missing','attachment','after-timeout','http-cleanup','late-capture-gap'}
    assert e.data_origin.value=='EXAMPLE' and res.verdicts.functional.value==('FAIL' if fail else 'UNKNOWN')
    assert res.verdicts.security.value==res.verdicts.performance.value=='UNKNOWN' and e.normal_control.succeeded is None
    if mode in {'actual-lab-false','version032','missing-credential','same-credential'}:
        assert not world.sent and e.observations['session_existing_connection_constraint_violated'].value is None
    else:
        assert e.observations['session_existing_connection_constraint_violated'].value==fail
        assert len(world.sent)==5
        assert all(e.observations[key].value is False for key in ('session_issuer_event_verified','session_provider_propagation_verified',
            'session_application_session_or_handle_verified','session_serving_session_receipt_verified','session_scope_complete'))
    if mode in {'native-unbound','native-duplicate','engine-replaced','native-partial','native-one-missing','attachment','after-timeout',
        'http-cleanup','late-capture-gap','wrong-response-id','actual-lab-false','version032','missing-credential','same-credential'}:assert e.execution.status.value=='ABORTED'
    assert bool(e.execution.cleanup_errors)==(mode=='http-cleanup')
    secrets=(RETIRED,CONTROL,PRIVATE,hashlib.sha256(RETIRED.encode()).hexdigest(),hashlib.sha256(CONTROL.encode()).hexdigest())
    assert all(secret not in canonical_json(e.to_dict()).decode() for secret in secrets)
    for artifact in e.artifacts:assert all(secret.encode() not in (tmp_path/'store'/artifact.path).read_bytes() for secret in secrets)
    objects=[o for kind in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(kind)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_pack_keeps_original_existing_session_criteria_and_other76(tmp_path):
    seed=baseline_pack();receipt=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and receipt['unchanged_definition_bytes']==76 and not receipt['full_PLT11_complete']
    for name in ('question','normal_control','challenge_steps','satisfied_if','fail_criteria','unknown_criteria','legacy','method_and_evidence'):
        assert getattr(seed.checks['PLT-11'],name)==getattr(pack.checks['PLT-11'],name)
    for path in seed.path.rglob('*'):
        if path.is_file() and path.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/PLT-11.yaml'}:assert path.read_bytes()==(pack.path/path.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('key,value',[('session_residual_policy','infer-from-http'),('session_max_wait_seconds',11),('session_max_clock_skew_ms',True),
    ('session_deadline_basis','auto'),('session_issuer_id',''),('session_event_at','2026-10-30T00:00:00')])
def test_policy_requires_explicit_existing_connection_scope(tmp_path,group,native_target,writer_factory,monkeypatch,serving_loop,key,value):
    _,values,_=inputs(tmp_path,group,native_target,'denied',writer_factory,monkeypatch,serving_loop);values[key]=value
    with pytest.raises(ValueError,match='SESSION_REVOCATION_POLICY_INVALID'):normalize_policy(values)


@pytest.mark.parametrize('mode',['accepted','closed'])
def test_actual_owned_linux_keepalive_is_distinct_from_reconnection(mode):
    from guard.runtime.bounded import capture_pipes
    root=Path(__file__).resolve().parents[1]/'src/guard/executors'
    def native_path(path):
        value=str(path).replace('\\','/')
        return '/mnt/'+value[0].lower()+value[2:] if sys.platform=='win32' else value
    program=r'''
import hashlib,json,sys,threading,time,types
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
support={'__name__':'owned_stream_support'};exec(compile(Path(sys.argv[1]).read_bytes(),'owned-stream-support','exec'),support)
probe={'__name__':'owned_session_probe'};exec(compile(Path(sys.argv[2]).read_bytes(),'owned-session-probe','exec'),probe)
mode=sys.argv[3];seen=[];held=[]
class Handler(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_GET(self):
  assert self.path=='/v1/models' and self.headers.get('Content-Length') is None
  slot=self.headers['X-Request-Id'].split('ab'*16+'-',1)[1]
  held.append(self.connection);seen.append((slot,self.connection))
  key='EXAMPLE_PLT11_RETIRED' if slot.startswith('retired-') else 'EXAMPLE_PLT11_CONTROL'
  assert self.headers['Authorization']=='Bearer '+key
  raw=json.dumps({'object':'list','data':[{'id':'fixture-model'}]}).encode()
  self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.send_header('Content-Type','application/json')
  self.send_header('Connection','close' if mode=='closed' and slot=='retired-session-open' else 'keep-alive');self.end_headers();self.wfile.write(raw);self.wfile.flush()
  if mode=='closed' and slot=='retired-session-open':self.close_connection=True
server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
try:
 now=time.time_ns();spec={'schema':probe['SCHEMA'],'url':'http://127.0.0.1:'+str(server.server_port),'api_model':'fixture-model','request_token':'ab'*16,
  'keys':{'retired':'EXAMPLE_PLT11_RETIRED','control':'EXAMPLE_PLT11_CONTROL'},'open_before_ns':now+2*10**9,'reuse_after_ns':now+2400*10**6,
  'max_clock_skew_ms':100,'max_wait_seconds':5,'timeout_seconds':12}
 packet=probe['collect'](spec,support['_Response']);rows=packet['rows'];opened,reused,new=rows[1:4]
 assert not packet['cleanup_errors'] and not packet['issuer_mutated'] and packet['probe_process_only']
 assert all(row['clock_consistent'] for row in rows if row['attempted'])
 assert opened['selected_existing_socket_verified']==(mode=='accepted')
 assert reused['selected_existing_socket_verified']==(mode=='accepted')
 assert new['new_distinct_probe_connection_verified']==(mode=='accepted')
 if mode=='accepted':
  assert len(seen)==5 and seen[1][1] is seen[2][1] and seen[3][1] is not seen[1][1]
  assert opened['probe_socket_before']==opened['probe_socket_after']==reused['probe_socket_before']==reused['probe_socket_after']
  assert reused['started_wall_ns']>spec['reuse_after_ns']+spec['max_clock_skew_ms']*10**6
 else:
  assert len(seen)==4 and reused['diagnostic']=='SESSION_PROBE_SELECTED_ACCESS_UNAVAILABLE' and not reused['attempted']
 raw=json.dumps(packet)
 assert all(value not in raw for key in spec['keys'].values() for value in (key,hashlib.sha256(key.encode()).hexdigest()))
 assert not packet['application_session_or_handle_verified'] and not packet['serving_session_receipt_verified']
 print(json.dumps({'actual_owned_CPU_keepalive_test':True,'mode':mode,'wire_requests':len(seen),'actual_serving_issuer_GPU':False}))
finally:
 server.shutdown();server.server_close();thread.join(2);assert not thread.is_alive()
'''
    argv=([sys.executable] if sys.platform!='win32' else ['wsl','-d','Ubuntu','--','python3'])+['-c',program,native_path(root/'stream_probe.py'),native_path(root/'persistent_session_probe.py'),mode]
    result=capture_pipes(argv,timeout=25,env_extra={},max_stdout_bytes=65536,max_stderr_bytes=65536)
    assert result['returncode']==0 and result['capture']['complete'],result
    value=json.loads(result['stdout_bytes']);assert value['actual_owned_CPU_keepalive_test'] and not value['actual_serving_issuer_GPU']
