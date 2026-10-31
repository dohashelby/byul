"""A2 native17 declared-scope read candidates; no worker/model approval verdict.

Only the existing A1 admitted capture_session packet and validated prepared
manifest are inputs. Public shared journals retain source/code/runtime/session
and FD semantics. No model file, transport, selector or decoder is opened here.
"""
from pathlib import Path
from guard.adapters import vllm_file_read_metadata as common
from guard.adapters import vllm_file_read_sensor as sensor
from guard.models import canonical_json,sha256_hex

VERSION='0.1.0'
SCHEMA='guard-native-scoped-file-read-candidates/1'
SOURCE_PINS={
    'vllm_file_read_metadata.py':'b8c08652687eaf03a4749f85f099b0bcf3a09e2ec27a7e96eb767efdff346c93',
    'vllm_file_read_sensor.py':'e32c0a53e810735f9eaca1a648397af49ca259d264020fd0f99193c401b2697d',
    'vllm_weight_iteration_metadata.py':'f8ced901b1b1c7d918f96c9ef8ec0899696c2d161efbaefdd43dbedd340f04c0',
    'vllm_weight_iteration_sensor.py':'6048767399a893605c693c49f89a3f97360ec081b59169b7ac29fb08ac9e7648',
}
MAX_CANDIDATES=256
MAX_UNJOINED=64
MAX_PATH_LOCATORS=64
TRUTH=(*common.TRUTH,'reference_authority_verified','per_file_consumption_verified','actual_model_generation_verified','full_inventory_verified','scope_complete')


def _require(value):
    if not value:raise ValueError('NATIVE_MODEL_FILE_READ_INPUT_INVALID')


def _source_gate():
    root=Path(__file__).resolve().parents[1]/'adapters'
    try:matched=all(sha256_hex((root/name).read_bytes())==expected for name,expected in SOURCE_PINS.items())
    except OSError:matched=False
    if not matched:raise ValueError('NATIVE_MODEL_FILE_READ_COMMON_SOURCE_MISMATCH')


def _inputs(packet,prepared):
    _require(type(prepared) is dict and prepared.get('profile')==sensor.PROFILE)
    scope=prepared['model_read_scope'];asset=prepared['assets']['model-read-scope.json']
    _require(type(scope) is dict and type(asset) is dict and sensor.fingerprint(asset['sha256']))
    _require(scope['nonsecret_model_weights'] is True and scope['data_origin'] in {'EXAMPLE','DECLARED'}
        and type(scope['hash_model_bytes']) is bool and type(scope['max_hash_bytes']) is int and 0<scope['max_hash_bytes']<=sensor.MAX_HASH_BYTES)
    _require(all(sensor.fingerprint(scope[k]) for k in ('target_sha256','model_sha256','revision_sha256','inventory_sha256','manifest_bytes_sha256')))
    files=scope['files'];_require(type(files) is list and 0<len(files)<=256)
    allowed={}
    for row in files:
        _require(type(row) is dict and set(row)=={'normalized_path_sha256','reference_sha256','bytes'}
            and sensor.fingerprint(row['normalized_path_sha256']) and sensor.fingerprint(row['reference_sha256'])
            and type(row['bytes']) is int and 0<=row['bytes']<2**63 and row['normalized_path_sha256'] not in allowed)
        allowed[row['normalized_path_sha256']]=row
    _require(type(packet) is dict and type(packet.get('recordings')) is list and len(packet['recordings'])<=32)
    missing=[]
    for index,recording in enumerate(packet['recordings']):
        _require(type(recording) is dict and 'capture' in recording)
        if recording['capture'] is None:
            _require(recording.get('session_owner_match') is False and recording.get('listed_file_identity_match') is False)
            missing.append('model_file_recording_capture_unavailable');continue
        capture=recording['capture'];_require(type(capture) is dict)
        _require(all(v is None or type(v) is bool for v in (capture.get('process_context_verified'),recording.get('session_owner_match'),recording.get('listed_file_identity_match'))))
        rows=capture['records'];_require(type(rows) is list and len(rows)<=sensor.MAX_ROWS and len(canonical_json(rows))<=sensor.MAX_BYTES)
        if not rows:missing.append('model_file_recording_rows_unavailable');continue
        _require(rows[0]['event']=='START' and rows[0]['payload']['profile']==sensor.PROFILE)
        context=rows[0]['payload']['context'];_require(type(context['pid']) is int and context['pid']>0 and type(context['start_ticks']) is int and context['start_ticks']>=0)
        seen=set()
        for row in rows:
            _require(type(row) is dict and type(row.get('seq')) is int and 0<=row['seq']<2**63 and row['seq'] not in seen)
            seen.add(row['seq'])
    return scope,allowed,missing


