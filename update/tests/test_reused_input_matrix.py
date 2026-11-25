"""Controlled response identities and selected source facts are never GPU reads."""
from copy import deepcopy
from types import SimpleNamespace as NS
import hashlib,hmac,json
from pathlib import Path
import pytest
from guard.adapters import vllm_reused_input as projection
from guard.collectors import native_input as native,reused_input_qualification as qualification
from guard.executors import kubernetes_reused_input_matrix as executor
from guard.input_policy import policy_context
from guard.models import CheckDef,Observation,load_pack
from tests.builders import assert_unconfirmed_security_failure, baseline_pack
from tests.test_native_input_capture import captured  # noqa: F401
from tests.test_native_input_rejection import current,facts as original_facts  # noqa: F401


def definition():
    doc=load_pack(Path(__file__).parents[1]/'packs/PACK-BASELINE').checks['REQ-08'].model_dump(mode='json')
    doc['collection_profile']=executor.PROFILE
    doc['policy_params']=[p for p in doc['policy_params']if p['key']not in {'reused_token_fields','reused_field_endpoints'}]
    doc['policy_params'].extend([{'key':'reused_token_fields','type':'list[str]','required':True},
        {'key':'reused_field_endpoints','type':'dict','required':True}])
    doc['required_observations']=[o for o in doc['required_observations']if o['key']not in executor.MARKERS]
    doc['required_observations'].extend({'key':key,'type':kind}for key,kind in executor.MARKERS.items())
    return CheckDef.model_validate(doc)


def values():
    fields=['prompt_token_ids','kv_transfer_params.prompt_token_ids']
    return {'reused_token_field':fields[0],'reused_token_fields':fields,
        'reused_field_endpoints':{f:'POST /v1/chat/completions'for f in fields},
        'reused_token_allowed':False,'rejection_codes':['400','422']}


def test_full_explicit_policy_is_required_before_any_target_io():
    cfg={'native_input_capture':{},'vocab_size':100,'vocab_source':'EXAMPLE','reused_token_ids':[5,7]}
    fields,endpoints=executor.validate(definition(),values(),cfg)
    assert len(fields)==2 and set(endpoints)==set(fields)
    assert not policy_context(definition(),values()).blocked
    assert policy_context(baseline_pack().checks['REQ-08'],values())is None


@pytest.mark.parametrize('damage',['field-object','duplicate','primary-missing','bad-path','endpoint-object','unknown-endpoint','capture','ids','budget'])
def test_invalid_matrix_stops_before_requests(damage):
    p=values();cfg={'native_input_capture':{},'vocab_size':100,'vocab_source':'EXAMPLE','reused_token_ids':[5,7]}
    if damage=='field-object':p['reused_token_fields'][0]={}
    elif damage=='duplicate':p['reused_token_fields'].append(p['reused_token_fields'][0])
    elif damage=='primary-missing':p['reused_token_field']='different'
    elif damage=='bad-path':p['reused_token_fields'][0]='unsafe..field'
    elif damage=='endpoint-object':p['reused_field_endpoints'][p['reused_token_fields'][0]]={}
    elif damage=='unknown-endpoint':p['reused_field_endpoints'][p['reused_token_fields'][0]]='POST /admin/reset'
    elif damage=='capture':cfg.pop('native_input_capture')
    elif damage=='ids':cfg['reused_token_ids']=[100]
    else:cfg['max_duration_seconds']=True
    with pytest.raises(ValueError):executor.validate(definition(),p,cfg)


