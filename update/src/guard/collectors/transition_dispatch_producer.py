"""LIF-06 source-bound V1 model dispatch support, distinct from scheduler allocation.

Raw call/session/process/source references survive projection. This producer is
unregistered until GPU confirmation. Closed dispatches do not establish weights
bytes, individual KV reads, reload atomicity, admission, or live rollback.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from guard.adapters import vllm_transition_dispatch_callback as callback
from guard.adapters import vllm_worker_shard_callback as worker
from . import gpu_common_inputs as contract

NAME = 'guard-engine-callback-transition-dispatch'
VERSION = '1'
LIMITATIONS = ('TRANSITION_WEIGHTS_CONTENT_UNOBSERVED', 'TRANSITION_KV_KERNEL_READ_UNOBSERVED',
               'TRANSITION_ADMISSION_ATTEMPT_UNLINKED', 'TRANSITION_RELOAD_ATOMICITY_UNOBSERVED')


def producer():
    digest = hashlib.sha256()
    for path in (Path(callback.__file__), Path(worker.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': NAME, 'version': VERSION, 'source_sha256': digest.hexdigest()}


def record(rows, *, pod, environment_ref, target_sha256, prepared_sha256, session, source_ended=True):
    codes, bindings, events, identities = set(), [], [], {}
    process_keys = {}
    expected_session = hashlib.sha256(session.encode()).hexdigest()
    for row in rows:
        if type(row) is not dict:
            codes.add('TRANSITION_DISPATCH_ROW_INVALID'); continue
        if row.get('phase') == 'GAP':
            codes.add('TRANSITION_DISPATCH_SOURCE_GAP')
            if contract._check(row.get('code'), 'id'):
                codes.add(row['code'])
            continue
        if row.get('schema') != callback.SCHEMA:
            continue
        phase = row.get('phase')
        if (row.get('point') != 'GPUModelRunner._model_forward' or phase not in ('CALL', 'RETURN', 'RAISE')
                or row.get('entry_qualified_before') is not True
                or (phase != 'CALL' and row.get('entry_qualified_after') is not True)
                or row.get('session_sha256') != expected_session
                or row.get('source_file_sha256') != callback.cache.base.SOURCE_SHA[callback.RUNNER]
                or row.get('runtime_context_injected') is not False):
            codes.add('TRANSITION_DISPATCH_ENTRY_UNQUALIFIED'); continue
        f = row.get('fields') or {}
        if type(f) is not dict:
            codes.add('TRANSITION_DISPATCH_ROW_INVALID'); continue
        identity = (row.get('process_sha256'), f.get('pid'), f.get('start_ticks'), f.get('rank'))
        if (not contract._check(identity[0], 'sha') or not all(type(v) is int and v >= 0 for v in identity[1:])
                or identity[1] == 0):
            codes.add('TRANSITION_DISPATCH_IDENTITY_UNAVAILABLE'); continue
        key = identity[1:]
        if key in process_keys and process_keys[key] != identity[0]:
            codes.add('TRANSITION_DISPATCH_PROCESS_MISMATCH'); continue
        process_keys[key] = identity[0]
        if identity not in identities:
            if len(bindings) == 64:
                codes.add('TRANSITION_DISPATCH_BINDING_BOUND'); continue
            identities[identity] = len(bindings)
            bindings.append({'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://'),
                             'restart_count': pod['restart_count'], 'worker_pid': identity[1],
                             'start_ticks': identity[2], 'rank': identity[3]})
        requests = f.get('requests')
        if type(requests) is not list or not requests or len(requests) > callback.MAX_ITEMS:
            # Startup/profile calls without a request are never admitted request rows.
            if requests != []:
                codes.add('TRANSITION_DISPATCH_REQUESTS_UNAVAILABLE')
            continue
        stamp = row.get('operation_started_ns') if phase == 'CALL' else row.get('operation_finished_ns')
        values = {key: f.get(key) for key in ('model_sha256', 'weights_sha256', 'kv_sha256')}
        refs = {'native_session_sha256': row.get('session_sha256'), 'native_process_sha256': identity[0],
                'native_call_sha256': row.get('call_sha256'), 'source_sha256': row.get('source_file_sha256'),
                'entry_sha256': row.get('entry_code_sha256')}
        if not contract._check(stamp, 'int') or not all(contract._check(v, 'sha') for v in (*values.values(), *refs.values())):
            codes.add('TRANSITION_DISPATCH_FACT_UNAVAILABLE'); continue
        for req in requests:
            if (type(req) is not dict or set(req) != {'request_sha256', 'attempt_sha256'}
                    or not all(contract._check(v, 'sha') for v in req.values())):
                codes.add('TRANSITION_DISPATCH_REQUEST_IDENTITY_UNAVAILABLE'); continue
            if len(events) >= contract.MAX_EVENTS:
                codes.add('TRANSITION_DISPATCH_EVENT_BOUND'); break
            events.append({'seq': len(events), 'event': {'CALL': 'forward_entered', 'RETURN': 'forward_returned', 'RAISE': 'forward_failed'}[phase],
                           'binding': identities[identity], 'at_ms': stamp // 10**6, **req, **values, **refs})
    if not bindings:
        raise ValueError('TRANSITION_DISPATCH_NO_WORKER_IDENTITY')
    value = {'schema': contract.SCHEMA, 'kind': 'transition-dispatch', 'producer': producer(),
             'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
             'session': session, 'bindings': bindings, 'events': events,
             'completeness': {'source_ended': source_ended and not codes, 'dropped': 0, 'diagnostic_codes': sorted(codes)}}
    return value, contract.validate(value, kind='transition-dispatch', environment_ref=environment_ref,
                                   target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


def projection(value):
    """A consumer can retain these dispatch facts alongside B's original native index.

    No output is an admitted native row or permission to flip B's verification flag.
    """
    return {'dispatch_facts': [dict(r, worker=dict(value['bindings'][r['binding']])) for r in value['events']],
            'actual_weights_cache_consumption_verified': False, 'missing': list(LIMITATIONS),
            'source_diagnostic_codes': list(value['completeness']['diagnostic_codes'])}
