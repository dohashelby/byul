"""Owned Python/SHM fixtures for reply publication; never a GPU measurement."""
from copy import deepcopy
from hashlib import sha256
import json
from multiprocessing.shared_memory import SharedMemory
from pathlib import Path
import sys
import threading
import time
import types

import pytest

from guard.adapters import vllm_rank_ipc_hooks as ipc
from guard.adapters import vllm_rank_ipc_runtime as runtime
from guard.adapters import vllm_rank_ipc_sources as sources
from guard.adapters import vllm_rank_rpc_control as control
from guard.adapters import vllm_rank_rpc_hooks as rpc
from tests.test_rank_rpc_product_binding import supply

RAW = b'''calls = []
class Scheduled:
    pass
class Queue:
    def enqueue(self, output):
        calls.append('queue')
        if self.error is not None:
            raise self.error
        return output
class Worker:
    def execute(self, scheduler_output):
        calls.append('compute')
        return self.publish(self.output)
    def publish(self, output):
        calls.append('publication')
        return self.queue.enqueue(output)
class External:
    @staticmethod
    def header(value):
        return value
'''


def spec(raw, name='rank_reply_fixture'):
    common={'phase':'around-original','role':'EXAMPLE','source_modules':[name]}
    hooks={
        'Worker.execute':{**common,'kind':'rpc','requests':{'root':'scheduler_output','layout':'scheduled'}},
        'Worker.publish':{**common,'kind':'rpc-reply','parent':{'point':'Worker.execute','worker_path':[]},
            'synchronous_false_path':['use_async_scheduling']},
        'Queue.enqueue':{**common,'kind':'rpc-reply','parent':{'point':'Worker.publish','worker_path':['queue']},
            'synchronous_false_root':'rpc_parent','synchronous_false_path':['use_async_scheduling'],
            'scalars':{'gpu_rank':{'root':'rpc_parent','path':['rank']},
                'configured_tp_size':{'root':'rpc_parent','path':['tp']}},
            'endpoints':{'response':{'kind':'shared-memory','root':'self','path':['shared']}},
            'fault_eligible':True,'fault_kinds':['communication-stall'],'required_initial_endpoint':'response'},
        'External.header':{**common,'kind':'external-id'}}
    codes=sources._codes(compile(raw,'fixture.py','exec',dont_inherit=True,optimize=0))
    return {'id':'EXAMPLE-reply','image':'fixture-image','runtime':sys.implementation.name+'-'+'.'.join(map(str,sys.version_info[:3])),
        'sources':{},'rpc_sources':{name:{'path':'fixture.py','bytes':len(raw),'sha256':sha256(raw).hexdigest(),
            'functions':list(hooks),'hooks':hooks,'body_code_sha256':{point:sources.code_digest(codes[point]) for point in hooks}}},
        'request_layouts':{'scheduled':{'kind':'positive-dict-keys','type':{'module':name,'name':'Scheduled'},
            'field':'num_scheduled_tokens','item_types':[]}},
        'request_binding':{'kind':'direct','external_prefix':'','external_suffix':''}}


@pytest.fixture
def reply_setup(tmp_path,monkeypatch):
    name='rank_reply_fixture'; path=tmp_path/'fixture.py';path.write_bytes(RAW)
    module=types.ModuleType(name);module.__file__=str(path)
    exec(compile(RAW,str(path),'exec',dont_inherit=True,optimize=0),vars(module))
    monkeypatch.setitem(sys.modules,name,module)
    profile=spec(RAW)
    catalog=tmp_path/'profiles.json'
    catalog.write_text(json.dumps({'schema':'guard-rank-ipc-source-candidates/1','profiles':[profile]}))
    monkeypatch.setattr(sources,'PROFILES',catalog)
    rows=[]; identity=ipc.IPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append)
    observer=rpc.RPCObserver(image='fixture-image',key=b'K'*32,emit=rows.append,identity_observer=identity)
    observer.attach(module)
    shared=SharedMemory(create=True,size=1)
    worker=module.Worker();worker.rank=0;worker.tp=2;worker.use_async_scheduling=False
    queue=module.Queue();queue.shared=shared;queue.error=None;worker.queue=queue;worker.output=object()
    scheduled=module.Scheduled();scheduled.num_scheduled_tokens={'EXAMPLE-private-request':1}
    try:yield module,observer,rows,worker,scheduled
    finally:shared.close();shared.unlink()


