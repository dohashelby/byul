"""Exact source boundaries over EXAMPLE globals and owned CPU pipes.

The Process.start dependency is an explicit inline EXAMPLE double. No process
launch/serving role/Torch/GPU is claimed. Physical stdlib spawn/fork feasibility
is recorded separately; positive cross-recording cases use declared EXAMPLE
identities only and never become MEASURED.
"""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import multiprocessing,os,sys,threading,time,types
import pytest
from guard.adapters import vllm_ready_endpoint_sensor as sensor,vllm_ready_endpoint_contract as contract
from guard.adapters import vllm_ready_endpoint_metadata as metadata
from guard.adapters import vllm_worker_generation_sensor as generation
from tests import test_a1_worker_generation_sensor as original
from tests import test_a1_initialization_sensor as parent_fixture
from tests.test_a1_ready_endpoint_contract import source_world

PRIVATE='EXAMPLE_READY_ENDPOINT_PRIVATE'


def make_world(tmp_path,patch,*,session='b'*32,mode='normal',path=None,scope_path=None,instrument=True):
    tmp_path.mkdir(parents=True,exist_ok=True)
    class Facade:
        def __getattr__(self,name):return getattr(generation,name)
        def configure(self,base,joint,prior):
            return sensor.configure(generation.configure(base,joint,prior),generation,contract)
    with patch.context() as local:
        local.setattr(original,'sensor',Facade())
        world=original.make_world(tmp_path,patch,session=session,mode=mode,path=path,scope_path=scope_path,instrument=False)
    cls=type(world.observer);cls.emit=sensor.EndpointObservation.emit
    extra=source_world(patch);utilities=extra.utilities;mp=world.mp
    patch.setitem(sys.modules,contract.MP,mp)
    parent=parent_fixture.make_world(session=session)
    for name,value in vars(parent.module).items():
        if name not in vars(mp):setattr(mp,name,value)
    mp.UnreadyWorkerProcHandle=extra.mp.UnreadyWorkerProcHandle
    mp.get_mp_context=utilities.get_mp_context
    raw=Path(mp.__file__).read_bytes();compiled=original.codes(compile(raw,mp.__file__,'exec',dont_inherit=True,optimize=0))
    make=types.FunctionType(compiled['WorkerProc.make_worker_process'],vars(mp),'make_worker_process');make.__defaults__=(None,)
    mp.WorkerProc.make_worker_process=staticmethod(make)
    mp.numa_utils=NS(configure_subprocess=lambda *a,**kw:nullcontext())
    mp.envs.VLLM_MQ_MAX_CHUNK_BYTES_MB=1
    created=[];world.created=created;world.original_starts=[]
    def process(*,target,kwargs,name,daemon):
        # A real stdlib Process object carries a declared PID claim; start is an
        # explicit inline dependency double. It must never count as a worker.
        proc=multiprocessing.get_context('spawn').Process(target=lambda:None,name=name,daemon=daemon)
        def start():
            world.original_starts.append(kwargs['rank'])
            proc._popen=NS(pid=43+kwargs['rank'])
            if mode=='eof':kwargs['ready_pipe'].close();return
            return target(**kwargs)
        proc.start=start;return proc
    def pipe(*,duplex):
        assert duplex is False
        ends=multiprocessing.Pipe(duplex=False)
        if mode=='send-failure' and len(created)%4==0:ends[0].close()
        created.extend(ends);return ends
    context=NS(Pipe=pipe,Process=process,Lock=threading.Lock,get_start_method=lambda:'spawn')
    utilities.os=NS(environ={'VLLM_WORKER_MULTIPROC_METHOD':'spawn'})
    utilities.envs=NS(VLLM_WORKER_MULTIPROC_METHOD='spawn')
    utilities.current_platform=NS(is_rocm=lambda:False)
    utilities.multiprocessing=NS(get_context=lambda method:context)
    # Keep exact source helper bodies; only dependency namespaces are EXAMPLE.
    mp.get_mp_context=utilities.get_mp_context
    world.source.worker.torch.accelerator.device_count=lambda:64
    def queues(owner,*args):
        owner.worker_response_mq=NS(export_handle=lambda:NS(local_reader_ranks=[],remote_subscribe_addr=None),wait_until_ready=lambda:None)
        owner.peer_response_handles=[];owner.rpc_broadcast_mq=None
    mp.WorkerProc._init_message_queues=queues
    world.executor=mp.MultiprocExecutor();world.executor.__dict__.update(parent.executor.__dict__)
    world.executor.vllm_config=world.config
    if instrument:
        for module in (world.wrapper,mp,world.source.worker):world.observer.instrument(module)
    world.invoke=lambda:world.executor._init_executor()
    old_close=world.close
    def close():
        try:old_close()
        finally:
            finalizer=vars(world.executor).get('_finalizer')
            if finalizer:finalizer.detach()
            for value in created:value.close()
    world.close=close
    return world


