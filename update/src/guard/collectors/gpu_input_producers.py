"""#196 공통 입력 생산자(개발 담당, 2단계 P1). 엔진 관측을 `gpu_common_inputs` 계약 기록으로 만든다.

생산자는 엔진 안 관측(`adapters/vllm_attempt_callback.py`)과 이 변환을 합친 것이다. 값은 관측에서만 온다.
- 관측 지점 앞뒤로 원 코드가 그대로였던 행(entry_qualified_before·after)만 쓴다.
- 세대 결합은 Kubernetes 소유 Pod 바인딩(Pod UID·container ID·restart)과 기록 파일의 프로세스 PID·시작 ticks다.
  시작 ticks를 읽지 못한 프로세스의 행은 쓰지 않고 진단 코드로 남긴다.
- `source_ended`는 고른 요청마다 종료(finished/abort)와 KV 반환 행이 모두 있고 읽기가 잘리지 않았을 때만 참이다.
  엔진 프로세스 전체가 끝났다는 뜻이 아니라, 고른 요청의 기록 구간이 닫혔다는 뜻이다.

지원 생산자 목록(`SUPPORTED_PRODUCERS`) 등록은 GPU 확인 뒤에 한다. 그 전까지 이 기록은 형식이 맞아도 complete가 아니다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from guard.adapters import vllm_attempt_callback as attempt_callback
from . import gpu_common_inputs as contract

ATTEMPT_PRODUCER_NAME = 'guard-engine-callback-request-attempt'
ATTEMPT_PRODUCER_VERSION = '1'
_FREE_ABORT = {'FINISHED_ABORTED'}


def attempt_producer():
    """생산자 신원: 관측기와 이 변환의 소스 bytes 해시를 함께 묶는다."""
    digest = hashlib.sha256()
    for path in (Path(attempt_callback.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': ATTEMPT_PRODUCER_NAME, 'version': ATTEMPT_PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _qualified(row):
    return (row.get('schema') == attempt_callback.SCHEMA and row.get('entry_qualified_before') is True
            and row.get('phase') in ('CALL', 'RETURN') and (row['phase'] == 'CALL' or row.get('entry_qualified_after') is True))


def _row(seq, event, binding, request, attempt, at_ms, **values):
    base = {'stage': None, 'cost': None, 'kv_blocks': None, 'gpu_bytes': None, 'retry_cause': None,
            'next_attempt': None, 'consumed_by_attempt': None}
    return {'seq': seq, 'event': event, 'binding': binding, 'request_sha256': request, 'attempt': attempt, 'at_ms': at_ms,
            **base, **values}


def request_attempt_events(processes, *, requests=None):
    """프로세스별 attempt 콜백 행 → 계약 행. requests가 있으면 그 요청 지문만 남긴다."""
    events, bindings, codes = [], [], set()
    rows = []
    for process in processes:
        if process['start_ticks'] is None:
            if any(_qualified(r) for r in process['events']):
                codes.add('ATTEMPT_PROCESS_START_UNAVAILABLE')
            continue
        index = None
        for row in process['events']:
            if row.get('phase') == 'GAP':
                codes.add(row.get('code') if type(row.get('code')) is str else 'ATTEMPT_PRODUCER_GAP'); continue
            if not _qualified(row):
                continue
            if index is None:
                index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
            rows.append((row.get('operation_finished_ns') or 0, len(rows), index, row))
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows)
    attempts = {}; started = set()
    def keep(request):
        return request is not None and (requests is None or request in requests)
    for finished_ns, _, binding, row in sorted(rows):
        at_ms = max(0, (finished_ns - origin) // 1_000_000)
        fields = row.get('fields') or {}; point = row['point']; phase = row['phase']
        request = fields.get('request_sha256')
        def add(event, req, **values):
            if keep(req):
                events.append(_row(len(events), event, binding, req, attempts.setdefault(req, 1), at_ms, **values))
        if point == 'Scheduler.add_request' and phase == 'RETURN':
            add('admitted', request); add('queued', request)
        elif point == 'Scheduler.schedule' and phase == 'RETURN':
            for item in fields.get('scheduled') or []:
                req = item.get('request_sha256')
                if keep(req):
                    key = (req, attempts.setdefault(req, 1)); first = key not in started; started.add(key)
                    if first and type(fields.get('budget_tokens')) is int:
                        add('budget', req, cost=fields['budget_tokens'])
                    add('dequeued' if first else 'stage', req, stage='scheduled', cost=item.get('tokens'),
                        consumed_by_attempt=attempts[req])
        elif point == 'KVCacheManager.allocate_slots' and phase == 'RETURN':
            if fields.get('allocated') is True:
                add('kv_owned', request, kv_blocks=fields.get('new_blocks'), consumed_by_attempt=attempts.get(request, 1))
            elif fields.get('allocated') is False:
                add('limit_enforced', request, cost=fields.get('num_new_tokens'))
        elif point == 'Scheduler._preempt_request' and phase == 'RETURN':
            if keep(request):
                current = attempts.setdefault(request, 1)
                add('retry', request, retry_cause='preempted', next_attempt=current + 1)
                attempts[request] = current + 1
        elif point == 'Scheduler.update_from_output' and phase == 'RETURN':
            for req in fields.get('output_request_sha256') or []:
                add('gpu_fence', req)
        elif point == 'Scheduler._free_request' and phase == 'CALL':
            add('abort' if fields.get('status') in _FREE_ABORT else 'finished', request)
        elif point == 'KVCacheManager.free' and phase == 'RETURN':
            add('kv_released', request)
    return events, bindings, sorted(codes)


def request_attempt_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, requests=None):
    """계약 봉투를 만들고 계약 검증까지 거친 결과를 함께 돌려준다."""
    events, processes_used, codes = request_attempt_events(processes, requests=requests)
    if truncated:
        codes = sorted(set(codes) | {'ATTEMPT_READ_TRUNCATED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    selected = {e['request_sha256'] for e in events}
    closed = bool(selected) and all({'kv_released'} <= {e['event'] for e in events if e['request_sha256'] == r}
                                    and any(e['event'] in ('finished', 'abort') for e in events if e['request_sha256'] == r)
                                    for r in selected)
    record = {'schema': contract.SCHEMA, 'kind': 'request-attempt', 'producer': attempt_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings or [], 'events': events,
              'completeness': {'source_ended': closed and not truncated, 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return record, contract.validate(record, kind='request-attempt', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


# ---------------------------------------------------------------------------
# generation-transition (LIF-05·06·07·09)
# ---------------------------------------------------------------------------
from guard.adapters import vllm_transition_callback as transition_callback

TRANSITION_PRODUCER_NAME = 'guard-engine-callback-generation-transition'
TRANSITION_PRODUCER_VERSION = '1'
_COMPONENT = {'EngineCore.collective_rpc': 'weights', 'EngineCore.add_lora': 'adapter', 'EngineCore.remove_lora': 'adapter',
              'EngineCore.reset_prefix_cache': 'cache'}
_GATE_CLOSE = {'EngineCore.pause_scheduler', 'EngineCoreProc.pause_scheduler', 'EngineCore.sleep'}
_GATE_OPEN = {'EngineCore.resume_scheduler', 'EngineCore.wake_up'}


def transition_producer():
    digest = hashlib.sha256()
    for path in (Path(transition_callback.__file__), Path(attempt_callback.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': TRANSITION_PRODUCER_NAME, 'version': TRANSITION_PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _transition_qualified(row):
    return (row.get('schema') == transition_callback.SCHEMA and row.get('entry_qualified_before') is True
            and row.get('phase') in ('CALL', 'RETURN', 'RAISE') and (row['phase'] == 'CALL' or row.get('entry_qualified_after') is True))


def generation_transition_events(processes, *, requests=None):
    """전환 진입점 행과 request-attempt 단계 행을 시간 순서로 맞춰 계약 행을 만든다.

    - 세대 이름은 구성요소별 성공 전환 횟수다(`weights-0` → 전환 성공 뒤 `weights-1`).
    - 전환 진입 시 scheduler의 요청은 in_flight, 단계 배정은 그 시점 세대의 consumed다.
    - 접수 차단(pause·sleep) 중이 아니면 전환 성공 직후 service_gate open, 차단이면 재개 때 open이다.
    - 전환 실패(RAISE)는 failure, 같은 구성요소의 다음 성공 전환은 recovery다.
    """
    events, bindings, codes, rows = [], [], set(), []
    for process in processes:
        if process['start_ticks'] is None:
            if any(_qualified(r) or _transition_qualified(r) for r in process['events']):
                codes.add('TRANSITION_PROCESS_START_UNAVAILABLE')
            continue
        index = None
        for row in process['events']:
            if row.get('phase') == 'GAP':
                codes.add(row.get('code') if type(row.get('code')) is str else 'TRANSITION_PRODUCER_GAP'); continue
            if not (_qualified(row) or _transition_qualified(row)):
                continue
            if index is None:
                index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
            stamp = row.get('operation_started_ns') if row['phase'] == 'CALL' else row.get('operation_finished_ns')
            rows.append((stamp or 0, len(rows), index, row))
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows)
    generation = {'weights': 0, 'adapter': 0, 'cache': 0, 'service': 0}; changed = set(); failed = set()
    gate_open = True; seen_admit = set(); waiting_gate = set()
    def keep(request):
        return request is None or requests is None or request in requests
    def add(event, binding, at_ms, component, request=None, state=None, error=None):
        if keep(request):
            events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at_ms, 'request_sha256': request,
                           'generation': f'{component}-{generation[component]}', 'component': component, 'state': state,
                           'error_sha256': error, 'alias_sha256': None})
    for stamp, _, binding, row in sorted(rows):
        at_ms = max(0, (stamp - origin) // 1_000_000); fields = row.get('fields') or {}; point = row['point']; phase = row['phase']
        if row.get('schema') == transition_callback.SCHEMA:
            component = _COMPONENT.get(point)
            if point == 'EngineCore.collective_rpc' and fields.get('rpc_transition') is not True:
                continue
            if phase == 'CALL':
                kind = component or 'service'
                for request in fields.get('in_flight_request_sha256') or []:
                    add('in_flight', binding, at_ms, kind, request)
                if point in _GATE_CLOSE:
                    gate_open = False; add('drain', binding, at_ms, 'service', state=fields.get('mode') or 'closed')
            elif phase == 'RAISE':
                if component:
                    failed.add(component); add('failure', binding, at_ms, component, state='raised')
            elif point in _GATE_OPEN:
                gate_open = True; generation['service'] += 1
                add('routing', binding, at_ms, 'service', state='resumed'); add('service_gate', binding, at_ms, 'service', state='open')
            elif component and (fields.get('result') is not False):
                generation[component] += 1; changed.add(component)
                add('model_loaded' if component in ('weights', 'adapter') else 'backend_ready', binding, at_ms, component, state='committed')
                if component in failed:
                    failed.discard(component); add('recovery', binding, at_ms, component, state='committed')
                if gate_open:
                    add('service_gate', binding, at_ms, 'service', state='open')
            continue
        if point == 'Scheduler.add_request' and phase == 'RETURN':
            request = fields.get('request_sha256')
            if request:
                seen_admit.add(request)
                if any(e['event'] == 'service_gate' for e in events):
                    waiting_gate.add(request)
        elif point == 'Scheduler.schedule' and phase == 'RETURN':
            for item in fields.get('scheduled') or []:
                request = item.get('request_sha256')
                if not request:
                    continue
                for component in ['weights', *sorted(changed - {'weights'})]:
                    add('consumed', binding, at_ms, component, request, state='scheduled')
                if request in waiting_gate:
                    waiting_gate.discard(request); add('accepted', binding, at_ms, 'service', request, state='scheduled')
    return events, bindings, sorted(codes)


def generation_transition_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, requests=None):
    events, processes_used, codes = generation_transition_events(processes, requests=requests)
    if truncated:
        codes = sorted(set(codes) | {'TRANSITION_READ_TRUNCATED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    transitions = [e for e in events if e['event'] in ('model_loaded', 'backend_ready', 'failure')]
    closed = bool(transitions) and any(e['event'] == 'service_gate' and e['at_ms'] >= transitions[-1]['at_ms'] for e in events)
    record = {'schema': contract.SCHEMA, 'kind': 'generation-transition', 'producer': transition_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': closed and not truncated, 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return record, contract.validate(record, kind='generation-transition', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
