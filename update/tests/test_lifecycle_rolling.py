"""Bounded shard chains preserve original bytes, live deadlines and failure ceilings."""
from copy import deepcopy
import hashlib
import json
import pytest
from guard.executors.lifecycle_checkpoint import canonical,prefix,covered,recording_covered
from guard.executors.lifecycle_rolling import active_for,inventory_reader,parse_inventory
from guard.executors.lifecycle_session import INVENTORY_READER
from guard.adapters.vllm_termination_projection import project_checkpoint,project_native
from tests.test_live_termination_checkpoint import live_packet,SESSION
from tests.test_request_termination_projection23 import EXT


def chained(rows):
    previous='0'*64
    for seq,row in enumerate(rows):
        row.update(seq=seq,previous_sha256=previous);previous=hashlib.sha256(canonical(row)).hexdigest()
    return b''.join(canonical(r) for r in rows)


def rolling_packet():
    p=live_packet();p['capture_mode']='live_checkpoint_rolling'
    original=p['recordings'][0];rows=original['capture']['records']
    split=next(i for i,r in enumerate(rows) if r['payload'].get('point')=='KVCacheManager.free')
    old=deepcopy(original);old['segment']=0
    oldrows=deepcopy(rows[:split]);endstamp=oldrows[-1]['monotonic_ns']
    oldrows.append(dict(event='END',payload={'reason':'SEGMENT_ROLLOVER'},monotonic_ns=endstamp))
    oldraw=chained(oldrows);old['capture']['records']=oldrows
    old['capture']['summary'].update(recording_closed=True,recording_end='SEGMENT_ROLLOVER')
    old['capture'].pop('checkpoint_prefix',None)
    old['capture']['provenance']['capture_sha256']=hashlib.sha256(oldraw).hexdigest()
    active=deepcopy(original);active['segment']=None
    header=deepcopy(rows[0]);header['monotonic_ns']=endstamp
    link=dict(event='SEGMENT',monotonic_ns=endstamp,payload=dict(schema='guard-native-segment/1',session=SESSION,
        segment=1,previous_segment=0,previous_sha256=hashlib.sha256(oldraw).hexdigest(),previous_bytes=len(oldraw)))
    current=[header,link,*deepcopy(rows[split:-1])]
    chained(current)
    marker=deepcopy(rows[-1]);marker['payload'].update(prefix_seq=len(current),prefix_sha256=hashlib.sha256(canonical(current[-1])).hexdigest())
    current.append(marker);chained(current)
    active['capture']['records']=current;active['capture']['checkpoint_prefix']=prefix(current,active['capture']['summary'],SESSION)[2]
    p['recordings']=[old,active]
    for side in ('before','after'):
        meta=p[side]['metadata'];meta['schema']='guard-lifecycle-rolling-inventory/1'
        base=meta['files'][0];meta['files']=[dict(base,segment=0),dict(base,segment=None,inode=3)]
    return p


def test_exact_contiguous_rolling_shards_prove_current_request_without_sealing():
    p=rolling_packet();before=deepcopy(p)
    assert covered(p)
    assert active_for(p['recordings'][0],p) is p['recordings'][1]
    assert recording_covered(p['recordings'][0],SESSION,1_001_000_010,packet=p)
    result=project_checkpoint(p,EXT,1,['asgi_transport'])
    assert result['whole_scope_complete'] and result['checkpoint_coverage_verified']
    assert not project_native(p,EXT,1,['asgi_transport'])['whole_scope_complete']
    assert p==before


@pytest.mark.parametrize('fault',['hash','bytes','segment','duplicate','missing-active','missing-old','wrong-header','old-end','marker','new-process'])
def test_partial_or_changed_rolling_chain_never_proves_closure(fault):
    p=rolling_packet();old,active=p['recordings'];link=active['capture']['records'][1]['payload']
    if fault=='hash':link['previous_sha256']='0'*64
    elif fault=='bytes':link['previous_bytes']+=1
    elif fault=='segment':old['segment']=5
    elif fault=='duplicate':p['recordings'].append(deepcopy(old))
    elif fault=='missing-active':p['recordings'].pop()
    elif fault=='missing-old':p['recordings'].pop(0)
    elif fault=='wrong-header':active['capture']['records'][0]['payload']['settings_sha256']='0'*64
    elif fault=='old-end':old['capture']['records'][-1]['payload']['reason']='BOUND_REACHED'
    elif fault=='marker':active['capture']['records'].pop()
    else:p['after']['metadata']['processes'][0]['start_ticks']+=1
    assert not project_checkpoint(p,EXT,1,['asgi_transport'])['whole_scope_complete']


def test_rolling_inventory_is_separate_and_retains_duplicate_process_shard_identity():
    p=rolling_packet();raw=json.dumps(p['before']['metadata']).encode()
    value=parse_inventory(raw,SESSION)
    assert {r['segment'] for r in value['files']}=={None,0}
    program=inventory_reader(INVENTORY_READER)
    compile(program,'rolling-inventory','exec')
    assert 'guard-lifecycle-rolling-inventory/1' in program and 'segment=int(match[3])' in program
    assert 'len(files)>=96' in program and 'seen>128' in program
    duplicate=deepcopy(value);duplicate['files'].append(deepcopy(value['files'][0]))
    with pytest.raises(ValueError):parse_inventory(json.dumps(duplicate).encode(),SESSION)


