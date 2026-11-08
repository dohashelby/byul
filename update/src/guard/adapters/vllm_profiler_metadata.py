"""Closed native15 profiler facts; wrapper flags never imply backend truth."""
import re
from . import vllm_profiler_sensor as sensor

STATE_FIELDS=frozenset({'owner_kind','wrapper_sha256','backend_kind','rank','local_rank','active','running','active_iteration_count','profiling_for_iters'})
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_verified','observer_runtime','observer_code_sha256',
 'call_sha256','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start',
 'state_before','state_after','api_status_code','exception_kind','operation_started_ns','operation_finished_ns',
 'backend_collection_stopped_verified','trace_output_completed_verified','cross_process_rpc_verified','producer_authenticated'})
BACKEND_FIELDS=frozenset({'backend_before','backend_after','backend_generation_sha256','backend_probe_qualified','trace_before','trace_after'})
CAPTURE_KEYS=BACKEND_FIELDS|{'value','collected','backend_sha256','thread_id','status','directory_identity',
    'collision_sha256','files','name_sha256','kind','sha256','bytes','identity','trace_events'}
CAPTURE_WORDS={'unavailable','owned-collision','directory','trace','table',sensor.LEGACY_EVENT_SCHEMA,
    *sensor.LEGACY_RUNTIME_CODE_CONTRACTS}


def validate_backend(value):
    if type(value) is not dict or set(value)!={'value','collected','backend_sha256','thread_id'}:raise ValueError
    if type(value['collected']) is not bool or value['collected']!=(type(value['value']) is bool):raise ValueError
    if value['backend_sha256'] is not None and not fingerprint(value['backend_sha256']):raise ValueError
    if value['thread_id'] is not None and (type(value['thread_id']) is not int or not 0<value['thread_id']<2**63):raise ValueError
    if value['collected'] and value['thread_id'] is None:raise ValueError


def validate_inventory(value):
    if type(value) is not dict or set(value)!={'status','directory_identity','collision_sha256','files'}:raise ValueError
    if value['status'] not in {'unavailable','owned-collision','directory'} or type(value['files']) is not list or len(value['files'])>16:raise ValueError
    if value['status']=='owned-collision':
        if not fingerprint(value['collision_sha256']) or value['files'] or value['directory_identity'] is not None:raise ValueError
    elif value['collision_sha256'] is not None:raise ValueError
    if value['status']=='directory':
        if type(value['directory_identity']) is not list or len(value['directory_identity'])!=4 or any(type(v) is not int or v<0 for v in value['directory_identity']):raise ValueError
    elif value['directory_identity'] is not None or value['files']:raise ValueError
    total=0;names=set()
    for row in value['files']:
        if type(row) is not dict or set(row)!={'name_sha256','kind','sha256','bytes','identity','trace_events'}:raise ValueError
        if not fingerprint(row['name_sha256']) or row['name_sha256'] in names or not fingerprint(row['sha256']) or row['kind'] not in {'trace','table'}:raise ValueError
        names.add(row['name_sha256'])
        if type(row['bytes']) is not int or row['bytes']<=0:raise ValueError
        total+=row['bytes']
        if type(row['identity']) is not list or len(row['identity'])!=7 or any(type(v) is not int or v<0 for v in row['identity']) or row['identity'][4]!=row['bytes']:raise ValueError
        if row['kind']=='trace':
            if type(row['trace_events']) is not int or row['trace_events']<=0:raise ValueError
        elif row['trace_events'] is not None:raise ValueError
    if total>8*1024**2:raise ValueError


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_state(value):
    if type(value) is not dict or set(value)!=STATE_FIELDS:raise ValueError('PROFILER_STATE_INVALID')
    if value['owner_kind'] not in {None,'worker','torch-wrapper'} or value['backend_kind'] not in {None,'torch','cuda','proton','other'}:raise ValueError('PROFILER_STATE_INVALID')
    if value['wrapper_sha256'] is not None and not fingerprint(value['wrapper_sha256']):raise ValueError('PROFILER_STATE_INVALID')
    for key in ('active','running'):
        if value[key] is not None and type(value[key]) is not bool:raise ValueError('PROFILER_STATE_INVALID')
    for key in ('rank','local_rank','active_iteration_count','profiling_for_iters'):
        if value[key] is not None and (type(value[key]) is not int or not 0<=value[key]<(64 if key in {'rank','local_rank'} else 2**63)):raise ValueError('PROFILER_STATE_INVALID')