@pytest.mark.parametrize('damage',[None,'missing-field','unqualified','unknown','wrong-endpoint','missing-complete'])
def test_rejudge_never_promotes_partial_matrix(damage):
    p=values();rows=[{'field':f,'endpoint':p['reused_field_endpoints'][f],'state':'ignored',
        'qualified_frontend_response_identity':True}for f in p['reused_token_fields']]
    obs={key:Observation(value=value,collected=True)for key,value in{
        'reused_input_trials':rows,'reused_fields_tested':p['reused_token_fields'], 'reused_matrix_complete':True}.items()}
    if damage=='missing-field':obs['reused_input_trials'].value.pop()
    elif damage=='unqualified':rows[0]['qualified_frontend_response_identity']=False
    elif damage=='unknown':rows[0]['state']='accepted_unverified'
    elif damage=='wrong-endpoint':rows[0]['endpoint']='POST /admin/reset'
    elif damage=='missing-complete':obs.pop('reused_matrix_complete')
    assert policy_context(definition(),p,obs).blocked is (damage is not None)


def test_response_wrapper_returns_original_objects_and_retains_only_identity_metadata():
    from tests.builders import completion_response
    request={'id':'EXAMPLE','headers':{'X-Request-Id':'EXAMPLE-private-request'},'body':{'model':'EXAMPLE','prompt':[0]}}
    response=completion_response(request);body=json.loads(response['body']);body['prompt_token_ids']=[8,9]
    response['body']=json.dumps(body);rows=[response]
    ctx=NS(_native_input_capture=NS(settings={'key':'a'*64}),api_model='EXAMPLE',check=definition())
    wrapper=executor.ResponseIdentity(ctx,{'reused_token_ids':[5,7]},lambda *args:rows)
    assert wrapper('engine',[request],3)is rows and rows[0]is response
    record,=wrapper.records
    expected=hmac.new(bytes.fromhex('a'*64),native.runtime.canonical(['tokens',[8,9]]),hashlib.sha256).hexdigest()
    assert record['response_tokens_hmac_sha256']==expected and record['metadata_verified']
    text=json.dumps(record)
    assert 'EXAMPLE-private-request'not in text and '[8, 9]'not in text and '[5, 7]'not in text and 'a'*64 not in text


def selected_review(current):
    review,_=original_facts(current)
    review=deepcopy(review)
    review['requests']={key:review['requests'][old]for key,old in[
        ('before','before'),('challenge-0','after_token_id'),('after-0','after_length')]}
    requests={key:rows[0]['request_hmac_sha256']for key,rows in review['requests'].items()}
    return review,requests


def test_exact_original_current_request_frontend_returns_are_narrow_source_facts(current):
    if current is None:return
    review,requests=selected_review(current)
    result=projection.project(engine='vllm',version='0.30.0',image=review['image'],review=review,requests=requests)
    assert not result['complete']and all(r['frontend_validation_return_observed']for r in result['cases'].values())
    assert all(not r['renderer_reuse_return_observed']for r in result['cases'].values())
    assert result['native_gpu_token_consumption_verified']is result['whole_question_complete']is False


def example_pipeline_review(current):
    """EXAMPLE graph additions for the pure projector, never HMAC/GPU evidence."""
    from guard.adapters import vllm_native_input_profiles
    review,requests=selected_review(current);profile=vllm_native_input_profiles.select(review['image'])
    for label,rows in review['requests'].items():
        start=next(r for r in rows if r['event']=='CALL'and r['point'].endswith('._validate_model_input'))
        end=next(r for r in rows if r['event']=='RETURN'and r['call_hmac_sha256']==start['call_hmac_sha256'])
        for module,point in [('vllm.renderers.online_renderer','_reused_prompt_token_ids'),
                ('vllm.renderers.online_renderer','OnlineRenderer.render_chat'),
                ('vllm.v1.engine.input_processor','InputProcessor.process_inputs')]:
            pin=profile['sources'][module];call=hashlib.sha256(('EXAMPLE-'+label+point).encode()).hexdigest()
            for original in(start,end):
                row=deepcopy(original);row.update(source_module=module,point=point,source_sha256=pin['sha256'],
                    body_code_sha256=pin['body_code_sha256'][point],call_hmac_sha256=call)
                rows.append(row)
    return review,requests


