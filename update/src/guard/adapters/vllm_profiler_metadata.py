"""Closed native15 profiler facts; wrapper flags never imply backend truth."""
import re
from . import vllm_profiler_sensor as sensor

STATE_FIELDS=frozenset({'owner_kind','wrapper_sha256','backend_kind','rank','local_rank','active','running','active_iteration_count','profiling_for_iters'})
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_verified','observer_runtime','observer_code_sha256',
 'call_sha256','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start',
 'state_before','state_after','api_status_code','exception_kind','operation_started_ns','operation_finished_ns',
 'backend_collection_stopped_verified','trace_output_completed_verified','cross_process_rpc_verified','producer_authenticated'})


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
        if type(value) is not dict or set(value)!=FIELDS or value['schema']!=sensor.EVENT_SCHEMA or value['point'] not in sensor.POINTS:raise ValueError
        if value['phase'] not in {'CALL','RETURN','RAISE'} or value['source_module']!=sensor.POINT_SOURCES[value['point']] or value['source_sha256']!=sensor.SOURCE_ADDITIONS[value['source_module']]:raise ValueError
        if type(value['source_verified']) is not bool or type(value['registered_handler_observed']) is not bool:raise ValueError
        if value['observer_runtime'] not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[value['observer_runtime']]:raise ValueError
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
    except (TypeError,KeyError,ValueError):raise ValueError('PROFILER_SAMPLE_INVALID') from None


def project(packet,required_ranks=()):
    samples=[];calls={};missing=[];candidates=[]
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
        fields=('point','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start','state_before')
        if (any(first[k]!=last[k] for k in fields) or first['seq']>=last['seq'] or not first['source_verified'] or not last['source_verified']
            or not first['physical_process_binding_verified'] or not last['physical_process_binding_verified'] or not last['recording_closed']):
            missing.append('profiler_source_process_call_binding_unavailable');continue
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
        'backend_collection_stopped_verified':False,'trace_output_completed_verified':False,'cross_process_rpc_verified':False,
        'state_semantics_verified':False,'scope_complete':False,'producer_authenticated':False}
