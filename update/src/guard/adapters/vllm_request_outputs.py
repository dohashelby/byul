"""REQ12 collector/output projection from explicitly selected /11 recordings."""
from . import vllm_request_output_sensor as sensor

MAX_EVENTS=4096


def project_outputs(packet,result):
    for recording_index,recording in enumerate(packet.get('recordings',[])):
        capture=recording.get('capture')
        if not capture or capture['records'][0]['payload']['profile']!=sensor.PROFILE:continue
        rows=[r for r in capture['records'] if r['event']=='OUTPUT']
        if capture['summary'].get('output_incomplete_calls'):
            result['diagnostic_codes'].append('REQUEST_ATTEMPT_OUTPUT_CAPTURE_INCOMPLETE')
        calls={}
        for row in rows:calls.setdefault(row['payload']['call_sha256'],[]).append(row)
        completed={key for key,items in calls.items() if len(items)==2 and items[0]['payload']['phase']=='CALL'
            and items[1]['payload']['phase']=='RETURN' and items[0]['seq']<items[1]['seq']}
        bindings=[b for b in result['bindings'] if b['recording']==recording_index]
        additions=[];generated=[];stored=[]
        def native_matches(payload):
            return [b for b in bindings if b['native_request_sha256']==payload['native_request_sha256']]
        def caller_matches(payload):
            return [b for b in bindings if b['asgi_invocation_sha256']==payload['asgi_invocation_sha256']
                and b['external_request_sha256']==payload['asgi_request_sha256']]
        for row in rows:
            if len(result['output_events'])>=MAX_EVENTS:
                result['diagnostic_codes'].append('REQUEST_ATTEMPT_EVENT_BOUND_REACHED');return
            p=row['payload'];complete=p['call_sha256'] in completed
            callers=caller_matches(p)
            caller=callers[0] if len(callers)==1 and callers[0]['physical_process_binding_verified'] and p['asgi_context_live'] else None
            result['output_events'].append({'recording':recording_index,'seq':row['seq'],'monotonic_ns':row['monotonic_ns'],
                **p,'caller_case_id':caller['case_id'] if caller else None,'candidate_caller_case_ids':[b['case_id'] for b in callers],
                'complete_call_observed':complete,'producer_authenticated':False,'client_receipt_verified':False})
            if not complete or p['phase']!='RETURN':continue
            if p['point']=='OutputProcessor.add_request' and p['collector_sha256']:
                native=native_matches(p)
                if (len(native)==1 and caller is not None and caller['case_id']==native[0]['case_id']):
                    additions.append((row,native[0]))
            if p['point']=='RequestState.make_request_output' and p['output_object_sha256']:
                native=native_matches(p)
                linked=[a for a,b in additions if b in native and a['payload']['collector_sha256']==p['collector_sha256']]
                if len(native)==1 and len(linked)==1:generated.append((row,native[0]))
            if p['point']=='RequestOutputCollector.put' and p['stored_output_object_sha256']:
                origins=[(g,b) for g,b in generated if g['seq']<row['seq']
                    and g['payload']['collector_sha256']==p['collector_sha256']
                    and g['payload']['output_object_sha256']==p['provided_output_object_sha256']
                    and g['payload']['returned_text']==p['provided_text'] and p['provided_text']['outputs_complete']]
                unchanged=(p['stored_output_object_sha256']==p['provided_output_object_sha256'] and p['stored_text']==p['provided_text'])
                stored.append((row,origins,unchanged))
            if p['point'] not in {'RequestOutputCollector.get','RequestOutputCollector.get_nowait'} or not p['output_present']:continue
            candidates=[(s,origins,unchanged) for s,origins,unchanged in stored if s['seq']<row['seq']
                and s['payload']['collector_sha256']==p['collector_sha256']
                and s['payload']['stored_output_object_sha256']==p['output_object_sha256']
                and s['payload']['stored_text']==p['returned_text']]
            unique=(len(candidates)==1 and len(candidates[0][1])==1 and candidates[0][2]
                and caller is not None and capture['summary'].get('recording_closed') is True
                and not capture['summary'].get('output_incomplete_calls'))
            origin=candidates[0][1][0][1] if unique else None
            result['output_links'].append({'recording':recording_index,'get_seq':row['seq'],'collector_sha256':p['collector_sha256'],
                'output_object_sha256':p['output_object_sha256'],'returned_text':p['returned_text'],
                'caller_case_id':caller['case_id'] if caller else None,'origin_case_id':origin['case_id'] if origin else None,
                'origin_native_request_sha256':origin['native_request_sha256'] if origin else None,
                'put_seq':candidates[0][0]['seq'] if len(candidates)==1 else None,'candidate_put_count':len(candidates),
                'mutation_or_aggregation_observed':any(not c[2] for c in candidates),
                'origin_binding_observed':bool(unique),'cross_invocation_candidate':bool(origin and caller and origin['case_id']!=caller['case_id']),
                'confirmed_violation':False,'client_receipt_verified':False,'producer_authenticated':False})
        if any(not any(link['origin_case_id']==b['case_id'] for link in result['output_links']) for b in bindings):
            result['diagnostic_codes'].append('REQUEST_ATTEMPT_OUTPUT_LINK_UNAVAILABLE')
    result['native_output_collector_binding_observed']=all(any(link['origin_case_id']=='req12-'+case
        and link['origin_binding_observed'] for link in result['output_links']) for case in ('disconnect','replacement'))
