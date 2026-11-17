"""Actual stdlib Future/SharedMemory frames; never a GPU qualification."""
from collections import deque
from concurrent.futures import Future, InvalidStateError
from contextlib import suppress
from copy import deepcopy
import dis
from enum import Enum
import gc
from hashlib import sha256
import json
import os
from multiprocessing.shared_memory import SharedMemory
import sys
import subprocess
import threading
import types
import weakref

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc
from guard.adapters import vllm_rank_ipc_identity as identity
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_rank_rpc_hooks as rpc
from guard.adapters import vllm_rank_response_trace as tracing
from guard.adapters import vllm_rank_response_runtime as trace_runtime
from guard.adapters import vllm_rank_response_capture as capture

RAW=b'''class Scheduled:
    pass
class Buffer:
    pass
class Queue:
    def dequeue(self, timeout=0.1):
        if self.error is not None:
            raise self.error
        return self.output
    def enqueue(self, obj):
        self.last = obj
class ReplyStatus(Enum):
    SUCCESS = 1
    FAILURE = 2
class WorkerRPC:
    def execute(self, scheduler_output):
        try:
            if self.error is not None:
                raise self.error
            self.enqueue_output(self.output)
        except Exception as error:
            self.enqueue_output(error)
    def enqueue_output(self, output):
        if type(output) in (NativeLabFault, TimeoutError, RuntimeError, ValueError, TypeError):
            self.response.enqueue((ReplyStatus.FAILURE, None))
        else:
            self.response.enqueue((ReplyStatus.SUCCESS, output))
class NativeFuture(Future):
    def __init__(self, queue, get_response):
        self.queue, self.get_response = queue, get_response
        super().__init__()
        queue.appendleft(self)
    def result(self, timeout=None):
        while not self.done():
            self.queue.pop().wait()
        return super().result(timeout)
    def wait(self):
        try:
            self.set_result(self.get_response())
        except Exception as error:
            self.set_exception(error)
class Executor:
    def execute(self, scheduler_output):
        def get_response():
            try:
                return self.response.dequeue()
            except TimeoutError as error:
                raise TimeoutError('PRIVATE_MESSAGE') from error
        return NativeFuture(deque(), get_response)
class ResponseExecutor:
    def execute(self, scheduler_output):
        def get_response():
            status, result = self.response.dequeue()
            if status is ReplyStatus.FAILURE:
                raise RuntimeError('PRIVATE_WORKER_ERROR')
            return result
        return NativeFuture(deque(), get_response)
'''


