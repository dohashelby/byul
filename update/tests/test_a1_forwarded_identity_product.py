"""REQ04 same public Gateway/role readers and canonical CLI; all I/O is EXAMPLE."""
import ast,base64,json,socket,subprocess,sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import pytest
from typer.testing import CliRunner
from guard.cli import app
from guard.models import canonical_json,load_pack,sha256_hex,utcnow
from guard.runtime import kubernetes_http as http
from guard.runtime.kubernetes_forwarded_identity_http import KubernetesForwardedIdentityHTTP,EXTERNAL
from guard.runtime.kubernetes_forwarded_identity import normalize_header_contract
from guard.runtime.bounded import capture_pipes
from guard.executors import kubernetes_forwarded_identity as product
from guard.executors.forwarded_identity_contract import FIELDS,SCHEMA,normalize_policy,validate_record
from guard.executors.owned_capture import _FILE_READER
from guard.executors.kubernetes_lifecycle_capture import supervised_source
from guard.store import Store
from tests.builders import baseline_pack,policy,T0
from tests.test_kubernetes_routes import inputs as route_inputs,World as RouteWorld
from tests.test_stream_probe import _server
from tools.build_kubernetes_forwarded_identity_pack import derive

PRIVATE='EXAMPLE_REQ04_PRIVATE'
CREDENTIAL='EXAMPLE_REQ04_ALLOWED_CREDENTIAL'
NORMAL='EXAMPLE_REQ04_NORMAL_CLAIM'
ALTERNATE='EXAMPLE_REQ04_ALTERNATE_CLAIM'
SESSION='ef'*16
HEADER={'header_name':'X-Example-User','field_kind':'identity_claim','normal_value_env':'REQ04_NORMAL','alternate_value_env':'REQ04_ALTERNATE'}


