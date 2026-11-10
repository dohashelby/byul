import httpx
import pytest
from fastapi.testclient import TestClient
from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import RuntimeClient,RuntimeWindowIncomplete,RuntimeUnavailable,LiveRuntimeSource

A='a'*32;B='b'*32


def transport(request):
    return httpx.Response(503,json=dict(error=dict(code='REQUEST_WINDOW_INCOMPLETE',message='untrusted backend detail')))


def test_only_explicit_request_window_code_is_scoped_and_other_runtime_errors_remain_global():
    client=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(transport))
    try:
        with pytest.raises(RuntimeWindowIncomplete):client.get(f'/api/requests/{A}')
        with pytest.raises(RuntimeWindowIncomplete):client.get_bytes(f'/api/requests/{A}/evidence')
        with pytest.raises(RuntimeUnavailable):client.get('/api/status')
    finally:client.close()


def test_detail_and_evidence_invalidate_only_affected_uuid():
    client=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(transport));source=LiveRuntimeSource(client,'http://runtime.test')
    try:
        for method in (source.request_detail,source.request_evidence):
            source._final_events={A:(0,{'event_id':'req:'+A}),B:(0,{'event_id':'req:'+B})}
            with pytest.raises(RuntimeWindowIncomplete):method(A)
            assert A not in source._final_events and B in source._final_events
    finally:client.close()


def test_routes_report_one_limited_query_with_runtime_source_still_available(tmp_path):
    client=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(transport));source=LiveRuntimeSource(client,'http://runtime.test')
    app=create_app(Settings(runtime_base_url='http://runtime.test',platform_data_dir=tmp_path/'data',web_dist=tmp_path/'no-web'))
    app.state.byul.runtime=source
    try:
        with TestClient(app) as api:
            for suffix in ('','/evidence'):
                response=api.get(f'/api/runtime/requests/{A}'+suffix)
                assert response.status_code==503
                detail=response.json()['detail']
                assert detail['code']=='REQUEST_WINDOW_INCOMPLETE' and detail['source_available'] is True
                assert detail['request_id']==A and 'untrusted backend detail' not in response.text
    finally:client.close()


def test_one_incomplete_detail_does_not_abort_current_request_list_or_other_cached_uuid():
    import time
    class Client:
        def get(self,path,params=None):
            if path=='/api/requests':return dict(requests=[dict(request_id=A),dict(request_id=B)],window=dict(truncated=False,counts_lower_bound=False,total_lower_bound=2))
            raise RuntimeWindowIncomplete(path)
    source=LiveRuntimeSource(Client(),'http://runtime.test')
    event=dict(event_id='req:'+B,source='gateway')
    source._final_events[B]=(time.monotonic(),event)
    batch=source._gateway_events()
    assert batch.candidates==[event] and B in source._final_events
    assert batch.truncated and batch.source_windows['gateway']['truncated']


@pytest.mark.parametrize(('window','expected'), [(dict(truncated=False,counts_lower_bound=False,total_lower_bound=200),False),(dict(truncated=True),True),({},True),(dict(truncated=None),True)])
def test_exact_limit_uses_explicit_source_window_before_legacy_heuristic(window,expected):
    class Client:
        def get(self,path,params=None):
            assert path=='/api/requests'
            return dict(requests=[dict(event_id=str(i)) for i in range(200)],window=window)
    batch=LiveRuntimeSource(Client(),'http://runtime.test')._gateway_events()
    assert len(batch.candidates)==200
    assert batch.truncated is expected
    assert batch.source_windows['gateway']['truncated'] is expected
    if expected is False:assert batch.source_windows['gateway']['total']==200
