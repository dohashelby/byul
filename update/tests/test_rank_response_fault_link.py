"""EXAMPLE fault metadata plus real closed stdlib response traces, no GPU claim."""
from copy import deepcopy

import pytest

from guard.adapters import vllm_rank_response_capture as capture
from guard.adapters import vllm_rank_response_fault_link as linker
from tests.test_rank_response_trace import setup, sealed_capture


@pytest.fixture
def example(sealed_capture):
    fixture,prepared,sink,cfg=sealed_capture
    tracer,owner,scheduled=fixture[2],fixture[5],fixture[6]
    owner.response.error=TimeoutError('PRIVATE_ERROR')
    future=owner.execute(scheduled)
    tracer.install()
    with pytest.raises(TimeoutError):future.result()
    tracer.close()
    response=capture.verify_capture(prepared,sink)
    profile=tracer.profile
    def terminal(kind):
        return next(row for row in response['rows']if row['event']=='UNWIND'
                    and profile['sources'][row['source_module']]['points'][row['point']]['kind']==kind)
    reader,native,root=(terminal(kind)for kind in('reader','native-response','root'))
    binding=response['operations'][0]['binding'];context=root['process']
    endpoint=reader['reader_endpoint'];call='a'*64
    applied=reader['started_ns']-1;end=root['observed_ns']+1_000_000
    fault={'events':[{'event':'fault_applied','at_ns':applied}],
           'native_deferred_results':[{'event':'original-result-raised','exception_kind':'timeout','context':context,
                'deferred_rpc':binding['deferred_rpc'],'at_ns':root['observed_ns']+1}]}
    case={'arm':{'schema':'guard-rank-rpc-arm/3','fault_kind':'communication-stall','expires_ns':end,
                  'reply_binding':{'response_hmac_sha256':endpoint['identity_hmac_sha256']}},
          'ready':{'call_hmac_sha256':call},'physical_resume':None}
    rpc=[{'event':'CALL','call_hmac_sha256':call,'request_hmac_sha256':binding['request_hmac_sha256'],
          'context':context,'endpoints':{'response':endpoint}}]
    return deepcopy(fault),response,deepcopy(case),deepcopy(rpc),profile


def test_selected_channel_timeout_candidate_preserves_every_unqualified_boundary(example):
    result=linker._link_verified(*example)
    assert result['selected_stalled_channel_and_direct_timeout_link_observed']
    assert result['fault_applied_ns']<result['reader_error_ns']<=result['native_error_ns']<=result['future_error_ns']<result['fault_held_until_ns']
    assert all(result[k]is False for k in ('producer_registered','fault_causality_verified','native_detector_qualified',
        'upper_delivery_verified','recovery_verified','whole_resources_released','whole69_complete'))
    assert result['native_error_after_fault_ns']==result['native_error_ns']-result['fault_applied_ns']


@pytest.mark.parametrize('damage',['other-kind','newer-arm','different-worker-channel','different-reader-channel','wrong-request',
 'source-gap','missing-cause','unclosed-graph','duplicate-operation','zero-timeout','unknown-timeout','early-release',
 'source-cancelled','different-clock','different-future','no-old-deferred','before-fault','after-expiry'])
def test_wrong_or_released_channel_and_ambiguous_source_cannot_link(example,damage):
    fault,response,case,rpc,profile=deepcopy(example)
    root=next(row for row in response['rows']if row['point']=='NativeFuture.result'and row['event']=='UNWIND')
    reader=next(row for row in response['rows']if row['point']=='Queue.dequeue'and row['event']=='UNWIND')
    if damage=='other-kind':case['arm']['fault_kind']='death'
    elif damage=='newer-arm':case['arm']['schema']='guard-rank-rpc-arm/4'
    elif damage=='different-worker-channel':case['arm']['reply_binding']['response_hmac_sha256']='0'*64
    elif damage=='different-reader-channel':reader['reader_endpoint']['identity_hmac_sha256']='0'*64
    elif damage=='wrong-request':rpc[0]['request_hmac_sha256']=['0'*64]
    elif damage=='source-gap':response['gaps']=['EXAMPLE gap']
    elif damage=='missing-cause':response['operations'][0]['reader_error_is_direct_native_cause']=False
    elif damage=='unclosed-graph':response['operations'][0]['source_four_point_graph_complete']=False
    elif damage=='duplicate-operation':response['operations'].append(deepcopy(response['operations'][0]))
    elif damage=='zero-timeout':reader['reader_timeout_ns']=0
    elif damage=='unknown-timeout':reader['reader_timeout_ns']=None
    elif damage=='early-release':case['physical_resume']={'published_ns':reader['observed_ns']-1}
    elif damage=='source-cancelled':rpc.append({'event':'CONTROL_CANCELLED','call_hmac_sha256':rpc[0]['call_hmac_sha256'],'observation_ns':reader['observed_ns']-1})
    elif damage=='different-clock':rpc[0]['context']['boot_sha256']='0'*64
    elif damage=='different-future':fault['native_deferred_results'][0]['deferred_rpc']['future_object_hmac_sha256']='0'*64
    elif damage=='no-old-deferred':fault['native_deferred_results']=[]
    elif damage=='before-fault':fault['events'][0]['at_ns']=reader['observed_ns']+1
    else:case['arm']['expires_ns']=root['observed_ns']-1
    with pytest.raises(ValueError):linker._link_verified(fault,response,case,rpc,profile)


def test_public_link_needs_original_fault_and_response_captures_not_json_flags(tmp_path):
    with pytest.raises((OSError,ValueError)):
        linker.link(tmp_path/'absent-rank',tmp_path/'absent-fault',tmp_path/'absent-response-preparation',
                    tmp_path/'absent-response-files',{'fault_causality_verified':True})
