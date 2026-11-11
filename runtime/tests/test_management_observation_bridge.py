"""Management journal -> real polling API -> existing Platform event adapter."""
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from byul_platform.config import Settings
from byul_platform.main import create_app
from serving_runtime.enforcement.events import parse_management_access, EventError
from serving_runtime.mvp.management_listener import ManagementListener, ManagementListenerConfig, AccessRecorder
from serving_runtime.mvp.observation_feed import ObservationFeed
from serving_runtime.mvp.collectors import Collector
from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.mvp.policy import load_policy
from byul_platform.sources.runtime import RuntimeClient, LiveRuntimeSource, observation_event
from byul_platform.contracts import Contracts


def request(listener, path):
    c=HTTPConnection(*listener.address,timeout=2)
    try:c.request('GET',path);r=c.getresponse();r.read();return r.status
    finally:c.close()


def make_feed(tmp_path):
    path=tmp_path/'access.jsonl';heartbeat=tmp_path/'writer.json'
    recorder=AccessRecorder(path,heartbeat_path=heartbeat,scope={'target_ref':'engine-a','policy_ref':'policy-a','environment':'lab','run_id':'run-a'})
    recorder.start_heartbeat()
    feed=ObservationFeed(kind='management_listener',path=path,data_dir=tmp_path/'data',data_origin='real',environment='lab',run_id='run-a',heartbeat_path=heartbeat)
    return recorder,feed


def test_real_management_http_polling_runtime_and_platform(tmp_path):
    engine=MockEngine(port=0).start();gw=None;listener=None;client=None
    try:
        raw=tmp_path/'access.jsonl';heartbeat=tmp_path/'writer.json'
        listener=ManagementListener(ManagementListenerConfig.build(listen_port=0,engine_url='http://%s:%s'%engine.address,record_path=raw,heartbeat_path=heartbeat,target_ref='engine-a',policy_ref='policy-a',environment='lab',run_id='run-a')).start()
        policy=Path(__file__).parents[1]/'examples/mvp/policy.json'
        config=MVPConfig(gateway_port=0,engine_port=0,policy_path=str(policy),data_dir=str(tmp_path/'gw'),collector_interval_s=.05,observation_feeds=(('management_listener',str(raw)),),observation_heartbeats=(('management_listener',str(heartbeat)),),observation_environment='lab',observation_run_id='run-a')
        gw=GatewayApp(config,load_policy(policy),engine_address=engine.address).start()
        assert request(listener,'/v1/models')==200
        assert request(listener,'/not-allowed?secret=NEVER_KEEP_THIS')==404
        url='http://%s:%s'%gw.address;client=RuntimeClient(url,None);source=LiveRuntimeSource(client,url,cache_ttl=0)
        deadline=time.monotonic()+3
        while time.monotonic()<deadline:
            batch=source._observation_events()
            managed_events=[event for event in batch.candidates if event.get("source")=="management_listener"]
            if len(managed_events)==2:break
            time.sleep(.05)
        assert len(managed_events)==2
        contracts=Contracts(Path(__file__).parents[2]/'contracts')
        assert {e['outcome']for e in managed_events}=={'allowed','denied'}
        for event in managed_events:
            assert not contracts.errors('runtime-event',event)
            assert event['source']==event['enforcer']=='management_listener'
            assert 'OBS-01' in event['related_checks']
            assert event['target_ref']=='engine-a' and event['native']['record']['policy_ref']=='policy-a'
            assert event['scope']['run_id']=='run-a' and event['scope']['environment']=='lab'
            assert event['native']['record']['actor']=={'state':'unattributed','principal_id':None}
        assert 'NEVER_KEEP_THIS' not in json.dumps(batch.candidates)
        # The existing Platform REST endpoint calls the real Gateway, with no
        # HTTP transport stub or event adapter replacement.
        with TestClient(create_app(Settings(runtime_base_url=url,web_dist=tmp_path/'no-web',platform_data_dir=tmp_path/'platform'))) as platform:
            response=platform.get('/api/runtime/events')
            assert response.status_code==200
            payload=response.json()
            managed=[e for e in payload['items'] if e['source']=='management_listener']
            assert len(managed)==2 and payload['invalid']==[]
            assert {e['outcome']for e in managed}=={'allowed','denied'}
            assert platform.get('/api/runtime/checks').status_code==200

        status=next(x for x in gw.collectors_status()['collectors']if x['source']=='observation_feed_management_listener')
        assert status['state']=='ok'
        listener.stop();listener=None
        deadline=time.monotonic()+2
        while time.monotonic()<deadline:
            status=next(x for x in gw.collectors_status()['collectors']if x['source']=='observation_feed_management_listener')
            if status['state']=='error':break
            time.sleep(.05)
        assert status['state']=='error' and status['last_value']['writer']['state']=='stopped'
    finally:
        if client:client._http.close()
        if gw:gw.stop()
        if listener:listener.stop()
        engine.stop()