class World(RouteWorld):
    def __init__(self,env,target,mode='normal',header=None,normal=NORMAL,alternate=ALTERNATE):
        super().__init__(target);self.environment=env;self.mode=mode;self.header=header or deepcopy(HEADER)
        self.normal,self.alternate=normal,alternate;self.sent=[];self.records={'gateway':[],'engine':[]};self.reads=0;self.values=None
    def _packet(self,role):
        rows=deepcopy(self.records[role])
        if self.mode=='duplicate-phase' and role=='gateway' and rows:
            row=deepcopy(rows[0]);row['seq']=len(rows);row['event_id_sha256']='7'*64;rows.append(row)
        packet={'schema':SCHEMA,'role':role,'environment_ref':self.environment.ref().model_dump(mode='json'),
            'target_sha256':self.values['forwarded_target_sha256'],'run_sha256':sha256_hex(self.t['kubernetes']['run_id'].encode()),
            'session':SESSION,'producer_source_sha256':self.values['forwarded_'+role+'_source_sha256'],
            'producer_authenticated':False,'identity_policy_sha256':self.values['forwarded_identity_policy_sha256'],
            'header_name_sha256':sha256_hex(self.header['header_name'].lower().encode()),'field_kind':self.header['field_kind'],
            'source_ended':self.mode!='unclosed','dropped_events':1 if self.mode=='dropped' else 0,'events':rows}
        if self.mode=='bad-source':packet['producer_source_sha256']='0'*64
        if self.mode=='trust-claim':packet['producer_authenticated']=True
        if self.mode=='raw-field' and rows:rows[0]['raw']=PRIVATE
        if self.mode=='changed-record' and self.reads>=7 and rows:rows[0]['operation_sha256']='0'*64
        if self.mode=='late-source' and self.reads>=7:packet['producer_source_sha256']='0'*64
        return packet
    def add_records(self,request,slot):
        rid=request['headers']['X-Request-Id'];tag=sha256_hex(rid.encode());claim=request['headers'][self.header['header_name']]
        operation=sha256_hex(canonical_json({'method':'POST','path':'/v1/completions','body':request['body']}))
        ingress,norm,consume=(sha256_hex((rid+'-'+s).encode()) for s in ('ingress','norm','consume'))
        leak=slot=='alternate' and self.mode=='claim-leak'
        normalized=sha256_hex((claim if leak else self.normal).encode())
        principal='e'*64 if leak and self.header['field_kind']=='identity_claim' else 'd'*64
        def put(role,phase,**extra):
            row={k:None for k in FIELDS};row.update(seq=len(self.records[role]),event_id_sha256=sha256_hex((rid+phase).encode()),
                request_sha256=tag,phase=phase,operation_sha256=operation,monotonic_ns=len(self.records[role])+1,**extra)
            self.records[role].append(row)
        put('gateway','INGRESS',claim_sha256=sha256_hex(claim.encode()),call_sha256=ingress)
        put('gateway','NORMALIZED',claim_sha256=normalized,call_sha256=norm,parent_call_sha256=ingress,action='preserved' if leak else 'replaced',issuer_sha256='8'*64)
        put('engine','CONSUMED',claim_sha256=normalized,call_sha256=consume,parent_call_sha256=norm,principal_sha256=principal)
        put('engine','AUTHORIZED',principal_sha256=principal,call_sha256=sha256_hex((rid+'-authorize').encode()),parent_call_sha256=consume,authorization='allowed')
        if self.mode=='call-unlinked':self.records['engine'][-2]['parent_call_sha256']='0'*64
    def runner(self,argv,**kw):
        pipe={'complete':True,'bounded_at_source':True,'timed_out':False,'truncated':False}
        if 'exec' in argv and 'input_bytes' in kw:
            request,=json.loads(kw['input_bytes'])['requests'];self.sent.append(request['id'])
            slot=request['id'].removeprefix('req04-')
            assert self.t['kubernetes_routes']['external_probe']['pod'] in argv and self.t['kubernetes']['probe']['pod'] not in argv
            assert request['headers']['Authorization']=='Bearer '+CREDENTIAL
            assert request['headers'][self.header['header_name']]==(self.alternate if slot=='alternate' else self.normal)
            self.add_records(request,slot)
            text=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]})
            if self.mode=='before-shape' and slot=='normal-before':text='{}'
            if self.mode=='after-error' and slot=='normal-after':raise OSError(PRIVATE)
            row={'id':request['id'],'method':'POST','mode':'json','outcome':200,'body':text,'body_bytes':len(text.encode()),
                'body_sha256':sha256_hex(text.encode()),'capture_complete':True,'eof':True,'utf8_valid':True,'body_truncated':False}
            if self.mode=='http-cleanup' and slot=='normal-after':pipe.update(pipes_closed=False,read_errors=['child_reap'])
            if self.mode=='wrong-id' and slot=='alternate':row['id']='foreign'
            if self.mode=='late-restart' and slot=='normal-after':self.changed=True
            return {'returncode':0,'stdout_bytes':canonical_json([row]),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        if 'exec' in argv:
            self.reads+=1;command=argv[argv.index('--')+1:]
            role='gateway' if self.t['kubernetes_routes']['gateway']['pod'] in argv else 'engine'
            assert command[5]==supervised_source(_FILE_READER) and command[7:]==['/guard-identity/data/'+SESSION+'/forwarded-identity.json','65536']
            if self.mode=='missing-engine' and role=='engine' or self.mode=='final-missing' and self.reads>=7 or self.mode=='recovered' and self.reads<=2:raise OSError(PRIVATE)
            raw=canonical_json(self._packet(role));identity={'device':1,'inode':2,'size':len(raw),'mtime_ns':3,'ctime_ns':4}
            value={'schema':'guard-container-file/2','source_complete':True,'file_identity_stable':True,'truncated':False,
                'nofollow_components':True,'regular_file':True,'raw_base64':base64.b64encode(raw).decode(),
                'raw_retained_bytes':len(raw),'raw_retained_sha256':sha256_hex(raw),'file_before':identity,'file_after':identity,'path_after':identity}
            if self.mode=='record-cleanup' and self.reads==8:pipe.update(pipes_closed=False,read_errors=['child_reap'])
            return {'returncode':0,'stdout_bytes':canonical_json(value),'stderr_bytes':PRIVATE.encode(),'capture':pipe}
        result=super().runner(argv,**kw);result['capture'].update(pipe);return result


def inputs(tmp_path,mode='normal',header=None,normal=NORMAL,alternate=ALTERNATE):
    env,target=route_inputs();world=World(env,target,mode,header,normal,alternate);now=utcnow()
    values={'forwarded_environment_sha256':env.ref().sha256,'forwarded_target_sha256':sha256_hex(canonical_json(target)),
        'forwarded_session':SESSION,'forwarded_gateway_source_sha256':'b'*64,'forwarded_engine_source_sha256':'c'*64,
        'forwarded_identity_policy_sha256':'9'*64,'forwarded_validity_start':(now-timedelta(minutes=1)).isoformat(),'forwarded_validity_end':(now+timedelta(minutes=1)).isoformat()}
    world.values=values;receipt=derive(baseline_pack().path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    return env,world,values,pack,receipt


@pytest.mark.parametrize('mode',['normal','claim-leak','missing-engine','final-missing','recovered','duplicate-phase','call-unlinked','bad-source','trust-claim',
    'raw-field','dropped','unclosed','before-shape','after-error','wrong-id','http-cleanup','record-cleanup','late-restart','attachment','changed-record','late-source'])
def test_registered_cli_issuer_claims_errors_and_exact_ref_roundtrip(tmp_path,monkeypatch,mode):
    env,world,values,pack,_=inputs(tmp_path,mode)
    for key,value in {'REQ04_KEY':CREDENTIAL,'REQ04_NORMAL':NORMAL,'REQ04_ALTERNATE':ALTERNATE}.items():monkeypatch.setenv(key,value)
    for name,value in [('environment',env.to_dict()),('target',world.t),('policy',policy(pack.ref(),scope={'include':['REQ-04']},values={'REQ-04':values}).to_dict())]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(value))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\nforwarded_identity="kubernetes"\n[adapter]\nname="vllm"\n[forwarded_identity]\ntarget='
        +json.dumps(str(tmp_path/'target.json'))+'\nroles={allowed="REQ04_KEY"}\n[forwarded_identity.header]\n'
        +''.join(k+'='+json.dumps(v)+'\n' for k,v in HEADER.items()),encoding='utf-8')
    monkeypatch.setattr(http,'capture_pipes',world.runner);monkeypatch.setattr(product,'capture_pipes',world.runner)
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
        assert not any(s in r.output for s in (PRIVATE,CREDENTIAL,NORMAL,ALTERNATE))
    cli(['select','--pack',pack.path,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common]);plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    assert next(i for i in plan.items if i.check_id=='REQ-04').selected
    cli(['run','--plan',plan.id,'--pack',pack.path,*common],1 if mode in {'http-cleanup','record-cleanup'} else 0)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common]);cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');e,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert e.data_origin.value=='EXAMPLE' and all(getattr(res.verdicts,k).value=='UNKNOWN' for k in ('security','functional','performance'))
    assert e.normal_control.succeeded is None
    for key in ('forwarded_producer_authenticated','forwarded_principal_identity_verified','forwarded_scope_complete'):assert e.observations[key].value is False
    trials=e.observations['forwarded_trials'].value;records=e.observations['forwarded_records'].value;matches=e.observations['forwarded_matches'].value
    conflicts=e.observations['forwarded_conflict_candidates'].value
    assert len(world.sent)==(2 if mode in {'before-shape','wrong-id'} else 3)
    if mode in {'normal','claim-leak','attachment','http-cleanup','record-cleanup'}:assert len(records)==12 and all(m['declared_chain_observed'] and m['declared_authorization_link_observed'] for m in matches)
    if mode=='normal':assert e.execution.status.value=='COMPLETED' and not conflicts
    if mode=='claim-leak':assert len(conflicts)==2
    if mode in {'missing-engine','final-missing','recovered','after-error','http-cleanup','record-cleanup','late-restart','attachment','bad-source','trust-claim','raw-field','dropped','unclosed','wrong-id','before-shape','late-source'}:
        assert e.execution.status.value=='ABORTED'
    if mode in {'missing-engine','final-missing','recovered','after-error','late-restart'}:assert records
    if mode=='call-unlinked':assert not any(m['declared_chain_observed'] for m in matches)
    if mode=='duplicate-phase':assert e.observations['forwarded_missing'].value
    if mode=='changed-record':assert any(c['kind']=='event_id_content_changed' for c in conflicts) and not matches[0]['declared_chain_observed']
    if mode=='late-source':assert records and e.observations['forwarded_missing'].value
    assert all(c['confirmed_violation'] is False for c in conflicts)
    assert not any(s in canonical_json(e.to_dict()).decode() for s in (PRIVATE,CREDENTIAL,NORMAL,ALTERNATE))
    for artifact in e.artifacts:assert not any(s.encode() in (tmp_path/'store'/artifact.path).read_bytes() for s in (PRIVATE,CREDENTIAL,NORMAL,ALTERNATE))
    objects=[o for k in ('ENV','POL','PLAN','EVD','RES','RPT') for o in store.iter_kind(k)];assert len(objects)==6
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored');assert all(restored.resolve(o.ref()).to_dict()==o.to_dict() for o in objects)
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),'--store',tmp_path/'restored','--config',cfg]);assert [r.ref() for r in Store.read_only(tmp_path/'restored').iter_kind('RES')]==[res.ref()]
    cli(['verify','--store',tmp_path/'restored','--config',cfg])