def validate_sample(value):
    try:
        if type(value) is not dict:raise ValueError
        legacy=value.get('schema')==sensor.LEGACY_EVENT_SCHEMA
        if set(value)!=(FIELDS if legacy else FIELDS|BACKEND_FIELDS) or value['schema'] not in {sensor.EVENT_SCHEMA,sensor.LEGACY_EVENT_SCHEMA} or value['point'] not in sensor.POINTS:raise ValueError
        if value['phase'] not in {'CALL','RETURN','RAISE'} or value['source_module']!=sensor.POINT_SOURCES[value['point']] or value['source_sha256']!=sensor.SOURCE_ADDITIONS[value['source_module']]:raise ValueError
        if type(value['source_verified']) is not bool or type(value['registered_handler_observed']) is not bool:raise ValueError
        contracts=sensor.LEGACY_RUNTIME_CODE_CONTRACTS if legacy else sensor.RUNTIME_CODE_CONTRACTS
        if value['observer_runtime'] not in contracts or value['observer_code_sha256']!=contracts[value['observer_runtime']]:raise ValueError
        if not fingerprint(value['call_sha256']):raise ValueError
        for key in ('parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256'):
            if value[key] is not None and not fingerprint(value[key]):raise ValueError
        if (value['asgi_request_sha256'] is None)!=(value['asgi_invocation_sha256'] is None):raise ValueError
        if value['registered_handler_observed']!=(value['point'] in sensor.API_POINTS):raise ValueError
        if value['is_start'] is not None and (value['point']!='Worker.profile' or type(value['is_start']) is not bool):raise ValueError
        validate_state(value['state_before'])
        if value['phase']=='CALL':
            if any(value[key] is not None for key in ('state_after','api_status_code','exception_kind','operation_started_ns','operation_finished_ns')):raise ValueError
        else:
            validate_state(value['state_after'])
            for key in ('operation_started_ns','operation_finished_ns'):
                if type(value[key]) is not int or not 0<=value[key]<2**63:raise ValueError
            if value['operation_started_ns']>value['operation_finished_ns']:raise ValueError
            if value['exception_kind'] not in ({'exception','base-exception'} if value['phase']=='RAISE' else {None}):raise ValueError
        if value['api_status_code'] is not None and (value['phase']!='RETURN' or value['point'] not in sensor.API_POINTS or type(value['api_status_code']) is not int or not 100<=value['api_status_code']<=599):raise ValueError
        if any(value[key] is not False for key in ('backend_collection_stopped_verified','trace_output_completed_verified','cross_process_rpc_verified','producer_authenticated')):raise ValueError
        if not legacy:
            observed=value['point'] in {'TorchProfilerWrapper._start','TorchProfilerWrapper._stop'}
            if type(value['backend_probe_qualified']) is not bool:raise ValueError
            if value['backend_generation_sha256'] is not None and not fingerprint(value['backend_generation_sha256']):raise ValueError
            for side in ('before','after'):
                expected=observed and (side=='before' or value['phase']!='CALL')
                if expected:validate_backend(value['backend_'+side]);validate_inventory(value['trace_'+side])
                elif value['backend_'+side] is not None or value['trace_'+side] is not None:raise ValueError
            if value['backend_probe_qualified'] and (not observed or value['phase']=='CALL' or not fingerprint(value['backend_generation_sha256'])):raise ValueError
            if value['backend_probe_qualified']:
                before,after=value['backend_before'],value['backend_after']
                if (not before['collected'] or not after['collected'] or before['thread_id']!=after['thread_id']
                        or not fingerprint(after['backend_sha256'])):raise ValueError
                if value['point'].endswith('._start'):
                    if before['value'] is not False or after['value'] is not True:raise ValueError
                elif before['value'] is not True or before['backend_sha256']!=after['backend_sha256']:raise ValueError
    except (TypeError,KeyError,ValueError):raise ValueError('PROFILER_SAMPLE_INVALID') from None


