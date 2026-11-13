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
import json
from pathlib import Path

from guard.adapters import vllm_attempt_callback as attempt_callback
from . import gpu_common_inputs as contract
from . import gpu_common_input_v4 as v4
from guard.adapters import vllm_common_input_metadata as common_metadata

ATTEMPT_PRODUCER_NAME = 'guard-engine-callback-request-attempt'
ATTEMPT_PRODUCER_VERSION = '1'
_FREE_ABORT = {'FINISHED_ABORTED'}


def _source_dependencies(paths):
    # 관측기를 고르는 새 데이터도 생산자 신원에 포함한다. 등록은 GPU 확인 뒤에만 한다.
    root = Path(attempt_callback.__file__).parent
    return (*paths, root / 'vllm_cache_callback.py', root / 'vllm_observer_pin_rows.py',
            root / 'assets/vllm_observer_pin_rows.json')


def attempt_producer():
    """생산자 신원: 관측기와 이 변환의 소스 bytes 해시를 함께 묶는다."""
    digest = v4.source_digest(_source_dependencies((Path(attempt_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': ATTEMPT_PRODUCER_NAME, 'version': ATTEMPT_PRODUCER_VERSION, 'source_sha256': digest}


def _qualified(row):
    return (row.get('schema') == attempt_callback.SCHEMA and row.get('entry_qualified_before') is True
            and row.get('phase') in ('CALL', 'RETURN') and (row['phase'] == 'CALL' or row.get('entry_qualified_after') is True))


def _row(seq, event, binding, request, attempt, at_ms, **values):
    base = {'stage': None, 'cost': None, 'kv_blocks': None, 'gpu_bytes': None, 'retry_cause': None,
            'next_attempt': None, 'consumed_by_attempt': None}
    return {'seq': seq, 'event': event, 'binding': binding, 'request_sha256': request, 'attempt': attempt, 'at_ms': at_ms,
            **base, **values}


def request_attempt_events(processes, *, requests=None, wire_schema=contract.SCHEMA):
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
                add('limit_enforced', request, cost=fields.get('num_new_tokens'),
                    **({'enforcement_method': 'alloc_refused'} if wire_schema == v4.SCHEMA else {}))
        elif point == 'Scheduler._preempt_request' and phase == 'RETURN':
            if keep(request):
                current = attempts.setdefault(request, 1)
                if wire_schema == v4.SCHEMA:
                    add('limit_enforced', request, enforcement_method='preempted')
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


def request_attempt_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, requests=None, wire_schema=contract.SCHEMA):
    """계약 봉투를 만들고 계약 검증까지 거친 결과를 함께 돌려준다."""
    events, processes_used, codes = request_attempt_events(processes, requests=requests, wire_schema=wire_schema)
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
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
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
    digest = v4.source_digest(_source_dependencies((Path(transition_callback.__file__), Path(attempt_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': TRANSITION_PRODUCER_NAME, 'version': TRANSITION_PRODUCER_VERSION, 'source_sha256': digest}


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


def generation_transition_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, requests=None, wire_schema=contract.SCHEMA):
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
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
    return record, contract.validate(record, kind='generation-transition', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


# ---------------------------------------------------------------------------
# health-worker-causal (OBS-02)
# ---------------------------------------------------------------------------
from guard.adapters import vllm_health_callback as health_callback

HEALTH_PRODUCER_NAME = 'guard-engine-callback-health-worker'
HEALTH_PRODUCER_VERSION = '1'


def health_producer():
    digest = v4.source_digest(_source_dependencies((Path(health_callback.__file__), Path(attempt_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': HEALTH_PRODUCER_NAME, 'version': HEALTH_PRODUCER_VERSION, 'source_sha256': digest}


def health_worker_events(processes):
    """API health 판단(API 서버 프로세스)과 worker 단계 상태(EngineCore)를 각자의 세대로 남긴다.

    - api_health: health 진입 시 표식. 둘 다 정상(engine_dead=False, 출력 처리 task 실행 중)이면 healthy, 아니면 unhealthy/unknown.
    - worker_state: `update_from_output` 반환(output) 또는 예외(raised). 단계 출력이 host에 도착했다는 뜻이다.
    - request_accepted: EngineCore 접수(`add_request` 반환).
    - 세대는 프로세스(PID·시작 ticks)다. causal 지문은 행마다 고유하다. vLLM health는 worker 왕복이 없어 공유 인과 키가 없다.
    """
    events, bindings, codes, rows = [], [], set(), []
    for process in processes:
        relevant = [r for r in process['events'] if r.get('schema') in (health_callback.SCHEMA, attempt_callback.SCHEMA)]
        if process['start_ticks'] is None:
            if relevant:
                codes.add('HEALTH_PROCESS_START_UNAVAILABLE')
            continue
        index = None
        for row in process['events']:
            if row.get('phase') == 'GAP':
                codes.add(row.get('code') if type(row.get('code')) is str else 'HEALTH_PRODUCER_GAP'); continue
            if row.get('entry_qualified_before') is not True or row.get('schema') not in (health_callback.SCHEMA, attempt_callback.SCHEMA):
                continue
            if row['phase'] != 'CALL' and row.get('entry_qualified_after') is not True:
                continue
            if index is None:
                index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
            stamp = row.get('operation_started_ns') if row['phase'] == 'CALL' else row.get('operation_finished_ns')
            rows.append((stamp or 0, len(rows), index, row))
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows)
    for stamp, number, binding, row in sorted(rows):
        at_ms = max(0, (stamp - origin) // 1_000_000); fields = row.get('fields') or {}; point = row['point']; phase = row['phase']
        generation = 'process-%d-%d' % (bindings[binding]['pid'], bindings[binding]['start_ticks'])
        causal = hashlib.sha256(('%s:%s:%d' % (row.get('process_sha256'), row.get('call_sha256'), number)).encode()).hexdigest()
        if point == 'AsyncLLM.check_health' and phase == 'CALL':
            dead, running = fields.get('engine_dead'), fields.get('output_handler_running')
            state = 'healthy' if dead is False and running is True else 'unhealthy' if dead is True or running is False else 'unknown'
            event = 'api_health'
        elif point == 'Scheduler.update_from_output' and phase in ('RETURN', 'RAISE'):
            event, state = 'worker_state', 'output' if phase == 'RETURN' else 'raised'
        elif point == 'Scheduler.add_request' and phase == 'RETURN':
            event, state = 'request_accepted', 'admitted'
        else:
            continue
        events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at_ms, 'generation': generation,
                       'causal_sha256': causal, 'state': state})
    return events, bindings, sorted(codes)


def health_worker_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, wire_schema=contract.SCHEMA):
    events, processes_used, codes = health_worker_events(processes)
    if truncated:
        codes = sorted(set(codes) | {'HEALTH_READ_TRUNCATED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    # 공유 인과 키가 없으므로 생산자 진단으로 남긴다. 소비자는 시간 근접으로 health와 worker 상태를 합치지 않는다.
    codes = sorted(set(codes) | {'HEALTH_WORKER_CAUSAL_LINK_ABSENT_IN_ENGINE'})
    record = {'schema': contract.SCHEMA, 'kind': 'health-worker-causal', 'producer': health_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': not truncated and any(e['event'] == 'api_health' for e in events)
                               and any(e['event'] == 'worker_state' for e in events), 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
    return record, contract.validate(record, kind='health-worker-causal', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


# ---------------------------------------------------------------------------
# profiler-control (LIF-14·OBS-03)
# ---------------------------------------------------------------------------
from guard.adapters import vllm_profiler_callback as profiler_callback

PROFILER_PRODUCER_NAME = 'guard-engine-callback-profiler-control'
PROFILER_PRODUCER_VERSION = '1'


def profiler_producer():
    digest = v4.source_digest(_source_dependencies((Path(profiler_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': PROFILER_PRODUCER_NAME, 'version': PROFILER_PRODUCER_VERSION, 'source_sha256': digest}


def profiler_control_events(processes):
    """API·EngineCore·worker의 profiler 제어 행을 계약 행으로 옮긴다.

    - api_call: API 진입, rpc: EngineCore 반환, worker_control: worker 반환, backend_state: torch profiler 시작·중지 반환
    - stop_failed: 중지 쪽 예외(worker·backend)
    - output_complete: 중지 반환 뒤 출력 폴더의 파일 목록(이름 지문·크기). artifact 지문은 그 목록의 해시, 파일 세대는 파일 수다.
    - next_collection: 출력 완료 뒤 다시 시작된 backend
    - causal 지문은 행마다 고유하다. 세 프로세스를 잇는 키가 vLLM에 없어서다.
    """
    events, bindings, codes, rows = [], [], set(), []
    for process in processes:
        relevant = [r for r in process['events'] if r.get('schema') == profiler_callback.SCHEMA]
        if process['start_ticks'] is None:
            if relevant:
                codes.add('PROFILER_PROCESS_START_UNAVAILABLE')
            continue
        index = None
        for row in process['events']:
            if row.get('phase') == 'GAP':
                codes.add(row.get('code') if type(row.get('code')) is str else 'PROFILER_PRODUCER_GAP'); continue
            if row.get('schema') != profiler_callback.SCHEMA or row.get('entry_qualified_before') is not True:
                continue
            if row['phase'] != 'CALL' and row.get('entry_qualified_after') is not True:
                continue
            if index is None:
                index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
            stamp = row.get('operation_started_ns') if row['phase'] == 'CALL' else row.get('operation_finished_ns')
            rows.append((stamp or 0, len(rows), index, row))
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows); completed = False
    for stamp, number, binding, row in sorted(rows):
        at_ms = max(0, (stamp - origin) // 1_000_000); fields = row.get('fields') or {}; point = row['point']; phase = row['phase']
        causal = hashlib.sha256(('%s:%s:%d' % (row.get('process_sha256'), row.get('call_sha256'), number)).encode()).hexdigest()
        start = fields.get('is_start')
        state = 'start' if start is True else 'stop' if start is False else None
        def add(event, state=state, artifact=None, generation=None):
            events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at_ms, 'causal_sha256': causal,
                           'artifact_sha256': artifact, 'file_generation': generation, 'state': state})
        if point.startswith('AsyncLLM.') and phase == 'CALL':
            add('api_call')
        elif point == 'EngineCore.profile' and phase == 'RETURN':
            add('rpc')
        elif point == 'EngineCore.profile' and phase == 'RAISE':
            add('rpc', state='raised')
        elif point == 'Worker.profile' and phase == 'RETURN':
            add('worker_control')
        elif point == 'Worker.profile' and phase == 'RAISE':
            add('stop_failed' if start is False else 'worker_control', state='raised')
        elif point == 'TorchProfilerWrapper._start' and phase == 'RETURN':
            add('next_collection' if completed else 'backend_state', state='started')
        elif point == 'TorchProfilerWrapper._stop' and phase == 'RAISE':
            add('stop_failed', state='raised')
        elif point == 'TorchProfilerWrapper._stop' and phase == 'RETURN':
            add('backend_state', state='stopped')
            files = fields.get('output_files')
            if type(files) is list and files:
                completed = True
                add('output_complete', state='files', artifact=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
                    generation='files-%d' % len(files))
            else:
                codes.add('PROFILER_OUTPUT_UNOBSERVED')
    return events, bindings, sorted(codes)


def profiler_control_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, wire_schema=contract.SCHEMA):
    events, processes_used, codes = profiler_control_events(processes)
    codes = set(codes) | {'PROFILER_CAUSAL_ID_ABSENT_IN_ENGINE'} | ({'PROFILER_READ_TRUNCATED'} if truncated else set())
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    stops = [e for e in events if e['event'] == 'api_call' and e['state'] == 'stop']
    closed = bool(stops) and any(e['event'] == 'output_complete' and e['at_ms'] >= stops[-1]['at_ms'] for e in events)
    record = {'schema': contract.SCHEMA, 'kind': 'profiler-control', 'producer': profiler_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': closed and not truncated, 'dropped': 0,
                               'diagnostic_codes': sorted(c for c in codes if contract._ID.fullmatch(c))}}
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
    return record, contract.validate(record, kind='profiler-control', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


# ---------------------------------------------------------------------------
# runner-consumption (P2: REQ-08·09·10)
# ---------------------------------------------------------------------------
from guard.adapters import vllm_consumption_callback as consumption_callback

CONSUMPTION_PRODUCER_NAME = 'guard-engine-callback-runner-consumption'
CONSUMPTION_PRODUCER_VERSION = '1'
_CONSUMPTION_FIELDS = ('prompt_tokens', 'max_token_id') + consumption_callback.FIELDS


def consumption_producer():
    digest = v4.source_digest(_source_dependencies((Path(consumption_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': CONSUMPTION_PRODUCER_NAME, 'version': CONSUMPTION_PRODUCER_VERSION, 'source_sha256': digest}


def runner_consumption_events(processes, *, requests=None):
    """API 검증(API 서버)·runner 도착·sampler 적용(worker) 행을 계약 행으로 옮긴다.

    - api_validated / api_rejected: `process_inputs` 안의 `_validate_model_input`·`_validate_params` 결과. 예외면 거부다.
    - runner_received: `_update_states`의 새 요청(요청마다 한 번)
    - sampler_applied: `_sample` 진입 때 batch의 요청별 값. 처음 값과 이후 달라진 값을 남긴다.
    """
    events, bindings, codes, rows = [], [], set(), []
    for process in processes:
        relevant = [r for r in process['events'] if r.get('schema') == consumption_callback.SCHEMA]
        if process['start_ticks'] is None:
            if relevant:
                codes.add('CONSUMPTION_PROCESS_START_UNAVAILABLE')
            continue
        index = None
        for row in process['events']:
            if row.get('phase') == 'GAP':
                codes.add(row.get('code') if type(row.get('code')) is str else 'CONSUMPTION_PRODUCER_GAP'); continue
            if row.get('schema') != consumption_callback.SCHEMA or row.get('entry_qualified_before') is not True:
                continue
            if row['phase'] != 'CALL' and row.get('entry_qualified_after') is not True:
                continue
            if index is None:
                index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
            stamp = row.get('operation_started_ns') if row['phase'] == 'CALL' else row.get('operation_finished_ns')
            rows.append((stamp or 0, len(rows), index, row))
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows)
    pending = {}; applied = {}; received = set()
    def keep(request):
        return request is not None and (requests is None or request in requests)
    def add(event, binding, at_ms, request, stage=None, **values):
        if keep(request):
            row = {'seq': len(events), 'event': event, 'request_sha256': request, 'binding': binding, 'at_ms': at_ms, 'stage': stage}
            row.update({name: values.get(name) for name in _CONSUMPTION_FIELDS})
            events.append(row)
    calls = {}
    for stamp, _, binding, row in sorted(rows):
        at_ms = max(0, (stamp - origin) // 1_000_000); fields = row.get('fields') or {}; point = row['point']; phase = row['phase']
        call = (binding, row.get('process_sha256'), row.get('call_sha256'))
        if phase == 'CALL':
            calls[call] = fields
        entry = calls.get(call, fields)
        if point == 'InputProcessor._validate_model_input' and phase in ('RETURN', 'RAISE'):
            request = entry.get('request_sha256')
            state = pending.setdefault(request, {'binding': binding, 'values': {}, 'rejected': None})
            state['values'].update(prompt_tokens=entry.get('prompt_tokens'), max_token_id=entry.get('max_token_id'))
            if phase == 'RAISE':
                state['rejected'] = 'model_input'
        elif point == 'InputProcessor._validate_params' and phase in ('RETURN', 'RAISE'):
            request = entry.get('request_sha256')
            state = pending.setdefault(request, {'binding': binding, 'values': {}, 'rejected': None})
            state['values'].update(entry.get('params') or {})
            if phase == 'RAISE':
                state['rejected'] = 'params'
        elif point == 'InputProcessor.process_inputs' and phase in ('RETURN', 'RAISE'):
            request = entry.get('request_sha256'); state = pending.pop(request, None)
            if state is None:
                if phase == 'RAISE':
                    add('api_rejected', binding, at_ms, request, stage='process_inputs')
                continue
            rejected = state['rejected'] or ('process_inputs' if phase == 'RAISE' else None)
            add('api_rejected' if rejected else 'api_validated', state['binding'], at_ms, request, stage=rejected or 'validated',
                **state['values'])
        elif point == 'GPUModelRunner._update_states' and phase == 'CALL':
            for item in fields.get('received') or []:
                request = item.get('request_sha256')
                if request in received:
                    continue
                received.add(request)
                add('runner_received', binding, at_ms, request, stage='new_request', prompt_tokens=item.get('prompt_tokens'),
                    max_token_id=item.get('max_token_id'), **(item.get('params') or {}))
        elif point == 'GPUModelRunner._sample' and phase == 'CALL':
            for item in fields.get('applied') or []:
                request = item.get('request_sha256')
                values = {k: v for k, v in item.items() if k != 'request_sha256'}
                if request in applied and applied[request] == values:
                    continue
                stage = 'sample_changed' if request in applied else 'first_sample'
                applied[request] = values
                add('sampler_applied', binding, at_ms, request, stage=stage, **values)
    if requests is not None and set(requests) - {e['request_sha256'] for e in events}:
        codes.add('CONSUMPTION_SELECTED_REQUEST_UNOBSERVED')
    return events, bindings, sorted(codes)


def runner_consumption_record(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False, requests=None, wire_schema=contract.SCHEMA):
    events, processes_used, codes = runner_consumption_events(processes, requests=requests)
    if truncated:
        codes = sorted(set(codes) | {'CONSUMPTION_READ_TRUNCATED'})
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    validated = {e['request_sha256'] for e in events if e['event'] == 'api_validated'}
    closed = bool(validated) and all(all(any(e['request_sha256'] == r and e['event'] == stage for e in events)
                                       for stage in ('runner_received', 'sampler_applied')) for r in validated)
    record = {'schema': contract.SCHEMA, 'kind': 'runner-consumption', 'producer': consumption_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': closed and not truncated, 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
    return record, contract.validate(record, kind='runner-consumption', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


# ---------------------------------------------------------------------------
# supply-reads (P2: SUP-03)
# ---------------------------------------------------------------------------
from guard.adapters import vllm_supply_read_callback as supply_callback

SUPPLY_PRODUCER_NAME = 'guard-engine-callback-supply-reads'
SUPPLY_PRODUCER_VERSION = '1'


def supply_producer():
    digest = v4.source_digest(_source_dependencies((Path(supply_callback.__file__), Path(__file__))) + common_metadata.SOURCE_PATHS)
    return {'name': SUPPLY_PRODUCER_NAME, 'version': SUPPLY_PRODUCER_VERSION, 'source_sha256': digest}


def path_sha256(relative):
    """승인 목록과 같은 방식으로 대조할 수 있게 상대 경로의 평문 SHA-256을 쓴다."""
    return hashlib.sha256(relative.encode('utf-8')).hexdigest()


def supply_open_files(processes):
    """해시를 계산할 고유 파일 목록(루트 번호·상대 경로)."""
    seen = []
    for process in processes:
        for row in process['events']:
            if row.get('schema') == supply_callback.SCHEMA and row.get('phase') in ('OPEN', 'INOTIFY'):
                key = {'root': row['root'], 'relative': row['relative']}
                if key not in seen:
                    seen.append(key)
    return seen


def supply_read_events(processes, hashes):
    events, bindings, codes, rows = [], [], set(), []
    for process in processes:
        mine = [r for r in process['events'] if r.get('schema') == supply_callback.SCHEMA and r.get('phase') in ('OPEN', 'INOTIFY')]
        codes |= {r.get('code') for r in process['events'] if r.get('phase') == 'GAP' and type(r.get('code')) is str and r['code'].startswith('SUPPLY_')}
        if not mine:
            continue
        if process['start_ticks'] is None:
            codes.add('SUPPLY_PROCESS_START_UNAVAILABLE'); continue
        index = len(bindings); bindings.append({'pid': process['pid'], 'start_ticks': process['start_ticks']})
        rows += [(r.get('opened_ns') or 0, len(rows), index, r) for r in mine]
    if not rows:
        return [], bindings, sorted(codes)
    origin = min(r[0] for r in rows); last = 0
    for stamp, _, binding, row in sorted(rows):
        at_ms = max(0, (stamp - origin) // 1_000_000); last = max(last, at_ms)
        if type(row.get('relative')) is not str or type(row.get('root')) is not int:
            codes.add('SUPPLY_ROW_INVALID'); continue
        if row['phase'] == 'INOTIFY':
            # inotify 행: 연 프로세스를 모른다. binding은 감시한 프로세스이며 귀속 근거가 아니다.
            codes.add('SUPPLY_INOTIFY_OBSERVED')
            events.append({'seq': len(events), 'event': 'file_observed', 'binding': binding, 'at_ms': at_ms, 'root': row['root'],
                           'path_sha256': path_sha256(row['relative']), 'access': 'read', 'size': None, 'bytes_sha256': None,
                           'state': 'inotify_unattributed'})
            continue
        events.append({'seq': len(events), 'event': 'file_opened', 'binding': binding, 'at_ms': at_ms, 'root': row['root'],
                       'path_sha256': path_sha256(row['relative']), 'access': 'write' if row.get('write') else 'read',
                       'size': row.get('size') if type(row.get('size')) is int else None, 'bytes_sha256': None,
                       'state': 'regular' if row.get('regular') else 'not_regular' if row.get('regular') is False else None})
    for item in hashes:
        events.append({'seq': len(events), 'event': 'file_hashed', 'binding': 0, 'at_ms': last, 'root': item['root'],
                       'path_sha256': path_sha256(item['relative']), 'access': 'read',
                       'size': item.get('size') if type(item.get('size')) is int else None,
                       'bytes_sha256': item.get('sha256'), 'state': item.get('state')})
        if item.get('state') != 'hashed':
            codes.add('SUPPLY_FILE_NOT_HASHED')
    return events, bindings, sorted(codes)


def mount_scope_codes(before, after):
    """수집 전후 두 번의 마운트 전용성 확인을 진단 코드로 바꾼다. 두 번 모두 모든 루트가 전용이어야 빈 목록이다."""
    if before is None or after is None:
        return ['SUPPLY_MOUNT_EXCLUSIVITY_UNVERIFIED']
    rows = list(before) + list(after)
    if not rows or len(before) != len(after):
        return ['SUPPLY_MOUNT_EXCLUSIVITY_UNVERIFIED']
    codes = set()
    for row in rows:
        if row.get('exclusive') is True:
            continue
        codes.add('SUPPLY_MOUNT_SHARED' if str(row.get('reason', '')).startswith('shared') else 'SUPPLY_MOUNT_EXCLUSIVITY_UNVERIFIED')
    return sorted(codes)


def supply_read_record(processes, hashes, *, pod, environment_ref, target_sha256, prepared_sha256, session, truncated=False,
                       mount_before=None, mount_after=None, wire_schema=contract.SCHEMA):
    """기록된 연 파일마다 해시 행이 있어야 닫힌다. 너무 큰 파일(가중치 등)은 해시하지 않고 상태로 남긴다.

    - inotify 감시가 없으면 native 읽기를 못 보므로 `SUPPLY_NATIVE_READS_UNOBSERVED` 진단이 붙는다.
    - inotify 감시가 있으면 native 읽기 파일은 보이지만 연 프로세스를 모른다. 같은 노드의 다른 Pod 읽기도 섞일 수 있다.
      수집 전후 마운트 전용성 확인(`supply_mount_exclusivity`)이 두 번 모두 전용이면 이 Pod의 읽기로 좁히고 진단을 붙이지 않는다.
      확인이 없거나 공유면 `SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED`와 그 이유 코드가 붙는다.
      노드의 Kubernetes 밖 프로세스는 API로 보이지 않아 이 확인 범위 밖이다.
    """
    events, processes_used, codes = supply_read_events(processes, hashes)
    # Python audit hook은 native 코드(Rust tokenizers, C++ 로더 등)의 파일 읽기를 보지 못한다(k3d run 17: vocab.json·merges.txt).
    # 그래서 이 기록만으로는 실제 소비 전체가 아니라는 진단을 항상 남긴다.
    watched = 'SUPPLY_INOTIFY_OBSERVED' in codes
    codes = set(codes) - {'SUPPLY_INOTIFY_OBSERVED'}
    if not watched:
        codes.add('SUPPLY_NATIVE_READS_UNOBSERVED')
    else:
        # 공급 루트 볼륨이 전후 모두 이 엔진 Pod 전용이면 inotify 사건을 이 Pod의 읽기로 좁힌다(Pod 안 프로세스 구분은 SUP-03에 필요 없다).
        scope = mount_scope_codes(mount_before, mount_after)
        if scope:
            codes |= {'SUPPLY_NATIVE_READ_PROCESS_UNATTRIBUTED', *scope}
        else:
            for event in events:
                if event['event'] == 'file_observed':
                    event['state'] = 'inotify_pod_exclusive'
    codes = sorted(codes | ({'SUPPLY_READ_TRUNCATED'} if truncated else set()))
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': p['pid'], 'start_ticks': p['start_ticks'], 'rank': 0}
                for p in processes_used]
    opened = {(e['root'], e['path_sha256']) for e in events if e['event'] in ('file_opened', 'file_observed')}
    covered = {(e['root'], e['path_sha256']) for e in events if e['event'] == 'file_hashed'}
    record = {'schema': contract.SCHEMA, 'kind': 'supply-reads', 'producer': supply_producer(),
              'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
              'session': session, 'bindings': bindings, 'events': events,
              'completeness': {'source_ended': bool(opened) and opened <= covered and not truncated, 'dropped': 0,
                               'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    record = v4.encode(record, wire_schema=wire_schema, kv_unit=common_metadata.KV_UNIT,
                       kv_unit_tokens=common_metadata.KV_UNIT_TOKENS)
    return record, contract.validate(record, kind='supply-reads', environment_ref=environment_ref,
                                     target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)
