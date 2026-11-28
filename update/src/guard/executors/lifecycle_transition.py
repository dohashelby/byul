"""Passive, scheduled transition evidence, never a deployment controller or judge.

The operator performs the approved transition outside this executor. Schedule labels
are declarations: neither elapsed time nor two snapshots prove that it happened.
Only session/owner scoped native request IDs link model configuration observations.
"""
from __future__ import annotations
from collections import Counter
from copy import deepcopy

from guard.adapters.engine_profiles import supports

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import re
import time
import uuid

from guard.adapters import vllm_worker_generation_sensor as generation_sensor
from guard.adapters import vllm_ready_endpoint_sensor as endpoint_sensor
from guard.adapters import vllm_worker_generation_metadata as generation_metadata
from guard.adapters import vllm_ready_endpoint_metadata as endpoint_metadata
from guard.adapters.vllm_route_authentication import selection_index
from guard.collectors.file import load_bounded_json, load_environment
from guard.transport_options import valid_duration
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.engine_http import KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.runtime.kubernetes_binding import validate_container_binding, verify_owned_pod
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader, validate_target
from .lifecycle_session import capture_session, request_index

PROFILES = {
    'lif05-stopped-redeploy-v1': 'LIF-05',
    'lif06-live-weights-v1': 'LIF-06',
    'lif06-lora-v1': 'LIF-06',
    'lif06-rolling-v1': 'LIF-06',
    'lif06-multi-replica-v1': 'LIF-06',
    'lif16-restore-redeploy-v1': 'LIF-16',
}
PHASES = ('before', 'during', 'after', 'recovered')
REDEPLOY_CHECKS = frozenset({'LIF-05', 'LIF-16'})
REDEPLOY_SIDE_REUSE = 'TRANSITION_GENERATION_SIDE_REUSE'
GENERATION_MANIFEST_PIN = 'generation_manifest_sha256'
POLICY_TYPES = {'transition_inputs_sha256': 'str',
    **{f'transition_{phase}_at': 'str' for phase in PHASES},
    **{f'transition_{phase}_generation': 'str' for phase in PHASES}}
OBSERVATIONS = {key: 'list' for key in ('transition_native_generation_facts',
    'transition_request_generation_bindings', 'transition_request_configuration_facts',
    'transition_request_configuration_conflicts', 'transition_trials', 'transition_missing')}
OBSERVATIONS.update({key: 'bool' for key in ('transition_inflight_task_schedule_overlap',
    'transition_scope_complete', 'transition_actual_consumption_verified')})


def normalize_policy_values(values):
    """Publish scalar policy types supported by select; retain old private calls.

    The pre-integration helper API used nested dicts. Keeping its reader is not
    permission to declare unsupported dict policy types in a product CheckDef.
    """
    if type(values) is not dict:
        raise ValueError('TRANSITION_POLICY_INVALID')
    flat = set(POLICY_TYPES) - {'transition_inputs_sha256'}
    if not flat.intersection(values):
        return values
    if not flat <= set(values) or {'transition_sample_at', 'transition_phase_generation'} & set(values):
        raise ValueError('TRANSITION_POLICY_INVALID')
    return {**{key: value for key, value in values.items() if key not in flat},
        'transition_sample_at': {phase: values[f'transition_{phase}_at'] for phase in PHASES},
        'transition_phase_generation': {phase: values[f'transition_{phase}_generation'] for phase in PHASES}}


def stamp(value):
    if type(value) is not str:
        raise ValueError('TRANSITION_TIMESTAMP_INVALID')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('TRANSITION_TIMESTAMP_INVALID')
    return result.astimezone(timezone.utc)


def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def normalize_policy(values):
    """Absolute operator-selected sample times, not inferred transition boundaries."""
    values = normalize_policy_values(values)
    try:
        times = [stamp(values['transition_sample_at'][p]) for p in PHASES]
        if set(values['transition_sample_at']) != set(PHASES) or not all(a < b for a, b in zip(times, times[1:])):
            raise ValueError
        if not 0 < (times[-1] - times[0]).total_seconds() <= 240:
            raise ValueError
        if not _sha(values['transition_inputs_sha256']):
            raise ValueError
        selected = values['transition_phase_generation']
        if set(selected) != set(PHASES) or any(type(v) is not str or not re.fullmatch('[a-z][a-z0-9-]{0,31}', v) for v in selected.values()):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError('TRANSITION_POLICY_INVALID') from None
    return dict(zip(PHASES, times))


@dataclass
class Generation:
    key: str
    env: object
    target: dict
    native_target: dict
    prepared: dict
    reader: object = None


def _manifest_row(key, env, target, prepared):
    return {'id': key, 'environment_ref': env.ref().model_dump(mode='json'),
        'target_sha256': sha256_hex(canonical_json(target)), 'prepared_sha256': sha256_hex(canonical_json(prepared))}


def _verify_manifest_pin(cfg, digest):
    if cfg.get(GENERATION_MANIFEST_PIN, digest) != digest:
        raise ValueError('TRANSITION_GENERATION_MANIFEST_CHANGED')


def generation_identity(target, env, prepared):
    """Declared owner, release and producer session for side separation."""
    binding = target['kubernetes']
    engine = binding['engine']
    validate_container_binding(engine)
    release = env.engine
    return ((binding['context'], engine['namespace_uid'], engine['pod_uid'], engine['container_id']),
            (release.name, release.version, release.image_digest), prepared['session'])


def redeployment_side_reuse(before, after):
    return any(session == prior_session or owner == prior_owner and release != prior_release
               for owner, release, session in after for prior_owner, prior_release, prior_session in before)


