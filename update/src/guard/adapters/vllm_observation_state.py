"""OBS02 source correlations; listener, requests and worker truth stay distinct."""
from __future__ import annotations
import hashlib
from .vllm_lifecycle_links import assignment,asgi_assignment
from .vllm_request_outputs import project_outputs

COMPONENTS=frozenset({'http_listener','request_selection','asgi_application','native_request_state','native_forward','output_collector'})
MAX_EVENTS=4096


def project(packet,trials,required):
    result={'bindings':[],'events':[],'asgi_events':[],'output_events':[],'output_links':[],
        'diagnostic_codes':[],'conflict_candidates':[],'component_status':[],
        'producer_authenticated':False,'worker_identity_verified':False,'state_semantics_verified':False,
        'whole_scope_complete':False}
    if not packet:
        result['diagnostic_codes'].append('OBSERVATION_STATE_NATIVE_UNAVAILABLE')
    else:
        for trial in trials:
            if not trial['attempted'] or trial['method']!='POST':continue
            matches=[]
            for index,recording in enumerate(packet.get('recordings',[])):
                capture=recording.get('capture')
                if not capture:continue
                rows=capture['records'];ctx=rows[0]['payload']['context']
                selected=[r for r in rows if r['event']=='SELECTION' and r['payload']['phase']=='RETURN'
                    and r['payload']['point']=='InputProcessor.assign_request_id'
                    and r['payload']['metadata']['request_selection'].get('asgi_request_sha256')==trial['request_id_sha256']]
                for row in rows:
                    pair=assignment(row) if row['event']=='NATIVE' else None
                    if pair is None or asgi_assignment(row)!=trial['request_id_sha256']:continue
                    aligned=[s for s in selected if s['payload']['metadata']['request_selection'].get('request_id_observed') is True
                        and s['payload']['metadata']['request_selection'].get('selected_request_id_sha256')==pair[1]
                        and s['payload']['metadata']['request_selection'].get('entry_method_sha256')==hashlib.sha256(b'POST').hexdigest()
                        and s['payload']['metadata']['request_selection'].get('entry_path_sha256')==hashlib.sha256(b'/v1/completions').hexdigest()]
                    if len(aligned)!=1:continue
                    meta=aligned[0]['payload']['metadata']['request_selection'];invocation=meta.get('asgi_invocation_sha256')
                    if not invocation:continue
                    matches.append({'case_id':trial['case_id'],'recording':index,'seq':row['seq'],'pid':ctx['pid'],
                        'start_ticks':ctx['start_ticks'],'native_request_sha256':pair[1],
                        'external_request_sha256':trial['request_id_sha256'],'asgi_invocation_sha256':invocation,
                        'entry_root_path_sha256':meta.get('entry_root_path_sha256'),'source_sha256':row['payload']['source_sha256'],
                        'image_digest_verified':capture.get('image_digest_verified') is True,
                        'physical_process_binding_verified':bool(capture.get('process_context_verified') and recording.get('session_owner_match')
                            and recording.get('listed_file_identity_match')),'producer_authenticated':False})
            if len(matches)==1:result['bindings'].append(matches[0])
            else:result['diagnostic_codes'].append('OBSERVATION_STATE_UNIQUE_SELECTION_UNAVAILABLE')
        for i,a in enumerate(result['bindings']):
            for b in result['bindings'][i+1:]:
                if (a['recording'],a['pid'],a['start_ticks'])==(b['recording'],b['pid'],b['start_ticks']) and (
                        a['native_request_sha256']==b['native_request_sha256'] or a['asgi_invocation_sha256']==b['asgi_invocation_sha256']):
                    result['conflict_candidates'].append({'kind':'distinct_requests_share_native_or_invocation',
                        'cases':[a['case_id'],b['case_id']],'confirmed_violation':False})
        for index,recording in enumerate(packet.get('recordings',[])):
            capture=recording.get('capture')
            if not capture:continue
            ctx=capture['records'][0]['payload']['context']
            local=[b for b in result['bindings'] if b['recording']==index]
            for row in capture['records']:
                if len(result['events'])+len(result['asgi_events'])>=MAX_EVENTS:
                    result['diagnostic_codes'].append('OBSERVATION_STATE_EVENT_BOUND_REACHED');break
                p=row['payload'];base={'recording':index,'seq':row['seq'],'pid':ctx['pid'],'start_ticks':ctx['start_ticks'],
                    'monotonic_ns':row['monotonic_ns'],'producer_authenticated':False,'worker_identity_verified':False}
                if row['event'] in {'ASGI','ATTEMPT'}:
                    tag=p.get('asgi_request_sha256',p.get('request_sha256'))
                    candidates=[b for b in local if b['external_request_sha256']==tag]
                    exact=[b for b in candidates if row['event']=='ATTEMPT' and b['asgi_invocation_sha256']==p['asgi_invocation_sha256']
                        and b['physical_process_binding_verified'] and p['context_live']]
                    if not candidates:continue
                    result['asgi_events'].append({**base,'phase':p['phase'],'case_id':exact[0]['case_id'] if len(exact)==1 else None,
                        'candidate_case_ids':[b['case_id'] for b in candidates],
                        'invocation_binding_observed':len(exact)==1,'event_kind':row['event'],
                        'client_receipt_verified':False,'source_sha256':None,
                        'observer_code_sha256':p.get('observer_code_sha256')})
                if row['event']!='NATIVE':continue
                point=p['point']
                component=('native_forward' if point=='GPUModelRunner._model_forward' else
                    'native_request_state' if point.startswith(('OutputProcessor.','AsyncLLM.','EngineCore.','Scheduler.','GPUModelRunner.','InputBatch.')) else None)
                if component is None:continue
                for state in p['requests']:
                    candidates=[b for b in result['bindings'] if b['native_request_sha256']==state['request_sha256']]
                    if not candidates:continue
                    if len(result['events'])+len(result['asgi_events'])>=MAX_EVENTS:
                        result['diagnostic_codes'].append('OBSERVATION_STATE_EVENT_BOUND_REACHED');break
                    exact=[b for b in candidates if b['recording']==index and b['physical_process_binding_verified']]
                    result['events'].append({**base,'point':point,'phase':p['phase'],'source_sha256':p['source_sha256'],
                        'component':component,'case_id':exact[0]['case_id'] if len(exact)==1 else None,
                        'candidate_case_ids':[b['case_id'] for b in candidates],'native_request_sha256':state['request_sha256'],
                        'request_present':state['request_present'],'output_state_present':state['output_state_present'],
                        'batch_row_present':state['batch_row_present'],'whole_scope_verified':False})
        # Reuse A1's strict /11 collector/object provenance projection with this
        # check's own case IDs. Its REQ12-specific aggregate flag is not consumed.
        project_outputs(packet,result)
    for component in sorted(COMPONENTS):
        if component=='http_listener':
            events=[t for t in trials if t['method']=='GET' and t['attempted']]
            bound=[t for t in events if t['capture_complete'] and t['binding_verified']]
        elif component=='request_selection':events=result['bindings'];bound=[b for b in events if b['physical_process_binding_verified']]
        elif component=='asgi_application':events=result['asgi_events'];bound=[e for e in events if e['invocation_binding_observed']]
        elif component=='output_collector':events=result['output_events'];bound=[e for e in events if e['complete_call_observed'] and e['caller_case_id']]
        else:events=[e for e in result['events'] if e['component']==component];bound=[e for e in events if e['case_id']]
        result['component_status'].append({'component':component,'required':component in required,'observed_events':len(events),
            'bound_events':len(bound),'state_truth_verified':False})
        if component in required and not bound:result['diagnostic_codes'].append('OBSERVATION_STATE_REQUIRED_COMPONENT_UNVERIFIED')
    result['diagnostic_codes']=sorted(set(result['diagnostic_codes']+['OBSERVATION_STATE_INDEPENDENT_WORKER_AND_SEMANTICS_UNVERIFIED']))
    return result