@pytest.fixture
def setup(tmp_path,monkeypatch):
    name='EXAMPLE_response_trace';path=tmp_path/'fixture.py';path.write_bytes(RAW)
    module=types.ModuleType(name);module.__file__=str(path)
    module.Future=Future;module.deque=deque;module.Enum=Enum;module.NativeLabFault=rpc.control_code.NativeLabFault
    exec(compile(RAW,str(path),'exec',dont_inherit=True,optimize=0),vars(module))
    monkeypatch.setitem(sys.modules,name,module)
    codes=sources._codes(compile(RAW,'fixture.py','exec',dont_inherit=True,optimize=0))
    common={'phase':'around-original','role':'EXAMPLE','source_modules':[name]}
    hooks={'Executor.execute':{**common,'kind':'rpc','requests':{'root':'scheduler_output','layout':'scheduled'},
        'deferred_result':{'class':'NativeFuture','point':'NativeFuture.result'}},
        'NativeFuture.result':{**common,'kind':'deferred-result'},
        'WorkerRPC.execute':{**common,'kind':'rpc','requests':{'root':'scheduler_output','layout':'scheduled'},
            'scalars':{'rpc_rank':{'root':'self','path':['rpc_rank']}}},
        'ResponseExecutor.execute':{**common,'kind':'rpc','requests':{'root':'scheduler_output','layout':'scheduled'},
            'deferred_result':{'class':'NativeFuture','point':'NativeFuture.result'}}}
    source={'path':'fixture.py','bytes':len(RAW),'sha256':sha256(RAW).hexdigest(),
        'functions':list(hooks),'hooks':hooks,
        'body_code_sha256':{p:sources.code_digest(codes[p])for p in hooks}}
    runtime=sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3]))
    old={'id':'EXAMPLE-response','image':'EXAMPLE-image','runtime':runtime,'sources':{},'rpc_sources':{name:source},
        'request_layouts':{'scheduled':{'kind':'positive-dict-keys','type':{'module':name,'name':'Scheduled'},
        'field':'num_scheduled_tokens','item_types':[]}}}
    old_path=tmp_path/'old.json';old_path.write_text(json.dumps({'schema':'guard-rank-ipc-source-candidates/1','profiles':[old]}))
    monkeypatch.setattr(sources,'PROFILES',old_path)
    points={'NativeFuture.result':{'kind':'root'},'NativeFuture.wait':{'kind':'response-wait','parent_kind':'root','future_owner_argument':'self'},
        'Executor.execute.<locals>.get_response':{'kind':'native-response','parent_kind':'response-wait'},
        'Queue.dequeue':{'kind':'reader','parent_kind':'native-response','queue_class':'Queue','buffer_class':'Buffer','timeout_argument':'timeout'},
        'WorkerRPC.execute':{'kind':'worker-root'},
        'WorkerRPC.enqueue_output':{'kind':'worker-output','parent_kind':'worker-root','worker_owner_argument':'self','output_argument':'output'},
        'ResponseExecutor.execute.<locals>.get_response':{'kind':'native-response','parent_kind':'response-wait'},
        'Queue.enqueue':{'kind':'worker-writer','parent_kind':'worker-output','queue_class':'Queue','buffer_class':'Buffer',
            'queue_role':'writer','status_tuple_argument':'obj','status_module':name,'status_type':['ReplyStatus'],
            'success_member':'SUCCESS','failure_member':'FAILURE'}}
    new_source={k:source[k]for k in('path','bytes','sha256')}
    new_source.update(points=points,body_code_sha256={p:sources.code_digest(codes[p])for p in points},
        return_offsets={p:[i.offset for i in dis.get_instructions(codes[p])if i.opname in {'RETURN_VALUE','RETURN_CONST'}]for p in points},
        instruction_lines={p:{str(i.offset):i.positions.lineno for i in dis.get_instructions(codes[p],show_caches=True)}for p in points})
    new_source['direct_raise_offsets']={p:[]for p in points}
    raised='ResponseExecutor.execute.<locals>.get_response'
    line,=[i for i,text in enumerate(RAW.splitlines(),1)if text.strip()==b"raise RuntimeError('PRIVATE_WORKER_ERROR')"]
    new_source['direct_raise_offsets'][raised]=[{'instruction_offset':i.offset,'source_line':line,'exception_kind':'runtime-error'}
        for i in dis.get_instructions(codes[raised])if i.opname=='RAISE_VARARGS'and i.positions.lineno==line]
    assert len(new_source['direct_raise_offsets'][raised])==1
    new_path=tmp_path/'new.json';new_path.write_text(json.dumps({'schema':'guard-rank-response-trace-profiles/1','profiles':[
        {'id':'EXAMPLE-response-trace','image':'EXAMPLE-image','runtime':runtime,'sources':{name:new_source}}]}))
    monkeypatch.setattr(tracing,'PROFILES',new_path)
    old_rows=[];new_rows=[]
    observer=rpc.RPCObserver(image='EXAMPLE-image',key=b'K'*32,emit=old_rows.append,
        identity_observer=ipc.IPCObserver(image='EXAMPLE-image',key=b'K'*32,emit=old_rows.append))
    observer.attach(module)
    tracer=tracing.ResponseTrace(observer=observer,key=b'K'*32,emit=new_rows.append)
    owner=module.Executor();owner.response=module.Queue();owner.response.error=None;owner.response.output=object()
    memory=SharedMemory(create=True,size=32)
    owner.response.buffer=module.Buffer();owner.response.buffer.shared_memory=memory
    owner.response._is_local_reader=True;owner.response._is_writer=False
    scheduled=module.Scheduled();scheduled.num_scheduled_tokens={'PRIVATE_REQUEST':1}
    try:
        yield module,observer,tracer,old_rows,new_rows,owner,scheduled,path,memory
    finally:
        if tracer.owner_thread is not None:tracer.close()
        memory.close();memory.unlink()


