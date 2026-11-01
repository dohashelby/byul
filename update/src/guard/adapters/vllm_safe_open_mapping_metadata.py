"""Closed profile18 mapping hints; never model read, release or tensor proof."""
from . import vllm_safe_open_mapping_sensor as sensor
from . import vllm_weight_iteration_sensor as iteration
from . import vllm_weight_iteration_metadata as weights

MAP_FIELDS=frozenset({'region_sha256','file_offset','mapped_bytes','permissions','deleted','device_sha256','inode_sha256'})
SNAPSHOT_FIELDS=frozenset({'status','complete','non_atomic','matches'})
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_method_code_sha256','source_verified',
 'observer_runtime','observer_code_sha256','scope_sha256','context_sha256','call_sha256','root_load_sha256','model_object_sha256',
 'loader_call_sha256','loader_model_sha256','operation_index','iterator_step_link_observed','normalized_path_sha256',
 'library_files_verified_at_open','native_binary_file_sha256','operation_started_ns','operation_finished_ns','before','after',
 'path_identity_unchanged_observed','exception_kind','tensor_return_observed',*sensor.TRUTH})
IDENTITY=('context_sha256','call_sha256','root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256',
 'normalized_path_sha256','scope_sha256','source_method_code_sha256','observer_runtime','observer_code_sha256','library_files_verified_at_open')
PERMISSIONS=frozenset(a+b+c+d for a in 'r-' for b in 'w-' for c in 'x-' for d in 'ps')


def validate_sample(value):
    try:
        if type(value) is not dict or set(value)!=FIELDS or value['schema']!=sensor.EVENT_SCHEMA or value['point']!=sensor.POINT or value['phase'] not in sensor.PHASES:raise ValueError
        runtime=value['observer_runtime']
        if (runtime not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]
                or value['source_module']!=sensor.WEIGHTS or value['source_sha256']!=iteration.SOURCE_ADDITIONS[sensor.WEIGHTS]
                or value['source_method_code_sha256']!=iteration.RUNTIME_POINT_CODES[runtime][sensor.POINT]):raise ValueError
        for key in ('scope_sha256','context_sha256','call_sha256','normalized_path_sha256'):
            if not sensor.fingerprint(value[key]):raise ValueError
        for key in ('root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256'):
            if value[key] is not None and not sensor.fingerprint(value[key]):raise ValueError
        if value['root_load_sha256'] is None and value['model_object_sha256'] is not None:raise ValueError
        for key in ('source_verified','iterator_step_link_observed','library_files_verified_at_open','path_identity_unchanged_observed','tensor_return_observed'):
            if type(value[key]) is not bool:raise ValueError
        if any(value[key] is not False for key in sensor.TRUTH):raise ValueError
        index=value['operation_index']
        if (index is not None and (type(index) is not int or not 0<index<2**63)) or (index is not None)!=value['iterator_step_link_observed']:raise ValueError
        if any(type(value[k]) is not int or not 0<=value[k]<2**63 for k in ('operation_started_ns','operation_finished_ns')) or value['operation_started_ns']>value['operation_finished_ns']:raise ValueError
        if value['exception_kind'] not in ({'exception','base-exception'} if value['phase'].endswith('RAISE') else {None}):raise ValueError
        if value['tensor_return_observed']!=(value['phase']=='TENSOR_RETURN'):raise ValueError
        if value['native_binary_file_sha256']!=(sensor.LIBRARY_FILES['_safetensors_rust.abi3.so'] if value['library_files_verified_at_open'] else None):raise ValueError
        if (value['phase']=='LIBRARY_UNAVAILABLE')==value['library_files_verified_at_open']:raise ValueError
        for key in ('before','after'):
            snapshot=value[key]
            if value['phase']=='LIBRARY_UNAVAILABLE':
                if snapshot is not None:raise ValueError
                continue
            if type(snapshot) is not dict or set(snapshot)!=SNAPSHOT_FIELDS or snapshot['status'] not in {'observed','bounded','unavailable'}:raise ValueError
            if snapshot['non_atomic'] is not True or type(snapshot['complete']) is not bool or snapshot['complete']!=(snapshot['status']=='observed'):raise ValueError
            if type(snapshot['matches']) is not list or len(snapshot['matches'])>sensor.MAX_MATCHES:raise ValueError
            seen=set()
            for row in snapshot['matches']:
                if type(row) is not dict or set(row)!=MAP_FIELDS or any(not sensor.fingerprint(row[k]) for k in ('region_sha256','device_sha256','inode_sha256')):raise ValueError
                if row['region_sha256'] in seen or type(row['deleted']) is not bool or row['permissions'] not in PERMISSIONS:raise ValueError
                if type(row['file_offset']) is not int or not 0<=row['file_offset']<2**63 or type(row['mapped_bytes']) is not int or not 0<row['mapped_bytes']<2**63:raise ValueError
                seen.add(row['region_sha256'])
            if snapshot['status']=='unavailable' and snapshot['matches']:raise ValueError
    except (ValueError,KeyError,TypeError):raise ValueError('SAFE_OPEN_MAPPING_EVENT_INVALID') from None
    return value


