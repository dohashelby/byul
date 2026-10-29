"""REQ06 real public registry/CLI; exact owned four-Pod HTTP I/O is EXAMPLE."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
import json,socket,subprocess,sys
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.store import Store
from guard.runtime import kubernetes_http as http
from guard.runtime.bounded import capture_pipes
from guard.runtime.kubernetes_stored_response_scope import OwnedResponseScope,normalize_scope
from guard.runtime.kubernetes_stored_responses import KubernetesStoredResponsesHTTP
from guard.adapters.vllm_stored_responses import REVIEWED_SOURCES
from guard.executors import kubernetes_stored_responses as product
from tests.builders import baseline_pack,policy,T0
from tests.test_kubernetes_routes import inputs as route_inputs,World as RouteWorld
from tests.test_stream_probe import _server
from tools.build_kubernetes_stored_responses_pack import derive

PRIVATE='EXAMPLE_REQ06_PRIVATE'
KEY_A='EXAMPLE_REQ06_CREDENTIAL_A'
KEY_B='EXAMPLE_REQ06_CREDENTIAL_B'
ROLES={'A':'REQ06_A','B':'REQ06_B'}
SESSION='ab'*16


class World(RouteWorld):
    def __init__(self,target,mode='enforced'):
        super().__init__(target);self.mode=mode;self.sent=[];self.database={};self.raw_ids=set();self.cleanup_attempts=0

    def handle(self,request):
        actor={KEY_A:'A',KEY_B:'B'}[request['headers']['Authorization'].removeprefix('Bearer ')]
        url=urlsplit(request['url']);origin='gateway' if url.netloc==urlsplit(self.t['gateway']['url']).netloc else 'direct'
        path=url.path;body=request.get('body');self.sent.append({'origin':origin,'actor':actor,'method':request['method'],'path':path})
        if path=='/v1/responses':
            assert request['method']=='POST' and body['model']=='fixture-model' and body['max_output_tokens']==1
            assert body['temperature']==0 and body['stream'] is False and body['input'] in {'hello','goodbye'}
            oid=body['request_id'];previous=body.get('previous_response_id');key=(origin,oid)
            assert oid.startswith('resp_byul_req06_');self.raw_ids.add(oid)
            previous_row=self.database.get((origin,previous)) if previous else None
            if previous and (previous_row is None or previous_row['owner']!=actor and self.mode!='vulnerable'):return 403,{'private':PRIVATE}
            if key in self.database and self.database[key]['owner']!=actor and self.mode!='vulnerable':return 403,{'private':PRIVATE}
            value={'id':oid,'object':'response','model':'fixture-model','status':'queued' if body['background'] and self.mode!='synchronous' else 'completed',
                'output':[{'type':'message','content':[{'type':'output_text','text':PRIVATE}]}],
                'metadata':deepcopy(body['metadata']),'previous_response_id':previous}
            if body['store'] and self.mode!='store-disabled':self.database[key]={'owner':actor,'value':deepcopy(value)}
            if self.mode=='missing-id' and oid.endswith('_A_read'):value['id']='foreign-response'
            return 200,value
        parts=path.split('/');assert parts[:3]==['','v1','responses'] and len(parts) in {4,5}
        oid=parts[3];assert oid in self.raw_ids,'foreign/previous-run object was requested'
        record=self.database.get((origin,oid))
        if record is None:return 404,{'private':PRIVATE}
        if actor!=record['owner'] and self.mode!='vulnerable':return 403,{'private':PRIVATE}
        if len(parts)==5:
            assert parts[-1]=='cancel' and request['method']=='POST' and oid.endswith(('cancel-normal','cancel-target'))
            if record['value']['status'] not in {'queued','in_progress'}:return 400,{'private':PRIVATE}
            if oid.endswith('cancel-target') and actor==record['owner']:
                self.cleanup_attempts+=1
                if self.mode=='cleanup-error':raise OSError(PRIVATE)
            record['value']['status']='cancelled'
        else:assert request['method']=='GET'
        return 200,deepcopy(record['value'])

    def runner(self,argv,**kw):
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if 'exec' in argv:
            request,=json.loads(kw['input_bytes'])['requests']
            assert self.t['kubernetes_routes']['external_probe']['pod'] in argv and self.t['kubernetes']['probe']['pod'] not in argv
            self.packets.append([deepcopy(request)])
            status,value=self.handle(request);text=json.dumps(value)
            if self.mode=='after-error' and len(self.sent)==25:raise OSError(PRIVATE)
            row={'id':request['id'],'mode':'json','method':request['method'],'outcome':status,'body':text,
                'body_bytes':len(text.encode()),'body_sha256':sha256_hex(text.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False}
            if self.mode=='partial' and len(self.sent)==25:row['capture_complete']=False
            if self.mode=='timeout' and len(self.sent)==25:row.update(outcome='timeout',capture_complete=False)
            if self.mode=='before-error' and len(self.sent)==1:row['outcome']=500
            if self.mode=='cross-error' and request['method']=='GET' and request['url'].endswith('_A_read') and request['headers']['Authorization']=='Bearer '+KEY_B:row['outcome']=503
            if self.mode=='wrong-id' and len(self.sent)==25:row['id']='foreign'
            if self.mode=='late-restart' and len(self.sent)==25:self.changed=True
            if self.mode=='parent-cleanup' and len(self.sent)==25:pipe.update(pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='engine-replaced' and len(self.sent)==25:
                def replace(role,pod):
                    if role=='engine':pod['metadata']['uid']='foreign'
                self.tamper=replace
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        value=super().runner(argv,**kw);value['capture'].update(pipe);return value


def inputs(tmp_path,mode='enforced',*,origins=None,operations=None):
    env,target=route_inputs();world=World(target,mode);now=utcnow()
    values={'stored_environment_sha256':env.ref().sha256,'stored_target_sha256':sha256_hex(canonical_json(target)),
        'stored_session':SESSION,'stored_identity_policy_sha256':'9'*64,
        **{'stored_'+key+'_source_sha256':sha for key,sha in REVIEWED_SOURCES.items()},
        'stored_validity_start':(now-timedelta(minutes=1)).isoformat(),'stored_validity_end':(now+timedelta(minutes=1)).isoformat(),
        'stored_origins':['gateway','direct'] if origins is None else origins,'stored_operations':['read','cancel','previous','overwrite'] if operations is None else operations,
        'stored_retention':'retain-owned-lab-objects'}
    receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return env,world,values,pack,receipt


@pytest.mark.parametrize('mode',['enforced','vulnerable','store-disabled','synchronous','missing-id','wrong-id','partial','late-restart',
    'engine-replaced','after-error','parent-cleanup','cleanup-error','attachment','timeout','before-error','cross-error'])
def test_existing_registry_cli_all_operations_exact_refs_and_no_raw_objects(tmp_path,monkeypatch,mode):
    env,world,values,pack,_=inputs(tmp_path,mode)
    for key,value in {'REQ06_A':KEY_A,'REQ06_B':KEY_B}.items():monkeypatch.setenv(key,value)
    pol=policy(pack.ref(),scope={'include':['REQ-06']},values={'REQ-06':values})
    for name,value in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',world.t)]:(tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nstored_responses="kubernetes"\n[adapter]\nname="vllm"\n[stored_responses]\ntarget='+json.dumps(str(tmp_path/'target.json'))+'\nroles={A="REQ06_A",B="REQ06_B"}\n',encoding='utf-8')
    monkeypatch.setattr(http,'capture_pipes',world.runner)
    def forbidden(*a,**kw):raise AssertionError('unexpected runtime')
    monkeypatch.setattr(subprocess,'Popen',forbidden);monkeypatch.setattr(socket,'socket',forbidden)
    if mode=='attachment':
        original=Path.open
        def fail(path,*a,**kw):
            if path.name=='probe_log.jsonl' and a and a[0]=='w':raise OSError(PRIVATE)
            return original(path,*a,**kw)
        monkeypatch.setattr(Path,'open',fail)
    runner=CliRunner();common=['--store',tmp_path/'store','--config',cfg]
    def cli(args,expected=0):
        r=runner.invoke(app,list(map(str,args)));assert r.exit_code==expected,(args,r.output,r.exception)
        assert not any(s in r.output for s in (PRIVATE,KEY_A,KEY_B,'resp_byul_req06_'))
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='REQ-06').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode=='parent-cleanup' else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and e.normal_control.succeeded is None
    assert all(getattr(res.verdicts,k).value=='UNKNOWN' for k in ('security','functional','performance'))
    assert all(e.observations[k].value is False for k in ('stored_producer_authenticated','stored_object_owner_verified','stored_worker_effect_verified','stored_scope_complete'))
    trials=e.observations['stored_trials'].value;objects=e.observations['stored_objects'].value;effects=e.observations['stored_effects'].value
    conflicts=e.observations['stored_conflict_candidates'].value;missing=e.observations['stored_missing'].value
    assert 0<len(world.sent)<=128 and objects and all(c['confirmed_violation'] is False for c in conflicts)
    assert e.execution.status.value==('COMPLETED' if mode in {'enforced','vulnerable'} else 'ABORTED')
    if mode in {'enforced','vulnerable'}:
        assert len(objects)==20 and {r['origin'] for r in trials}=={'gateway','direct'}
        assert {r['kind_of_effect'] for r in effects}=={'own_api_cancel','own_previous_reference','own_overwrite_effect','cross_subject_read','cross_subject_cancel','cross_subject_previous_reference','cross_subject_overwrite'}
        assert not missing and len(conflicts)==(16 if mode=='vulnerable' else 0)
        assert all(r['policy_window_verified'] for r in trials)
    if mode=='enforced':assert world.cleanup_attempts==4 and all(r['api_cancelled_observed'] for r in e.observations['stored_cleanup'].value)
    if mode=='store-disabled':assert missing and not any(r['stage'].startswith('cross-') for r in trials)
    if mode=='synchronous':assert missing and not any(r['action']=='cancel' and r['attempted'] for r in trials)
    if mode=='cross-error':assert any(m['reason']=='cross_response_or_effect_unavailable' for m in missing)
    if mode in {'wrong-id','late-restart','engine-replaced','after-error','parent-cleanup','cleanup-error'}:
        assert any(r['attempted'] and not r['binding_verified'] or r.get('diagnostic') for r in trials) and effects
    assert not any(s in canonical_json(e.to_dict()).decode() for s in (PRIVATE,KEY_A,KEY_B,'resp_byul_req06_','foreign-response'))
    for artifact in e.artifacts:assert not any(s.encode() in (tmp_path/'store'/artifact.path).read_bytes() for s in (PRIVATE,KEY_A,KEY_B,'resp_byul_req06_','foreign-response'))
    objs=[o for k in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(k)];assert len(objs)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip']);cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objs)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def scope_value():return {'session':SESSION,'origins':['gateway','direct'],'operations':['read','cancel','previous','overwrite'],'roles':deepcopy(ROLES)}


def test_fresh_scope_never_accepts_foreign_previous_role_or_uncreated_object():
    first,second=OwnedResponseScope(scope_value()),OwnedResponseScope(scope_value());oid=first.expected_id('gateway','A','read')
    assert oid!=second.expected_id('gateway','A','read')
    with pytest.raises(ValueError):first.require_created('gateway','A','read',oid)
    with pytest.raises(ValueError):first.remember_creation_claim('gateway','B','read',oid)
    first.remember_creation_claim('gateway','A','read',oid);assert first.require_created('gateway','A','read',oid)==oid
    with pytest.raises(ValueError):second.remember_creation_claim('gateway','A','read',oid)
    first.invalidate('restart');assert len(first.report()['creation_claims'])==1 and oid not in json.dumps(first.report())
    with pytest.raises(ValueError):first.require_created('gateway','A','read',oid)


@pytest.mark.parametrize('damage',['raw','origin','duplicate','operation','role','same-env','session','extra'])
def test_scope_is_explicit_and_closed(damage):
    value=scope_value()
    if damage=='raw':value['roles']['A']=PRIVATE+'\n'
    elif damage=='origin':value['origins']=['other']
    elif damage=='duplicate':value['origins']=['gateway','gateway']
    elif damage=='operation':value['operations']=[]
    elif damage=='role':value['roles']['C']='KEY'
    elif damage=='same-env':value['roles']['B']=value['roles']['A']
    elif damage=='session':value['session']='foreign'
    else:value['object_id']=PRIVATE
    with pytest.raises(ValueError,match='STORED_RESPONSE_SCOPE_INVALID'):normalize_scope(value)


@pytest.mark.parametrize('damage',['missing','extra','expired','source','env','target','retention','roles','same-key','native032','production','model','direct-target'])
def test_invalid_policy_source_configuration_rejected_before_io(tmp_path,damage):
    from guard.adapters.vllm import VllmAdapter
    env,world,values,pack,_=inputs(tmp_path);roles=deepcopy(ROLES);keys={'REQ06_A':KEY_A,'REQ06_B':KEY_B}
    if damage=='missing':values.pop('stored_identity_policy_sha256')
    elif damage=='extra':values['unexpected']=PRIVATE
    elif damage=='expired':values.update(stored_validity_start='2020-01-01T00:00:00Z',stored_validity_end='2020-01-01T00:01:00Z')
    elif damage=='source':values['stored_serving_source_sha256']='0'*64
    elif damage=='env':values['stored_environment_sha256']='0'*64
    elif damage=='target':values['stored_target_sha256']='0'*64
    elif damage=='retention':values['stored_retention']='delete-anything'
    elif damage=='roles':roles['C']='KEY'
    elif damage=='same-key':keys['REQ06_B']=KEY_A
    elif damage=='native032':env=env.model_copy(update={'engine':env.engine.model_copy(update={'version':'0.32.0'})}).seal();values['stored_environment_sha256']=env.ref().sha256
    elif damage=='production':world.t['lab']=False;values['stored_target_sha256']=sha256_hex(canonical_json(world.t))
    elif damage=='model':env=env.model_copy(update={'model':env.model.model_copy(update={'revision':None})})
    else:world.t['direct_targets']=[];values['stored_target_sha256']=sha256_hex(canonical_json(world.t))
    path=tmp_path/'target.json';path.write_bytes(canonical_json(world.t))
    with pytest.raises((ValueError,http.KubernetesBindingError)):
        product.KubernetesStoredResponsesExecutor({'target':str(path),'roles':roles},runner=world.runner,environ=keys).run(pack.checks['REQ-06'],env,values,VllmAdapter(),tmp_path/'work')
    assert not world.calls and not world.sent


@pytest.mark.parametrize('damage',['origin','actor','owner','kind','action','stage','uncreated','raw-list','generic'])
def test_public_transport_has_no_arbitrary_path_or_foreign_object_entry(tmp_path,damage):
    env,world,_,_,_=inputs(tmp_path);t=KubernetesStoredResponsesHTTP(world.t,env,scope=scope_value(),credentials={'A':KEY_A,'B':KEY_B},runner=world.runner)
    args=['gateway','A','A','read','create','create']
    if damage=='origin':args[0]='other'
    elif damage=='actor':args[1]=PRIVATE
    elif damage=='owner':args[2]='C'
    elif damage=='kind':args[3]='../../foreign'
    elif damage=='action':args[4]='DELETE'
    elif damage=='stage':args[5]='owner-cancel'
    elif damage=='uncreated':args[4:]=['get','owner-before']
    elif damage=='raw-list':args[0]=[PRIVATE]
    with pytest.raises(http.KubernetesBindingError):
        t('kubernetes-external-probe',[{'method':'DELETE','url':'http://other/v1/responses/foreign'}],10) if damage=='generic' else t.perform(*args)
    assert not world.calls and not world.sent and PRIVATE not in canonical_json(t.report()).decode()


def test_pack_preserves_full_original_branch_and_other76_bytes(tmp_path):
    seed=baseline_pack();r=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and r['unchanged_definition_bytes']==76 and not r['full_REQ06_complete']
    assert all(p.type=='list[str]' for p in pack.checks['REQ-06'].policy_params if p.key in {'stored_origins','stored_operations'})
    for key in ('question','challenge_steps','normal_control','fail_criteria','unknown_criteria','satisfied_if','legacy','method_and_evidence'):
        assert getattr(seed.checks['REQ-06'],key)==getattr(pack.checks['REQ-06'],key)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/REQ-06.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


@pytest.mark.parametrize('kind,action,stage',[('read','get','cross-read'),('cancel-target','cancel','cross-cancel'),('previous','previous','cross-previous'),('overwrite','overwrite','cross-overwrite')])
def test_cross_actor_public_entry_requires_that_actors_own_normal_control(tmp_path,kind,action,stage):
    env,world,_,_,_=inputs(tmp_path);t=KubernetesStoredResponsesHTTP(world.t,env,scope=scope_value(),credentials={'A':KEY_A,'B':KEY_B},runner=world.runner)
    t.perform('gateway','A','A',kind,'create','create');t.perform('gateway','A','A',kind,'get','owner-before')
    if kind!='read':
        t.perform('gateway','B','B',kind,'create','create');t.perform('gateway','B','B',kind,'get','owner-before')
    count=len(world.calls),len(world.sent)
    with pytest.raises(http.KubernetesBindingError):t.perform('gateway','B','A',kind,action,stage)
    assert (len(world.calls),len(world.sent))==count


def test_exact_selected_subset_keeps_other_operations_explicitly_missing(tmp_path):
    from guard.adapters.vllm import VllmAdapter
    env,world,values,pack,_=inputs(tmp_path,origins=['gateway'],operations=['read']);path=tmp_path/'target.json';path.write_bytes(canonical_json(world.t))
    draft=product.KubernetesStoredResponsesExecutor({'target':str(path),'roles':ROLES},runner=world.runner,environ={'REQ06_A':KEY_A,'REQ06_B':KEY_B}).run(pack.checks['REQ-06'],env,values,VllmAdapter(),tmp_path/'work')
    assert draft.status.value=='COMPLETED' and len(draft.observations['stored_objects'].value)==2
    missing=draft.observations['stored_missing'].value
    assert {m['reason'] for m in missing}=={'origin_not_selected','operation_not_selected'} and not draft.observations['stored_scope_complete'].value


def test_real_pinned_probe_posts_and_gets_only_owned_listener_objects(tmp_path):
    env,world,_,_,_=inputs(tmp_path)
    def handle(conn,raw,index,stop):
        head,_,wire=raw.partition(b'\r\n\r\n');lines=head.decode().split('\r\n');method,path,_=lines[0].split(' ')
        headers=dict(line.split(': ',1) for line in lines[1:]);request={'method':method,'url':world.t['gateway']['url']+path,
            'headers':{'Authorization':headers['Authorization']},'body':json.loads(wire) if wire else None}
        status,value=world.handle(request);body=json.dumps(value).encode()
        conn.sendall(b'HTTP/1.1 '+str(status).encode()+b' OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(body)).encode()+b'\r\nConnection: close\r\n\r\n'+body)
    with _server(handle) as (url,accepted):
        world.t['gateway']['url']=url;world.t['kubernetes_routes']['gateway']['pod_ip']='127.0.0.1';world.t['kubernetes']['engine']['pod_ip']='127.0.0.10'
        world.t['engine']['trusted_url']='http://127.0.0.10:8000';world.t['direct_targets']=[{'origin':'external','url':world.t['engine']['trusted_url']}]
        def runner(argv,**kw):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kw)
            return world.runner(argv,**kw)
        t=KubernetesStoredResponsesHTTP(world.t,env,scope={**scope_value(),'origins':['gateway'],'operations':['read']},credentials={'A':KEY_A,'B':KEY_B},duration=60,runner=runner)
        for actor in ('A','B'):
            assert t.perform('gateway',actor,actor,'read','create','create')['object_shape_verified']
            assert t.perform('gateway',actor,actor,'read','get','owner-before')['object_shape_verified']
        assert t.perform('gateway','B','A','read','get','cross-read')['outcome']==403 and len(accepted)==5
    assert not any(s in canonical_json(t.report()).decode() for s in (PRIVATE,KEY_A,KEY_B,'resp_byul_req06_'))