def records(world):
    values=[{'seq':0,'event':'START','monotonic_ns':0,'payload':{'profile':sensor.PROFILE,'schema':sensor.SCHEMA,
        'session':world.observer.config['session'],'context':world.observer.writer.context}}]
    values += [{**row,'seq':index+1} for index,row in enumerate(world.observer.recorded)]
    return values


def packet(world,*,bound=True):
    return {'recordings':[{'session_owner_match':True,'listed_file_identity_match':True,
        'capture':{'records':records(world),'process_context_verified':bound,'image_digest_verified':bound}}]}


def rebase_rows(rows,context,*,start_index=1):
    """Reindex only declared EXAMPLE rows; never qualifies a physical process."""
    old={row['seq']:row for row in rows}
    positions={seq:index+start_index for index,seq in enumerate(old)}
    process_sha=sensor.sha(sensor.canonical(context))
    for row in rows:
        value=row['payload']
        if 'process_sha256' in value:value['process_sha256']=process_sha
        if row['event']=='WORKER_STARTUP' and value.get('callback'):
            value['callback']['binding']['pid']=context['pid']
            value['callback']['binding']['process_start_sha256']=process_sha
    for row in rows:
        value=row['payload']
        if row['event']=='WORKER_STARTUP_LINK':
            target=old[value['weight_event_seq']];value['weight_event_seq']=positions[target['seq']]
            value['weight_payload_sha256']=sensor.sha(sensor.canonical(target['payload']))
        if row['event']=='WORKER_GENERATION_LINK':
            target=old[value['worker_event_seq']]
            for key in ('worker_event_seq','constructor_event_seq','wrapper_event_seq'):value[key]=positions[value[key]]
            value['worker_payload_sha256']=sensor.sha(sensor.canonical(target['payload']))
        if row['event']=='READY_ENDPOINT':
            for seq_key,hash_key in (('parent_init_seq','parent_init_payload_sha256'),('wait_seq','wait_payload_sha256'),('generation_seq','generation_payload_sha256')):
                if value[seq_key] is not None:
                    target=old[value[seq_key]];value[seq_key]=positions[target['seq']]
                    value[hash_key]=sensor.sha(sensor.canonical(target['payload']))
    return [{**row,'seq':index+start_index} for index,row in enumerate(rows)]


def split_example_packet(world):
    """Two logical EXAMPLE identities exercise the closed join, not real roles."""
    rows=records(world);common=[r for r in rows[1:] if r['event'] in {'MODULE','END','READY_ENDPOINT_CLOSE'}]
    parent=[];child=[]
    for row in rows[1:]:
        if row in common:continue
        value=row['payload']
        if row['event']=='INITIALIZATION' or row['event']=='READY_ENDPOINT' and value['operation']!='SEND':parent.append(row)
        elif row['event']=='READY_ENDPOINT_GAP' and value['point']!='WorkerProc.worker_main':parent.append(row)
        else:child.append(row)
    result={'recordings':[]}
    for pid,start,values in ((42,99,parent),(43,100,child)):
        context={**world.observer.writer.context,'pid':pid,'start_ticks':start}
        source=deepcopy(sorted(common+values,key=lambda r:r['seq']))
        rebased=rebase_rows(source,context)
        header={'seq':0,'event':'START','monotonic_ns':0,'payload':{'profile':sensor.PROFILE,'schema':sensor.SCHEMA,
                'session':world.observer.config['session'],'context':context}}
        result['recordings'].append({'session_owner_match':True,'listed_file_identity_match':True,
            'capture':{'records':[header,*rebased],'process_context_verified':True,'image_digest_verified':True}})
    return result


