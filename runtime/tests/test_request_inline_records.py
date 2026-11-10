"""Opt-in event records reuse the current bounded list snapshot only."""
from unittest.mock import patch
import pytest
from serving_runtime.mvp.query import QueryService, QueryError
from serving_runtime.mvp.store import JsonlStore


def row(rid,phase='final',at='2026-11-09T12:00:00Z'):
    return dict(request_id=rid,phase=phase,recorded_at=at,request={'model':'test-model'},
                decision={'result':'ALLOWED'},policy={'policy_id':'lab'},delivery={})


def test_opt_in_uses_one_recent_read_no_detail_scan_and_selected_ids_only(tmp_path):
    gateway=JsonlStore(tmp_path/'gateway.jsonl');engine=JsonlStore(tmp_path/'engine.jsonl')
    a,b='a'*32,'b'*32
    for record in [row(a,'started'),row(a),row(b,at='2026-11-09T12:01:00Z')]:gateway.append(record)
    service=QueryService(gateway,engine,lambda:{})
    with patch.object(gateway,'read_recent',wraps=gateway.read_recent) as recent, \
         patch.object(gateway,'read_matching_request',side_effect=AssertionError('detail scan')), \
         patch.object(engine,'read_recent',side_effect=AssertionError('engine scan')):
        page=service.request_page(limit=1,include_records=True)
    recent.assert_called_once_with()
    assert set(page['gateway_records'])=={b}
    assert page['gateway_records'][b]==service._normalize([row(b,at='2026-11-09T12:01:00Z')])
    assert page['window']['truncated'] is True
    assert 'gateway_records' not in service.request_page(limit=1000)


def test_opt_in_preserves_truncated_corrupt_current_window_without_historical_promotion(tmp_path):
    gateway=JsonlStore(tmp_path/'gateway.jsonl');engine=JsonlStore(tmp_path/'engine.jsonl')
    service=QueryService(gateway,engine,lambda:{})
    started=row('a'*32,'started')
    with patch.object(gateway,'read_recent',return_value=([started],[{'line':1}],True)), \
         patch.object(gateway,'read_matching_request',side_effect=AssertionError('historical scan')):
        page=service.request_page(include_records=True)
    assert page['requests'][0]['result']=='INCOMPLETE'
    assert page['gateway_records']['a'*32][0]['phase']=='started'
    assert page['window']['truncated'] is page['window']['counts_lower_bound'] is True
    assert page['window']['max_bytes']==4*1024*1024 and page['window']['max_records']==4096


def test_inline_maximum_200_without_changing_legacy_limit(tmp_path):
    service=QueryService(JsonlStore(tmp_path/'g'),JsonlStore(tmp_path/'e'),lambda:{})
    assert service.request_page(limit=1000)['requests']==[]
    for args in ({'limit':201,'include_records':True},{'include_records':1}):
        with pytest.raises(QueryError):service.request_page(**args)
