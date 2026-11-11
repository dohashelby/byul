"""EXAMPLE live-prefix coverage; unchanged sealed projection and positive predicates."""
from copy import deepcopy
import hashlib
import json
import pytest
from guard.executors.lifecycle_checkpoint import canonical, prefix
from guard.adapters.vllm_termination_projection import project_native, project_checkpoint
from tests.test_request_termination_projection23 import packet, EXT

SESSION='a'*32


def live_packet():
    p,_=packet()
    p['session']=SESSION;p['capture_mode']='live_checkpoint'
    for side in ('before','after'):
        p[side]['metadata'].update(sealed=False,directory_stable=True,unknown_entries=0,
            files_truncated=False,processes_truncated=False,unreadable_pids=[])
        directory={'device':1,'inode':1,'uid':1000,'mode':0o700,'mtime_ns':1}
        p[side]['metadata'].update(schema='guard-lifecycle-session-inventory/1',session=SESSION,
            directory_before=directory,directory_after=dict(directory),files=[],processes=[])
        p[side]['owner_stable']=True
    for item in p['recordings']:
        cap=item['capture'];rows=cap['records'][:-1]
        context=rows[0]['payload']['context'];context.setdefault('uid',1000)
        for side in ('before','after'):
            p[side]['metadata']['files'].append(dict(pid=context['pid'],start_ticks=context['start_ticks'],bytes=1,
                uid=1000,mode=0o600,device=1,inode=2,mtime_ns=1,ctime_ns=1,regular=True))
            p[side]['metadata']['processes'].append(dict(context,parent_pid=1,same_executable=True))
        rows[0]['payload']['session']=SESSION
        previous='0'*64
        for seq,row in enumerate(rows):
            row['seq']=seq;row['previous_sha256']=previous
            previous=hashlib.sha256(canonical(row)).hexdigest()
        marker={'event':'CHECKPOINT','seq':len(rows),'previous_sha256':previous,
            'monotonic_ns':max(2_000_000_000,rows[-1]['monotonic_ns']),
            'payload':{'schema':'guard-native-checkpoint-ack/1','session':SESSION,
                'prefix_seq':len(rows),'prefix_sha256':previous}}
        rows.append(marker)
        cap['summary'].update(recording_closed=False,recording_end=None,incomplete_final_row=False,discarded_tail_bytes=0)
        cap['records']=rows;cap['checkpoint_prefix']=prefix(rows,cap['summary'],SESSION)[2]
    return p


def projected(p):return project_checkpoint(p,EXT,1,['asgi_transport'])


def test_live_checkpoints_keep_session_open_and_reuse_all_seven_positive_scopes():
    p=live_packet();original=deepcopy(p)
    old=project_native(p,EXT,1,['asgi_transport'])
    assert old['binding_observed'] and not old['whole_scope_complete']
    result=projected(p)
    assert result['checkpoint_coverage_verified'] and result['whole_scope_complete']
    assert result['worker_gpu_release_verified'] and all(r['termination_verified'] for r in result['scope_status'])
    assert result['closure_basis']=='live_checkpoint'
    assert p==original and all(not p[s]['metadata']['sealed'] for s in ('before','after'))
    assert all(not r['capture']['summary']['recording_closed'] for r in p['recordings'])


