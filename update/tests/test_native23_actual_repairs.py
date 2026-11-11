"""Native23 actual-shape regressions; EXAMPLE packets never supply GPU proof."""
import ast,hashlib,json,pathlib
from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
import pytest
from guard.adapters import vllm_lifecycle_sensor as base
from guard.executors import lifecycle_session
from tests.test_lifecycle_sensor import RecordingObserver,writer_factory
from tests.test_lifecycle_capture import case,external
from tests.test_lifecycle_session import group,inventory_packet


def test_exact_defaultdict_blocks_uses_get_without_allocating_missing_keys():
    class Pool:pass
    observer=RecordingObserver();pool=Pool();pool.blocks=[]
    mapping=defaultdict(list);manager=SimpleNamespace(req_to_blocks=mapping,block_pool=pool)
    owner=SimpleNamespace(coordinator=SimpleNamespace(single_type_managers=[manager]))
    result=observer.blocks(owner,('selected',))
    assert result['complete'] and result['groups'][0]['block_sha256']==[]
    assert dict(mapping)=={}


def test_arbitrary_dict_subclass_is_not_read_as_native_block_mapping():
    class Trap(dict):
        def get(self,*args):raise AssertionError('subclass executed')
    class Pool:pass
    observer=RecordingObserver();pool=Pool();pool.blocks=[];manager=SimpleNamespace(req_to_blocks=Trap(),block_pool=pool)
    result=observer.blocks(SimpleNamespace(coordinator=SimpleNamespace(single_type_managers=[manager])),('selected',))
    assert result=={'complete':False,'groups':[]}

@pytest.mark.parametrize('raises',[False,True])
def test_core_abort_none_return_joins_call_input_and_scheduler_state(raises):
    observer=RecordingObserver();owner=SimpleNamespace(scheduler=SimpleNamespace(requests={'selected':object()}));given=['selected']
    fixture=pathlib.Path(__file__).parent/'fixtures/vllm-mp-031/core.py';raw=fixture.read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='f116962c35d9d312b20b0b76c34a8cdaa6d3f1d31f2036db4e123957d2d1555b'
    cls=next(n for n in ast.parse(raw).body if isinstance(n,ast.ClassDef)and n.name=='EngineCore')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef)and n.name=='abort_requests')
    namespace={'RequestStatus':SimpleNamespace(FINISHED_ABORTED=5)}
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(fixture),'exec',dont_inherit=True,optimize=0),namespace)
    def finish(request_ids,status):
        assert status==5;owner.scheduler.requests.clear();request_ids.clear()
        if raises:raise RuntimeError('native fixture')
    owner.scheduler.finish_requests=finish
    wrapped=observer.wrap('vllm.v1.engine.core','EngineCore.abort_requests',namespace['abort_requests'])
    if raises:
        with pytest.raises(RuntimeError):wrapped(owner,given)
    else:assert wrapped(owner,given)is None
    last=observer.recorded[-1]['payload']
    assert last['phase']==('RAISE'if raises else 'RETURN')
    if not raises:assert last['requests']==[{'request_sha256':base.id_sha('selected'),'request_present':False,'output_state_present':None,'batch_row_present':None,'status_code':None}]
    assert given==[]
    assert all('_abort_request_ids'not in row for row in last['call_chain'])



def test_untrusted_role_field_cannot_be_admitted_by_the_original_inventory_parser(group):
    packet=inventory_packet(group)
    packet['processes'][0]['auxiliary_role']={'role':'multiprocessing.resource_tracker'}
    with pytest.raises(ValueError,match='LIFECYCLE_INVENTORY_PROCESS_INVALID'):
        lifecycle_session.parse_inventory(json.dumps(packet).encode(),group.prepared['session'])
