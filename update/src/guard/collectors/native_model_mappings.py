"""A2 exact native18 scoped context candidates over A1 admitted journals.

Mapping coordinates are non-atomic hints. No read, tensor application,
worker, approval, mapping continuity or release is established here.
"""
from pathlib import Path
from guard.adapters import vllm_safe_open_mapping_metadata as common
from guard.adapters import vllm_safe_open_mapping_sensor as sensor
from guard.models import canonical_json,sha256_hex

VERSION='0.1.0'
SCHEMA='guard-native-scoped-mapping-candidates/1'
SOURCE_PINS={
    'vllm_safe_open_mapping_metadata.py':'833030d5d24aad97bc7ec0050ef105488daeb0f2023987d75bb1d8ffdbc0be29',
    'vllm_safe_open_mapping_sensor.py':'8984896304147ac6c47b3a3f84a56b2831df0f302e52fd95d8f9ccf7d103f92d',
    'vllm_file_read_metadata.py':'b8c08652687eaf03a4749f85f099b0bcf3a09e2ec27a7e96eb767efdff346c93',
    'vllm_file_read_sensor.py':'7729661d6611e40f527760b3029822bb9cb690f8a48931aa9e9c9fb00cab08e5',
    'vllm_weight_iteration_metadata.py':'f8ced901b1b1c7d918f96c9ef8ec0899696c2d161efbaefdd43dbedd340f04c0',
    'vllm_weight_iteration_sensor.py':'e7494edaa0a512201652228acc73ac13621cb40d938e6f23b6d4eaba73d05bc6',
}
MAX_CANDIDATES=128
MAX_EVENTS=64
MAX_COORDINATES=128
MAX_UNJOINED=64
MAX_PATH_LOCATORS=64
TRUTH=(*sensor.TRUTH,'reference_authority_verified','per_file_consumption_verified',
       'actual_model_generation_verified','full_inventory_verified','mapping_continuity_verified')


def _require(value):
    if not value:raise ValueError('NATIVE_MODEL_MAPPING_INPUT_INVALID')


def _source_gate():
    root=Path(__file__).resolve().parents[1]/'adapters'
    try:matched=all(sha256_hex((root/name).read_bytes())==expected for name,expected in SOURCE_PINS.items())
    except OSError:matched=False
    if not matched:raise ValueError('NATIVE_MODEL_MAPPING_COMMON_SOURCE_MISMATCH')


def _inputs(packet,prepared):
    _require(type(prepared) is dict and prepared.get('profile')==sensor.PROFILE)
    scope=prepared['model_read_scope'];asset=prepared['assets']['model-read-scope.json']
    _require(type(scope) is dict and type(asset) is dict and sensor.fingerprint(asset['sha256'])
             and scope['nonsecret_model_weights'] is True and scope['data_origin'] in {'EXAMPLE','DECLARED'})
    _require(all(sensor.fingerprint(scope[k]) for k in ('target_sha256','model_sha256','revision_sha256','inventory_sha256','manifest_bytes_sha256')))
    files=scope['files'];_require(type(files) is list and 0<len(files)<=256);allowed={}
    for row in files:
        _require(type(row) is dict and set(row)=={'normalized_path_sha256','reference_sha256','bytes'}
            and sensor.fingerprint(row['normalized_path_sha256']) and sensor.fingerprint(row['reference_sha256'])
            and type(row['bytes']) is int and 0<=row['bytes']<2**63 and row['normalized_path_sha256'] not in allowed)
        allowed[row['normalized_path_sha256']]=row
    _require(type(packet) is dict and type(packet.get('recordings')) is list and len(packet['recordings'])<=32)
    missing=[]
    for recording in packet['recordings']:
        _require(type(recording) is dict and 'capture' in recording)
        if recording['capture'] is None:
            _require(recording.get('session_owner_match') is False and recording.get('listed_file_identity_match') is False)
            missing.append('mapping_recording_capture_unavailable');continue
        capture=recording['capture'];_require(type(capture) is dict)
        _require(type(capture.get('summary',{})) is dict)
        _require(all(v is None or type(v) is bool for v in (capture.get('process_context_verified'),recording.get('session_owner_match'),recording.get('listed_file_identity_match'))))
        rows=capture['records'];_require(type(rows) is list and len(rows)<=sensor.MAX_ROWS and len(canonical_json(rows))<=sensor.MAX_BYTES)
        if not rows:missing.append('mapping_recording_rows_unavailable');continue
        _require(rows[0]['event']=='START' and rows[0]['payload']['profile']==sensor.PROFILE)
        context=rows[0]['payload']['context']
        _require(type(context['pid']) is int and context['pid']>0 and type(context['start_ticks']) is int and context['start_ticks']>=0)
        previous=-1
        for row in rows:
            _require(type(row) is dict and type(row.get('seq')) is int and previous<row['seq']<2**63)
            previous=row['seq']
    return scope,allowed,missing


