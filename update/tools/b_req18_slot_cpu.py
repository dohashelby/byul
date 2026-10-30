"""Pinned V1 InputBatch/Sampler CPU source-invariant diagnostics, never GPU proof."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import inspect
import json
from pathlib import Path
import sys
import uuid
from guard.adapters import vllm_slot_mask as native

SOURCE_COMMIT='db9527a46873454610df6dbedf79a36d6bf1a7f6'
SOURCE_PROFILE='target031'
PRODUCT_COMPONENT_COMMIT='42e62a14776893dec8d70d7bd8e49769ce08b4de'  # Root fixes this after native-source freeze.
HELPER_SHA256='858666f5595fa6328a2975ef5a91eb9db19ad0712d11be08f64d8296cadaf6bf'
BASE_SHA='c9af729cd1893eda14d0bb222a8fab46c2a6d7b8'
PINNED_PATHS=('update/src/guard','update/packs','update/uv.lock','update/pyproject.toml','update/reference/framework')
FIXTURE={'vocab_size':32,'max_model_len':16,'max_num_batched_tokens':16,'constrained_id':13,'preferred_id':31,'temperature':0,'device':'cpu'}


def verify_component():
    from tools import b1_integration_cpu as helper
    repo=Path(__file__).resolve().parents[2];file=repo/'update/tools/b1_integration_cpu.py'
    if (not isinstance(PRODUCT_COMPONENT_COMMIT,str) or len(PRODUCT_COMPONENT_COMMIT)!=40):raise ValueError('REQ18_COMPONENT_PIN_UNCONFIGURED')
    if (file.is_symlink() or not Path(helper.__file__).resolve().samefile(file)
            or not Path(inspect.getsourcefile(helper.verify_snapshot)).resolve().samefile(file)
            or hashlib.sha256(file.read_bytes()).hexdigest()!=HELPER_SHA256 or helper.BASE_SHA!=BASE_SHA
            or tuple(helper.PINNED_PATHS)!=PINNED_PATHS):raise ValueError('REQ18_COMPONENT_HELPER_MISMATCH')
    expected=repo/'update/src/guard/adapters/vllm_slot_mask.py'
    if not Path(native.__file__).resolve().samefile(expected) or not Path(inspect.getsourcefile(native.snapshot_slot_mask)).resolve().samefile(expected):raise ValueError('REQ18_NATIVE_IMPORT_MISMATCH')
    helper.verify_snapshot(repo,PRODUCT_COMPONENT_COMMIT,PINNED_PATHS)


def _post(root,own,native_sha):
    try:
        verify_component()
        from guard.executors.structured_tools_cpu import _verify_source
        _verify_source(root,expected_commit=SOURCE_COMMIT)
        if hashlib.sha256(Path(__file__).read_bytes()).hexdigest()!=own or hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()!=native_sha:raise ValueError('REQ18_PRODUCER_BYTES_CHANGED')
        return {'status':'verified'}
    except ValueError as error:return {'status':'mismatch','error_type':type(error).__name__}
    except Exception as error:return {'status':'unavailable','error_type':type(error).__name__}


def _run_actual(root,record=None):
    import torch
    from vllm.v1.worker.gpu_input_batch import InputBatch,CachedRequestState
    from vllm.sampling_params import SamplingParams
    from vllm.v1.sample.sampler import Sampler
    from vllm.utils.torch_utils import PIN_MEMORY
    if torch.version.cuda is not None or PIN_MEMORY is not False:raise ValueError('REQ18_ORIGINAL_CPU_PIN_MEMORY_REQUIRED')
    for cls,path in ((InputBatch,'vllm/v1/worker/gpu_input_batch.py'),(Sampler,'vllm/v1/sample/sampler.py')):
        if not Path(inspect.getfile(cls)).resolve().samefile(root/path):raise ValueError('REQ18_TARGET_IMPORT_MISMATCH')
    snapshots=record['native_snapshots'] if record is not None else [];results=record['results'] if record is not None else []
    constraints={};sampler=Sampler()
    def invoke(point,owner,fn,*args):
        binding={'call_id_sha256':hashlib.sha256(uuid.uuid4().bytes).hexdigest()}
        snapshots.append(native.snapshot_slot_mask(point,(owner,*args),{},phase='CALL',binding=binding))
        try:result=fn(*args)
        except Exception as error:
            snapshots.append(native.snapshot_slot_mask(point,(owner,*args),{},phase='RAISE',exception=error,binding=binding));raise
        snapshots.append(native.snapshot_slot_mask(point,(owner,*args),{},phase='RETURN',result=result,binding=binding));return result
    def batch(size):
        obj=InputBatch(max_num_reqs=size,max_model_len=16,max_num_batched_tokens=16,device=torch.device('cpu'),
            vocab_size=32,block_sizes=[1],kernel_block_sizes=[1],max_num_blocks_per_req=[16])
        snapshots.append(native.snapshot_slot_mask('InputBatch.__init__',(obj,),{},phase='RETURN'))
        return obj
    def request(label,allowed=None):
        return CachedRequestState(req_id=label,prompt_token_ids=[1,2,3],sampling_params=SamplingParams(temperature=0,allowed_token_ids=allowed),
            pooling_params=None,mm_features=[],block_ids=([],),generator=None,num_computed_tokens=0,output_token_ids=[])
    def add(obj,label,allowed=None):
        value=request(label,allowed);constraints[label]=native.digest(value.sampling_params.allowed_token_ids)
        return invoke('InputBatch.add_request',obj,obj.add_request,value)
    def sample(obj,b_label):
        invoke('InputBatch.refresh_metadata',obj,obj.refresh_metadata)
        metadata=obj.sampling_metadata;row=obj.req_id_to_index[b_label]
        logits=torch.zeros((obj.num_reqs,32),dtype=torch.float32,device='cpu');logits[:,31]=100.0
        input_hash=native.digest(logits[row].tolist())
        host=native.cpu_mask_row(obj.allowed_token_ids_mask_cpu_tensor,row) if obj.allowed_token_ids_mask_cpu_tensor is not None else {'status':'absent'}
        # No mask is the genuine unrestricted branch, not a manufactured zero mask.
        arg_mask=metadata.allowed_token_ids_mask
        if arg_mask is None:
            device={'status':'absent'}
        else:device=native.cpu_mask_row(arg_mask,row)
        output=invoke('Sampler.forward',sampler,sampler.forward,logits,metadata)
        sampled=native.cpu_output(output.sampled_token_ids)
        return {'collected':sampled.get('status')=='collected' and host.get('status') in ('collected','absent') and device.get('status') in ('collected','absent'),
            'current_b_unrestricted':b_label not in obj.has_allowed_token_ids,'constraint_sha256':constraints[b_label],
            'host_mask_status':host['status'],'metadata_mask_status':device['status'],'metadata_mask_present':arg_mask is not None,
            'effective_unrestricted':b_label not in obj.has_allowed_token_ids and (arg_mask is None or device.get('masked_count')==0),
            'logits_sha256':input_hash,'host_mask_sha256':host.get('mask_sha256'),'host_masked_count':host.get('masked_count'),
            'metadata_mask_sha256':device.get('mask_sha256'),'metadata_masked_count':device.get('masked_count'),
            'sampling_metadata_token':native._token(metadata),'sampling_mask_token':native._token(arg_mask),
            'sampled_count':1,'sampled_ids_sha256':sampled.get('row_sha256',[None]*obj.num_reqs)[row],
            'sampler_returned':True,'metadata_argument_matches_refresh':metadata is obj.sampling_metadata,
            'request_sha256':hashlib.sha256(b_label.encode()).hexdigest(),'slot_index':row},output.sampled_token_ids
    def trial_guard():
        if record is None:return {'status':'unavailable'}
        return _post(root,record['own_producer_sha256'],record['native_callback_sha256'])
    def begin_trial():
        status=trial_guard()
        if record is not None and status['status']!='verified':raise ValueError('REQ18_TRIAL_PRECHECK_UNAVAILABLE')
    def finish(row,standalone):
        row.update(standalone_output_sha256=standalone['sampled_ids_sha256'],standalone_mask_sha256=standalone['host_mask_sha256'],
            mask_matches_standalone=row['effective_unrestricted'] and standalone['effective_unrestricted'],
            effective_constraint_matches_standalone=row['effective_unrestricted'] and standalone['effective_unrestricted'],
            mask_comparison_basis='effective_unrestricted_branches',
            physical_mask_hash_matches_standalone=row['host_mask_sha256']==standalone['host_mask_sha256'] if row['host_mask_sha256'] is not None and standalone['host_mask_sha256'] is not None else None,
            output_matches_standalone=row['sampled_ids_sha256']==standalone['sampled_ids_sha256'])
        row['source_invariant_mismatch']=row['collected'] and (not row['effective_constraint_matches_standalone'] or not row['output_matches_standalone'] or not row['restricted_request_preserved'])
        row['source_validation']={'pre':'verified' if record is not None else 'unavailable',
            'post':trial_guard(),'boundary':'source_component'}
        results.append(row)
    begin_trial()
    base=batch(1);add(base,'standalone-b');standalone,_=sample(base,'standalone-b')
    standalone.update(case_id='standalone-b',slot_reused=False,restricted_request_preserved=True)
    finish(standalone,standalone)
    begin_trial()
    condensed=batch(4);add(condensed,'old');add(condensed,'middle');add(condensed,'restricted-a',[13])
    before=condensed.req_id_to_index['restricted-a']
    invoke('InputBatch.remove_request',condensed,condensed.remove_request,'old')
    invoke('InputBatch.condense',condensed,condensed.condense)
    moved=condensed.req_id_to_index['restricted-a'];retired=native.cpu_mask_row(condensed.allowed_token_ids_mask_cpu_tensor,before);new_slot=add(condensed,'reused-b')
    reused,output=sample(condensed,'reused-b')
    reused.update(case_id='condense-reuse',slot_reused=before==new_slot and moved!=before,retired_host_mask_sha256=retired.get('mask_sha256'),retired_host_masked_count=retired.get('masked_count'),
        restricted_request_preserved=int(output[moved,0].item())==13)
    finish(reused,standalone)
    begin_trial()
    swapped=batch(2);add(swapped,'restricted-a',[13]);add(swapped,'swap-b')
    invoke('InputBatch.swap_states',swapped,swapped.swap_states,0,1)
    swap,output=sample(swapped,'swap-b')
    swap.update(case_id='swap',slot_reused=swapped.req_id_to_index['swap-b']==0,
        restricted_request_preserved=int(output[swapped.req_id_to_index['restricted-a'],0].item())==13)
    finish(swap,standalone)
    begin_trial()
    follow=batch(1);add(follow,'follow-b');followup,_=sample(follow,'follow-b')
    followup['matches_standalone']=followup['sampled_ids_sha256']==standalone['sampled_ids_sha256']
    if record is not None:record['followup']=followup
    return results,snapshots,followup


def capture_slot_cpu(source_root:Path,workdir:Path,*,source_profile='target031'):
    if source_profile!=SOURCE_PROFILE or sys.flags.optimize or sys.version_info[:2]!=(3,12):raise ValueError('REQ18_CPU_PROFILE_INVALID')
    verify_component()
    from guard.executors.structured_tools_cpu import _verify_source,_validate_target031_dependencies
    root=source_root.resolve();_verify_source(root,expected_commit=SOURCE_COMMIT);_validate_target031_dependencies()
    workdir.mkdir(parents=True,exist_ok=False)
    own=hashlib.sha256(Path(__file__).read_bytes()).hexdigest();native_sha=hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest()
    record={'schema':'byul-req18-slot-cpu-capture/1','source_profile':SOURCE_PROFILE,'source_commit':SOURCE_COMMIT,
        'scope':'actual_v1_cpu_inputbatch_sampler_only','data_origin':'DECLARED','own_producer_sha256':own,'native_callback_sha256':native_sha,
        'collection_id':'req18-slot-cpu-'+uuid.uuid4().hex,'capture_started_at':datetime.now(timezone.utc).isoformat(),
        'source_validation':{'pre':'verified','boundary':'source_component'},'fixture_config':FIXTURE,'fixture_sha256':native.digest(FIXTURE),
        'results':[],'native_snapshots':[],'followup':None,'gpu_consumption_verified':False,'kubernetes_verified':False,'capture_status':'incomplete'}
    try:_run_actual(root,record)
    except Exception as error:record['error_type']=type(error).__name__
    record['capture_finished_at']=datetime.now(timezone.utc).isoformat()
    record['source_validation']['post']=_post(root,own,native_sha)
    record['capture_status']='completed' if len(record['results'])==3 and all(r['collected'] and r.get('source_validation',{}).get('pre')=='verified' and r.get('source_validation',{}).get('post',{}).get('status')=='verified' for r in record['results']) and isinstance(record['followup'],dict) and record['followup'].get('collected') is True and record['followup'].get('matches_standalone') is True and record['source_validation']['post']['status']=='verified' else 'incomplete'
    record['auxiliary_write']={'status':'completed'}
    try:(workdir/'result.json').write_text(json.dumps(record,indent=2))
    except Exception as error:record.update(capture_status='incomplete',auxiliary_write={'status':'failed','error_type':type(error).__name__})
    return record
