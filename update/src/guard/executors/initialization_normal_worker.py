"""Project source-bound normal worker facts into the initialization checks."""

import re

from guard.adapters import vllm_ready_endpoint_sensor, vllm_worker_generation_sensor
from guard.adapters.engine_profiles import supports
from guard.adapters.vllm_lifecycle_profiles import public_commit
from guard.adapters.vllm_release_profiles import release_profile


ACTIVATION_TYPES = {
    'activation_steps': 'list',
    'activation_violations': 'list',
    'activation_missing': 'list',
    'activation_scope_complete': 'bool',
}
GENERATION_PROFILES = {vllm_worker_generation_sensor.PROFILE, vllm_ready_endpoint_sensor.PROFILE}


def has_activation_contract(check):
    definitions = {item.key: item.type for item in check.required_observations}
    return all(definitions.get(key) == kind for key, kind in ACTIVATION_TYPES.items())


def generation_release_supported(env, profile):
    """Match the pinned worker source commit to the reviewed release image."""
    engine = env.engine
    release = release_profile(engine.version)
    if (profile not in GENERATION_PROFILES or not supports(engine.name, engine.version, 'worker_readiness')
            or not release or engine.image_digest not in release['images']):
        return False
    commit = public_commit(profile)
    if release.get('usage_report_source', {}).get('source_commit') != commit:
        return False
    observed = engine.source_commit
    abbreviated = (engine.source_commit_status == 'ABBREVIATED_MATCH' and engine.expected_source_commit == commit
                   and bool(observed) and len(observed) < len(commit) and commit.startswith(observed))
    modes = [value.strip() for field in (env.runtime.platform or '').split(';')
             for name, _, value in [field.partition('=')] if name.strip() == 'engine_requested']
    return (engine.source_commit_status != 'MISMATCH' and engine.expected_source_commit in (None, commit)
            and (observed in (None, commit) or abbreviated)
            and (not modes or modes == [release['launch']['engine']]))


def _recording_proof(packet, index, profile):
    recordings = packet.get('recordings', ())
    if type(index) is not int or not 0 <= index < len(recordings):
        return None
    recording = recordings[index]
    capture = recording.get('capture') or {}
    summary = capture.get('summary') or {}
    provenance = capture.get('provenance') or {}
    digest = provenance.get('capture_sha256')
    if not (recording.get('session_owner_match') is True and recording.get('listed_file_identity_match') is True
            and capture.get('process_context_verified') is True and provenance.get('owner_binding_verified') is True
            and summary.get('recording_closed') is True and summary.get('incomplete_final_row') is False
            and summary.get('discarded_tail_bytes') == 0
            and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)):
        return None
    if capture.get('image_digest_verified') is True:
        return digest, 'node-verified-image-digest'
    # /21's projector verifies process binding separately from image identity.
    image = provenance.get('native_image_reference') or {}
    declared = image.get('declared_image')
    if (profile == vllm_worker_generation_sensor.PROFILE
            and image.get('api_binding_verified_before') is True and image.get('api_binding_verified_after') is True
            and isinstance(declared, str) and re.fullmatch(r'.+@sha256:[0-9a-f]{64}', declared)
            and image.get('reported_image_id') == declared):
        return digest, 'api-reported-exact-image-digest'
    return None


def _normal_worker_step(packet, model, worker, prepared, expected_ranks):
    if (model['process_binding_verified'] is not True or model['worker_point'] != 'Worker.load_model'
            or model['local_worker_generation_model_candidate'] is not True):
        return None
    recording = model['recording']
    proof = _recording_proof(packet, recording, prepared['profile'])
    if proof is None:
        return None
    digest, image_basis = proof
    call = model['worker_call_sha256']
    terminal = [row for row in worker['events'] if row['recording'] == recording
                and row['call_sha256'] == call and row['event_seq'] == model['worker_end_seq']
                and row['phase'] == 'RETURN' and row['source_verified'] is True]
    links = [row for row in model['worker_generation_candidates']
             if row['phase'] == 'RETURN' and row['worker_event_seq'] == model['worker_end_seq']
             and row['local_object_generation_candidate'] is True and row['constructor_return_observed'] is True]
    if len(terminal) != 1 or len(links) != 1:
        return None
    link, = links
    if str(link['rank']) not in expected_ranks:
        return None
    sends = [row for row in worker['ready_sends'] if row['recording'] == recording
             and row['main_call_sha256'] == link['main_call_sha256'] and row['status'] == 'READY'
             and row['original_send_return_observed'] is True and model['worker_end_seq'] < row['start_seq']]
    if len(sends) != 1:
        return None
    ready, = sends
    return {
        'step': 'native-normal-worker-load-return-and-ready-send',
        'recording_profile': prepared['profile'], 'session': prepared['session'],
        'source_recording_sha256': digest, 'image_identity_basis': image_basis,
        'rank': link['rank'], 'local_rank': link['local_rank'],
        'worker_load_call_sha256': call,
        'worker_load_call_and_return_seq': [model['worker_start_seq'], model['worker_end_seq']],
        'model_root_event_seq': model['root_event_seq'],
        'ready_send_call_and_return_seq': [ready['start_seq'], ready['end_seq']],
    }


def normal_worker_steps(packet, prepared, worker, *, declared_phase, expected_ranks, sealed):
    """Reuse the native projector's joins and retain only qualified local returns.

    The caller supplies the original captured packet and existing /21 or /22
    projection from the same collection. READY is a sender-side observation.
    """
    if (declared_phase != 'normal' or sealed is not True
            or prepared['profile'] not in GENERATION_PROFILES
            or worker.get('recording_profile') != prepared['profile'] or not packet
            or packet.get('session') != prepared['session'] or packet.get('recorded_files_covered') is not True):
        return []
    steps = []
    for model in worker['model_candidates']:
        step = _normal_worker_step(packet, model, worker, prepared, expected_ranks)
        if step is not None and step not in steps:
            steps.append(step)
    return steps
