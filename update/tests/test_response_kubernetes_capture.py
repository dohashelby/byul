"""EXAMPLE public transport calls only; A capability is intentionally unapproved."""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from urllib.parse import urlsplit

import pytest
from guard.models import DataOrigin
from tests.builders import environment
from tools import b2_response_kubernetes_capture as capture

FIELDS=['error.message','error.type','error.param','error.code']


def response(label,status,value,**extra):
    body=json.dumps(value);raw=body.encode()
    return {'id':label,'outcome':status,'body':body,'body_bytes':len(raw),
        'body_sha256':hashlib.sha256(raw).hexdigest(),'body_truncated':False,'capture_complete':True,
        'utf8_valid':True,'eof':True,**extra}


class ExampleTransport:
    def __init__(self,target,env,**kwargs):
        self.profile=kwargs['profile'];self.target_sha256=capture._digest(target)
        self.binding={'scope':'EXAMPLE'};self.endpoint=urlsplit('http://192.0.2.1:8000')
        self.api_model='api-alias';self.calls=[];self.rows=[];self.cleanup_errors=[]
        self.variant=target.get('example_variant');self.kwargs=kwargs
    def __call__(self,network,requests,timeout):
        assert network=='kubernetes-probe' and len(requests)==1
        request=deepcopy(requests[0]);self.calls.append(request);label=request['id']
        slot=next(k for k,v in capture.CASE_IDS.items() if v==label)
        assert request['body']['model']==self.api_model
        if slot=='after' and self.variant=='after_error':
            self.cleanup_errors.append('PRIVATE_TRANSPORT_EXCEPTION')
            raise OSError('PRIVATE_TRANSPORT_EXCEPTION')
        if slot=='primary':
            assert type(request['body']['max_tokens']) is str and len(request['body']['max_tokens'])<32
            assert request['body']['stream'] is False
            value={'error':{'message':request['body']['max_tokens'],'type':'BadRequest','param':None,'code':400}}
            if self.variant in ('extra','after_error','status500','post_source_error'):value['PRIVATE_FIELD']='PRIVATE_RESPONSE'
            status=500 if self.variant=='status500' else 200 if self.variant=='status200' else 400
            if self.variant=='empty_fields':value={}
            result=response(label,status,value)
            if self.variant=='partial':result.update(capture_complete=False,eof=False)
            if self.variant=='empty_fields':result.update(body=None,body_bytes=0,body_sha256=None,capture_complete=False,eof=False)
            if self.variant=='wire_hash_diff':result['body_sha256']='a'*64
        else:
            assert urlsplit(request['url']).path=='/tokenize' and set(request['body'])=={'model','prompt','add_special_tokens','return_token_strs'}
            assert request['body']['return_token_strs'] is False and request['body']['add_special_tokens'] is False
            value={'tokens':[3],'count':1,'max_model_len':2048}
            if slot=='before' and self.variant=='before_invalid_count':value['count']=2
            result=response(label,200,value)
        self.rows.append({'id':label,'binding_verified_before_after':not(self.variant=='bad_binding' and slot=='primary'),
                          'outcome':result['outcome'],'capture_complete':result['capture_complete']})
        if self.variant=='wrong_id' and slot=='primary':result['id']='unrelated'
        return [result]
    def report(self):
        return {'profile':self.profile,'binding_sha256':capture._digest(self.binding),'requests':deepcopy(self.rows),
            'cleanup_errors':self.cleanup_errors,'collection_gap':bool(self.cleanup_errors) or any(not x['binding_verified_before_after'] for x in self.rows)}


@pytest.fixture
def inputs():
    env=environment(engine={'name':'vllm','version':'0.31.0'},model={'id':'repository/model','revision':'r1'})
    return env,{'environment_ref':env.ref().model_dump(mode='json')}


def run_example(inputs,variant=None,**kwargs):
    env,target=inputs;target={**target,'example_variant':variant};instances=[]
    def factory(*a,**kw):
        obj=ExampleTransport(*a,**kw);instances.append(obj);return obj
    result=capture.capture_validation_error(env,target,_transport_factory=factory,allowed_error_fields=FIELDS,**kwargs)
    return result,instances[0]


