"""Source-bound frontend facts for explicitly selected reused-input requests."""
from . import vllm_native_input_rejection as sources
from . import vllm_native_input_profiles as native_profiles


def _returned(rows,good,spec):
    returned=[r for r in rows if r.get('event')=='RETURN'and r.get('original_invoked')is True
        and all(r.get(k)==spec[k]for k in('source_module','point','source_sha256','body_code_sha256'))]
    if len(returned)!=1:return None
    row,=returned;calls=[r for r in rows if r['call_hmac_sha256']==row['call_hmac_sha256']and r['event']=='CALL']
    parents=[(start,end)for root,start,end in good if root==row['root_call_hmac_sha256']]
    if len(calls)!=1 or len(parents)!=1:return None
    call,=calls;start,end=parents[0]
    if(any(call[k]!=row[k]for k in('context','point','source_module','source_sha256','body_code_sha256',
            'request_hmac_sha256','root_call_hmac_sha256','object_hmac_sha256','operation_started_ns'))
            or not start['observation_ns']<=row['operation_started_ns']<=call['observation_ns']<=row['observation_ns']<=end['observation_ns']):return None
    return {k:row[k]for k in('source_module','point','source_sha256','body_code_sha256',
        'call_hmac_sha256','root_call_hmac_sha256','request_hmac_sha256','observation_ns')}


def project(*,engine,version,image,review,requests):
    selected=sources.profile(engine,version,image);spec=selected['cases']['token_id']
    profile=native_profiles.select(image)
    points={'frontend_validation_return_observed':(spec['source_module'],spec['point']),
        'renderer_reuse_return_observed':('vllm.renderers.online_renderer','_reused_prompt_token_ids'),
        'chat_renderer_return_observed':('vllm.renderers.online_renderer','OnlineRenderer.render_chat'),
        'input_processing_return_observed':('vllm.v1.engine.input_processor','InputProcessor.process_inputs')}
    result={'schema':'guard-current-reused-input-source-facts/1','cases':{},
        'complete':False,'native_gpu_token_consumption_verified':False,'whole_question_complete':False}
    if review.get('gaps')or review.get('image')!=image:return result
    for label in('before','challenge-0','after-0'):
        rows=review.get('requests',{}).get(label,[]);expected=requests.get(label)
        fact={'current_root_verified':False,**{key:False for key in points},'source_refs':[]}
        result['cases'][label]=fact
        if not rows or any(r.get('request_hmac_sha256')!=expected for r in rows):continue
        roots={r['call_hmac_sha256']for r in rows if r.get('event')=='CALL'and r['call_hmac_sha256']==r['root_call_hmac_sha256']}
        good=[]
        for root in roots:
            events=[r for r in rows if r['call_hmac_sha256']==root]
            call=[r for r in events if r['event']=='CALL'];end=[r for r in events if r['event']=='RETURN']
            if(len(events)==2 and len(call)==len(end)==1 and call[0]['context']==end[0]['context']
                    and call[0]['observation_ns']<=end[0]['observation_ns']):good.append((root,call[0],end[0]))
        if not good:continue
        fact['current_root_verified']=True
        for key,(module,point)in points.items():
            pin=profile['sources'][module]
            source={'source_module':module,'point':point,'source_sha256':pin['sha256'],'body_code_sha256':pin['body_code_sha256'][point]}
            row=_returned(rows,good,source)
            if row is not None:fact[key]=True;fact['source_refs'].append(row)
    result['complete']=(len(result['cases'])==3 and all(r['current_root_verified']for r in result['cases'].values())
        and all(all(result['cases'][label][key]for key in points)for label in('before','after-0')))
    return result