@pytest.mark.parametrize('missing',[None,'renderer','processing','control'])
def test_pure_projector_requires_the_whole_selected_original_frontend_graph(current,missing):
    if current is None:return
    review,requests=example_pipeline_review(current)
    if missing=='renderer':review['requests']['before']=[r for r in review['requests']['before']if r['point']!='OnlineRenderer.render_chat']
    elif missing=='processing':review['requests']['after-0']=[r for r in review['requests']['after-0']if r['point']!='InputProcessor.process_inputs']
    elif missing=='control':review['requests']['before']=[]
    result=projection.project(engine='vllm',version='0.30.0',image=review['image'],review=review,requests=requests)
    assert result['complete']is (missing is None)
    assert result['native_gpu_token_consumption_verified']is False


@pytest.mark.parametrize('damage',['source','context','request','unclosed','gap','missing'])
def test_changed_current_source_cannot_prove_validated_reuse(current,damage):
    if current is None:return
    review,requests=selected_review(current);rows=review['requests']['challenge-0']
    row=next(r for r in rows if r['event']=='RETURN'and r['point'].endswith('._validate_model_input'))
    if damage=='source':row['source_sha256']='0'*64
    elif damage=='context':row['context']={**row['context'],'pid':row['context']['pid']+1}
    elif damage=='request':requests['challenge-0']='0'*64
    elif damage=='unclosed':rows[:]=[r for r in rows if not(r['event']=='RETURN'and r['call_hmac_sha256']==r['root_call_hmac_sha256'])]
    elif damage=='gap':review['gaps']=['EXAMPLE-incomplete']
    else:rows.clear()
    result=projection.project(engine='vllm',version='0.30.0',image=review['image'],review=review,requests=requests)
    assert not(result.get('cases',{}).get('challenge-0',{}).get('current_root_verified')
        and result['cases']['challenge-0']['frontend_validation_return_observed'])


def test_empty_qualification_does_not_borrow_native_capture_registration(tmp_path,monkeypatch):
    file=tmp_path/'empty.json';file.write_text('{"schema":"guard-reused-field-matrix-qualifications/1","qualifications":[]}')
    monkeypatch.setattr(qualification,'REGISTRY',file)
    result=qualification.status('vllm','0.30.0','EXAMPLE')
    assert result['registered']is result['native_gpu_token_consumption_qualified']is result['whole69_complete']is False
    assert native.producer()['source_sha256']=='5c9e6a5c956b8f2ec0873e71941776eb7027b16b4fd185c4606dd2125938b5d6'


