"""Monitor health preserves historical evidence and requires current census coverage."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import pytest
from guard.executors.lifecycle_checkpoint import canonical
from tests.test_live_termination_checkpoint import live_packet,SESSION
from tests.test_lifecycle_rolling import rolling_packet,chained

spec=importlib.util.spec_from_file_location('native_health_tool',Path(__file__).parents[2]/'runtime/tools/update_engine_link.py')
tool=importlib.util.module_from_spec(spec);spec.loader.exec_module(tool)


def historical(p):
    item=deepcopy(next(i for i in p['recordings'] if i.get('segment') is None))
    cap=item['capture'];header=deepcopy(cap['records'][0]);ctx=header['payload']['context'];ctx.update(pid=999,start_ticks=123)
    header['monotonic_ns']=1
    marker=dict(event='CHECKPOINT',monotonic_ns=2,payload=dict(schema='guard-native-checkpoint-ack/1',session=SESSION,prefix_seq=1,prefix_sha256=hashlib.sha256(canonical(dict(header,seq=0,previous_sha256='0'*64))).hexdigest()))
    rows=[header,marker,dict(event='END',monotonic_ns=3,payload={'reason':'PROCESS_EXIT'})]
    cap['records']=rows;cap['summary'].update(recording_closed=True,recording_end='PROCESS_EXIT',incomplete_final_row=False,discarded_tail_bytes=0)
    cap['process_context_verified']=False;cap['image_digest_verified']=True
    cap['process_recheck']=dict(pid=999,start_ticks=123,recording_end='PROCESS_EXIT',recording_closed=True,result='process_exited')
    cap['provenance']['owner']=deepcopy(p['before']['owner'])
    cap['provenance']['capture_sha256']=hashlib.sha256(chained(rows)).hexdigest()
    p['recordings'].append(item)
    for side in ('before','after'):
        f=deepcopy(p[side]['metadata']['files'][-1]);f.update(pid=999,start_ticks=123,inode=99)
        p[side]['metadata']['files'].append(f)
    return item


@pytest.mark.parametrize('factory',[live_packet,rolling_packet])
def test_idle_exited_helper_does_not_stop_current_verified_monitor(factory):
    p=factory();assert tool._capture_health(p)==('active',None)
    historical(p);before=deepcopy(p)
    from guard.adapters.vllm_termination_projection import project_checkpoint
    from tests.test_request_termination_projection23 import EXT
    adjudication=project_checkpoint(p,EXT,1,['asgi_transport'])
    assert tool._capture_health(p)==('active',None)
    assert project_checkpoint(p,EXT,1,['asgi_transport'])==adjudication
    assert p==before


@pytest.mark.parametrize('fault',['request','negative','gap','unverified-module','recheck','owner','image','hash','chain','clock','late','header','partial','bound','current-physical','current-identity','current-stale','census'])
def test_historical_exclusion_never_skips_incomplete_or_request_evidence(fault):
    p=rolling_packet();old=historical(p);cap=old['capture'];rows=cap['records'];current=p['recordings'][1]
    if fault in {'request','negative','gap','unverified-module'}:
        rows[1].update(event={'request':'NATIVE','negative':'TERMINATION','gap':'OBSERVER_GAP','unverified-module':'MODULE'}[fault],payload={'verified':False,'phase':'FAIL'})
        cap['provenance']['capture_sha256']=hashlib.sha256(chained(rows)).hexdigest()
    elif fault=='recheck':cap['process_recheck']['start_ticks']+=1
    elif fault=='owner':old['session_owner_match']=False
    elif fault=='image':cap['image_digest_verified']=False
    elif fault=='hash':cap['provenance']['capture_sha256']='0'*64
    elif fault=='chain':rows[-1]['previous_sha256']='0'*64
    elif fault=='clock':rows[0]['payload']['context']['time_namespace']='other'
    elif fault=='late':rows[-1]['monotonic_ns']=current['capture']['records'][-1]['monotonic_ns']
    elif fault=='header':rows[0]['payload']['settings_sha256']='a'*64
    elif fault=='partial':cap['summary']['incomplete_final_row']=True
    elif fault=='bound':cap['summary']['recording_end']='BOUND_REACHED'
    elif fault=='current-physical':current['capture']['process_context_verified']=False
    elif fault=='current-identity':current['listed_file_identity_match']=False
    elif fault=='current-stale':
        current['capture']['process_context_verified']=False
        current['capture']['diagnostic_codes']=['LIFECYCLE_CHECKPOINT_STALE_OR_UNAVAILABLE']
    else:p['python_processes_without_recording']=[{'pid':123,'start_ticks':3}]
    before=deepcopy(p)
    assert tool._capture_health(p)[0]!='active'
    if fault=='bound':assert tool._capture_health(p)[0]=='bound_reached'
    assert p==before


def test_sealed_and_bound_monitor_states_preserved():
    p=live_packet()
    for side in ('before','after'):p[side]['metadata']['sealed']=True
    assert tool._capture_health(p)==('closed',None)
    p['recordings'][0]['capture']['summary']['recording_end']='BOUND_REACHED'
    assert tool._capture_health(p)==('bound_reached',None)
