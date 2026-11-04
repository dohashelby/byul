"""BYUL runtime Gateway 요청 기록을 guard 점검의 닫힌 기록 형식으로 옮기는 lab 연결(개발 담당, 1단계).

생산자는 runtime Gateway(`gateway_records.jsonl`, `runtime-mvp-record/1.0`)다. 이 도구는 값을 만들지 않고
옮기기만 한다. 식별자는 SHA-256으로만 넘기고, 토큰·본문·경로 원문 외 값은 넣지 않는다.
결과 파일의 생산자는 guard 쪽에서 계속 미인증이다(실행기가 producer_authenticated=False로 둔다).

route: REQ-02 `guard-route-native-observations/1`.
- 외부 경로·인증 경로·전달 경로는 Gateway 기록의 route 하나다. runtime Gateway는 경로를 바꾸지 않는다.
- 인증 판단은 decision.result에서 읽는다(ALLOWED→allow, DENIED·INVALID→deny, 그 밖→unknown).
- handler 도달은 Gateway가 본 엔진 전달 결과다. 엔진 안의 handler 진입 관측이 아니므로
  전달하지 않은 요청은 unobserved로 둔다(거부를 handler rejected로 바꾸지 않는다).
"""
from __future__ import annotations

import hashlib
import json
import re

RECORD_SCHEMA = 'runtime-mvp-record/1.0'
PRODUCER = 'byul-runtime-gateway'
_PATH = re.compile(r'/[A-Za-z0-9/_-]{1,255}')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}')


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def final_records(lines, *, run_id, session=None):
    """같은 run(과 관측 세션)의 final 기록만 순서대로. 형식이 다른 줄은 버리지 않고 오류로 낸다."""
    rows = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            raise ValueError(f'RUNTIME_RECORD_LINE_INVALID:{number}') from None
        if type(value) is not dict or value.get('schema_version') != RECORD_SCHEMA:
            raise ValueError(f'RUNTIME_RECORD_SCHEMA_UNSUPPORTED:{number}')
        if value.get('run_id') != run_id or session is not None and value.get('observation_session_id') != session:
            continue
        if value.get('phase') == 'final':
            rows.append(value)
    return rows


def _decision(record):
    result = (record.get('decision') or {}).get('result')
    return {'ALLOWED': 'allow', 'DENIED': 'deny', 'INVALID': 'deny'}.get(result, 'unknown')


def _handler(record):
    delivery = record.get('delivery') or {}
    if delivery.get('attempted') is not True:
        return 'unobserved'
    if record.get('final_outcome') == 'completed' and delivery.get('response') == 'completed':
        return 'entered'
    engine = (record.get('decision') or {}).get('engine_http_status')
    return 'rejected' if type(engine) is int and 400 <= engine < 500 else 'unobserved'


def route_events(records, *, ingress_id, upstream_id):
    """POST 경로 기록만 옮긴다(현행 route 형식은 POST만 받는다). 64개를 넘으면 앞의 64개만."""
    if not _ID.fullmatch(ingress_id) or not _ID.fullmatch(upstream_id):
        raise ValueError('RUNTIME_ROUTE_ID_INVALID')
    events = []
    for record in records:
        method, _, path = (record.get('route') or '').partition(' ')
        if method != 'POST' or not _PATH.fullmatch(path):
            continue
        auth = record.get('auth') or {}
        principal = auth.get('principal_id') if auth.get('state') == 'authenticated' else None
        operation = path.strip('/').replace('/', '.')
        events.append({
            'event_id': _sha(record['request_id'])[:32], 'producer': PRODUCER,
            'ingress_id': ingress_id, 'upstream_id': upstream_id,
            'request_sha256': _sha(record['request_id']),
            'principal_sha256': _sha('principal:' + principal if principal else 'unauthenticated'),
            'external_method': 'POST', 'external_path': path,
            'auth_method': 'POST', 'auth_path': path, 'auth_operation': operation,
            'auth_decision': _decision(record),
            'rewritten_method': 'POST', 'rewritten_path': path,
            'handler_id': 'vllm-openai', 'handler_operation': operation, 'handler_outcome': _handler(record)})
        if len(events) == 64:
            break
    if not events:
        raise ValueError('RUNTIME_ROUTE_EVENTS_EMPTY')
    return events


def route_packet(records, *, run_id, environment_ref, image, ingress_id, upstream_id):
    return {'schema': 'guard-route-native-observations/1', 'run_id': run_id, 'environment_ref': environment_ref,
            'image': image, 'events': route_events(records, ingress_id=ingress_id, upstream_id=upstream_id)}