@pytest.mark.parametrize('fault',['marker','wrong-session','short-cutoff','marker-hash','bound','gap','census','unreadable','unknown-worker','clock','identity','no-worker-facts','pending-fence','retained-slot'])
def test_incomplete_live_checkpoint_never_upgrades_cleanup(fault):
    p=live_packet();item=p['recordings'][0];cap=item['capture'];rows=cap['records']
    if fault=='marker':rows.pop()
    elif fault=='wrong-session':rows[-1]['payload']['session']='b'*32
    elif fault=='short-cutoff':rows[-1]['monotonic_ns']=0
    elif fault=='marker-hash':cap['checkpoint_prefix']['prefix_sha256']='0'*64
    elif fault=='bound':cap['summary'].update(recording_closed=True,recording_end='BOUND_REACHED')
    elif fault=='gap':
        row=next(r for r in rows if r['event']=='TERMINATION');row['payload']['phase']='OBSERVER_GAP'
    elif fault=='census':p['after']['metadata']['files'].append({'pid':999})
    elif fault=='unreadable':p['after']['metadata']['unreadable_pids']=[999]
    elif fault=='unknown-worker':p['python_processes_without_recording']=[{'pid':999,'start_ticks':1}]
    elif fault=='clock':rows[0]['payload']['context']['time_namespace']=999
    elif fault=='identity':item['listed_file_identity_match']=False
    elif fault=='no-worker-facts':cap['records']=[r for r in rows if r['event']!='TERMINATION' or r['payload']['point']!='GPUModelRunner.finish_requests']
    elif fault=='pending-fence':
        for r in rows:
            if r['event']=='TERMINATION' and r['payload']['phase']=='FENCE_COMPLETE':r['payload']['phase']='FENCE_PENDING'
    else:
        for r in rows:
            if r['event']=='TERMINATION':
                for state in r['payload'].get('states',[]):
                    if 'worker_slot_present'in state:state['worker_slot_present']=True
    result=projected(p)
    assert not result['whole_scope_complete'] and not result['worker_gpu_release_verified']


def test_empty_marker_without_positive_resource_facts_cannot_prove_absence():
    p=live_packet()
    for item in p['recordings']:
        item['capture']['records']=[r for r in item['capture']['records'] if r['event'] not in {'NATIVE','TERMINATION'}]
    assert not projected(p)['whole_scope_complete']

@pytest.mark.parametrize('backend',['vllm','sglang'])
@pytest.mark.parametrize('fault',['none','stale','future','clock','inode','owner','rewrite','later-partial','later-bound','later-gap'])
def test_capture_reads_exact_append_only_live_prefix_and_checks_current_clock(tmp_path,monkeypatch,fault,backend):
    from pathlib import Path
    from guard.collectors.lifecycle import prepare_assets
    from guard.executors import lifecycle_capture as capture
    from tests.test_lifecycle_sensor import CONTEXT
    prepared=prepare_assets(tmp_path/'assets',Path(__file__).resolve().parents[2],profile='vllm-native-lifecycle-metadata/23' if backend=='vllm' else 'sglang-native-lifecycle-metadata/1')
    context=dict(CONTEXT)
    rows=[dict(seq=0,event='START',monotonic_ns=1,previous_sha256='0'*64,payload=dict(
        schema=capture.profiles.SCHEMAS[prepared['profile']],profile=prepared['profile'],
        commit=capture.profiles.public_commit(prepared['profile']),session=prepared['session'],
        settings_sha256=prepared['assets']['settings.json']['sha256'],sensor_sha256=prepared['assets']['guard_lifecycle_sensor.py']['sha256'],context=context,producer_authenticated=False))]
    def checkpoint(rows,stamp):
        previous=hashlib.sha256(canonical(rows[-1])).hexdigest()
        return dict(seq=len(rows),event='CHECKPOINT',monotonic_ns=stamp,previous_sha256=previous,
            payload=dict(schema='guard-native-checkpoint-ack/1',session=prepared['session'],prefix_seq=len(rows),prefix_sha256=previous))
    rows.append(checkpoint(rows,100));raw=b''.join(canonical(r) for r in rows)
    later=raw+canonical(checkpoint(rows,200))
    initial={'owner':{'started_at':'fixed','image_id':'sha256:'+'1'*64},'file_identity':dict(device=1,inode=2,size=len(raw),mtime_ns=1,ctime_ns=1),'capture_sha256':hashlib.sha256(raw).hexdigest()}
    second=deepcopy(initial);second['file_identity'].update(size=len(later),mtime_ns=2,ctime_ns=2)
    if fault=='inode':second['file_identity']['inode']=3
    if fault=='owner':second['owner']['started_at']='new'
    if fault=='rewrite':later=b'X'+later[1:]
    if fault.startswith('later-'):
        after_rows=rows+[checkpoint(rows,200)]
        if fault=='later-partial':later+=b'{"seq":'
        else:
            payload={'reason':'BOUND_REACHED'} if fault=='later-bound' else {'phase':'OBSERVER_GAP'}
            extra=dict(event='END' if fault=='later-bound' else 'TERMINATION',seq=len(after_rows),monotonic_ns=201,
                previous_sha256=hashlib.sha256(canonical(after_rows[-1])).hexdigest(),payload=payload)
            later+=canonical(extra)
    reads=iter([(raw,initial),(later,second)])
    observed=100+20_000_000_000 if fault=='stale' else 99 if fault=='future' else 201
    def read_context(*args,**kwargs):
        if kwargs.get('with_clock'):
            if fault=='clock':raise ValueError('unqualified-clock')
            return context,observed
        return context
    monkeypatch.setattr(capture,'process_context',read_context)
    result=capture.capture(object(),{},source=dict(container='engine',image='repo@sha256:'+'1'*64,path=f"/guard-lifecycle/data/{prepared['session']}/pid-{context['pid']}-{context['start_ticks']}.jsonl"),prepared=prepared,read_file=lambda *a,**k:next(reads),image_check=lambda *a:True,checkpoint=True)
    assert result['process_context_verified'] is (fault=='none')
    assert result['records']==rows and result['checkpoint_prefix']['prefix_sha256']==hashlib.sha256(raw).hexdigest()
    assert not result['summary']['recording_closed']


