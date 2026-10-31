"""Profile19 source-qualified outer calls and explicit model-root links.

No header relabeling or cross-recording/time-only model joins are used.
"""
from . import vllm_worker_startup_sensor as sensor,vllm_worker_startup_contract as contract
from . import vllm_worker_readiness as callback
from . import vllm_safe_open_mapping_metadata as mapping
from . import vllm_weight_iteration_metadata as weights

POINT_CODES={'cpython-3.12.15': {'worker': {'Worker.init_device': '2280d202223a1ba3076316cc4a72f3bfc0b72a68b2f0aa33d578043c276e5879', 'Worker.load_model': '99c39c0d8b090884dbd344eaefd5d4ae96e8e2b5eec55daa50a819b69b963037', 'Worker.initialize_from_config': '5a38745686bc4ac396b3db769a90f08900a18d37913b379b0d4ede1c92556b52', 'Worker.compile_or_warm_up_model': '1b42495224a64a06c6ff69c20d1dc1fd3d909f27ca05ee7200b8cb09f3398f2c'}, 'otel_sync': '9fde31857299fbabbc2c5c322bc72ee76f47155aca75762602f4de1bd00d8132', 'callback_codes': {'guard.adapters.vllm_worker_readiness': 'd4594a4cb12f700a4e9af839cf03b42e80615107843922e918ace29df9bfe660', 'guard.adapters.vllm_slot_mask': '28fb87dbd3841dea7986b4e17437cd64c57812c5c2c3c1aef46f72dc6b14ee84'}}, 'cpython-3.12.14': {'worker': {'Worker.init_device': '2280d202223a1ba3076316cc4a72f3bfc0b72a68b2f0aa33d578043c276e5879', 'Worker.load_model': '99c39c0d8b090884dbd344eaefd5d4ae96e8e2b5eec55daa50a819b69b963037', 'Worker.initialize_from_config': '5a38745686bc4ac396b3db769a90f08900a18d37913b379b0d4ede1c92556b52', 'Worker.compile_or_warm_up_model': '1b42495224a64a06c6ff69c20d1dc1fd3d909f27ca05ee7200b8cb09f3398f2c'}, 'otel_sync': '9fde31857299fbabbc2c5c322bc72ee76f47155aca75762602f4de1bd00d8132', 'callback_codes': {'guard.adapters.vllm_worker_readiness': 'd4594a4cb12f700a4e9af839cf03b42e80615107843922e918ace29df9bfe660', 'guard.adapters.vllm_slot_mask': '28fb87dbd3841dea7986b4e17437cd64c57812c5c2c3c1aef46f72dc6b14ee84'}}, 'cpython-3.12.3': {'worker': {'Worker.init_device': '2280d202223a1ba3076316cc4a72f3bfc0b72a68b2f0aa33d578043c276e5879', 'Worker.load_model': '97e5fb28df82ff012e2ea7636ac601b4996cc1eff1cd9bf6adcc57712aea2df9', 'Worker.initialize_from_config': '5a38745686bc4ac396b3db769a90f08900a18d37913b379b0d4ede1c92556b52', 'Worker.compile_or_warm_up_model': 'a652b22ea0053a715de8530e55c3ecc72de222afe2632dfeeeca6b4a4b4f8733'}, 'otel_sync': '64a5e83f01a56099c7fd66af1865cdc9ccbde9203196e801df1c18dcfa51bdca', 'callback_codes': {'guard.adapters.vllm_worker_readiness': '27e4244ae29964ca0dbefd9d0fc408dab4a212185427f1a4ab932bd2046b8557', 'guard.adapters.vllm_slot_mask': '4a06b34e6bc029457383efa54eeaa93de99d0558e459842e7be60cfc5d999de0'}}}
FIELDS=frozenset({'schema','point','phase','source_module','source_files','source_verified','body_code_sha256','entry_code_sha256',
 'callback_source_sha256','callback_helper_source_sha256','callback_code_sha256','callback_helper_code_sha256','callback_code_verified',
 'entry_kind','inner_body_entry_observed','observer_runtime','observer_code_sha256','call_sha256','parent_call_sha256','call_started_ns',
 'operation_started_ns','operation_finished_ns','callback','callback_error','exception_kind',*sensor.TRUTH})
