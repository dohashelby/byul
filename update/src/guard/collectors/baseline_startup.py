"""Consume exact baseline candidate records without relabeling IPC READY.

This projection is evidence preparation, outside registered product profiles.
Local component failure/constructor propagation is distinct from parent-process
aggregate failure delivery and external admission; neither is inferred by time.
"""
from __future__ import annotations

from guard.adapters import vllm_baseline_startup as sensor
from guard.adapters import vllm_worker_readiness as callback
from guard.adapters import vllm_worker_startup_contract as contract

FIELDS = frozenset({'schema', 'call_sha256', 'parent_call_sha256', 'started_ns', 'source_sha256',
    'runtime', 'point', 'phase', 'binding', 'finished_ns', 'exception_kind', 'rank', 'workers',
    'returned_worker', 'worker_snapshot', 'producer_authenticated', 'whole_scope_complete', 'service_gate_verified'})


def consume_worker_snapshot(value):
    """Original producer default and unsupported records remain valid gaps."""
    value = contract.closed_callback(value, callback)
    data = value['metadata']
    transition = data.get('call_transition', {})
    return {'point': value['point'], 'phase': value['phase'], 'status': value['status'],
            'rank': data.get('rank'), 'owner_token': data.get('owner_token'),
            'paired': transition.get('paired') is True, 'returned': transition.get('returned'),
            'gaps': list(value['gaps']), 'source_qualified': False, 'whole_scope_complete': False}


def _uint(value):
    return type(value) is int and 0 <= value < 2**63


def _handles(value):
    if value is None:
        return
    if type(value) is not list or not 1 <= len(value) <= 64:
        raise ValueError
    ranks = set()
    for row in value:
        if (type(row) is not dict or set(row) != {'rank', 'pid'} or not _uint(row['rank'])
                or row['rank'] > 63 or row['rank'] in ranks
                or row['pid'] is not None and (not _uint(row['pid']) or row['pid'] == 0)):
            raise ValueError
        ranks.add(row['rank'])


def _validate(row, binding):
    if (type(row) is not dict or set(row) != FIELDS or row['schema'] != sensor.SCHEMA
            or row['binding'] != binding or not contract.fingerprint(row['call_sha256'])
            or row['parent_call_sha256'] is not None and not contract.fingerprint(row['parent_call_sha256'])
            or row['runtime'] not in {'cpython-3.12.3', 'cpython-3.12.8'}
            or not _uint(row['started_ns']) or row['phase'] not in {'CALL', 'RETURN', 'RAISE'}
            or any(row[k] is not False for k in ('producer_authenticated', 'whole_scope_complete', 'service_gate_verified'))):
        raise ValueError
    matches = [source for source in sensor.ROWS.values() if row['point'] in source['functions']]
    if len(matches) != 1 or row['source_sha256'] != matches[0]['sha256']:
        raise ValueError
    if row['rank'] is not None and (not _uint(row['rank']) or row['rank'] > 63):
        raise ValueError
    if row['phase'] == 'CALL':
        if row['finished_ns'] is not None or row['exception_kind'] is not None:
            raise ValueError
    elif not _uint(row['finished_ns']) or row['finished_ns'] < row['started_ns']:
        raise ValueError
    elif row['exception_kind'] not in ({'exception', 'base-exception'} if row['phase'] == 'RAISE' else {None}):
        raise ValueError
    _handles(row['workers'])
    _handles(row['returned_worker'])
    if row['workers'] is not None and (row['point'] != 'MultiprocExecutor._init_executor' or row['phase'] == 'CALL'):
        raise ValueError
    if row['returned_worker'] is not None and (row['point'] != 'WorkerProc.make_worker_process' or row['phase'] != 'RETURN'):
        raise ValueError
    snapshot = row['worker_snapshot']
    if row['point'] in sensor.ROWS[sensor.WORKER]['functions']:
        consume_worker_snapshot(snapshot)
        if (snapshot['point'] != row['point'] or snapshot['phase'] != row['phase']
                or snapshot['binding'] != {**binding, 'call_id_sha256': row['call_sha256']}):
            raise ValueError
    elif snapshot is not None:
        raise ValueError


def project(records, *, binding):
    """Pair local calls and preserve readiness/model distinctions with exact scope."""
    try:
        from guard.adapters.vllm_slot_mask import _binding
        if (type(binding) is not dict or _binding(binding) != binding
                or not all(binding.get(k) for k in ('pid', 'session_id_sha256', 'process_start_sha256'))
                or type(records) is not list or len(records) > 2048):
            raise ValueError
        for row in records:
            _validate(row, binding)
        calls = {}
        for row in records:
            calls.setdefault(row['call_sha256'], []).append(row)
        paired = {}
        missing = []
        for token, rows in calls.items():
            stable = ('point', 'parent_call_sha256', 'started_ns', 'source_sha256', 'runtime', 'binding', 'rank')
            if (len(rows) != 2 or rows[0]['phase'] != 'CALL' or rows[1]['phase'] not in {'RETURN', 'RAISE'}
                    or any(rows[0][key] != rows[1][key] for key in stable)):
                missing.append('unique_ordered_call_pair_unavailable')
                continue
            paired[token] = rows[1]
        preparations, propagation, handshakes = [], [], []
        for token, row in paired.items():
            point = row['point']
            parent = paired.get(row['parent_call_sha256'])
            nested = bool(parent and parent['started_ns'] <= row['started_ns'] <= row['finished_ns'] <= parent['finished_ns'])
            if point in sensor.ROWS[sensor.WORKER]['functions']:
                fact = consume_worker_snapshot(row['worker_snapshot'])
                valid = bool(fact['paired'] and nested and parent['point'] == 'WorkerProc.__init__'
                             and fact['rank'] == parent['rank'])
                preparations.append({**fact, 'call_sha256': token,
                    'constructor_call_sha256': parent['call_sha256'] if valid else None,
                    'constructor_interval_observed': valid, 'python_body_return_observed': valid and row['phase'] == 'RETURN'})
                if point in {'Worker.init_device', 'Worker.load_model'} and valid and row['phase'] == 'RAISE':
                    propagation.append({'rank': fact['rank'], 'point': point, 'component_call_sha256': token,
                        'constructor_call_sha256': parent['call_sha256'],
                        'component_failure_constructor_failure_observed': parent['phase'] == 'RAISE',
                        'component_failure_constructor_return_candidate': parent['phase'] == 'RETURN',
                        'parent_process_aggregate_verified': False, 'confirmed_violation': False})
            elif point == 'WorkerProc.wait_for_startup':
                handshakes.append({'call_sha256': token, 'phase': row['phase'],
                    'maker_interval_observed': nested and parent['point'] == 'WorkerProc.make_worker_process',
                    'transport_handshake_return_observed': row['phase'] == 'RETURN',
                    'model_ready_verified': False})
        return {'preparation_calls': preparations, 'local_failure_propagation': propagation,
                'transport_handshakes': handshakes, 'paired_calls': len(paired),
                'missing': sorted(set(missing + ['cross_process_worker_generation_and_aggregate_unverified',
                    'failure_activation_and_repaired_launch_unverified', 'gpu_completion_and_all_preparation_unverified',
                    'external_service_admission_unverified', 'producer_authentication_unavailable'])),
                'producer_authenticated': False, 'whole_scope_complete': False, 'service_gate_verified': False}
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('BASELINE_STARTUP_RECORD_INVALID') from None