def _analyze(packet,prepared):
    scope,allowed,missing=_inputs(packet,prepared)
    reads=common.project(packet,prepared)
    iterations=common.project_iterations(packet)
    roots={(root['recording'],root['call_sha256']):root for root in iterations['model_candidates']}
    model_roots={}
    for root in roots.values():
        if root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']:
            model_roots.setdefault((root['recording'],root['model_object_sha256']),set()).add(root['call_sha256'])
    candidates=[];unjoined=[];unjoined_count=0;groups={}
    for row in reads['samples']:
        root=roots.get((row['recording'],row['root_load_sha256']))
        if row['phase']!='READ_RETURN' or not row['source_fd_read_return_observed']:reason='read_return_unavailable'
        elif not row['process_binding_verified']:reason='process_binding_unverified'
        elif not row['source_sequence_verified']:reason='read_source_sequence_unverified'
        elif not row['iterator_step_link_observed']:reason='read_iterator_step_link_unavailable'
        elif root is None:reason='loader_root_unavailable'
        elif not (root['process_binding_verified'] and root['source_verified'] and root['loader_call_model_link_observed']):reason='loader_root_binding_unverified'
        elif not (row['model_object_sha256']==root['model_object_sha256']==row['loader_model_sha256']==root['loader_model_sha256']
                and row['loader_call_sha256']==root['loader_call_sha256'] and root['factory_seq']<row['read_seq']):reason='loader_model_generation_mismatch'
        else:reason=None
        if reason is not None:
            unjoined_count+=1;missing.append(reason)
            if len(unjoined)<MAX_UNJOINED:unjoined.append({'read_event':[row['recording'],row['read_seq']],'phase':row['phase'],'reason':reason})
            continue
        if len(candidates)>=MAX_CANDIDATES:missing.append('model_file_candidate_output_bound');continue
        reference=allowed[row['normalized_path_sha256']]
        complete_comparison=bool(row['full_fd_read_return_observed'] and row['hash_status']=='complete')
        events=[[row['recording'],row[key]] for key in ('open_seq','read_seq','close_seq') if row[key] is not None]
        candidate={'recording':row['recording'],'pid':row['pid'],'start_ticks':row['start_ticks'],
            'root_event':[row['recording'],root['factory_seq']],'root_last_seq':root['last_seq'],
            'root_load_sha256':row['root_load_sha256'],'observed_model_object_sha256':row['model_object_sha256'],
            'loader_call_sha256':row['loader_call_sha256'],'handle_sha256':row['handle_sha256'],'source_events':events,
            'observed_loader_root_count':len(model_roots[(row['recording'],row['model_object_sha256'])]),
            'multiple_loader_roots_observed':len(model_roots[(row['recording'],row['model_object_sha256'])])>1,
            'normalized_path_sha256':row['normalized_path_sha256'],'reference_sha256':reference['reference_sha256'],'reference_bytes':reference['bytes'],
            'read_return_bytes':row['returned_bytes'],'read_return_bytes_sha256':row['returned_bytes_sha256'],'hash_status':row['hash_status'],
            'fd_metadata_stable_observed':row['fd_metadata_stable_observed'],'path_fd_metadata_match_observed':row['path_fd_metadata_match_observed'],
            'full_fd_read_return_observed':row['full_fd_read_return_observed'],'declared_reference_bytes_match_observed':row['declared_reference_bytes_match_observed'],
            'declared_reference_comparison_observed':complete_comparison,'declared_reference_mismatch_observed':complete_comparison and not row['declared_reference_bytes_match_observed'],
            'close_observed':row['close_observed'],'recording_closed':row['recording_closed'],
            'root_operation_pairs_complete':root['operation_pairs_complete'],'root_exhausted_observed':root['root_exhausted_observed'],
            **{key:False for key in TRUTH}}
        candidates.append(candidate)
        key=(row['recording'],root['call_sha256'],root['model_object_sha256'],root['loader_call_sha256'])
        group=groups.setdefault(key,{'root_event':candidate['root_event'],'root_load_sha256':key[1],
            'observed_model_object_sha256':key[2],'loader_call_sha256':key[3],
            'observed_loader_root_count':candidate['observed_loader_root_count'],'multiple_loader_roots_observed':candidate['multiple_loader_roots_observed'],
            'paths':set(),'read_return_observations':0})
        group['paths'].add(row['normalized_path_sha256']);group['read_return_observations']+=1
    grouped=[]
    for group in groups.values():
        paths=group.pop('paths');unobserved=sorted(allowed.keys()-paths)
        grouped.append({**group,'declared_path_count':len(allowed),'paths_with_read_candidate_count':len(paths),
            'declared_paths_without_candidate_count':len(unobserved),'declared_paths_without_candidate_sha256':unobserved[:MAX_PATH_LOCATORS],
            'unobserved_path_locators_complete':len(unobserved)<=MAX_PATH_LOCATORS,**{key:False for key in TRUTH}})
        if unobserved:missing.append('declared_scope_file_read_candidates_unavailable')
    failures=[{'open_event':[row['recording'],row['open_seq']],'normalized_path_sha256':row['normalized_path_sha256'],
        'process_binding_verified':row['process_binding_verified'],'exception_kind':row['exception_kind']} for row in reads['open_failures'][:MAX_UNJOINED]]
    if not candidates:missing.append('same_loader_model_file_read_candidate_unavailable')
    return {'schema':SCHEMA,'version':VERSION,'profile':sensor.PROFILE,'candidates':candidates,'groups':grouped,
        'unjoined_read_count':unjoined_count,'unjoined_reads':unjoined,'unjoined_locators_complete':unjoined_count<=MAX_UNJOINED,
        'open_failure_count':len(reads['open_failures']),'open_failures':failures,'open_failure_locators_complete':len(reads['open_failures'])<=MAX_UNJOINED,
        'declared_scope':{'scope_sha256':prepared['assets']['model-read-scope.json']['sha256'],
            'declared_model_id_sha256':scope['model_sha256'],'declared_revision_sha256':scope['revision_sha256'],
            'declared_inventory_sha256':scope['inventory_sha256'],'manifest_bytes_sha256':scope['manifest_bytes_sha256'],
            'reference_origin':scope['data_origin'],'declared_path_count':len(allowed)},
        'missing':sorted(set(missing+reads['missing']+iterations['missing'])),'security':'UNKNOWN',**{key:False for key in TRUTH}}


def analyze(packet,prepared):
    _source_gate()
    try:return _analyze(packet,prepared)
    except (ValueError,KeyError,TypeError,UnicodeError,RecursionError):
        raise ValueError('NATIVE_MODEL_FILE_READ_INPUT_INVALID') from None
