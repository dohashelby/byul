"""REQ-09 typed MM embedding -> actual V1 worker gather method CPU support.

All embeddings, request state and scheduling collaborators are EXAMPLE. No
processor/model is selected. In particular a text model is never labelled MM.
The original gather implementation, typed feature and placeholder are reused.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

CONTRACT = 'guard-mm-gather-cpu/1'
SOURCES = {
    'vllm.multimodal.parse': 'deb26b02631df848aa6562ffd0ff2a4430d827957c79cf3d9d0b01d75ab584c5',
    'vllm.multimodal.inputs': '3ce5d3cc5bedf0343d75e1a7cfbca10a0061b960de56a9fb6465cde0f14bc8b9',
    'vllm.v1.worker.gpu_model_runner': 'bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a',
}
CASES = ('embeddings', 'masked', 'partial-window', 'missing-cache', 'hidden-mismatch', 'text-default')
MASKS = {'embeddings': [False, True, True, False], 'masked': [False, True, False, True, False],
         'partial-window': [True, True], 'text-default': [False, False, False, False]}


def producer():
    return {'name': 'guard-mm-gather-cpu', 'version': '1', 'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


def project(value):
    if (type(value) is not dict or set(value) != {'contract', 'producer', 'data_origin', 'sources_before', 'sources_after', 'rows', 'source_ended', 'model_binding'}
            or value['contract'] != CONTRACT or value['producer'] != producer() or value['data_origin'] != 'EXAMPLE'
            or value['model_binding'] is not None or value['sources_before'] != SOURCES or value['sources_after'] != SOURCES
            or type(value['source_ended']) is not bool or type(value['rows']) is not list or len(value['rows']) > len(CASES)):
        raise ValueError('MM_GATHER_ENVELOPE_INVALID')
    seen, errors, candidates = set(), set(), []
    for row in value['rows']:
        if (type(row) is not dict or set(row) != {'case', 'outcome', 'mask', 'embedding_shapes', 'reference_matches', 'error_sha256'}
                or type(row.get('case')) is not str or row['case'] not in CASES or row['case'] in seen
                or row.get('outcome') not in ('gathered', 'parser_rejected', 'cache_miss', 'execution_error')
                or type(row.get('mask')) is not list or len(row['mask']) > 8 or any(type(v) is not bool for v in row['mask'])
                or type(row.get('embedding_shapes')) is not list or len(row['embedding_shapes']) > 1
                or any(type(s) is not list or len(s) != 2 or any(type(i) is not int or i < 0 for i in s) for s in row['embedding_shapes'])
                or type(row.get('reference_matches')) is not bool
                or (row['error_sha256'] is not None and (type(row['error_sha256']) is not str or len(row['error_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['error_sha256'])))):
            errors.add('MM_GATHER_ROW_INVALID'); continue
        name = row['case']; seen.add(name)
        if name in MASKS:
            if row['outcome'] != 'gathered' or row['error_sha256'] is not None:
                errors.add('MM_GATHER_EXECUTION_INCOMPLETE'); continue
            expected_shapes = [] if name == 'text-default' else [[2, 8]]
            if row['mask'] != MASKS[name] or row['embedding_shapes'] != expected_shapes or not row['reference_matches']:
                candidates.append({'case': name, 'reason': 'typed_embedding_gather_mismatch'})
        else:
            expected = 'parser_rejected' if name == 'hidden-mismatch' else 'cache_miss'
            error_type = b'builtins.ValueError' if name == 'hidden-mismatch' else b'builtins.RuntimeError'
            if row['outcome'] == 'gathered':
                candidates.append({'case': name, 'reason': 'invalid_embedding_or_missing_cache_gathered'})
            elif (row['outcome'] != expected or row['mask'] or row['embedding_shapes']
                  or row['error_sha256'] != hashlib.sha256(error_type).hexdigest()):
                errors.add('MM_GATHER_REJECTION_UNVERIFIED')
    if seen != set(CASES):
        errors.add('MM_GATHER_CASE_MISSING')
    if not value['source_ended']:
        errors.add('MM_GATHER_SOURCE_INCOMPLETE')
    return {'component_scope_complete': not errors, 'errors': sorted(errors), 'violation_candidates': candidates,
            'selected_processor_worker_complete': False, 'admitted_worker_consumption_verified': False,
            'missing': ['selected_immutable_MM_model_and_processor_missing', 'raw_media_processor_apply_unobserved',
                        'actual_worker_binding_missing', 'actual_model_inputs_embeds_consumption_unobserved']}


RUNNER = r'''
import hashlib,importlib,json,pathlib,sys,types
pins,producer=json.loads(sys.argv[1]),json.loads(sys.argv[2])
def sources(): return {name:hashlib.sha256(pathlib.Path(importlib.import_module(name).__file__).read_bytes()).hexdigest() for name in pins}
before=sources()
if before!=pins: raise RuntimeError('MM_GATHER_SOURCE_UNSUPPORTED')
import torch
torch.set_num_threads(1)
from vllm.multimodal.parse import MultiModalDataParser
from vllm.multimodal.inputs import MultiModalFeatureSpec,PlaceholderRange
from vllm.v1.worker.gpu_model_runner import GPUModelRunner
rows=[]
for name in ('embeddings','masked','partial-window','missing-cache','hidden-mismatch','text-default'):
    row={'case':name,'outcome':'execution_error','mask':[],'embedding_shapes':[],'reference_matches':False,'error_sha256':None}
    stage='parser'
    try:
        length=3 if name=='partial-window' else 2
        hidden=7 if name=='hidden-mismatch' else 8
        raw=torch.arange(length*hidden,dtype=torch.float32).reshape(1,length,hidden)
        parsed=MultiModalDataParser(expected_hidden_size=8).parse_mm_data({'image':raw})
        tensor=parsed['image'].get(0)
        request_id='guard-mm-'+name
        mask=torch.tensor([True,False,True]) if name=='masked' else None
        position=PlaceholderRange(offset=1,length=3 if name=='masked' else length,is_embed=mask)
        feature=MultiModalFeatureSpec(data=None,modality='image',identifier='guard-mm-feature',mm_position=position)
        # Explicit CPU collaborators; no worker constructor, target, model or native admission.
        runner=GPUModelRunner.__new__(GPUModelRunner)
        runner.input_batch=types.SimpleNamespace(req_ids=[request_id])
        computed=2 if name=='partial-window' else 0
        runner.requests={request_id:types.SimpleNamespace(num_computed_tokens=computed,mm_features=[] if name=='text-default' else [feature])}
        runner.encoder_cache={} if name=='missing-cache' else {'guard-mm-feature':tensor}
        runner.is_multimodal_pruning_enabled=False;runner.uses_mrope=False
        count=2 if name=='partial-window' else 5 if name=='masked' else 4
        scheduled=types.SimpleNamespace(total_num_scheduled_tokens=count,num_scheduled_tokens={request_id:count})
        stage='gather'
        gathered,positions=runner._gather_mm_embeddings(scheduled)
        expected=tensor[1:3] if name=='partial-window' else tensor
        row.update(outcome='gathered',mask=positions.tolist(),embedding_shapes=[list(t.shape) for t in gathered],
                   reference_matches=(not gathered if name=='text-default' else len(gathered)==1 and torch.equal(gathered[0],expected) and gathered[0].untyped_storage().data_ptr()==tensor.untyped_storage().data_ptr()))
    except Exception as exc:
        row['error_sha256']=hashlib.sha256((type(exc).__module__+'.'+type(exc).__qualname__).encode()).hexdigest()
        row['outcome']='parser_rejected' if stage=='parser' and isinstance(exc,ValueError) and str(exc).startswith('Image embedding hidden dimension mismatch:') else 'cache_miss' if name=='missing-cache' and stage=='gather' and isinstance(exc,RuntimeError) and str(exc).startswith('Encoder cache miss for ') else 'execution_error'
    rows.append(row)
print(json.dumps({'contract':'guard-mm-gather-cpu/1','producer':producer,'data_origin':'EXAMPLE','sources_before':before,
 'sources_after':sources(),'rows':rows,'source_ended':True,'model_binding':None}))
'''


def runner_args():
    return [json.dumps(SOURCES), json.dumps(producer())]