def test_operating_selector_zero_one_are_not_principal_or_raw_field_leaks(tmp_path):
    from guard.adapters.vllm import VllmAdapter
    header={**HEADER,'header_name':'X-Vllm-Priority','field_kind':'operating_selector'}
    env,world,values,pack,_=inputs(tmp_path,'claim-leak',header,'0','1')
    target=tmp_path/'target.json';target.write_bytes(canonical_json(world.t))
    draft=product.KubernetesForwardedIdentityExecutor({'target':str(target),'roles':{'allowed':'KEY'},'header':header},runner=world.runner,
        environ={'KEY':CREDENTIAL,'REQ04_NORMAL':'0','REQ04_ALTERNATE':'1'}).run(pack.checks['REQ-04'],env,values,VllmAdapter(),tmp_path/'work')
    assert draft.data_origin.value=='EXAMPLE' and draft.status.value=='COMPLETED'
    assert len(draft.observations['forwarded_records'].value)==12
    c,=draft.observations['forwarded_conflict_candidates'].value
    assert c['field_kind']=='operating_selector' and not c['confirmed_violation']


def test_seed_and_all_other_definitions_remain_unchanged(tmp_path):
    seed=baseline_pack();r=derive(seed.path,tmp_path/'pack');pack=load_pack(tmp_path/'pack')
    assert len(pack.checks)==77 and r['unchanged_definition_bytes']==76 and not r['full_REQ04_complete']
    for key in ('question','challenge_steps','normal_control','fail_criteria','unknown_criteria','satisfied_if','legacy','method_and_evidence'):
        assert getattr(seed.checks['REQ-04'],key)==getattr(pack.checks['REQ-04'],key)
    for p in seed.path.rglob('*'):
        if p.is_file() and p.relative_to(seed.path).as_posix() not in {'pack.yaml','checks/REQ-04.yaml'}:assert p.read_bytes()==(pack.path/p.relative_to(seed.path)).read_bytes()


