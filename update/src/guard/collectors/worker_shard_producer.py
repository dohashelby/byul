"""SUP-06 worker-shard-load 생산자(개발 담당, B 의존 반환 5). 엔진 콜백의 worker shard 행을 #196 기록으로 만든다.

생산자 신원은 관측기(`adapters/vllm_worker_shard_callback.py`)와 이 변환의 소스 bytes 해시다. 등록은 GPU(TP2) 확인 뒤에 한다.

프로세스(콜백 행의 process_sha256)마다 행에 남은 PID·시작 ticks와 rank(READY 후보 행의 worker rank, 없으면 TP rank)로 바인딩을 만든다.
PID·시작 ticks를 읽지 못하거나 rank가 확인되지 않은 프로세스는 바인딩하지 않고 진단으로 남긴다.
필수 shard는 index의 weight_map 값이다. index 부재를 관측한 경우에만 배정 파일을 사용한다(`REQUIRED_FROM_ASSIGNED`).
`shard_rows`는 기존 `supply_boot_contract._worker_shard`가 받는 `{rank, required_missing, ready_accepted}` 행을 낸다.
"""
from __future__ import annotations

import hashlib

from guard.adapters import vllm_worker_shard_callback as callback
from . import gpu_common_inputs as contract
from .source_pins import producer_source_sha256

PRODUCER_NAME = 'guard-engine-callback-worker-shard-load'
PRODUCER_VERSION = '2'
_CALLBACK_SOURCES = {point: pins if type(pins) is tuple else (pins,)
                     for module, points in callback.TARGETS.items() for point in points
                     for pins in (callback.SOURCES[module],)}
_CALL_IDENTITY = ("point", "process_sha256", "parent_call_sha256", "source_file_sha256",
                  "entry_code_sha256", "operation_started_ns")
_ENGINE_GAP_SCHEMA = "guard-engine-callback-events/1"
_ENGINE_GAP_FIELDS = {"schema", "phase", "code", "process_sha256", "detail_sha256"}
_WORKER_SHARD_GAP_CODES = {"WORKER_SHARD_PREPARE_SHAPE_UNAVAILABLE",
                           "WORKER_SHARD_FILE_SET_UNAVAILABLE", "WORKER_SHARD_INDEX_UNREADABLE"}


def producer():
    source_sha256 = producer_source_sha256(callback.__file__, __file__)
    return {'name': PRODUCER_NAME, 'version': PRODUCER_VERSION, 'source_sha256': source_sha256}


def _ms(row):
    return max(0, int(row.get('operation_finished_ns') or row.get('operation_started_ns') or 0) // 10**6)


def _valid_shard_hashes(value):
    return (type(value) is list and 0 < len(value) <= callback.MAX_FILES
            and all(contract._check(shard, 'sha') for shard in value))


def events_from(rows):
    """콜백 행 → (행, 바인딩 신원 목록, 진단 코드)."""
    codes = set()
    mine = [r for r in rows if r.get('schema') == callback.SCHEMA and r.get('phase') in ('CALL', 'RETURN', 'RAISE')]
    gaps = [r for r in rows if r.get('phase') == 'GAP' and str(r.get('code', '')).startswith('WORKER_SHARD')]
    codes |= {r['code'] for r in gaps}
    by_process = {}
    for row in mine:
        by_process.setdefault(row.get('process_sha256'), []).append(row)
    identities, events = [], []
    for process, items in sorted(by_process.items(), key=lambda kv: str(kv[0])):
        ready = [r for r in items if r['point'] == 'WorkerProc.monitor_death_pipe' and r['phase'] == 'CALL' and r.get('fields')]
        known = [r['fields'] for r in items if r.get('fields') and type(r['fields'].get('pid')) is int and type(r['fields'].get('start_ticks')) is int]
        if not known or len({(f['pid'], f['start_ticks']) for f in known}) != 1:
            codes.add('WORKER_IDENTITY_UNOBSERVED'); continue
        if any(r.get('entry_qualified_before') is not True for r in items):
            codes.add('WORKER_SHARD_ENTRY_UNQUALIFIED')
        worker_ranks = {row['fields']['rank'] for row in ready if type(row['fields'].get('rank')) is int}
        tp_ranks = {fields['tp_rank'] for fields in known if type(fields.get('tp_rank')) is int}
        # Worker and TP ranks use different numbering scopes. Check consistency within each scope.
        if len(worker_ranks) > 1 or len(tp_ranks) > 1:
            codes.add('WORKER_RANK_CONFLICT')
            continue
        ranks = worker_ranks or tp_ranks
        if not ranks:
            codes.add('WORKER_RANK_UNOBSERVED')
            continue
        rank, = ranks
        binding = len(identities)
        identities.append({'pid': known[0]['pid'], 'start_ticks': known[0]['start_ticks'], 'rank': rank})

        def add(event, at, shard=None, size=None):
            events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at, 'shard_sha256': shard, 'bytes': size})
        prepared = [r for r in items if r['point'] == 'DefaultModelLoader._prepare_weights' and r['phase'] == 'RETURN' and r.get('fields')]
        for row in prepared:
            f = row['fields']
            index_present = f.get('index_present')
            required = f.get('required')
            if index_present is False and 'required' in f and required is None:
                required = f.get('files') or []
                codes.add('REQUIRED_FROM_ASSIGNED')
            elif index_present is True:
                if not _valid_shard_hashes(required):
                    required = None
                    codes.add('WORKER_SHARD_INDEX_UNREADABLE')
            else:
                required = None
                codes.add('WORKER_SHARD_INDEX_UNOBSERVED')
            for shard in sorted(set(required or [])):
                add('shard_required', _ms(row), shard)
            for shard, size in zip(f.get('files') or [], f.get('file_bytes') or []):
                add('shard_assigned', _ms(row), shard, size if type(size) is int else None)
        if not prepared:
            codes.add('WORKER_SHARD_ASSIGNMENT_UNOBSERVED')
        for row in items:
            if row['point'] == 'DefaultModelLoader._prepare_weights' and row['phase'] == 'RAISE':
                add('load_failed', _ms(row))  # 배정 단계 예외(예: index가 가리키는 shard 없음). 배정 파일은 모른다
        for row in items:
            if row['point'] == 'DefaultModelLoader.track_weights_loading' and row['phase'] in ('RETURN', 'RAISE'):
                add('load_completed' if row['phase'] == 'RETURN' else 'load_failed', _ms(row))
        for row in ready:
            if row['fields'].get('ready_candidate') is True:
                add('rank_ready', _ms(row))
    if len(events) > contract.MAX_EVENTS:
        codes.add('WORKER_SHARD_ROWS_BOUND'); events = events[:contract.MAX_EVENTS]
    return events, identities, sorted(codes)