LINK_FIELDS=frozenset({'schema','worker_call_sha256','weight_event_seq','weight_payload_sha256','weight_call_sha256','model_object_sha256','observer_runtime','observer_code_sha256',*sensor.TRUTH})
CLOSE_FIELDS=frozenset({'schema','session_sha256','observer_runtime','observer_code_sha256','result',*sensor.TRUTH})
LIMIT_FIELDS=frozenset({'schema','session_sha256','observer_runtime','observer_code_sha256','bound','limit',*sensor.TRUTH})
CLOSE_RESULT_FIELDS=frozenset({'complete','pending_calls','collection_gap','gaps'})
PHASES=frozenset({'CALL','RETURN','RAISE'})
ENTRY_KINDS=frozenset({'direct-body','otel-sync-wrapper'})


def validate_common(value,fields,schema):
    if type(value) is not dict or set(value)!=fields or value['schema']!=schema:raise ValueError
    runtime=value['observer_runtime']
    if runtime not in POINT_CODES or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]:raise ValueError
    if any(value[key] is not False for key in sensor.TRUTH):raise ValueError
    return POINT_CODES[runtime]


def validate_sample(value):
    try:
        codes=validate_common(value,FIELDS,sensor.EVENT_SCHEMA);point=value['point'];kind=value['entry_kind']
        if point not in contract.SPAN_NAMES or value['phase'] not in PHASES or kind not in ENTRY_KINDS:raise ValueError
        if (value['source_module']!=contract.WORKER or value['source_files']!=contract.SOURCE_SHA256
                or type(value['source_verified']) is not bool or value['inner_body_entry_observed'] is not False
                or value['body_code_sha256']!=codes['worker'][point]
                or value['entry_code_sha256']!=(codes['worker'][point] if kind=='direct-body' else codes['otel_sync'])
                or point=='Worker.load_model' and kind!='direct-body'):raise ValueError
        if (value['callback_source_sha256']!=sensor.CALLBACK_SHA256 or value['callback_helper_source_sha256']!=sensor.SUPPORT_SHA256
                or value['callback_code_sha256']!=codes['callback_codes'][contract.CALLBACK_MODULE]
                or value['callback_helper_code_sha256']!=codes['callback_codes'][contract.SUPPORT_MODULE]):raise ValueError
        if type(value['callback_code_verified']) is not bool:raise ValueError
        if not contract.fingerprint(value['call_sha256']) or value['parent_call_sha256'] is not None and not contract.fingerprint(value['parent_call_sha256']):raise ValueError
        if any(type(value[k]) is not int or not 0<=value[k]<2**63 for k in ('call_started_ns','operation_started_ns','operation_finished_ns')):raise ValueError
        if not value['call_started_ns']<=value['operation_started_ns']<=value['operation_finished_ns']:raise ValueError
        if value['exception_kind'] not in ({'exception','base-exception'} if value['phase']=='RAISE' else {None}):raise ValueError
        if value['callback_error'] is None:
            if value['callback_code_verified'] is not True:raise ValueError
            contract.closed_callback(value['callback'],callback)
            snapshot=value['callback']
            if snapshot['point']!=point or snapshot['phase']!=value['phase'] or snapshot['binding'].get('call_id_sha256')!=value['call_sha256']:raise ValueError
            if set(snapshot['binding'])!={'pid','call_id_sha256','session_id_sha256','process_start_sha256'}:raise ValueError
        elif value['callback_error']!='CALLBACK_UNAVAILABLE' or value['callback'] is not None:raise ValueError
        return value
    except (ValueError,KeyError,TypeError,OverflowError):raise ValueError('WORKER_STARTUP_EVENT_INVALID') from None


def validate_link(value):
    try:
        validate_common(value,LINK_FIELDS,sensor.LINK_SCHEMA)
        if (type(value['weight_event_seq']) is not int or not 0<=value['weight_event_seq']<sensor.MAX_ROWS
                or any(not contract.fingerprint(value[k]) for k in ('worker_call_sha256','weight_payload_sha256','weight_call_sha256'))
                or value['model_object_sha256'] is not None and not contract.fingerprint(value['model_object_sha256'])):raise ValueError
        return value
    except (ValueError,KeyError,TypeError,OverflowError):raise ValueError('WORKER_STARTUP_LINK_INVALID') from None