def test_partial_final_write_cannot_reuse_previous_healthy_marker():
    p=live_packet();cap=p['recordings'][0]['capture']
    cap['summary'].update(incomplete_final_row=True,discarded_tail_bytes=3)
    assert prefix(cap['records'],cap['summary'],SESSION) is None
    assert not projected(p)['whole_scope_complete']


def test_stalled_current_monitor_preserves_previously_verified_request_checkpoint(monkeypatch):
    from datetime import datetime,timezone
    from serving_runtime.mvp import native_receipts
    from serving_runtime.mvp.collectors import CollectionFailure
    from serving_runtime.mvp.update_engine_link import project_termination
    from tests.test_runtime_termination_native23 import inputs,RID,UPSTREAM,replace
    records,original,binding=inputs();p=replace(live_packet(),EXT,hashlib.sha256(UPSTREAM.encode()).hexdigest())
    for side in ('before','after'):p[side]['owner']=deepcopy(original[side]['owner'])
    p['recordings'][0]['capture']['provenance']['owner']=deepcopy(original['before']['owner'])
    # Refresh content-addressed prefix receipt after replacing selected request.
    cap=p['recordings'][0]['capture'];cap['checkpoint_prefix']=prefix(cap['records'],cap['summary'],SESSION)[2]
    proof=project_termination(records,RID,p,1,['asgi_transport'],source_binding=binding,source_data_origin='EXAMPLE',checkpoint=True)
    assert proof['native_termination_verified'] and proof['checkpoint_coverage_verified']
    now=datetime.now(timezone.utc).isoformat()
    entry=dict(request_id=RID,projection=proof,capture_state='active',capture_observed_at=now,session_sealed=False,
        selected_external_request_sha256=hashlib.sha256(UPSTREAM.encode()).hexdigest(),
        projection_sha256=hashlib.sha256(native_receipts.canonical(proof)).hexdigest(),
        source_capture=dict(sha256='1'*64,bytes=100,target_sha256='2'*64,session=SESSION))
    entry['entry_sha256']=hashlib.sha256(native_receipts.canonical(entry)).hexdigest()
    document=dict(schema=native_receipts.SCHEMA,producer_source_sha256='3'*64,generated_at=now,state='ok',
        producer_authenticated=False,source_data_origin='EXAMPLE',capture_state='active',capture_observed_at=now,
        source_event_times=[],entries=[entry])
    monkeypatch.setattr(native_receipts,'read_host_feed',lambda *a:deepcopy(document))
    feed=native_receipts.NativeReceiptFeed('/owned/feed.json',reader_gid=10010,producer_source_sha256='3'*64)
    feed.collect();assert not feed.lookup(records,RID,'runtime-session').get('historical_receipt')
    document.update(capture_state='unavailable',capture_health_reason='checkpoint_stalled',entries=[])
    with pytest.raises(CollectionFailure) as error:feed.collect()
    assert error.value.last_value['capture_state']=='unavailable' and error.value.last_value['capture_health_reason']=='checkpoint_stalled' and error.value.last_value['future_observation_available'] is False
    historical=feed.lookup(records,RID,'runtime-session')
    assert historical['historical_receipt'] and historical['projection']==proof
    assert feed.lookup(records,RID,'later-runtime-session')['historical_receipt']


