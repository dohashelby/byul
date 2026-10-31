"""Draft closed rank facts; timeout never identifies delay vs communication stall."""
import re
from . import vllm_rank_failure_sensor as sensor

FAULT_KINDS=('exception','death','delay','communication-stall')
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_verified','observer_runtime','observer_code_sha256',
 'owner_sha256','executor_sha256','call_sha256','parent_call_sha256','role','native_request_sha256','rpc_method','rank','world_size',
 'configured_output_rank','selected_reply_rank','non_block','executor_is_failed','worker_output_is_exception','future_state','future_has_exception',
 'returned_kind','exception_kind','executor_failed_marker_observed','monitor_caller_verified','monitor_dead_rank','monitor_dead_pid',
 'monitor_exit_code_abs','monitor_exit_code_sign','operation_started_ns','operation_finished_ns','fault_activation_verified',
 'cross_process_group_verified','upper_client_receipt_verified'})
POINT_MODULES={cls+'.'+method:module for module,classes in sensor.TARGETS.items() for cls,methods in classes.items() for method in methods}


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_sample(v):
    try:
        if (type(v) is not dict or set(v)!=FIELDS or v['schema']!=sensor.EVENT_SCHEMA or type(v['point']) is not str
                or v['point'] not in POINT_MODULES or v['source_module']!=POINT_MODULES[v['point']]
                or v['source_sha256']!=sensor.SOURCES[v['source_module']] or v['phase'] not in {'CALL','RETURN','RAISE'}
                or type(v['observer_runtime']) is not str or v['observer_runtime'] not in sensor.RUNTIME_CODE_CONTRACTS
                or v['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[v['observer_runtime']]):raise ValueError
        role={'MultiprocExecutor':'executor','FutureWrapper':'future','WorkerProc':'worker','EngineCoreProc':'core'}[v['point'].split('.')[0]]
        if v['role']!=role:raise ValueError
        for key in ('source_verified','executor_failed_marker_observed','monitor_caller_verified','fault_activation_verified','cross_process_group_verified','upper_client_receipt_verified'):
            if type(v[key]) is not bool:raise ValueError
        if any(v[k] is not False for k in ('fault_activation_verified','cross_process_group_verified','upper_client_receipt_verified')):raise ValueError
        for key in ('owner_sha256','call_sha256'):
            if not fingerprint(v[key]):raise ValueError
        for key in ('executor_sha256','parent_call_sha256'):
            if v[key] is not None and not fingerprint(v[key]):raise ValueError
        if (type(v['native_request_sha256']) is not list or len(v['native_request_sha256'])>8
                or any(not fingerprint(k) for k in v['native_request_sha256']) or len(set(v['native_request_sha256']))!=len(v['native_request_sha256'])):raise ValueError
        for key in ('rank','world_size','configured_output_rank','selected_reply_rank','monitor_dead_rank','monitor_dead_pid','monitor_exit_code_abs','operation_started_ns','operation_finished_ns'):
            if v[key] is not None and (type(v[key]) is not int or not 0<=v[key]<2**63):raise ValueError
        for key in ('non_block','executor_is_failed','worker_output_is_exception','future_has_exception'):
            if v[key] is not None and type(v[key]) is not bool:raise ValueError
        if v['rpc_method'] is not None and v['rpc_method']!='execute_model':raise ValueError
        if v['future_state'] is not None and v['future_state'] not in {'PENDING','RUNNING','FINISHED','CANCELLED','CANCELLED_AND_NOTIFIED'}:raise ValueError
        if role!='future' and any(v[k] is not None for k in ('future_state','future_has_exception')):raise ValueError
        if role!='worker' and v['rank'] is not None:raise ValueError
        if role!='executor' and any(v[k] is not None for k in ('world_size','configured_output_rank','executor_is_failed')):raise ValueError
        if v['point'] not in {'WorkerProc.handle_output','WorkerProc.enqueue_output'} and v['worker_output_is_exception'] is not None:raise ValueError
        if v['point']!='EngineCoreProc._handle_client_request' and v['executor_failed_marker_observed']:raise ValueError
        if v['monitor_caller_verified']:
            if v['point']!='MultiprocExecutor.shutdown':raise ValueError
        elif any(v[k] is not None for k in ('monitor_dead_rank','monitor_dead_pid','monitor_exit_code_abs','monitor_exit_code_sign')):raise ValueError
        if v['monitor_exit_code_sign'] is not None and v['monitor_exit_code_sign'] not in {'negative','zero','positive'}:raise ValueError
        if (v['monitor_exit_code_abs'] is None)!=(v['monitor_exit_code_sign'] is None):raise ValueError
        if v['monitor_exit_code_abs'] is not None and (v['monitor_exit_code_abs']>255 or (v['monitor_exit_code_abs']==0)!=(v['monitor_exit_code_sign']=='zero')):raise ValueError
        if v['phase']=='CALL':
            if any(v[k] is not None for k in ('operation_started_ns','operation_finished_ns','returned_kind','exception_kind')):raise ValueError
        else:
            if v['operation_started_ns'] is None or v['operation_finished_ns'] is None or v['operation_started_ns']>v['operation_finished_ns']:raise ValueError
            if v['phase']=='RETURN':
                if v['returned_kind'] not in {'none','future','value'} or v['exception_kind'] is not None:raise ValueError
            elif v['returned_kind'] is not None or v['exception_kind'] not in {'timeout','exception','base-exception'}:raise ValueError
    except (KeyError,TypeError,ValueError):raise ValueError('RANK_FAILURE_SAMPLE_INVALID') from None


def project(packet,limits):
    result={'samples':[],'calls':[],'source_failure_candidates':[],'fault_cases':[],'missing':[],
        'producer_authenticated':False,'whole_scope_complete':False,'all_required_ranks_completed':False,'upper_client_receipt_verified':False}
    for index,recording in enumerate([] if not packet else packet.get('recordings',[])):
        capture=recording.get('capture')
        if not capture:continue
        context=capture['records'][0]['payload']['context']
        for row in capture['records']:
            if row['event']!='RANK_FAILURE':continue
            value=row['payload'];validate_sample(value)
            result['samples'].append({'recording':index,'seq':row['seq'],'monotonic_ns':row['monotonic_ns'],'pid':context['pid'],
                'start_ticks':context['start_ticks'],'boot_sha256':context['boot_sha256'],'pid_namespace':context['pid_namespace'],
                'time_namespace':context['time_namespace'],'time_offsets_sha256':context['time_offsets_sha256'],
                'process_binding_verified':bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match')),
                'image_digest_verified':capture.get('image_digest_verified') is True,**value})
    groups={}
    for row in result['samples']:
        key=(row['recording'],row['pid'],row['start_ticks'],row['call_sha256']);groups.setdefault(key,[]).append(row)
    for key,rows in groups.items():
        first=[r for r in rows if r['phase']=='CALL'];last=[r for r in rows if r['phase'] in {'RETURN','RAISE'}]
        stable=('owner_sha256','executor_sha256','parent_call_sha256','point','role','native_request_sha256','rpc_method','selected_reply_rank','observer_runtime','observer_code_sha256')
        paired=(len(rows)==2 and len(first)==len(last)==1 and all(first[0][k]==last[0][k] for k in stable)
            and first[0]['monotonic_ns']<=last[0]['operation_started_ns']<=last[0]['operation_finished_ns']<=last[0]['monotonic_ns'])
        bound=paired and all(r['source_verified'] and r['process_binding_verified'] for r in rows)
        value=last[0] if len(last)==1 else rows[0]
        call={'recording':key[0],'pid':key[1],'start_ticks':key[2],'call_sha256':key[3],'point':value['point'],
            'owner_sha256':value['owner_sha256'],'executor_sha256':value['executor_sha256'],'parent_call_sha256':value['parent_call_sha256'],
            'native_request_sha256':value['native_request_sha256'],'paired':paired,'source_process_binding_observed':bound,
            'phase':value['phase'],'exception_kind':value['exception_kind'],'future_has_exception':value['future_has_exception'],
            'monitor_caller_verified':value['monitor_caller_verified'],'executor_failed_marker_observed':value['executor_failed_marker_observed'],
            'operation_started_ns':value['operation_started_ns'],'operation_finished_ns':value['operation_finished_ns'],
            'parent_interval_observed':False,'fault_activation_verified':False,'upper_client_receipt_verified':False}
        result['calls'].append(call)
        if not paired:result['missing'].append({'call_sha256':key[3],'reason':'complete_unique_same_process_call_unavailable'})
        if bound:
            facts=[]
            if value['worker_output_is_exception'] is True:facts.append('worker_output_exception_object_observed')
            if value['future_has_exception'] is True:facts.append('selected_future_exception_observed')
            if value['phase']=='RAISE':facts.append('selected_call_raised_'+value['exception_kind'])
            if value['monitor_caller_verified']:facts.append('verified_monitor_entered_executor_shutdown')
            if value['executor_failed_marker_observed']:facts.append('core_executor_failed_marker_observed')
            expected_ranks={int(v) for v in limits['rank_expected_ranks']}
            if value['rank'] is not None and value['rank'] not in expected_ranks:facts.append('observed_rank_outside_declared_required_ranks')
            if value['configured_output_rank'] is not None and value['configured_output_rank']!=limits['rank_output_rank']:facts.append('configured_output_rank_differs_from_declaration')
            if value['selected_reply_rank'] is not None and value['selected_reply_rank']!=limits['rank_output_rank']:facts.append('selected_reply_rank_differs_from_declaration')
            if value['world_size'] is not None and value['world_size']!=len(expected_ranks):facts.append('observed_world_size_differs_from_declared_rank_count')
            result['source_failure_candidates'].extend({'call_sha256':key[3],'kind':fact,'confirmed_violation':False,'four_fault_kind_inferred':False} for fact in facts)
    for call in result['calls']:
        if not call['source_process_binding_observed'] or call['parent_call_sha256'] is None:continue
        parents=[p for p in result['calls'] if p['recording']==call['recording'] and p['pid']==call['pid'] and p['start_ticks']==call['start_ticks']
            and p['call_sha256']==call['parent_call_sha256'] and p['source_process_binding_observed']]
        call['parent_interval_observed']=bool(len(parents)==1 and parents[0]['operation_started_ns']<=call['operation_started_ns']<=call['operation_finished_ns']<=parents[0]['operation_finished_ns'])
    for kind in FAULT_KINDS:
        result['fault_cases'].append({'kind':kind,'declared_detection_deadline_ms':limits['rank_'+kind.replace('-','_')+'_detection_ms'],
            'declared_notification_deadline_ms':limits['rank_'+kind.replace('-','_')+'_notification_ms'],
            'fault_activation_verified':False,'occurrence_clock_verified':False,'detection_deadline_verified':False,
            'upper_notification_deadline_verified':False,'affected_request_error_verified':False,'whole_kind_complete':False})
        result['missing'].append({'kind':kind,'reason':'independent_fault_activation_rank_occurrence_detection_upper_receipt_binding_unavailable'})
    result['missing'].append({'kind':None,'reason':'required_rank_normal_computation_communication_and_selected_output_unverified'})
    return result