def validate_close(value):
    try:
        validate_common(value,CLOSE_FIELDS,sensor.CLOSE_SCHEMA)
        if not contract.fingerprint(value['session_sha256']):raise ValueError
        result=value['result']
        if type(result) is not dict or set(result)!=CLOSE_RESULT_FIELDS:raise ValueError
        if type(result['complete']) is not bool or type(result['collection_gap']) is not bool:raise ValueError
        pending=result['pending_calls']
        if pending is not None and (type(pending) is not int or not 0<=pending<=64):raise ValueError
        if result['complete'] and pending is None or (not result['complete'] or pending is not None and pending>0) and not result['collection_gap']:raise ValueError
        gaps=result['gaps']
        if type(gaps) is not list or len(gaps)>len(callback.GAPS)+1 or len(set(gaps))!=len(gaps) or any(type(x) is not str or x not in callback.GAPS|{'CALLBACK_CLOSE_UNAVAILABLE'} for x in gaps):raise ValueError
        return value
    except (ValueError,KeyError,TypeError,OverflowError):raise ValueError('WORKER_STARTUP_CLOSE_INVALID') from None


def journal(records,prepared,context):
    events=[];links=[];closes=[];states={};missing=[];by_seq={row['seq']:row for row in records}
    runtime_values={row['payload']['observer_runtime'] for row in records if row['event'] in {'WORKER_STARTUP','WORKER_STARTUP_LINK','WORKER_STARTUP_CLOSE','WORKER_STARTUP_LIMIT','WEIGHT_ITERATION','SAFE_OPEN_MAPPING'}}
    if len(runtime_values)>1:raise ValueError('WORKER_STARTUP_RUNTIME_MISMATCH')
    weight_data=weights.journal([row for row in records if row['event']=='WEIGHT_ITERATION'])
    session_sha=sensor.sha(prepared['session'].encode());process_sha=sensor.sha(sensor.canonical(context))
    for row in records:
        event=row['event'];value=row['payload']
        if event=='WORKER_STARTUP':
            validate_sample(value);state=states.setdefault(value['call_sha256'],{'calls':[],'ends':[]})
            state['calls' if value['phase']=='CALL' else 'ends'].append(row)
            if value['callback'] is None:missing.append('worker_startup_callback_unavailable')
            else:
                binding=value['callback']['binding']
                if binding['pid']!=context['pid'] or binding['session_id_sha256']!=session_sha or binding['process_start_sha256']!=process_sha:
                    raise ValueError('WORKER_STARTUP_PROCESS_BINDING_INVALID')
                if value['callback']['status']=='unavailable':missing.append('worker_startup_callback_metadata_unavailable')
            events.append(row)
        elif event=='WORKER_STARTUP_LINK':validate_link(value);links.append(row)
        elif event=='WORKER_STARTUP_CLOSE':
            validate_close(value)
            if value['session_sha256']!=session_sha:raise ValueError('WORKER_STARTUP_CLOSE_SESSION_INVALID')
            closes.append(row)
        elif event=='WORKER_STARTUP_LIMIT':
            validate_limit(value)
            if value['session_sha256']!=session_sha:raise ValueError('WORKER_STARTUP_LIMIT_SESSION_INVALID')
            missing.append('worker_startup_observer_call_bound')
    spans=[]
    identity=tuple(FIELDS-{'phase','operation_started_ns','operation_finished_ns','callback','callback_error','callback_code_verified','exception_kind'})
    for token,state in states.items():
        first=state['calls'][0] if len(state['calls'])==1 else None;last=state['ends'][0] if len(state['ends'])==1 else None
        paired=bool(first and last and first['seq']<last['seq']
            and all(first['payload'][key]==last['payload'][key] for key in identity)
            and first['payload']['operation_finished_ns']<=last['payload']['operation_started_ns'])
        if not paired:missing.append('worker_startup_unique_call_pair_unavailable')
        if first:
            value=first['payload'];end=last['payload'] if last else None
            b=end['callback'] if end else None;transition=b['metadata'].get('call_transition',{}) if b else {}
            spans.append({'call_sha256':token,'point':value['point'],'start_seq':first['seq'],'end_seq':last['seq'] if last else None,
                'outer_call_pair_verified':paired,'callback_method_pair_verified':bool(paired and transition.get('paired') is True),
                'source_verified':bool(paired and value['source_verified'] and end['source_verified']),
                'entry_kind':value['entry_kind'],'returned':bool(paired and end['phase']=='RETURN'),**{key:False for key in sensor.TRUTH}})
    models=[]
    for row in links:
        value=row['payload'];target=by_seq.get(value['weight_event_seq']);span=next((s for s in spans if s['call_sha256']==value['worker_call_sha256']),None)
        root=next((root for root in weight_data['roots'] if root['call_sha256']==value['weight_call_sha256'] and root['factory_seq']==value['weight_event_seq'] and root['model_object_sha256']==value['model_object_sha256']),None)
        if not (target and row['seq']==target['seq']+1 and target['event']=='WEIGHT_ITERATION'
                and target['payload']['phase']=='FACTORY' and target['payload']['point']=='AutoWeightsLoader._load_module'
                and value['weight_payload_sha256']==sensor.sha(sensor.canonical(target['payload']))
                and value['weight_call_sha256']==target['payload']['call_sha256'] and value['model_object_sha256']==target['payload']['model_object_sha256']):
            raise ValueError('WORKER_STARTUP_ROOT_LINK_SUBSTITUTED')
        matched=bool(span and root and root['source_verified'] and contract.fingerprint(value['model_object_sha256'])
            and span['start_seq']<target['seq'] and span['end_seq'] is not None and row['seq']<span['end_seq']
            and span['outer_call_pair_verified'] and span['source_verified'] and target['payload']['source_verified'])
        if not matched:missing.append('worker_startup_root_call_pair_unavailable')
        models.append({'worker_call_sha256':value['worker_call_sha256'],'worker_point':span['point'] if span else None,
            'worker_start_seq':span['start_seq'] if span else None,'worker_end_seq':span['end_seq'] if span else None,
            'root_event_seq':target['seq'],'link_event_seq':row['seq'],'root_load_sha256':value['weight_call_sha256'],
            'model_object_sha256':value['model_object_sha256'],'same_startup_model_root_candidate':matched,**{key:False for key in sensor.TRUTH}})
    if not closes:missing.append('worker_startup_observer_close_unavailable')
    if len(closes)>1:missing.append('worker_startup_observer_close_ambiguous')
    if any(not r['payload']['result']['complete'] or r['payload']['result']['collection_gap'] for r in closes):missing.append('worker_startup_observer_close_incomplete')
    if not events:missing.append('worker_startup_method_observation_unavailable')
    if not links:missing.append('worker_startup_model_root_link_unavailable')
    missing+=['inner_method_body_entry_and_GPU_completion_unverified','actual_worker_assignment_and_READY_send_unverified','approval_and_inspection_authority_unverified']
    return {'events':events,'spans':spans,'model_candidates':models,'close_events':closes,'missing':sorted(set(missing))}


