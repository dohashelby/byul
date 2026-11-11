"""Request-scoped native23 closure facts over an owned, parsed session packet.

Positive method/slot/fence receipts establish completion. Missing rows never
establish that work did not exist. A pending event/retained slot is a residual
fact; an incomplete recording only lowers the evidence coverage.
"""
from __future__ import annotations
import hashlib
from . import vllm_termination_sensor as sensor,vllm_termination_metadata as metadata
from .vllm_lifecycle_links import assignment,asgi_assignment
from .vllm_request_termination import SCOPES,clock,digest

MAX_EVENTS=4096
PATHS={hashlib.sha256(p).hexdigest()for p in (b'/v1/completions',b'/v1/chat/completions')}
POST=hashlib.sha256(b'POST').hexdigest()


def _settled_completion(observations):
    """First empty observation after the last retained or unknown state.

    Later empty samples confirm that interval; they do not move completion
    beyond a deadline. A reappearing state starts a new interval.
    """
    completion=None
    for stamp,active in sorted(observations,key=lambda row:row[0]):
        if active is False:
            if completion is None:completion=stamp
        else:completion=None
    return completion


def _result(required):
    return {'binding_observed':False,'anchor':None,'historical_import_only_recordings':[],'events':[],'deadline_conflict_candidates':[],
        'scope_status':[{'scope':s,'required':s in required,'observed_events':0,'termination_verified':False,'completion_elapsed_ns':None,'evidence_refs':[]}for s in sorted(SCOPES)],
        'diagnostic_codes':[],'whole_scope_complete':False,'worker_gpu_release_verified':False,
        'activation_steps':[],'activation_violations':[],'activation_missing':[],
        'activation_scope_complete':False,'requested_scope_complete':False,'scope':'owned native23 request state, body returns and request stream receipts'}


def retired_import_only(item,source_context,anchor_ns,expected_owner):
    """Exclude an already exited import-only process from this later request.

    The original recording remains in the packet. This supplies no physical
    worker completion proof and cannot exclude any request/native activity.
    """
    capture=item.get('capture')or{};rows=capture.get('records')or[]
    if len(rows)<2:return False
    context=rows[0].get('payload',{}).get('context')or{};summary=capture.get('summary')or{}
    recheck=capture.get('process_recheck')or{}
    identity={'pid':context.get('pid'),'start_ticks':context.get('start_ticks'),'recording_end':'PROCESS_EXIT','recording_closed':True,'result':'process_exited'}
    if(not all(item.get(k)is True for k in ('session_owner_match','listed_file_identity_match'))
        or capture.get('process_context_verified')is not False or capture.get('image_digest_verified')is not True
        or capture.get('provenance',{}).get('owner')!=expected_owner
        or not metadata.fingerprint(capture.get('provenance',{}).get('capture_sha256'))
        or recheck!=identity or rows[0].get('event')!='START'or rows[-1].get('event')!='END'
        or rows[-1].get('payload')!={'reason':'PROCESS_EXIT'}
        or summary.get('recording_closed')is not True or summary.get('recording_end')!='PROCESS_EXIT'
        or summary.get('incomplete_final_row')is not False or summary.get('discarded_tail_bytes')!=0
        or type(rows[-1].get('monotonic_ns'))is not int or not 0<=rows[-1]['monotonic_ns']<anchor_ns
        or clock(context)!=clock(source_context)or context.get('pid_namespace')!=source_context.get('pid_namespace')
        or type(context.get('uid'))is not int or not 0<=context['uid']<2**32
        or context.get('uid')!=source_context.get('uid')):return False
    from .vllm_lifecycle_profiles import source_hashes
    sources=source_hashes(sensor.PROFILE)
    checkpoint_rows=any(row.get('event')=='CHECKPOINT' for row in rows)
    if checkpoint_rows:
        from guard.executors.lifecycle_checkpoint import canonical,marker
        previous='0'*64;last_clock=-1
        for seq,row in enumerate(rows):
            if (type(row.get('seq'))is not int or row['seq']!=seq or row.get('previous_sha256')!=previous
                or type(row.get('monotonic_ns'))is not int or row['monotonic_ns']<last_clock):return False
            previous=hashlib.sha256(canonical(row)).hexdigest();last_clock=row['monotonic_ns']
        if hashlib.sha256(b''.join(canonical(row) for row in rows)).hexdigest()!=capture['provenance']['capture_sha256']:return False
    for row in rows[1:-1]:
        p=row.get('payload')or{}
        if row.get('event')=='CHECKPOINT':
            if not marker(row,rows[0]['payload'].get('session')):return False
            continue
        if(row.get('event')!='MODULE'or p.get('verified')is not True or p.get('module')not in sources
            or p.get('source_sha256')!=sources[p['module']]or not metadata.fingerprint(p.get('code_sha256'))):return False
    return True