@pytest.mark.parametrize('case',['ignored','consumed','rejected','same-fixture','changed-after','unregistered','tampered-summary'])
def test_source_and_identity_connection_requires_distinct_stable_controls(current,monkeypatch,case):
    if current is None:return
    from guard.models import canonical_json
    ctx,draft,cfg,capture,output=current
    example,requests=example_pipeline_review(current)
    projected=projection.project(engine='vllm',version='0.30.0',image=example['image'],review=example,requests=requests)
    labels={'before':'before','challenge-0':'after_token_id','after-0':'after_length'}
    capture.requests={label:capture.requests[old]for label,old in labels.items()}
    capture.cases=[{'case':label,'request_fixture_sha256':str(index)*64,'dispatched':True,
        'outcome':400 if case=='rejected'and label=='challenge-0'else 200}for index,label in enumerate(labels)]
    review=native.verify_capture(capture.prepared,ctx.workdir/'native-input-originals',requests=capture.requests,expected_uid=capture.prepared_uid)
    artifact=next(a for a in draft.artifacts if a.name=='native_input_capture.json')
    document={'capture':{'target_generation_stable':True,'cases':capture.cases,'source_capture':review}}
    if case=='tampered-summary':document['capture']['target_generation_stable']=False
    artifact.path.write_bytes(canonical_json(document))
    key=bytes.fromhex(capture.settings['key'])
    rows=[{'label':label,'request_hmac_sha256':hmac.new(key,native.runtime.canonical(['request',capture.requests[label]]),hashlib.sha256).hexdigest(),
        'request_fixture_sha256':str(index)*64,'fixture_tokens_hmac_sha256':'f'*64,
        'response_tokens_hmac_sha256':'b'*64,'generation_shape_valid':True,'metadata_verified':True,
        'status':capture.cases[index]['outcome']}for index,label in enumerate(labels)]
    if case=='consumed':rows[1]['response_tokens_hmac_sha256']='f'*64
    elif case=='same-fixture':rows[0]['response_tokens_hmac_sha256']='f'*64;rows[2]['response_tokens_hmac_sha256']='f'*64
    elif case=='changed-after':rows[2]['response_tokens_hmac_sha256']='d'*64
    ctx.policy_values={'rejection_codes':['400','422']}
    monkeypatch.setattr(qualification,'status',lambda *args:{'registered':case!='unregistered'})
    # EXAMPLE complete projector only; the actual fixture above has no renderer
    # calls and its independent test must keep it incomplete.
    monkeypatch.setattr(ctx.adapter,'native_reused_field_facts',lambda **kwargs:projected)
    child=NS(current=ctx,response_identity=NS(records=rows))
    result=executor.facts(child,draft)
    assert result['state']==(case if case in {'ignored','consumed','rejected'}else'accepted_unverified')
    assert result['native_gpu_token_consumption_verified']is result['whole_question_complete']is False


@pytest.mark.parametrize('mode',['resolved','violation','unqualified','missing'])
def test_parent_retains_both_fields_and_original_partial_failure(tmp_path,monkeypatch,mode):
    from guard.executors.base import EvidenceDraft
    from guard.models import ExecStatus,DataOrigin
    check=definition();p=values();cfg={'native_input_capture':{},'vocab_size':100,'vocab_source':'EXAMPLE','reused_token_ids':[5,7]}
    visited=[]
    def child_run(self,subcheck,env,policy,adapter,workdir):
        field=policy['reused_token_field'];visited.append((field,subcheck.functional_probes[0].id))
        if mode=='missing'and len(visited)==2:raise ValueError('EXAMPLE-only missing owned source')
        state='consumed'if mode=='violation'and len(visited)==1 else'ignored'
        self.example_state=state
        return EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
            normal_control={'attempted':True,'succeeded':True},observations={
                'reused_input_trials':{'value':[{'field':field,'state':state,'outcome':'200'}],'collected':True},
                'functional_probe_results':{'value':{'before':'pass','after-0':'pass'},'collected':True}})
    monkeypatch.setattr(executor._Child,'run',child_run)
    monkeypatch.setattr(executor,'facts',lambda child,draft:{'state':child.example_state if mode!='unqualified'else'accepted_unverified',
        'current_links_verified':True,'qualification':{'registered':mode!='unqualified'}})
    parent=NS(cfg=cfg,_kubectl_runner=None,_expected_target_sha256=None)
    result=executor.run(parent,check,NS(data_origin=DataOrigin.EXAMPLE),p,None,tmp_path)
    assert visited==[(f,p['reused_field_endpoints'][f])for f in p['reused_token_fields']]
    assert result.data_origin==DataOrigin.EXAMPLE
    assert len(result.observations['reused_input_trials'].value)==(1 if mode=='missing'else 2)
    assert result.observations['reused_matrix_complete'].value is (mode in {'resolved','violation'})
    assert result.observations['reused_input_violations'].value==([p['reused_token_fields'][0]]if mode=='violation'else[])
    assert result.status==(ExecStatus.ABORTED if mode=='missing'else ExecStatus.COMPLETED)


