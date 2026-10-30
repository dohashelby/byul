"""Closed native17 FD/read journal, independent of model approval and tensor use."""
from . import vllm_file_read_sensor as sensor,vllm_weight_iteration_sensor as iteration

SNAPSHOT=frozenset({'device_sha256','inode_sha256','mode','uid','bytes','mtime_ns','ctime_ns','regular'})
TRUTH=('worker_assignment_verified','approved_model_file_verified','producer_authenticated','tensor_storage_applied_verified',
    'decoder_completion_verified','nofollow_components_verified')
FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_method_code_sha256','source_verified',
    'observer_runtime','observer_code_sha256','scope_sha256','handle_sha256','call_sha256','root_load_sha256','model_object_sha256',
    'loader_call_sha256','loader_model_sha256','operation_index','normalized_path_sha256','cwd_sha256','operation_started_ns',
    'operation_finished_ns','fd_before','fd_after','path_before','path_after','returned_bytes','returned_bytes_sha256','hash_status',
    'exception_kind','fd_closed_observed','source_fd_read_return_observed',*TRUTH})
IDENTITY=('call_sha256','root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256','operation_index',
    'normalized_path_sha256','cwd_sha256','scope_sha256','observer_runtime','observer_code_sha256','source_method_code_sha256')


def validate_sample(value):
    try:
        if (type(value) is not dict or set(value)!=FIELDS or value['schema']!=sensor.EVENT_SCHEMA or value['point']!=sensor.POINT
                or value['phase'] not in sensor.PHASES or value['source_module']!=sensor.WEIGHTS
                or value['source_sha256']!=sensor.SOURCE_ADDITIONS[sensor.WEIGHTS]):raise ValueError
        runtime=value['observer_runtime'];phase=value['phase']
        if (runtime not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]
                or value['source_method_code_sha256']!=iteration.RUNTIME_POINT_CODES[runtime][sensor.POINT]):raise ValueError
        for key in ('scope_sha256','handle_sha256','call_sha256','normalized_path_sha256','cwd_sha256'):
            if not sensor.fingerprint(value[key]):raise ValueError
        for key in ('root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256','returned_bytes_sha256'):
            if value[key] is not None and not sensor.fingerprint(value[key]):raise ValueError
        if (type(value['operation_index']) is not int or not 0<value['operation_index']<2**63
                or any(type(value[k]) is not int or not 0<=value[k]<2**63 for k in ('operation_started_ns','operation_finished_ns'))
                or value['operation_finished_ns']<value['operation_started_ns']):raise ValueError
        for key in ('source_verified','fd_closed_observed','source_fd_read_return_observed'):
            if type(value[key]) is not bool:raise ValueError
        if any(value[key] is not False for key in TRUTH):raise ValueError
        if value['root_load_sha256'] is None and value['model_object_sha256'] is not None:raise ValueError
        for key in ('fd_before','fd_after','path_before','path_after'):
            row=value[key]
            if row is None:continue
            if (type(row) is not dict or set(row)!=(SNAPSHOT|{'offset'} if key.startswith('fd_') else SNAPSHOT)
                    or type(row['regular']) is not bool or any(not sensor.fingerprint(row[k]) for k in ('device_sha256','inode_sha256'))
                    or any(type(v) is not int or not 0<=v<2**63 for k,v in row.items() if k not in {'regular','device_sha256','inode_sha256'})):raise ValueError
        if value['exception_kind'] not in ({'exception','base-exception'} if phase.endswith('RAISE') else {None}):raise ValueError
        if value['hash_status'] not in {'disabled','unavailable','complete','too-large'}:raise ValueError
        if phase=='READ_RETURN':
            if type(value['returned_bytes']) is not int or not 0<=value['returned_bytes']<2**63 or value['source_fd_read_return_observed'] is not True:raise ValueError
            if (value['returned_bytes_sha256'] is not None)!=(value['hash_status']=='complete'):raise ValueError
        elif value['returned_bytes'] is not None or value['returned_bytes_sha256'] is not None or value['hash_status']!='disabled' or value['source_fd_read_return_observed']:raise ValueError
        if phase in {'OPEN_RETURN','OPEN_RAISE','READ_RETURN','READ_RAISE'} and value['fd_closed_observed']:raise ValueError
        if phase=='CLOSE_RETURN' and value['fd_closed_observed'] is not True:raise ValueError
    except (ValueError,KeyError,TypeError):raise ValueError('FILE_READ_SAMPLE_INVALID') from None


def identity(value):return {key:value[key] for key in SNAPSHOT} if value else None