def sglang_live_packet():
    from guard.adapters import sglang_request_termination as sg
    from tests.test_sglang_request_termination import packet as sg_packet, G
    p=sg_packet();p.update(session=SESSION,capture_mode='live_checkpoint',python_processes_without_recording=[])
    directory=dict(device=1,inode=1,uid=2000,mode=0o700,mtime_ns=1)
    for side in ('before','after'):
        p[side]=dict(owner={'id':'sg-engine'},owner_stable=True,metadata=dict(schema='guard-lifecycle-session-inventory/1',
            session=SESSION,directory_before=directory,directory_after=dict(directory),directory_stable=True,sealed=False,
            unknown_entries=0,files_truncated=False,processes_truncated=False,unreadable_pids=[],files=[],processes=[]))
    for item in p['recordings']:
        cap=item['capture'];rows=cap['records'][:-1];context=rows[0]['payload']['context'];context['pid_namespace']=9
        rows[0]['payload']['session']=SESSION
        previous='0'*64
        for seq,row in enumerate(rows):
            row.update(seq=seq,previous_sha256=previous);previous=hashlib.sha256(canonical(row)).hexdigest()
        rows.append(dict(event='CHECKPOINT',seq=len(rows),previous_sha256=previous,monotonic_ns=2_000_000_000,
            payload=dict(schema='guard-native-checkpoint-ack/1',session=SESSION,prefix_seq=len(rows),prefix_sha256=previous)))
        cap['summary'].update(recording_closed=False,recording_end=None)
        cap['records']=rows;cap['checkpoint_prefix']=prefix(rows,cap['summary'],SESSION)[2]
        for side in ('before','after'):
            p[side]['metadata']['files'].append(dict(pid=context['pid'],start_ticks=context['start_ticks'],bytes=1,
                uid=2000,mode=0o600,device=1,inode=context['pid'],mtime_ns=1,ctime_ns=1,regular=True))
            p[side]['metadata']['processes'].append(dict(context,parent_pid=1,same_executable=True))
    return p


def test_sglang_live_checkpoint_uses_original_six_scope_predicates():
    from guard.adapters import sglang_request_termination as sg
    from tests.test_sglang_request_termination import G
    p=sglang_live_packet()
    assert not sg.project_native(p,G,1000,sg.SCOPES)['native_termination_verified']
    result=sg.project_checkpoint(p,G,1000,sg.SCOPES)
    assert result['native_termination_verified'] and result['checkpoint_coverage_verified']
    assert result['security']=='UNKNOWN' and not result['producer_authenticated']
    p['recordings'][1]['capture']['records'][-1]['monotonic_ns']=200_000_000
    assert not sg.project_checkpoint(p,G,1000,sg.SCOPES)['native_termination_verified']


def test_post_marker_positive_retained_state_is_preserved_in_projection():
    p=live_packet();cap=p['recordings'][0]['capture'];rows=cap['records']
    _,term=packet();stamp=rows[-1]['monotonic_ns']+1
    from tests.test_request_termination_projection23 import NATIVE
    value=term('GPUModelRunner.finish_requests','STATE_SNAPSHOT',call='7'*64)
    value.update(operation_started_ns=stamp,operation_finished_ns=stamp,
        states=[dict(request_sha256=NATIVE,worker_slot_present=True,worker_mapping_complete=True)])
    row=dict(event='TERMINATION',payload=value,seq=rows[-1]['seq']+1,monotonic_ns=stamp,
        previous_sha256=hashlib.sha256(canonical(rows[-1])).hexdigest())
    cap['checkpoint_tail']=[row]
    cap['checkpoint_tail_sha256']=hashlib.sha256(b''.join(canonical(r) for r in rows+[row])).hexdigest()
    result=projected(p)
    assert not result['whole_scope_complete'] and not result['worker_gpu_release_verified']
    assert any(v['scope']=='worker_batch' for v in result['activation_violations'])


