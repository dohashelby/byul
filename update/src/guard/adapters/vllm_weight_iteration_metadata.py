"""Closed native16 step journal; Python progress is not physical model use."""
import re
from . import vllm_weight_iteration_sensor as sensor

FIELDS=frozenset({'schema','point','phase','source_module','source_sha256','source_method_code_sha256','source_verified',
 'observer_runtime','observer_code_sha256','call_sha256','parent_call_sha256','root_load_sha256','model_object_sha256',
 'factory_model_hint_sha256','loader_call_sha256','loader_model_sha256','parameter_object_sha256','operation_index','yield_index','action','operation_started_ns','operation_finished_ns',
 'yielded_name_sha256','yielded_tensor_object_sha256','exception_kind','iterator_closed_observed','root_load_active_observed',
 'parameter_callback_returned_observed','worker_assignment_verified','file_read_verified','tensor_storage_applied_verified',
 'callback_implementation_verified','producer_authenticated'})
TRUTH=('worker_assignment_verified','file_read_verified','tensor_storage_applied_verified','callback_implementation_verified','producer_authenticated')
HASHES=('parent_call_sha256','root_load_sha256','model_object_sha256','factory_model_hint_sha256','loader_call_sha256','loader_model_sha256','parameter_object_sha256','yielded_name_sha256','yielded_tensor_object_sha256')
IDENTITY=('point','source_module','source_sha256','source_method_code_sha256','observer_runtime','observer_code_sha256','factory_model_hint_sha256','parameter_object_sha256')
ROOT=('root_load_sha256','model_object_sha256','parent_call_sha256','loader_call_sha256','loader_model_sha256')
END_PHASES=frozenset({'STEP_YIELD','STEP_STOP','STEP_RAISE','STEP_CLOSED'})


def fingerprint(value):return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_sample(value):
    try:
        if type(value) is not dict or set(value)!=FIELDS or value['schema']!=sensor.EVENT_SCHEMA or value['point'] not in sensor.POINTS:raise ValueError
        point=value['point'];phase=value['phase'];runtime=value['observer_runtime']
        if phase not in sensor.PHASES or value['source_module']!=sensor.POINT_SOURCES[point] or value['source_sha256']!=sensor.SOURCE_ADDITIONS[value['source_module']]:raise ValueError
        if (runtime not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]
                or value['source_method_code_sha256']!=sensor.RUNTIME_POINT_CODES[runtime][point]):raise ValueError
        if not fingerprint(value['call_sha256']) or any(value[k] is not None and not fingerprint(value[k]) for k in HASHES):raise ValueError
        for key in ('source_verified','iterator_closed_observed','root_load_active_observed','parameter_callback_returned_observed'):
            if type(value[key]) is not bool:raise ValueError
        if any(value[k] is not False for k in TRUTH):raise ValueError
        if any(type(value[k]) is not int or not 0<=value[k]<2**63 for k in ('operation_index','yield_index')):raise ValueError
        if value['root_load_sha256'] is None and (value['model_object_sha256'] is not None or value['root_load_active_observed']):raise ValueError
        if value['factory_model_hint_sha256'] is not None and point not in {'DefaultModelLoader.get_all_weights','AutoWeightsLoader._load_module'}:raise ValueError
        if value['parameter_object_sha256'] is not None and point!='AutoWeightsLoader._load_param':raise ValueError
        if phase=='FACTORY':
            if value['operation_index'] or value['yield_index'] or any(value[k] is not None for k in ('action','operation_started_ns','operation_finished_ns','exception_kind')) or value['iterator_closed_observed']:raise ValueError
        else:
            if value['operation_index']<1 or value['action'] not in {'NEXT','SEND','THROW','CLOSE'}:raise ValueError
            if type(value['operation_started_ns']) is not int or not 0<=value['operation_started_ns']<2**63:raise ValueError
            if phase=='STEP_BEGIN':
                if value['operation_finished_ns'] is not None or value['exception_kind'] is not None or value['iterator_closed_observed']:raise ValueError
            elif type(value['operation_finished_ns']) is not int or not value['operation_started_ns']<=value['operation_finished_ns']<2**63:raise ValueError
            if value['exception_kind'] not in ({'exception','base-exception'} if phase=='STEP_RAISE' else {None}):raise ValueError
            if phase=='STEP_CLOSED' and (value['action']!='CLOSE' or not value['iterator_closed_observed']):raise ValueError
            if phase=='STEP_YIELD' and (value['action']=='CLOSE' or value['iterator_closed_observed'] or value['yield_index']<1):raise ValueError
            if phase=='STEP_STOP' and (value['action']=='CLOSE' or not value['iterator_closed_observed']):raise ValueError
        if phase!='STEP_YIELD' and (value['yielded_name_sha256'] is not None or value['yielded_tensor_object_sha256'] is not None):raise ValueError
        if value['parameter_callback_returned_observed']!=(phase=='STEP_YIELD' and point=='AutoWeightsLoader._load_param' and value['yielded_name_sha256'] is not None):raise ValueError
    except (ValueError,KeyError,TypeError):raise ValueError('WEIGHT_ITERATION_SAMPLE_INVALID') from None