def test_source_reader_endpoint_and_original_future_request_are_bound_without_inspecting_return(setup):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    class Opaque:
        def __repr__(self):raise AssertionError('output repr forbidden')
        def __iter__(self):raise AssertionError('output iteration forbidden')
        @property
        def __dict__(self):raise AssertionError('output attrs forbidden')
    output=Opaque();owner.response.output=output
    future=owner.execute(scheduled)
    tracer.install()
    assert future.result()is output
    tracer.close()
    assert not tracer.disabled or tracer.diagnostic is None
    assert not tracer.frames and len(rows)==8
    assert {r['event']for r in rows}=={'CALL','RETURN'}
    assert len({r['operation_hmac_sha256']for r in rows})==1
    assert all(r['binding']['request_hmac_sha256']==old_rows[-1]['request_hmac_sha256']for r in rows)
    queue_rows=[r for r in rows if r['point']=='Queue.dequeue']
    assert all(r['reader_endpoint']['identity_hmac_sha256']==identity.shared_memory_endpoint(memory,key=b'K'*32)['identity_hmac_sha256']for r in queue_rows)
    assert b'PRIVATE_REQUEST'not in json.dumps(rows).encode()
    assert all(r[k]is False for r in rows for k in('producer_registered','fault_causality_verified','upper_delivery_verified','recovery_verified','whole_resources_released'))


@pytest.mark.parametrize('error',[TimeoutError('PRIVATE_MESSAGE'),RuntimeError('PRIVATE_MESSAGE'),ValueError('PRIVATE_MESSAGE')])
def test_original_exception_and_its_cause_flow_to_same_future_without_message_read(setup,error):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    owner.response.error=error;future=owner.execute(scheduled)
    tracer.install()
    with pytest.raises(type(error))as raised:future.result()
    tracer.close()
    assert tracer.diagnostic is None and tracer.frames=={}
    native=[r for r in rows if r['point']=='Executor.execute.<locals>.get_response'and r['event']=='UNWIND']
    root=[r for r in rows if r['point']=='NativeFuture.result'and r['event']=='UNWIND']
    assert len(native)==len(root)==1
    assert native[0]['exception']['object_hmac_sha256']==root[0]['exception']['object_hmac_sha256']
    if type(error)is TimeoutError:
        assert raised.value.__cause__ is error and raised.value is not error
        queue,=[r for r in rows if r['point']=='Queue.dequeue'and r['event']=='UNWIND']
        assert native[0]['exception']['cause_hmac_sha256']==queue['exception']['object_hmac_sha256']
    else:assert raised.value is error
    wait,=[r for r in rows if r['point']=='NativeFuture.wait'and r['event']in {'RETURN','UNWIND'}]
    assert wait['event']=='RETURN'  # Handled/set_exception is not function unwind.
    assert b'PRIVATE_MESSAGE'not in json.dumps(rows).encode()


def test_unbound_future_and_outside_reader_calls_do_not_inherit_another_request(setup):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=module.NativeFuture(deque(),lambda:123)
    tracer.install();assert future.result()==123 and owner.response.dequeue()is owner.response.output;tracer.close()
    assert rows==[] and tracer.diagnostic is None


