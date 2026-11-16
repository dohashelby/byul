"""Initial V1 supported sampling metadata, without reading GPU/token values.

Helpers for the existing SlotObserver. Object lineage and source-call outcomes
are partial facts; tensor identities are never copy completion or value truth.
"""
from __future__ import annotations
import math
from . import vllm_cache_callback as cache

BATCH = 'vllm.v1.worker.gpu_input_batch'
RUNNER = 'vllm.v1.worker.gpu_model_runner'
SAMPLER = 'vllm.v1.sample.sampler'
QWEN = 'vllm.model_executor.models.qwen2'
LLAMA = 'vllm.model_executor.models.llama'
TARGETS = {BATCH: ('InputBatch.make_sampling_metadata',), RUNNER: ('GPUModelRunner._prepare_sampling',),
    QWEN: ('Qwen2ForCausalLM.sample',), LLAMA: ('LlamaForCausalLM.sample',),
    SAMPLER: ('Sampler.forward', 'Sampler.sample', 'Sampler.apply_temperature')}
SOURCES = {BATCH: '89770d22d9398a061d96e4aa547a8afbacaf0eeff589e1dcf3df45503a4ab40c',
    RUNNER: '1ac3fb18b2adad3609dcf58a1656d6fdd4a568eea6740258e00f6a43c7d4b813',
    SAMPLER: '9082d291c73104024f5658d5858393aedc192d3d4a7ebfd3d415a249c4d4f019',
    QWEN: '10015a92c8ed412de2cb33ade19067fd71f628a7ac5e45792feced88b60e37c6',
    LLAMA: '723d0728fd985f9a9b0197fc888f6e74de65ff2c84ab15c18a17d6d2b6016144'}
POINT_MODULES = {point: module for module, points in TARGETS.items() for point in points}
BATCH_POINT, RUNNER_POINT = TARGETS[BATCH][0], TARGETS[RUNNER][0]
MODEL_POINTS = frozenset(TARGETS[QWEN] + TARGETS[LLAMA])
FORWARD_POINT, SELECTION_POINT, TEMPERATURE_POINT = TARGETS[SAMPLER]
MAX_REQUESTS = 256
SCHEMA = 'guard-vllm-slot-callback/1'
# Adapter stimulus only, within REQ-18's existing supported restriction question.
STIMULI = {'restricted': {'temperature': 1.0, 'top_p': 1.0, 'top_k': 1, 'seed': 0},
           'reused': {'temperature': 1.0, 'top_p': 1.0, 'top_k': -1, 'seed': 0}}
FLAGS = ('all_greedy', 'all_random', 'no_top_p', 'no_top_k', 'no_penalties')
TENSORS = ('temperature', 'top_p', 'top_k')


def _milli(value):
    if type(value) not in (int, float) or not 0 <= value < 1e6 or not math.isfinite(value):
        return None
    return int(round(value * 1000))


def restrictions(params):
    fields = cache.base.attrs(params)
    top_k = fields.get('top_k')
    return {'temperature_milli': _milli(fields.get('temperature')), 'top_p_milli': _milli(fields.get('top_p')),
        'temperature_hex': float(fields.get('temperature')).hex() if _milli(fields.get('temperature')) is not None else None,
        'top_p_hex': float(fields.get('top_p')).hex() if _milli(fields.get('top_p')) is not None else None,
        'top_k': 0 if type(top_k) is int and top_k in (-1, 0) else cache._uint(top_k),
        'allowed_token_ids_supported': False,
        'allowed_token_ids_present': fields.get('allowed_token_ids') is not None}


def restricted(params):
    value = restrictions(params)
    if any(value[key] is None for key in ('temperature_milli', 'top_p_milli', 'top_k')):
        return None
    fields = cache.base.attrs(params)
    return fields.get('temperature') == 0 or fields.get('top_p') < 1 or value['top_k'] > 0


def _child(owner, name):
    fields = cache.base.attrs(owner)
    modules = fields.get('_modules')
    return modules.get(name) if type(modules) is dict and name in modules else fields.get(name)


def _metadata(observer, value):
    fields = cache.base.attrs(value)
    return {'metadata_sha256': observer._batch_identity(value),
        'flags': {key: fields.get(key) if type(fields.get(key)) is bool else None for key in FLAGS},
        'tensor_sha256': {key: observer._batch_identity(fields.get(key)) for key in TENSORS},
        'gpu_values_verified': False, 'copy_completion_verified': False}


def snapshot(observer, point, args, kwargs, result=None, returned=False):
    owner = args[0] if args else kwargs.get('self')
    common = {'sampling_stage': point, 'gpu_values_verified': False, 'copy_completion_verified': False,
              'full_sampling_lineage_verified': False}
    if point == 'InputBatch.make_sampling_metadata':
        skip = args[2] if len(args) > 2 else kwargs.get('skip_copy', False)
        return {**common, 'batch_sha256': observer._batch_identity(owner), 'slot_mapping': observer._mapping(owner),
            'skip_copy': skip if type(skip) is bool else None,
            'sampling_metadata': _metadata(observer, result) if returned else None}
    if point == 'GPUModelRunner._prepare_sampling':
        fields = cache.base.attrs(owner)
        batch = fields.get('input_batch')
        rows = observer._mapping(batch)
        requests = fields.get('requests')
        declared = [] if rows is not None and type(requests) is dict and len(requests) <= MAX_REQUESTS else None
        mapping = cache.base.attrs(batch).get('req_id_to_index')
        if declared is not None and type(mapping) is dict:
            for req_id, index in mapping.items():
                request = requests.get(req_id)
                declared.append({'request_sha256': observer.fingerprint(req_id), 'slot': index,
                    'restrictions': restrictions(cache.base.attrs(request).get('sampling_params'))})
        return {**common, 'runner_sha256': observer._batch_identity(owner), 'batch_sha256': observer._batch_identity(batch),
            'model_sha256': observer._batch_identity(fields.get('model')), 'slot_mapping': rows,
            'declared_restrictions': declared, 'sampling_metadata': _metadata(observer, result) if returned else None}
    if point in {'Qwen2ForCausalLM.sample', 'LlamaForCausalLM.sample'}:
        metadata = args[2] if len(args) > 2 else kwargs.get('sampling_metadata')
        return {**common, 'model_sha256': observer._batch_identity(owner), 'sampler_sha256': observer._batch_identity(_child(owner, 'sampler')),
            'sampling_metadata': _metadata(observer, metadata)}
    if point in {'Sampler.forward', 'Sampler.sample'}:
        metadata = args[2] if len(args) > 2 else kwargs.get('sampling_metadata')
        return {**common, 'sampler_sha256': observer._batch_identity(owner), 'sampling_metadata': _metadata(observer, metadata)}
    if point == 'Sampler.apply_temperature':
        temperature = args[2] if len(args) > 2 else kwargs.get('temp')
        return {**common, 'sampler_sha256': observer._batch_identity(owner), 'temperature_tensor_sha256': observer._batch_identity(temperature)}
    return {**common, 'sampling_stage': 'unsupported'}
