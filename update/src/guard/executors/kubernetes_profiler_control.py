"""LIF14 control sequence over prepared15; adapter and backend truth stay distinct."""
from copy import deepcopy
from pathlib import Path
import re
import uuid

from guard.adapters import vllm_profiler_sensor as sensor, vllm_profiler_metadata as metadata
from guard.collectors.lifecycle import load_prepared
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_profiler_control import KubernetesProfilerControlHTTP, SLOTS, CONTROL_PATHS
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader, validate_target
from .kubernetes_request_termination import _time
from .lifecycle_session import capture_session

PROFILE = 'kubernetes-profiler-control-v1'
POLICY_TYPES = {'profiler_environment_sha256': 'str', 'profiler_target_sha256': 'str',
    'profiler_prepared_sha256': 'str', 'profiler_validity_start': 'str', 'profiler_validity_end': 'str',
    'profiler_backend_kind': 'str', 'profiler_required_ranks': 'list[str]', 'profiler_case': 'str'}
OBSERVATIONS = {'profiler_control_trials': 'list', 'profiler_control_projections': 'list',
    'profiler_control_api_links': 'list', 'profiler_control_flag_adapter_conflicts': 'list',
    'profiler_control_missing': 'list', 'profiler_next_start_api_linked': 'bool',
    'profiler_backend_collection_stopped_verified': 'bool', 'profiler_trace_output_completed_verified': 'bool',
    'profiler_cross_process_rpc_verified': 'bool', 'profiler_scope_complete': 'bool'}


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values) != set(POLICY_TYPES): raise ValueError
        for key in ('profiler_environment_sha256', 'profiler_target_sha256', 'profiler_prepared_sha256'):
            if type(values[key]) is not str or re.fullmatch('[0-9a-f]{64}', values[key]) is None: raise ValueError
        start, end = (_time(values[k]) for k in ('profiler_validity_start', 'profiler_validity_end'))
        if not 0 < (end - start).total_seconds() <= 600: raise ValueError
        if values['profiler_backend_kind'] != 'torch' or values['profiler_case'] not in {'normal', 'prepared-stop-failure'}: raise ValueError
        ranks = values['profiler_required_ranks']
        if (type(ranks) is not list or not 1 <= len(ranks) <= 64 or len(set(ranks)) != len(ranks)
                or any(type(v) is not str or re.fullmatch('0|[1-9][0-9]?', v) is None or int(v) > 63 for v in ranks)):
            raise ValueError
        return start, end
    except (KeyError, TypeError, ValueError): raise ValueError('PROFILER_CONTROL_POLICY_INVALID') from None


def project_captures(captures, trials):
    """Only exact source/physical process/ASGI identity pairs join HTTP and API.

    Existing metadata validates native15 records. Do not infer an API-to-worker
    cross-process edge or backend status from flags, timestamps or HTTP success.
    """
    conflicts = []; seen = set(); links = []; missing = []
    for number, capture in enumerate(captures):
        data = capture.get('projection')
        if data is None:
            missing.append('profiler_native_projection_unavailable'); continue
        missing.extend(data['missing'])
        session = capture['session']
        for candidate in data['conflict_candidates']:
            key = (session, candidate['call_sha256'])
            if key not in seen:
                seen.add(key); conflicts.append({'capture_index': number, 'session': session, **candidate})
    for trial in trials:
        slot = trial['case_id']
        if slot not in CONTROL_PATHS or not trial.get('attempted'): continue
        point = 'ProfilerAPI.start_profile' if slot.endswith('start') or slot == 'start' else 'ProfilerAPI.stop_profile'
        matched = {}
        for number, capture in enumerate(captures):
            samples = (capture.get('projection') or {}).get('samples', [])
            for end in samples:
                if (end['point'] != point or end['phase'] not in {'RETURN', 'RAISE'}
                        or end['asgi_request_sha256'] != trial['request_id_sha256'] or not end['asgi_invocation_sha256']
                        or not end['registered_handler_observed'] or not end['source_verified']
                        or not end['physical_process_binding_verified'] or not end['recording_closed']): continue
                starts = [s for s in samples if s['recording'] == end['recording'] and s['call_sha256'] == end['call_sha256'] and s['phase'] == 'CALL']
                ends = [s for s in samples if s['recording'] == end['recording'] and s['call_sha256'] == end['call_sha256'] and s['phase'] != 'CALL']
                stable = ('point', 'parent_call_sha256', 'owner_sha256', 'asgi_request_sha256',
                    'asgi_invocation_sha256', 'registered_handler_observed', 'is_start', 'state_before')
                if (len(starts) != 1 or len(ends) != 1 or starts[0]['seq'] >= end['seq']
                        or not starts[0]['source_verified'] or not starts[0]['physical_process_binding_verified']
                        or any(starts[0][key] != end[key] for key in stable)): continue
                key = (capture['session'], end['pid'], end['start_ticks'], end['call_sha256'])
                matched[key] = {'case_id': slot, 'capture_index': number, 'session': capture['session'],
                    'pid': end['pid'], 'start_ticks': end['start_ticks'], 'call_sha256': end['call_sha256'],
                    'phase': end['phase'], 'event_ref': [end['recording'], end['seq']],
                    'request_id_sha256': trial['request_id_sha256'], 'api_status_code': end['api_status_code'],
                    'http_status_matches': bool(trial.get('binding_verified_before_after') and trial.get('capture_complete')
                        and end['phase'] == 'RETURN' and trial.get('outcome') == end['api_status_code'])}
        if len(matched) == 1: links.extend(matched.values())
        else: missing.append('profiler_control_api_unique_binding_unavailable:' + slot)
    return {'api_links': links, 'flag_adapter_conflicts': conflicts, 'missing': sorted(set(missing))}