def test_reply_chain_uses_actual_parent_and_queue_without_inspecting_output(reply_setup):
    module,observer,rows,worker,scheduled=reply_setup
    class Opaque:
        def __repr__(self):raise AssertionError('output repr forbidden')
        def __iter__(self):raise AssertionError('output iteration forbidden')
        @property
        def __dict__(self):raise AssertionError('output attribute access forbidden')
    worker.output=Opaque()
    assert worker.execute(scheduled) is worker.output
    assert module.calls==['compute','publication','queue']
    selected=[row for row in rows if row.get('point')=='Queue.enqueue']
    assert [row['event'] for row in selected]==['CALL','RETURN']
    assert all(row['rpc_parent_frame_verified'] and row['rpc_parent_worker_verified'] and not row['gaps'] for row in selected)
    assert selected[0]['object_hmac_sha256']!=selected[0]['rpc_parent_object_hmac_sha256']
    assert selected[0]['endpoints']['response']['identity_hmac_sha256']==selected[1]['endpoints']['response']['identity_hmac_sha256']
    assert selected[0]['scalars']=={'gpu_rank':0,'configured_tp_size':2}
    assert 'EXAMPLE-private-request' not in json.dumps(rows)
    assert observer.current.get() is None


def test_unrelated_queue_original_exception_is_not_retried(reply_setup):
    module,_,rows,worker,_=reply_setup
    error=RuntimeError('EXAMPLE');worker.queue.error=error
    with pytest.raises(RuntimeError) as caught:worker.queue.enqueue(worker.output)
    assert caught.value is error and module.calls==['queue'] and rows==[]


def test_async_reply_has_no_fabricated_parent_but_preserves_original(reply_setup):
    module,_,rows,worker,scheduled=reply_setup
    worker.use_async_scheduling=True
    assert worker.execute(scheduled) is worker.output
    assert module.calls==['compute','publication','queue']
    assert not any(row.get('point')=='Queue.enqueue' for row in rows)
    assert any(row.get('diagnostic_code')=='RANK_RPC_REPLY_ASYNC_UNSUPPORTED' for row in rows)


@pytest.fixture
def controlled_reply(reply_setup,tmp_path,monkeypatch):
    module,observer,rows,worker,scheduled=reply_setup
    directory=tmp_path/'data';directory.mkdir(mode=0o700)
    settings={'session':'a'*32,'image':'fixture-image','key':(b'K'*32).hex(),'output_dir':str(directory),
        'assets':{'guard/adapters/vllm_rank_rpc_control.py':{'bytes':1,'sha256':'b'*64}}}
    digest=control.digest(settings);writer=runtime.SnapshotWriter(settings,digest)
    def emit(row):rows.append(row);writer(row)
    observer.emit=emit
    instance=control.Control(settings,digest,observer.profile,emit,lambda:None)
    binding=supply(instance,monkeypatch)
    assert worker.execute(scheduled) is worker.output
    initial=next(row for row in rows if row.get('point')=='Queue.enqueue' and row.get('event')=='CALL')
    observer.control=instance
    return (*reply_setup,instance,binding,initial)


def arm_reply(data,*,lifetime_ns=4_000_000_000,damage=None):
    module,_,_,_,scheduled,instance,binding,initial=data
    from guard.adapters.vllm_rank_rpc_actor import _id
    header='EXAMPLE-new-reply';now=time.monotonic_ns()
    arm={'schema':'guard-rank-rpc-arm/3','session':instance.settings['session'],'image':instance.settings['image'],
        'settings_sha256':instance.settings_sha256,'assets_sha256':control.digest(instance.settings['assets']),
        'target_sha256':'1'*64,'nonce':'2'*32,'fault_kind':'communication-stall','issued_ns':now,'expires_ns':now+lifetime_ns,
        'target_context':initial['context'],'parent_object_hmac_sha256':initial['rpc_parent_object_hmac_sha256'],
        'target_point':initial['point'],'source_sha256':initial['source_sha256'],'body_code_sha256':initial['body_code_sha256'],
        'header_hmac_sha256':_id(header,instance.key),'external_hmac_sha256':_id(header,instance.key),
        'header_context':initial['context'],'assignment_context':None,'initial_capture_sha256':'3'*64,
        'product_binding':binding,'reply_binding':{'object_hmac_sha256':initial['object_hmac_sha256'],
            'response_hmac_sha256':initial['endpoints']['response']['identity_hmac_sha256']}}
    if damage is not None:damage(arm)
    arm_sha=instance.write('control-arm.json',arm)
    assert module.External.header(header)==header
    scheduled.num_scheduled_tokens={header:1}
    return arm,arm_sha


