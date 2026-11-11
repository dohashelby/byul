"""Bounded production-reader fork isolation; no subprocesses, engine or GPU."""
from copy import deepcopy
import threading
from types import SimpleNamespace

import pytest

from guard.executors import lifecycle_session as session
from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.runtime.bounded import capture_pipes


def setup(monkeypatch, *, mode=None, injected=False, count=5):
    reader=object.__new__(KubernetesLifecycleReader)
    reader.injected=injected;reader.runner=capture_pipes;reader.rows=[];reader.gap=False
    reader._parent_cleanup_failures=0;reader.node_image_receipts=[];reader.node_image_proof_failures=0
    reader.deadline=12345.0;reader.binding={'EXAMPLE':'immutable fixture'}
    owner={'container':'EXAMPLE'};files=[]
    for pid in range(1,count+1):
        files.append({'pid':pid,'start_ticks':10,'bytes':20,'uid':2000,'mode':0o600,'regular':True,
                      'device':1,'inode':pid,'mtime_ns':2,'ctime_ns':3})
    metadata={'files':files,'directory_before':{'uid':2000},'directory_stable':True,'unknown_entries':0,
              'files_truncated':False,'processes_truncated':False,'unreadable_pids':[],
              'processes':[{'pid':f['pid'],'start_ticks':10,'same_executable':True}for f in files]}
    inventory_calls=[];active=0;peak=0;lock=threading.Lock();barrier=threading.Barrier(min(4,count));fourth_done=threading.Event();completion=[]
    def inventory(actual,*args,**kwargs):
        assert actual is reader
        with lock:assert active==0
        inventory_calls.append(True);value=deepcopy(metadata)
        if mode=='census-changed'and len(inventory_calls)==2:value['unknown_entries']=1
        return {'metadata':value,'owner':owner,'owner_stable':True}
    def capture(actual,target,*,source,prepared,**kwargs):
        nonlocal active,peak
        pid=int(source['path'].split('/pid-')[1].split('-')[0])
        assert actual.deadline==reader.deadline and actual.binding is reader.binding
        assert kwargs['read_file'].__self__ is actual and kwargs['image_check'].__self__ is actual
        with lock:active+=1;peak=max(peak,active)
        if not injected and pid<=min(4,count):
            barrier.wait(timeout=5)
            if pid==1:assert fourth_done.wait(timeout=5)
        actual.rows.append({'program':'context','pid':pid,'diagnostic_codes':[]})
        actual._parent_cleanup_failures+=1
        actual.node_image_receipts.append({'pid':pid})
        if mode=='missing-context'and pid==2:
            actual.mark_collection_gap(pid,'EXAMPLE_CONTEXT_MUTATION')
            actual.node_image_proof_failures+=1;actual.node_image_failure_codes.add('EXAMPLE_IMAGE_MUTATION')
        if mode=='foreign-pid'and pid==2:
            # A sibling's context record must never satisfy a missing PID check.
            with pytest.raises(ValueError):actual.mark_collection_gap(1,'FOREIGN')
        file=files[pid-1];identity={k:file['bytes'if k=='size'else k]for k in ('device','inode','size','mtime_ns','ctime_ns')}
        if mode=='file-replaced'and pid==2:identity['inode']=99
        record={'capture':True,'records':[{'event':'START','seq':0,'payload':{'session':'a'*32,'profile':'EXAMPLE','context':{
            'boot_sha256':'a','pid_namespace':1,'time_namespace':2,'time_offsets_sha256':'b'}}}],
            'provenance':{'owner':({'container':'FOREIGN'}if mode=='owner-changed'and pid==2 else owner),
                          'file_identity':identity}}
        with lock:active-=1;completion.append(pid)
        if pid==4:fourth_done.set()
        return record
    monkeypatch.setattr(session,'inventory',inventory);monkeypatch.setattr(session,'capture',capture)
    def collect():return session.capture_session(reader,{},container='engine',image='EXAMPLE',prepared={'session':'a'*32},
        read_file=reader.read_file,image_check=reader.image_check)
    get_peak=lambda:peak
    get_peak.completion=completion
    return reader,collect,inventory_calls,get_peak


def test_parallel_bounded_four_and_deterministic_results_and_telemetry(monkeypatch):
    reader,collect,census,peak=setup(monkeypatch)
    value=collect()
    assert peak()==4 and len(census)==2
    assert peak.completion.index(4)<peak.completion.index(1)
    assert [r['pid']for r in value['recordings']]==[1,2,3,4,5]
    assert [r['pid']for r in reader.rows]==[1,2,3,4,5]
    assert reader._parent_cleanup_failures==5
    assert reader.node_image_receipts==[{'pid':p}for p in range(1,6)]
    assert value['recorded_files_covered']and value['live_census_stable']


@pytest.mark.parametrize('mode',['owner-changed','file-replaced','census-changed','missing-context','foreign-pid'])
def test_parallel_foreign_owner_file_census_and_context_failures_not_lost(monkeypatch,mode):
    reader,collect,census,peak=setup(monkeypatch,mode=mode)
    value=collect();assert peak()==4 and len(census)==2
    if mode in ['owner-changed','file-replaced','census-changed']:assert not value['recorded_files_covered']
    if mode=='owner-changed':assert not value['recordings'][1]['session_owner_match']
    if mode=='file-replaced':assert not value['recordings'][1]['listed_file_identity_match']
    if mode=='census-changed':assert not value['live_census_stable']
    if mode=='missing-context':
        assert reader.gap and reader.node_image_proof_failures==1
        assert reader.node_image_failure_codes=={'EXAMPLE_IMAGE_MUTATION'}
        assert reader.rows[1]['diagnostic_codes']==['EXAMPLE_CONTEXT_MUTATION']
    assert reader._parent_cleanup_failures==5


def test_injected_runner_keeps_serial_contract(monkeypatch):
    reader,collect,census,peak=setup(monkeypatch,injected=True)
    value=collect();assert peak()==1 and len(census)==2
    assert value['recorded_files_covered']