def project(packet,prepared):
    events=[];spans=[];models=[];missing=[];closed=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=sensor.PROFILE:continue
        context=records[0]['payload']['context'];data=journal(records,prepared,context)
        bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        for row in data['events']:events.append({'recording':number,'event_seq':row['seq'],'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,**row['payload']})
        spans.extend({'recording':number,'process_binding_verified':bound,**row} for row in data['spans'])
        models.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,**row} for row in data['model_candidates'])
        closed.extend({'recording':number,'event_seq':row['seq'],**row['payload']} for row in data['close_events']);missing+=data['missing']
    return {'schema':'guard-worker-startup-projection/1','recording_profile':sensor.PROFILE,'events':events,'spans':spans,
        'model_candidates':models,'close_events':closed,'missing':sorted(set(missing)),**{key:False for key in sensor.TRUTH}}


def attach_configured_models(trials,projection):
    for trial in trials:
        for model in trial['configured_model_candidates']:
            locator=model['native_locator']
            model['worker_startup_candidates']=[row for row in projection['model_candidates']
                if row['recording']==locator['recording'] and row['model_object_sha256']==model['session_model_object_sha256']
                and row['root_event_seq']<row['link_event_seq']<locator['forward_seq']
                and row['same_startup_model_root_candidate'] and row['process_binding_verified']]
    return trials



