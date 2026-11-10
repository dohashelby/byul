"""Existing engine receipt journal survives observer/Gateway restart; source bytes remain separate."""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import pytest
from serving_runtime.mvp import native_receipts
from serving_runtime.mvp.collectors import CollectionFailure
from serving_runtime.mvp.query import QueryService
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.update_engine_link import project_termination
from tests.test_live_termination_checkpoint import live_packet,SESSION
from tests.test_request_termination_projection23 import EXT
from tests.test_runtime_termination_native23 import inputs,RID,UPSTREAM,replace


def digest(value):return hashlib.sha256(native_receipts.canonical(value)).hexdigest()


def document():
    records,original,binding=inputs();p=replace(live_packet(),EXT,hashlib.sha256(UPSTREAM.encode()).hexdigest())
    for side in ('before','after'):p[side]['owner']=deepcopy(original[side]['owner'])
    cap=p['recordings'][0]['capture'];cap['provenance']['owner']=deepcopy(original['before']['owner'])
    from guard.executors.lifecycle_checkpoint import prefix
    cap['checkpoint_prefix']=prefix(cap['records'],cap['summary'],SESSION)[2]
    proof=project_termination(records,RID,p,1,['asgi_transport'],source_binding=binding,source_data_origin='EXAMPLE',checkpoint=True)
    now=datetime.now(timezone.utc).isoformat()
    entry=dict(request_id=RID,projection=proof,capture_state='active',capture_observed_at=now,session_sealed=False,
        selected_external_request_sha256=hashlib.sha256(UPSTREAM.encode()).hexdigest(),projection_sha256=digest(proof),
        source_capture=dict(sha256='1'*64,bytes=100,target_sha256='2'*64,session=SESSION))
    entry['entry_sha256']=digest(entry)
    doc=dict(schema=native_receipts.SCHEMA,producer_source_sha256='3'*64,generated_at=now,state='ok',
        producer_authenticated=False,source_data_origin='EXAMPLE',capture_state='active',capture_observed_at=now,
        source_event_times=[],entries=[entry])
    return doc,records


def feed(store,pin='3'*64):return native_receipts.NativeReceiptFeed('/owned/feed.json',reader_gid=10010,producer_source_sha256=pin,receipt_store=store)


def test_same_existing_store_restores_exact_historical_projection_after_restart(tmp_path,monkeypatch):
    doc,records=document();monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    store=JsonlStore(tmp_path/'engine_receipts.jsonl');first=feed(store)
    first.collect();first.collect();rows,corrupt=store.read_all()
    assert not corrupt and len(rows)==1 and rows[0]['received_at']==doc['generated_at']
    assert rows[0]['source_bytes_retention']=='not_guaranteed' and store.path.stat().st_mode&0o777==0o600
    restarted=feed(store)
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:(_ for _ in ()).throw(FileNotFoundError()))
    with pytest.raises(CollectionFailure):restarted.collect()
    historical=restarted.lookup(records,RID,'new-runtime-session')
    assert historical['historical_receipt'] and historical['source_bytes_retention']=='not_guaranteed'
    assert historical['projection']==doc['entries'][0]['projection']
    gateway=JsonlStore(tmp_path/'gateway.jsonl')
    for row in records:gateway.append({**row,'decision':{'result':'ALLOWED'},'delivery':{'attempted':True},'request':{}})
    query=QueryService(gateway,store,lambda:{},'new-runtime-session',native_receipt_lookup=restarted.lookup)
    detail=query.detail(RID,lambda _:pytest.fail('mock lookup on real backend'),'new-runtime-session')
    assert detail['engine_live']['status']=='not_available' and detail['engine_receipts'][0]['historical_receipt']


@pytest.mark.parametrize('fault',['pin','entry-hash','projection-hash','request-context'])
def test_restored_receipts_revalidate_source_hashes_and_exact_context(tmp_path,monkeypatch,fault):
    doc,records=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    if fault=='entry-hash':doc['entries'][0]['entry_sha256']='0'*64
    if fault=='projection-hash':doc['entries'][0]['projection']['worker_gpu_release_verified']=False
    if fault=='request-context':
        for row in records:row['run_id']='other'
    store.append(dict(kind='source_qualified_native_receipt',request_id=RID,native_feed_document=doc))
    restored=feed(store,pin='4'*64 if fault=='pin' else '3'*64)
    assert restored.lookup(records,RID,'later-session') is None


