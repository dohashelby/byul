"""Installed .30 GPU source fixtures; CPU EXAMPLE calls are not engine runs."""
import hashlib
import json
from pathlib import Path
import types
from types import SimpleNamespace as NS

import pytest

from guard.adapters import engine_profiles
from guard.adapters.vllm import VllmAdapter, _INPUT_REJECTION_REVIEWS
from tests.test_a1_rank_failure_sensor import codes

FIXTURES=Path(__file__).parent/'fixtures/vllm-0.30.0'
REVIEW=_INPUT_REJECTION_REVIEWS['0.30.0']


def method(path,point,namespace):
    raw=(FIXTURES/(path+'.txt')).read_bytes()
    root=compile(raw,path,'exec',dont_inherit=True,optimize=0)
    return types.FunctionType(codes(root)[point],namespace)


def exception_class():
    # This source imports only standard-library HTTPStatus and Any.
    scope={}
    path='vllm/exceptions.py'
    exec(compile((FIXTURES/(path+'.txt')).read_bytes(),path,'exec',dont_inherit=True,optimize=0),scope)
    return scope['VLLMValidationError']


def proof(message,*,case='token_id',param=None,prompt=None,image=REVIEW['image'],status=400):
    return VllmAdapter().input_rejection_proof(engine_version='0.30.0',engine_image_digest=image,
        case=case,method='POST',path='/v1/completions',
        request_body={'model':'EXAMPLE','prompt':([100] if prompt is None else prompt),'max_tokens':1},
        response_status=status,response_body={'error':{'message':message,'type':'BadRequestError','param':param,'code':400}})


def test_all_rejection_source_rows_match_exported_installed_fixtures():
    provenance=json.loads((FIXTURES/'PROVENANCE.json').read_bytes())['files']
    for path,digest,lines in [*REVIEW['token'],*REVIEW['length'],REVIEW['error'],REVIEW['exception']]:
        raw=(FIXTURES/(path+'.txt')).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==digest==provenance[path+'.txt']['sha256']
        for span in lines.split(','):
            assert 1<=int(span.split('-')[-1])<=len(raw.splitlines())
    support=engine_profiles.resolve('vllm','0.30.0','request_input_checks')
    assert support.available and not support.runtime_qualified


def test_exact_token_validator_produces_reviewed_error_without_importing_engine():
    error=exception_class()
    fn=method('vllm/v1/engine/input_processor.py','InputProcessor._validate_model_input',{
        'VLLMValidationError':error,'length_from_prompt_token_ids_or_embeds':lambda ids,embeds:len(ids)})
    obj=NS(model_config=NS(get_vocab_size=lambda:100),tokenizer=NS(max_token_id=99),
        _validate_prompt_len=lambda length,kind:None)
    assert fn(obj,{'type':'token','prompt_token_ids':[99]},'decoder') is None
    with pytest.raises(error) as caught:fn(obj,{'type':'token','prompt_token_ids':[100]},'decoder')
    result=proof(str(caught.value))
    assert result['state']=='verified' and result['reason']=='REVIEWED_INPUT_REJECTION'
    assert result['sources'][0]['sha256']==REVIEW['token'][0][1]


@pytest.mark.parametrize('count',[9,10])
def test_exact_length_validator_and_exception_keep_numeric_request_binding(count):
    error=exception_class()
    fn=method('vllm/renderers/params.py','TokenizeParams._token_len_check',{'VLLMValidationError':error})
    obj=NS(max_input_tokens=8,max_output_tokens=1,max_total_tokens=9)
    assert fn(obj,None,[0]*8)==[0]*8
    with pytest.raises(error) as caught:fn(obj,None,[0]*count)
    assert caught.value.parameter=='input_tokens' and caught.value.value==count
    result=proof(str(caught.value),case='length',param='input_tokens',prompt=[0]*count)
    assert result['state']=='verified'
    assert proof(str(caught.value),case='length',param='input_tokens',prompt=[0]*(count+1))['state']=='unverified'


@pytest.mark.parametrize('damage',['image','alias','version-image','message','status','parameter'])
def test_rejection_cannot_be_inherited_by_unreviewed_response_or_image(damage):
    kwargs={}
    message='Token id 100 is out of vocabulary'
    if damage=='image':kwargs['image']=REVIEW['image'][:-1]+'1'
    elif damage=='alias':kwargs['image']=REVIEW['image'].removeprefix('docker.io/')
    elif damage=='version-image':kwargs['image']='docker.io/vllm/vllm-openai@sha256:'+'a'*64
    elif damage=='message':message='Token id 99 is out of vocabulary'
    elif damage=='status':kwargs['status']=200
    else:kwargs['param']='model'
    result=proof(message,**kwargs)
    assert result['state']=='unverified' and result['sources']==[]


def test_actual_nested_reuse_branch_consumes_only_forwarded_ids_and_pops_metadata():
    fn=method('vllm/renderers/online_renderer.py','_reused_prompt_token_ids',{})
    request=NS(prompt_token_ids=[91],kv_transfer_params={'prompt_token_ids':[5,7],'other':'EXAMPLE'})
    assert fn(request)==[5,7]
    assert request.kv_transfer_params=={'other':'EXAMPLE'} and request.prompt_token_ids==[91]
    assert fn(request) is None
    assert fn(NS(prompt_token_ids=[5,7])) is None