def test_duplicate_restart_storage_failure_and_recovery(tmp_path):
    recorder,feed=make_feed(tmp_path)
    try:
        recorder.record(allowed=True,path='/health',status_code=403,method='GET')
        collector=Collector('observation_feed_management_listener',feed.collect)
        with patch.object(feed.store,'append',side_effect=OSError('private-details')):collector.collect()
        assert collector.snapshot()['state']=='error' and feed._offset==0
        collector.collect();assert collector.snapshot()['state']=='ok'
        rows=feed.store.read_all()[0];assert len(rows)==1 and observation_event(rows[0])['outcome']=='allowed'
        with feed.path.open('ab')as out:out.write(feed.path.read_bytes())
        assert feed.collect()[0]['duplicates']==1
        restored=ObservationFeed(kind='management_listener',path=feed.path,data_dir=feed.state_path.parent,data_origin='real',environment='lab',run_id='run-a',heartbeat_path=feed.heartbeat_path)
        assert restored.collect()[0]['accepted']==0 and len(restored.store.read_all()[0])==1
        assert restored.collect()[0]['storage_errors_total']==1
        recorder._write_failure_count=1;recorder._write_failed=True;recorder._last_write_error_class='OSError';recorder._last_write_error_at=datetime.now(timezone.utc).isoformat();recorder._write_heartbeat('running');collector.collect()
        assert collector.snapshot()['state']=='error'
        recorder._write_failed=False;recorder._write_heartbeat('running');collector.collect()
        assert collector.snapshot()['state']=='ok'
    finally:recorder.close()


@pytest.mark.parametrize('change',[{'recorded_at':'2026-01-01'}, {'recorded_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}, {'scope':{'environment':'other'}}, {'allowed':1}, {'status_code':True}, {'method':'GET\nsecret'}, {'event_id':'bad'}, {'path':'/health?token=private'}])
def test_invalid_time_scope_and_fields_are_rejected(tmp_path,change):
    recorder,feed=make_feed(tmp_path)
    try:
        recorder.record(allowed=False,path='/blocked',status_code=403,method='GET')
        row=json.loads(feed.path.read_text());row.update(change)
        with pytest.raises(EventError):parse_management_access(row,data_origin='real',environment='lab',run_id='run-a')
    finally:recorder.close()


def test_old_rows_and_untrusted_actor_do_not_gain_identity(tmp_path):
    recorder,feed=make_feed(tmp_path)
    try:
        recorder.record(allowed=True,path='/health',status_code=403,method='GET')
        row=json.loads(feed.path.read_text());row.pop('allowed');row['actor']={'state':'authenticated','principal_id':'forged'}
        item=parse_management_access(row,data_origin='real')
        assert item['actor']=={'state':'unattributed','principal_id':None}
        assert observation_event(item)['outcome']=='unknown'
    finally:recorder.close()


def test_management_journal_permissions_and_symlink(tmp_path):
    recorder,feed=make_feed(tmp_path)
    try:
        feed.path.chmod(0o666)
        with pytest.raises(PermissionError):feed.collect()
        feed.path.chmod(0o600);target=tmp_path/'other';target.write_text('');feed.path.unlink();feed.path.symlink_to(target)
        with pytest.raises(OSError):feed.collect()
    finally:recorder.close()


def test_actual_writer_sink_failure_recovers_without_erasing_failure_count(tmp_path):
    class Broken:
        def write(self,_):raise OSError('secret-source-value')
        def flush(self):pass
    recorder,feed=make_feed(tmp_path)
    try:
        recorder._stream=Broken()
        recorder.record(allowed=True,path='/health',status_code=200,method='GET')
        recorder._write_heartbeat('running')
        collector=Collector('observation_feed_management_listener',feed.collect);collector.collect()
        assert collector.snapshot()['state']=='error'
        from byul_platform.sources.runtime import _collector_detail
        detail=_collector_detail(collector.snapshot())
        assert detail['writer_write_failure_count']==1 and detail['writer_write_failed'] is True
        assert detail['writer_last_write_error_class']=='OSError' and detail['writer_last_write_error_at']
        recorder._stream=None
        recorder.record(allowed=True,path='/health',status_code=200,method='GET')
        recorder._write_heartbeat('running');collector.collect()
        assert collector.snapshot()['state']=='ok'
        assert collector.snapshot()['last_value']['writer']['recording']['write_failure_count']==1
        detail=_collector_detail(collector.snapshot())
        assert detail['writer_write_failed'] is False and detail['writer_write_failure_count']==1
        assert 'secret-source-value' not in json.dumps(collector.snapshot())
    finally:recorder.close()


@pytest.mark.parametrize('mode',[0o666,0o644,0o640])
def test_writer_uses_shared_store_private_permission_admission(tmp_path,mode):
    path=tmp_path/'access.jsonl';path.write_text('');path.chmod(mode)
    with pytest.raises(PermissionError):AccessRecorder(path)
    assert path.stat().st_mode & 0o777 == mode


def test_writer_rechecks_boundary_after_open_and_retains_failure_receipt(tmp_path):
    path=tmp_path/'access.jsonl';recorder=AccessRecorder(path)
    try:
        path.chmod(0o666)
        recorder.record(allowed=False,path='/blocked',status_code=404,method='GET')
        assert path.read_text()=='' and recorder.status()['write_failed'] is True
        assert recorder.status()['last_write_error_class']=='PermissionError'
        path.chmod(0o600)
        recorder.record(allowed=False,path='/blocked',status_code=404,method='GET')
        assert len(path.read_text().splitlines())==1
        assert recorder.status()['write_failed'] is False
        assert recorder.status()['write_failure_count']==1
    finally:recorder.close()
