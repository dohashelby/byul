"""LIF09 Worker facts alongside MP claims, without cross-process promotion."""
from . import vllm_worker_initialization_sensor as sensor
from . import vllm_worker_startup_sensor as startup, vllm_worker_startup_metadata as metadata

SCHEMA = 'guard-worker-mp-initialization-projection/1'


def project(packet, prepared):
    if prepared.get('profile') != sensor.PROFILE:
        raise ValueError('WORKER_INITIALIZATION_PROFILE_UNSUPPORTED')
    result = {'schema':SCHEMA, 'recording_profile':sensor.PROFILE, 'events':[],
        'spans':[], 'model_candidates':[], 'close_events':[], 'missing':[],
        'cross_process_lineage_verified':False, **{key:False for key in startup.TRUTH}}
    for index, recording in enumerate((packet or {}).get('recordings', [])):
        capture = recording.get('capture') or {}
        records = capture.get('records', [])
        if not records:
            continue
        header = records[0]['payload']
        if (header.get('profile') != sensor.PROFILE or header.get('schema') != sensor.SCHEMA
                or header.get('session') != prepared['session']):
            raise ValueError('WORKER_INITIALIZATION_RECORDING_MISMATCH')
        context = header['context']
        data = metadata.journal(records, prepared, context)
        bound = bool(capture.get('process_context_verified') and recording.get('session_owner_match')
            and recording.get('listed_file_identity_match'))
        identity = {'recording':index, 'pid':context['pid'], 'start_ticks':context['start_ticks'],
            'process_binding_verified':bound}
        result['events'].extend({**identity, 'event_seq':row['seq'], **row['payload']} for row in data['events'])
        result['spans'].extend({**identity, **row} for row in data['spans'])
        result['model_candidates'].extend({**identity, **row} for row in data['model_candidates'])
        result['close_events'].extend({**identity,'event_seq':row['seq'],**row['payload']} for row in data['close_events'])
        result['missing'].extend(data['missing'])
    if not result['events']:
        result['missing'].append('worker_startup_method_observation_unavailable')
    result['missing'] = sorted(set(result['missing'] + [
        'parent_handle_PID_is_not_worker_start_generation_or_assignment',
        'independent_worker_failure_and_service_admission_unverified',
        'normal_failure_repaired_launch_lineage_unverified']))
    return result