@pytest.mark.parametrize('damage',['source','code','reader-role','queue-type','emitter','row-bound','process'])
def test_observer_failure_preserves_original_result_and_cannot_claim_complete_scope(setup,damage,monkeypatch):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=owner.execute(scheduled);expected=owner.response.output
    if damage=='source':path.write_bytes(RAW+b'\n# changed')
    elif damage=='code':
        exec('def changed(self):return self.output',vars(module));module.Queue.dequeue=module.changed
    elif damage=='reader-role':owner.response._is_local_reader=False
    elif damage=='queue-type':
        class Derived(module.Buffer):pass
        owner.response.buffer=Derived();owner.response.buffer.shared_memory=memory
    elif damage=='emitter':
        def broken(row):raise OSError('PRIVATE_DIAGNOSTIC')
        tracer.emit=broken
    elif damage=='row-bound':monkeypatch.setattr(tracing,'MAX_ROWS',2)
    else:tracer.pid-=1
    if damage=='process':
        with pytest.raises(ValueError):tracer.install()
        assert future.result()is expected and rows==[]
        return
    tracer.install();assert future.result()is expected;tracer.close()
    if damage=='code':
        # Selected source frames are absent, not invented. A downstream verifier
        # must require the complete four-point graph; mere root return is weaker.
        assert not any(r['point']=='Queue.dequeue'for r in rows)
    else:assert tracer.diagnostic=='RANK_RESPONSE_OBSERVATION_INCOMPLETE'
    assert all(not r['fault_causality_verified']for r in rows)
    assert tracer.frames=={}


@pytest.mark.parametrize('kind',['trace','thread-trace','profile','thread-profile'])
def test_existing_debugger_or_profiler_is_preserved(setup,kind):
    tracer=setup[2]
    getter,setter={'trace':(sys.gettrace,sys.settrace),'thread-trace':(threading.gettrace,threading.settrace),
        'profile':(sys.getprofile,sys.setprofile),'thread-profile':(threading.getprofile,threading.setprofile)}[kind]
    before=getter()
    def other(*args):return None
    setter(other)
    try:
        with pytest.raises(ValueError,match='TRACE_ALREADY_ACTIVE'):tracer.install()
        assert getter()is other
    finally:setter(before)


def test_no_frame_future_or_exception_is_retained_by_observer(setup):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=owner.execute(scheduled);reference=weakref.ref(future)
    tracer.install();future.result();tracer.close()
    del future;gc.collect()
    assert reference()is None and tracer.frames=={}


@pytest.mark.parametrize('timeout',[None,0,0.1,300,3600])
def test_actual_reader_budget_is_metadata_without_changing_engine_timeout(setup,timeout,monkeypatch):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    monkeypatch.setattr(module.Queue.dequeue,'__defaults__',(timeout,))
    future=owner.execute(scheduled);tracer.install();assert future.result()is owner.response.output;tracer.close()
    reader,= [row for row in rows if row['point']=='Queue.dequeue'and row['event']=='CALL']
    assert reader['reader_timeout_ns']==(None if timeout is None else int(timeout*1_000_000_000))
    assert tracer.diagnostic is None


@pytest.mark.parametrize('timeout',[True,-1,float('nan'),float('inf'),2**31+1,object()])
def test_unrepresentable_reader_budget_is_a_gap_and_never_changes_original_result(setup,timeout,monkeypatch):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    monkeypatch.setattr(module.Queue.dequeue,'__defaults__',(timeout,))
    future=owner.execute(scheduled);tracer.install();assert future.result()is owner.response.output;tracer.close()
    assert tracer.diagnostic=='RANK_RESPONSE_OBSERVATION_INCOMPLETE'
    assert not any(row['point']=='Queue.dequeue'for row in rows)