@pytest.mark.parametrize('scope,point',[('frontend_request','TokenizerManager._handle_batch_output'),('scheduler_state','Scheduler.on_idle'),('worker_batch','ScheduleBatch.filter_batch'),('kv_references','common.release_kv_cache')])
def test_sglang_post_marker_retained_state_prevents_live_closure(scope,point):
    from guard.adapters import sglang_request_termination as sg
    from tests.test_sglang_request_termination import G
    p=sglang_live_packet()
    item=next(item for item in p['recordings'] if any(r['payload'].get('point')==point for r in item['capture']['records']))
    cap=item['capture'];rows=cap['records'];row=deepcopy(next(r for r in rows if r['payload'].get('point')==point))
    row['monotonic_ns']=rows[-1]['monotonic_ns']+1;row['seq']=rows[-1]['seq']+1
    row['previous_sha256']=hashlib.sha256(canonical(rows[-1])).hexdigest()
    if scope=='kv_references':row['payload']['requests'][0].update(kv_registered=True,kv_allocated=1)
    else:row['payload']['requests'][0]['state_present']=True
    cap['checkpoint_tail']=[row];cap['checkpoint_tail_sha256']=hashlib.sha256(b''.join(canonical(r) for r in rows+[row])).hexdigest()
    result=sg.project_checkpoint(p,G,1000,sg.SCOPES)
    assert not result['native_termination_verified']
    assert not next(r for r in result['scope_status'] if r['scope']==scope)['termination_verified']


def refresh_live_rows(p):
    for item in p['recordings']:
        cap=item['capture'];previous='0'*64
        for seq,row in enumerate(cap['records']):
            row.update(seq=seq,previous_sha256=previous)
            if row['event']=='CHECKPOINT':row['payload'].update(prefix_seq=seq,prefix_sha256=previous)
            previous=hashlib.sha256(canonical(row)).hexdigest()
        cap['checkpoint_prefix']=prefix(cap['records'],cap['summary'],SESSION)[2]


def test_exact_request_index_can_be_complete_when_old_global_index_is_bounded():
    p=live_packet();p['request_index']['stored_record_index_complete']=False;original=deepcopy(p)
    r=projected(p)
    assert r['whole_scope_complete'] and r['request_window_index_complete']
    assert r['source_stored_record_index_complete'] is False
    assert r['request_window']['unique_assignment_verified'] and p==original
    assert not project_native(p,EXT,1,['asgi_transport'])['whole_scope_complete']


@pytest.mark.parametrize('damage',['missing_assignment','duplicate_selection','alias_before_window','target_budget','contrary_tail'])
def test_scoped_request_index_does_not_hide_missing_alias_bound_or_negative_tail(damage):
    p=live_packet();p['request_index']['stored_record_index_complete']=False
    cap=p['recordings'][0]['capture'];rows=cap['records']
    assignment=next(r for r in rows if r['event']=='NATIVE' and r['payload']['point']=='InputProcessor.assign_request_id')
    selection=next(r for r in rows if r['event']=='SELECTION')
    if damage=='missing_assignment':rows.remove(assignment)
    elif damage=='duplicate_selection':rows.insert(rows.index(selection),deepcopy(selection))
    elif damage=='alias_before_window':
        alias=deepcopy(assignment);alias['payload']['metadata']['asgi_request_sha256']='0'*64
        alias['monotonic_ns']=assignment['monotonic_ns'];rows.insert(rows.index(assignment),alias)
    elif damage=='target_budget':
        repeat=deepcopy(next(r for r in rows if r['event']=='NATIVE' and r['payload']['point']=='OutputProcessor.abort_requests'))
        rows[-1:-1]=[deepcopy(repeat)for _ in range(4096)]
    else:
        contrary=deepcopy(next(r for r in rows if r['event']=='NATIVE' and r['payload']['point']=='OutputProcessor.abort_requests'))
        contrary['payload']['requests'][0]['output_state_present']=True
        contrary['monotonic_ns']=rows[-1]['monotonic_ns']+1
        cap['checkpoint_tail']=[contrary]
    refresh_live_rows(p)
    if damage=='contrary_tail':
        contrary.update(seq=rows[-1]['seq']+1,previous_sha256=hashlib.sha256(canonical(rows[-1])).hexdigest())
        cap['checkpoint_tail_sha256']=hashlib.sha256(b''.join(canonical(r)for r in rows+[contrary])).hexdigest()
    r=projected(p)
    assert not r['whole_scope_complete']
    if damage=='alias_before_window':assert not r['request_window_index_complete'] and not r['request_window']['unique_assignment_verified']
