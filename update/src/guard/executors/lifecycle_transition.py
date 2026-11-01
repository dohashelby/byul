"""Passive, scheduled transition evidence, never a deployment controller or judge.

The operator performs the approved transition outside this executor. Schedule labels
are declarations: neither elapsed time nor two snapshots prove that it happened.
Only session/owner scoped native request IDs link model configuration observations.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import math
import re
import time
import uuid

from guard.adapters import vllm_worker_generation_sensor as generation_sensor
from guard.adapters import vllm_ready_endpoint_sensor as endpoint_sensor
from guard.adapters import vllm_worker_generation_metadata as generation_metadata
from guard.adapters import vllm_ready_endpoint_metadata as endpoint_metadata
from guard.collectors.file import load_environment
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
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


def load_generations(cfg, env, values):
    """Load only already prepared targets. No discovery or replacement fallback."""
    from guard.collectors.lifecycle import load_prepared
    try:
        if set(cfg) - {'generations', 'kubectl_binary', 'request_timeout', 'max_duration_seconds'}:
            raise ValueError
        rows = cfg['generations']
        if type(rows) is not list or not 1 <= len(rows) <= 4:
            raise ValueError
        generations = {}; manifest = []
        sessions = set()
        for row in rows:
            if set(row) != {'id', 'target', 'prepared', 'environment'} or not re.fullmatch('[a-z][a-z0-9-]{0,31}', row['id']):
                raise ValueError
            selected_env = load_environment(Path(row['environment']))
            if selected_env.engine.name != 'vllm' or selected_env.engine.version != '0.31.0':
                raise ValueError
            with Path(row['target']).open('rb') as stream:
                target = strict_json(stream.read(65537), limit=65536)
            prepared = load_prepared(row['prepared'])
            if row['id'] in generations or prepared['session'] in sessions:
                raise ValueError
            if prepared['profile'] not in {generation_sensor.PROFILE, endpoint_sensor.PROFILE}:
                raise ValueError
            sessions.add(prepared['session'])
            native = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': target['lab'],
                'run_id': target['kubernetes']['run_id'], 'environment_ref': selected_env.ref().model_dump(mode='json'),
                'kubernetes': {'context': target['kubernetes']['context'], 'engine': target['kubernetes']['engine']}}
            validate_target(native, selected_env)
            from guard.collectors.model_read_scope import validate_binding
            validate_binding(prepared, selected_env, native)
            generations[row['id']] = Generation(row['id'], selected_env, target, native, prepared)
            manifest.append({'id': row['id'], 'environment_ref': selected_env.ref().model_dump(mode='json'),
                'target_sha256': sha256_hex(canonical_json(target)), 'prepared_sha256': sha256_hex(canonical_json(prepared))})
        if sha256_hex(canonical_json(manifest)) != values['transition_inputs_sha256']:
            raise ValueError
        if set(values['transition_phase_generation'].values()) - set(generations):
            raise ValueError
        if generations[values['transition_phase_generation']['before']].env.ref() != env.ref():
            raise ValueError
        return generations, manifest
    except (KeyError, TypeError, ValueError, OSError, RuntimeError):
        raise ValueError('TRANSITION_CONFIGURATION_INVALID') from None


def project(samples, trials):
    """Rebuild the existing producer index from individually verified recordings.

    A configured model/revision is not consumed weights or cache identity. Keep the
    original event reference for offline judging and never merge across sessions.
    """
    facts = []; bindings = []; seen = set(); binding_seen = set(); missing = []
    request_ids = {t['request_id_sha256'] for t in trials}
    for sample_number, sample in enumerate(samples):
        packet = sample.get('native')
        if packet is None:
            missing.append(f"{sample['phase']}:{sample['generation']}:native_unavailable")
            continue
        recordings = []
        for recording in packet.get('recordings', []):
            capture = recording.get('capture')
            good = bool(capture and recording.get('session_owner_match') is True
                and recording.get('listed_file_identity_match') is True and capture.get('process_context_verified') is True
                and capture.get('provenance', {}).get('owner_binding_verified') is True)
            recordings.append(recording if good else {'capture': None})
        index = request_index(recordings)
        for domain_id, domain in index['domains'].items():
            for request in request_ids:
                assignments = {a['internal_sha256'] for a in domain['assignments']
                    if a.get('asgi_request_sha256') == request}
                if len(assignments) != 1:
                    continue
                internal = next(iter(assignments))
                binding_key = (sample['generation'], packet['session'], domain_id, request, internal)
                if binding_key not in binding_seen:
                    binding_seen.add(binding_key)
                    bindings.append({'generation': sample['generation'], 'session': packet['session'],
                        'domain': domain_id, 'request_id_sha256': request, 'internal_request_sha256': internal,
                        'sample': sample_number, 'actual_consumption_verified': False})
                for row in domain['model_observations']:
                    if internal not in row['request_sha256']:
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
                    facts.append({'generation': sample['generation'], 'session': packet['session'], 'domain': domain_id,
                        'request_id_sha256': request, 'internal_request_sha256': internal,
                        'configured_generation': configured, 'point': row['point'], 'phase': row['phase'],
                        'sample': sample_number, 'event': row['event'], 'actual_consumption_verified': False})
        if not packet.get('recorded_files_covered'):
            missing.append(f"{sample['phase']}:{sample['generation']}:session_coverage_unavailable")
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
    return {'request_generation_bindings': bindings, 'request_configuration_facts': facts, 'request_configuration_conflicts': conflicts,
        'missing': sorted(set(missing)), 'actual_weights_cache_consumption_verified': False}


class LifecycleTransitionExecutor:
    name = 'lifecycle_transition'
    version = '0.1.0'

    def __init__(self, cfg, *, runner=None, clock=None, wait=None, expected_target_sha256=None):
        self.cfg = cfg; self.runner = runner; self.clock = clock or utcnow
        self.wait = wait or time.sleep
        self.expected_target = expected_target_sha256

    def validate_policy(self, check, values):
        if PROFILES.get(getattr(check, 'collection_profile', None)) != check.id:
            raise ValueError('TRANSITION_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def _reader(self, generation):
        return KubernetesLifecycleReader(generation.native_target, generation.env, generation.prepared,
            runner=self.runner, binary=self.cfg.get('kubectl_binary', 'kubectl'),
            duration=self.cfg.get('max_duration_seconds', 300))

    def _capture(self, generation):
        reader = generation.reader; engine = reader.binding['engine']
        packet = capture_session(reader, generation.native_target, container=engine['container'], image=engine['image'],
            prepared=generation.prepared, owner_identity=reader.owner_identity, read_file=reader.read_file,
            image_check=reader.image_check)
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

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        schedule = normalize_policy(policy_values)
        generations, manifest = load_generations(self.cfg, env, policy_values)
        if self.expected_target is not None:
            approved = {row['id']: row['target_sha256'] for row in manifest}
            matches = self.expected_target == approved
            if type(self.expected_target) is str and len(approved) == 1:
                matches = self.expected_target == next(iter(approved.values()))
            if not matches:
                raise ValueError('TRANSITION_EXPLICIT_GENERATION_TARGET_APPROVAL_REQUIRED')
        timeout = self.cfg.get('request_timeout', 30)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError('TRANSITION_CONFIGURATION_INVALID')
        duration = self.cfg.get('max_duration_seconds', 300)
        binary = self.cfg.get('kubectl_binary', 'kubectl')
        if (type(duration) not in (int, float) or not math.isfinite(duration) or not 0 < duration <= 300
                or type(binary) is not str or not binary.strip()):
            raise ValueError('TRANSITION_CONFIGURATION_INVALID')
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

        def sample(phase):
            for generation in generations.values():
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
        # Seal only selected prepared sessions after all transition/recovery probes.
        for generation in generations.values():
            if generation.reader is None:
                continue
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
        sample('sealed')
        projection = project(samples, trials)
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
        summary = {'profile': check.collection_profile, 'manifest': manifest, 'samples': samples,
            'observations': observations, 'engine_started': False, 'transition_control_implemented': False,
            'producer_authenticated': False, 'whole_run_coverage_verified': False}
        workdir = Path(workdir); artifacts = []; workdir.mkdir(parents=True, exist_ok=True)
        try:
            path = workdir / 'lifecycle-transition.json'; path.write_bytes(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=self.name + '/' + self.version))
        except OSError:
            cleanup.append('TRANSITION_ARTIFACT_UNAVAILABLE')
        return EvidenceDraft(status=ExecStatus.ABORTED if cleanup or diagnostics or projection['missing'] else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if self.runner is not None or any(g.target.get('lab') for g in generations.values()) else DataOrigin.DECLARED,
            normal_control={'attempted': before['attempted'], 'succeeded': all(t['normal_shape_verified'] for t in trials if t['case_id'] in PHASES),
                'detail': 'Scheduled normal HTTP shapes; native generation, consumption and admission guarantees are separate observations.'},
            challenges=[{'step': 'passive externally controlled transition', 'attempted': True,
                'observations': {'transition_activation_verified': False}}],
            observations={k: {'value': v, 'collected': True} for k, v in observations.items()}, artifacts=artifacts,
            config={'profile': check.collection_profile, 'manifest': manifest, 'sample_at': policy_values['transition_sample_at']},
            cleanup_errors=cleanup, started_at=started, finished_at=self.clock())