def start(data):
    worker,scheduled=data[3:5];outcome=[]
    def run():
        try:outcome.append(('returned',worker.execute(scheduled)))
        except BaseException as error:outcome.append(('raised',error))
    thread=threading.Thread(target=run);thread.start()
    return thread,outcome


def wait_for(test):
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        value=test()
        if value:return value
        time.sleep(.005)
    pytest.fail('owned reply control did not reach the expected state')


def action(instance,name,arm,arm_sha,ready_sha,kind):
    return instance.write(name,{'schema':'guard-rank-rpc-action/1','nonce':arm['nonce'],
        'arm_sha256':arm_sha,'ready_sha256':ready_sha,'action':kind,'receipt_sha256':'4'*64})


def test_ready_stall_and_separate_release_are_distinct_original_source_events(controlled_reply):
    module,_,rows,worker,_,instance,_,_=controlled_reply
    arm,arm_sha=arm_reply(controlled_reply);before=len(module.calls)
    thread,outcome=start(controlled_reply)
    ready=wait_for(lambda:next((row for row in rows if row.get('event')=='CONTROL_READY'),None))
    ready_sha=instance.read('control-ready.json')[1]
    assert thread.is_alive() and outcome==[] and module.calls[before:]==['compute','publication']
    assert not any(row.get('event')=='CONTROL_STALL' for row in rows)
    action_sha=action(instance,'control-action.json',arm,arm_sha,ready_sha,'stall')
    stalled=wait_for(lambda:next((row for row in rows if row.get('event')=='CONTROL_STALL'),None))
    assert stalled['control']['barrier_only'] is False and stalled['control']['action_sha256']==action_sha
    assert thread.is_alive() and module.calls[before:]==['compute','publication']
    original_action=instance.read_raw('control-action.json')
    action(instance,'control-stall-release.json',arm,arm_sha,ready_sha,'release')
    thread.join(3)
    assert not thread.is_alive() and outcome==[('returned',worker.output)]
    assert module.calls[before:]==['compute','publication','queue']
    assert instance.read_raw('control-action.json')==original_action
    assert [row['event'] for row in rows if row.get('event','').startswith('CONTROL_')]==[
        'CONTROL_READY','CONTROL_STALL','CONTROL_RELEASE']
    assert all(row['producer_registered'] is row['source_authenticated'] is False for row in rows if 'producer_registered' in row)


@pytest.mark.parametrize('damage',['object','endpoint','product','legacy-schema'])
def test_unbound_or_changed_reply_queue_never_stalls(controlled_reply,damage):
    module,_,rows,worker,_,instance,_,_=controlled_reply
    def change(arm):
        if damage=='object':arm['reply_binding']['object_hmac_sha256']='f'*64
        elif damage=='endpoint':arm['reply_binding']['response_hmac_sha256']='f'*64
        elif damage=='product':arm['product_binding']={**arm['product_binding'],'container_id':'f'*64}
        else:arm['schema']='guard-rank-rpc-arm/2';arm.pop('reply_binding')
    arm_reply(controlled_reply,damage=change)
    thread,outcome=start(controlled_reply);thread.join(3)
    assert not thread.is_alive() and outcome==[('returned',worker.output)]
    assert not any(row.get('event') in {'CONTROL_READY','CONTROL_STALL'} for row in rows)


def test_unsigned_or_wrong_release_fails_open_without_publishing_a_verified_release(controlled_reply):
    _,_,rows,worker,_,instance,_,_=controlled_reply
    arm,arm_sha=arm_reply(controlled_reply);thread,outcome=start(controlled_reply)
    wait_for(lambda:any(row.get('event')=='CONTROL_READY' for row in rows))
    ready_sha=instance.read('control-ready.json')[1]
    action(instance,'control-action.json',arm,arm_sha,ready_sha,'stall')
    wait_for(lambda:any(row.get('event')=='CONTROL_STALL' for row in rows))
    instance.write('control-stall-release.json',{'schema':'EXAMPLE-invalid'})
    thread.join(3)
    assert not thread.is_alive() and outcome==[('returned',worker.output)]
    assert not any(row.get('event')=='CONTROL_RELEASE' for row in rows)
    assert any(row.get('schema')=='guard-rank-rpc-control-gap/1' for row in rows)