@pytest.mark.parametrize('damage',[None,'renderer','source','scope','gpu-claim','whole69','dependency','hash','duplicate','extra'])
def test_separate_receipt_cannot_expand_or_borrow_the_capture_qualification(tmp_path,monkeypatch,damage):
    from guard.collectors import native_input_qualification
    proof={'schema':'byul-reused-field-matrix-actual-qualification/1','producer':qualification.producer(),
        'native_capture_dependency':native.producer(),'scope':qualification.SCOPE,'image':'EXAMPLE-only',
        'actual_gpu_source_qualified':True,'both_field_endpoint_cases_verified':True,
        'normal_control_fixture_distinct_verified':True,'current_original_source_and_response_hmac_verified':True,
        'original_chat_renderer_reuse_and_processing_returns_verified':True,
        'same_fixture_ambiguity_refused':True,'missing_or_tampered_source_refused':True,
        'native_gpu_token_consumption_qualified':False,'whole69_complete':False}
    if damage=='renderer':proof['original_chat_renderer_reuse_and_processing_returns_verified']=False
    elif damage=='source':proof['producer']={**proof['producer'],'source_sha256':'0'*64}
    elif damage=='scope':proof['scope']='EXAMPLE-another-scope'
    elif damage=='gpu-claim':proof['native_gpu_token_consumption_qualified']=True
    elif damage=='whole69':proof['whole69_complete']=True
    elif damage=='dependency':proof['native_capture_dependency']={**native.producer(),'source_sha256':'0'*64}
    raw=json.dumps(proof).encode();name='EXAMPLE-only-receipt.json';(tmp_path/name).write_bytes(raw)
    row={'engine':'mock','version':'EXAMPLE','image':proof['image'],'producer':qualification.producer(),
        'native_capture_dependency':native.producer(),'scope':qualification.SCOPE,'evidence_file':name,
        'evidence_bytes':len(raw),'evidence_sha256':hashlib.sha256(raw).hexdigest(),'verified_export_sha256':'a'*64}
    if damage=='hash':row['evidence_sha256']='0'*64
    elif damage=='extra':row['full_question_complete']=True
    registry=tmp_path/'registry.json';registry.write_text(json.dumps({'schema':'guard-reused-field-matrix-qualifications/1',
        'qualifications':[row,row]if damage=='duplicate'else[row]}))
    monkeypatch.setattr(qualification,'REGISTRY',registry);monkeypatch.setattr(qualification,'ASSETS',tmp_path)
    # Parser-only EXAMPLE data; no file in the actual registry is edited.
    monkeypatch.setattr(native_input_qualification,'status',lambda *args:{'registered':True})
    if damage is None:
        value=qualification.status(row['engine'],row['version'],row['image'])
        assert value['registered']and not value['native_gpu_token_consumption_qualified']and not value['whole69_complete']
    else:
        with pytest.raises(ValueError):qualification.status(row['engine'],row['version'],row['image'])