def project(packet,required_ranks=()):
    samples=[];calls={};missing=[];candidates=[];binding_failures={}
    for number,recording in enumerate([] if not packet else packet.get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records:continue
        context=records[0]['payload']['context']
        bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        for row in records:
            if row['event']!='PROFILER':continue
            payload=row['payload'];validate_sample(payload)
            sample={'recording':number,'seq':row['seq'],'monotonic_ns':row['monotonic_ns'],'pid':context['pid'],'start_ticks':context['start_ticks'],
                'physical_process_binding_verified':bound,'image_digest_verified':capture.get('image_digest_verified') is True,
                'recording_closed':capture.get('summary',{}).get('recording_closed') is True,**payload}
            samples.append(sample);calls.setdefault((number,payload['call_sha256']),[]).append(sample)
    complete={}
    for key,rows in calls.items():
        starts=[r for r in rows if r['phase']=='CALL'];ends=[r for r in rows if r['phase'] in {'RETURN','RAISE'}]
        if len(starts)!=1 or len(ends)!=1:
            missing.append('profiler_call_pair_unavailable');continue
        first,last=starts[0],ends[0]
        fields=('schema','point','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start','state_before')
        if first['schema']==sensor.EVENT_SCHEMA:
            fields+=('schema','backend_before','trace_before')
        # Same gap code as before; the separate counts only say which condition was missing.
        reasons=[name for name,failed in (('call_fields_changed',any(first[k]!=last[k] for k in fields)),
            ('call_order_invalid',first['seq']>=last['seq']),('source_unverified',not first['source_verified'] or not last['source_verified']),
            ('physical_process_binding_unverified',not first['physical_process_binding_verified'] or not last['physical_process_binding_verified']),
            ('recording_not_closed',not last['recording_closed'])) if failed]
        if reasons:
            missing.append('profiler_source_process_call_binding_unavailable')
            for reason in reasons:binding_failures[reason]=binding_failures.get(reason,0)+1
            continue
        complete[key]=(first,last)
    worker_calls=[{'recording':key[0],'call_sha256':key[1],'phase':last['phase'],'rank':last['state_after']['rank'],
        'backend_kind':last['state_after']['backend_kind'],'wrapper_sha256':last['state_after']['wrapper_sha256'],
        'is_start':last['is_start'],'operation_started_ns':last['operation_started_ns'],'operation_finished_ns':last['operation_finished_ns']}
        for key,(first,last) in complete.items() if last['point']=='Worker.profile']
    for key,(first,last) in complete.items():
        if last['point']!='WorkerProfiler._call_stop' or last['phase']!='RETURN':continue
        selected=last['state_after']['wrapper_sha256']
        children=[end for (recording,call),(begin,end) in complete.items() if recording==key[0] and end['parent_call_sha256']==key[1]
            and end['point']=='TorchProfilerWrapper._stop' and end['state_before']['wrapper_sha256']==selected and selected is not None
            and last['operation_started_ns']<=end['operation_started_ns']<=end['operation_finished_ns']<=last['operation_finished_ns']]
        if len(children)!=1:
            missing.append('selected_profiler_stop_adapter_pair_unavailable');continue
        adapter=children[0]
        candidates.append({'recording':key[0],'wrapper_sha256':selected,'call_sha256':key[1],
            'wrapper_event_ref':[key[0],last['seq']],'adapter_event_ref':[key[0],adapter['seq']],
            'adapter_stop_raised':adapter['phase']=='RAISE','wrapper_running_false':last['state_after']['running'] is False,
            'flag_adapter_conflict_observed':adapter['phase']=='RAISE' and last['state_after']['running'] is False,
            'actual_backend_stop_failed_verified':False,'actual_collection_continued_verified':False,'trace_output_completed_verified':False,
            'cross_process_API_worker_link_verified':False,'confirmed_full_state_violation':False})
    cycles=backend_cycles(complete)
    for candidate in candidates:
        related=[c for c in cycles if c['stop_event_ref']==candidate['adapter_event_ref']]
        if len(related)==1:
            cycle=related[0]
            candidate.update(actual_backend_stop_failed_verified=cycle['collection_continued_verified'],
                actual_collection_continued_verified=cycle['collection_continued_verified'],
                trace_output_completed_verified=cycle['trace_output_completed_verified'])
    rank_status=[]
    for rank in required_ranks:
        selected=[c for c in worker_calls if c['rank']==int(rank) and c['backend_kind']=='torch' and c['wrapper_sha256'] is not None]
        rank_status.append({'rank':int(rank),'source_bound_worker_calls':len(selected),
            'observed_wrapper_ids':sorted({c['wrapper_sha256'] for c in selected}),
            'actual_backend_collection_truth_verified':False,'cross_process_rank_membership_verified':False,'scope_complete':False})
        if not selected:missing.append('required_profiler_worker_rank_unavailable')
    if not samples:missing.append('selected_native_profiler_facts_unavailable')
    missing.extend(['independent_backend_activity_and_trace_callback_output_transfer_unverified','cross_process_API_RPC_worker_causality_unverified',
        'next_normal_profiler_start_unverified','wrapper_flag_is_not_collection_or_output_truth'])
    return {'samples':samples,'complete_calls':len(complete),'worker_calls':worker_calls,'rank_status':rank_status,'conflict_candidates':candidates,'missing':sorted(set(missing)),
        'call_binding_failures':dict(sorted(binding_failures.items())),
        'backend_collection_stopped_verified':False,'trace_output_completed_verified':False,'cross_process_rpc_verified':False,
        'state_semantics_verified':False,'scope_complete':False,'producer_authenticated':False,'backend_cycles':cycles}


def backend_cycles(complete):
    """Only source/process/call-bound C-probe generations. No API/RPC time joins."""
    result=[]
    for (recording,call),(first,start) in complete.items():
        if (start['schema']!=sensor.EVENT_SCHEMA or start['point']!='TorchProfilerWrapper._start'
                or start['phase']!='RETURN' or not start['backend_probe_qualified']
                or start['backend_before']['value'] is not False or start['backend_after']['value'] is not True):continue
        selected=start['state_after']['wrapper_sha256'];generation=start['backend_generation_sha256']
        backend=start['backend_after']['backend_sha256'];thread=start['backend_after']['thread_id']
        stops=[(begin,end) for (rec,key),(begin,end) in complete.items() if rec==recording
            and end['schema']==sensor.EVENT_SCHEMA and end['point']=='TorchProfilerWrapper._stop'
            and end['backend_generation_sha256']==generation and end['state_before']['wrapper_sha256']==selected
            and end['seq']>start['seq'] and end['backend_before']['backend_sha256']==backend
            and end['backend_before']['thread_id']==thread and end['backend_probe_qualified']]
        if len(stops)!=1:continue
        stop_first,stop=stops[0]
        def worker_ancestor(row):
            seen=set()
            while row['parent_call_sha256'] is not None and len(seen)<8:
                key=(recording,row['parent_call_sha256'])
                if key in seen or key not in complete:return None
                seen.add(key);begin,end=complete[key]
                if not begin['seq']<row['seq']<=end['seq']:return None
                if end['point']=='Worker.profile':return end
                row=end
            return None
        start_worker=worker_ancestor(start);stop_worker=worker_ancestor(stop)
        rank=start_worker['state_after']['rank'] if start_worker is not None else None
        worker_bound=bool(start_worker and stop_worker and rank is not None
            and stop_worker['state_after']['rank']==rank and start_worker['is_start'] is True and stop_worker['is_start'] is False
            and start_worker['state_after']['wrapper_sha256']==stop_worker['state_after']['wrapper_sha256']==selected)
        before=stop['trace_before'];after=stop['trace_after']
        old={(r['name_sha256'],r['sha256']) for r in before['files']}
        new=[r for r in after['files'] if r['kind']=='trace' and (r['name_sha256'],r['sha256']) not in old]
        output=bool(stop['phase']=='RETURN' and before['status']=='directory' and after['status']=='directory'
            and before['directory_identity']==after['directory_identity'] and new)
        stopped=stop['backend_after']['value'] is False
        wrapper_stop=complete.get((recording,stop['parent_call_sha256']))
        continued_violation=bool(not stopped and wrapper_stop and wrapper_stop[1]['point']=='WorkerProfiler._call_stop'
            and wrapper_stop[1]['phase']=='RETURN' and wrapper_stop[1]['state_after']['running'] is False)
        result.append({'recording':recording,'pid':start['pid'],'start_ticks':start['start_ticks'],'wrapper_sha256':selected,
            'backend_sha256':backend,'thread_id':thread,'generation_sha256':generation,'rank':rank,'worker_binding_verified':worker_bound,
            'start_event_ref':[recording,start['seq']],'stop_event_ref':[recording,stop['seq']],
            'start_call_sha256':call,'stop_call_sha256':stop['call_sha256'],'stop_phase':stop['phase'],
            'backend_collection_stopped_verified':stopped,'collection_continued_verified':not stopped,
            'confirmed_wrapper_backend_violation':continued_violation,
            'trace_output_completed_verified':output,'trace_file_facts':new if output else [],
            'trace_directory_identity':after['directory_identity'],
            'owned_collision_observed':before['status']==after['status']=='owned-collision',
            'collision_sha256':before['collision_sha256'] if before==after and before['status']=='owned-collision' else None})
    return result