def transport(world):
    return KubernetesForwardedIdentityHTTP(world.t,world.environment,header_contract=world.header,credential=CREDENTIAL,
        normal_claim=world.normal,alternate_claim=world.alternate,runner=world.runner,duration=60)


def request(t,slot,token='ab'*16):
    return {'id':'req04-'+slot,'mode':'json','method':'POST','url':t.gateway.geturl()+'/v1/completions',
        'headers':{'Authorization':'Bearer '+CREDENTIAL,'X-Request-Id':'byul-req04-'+token+'-'+slot,t.header_contract['header_name']:t._claims[slot]},
        'body':{'model':t.api_model,'prompt':'hello','max_tokens':1,'temperature':0,'stream':False}}


@pytest.mark.parametrize('damage',['path','engine','network','method','mode','header','credential','claim','id','prompt','model','bool-token','time','after-first','private'])
def test_closed_requests_never_dispatch_invalid_shapes(tmp_path,damage):
    _,world,_,_,_=inputs(tmp_path);t=transport(world);r=request(t,'normal-before');timeout=10;network=EXTERNAL
    if damage=='path':r['url']+='/other'
    elif damage=='engine':r['url']=t.engine.geturl()+'/v1/completions'
    elif damage=='network':network='kubernetes-probe'
    elif damage=='method':r['method']='DELETE'
    elif damage=='mode':r['mode']='sse'
    elif damage=='header':r['headers']['X-Unknown']=PRIVATE
    elif damage=='credential':r['headers']['Authorization']='Bearer '+ALTERNATE
    elif damage=='claim':r['headers'][HEADER['header_name']]=ALTERNATE
    elif damage=='id':r['headers']['X-Request-Id']='foreign'
    elif damage=='prompt':r['body']['prompt']=PRIVATE
    elif damage=='model':r['body']['model']='other'
    elif damage=='bool-token':r['body']['max_tokens']=True
    elif damage=='time':timeout=11
    elif damage=='after-first':r=request(t,'normal-after')
    else:r['id']=PRIVATE
    with pytest.raises(http.KubernetesBindingError):t(network,[r],timeout)
    assert not world.calls and not world.sent and PRIVATE not in canonical_json(t.report()).decode()