def journal(rows,prepared,weight_rows):
    allowed={row['normalized_path_sha256'] for row in prepared['model_read_scope']['files']};states={};samples=[];missing=[];runtime=None
    roots={root['call_sha256']:root for root in weights.journal(weight_rows)['roots']}
    for row in rows:
        value=validate_sample(row['payload']);phase=value['phase'];context=value['context_sha256']
        if value['scope_sha256']!=prepared['assets']['model-read-scope.json']['sha256'] or value['normalized_path_sha256'] not in allowed:raise ValueError('SAFE_OPEN_MAPPING_SCOPE_INVALID')
        if runtime is not None and runtime!=value['observer_runtime']:raise ValueError('SAFE_OPEN_MAPPING_RUNTIME_MISMATCH')
        runtime=value['observer_runtime'];identity=tuple(value[key] for key in IDENTITY)
        state=states.setdefault(context,{'identity':identity,'open':None,'enter':None,'exit':None,'last':None,'unavailable':False})
        if state['identity']!=identity:raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_SUBSTITUTED')
        last=state['last']
        if last and (last['seq']>=row['seq'] or last['payload']['operation_finished_ns']>value['operation_started_ns']):raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_ORDER_INVALID')
        if state['exit'] or state['unavailable'] or state['open'] and state['open']['payload']['phase']=='OPEN_RAISE' or state['enter'] and state['enter']['payload']['phase']=='ENTER_RAISE':raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_REUSED')
        if phase=='LIBRARY_UNAVAILABLE':state['unavailable']=True;missing.append('safe_open_library_file_provenance_unavailable')
        elif phase.startswith('OPEN_'):
            if last is not None:raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_REUSED')
            state['open']=row
        elif phase.startswith('ENTER_'):
            if state['enter'] is not None:raise ValueError('SAFE_OPEN_MAPPING_CONTEXT_REUSED')
            state['enter']=row
        elif phase.startswith('EXIT_'):state['exit']=row
        elif state['enter'] is None:missing.append('safe_open_enter_unavailable')
        if phase!='LIBRARY_UNAVAILABLE' and state['open'] is None:missing.append('safe_open_constructor_unavailable')
        step=[event for event in weight_rows if event['payload']['call_sha256']==value['call_sha256'] and event['payload']['operation_index']==value['operation_index']]
        begin=next((event for event in step if event['payload']['phase']=='STEP_BEGIN'),None)
        end=next((event for event in step if event['payload']['phase'] in iteration.PHASES-{'FACTORY','STEP_BEGIN'}),None)
        linked=bool(value['iterator_step_link_observed'] and begin and end and begin['seq']<row['seq']<end['seq']
            and begin['payload']['operation_started_ns']<=value['operation_started_ns']<=value['operation_finished_ns']<=end['payload']['operation_finished_ns']
            and all(event['payload']['point']==sensor.POINT and all(event['payload'][key]==value[key] for key in ('root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256','observer_runtime')) for event in (begin,end)))
        if not linked:missing.append('safe_open_iterator_step_pair_unavailable')
        if phase!='LIBRARY_UNAVAILABLE' and not all(value[key]['complete'] for key in ('before','after')):missing.append('safe_open_mapping_snapshot_incomplete')
        root=roots.get(value['root_load_sha256'])
        loader=bool(root and root['source_verified'] and root['factory_seq']<row['seq'] and value['source_verified']
            and root['loader_call_sha256'] and root['loader_call_sha256']==value['loader_call_sha256']
            and root['model_object_sha256']==value['model_object_sha256']==value['loader_model_sha256']==root['loader_model_sha256'])
        samples.append({'event_seq':row['seq'],'iterator_pair_verified':linked,'loader_model_candidate_observed':loader,**value})
        state['last']=row
    contexts=[]
    for token,state in states.items():
        if state['exit'] is None:missing.append('safe_open_exit_unavailable')
        contexts.append({'context_sha256':token,'constructor_seq':state['open']['seq'] if state['open'] else None,
            'enter_seq':state['enter']['seq'] if state['enter'] else None,'exit_seq':state['exit']['seq'] if state['exit'] else None,
            'exit_return_observed':bool(state['exit'] and state['exit']['payload']['phase']=='EXIT_RETURN'),**{key:False for key in sensor.TRUTH}})
    return {'samples':samples,'contexts':contexts,'missing':sorted(set(missing))}


