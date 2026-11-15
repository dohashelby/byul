"""기존 native24 엔진 안 관측을 공통 rank-fault 의미로 투영한다.

주입을 수행하지 않는다. 파일 READY만으로 적용·해제를 만들지 않으며,
기존 원 기록/소스/프로세스/요청/clock 검증을 그대로 재사용한다.
공통 시각은 해당 사건의 관측 시각이고 기한 판정은 원 투영에 남긴다.
"""
from . import vllm_fault_projection as native


def _time(row):
    return row['p'].get('operation_finished_ns') or row['time']


def _paired(rows):
    out = []
    for row in rows:
        p = row['p']
        if row['event'] != 'RANK_FAILURE' or p['phase'] == 'CALL' or not row['physical'] or not p['source_verified']:
            continue
        starts = [r for r in rows if r['physical'] and r['event'] == 'RANK_FAILURE' and r['context'] == row['context']
            and r['p']['phase'] == 'CALL' and all(r['p'][k] == p[k] for k in
                ('point', 'call_sha256', 'owner_sha256', 'executor_sha256', 'native_request_sha256',
                 'source_module', 'source_sha256', 'role', 'rank', 'world_size', 'configured_output_rank',
                 'rpc_method', 'selected_reply_rank', 'non_block'))]
        if len(starts) == 1 and starts[0]['time'] <= p['operation_started_ns'] <= p['operation_finished_ns'] <= row['time']:
            out.append(row)
    return out


def _recovery(rows, client, release_ns, clock):
    """같은 소유 범위에서 새 정상 요청의 worker·executor·최종 수신을 결합한다."""
    if (type(client) is not dict or client.get('clock_context') != clock
            or not native.control.fingerprint(client.get('external_request_sha256'))
            or type(client.get('outcome')) is not int or client['outcome'] != 200
            or client.get('complete') is not True or type(client.get('request_started_ns')) is not int
            or type(client.get('response_finished_ns')) is not int
            or not release_ns <= client['request_started_ns'] <= client['response_finished_ns']):
        return None
    selections = [r for r in rows if r['physical'] and r['event'] == 'SELECTION'
        and r['p'].get('point') == 'InputProcessor.assign_request_id' and r['p'].get('phase') == 'RETURN'
        and r['p'].get('metadata', {}).get('request_selection', {}).get('asgi_request_sha256') == client.get('external_request_sha256')
        and client['request_started_ns'] <= r['time'] <= client['response_finished_ns']]
    if len(selections) != 1:
        return None
    selected = selections[0]['p']['metadata']['request_selection']
    request = selected.get('selected_request_id_sha256')
    if not native.control.fingerprint(request):
        return None
    calls = [r for r in _paired(rows) if r['p']['phase'] == 'RETURN' and r['p']['exception_kind'] is None
        and request in r['p']['native_request_sha256']
        and selections[0]['time'] <= r['p']['operation_started_ns'] <= _time(r) <= client['response_finished_ns']]
    workers = [r for r in calls if r['p']['point'] == 'WorkerProc._execute_worker_rpc' and r['p']['rank'] == 0]
    executors = [r for r in calls if r['p']['point'] == 'MultiprocExecutor.execute_model'
        and r['p']['world_size'] == 1 and r['p']['configured_output_rank'] == 0 and r['p']['executor_is_failed'] is False]
    futures = [r for r in calls if r['p']['point'] == 'FutureWrapper.result' and r['p']['future_has_exception'] is False]
    terminal = [r for r in rows if r['physical'] and r['event'] == 'TERMINATION'
        and r['p'].get('point') == 'ASGI.request' and r['p'].get('phase') == 'ASGI_APP_RETURN'
        and r['p'].get('asgi_request_sha256') == client['external_request_sha256']
        and r['p'].get('asgi_invocation_sha256') == selected.get('asgi_invocation_sha256')
        and max([release_ns, *[_time(r) for r in workers + executors + futures]]) <= r['time'] <= client['response_finished_ns']]
    # worker 로컬 객체 지문을 다른 프로세스 executor 지문이라고 해석하지 않는다.
    groups = {r['p']['executor_sha256'] for r in executors + futures}
    worker_processes = {(r['context']['pid'], r['context']['start_ticks']) for r in workers}
    if (not workers or not executors or not futures or len(worker_processes) != 1
            or len(groups) != 1 or None in groups or len(terminal) != 1):
        return None
    return workers[-1], client['response_finished_ns'], request


