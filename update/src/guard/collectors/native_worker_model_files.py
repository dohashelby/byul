"""Exact19 startup/root/scoped-context correlation, never actual worker proof."""
from pathlib import Path
from guard.adapters import vllm_worker_startup_metadata as common
from guard.adapters import vllm_worker_startup_sensor as sensor
from guard.adapters import vllm_safe_open_mapping_sensor as mapping
from guard.models import canonical_json,sha256_hex

VERSION='0.1.0'
SCHEMA='guard-native-startup-file-context-candidates/1'
SOURCE_PINS={
    'vllm_worker_startup_metadata.py':'d122b34e03da52f78f425bca84660b302cac062a889dfecccf8c5393091c25cc',
    'vllm_worker_startup_sensor.py':'2fb8b6386bc59e236c9227251da05f8e8704e08a935dcff27a8c18bc57ed91f4',
    'vllm_worker_startup_contract.py':'6788df2f4c6f04f46e681eb5eab48631e2ba037754d26166bb02341e4b592950',
    'vllm_safe_open_mapping_metadata.py':'833030d5d24aad97bc7ec0050ef105488daeb0f2023987d75bb1d8ffdbc0be29',
    'vllm_safe_open_mapping_sensor.py':'dd9e75f2752e042b43527ddd9cf13cc06a3c82fd4c5ba73cfe7114df7dcafaf1',
    'vllm_weight_iteration_metadata.py':'f8ced901b1b1c7d918f96c9ef8ec0899696c2d161efbaefdd43dbedd340f04c0',
    'vllm_weight_iteration_sensor.py':'6048767399a893605c693c49f89a3f97360ec081b59169b7ac29fb08ac9e7648',
    'vllm_file_read_sensor.py':'e32c0a53e810735f9eaca1a648397af49ca259d264020fd0f99193c401b2697d',
}
MAX_CANDIDATES=128
MAX_LOCATORS=64
TRUTH=tuple(dict.fromkeys((*sensor.TRUTH,*mapping.TRUTH,'reference_authority_verified',
    'per_file_consumption_verified','actual_model_generation_verified','full_inventory_verified','mapping_continuity_verified')))


def _require(value):
    if not value:raise ValueError('NATIVE_WORKER_FILE_INPUT_INVALID')


def _source_gate():
    root=Path(__file__).resolve().parents[1]/'adapters'
    try:matched=all(sha256_hex((root/name).read_bytes())==expected for name,expected in SOURCE_PINS.items())
    except OSError:matched=False
    if not matched:raise ValueError('NATIVE_WORKER_FILE_COMMON_SOURCE_MISMATCH')


def _inputs(packet,prepared):
    _require(type(prepared) is dict and prepared.get('profile')==sensor.PROFILE)
    session=prepared['session'];_require(type(session) is str and len(session)==32 and all(c in '0123456789abcdef' for c in session))
    scope=prepared['model_read_scope'];asset=prepared['assets']['model-read-scope.json']
    _require(type(scope) is dict and type(asset) is dict and mapping.fingerprint(asset['sha256']) and scope['nonsecret_model_weights'] is True and scope['data_origin'] in {'EXAMPLE','DECLARED'})
    files=scope['files'];_require(type(files) is list and 0<len(files)<=256);allowed={}
    for row in files:
        _require(type(row) is dict and set(row)=={'normalized_path_sha256','reference_sha256','bytes'} and mapping.fingerprint(row['normalized_path_sha256'])
            and mapping.fingerprint(row['reference_sha256']) and type(row['bytes']) is int and 0<=row['bytes']<2**63 and row['normalized_path_sha256'] not in allowed)
        allowed[row['normalized_path_sha256']]=row
    _require(type(packet) is dict and type(packet.get('recordings')) is list and len(packet['recordings'])<=32)
    missing=[]
    for recording in packet['recordings']:
        _require(type(recording) is dict and 'capture' in recording)
        if recording['capture'] is None:
            _require(recording.get('session_owner_match') is False and recording.get('listed_file_identity_match') is False)
            missing.append('startup_file_recording_capture_unavailable');continue
        capture=recording['capture'];_require(type(capture) is dict and type(capture.get('summary',{})) is dict)
        _require(all(v is None or type(v) is bool for v in (capture.get('process_context_verified'),recording.get('session_owner_match'),recording.get('listed_file_identity_match'))))
        rows=capture['records'];_require(type(rows) is list and len(rows)<=2048 and len(canonical_json(rows))<=2*1024**2)
        if not rows:missing.append('startup_file_recording_rows_unavailable');continue
        _require(rows[0]['event']=='START' and rows[0]['payload']['profile']==sensor.PROFILE)
        context=rows[0]['payload']['context'];_require(type(context['pid']) is int and context['pid']>0 and type(context['start_ticks']) is int and context['start_ticks']>=0)
        previous=-1
        for row in rows:
            _require(type(row) is dict and type(row.get('seq')) is int and previous<row['seq']<2**63);previous=row['seq']
    return allowed,missing