def journal(rows,prepared,weight_rows):
    handles={};missing=[];facts=[];open_failures=[];runtime=None
    scope=prepared['model_read_scope'];allowed={row['normalized_path_sha256']:row for row in scope['files']}
    for row in rows:
        value=row['payload'];validate_sample(value)
        if value['scope_sha256']!=prepared['assets']['model-read-scope.json']['sha256'] or value['normalized_path_sha256'] not in allowed:raise ValueError('FILE_READ_SCOPE_MISMATCH')
        if runtime is not None and runtime!=value['observer_runtime']:raise ValueError('FILE_READ_RUNTIME_MISMATCH')
        runtime=value['observer_runtime'];phase=value['phase'];handle=value['handle_sha256']
        state=handles.setdefault(handle,{'identity':tuple(value[key] for key in IDENTITY),'open':None,'read':None,'close':None})
        if state['identity']!=tuple(value[key] for key in IDENTITY):raise ValueError('FILE_READ_HANDLE_SUBSTITUTED')
        key='open' if phase.startswith('OPEN_') else 'read' if phase.startswith('READ_') else 'close'
        if state[key] is not None or state['close'] is not None or key=='open' and state['read'] is not None or key!='open' and state['open'] and state['open']['payload']['phase']=='OPEN_RAISE':raise ValueError('FILE_READ_HANDLE_REUSED')
        state[key]=row
        if value['hash_status']=='complete' and (not scope['hash_model_bytes'] or value['returned_bytes']>scope['max_hash_bytes']):raise ValueError('FILE_READ_HASH_SCOPE_INVALID')
        if value['hash_status']=='too-large' and (not scope['hash_model_bytes'] or value['returned_bytes']<=scope['max_hash_bytes']):raise ValueError('FILE_READ_HASH_SCOPE_INVALID')
    for handle,state in handles.items():
        opened,read,closed=state['open'],state['read'],state['close']
        ordered=[row for row in (opened,read,closed) if row]
        if any(a['seq']>=b['seq'] or a['payload']['operation_finished_ns']>b['payload']['operation_started_ns'] for a,b in zip(ordered,ordered[1:])):raise ValueError('FILE_READ_ORDER_INVALID')
        if opened is None:missing.append('file_open_unavailable')
        if read is None:
            missing.append('file_read_unavailable')
            if opened and opened['payload']['phase']=='OPEN_RAISE':open_failures.append({'open_seq':opened['seq'],**opened['payload']})
            continue
        if closed is None:missing.append('file_close_unavailable')
        value=read['payload'];reference=allowed[value['normalized_path_sha256']]
        step=[row for row in weight_rows if row['payload']['call_sha256']==value['call_sha256'] and row['payload']['operation_index']==value['operation_index']]
        begin=next((row for row in step if row['payload']['phase']=='STEP_BEGIN'),None)
        end=next((row for row in step if row['payload']['phase'] in iteration.PHASES-{'FACTORY','STEP_BEGIN'}),None)
        linked=bool(begin and end and begin['seq']<ordered[0]['seq']<=read['seq']<=ordered[-1]['seq']<end['seq']
            and all(row['payload']['point']==sensor.POINT and all(row['payload'][key]==value[key] for key in ('root_load_sha256','model_object_sha256','loader_call_sha256','loader_model_sha256','observer_runtime')) for row in (begin,end))
            and begin['payload']['operation_started_ns']<=ordered[0]['payload']['operation_started_ns']
            and ordered[-1]['payload']['operation_finished_ns']<=end['payload']['operation_finished_ns'])
        if not linked:missing.append('file_read_iterator_step_link_unavailable')
        snapshots=[row['payload'][key] for row in (opened,read,closed) if row for key in ('fd_before','fd_after') if row['payload'][key] is not None]
        stable=bool(opened and value['fd_before'] and value['fd_after'] and all(v['regular'] for v in snapshots)
            and len({tuple(identity(v).items()) for v in snapshots})==1)
        path_snapshots=[row['payload'][key] for row in (opened,read,closed) if row for key in ('path_before','path_after')]
        path_match=bool(stable and path_snapshots and all(v is not None and identity(v)==identity(snapshots[0]) for v in path_snapshots))
        full=bool(stable and value['phase']=='READ_RETURN' and value['fd_before']['offset']==0
            and value['fd_after']['offset']==value['returned_bytes']==value['fd_after']['bytes'])
        reference_match=bool(full and value['hash_status']=='complete' and value['returned_bytes_sha256']==reference['reference_sha256'] and value['returned_bytes']==reference['bytes'])
        facts.append({'handle_sha256':handle,'open_seq':opened['seq'] if opened else None,'read_seq':read['seq'],
            'close_seq':closed['seq'] if closed else None,**value,'iterator_step_link_observed':linked,
            'source_sequence_verified':all(row['payload']['source_verified'] for row in ordered),
            'fd_metadata_stable_observed':stable,'path_fd_metadata_match_observed':path_match,
            'full_fd_read_return_observed':full,'declared_reference_bytes_match_observed':reference_match,
            'close_observed':bool(closed and closed['payload']['fd_closed_observed']),
            'reference_authority_verified':False,'scope_complete':False})
    if not facts:missing.append('source_bound_eager_fd_read_unavailable')
    return {'facts':facts,'open_failures':open_failures,'missing':sorted(set(missing))}


def project(packet,prepared):
    samples=[];open_failures=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=sensor.PROFILE:continue
        data=journal([row for row in records if row['event']=='FILE_READ'],prepared,[row for row in records if row['event']=='WEIGHT_ITERATION'])
        context=records[0]['payload']['context'];bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        samples.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],
            'process_binding_verified':bound,'recording_closed':capture.get('summary',{}).get('recording_closed') is True,**fact} for fact in data['facts'])
        open_failures.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],
            'process_binding_verified':bound,**fact} for fact in data['open_failures'])
        missing.extend(data['missing'])
    missing.extend(['ordinary_C_safe_open_read_and_mmap_unobserved','tensor_storage_and_backend_apply_completion_unverified',
        'worker_assignment_and_rank_shard_inventory_unverified','approval_and_inspection_authority_unverified'])
    return {'schema':'guard-file-read-projection/1','samples':samples,'open_failures':open_failures,'missing':sorted(set(missing)),**{key:False for key in TRUTH},'scope_complete':False}


def attach_configured_models(trials,projection):
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator']
            model['eager_file_read_candidates']=[row for row in projection['samples'] if row['recording']==locator['recording']
                and row['model_object_sha256']==model['session_model_object_sha256'] and row['read_seq']<locator['forward_seq']]
            model['actual_tensor_consumption_verified']=False
    return trials