def _snapshot(value):
    return None if value is None else {k:value[k] for k in ('status','complete','non_atomic')}|{'match_count':len(value['matches'])}


def _analyze(packet,prepared):
    scope,allowed,missing=_inputs(packet,prepared)
    mapped=common.project(packet,prepared);iterations=common.project_iterations(packet)
    roots={(r['recording'],r['call_sha256']):r for r in iterations['model_candidates']}
    model_roots={}
    for root in roots.values():
        if root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']:
            model_roots.setdefault((root['recording'],root['model_object_sha256']),set()).add(root['call_sha256'])
    joined={};eligible_contexts=set();unjoined=[];unjoined_count=0
    for row in mapped['samples']:
        root=roots.get((row['recording'],row['root_load_sha256']))
        if not row['library_files_verified_at_open']:reason='safe_open_library_file_provenance_unavailable'
        elif not row['process_binding_verified']:reason='safe_open_process_binding_unverified'
        elif not row['iterator_pair_verified']:reason='safe_open_iterator_pair_unavailable'
        elif root is None:reason='safe_open_loader_root_unavailable'
        elif not (root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']):reason='safe_open_loader_root_binding_unverified'
        elif not (row['same_loader_model_candidate'] and root['pid']==row['pid'] and root['start_ticks']==row['start_ticks']
                and root['factory_seq']<row['event_seq'] and row['model_object_sha256']==root['model_object_sha256']==row['loader_model_sha256']==root['loader_model_sha256']
                and row['loader_call_sha256']==root['loader_call_sha256']):reason='safe_open_loader_model_generation_mismatch'
        else:reason=None
        if reason is not None:
            unjoined_count+=1;missing.append(reason)
            if len(unjoined)<MAX_UNJOINED:unjoined.append({'event':[row['recording'],row['event_seq']],'phase':row['phase'],'reason':reason})
            continue
        key=(row['recording'],row['context_sha256'])
        eligible_contexts.add(key)
        if key not in joined:
            if len(joined)>=MAX_CANDIDATES:missing.append('mapping_candidate_output_bound');continue
            joined[key]={'root':root,'rows':[]}
        joined[key]['rows'].append(row)
    candidates=[];groups={}
    for key,value in joined.items():
        root=value['root'];rows=value['rows'];first=rows[0];events=[];coordinates=[];coordinate_count=0
        for row in rows:
            locator=[row['recording'],row['event_seq']]
            if len(events)<MAX_EVENTS:
                events.append({'event':locator,'phase':row['phase'],'tensor_return_observed':row['tensor_return_observed'],
                    'operation_index':row['operation_index'],'iterator_pair_verified':row['iterator_pair_verified'],
                    'path_identity_unchanged_observed':row['path_identity_unchanged_observed'],'exception_kind':row['exception_kind'],
                    'before':_snapshot(row['before']),'after':_snapshot(row['after'])})
            for side in ('before','after'):
                for region in (row[side] or {}).get('matches',[]):
                    coordinate_count+=1
                    if len(coordinates)<MAX_COORDINATES:coordinates.append({'event':locator,'side':side,**region})
        reference=allowed[first['normalized_path_sha256']]
        complete_events=len(rows)<=MAX_EVENTS;complete_coordinates=coordinate_count<=MAX_COORDINATES
        if not complete_events:missing.append('mapping_context_event_output_bound')
        if not complete_coordinates:missing.append('mapping_coordinate_output_bound')
        if any(not row['path_identity_unchanged_observed'] for row in rows):missing.append('mapping_path_identity_change_observed')
        capture=packet['recordings'][key[0]]['capture']
        candidate={'recording':key[0],'pid':first['pid'],'start_ticks':first['start_ticks'],'context_sha256':key[1],
            'root_event':[key[0],root['factory_seq']],'root_load_sha256':root['call_sha256'],'root_last_seq':root['last_seq'],
            'observed_model_object_sha256':first['model_object_sha256'],'loader_call_sha256':first['loader_call_sha256'],
            'iterator_call_sha256':first['call_sha256'],'scope_sha256':first['scope_sha256'],
            'normalized_path_sha256':first['normalized_path_sha256'],'declared_reference_sha256':reference['reference_sha256'],'declared_reference_bytes':reference['bytes'],
            'same_process_safe_open_context_observed':True,'library_files_verified_at_open':True,
            'constructor_return_observed':any(r['phase']=='OPEN_RETURN' for r in rows),'enter_return_observed':any(r['phase']=='ENTER_RETURN' for r in rows),
            'tensor_boundary_return_observed':any(r['phase']=='TENSOR_RETURN' for r in rows),'context_exit_return_observed':any(r['phase']=='EXIT_RETURN' for r in rows),
            'context_exception_observed':any(r['phase'].endswith('RAISE') for r in rows),
            'mapping_coordinates_observed':coordinate_count>0,'mapping_coordinate_observation_count':coordinate_count,
            'mapping_coordinate_locators_complete':complete_coordinates,'mapping_coordinates':coordinates,
            'snapshots_complete_observed':all(r[side] and r[side]['complete'] for r in rows for side in ('before','after')),
            'all_path_identity_checks_unchanged_observed':all(r['path_identity_unchanged_observed'] for r in rows),
            'events':events,'source_event_count':len(rows),'source_event_locators_complete':complete_events,
            'first_source_event':[key[0],rows[0]['event_seq']],'last_source_event':[key[0],rows[-1]['event_seq']],
            'source_events':[[key[0],r['event_seq']] for r in rows[:MAX_EVENTS]],
            'recording_closed':capture.get('summary',{}).get('recording_closed') is True,
            'root_operation_pairs_complete':root['operation_pairs_complete'],'root_exhausted_observed':root['root_exhausted_observed'],
            'observed_loader_root_count':len(model_roots[(key[0],first['model_object_sha256'])]),**{k:False for k in TRUTH}}
        candidates.append(candidate)
        group=groups.setdefault((key[0],root['call_sha256']),{'root_event':candidate['root_event'],'root_load_sha256':root['call_sha256'],
            'observed_model_object_sha256':first['model_object_sha256'],'loader_call_sha256':first['loader_call_sha256'],'paths':set(),'context_candidate_count':0})
        group['paths'].add(first['normalized_path_sha256']);group['context_candidate_count']+=1
    grouped=[]
    for group in groups.values():
        paths=group.pop('paths');unobserved=sorted(allowed.keys()-paths)
        grouped.append({**group,'declared_path_count':len(allowed),'paths_with_safe_open_context_candidate_count':len(paths),
            'declared_paths_without_context_candidate_count':len(unobserved),'declared_paths_without_context_candidate_sha256':unobserved[:MAX_PATH_LOCATORS],
            'unobserved_path_locators_complete':len(unobserved)<=MAX_PATH_LOCATORS,**{k:False for k in TRUTH}})
        if unobserved:missing.append('declared_scope_context_candidates_unavailable')
    if not candidates:missing.append('same_loader_model_mapping_context_candidate_unavailable')
    return {'schema':SCHEMA,'version':VERSION,'profile':sensor.PROFILE,'candidates':candidates,'groups':grouped,
        'candidate_context_count':len(eligible_contexts),'candidate_context_locators_complete':len(eligible_contexts)<=MAX_CANDIDATES,
        'unjoined_event_count':unjoined_count,'unjoined_events':unjoined,'unjoined_locators_complete':unjoined_count<=MAX_UNJOINED,
        'declared_scope':{'scope_sha256':prepared['assets']['model-read-scope.json']['sha256'],'declared_model_id_sha256':scope['model_sha256'],
            'declared_revision_sha256':scope['revision_sha256'],'declared_inventory_sha256':scope['inventory_sha256'],
            'manifest_bytes_sha256':scope['manifest_bytes_sha256'],'reference_origin':scope['data_origin'],'declared_path_count':len(allowed)},
        'missing':sorted(set(missing+mapped['missing']+iterations['missing']+['mapping_coordinates_do_not_prove_continuity_or_release'])),
        'security':'UNKNOWN',**{k:False for k in TRUTH}}


def analyze(packet,prepared):
    _source_gate()
    try:return _analyze(packet,prepared)
    except (ValueError,KeyError,TypeError,UnicodeError,RecursionError):
        raise ValueError('NATIVE_MODEL_MAPPING_INPUT_INVALID') from None
