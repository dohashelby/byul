"""REQ-10 real typed/scheduler/batch/metadata/Sampler CPU component support.

The logits and KV capacity are EXAMPLE inputs. CachedRequestState is explicitly
constructed by the harness from the real scheduler output, not by a worker.
This is not an admitted native run or deployed model/GPU consumption proof.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

CONTRACT = 'guard-sampling-consumption-cpu/1'
SOURCES = {
    'vllm.exceptions': '22fe819c7ef10438f740c251d81af204871a04c3aa3eb1fd0e68604dc4c2146f',
    'vllm.sampling_params': '3c37c655a0a60851e3d96d946eb10d1cbb347dee6cd143f38a7fc18c6f66dd8b',
    'vllm.v1.request': 'dd7c30bf8b0894dc123a739c299c0133566f8cc075b081e45905f1c29506e69d',
    'vllm.v1.core.sched.scheduler': 'f820bee4f42b878b262a30f3728a97d7413477b2a277748e20f2528ac2ec2f2a',
    'vllm.v1.worker.gpu_input_batch': 'e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d',
    'vllm.v1.sample.metadata': '6582cfe1a240c7ddb975f0ef6dd7dcd28dcf0fc7670394390250013977aa7ffd',
    'vllm.v1.sample.sampler': 'd299305528479ff505b416b759db208fcf08ca6e86cc1f7cfc38be85bbd0e844',
    'vllm.v1.sample.ops.topk_topp_sampler': 'c5c7de950e4e1380c97328057aa6a39c86e734c8b2de35beffa9695b705fbba3',
}
FIELDS = ('temperature', 'top_p', 'top_k', 'frequency_penalty', 'presence_penalty', 'repetition_penalty', 'allowed_tokens')
CASES = (
    ('normal', {'temperature': 0}, 7),
    ('allowed', {'temperature': 0, 'allowed_token_ids': [2]}, 2),
    ('normal-after-allowed', {'temperature': 0}, 7),
    ('top-k', {'temperature': .7, 'top_p': .8, 'top_k': 1, 'seed': 123}, 7),
    ('penalties', {'temperature': 0, 'frequency_penalty': 1, 'presence_penalty': 1, 'repetition_penalty': 2}, 11),
    ('invalid-temperature', {'temperature': -1}, None),
    ('invalid-top-p', {'top_p': 1.1}, None),
    ('invalid-top-k', {'top_k': -2}, None),
)


def producer():
    return {'name': 'guard-sampling-consumption-cpu', 'version': '1', 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def project(value):
    if (type(value) is not dict or set(value) != {'contract', 'producer', 'data_origin', 'sources_before', 'sources_after', 'rows', 'source_ended'}
            or value['contract'] != CONTRACT or value['producer'] != producer() or value['data_origin'] != 'EXAMPLE'
            or value['sources_before'] != SOURCES or value['sources_after'] != SOURCES
            or type(value['source_ended']) is not bool or type(value['rows']) is not list or len(value['rows']) > len(CASES)):
        raise ValueError('SAMPLING_CONSUMPTION_ENVELOPE_INVALID')
    specs = {name: (options, expected) for name, options, expected in CASES}
    errors, candidates, seen = set(), [], set()
    for row in value['rows']:
        if (type(row) is not dict or set(row) != {'case', 'request_sha256', 'outcome', 'stages', 'same_scheduler_params', 'slot', 'expected_output_matched', 'error_stage', 'error_sha256'}
                or type(row.get('case')) is not str or row['case'] not in specs or row['case'] in seen
                or row.get('request_sha256') != hashlib.sha256(('guard-' + row['case']).encode()).hexdigest()
                or row.get('outcome') not in ('sampled', 'typed_rejected', 'typed_accepted', 'execution_error')
                or type(row.get('stages')) is not dict or type(row.get('same_scheduler_params')) is not bool
                or row.get('error_stage') not in (None, 'typed', 'scheduler', 'batch', 'sampler')
                or (row['error_sha256'] is not None and (type(row['error_sha256']) is not str or len(row['error_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['error_sha256'])))
                or (row['slot'] is not None and (type(row['slot']) is not int or row['slot'] < 0))
                or (row['expected_output_matched'] is not None and type(row['expected_output_matched']) is not bool)):
            errors.add('SAMPLING_CONSUMPTION_ROW_INVALID'); continue
        seen.add(row['case'])
        expected = specs[row['case']][1]
        if expected is None:
            if row['outcome'] == 'typed_accepted':
                candidates.append({'case': row['case'], 'reason': 'invalid_typed_options_accepted'})
            elif (row['outcome'] != 'typed_rejected' or row['stages'] or row['expected_output_matched'] is not None
                  or row['error_stage'] != 'typed' or row['error_sha256'] != hashlib.sha256(b'vllm.exceptions.VLLMValidationError').hexdigest()):
                errors.add('SAMPLING_CONSUMPTION_REJECTION_UNVERIFIED')
            continue
        stages = row['stages']
        if set(stages) != {'typed', 'scheduler', 'batch', 'metadata'}:
            errors.add('SAMPLING_CONSUMPTION_STAGE_MISSING'); continue
        if any(type(s) is not dict or set(s) != set(FIELDS) or
               any(type(v) not in (int, float) or not math.isfinite(v) for v in s.values()) for s in stages.values()):
            errors.add('SAMPLING_CONSUMPTION_STAGE_INVALID'); continue
        if (row['outcome'] != 'sampled' or row['slot'] is None or row['expected_output_matched'] is None
                or row['error_stage'] is not None or row['error_sha256'] is not None):
            errors.add('SAMPLING_CONSUMPTION_EXECUTION_INCOMPLETE')
        if not row['same_scheduler_params'] or any(stage != stages['typed'] for stage in stages.values()):
            candidates.append({'case': row['case'], 'reason': 'typed_options_changed_before_sampler'})
        if row['expected_output_matched'] is False:
            candidates.append({'case': row['case'], 'reason': 'sampler_output_control_mismatch'})
    if seen != set(specs):
        errors.add('SAMPLING_CONSUMPTION_CASE_MISSING')
    if not value['source_ended']:
        errors.add('SAMPLING_CONSUMPTION_SOURCE_INCOMPLETE')
    return {'component_scope_complete': not errors, 'errors': sorted(errors), 'violation_candidates': candidates,
            'admitted_worker_consumption_verified': False, 'producer_authenticated': False,
            'missing': ['actual_worker_update_states_unobserved', 'actual_model_logits_unobserved',
                        'structured_output_grammar_unobserved', 'V2_sampler_unobserved']}


RUNNER = r'''
import hashlib,importlib,json,pathlib,sys
pins,producer,cases,model=map(json.loads,sys.argv[1:])
def sources():
    return {name:hashlib.sha256(pathlib.Path(importlib.import_module(name).__file__).read_bytes()).hexdigest() for name in pins}
before=sources()
if before!=pins: raise RuntimeError('SAMPLING_CONSUMPTION_SOURCE_UNSUPPORTED')
import torch
torch.set_num_threads(1)
from vllm import EngineArgs,SamplingParams
from vllm.exceptions import VLLMValidationError
from vllm.config import set_current_vllm_config
from vllm.v1.core.sched.scheduler import Scheduler
from vllm.v1.structured_output import StructuredOutputManager
from vllm.v1.kv_cache_interface import KVCacheConfig,KVCacheGroupSpec,FullAttentionSpec
from vllm.v1.worker.gpu_input_batch import InputBatch,CachedRequestState
from vllm.v1.request import Request
from vllm.v1.sample.sampler import Sampler
cfg=EngineArgs(model=model,max_model_len=128,max_num_seqs=2,max_num_batched_tokens=128,enforce_eager=True,dtype='float32',enable_prefix_caching=False).create_engine_config()
cfg.cache_config.num_gpu_blocks=16  # EXAMPLE scheduler capacity, not measured GPU memory
vocab=cfg.model_config.get_vocab_size()
def params(p):
    return {'temperature':round(p.temperature,6),'top_p':round(p.top_p,6),'top_k':0 if p.top_k in (-1,0) or p.top_k>=vocab else p.top_k,
            'frequency_penalty':round(p.frequency_penalty,6),'presence_penalty':round(p.presence_penalty,6),'repetition_penalty':round(p.repetition_penalty,6),'allowed_tokens':len(set(p.allowed_token_ids or []))}
def batch_values(b):
    value={name:round(float(getattr(b,attr)[0]),6) for name,attr in [('temperature','temperature_cpu'),('top_p','top_p_cpu'),('frequency_penalty','frequency_penalties_cpu'),('presence_penalty','presence_penalties_cpu'),('repetition_penalty','repetition_penalties_cpu')]}
    value['top_k']=int(b.top_k_cpu[0]) if int(b.top_k_cpu[0])<vocab else 0
    value['allowed_tokens']=int((~b.allowed_token_ids_mask_cpu_tensor[0]).sum().item()) if b.has_allowed_token_ids else 0
    return value
def meta_values(m):
    def at(x,default): return round(float(x[0].item()),6) if x is not None else default
    return {'temperature':at(m.temperature,0),'top_p':at(m.top_p,1),'top_k':int(at(m.top_k,0)),
            'frequency_penalty':at(m.frequency_penalties,0) if not m.no_penalties else 0,
            'presence_penalty':at(m.presence_penalties,0) if not m.no_penalties else 0,
            'repetition_penalty':at(m.repetition_penalties,1) if not m.no_penalties else 1,
            'allowed_tokens':int((~m.allowed_token_ids_mask[0]).sum().item()) if m.allowed_token_ids_mask is not None else 0}
rows=[]
with set_current_vllm_config(cfg):
    batch=InputBatch(2,128,128,torch.device('cpu'),vocab,[16],[16],[8])
    for name,options,expected in cases:
        request_id='guard-'+name
        row={'case':name,'request_sha256':hashlib.sha256(request_id.encode()).hexdigest(),'outcome':'execution_error','stages':{},'same_scheduler_params':False,'slot':None,'expected_output_matched':None,'error_stage':None,'error_sha256':None}
        stage='typed'
        try:
            try: p=SamplingParams(max_tokens=1,**options)
            except VLLMValidationError as exc:
                row['outcome']='typed_rejected';row['error_stage']=stage;row['error_sha256']=hashlib.sha256((type(exc).__module__+'.'+type(exc).__qualname__).encode()).hexdigest();rows.append(row);continue
            if expected is None:
                row['outcome']='typed_accepted';rows.append(row);continue
            stage='scheduler'
            kv=KVCacheConfig(num_blocks=16,kv_cache_tensors=[],kv_cache_groups=[KVCacheGroupSpec(['layer'],FullAttentionSpec(block_size=16,num_kv_heads=1,head_size=64,dtype=torch.float32))])
            scheduler=Scheduler(cfg,kv,StructuredOutputManager(cfg),block_size=16)
            request=Request(request_id,[7,2,3],p,None)
            scheduler.add_request(request);scheduled=scheduler.schedule();item=scheduled.scheduled_new_reqs[0]
            row['same_scheduler_params']=item.sampling_params is p and item.req_id==request_id
            row['stages']['typed']=params(p);row['stages']['scheduler']=params(item.sampling_params)
            stage='batch'
            for old in list(batch.req_ids): batch.remove_request(old)
            batch.condense()
            state=CachedRequestState(req_id=item.req_id,prompt_token_ids=item.prompt_token_ids,mm_features=[],sampling_params=item.sampling_params,generator=None,block_ids=item.block_ids,num_computed_tokens=item.num_computed_tokens,output_token_ids=[])
            row['slot']=batch.add_request(state);batch.refresh_metadata()
            row['stages']['batch']=batch_values(batch);row['stages']['metadata']=meta_values(batch.sampling_metadata)
            logits=torch.zeros((1,vocab));logits[0,7]=4;logits[0,11]=3;logits[0,2]=2
            stage='sampler'
            sampled=Sampler()(logits,batch.sampling_metadata)
            row['expected_output_matched']=int(sampled.sampled_token_ids[0,0])==expected
            row['outcome']='sampled'
        except Exception as exc:
            row['outcome']='execution_error';row['error_stage']=stage;row['error_sha256']=hashlib.sha256((type(exc).__module__+'.'+type(exc).__qualname__).encode()).hexdigest()
        rows.append(row)
print(json.dumps({'contract':'guard-sampling-consumption-cpu/1','producer':producer,'data_origin':'EXAMPLE','sources_before':before,'sources_after':sources(),'rows':rows,'source_ended':True}))
'''


def runner_args(model):
    if type(model) is not str or not model.startswith('/') or '..' in Path(model).parts:
        raise ValueError('SAMPLING_CONSUMPTION_MODEL_PATH_INVALID')
    return [json.dumps(v) for v in (SOURCES, producer(), CASES, model)]