def inputs_manifest(cfg, *, generation_identities=None):
    """`transition_inputs_sha256` source for guard-run-binding/2: the same rows load_generations hashes.

    Only loads the configured files; load_generations still validates them and
    compares this hash again when the executor actually reads them.
    """
    from guard.collectors.lifecycle import load_prepared
    try:
        rows = cfg['generations']
        if type(rows) is not list or not 1 <= len(rows) <= 4:
            raise ValueError
        manifest, identities = [], set()
        for row in rows:
            if type(row) is not dict or set(row) != {'id', 'target', 'prepared', 'environment'}:
                raise ValueError
            target = load_bounded_json(row['target'])
            env = load_environment(Path(row['environment']))
            prepared = load_prepared(row['prepared'])
            manifest.append(_manifest_row(row['id'], env, target, prepared))
            if generation_identities is not None:
                identities.add(generation_identity(target, env, prepared))
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        raise ValueError('TRANSITION_CONFIGURATION_INVALID') from None
    if GENERATION_MANIFEST_PIN in cfg:
        _verify_manifest_pin(cfg, sha256_hex(canonical_json(manifest)))
    if generation_identities is not None:
        generation_identities.update(identities)
    return manifest


def generation_native_target(target, env, *, require_lab=True):
    """Return the native reader's owner projection of a generation target."""
    binding = target['kubernetes']
    native = {
        'schema': 'guard-kubernetes-lifecycle-target/1',
        'lab': target['lab'],
        'run_id': binding['run_id'],
        'environment_ref': env.ref().model_dump(mode='json'),
        'kubernetes': {'context': binding['context'], 'engine': deepcopy(binding['engine'])},
    }
    if native['lab'] is False and (target.get('synthetic', False) is not False
            or env.data_origin == DataOrigin.EXAMPLE):
        raise ValueError('TRANSITION_CONFIGURATION_INVALID')
    validate_target(native, env, require_lab=require_lab)
    return native


def validate_generation_http_target(target, env):
    """Check the HTTP owner and model mapping without opening a connection."""
    try:
        KubernetesHTTP(target, env, duration=1, profile=REQUEST_BINDING_PROFILE)
    except (KubernetesBindingError, OSError):
        raise ValueError('TRANSITION_GENERATION_HTTP_TARGET_INVALID') from None


def _validate_redeployment(generations, phases):
    before = generations[phases['before']]
    if before.key in (phases['after'], phases['recovered']):
        raise ValueError('TRANSITION_DISTINCT_GENERATIONS_REQUIRED')
    release = before.env.engine
    slots = {}
    containers = set()
    restarts = set()
    old_binding = before.native_target['kubernetes']
    old = old_binding['engine']
    old_pod = (old_binding['context'], old['pod_uid'])
    for generation in generations.values():
        engine = generation.env.engine
        if (engine.name, engine.version, engine.image_digest) != (release.name, release.version, release.image_digest):
            raise ValueError('TRANSITION_GENERATION_RELEASE_MISMATCH')
        target = generation.native_target['kubernetes']
        owner = target['engine']
        pod = (target['context'], owner['pod_uid'])
        container = (target['context'], owner['container_id'])
        slot = tuple(owner[key] for key in ('namespace', 'namespace_uid', 'pod', 'node', 'container'))
        restart = (pod, owner['restart_count'])
        if (slots.setdefault(pod, slot) != slot or restart in restarts or container in containers
                or pod == old_pod and generation.key in (phases['after'], phases['recovered'])
                and owner['restart_count'] <= old['restart_count']):
            raise ValueError('TRANSITION_DISTINCT_GENERATIONS_REQUIRED')
        restarts.add(restart)
        containers.add(container)
        validate_generation_http_target(generation.target, generation.env)


def load_generations(cfg, env, values, check_id=None):
    """Load only already prepared targets. No discovery or replacement fallback."""
    from guard.collectors.lifecycle import load_prepared
    values = normalize_policy_values(values)
    try:
        if set(cfg) - {'generations', GENERATION_MANIFEST_PIN, 'kubectl_binary', 'request_timeout', 'max_duration_seconds'}:
            raise ValueError
        pinned_redeployment = check_id in REDEPLOY_CHECKS and GENERATION_MANIFEST_PIN in cfg
        rows = cfg['generations']
        if type(rows) is not list or not 1 <= len(rows) <= 4:
            raise ValueError
        generations = {}; manifest = []
        sessions = set()
        for row in rows:
            if set(row) != {'id', 'target', 'prepared', 'environment'} or not re.fullmatch('[a-z][a-z0-9-]{0,31}', row['id']):
                raise ValueError
            selected_env = load_environment(Path(row['environment']))
            if not supports(selected_env.engine.name, selected_env.engine.version):
                raise ValueError
            target = load_bounded_json(row['target'])
            prepared = load_prepared(row['prepared'])
            if row['id'] in generations or prepared['session'] in sessions:
                raise ValueError
            if prepared['profile'] not in {generation_sensor.PROFILE, endpoint_sensor.PROFILE}:
                raise ValueError
            sessions.add(prepared['session'])
            native = generation_native_target(target, selected_env, require_lab=not pinned_redeployment)
            from guard.collectors.model_read_scope import validate_binding
            validate_binding(prepared, selected_env, native)
            generations[row['id']] = Generation(row['id'], selected_env, target, native, prepared)
            manifest.append(_manifest_row(row['id'], selected_env, target, prepared))
        if len({generation.native_target['lab'] for generation in generations.values()}) != 1:
            raise ValueError
        digest = sha256_hex(canonical_json(manifest))
        if digest != values['transition_inputs_sha256']:
            raise ValueError
        if set(values['transition_phase_generation'].values()) - set(generations):
            raise ValueError
        if generations[values['transition_phase_generation']['before']].env.ref() != env.ref():
            raise ValueError
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        raise ValueError('TRANSITION_CONFIGURATION_INVALID') from None
    _verify_manifest_pin(cfg, digest)
    if check_id in REDEPLOY_CHECKS:
        _validate_redeployment(generations, values['transition_phase_generation'])
    return generations, manifest


