"""EXAMPLE objects test snapshot ABI; real CPU objects are checked separately."""
from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from guard.adapters import vllm_slot_mask as native


class Object:
    def __init__(self,**kwargs):self.__dict__.update(kwargs)


class Tensor:
    __module__='torch'
    def __init__(self,values,*,device='cpu',dtype='torch.bool'):
        self.values=values;self.device=SimpleNamespace(type=device);self.dtype=dtype
        self.shape=(len(values),len(values[0]) if values else 0);self.reads=0
    def __getitem__(self,index):
        self.reads+=1
        if self.device.type!='cpu':raise AssertionError('GPU_READBACK')
        return SimpleNamespace(tolist=lambda:deepcopy(self.values[index]))
    def tolist(self):
        self.reads+=1
        if self.device.type!='cpu':raise AssertionError('GPU_READBACK')
        return deepcopy(self.values)


@pytest.fixture
def batch():
    host=Tensor([[False,True,True,True],[False]*4])
    meta=Object(allowed_token_ids_mask=Tensor(deepcopy(host.values)))
    return Object(vocab_size=4,max_model_len=16,max_num_reqs=2,device=SimpleNamespace(type='cpu'),
        is_pooling_model=False,req_id_to_index={'PRIVATE_A':0,'PRIVATE_B':1},has_allowed_token_ids={'PRIVATE_A'},
        allowed_token_ids_mask_cpu_tensor=host,sampling_metadata=meta)


def test_refresh_return_emits_real_dimensions_rows_and_metadata_identity(batch):
    before=deepcopy(batch.allowed_token_ids_mask_cpu_tensor.values)
    result=native.snapshot_slot_mask('InputBatch.refresh_metadata',(batch,),{},phase='RETURN')
    data=result['metadata']
    assert data['dimensions']=={'vocab_size':4,'max_model_len':16,'max_num_reqs':2,'device_type':'cpu','is_pooling_model':False}
    assert data['slot_map_complete'] is True and data['rows'][0]['restricted'] is True
    assert data['rows'][0]['cpu_mask']['masked_count']==3 and data['rows'][1]['cpu_mask']['masked_count']==0
    assert data['sampling_mask_present'] is True and data['sampling_mask']['shape']==[2,4]
    assert batch.allowed_token_ids_mask_cpu_tensor.values==before and 'PRIVATE_' not in json.dumps(result)
    sample=native.snapshot_slot_mask('Sampler.forward',(SimpleNamespace(),Tensor([[0.0]*4]*2,dtype='torch.float32'),batch.sampling_metadata),{},phase='CALL')
    assert data['sampling_metadata_token'] is not None and data['sampling_mask_token'] is not None
    assert sample['metadata']['sampling_metadata_token']==data['sampling_metadata_token']
    assert sample['metadata']['sampling_mask_token']==data['sampling_mask_token']


def test_same_object_tokens_are_opaque_and_new_instance_is_distinct(batch):
    first=native.snapshot_slot_mask('InputBatch.__init__',(batch,),{},phase='RETURN')
    second=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='CALL')
    other=deepcopy(batch)
    third=native.snapshot_slot_mask('InputBatch.__init__',(other,),{},phase='RETURN')
    assert first['metadata']['batch_token']==second['metadata']['batch_token']
    assert first['metadata']['batch_token']!=third['metadata']['batch_token']
    assert len(first['metadata']['batch_token'])==64 and '0x' not in json.dumps(first)


def test_absent_mask_is_not_zero_or_missing_metadata():
    meta=Object(allowed_token_ids_mask=None)
    result=native.snapshot_slot_mask('Sampler.forward',(SimpleNamespace(),None,meta),{},phase='CALL')
    assert result['metadata']['sampling_mask_present'] is False
    assert result['metadata']['sampling_mask']=={'status':'absent'}
    assert result['metadata']['sampling_mask_token'] is None
    missing=native.snapshot_slot_mask('Sampler.forward',(),{},phase='CALL')
    assert missing['metadata']['sampling_mask_present'] is None
    assert missing['metadata']['sampling_mask']=={'status':'unavailable'}


def test_gpu_tensor_descriptor_does_not_read_values(batch):
    gpu=Tensor([[False]*4]*2,device='cuda');batch.sampling_metadata.allowed_token_ids_mask=gpu
    result=native.snapshot_slot_mask('InputBatch.refresh_metadata',(batch,),{},phase='RETURN')
    descriptor=result['metadata']['sampling_mask']
    assert descriptor['device_type']=='cuda' and descriptor['values_collected'] is False
    assert native.cpu_mask_row(gpu,0)=={'status':'unavailable'}
    assert native.cpu_output(gpu)=={'status':'unavailable'} and gpu.reads==0


def test_cpu_sampler_output_only_counts_and_hashes(batch):
    output=Tensor([[3],[1]],dtype='torch.int64')
    result=native.snapshot_slot_mask('Sampler.forward',(SimpleNamespace(),None,batch.sampling_metadata),{},phase='RETURN',result=SimpleNamespace(sampled_token_ids=output))
    value=result['metadata']['sampled_output']
    assert value=={'status':'collected','count':2,'sha256':native.digest([3,1]),'row_sha256':[native.digest([3]),native.digest([1])]}
    assert 'values' not in value and output.values==[[3],[1]]


