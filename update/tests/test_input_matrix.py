"""Explicit input pairs keep the historical token-only policy unchanged."""
from copy import deepcopy
import json
from types import SimpleNamespace as NS
import pytest
from guard.adapters.vllm import VllmAdapter
from guard.adapters import vllm_input_matrix
from guard.input_matrix_policy import PROFILE,MARKERS,planned,pairs,context
from guard.input_policy import policy_context
from guard.models import Observation
from guard.runtime.kubernetes_http import KubernetesBindingError
from guard.runtime.kubernetes_input_matrix import KubernetesInputMatrixHTTP
from guard.executors import kubernetes_input_matrix as executor
from guard.executors.registry import resolve_executor
from tests.builders import baseline_pack
from tests.test_kubernetes_input_validation import transport,TOKENS,request


def definition():
    value=baseline_pack().checks['REQ-09'].model_dump(mode='json')
    value['collection_profile']=PROFILE;value['revision']+=1
    value['policy_params'].append({'key':'allowed_input_pairs','type':'list[str]','required':True})
    value['required_observations'].extend({'key':key,'type':kind}for key,kind in MARKERS.items())
    value['functional_probes'].append({'method':'POST','path':'/v1/chat/completions','via':'engine','body':{
        'model':'{model}','messages':[{'role':'user','content':'hello'}],'max_tokens':1},'expect':[200]})
    from guard.models import CheckDef
    return CheckDef.model_validate(value)


def values():
    return {'max_input_tokens':8,'rejection_codes':['400','422'],
        'allowed_endpoints':['POST /v1/completions','POST /v1/chat/completions'],
        'allowed_input_formats':['text','token_ids'],'allowed_input_pairs':[
            'POST /v1/completions|token_ids','POST /v1/completions|text','POST /v1/chat/completions|text']}


def observations():
    p=values();check=definition();ids=planned(check,p)
    return {key:Observation(value=value,collected=True)for key,value in{
        'input_endpoints_tested':p['allowed_endpoints'],'input_formats_tested':p['allowed_input_formats'],
        'input_matrix_planned':ids,'input_matrix_cases':[{'case_id':key,'rejection_state':'verified','normal_controls_pass':True}for key in ids],
        'input_matrix_complete':True}.items()}


def test_only_explicit_complete_matrix_contract_accepts_multiple_inputs():
    check=definition();p=values()
    assert not context(check,p).blocked and not policy_context(check,p,observations()).blocked
    assert len(planned(check,p))==4
    assert policy_context(baseline_pack().checks['REQ-09'],p).blocked
    assert isinstance(resolve_executor('input_validation','kubernetes')({'input_validation':{}}),executor.KubernetesMatrixInputValidationExecutor)


@pytest.mark.parametrize('damage',['pair-missing','pair-duplicate','pair-unknown','endpoint-missing','format-missing','definition','observation','normal','case','scope'])
def test_partial_matrix_never_satisfies_full_declared_scope(damage):
    check=definition();p=values();obs=observations()
    if damage=='pair-missing':p.pop('allowed_input_pairs')
    elif damage=='pair-duplicate':p['allowed_input_pairs'].append(p['allowed_input_pairs'][0])
    elif damage=='pair-unknown':p['allowed_input_pairs'][0]='POST /admin/reset|token_ids'
    elif damage=='endpoint-missing':p['allowed_input_pairs'].pop()
    elif damage=='format-missing':p['allowed_input_pairs']=['POST /v1/completions|text','POST /v1/chat/completions|text']
    elif damage=='definition':check=check.model_copy(update={'required_observations':[o for o in check.required_observations if o.key!='input_matrix_cases']})
    elif damage=='observation':obs.pop('input_matrix_cases')
    elif damage=='normal':obs['input_matrix_cases'].value[0]['normal_controls_pass']=False
    elif damage=='case':obs['input_matrix_cases'].value[0]['rejection_state']='unverified'
    else:obs['input_matrix_complete'].value=False
    assert policy_context(check,p,obs).blocked


def test_exact_engine_fixture_pairs_are_small_and_do_not_expand_unknown_pairs():
    from guard.adapters.vllm_native_input_rejection import PROFILES
    selected,=json.loads(PROFILES.read_bytes())['profiles']
    import sys
    kwargs={k:selected[k]for k in('engine','version','image')}
    args=dict(pairs=pairs(definition(),values()),model='EXAMPLE-model',vocab_size=100,limit=8)
    runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    if runtime!=selected['runtime']:
        with pytest.raises(ValueError):vllm_input_matrix.fixtures(**kwargs,**args)
        return
    fixtures=vllm_input_matrix.fixtures(**kwargs,**args)
    assert [(f['endpoint'],f['format'])for f in fixtures]==pairs(definition(),values())
    assert sum(len(f['cases'])for f in fixtures)==4
    assert fixtures[0]['cases']['token_id']['prompt']==[100]
    assert fixtures[0]['cases']['length']['prompt']==[0]*9
    assert all(len(json.dumps(body))<4096 for f in fixtures for body in(f['control'],*f['cases'].values()))
    with pytest.raises(ValueError):vllm_input_matrix.fixtures(**kwargs,**(args|{'pairs':[('POST /v1/chat/completions','token_ids')]}))
    with pytest.raises(ValueError):vllm_input_matrix.fixtures(**kwargs,**(args|{'limit':513}))