def project(packet, client, receipt, scope, deadlines, *, recovery_client=None, closure_evidence=None):
    """형식화된 사실과 원 기한/위반을 함께 돌려준다. 새 판정은 만들지 않는다."""
    proof = native.validate_fault_case(packet, client, receipt, scope, deadlines)
    result = {'events': [], 'diagnostic_codes': [], 'native_validation': proof,
              'scope_complete': False, 'recovery_scope': 'single_rank_same_owner', 'producer_authenticated': False}
    if not proof['occurrence_verified'] or not proof['clock_verified']:
        result['diagnostic_codes'].append('RANK_FAULT_SOURCE_OCCURRENCE_UNVERIFIED')
        return result
    try:
        pre, previous, _, _ = native._rows(receipt['pre_signal_packet'], scope, active=True)
        rows, _, covered, _ = native._rows(packet, scope, previous)
        exact = lambda r: r['physical'] and r['event'] == 'FAULT_CONTROL' and all(r['p'][k] == scope[k]
            for k in ('session', 'target_sha256', 'nonce', 'fault_kind', 'rank', 'external_request_sha256'))
        ready = [r for r in pre if exact(r) and r['p']['phase'] == 'READY']
        if len(ready) != 1:
            raise ValueError
        active = ready[0]
        native_request = active['p']['native_request_sha256']
        def emit(event, source, stamp, request=native_request):
            result['events'].append({'event': event, 'at_ns': stamp, 'ack': True,
                'fault_kind': scope['fault_kind'], 'request_sha256': request,
                'context': source['context'] if event == 'recovered' else active['context'],
                'observer_context': source['context'],
                'source_ref': {k: source[k] for k in ('index', 'seq', 'capture_sha256')}})
        # 적재 파일이나 ARM/READY의 존재가 아니라 기존 투영이 검증한 물리 적용이다.
        observed = receipt.get('state_observed_ns') if receipt.get('action') == 'signal' else receipt.get('publish_after_ns')
        emit('fault_applied', active, observed)
        release = [r for r in rows if exact(r) and r['p']['phase'] == 'RELEASE'
            and r['p']['native_request_sha256'] == native_request and r['context'] == active['context']
            and r['p']['call_sha256'] == active['p']['call_sha256'] and r['time'] >= observed
            and r['p']['ready_receipt_sha256'] == active['p']['ready_receipt_sha256']
            and native.control.fingerprint(r['p']['action_receipt_sha256'])]
        closure = None
        if closure_evidence is not None:
            from .vllm_rank_fault_closure import inspect
            closure = inspect(closure_evidence, packet, receipt, scope, active,
                              release[0] if len(release) == 1 else None)
            result['source_closure'] = closure
            result['recovery_scope'] = 'tp2_new_process_generation_same_owner'
            result['diagnostic_codes'] += closure['diagnostic_codes']
        if scope['fault_kind'] in {'death', 'delay'} and not (closure and closure['physical_release_verified']):
            # RELEASE는 barrier 해제이며 SIGCONT/사망 worker 복구를 증명하지 않는다.
            result['diagnostic_codes'].append('RANK_FAULT_SIGNAL_RELEASE_UNVERIFIED')
            release = []
        if len(release) == 1:
            emit('fault_released', release[0], closure['release_ns'] if closure and closure['physical_release_verified'] else release[0]['time'])
        else:
            result['diagnostic_codes'].append('RANK_FAULT_RELEASE_UNOBSERVED')
        # 기존 기한 투영이 선택한 원 참조를 재사용한다. 늦은 탐지/전달도 숨기지 않는다.
        refs = [r for r in proof['evidence_refs'] if r['kind'] == 'post']
        for event, index in (('detected', 0),):
            if len(refs) > index and 'native_detection' not in proof['missing']:
                ref = refs[index]
                found = [r for r in rows if r['index'] == ref['recording'] and r['seq'] == ref['seq']
                    and r['capture_sha256'] == ref['capture_sha256'] and r['physical']]
                if len(found) == 1:
                    emit(event, found[0], _time(found[0]))
        # 정상 HTTP 성공은 장애 전달이 아니다. 원 판정의 실제 오류 수신 근거가 필요하다.
        response_observed = proof['affected_request_error_verified'] or any(
            v['code'] == 'affected_request_error_DEADLINE_EXCEEDED' for v in proof['violations'])
        if response_observed and refs:
            source = next(r for r in rows if r['index'] == refs[-1]['recording'] and r['seq'] == refs[-1]['seq'])
            emit('client_delivered', source, client['response_finished_ns'])
        recovery = None
        if closure:
            if closure['new_generation_recovery_observed']:
                item = closure['recovery']
                recovery = item['source'], item['at_ns'], item['request_sha256']
        elif len(release) == 1:
            recovery = _recovery(rows, recovery_client, release[0]['time'], native._clock(active['context']))
        if recovery is not None and recovery[2] != native_request:
            emit('recovered', recovery[0], recovery[1], recovery[2])
        else:
            result['diagnostic_codes'].append('RANK_FAULT_RECOVERY_UNOBSERVED')
        if proof['missing'] or not covered:
            result['diagnostic_codes'].append('RANK_FAULT_NATIVE_SCOPE_INCOMPLETE')
        expected = {'fault_applied', 'fault_released', 'detected', 'client_delivered', 'recovered'}
        result['scope_complete'] = bool(not result['diagnostic_codes'] and proof['whole_kind_complete']
            and expected <= {r['event'] for r in result['events']})
        result['events'].sort(key=lambda r: r['at_ns'])
    except (ValueError, KeyError, TypeError, IndexError, StopIteration):
        result['diagnostic_codes'].append('RANK_FAULT_PROJECTION_INCOMPLETE')
    return result