def test_a_unapproved_profile_closes_before_transport_without_empty_collected_evidence(inputs,monkeypatch):
    env,target=inputs;env=env.model_copy(update={'data_origin':DataOrigin.DECLARED}).seal();target['environment_ref']=env.ref().model_dump(mode='json')
    def forbidden(*a,**kw):raise AssertionError('NETWORK_FACTORY_CALLED')
    monkeypatch.setattr(capture.kubernetes_http,'KubernetesHTTP',forbidden)
    result=capture.capture_validation_error(env,target,allowed_error_fields=FIELDS)
    assert result['capture_status']=='incomplete' and result['attempted_requests']==0
    assert result['error_observation'] is None and result['normal_controls']['before']['collected'] is False
    assert result['cleanup']['complete'] is None and 'A_CAPABILITY_UNAVAILABLE' in result['gaps']
    assert datetime.fromisoformat(result['capture_started_at'])<=datetime.fromisoformat(result['capture_finished_at'])


def test_example_capture_uses_three_closed_cases_api_alias_and_hash_only_metadata(inputs):
    result,transport=run_example(inputs,binary='/owned/kubectl')
    assert result['capture_status']=='completed' and result['data_origin']=='EXAMPLE' and result['attempted_requests']==3
    assert [x['id'] for x in transport.calls]==list(capture.CASE_IDS.values())
    assert transport.kwargs['binary']=='/owned/kubectl'
    assert result['api_model_sha256']==hashlib.sha256(b'api-alias').hexdigest()
    assert all(x['body']['model']!='repository/model' for x in transport.calls)
    assert all(value['succeeded'] is True and value['token_count']==1 and value['max_model_len']==2048 for value in result['normal_controls'].values())
    assert result['error_observation']['field_path_sha256']==sorted(hashlib.sha256(x.encode()).hexdigest() for x in FIELDS)
    assert result['error_observation']['marker_contexts_seen']==['error_input']
    assert result['error_observation']['violations']==[]
    assert result['error_observation']['complete'] is False and result['error_observation']['coverage_gaps']==['MARKER_POLICY_UNSET']
    assert result['gaps']==['POLICY_UNSET'] and result['cleanup']=={'collected':True,'complete':True,'collection_gap':False,'error_sha256':[]}
    assert result['native_handler_consumption_verified'] is False and result['source_routing_verified'] is False and result['model_inference_verified'] is False
    saved=json.dumps(result)
    assert transport.calls[1]['body']['max_tokens'] not in saved and 'hello' not in saved and 'api-alias' not in saved


def test_after_failure_preserves_confirmed_forbidden_field_without_exception_or_body(inputs):
    result,_=run_example(inputs,'after_error')
    assert result['capture_status']=='incomplete' and result['attempted_requests']==3
    assert result['error_observation']['violations']==['NONPERMITTED_FIELD_SHA256:'+hashlib.sha256(b'PRIVATE_FIELD').hexdigest()]
    assert result['normal_controls']['after']['succeeded'] is None
    assert result['cleanup']['complete'] is False and len(result['cleanup']['error_sha256'])==1
    assert 'PRIVATE_' not in json.dumps(result)


@pytest.mark.parametrize('variant',['bad_binding','wrong_id'])
def test_unattributed_primary_response_is_not_projected(inputs,variant):
    result,_=run_example(inputs,variant)
    assert result['error_observation'] is None and 'PRIMARY_UNAVAILABLE' in result['gaps']


def test_http_success_is_not_classified_as_error_field_violation(inputs):
    result,_=run_example(inputs,'status200')
    assert result['error_observation'] is None and 'PRIMARY_NOT_ERROR_STATUS' in result['gaps']


def test_error_status_mismatch_retains_independent_actual_error_field_violation(inputs):
    result,_=run_example(inputs,'status500')
    assert result['capture_status']=='incomplete' and result['error_observation']['outcome']==500
    assert 'EXPECTED_ERROR_STATUS_UNCONFIRMED' in result['gaps']
    assert result['error_observation']['violations']


def test_missing_error_body_is_uncollected(inputs):
    result,_=run_example(inputs,'empty_fields')
    assert result['error_observation'] is None and 'PRIMARY_OBSERVATION_UNAVAILABLE' in result['gaps']


def test_before_shape_mismatch_prevents_primary_but_runs_after_control(inputs):
    result,transport=run_example(inputs,'before_invalid_count')
    assert result['attempted_requests']==2 and all(x['id']!=capture.CASE_IDS['primary'] for x in transport.calls)
    assert result['normal_controls']['before']['succeeded'] is False
    assert result['normal_controls']['after']['succeeded'] is True and result['error_observation'] is None