class KubernetesProfilerControlExecutor:
    name = 'profiler_observation'; version = '0.1.0'; collection_profile = PROFILE

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg; self.runner = runner; self.expected_target_sha256 = expected_target_sha256

    def validate_policy(self, check, values):
        if check.id != 'LIF-14' or getattr(check, 'collection_profile', None) != PROFILE:
            raise ValueError('PROFILER_CONTROL_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def _capture(self, reader, target, prepared):
        engine = reader.binding['engine']
        return capture_session(reader, target, container=engine['container'], image=engine['image'], prepared=prepared,
            owner_identity=reader.owner_identity, read_file=reader.read_file, image_check=reader.image_check)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values); start, end = normalize_policy(policy_values)
        try:
            if set(self.cfg) - {'target', 'prepared', 'kubectl_binary', 'max_duration_seconds'}: raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id, env.model.revision)): raise ValueError
            with Path(self.cfg['target']).open('rb') as stream: target = strict_json(stream.read(65537), limit=65536)
            prepared = load_prepared(self.cfg['prepared']); digest = sha256_hex(canonical_json(target))
            if (digest != policy_values['profiler_target_sha256'] or env.ref().sha256 != policy_values['profiler_environment_sha256']
                    or self.expected_target_sha256 is not None and self.expected_target_sha256 != digest
                    or prepared['profile'] != sensor.PROFILE or sha256_hex(canonical_json(prepared)) != policy_values['profiler_prepared_sha256']
                    or not start <= utcnow() <= end): raise ValueError
            native_target = {'schema': 'guard-kubernetes-lifecycle-target/1', 'lab': target['lab'],
                'run_id': target['kubernetes']['run_id'], 'environment_ref': env.ref().model_dump(mode='json'),
                'kubernetes': {k: target['kubernetes'][k] for k in ('context', 'engine')}}
            validate_target(native_target, env)
        except (KeyError, ValueError, TypeError, OSError, RuntimeError):
            raise ValueError('PROFILER_CONTROL_CONFIGURATION_INVALID') from None
        transport = KubernetesProfilerControlHTTP(target, env, runner=self.runner,
            duration=self.cfg.get('max_duration_seconds', 180), binary=self.cfg.get('kubectl_binary', 'kubectl'))
        started = utcnow(); reader = None; captures = []; trials = []; diagnostics = []; cleanup = []; sealed = False
        token = uuid.uuid4().hex
        try:
            reader = KubernetesLifecycleReader(native_target, env, prepared, runner=self.runner,
                duration=self.cfg.get('max_duration_seconds', 180), binary=self.cfg.get('kubectl_binary', 'kubectl'))
            reader.snapshot()
        except Exception: diagnostics.append('PROFILER_CONTROL_PREFLIGHT_NATIVE_UNAVAILABLE')

        def capture(phase):
            row = {'phase': phase, 'session': prepared['session'], 'native': None, 'projection': None}
            try:
                if reader is None: raise ValueError
                row['native'] = self._capture(reader, native_target, prepared)
                row['projection'] = metadata.project(row['native'], policy_values['profiler_required_ranks'])
                if not row['native'].get('recorded_files_covered'): diagnostics.append('PROFILER_CONTROL_NATIVE_COVERAGE_UNAVAILABLE')
            except Exception: diagnostics.append('PROFILER_CONTROL_CAPTURE_UNAVAILABLE:' + phase)
            captures.append(row)

        for slot in SLOTS:
            trial = {'case_id': slot, 'request_id_sha256': sha256_hex(('byul-lif14-' + token + '-' + slot).encode()),
                'attempted': False, 'normal_shape_verified': False, 'profiler_control_response_success': False,
                'started_at': None, 'finished_at': None, 'policy_window_verified': False}
            trials.append(trial)
            if slot != 'normal-before' and slot != 'normal-after' and not trials[0].get('normal_shape_verified'):
                trial['diagnostic'] = 'PROFILER_CONTROL_NORMAL_PRECONDITION_UNAVAILABLE'; continue
            try:
                now = utcnow()
                if not start <= now <= end: raise ValueError
                trial.update(attempted=True, started_at=now.isoformat())
                trial.update(transport.perform(slot, 'byul-lif14-' + token + '-' + slot))
            except Exception:
                diagnostics.append('PROFILER_CONTROL_REQUEST_UNAVAILABLE:' + slot)
                # A validated response fact can precede later shape/follow-up failure.
                rows = transport.report()['requests']
                if rows and rows[-1]['case_id'] == slot: trial.update(rows[-1])
            finally:
                if trial['attempted']: trial['finished_at'] = utcnow().isoformat()
                trial['policy_window_verified'] = bool(trial['started_at'] and start <= _time(trial['started_at']) <= _time(trial['finished_at']) <= end)
                if trial['attempted'] and not trial['policy_window_verified']: diagnostics.append('PROFILER_CONTROL_POLICY_WINDOW_UNVERIFIED')
            if slot in {'stop', 'next-stop'}: capture(slot)
        if reader is not None:
            try: sealed = reader.seal_session().get('sealed') is True
            except Exception: pass
            if not sealed: cleanup.append('PROFILER_CONTROL_SESSION_SEAL_UNAVAILABLE')
        capture('final')
        http = transport.report(); cleanup.extend(http['cleanup_errors'])
        if reader is not None:
            try:
                if reader.report().get('parent_cleanup_unverified'): cleanup.append('PROFILER_CONTROL_READER_CLEANUP_UNVERIFIED')
            except Exception: cleanup.append('PROFILER_CONTROL_READER_REPORT_UNAVAILABLE')
        projection = project_captures(captures, trials)
        normal_slots = [t for t in trials if t['case_id'] not in CONTROL_PATHS]
        if not all(t['normal_shape_verified'] for t in normal_slots): diagnostics.append('PROFILER_CONTROL_NORMAL_REQUEST_UNAVAILABLE')
        stop_attempts = [t for t in trials if t['case_id'] in {'stop', 'next-stop'} and t['attempted']]
        if any(not t.get('profiler_control_response_success') for t in stop_attempts): cleanup.append('PROFILER_CONTROL_STOP_RESPONSE_UNVERIFIED')
        by_slot = {t['case_id']: t for t in trials}
        if any(by_slot[a]['attempted'] and not by_slot[b]['attempted'] for a, b in (('start', 'stop'), ('next-start', 'next-stop'))):
            cleanup.append('PROFILER_CONTROL_STOP_NOT_ATTEMPTED')
        values = {'profiler_control_trials': trials, 'profiler_control_projections': [c['projection'] for c in captures],
            'profiler_control_api_links': projection['api_links'], 'profiler_control_flag_adapter_conflicts': projection['flag_adapter_conflicts'],
            'profiler_control_missing': sorted(set(projection['missing'] + diagnostics + [
                'independent_backend_stop_trace_and_transfer_unverified', 'prepared_fault_activation_unverified',
                'next_start_API_success_is_not_next_backend_collection'])),
            'profiler_next_start_api_linked': any(r['case_id'] == 'next-start' and r['http_status_matches'] for r in projection['api_links']),
            'profiler_backend_collection_stopped_verified': False, 'profiler_trace_output_completed_verified': False,
            'profiler_cross_process_rpc_verified': False, 'profiler_scope_complete': False}
        summary = {'profile': PROFILE, 'declared_case': policy_values['profiler_case'], 'policy_values': deepcopy(policy_values),
            'trials': trials, 'captures': captures, 'transport': http, 'observations': values,
            'selected_session_sealed': sealed, 'faults_injected': False, 'engine_started': False, 'whole_scope_complete': False}
        artifacts = []
        try:
            path = Path(workdir) / 'profiler-control.json'; path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream: stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=self.name + '/' + self.version))
        except OSError: cleanup.append('PROFILER_CONTROL_ARTIFACT_UNAVAILABLE')
        gap = bool(diagnostics or cleanup or http['collection_gap']
            or any(v.startswith('profiler_control_api_unique_binding_unavailable') for v in projection['missing']))
        return EvidenceDraft(status=ExecStatus.ABORTED if gap else ExecStatus.COMPLETED, data_origin=DataOrigin.EXAMPLE,
            normal_control={'attempted': trials[0]['attempted'], 'succeeded': all(t['normal_shape_verified'] for t in normal_slots),
                'detail': 'Closed API normal controls only; backend collection/output/next-start remain independently unverified.'},
            challenges=[{'step': 'observe prepared profiler stop failure and next start', 'attempted': any(t['attempted'] for t in stop_attempts),
                'observations': {'fault_activation_verified': False}}],
            observations={k: {'value': v, 'collected': True} for k, v in values.items()}, artifacts=artifacts,
            cleanup_errors=cleanup, config={'profile': PROFILE, 'target_sha256': digest, 'prepared_sha256': sha256_hex(canonical_json(prepared))},
            started_at=started, finished_at=utcnow())