def test_older_future_drained_by_current_result_cannot_inherit_current_request(setup):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=owner.execute(scheduled)
    previous=module.NativeFuture(future.queue,lambda:None)
    # Drain the older request first, as the native Future queue can do.
    future.queue.clear();future.queue.extend([future,previous])
    tracer.install();assert future.result()is owner.response.output;tracer.close()
    assert previous.done()and tracer.diagnostic=='RANK_RESPONSE_OBSERVATION_INCOMPLETE'
    assert len(rows)==1 and rows[0]['event']=='CALL'and rows[0]['point']=='NativeFuture.result'


def test_new_thread_has_own_operation_and_no_foreign_trace_is_removed(setup):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=owner.execute(scheduled);output=[]
    tracer.install()
    worker=threading.Thread(target=lambda:output.append(future.result()))
    worker.start();worker.join(timeout=5)
    assert not worker.is_alive()and output==[owner.response.output]
    assert tracer.diagnostic is None and rows
    def other(*args):return None
    sys.settrace(other)
    try:
        tracer.close()
        assert sys.gettrace()is other and threading.gettrace()is None
    finally:sys.settrace(None)


def worker(fixture):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=fixture
    current=module.WorkerRPC();current.error=None;current.output=object();current.rpc_rank=0
    current.response=module.Queue();current.response._is_writer=True;current.response._is_local_reader=False
    current.response.buffer=module.Buffer();current.response.buffer.shared_memory=memory
    return current


@pytest.mark.parametrize('fault',[False,True])
def test_worker_original_exception_and_status_publication_have_separate_source_graph(sealed_capture,fault):
    fixture,prepared,sink,cfg=sealed_capture
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=fixture
    current=worker(fixture)
    class Opaque:
        def __repr__(self):raise AssertionError('model output repr forbidden')
        @property
        def __dict__(self):raise AssertionError('model output attrs forbidden')
    current.output=Opaque()
    if fault:current.error=rpc.control_code.NativeLabFault('PRIVATE_WORKER_ERROR')
    tracer.install();assert current.execute(scheduled)is None;tracer.close()
    assert tracer.diagnostic is None
    result=capture.verify_capture(prepared,sink);operation,=result['operations']
    assert operation['source_role']=='worker-response'and operation['worker_three_point_graph_complete']
    assert operation['worker_error_forwarded_as_failure_reply_observed']is fault
    assert not operation['source_four_point_graph_complete']and not operation['same_native_error_reached_future']
    written,=[r for r in result['rows']if r['point']=='Queue.enqueue'and r['event']=='RETURN']
    assert written['reader_endpoint']is None and written['writer_endpoint']['identity_hmac_sha256']==identity.shared_memory_endpoint(memory,key=b'K'*32)['identity_hmac_sha256']
    assert written['reply_status']==('failure'if fault else'success')
    assert current.response.last[0]is(module.ReplyStatus.FAILURE if fault else module.ReplyStatus.SUCCESS)
    if not fault:assert current.response.last[1]is current.output
    assert 'PRIVATE_WORKER_ERROR'not in json.dumps(result)and cfg['key']not in json.dumps(result)
    assert all(operation[k]is False for k in capture.FLAGS)


def test_worker_unbound_output_cannot_inherit_a_current_request(setup):
    current=worker(setup);tracer=setup[2];rows=setup[4]
    tracer.install();current.enqueue_output(current.output);tracer.close()
    assert rows==[]and tracer.diagnostic is None


