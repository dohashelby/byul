"""Real Qwen2-VL processor/vision/language CPU components with tiny random weights.

No pretrained model or tokenizer is downloaded. The configuration and WordLevel
tokenizer are EXAMPLE test inputs; this is not Qwen2-VL-2B deployment evidence.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA = 'guard-mm-processor-model-cpu/1'
SOURCES = {
    'transformers.models.qwen2_vl.configuration_qwen2_vl': '5906d29ee8a444b0f7c50be5ca043d92e3137943508de6fa15b4903ab20f9466',
    'transformers.models.qwen2_vl.modeling_qwen2_vl': '6872a187d6603243b4e78a1706d85e99366ef13e77a0018c1d74493b66da9b9f',
    'transformers.models.qwen2_vl.image_processing_qwen2_vl': '4e1da45f9e7e157ca08aa88243fcc1495ea6a333e40e0c5035ef17ef2747a97d',
    'transformers.models.qwen2_vl.processing_qwen2_vl': '1ae132eda985b3f1a6c9ecc11d4fe56fbb19cd04ebb7c6ed37421d96585c9780',
    'transformers.models.qwen2_vl.video_processing_qwen2_vl': '810143b5e06c17ba9ec43962c991e4946bac8ad227d8299a92db3d990edaf1e1',
}
CONFIG = {'text_config': {'vocab_size': 32, 'hidden_size': 16, 'intermediate_size': 32, 'num_hidden_layers': 1,
                         'num_attention_heads': 2, 'num_key_value_heads': 1, 'max_position_embeddings': 64,
                         'rope_parameters': {'rope_type': 'default', 'mrope_section': [2, 1, 1]},
                         'bos_token_id': 30, 'eos_token_id': 31, 'pad_token_id': 0},
          'vision_config': {'depth': 1, 'embed_dim': 16, 'hidden_size': 16, 'num_heads': 2, 'mlp_ratio': 2,
                            'patch_size': 2, 'spatial_merge_size': 2, 'temporal_patch_size': 2},
          'image_token_id': 3, 'video_token_id': 4, 'vision_start_token_id': 1, 'vision_end_token_id': 2}
CASES = ('image-rgb', 'image-grayscale', 'token-mismatch', 'recovery')


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def producer():
    return {'name': 'guard-mm-processor-model-cpu', 'version': '1', 'source_sha256': _hash([SOURCES, CONFIG, RUNNER])}


def project(record):
    keys = {'schema', 'producer', 'data_origin', 'sources_before', 'sources_after', 'config_identity', 'target_model',
            'pretrained_weights_loaded', 'source_ended', 'rows'}
    if (type(record) is not dict or set(record) != keys or record['schema'] != SCHEMA or record['producer'] != producer()
            or record['data_origin'] != 'EXAMPLE' or record['sources_before'] != SOURCES or record['sources_after'] != SOURCES
            or record['config_identity'] != _hash(CONFIG) or record['target_model'] is not None
            or record['pretrained_weights_loaded'] is not False or type(record['source_ended']) is not bool
            or type(record['rows']) is not list or len(record['rows']) > len(CASES)):
        raise ValueError('MM_PROCESSOR_MODEL_ENVELOPE_INVALID')
    errors, seen, candidates = set(), set(), []
    row_keys = {'case', 'processor_returned', 'pixel_values_shape', 'image_grid_shape', 'image_tokens', 'vision_shape',
                'language_shape', 'embedding_values_match', 'logits_shape', 'logits_finite', 'recovery_logits_match', 'outcome', 'error_sha256'}
    for row in record['rows']:
        if (type(row) is not dict or set(row) != row_keys or type(row.get('case')) is not str or row['case'] not in CASES or row['case'] in seen
                or row.get('outcome') not in ('returned', 'token_feature_mismatch', 'execution_error')
                or any(type(row.get(k)) is not bool for k in ('processor_returned', 'embedding_values_match', 'logits_finite'))
                or (row['recovery_logits_match'] is not None and type(row['recovery_logits_match']) is not bool)
                or type(row.get('image_tokens')) is not int or row['image_tokens'] < 0
                or any(type(row.get(k)) is not list or len(row[k]) > 3 or any(type(v) is not int or v < 0 for v in row[k])
                       for k in ('pixel_values_shape', 'image_grid_shape', 'vision_shape', 'language_shape', 'logits_shape'))
                or (row['error_sha256'] is not None and (type(row['error_sha256']) is not str or len(row['error_sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['error_sha256'])))):
            errors.add('MM_PROCESSOR_MODEL_ROW_INVALID'); continue
        seen.add(row['case'])
        if not row['processor_returned'] or row['pixel_values_shape'] != [16, 24] or row['image_grid_shape'] != [1, 3]:
            errors.add('MM_PROCESSOR_RESULT_UNVERIFIED')
        if row['case'] == 'token-mismatch':
            if row['outcome'] == 'returned' or row['language_shape']:
                candidates.append({'case': row['case'], 'reason': 'mismatched_image_tokens_reached_language_model'})
            if (row['outcome'] != 'token_feature_mismatch' or row['image_tokens'] != 3 or row['vision_shape'] != [4, 16]
                    or row['error_sha256'] != hashlib.sha256(b'builtins.ValueError').hexdigest()):
                errors.add('MM_MODEL_REJECTION_UNVERIFIED')
            continue
        if row['outcome'] != 'returned' or row['error_sha256'] is not None:
            errors.add('MM_PROCESSOR_MODEL_EXECUTION_INCOMPLETE'); continue
        if (row['image_tokens'] != 4 or row['vision_shape'] != [4, 16] or row['language_shape'] != [1, 7, 16]
                or row['logits_shape'] != [1, 7, 32] or not row['logits_finite']):
            errors.add('MM_PROCESSOR_MODEL_SHAPE_INCOMPLETE')
        if not row['embedding_values_match']:
            candidates.append({'case': row['case'], 'reason': 'vision_features_changed_before_language_consumption'})
        if row['case'] == 'recovery' and row['recovery_logits_match'] is not True:
            candidates.append({'case': row['case'], 'reason': 'post_rejection_normal_control_changed'})
    if seen != set(CASES):
        errors.add('MM_PROCESSOR_MODEL_CASE_MISSING')
    if not record['source_ended']:
        errors.add('MM_PROCESSOR_MODEL_SOURCE_INCOMPLETE')
    return {'processor_model_component_complete': not errors, 'errors': sorted(errors), 'violation_candidates': candidates,
            'selected_pretrained_model_verified': False, 'vllm_worker_verified': False, 'GPU_verified': False,
            'missing': ['approved_Qwen2_VL_2B_snapshot_not_loaded', 'actual_vllm_MM_worker_transport_not_run']}


RUNNER = r'''
import hashlib,importlib,json,pathlib,sys
pins,config,producer=map(json.loads,sys.argv[1:])
def sources(): return {n:hashlib.sha256(pathlib.Path(importlib.import_module(n).__file__).read_bytes()).hexdigest() for n in pins}
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
before=sources()
config_identity=digest(config)
if before!=pins: raise RuntimeError('MM_PROCESSOR_MODEL_SOURCE_UNSUPPORTED')
import torch
from PIL import Image
from tokenizers import Tokenizer,models,pre_tokenizers
from transformers import Qwen2VLConfig,Qwen2VLForConditionalGeneration,Qwen2VLImageProcessor,Qwen2VLVideoProcessor,Qwen2VLProcessor,PreTrainedTokenizerFast
torch.set_num_threads(1);torch.manual_seed(0)
cfg=Qwen2VLConfig(**config);cfg._attn_implementation='eager'
model=Qwen2VLForConditionalGeneration(cfg).eval()
backend=Tokenizer(models.WordLevel({'<unk>':0,'<|vision_start|>':1,'<|vision_end|>':2,'<|image_pad|>':3,'<|video_pad|>':4,'Hello':5,'<|endoftext|>':31},unk_token='<unk>'))
backend.pre_tokenizer=pre_tokenizers.WhitespaceSplit()
tokenizer=PreTrainedTokenizerFast(tokenizer_object=backend,unk_token='<unk>',pad_token='<unk>',extra_special_tokens=['<|vision_start|>','<|vision_end|>','<|image_pad|>','<|video_pad|>'])
processor=Qwen2VLProcessor(image_processor=Qwen2VLImageProcessor(patch_size=2,temporal_patch_size=2,merge_size=2,do_resize=False),
                         video_processor=Qwen2VLVideoProcessor(patch_size=2,temporal_patch_size=2,merge_size=2,do_resize=False),tokenizer=tokenizer)
seen={}
def vision(module,args,output): seen['vision']=output.pooler_output
def language(module,args,kwargs): seen['language']=kwargs.get('inputs_embeds')
model.model.visual.register_forward_hook(vision)
model.model.language_model.register_forward_pre_hook(language,with_kwargs=True)
rows=[];baseline=None
for name in ('image-rgb','image-grayscale','token-mismatch','recovery'):
    seen.clear()
    row={'case':name,'processor_returned':False,'pixel_values_shape':[],'image_grid_shape':[],'image_tokens':0,'vision_shape':[],
         'language_shape':[],'embedding_values_match':False,'logits_shape':[],'logits_finite':False,'recovery_logits_match':None,
         'outcome':'execution_error','error_sha256':None}
    try:
        image=Image.new('L',(8,8),32) if name=='image-grayscale' else Image.new('RGB',(8,8),(16,32,48))
        inputs=processor(text=['<|vision_start|><|image_pad|><|vision_end|> Hello'],images=[image],return_tensors='pt')
        row.update(processor_returned=True,pixel_values_shape=list(inputs['pixel_values'].shape),image_grid_shape=list(inputs['image_grid_thw'].shape))
        if name=='token-mismatch':
            first=int((inputs['input_ids'][0]==3).nonzero()[0,0])
            keep=[i for i in range(inputs['input_ids'].shape[1]) if i!=first]
            for key in ('input_ids','attention_mask','mm_token_type_ids'): inputs[key]=inputs[key][:,keep]
        row['image_tokens']=int((inputs['input_ids']==3).sum())
        with torch.inference_mode(): output=model(**inputs,use_cache=False)
        row.update(outcome='returned',logits_shape=list(output.logits.shape),logits_finite=bool(torch.isfinite(output.logits).all()),
                   embedding_values_match=torch.equal(seen['language'][inputs['input_ids']==3],seen['vision']))
        if name=='image-rgb': baseline=output.logits.clone()
        if name=='recovery': row['recovery_logits_match']=torch.equal(output.logits,baseline)
    except Exception as exc:
        row['error_sha256']=hashlib.sha256((type(exc).__module__+'.'+type(exc).__qualname__).encode()).hexdigest()
        row['outcome']='token_feature_mismatch' if isinstance(exc,ValueError) and str(exc).startswith('Image features and image tokens do not match,') else 'execution_error'
    row['vision_shape']=list(seen['vision'].shape) if 'vision' in seen else []
    row['language_shape']=list(seen['language'].shape) if 'language' in seen else []
    rows.append(row)
print(json.dumps({'schema':'guard-mm-processor-model-cpu/1','producer':producer,'data_origin':'EXAMPLE','sources_before':before,'sources_after':sources(),
                  'config_identity':config_identity,'target_model':None,'pretrained_weights_loaded':False,'source_ended':True,'rows':rows}))
'''


def runner_args():
    return [json.dumps(v) for v in (SOURCES, CONFIG, producer())]
