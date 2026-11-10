"""Background health transitions persist without inference; no worker-causality claim."""
from copy import deepcopy
import json
import pytest
from serving_runtime.mvp.collectors import Collector
from serving_runtime.mvp.engine_health_events import EngineHealthEvents
from serving_runtime.mvp.store import JsonlStore


def context():return dict(environment='lab',run_id='run1',backend_kind='vllm',target_id='target1',observation_session_id='session1',
    target=dict(instance_id='instance1',pod_uid='pod1',container_id='containerd://one',restart_count=0,image_digest='sha256:'+'1'*64),
    policy=dict(policy_id='p',policy_version='1',sha256='2'*64),policy_bundle=dict(bundle_id='bundle1'))


def sample(ok,second):return dict(success=ok,attempted_at=f'2026-11-08T10:00:{second:02d}+00:00',observed_at=f'2026-11-08T10:00:{second:02d}.100000+00:00',error_kind=None if ok else 'TimeoutError')


def test_background_failure_recovery_records_exact_context_without_poll_spam(tmp_path):
    store=JsonlStore(tmp_path/'observations.jsonl');journal=EngineHealthEvents(store,context)
    for ok,second in ((True,0),(True,1),(False,2),(False,3),(True,4),(True,5)):journal.observe(sample(ok,second))
    rows,corrupt=store.read_all();assert not corrupt
    assert [r['kind'] for r in rows]==['engine_healthy','engine_unavailable','engine_recovered']
    assert rows[2]['previous_event_key']==rows[1]['event_key'] and rows[2]['first_failure_at']==sample(False,2)['observed_at']
    assert rows[2]['failure_attempt_count']==2 and rows[2]['failure_attempt_count_complete']
    for row in rows:
        assert row['target']['pod_uid']=='pod1' and row['target']['container_id']=='containerd://one'
        assert row['run_id']=='run1' and row['policy']['sha256']=='2'*64
        assert not row['worker_failure_causality_verified'] and not row['inference_verified']
    assert store.path.stat().st_mode&0o777==0o600


def test_restart_recovers_same_episode_and_new_instance_does_not_recover_old_target(tmp_path):
    store=JsonlStore(tmp_path/'observations.jsonl');scope=context();first=EngineHealthEvents(store,lambda:scope)
    first.observe(sample(False,0));again=EngineHealthEvents(store,lambda:scope);again.observe(sample(True,1))
    rows,_=store.read_all();assert rows[-1]['kind']=='engine_recovered' and not rows[-1]['failure_attempt_count_complete']
    again.observe(sample(False,2));scope['target']['container_id']='containerd://new';scope['target']['restart_count']=1
    again.observe(sample(True,3));assert store.read_all()[0][-1]['kind']=='engine_healthy'


def test_failed_journal_retries_original_failure_before_recovery(tmp_path,monkeypatch):
    store=JsonlStore(tmp_path/'observations.jsonl');append=store.append;fail=[True]
    def write(row):
        if fail[0]:raise OSError('private')
        append(row)
    monkeypatch.setattr(store,'append',write);journal=EngineHealthEvents(store,context)
    with pytest.raises(OSError,match='journal unavailable'):journal.observe(sample(False,0))
    fail[0]=False;journal.observe(sample(True,1))
    assert [r['kind'] for r in store.read_all()[0]]==['engine_unavailable','engine_recovered']


def test_collector_notifies_failure_and_success_after_lock_and_exposes_sink_error(tmp_path):
    values=iter([OSError('health failed'),({'reachable':True},'external_backend')]);seen=[]
    def fetch():
        value=next(values)
        if isinstance(value,Exception):raise value
        return value
    collector=None
    def observe(row):
        seen.append((row,collector.snapshot()))
        if not row['success']:raise OSError('disk')
    collector=Collector('engine_health',fetch,on_updated=observe)
    collector.collect();assert 'journal unavailable' in collector.snapshot()['last_error']
    collector.collect();assert collector.snapshot()['state']=='ok'
    assert [row['success'] for row,_ in seen]==[False,True]
    assert seen[0][0]['observed_at']>=seen[0][0]['attempted_at']


def test_gateway_health_collector_hook_reaches_existing_observation_query_without_requests(tmp_path,monkeypatch):
    from pathlib import Path
    from serving_runtime.mvp.app import GatewayApp
    from serving_runtime.mvp.config import MVPConfig
    from serving_runtime.mvp.policy import load_policy
    policy=load_policy(Path(__file__).resolve().parents[1]/'examples/mvp/policy.json')
    app=GatewayApp(MVPConfig(data_dir=str(tmp_path),environment='lab',run_id='run1'),policy,engine_address=('127.0.0.1',1))
    monkeypatch.setattr(app,'_target_record_context',lambda:context()['target'])
    collector=next(c for c in app._collectors.collectors if c.source=='engine_health')
    collector.fetch=lambda:(_ for _ in ()).throw(OSError('unreachable'))
    collector.collect()
    collector.fetch=lambda:({'status':'ok'},'mock')
    collector.collect()
    records=app._observations.query({'source':'engine_health'})['observations']
    assert {r['kind'] for r in records}=={'engine_unavailable','engine_recovered'}
    assert all(r['target']['container_id']=='containerd://one' for r in records)
    assert not app._records.store.read_all()[0]


def test_restore_never_reads_entire_shared_journal_and_truncated_history_is_unknown(tmp_path,monkeypatch):
    store=JsonlStore(tmp_path/'observations.jsonl')
    journal=EngineHealthEvents(store,context);journal.observe(sample(False,0))
    with store._lock,store._open(__import__('os').O_WRONLY|__import__('os').O_APPEND) as stream:
        stream.write((b'{"source":"hubble","padding":"'+b'x'*1000+b'"}\n')*5000)
    monkeypatch.setattr(store,'read_all',lambda:pytest.fail('unbounded restore'))
    restored=EngineHealthEvents(store,context);restored.observe(sample(True,1))
    rows,_,truncated=store.read_recent()
    assert truncated and rows[-1]['kind']=='engine_healthy'
    assert not rows[-1]['history_window_complete'] and not rows[-1]['failure_attempt_count_complete']