@pytest.mark.parametrize('damage',['other-owner','reader-instead-of-writer','wrong-buffer','unknown-status'])
def test_worker_response_gaps_do_not_change_original_model_handling(setup,damage,monkeypatch):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    current=worker(setup)
    if damage=='other-owner':
        other=worker(setup);current.enqueue_output=other.enqueue_output
    elif damage=='reader-instead-of-writer':
        current.response._is_writer=False;current.response._is_local_reader=True
    elif damage=='wrong-buffer':current.response.buffer=object()
    else:
        class OtherStatus:SUCCESS=object();FAILURE=object()
        # Only the status lookup is replaced in the observer's candidate data.
        # The actual engine Enum and the original method remain unchanged.
        monkeypatch.setitem(vars(module),'EXAMPLE_OtherStatus',OtherStatus)
        tracer.profile['sources'][module.__name__]['points']['Queue.enqueue']['status_type']=['EXAMPLE_OtherStatus']
    tracer.install();assert current.execute(scheduled)is None;tracer.close()
    assert tracer.diagnostic=='RANK_RESPONSE_OBSERVATION_INCOMPLETE'
    assert tracer.frames=={}and all(not row['fault_causality_verified']for row in rows)


def test_official_candidate_pins_match_preserved_source_functions_and_return_instructions():
    root=__import__('pathlib').Path(__file__).parent/'fixtures/vllm-0.30.0'
    data=json.loads(tracing.PROFILES.read_bytes())
    assert len(data['profiles'])==1
    for name,row in data['profiles'][0]['sources'].items():
        raw=(root/(row['path']+'.txt')).read_bytes()
        assert len(raw)==row['bytes']and sha256(raw).hexdigest()==row['sha256']
        codes=sources._codes(compile(raw,row['path'],'exec',dont_inherit=True,optimize=0))
        for point in row['points']:
            assert sources.code_digest(sources.relative_code(codes[point],row['path']))==row['body_code_sha256'][point]
            assert [i.offset for i in dis.get_instructions(codes[point])if i.opname in {'RETURN_VALUE','RETURN_CONST'}]==row['return_offsets'][point]
            assert {str(i.offset):i.positions.lineno for i in dis.get_instructions(codes[point],show_caches=True)}==row['instruction_lines'][point]
            instructions={i.offset:i for i in dis.get_instructions(codes[point])}
            for site in row['direct_raise_offsets'][point]:
                assert instructions[site['instruction_offset']].opname=='RAISE_VARARGS'
                assert instructions[site['instruction_offset']].positions.lineno==site['source_line']
                assert site['exception_kind']in {'timeout','runtime-error'}


@pytest.fixture
def sealed_capture(setup,tmp_path):
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    prepared=tmp_path/'prepared';prepared.mkdir(mode=0o700)
    files=capture._files()
    assets={name:{'bytes':len(raw),'sha256':sha256(raw).hexdigest()}for name,raw in files.items()}
    cfg={'schema':'guard-rank-response-trace-settings/1','session':'a'*32,'image':'EXAMPLE-image','key':(b'K'*32).hex(),
         'output_dir':str(trace_runtime.ROOT/'data'/('a'*32)),'assets':assets,'rank_settings_sha256':'e'*64}
    cfg_raw=capture.canonical(cfg);digest=sha256(cfg_raw).hexdigest()
    manifest={'schema':'guard-rank-response-trace-prepared/1','session':cfg['session'],'image':cfg['image'],
        'settings_sha256':digest,'assets':assets,'rank_settings_sha256':cfg['rank_settings_sha256'],
        'source_manifest_sha256':'f'*64,**capture.FLAGS}
    files.update({'settings.json':cfg_raw,'manifest.json':capture.canonical(manifest)})
    for name,raw in files.items():
        dest=prepared/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw);dest.chmod(0o600)
    sink=tmp_path/'closed';sink.mkdir(mode=0o700)
    writer=trace_runtime.SnapshotWriter({**cfg,'output_dir':str(sink)},digest)
    tracer.emit=writer
    return setup,prepared,sink,cfg