def example_pair(tmp_path,patch):
    world=make_world(tmp_path,patch);world.executor.world_size=world.executor.local_world_size=1
    world.executor._get_parallel_sizes=lambda:(1,1,1)
    try:
        try:world.invoke()
        except Exception:
            assert sys.platform!='linux'
    finally:world.close()
    return world,split_example_packet(world)


@pytest.mark.parametrize('mode',['normal','eof','constructor-failure','send-failure'])
def test_exact_creator_receiver_sender_original_behavior_and_cleanup(tmp_path,monkeypatch,mode):
    world=make_world(tmp_path,monkeypatch,mode=mode)
    try:
        if sys.platform!='linux' or mode in {'eof','constructor-failure','send-failure'}:
            with pytest.raises(Exception):world.invoke()
        else:world.invoke()
    finally:world.close()
    raw=records(world);result=metadata.project(packet(world),{'profile':sensor.PROFILE,'session':world.observer.config['session']})
    assert result['endpoint_events'] and all(result[key] is False for key in sensor.TRUTH)
    assert all(value.closed for value in world.created)
    assert all('recv' not in vars(value) for value in world.created)
    assert world.original_starts
    assert PRIVATE not in str(raw)
    # Both roles here execute in one EXAMPLE interpreter. PID claims alone
    # must not produce a cross-process positive candidate.
    assert not any(value['same_held_fifo_candidate'] for value in result['endpoint_candidates'])
    if sys.platform=='linux' and mode=='normal':
        assert {value['operation'] for value in result['endpoint_events']}=={'CREATE','RECV','SEND'}
        assert sum(value['operation']=='RECV' and value['phase']=='RETURN' for value in result['endpoint_events'])==2
    if sys.platform!='linux':assert 'ready_endpoint_unsupported-platform' in result['missing']


@pytest.mark.parametrize('failure',['event','snapshot','binding'])
def test_observer_failure_preserves_original_source_behavior(tmp_path,monkeypatch,failure):
    world=make_world(tmp_path,monkeypatch)
    def fail(*args,**kwargs):raise RuntimeError(PRIVATE)
    monkeypatch.setattr(world.observer,{'event':'endpoint_event','snapshot':'endpoint_snapshot','binding':'endpoint_binding'}[failure],fail)
    try:
        if sys.platform=='linux':world.invoke()
        else:
            with pytest.raises(AssertionError):world.invoke()
        assert world.original_starts
    finally:world.close()
    assert all(value.closed for value in world.created)
    assert all('recv' not in vars(value) for value in world.created)


def test_selected_session_close_does_not_close_pipes_or_other_observer(tmp_path,monkeypatch):
    first=make_world(tmp_path/'first',monkeypatch,instrument=False)
    second=make_world(tmp_path/'second',monkeypatch,session='c'*32,instrument=False)
    reader,writer=multiprocessing.Pipe(duplex=False)
    try:
        first.close()
        assert first.observer.endpoint_closed and not second.observer.endpoint_closed
        assert not reader.closed and not writer.closed
    finally:second.close();reader.close();writer.close()


