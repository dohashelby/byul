"""Large retained journals preserve originals and expose finite live query scope."""
import json
from types import SimpleNamespace

import pytest

from serving_runtime.mvp.query import QueryService, QueryWindowIncompleteError
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.http_handler import Handler as MVPRequestHandler


def row(request_id, *, backend='vllm'):
    return {'request_id': request_id, 'phase': 'final', 'backend_kind': backend,
            'observation_session_id': 'a'*32, 'recorded_at': '2026-11-08T00:00:00+00:00',
            'decision': {'result': 'ALLOWED', 'reason_code': 'allowed'},
            'request': {'model': 'm'*128, 'max_tokens': 32},
            'delivery': {'attempted': True, 'response': 'completed'}}


def stores(tmp_path):
    gateway = JsonlStore(tmp_path/'gateway.jsonl')
    engine = JsonlStore(tmp_path/'engine.jsonl')
    gateway.append(row('1'*32))
    return gateway, engine, QueryService(gateway, engine, lambda: {'ok': True}, 'a'*32)


def test_polling_is_bounded_and_old_retained_request_uses_targeted_stream(tmp_path, monkeypatch):
    gateway, engine, service = stores(tmp_path)
    with gateway.path.open('ab') as stream:
        for n in range(10000):
            stream.write((json.dumps(row(f'{n+100:032x}'))+'\n').encode())
    original_size = gateway.path.stat().st_size
    def forbidden():
        raise AssertionError('polling must never load a complete journal')
    monkeypatch.setattr(gateway, 'read_all', forbidden)
    monkeypatch.setattr(engine, 'read_all', forbidden)
    summary = service.summary()
    assert summary['window']['counts_lower_bound'] is True
    assert summary['total'] <= 4096
    page = service.request_page(limit=20)
    assert len(page['requests']) == 20
    assert page['window']['truncated'] is True
    assert page['window']['counts_lower_bound'] is True
    detail = service.detail('1'*32, lambda rid: {'status': 'unknown'}, 'a'*32)
    assert detail['gateway_records'][0]['request_id'] == '1'*32
    assert detail['window']['gateway_search_complete'] is True
    assert gateway.path.stat().st_size == original_size


def test_omitted_old_uuid_is_unknown_not_not_found_or_false_engine_absence(tmp_path):
    gateway, engine, service = stores(tmp_path)
    with gateway.path.open('r+b') as stream:
        stream.seek(65*1024*1024)
        stream.write(b'\n'+(json.dumps(row('2'*32))+'\n').encode())
    with pytest.raises(QueryWindowIncompleteError):
        service.detail('1'*32, lambda rid: {'status': 'not_found'}, 'a'*32)
    assert service.detail('2'*32, lambda rid: {'status': 'unknown'}, 'a'*32)['window']['truncated'] is True
    errors = []
    handler = object.__new__(MVPRequestHandler)
    handler.dependencies = SimpleNamespace(queries=service, receipt_lookup=lambda rid: {}, observation_session_id='a'*32)
    handler._send_query_error = lambda *args: errors.append(args)
    handler._request_detail('1'*32)
    assert errors[0][:2] == (503, 'REQUEST_WINDOW_INCOMPLETE')


def test_corrupt_engine_line_still_prevents_negative_receipt_claim(tmp_path):
    gateway, engine, service = stores(tmp_path)
    gateway.path.write_text(json.dumps(row('1'*32, backend='mock'))+'\n')
    engine.append({'request_id': '2'*32})
    with engine.path.open('ab') as stream:
        stream.write(b'broken json\n')
    detail = service.detail('1'*32, lambda rid: {'status': 'not_found'}, 'a'*32)
    assert detail['evidence_state'] == 'unknown'
    assert detail['corrupt_lines']['engine'] == 1


def test_corrupt_gateway_row_cannot_establish_absence(tmp_path):
    gateway,engine,service=stores(tmp_path)
    gateway.path.write_bytes(b'{"request_id":"'+b'1'*32+b'",broken\n')
    with pytest.raises(QueryWindowIncompleteError):
        service.detail('1'*32,lambda _: {'status':'not_found'},'a'*32)


def test_unrelated_corruption_does_not_claim_retained_history_was_omitted(tmp_path):
    gateway, engine, service = stores(tmp_path)
    with gateway.path.open('ab') as stream:
        stream.write(b'broken json\n')
    detail = service.detail('1'*32, lambda _: {'status': 'not_found'}, 'a'*32)
    assert detail['corrupt_lines']['gateway'] == 1
    assert detail['window']['gateway_search_complete'] is False
    assert detail['window']['truncated'] is False
    assert '조회 범위 밖' not in detail['evidence_summary']


def test_started_record_is_recovered_when_final_is_inside_tail(tmp_path):
    gateway,engine,service=stores(tmp_path)
    started=row('1'*32);started['phase']='started'
    gateway.path.write_text(json.dumps(started)+'\n')
    with gateway.path.open('ab') as stream:
        noise=(json.dumps(row('2'*32))+'\n').encode()
        for _ in range(10000):stream.write(noise)
        stream.write((json.dumps(row('1'*32))+'\n').encode())
    detail=service.detail('1'*32,lambda _: {'status':'unknown'},'a'*32)
    assert [r['phase'] for r in detail['gateway_records']]==['started','final']
    assert detail['window']['gateway_search_complete'] is True


def test_started_only_never_claims_final_delivery_or_http_success(tmp_path):
    gateway,engine,service=stores(tmp_path)
    pending=row('1'*32);pending['phase']='started';pending['delivery']['attempted']=False
    gateway.path.write_text(json.dumps(pending)+'\n')
    detail=service.detail('1'*32,lambda _: {'status':'not_found'},'a'*32)
    assert detail['final_state']['result']=='INCOMPLETE'
    assert detail['final_state']['http_status'] is None
    assert detail['final_state']['delivery']['attempted'] is None
    assert '미전달 확인' not in detail['evidence_summary']