def project_native(packet,request_sha256,max_delay_ms,required_scopes):
    result=_result(required_scopes if type(required_scopes)is list else [])
    try:return _project(packet,request_sha256,max_delay_ms,required_scopes,result)
    except (KeyError,TypeError,ValueError,IndexError,AttributeError):
        # Reject the entire derivation if a malformed caller-supplied packet is
        # encountered; never keep an earlier positive scope from that packet.
        result=_result(required_scopes if type(required_scopes)is list else [])
        result['diagnostic_codes']=['TERMINATION_NATIVE23_PACKET_INVALID'];result['activation_missing']=['native23_packet'];return result


def project_checkpoint(packet, request_sha256, max_delay_ms, required_scopes):
    """Use observed live checkpoint coverage with the original positive predicates."""
    result = _result(required_scopes if type(required_scopes) is list else [])
    result.update(closure_basis='live_checkpoint', checkpoint_coverage_verified=False)
    try:
        return _project(packet, request_sha256, max_delay_ms, required_scopes, result, checkpoint=True)
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        result = _result(required_scopes if type(required_scopes) is list else [])
        result.update(closure_basis='live_checkpoint', checkpoint_coverage_verified=False)
        result['diagnostic_codes'] = ['TERMINATION_CHECKPOINT_PACKET_INVALID']
        return result