@pytest.mark.parametrize('failure',['set','reset'])
def test_context_observer_failure_never_masks_original_startup(tmp_path,monkeypatch,failure):
    world=make_world(tmp_path,monkeypatch);original=sensor._INIT
    class BrokenContext:
        def get(self):return original.get()
        def set(self,value):
            if failure=='set':raise RuntimeError(PRIVATE)
            return original.set(value)
        def reset(self,token):
            original.reset(token)
            if failure=='reset':raise RuntimeError(PRIVATE)
    monkeypatch.setattr(sensor,'_INIT',BrokenContext())
    try:
        if sys.platform=='linux':world.invoke()
        else:
            with pytest.raises(AssertionError):world.invoke()
        assert world.original_starts
    finally:world.close()
    assert all(value.closed for value in world.created)


def test_close_and_call_bound_record_gaps_without_new_process_actions(tmp_path,monkeypatch):
    world=make_world(tmp_path,monkeypatch,instrument=False)
    try:
        world.observer.endpoint_calls=sensor.MAX_CALLS
        assert world.observer.endpoint_allow_call('WorkerProc.worker_main') is False
        assert world.original_starts==[]
    finally:world.close()
    assert any(row['event']=='READY_ENDPOINT_GAP' and row['payload']['reason']=='calls-bound' for row in world.observer.recorded)


@pytest.mark.parametrize('change',['reference','session','process','domain-clock','truth','raw-field'])
def test_typed_endpoint_rows_reject_substitution_or_privilege_promotion(tmp_path,monkeypatch,change):
    world=make_world(tmp_path,monkeypatch)
    try:
        try:world.invoke()
        except Exception:
            assert sys.platform!='linux'
    finally:world.close()
    rows=records(world);target=next(row for row in rows if row['event']=='READY_ENDPOINT')
    value=target['payload']
    if change=='reference':value['parent_init_payload_sha256']='0'*64
    elif change=='session':value['session_sha256']='0'*64
    elif change=='process':value['process_sha256']='0'*64
    elif change=='domain-clock':value['operation_finished_ns']=target['monotonic_ns']+1
    elif change=='truth':value['cross_process_lineage_verified']=True
    else:value['raw_fd']=123
    with pytest.raises(ValueError,match='READY_ENDPOINT_'):
        metadata.journal(rows,{'profile':sensor.PROFILE,'session':world.observer.config['session']},world.observer.writer.context)


@pytest.mark.parametrize('change',['normal','child-pid','namespace','time-domain','missing-send','duplicate-creator','unbound','different-endpoint'])
def test_cross_recording_example_join_requires_unique_endpoint_and_exact_context(tmp_path,monkeypatch,change):
    world,value=example_pair(tmp_path,monkeypatch)
    parent=value['recordings'][0]['capture'];child=value['recordings'][1]['capture']
    if change in {'child-pid','namespace','time-domain'}:
        key={'child-pid':'pid','namespace':'pid_namespace','time-domain':'time_namespace'}[change]
        child['records'][0]['payload']['context'][key]+=1
        child['records'][1:]=rebase_rows(child['records'][1:],child['records'][0]['payload']['context'])
    elif change=='missing-send':
        child['records']=[r for r in child['records'] if not (r['event']=='READY_ENDPOINT' and r['payload']['phase']=='RETURN')]
    elif change=='duplicate-creator':
        target=deepcopy(next(r for r in parent['records'] if r['event']=='READY_ENDPOINT' and r['payload']['operation']=='CREATE' and r['payload']['phase']=='CALL'))
        target['seq']=max(r['seq'] for r in parent['records'])+1;parent['records'].append(target)
    elif change=='unbound':child['process_context_verified']=False
    elif change=='different-endpoint':
        for row in child['records']:
            if row['event']=='READY_ENDPOINT':
                for key in ('endpoint_before_sha256','endpoint_after_sha256'):
                    if row['payload'][key] is not None:row['payload'][key]='f'*64
    result=metadata.project(value,{'profile':sensor.PROFILE,'session':world.observer.config['session']})
    assert all(result[key] is False for key in sensor.TRUTH)
    assert any(c['same_held_fifo_candidate'] for c in result['endpoint_candidates'])==(change=='normal' and sys.platform=='linux')
    assert result['generation_events'] and result['endpoint_events']