@pytest.mark.parametrize('error',[False,True])
def test_sealed_original_source_graph_roundtrip_preserves_normal_and_actual_stdlib_exception(sealed_capture,error):
    setup,prepared,sink,cfg=sealed_capture
    module,observer,tracer,old_rows,rows,owner,scheduled,path,memory=setup
    future=owner.execute(scheduled)
    if error:owner.response.error=TimeoutError('PRIVATE_MESSAGE')
    tracer.install()
    if error:
        with pytest.raises(TimeoutError):future.result()
    else:assert future.result()is owner.response.output
    tracer.close()
    result=capture.verify_capture(prepared,sink)
    operation,=result['operations']
    assert operation['source_four_point_graph_complete']
    assert operation['same_native_error_reached_future']is error
    assert operation['reader_error_is_direct_native_cause']is error
    assert all(result[k]is False for k in capture.FLAGS)
    assert not result['gaps']and 'PRIVATE_MESSAGE'not in json.dumps(result)
    assert cfg['key']not in json.dumps(result)
    assert {r['name']for r in result['records']}=={p.name for p in sink.glob('pid-*.json')}


@pytest.mark.parametrize('damage',['signature','source','line','claim','context','exception','sequence','return-offset','endpoint','origin'])
def test_snapshot_or_resigned_semantic_tamper_is_rejected(sealed_capture,damage):
    setup,prepared,sink,cfg=sealed_capture
    tracer,owner,scheduled=setup[2],setup[5],setup[6]
    future=owner.execute(scheduled);tracer.install();future.result();tracer.close()
    files=sorted(sink.glob('pid-*.json'),key=lambda p:int(p.stem.rsplit('-',1)[1]))
    path=files[0];record=json.loads(path.read_bytes());row=record['snapshot']['payload']
    if damage=='signature':record['hmac_sha256']='0'*64
    elif damage=='source':row['body_code_sha256']='0'*64
    elif damage=='line':row['line']+=1
    elif damage=='claim':row['fault_causality_verified']=True
    elif damage=='context':row['process']['start_ticks']+=1
    elif damage=='exception':row['exception']={'object_hmac_sha256':'0'*64,'cause_hmac_sha256':None,'kind':'timeout'}
    elif damage=='sequence':row['sequence']=2
    elif damage=='return-offset':row['event']='RETURN';row['instruction_offset']=9999999
    elif damage=='endpoint':row['reader_endpoint']={'EXAMPLE':'unverified'}
    else:row['binding']['deferred_rpc']['point']='not-an-origin'
    if damage!='signature':
        record['hmac_sha256']=__import__('hmac').new(bytes.fromhex(cfg['key']),capture.canonical(record['snapshot']),sha256).hexdigest()
    path.write_bytes(capture.canonical(record))
    with pytest.raises((ValueError,KeyError)):capture.verify_capture(prepared,sink)


def test_missing_terminal_cannot_complete_even_with_valid_remaining_signatures(sealed_capture):
    setup,prepared,sink,cfg=sealed_capture
    tracer,owner,scheduled=setup[2],setup[5],setup[6]
    future=owner.execute(scheduled);tracer.install();future.result();tracer.close()
    last=max(sink.glob('pid-*.json'),key=lambda p:int(p.stem.rsplit('-',1)[1]));last.unlink()
    result=capture.verify_capture(prepared,sink);operation,=result['operations']
    assert not operation['source_four_point_graph_complete']and not operation['same_native_error_reached_future']
    assert 'RANK_RESPONSE_FRAME_NOT_CLOSED'in operation['gaps']


@pytest.mark.parametrize('raw',[b'{"a":1,"a":2}',b'NaN',b'1e999',b'1.5',b'\xff',b'['*65+b'0'+b']'*65,b'\xff\xfe{\x00}\x00'])
def test_capture_json_rejects_ambiguous_numbers_depth_encoding_and_duplicates(raw):
    with pytest.raises(ValueError):capture.document(raw)