@pytest.mark.parametrize('damage',['raw','secret-in-hash','source','trust','role','bool-time','extra','operation','phase'])
def test_strict_source_record_rejects_raw_fields_and_forged_claims(tmp_path,damage):
    env,world,values,_,_=inputs(tmp_path);t=transport(world);r=request(t,'normal-before');world.add_records(r,'normal-before')
    p=world._packet('gateway')
    if damage=='raw':p['events'][0]['raw']=PRIVATE
    elif damage=='secret-in-hash':p['events'][0]['principal_sha256']='f'*64
    elif damage=='source':p['producer_source_sha256']='0'*64
    elif damage=='trust':p['producer_authenticated']=True
    elif damage=='role':p['role']='engine'
    elif damage=='bool-time':p['events'][0]['monotonic_ns']=True
    elif damage=='extra':p['env']=PRIVATE
    elif damage=='operation':p['events'][0]['operation_sha256']=PRIVATE
    else:p['events'][0]['phase']='CONSUMED'
    with pytest.raises(ValueError,match='FORWARDED_IDENTITY_RECORD_INVALID'):
        validate_record(p,role='gateway',environment_ref=env.ref().model_dump(mode='json'),target_sha256=values['forwarded_target_sha256'],
            run_sha256=sha256_hex(world.t['kubernetes']['run_id'].encode()),session=SESSION,source_sha256=values['forwarded_gateway_source_sha256'],
            policy_sha256=values['forwarded_identity_policy_sha256'],header_contract=HEADER,forbidden_values=[PRIVATE,'f'*64])


@pytest.mark.parametrize('name',['Authorization','x-request-id','Cookie','Host','Content-Length','Content-Type','X-Bad\r\nInjected'])
def test_claim_slot_cannot_replace_credentials_protocol_or_correlation(name):
    with pytest.raises(ValueError):normalize_header_contract({**HEADER,'header_name':name})