def record(rows, *, pod, environment_ref, target_sha256, prepared_sha256, session, source_ended=True):
    """SUP-06 공통 입력 기록. `source_ended`는 콜백 세션 봉인·수집 완결 여부(전송이 준다)."""
    events, identities, codes = events_from(rows)
    if not identities:
        raise ValueError('WORKER_SHARD_NO_WORKER_IDENTITY')
    bindings = [{'pod_uid': pod['pod_uid'], 'container_id': pod['container_id'].removeprefix('containerd://')[:128],
                 'restart_count': pod['restart_count'], 'worker_pid': i['pid'], 'start_ticks': i['start_ticks'], 'rank': i['rank']}
                for i in identities]
    blocking = {'WORKER_SHARD_ROWS_BOUND', 'WORKER_SHARD_ENTRY_UNQUALIFIED', 'WORKER_IDENTITY_UNOBSERVED',
                'WORKER_SHARD_ASSIGNMENT_UNOBSERVED', 'WORKER_SHARD_INDEX_UNOBSERVED', 'WORKER_SHARD_INDEX_UNREADABLE',
                'WORKER_RANK_UNOBSERVED', 'WORKER_RANK_CONFLICT'}
    value = {'schema': contract.SCHEMA, 'kind': 'worker-shard-load', 'producer': producer(),
             'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
             'session': session, 'bindings': bindings, 'events': events,
             'completeness': {'source_ended': source_ended and not (set(codes) & blocking), 'dropped': 0,
                              'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return value, contract.validate(value, kind='worker-shard-load', environment_ref=environment_ref,
                                    target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


def _page_fields_valid(point, phase, fields):
    if 'tp_rank' not in fields or not contract._check(fields['tp_rank'], 'opt_int'):
        return False
    if point == 'WorkerProc.monitor_death_pipe' and phase == 'CALL':
        return ('rank' in fields and contract._check(fields['rank'], 'opt_int')
                and type(fields.get('ready_candidate')) is bool)
    if point == 'DefaultModelLoader.track_weights_loading' and phase == 'RETURN':
        return fields.get('completed') is True
    if point != callback.PREPARE or phase != 'RETURN':
        return True
    files, sizes = fields.get('files'), fields.get('file_bytes')
    index_present, required = fields.get('index_present'), fields.get('required')
    return (_valid_shard_hashes(files)
            and type(sizes) is list and len(sizes) == len(files)
            and all(contract._check(size, 'opt_int') for size in sizes)
            and 'index_present' in fields and contract._check(index_present, 'opt_bool')
            and 'required' in fields and (required is None or index_present is True and _valid_shard_hashes(required)))


def _joined_invocations(rows):
    """Keep page order and admit a terminal only with its unique native call."""
    by_call = {}
    for index, (process, row) in enumerate(rows):
        by_call.setdefault((process, row["call_sha256"]), []).append((index, row))
    admitted = set()
    unjoined = False
    for invocation in by_call.values():
        calls = [(index, row) for index, row in invocation if row["phase"] == "CALL"]
        terminals = [(index, row) for index, row in invocation if row["phase"] != "CALL"]
        if len(calls) != 1 or len(terminals) > 1:
            unjoined = True
            continue
        call_index, call = calls[0]
        admitted.add(call_index)
        if terminals:
            terminal_index, terminal = terminals[0]
            if call_index < terminal_index and all(call.get(key) == terminal.get(key) for key in _CALL_IDENTITY):
                admitted.add(terminal_index)
            else:
                unjoined = True
    return [entry for index, entry in enumerate(rows) if index in admitted], unjoined


def record_from_processes(processes, *, pod, environment_ref, target_sha256, prepared_sha256, session):
    """Project only page-scoped worker rows. A checkpoint is an open prefix."""
    session_sha256 = hashlib.sha256(session.encode()).hexdigest()
    rows = []
    reported_gaps = set()
    scope_mismatch = False
    for process in processes:
        if type(process) is not dict or type(process.get('events')) is not list:
            scope_mismatch = True
            continue
        pid, ticks = process.get('pid'), process.get('start_ticks')
        valid_process = contract._check(pid, 'int') and pid > 0 and contract._check(ticks, 'int')
        for row in process['events']:
            if type(row) is not dict:
                continue
            if row.get('schema') == _ENGINE_GAP_SCHEMA:
                code = row.get('code')
                if (valid_process and row.keys() == _ENGINE_GAP_FIELDS and row['phase'] == 'GAP'
                        and type(code) is str and code in _WORKER_SHARD_GAP_CODES
                        and contract._check(row['process_sha256'], 'sha')
                        and contract._check(row['detail_sha256'], 'sha')):
                    reported_gaps.add(((pid, ticks), row['process_sha256'], code))
                continue
            if row.get('schema') != callback.SCHEMA:
                continue
            fields, point, phase = row.get('fields'), row.get('point'), row.get('phase')
            started, finished = row.get('operation_started_ns'), row.get('operation_finished_ns')
            if (not valid_process or row.get('session_sha256') != session_sha256
                    or not contract._check(row.get('process_sha256'), 'sha')
                    or type(point) is not str or point not in _CALLBACK_SOURCES
                    or row.get("source_file_sha256") not in _CALLBACK_SOURCES[point]
                    or not contract._check(row.get("entry_code_sha256"), "sha")
                    or not contract._check(row.get("call_sha256"), "sha")
                    or row.get("parent_call_sha256") is not None
                        and not contract._check(row["parent_call_sha256"], "sha")
                    or phase not in ('CALL', 'RETURN', 'RAISE') or row.get('entry_qualified_before') is not True
                    or (phase != 'CALL' and row.get('entry_qualified_after') is not True)
                    or type(fields) is not dict or type(fields.get('pid')) is not int or fields['pid'] != pid
                    or type(fields.get('start_ticks')) is not int or fields['start_ticks'] != ticks
                    or not contract._check(started, 'int') or not contract._check(finished, 'int') or finished < started
                    or phase == "CALL" and finished != started
                    or not _page_fields_valid(point, phase, fields)):
                scope_mismatch = True
                continue
            rows.append(((pid, ticks), row))
    # Keep independently qualified siblings. A page group's identity, rather
    # than the callback's private process key, binds the resulting observations.
    joined, unjoined = _joined_invocations(rows)
    native_processes = {}
    for process, row in joined:
        native_processes.setdefault(process, set()).add(row["process_sha256"])
    # Generic gap rows carry no session or PID of their own. Bind them only through
    # the same page process and its one qualified native shard identity.
    codes = {code for process, native_process, code in reported_gaps
             if native_processes.get(process) == {native_process}}
    # One page identity cannot combine separate native process contexts.
    ambiguous_processes = {process for process, contexts in native_processes.items() if len(contexts) > 1}
    rows = [{**row, "process_sha256": process} for process, row in joined if process not in ambiguous_processes]
    rows.extend({"phase": "GAP", "code": code} for code in sorted(codes))
    if unjoined:
        rows.append({'phase': 'GAP', 'code': 'WORKER_SHARD_TERMINAL_UNJOINED'})
    if scope_mismatch or ambiguous_processes:
        rows.append({'phase': 'GAP', 'code': 'WORKER_SHARD_SCOPE_MISMATCH'})
    return record(rows, pod=pod, environment_ref=environment_ref, target_sha256=target_sha256,
                  prepared_sha256=prepared_sha256, session=session, source_ended=False)


def shard_rows(value):
    """기존 SUP-06 소비자(`supply_boot_contract._worker_shard`) 입력 행. rank마다 필수 누락 여부와 READY 수락 여부."""
    out = []
    for index, binding in enumerate(value['bindings']):
        rows = [r for r in value['events'] if r['binding'] == index]
        required = {r['shard_sha256'] for r in rows if r['event'] == 'shard_required'}
        assigned = {r['shard_sha256'] for r in rows if r['event'] == 'shard_assigned'}
        out.append({'rank': binding['rank'], 'required_missing': bool(required - assigned),
                    'ready_accepted': any(r['event'] == 'rank_ready' for r in rows),
                    'load_completed': any(r['event'] == 'load_completed' for r in rows),
                    'load_failed': any(r['event'] == 'load_failed' for r in rows)})
    return out
