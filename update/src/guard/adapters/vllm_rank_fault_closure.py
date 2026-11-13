"""TP2·물리 신호 해제·새 세대의 원 근거를 검증하는 미등록 후보.

기존 단일 rank 자격·판정은 바꾸지 않는다. 같은 요청의 두 rank 관측은
프로세스 사이의 인과적 그룹 소속이나 실제 GPU 복구를 증명하지 않는다.
"""
from hashlib import sha256

from guard.models import canonical_json
from . import vllm_fault_projection as native
from . import vllm_rank_fault_input as source

SCHEMA = 'guard-rank-fault-closure-evidence/1'
FIELDS = frozenset({'schema', 'normal_packet', 'normal_client', 'release_receipt',
                    'recovery_packet', 'recovery_client', 'expected_recovery'})
RESUME_SCHEMA = 'guard-rank-fault-resume-receipt/1'
RESUME_FIELDS = frozenset({'schema', 'scope_sha256', 'injection_sha256', 'ready_receipt_sha256',
    'context', 'signal', 'before_state', 'after_state', 'before_started_ns', 'before_finished_ns',
    'signal_started_ns', 'signal_finished_ns', 'after_started_ns', 'after_finished_ns'})


def _require(value):
    if not value:
        raise ValueError('RANK_FAULT_CLOSURE_EVIDENCE_INVALID')


def _digest(value):
    return sha256(canonical_json(value)).hexdigest()


def _ref(row):
    return {k: row[k] for k in ('index', 'seq', 'capture_sha256')}


def _normal(packet, client, scope, *, after_ns, before_ns=None):
    """고정 소스·닫힌 기록에서 같은 정상 요청의 rank0/1·executor·future를 찾는다."""
    _require(type(client) is dict and type(client.get('outcome')) is int and client['outcome'] == 200
        and client.get('complete') is True and native.control.fingerprint(client.get('external_request_sha256')))
    start, end = client.get('request_started_ns'), client.get('response_finished_ns')
    _require(type(start) is int and type(end) is int and 0 <= after_ns <= start < end < 2**63
        and (before_ns is None or end < before_ns))
    native.control.clock_context(client.get('clock_context'))
    rows, processes, covered, owner = native._rows(packet, scope)
    _require(covered and all(p['physical'] and p['closed'] for p in processes.values())
        and all(native._clock(r['context']) == client['clock_context'] for r in rows))
    selections = [r for r in rows if r['physical'] and r['event'] == 'SELECTION'
        and r['p'].get('point') == 'InputProcessor.assign_request_id' and r['p'].get('phase') == 'RETURN'
        and r['p'].get('source_sha256') == native.metadata.control_source()[native.sensor.INPUT]
        and r['p'].get('metadata', {}).get('request_selection', {}).get('asgi_request_sha256') == client['external_request_sha256']
        and start <= r['time'] <= end]
    _require(len(selections) == 1)
    selected = selections[0]['p']['metadata']['request_selection']
    request = selected.get('selected_request_id_sha256')
    _require(native.control.fingerprint(request) and native.control.fingerprint(selected.get('asgi_invocation_sha256')))
    calls = [r for r in source._paired(rows) if r['p']['phase'] == 'RETURN'
        and r['p']['exception_kind'] is None and r['p']['native_request_sha256'] == [request]
        and selections[0]['time'] <= r['p']['operation_started_ns'] <= source._time(r) <= end]
    workers = [r for r in calls if r['p']['point'] == 'WorkerProc._execute_worker_rpc'
        and r['p']['rpc_method'] == 'execute_model']
    executors = [r for r in calls if r['p']['point'] == 'MultiprocExecutor.execute_model'
        and r['p']['world_size'] == 2 and r['p']['configured_output_rank'] == 0
        and r['p']['executor_is_failed'] is False and r['p']['selected_reply_rank'] == 0
        and r['p']['returned_kind'] == 'future' and r['p']['non_block'] is True]
    futures = [r for r in calls if r['p']['point'] == 'FutureWrapper.result'
        and r['p']['future_has_exception'] is False and r['p']['future_state'] == 'FINISHED'
        and r['p']['selected_reply_rank'] == 0]
    _require(len(workers) == 2 and {r['p']['rank'] for r in workers} == {0, 1}
        and len(executors) == len(futures) == 1)
    workers.sort(key=lambda r: r['p']['rank'])
    executor, future = executors[0], futures[0]
    _require(len({(r['context']['pid'], r['context']['start_ticks']) for r in workers + [executor]}) == 3
        and executor['context'] == future['context']
        and native.control.fingerprint(executor['p']['executor_sha256'])
        and executor['p']['executor_sha256'] == future['p']['executor_sha256'])
    terminal = [r for r in rows if r['physical'] and r['event'] == 'TERMINATION'
        and r['p'].get('point') == 'ASGI.request' and r['p'].get('phase') == 'ASGI_APP_RETURN'
        and r['p'].get('asgi_request_sha256') == client['external_request_sha256']
        and r['p'].get('asgi_invocation_sha256') == selected['asgi_invocation_sha256']
        and r['p'].get('operation_started_ns') == start
        and max(source._time(r) for r in workers + [executor, future]) <= source._time(r) <= r['time'] <= end]
    _require(len(terminal) == 1 and terminal[0]['context'] == selections[0]['context'])
    return {'workers': workers, 'executor': executor, 'request_sha256': request,
        'external_request_sha256': client['external_request_sha256'], 'completed_ns': end,
        'clock_context': client['clock_context'], 'owner': owner,
        'source_refs': [_ref(r) for r in [selections[0], *workers, executor, future, terminal[0]]]}