def _record_receipt(recordings, event):
    capture = recordings[event[0]]['capture']
    provenance = capture['provenance']
    context = capture['records'][0]['payload']['context']
    return {'event': event, 'pid': context['pid'], 'start_ticks': context['start_ticks'],
        'source_sha256': capture['records'][event[1]]['payload']['source_sha256'],
        'recording_sha256': provenance['capture_sha256'],
        'file_identity_sha256': sha256_hex(canonical_json(provenance['file_identity']))}


def _chat_selection(recordings):
    selection = selection_index(recordings)
    if not selection['stored_index_complete']:
        return [], selection['invocations']
    path = sha256_hex(b'/v1/chat/completions')
    method = sha256_hex(b'POST')
    chat = [row for row in selection['events']
        if row['point'] == 'InputProcessor.assign_request_id' and row['phase'] == 'RETURN'
        and row['metadata']['entry_path_sha256'] == path and row['metadata']['entry_method_sha256'] == method
        and row['metadata']['request_id_observed'] is True and _sha(row['metadata']['selected_request_id_sha256'])]
    return chat, selection['invocations']


def _chat_assignment(domain, chat, request):
    selected = [row for row in chat if row['metadata']['asgi_request_sha256'] == request]
    if len(selected) != 1:
        return None, None
    row = selected[0]
    recording, seq = row['event']
    internal = row['metadata']['selected_request_id_sha256']
    if sum(other['event'][0] == recording and other['metadata']['selected_request_id_sha256'] == internal
            for other in chat) != 1:
        return None, None
    assignments = [candidate for candidate in domain['assignments'] if candidate['internal_sha256'] == internal
        and candidate['event'][0] == recording and candidate['event'][1] < seq]
    return (assignments[0], row['event']) if len(assignments) == 1 else (None, None)


def _recording_owner_attributed(recording):
    capture = recording.get('capture')
    return bool(capture and recording.get('session_owner_match') is True
        and recording.get('listed_file_identity_match') is True
        and capture.get('process_context_verified') is True
        and capture.get('provenance', {}).get('owner_binding_verified') is True)


def _reader_coverage(packet):
    """Aggregate the actual native reader results without exposing records or process IDs."""
    def listed(boundary):
        inventory = packet.get(boundary)
        return len(inventory['metadata']['files']) if inventory is not None else None

    recordings = packet.get('recordings', [])
    parsed = [row for row in recordings if row.get('capture') is not None]
    prefixed = [row for row in parsed if row['capture'].get('checkpoint_prefix') is not None]
    codes = Counter(code for row in recordings
        for code in set(row.get('diagnostic_codes', [])) | set((row.get('capture') or {}).get('diagnostic_codes', []))
        if type(code) is str and re.fullmatch(r'LIFECYCLE_[A-Z0-9_]{1,96}',code))
    return {
        'capture_mode':packet.get('capture_mode'),
        'before_files_listed':listed('before'), 'after_files_listed':listed('after'),
        'live_census_stable':packet.get('live_census_stable') is True,
        'recordings_attempted':len(recordings), 'recordings_parsed':len(parsed),
        'recordings_matching_listed_file':sum(row.get('listed_file_identity_match') is True for row in parsed),
        'recordings_matching_session_owner':sum(row.get('session_owner_match') is True for row in parsed),
        'live_checkpoint_ack_rows_observed':sum(record['event'] == 'CHECKPOINT' for row in parsed
            for record in row['capture'].get('records', [])) if parsed else None,
        'checkpoint_prefixes_observed':len(prefixed),
        'checkpoint_prefixes_attributed':sum(_recording_owner_attributed(row) for row in prefixed),
        'recording_diagnostic_counts':dict(sorted(codes.items())),
    }