def _analyze(packet,prepared):
    allowed,missing=_inputs(packet,prepared)
    startups=common.project(packet,prepared);weights=common.project_iterations(packet);maps=common.project_mappings(packet,prepared)
    roots={(r['recording'],r['call_sha256']):r for r in weights['model_candidates']}
    joined={};eligible=set();unjoined=[];unjoined_count=0
    spans={(r['recording'],r['call_sha256']):r for r in startups['spans']}
    for row in maps['samples']:
        root=roots.get((row['recording'],row['root_load_sha256']))
        matching=[s for s in startups['model_candidates'] if s['recording']==row['recording'] and s['root_load_sha256']==row['root_load_sha256']
            and s['same_startup_model_root_candidate'] and s['process_binding_verified']]
        if not row['same_loader_model_candidate']:reason='startup_file_mapping_attribution_unavailable'
        elif root is None or not root['loader_call_model_link_observed']:reason='startup_file_loader_root_unavailable'
        elif len(matching)!=1:reason='startup_file_unique_startup_root_link_unavailable'
        else:
            start=matching[0]
            valid=(root['pid']==start['pid']==row['pid'] and root['start_ticks']==start['start_ticks']==row['start_ticks']
                and root['factory_seq']==start['root_event_seq'] and start['link_event_seq']==root['factory_seq']+1
                and root['model_object_sha256']==start['model_object_sha256']==row['model_object_sha256']==row['loader_model_sha256']==root['loader_model_sha256']
                and root['loader_call_sha256']==row['loader_call_sha256']
                and start['worker_start_seq']<start['root_event_seq']<start['link_event_seq']<=row['event_seq']<start['worker_end_seq'])
            reason=None if valid else 'startup_file_source_or_generation_boundary_mismatch'
        if reason:
            missing.append(reason);unjoined_count+=1
            if len(unjoined)<MAX_LOCATORS:unjoined.append({'event':[row['recording'],row['event_seq']],'phase':row['phase'],'reason':reason})
            continue
        key=(row['recording'],start['worker_call_sha256'],root['call_sha256'],row['context_sha256']);eligible.add(key)
        if key not in joined:
            if len(joined)>=MAX_CANDIDATES:missing.append('startup_file_candidate_output_bound');continue
            joined[key]={'start':start,'root':root,'rows':[]}
        joined[key]['rows'].append(row)
    candidates=[]
    for key,value in joined.items():
        start,root,rows=value['start'],value['root'],value['rows'];first=rows[0];ref=allowed[first['normalized_path_sha256']];span=spans[(key[0],key[1])]
        complete=len(rows)<=MAX_LOCATORS
        if not complete:missing.append('startup_file_event_locator_output_bound')
        candidates.append({'recording':key[0],'pid':first['pid'],'start_ticks':first['start_ticks'],'startup_call_sha256':key[1],
            'startup_point':start['worker_point'],'startup_call_event':[key[0],start['worker_start_seq']],'startup_terminal_event':[key[0],start['worker_end_seq']],
            'startup_return_observed':span['returned'],'startup_callback_method_pair_verified':span['callback_method_pair_verified'],
            'root_event':[key[0],root['factory_seq']],'root_link_event':[key[0],start['link_event_seq']],'root_load_sha256':key[2],
            'observed_model_object_sha256':first['model_object_sha256'],'loader_call_sha256':first['loader_call_sha256'],'context_sha256':key[3],
            'scope_sha256':first['scope_sha256'],'normalized_path_sha256':first['normalized_path_sha256'],
            'declared_reference_sha256':ref['reference_sha256'],'declared_reference_bytes':ref['bytes'],
            'same_startup_root_file_context_candidate':True,'first_mapping_event':[key[0],rows[0]['event_seq']],'last_mapping_event':[key[0],rows[-1]['event_seq']],
            'mapping_source_event_count':len(rows),'mapping_source_events':[[key[0],r['event_seq']] for r in rows[:MAX_LOCATORS]],'mapping_source_event_locators_complete':complete,
            'mapping_coordinates_observed':any((r[s] or {}).get('matches') for r in rows for s in ('before','after')),
            'snapshots_complete_observed':all(r[s] and r[s]['complete'] for r in rows for s in ('before','after')),
            'tensor_boundary_return_observed':any(r['tensor_return_observed'] for r in rows),'context_exit_return_observed':any(r['phase']=='EXIT_RETURN' for r in rows),
            'context_exception_observed':any(r['phase'].endswith('RAISE') for r in rows),'root_exhausted_observed':root['root_exhausted_observed'],
            'recording_closed':root['recording_closed'],**{k:False for k in TRUTH}})
    if not candidates:missing.append('startup_scoped_file_context_candidate_unavailable')
    return {'schema':SCHEMA,'version':VERSION,'profile':sensor.PROFILE,'candidates':candidates,
        'candidate_context_count':len(eligible),'candidate_context_locators_complete':len(eligible)<=MAX_CANDIDATES,
        'unjoined_mapping_event_count':unjoined_count,'unjoined_mapping_events':unjoined,'unjoined_locators_complete':unjoined_count<=MAX_LOCATORS,
        'declared_scope_sha256':prepared['assets']['model-read-scope.json']['sha256'],'declared_path_count':len(allowed),
        'missing':sorted(set(missing+startups['missing']+weights['missing']+maps['missing'])),
        'security':'UNKNOWN',**{k:False for k in TRUTH}}


def analyze(packet,prepared):
    _source_gate()
    try:return _analyze(packet,prepared)
    except (ValueError,KeyError,TypeError,UnicodeError,RecursionError):
        raise ValueError('NATIVE_WORKER_FILE_INPUT_INVALID') from None