def matrix_transport(tmp_path):
    old=transport(tmp_path);value=object.__new__(KubernetesInputMatrixHTTP);value.__dict__.update(old.__dict__)
    value._native_ids=set();value._native_pending=set()
    return value


def test_transport_accepts_only_fixed_fixture_bytes_and_single_use_request_header(tmp_path):
    value=matrix_transport(tmp_path);challenge={**TOKENS,'prompt':'a '*10}
    value.declare_matrix('/v1/completions',[TOKENS,challenge])
    identifier='byul-input-'+'a'*32+'-length';value.declare_request_id(identifier)
    accepted=request(challenge,headers={'X-Request-Id':identifier})
    value._input_validation_request(accepted)
    with pytest.raises(KubernetesBindingError):value._input_validation_request(accepted)
    with pytest.raises(KubernetesBindingError):value.declare_matrix('/v1/completions',[TOKENS,challenge])


@pytest.mark.parametrize('damage',['body','path','header','extra','id','method','mode','undeclared'])
def test_transport_refuses_nonfixture_changes_without_consuming_valid_id(tmp_path,damage):
    value=matrix_transport(tmp_path);value.declare_matrix('/v1/completions',[TOKENS,{**TOKENS,'prompt':[100]}])
    identifier='byul-input-'+'a'*32+'-before';value.declare_request_id(identifier)
    req=request(TOKENS,headers={'X-Request-Id':identifier})
    if damage=='body':req['body']={**TOKENS,'prompt':'not declared'}
    elif damage=='path':req['url']=req['url'].replace('/v1/completions','/admin/reset')
    elif damage=='header':req['headers']['Authorization']='EXAMPLE-private'
    elif damage=='extra':req['body']={**TOKENS,'unapproved':True}
    elif damage=='id':req['id']='different'
    elif damage=='method':req['method']='DELETE'
    elif damage=='mode':req['mode']='sse'
    else:req['headers']['X-Request-Id']='byul-input-'+'b'*32+'-before'
    with pytest.raises(KubernetesBindingError):value._input_validation_request(req)
    assert identifier in value._native_pending


@pytest.mark.parametrize('damage',['missing-capture','large-limit','bad-budget','unknown-setting','missing-pairs'])
def test_matrix_admission_stops_before_target_or_network_io(damage):
    check=definition();p=values();cfg={'target':'/EXAMPLE-nonexistent','vocab_size':100,'vocab_source':'EXAMPLE',
        'native_input_capture':{},'max_probe_tokens':4096}
    if damage=='missing-capture':cfg.pop('native_input_capture')
    elif damage=='large-limit':p['max_input_tokens']=513
    elif damage=='bad-budget':cfg['max_probe_tokens']=8
    elif damage=='unknown-setting':cfg['shell']='unapproved'
    else:p.pop('allowed_input_pairs')
    with pytest.raises(ValueError):executor.validate(check,p,cfg)


