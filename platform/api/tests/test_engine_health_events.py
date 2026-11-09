from byul_platform.sources.runtime import observation_event,_collector_detail,LiveRuntimeSource


def record(state='unavailable'):
    return dict(schema_version='runtime-engine-health-event/1.0',source='engine_health',kind='engine_unavailable' if state=='unavailable' else 'engine_recovered',
        health_state=state,observed_at='2026-11-08T10:00:00+00:00',event_key='a'*64,environment='lab',run_id='run1',
        target_id='target1',target_instance_id='instance1',observation_session_id='session1',policy=dict(policy_id='p',policy_version='1',sha256='2'*64),
        target=dict(pod_uid='pod1',container_id='containerd://cid1',restart_count=0),data_origin='real',reason='engine_health_check_failed')


def test_health_event_preserves_actual_scope_policy_and_original_record(contracts):
    for state,outcome in [('unavailable','error'),('healthy','observed')]:
        original=record(state);event=observation_event(original)
        assert event['source']=='engine_health' and event['category']=='process' and event['outcome']==outcome
        assert event['scope']==dict(environment='lab',run_id='run1',observation_session_id='session1',request_id=None)
        assert event['target_instance_id']=='instance1' and event['policy_ref']['sha256']=='2'*64
        assert event['related_checks']==['LIF-08'] and event['native']['record']==original
        assert not contracts.errors('runtime-event',event)


def test_live_source_fetches_health_observations_without_inference_and_cache_uses_asset_view():
    source=LiveRuntimeSource.__new__(LiveRuntimeSource);calls=[]
    def get(path,params):
        calls.append((path,params))
        rows=[record()] if params['source']=='engine_health' else []
        return dict(observations=rows,counts=dict(total=len(rows)))
    source._get=get
    batch=source._observation_events()
    assert len(batch.candidates)==1 and batch.candidates[0]['source']=='engine_health'
    assert all(path=='/api/observations' for path,_ in calls)
    detail=_collector_detail(dict(source='cache_integrity',last_value=dict(intact=False,total=1,counts=dict(changed=1),limit='selected generation/mount check')))
    assert detail['kind']=='asset_integrity' and detail['limit']=='selected generation/mount check'


def test_partial_history_counts_are_lower_bounds_not_fabricated_full_totals():
    source=LiveRuntimeSource.__new__(LiveRuntimeSource)
    source._get=lambda *a:dict(observations=[record()],counts=dict(total=1),window=dict(truncated=True,counts_lower_bound=True))
    batch=source._observation_events()
    assert batch.truncated
    assert all(w['total'] is None and w['total_lower_bound']==1 and w['truncated'] for w in batch.source_windows.values())


def test_request_summary_preserves_bounded_count_flags():
    source=LiveRuntimeSource.__new__(LiveRuntimeSource)
    source._get=lambda *a:dict(counts=dict(ALLOWED=2),total=2,window=dict(truncated=True,counts_lower_bound=True))
    summary=source.summary()
    assert summary.total==2 and summary.counts_lower_bound and summary.history_truncated


def test_request_detail_retains_exact_search_completeness_window():
    from byul_platform.sources.runtime import request_detail_doc
    window=dict(truncated=True,gateway_search_complete=False,engine_search_complete=False,max_search_bytes=64*1024*1024)
    result=request_detail_doc(dict(request_id='a'*32,gateway_records=[],engine_receipts=[],window=window))
    assert result['query_window']==window


def test_native_scope_and_pending_loss_metadata_survive_platform_normalization():
    value=dict(capture_state='unavailable',truncated=True,historical_pending_receipts=2,
        historical_pending_dropped=3,historical_storage_state='error',capture_health_reason='checkpoint_stalled')
    detail=_collector_detail(dict(source='native_receipt_observer',last_value=value))
    assert all(detail[k]==v for k,v in value.items())
    assert 'lost_at' not in detail
