"""SUP-06 worker-shard-load 생산자(개발 담당, B 의존 반환 5). 엔진 콜백의 worker shard 행을 #196 기록으로 만든다.

생산자 신원은 관측기(`adapters/vllm_worker_shard_callback.py`)와 이 변환의 소스 bytes 해시다. 등록은 GPU(TP2) 확인 뒤에 한다.

프로세스(콜백 행의 process_sha256)마다 행에 남은 PID·시작 ticks와 rank(READY 후보 행의 worker rank, 없으면 TP rank)로 바인딩을 만든다.
PID·시작 ticks를 읽지 못한 프로세스는 바인딩하지 않고 진단으로 남긴다(`WORKER_IDENTITY_UNOBSERVED`). rank를 모르면 0과 진단이다.
필수 shard는 index의 weight_map 값, index가 없으면 배정된 파일 자체다(그 경우 `REQUIRED_FROM_ASSIGNED`).
`shard_rows`는 기존 `supply_boot_contract._worker_shard`가 받는 `{rank, required_missing, ready_accepted}` 행을 낸다.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from guard.adapters import vllm_worker_shard_callback as callback
from . import gpu_common_inputs as contract

PRODUCER_NAME = 'guard-engine-callback-worker-shard-load'
PRODUCER_VERSION = '1'


def producer():
    digest = hashlib.sha256()
    for path in (Path(callback.__file__), Path(__file__)):
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return {'name': PRODUCER_NAME, 'version': PRODUCER_VERSION, 'source_sha256': digest.hexdigest()}


def _ms(row):
    return max(0, int(row.get('operation_finished_ns') or row.get('operation_started_ns') or 0) // 10**6)


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
        rank = next((r['fields']['rank'] for r in ready if type(r['fields'].get('rank')) is int), None)
        if rank is None:
            rank = next((f['tp_rank'] for f in known if type(f.get('tp_rank')) is int), None)
        if rank is None:
            codes.add('WORKER_RANK_UNOBSERVED'); rank = 0
        binding = len(identities)
        identities.append({'pid': known[0]['pid'], 'start_ticks': known[0]['start_ticks'], 'rank': rank})

        def add(event, at, shard=None, size=None):
            events.append({'seq': len(events), 'event': event, 'binding': binding, 'at_ms': at, 'shard_sha256': shard, 'bytes': size})
        prepared = [r for r in items if r['point'] == 'DefaultModelLoader._prepare_weights' and r['phase'] == 'RETURN' and r.get('fields')]
        for row in prepared:
            f = row['fields']
            required = f.get('required')
            if required is None:
                required = f.get('files') or []
                codes.add('REQUIRED_FROM_ASSIGNED')
            for shard in sorted(set(required)):
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
                'WORKER_SHARD_ASSIGNMENT_UNOBSERVED'}
    value = {'schema': contract.SCHEMA, 'kind': 'worker-shard-load', 'producer': producer(),
             'environment_ref': environment_ref, 'target_sha256': target_sha256, 'prepared_sha256': prepared_sha256,
             'session': session, 'bindings': bindings, 'events': events,
             'completeness': {'source_ended': source_ended and not (set(codes) & blocking), 'dropped': 0,
                              'diagnostic_codes': [c for c in codes if contract._ID.fullmatch(c)]}}
    return value, contract.validate(value, kind='worker-shard-load', environment_ref=environment_ref,
                                    target_sha256=target_sha256, prepared_sha256=prepared_sha256, session=session)


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
