import httpx
from fastapi.testclient import TestClient
from byul_platform.config import Settings
from byul_platform.main import create_app
from byul_platform.sources.runtime import LiveRuntimeSource, RuntimeClient


def test_unconfigured_example_does_not_fabricate_verification(example_client):
    response=example_client.get('/api/runtime/tetragon-status')
    assert response.status_code==200
    item=response.json()['item']
    assert item['state']=='not_configured'
    assert item['verifications']==[]
    assert item['fresh'] is False


def test_invalid_snapshot_is_not_shown_as_current(store_client,monkeypatch):
    monkeypatch.setattr(store_client.app.state.byul.runtime,'tetragon_status',lambda:{'fresh':True,'state':'ok'})
    item=store_client.get('/api/runtime/tetragon-status').json()['item']
    assert item['state']=='error'
    assert item['fresh'] is False
    assert item['policies']==[]


def test_live_projection_keeps_effects_separate_and_only_reads(tmp_path):
    calls=[]
    effects={'expected_effect':'process_termination','file_read_denial':'unknown','process_termination':'confirmed',
             'service_recovery':'confirmed','monitor_restored':True,'status':'passed'}
    snapshot={'schema_version':'runtime-tetragon-state/1.0','state':'ok','fresh':True,'context':'kind-runtime-lab',
              'monitoring':{'state':'recovering','reasons':['waiting_for_attributed_event'],
                            'policies_verified':True,'writer_verified':True,'identities_verified':True,'events_verified':False,
                            'open_gap':{'opened_at':'2026-11-06T00:00:00Z','reason':'agent_epoch_changed'}},
              'verifications':[{'id':'probe','context':'kind-runtime-lab','namespace':'trial','policy':'trial',
                'method':'sigkill_fd_install','data_origin':'real','started_at':'2026-11-06T00:00:00Z',
                'finished_at':'2026-11-06T00:00:03Z','verification':effects,
                'task_identity':{'pod_uid':'uid','namespace':'trial','container_id':'fake-id','node_pid':123,
                                 'ns_pid':1,'pidns':456,'start_ticks':1000,'clock_ticks':100},
                'events':[{'time':'2026-11-06T00:00:02Z','policy':'trial','pod_uid':'uid','pid':42,
                           'identity_source':'kernel_task','kernel_task':{'pid':42,'start_ns':10_005_000_000,'pidns':456}}]}]}
    def respond(request):
        calls.append((request.method,request.url.path))
        return httpx.Response(200,json=snapshot)
    runtime=RuntimeClient('http://runtime.test',None,transport=httpx.MockTransport(respond))
    app=create_app(Settings(web_dist=tmp_path/'missing'))
    with TestClient(app) as client:
        client.app.state.byul.runtime=LiveRuntimeSource(runtime,'http://runtime.test')
        response=client.get('/api/runtime/tetragon-status')
        assert response.status_code==200
        verification=response.json()['item']['verifications'][0]['verification']
        assert verification['process_termination']=='confirmed'
        assert verification['file_read_denial']=='unknown'
        proof=response.json()['item']['verifications'][0]
        assert proof['task_identity']['pidns']==456
        assert proof['events'][0]['identity_source']=='kernel_task'
        assert proof['events'][0]['kernel_task']['start_ns']=='10005000000'
        monitoring=response.json()['item']['monitoring']
        assert monitoring['state']=='recovering'
        assert monitoring['events_verified'] is False
        assert monitoring['open_gap']['reason']=='agent_epoch_changed'
        assert calls==[('GET','/api/tetragon-status')]
        assert client.post('/api/runtime/tetragon-status').status_code==405
    runtime.close()