def project(samples, trials, *, retain_receipts=False, strict_generation=False, chat_cases=()):
    """Rebuild the existing producer index from individually verified recordings.

    A configured model/revision is not consumed weights or cache identity. Keep the
    original event reference for offline judging and never merge across sessions.
    """
    facts = []; bindings = []; seen = set(); binding_seen = set(); missing = []
    request_ids = {t['request_id_sha256'] for t in trials
                   if not strict_generation or (t['attempted'] and _sha(t['request_id_sha256'])
                       and t.get('transport_dispatched') is True
                       and t.get('stock_generation_attribution_verified') is True)}
    chat_ids = request_ids if retain_receipts else {
        t['request_id_sha256'] for t in trials if t['case_id'] in chat_cases} & request_ids
    planned_generations = {}
    observed_invocations = {}
    ambiguous_requests = set()
    if chat_ids or strict_generation:
        for trial in trials:
            request = trial['request_id_sha256']
            planned_generations[request] = None if request in planned_generations else trial['generation']
    for sample_number, sample in enumerate(samples):
        packet = sample.get('native')
        if packet is None:
            missing.append(f"{sample['phase']}:{sample['generation']}:native_unavailable")
            continue
        recordings = []
        for recording in packet.get('recordings', []):
            good = _recording_owner_attributed(recording)
            if good and chat_ids:
                provenance = recording['capture']['provenance']
                good = _sha(provenance.get('capture_sha256')) and type(provenance.get('file_identity')) is dict
            recordings.append(recording if good else {'capture': None})
        index = request_index(recordings)
        if chat_ids:
            chat, invocations = _chat_selection(recordings)
            for invocation, row in invocations.items():
                request = row['asgi_request_sha256']
                if (request in chat_ids and planned_generations.get(request) == sample['generation']
                        and observed_invocations.setdefault(request, invocation) != invocation):
                    ambiguous_requests.add(request)
        for domain_id, domain in index['domains'].items():
            for request in request_ids:
                if strict_generation and planned_generations.get(request) != sample['generation']:
                    continue
                from_chat = request in chat_ids
                if from_chat:
                    if (not index['stored_record_index_complete'] or request in ambiguous_requests
                            or planned_generations[request] != sample['generation']):
                        continue
                    assignment, selection_event = _chat_assignment(domain, chat, request)
                    if assignment is None:
                        continue
                    internal = assignment['internal_sha256']
                else:
                    assignments = {a['internal_sha256'] for a in domain['assignments']
                        if a.get('asgi_request_sha256') == request}
                    if len(assignments) != 1:
                        continue
                    internal = next(iter(assignments))
                binding_key = (sample['generation'], packet['session'], domain_id, request, internal)
                if binding_key not in binding_seen:
                    binding_seen.add(binding_key)
                    binding = {'generation': sample['generation'], 'session': packet['session'],
                        'domain': domain_id, 'request_id_sha256': request, 'internal_request_sha256': internal,
                        'sample': sample_number, 'actual_consumption_verified': False}
                    if from_chat:
                        binding['source_receipt'] = _record_receipt(recordings, assignment['event'])
                        binding['selection_source_receipt'] = _record_receipt(recordings, selection_event)
                    bindings.append(binding)
                for row in domain['model_observations']:
                    if internal not in row['request_sha256'] or from_chat and row['event'][0] != assignment['event'][0]:
                        continue
                    metadata = row['metadata']
                    configured = [metadata.get('configured_model_sha256'), metadata.get('configured_revision_sha256')]
                    if not all(_sha(value) for value in configured):
                        continue
                    # Repeated live samples must not multiply one native fact.
                    key = (sample['generation'], packet['session'], domain_id, request, tuple(row['event']))
                    if key in seen:
                        continue
                    seen.add(key)
                    fact = {'generation': sample['generation'], 'session': packet['session'], 'domain': domain_id,
                        'request_id_sha256': request, 'internal_request_sha256': internal,
                        'configured_generation': configured, 'point': row['point'], 'phase': row['phase'],
                        'sample': sample_number, 'event': row['event'], 'actual_consumption_verified': False}
                    if from_chat:
                        fact['source_receipt'] = _record_receipt(recordings, row['event'])
                    facts.append(fact)
        if not packet.get('recorded_files_covered'):
            missing.append(f"{sample['phase']}:{sample['generation']}:session_coverage_unavailable")
    # A later qualified invocation supersedes the uniqueness of earlier samples.
    if ambiguous_requests:
        bindings = [row for row in bindings if row['request_id_sha256'] not in ambiguous_requests]
        facts = [row for row in facts if row['request_id_sha256'] not in ambiguous_requests]
    grouped = {}
    for fact in facts:
        key = (fact['generation'], fact['session'], fact['domain'], fact['request_id_sha256'])
        grouped.setdefault(key, set()).add(tuple(fact['configured_generation']))
    conflicts = [{'generation': k[0], 'session': k[1], 'domain': k[2], 'request_id_sha256': k[3],
        'configured_generations': sorted(map(list, v))} for k, v in grouped.items() if len(v) > 1]
    linked = {f['request_id_sha256'] for f in bindings}
    for trial in trials:
        if trial['request_id_sha256'] not in linked:
            missing.append(trial['case_id'] + ':native_request_generation_unlinked')
        if trial['request_id_sha256'] in ambiguous_requests:
            missing.append(trial['case_id'] + ':native_request_generation_ambiguous')
    return {'request_generation_bindings': bindings, 'request_configuration_facts': facts, 'request_configuration_conflicts': conflicts,
        'missing': sorted(set(missing)), 'actual_weights_cache_consumption_verified': False}


def _canonical_live_observations(projection, trials, manifest, missing, *, include_native=True):
    controls = []
    for trial in trials:
        transport = trial.get('transport', {})
        requests = transport.get('requests', [])
        if (trial['case_id'] in PHASES and trial['normal_shape_verified'] and trial.get('capture_complete') is True
                and transport.get('collection_gap') is False and not transport.get('cleanup_errors')
                and len(requests) == 1 and requests[0].get('binding_verified_before_after') is True):
            controls.append({key: trial[key] for key in ('case_id', 'generation', 'request_id_sha256',
                'started_at', 'finished_at', 'normal_shape_verified')})
    if {row['case_id'] for row in controls} != set(PHASES):
        missing = [*missing, 'selected_phase_normal_control_incomplete']
    native = {}
    has_observations = bool(controls)
    if include_native:
        bindings = projection['request_generation_bindings']
        facts = projection['request_configuration_facts']
        has_observations = has_observations or bool(bindings or facts)
        if not facts:
            missing = [*missing, 'selected_request_configuration_observation_unavailable']
        native = {'request_generation_bindings': bindings, 'request_configuration_facts': facts,
            'request_configuration_conflict_candidates': projection['request_configuration_conflicts']}
    else:
        observed_generations = {row['generation'] for row in controls}
        manifest = [row for row in manifest if row['id'] in observed_generations]
    steps = []
    if has_observations:
        steps.append({'step': 'passive-selected-transition-observation', 'prepared_scopes': manifest,
            'normal_controls': controls, **native,
            'transition_activation_verified': False, 'actual_consumption_verified': False})
    return {'activation_steps': steps, 'activation_missing': sorted(set(missing)),
        'activation_violations': [], 'activation_scope_complete': False}


