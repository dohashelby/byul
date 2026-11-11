"""Read-only before/after model-selector retirement acceptance.

The deployment owner changes the approved model/policy between commands. This
module never mutates a deployment, policy, model map, credential or engine.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.selected_runtime_check import concurrent_precondition, document, fact, header, json_body, origin, request
from tools.selected_runtime_check import UUID
from tools.selected_request_checks import record_for, platform_comparison


def bounded_object(path):
    with Path(path).open('rb') as source:raw=source.read(1024*1024+1)
    if len(raw)>1024*1024:raise ValueError('model evidence input cap')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('model evidence object required')
    return value,hashlib.sha256(raw).hexdigest()


def prior_phase_valid(before,old_model,new_model):
    life=before.get('lifecycle') or {};rows=before.get('cases')
    ids=('MODEL-normal-before','MODEL-retired-or-not-yet-allowed','MODEL-normal-after','MODEL-PHASE-ACTOR','MODEL-PHASE-STABLE')
    scope=life.get('target_scope') or {}
    if (before.get('mode')!='lifecycle' or life.get('phase')!='before' or
            (life.get('old_model'),life.get('new_model'))!=(old_model,new_model) or
            not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows) or len(rows)!=len(ids) or {r.get('id') for r in rows}!=set(ids) or
            any(r.get('result')!='passed' for r in rows) or not isinstance(life.get('principal_id'),str) or not life['principal_id'] or
            not isinstance(life.get('policy_sha256'),str) or not life['policy_sha256'] or
            not isinstance(scope,dict) or not {'policy_sha256','target_id','run_id','target_instance_id'}<=scope.keys() or
            scope.get('policy_sha256')!=life['policy_sha256'] or not isinstance(scope.get('target_id'),str) or not scope['target_id']):
        return False
    ordered={r['id']:r for r in rows};request_ids=[]
    stable=ordered['MODEL-PHASE-STABLE'].get('facts') or {}
    if stable.get('scope_before')!=scope or stable.get('scope_after')!=scope:return False
    if (ordered['MODEL-PHASE-ACTOR'].get('facts') or {}).get('same_verified_principal') is not True:return False
    for label,model,allowed in ((ids[0],old_model,True),(ids[1],new_model,False),(ids[2],old_model,True)):
        facts=ordered[label].get('facts') or {};raw=facts.get('journal') or {};rid=facts.get('request_id')
        if not isinstance(rid,str) or not UUID.fullmatch(rid):return False
        request_ids.append(rid)
        if (facts.get('model_selector')!=model or facts.get('http_status')!=(200 if allowed else 403) or
                raw.get('state')!='observed' or raw.get('record_http_status')!=(200 if allowed else 403) or
                raw.get('request_id')!=rid or raw.get('record_request_ids_match_query') is not True or
                raw.get('auth_state')!='authenticated' or raw.get('principal_id')!=life['principal_id'] or
                raw.get('policy_sha256')!=life['policy_sha256'] or
                any(key not in raw or raw[key]!=value for key,value in scope.items()) or
                raw.get('decision_result')!=('ALLOWED' if allowed else 'DENIED') or raw.get('delivery_attempted') is not allowed):
            return False
        if allowed and (facts.get('response_shape_valid') is not True or raw.get('started_decision_result')!='ALLOWED' or
                        raw.get('context_stable') is not True or raw.get('final_outcome')!='completed'):return False
        if not allowed and raw.get('reason_code')!='MODEL_NOT_ALLOWED':return False
    return len(set(request_ids))==3


def model_phase(gateway,platform,old_model,new_model,inference,viewer,phase,*,timeout=15,before_report=None,backend_config=None):
    if phase not in ('before','after') or old_model==new_model or any(not isinstance(m,str) or not 1<=len(m)<=256 or any(ord(c)<32 for c in m) for m in (old_model,new_model)):
        raise ValueError('two distinct bounded model selectors and before/after phase required')
    before,before_digest=bounded_object(before_report) if phase=='after' and before_report else ({},None)
    if phase=='after' and not prior_phase_valid(before,old_model,new_model):
        return {'execution':'not_run','cases':[fact('MODEL-BEFORE-REQUIRED',None,{'before_report_sha256':before_digest},scope='exact completed before phase required before any POST')]}
    base=origin(gateway);cases=[]
    status_reply=request('GET',base+'/api/status',token=viewer,timeout=timeout)
    status=document(status_reply) if status_reply.status==200 else {}
    policy_reply=request('GET',base+'/api/policy',token=viewer,timeout=timeout)
    current=document(policy_reply).get('current',{}) if policy_reply.status==200 else {}
    expected={'policy_sha256':current.get('sha256'),'target_id':current.get('target_id'),'run_id':status.get('run_id'),
              'target_instance_id':(((status.get('target') or {}).get('instance') or {}).get('current') or {}).get('instance_id')}
    limit=current.get('output_max_tokens')
    if (status_reply.status!=200 or policy_reply.status!=200 or 'GET /api/*' not in (status.get('auth') or {}).get('protected_routes',[]) or
        not expected['policy_sha256'] or not expected['target_id'] or type(limit) is not int or not 1<=limit<=65536):
        return {'execution':'not_run','cases':[fact('MODEL-PREFLIGHT',None,{'status_http':status_reply.status,'policy_http':policy_reply.status},scope='protected viewer and selected policy required')]}
    allow,deny=(old_model,new_model) if phase=='before' else (new_model,old_model)
    mapping={'state':'not_supplied','active_gateway_config_binding_verified':False}
    if backend_config:
        config,config_digest=bounded_object(backend_config)
        mapped=(config.get('model_map') or {}).get(allow)
        mapping={'state':'observed' if isinstance(mapped,str) and mapped else 'unknown','source_sha256':config_digest,
                 'selected_backend_model_sha256':hashlib.sha256(mapped.encode()).hexdigest() if isinstance(mapped,str) else None,
                 'endpoint_kind_match':config.get('kind')==(status.get('backend') or {}).get('kind') and config.get('base_url')==(status.get('backend') or {}).get('base_url'),
                 'active_gateway_config_binding_verified':False}
    normal_rows=[];denied_row=None
    for label,model,normal in (('normal-before',allow,True),('retired-or-not-yet-allowed',deny,False),('normal-after',allow,True)):
        ready,capacity=concurrent_precondition(base,viewer,timeout,required_slots=1)
        if ready is not True:
            cases.append(fact('MODEL-'+label,None,{'dispatched':False,'precondition':capacity},scope='model phase idle precondition'));continue
        reply=request('POST',base+'/v1/chat/completions',token=inference,
                      body=json.dumps(json_body(model,max_tokens=min(8,limit))).encode(),timeout=timeout)
        rid=header(reply,'X-Request-ID');raw=record_for(base,rid,viewer,timeout)
        value=document(reply);error=value.get('error') or {};response_model=value.get('model')
        model_digest=hashlib.sha256(response_model.encode()).hexdigest() if isinstance(response_model,str) else None
        api=platform_comparison(platform,rid,raw,timeout)
        trusted=(raw.get('state')=='observed' and raw.get('request_id')==rid and raw.get('record_request_ids_match_query') is True and
                 raw.get('auth_state')=='authenticated' and bool(raw.get('principal_id')) and
                 all(raw.get(k)==v for k,v in expected.items()))
        choices=value.get('choices')
        shape=isinstance(choices,list) and bool(choices) and all(isinstance(c,dict) and
              (isinstance((c.get('message') or {}).get('content'),str) or isinstance(c.get('text'),str)) for c in choices)
        if normal:
            matched=(reply.status==200 and shape and trusted and raw.get('context_stable') is True and
                     raw.get('decision_result')=='ALLOWED' and raw.get('delivery_attempted') is True and raw.get('final_outcome')=='completed')
            normal_rows.append(raw)
        else:
            matched=(reply.status==403 and error.get('code')=='MODEL_NOT_ALLOWED' and trusted and
                     raw.get('record_http_status')==403 and raw.get('reason_code')=='MODEL_NOT_ALLOWED' and
                     raw.get('decision_result')=='DENIED' and raw.get('delivery_attempted') is False)
            denied_row=raw
        if platform:matched=matched and api.get('same_trusted_fields') is True and api.get('invalid') is False
        passed=matched if raw.get('state')!='unknown' and api.get('state')!='unknown' else None
        if api.get('invalid') is True:passed=False
        cases.append(fact('MODEL-'+label,passed,{'dispatched':True,'http_status':reply.status,'request_id':rid,
                          'model_selector':model,'response_shape_valid':shape if normal else None,'journal':raw,'platform':api,
                          'engine_response_model_sha256':model_digest,'supplied_mapping_model_name_match':model_digest==mapping.get('selected_backend_model_sha256') if model_digest and mapping.get('selected_backend_model_sha256') else None,
                          'response_bytes':len(reply.body),'response_sha256':hashlib.sha256(reply.body).hexdigest()},
                          scope='selected Gateway model authorization and actual normal response; engine logs/deployment binding separate'))
    principals={r.get('principal_id') for r in [*normal_rows,*([denied_row] if denied_row else [])]}
    same_actor=len(normal_rows)==2 and denied_row is not None and len(principals)==1 and None not in principals
    actor_result=same_actor if None not in principals and len(normal_rows)==2 and denied_row else None
    cases.append(fact('MODEL-PHASE-ACTOR',actor_result,{'same_verified_principal':same_actor},scope='normal and rejected selectors use the same authenticated actor'))
    after_reply=request('GET',base+'/api/status',token=viewer,timeout=timeout)
    after=document(after_reply) if after_reply.status==200 else {}
    observed={'policy_sha256':(after.get('policy') or {}).get('sha256'),'target_id':(after.get('policy') or {}).get('target_id'),
              'run_id':after.get('run_id'),'target_instance_id':(((after.get('target') or {}).get('instance') or {}).get('current') or {}).get('instance_id')}
    stable=all(observed[k]==v for k,v in expected.items())
    cases.append(fact('MODEL-PHASE-STABLE',stable if after_reply.status==200 else None,{'scope_before':expected,'scope_after':observed},scope='same policy/target/run/generation throughout one phase'))
    result={'cases':cases,'lifecycle':{'phase':phase,'old_model':old_model,'new_model':new_model,'policy_sha256':expected['policy_sha256'],
            'target_scope':expected,'principal_id':normal_rows[0].get('principal_id') if normal_rows else None},
            'supplied_backend_mapping':mapping,'deployment_mutated':False,'policy_mutated':False,
            'engine_log_nonarrival_verified':False,'model_weight_change_claim':False,'security_verdict':None}
    if phase=='after':
        prior=before.get('lifecycle') or {}
        valid=(prior_phase_valid(before,old_model,new_model) and prior.get('phase')=='before' and
               (prior.get('old_model'),prior.get('new_model'))==(old_model,new_model) and bool(before.get('cases')) and
               all(c.get('result')=='passed' for c in before['cases']) and all(c['result']=='passed' for c in cases) and
               prior.get('principal_id')==result['lifecycle']['principal_id'] and bool(prior.get('principal_id')) and
               bool(prior.get('policy_sha256')) and prior['policy_sha256']!=expected['policy_sha256'])
        cases.append(fact('MODEL-RETIREMENT-TRANSITION',valid if before_report else None,
                          {'before_report_sha256':before_digest,'same_selectors_and_actor':prior.get('principal_id')==result['lifecycle']['principal_id'],
                           'policy_changed':bool(prior.get('policy_sha256')) and prior.get('policy_sha256')!=expected['policy_sha256']},
                          scope='before old allowed/new denied then after old denied/new allowed; model-selector retirement only'))
    return result