def project_iterations(packet):
    return _project_iterations(packet,sensor.PROFILE)


def _project_iterations(packet,profile,*,upper_anchors=False):
    samples=[];models=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=profile:continue
        data=weights.journal([row for row in records if row['event']=='WEIGHT_ITERATION'])
        context=records[0]['payload']['context'];bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        samples.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,**fact} for fact in data['facts'])
        for root in data['roots']:
            facts=[fact for fact in data['facts'] if fact['root_load_sha256']==root['call_sha256'] and fact['model_object_sha256']==root['model_object_sha256']
                and fact['factory_present'] and fact['source_pair_verified'] and fact['root_load_active_observed'] and root['factory_seq']<fact['end_seq']]
            callbacks=[fact for fact in facts if fact['parameter_callback_returned_observed']]
            models.append({'recording':number,**root,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,
                'loader_call_model_link_observed':bool(bound and root['loader_call_sha256'] and root['loader_model_sha256']==root['model_object_sha256'] and root['model_object_sha256']),
                'same_process_iteration_observed':bool(bound and root['source_verified'] and facts and root['model_object_sha256']),
                'step_yields_observed':len(facts),'parameter_callback_returns_observed':len(callbacks),'source_events':[[number,fact['end_seq']] for fact in facts[:64]],
                'source_event_locators_complete':len(facts)<=64,'recording_closed':capture.get('summary',{}).get('recording_closed') is True,
                **{key:False for key in weights.TRUTH},'scope_complete':False})
            if upper_anchors:
                models[-1]['first_source_event']=[number,facts[0]['end_seq']] if facts else None
                models[-1]['last_source_event']=[number,facts[-1]['end_seq']] if facts else None
        missing+=data['missing']
    missing+=['worker_FD_read_and_range_unverified','tensor_storage_and_backend_apply_completion_unverified','worker_assignment_and_rank_shard_inventory_unverified','approval_and_inspection_authority_unverified']
    if not samples:missing.append('source_bound_weight_iteration_unavailable')
    return {'schema':'guard-weight-iteration-projection/1','samples':samples,'model_candidates':models,'missing':sorted(set(missing)),**{key:False for key in weights.TRUTH},'scope_complete':False}


def project_mappings(packet,prepared):
    return _project_mappings(packet,prepared,sensor.PROFILE)


def _project_mappings(packet,prepared,profile):
    samples=[];contexts=[];missing=[]
    for number,recording in enumerate((packet or {}).get('recordings',[])):
        capture=recording.get('capture') or {};records=capture.get('records',[])
        if not records or records[0]['payload'].get('profile')!=profile:continue
        data=mapping.journal([row for row in records if row['event']=='SAFE_OPEN_MAPPING'],prepared,[row for row in records if row['event']=='WEIGHT_ITERATION'])
        context=records[0]['payload']['context'];bound=bool(capture.get('process_context_verified') and recording.get('session_owner_match') and recording.get('listed_file_identity_match'))
        samples.extend({'recording':number,'pid':context['pid'],'start_ticks':context['start_ticks'],'process_binding_verified':bound,
            'same_loader_model_candidate':bool(bound and row['loader_model_candidate_observed'] and row['iterator_pair_verified'] and row['library_files_verified_at_open']),**row} for row in data['samples'])
        contexts.extend({'recording':number,**row} for row in data['contexts']);missing+=data['missing']
    missing+=['mapped_file_does_not_prove_read_or_tensor_use','native_loaded_memory_and_worker_assignment_unverified','approval_and_inspection_authority_unverified']
    if not samples:missing.append('safe_open_mapping_observation_unavailable')
    return {'schema':'guard-safe-open-mapping-projection/1','samples':samples,'contexts':contexts,'missing':sorted(set(missing)),**{key:False for key in mapping.sensor.TRUTH}}



def validate_limit(value):
    try:
        validate_common(value,LIMIT_FIELDS,sensor.LIMIT_SCHEMA)
        if not contract.fingerprint(value['session_sha256']) or value['bound']!='calls' or type(value['limit']) is not int or value['limit']!=sensor.MAX_CALLS:raise ValueError
        return value
    except (ValueError,KeyError,TypeError,OverflowError):raise ValueError('WORKER_STARTUP_LIMIT_INVALID') from None



