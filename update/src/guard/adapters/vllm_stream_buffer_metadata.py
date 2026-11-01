"""Closed /12 transport samples; whole process RSS never charges one connection."""
import re
from . import vllm_stream_buffer_sensor as sensor

FIELDS=frozenset({'schema','phase','source_module','source_sha256','source_verified','request_sha256','asgi_invocation_sha256',
 'asgi_context_live','connection_sha256','associated_requests','call_sha256','body_bytes','flow_write_paused','cycle_disconnected',
 'observer_runtime','observer_code_sha256','transport_kind','write_buffer_bytes','kernel_outq_bytes','socket_inode','socket_identity_stable',
 'transport_closing','process_rss_bytes','operation_started_ns','operation_finished_ns','parent_call_sha256'})


def fingerprint(v):return type(v) is str and re.fullmatch('[0-9a-f]{64}',v) is not None


def validate_sample(v):
    try:
        if (type(v) is not dict or set(v)!=FIELDS or v['schema']!=sensor.STREAM_SCHEMA or type(v['phase']) is not str
                or v['phase'] not in sensor.PHASES or type(v['source_module']) is not str or v['source_module'] not in sensor.SOURCE_ADDITIONS
                or v['source_sha256']!=sensor.SOURCE_ADDITIONS[v['source_module']]
                or type(v['observer_runtime']) is not str or v['observer_runtime'] not in sensor.RUNTIME_CODE_CONTRACTS
                or v['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[v['observer_runtime']]):raise ValueError
        for key in ('source_verified','asgi_context_live','socket_identity_stable'):
            if type(v[key]) is not bool:raise ValueError
        for key in ('flow_write_paused','cycle_disconnected','transport_closing'):
            if v[key] is not None and type(v[key]) is not bool:raise ValueError
        for key in ('body_bytes','write_buffer_bytes','kernel_outq_bytes','socket_inode','process_rss_bytes','operation_started_ns','operation_finished_ns'):
            if v[key] is not None and (type(v[key]) is not int or not 0<=v[key]<2**63):raise ValueError
        for key in ('connection_sha256','call_sha256'):
            if not fingerprint(v[key]):raise ValueError
        for key in ('request_sha256','asgi_invocation_sha256','parent_call_sha256'):
            if v[key] is not None and not fingerprint(v[key]):raise ValueError
        if (type(v['associated_requests']) is not list or not 1<=len(v['associated_requests'])<=8
                or any(not fingerprint(x) for x in v['associated_requests']) or len(set(v['associated_requests']))!=len(v['associated_requests'])
                or v['request_sha256'] is not None and v['request_sha256'] not in v['associated_requests']
                or v['asgi_context_live'] and (v['request_sha256'] is None or v['asgi_invocation_sha256'] is None)):
            raise ValueError
        close=v['phase'].startswith('CONNECTION_LOST')
        terminal=v['phase'].endswith(('RETURN','RAISE'))
        if terminal:
            if v['operation_started_ns'] is None or v['operation_finished_ns'] is None or v['operation_started_ns']>v['operation_finished_ns']:raise ValueError
        elif v['operation_started_ns'] is not None or v['operation_finished_ns'] is not None:raise ValueError
        if close!=(v['request_sha256'] is None):raise ValueError
        drain=v['phase'].startswith('DRAIN')
        if drain!=(v['source_module']=='uvicorn.protocols.http.flow_control'):raise ValueError
        if drain!=fingerprint(v['parent_call_sha256']):raise ValueError
        if not drain and v['parent_call_sha256'] is not None:raise ValueError
        if not v['phase'].startswith('SEND') and v['body_bytes'] is not None:raise ValueError
        if type(v['transport_kind']) is not str or v['transport_kind'] not in {'unsupported','asyncio-selector-tcp'}:raise ValueError
        if v['transport_kind']=='unsupported' and (any(v[k] is not None for k in ('write_buffer_bytes','kernel_outq_bytes','socket_inode','transport_closing')) or v['socket_identity_stable']):raise ValueError
        if v['transport_kind']=='asyncio-selector-tcp' and v['write_buffer_bytes'] is None:raise ValueError
        if v['socket_identity_stable']!=(v['kernel_outq_bytes'] is not None and v['socket_inode'] is not None and v['socket_inode']>0):raise ValueError
    except (TypeError,KeyError,ValueError):raise ValueError('STREAM_BUFFER_SAMPLE_INVALID') from None


def project(packet,trials,limits):
    result={'samples':[],'cases':[],'missing':[],'conflict_candidates':[],'producer_authenticated':False,'buffer_scope_complete':False}
    recordings=[] if not packet else packet.get('recordings',[])
    for index,recording in enumerate(recordings):
        capture=recording.get('capture')
        if not capture:continue
        rows=capture['records'];context=rows[0]['payload']['context']
        for row in rows:
            if row['event']!='STREAM_BUFFER':continue
            payload=row['payload'];validate_sample(payload)
            result['samples'].append({'recording':index,'seq':row['seq'],'monotonic_ns':row['monotonic_ns'],
                'pid':context['pid'],'start_ticks':context['start_ticks'],'time_namespace_inode':context['time_namespace'],
                'process_binding_verified':bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match')),
                'image_digest_verified':capture.get('image_digest_verified') is True,**payload})
    for trial in trials:
        if not trial['attempted']:continue
        request=trial['request_id_sha256'];rows=[s for s in result['samples'] if s['request_sha256']==request or request in s['associated_requests']]
        identities={(r['recording'],r['pid'],r['start_ticks'],r['connection_sha256']) for r in rows if r['request_sha256']==request}
        unique=len(identities)==1
        selected=[r for r in rows if unique and (r['recording'],r['pid'],r['start_ticks'],r['connection_sha256']) in identities]
        verified=[r for r in selected if r['source_verified'] and r['process_binding_verified']]
        writes=[r['write_buffer_bytes'] for r in verified if r['write_buffer_bytes'] is not None]
        rss=[r['process_rss_bytes'] for r in verified if r['process_rss_bytes'] is not None]
        drains={};sends={}
        for r in verified:
            groups=drains if r['phase'].startswith('DRAIN') else sends if r['phase'].startswith('SEND') else None
            if groups is not None:groups.setdefault(r['call_sha256'],[]).append(r)
        pairs=[]
        for values in drains.values():
            before=[r for r in values if r['phase']=='DRAIN_ENTER'];after=[r for r in values if r['phase']=='DRAIN_RETURN']
            if len(before)!=1 or len(after)!=1 or before[0]['parent_call_sha256']!=after[0]['parent_call_sha256']:continue
            parents=[r for r in sends.get(after[0]['parent_call_sha256'],[]) if r['phase'] in {'SEND_RETURN','SEND_RAISE'}]
            if len(parents)==1 and parents[0]['operation_started_ns']<=after[0]['operation_started_ns']<=after[0]['operation_finished_ns']<=parents[0]['operation_finished_ns']:
                pairs.append(after[0]['operation_finished_ns']-after[0]['operation_started_ns'])
        closes=[r for r in verified if r['phase']=='CONNECTION_LOST_RETURN']
        missing=[]
        if not unique:missing.append('unique_request_connection_unavailable')
        if not verified:missing.append('source_or_physical_process_binding_unavailable')
        if not writes:missing.append('selected_transport_buffer_unavailable')
        if not closes:missing.append('connection_close_sample_unavailable')
        missing.append('full_buffer_inventory_and_client_close_release_epoch_unverified')
        if trial['case_id'] in {'lif04-slow','lif04-slow-close'} and not pairs:missing.append('completed_drain_interval_unavailable' if drains else 'actual_drain_backpressure_unobserved')
        if any(len(v)!=2 for v in sends.values()):missing.append('send_end_or_unique_call_unavailable')
        case={'case_id':trial['case_id'],'unique_connection_observed':unique,'sample_count':len(selected),
            'sampled_write_buffer_peak_bytes':max(writes) if writes else None,'process_rss_peak_bytes':max(rss) if rss else None,
            'process_rss_is_connection_cost':False,'drain_intervals_ns':pairs,'close_write_buffer_bytes':[r['write_buffer_bytes'] for r in closes],
            'declared_release_deadline_ms':limits['buffer_release_deadline_ms'],
            'missing':missing,'producer_authenticated':False,'full_buffer_inventory_verified':False,'release_verified':False}
        result['cases'].append(case);result['missing'].extend({'case_id':trial['case_id'],'reason':m} for m in missing)
        for field,limit in (('write_buffer_bytes',limits['buffer_max_write_bytes']),('process_rss_bytes',limits['buffer_max_process_rss_bytes'])):
            if any(r[field] is not None and r[field]>limit for r in verified):
                result['conflict_candidates'].append({'case_id':trial['case_id'],'kind':'sampled_'+field+'_exceeds_declared_budget','confirmed_violation':False})
        if any(delay>limits['buffer_max_send_wait_ms']*1_000_000 for delay in pairs):
            result['conflict_candidates'].append({'case_id':trial['case_id'],'kind':'actual_drain_wait_exceeds_declared_budget','confirmed_violation':False})
    result['missing'].append({'case_id':None,'reason':'debug_route_source_activation_and_resource_producer_unverified'})
    return result
