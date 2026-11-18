"""Real installed CodeTypes and private HMAC fixtures; never GPU qualification."""
import ast,asyncio,dis,hashlib,json,sys,types
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from guard.adapters import vllm_native_input_rejection as projection
from guard.adapters.vllm import VllmAdapter
from guard.collectors import native_input as native,native_input_rejection_qualification as qualification
from guard.executors import native_input_link as link
from guard.executors import kubernetes_observed_input as consumer
from guard.executors.base import EvidenceDraft
from guard.models import DataOrigin,ExecStatus,canonical_json
from guard.runtime.kubernetes_native_input import KubernetesNativeInputHTTP,READER
from tests.builders import baseline_pack
from tests.test_native_input_capture import captured  # noqa: F401


def test_original_raise_sites_match_fixed_source_instructions_on_qualified_abi():
    profile,=json.loads(projection.PROFILES.read_bytes())['profiles']
    root=Path(__file__).parent/'fixtures/vllm-0.30.0'
    from guard.adapters.vllm_rank_ipc_sources import _codes,relative_code,code_digest
    for spec in profile['cases'].values():
        path=spec['source_module'].replace('.','/')+'.py';raw=(root/(path+'.txt')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==spec['source_sha256']
        code=_codes(compile(raw,path,'exec',dont_inherit=True,optimize=0))[spec['point']]
        runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
        if runtime==profile['runtime']:
            assert code_digest(relative_code(code,path))==spec['body_code_sha256']
            instruction,=[i for i in dis.get_instructions(code,show_caches=True)if i.offset==spec['code_offset']]
            assert instruction.opname=='RAISE_VARARGS'and instruction.positions.lineno==spec['source_line']
        assert spec['source_line']in{n.lineno for n in ast.walk(ast.parse(raw))if isinstance(n,ast.Raise)}


@pytest.fixture
def current(captured,tmp_path,monkeypatch):
    if captured is None:return None
    prepared,output,cfg,writer,old_invoke,modules=captured
    manifest,_,_=native.load_prepared(prepared)
    reader=types.ModuleType('EXAMPLE_owned_rejection_reader');exec(compile(READER.read_bytes(),str(READER),'exec'),vars(reader))
    original_open=__import__('os').open
    def own_open(path,*args,**kwargs):return original_open(str(prepared)if path=='/guard-native-input'else path,*args,**kwargs)
    monkeypatch.setattr(__import__('os'),'open',own_open)
    folder=prepared/'data'/cfg['session']
    def records(_manifest):
        for path in output.glob('pid-*.json'):
            if not(folder/path.name).exists():__import__('os').link(path,folder/path.name)
        return reader.run({k:manifest[k]for k in('session','settings_sha256','assets')})
    transport=object.__new__(KubernetesNativeInputHTTP);transport.binding={'EXAMPLE_generation':'a'}
    transport._native_ids=set();transport._native_pending=set();transport.injected=True
    monkeypatch.setattr(transport,'native_input_records',records)
    env=NS(engine=NS(name='vllm',version='0.30.0',image_digest=manifest['image']),model=NS(id='EXAMPLE-model'))
    ctx=NS(check=baseline_pack().checks['REQ-09'],target={'lab':True},env=env,adapter=VllmAdapter(),cprobe=transport,
        policy_values={'max_input_tokens':8,'rejection_codes':['400']},workdir=tmp_path/'work',secrets=[])
    spec={'prepared_dir':str(prepared),'prepared_sha256':hashlib.sha256(native.runtime.canonical(manifest)).hexdigest()}
    capture=link.Capture(ctx,spec);ctx._native_input_capture=capture
    params=modules['vllm.renderers.params'].TokenizeParams();params.max_input_tokens=8;params.max_output_tokens=1;params.max_total_tokens=9
    processor=modules['vllm.v1.engine.input_processor'].InputProcessor()
    processor.model_config=NS(get_vocab_size=lambda:100);processor.tokenizer=NS(max_token_id=99)
    processor._validate_prompt_len=lambda length,role:None
    modules['vllm.v1.engine.input_processor'].length_from_prompt_token_ids_or_embeds=lambda ids,embeds:len(ids)
    modules['vllm.v1.engine.input_processor'].VLLMValidationError=modules['vllm.exceptions'].VLLMValidationError
    api=modules['vllm.entrypoints.openai.completion.serving'].OpenAIServingCompletion()
    async def create(tokens,raw):
        params._token_len_check(None,tokens)
        processor._validate_model_input({'type':'token','prompt_token_ids':tokens},'decoder')
        return 'EXAMPLE-success'
    async def cleanup(coroutine,request,raw):
        try:return await coroutine
        except modules['vllm.exceptions'].VLLMValidationError:return 'EXAMPLE-rejected'
    api._create_completion=create;api._with_kv_transfer_rejection_cleanup=cleanup
    for label,prompt in[('before',[0]),('token_id',[100]),('after_token_id',[0]),('length',[0]*9),('after_length',[0])]:
        body={'model':'EXAMPLE-model','prompt':prompt,'max_tokens':1}
        def dispatch(request):
            class Raw:pass
            raw=Raw();raw.scope={'type':'http','method':'POST','path':'/v1/completions','headers':[
                (b'x-request-id',request['headers']['X-Request-Id'].encode())]}
            result=asyncio.run(api.create_completion(request['body']['prompt'],raw))
            return {'outcome':400 if result=='EXAMPLE-rejected'else 200}
        capture.call(label,{'headers':{},'body':body},dispatch)
    draft=EvidenceDraft(status=ExecStatus.COMPLETED,data_origin=DataOrigin.EXAMPLE,
        normal_control={'attempted':True,'succeeded':True},
        observations={'challenge_outcomes':{'value':{'token_id':'400','length':'400'},'collected':True},
            'challenge_rejection_results':{'value':{'token_id':'unverified','length':'unverified'},'collected':True},
            'confirmed_input_violations':{'value':['EXAMPLE-prior-violation'],'collected':True},
            'challenge_consumption':{'value':{'token_id':'unknown','length':'unknown'},'collected':True}})
    draft=link.finish(ctx,draft)
    return ctx,draft,{'vocab_size':100},capture,output


def facts(current):
    ctx,draft,cfg,capture,output=current
    reviewed=native.verify_capture(capture.prepared,ctx.workdir/'native-input-originals',requests=capture.requests,expected_uid=capture.prepared_uid)
    selected=projection.profile('vllm','0.30.0',ctx.env.engine.image_digest)
    return reviewed,selected


def test_original_source_rejections_and_normal_returns_are_positive_facts(current):
    if current is None:return
    reviewed,selected=facts(current);result=projection.project(reviewed,selected)
    assert all(result['cases'][case]['original_rejection_observed']and result['cases'][case]['normal_returns_observed']for case in('token_id','length'))
    assert result['consumer_nonreach_verified']is result['full_check_scope_verified']is result['whole69_complete']is False
    assert all('EXAMPLE'not in json.dumps(row['source_refs'])for row in result['cases'].values())


@pytest.mark.parametrize('damage',['offset','line','propagated','other-error','same-http-only','different-request','normal-missing','unclosed-handler','gap'])
def test_wrong_sites_missing_normals_or_source_gaps_cannot_prove_rejection(current,damage):
    if current is None:return
    reviewed,selected=facts(current);reviewed=deepcopy(reviewed)
    row=next(r for r in reviewed['requests']['length']if r['event']=='RAISE'and r['exception_kind']=='native-validation-error')
    if damage=='offset':row['exception_site']['code_offset']+=2
    elif damage=='line':row['exception_site']['source_line']+=1
    elif damage=='propagated':row['exception_site']['direct_raise']=False
    elif damage=='other-error':row['exception_kind']='other-error'
    elif damage=='same-http-only':reviewed['requests']['length']=[r for r in reviewed['requests']['length']if r['point']!=row['point']]
    elif damage=='different-request':row['request_hmac_sha256']='0'*64
    elif damage=='normal-missing':reviewed['requests']['after_length']=[]
    elif damage=='unclosed-handler':reviewed['requests']['length']=[r for r in reviewed['requests']['length']if not(r['call_hmac_sha256']==row['root_call_hmac_sha256']and r['event']=='RETURN')]
    else:reviewed['gaps']=['EXAMPLE_missing_record']
    result=projection.project(reviewed,selected)
    assert not(result.get('cases',{}).get('length',{}).get('original_rejection_observed')and result['cases']['length']['normal_returns_observed'])


@pytest.mark.parametrize('registered',[False,True])
def test_product_connection_needs_independent_projection_qualification_and_preserves_prior_violation(current,monkeypatch,registered):
    if current is None:return
    ctx,draft,cfg,capture,output=current
    # EXAMPLE-only branch exercise. No actual registry entry or GPU receipt.
    monkeypatch.setattr(qualification,'status',lambda *args:{'registered':registered,'whole69_complete':False,'full_check_scope_qualified':False})
    result=consumer.connect(ctx,draft,cfg)
    expected='verified'if registered else'unverified'
    assert result.observations['challenge_rejection_results'].value=={'token_id':expected,'length':expected}
    assert result.observations['confirmed_input_violations'].value==['EXAMPLE-prior-violation']
    assert result.observations['challenge_consumption'].value=={'token_id':'unknown','length':'unknown'}
    assert result.data_origin==DataOrigin.EXAMPLE
    artifact,=[a for a in result.artifacts if a.name=='native_original_rejection.json']
    document=json.loads(artifact.path.read_bytes())['capture']
    assert document['scope_complete']is document['consumer_nonreach_verified']is False
    assert document['applied_cases']==(['token_id','length']if registered else[])


@pytest.mark.parametrize('damage',['fixture','summary','record','status','uid'])
def test_changed_current_fixture_source_summary_and_uid_never_get_applied(current,monkeypatch,damage):
    if current is None:return
    ctx,draft,cfg,capture,output=current
    monkeypatch.setattr(qualification,'status',lambda *args:{'registered':True})
    if damage=='fixture':cfg['vocab_size']=101
    elif damage=='summary':
        artifact=next(a for a in draft.artifacts if a.name=='native_input_capture.json');value=json.loads(artifact.path.read_bytes());value['capture']['target_generation_stable']=False;artifact.path.write_bytes(canonical_json(value))
    elif damage=='record':next((ctx.workdir/'native-input-originals').glob('pid-*.json')).write_text('{}')
    elif damage=='status':draft.status=ExecStatus.ABORTED
    else:capture.prepared_uid+=1
    result=consumer.connect(ctx,draft,cfg)
    assert result.observations['challenge_rejection_results'].value['token_id']=='unverified'


def test_empty_projection_registry_cannot_borrow_native_sdk_registration(tmp_path,monkeypatch):
    registry=tmp_path/'empty-qualifications.json'
    registry.write_text('{"schema":"guard-original-input-rejection-qualifications/1","qualifications":[]}')
    monkeypatch.setattr(qualification,'REGISTRY',registry)
    selected,=json.loads(projection.PROFILES.read_bytes())['profiles']
    status=qualification.status(selected['engine'],selected['version'],selected['image'])
    assert not status['registered']and not status['full_check_scope_qualified']and not status['whole69_complete']
    assert native.producer()['source_sha256']=='5c9e6a5c956b8f2ec0873e71941776eb7027b16b4fd185c4606dd2125938b5d6'


@pytest.mark.parametrize('damage',[None,'normal','source','scope','whole69','dependency','hash','duplicate','extra-field'])
def test_qualification_requires_exact_separate_receipt_and_keeps_whole_scope_false(tmp_path,monkeypatch,damage):
    selected,=json.loads(projection.PROFILES.read_bytes())['profiles']
    proof={'schema':'byul-original-input-rejection-actual-qualification/1','producer':qualification.producer(),
        'native_capture_dependency':native.producer(),'scope':qualification.SCOPE,'image':selected['image'],
        'actual_gpu_source_qualified':True,'source_and_raise_instructions_matched':True,
        'current_request_fixture_links_verified':True,'normal_and_two_original_rejections_verified':True,'all_explicit_pairs_verified':True,
        'tampered_source_rejected':True,'missing_or_other_site_rejected':True,
        'whole69_complete':False,'full_check_scope_qualified':False}
    if damage=='normal':proof['normal_and_two_original_rejections_verified']=False
    elif damage=='source':proof['producer']={**proof['producer'],'source_sha256':'0'*64}
    elif damage=='scope':proof['scope']='EXAMPLE-other-scope'
    elif damage=='whole69':proof['whole69_complete']=True
    elif damage=='dependency':proof['native_capture_dependency']={**native.producer(),'source_sha256':'0'*64}
    raw=json.dumps(proof).encode();name='EXAMPLE-unit-receipt.json';(tmp_path/name).write_bytes(raw)
    row={k:selected[k]for k in('engine','version','image')}
    row.update(producer=qualification.producer(),native_capture_dependency=native.producer(),scope=qualification.SCOPE,
        evidence_file=name,evidence_bytes=len(raw),evidence_sha256=hashlib.sha256(raw).hexdigest(),verified_export_sha256='a'*64)
    if damage=='hash':row['evidence_sha256']='0'*64
    if damage=='extra-field':row['whole_check_qualified']=True
    registry=tmp_path/'EXAMPLE-registry.json';registry.write_text(json.dumps({'schema':'guard-original-input-rejection-qualifications/1',
        'qualifications':[row,row]if damage=='duplicate'else[row]}))
    monkeypatch.setattr(qualification,'REGISTRY',registry);monkeypatch.setattr(qualification,'ASSETS',tmp_path)
    # EXAMPLE-only registry parser test. This creates no actual registration.
    monkeypatch.setattr(qualification.native_input_qualification,'status',lambda *args:{'registered':True})
    if damage is None:
        result=qualification.status(row['engine'],row['version'],row['image'])
        assert result['registered']and not result['whole69_complete']and not result['full_check_scope_qualified']
    else:
        with pytest.raises(ValueError):qualification.status(row['engine'],row['version'],row['image'])