def test_store_write_failure_is_visible_without_fabricating_persistence(monkeypatch):
    doc,records=document();monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    class Broken:
        def read_recent(self):return [],[],False
        def append(self,row):raise OSError('disk unavailable')
    reader=feed(Broken())
    with pytest.raises(CollectionFailure,match='history journal unavailable') as error:reader.collect()
    status=error.value.last_value
    assert status['historical_storage_state']=='error'
    assert reader.lookup(records,RID,'runtime-session')['projection']==doc['entries'][0]['projection']


def test_confirmed_failure_survives_later_weaker_receipt_and_restart(tmp_path,monkeypatch):
    doc,records=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    reader=feed(store);reader.collect()
    entry=doc['entries'][0];proof=entry['projection']
    proof['native_projection']['activation_violations']=[{'scope':'worker_batch','code':'REQUEST_STATE_RETAINED_AFTER_DEADLINE'}]
    proof['native_termination_verified']=False
    entry['projection_sha256']=digest(proof);entry.pop('entry_sha256');entry['entry_sha256']=digest(entry)
    reader.collect();strong=deepcopy(entry)
    doc['entries'][0]=document()[0]['entries'][0]
    # Fresh timestamp envelope must match the exact new entry.
    doc['capture_observed_at']=doc['entries'][0]['capture_observed_at']
    reader.collect()
    assert reader.lookup(records,RID,'runtime-session')['projection']==strong['projection']
    assert feed(store).lookup(records,RID,'later-session')['projection']==strong['projection']
    assert len(store.read_all()[0])==2


def test_native_history_row_cannot_become_mock_engine_receipt(tmp_path):
    doc,records=document();gateway=JsonlStore(tmp_path/'gateway.jsonl');store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    for row in records:
        gateway.append({**row,'backend_kind':'mock','decision':{'result':'ALLOWED'},'delivery':{'attempted':True},'request':{}})
    store.append(dict(kind='source_qualified_native_receipt',request_id=RID,native_feed_document=doc))
    query=QueryService(gateway,store,lambda:{},'later-session')
    detail=query.detail(RID,lambda _:dict(status='not_found'),'later-session')
    assert detail['engine_receipts']==[] and detail['evidence_state']=='unknown'


def test_native_only_held_run_survives_existing_retention_without_gateway_pair(tmp_path,monkeypatch):
    import json
    from datetime import timedelta
    from serving_runtime.mvp.retention import RecordRetention,HOLD_SCHEMA
    doc,_=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    reader=feed(store);reader.collect()
    saved=store.read_all()[0][0]
    assert saved['run_id']==doc['entries'][0]['projection']['request']['context']['run_id']
    hold=tmp_path/'hold.json';hold.write_text(json.dumps(dict(schema=HOLD_SCHEMA,run_ids=[saved['run_id']])))
    future=datetime.now(timezone.utc)+timedelta(days=100)
    RecordRetention(tmp_path,enforce=True,hold_path=hold,max_age_days=1,max_bytes=1).run(now=future)
    assert len(store.read_all()[0])==1


def test_distinct_scope_failures_keep_both_original_receipts_across_restart(tmp_path,monkeypatch):
    doc,records=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    reader=feed(store)
    def failure(scope):
        entry=doc['entries'][0];proof=entry['projection'];proof['native_termination_verified']=False
        proof['native_projection']['activation_violations']=[{'scope':scope,'code':'REQUEST_STATE_RETAINED_AFTER_DEADLINE'}]
        entry['projection_sha256']=digest(proof);entry.pop('entry_sha256',None);entry['entry_sha256']=digest(entry)
        reader.collect()
    failure('worker_batch');failure('kv_references');failure('kv_references')
    assert len(store.read_all()[0])==2
    assert reader._status()['historical_receipts']==2
    for observed in (reader.lookup(records,RID,'runtime-session'),feed(store).lookup(records,RID,'later-session')):
        receipts=[observed,*observed['historical_related_receipts']]
        assert {r['projection']['native_projection']['activation_violations'][0]['scope'] for r in receipts}=={'worker_batch','kv_references'}


def test_unsaved_original_failure_is_retried_before_weaker_receipt_after_disk_recovery(tmp_path,monkeypatch):
    doc,records=document();weak=deepcopy(doc);entry=doc['entries'][0];proof=entry['projection']
    proof['native_projection']['activation_violations']=[{'scope':'worker_batch','code':'REQUEST_STATE_RETAINED_AFTER_DEADLINE'}]
    proof['native_termination_verified']=False;entry['projection_sha256']=digest(proof);entry.pop('entry_sha256');entry['entry_sha256']=digest(entry)
    store=JsonlStore(tmp_path/'engine_receipts.jsonl');append=store.append;failing=[True]
    def flaky(row):
        if failing[0]:raise OSError('disk unavailable')
        append(row)
    monkeypatch.setattr(store,'append',flaky)
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    reader=feed(store)
    with pytest.raises(CollectionFailure,match='history journal unavailable') as error:reader.collect()
    status=error.value.last_value
    assert status['historical_storage_state']=='error' and status['historical_pending_receipts']==1
    failing[0]=False;doc.clear();doc.update(weak)
    status,_=reader.collect()
    assert status['historical_storage_state']=='connected' and status['historical_pending_receipts']==0
    restored=feed(store).lookup(records,RID,'later-session')
    assert restored['projection']['native_projection']['activation_violations'][0]['scope']=='worker_batch'
    assert len(store.read_all()[0])==1