def _resume(value, injection, scope, active, release):
    """동일 세대의 정지 전후 상태와 SIGCONT 호출 구간을 별도 영수증으로 받는다."""
    _require(type(value) is dict and set(value) == RESUME_FIELDS and value['schema'] == RESUME_SCHEMA
        and scope['fault_kind'] == 'delay' and value['signal'] == 18 and type(value['signal']) is int
        and value['scope_sha256'] == _digest(scope) and value['injection_sha256'] == _digest(injection)
        and value['ready_receipt_sha256'] == active['p']['ready_receipt_sha256']
        and value['context'] == active['context'] and value['before_state'] == 'T'
        and value['after_state'] in {'R', 'S', 'D', 'I'})
    stamps = [value[k] for k in ('before_started_ns', 'before_finished_ns', 'signal_started_ns',
                                'signal_finished_ns', 'after_started_ns', 'after_finished_ns')]
    _require(all(type(t) is int and 0 <= t < 2**63 for t in stamps)
        and stamps == sorted(stamps) and injection['state_observed_ns'] <= stamps[0]
        and stamps[3] <= release['time'])
    return max(stamps[-1], release['time'])


def _expected(value, recovery, normal):
    """예상 세대는 원 packet과 별도로 받아 실제 새 세대와 정확히 대조한다."""
    _require(type(value) is dict and set(value) == {'workers', 'executor_context', 'executor_sha256'})
    expected_workers = [{'rank': r['p']['rank'], 'context': r['context']} for r in recovery['workers']]
    _require(value['workers'] == expected_workers and value['executor_context'] == recovery['executor']['context']
        and value['executor_sha256'] == recovery['executor']['p']['executor_sha256'])
    identity = lambda r: (r['context']['pid'], r['context']['start_ticks'], r['context']['pid_namespace'])
    _require(all(identity(a) != identity(b) for a, b in zip(normal['workers'], recovery['workers']))
        and identity(normal['executor']) != identity(recovery['executor'])
        and normal['executor']['p']['executor_sha256'] != recovery['executor']['p']['executor_sha256'])


def inspect(value, packet, injection, scope, active, release):
    """부분별 사실만 반환한다. 기존 TP1 자격을 TP2 수락으로 넓히지 않는다."""
    result = {'tp2_normal_observed': False, 'physical_release_verified': False,
        'new_generation_recovery_observed': False, 'cross_process_group_verified': False,
        'scope_complete': False, 'release_ns': None, 'recovery': None,
        'source_refs': [], 'diagnostic_codes': ['RANK_FAULT_TP2_CAUSAL_GROUP_UNVERIFIED']}
    try:
        _require(type(value) is dict and set(value) == FIELDS and value['schema'] == SCHEMA)
        normal = _normal(value['normal_packet'], value['normal_client'], scope, after_ns=0, before_ns=active['time'])
        _require(normal['clock_context'] == native._clock(active['context'])
            and normal['workers'][scope['rank']]['context'] == active['context']
            and normal['external_request_sha256'] != scope['external_request_sha256']
            and normal['request_sha256'] != active['p']['native_request_sha256'])
        # ARM 때 같은 요청에 두 rank가 참여한 관측 사실만 대응시킨다.
        pre, _, covered, owner = native._rows(injection['pre_signal_packet'], scope, active=True)
        _require(covered and owner == normal['owner'])
        for worker in normal['workers']:
            calls = [r for r in pre if r['physical'] and r['event'] == 'RANK_FAILURE'
                and r['p']['point'] == 'WorkerProc._execute_worker_rpc' and r['p']['phase'] == 'CALL'
                and r['p']['source_verified'] and r['p']['rank'] == worker['p']['rank']
                and r['p']['native_request_sha256'] == [active['p']['native_request_sha256']]
                and r['context'] == worker['context'] and normal['completed_ns'] < r['time'] <= active['time']]
            _require(len(calls) == 1)
        executors = [r for r in source._paired(pre) if r['p']['point'] == 'MultiprocExecutor.execute_model'
            and r['p']['phase'] == 'RETURN' and r['p']['world_size'] == 2 and r['p']['configured_output_rank'] == 0
            and r['context'] == normal['executor']['context']
            and r['p']['executor_sha256'] == normal['executor']['p']['executor_sha256']
            and r['p']['native_request_sha256'] == [active['p']['native_request_sha256']]
            and normal['completed_ns'] < source._time(r) <= active['time']]
        _require(len(executors) == 1)
        result['tp2_normal_observed'] = True
        result['source_refs'] += normal['source_refs']
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        result['diagnostic_codes'].append('RANK_FAULT_TP2_NORMAL_UNVERIFIED')
        return result
    try:
        _require(release is not None)
        result['release_ns'] = _resume(value['release_receipt'], injection, scope, active, release)
        result['physical_release_verified'] = True
        result['source_refs'].append(_ref(release))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        result['diagnostic_codes'].append('RANK_FAULT_SIGNAL_RELEASE_UNVERIFIED')
        return result
    try:
        recovery = _normal(value['recovery_packet'], value['recovery_client'], scope, after_ns=result['release_ns'])
        _require(recovery['clock_context'] == normal['clock_context'] and recovery['owner'] == normal['owner']
            and recovery['request_sha256'] not in {normal['request_sha256'], active['p']['native_request_sha256']}
            and recovery['external_request_sha256'] not in {normal['external_request_sha256'], scope['external_request_sha256']})
        _expected(value['expected_recovery'], recovery, normal)
        result['new_generation_recovery_observed'] = True
        result['recovery'] = {'source': recovery['workers'][scope['rank']],
            'at_ns': recovery['completed_ns'], 'request_sha256': recovery['request_sha256']}
        result['source_refs'] += recovery['source_refs']
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        result['diagnostic_codes'].append('RANK_FAULT_NEW_GENERATION_UNVERIFIED')
    return result