def _project(packet,external,limit_ms,required,result,checkpoint=False):
    if(not metadata.fingerprint(external)or type(limit_ms)is not int or not 0<=limit_ms<=300000
        or type(required)is not list or not required or len(set(required))!=len(required)or any(s not in SCOPES for s in required)):
        raise ValueError
    if type(packet)is not dict or type(packet.get('recordings'))is not list or not 0<len(packet['recordings'])<=(96 if checkpoint and packet.get('capture_mode')=='live_checkpoint_rolling' else 32):raise ValueError
    if not isinstance(packet.get('session'),str)or len(packet['session'])!=32:raise ValueError
    before=packet.get('before',{});after=packet.get('after',{});expected_owner=before.get('owner')
    owner_bound=bool(expected_owner and expected_owner==after.get('owner')and before.get('owner_stable')is True and after.get('owner_stable')is True and before.get('metadata')==after.get('metadata')and before.get('metadata',{}).get('sealed')is True)
    if checkpoint:
        from guard.executors.lifecycle_checkpoint import covered, recording_covered
        owner_bound = covered(packet)
    rolling=checkpoint and packet.get('capture_mode')=='live_checkpoint_rolling'
    def process_key(index):
        if not rolling:return index
        ctx=packet['recordings'][index]['capture']['records'][0]['payload']['context']
        return (ctx['pid'],ctx['start_ticks'])
    def same_process(left,right):return process_key(left)==process_key(right)
    header_identity=None
    recording_coverage=[];records=[];coverage=owner_bound and packet.get('recorded_files_covered')is True and packet.get('live_census_stable')is True and packet.get('request_index',{}).get('stored_record_index_complete')is True
    if checkpoint:
        result['source_stored_record_index_complete']=packet.get('request_index',{}).get('stored_record_index_complete') is True
        result['request_window_index_complete']=False
        coverage=owner_bound and packet.get('recorded_files_covered') is True and packet.get('live_census_stable') is True
    coverage=coverage and not packet.get('python_processes_without_recording',[])
    window_start = None
    if checkpoint:
        candidates = []
        for item in packet['recordings']:
            value=item.get('capture') or {}
            for row in value.get('records',[]) + value.get('checkpoint_tail',[]):
                if row.get('event')=='SELECTION' and row.get('payload',{}).get('metadata',{}).get('request_selection',{}).get('asgi_request_sha256')==external:
                    candidates.append(row['monotonic_ns'])
        if candidates:window_start=min(candidates)
    for index,item in enumerate(packet['recordings']):
        capture=item.get('capture')
        if not capture:coverage=False;continue
        rows=capture['records']
        if checkpoint:rows=rows+capture.get('checkpoint_tail',[])
        header=rows[0]['payload'];context=header['context']
        if header.get('profile')!=sensor.PROFILE or header.get('session')!=packet['session']:raise ValueError
        current_identity=(header.get('sensor_sha256'),header.get('settings_sha256'))
        if not all(metadata.fingerprint(v)for v in current_identity)or header_identity is not None and header_identity!=current_identity:raise ValueError
        header_identity=current_identity
        physical=all(item.get(k)is True for k in ('session_owner_match','listed_file_identity_match'))and capture.get('process_context_verified')is True and capture.get('image_digest_verified')is True and capture.get('provenance',{}).get('owner')==expected_owner
        closed=(capture.get('summary',{}).get('recording_closed')is True and capture.get('summary',{}).get('recording_end')=='SEALED'and rows[-1]['event']=='END'and rows[-1]['payload']=={'reason':'SEALED'})
        if checkpoint:
            closed = recording_covered(item, packet['session'], 0, packet=packet)
        recording_coverage.append((index,physical,closed))
        for row in rows:
            if checkpoint and window_start is not None and row['event']!='START' and row['monotonic_ns'] < window_start:
                # Assignment immediately precedes selection; retain its exact row.
                if not (row['event']=='NATIVE' and asgi_assignment(row)==external):continue
            if len(records)>=MAX_EVENTS:raise ValueError
            if row['event']=='TERMINATION':metadata.validate_sample(row['payload'])
            records.append((index,context,physical,row))
    # Selection RETURN has a live HTTP invocation; a repeated header is not a
    # unique request, and native suffix guessing is never used.
    selections=[]
    for index,context,physical,row in records:
        p=row['payload']
        if row['event']!='SELECTION'or p.get('point')!='InputProcessor.assign_request_id'or p.get('phase')!='RETURN':continue
        s=p['metadata'].get('request_selection',{})
        if(s.get('asgi_request_sha256')==external and s.get('request_id_observed')is True and metadata.fingerprint(s.get('selected_request_id_sha256'))and metadata.fingerprint(s.get('asgi_invocation_sha256'))and s.get('entry_path_sha256')in PATHS and s.get('entry_method_sha256')==POST):
            native=s['selected_request_id_sha256'];invocation=s['asgi_invocation_sha256']
            assignments=[r for i,_,_,r in records if same_process(i,index) and assignment(r)is not None and assignment(r)[1]==native and asgi_assignment(r)==external]
            disconnects=[r for i,_,_,r in records if same_process(i,index) and r['event']=='TERMINATION'and r['payload']['point']=='ASGI.request'and r['payload']['phase']=='ASGI_DISCONNECT_RECEIVED'and r['payload']['asgi_request_sha256']==external and r['payload']['asgi_invocation_sha256']==invocation and r['payload']['asgi_path_sha256']==s['entry_path_sha256']and r['monotonic_ns']>=row['monotonic_ns']]
            if len(assignments)==1 and len(disconnects)==1:
                anchor_index=next(i for i,_,_,r in records if r is disconnects[0])
                selections.append((anchor_index,context,physical,native,invocation,disconnects[0],row['monotonic_ns']))
    if len(selections)!=1:
        result['diagnostic_codes'].append('TERMINATION_UNIQUE_NATIVE_DISCONNECT_BINDING_UNAVAILABLE');result['activation_missing'].append('unique_request_invocation');return result
    source_index,source_context,source_physical,native,invocation,anchor,assignment_ns=selections[0];start=anchor['monotonic_ns'];deadline=start+limit_ms*1_000_000
    if checkpoint:
        # Reindex only this exact request, over original retained rows. Global
        # older-request index exhaustion stays visible and never becomes True.
        from guard.executors.lifecycle_session import request_index
        from .vllm_lifecycle_links import fingerprints
        scoped=[];target_links=[];scoped_count=0;latest=window_start
        for index,item in enumerate(packet['recordings']):
            capture=item['capture'];original=capture['records']+capture.get('checkpoint_tail',[])
            selected=[original[0]]
            for row in original[1:]:
                pair=assignment(row);asgi=asgi_assignment(row)
                if pair is not None and (asgi==external or pair[1]==native):
                    target_links.append((index,row['seq'],pair,asgi))
                    if len(target_links)>MAX_EVENTS:raise ValueError
                if (window_start is not None and row['monotonic_ns']>=window_start
                        and ({external,native}&set(fingerprints(row))) or pair is not None and (asgi==external or pair[1]==native)):
                    selected.append(row);scoped_count+=1
                    if scoped_count>MAX_EVENTS:raise ValueError
                    latest=max(latest or 0,row['monotonic_ns'])
            scoped.append({'capture':{**capture,'records':selected}})
        indexed=request_index(scoped)
        unique=(len(target_links)==1 and target_links[0][2][1]==native and target_links[0][3]==external)
        complete=bool(window_start is not None and unique and indexed['stored_record_index_complete'] is True)
        result['request_window_index_complete']=complete
        result['request_window']={'selection_start_ns':window_start,'last_indexed_event_ns':latest,
            'indexed_events':scoped_count,'max_events':MAX_EVENTS,
            'assignment_refs':[{'recording':i,'seq':seq}for i,seq,_,_ in target_links[:MAX_EVENTS]],
            'unique_assignment_verified':unique,'scope':'exact request fingerprints; original retained rows and refs'}
        coverage=coverage and complete
    excluded={index for index,physical,closed in recording_coverage if not(physical and closed)
        and retired_import_only(packet['recordings'][index],source_context,assignment_ns,expected_owner)}
    result['historical_import_only_recordings']=[{'recording':index,
        'pid':packet['recordings'][index]['capture']['records'][0]['payload']['context']['pid'],
        'start_ticks':packet['recordings'][index]['capture']['records'][0]['payload']['context']['start_ticks'],
        'capture_sha256':packet['recordings'][index]['capture']['provenance']['capture_sha256'],
        'reason':'closed_import_only_before_native_assignment'}for index in sorted(excluded)]
    coverage=coverage and all(physical and closed or index in excluded for index,physical,closed in recording_coverage)
    if checkpoint:
        coverage = coverage and all(index in excluded or recording_covered(item, packet['session'], deadline, source_context, packet=packet)
                                    for index, item in enumerate(packet['recordings']))
    result['binding_observed']=True
    result['anchor']={'recording':source_index,'seq':anchor['seq'],'pid':source_context['pid'],'start_ticks':source_context['start_ticks'],'native_request_sha256':native,'asgi_request_sha256':external,'asgi_invocation_sha256':invocation,'monotonic_ns':start,'clock_domain_sha256':digest(clock(source_context)),'physical_process_binding_verified':source_physical,'producer_authenticated':False}
    scope_rows={r['scope']:r for r in result['scope_status']};facts={s:[]for s in SCOPES};worker=[];operations={};outputs={};terms=[];gaps=False;kv_free=[];deferred_empty=[]
    for index,context,physical,row in records:
        p=row['payload'];event=row['event'];same=physical and source_physical and clock(context)==clock(source_context)and context['pid_namespace']==source_context['pid_namespace']
        if event=='TERMINATION'and p['phase']=='OBSERVER_GAP':gaps=True
        if event=='TERMINATION'and native in p['request_sha256']:
            terms.append((index,context,same,row))
            if not same:coverage=False
        if not same:continue
        phase=p.get('phase');point=p.get('point');stamp=row['monotonic_ns'];scope=None;active=None
        if event=='TERMINATION'and point=='ASGI.request'and same_process(index,source_index) and p['asgi_invocation_sha256']==invocation and p['asgi_request_sha256']==external:
            if phase in {'ASGI_APP_RETURN','ASGI_APP_RAISE'}:scope='asgi_transport';active=False
        elif event=='NATIVE':
            selected=[s for s in p.get('requests',[])if s['request_sha256']==native]
            if point in {'Scheduler._drain_deferred_frees','Scheduler._free_request_blocks','Scheduler.finish_requests'}and phase=='RETURN'and p.get('metadata',{}).get('deferred_complete')is True and p['metadata'].get('deferred_fences')==[]:deferred_empty.append((index,stamp,row))
            if len(selected)!=1:continue
            state=selected[0]
            if phase!='RETURN':continue
            if point in {'OutputProcessor.abort_requests','OutputProcessor._finish_request'}:scope='frontend_request';active=state.get('output_state_present')
            elif point=='EngineCore.abort_requests':scope='engine_core';active=state.get('request_present')
            elif point=='Scheduler.finish_requests':scope='scheduler_state';active=state.get('request_present')
            elif point in {'KVCacheManager.free','KVCacheManager.pop_blocks_for_free'}:
                own=p.get('metadata',{}).get('ownership',{});groups=[g for g in own.get('groups',[])if g['request_sha256']==native]
                if own.get('complete')is True and groups and all(metadata.fingerprint(g.get('pool_sha256'))for g in groups):
                    active=any(g['block_sha256']for g in groups);scope='kv_references'
                    if active is False:kv_free.append((index,stamp,row,{g['pool_sha256']for g in groups}))
        if scope is not None and stamp>=start:
            fact={'recording':index,'seq':row['seq'],'pid':context['pid'],'start_ticks':context['start_ticks'],'scope':scope,'point':point,'phase':phase,'native_request_sha256':native,'monotonic_ns':stamp,'declared_elapsed_ns':stamp-start,'same_clock_domain_verified':True,'active_state_observed':active,'observation_lower_ns':None,'image_digest_verified':True,'producer_authenticated':False}
            result['events'].append(fact);facts[scope].append((stamp,active,fact))
    for index,context,same,row in terms:
        if not same:continue
        p=row['payload'];point=p['point'];phase=p['phase'];stamp=row['monotonic_ns'];key=(process_key(index),p['call_sha256'])
        if point in {'GPUModelRunner.execute_model','GPUModelRunner.sample_tokens'}and native in p['gpu_work_selected_sha256']:
            state=operations.setdefault(key,{'calls':[],'ends':[],'fences':{}})
            if phase=='CALL':state['calls'].append(row)
            elif phase in {'RETURN','RAISE'}:state['ends'].append(row)
            elif phase.startswith('FENCE_'):state['fences'].setdefault(p['stream_kind'],[]).append(row)
        if point.startswith('AsyncOutput.')and p['output_token']is not None:
            outputs.setdefault((process_key(index),p['output_token']),[]).append(row)
        if p['phase'].startswith('FENCE_')or point.startswith('AsyncOutput.')and p['output_token']is not None:
            fact={'recording':index,'seq':row['seq'],'pid':context['pid'],'start_ticks':context['start_ticks'],'scope':'gpu_transfers','point':point,'phase':phase,'native_request_sha256':native,'monotonic_ns':stamp,'declared_elapsed_ns':stamp-start,'same_clock_domain_verified':True,'active_state_observed':None,'observation_lower_ns':p['operation_started_ns'],'image_digest_verified':True,'producer_authenticated':False}
            result['events'].append(fact);facts['gpu_transfers'].append((stamp,None,fact))
        states=[s for s in p['states']if s['request_sha256']==native]
        if point.startswith(('GPUModelRunner.','RequestState.'))and phase in {'RETURN','STATE_SNAPSHOT'}and stamp>=start:
            active=states[0]['worker_slot_present']if len(states)==1 and states[0]['worker_mapping_complete']is True else None
            worker.append((process_key(index),stamp,active,row));fact={'recording':index,'seq':row['seq'],'pid':context['pid'],'start_ticks':context['start_ticks'],'scope':'worker_batch','point':point,'phase':phase,'native_request_sha256':native,'monotonic_ns':stamp,'declared_elapsed_ns':stamp-start,'same_clock_domain_verified':True,'active_state_observed':active,'observation_lower_ns':p['operation_started_ns'],'image_digest_verified':True,'producer_authenticated':False};result['events'].append(fact);facts['worker_batch'].append((stamp,active,fact))
    # Worker completion requires a positive empty slot in every process that
    # held this request. No event from a different worker substitutes for it.
    workers={i for i,_,_,_ in worker}|{i for i,_ in operations}
    worker_done=[]
    for index in workers:
        relevant=[r for r in worker if r[0]==index]
        done=_settled_completion([(stamp,active)for _,stamp,active,_ in relevant])
        if done is None:worker_done=[];break
        worker_done.append(done)
    gpu_done=[];gpu_complete=bool(operations)
    for state in operations.values():
        if len(state['calls'])!=1 or len(state['ends'])!=1 or state['ends'][0]['payload']['phase']!='RETURN'or state['ends'][0]['monotonic_ns']<state['calls'][0]['monotonic_ns']:gpu_complete=False;continue
        for kind in ('main','copy'):
            receipts=state['fences'].get(kind,[]);recorded=[r for r in receipts if r['payload']['phase']=='FENCE_RECORDED'];finished=[r for r in receipts if r['payload']['phase']=='FENCE_COMPLETE']
            if(len(recorded)!=1 or len(finished)!=1 or any(r['payload']['phase']in {'FENCE_ERROR','FENCE_PENDING','FENCE_TIMEOUT'}for r in receipts)
                or not all(r['payload']['stream_set_complete']is True and r['payload']['caller_stream_bound']is True for r in (*recorded,*finished))
                or recorded[0]['payload']['stream_sha256']!=finished[0]['payload']['stream_sha256']
                or not state['ends'][0]['monotonic_ns']<=recorded[0]['monotonic_ns']<=finished[0]['monotonic_ns']):gpu_complete=False;continue
            gpu_done.append(finished[0]['monotonic_ns'])
    if not outputs:gpu_complete=False
    for rows in outputs.values():
        created=[r for r in rows if r['payload']['phase']=='OUTPUT_CREATED'];released=[r for r in rows if r['payload']['phase']=='OUTPUT_RELEASED'];returned=[r for r in rows if r['payload']['point']=='AsyncOutput.get_output'and r['payload']['phase']=='RETURN'];calls=[r for r in rows if r['payload']['point']=='AsyncOutput.get_output'and r['payload']['phase']=='CALL']
        if len(created)!=1 or len(released)!=1 or len(returned)!=1 or len(calls)!=1 or returned[0]['payload']['call_sha256']!=calls[0]['payload']['call_sha256']or not created[0]['monotonic_ns']<=calls[0]['monotonic_ns']<=returned[0]['monotonic_ns']<=released[0]['monotonic_ns']:gpu_complete=False
        else:gpu_done.extend((released[0]['monotonic_ns'],returned[0]['monotonic_ns']))
    # A copied-away KV map alone is insufficient: require a positive empty
    # deferred queue from the same Scheduler process after its release.
    kv_done=[]
    for index,stamp,row,pools in kv_free:
        candidates=[]
        for i,t,other in deferred_empty:
            own=other['payload'].get('metadata',{}).get('ownership',{});groups=[g for g in own.get('groups',[])if g['request_sha256']==native]
            if same_process(i,index) and t>=stamp and own.get('complete')is True and groups and {g.get('pool_sha256')for g in groups}==pools and all(g.get('block_sha256')==[]for g in groups):candidates.append(t)
        if candidates:kv_done.append(min(candidates))
    completion={}
    for scope in ('asgi_transport','frontend_request','engine_core','scheduler_state'):
        done=_settled_completion([(stamp,active)for stamp,active,_ in facts[scope]])
        if done is not None:completion[scope]=done
    if worker_done:completion['worker_batch']=max(worker_done)
    kv_last=sorted(facts['kv_references'],key=lambda v:v[0])
    if kv_done and kv_last and kv_last[-1][1]is False:completion['kv_references']=max(kv_done)
    if gpu_complete and gpu_done:completion['gpu_transfers']=max(gpu_done)
    coverage=bool(coverage and not gaps)
    if checkpoint:
        result['checkpoint_coverage_verified']=coverage
        if rolling:
            result['rolling_segments_verified']=coverage
            result['rolling_history_evicted']=packet.get('rolling_history_evicted') is True
    for scope,row in scope_rows.items():
        row['observed_events']=len(facts[scope]);stamp=completion.get(scope)
        row['termination_verified']=coverage and stamp is not None and stamp<=deadline
        row['completion_elapsed_ns']=None if stamp is None else stamp-start
        row['evidence_refs']=[{'recording':v[2]['recording'],'seq':v[2]['seq']}for v in facts[scope]if v[1]is False or scope=='gpu_transfers']
        if scope in SCOPES and not row['termination_verified']:result['activation_missing'].append(scope)
        for stamp,active,fact in facts[scope]:
            if active is True and stamp>deadline and scope in SCOPES:
                confirmed=bool(coverage and fact.get('observation_lower_ns')is not None and fact['observation_lower_ns']>deadline)
                candidate={**fact,'configured_deadline_ms':limit_ms,'confirmed_violation':confirmed};result['deadline_conflict_candidates'].append(candidate)
                if confirmed:result['activation_violations'].append({'code':'REQUEST_STATE_RETAINED_AFTER_DEADLINE','scope':scope,'recording':fact['recording'],'seq':fact['seq'],'elapsed_ns':stamp-start})
    for index,context,same,row in terms:
        p=row['payload']
        if same and p['phase']=='FENCE_PENDING'and p['stream_set_complete']and row['monotonic_ns']>deadline and 'gpu_transfers'in SCOPES:
            confirmed=bool(coverage and p['operation_started_ns']>deadline)
            candidate={'scope':'gpu_transfers','recording':index,'seq':row['seq'],'configured_deadline_ms':limit_ms,'confirmed_violation':confirmed};result['deadline_conflict_candidates'].append(candidate)
            if confirmed:result['activation_violations'].append({'code':'REQUEST_STREAM_PENDING_AFTER_DEADLINE',**candidate})
    if not coverage:result['diagnostic_codes'].append('TERMINATION_NATIVE23_COVERAGE_UNVERIFIED')
    if gaps:result['diagnostic_codes'].append('TERMINATION_OBSERVER_GAP')
    result['requested_scope_complete']=bool(coverage and all(scope_rows[s]['termination_verified']for s in required))
    result['whole_scope_complete']=bool(coverage and all(scope_rows[s]['termination_verified']for s in SCOPES))
    result['worker_gpu_release_verified']=bool(all(scope_rows[s]['termination_verified']for s in ('scheduler_state','worker_batch','kv_references','gpu_transfers')))
    if result['activation_violations']:result['whole_scope_complete']=False
    verified_failures={v['scope']for v in result['activation_violations']}
    for scope,row in scope_rows.items():row['evidence_complete']=bool(row['termination_verified']or scope in verified_failures)
    result['activation_missing']=[s for s in sorted(SCOPES)if not scope_rows[s]['evidence_complete']]
    result['activation_scope_complete']=bool(coverage and all(scope_rows[s]['evidence_complete']for s in SCOPES));result['activation_steps']=[{'scope':s,**scope_rows[s]}for s in sorted(SCOPES)]
    return result
