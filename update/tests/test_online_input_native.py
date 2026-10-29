"""EXAMPLE callback ABI tests, distinct from real-source CPU object checks."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace

import pytest
from guard.adapters import vllm_online_input as native


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


class Tokenizer:
    max_token_id=32
    def get_vocab(self):return {'PRIVATE_TOKEN_'+str(i):i for i in range(32)}


class Model:
    runner_type='generate'
    max_model_len=16
    def get_vocab_size(self):return 32


@pytest.fixture
def owner():
    return SimpleNamespace(model_config=Model(),renderer=SimpleNamespace(tokenizer=Tokenizer()),
        trust_request_chat_template=False,trust_request_mm_kwargs=False)


@pytest.fixture
def parsed_request():
    return SimpleNamespace(chat_template='PRIVATE_BODY',chat_template_kwargs={'suffix':'PRIVATE_SUFFIX'},
        mm_processor_kwargs={'code_revision':'PRIVATE_REVISION'},media_io_kwargs=None,
        echo=False,kv_transfer_params={'prompt_token_ids':[32]},__pydantic_fields_set__={'chat_template','kv_transfer_params'})


def test_render_call_records_parsed_hashes_without_body_or_mutation(owner,parsed_request):
    original=deepcopy(vars(parsed_request))
    result=native.snapshot_online_input('OnlineRenderer.render_chat',(owner,parsed_request),{},phase='CALL')
    assert result['schema']==native.SCHEMA and result['status']=='collected'
    fields=result['metadata']['parsed_fields']
    assert fields['chat_template']['canonical_sha256']==digest('PRIVATE_BODY')
    assert fields['chat_template']['explicitly_set'] is True and fields['echo']['explicitly_set'] is False
    assert fields['kv_transfer_params']['canonical_sha256']==digest({'prompt_token_ids':[32]})
    assert result['metadata']['trust_request_chat_template'] is False
    assert result['metadata']['effective_config']=={'max_model_len':16,'model_vocab_size':32,'runner_type':'generate'}
    assert vars(parsed_request)==original and 'PRIVATE_' not in json.dumps(result)


def test_process_return_binds_input_admitted_and_independent_domain(owner):
    rid='PRIVATE_NATIVE_RID';sha=hashlib.sha256(rid.encode()).hexdigest()
    binding={name:hashlib.sha256(name.encode()).hexdigest() for name in native.BINDING_HASHES}
    binding.update(native_request_id_sha256=sha,pid=123,raw_secret='PRIVATE_SECRET')
    prompt={'type':'token','prompt_token_ids':[32]};output=SimpleNamespace(prompt_token_ids=[32])
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,rid,prompt,None,('generate',)),{},
        phase='RETURN',result=output,binding=binding)
    meta=result['metadata'];domain=meta['token_id_domain']
    assert meta['processor_input']['canonical_sha256']==meta['admitted']['canonical_sha256']==digest([32])
    assert meta['native_request_id_sha256']==sha and meta['native_request_binding_matches'] is True
    assert result['binding']['pid']==123 and 'raw_secret' not in result['binding']
    assert meta['reported_cache_max_token_id']==32
    assert domain=={'status':'collected','model_vocab_size':32,'tokenizer_id_count':32,'min':0,'max':31,
        'canonical_sha256':digest(list(range(32))),'contiguous_from_zero':True,
        'request_ids_in_domain':False,'request_ids_within_model_vocab':False,'request_ids_canonical_sha256':digest([32])}
    assert meta['admitted_token_id_domain']==domain
    assert prompt['prompt_token_ids']==[32] and output.prompt_token_ids==[32]
    assert 'PRIVATE_' not in json.dumps(result)


def test_real_added_id_and_model_domain_are_separate(owner):
    class Added(Tokenizer):
        def get_vocab(self):return {str(i):i for i in range(33)}
    owner.renderer.tokenizer=Added()
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,'x',{'prompt_token_ids':[32]}),{},phase='CALL')
    domain=result['metadata']['token_id_domain']
    assert domain['tokenizer_id_count']==33 and domain['request_ids_in_domain'] is True
    assert domain['request_ids_within_model_vocab'] is False


@pytest.mark.parametrize('phase',['CALL','RETURN','RAISE'])
def test_phase_records_actual_return_or_exception_only(owner,phase):
    error=ValueError('PRIVATE_EXCEPTION_BODY')
    response=SimpleNamespace(error=SimpleNamespace(code=400,message='PRIVATE_RESPONSE_BODY'))
    result=native.snapshot_online_input('OnlineRenderer.validate_chat_template',
        (owner,'PRIVATE_TEMPLATE',{},False),{},phase=phase,result=response,exception=error)
    meta=result['metadata']
    assert ('native_error_code' in meta)==(phase=='RETURN')
    assert ('exception_type' in meta)==(phase=='RAISE')
    if phase=='RETURN':assert meta['native_error_code']==400 and meta['return_kind']=='error_response'
    if phase=='RAISE':assert meta['exception_type']=='ValueError'
    assert 'PRIVATE_' not in json.dumps(result)


def test_mm_gate_uses_actual_keyword_flags_without_inventing_mm_consumption():
    result=native.snapshot_online_input('protocol.validate_request_mm_kwargs',(),
        {'mm_processor_kwargs':{'code_revision':'PRIVATE_REVISION'},'media_io_kwargs':None,'trust_request_mm_kwargs':False},
        phase='RAISE',exception=RuntimeError('PRIVATE_ERROR'))
    meta=result['metadata']
    assert meta['trust_request_mm_kwargs'] is False
    assert meta['parsed_fields']['mm_processor_kwargs']['canonical_sha256']==digest({'code_revision':'PRIVATE_REVISION'})
    assert 'admitted' not in meta and 'mm_consumed' not in meta and 'PRIVATE_' not in json.dumps(result)


def test_rendered_input_return_is_hash_only_and_reuse_call_keeps_phase_metadata(owner,parsed_request):
    result=native.snapshot_online_input('OnlineRenderer.preprocess_chat',(owner,parsed_request),{},phase='RETURN',
        result=([],[{'prompt_token_ids':[3,4]}]))
    assert result['metadata']['rendered_inputs'][0]['canonical_sha256']==digest([3,4])
    before=deepcopy(parsed_request.kv_transfer_params)
    call=native.snapshot_online_input('online_renderer._reused_prompt_token_ids',(parsed_request,),{},phase='CALL')
    returned=native.snapshot_online_input('online_renderer._reused_prompt_token_ids',(parsed_request,),{},phase='RETURN',result=[32])
    assert call['metadata']['parsed_fields']['kv_transfer_params']['canonical_sha256']==digest(before)
    assert returned['metadata']['reused']['canonical_sha256']==digest([32])
    assert parsed_request.kv_transfer_params==before


def test_iterators_are_never_consumed(owner):
    touched=[]
    def values():
        touched.append(True);yield 32
    iterator=values()
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,'x',{'prompt_token_ids':iterator}),{},
        phase='RETURN',result=SimpleNamespace(prompt_token_ids=iterator))
    assert result['metadata']['processor_input']['status']=='unavailable'
    assert result['metadata']['admitted']['status']=='unavailable' and touched==[]


def test_unknown_properties_and_repr_are_not_executed():
    class Poison:
        @property
        def model_config(self):raise AssertionError('PROPERTY_CALLED')
        @property
        def chat_template(self):raise AssertionError('PROPERTY_CALLED')
        def __repr__(self):raise AssertionError('REPR_CALLED')
    result=native.snapshot_online_input('OnlineRenderer.render_chat',(Poison(),Poison()),{},phase='RAISE',exception=Poison())
    assert result['metadata']['parsed_fields']['chat_template']['present'] is False
    assert result['metadata']['exception_type']=='Exception'


def test_slots_are_observed_without_property_access(owner):
    class Output:
        __slots__=('prompt_token_ids',)
    output=Output();output.prompt_token_ids=[3,4]
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,'id',{'prompt_token_ids':[3,4]}),{},phase='RETURN',result=output)
    assert result['metadata']['admitted']['count']==2


def test_binding_mismatch_is_observed_not_repaired(owner):
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,'real',{'prompt_token_ids':[3]}),{},phase='CALL',
        binding={'native_request_id_sha256':'0'*64,'asgi_request_id_sha256':'PRIVATE','pid':True})
    assert result['metadata']['native_request_binding_matches'] is False
    assert result['binding']=={'native_request_id_sha256':'0'*64}


def test_getter_failure_remains_unknown_without_changing_original_exception(owner):
    class Broken(Tokenizer):
        def get_vocab(self):raise RuntimeError('PRIVATE_GETTER_ERROR')
    owner.renderer.tokenizer=Broken();error=ValueError('PRIVATE_NATIVE_ERROR')
    result=native.snapshot_online_input('InputProcessor._validate_model_input',(owner,{'prompt_token_ids':[32]}),{},
        phase='RAISE',exception=error)
    assert result['status']=='partial' and result['metadata']['token_id_domain']=={'status':'unavailable'}
    assert result['metadata']['exception_type']=='ValueError' and str(error)=='PRIVATE_NATIVE_ERROR'
    assert 'PRIVATE_' not in json.dumps(result)


def test_capture_bounds_are_unknown_and_do_not_become_request_limits(owner,monkeypatch):
    monkeypatch.setattr(native,'MAX_DOMAIN_IDS',2);monkeypatch.setattr(native,'MAX_IDS',2)
    result=native.snapshot_online_input('InputProcessor.process_inputs',(owner,'x',{'prompt_token_ids':[1,2,3]}),{},phase='CALL')
    assert result['metadata']['processor_input']['status']=='unavailable'
    assert result['metadata']['token_id_domain']['status']=='unavailable'
    assert result['status']=='partial'


@pytest.mark.parametrize('value',[{'x':'x'*70000}, {'x':object()}, {'x':float('nan')}])
def test_unsupported_or_oversized_fields_never_use_repr_or_raw_values(owner,value):
    parsed_request=SimpleNamespace(chat_template_kwargs=value)
    result=native.snapshot_online_input('OnlineRenderer.render_chat',(owner,parsed_request),{},phase='CALL')
    assert result['metadata']['parsed_fields']['chat_template_kwargs']['status']=='unavailable'
    assert 'canonical_sha256' not in result['metadata']['parsed_fields']['chat_template_kwargs']


def test_unknown_point_and_bad_call_shapes_do_not_throw():
    for point,args,kwargs,phase in [('private-point',(),{},'CALL'),([],(),{},'CALL'),
        ('OnlineRenderer.render_chat',iter(()),{},'CALL'),('OnlineRenderer.render_chat',(),{},'BAD')]:
        result=native.snapshot_online_input(point,args,kwargs,phase=phase)
        assert result['status']=='unavailable' and result['metadata']=={}


@pytest.mark.parametrize('runner_type',['generate','pooling','draft','unknown',None])
def test_effective_runner_type_is_actual_closed_source_enum(owner,runner_type):
    owner.model_config.runner_type=runner_type
    result=native.snapshot_online_input('InputProcessor._validate_prompt_len',(owner,16,'decoder'),{},phase='CALL')
    assert result['metadata']['candidate_prompt_count']==16
    assert result['metadata']['effective_config']['runner_type']==(runner_type if runner_type in ('generate','pooling','draft') else 'unavailable')
    assert result['metadata']['prompt_type']=='decoder'
    assert 'length_violation' not in result['metadata']


@pytest.mark.parametrize('point,argname',[
    ('InputProcessor._validate_prompt_len','prompt_len'),
    ('InputProcessor._validate_model_input','prompt_input')])
@pytest.mark.parametrize('prompt_type',['decoder','encoder','unknown',None])
@pytest.mark.parametrize('phase',['CALL','RETURN','RAISE'])
def test_validation_point_prompt_type_uses_actual_positional_or_keyword_argument(owner,point,argname,prompt_type,phase):
    value=16 if argname=='prompt_len' else {'prompt_token_ids':[3]*16}
    kwargs={argname:value,'prompt_type':prompt_type}
    result=native.snapshot_online_input(point,(owner,),kwargs,phase=phase,exception=ValueError('PRIVATE_ERROR'))
    assert result['metadata']['prompt_type']==(prompt_type if prompt_type in ('decoder','encoder') else 'unavailable')
    positional=native.snapshot_online_input(point,(owner,value,prompt_type),{},phase=phase)
    assert positional['metadata']['prompt_type']==result['metadata']['prompt_type']
    assert result['metadata']['effective_config']['runner_type']=='generate'


def test_missing_model_config_does_not_invent_generate(owner):
    owner.model_config=SimpleNamespace(max_model_len=16,get_vocab_size=lambda:32)
    result=native.snapshot_online_input('InputProcessor._validate_prompt_len',(owner,16,'decoder'),{},phase='RETURN')
    assert result['metadata']['effective_config']['runner_type']=='unavailable'
    assert result['metadata']['prompt_type']=='decoder'