def project(packet,prepared):
    samples=[];contexts=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=sensor.PROFILE:continue
        data=journal([r for r in records if r['event']=='SAFE_OPEN_MAPPING'],prepared,[r for r in records if r['event']=='WEIGHT_ITERATION'])
        context=records[0]['payload']['context'];bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        samples.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,
            'same_loader_model_candidate':bool(bound and row['loader_model_candidate_observed'] and row['iterator_pair_verified'] and row['library_files_verified_at_open']),**row} for row in data['samples'])
        contexts.extend({'recording':number,**row} for row in data['contexts']);missing.extend(data['missing'])
    missing+=['mapped_file_does_not_prove_read_or_tensor_use','native_loaded_memory_and_worker_assignment_unverified','approval_and_inspection_authority_unverified']
    if not samples:missing.append('safe_open_mapping_observation_unavailable')
    return {'schema':'guard-safe-open-mapping-projection/1','samples':samples,'contexts':contexts,'missing':sorted(set(missing)),**{key:False for key in sensor.TRUTH}}


def attach_configured_models(trials,projection):
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator']
            model['safe_open_mapping_candidates']=[row for row in projection['samples'] if row['recording']==locator['recording']
                and row['model_object_sha256']==model['session_model_object_sha256'] and row['event_seq']<locator['forward_seq'] and row['same_loader_model_candidate']]
    return trials


def project_iterations(packet):
    samples=[];models=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=sensor.PROFILE:continue
        events=[row for row in records if row['event']=='WEIGHT_ITERATION'];data=weights.journal(events)
        context=records[0]['payload']['context'];bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        for fact in data['facts']:samples.append({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,**fact})
        for root in data['roots']:
            facts=[fact for fact in data['facts'] if fact['root_load_sha256']==root['call_sha256'] and fact['model_object_sha256']==root['model_object_sha256']
                and fact['factory_present'] and fact['source_pair_verified'] and fact['root_load_active_observed'] and root['factory_seq']<fact['end_seq']]
            callbacks=[fact for fact in facts if fact['parameter_callback_returned_observed']]
            models.append({'recording':number,**root,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,
                'loader_call_model_link_observed':bool(bound and root['loader_call_sha256'] and root['loader_model_sha256']==root['model_object_sha256'] and root['model_object_sha256']),
                'same_process_iteration_observed':bool(bound and root['source_verified'] and facts and root['model_object_sha256']),
                'step_yields_observed':len(facts),'parameter_callback_returns_observed':len(callbacks),
                'source_events':[[number,fact['end_seq']] for fact in facts[:64]],'source_event_locators_complete':len(facts)<=64,
                'recording_closed':capture.get('summary',{}).get('recording_closed') is True,**{key:False for key in weights.TRUTH},'scope_complete':False})
        missing.extend(data['missing'])
    missing.extend(['worker_FD_read_and_range_unverified','tensor_storage_and_backend_apply_completion_unverified','worker_assignment_and_rank_shard_inventory_unverified','approval_and_inspection_authority_unverified'])
    if not samples:missing.append('source_bound_weight_iteration_unavailable')
    return {'schema':'guard-weight-iteration-projection/1','samples':samples,'model_candidates':models,'missing':sorted(set(missing)),**{key:False for key in weights.TRUTH},'scope_complete':False}
