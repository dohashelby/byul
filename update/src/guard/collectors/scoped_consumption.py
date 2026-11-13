"""Worker-scoped consumption writer/reader. Legacy /1 stays immutable.

This wire version preserves each source call. Coverage is checked against an
explicit per-stage rank plan, never inferred from the workers which survived.
Worker admission IDs identify native admissions, not scheduler retry counts.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from guard.adapters import vllm_consumption_callback as v1, vllm_consumption_v2_callback as v2
from . import gpu_common_inputs as legacy
from .producer_release import Qualification, release_digest

SCHEMA = 'guard-gpu-common-input/2'
KIND = 'runner-consumption'
NAME = 'guard-engine-callback-scoped-runner-consumption'
VERSION = '2'
FIELDS = ('prompt_tokens', 'max_token_id', *v1.FIELDS)
from .producer_approvals import legacy_scoped
SUPPORTED_PRODUCERS, QUALIFICATIONS = legacy_scoped()


def producer():
    from guard.adapters import vllm_scoped_consumption_callback as scoped
    from guard.adapters import vllm_cache_callback as cache
    from . import producer_release, producer_approvals
    paths = [Path(__file__), Path(legacy.__file__), Path(producer_release.__file__), Path(producer_approvals.__file__), Path(scoped.__file__), Path(v1.__file__), Path(v2.__file__),
             Path(cache.__file__), Path(cache.base.__file__)]
    for name in ('observer_runtime', 'vllm_worker_generation_contract', 'vllm_worker_startup_contract', 'vllm_worker_shard_callback'):
        paths.append(Path(scoped.__file__).with_name(name + '.py'))
    paths.extend((Path(cache.__file__).with_name('vllm_observer_pin_rows.py'),
                  Path(cache.__file__).parent / 'assets/vllm_observer_pin_rows.json'))
    return {'name': NAME, 'version': VERSION, 'source_sha256': release_digest(SCHEMA, paths)}


def _sha(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _uint(value):
    return type(value) is int and 0 <= value < 2**63


def validate(value, *, environment_ref, target_sha256, prepared_sha256, session, kind=KIND):
    keys = {'schema', 'kind', 'producer', 'environment_ref', 'target_sha256', 'prepared_sha256', 'session',
            'bindings', 'events', 'expected_stage_ranks', 'qualification_scope', 'expected_requests', 'completeness'}
    if type(value) is not dict or set(value) != keys or value['schema'] != SCHEMA or value['kind'] != KIND or kind != KIND:
        raise legacy.GpuInputError('GPU_INPUT_V2_ENVELOPE_INVALID')
    for key, expected in (('environment_ref', environment_ref), ('target_sha256', target_sha256),
                          ('prepared_sha256', prepared_sha256), ('session', session)):
        if value[key] != expected:
            raise legacy.GpuInputError('GPU_INPUT_SCOPE_MISMATCH')
    if not _sha(target_sha256) or not _sha(prepared_sha256) or type(session) is not str or legacy._SESSION.fullmatch(session) is None:
        raise legacy.GpuInputError('GPU_INPUT_SCOPE_MISMATCH')
    p = value['producer']
    if (type(p) is not dict or set(p) != {'name', 'version', 'source_sha256'} or not _sha(p['source_sha256'])
            or not legacy._check(p['name'], 'id') or not legacy._check(p['version'], 'id')):
        raise legacy.GpuInputError('GPU_INPUT_PRODUCER_INVALID')
    bindings = value['bindings']
    if type(bindings) is not list or not 1 <= len(bindings) <= 128:
        raise legacy.GpuInputError('GPU_INPUT_BINDING_INVALID')
    identities = set()
    for b in bindings:
        if type(b) is not dict or set(b) != {'pod_uid', 'container_id', 'restart_count', 'worker_pid', 'start_ticks', 'rank', 'role'}:
            raise legacy.GpuInputError('GPU_INPUT_BINDING_INVALID')
        if (not all(legacy._check(b[k], 'id') for k in ('pod_uid', 'container_id'))
                or not _uint(b['restart_count']) or b['start_ticks'] is not None and not _uint(b['start_ticks']) or not _uint(b['worker_pid']) or b['worker_pid'] == 0
                or b['rank'] is not None and not _uint(b['rank']) or b['role'] not in ('api', 'worker')):
            raise legacy.GpuInputError('GPU_INPUT_BINDING_INVALID')
        # Bindings are observations of a process generation, including unknown
        # rank. A later observed rank must not invalidate earlier raw facts.
        identity = (b['pod_uid'], b['container_id'], b['restart_count'], b['worker_pid'], b['start_ticks'], b['role'], b['rank'])
        if identity in identities:
            raise legacy.GpuInputError('GPU_INPUT_BINDING_INVALID')
        identities.add(identity)
    completeness = value['completeness']
    if (type(completeness) is not dict or set(completeness) != {'source_ended', 'dropped', 'diagnostic_codes'}
            or type(completeness['source_ended']) is not bool or not _uint(completeness['dropped'])
            or type(completeness['diagnostic_codes']) is not list
            or not all(legacy._check(c, 'id') for c in completeness['diagnostic_codes'])):
        raise legacy.GpuInputError('GPU_INPUT_COMPLETENESS_INVALID')
    expected = value['expected_stage_ranks']
    if expected is not None and (type(expected) is not dict or set(expected) != {'runner_received', 'sampler_applied'}
            or any(type(ranks) is not list or not ranks or len(ranks) > 64 or not all(_uint(r) and r < 64 for r in ranks) or len(set(ranks)) != len(ranks) for ranks in expected.values())):
        raise legacy.GpuInputError('GPU_INPUT_V2_EXPECTED_SCOPE_INVALID')
    planned = value['expected_requests']
    if planned is not None and (type(planned) is not list or not planned or len(planned) > 256 or not all(_sha(r) for r in planned) or len(set(planned)) != len(planned)):
        raise legacy.GpuInputError('GPU_INPUT_EXPECTED_REQUESTS_INVALID')
    events = value['events']
    if type(events) is not list or len(events) > legacy.MAX_EVENTS:
        raise legacy.GpuInputError('GPU_INPUT_EVENTS_INVALID')
    row_keys = {'seq', 'event', 'binding', 'request_sha256', 'admission_sha256', 'source_call', *FIELDS}
    for seq, row in enumerate(events):
        if (type(row) is not dict or set(row) != row_keys or row['seq'] != seq or type(row['seq']) is not int
                or row['event'] not in ('api_validated', 'api_rejected', 'runner_received', 'sampler_applied')
                or type(row['binding']) is not int or not 0 <= row['binding'] < len(bindings) or not _sha(row['request_sha256'])
                or row['admission_sha256'] is not None and not _sha(row['admission_sha256'])
                or not all(row[k] is None or _uint(row[k]) for k in FIELDS)
                or type(row['source_call']) is not dict or set(row['source_call']) != {'process_sha256', 'call_sha256', 'point'}
                or not all(_sha(row['source_call'][k]) for k in ('process_sha256', 'call_sha256'))
                or not legacy._check(row['source_call']['point'], 'id')):
            raise legacy.GpuInputError('GPU_INPUT_EVENT_INVALID')
        role = 'worker' if row['event'] in ('runner_received', 'sampler_applied') else 'api'
        if bindings[row['binding']]['role'] != role:
            raise legacy.GpuInputError('GPU_INPUT_V2_ACTOR_MISMATCH')
    gaps = list(completeness['diagnostic_codes'])
    if any(b['start_ticks'] is None for b in bindings):
        gaps.append('GPU_INPUT_PROCESS_GENERATION_UNOBSERVED')
    supported = SUPPORTED_PRODUCERS.get((p['name'], p['version'])) == p['source_sha256']
    qualified = any(receipt.matches(p, SCHEMA, value['qualification_scope'], expected) for receipt in QUALIFICATIONS)
    if not supported:
        gaps.append('GPU_INPUT_PRODUCER_UNSUPPORTED')
    if not qualified:
        gaps.append('GPU_INPUT_QUALIFICATION_SCOPE_UNREVIEWED')
    if not completeness['source_ended'] or completeness['dropped']:
        gaps.append('GPU_INPUT_SOURCE_INCOMPLETE')
    if expected is None:
        gaps.append('GPU_INPUT_EXPECTED_SCOPE_UNOBSERVED')
    api = {}
    for row in events:
        if row['event'] == 'api_validated':
            api.setdefault(row['request_sha256'], []).append(row)
    candidates = []
    if planned is None:
        gaps.append('GPU_INPUT_REQUEST_SCOPE_UNOBSERVED')
    elif {r['request_sha256'] for r in events if r['event'] in ('api_validated', 'api_rejected')} != set(planned):
        gaps.append('GPU_INPUT_REQUEST_COVERAGE_MISMATCH')
    if not api:
        gaps.append('GPU_INPUT_REQUIRED_EVENT_MISSING:api_validated')
    for request, admissions in api.items():
        if len(admissions) != 1:
            gaps.append('GPU_INPUT_API_ADMISSION_AMBIGUOUS'); continue
        first = admissions[0]
        for stage in ('runner_received', 'sampler_applied'):
            rows = [r for r in events if r['request_sha256'] == request and r['event'] == stage]
            observed = {bindings[r['binding']]['rank'] for r in rows}
            if not rows:
                gaps.append('GPU_INPUT_REQUIRED_EVENT_MISSING:' + stage)
            if None in observed:
                gaps.append('GPU_INPUT_WORKER_RANK_UNOBSERVED')
            if expected is not None and observed != set(expected[stage]):
                gaps.append('GPU_INPUT_STAGE_RANK_COVERAGE_MISMATCH:' + stage)
            for row in rows:
                if row['admission_sha256'] is None:
                    gaps.append('GPU_INPUT_WORKER_ADMISSION_UNOBSERVED')
                if stage == 'sampler_applied' and not any(r['event'] == 'runner_received' and r['request_sha256'] == request
                        and all(bindings[r['binding']][k] == bindings[row['binding']][k] for k in
                            ('pod_uid', 'container_id', 'restart_count', 'worker_pid', 'start_ticks', 'role'))
                        and r['admission_sha256'] == row['admission_sha256'] and r['seq'] < row['seq'] for r in events):
                    gaps.append('GPU_INPUT_SAMPLER_ADMISSION_NOT_LINKED')
                compared = (*v1.FIELDS, 'prompt_tokens', 'max_token_id') if stage == 'runner_received' else tuple(k for k in v1.FIELDS if k != 'max_tokens')
                for field in compared:
                    if field == 'logit_bias' and first[field] == 0 and row[field] is None:
                        continue
                    if first[field] is None or row[field] is None:
                        gaps.append('GPU_INPUT_CONSUMPTION_FIELD_UNOBSERVED:' + stage + ':' + field)
                    elif (bool(first[field]) != bool(row[field]) if field == 'bad_words' else first[field] != row[field]):
                        candidates.append({'seq': row['seq'], 'request_sha256': request, 'binding': row['binding'],
                                           'field': field, 'reason': 'value_changed_before_consumption'})
    for row in events:
        if row['event'] in ('runner_received', 'sampler_applied') and row['request_sha256'] not in api:
            gaps.append('GPU_INPUT_WORKER_API_NOT_LINKED')
        if row['event'] == 'api_rejected' and any(r['request_sha256'] == row['request_sha256'] and r['event'] in ('runner_received', 'sampler_applied') for r in events):
            candidates.append({'seq': row['seq'], 'reason': 'rejected_input_consumed'})
    return {'kind': KIND, 'checks': ['REQ-08', 'REQ-09', 'REQ-10'], 'supported': supported, 'producer_authenticated': False,
            'measurement_scope': 'functional_security', 'timing_qualified': False, 'runtime_qualified': qualified, 'complete': supported and qualified and not gaps, 'gaps': sorted(set(gaps)), 'generations': len(bindings), 'events': len(events),
            'violation_candidates': candidates}


def record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, expected_stage_ranks=None, qualification_scope=None, truncated=False, requests=None, expected_requests=None):
    """Closed native calls -> scoped events; no cross-worker deduplication."""
    events, bindings, diagnostics, api_pending = [], [], set(), {}
    ordered = []
    for process in processes:
        binding = {}
        calls = {}
        for raw in process['events']:
            if raw.get('phase') == 'GAP':
                diagnostics.add('NATIVE_SOURCE_GAP'); continue
            if raw.get('schema') not in (v1.SCHEMA, v2.SCHEMA):
                continue
            key = (raw.get('process_sha256'), raw.get('call_sha256'), raw.get('point'))
            calls.setdefault(key, []).append(raw)
        for key, pair in calls.items():
            if len(pair) != 2 or {r.get('phase') for r in pair} not in ({'CALL', 'RETURN'}, {'CALL', 'RAISE'}):
                diagnostics.add('NATIVE_CALL_NOT_CLOSED'); continue
            call = next(r for r in pair if r['phase'] == 'CALL'); end = next(r for r in pair if r['phase'] != 'CALL')
            callback = v1 if call.get('schema') == v1.SCHEMA else v2
            module = next((name for name, points in callback.TARGETS.items() if key[2] in points), None)
            sources = getattr(callback, 'SOURCES', v1.cache.base.SOURCE_SHA)
            if (not _sha(key[0]) or not _sha(key[1]) or call.get('entry_qualified_before') is not True or end.get('entry_qualified_after') is not True
                    or module is None or call.get('source_file_sha256') != sources[module] or end.get('source_file_sha256') != sources[module]
                    or call.get('entry_code_sha256') != end.get('entry_code_sha256') or not _sha(call.get('entry_code_sha256'))
                    or call.get('session_sha256') != hashlib.sha256(session.encode()).hexdigest()
                    or end.get('session_sha256') != call.get('session_sha256')
                    or call.get('runtime_context_injected') is not False or end.get('runtime_context_injected') is not False):
                diagnostics.add('NATIVE_CALL_UNQUALIFIED'); continue
            role = 'api' if key[2].startswith('InputProcessor.') else 'worker'
            scope = end.get('consumption_scope') or call.get('consumption_scope') or {}
            if type(scope) is not dict:
                diagnostics.add('NATIVE_SCOPE_MALFORMED'); scope = {}
            rank = scope.get('rank') if role == 'worker' else None
            if not _uint(rank) or rank >= 64:
                rank = None
            pid, ticks = scope.get('pid'), scope.get('start_ticks')
            if pid != process['pid'] or not _uint(ticks):
                diagnostics.add('NATIVE_PROCESS_GENERATION_UNOBSERVED')
                pid, ticks = process['pid'], process['start_ticks']
            elif process['start_ticks'] is not None and ticks != process['start_ticks']:
                diagnostics.add('NATIVE_PROCESS_GENERATION_CHANGED')
            identity = role, rank, ticks
            if identity not in binding:
                if len(bindings) >= 128:
                    diagnostics.add('GPU_INPUT_BINDING_LIMIT'); continue
                binding[identity] = len(bindings)
                bindings.append({'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                    'restart_count': pod['restart_count'], 'worker_pid': pid, 'start_ticks': ticks, 'role': role, 'rank': rank})
            ordered.append((end.get('operation_finished_ns') or 0, len(ordered), binding[identity], call, end))
    def add(event, binding, request, call, values, admission=None):
        if requests is None or request in requests:
            if len(events) >= legacy.MAX_EVENTS:
                diagnostics.add('GPU_INPUT_EVENT_LIMIT'); return
            events.append({'seq': len(events), 'event': event, 'binding': binding, 'request_sha256': request,
                'admission_sha256': admission, 'source_call': {k: call[k] for k in ('process_sha256', 'call_sha256', 'point')},
                **{name: values.get(name) for name in FIELDS}})
    for _, _, binding, call, end in sorted(ordered):
        point = call['point']; f = call.get('fields') or {}; scope = end.get('consumption_scope') or call.get('consumption_scope') or {}
        if type(f) is not dict or type(scope) is not dict or type(scope.get('admissions', {})) is not dict:
            diagnostics.add('NATIVE_SCOPE_MALFORMED'); continue
        request = f.get('request_sha256')
        pending_key = call['process_sha256'], request
        if point in ('InputProcessor._validate_model_input', 'InputProcessor._validate_params'):
            current = api_pending.setdefault(pending_key, {'values': {}, 'rejected': False})
            current['values'].update(f.get('params') or {k: f.get(k) for k in ('prompt_tokens', 'max_token_id')})
            current['rejected'] |= end['phase'] == 'RAISE'
        elif point == 'InputProcessor.process_inputs':
            current = api_pending.pop(pending_key, None)
            if current is None:
                diagnostics.add('API_VALIDATION_UNOBSERVED'); continue
            add('api_rejected' if current['rejected'] or end['phase'] == 'RAISE' else 'api_validated', binding, request, call, current['values'])
        elif end['phase'] == 'RETURN' and point in ('GPUModelRunner._update_states', 'GPUModelRunner.add_requests'):
            for row in f.get('received') or []:
                add('runner_received', binding, row['request_sha256'], call, {**row, **(row.get('params') or {})}, scope.get('admissions', {}).get(row['request_sha256']))
        elif end['phase'] == 'RETURN' and point in ('GPUModelRunner._sample', 'GPUModelRunner.sample'):
            for row in f.get('applied') or []:
                add('sampler_applied', binding, row['request_sha256'], call, row, scope.get('admissions', {}).get(row['request_sha256']))
    if truncated:
        diagnostics.add('NATIVE_READ_TRUNCATED')
    value = {'schema': SCHEMA, 'kind': KIND, 'producer': producer(), 'environment_ref': deepcopy(environment_ref),
        'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256, 'session': session, 'bindings': bindings, 'events': events,
        'expected_stage_ranks': deepcopy(expected_stage_ranks), 'expected_requests': deepcopy(expected_requests), 'qualification_scope': deepcopy(qualification_scope), 'completeness': {'source_ended': not truncated and not diagnostics,
        'dropped': 0, 'diagnostic_codes': sorted(diagnostics)}}
    return value, validate(value, environment_ref=environment_ref, target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