def test_explicit_field_policy_unset_remains_unknown_in_helper(inputs):
    env,target=inputs
    result=capture.capture_validation_error(env,target,_transport_factory=ExampleTransport)
    assert result['error_observation']['violations']==[]
    assert set(result['error_observation']['coverage_gaps'])=={'FIELD_POLICY_UNSET','MARKER_POLICY_UNSET'}
    assert result['allowed_error_fields_sha256'] is None and result['forbidden_contexts_configured'] is False


def test_partial_capture_remains_gap_and_does_not_gain_complete_coverage(inputs):
    result,_=run_example(inputs,'partial')
    assert result['capture_status']=='incomplete' and 'PRIMARY_CAPTURE_INCOMPLETE' in result['gaps']


def test_post_source_check_failure_does_not_erase_prior_verified_observation(inputs,monkeypatch):
    original=capture.verify_product_helpers;count=[]
    def checked():
        count.append(True)
        if len(count)==8:raise OSError('PRIVATE_POST_ERROR')
        original()
    # start + 2 per each of 3 sends + final = 8 checks.
    monkeypatch.setattr(capture,'verify_product_helpers',checked)
    result,_=run_example(inputs,'post_source_error')
    assert result['error_observation']['violations'] and result['capture_status']=='incomplete'
    assert 'A_SOURCE_POSTCHECK_UNAVAILABLE' in result['gaps'] and 'PRIVATE_' not in json.dumps(result)


@pytest.mark.parametrize('origin',[DataOrigin.DECLARED,DataOrigin.MEASURED])
def test_transport_injection_cannot_be_promoted_to_actual_origin(inputs,origin):
    env,target=inputs;env=env.model_copy(update={'data_origin':origin}).seal();target['environment_ref']=env.ref().model_dump(mode='json')
    with pytest.raises(ValueError,match='EXAMPLE_TRANSPORT_REQUIRES_EXAMPLE_ENV'):
        capture.capture_validation_error(env,target,_transport_factory=ExampleTransport)


@pytest.mark.parametrize('kwargs',[{'duration':float('nan')},{'timeout':0},{'binary':''},{'forbidden_contexts':['private_system']},{'allowed_error_fields':['']}])
def test_invalid_settings_fail_before_dispatch(inputs,kwargs):
    with pytest.raises(ValueError):capture.capture_validation_error(*inputs,_transport_factory=ExampleTransport,**kwargs)



def test_explicit_policy_and_request_hashes_bind_the_real_selected_inputs(inputs):
    result,transport=run_example(inputs)
    assert result['allowed_error_fields_sha256']==capture._digest(FIELDS)
    assert result['source_profile']==capture.SOURCE_PROFILE
    assert len(result['request_summaries'])==3
    for summary,request in zip(result['request_summaries'],transport.calls):
        assert summary['case_id']==request['id'] and summary['body_sha256']==capture._digest(request['body'])
        assert summary['request_id_sha256']==hashlib.sha256(request['headers']['X-Request-Id'].encode()).hexdigest()
        assert summary['binding_verified'] is True and summary['attempted'] is True
        assert datetime.fromisoformat(result['capture_started_at'])<=datetime.fromisoformat(summary['started_at'])<=datetime.fromisoformat(summary['finished_at'])<=datetime.fromisoformat(result['capture_finished_at'])


def test_helper_decoded_utf8_hash_is_distinct_from_a_raw_wire_hash(inputs):
    result,_=run_example(inputs,'wire_hash_diff')
    assert result['capture_status']=='completed'
    assert result['request_summaries'][1]['raw_response_bytes_sha256']=='a'*64
    assert result['error_observation']['capture_sha256']!='a'*64


def test_other_engine_version_is_unsupported_before_any_transport(inputs):
    env,target=inputs;env=env.model_copy(update={'engine':env.engine.model_copy(update={'version':'0.32.0'})}).seal()
    target['environment_ref']=env.ref().model_dump(mode='json')
    def forbidden(*a,**kw):raise AssertionError('UNSUPPORTED_DISPATCH')
    result=capture.capture_validation_error(env,target,_transport_factory=forbidden)
    assert result['attempted_requests']==0 and result['error_observation'] is None
    assert 'UNSUPPORTED_ENGINE_VERSION' in result['gaps'] and result['request_summaries']==[]
