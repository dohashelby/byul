"""runtime Gateway 기록 → guard route 기록 변환. 값은 옮기기만 하고 원 형식 검증을 그대로 통과해야 한다."""
import json

import pytest

from guard.executors.native_observations import validate_capture
from tools import dev1_runtime_records as bridge

IMAGE = 'k3d-byul-reg:5111/vllm/vllm-openai-cpu@sha256:' + 'a' * 64
ENV = {'kind': 'ENV', 'id': 'ENV-EXAMPLE', 'revision': 1, 'sha256': 'b' * 64}


def record(request_id, *, result='ALLOWED', auth='authenticated', attempted=True, final='completed', route='POST /v1/completions',
           phase='final', run_id='run-1'):
    return {'schema_version': 'runtime-mvp-record/1.0', 'phase': phase, 'request_id': request_id, 'route': route, 'run_id': run_id,
            'observation_session_id': 's', 'auth': {'state': auth, 'principal_id': 'p-general' if auth == 'authenticated' else None},
            'decision': {'result': result, 'engine_http_status': None},
            'delivery': {'attempted': attempted, 'response': 'completed' if final == 'completed' else 'none'}, 'final_outcome': final}


def lines(*rows):
    return [json.dumps(row) for row in rows]


def test_allowed_denied_and_unforwarded_rows_keep_their_meaning_and_pass_the_route_format():
    rows = bridge.final_records(lines(record('a', phase='started'), record('a'), record('d', result='DENIED', auth='invalid', attempted=False, final=None),
                                      record('x', run_id='other'), record('g', route='GET /v1/models')), run_id='run-1')
    packet = bridge.route_packet(rows, run_id='run-1', environment_ref=ENV, image=IMAGE, ingress_id='gw-pod', upstream_id='engine-pod')
    summary = validate_capture(packet, kind='route', env_ref=ENV, run_id='run-1', image=IMAGE)
    assert summary['events_seen'] == 2 and summary['producer_authenticated'] is False
    allowed, denied = packet['events']
    assert (allowed['auth_decision'], allowed['handler_outcome']) == ('allow', 'entered')
    # Gateway가 넘기지 않은 요청은 엔진 handler 거부가 아니라 관측 없음이다.
    assert (denied['auth_decision'], denied['handler_outcome']) == ('deny', 'unobserved')
    assert allowed['principal_sha256'] != denied['principal_sha256']
    assert 'p-general' not in json.dumps(packet) and '"a"' not in json.dumps(packet)


def test_unknown_results_engine_errors_and_limits():
    rows = [record('u', result='ERROR', attempted=False, final=None), {**record('e', final='failed'), 'decision': {'result': 'ALLOWED', 'engine_http_status': 400}}]
    events = bridge.route_events(rows, ingress_id='gw', upstream_id='engine')
    assert [(e['auth_decision'], e['handler_outcome']) for e in events] == [('unknown', 'unobserved'), ('allow', 'rejected')]
    assert len(bridge.route_events([record(str(i)) for i in range(70)], ingress_id='gw', upstream_id='engine')) == 64
    with pytest.raises(ValueError, match='RUNTIME_ROUTE_EVENTS_EMPTY'):
        bridge.route_events([record('g', route='GET /v1/models')], ingress_id='gw', upstream_id='engine')
    with pytest.raises(ValueError, match='RUNTIME_RECORD_SCHEMA_UNSUPPORTED'):
        bridge.final_records(['{"schema_version": "other"}'], run_id='run-1')
    with pytest.raises(ValueError, match='RUNTIME_RECORD_LINE_INVALID'):
        bridge.final_records(['{'], run_id='run-1')
