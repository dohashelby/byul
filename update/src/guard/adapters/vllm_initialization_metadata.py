"""Typed startup claims and same-process preparation/aggregate correspondence."""
import re

from guard.models import canonical_json, sha256_hex

from . import vllm_initialization_sensor as sensor

FIELDS=frozenset({'schema','call_sha256','executor_sha256','parent_call_sha256','point','expected_handles','response_status','phase',
 'source_module','source_sha256','source_verified','observer_runtime','observer_code_sha256','returned_handles','executor_workers',
 'world_size','exception_kind','operation_started_ns','operation_finished_ns','failure_activation_verified','cross_process_group_verified','service_gate_verified'})


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_sample(v,source_sha256=None):
    try:
        if (type(v) is not dict or set(v)!=FIELDS or v['schema']!=sensor.EVENT_SCHEMA or v['point'] not in sensor.POINTS
                or v['source_module']!=sensor.MP or v['source_sha256']!=(sensor.SOURCE_ADDITIONS[sensor.MP] if source_sha256 is None else source_sha256)
                or v['phase'] not in {'CALL','RETURN','RAISE'} or v['observer_runtime'] not in sensor.RUNTIME_CODE_CONTRACTS
                or v['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[v['observer_runtime']]):raise ValueError
        for key in ('source_verified','failure_activation_verified','cross_process_group_verified','service_gate_verified'):
            if type(v[key]) is not bool:raise ValueError
        if any(v[k] is not False for k in ('failure_activation_verified','cross_process_group_verified','service_gate_verified')):raise ValueError
        if not fingerprint(v['call_sha256']):raise ValueError
        for key in ('executor_sha256','parent_call_sha256'):
            if v[key] is not None and not fingerprint(v[key]):raise ValueError
        for key in ('expected_handles','returned_handles','executor_workers'):
            rows=v[key]
            if rows is not None:
                if type(rows) is not list or not 1<=len(rows)<=64:raise ValueError
                for row in rows:
                    if (type(row) is not dict or set(row)!={'rank','pid'} or type(row['rank']) is not int or not 0<=row['rank']<=63
                            or row['pid'] is not None and (type(row['pid']) is not int or not 0<row['pid']<2**63)):raise ValueError
                if len({r['rank'] for r in rows})!=len(rows):raise ValueError
        for key in ('world_size','operation_started_ns','operation_finished_ns'):
            if v[key] is not None and (type(v[key]) is not int or not 0<=v[key]<2**63):raise ValueError
        if v['response_status'] not in {None,'READY','other'}:raise ValueError
        if v['point']!='WorkerProc.wait_for_response_handle_ready' and v['response_status'] is not None:raise ValueError
        if v['point']=='MultiprocExecutor._init_executor':
            if v['expected_handles'] is not None or v['returned_handles'] is not None:raise ValueError
        elif v['executor_workers'] is not None or v['world_size'] is not None:raise ValueError
        if v['phase']=='CALL':
            if any(v[k] is not None for k in ('returned_handles','executor_workers','operation_started_ns','operation_finished_ns','exception_kind')):raise ValueError
        else:
            if v['operation_started_ns'] is None or v['operation_finished_ns'] is None or v['operation_started_ns']>v['operation_finished_ns']:raise ValueError
            if v['phase']=='RETURN' and v['exception_kind'] is not None:raise ValueError
            if v['phase']=='RAISE' and (v['exception_kind'] not in {'timeout','exception','base-exception'} or v['returned_handles'] is not None):raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('INITIALIZATION_SAMPLE_INVALID') from None


def _worker_pids(handles, required):
    if handles is None:
        return None
    by_rank = {handle['rank']: handle['pid'] for handle in handles}
    pids = set(by_rank.values())
    return by_rank if by_rank.keys() == required and None not in pids and len(pids) == len(by_rank) else None


def _current_children(packet, capture):
    """Bind only present-day child processes, independently of the READY event time."""
    before, after = packet.get('before'), packet.get('after')
    if not before or not after:
        return {}
    owner = capture['provenance']['owner']
    if any(snapshot['owner_stable'] is not True or snapshot['owner'] != owner
           or not fingerprint(snapshot['capture_sha256']) for snapshot in (before, after)):
        return {}
    initial, final = before['metadata'], after['metadata']
    if (not initial['directory_stable'] or not final['directory_stable']
            or not initial['sealed'] or not final['sealed']
            or initial['session'] != final['session'] or initial['directory_before'] != final['directory_before']):
        return {}
    original = {row['pid']: row for row in initial['processes']}
    current = {row['pid']: row for row in final['processes']}
    context = capture['records'][0]['payload']['context']
    executor = original.get(context['pid'])
    if (executor is None or executor != current.get(context['pid'])
            or executor['same_executable'] is not True
            or any(executor[key] != value for key, value in context.items())):
        return {}
    domain = ('boot_sha256', 'pid_namespace', 'time_namespace', 'time_offsets_sha256', 'uid')
    return {
        pid: {'before_sha256': before['capture_sha256'], 'after_sha256': after['capture_sha256'],
              'process_identity_sha256': sha256_hex(canonical_json(row))}
        for pid, row in original.items()
        if row['parent_pid'] == executor['pid'] and pid != executor['pid']
        and row == current.get(pid) and row['start_ticks'] >= executor['start_ticks']
        and row['same_executable'] is True and all(row[key] == executor[key] for key in domain)
    }


def _rank_ready_responses(calls, key, ready, workers, paired_events, required, current_children):
    ready_seq = paired_events[(*key, ready['call_sha256'])]
    ranks = {}
    for call in calls:
        if (call['point'] == 'WorkerProc.wait_for_response_handle_ready'
                and (call['recording'], call['pid'], call['start_ticks']) == key
                and call['parent_call_sha256'] == ready['call_sha256']
                and call['executor_sha256'] == ready['executor_sha256']
                and len(call['expected_handles'] or []) == 1):
            ranks.setdefault(call['expected_handles'][0]['rank'], []).append(call)
    handle_sets = (ready['expected_handles'] or [], ready['returned_handles'] or [], workers or [])
    observed = []
    for rank in sorted(ranks.keys() & required):
        if len(ranks[rank]) != 1:
            continue
        call, = ranks[rank]
        handle, = call['expected_handles']
        seq = paired_events.get((*key, call['call_sha256']))
        if (call['source_process_binding_observed'] and call['phase'] == 'RETURN' and call['response_status'] == 'READY'
                and seq is not None and ready_seq[0] < seq[0] < seq[1] < ready_seq[1]
                and ready['operation_started_ns'] <= call['operation_started_ns']
                <= call['operation_finished_ns'] <= ready['operation_finished_ns']
                and handle['pid'] is not None and call['returned_handles'] == [handle]
                and all([row for row in handles if row['rank'] == rank or row['pid'] == handle['pid']] == [handle]
                        for handles in handle_sets)):
            response = {'rank': rank, 'response_status': 'READY', 'call_and_return_seq': seq,
                        'worker_handle_pid_match_observed': True}
            census = current_children.get(handle['pid'])
            if census is not None:
                response['current_child_process_census'] = census
            observed.append(response)
    return observed


def project(packet,limits,*,source_sha256=None):
    result={'samples':[],'calls':[],'correspondence':[],'missing':[],
        'producer_authenticated':False,'whole_scope_complete':False,'failure_activation_verified':False,
        'all_preparation_stages_verified':False,'cross_phase_lineage_verified':False,'service_gate_verified':False}
    source_recordings = {}
    for index,recording in enumerate([] if not packet else packet.get('recordings',[])):
        capture=recording.get('capture')
        if not capture:continue
        provenance = capture.get('provenance', {})
        summary = capture.get('summary', {})
        if (limits.get('init_phase') in {'normal', 'recovered-normal'}
                and type(provenance) is dict and type(summary) is dict
                and provenance.get('owner_binding_verified') is True
                and fingerprint(provenance.get('capture_sha256'))
                and summary.get('recording_closed') is True and summary.get('discarded_tail_bytes') == 0):
            source_recordings[index] = capture
        context=capture['records'][0]['payload']['context']
        for row in capture['records']:
            if row['event']!='INITIALIZATION':continue
            value=row['payload'];validate_sample(value,source_sha256=source_sha256)
            result['samples'].append({'recording':index,'seq':row['seq'],'monotonic_ns':row['monotonic_ns'],
                **{k:context[k] for k in ('pid','start_ticks','boot_sha256','pid_namespace','time_namespace','time_offsets_sha256')},
                'safe_file_claim_retained':True,'process_binding_verified':bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match')),
                'image_digest_verified':capture.get('image_digest_verified') is True,**value})
    grouped={}
    paired_events = {}
    for row in result['samples']:grouped.setdefault((row['recording'],row['pid'],row['start_ticks'],row['call_sha256']),[]).append(row)
    for key,rows in grouped.items():
        first=[r for r in rows if r['phase']=='CALL'];last=[r for r in rows if r['phase'] in {'RETURN','RAISE'}]
        stable=('executor_sha256','parent_call_sha256','point','expected_handles','response_status','observer_runtime','observer_code_sha256')
        paired=(len(rows)==2 and len(first)==len(last)==1 and all(first[0][k]==last[0][k] for k in stable)
            and first[0]['monotonic_ns']<=last[0]['operation_started_ns']<=last[0]['operation_finished_ns']<=last[0]['monotonic_ns'])
        value=last[0] if len(last)==1 else rows[0]
        if (paired and key[0] in source_recordings and value['phase'] == 'RETURN'
                and type(first[0]['seq']) is type(value['seq']) is int
                and 0 <= first[0]['seq'] < value['seq'] < sensor.MAX_ROWS):
            paired_events[key] = [first[0]['seq'], value['seq']]
        result['calls'].append({**{k:value[k] for k in ('point','call_sha256','executor_sha256','parent_call_sha256','phase','expected_handles','returned_handles','executor_workers','world_size','exception_kind','operation_started_ns','operation_finished_ns')},
            'response_status':value['response_status'],
            'recording':key[0],'pid':key[1],'start_ticks':key[2],'paired':paired,
            'source_process_binding_observed':bool(paired and all(r['source_verified'] and r['process_binding_verified'] for r in rows))})
        if not paired:result['missing'].append('unique_complete_startup_call_unavailable')
        if not all(r['process_binding_verified'] for r in rows):result['missing'].append('recording_claim_current_process_binding_unavailable')
    required={int(v) for v in limits['init_expected_ranks']}
    ranks=lambda rows:{r['rank'] for r in rows} if rows is not None else None
    for parent in result['calls']:
        if parent['point']!='MultiprocExecutor._init_executor':continue
        children=[c for c in result['calls'] if c['point']=='WorkerProc.wait_for_ready' and c['recording']==parent['recording']
            and c['pid']==parent['pid'] and c['start_ticks']==parent['start_ticks'] and c['parent_call_sha256']==parent['call_sha256']
            and c['executor_sha256']==parent['executor_sha256']]
        child=children[0] if len(children)==1 else None
        linked=bool(child and parent['source_process_binding_observed'] and child['source_process_binding_observed']
            and parent['operation_started_ns']<=child['operation_started_ns']<=child['operation_finished_ns']<=parent['operation_finished_ns'])
        result['correspondence'].append({'executor_call_sha256':parent['call_sha256'],'ready_call_sha256':child['call_sha256'] if child else None,
            'same_process_interval_observed':linked,'ready_phase':child['phase'] if child else None,'aggregate_phase':parent['phase'],
            'required_rank_ready_return_observed':bool(linked and child['phase']=='RETURN' and ranks(child['expected_handles'])==ranks(child['returned_handles'])==required),
            'ready_failure_propagation_observed':bool(linked and child['phase']=='RAISE' and parent['phase']=='RAISE'),
            'ready_failure_aggregate_success_candidate':bool(linked and child['phase']=='RAISE' and parent['phase']=='RETURN'),
            'declared_world_rank_workers_match':bool(linked and parent['world_size']==len(required) and ranks(parent['executor_workers'])==required),
            'confirmed_violation':False,'all_model_loads_verified':False,'failure_activation_verified':False,'service_gate_verified':False})
        if linked:
            if not fingerprint(parent['executor_sha256']):
                result['missing'].append('initialization_executor_instance_unverified')
                continue
            key = (parent['recording'], parent['pid'], parent['start_ticks'])
            aggregate_events = paired_events.get((*key, parent['call_sha256']))
            ready_events = paired_events.get((*key, child['call_sha256']))
            if (aggregate_events is not None and ready_events is not None
                    and aggregate_events[0] < ready_events[0] < ready_events[1] < aggregate_events[1]):
                capture = source_recordings[parent['recording']]
                result['correspondence'][-1]['source_recording'] = {
                    'sha256': capture['provenance']['capture_sha256'],
                    'aggregate_call_and_return_seq': aggregate_events,
                    'ready_call_and_return_seq': ready_events,
                }
                expected_pids = _worker_pids(child['expected_handles'], required)
                # These are PID claims in the executor's handles, not a
                # census or a verification of the worker process itself.
                result['correspondence'][-1]['required_rank_worker_pid_chain_observed'] = bool(
                    parent['world_size'] == len(required) and expected_pids is not None
                    and expected_pids == _worker_pids(child['returned_handles'], required)
                    == _worker_pids(parent['executor_workers'], required))
                ready_responses = _rank_ready_responses(result['calls'], key, child, parent['executor_workers'],
                                                        paired_events, required, _current_children(packet, capture))
                if ready_responses:
                    result['correspondence'][-1]['rank_ready_responses'] = ready_responses
    result['missing'].extend(['independent_failure_activation_and_component_root_outcome_unavailable',
        'all_required_model_preparation_and_inflight_state_unavailable','normal_failure_repaired_launch_lineage_unavailable',
        'readiness_and_http_shape_are_not_independent_admission_oracle','source_record_producer_authentication_unavailable'])
    result['missing']=sorted(set(result['missing']));return result