def test_future_request_window_survives_more_than_global_event_bound_of_old_history():
    p=live_packet();p['capture_mode']='live_checkpoint_rolling';template=p['recordings'][0]
    original=deepcopy(template['capture']['records']);header=deepcopy(original[0]);base=header['monotonic_ns']
    noise=deepcopy(next(r for r in original if r['event']=='NATIVE'))
    noise['payload']['metadata']['asgi_request_sha256']='0'*64
    noise['payload']['metadata']['external_request_sha256']='0'*64
    noise['payload']['requests'][0]['request_sha256']='0'*64
    items=[];previous_raw=None
    for generation in range(3):
        item=deepcopy(template);item['segment']=generation if generation<2 else None
        first=deepcopy(header);first['monotonic_ns']=base-10_000+generation*3_000
        rows=[first]
        if generation:
            rows.append(dict(event='SEGMENT',monotonic_ns=first['monotonic_ns'],payload=dict(
                schema='guard-native-segment/1',session=SESSION,segment=generation,previous_segment=generation-1,
                previous_sha256=hashlib.sha256(previous_raw).hexdigest(),previous_bytes=len(previous_raw))))
        if generation<2:
            for number in range(2040):
                row=deepcopy(noise);row['monotonic_ns']=first['monotonic_ns']+number+1;rows.append(row)
            rows.append(dict(event='END',payload={'reason':'SEGMENT_ROLLOVER'},monotonic_ns=rows[-1]['monotonic_ns']))
            item['capture']['summary'].update(recording_closed=True,recording_end='SEGMENT_ROLLOVER')
            item['capture'].pop('checkpoint_prefix',None)
        else:
            rows+=deepcopy(original[1:-1]);chained(rows)
            marker=deepcopy(original[-1]);marker['payload'].update(prefix_seq=len(rows),prefix_sha256=hashlib.sha256(canonical(rows[-1])).hexdigest());rows.append(marker)
        raw=chained(rows);item['capture']['records']=rows;item['capture']['provenance']['capture_sha256']=hashlib.sha256(raw).hexdigest()
        if generation==2:item['capture']['checkpoint_prefix']=prefix(rows,item['capture']['summary'],SESSION)[2]
        previous_raw=raw;items.append(item)
    p['recordings']=items
    for side in ('before','after'):
        meta=p[side]['metadata'];meta['schema']='guard-lifecycle-rolling-inventory/1';file=meta['files'][0]
        meta['files']=[dict(file,segment=value,inode=index+2) for index,value in enumerate((0,1,None))]
    assert sum(len(r['capture']['records']) for r in items)>4096
    result=project_checkpoint(p,EXT,1,['asgi_transport'])
    assert result['whole_scope_complete'] and result['rolling_segments_verified']


def test_actual_kubernetes_reader_dispatches_fixed_rolling_source_and_archive_paths(monkeypatch):
    from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError,supervised_source,ROLLING_INVENTORY_READER
    from guard.executors.owned_capture import _FILE_READER,MAX_BYTES
    # Exercise the actual constrained dispatcher and owner-recheck path without
    # a Kubernetes transport; it must select the registered supervised source.
    reader=KubernetesLifecycleReader.__new__(KubernetesLifecycleReader)
    binding={'engine':dict(container='engine',namespace='serving',pod='engine-pod')}
    reader._initialize(binding,{},dict(session=SESSION),binary='kubectl',duration=30,runner=lambda *a,**k:None)
    owner={'id':'fixed-owner'};calls=[]
    monkeypatch.setattr(reader,'_owner',lambda *a:owner)
    def request(argv,**kwargs):
        calls.append((argv,kwargs))
        return b'{}'
    monkeypatch.setattr(reader,'_request',request)
    result=reader.exec_bounded('engine',['python3','-c',ROLLING_INVENTORY_READER,SESSION],timeout=5,max_bytes=131072)
    assert result.owner_binding_verified and calls[-1][1]['cap']==131072
    assert supervised_source(ROLLING_INVENTORY_READER) in calls[-1][0]
    path=f'/guard-lifecycle/data/{SESSION}/pid-42-100-segment-1.jsonl'
    result=reader.exec_bounded('engine',['python3','-c',_FILE_READER,path,str(MAX_BYTES)],timeout=5,max_bytes=2*MAX_BYTES)
    assert result.owner_binding_verified and path in calls[-1][0]
    for source,cap in ((ROLLING_INVENTORY_READER+'\n',131072),(ROLLING_INVENTORY_READER,65536)):
        with pytest.raises(KubernetesCaptureError):reader.exec_bounded('engine',['python3','-c',source,SESSION],timeout=5,max_bytes=cap)
    for bad in (path.replace(SESSION,'b'*32),path.replace('segment-1','segment--1'),path.replace('segment-1','segment-12345678901')):
        with pytest.raises(KubernetesCaptureError):reader.exec_bounded('engine',['python3','-c',_FILE_READER,bad,str(MAX_BYTES)],timeout=5,max_bytes=2*MAX_BYTES)
