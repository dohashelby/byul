"""Current list snapshot event projection has no per-request detail fanout."""
import time
import httpx
import pytest
from byul_platform.sources.runtime import LiveRuntimeSource,RuntimeClient,RuntimeUnauthorized,gateway_event
A='a'*32;B='b'*32


def row(rid,phase='final',at='2026-11-09T12:00:00Z',result='ALLOWED'):
    return dict(request_id=rid,phase=phase,recorded_at=at,decision={'result':result},delivery={},request={},data_origin='real')


def test_200_inline_events_use_one_fresh_list_and_exact_existing_projection():
    ids=[f'{i:032x}' for i in range(200)];calls=[]
    records={rid:[row(rid,'started'),row(rid)] for rid in ids}
    records[ids[0]]+=[row(ids[0],at='2026-11-09T12:01:00Z',result='DENIED'),row(ids[0],'started',at='2026-11-09T12:02:00Z')]
    def transport(request):
        calls.append(request.url.path)
        assert request.url.path=='/api/requests'
        assert request.url.params['include_records']=='1' and request.url.params['limit']=='200'
        return httpx.Response(200,json={'requests':[{'request_id':rid}for rid in ids],
             'gateway_records':records,'window':{'truncated':False,'counts_lower_bound':False,'total_lower_bound':200}})
    client=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(transport));source=LiveRuntimeSource(client,'http://runtime.test')
    try:
        source._final_events[ids[0]]=(time.monotonic(),{'old':'cached final'})
        batch=source._gateway_events()
        assert len(calls)==1 and len(batch.candidates)==200 and not batch.truncated
        assert batch.candidates[0]==gateway_event(records[ids[0]][-2])
        assert batch.candidates[1]==gateway_event(records[ids[1]][-1])
        records[ids[0]]=[row(ids[0],result='ERROR')]
        assert source._gateway_events().candidates[0]==gateway_event(records[ids[0]][0])
        assert len(calls)==2
    finally:client.close()


def test_inline_deletion_and_authorization_are_checked_each_time_and_clear_old_cache():
    payload={'requests':[{'request_id':A}],'gateway_records':{A:[row(A)]}};status=200
    def transport(request):return httpx.Response(status,json=payload)
    client=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(transport));source=LiveRuntimeSource(client,'http://runtime.test')
    try:
        assert len(source._gateway_events().candidates)==1
        source._final_events[A]=(time.monotonic(),gateway_event(row(A)))
        payload={'requests':[],'gateway_records':{}}
        assert source._gateway_events().candidates==[] and not source._final_events
        source._final_events[A]=(time.monotonic(),gateway_event(row(A)))
        status=403
        with pytest.raises(RuntimeUnauthorized):source._gateway_events()
        assert not source._final_events
    finally:client.close()


def test_inline_corruption_or_missing_record_keeps_current_window_limited_and_ignores_cache():
    class Client:
        def get(self,path,params=None):
            assert path=='/api/requests'
            return {'requests':[{'request_id':A},{'request_id':B}],
                    'gateway_records':{A:[row(A,'started')],B:[row(A)]},
                    'window':{'truncated':True,'counts_lower_bound':True,'total_lower_bound':2}}
    source=LiveRuntimeSource(Client(),'http://runtime.test');source._final_events[B]=(time.monotonic(),gateway_event(row(B)))
    batch=source._gateway_events()
    assert batch.candidates==[gateway_event(row(A,'started'))]
    assert B not in source._final_events
    assert batch.truncated and batch.source_windows['gateway']['total'] is None
    assert batch.source_windows['gateway']['total_lower_bound']==2