def test_private_prepared_source_owner_and_permissions_cannot_be_substituted(sealed_capture):
    setup,prepared,sink,cfg=sealed_capture
    assert capture.load_prepared(prepared)[1]==cfg
    with pytest.raises(ValueError):capture.load_prepared(prepared,expected_uid=True)
    with pytest.raises(ValueError):capture.load_prepared(prepared,expected_uid=__import__('os').geteuid()+1)
    path=prepared/'settings.json';path.chmod(0o644)
    with pytest.raises(ValueError,match='PRIVATE_SETTINGS_REQUIRED'):capture.load_prepared(prepared)
    path.chmod(0o600)
    asset=prepared/'guard/adapters/vllm_rank_response_trace.py';asset.write_bytes(asset.read_bytes()+b'\n#changed')
    with pytest.raises(ValueError):capture.load_prepared(prepared)


def test_prepare_keeps_original_sdk_bytes_and_key_private_and_verifies_loaded_extension(tmp_path):
    from pathlib import Path
    from tools import rank_ipc_candidates
    image=json.loads(tracing.PROFILES.read_bytes())['profiles'][0]['image']
    rank=tmp_path/'rank';rank_ipc_candidates.prepare(rank,image=image)
    def hashes():return {str(p.relative_to(rank)):sha256(p.read_bytes()).hexdigest()for p in rank.rglob('*')if p.is_file()}
    before=hashes();extension=tmp_path/'extension'
    manifest=capture.prepare(extension,rank_prepared=rank,repository=Path(__file__).resolve().parents[2])
    assert hashes()==before and manifest['rank_settings_sha256']==sha256((rank/'settings.json').read_bytes()).hexdigest()
    loaded,cfg,profile=capture.load_prepared(extension)
    assert loaded==manifest and cfg['key']==json.loads((rank/'settings.json').read_bytes())['key']
    assert cfg['key']not in json.dumps(manifest)and all(manifest[k]is False for k in capture.FLAGS)
    trace_runtime.freeze(cfg,extension)
    with pytest.raises(ValueError):capture.prepare(extension,rank_prepared=rank,repository=Path(__file__).resolve().parents[2])
    asset=rank/'guard/adapters/vllm_rank_rpc_hooks.py';asset.write_bytes(asset.read_bytes()+b'\n#changed')
    with pytest.raises(ValueError):capture.prepare(tmp_path/'refused',rank_prepared=rank,repository=Path(__file__).resolve().parents[2])
    assert not(tmp_path/'refused').exists()


def test_bootstrap_preserves_original_sitecustomize_and_application_namespace(tmp_path):
    from guard.adapters.vllm_rank_response_bootstrap import BOOTSTRAP
    extension=tmp_path/'extension';extension.mkdir()
    (extension/'sitecustomize.py').write_bytes(BOOTSTRAP)
    adapters=extension/'guard/adapters';adapters.mkdir(parents=True)
    (adapters/'vllm_rank_response_runtime.py').write_text('import builtins\ndef install(): builtins.EXAMPLE_response_installed=True\n')
    original=tmp_path/'original';original.mkdir()
    (original/'sitecustomize.py').write_text('''import sys,types,builtins
builtins.EXAMPLE_original_ran=True
application=types.ModuleType('guard');application.EXAMPLE_unchanged=True;sys.modules['guard']=application
rank=types.ModuleType('_byul_rank_ipc');rank.__path__=[]
adapters=types.ModuleType('_byul_rank_ipc.adapters');adapters.__path__=[]
sys.modules[rank.__name__]=rank;sys.modules[adapters.__name__]=adapters
''')
    code='import builtins,sys,json;print(json.dumps([builtins.EXAMPLE_original_ran,builtins.EXAMPLE_response_installed,sys.modules["guard"].EXAMPLE_unchanged,"_byul_rank_ipc.adapters.vllm_rank_response_runtime"in sys.modules]))'
    env={**os.environ,'PYTHONPATH':os.pathsep.join(map(str,(extension,original)))}
    result=subprocess.run([sys.executable,'-c',code],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)==[True,True,True,True]
    assert 'RANK_RESPONSE_OBSERVER_UNAVAILABLE'not in result.stderr