@pytest.mark.parametrize('registered, chat_rejection', [(False, 400), (True, 400), (True, 422), (True, 413)])
def test_all_explicit_pairs_flow_through_registered_class_and_real_evd_report_export(
        tmp_path, monkeypatch, registered, chat_rejection):
    """EXAMPLE IO boundary plus real policy, class, CLI, Store and report."""
    from pathlib import Path
    import hashlib
    from typer.testing import CliRunner
    from guard.cli import app
    from guard.models import canonical_json,load_pack
    from guard.store import Store
    from guard.runtime import kubernetes_http
    from guard.collectors import native_input
    from tests.builders import policy,T0
    from tests.test_model_preparation_integration import full_inputs,FullWorld
    env,target=full_inputs()
    from guard.adapters.vllm_native_input_rejection import PROFILES
    selected,=json.loads(PROFILES.read_bytes())['profiles']
    import sys
    if sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))!=selected['runtime']:
        with pytest.raises(ValueError):vllm_input_matrix.fixtures(**{k:selected[k]for k in('engine','version','image')},pairs=pairs(definition(),values()),model='EXAMPLE',vocab_size=100,limit=8)
        return
    env=env.model_copy(update={'engine':env.engine.model_copy(update={'version':selected['version'],'image_digest':selected['image']})}).seal()
    target['environment_ref']=env.ref().model_dump(mode='json');target['model_mapping']['environment_ref']=env.ref().model_dump(mode='json')
    target['engine']['image_digest']=selected['image'];target['kubernetes']['engine']['image']=selected['image'];target['lab']=True
    world=FullWorld(target);monkeypatch.setattr(kubernetes_http,'capture_pipes',world.runner)
    calls=[]
    class ExampleCapture:
        def __init__(self,ctx,spec):self.ctx=ctx;self.cases=[];self.requests={}
        def call(self,label,request,dispatch):
            identifier='byul-input-'+'a'*31+str(len(calls)%10)+'-'+label
            self.ctx.cprobe.transport.declare_request_id(identifier);self.requests[label]=identifier
            self.cases.append({'case':label,'request_fixture_sha256':hashlib.sha256(native_input.runtime.canonical(request['body'])).hexdigest(),'dispatched':True})
            return dispatch({**request,'headers':{'X-Request-Id':identifier}})
        def finish(self):
            (self.ctx.workdir/'native-input-originals').mkdir(parents=True)
            return {'schema':'guard-native-input-current-link/1','cases':self.cases,'capture_origin':'EXAMPLE',
                'runtime_qualified':False,'producer_registered':False,'scope_complete':False}
    def controlled_probe(ctx,network,request):
        wire={**request,'id':request['id'].replace(' ','_')}
        ctx.cprobe.transport._input_validation_request(wire)
        calls.append((request['url'],deepcopy(request['body'])))
        body=request['body'];is_chat='messages'in body;prompt=body['messages'][0]['content']if is_chat else body['prompt']
        valid=prompt in([0],'hello')
        if not valid:
            return {'outcome': chat_rejection if is_chat else 400, 'body': '{"error":{"type":"EXAMPLE"}}'}
        choice={'index':0,'finish_reason':'length'}
        choice.update({'message':{'role':'assistant','content':'EXAMPLE'}}if is_chat else{'text':'EXAMPLE'})
        return {'outcome':200,'body':json.dumps({'id':'EXAMPLE-response','model':ctx.cprobe.api_model,
            'object':'chat.completion'if is_chat else'text_completion','choices':[choice],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})}
    monkeypatch.setattr(executor,'_capture_class',lambda:ExampleCapture)
    monkeypatch.setattr(executor,'bounded_probe',controlled_probe)
    def example_source(ctx,draft,capture,fixture):
        return {'projection':{'cases':{key:{'original_rejection_observed':True,'normal_returns_observed':True}for key in fixture['cases']}},
            'qualification':{'registered':registered,'whole69_complete':False},'current_fixture_links_verified':True,'diagnostic_codes':[]}
    monkeypatch.setattr(executor,'source_facts',example_source)
    packdir = Path(__file__).resolve().parents[1] / 'packs/PACK-BASELINE'
    pack = load_pack(packdir)
    pol=policy(pack.ref(),scope={'include':['REQ-09']},values={'REQ-09':values()})
    for name,obj in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(obj))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\ninput_validation="kubernetes"\n[adapter]\nname="vllm"\n[input_validation]\nvocab_size=100\nvocab_source="EXAMPLE"\nmax_probe_tokens=4096\ntarget='+json.dumps(str(tmp_path/'target.json'))+'\n[input_validation.native_input_capture]\nprepared_dir="EXAMPLE-private-fixture"\nprepared_sha256="'+'a'*64+'"\n')
    client=CliRunner();common=['--config',str(cfg),'--store',str(tmp_path/'store')]
    def cli(args):
        result=client.invoke(app,list(map(str,args)))
        if result.exit_code:pytest.fail(result.output,pytrace=False)
    cli(['select','--pack',packdir,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',packdir,*common])
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert evd.execution.status.value=='COMPLETED'and evd.data_origin.value=='EXAMPLE'
    assert len(calls)==11 and len(evd.observations['input_matrix_cases'].value)==4
    assert evd.observations['input_endpoints_tested'].value==values()['allowed_endpoints']
    assert evd.observations['input_formats_tested'].value==values()['allowed_input_formats']
    complete = registered and str(chat_rejection) in values()['rejection_codes']
    assert evd.observations['input_matrix_complete'].value is complete
    assert res.verdicts.security.value == ('PASS' if complete else 'UNKNOWN')
    outcomes = evd.observations['challenge_outcomes'].value
    assert outcomes['POST /v1/completions|token_ids|length'] == '400'
    assert outcomes['POST /v1/completions|text|length'] == '400'
    assert outcomes['POST /v1/chat/completions|text|length'] == str(chat_rejection)
    assert evd.observations['challenge_consumption'].value=={'token_id':'unknown','length':'unknown'}
    assert sum(a.name.endswith('native_input_capture.json')for a in evd.artifacts)==3
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored')
    for kind in('ENV','POL','PLAN','EVD','RES','RPT'):
        for obj in store.iter_kind(kind):assert restored.resolve(obj.ref()).to_dict()==obj.to_dict()