class LifecycleTransitionExecutor:
    name = 'lifecycle_transition'
    version = '0.1.0'

    def __init__(self, cfg, *, runner=None, clock=None, wait=None, expected_target_sha256=None,
                 expected_manifest_sha256=None, staged_lif05=None):
        from .lif05_stages import SETTING
        self.b_common_inputs = deepcopy(cfg.get('common_inputs'))
        self.b_cpu_support_input = deepcopy(cfg.get('cpu_support_input'))
        self.stage_digest = cfg.get(SETTING)
        self.stage_requested = SETTING in cfg
        self.stage = staged_lif05
        self.cfg = {k: v for k, v in cfg.items() if k not in {'common_inputs', 'cpu_support_input', SETTING}}; self.runner = runner; self.clock = clock or utcnow
        self.wait = wait or time.sleep
        self.expected_target = expected_target_sha256
        self.expected_manifest = expected_manifest_sha256

    def validate_policy(self, check, values):
        if PROFILES.get(getattr(check, 'collection_profile', None)) != check.id:
            raise ValueError('TRANSITION_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def keys(self):
        return {}

    def prepared_generations(self, check, env, values):
        """Validate local generation files and scope without sampling or dispatch."""
        result = load_generations(self.cfg, env, values, check.id)
        if check.id in REDEPLOY_CHECKS:
            self._transport_settings()
        return result

    def _transport_settings(self):
        timeout = self.cfg.get('request_timeout', 30)
        if not valid_duration(timeout, 30):
            raise ValueError('TRANSITION_CONFIGURATION_INVALID')
        duration = self.cfg.get('max_duration_seconds', 300)
        binary = self.cfg.get('kubectl_binary', 'kubectl')
        if (not valid_duration(duration, 300)
                or type(binary) is not str or not binary.strip()):
            raise ValueError('TRANSITION_CONFIGURATION_INVALID')
        return timeout, duration, binary

    def _reader(self, generation):
        return KubernetesLifecycleReader(generation.native_target, generation.env, generation.prepared,
            runner=self.runner, binary=self.cfg.get('kubectl_binary', 'kubectl'),
            read_only=generation.native_target['lab'] is False,
            duration=self.cfg.get('max_duration_seconds', 300))

    def _capture(self, generation):
        reader = generation.reader; engine = reader.binding['engine']
        packet = capture_session(reader, generation.native_target, container=engine['container'], image=engine['image'],
            prepared=generation.prepared, owner_identity=reader.owner_identity, read_file=reader.read_file,
            image_check=reader.image_check, checkpoint=generation.native_target.get('lab') is False)
        # Reuse the exact profile's producer/consumer contract. Projection failure
        # cannot discard successfully captured independent request/native facts.
        try:
            projector = endpoint_metadata if generation.prepared['profile'] == endpoint_sensor.PROFILE else generation_metadata
            packet['transition_generation_projection'] = projector.project(packet, generation.prepared)
        except Exception:
            packet['transition_generation_projection'] = None
            packet.setdefault('diagnostic_codes', []).append('TRANSITION_GENERATION_PROJECTION_UNAVAILABLE')
        return packet

    def _probe(self, generation, phase, request_id, timeout):
        """Existing bounded Kubernetes transport; a new instance isolates failures."""
        transport = KubernetesHTTP(generation.target, generation.env, runner=self.runner,
            binary=self.cfg.get('kubectl_binary', 'kubectl'), duration=self.cfg.get('max_duration_seconds', 300),
            profile=REQUEST_BINDING_PROFILE)
        request = {'id': 'before' if phase in {'before', 'inflight'} else 'after', 'method': 'POST', 'mode': 'json',
            'url': generation.target['engine']['trusted_url'] + '/v1/chat/completions',
            'headers': {'X-Request-Id': request_id}, 'body': {'model': transport.api_model,
                'messages': [{'role': 'user', 'content': 'hello'}], 'max_tokens': 1, 'temperature': 0, 'stream': False}}
        try:
            result, = transport('kubernetes-probe', [request], timeout)
            text = result.get('body'); shape = False
            complete = (type(text) is str and result.get('capture_complete') is True and result.get('eof') is True
                and result.get('utf8_valid') is True and result.get('body_truncated') is False
                and result.get('body_bytes') == len(text.encode()) and result.get('body_sha256') == sha256_hex(text.encode()))
            if complete and result.get('outcome') == 200:
                body = strict_json(text.encode(), limit=131072)
                choices = body.get('choices') if type(body) is dict else None
                shape = bool(body.get('model') == transport.api_model and type(choices) is list and 1 <= len(choices) <= 8
                    and all(type(c) is dict and type(c.get('message')) is dict and type(c['message'].get('content')) is str for c in choices))
            return {'outcome': result.get('outcome'), 'capture_complete': complete, 'normal_shape_verified': shape,
                'transport': transport.report()}
        except Exception:
            return {'normal_shape_verified': False, 'diagnostic': 'TRANSITION_REQUEST_UNAVAILABLE',
                'transport': transport.report()}

    def _until(self, at):
        while True:
            remaining = (at - self.clock()).total_seconds()
            if remaining <= 0:
                return
            self.wait(min(remaining, 0.2))

    def _observe_retirement(self, generation, samples):
        """Read back retirement only for a generation previously verified live."""
        try:
            expected = generation.target['kubernetes']['engine']
            prior = next((s['native']['before'] for s in reversed(samples)
                if s['phase'] in ('preflight', 'before') and s['generation'] == generation.key
                and s.get('native') and s['native'].get('session') == generation.prepared['session']
                and s['native'].get('before') and s['native']['before'].get('owner_stable') is True
                and _sha(s['native']['before'].get('capture_sha256'))
                and all(s['native']['before']['owner'].get(key) == expected[key]
                        for key in ('namespace_uid', 'pod_uid', 'container_id', 'restart_count'))), None)
            if prior is None:
                return None
            transport = KubernetesHTTP(generation.target, generation.env, duration=5,
                binary=self.cfg.get('kubectl_binary', 'kubectl'), runner=self.runner, profile=REQUEST_BINDING_PROFILE)
            def namespace():
                value = transport._call(['get', 'namespace', expected['namespace'], '-o', 'json'])
                meta = value['metadata']
                if (value.get('kind') != 'Namespace' or meta.get('name') != expected['namespace']
                        or meta.get('uid') != expected['namespace_uid'] or meta.get('deletionTimestamp')):
                    raise ValueError
                return meta
            started = self.clock()
            before = namespace()
            pod = transport._call([f"--namespace={expected['namespace']}", 'get', 'pod', expected['pod'],
                '--ignore-not-found', '-o', 'json'], allow_empty=True)
            finished_at = None
            state = 'selected_pod_absent'
            if pod is not None:
                if pod.get('kind') != 'Pod':
                    raise ValueError
                verify_owned_pod(pod, before, expected, transport.binding['run_id'], require_ready=False, terminated=True)
                selected, = (row for row in pod['status']['containerStatuses'] if row['name'] == expected['container'])
                finished_at = stamp(selected['state']['terminated']['finishedAt']).isoformat()
                state = 'selected_container_terminated'
            namespace()
            if transport.cleanup_errors:
                return None
            return {'generation': generation.key, 'state': state, 'target_sha256': transport.target_sha256,
                    'prior_live_inventory_sha256': prior['capture_sha256'],
                    'observed_from': started.isoformat(), 'observed_until': self.clock().isoformat(),
                    'reported_container_finished_at': finished_at, 'native_terminal_tail_collected': False}
        except (KeyError, TypeError, ValueError, AttributeError, OSError, RuntimeError):
            return None

    def run(self, check, env, policy_values, adapter, workdir):
        policy_values = normalize_policy_values(policy_values)
        self.validate_policy(check, policy_values)
        schedule = normalize_policy(policy_values)
        pinned_redeployment = check.id in REDEPLOY_CHECKS and GENERATION_MANIFEST_PIN in self.cfg
        if pinned_redeployment and (not _sha(self.expected_manifest)
                or self.cfg[GENERATION_MANIFEST_PIN] != self.expected_manifest):
            raise ValueError('TRANSITION_PAIRED_GENERATION_PLAN_REQUIRED')
        generations, manifest = load_generations(self.cfg, env, policy_values, check.id if pinned_redeployment else None)
        if self.expected_target is not None:
            approved = {row['id']: row['target_sha256'] for row in manifest}
            matches = self.expected_target == approved
            if type(self.expected_target) is str and len(approved) == 1:
                matches = self.expected_target == next(iter(approved.values()))
            if not matches:
                raise ValueError('TRANSITION_EXPLICIT_GENERATION_TARGET_APPROVAL_REQUIRED')
        if self.stage_requested:
            from .lif05_stages import StagedLif05
            if (check.id != 'LIF-05' or not pinned_redeployment or self.runner is not None
                    or type(self.stage) is not StagedLif05):
                raise ValueError('LIF05_STAGE_COORDINATOR_REQUIRED')
            capture, retirement = self.stage.consume(
                self.stage_digest, generations, schedule, policy_values['transition_phase_generation'])
            return self._draft(check, env, policy_values, workdir, generations, manifest, schedule,
                               capture['samples'], capture['trials'], capture['diagnostics'], capture['cleanup'],
                               retirement, self.stage.started_at, stage=self.stage, request_trace=capture['request_trace'])
        timeout, _, _ = self._transport_settings()
        started = self.clock()
        if not started <= schedule['before'] or (schedule['recovered'] - started).total_seconds() > 250:
            raise ValueError('TRANSITION_MUST_START_BEFORE_SCHEDULE')
        samples = []; trials = []; diagnostics = []; cleanup = []; token = uuid.uuid4().hex

        def trial(phase):
            key = policy_values['transition_phase_generation']['before' if phase == 'inflight' else phase]
            row = {'case_id': phase, 'generation': key, 'request_id_sha256': sha256_hex((token + ':' + phase).encode()),
                'attempted': True, 'started_at': self.clock().isoformat(), 'finished_at': None,
                'normal_shape_verified': False}
            try:
                row.update(self._probe(generations[key], phase, token + ':' + phase, timeout))
            except Exception:
                row['diagnostic'] = 'TRANSITION_REQUEST_UNAVAILABLE'
            finally:
                row['finished_at'] = self.clock().isoformat()
            return row

        def sample(phase, *, retired_generation=None):
            for generation in generations.values():
                if generation.key == retired_generation:
                    continue
                row = {'phase': phase, 'generation': generation.key, 'sample_started_at': self.clock().isoformat(), 'native': None}
                try:
                    if generation.reader is None:
                        generation.reader = self._reader(generation)
                    row['native'] = self._capture(generation)
                except Exception:
                    row['diagnostic'] = 'TRANSITION_GENERATION_CAPTURE_UNAVAILABLE'
                row['sample_finished_at'] = self.clock().isoformat(); samples.append(row)

        # Begin native generation collection before any scheduled transition.
        sample('preflight')
        self._until(schedule['before']); trials.append(trial('before'))
        if stamp(trials[0]['finished_at']) >= schedule['during']:
            diagnostics.append('TRANSITION_PRECHANGE_CONTROL_WINDOW_MISSED')
        with ThreadPoolExecutor(max_workers=1) as pool:
            inflight = pool.submit(trial, 'inflight')
            sample('before')
            for phase in PHASES[1:]:
                self._until(schedule[phase])
                sample(phase)
                trials.append(trial(phase))
            trials.append(inflight.result())
        # A verified API retirement cannot supply a terminal journal from the old Pod.
        retirement = None
        if check.id == 'LIF-05':
            previous = generations[policy_values['transition_phase_generation']['before']]
            retirement = self._observe_retirement(previous, samples)
        retired_generation = retirement['generation'] if retirement else None
        read_only = all(generation.target.get('lab') is False for generation in generations.values())
        for generation in generations.values():
            if generation.reader is None:
                continue
            if not read_only and generation.key != retired_generation:
                try:
                    if generation.reader.seal_session().get('sealed') is not True:
                        cleanup.append('TRANSITION_SESSION_SEAL_UNAVAILABLE:' + generation.key)
                except Exception:
                    cleanup.append('TRANSITION_SESSION_SEAL_UNAVAILABLE:' + generation.key)
            try:
                report = generation.reader.report()
                if report.get('parent_cleanup_unverified'):
                    cleanup.append('TRANSITION_PARENT_CLEANUP_UNVERIFIED:' + generation.key)
            except Exception:
                cleanup.append('TRANSITION_READER_REPORT_UNAVAILABLE:' + generation.key)
        sample('final' if read_only else 'sealed', retired_generation=retired_generation)
        return self._draft(check, env, policy_values, workdir, generations, manifest, schedule,
                           samples, trials, diagnostics, cleanup, retirement, started)

    def _draft(self, check, env, policy_values, workdir, generations, manifest, schedule,
               samples, trials, diagnostics, cleanup, retirement, started, *, stage=None, request_trace=()):
        projection = project(samples, trials, retain_receipts=check.id == 'LIF-06', strict_generation=stage is not None,
                             chat_cases=PHASES if stage is not None else ())
        before = next(t for t in trials if t['case_id'] == 'before')
        inflight_row = next(t for t in trials if t['case_id'] == 'inflight')
        # A label/time overlap is only scheduling coverage, never a native join.
        overlap = stamp(inflight_row['started_at']) < schedule['during'] < stamp(inflight_row['finished_at'])
        if not overlap:
            diagnostics.append('TRANSITION_INFLIGHT_WINDOW_NOT_COVERED')
        for phase in PHASES:
            row = next(t for t in trials if t['case_id'] == phase)
            if not row['normal_shape_verified']:
                diagnostics.append('TRANSITION_NORMAL_CONTROL_UNAVAILABLE:' + phase)
        missing = [*projection['missing'], *diagnostics,
            'actual_transition_activation_unverified', 'actual_weights_cache_consumption_unverified',
            'inflight_actual_request_overlap_unverified',
            'drain_and_external_admission_gate_unverified']
        if retirement is not None:
            missing.append('retired_generation_native_terminal_tail_unobserved')
        reader_coverage = []
        if stage is not None:
            missing.append('request_kv_generation_and_release_unverified')
            for trial in trials:
                if ('client_response_headers' in trial['dispatch_evidence']
                        and trial['stock_generation_attribution_verified'] is not True):
                    missing.append('TRANSITION_CLIENT_HTTP_UNATTRIBUTED:' + trial['case_id'])
                if trial['transport_dispatched'] is None:
                    missing.append('TRANSITION_TRANSPORT_DISPATCH_UNVERIFIED:' + trial['case_id'])
            for sample in samples:
                coverage = _reader_coverage(sample['native']) if sample['native'] is not None else None
                reader_coverage.append(coverage)
                if coverage is None:
                    continue
                label = f"{sample['phase']}:{sample['generation']}:"
                if coverage['live_checkpoint_ack_rows_observed'] == 0:
                    missing.append(label + 'native_live_checkpoint_ack_unobserved')
                if coverage['checkpoint_prefixes_attributed'] == 0:
                    missing.append(label + 'native_live_checkpoint_prefix_attribution_unverified')
        if check.id == 'LIF-06':
            missing.extend(['selected_feature_activation_unverified', 'partial_failure_activation_unverified'])
        if check.id == 'LIF-16':
            missing.extend(['restore_material_integrity_and_actual_consumption_unverified',
                'tamper_activation_and_rejection_unverified', 'residual_state_access_unverified'])
        for t in trials:
            transport = t.get('transport', {})
            cleanup.extend(transport.get('cleanup_errors', []))
        generation_facts = [{'sample': i, 'generation': s['generation'], 'phase': s['phase'],
            'session': s['native']['session'], 'projection': s['native']['transition_generation_projection']}
            for i, s in enumerate(samples) if s.get('native') and s['native'].get('transition_generation_projection') is not None]
        observations = {'transition_native_generation_facts': generation_facts,
            'transition_request_generation_bindings': projection['request_generation_bindings'],
            'transition_request_configuration_facts': projection['request_configuration_facts'],
            'transition_request_configuration_conflicts': projection['request_configuration_conflicts'],
            'transition_trials': trials, 'transition_missing': sorted(set(missing)),
            'transition_inflight_task_schedule_overlap': overlap, 'transition_scope_complete': False,
            'transition_actual_consumption_verified': False}
        if check.id == 'LIF-05':
            trial_fields = ('case_id', 'generation', 'request_id_sha256', 'started_at', 'finished_at', 'normal_shape_verified')
            observations.update(
                activation_steps=[{
                    'step': 'passive-scheduled-redeployment-observation',
                    'trials': [{key: trial[key] for key in trial_fields} for trial in trials],
                    'request_generation_bindings': projection['request_generation_bindings'],
                    'request_configuration_facts': projection['request_configuration_facts'],
                    'request_configuration_conflict_candidates': projection['request_configuration_conflicts'],
                    'old_api_retirement': retirement,
                    'transition_activation_verified': False,
                    'actual_consumption_verified': False,
                }],
                activation_missing=observations['transition_missing'],
                activation_violations=[],
                activation_scope_complete=False,
            )
        elif check.id in {'LIF-06', 'LIF-16'}:
            observations.update(_canonical_live_observations(
                projection, trials, manifest, missing, include_native=check.id == 'LIF-06'))
        challenges = [{'step': 'passive externally controlled transition', 'attempted': True,
                       'observations': {'transition_activation_verified': False}}]
        if stage is not None:
            from .lif05_stages import LIF05_STEPS
            if tuple(check.challenge_steps) != LIF05_STEPS:
                raise ValueError('LIF05_STAGE_CHECK_DEFINITION_MISMATCH')
            traced = [case for row in request_trace for case in row['cases'] if case['request_observer_invoked']]
            observations['transition_owned_request_trace'] = request_trace
            challenges = [{'step': LIF05_STEPS[0], 'attempted': inflight_row['attempted']},
                          {'step': LIF05_STEPS[1], 'attempted': retirement is not None},
                          {'step': LIF05_STEPS[2], 'attempted': bool(traced),
                           'observations': {'request_observer_invoked_cases': [case['case_id'] for case in traced],
                               'bound_assignment_cases': [case['case_id'] for case in traced if case['assignment_bound']],
                               'all_phase_resource_and_routing_observations_verified': False}}]
            observations = stage.scrub(observations)
            observations['activation_steps'][0]['owned_sequential_generation_receipt'] = stage.public_receipt()
            # Keep the bounded native projection in observations. The held
            # checkpoint records may carry the legacy producer's request digests.
            samples = [{**{k: v for k, v in row.items() if k != 'native'},
                        'native': None if row['native'] is None else {
                            'session': row['native']['session'],
                            'recordings_seen': len(row['native'].get('recordings', [])),
                            'recorded_files_covered': row['native'].get('recorded_files_covered'),
                            'diagnostic_codes': row['native'].get('diagnostic_codes', []),
                            'reader_coverage': coverage,
                        }} for row, coverage in zip(samples, reader_coverage, strict=True)]
        summary = {'profile': check.collection_profile, 'manifest': manifest, 'samples': samples,
            'observations': observations, 'engine_started': False, 'transition_control_implemented': False,
            'producer_authenticated': False, 'whole_run_coverage_verified': False}
        if stage is not None:
            summary['measurement_source'] = 'owned-live-sequential-checkpoints'
        workdir = Path(workdir); artifacts = []; workdir.mkdir(parents=True, exist_ok=True)
        try:
            path = workdir / 'lifecycle-transition.json'; path.write_bytes(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=self.name + '/' + self.version))
        except OSError:
            cleanup.append('TRANSITION_ARTIFACT_UNAVAILABLE')
        draft = EvidenceDraft(status=ExecStatus.ABORTED if cleanup or diagnostics or projection['missing'] or retirement is not None else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if self.runner is not None or any(g.target.get('lab') for g in generations.values()) else DataOrigin.DECLARED,
            normal_control={'attempted': before['attempted'], 'succeeded': all(t['normal_shape_verified'] for t in trials if t['case_id'] in PHASES),
                'detail': 'Scheduled normal HTTP shapes; native generation, consumption and admission guarantees are separate observations.'},
            challenges=challenges,
            observations={k: {'value': v, 'collected': True} for k, v in observations.items()}, artifacts=artifacts,
            config={'profile': check.collection_profile, 'manifest': manifest, 'sample_at': policy_values['transition_sample_at'],
                    **({'staged_lif05_sha256': stage.digest()} if stage is not None else {})},
            cleanup_errors=cleanup, started_at=started, finished_at=self.clock())

        from .lifecycle_common_input_consumption import attach_inputs
        from .committed_cpu_support import attach as attach_cpu_support
        if self.b_common_inputs is None:
            return attach_cpu_support(draft, check, self.b_cpu_support_input, workdir)
        selected = generations.get(self.b_common_inputs.get('generation')) if type(self.b_common_inputs) is dict else None
        if selected is None:
            return attach_cpu_support(attach_inputs(draft, check, env, {}, {}, workdir),
                                      check, self.b_cpu_support_input, workdir)
        draft = attach_inputs(draft, check, selected.env, selected.target, self.b_common_inputs, workdir,
            prepared=selected.prepared, owner=selected.native_target['kubernetes']['engine'], generation=selected.key)
        return attach_cpu_support(draft, check, self.b_cpu_support_input, workdir)