@pytest.mark.parametrize('registered',[True,False])
def test_both_fields_flow_through_registry_cli_evd_judge_report_and_restore(tmp_path,monkeypatch,registered):
    import shutil,yaml
    from typer.testing import CliRunner
    from guard.cli import app
    from guard.executors.base import EvidenceDraft
    from guard.models import canonical_json,sha256_hex,ExecStatus,DataOrigin
    from guard.store import Store
    from tests.builders import policy,T0
    from tests.test_model_preparation_integration import full_inputs
    check=definition();p=values();calls=[]
    def child_run(self,subcheck,env,values,adapter,workdir):
        calls.append(values['reused_token_field'])
        return EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
            normal_control={'attempted':True,'succeeded':True},observations={
                'reused_input_trials':{'value':[{'field':values['reused_token_field'],'state':'ignored','outcome':'200'}],'collected':True},
                'functional_probe_results':{'value':{'before':'pass','after-0':'pass'},'collected':True}})
    monkeypatch.setattr(executor._Child,'run',child_run)
    monkeypatch.setattr(executor,'facts',lambda *args:{'state':'ignored'if registered else'accepted_unverified',
        'current_links_verified':True,'qualification':{'registered':registered}})
    env,target=full_inputs()
    from guard.adapters.vllm_native_input_rejection import PROFILES
    selected,=json.loads(PROFILES.read_bytes())['profiles']
    env=env.model_copy(update={'engine':env.engine.model_copy(update={'version':selected['version'],'image_digest':selected['image']})}).seal()
    target['environment_ref']=env.ref().model_dump(mode='json');target['model_mapping']['environment_ref']=env.ref().model_dump(mode='json')
    target['engine']['image_digest']=selected['image'];target['kubernetes']['engine']['image']=selected['image'];target['lab']=True
    packdir=tmp_path/'pack';shutil.copytree(baseline_pack().path,packdir)
    path=packdir/'checks/REQ-08.yaml';path.write_text(yaml.safe_dump(check.model_dump(mode='json'),allow_unicode=True,sort_keys=False))
    manifest=yaml.safe_load((packdir/'pack.yaml').read_bytes());manifest['revision']+=100
    for row in manifest['files_sha256']:
        if row['path']=='checks/REQ-08.yaml':row['sha256']=sha256_hex(path.read_bytes())
    (packdir/'pack.yaml').write_text(yaml.safe_dump(manifest,allow_unicode=True,sort_keys=False));pack=load_pack(packdir)
    pol=policy(pack.ref(),scope={'include':['REQ-08']},values={'REQ-08':p})
    for name,obj in [('environment',env.to_dict()),('policy',pol.to_dict()),('target',target)]:
        (tmp_path/(name+'.json')).write_bytes(canonical_json(obj))
    cfg=tmp_path/'guard.toml';cfg.write_text('[executors]\ninput_validation="kubernetes"\n[adapter]\nname="vllm"\n[input_validation]\nvocab_size=100\nvocab_source="EXAMPLE"\nreused_token_ids=[5,7]\ntarget='+json.dumps(str(tmp_path/'target.json'))+'\n[input_validation.native_input_capture]\nprepared_dir="EXAMPLE-only"\nprepared_sha256="'+'a'*64+'"\n')
    runner=CliRunner();common=['--config',str(cfg),'--store',str(tmp_path/'store')]
    def cli(args):
        result=runner.invoke(app,list(map(str,args)))
        if result.exit_code:pytest.fail(result.output,pytrace=False)
    cli(['select','--pack',packdir,'--env',tmp_path/'environment.json','--policy',tmp_path/'policy.json',*common])
    plan,=Store.read_only(tmp_path/'store').iter_kind('PLAN')
    cli(['run','--plan',plan.id,'--pack',packdir,*common])
    cli(['judge','--plan',plan.id,'--as-of',T0.isoformat(),*common])
    cli(['report','--plan',plan.id,'--as-of',T0.isoformat(),'--json',tmp_path/'report.json',*common]);cli(['verify',*common])
    store=Store.read_only(tmp_path/'store');evd,=store.iter_kind('EVD');res,=store.iter_kind('RES')
    assert calls==p['reused_token_fields']and evd.data_origin==DataOrigin.EXAMPLE
    assert evd.execution.status==ExecStatus.COMPLETED and evd.observations['reused_matrix_complete'].value is registered
    if registered:
        assert res.verdicts.security.value == 'PASS'
    else:
        assert_unconfirmed_security_failure(res)
    cli(['evidence','export','--store',tmp_path/'store','--output',tmp_path/'bundle.zip'])
    cli(['evidence','restore','--bundle',tmp_path/'bundle.zip','--destination',tmp_path/'restored'])
    restored=Store.read_only(tmp_path/'restored')
    for kind in('ENV','POL','PLAN','EVD','RES','RPT'):
        for obj in store.iter_kind(kind):assert restored.resolve(obj.ref()).to_dict()==obj.to_dict()