def test_actual_add_constraint_and_swap_arguments_are_preserved_as_hashes(batch):
    request=SimpleNamespace(req_id='PRIVATE_NEW',sampling_params=SimpleNamespace(allowed_token_ids=None))
    result=native.snapshot_slot_mask('InputBatch.add_request',(batch,request),{},phase='CALL')
    assert result['metadata']['argument_constraint']=={'status':'collected','restricted':False,'count':0,'sha256':native.digest(None)}
    swap=native.snapshot_slot_mask('InputBatch.swap_states',(batch,),{'i1':0,'i2':1},phase='RETURN')
    assert swap['metadata']['swap_indices']==[0,1]
    assert 'PRIVATE_' not in json.dumps(result)


def test_bounds_and_unknown_objects_remain_unavailable(batch,monkeypatch):
    monkeypatch.setattr(native,'MAX_COLUMNS',2)
    result=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='RETURN')
    assert all(row['cpu_mask']['status']=='unavailable' for row in result['metadata']['rows'])
    assert native.cpu_mask_row(iter(()),0)=={'status':'unavailable'}


def test_observer_does_not_iterate_arbitrary_sequences_or_replace_exception(batch):
    touched=[]
    def seq():touched.append(True);yield False
    batch.allowed_token_ids_mask_cpu_tensor=seq();error=RuntimeError('PRIVATE_EXCEPTION')
    result=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='RAISE',exception=error)
    assert touched==[] and result['metadata']['exception_type']=='RuntimeError'
    assert 'PRIVATE_EXCEPTION' not in json.dumps(result)
    assert native.snapshot_slot_mask([],(),{},phase='CALL')['status']=='unavailable'


def test_condense_pair_reads_actual_vacated_row_with_same_call_id(batch):
    batch.max_num_reqs=3
    batch.req_id_to_index={'PRIVATE_A':0,'PRIVATE_B':2}
    batch.allowed_token_ids_mask_cpu_tensor=Tensor([[False]*4,[False]*4,[False,True,True,True]])
    binding={'call_id_sha256':'a'*64}
    call=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='CALL',binding=binding)
    assert call['metadata']['slot_transition']=={'status':'pending'}
    batch.req_id_to_index={'PRIVATE_B':0}
    batch.allowed_token_ids_mask_cpu_tensor.values=[[False,True,True,True],[False]*4,[False]*4]
    returned=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='RETURN',binding=binding)
    transition=returned['metadata']['slot_transition']
    assert transition['status']=='collected' and transition['vacated_rows'][0]['slot_index']==2
    assert transition['vacated_rows'][0]['cpu_mask']['masked_count']==0
    assert len(transition['previous_rows'])==2 and 'PRIVATE_' not in json.dumps(returned)


def test_missing_call_or_wrong_call_id_cannot_infer_vacated_row_clear(batch):
    native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='CALL',binding={'call_id_sha256':'b'*64})
    returned=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='RETURN',binding={'call_id_sha256':'c'*64})
    assert returned['metadata']['slot_transition']=={'status':'unavailable'}


def test_add_return_reports_actual_assigned_slot_and_cpu_arg_mask_values(batch):
    req=SimpleNamespace(req_id='PRIVATE_NEW',sampling_params=SimpleNamespace(allowed_token_ids=None))
    result=native.snapshot_slot_mask('InputBatch.add_request',(batch,req),{},phase='RETURN',result=1)
    assert result['metadata']['assigned_slot_index']==1
    mask=result['metadata']['sampling_mask']
    assert mask['values_collected'] is True and len(mask['cpu_rows'])==2
    assert mask['cpu_rows'][1]['cpu_mask']['masked_count']==0


def test_duplicate_call_identity_cannot_reuse_an_older_transition(batch):
    binding={'call_id_sha256':'d'*64}
    native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='CALL',binding=binding)
    duplicate=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='CALL',binding=binding)
    returned=native.snapshot_slot_mask('InputBatch.condense',(batch,),{},phase='RETURN',binding=binding)
    assert duplicate['metadata']['slot_transition']['status']=='unavailable'
    assert returned['metadata']['slot_transition']['status']=='unavailable'


def test_inlined_field_and_binding_helpers_keep_reviewed_ast():
    import ast
    from pathlib import Path
    from guard.adapters import vllm_online_input
    original=ast.parse(Path(vllm_online_input.__file__).read_text())
    current=ast.parse(Path(native.__file__).read_text())
    for name in ('_get','_sha','_binding'):
        before=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name==name)
        after=next(n for n in current.body if isinstance(n,ast.FunctionDef) and n.name==name)
        assert ast.dump(before,include_attributes=False)==ast.dump(after,include_attributes=False)


def test_single_file_asset_load_needs_only_standard_library():
    import subprocess,sys
    process=subprocess.run([sys.executable,'-I','-S','-c',
        'import importlib.util,json,sys; spec=importlib.util.spec_from_file_location("slot_asset",sys.argv[1]); '
        'm=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); '
        'assert not any(k=="guard" or k.startswith("guard.") or k=="torch" for k in sys.modules); '
        'print(json.dumps(m.snapshot_slot_mask("unknown",(),{},phase="CALL")))',native.__file__],capture_output=True,text=True)
    assert process.returncode==0 and json.loads(process.stdout)['status']=='unavailable'