def test_targeted_restore_revalidates_bounded_supplied_rows_without_store_reread(tmp_path,monkeypatch):
    doc,records=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    row=dict(kind='source_qualified_native_receipt',request_id=RID,run_id='run-one',native_feed_document=doc)
    reader=feed(store)
    monkeypatch.setattr(store,'read_recent',lambda *a,**k:pytest.fail('restore_matching reread journal'))
    monkeypatch.setattr(store,'read_all',lambda *a,**k:pytest.fail('restore_matching reread journal'))
    assert reader.restore_matching([row])==1
    assert reader.lookup(records,RID,'later-session')['historical_receipt']
    wrong=native_receipts.NativeReceiptFeed('/owned/feed.json',reader_gid=10010,producer_source_sha256='4'*64)
    assert wrong.restore_matching([row])==0 and wrong.lookup(records,RID,'later-session') is None
    reader._historical.clear();bad=deepcopy(row);bad['run_id']='different-run'
    assert reader.restore_matching([bad])==0 and reader.lookup(records,RID,'later-session') is None


def test_query_lazily_restores_retained_receipt_outside_startup_tail(tmp_path,monkeypatch):
    import json
    doc,records=document();store=JsonlStore(tmp_path/'engine_receipts.jsonl')
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(doc))
    feed(store).collect()
    with store.path.open('ab') as stream:
        noise=(json.dumps({'request_id':'0'*32,'kind':'other_receipt','fixture_padding':'x'*900})+'\n').encode()
        for _ in range(7000):stream.write(noise)
    restored=feed(store)
    assert restored.lookup(records,RID,'new-runtime-session') is None
    gateway=JsonlStore(tmp_path/'gateway.jsonl')
    for row in records:gateway.append({**row,'decision':{'result':'ALLOWED'},'delivery':{'attempted':True},'request':{}})
    query=QueryService(gateway,store,lambda:{'ok':True},'new-runtime-session',
        native_receipt_lookup=restored.lookup,native_receipt_restore=restored.restore_matching)
    detail=query.detail(RID,lambda _: {'status':'not_available'},'new-runtime-session')
    assert detail['engine_receipts'][0]['projection']==doc['entries'][0]['projection']
    assert detail['engine_receipts'][0]['source_bytes_retention']=='not_guaranteed'


def test_current_feed_preserves_qualified_history_after_gateway_session_restart(tmp_path, monkeypatch):
    doc, records = document()
    monkeypatch.setattr(native_receipts, 'read_host_feed', lambda *a: deepcopy(doc))
    reader = feed(JsonlStore(tmp_path/'engine_receipts.jsonl'))
    reader.collect()
    historical = reader.lookup(records, RID, 'new-runtime-session')
    assert historical is not None
    assert historical['historical_receipt'] is True
    assert historical['projection'] == doc['entries'][0]['projection']


def test_restart_does_not_hide_stronger_unqualified_live_failure_behind_saved_success(tmp_path, monkeypatch):
    doc, records = document()
    monkeypatch.setattr(native_receipts, 'read_host_feed', lambda *a: deepcopy(doc))
    reader = feed(JsonlStore(tmp_path/'engine_receipts.jsonl'))
    reader.collect()
    saved = deepcopy(reader._historical[RID])
    entry = doc['entries'][0]
    proof = entry['projection']
    proof.update(native_termination_verified=False, worker_gpu_release_verified=False,
                 checkpoint_coverage_verified=False)
    proof['native_projection'].update(checkpoint_coverage_verified=False, whole_scope_complete=False,
        activation_violations=[{'scope':'kv_references','code':'REQUEST_STATE_RETAINED_AFTER_DEADLINE'}])
    entry['projection_sha256'] = digest(proof)
    entry.pop('entry_sha256')
    entry['entry_sha256'] = digest(entry)
    reader.collect()
    assert reader._historical[RID] == saved
    assert reader.lookup(records, RID, 'new-runtime-session') is None
