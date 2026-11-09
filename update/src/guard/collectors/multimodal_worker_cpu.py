"""Actual vLLM MM worker with random micro weights; no pretrained target claim."""
from __future__ import annotations

import hashlib
import json
from . import consumption_links as links
from .multimodal_processor_model import SOURCES as HF_SOURCES

SCHEMA = links.SCHEMA
callback, auxiliary = links.callback, links.auxiliary
SOURCES = {**HF_SOURCES, 'vllm.model_executor.models.qwen2_vl': '5021855de72a2b1b04befcb2bb766eab17afd0e7892dc6d4135c8eeef8c99978'}
CONFIG = {'text_config': {'vocab_size': 32, 'hidden_size': 128, 'intermediate_size': 256, 'num_hidden_layers': 1, 'num_attention_heads': 2, 'num_key_value_heads': 1, 'max_position_embeddings': 64, 'rope_parameters': {'rope_type': 'default', 'mrope_section': [8, 12, 12]}, 'bos_token_id': 30, 'eos_token_id': 31, 'pad_token_id': 0}, 'vision_config': {'depth': 1, 'embed_dim': 16, 'hidden_size': 128, 'num_heads': 2, 'mlp_ratio': 2, 'patch_size': 2, 'spatial_merge_size': 2, 'temporal_patch_size': 2}, 'image_token_id': 3, 'video_token_id': 4, 'vision_start_token_id': 1, 'vision_end_token_id': 2}
SYNTHETIC_ROOTS = ('/tmp/guard-random-qwen-mm-linked',)
CASES = ('image-rgb', 'image-grayscale', 'image-repeat', 'text-recovery')


def producer():
    digest = hashlib.sha256(json.dumps([SOURCES, CONFIG, RUNNER, links.producer()], sort_keys=True).encode()).hexdigest()
    return {'name': 'guard-multimodal-worker-cpu', 'version': '1', 'source_sha256': digest}


def runner_args(model_dir):
    return [json.dumps(v) for v in (SOURCES, CONFIG, producer())]


def project(record):
    result = links._project(record, expected_producer=producer(), cases=CASES, multimodal=True)
    result.update(selected_pretrained_model_verified=False, pretrained_weights_loaded=False, load_format='dummy')
    result['missing'] = ['approved_Qwen2_VL_2B_snapshot_not_loaded', 'GPU_model_and_kernel_consumption_unobserved', 'native_tokenizer_reads_unattributed']
    return result


RUNNER = r'''import os
os.environ['VLLM_ENABLE_V1_MULTIPROCESSING']='0'
os.environ.pop('VLLM_CPU_KVCACHE_SPACE',None)

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
cfg.architectures=['Qwen2VLForConditionalGeneration']
model=Qwen2VLForConditionalGeneration(cfg).eval()
backend=Tokenizer(models.WordLevel({'<unk>':0,'<|vision_start|>':1,'<|vision_end|>':2,'<|image_pad|>':3,'<|video_pad|>':4,'Hello':5,'<|endoftext|>':31},unk_token='<unk>'))
backend.pre_tokenizer=pre_tokenizers.WhitespaceSplit()
tokenizer=PreTrainedTokenizerFast(tokenizer_object=backend,unk_token='<unk>',pad_token='<unk>',extra_special_tokens=['<|vision_start|>','<|vision_end|>','<|image_pad|>','<|video_pad|>'])
processor=Qwen2VLProcessor(image_processor=Qwen2VLImageProcessor(patch_size=2,temporal_patch_size=2,merge_size=2,do_resize=False),
                         video_processor=Qwen2VLVideoProcessor(patch_size=2,temporal_patch_size=2,merge_size=2,do_resize=False),tokenizer=tokenizer)

folder='/tmp/guard-random-qwen-mm-linked'
if pathlib.Path(folder).exists(): raise RuntimeError('MM_SYNTHETIC_DIRECTORY_EXISTS')
cfg.save_pretrained(folder)
processor.save_pretrained(folder)
from vllm import LLM,SamplingParams
llm=LLM(model=folder,runner='generate',load_format='dummy',max_model_len=64,max_num_seqs=1,max_num_batched_tokens=64,enforce_eager=True,
        dtype='float32',distributed_executor_backend='mp',kv_cache_memory_bytes=32*1024*1024,
        limit_mm_per_prompt={'image':1,'video':0},mm_processor_kwargs={'min_pixels':64,'max_pixels':64})
import hmac
settings=json.load(open('/guard-lifecycle/engine-callbacks/settings.json'));secret=bytes.fromhex(settings['secret'])
def fingerprint(value): return hmac.new(secret,b's:'+value.encode(),hashlib.sha256).hexdigest()
results=[];baseline=None
try:
    for case in ('image-rgb','image-grayscale','image-repeat','text-recovery'):
        prompt='Hello' if case=='text-recovery' else '<|vision_start|><|image_pad|><|vision_end|> Hello'
        request={'prompt':prompt}
        if case!='text-recovery': request['multi_modal_data']={'image':Image.new('L',(8,8),32) if case=='image-grayscale' else Image.new('RGB',(8,8),(16,32,48))}
        output=llm.generate(request,SamplingParams(max_tokens=2,temperature=0,ignore_eos=True),use_tqdm=False)[0]
        tokens=list(output.outputs[0].token_ids)
        if case=='image-rgb': baseline=tokens
        results.append({'case':case,'request_sha256':fingerprint(output.request_id),'output_tokens':len(tokens),
                        'allowed_output_matched':None,'normal_output_matched':tokens==baseline if case=='image-repeat' else None,'grammar_output_matched':None})
    if sources()!=pins: raise RuntimeError('MM_WORKER_SOURCE_CHANGED')
finally:
    print('GUARD_CAPTURE_RESULT='+json.dumps(results),flush=True)
    llm.llm_engine.engine_core.shutdown()
'''