def journal(rows):
    """Reject contradictions; report missing pairs without inventing no progress."""
    calls={};facts=[];missing=[];runtime=None
    for row in rows:
        value=row['payload'];validate_sample(value)
        if runtime is not None and runtime!=value['observer_runtime']:raise ValueError('WEIGHT_ITERATION_RUNTIME_MISMATCH')
        runtime=value['observer_runtime'];call=value['call_sha256'];phase=value['phase']
        state=calls.setdefault(call,{'factory':None,'identity':tuple(value[k] for k in IDENTITY),'root':None,'operations':{},'closed':False})
        if state['identity']!=tuple(value[k] for k in IDENTITY):raise ValueError('WEIGHT_ITERATION_CALL_BINDING_MISMATCH')
        if phase=='FACTORY':
            if state['factory'] is not None or state['operations']:raise ValueError('WEIGHT_ITERATION_FACTORY_REUSED')
            state['factory']=row;continue
        root=tuple(value[k] for k in ROOT)
        if state['root'] is None:
            factory=state['factory']
            if factory and factory['payload']['root_load_sha256'] is not None and root!=tuple(factory['payload'][k] for k in ROOT):raise ValueError('WEIGHT_ITERATION_ROOT_SUBSTITUTED')
            if factory and factory['payload']['factory_model_hint_sha256'] is not None and value['root_load_sha256'] is not None and value['model_object_sha256']!=factory['payload']['factory_model_hint_sha256'] and value['point']=='DefaultModelLoader.get_all_weights':raise ValueError('WEIGHT_ITERATION_MODEL_SUBSTITUTED')
            state['root']=root
        elif state['root']!=root:raise ValueError('WEIGHT_ITERATION_ROOT_SUBSTITUTED')
        index=value['operation_index'];operation=state['operations'].setdefault(index,{'begin':None,'end':None})
        which='begin' if phase=='STEP_BEGIN' else 'end'
        if operation[which] is not None or state['closed']:raise ValueError('WEIGHT_ITERATION_OPERATION_REUSED')
        if which=='begin' and any(op['end'] is None for i,op in state['operations'].items() if i!=index):raise ValueError('WEIGHT_ITERATION_OPERATION_OVERLAP')
        operation[which]=row
        if which=='end':state['closed']=value['iterator_closed_observed']
    roots=[]
    for call,state in calls.items():
        factory=state['factory'];previous=0;yielded=0
        if factory is None:missing.append('weight_factory_unavailable')
        for index,operation in sorted(state['operations'].items()):
            begin,end=operation['begin'],operation['end']
            if index!=previous+1:missing.append('weight_operation_prefix_unavailable')
            previous=index
            if begin is None or end is None:
                missing.append('weight_operation_pair_unavailable');continue
            first,last=begin['payload'],end['payload']
            if (begin['seq']>=end['seq'] or first['action']!=last['action'] or first['operation_started_ns']!=last['operation_started_ns']
                    or first['root_load_active_observed']!=last['root_load_active_observed']):raise ValueError('WEIGHT_ITERATION_STEP_BINDING_MISMATCH')
            if first['yield_index']!=yielded and index==1 and factory is not None:raise ValueError('WEIGHT_ITERATION_YIELD_COUNTER_INVALID')
            if index>1 and first['yield_index']!=yielded:raise ValueError('WEIGHT_ITERATION_YIELD_COUNTER_INVALID')
            expected_yield=first['yield_index']+(last['phase']=='STEP_YIELD')
            if last['yield_index']!=expected_yield:raise ValueError('WEIGHT_ITERATION_YIELD_COUNTER_INVALID')
            yielded=last['yield_index']
            if last['phase']=='STEP_YIELD':facts.append({'begin_seq':begin['seq'],'end_seq':end['seq'],'factory_present':factory is not None,
                'source_pair_verified':first['source_verified'] and last['source_verified'],**last})
        if factory and factory['payload']['point']=='AutoWeightsLoader._load_module' and factory['payload']['root_load_sha256']==call:
            terminals=[op['end'] for op in state['operations'].values() if op['end']]
            terminal=max(terminals,key=lambda row:row['seq'])['payload']['phase'] if terminals else None
            roots.append({'call_sha256':call,'model_object_sha256':factory['payload']['model_object_sha256'],'factory_seq':factory['seq'],
                'loader_call_sha256':factory['payload']['loader_call_sha256'],'loader_model_sha256':factory['payload']['loader_model_sha256'],
                'source_verified':factory['payload']['source_verified'],'iterator_closed_observed':state['closed'],
                'terminal_phase':terminal,'root_exhausted_observed':state['closed'] and terminal=='STEP_STOP',
                'operation_pairs_complete':bool(state['operations']) and all(op['begin'] and op['end'] for op in state['operations'].values()),
                'last_seq':max((op['end']['seq'] for op in state['operations'].values() if op['end']),default=factory['seq'])})
        if not state['closed']:missing.append('weight_iterator_not_observed_closed')
    return {'facts':facts,'roots':roots,'missing':sorted(set(missing))}


def project(packet):
    samples=[];models=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=sensor.PROFILE:continue
        events=[row for row in records if row['event']=='WEIGHT_ITERATION'];data=journal(events)
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
                'recording_closed':capture.get('summary',{}).get('recording_closed') is True,**{key:False for key in TRUTH},'scope_complete':False})
        missing.extend(data['missing'])
    missing.extend(['worker_FD_read_and_range_unverified','tensor_storage_and_backend_apply_completion_unverified','worker_assignment_and_rank_shard_inventory_unverified','approval_and_inspection_authority_unverified'])
    if not samples:missing.append('source_bound_weight_iteration_unavailable')
    return {'schema':'guard-weight-iteration-projection/1','samples':samples,'model_candidates':models,'missing':sorted(set(missing)),**{key:False for key in TRUTH},'scope_complete':False}


def attach_configured_models(trials,projection):
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator']
            model['weight_iteration_candidates']=[row for row in projection['model_candidates'] if row['recording']==locator['recording']
                and row['model_object_sha256']==model['session_model_object_sha256'] and row['last_seq']<locator['forward_seq']]
            model['actual_tensor_consumption_verified']=False
    return trials