@pytest.mark.parametrize('damage',['missing-policy','extra-policy','expired','naive','wide-window','environment','target','model','roles','extra-config','missing-claim','same-claim','credential-claim','production','native032','expected-target'])
def test_policy_and_exact_configuration_rejected_before_any_io(tmp_path,damage):
    from guard.adapters.vllm import VllmAdapter
    env,world,values,pack,_=inputs(tmp_path)
    cfg={'target':str(tmp_path/'target.json'),'roles':{'allowed':'KEY'},'header':deepcopy(HEADER)}
    keys={'KEY':CREDENTIAL,'REQ04_NORMAL':NORMAL,'REQ04_ALTERNATE':ALTERNATE};expected=None
    if damage=='missing-policy':values.pop('forwarded_identity_policy_sha256')
    elif damage=='extra-policy':values['unexpected']=PRIVATE
    elif damage=='expired':values.update(forwarded_validity_start='2020-01-01T00:00:00Z',forwarded_validity_end='2020-01-01T00:01:00Z')
    elif damage=='naive':values['forwarded_validity_start']='2020-01-01T00:00:00'
    elif damage=='wide-window':values['forwarded_validity_end']=(utcnow()+timedelta(minutes=20)).isoformat()
    elif damage=='environment':values['forwarded_environment_sha256']='0'*64
    elif damage=='target':values['forwarded_target_sha256']='0'*64
    elif damage=='model':env=env.model_copy(update={'model':env.model.model_copy(update={'revision':None})})
    elif damage=='roles':cfg['roles']['extra']='OTHER';keys['OTHER']=PRIVATE
    elif damage=='extra-config':cfg['allow_mutating_routes']=True
    elif damage=='missing-claim':keys.pop('REQ04_NORMAL')
    elif damage=='same-claim':keys['REQ04_ALTERNATE']=NORMAL
    elif damage=='credential-claim':keys['REQ04_NORMAL']=CREDENTIAL
    elif damage=='production':world.t['lab']=False
    elif damage=='native032':env=env.model_copy(update={'engine':env.engine.model_copy(update={'version':'0.32.0'})}).seal()
    else:expected='0'*64
    if damage in {'production','native032'}:
        values['forwarded_environment_sha256']=env.ref().sha256
        values['forwarded_target_sha256']=sha256_hex(canonical_json(world.t))
    Path(cfg['target']).write_bytes(canonical_json(world.t))
    with pytest.raises((ValueError,http.KubernetesBindingError)):
        product.KubernetesForwardedIdentityExecutor(cfg,runner=world.runner,environ=keys,expected_target_sha256=expected).run(
            pack.checks['REQ-04'],env,values,VllmAdapter(),tmp_path/'work')
    assert not world.calls and not world.sent


def test_public_transport_rejects_repeated_and_cross_session_requests(tmp_path):
    _,world,_,_,_=inputs(tmp_path);t=transport(world)
    assert t(EXTERNAL,[request(t,'normal-before')],10)[0]['capture_complete']
    count=len(world.calls)
    with pytest.raises(http.KubernetesBindingError):t(EXTERNAL,[request(t,'alternate',token='cd'*16)],10)
    assert len(world.calls)==count and world.sent==['req04-normal-before']


def test_real_pinned_probe_uses_only_selected_gateway_and_same_credential(tmp_path):
    seen=[]
    def handle(conn,raw,index,stop):
        head,_,wire=raw.partition(b'\r\n\r\n');body=json.loads(wire);seen.append(head)
        assert head.startswith(b'POST /v1/completions HTTP/1.1') and body['max_tokens']==1
        value=json.dumps({'model':'fixture-model','choices':[{'text':PRIVATE}]}).encode()
        conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '+str(len(value)).encode()+b'\r\nConnection: close\r\n\r\n'+value)
    with _server(handle) as (url,accepted):
        env,world,values,_,_=inputs(tmp_path)
        world.t['gateway']['url']=url
        # The selected owned listener is the Gateway fixture. Move the engine
        # fixture off its loopback IP; all four identities remain distinct.
        world.t['kubernetes_routes']['gateway']['pod_ip']='127.0.0.1'
        world.t['kubernetes']['engine']['pod_ip']='127.0.0.10'
        world.t['engine']['trusted_url']='http://127.0.0.10:8000'
        world.t['direct_targets']=[{'origin':'external','url':world.t['engine']['trusted_url']}]
        def runner(argv,**kw):
            if 'exec' in argv:return capture_pipes([sys.executable,'-c',argv[-2],argv[-1]],**kw)
            return world.runner(argv,**kw)
        t=KubernetesForwardedIdentityHTTP(world.t,env,header_contract=HEADER,credential=CREDENTIAL,normal_claim=NORMAL,alternate_claim=ALTERNATE,duration=30,runner=runner)
        for slot in ('normal-before','alternate','normal-after'):assert t(EXTERNAL,[request(t,slot)],10)[0]['capture_complete']
        assert len(accepted)==3
    assert all(('Bearer '+CREDENTIAL).encode() in h for h in seen) and NORMAL.encode() in seen[0] and ALTERNATE.encode() in seen[1]
    assert not any(s in canonical_json(t.report()).decode() for s in (PRIVATE,CREDENTIAL,NORMAL,ALTERNATE))
