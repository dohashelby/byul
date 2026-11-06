"""P2 runner 최종 소비 생산자의 엔진 쪽 관측(개발 담당, 2단계). REQ-08·09·10의 입력·옵션이 실제로 어디까지 갔는가.

vLLM 0.31.0 세 단계를 원 소스 해시로 고정해 감싼다.
- API 서버 `InputProcessor.process_inputs`(요청 ID), `_validate_model_input`(프롬프트 길이·최대 토큰 ID),
  `_validate_params`(생성 옵션). 예외는 거부다.
- worker `GPUModelRunner._update_states`: 새 요청이 runner에 도착한 시점의 프롬프트·옵션
- worker `GPUModelRunner._sample`: sampler가 실제로 쓴 요청별 값. GPU 텐서가 아니라 InputBatch의 CPU 쪽 배열에서 읽는다(동기화 없음).

V1 runner 경로다(CPU, GPU에서는 `VLLM_USE_V2_MODEL_RUNNER=0`). V2 runner 경로는 별도 관측이 필요하다.
옵션 값은 정수로 바꿔 남긴다(온도·top_p는 천분율). 프롬프트·stop 문자열·logit bias 값은 남기지 않고 개수만 남긴다.
"""
from __future__ import annotations

from . import vllm_cache_callback as cache

INPUT = 'vllm.v1.engine.input_processor'
RUNNER = 'vllm.v1.worker.gpu_model_runner'
TARGETS = {INPUT: ('InputProcessor.process_inputs', 'InputProcessor._validate_model_input', 'InputProcessor._validate_params'),
           RUNNER: ('GPUModelRunner._update_states', 'GPUModelRunner._sample')}
SCHEMA = 'guard-vllm-consumption-callback/1'
MAX_BATCH = 256
FIELDS = ('temperature_milli', 'top_p_milli', 'top_k', 'max_tokens', 'logprobs', 'allowed_tokens', 'bad_words', 'logit_bias')


def _milli(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(round(number * 1000)) if 0 <= number < 1e9 else None


def _count(value):
    return len(value) if type(value) in (list, tuple, dict, set) else 0 if value is None else None


def _top_k(value, vocab_size=None):
    if type(value) is not int:
        return None
    # vLLM stores disabled top-k as vocab_size in InputBatch, and as -1/0 in SamplingParams.
    if value in (-1, 0) or type(vocab_size) is int and value >= vocab_size > 0:
        return 0
    return cache._uint(value)


def params_summary(params):
    """SamplingParams → 비교용 정수 값. 원문 문자열은 남기지 않는다."""
    fields = cache.base.attrs(params)
    if not fields:
        return None
    logprobs = fields.get('logprobs')
    allowed = fields.get('allowed_token_ids')
    return {'temperature_milli': _milli(fields.get('temperature')), 'top_p_milli': _milli(fields.get('top_p')),
            'top_k': _top_k(fields.get('top_k')),
            'max_tokens': cache._uint(fields.get('max_tokens')), 'logprobs': 0 if logprobs is None else cache._uint(logprobs),
            'allowed_tokens': len(set(allowed)) if type(allowed) in (list, tuple) and all(type(v) is int for v in allowed) else _count(allowed),
            'bad_words': _count(fields.get('bad_words')),
            'logit_bias': _count(fields.get('logit_bias'))}


def _prompt(ids):
    if type(ids) not in (list, tuple) or len(ids) > 1 << 20 or any(type(v) is not int for v in ids[:4096]):
        return None, None
    return len(ids), (max(ids) if ids else 0)


class ConsumptionObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    schema = SCHEMA
    profile = 'source-bound-consumption-callback-candidate-v1'

    def _parent_request(self):
        frame = cache._ACTIVE.get()
        return frame.get('request_sha256') if frame and frame.get('observer') is self else None

    def _batch(self, receiver):
        batch = cache.base.attrs(cache.base.attrs(receiver).get('input_batch'))
        ids = batch.get('_req_ids')
        if type(ids) is not list or len(ids) > MAX_BATCH:
            self.gaps.add('CONSUMPTION_CALLBACK_BATCH_SHAPE_UNAVAILABLE'); return None
        def at(name, index, scale=None):
            try:
                value = batch.get(name)[index]
                return _milli(value) if scale else cache._uint(int(value)) if value is not None and value == int(value) else None
            except (TypeError, ValueError, OverflowError, IndexError, KeyError):
                return None
        allowed = batch.get('allowed_token_ids_mask_cpu_tensor'); allowed_requests = batch.get('has_allowed_token_ids')
        bad = batch.get('bad_words_token_ids'); logprobs = batch.get('num_logprobs')
        rows = []
        for index, req in enumerate(ids):
            if req is None:
                continue
            restricted = 0 if type(allowed_requests) is set and req not in allowed_requests else None
            if type(allowed_requests) is set and req in allowed_requests and allowed is not None:
                try:
                    # 허용 목록이 있으면 mask 행에 막힌(True) 자리가 있다. 개수만 센다.
                    restricted = int((~allowed[index]).sum().item()) if hasattr(allowed[index], 'sum') else None
                except Exception:
                    restricted = None
            top_k = at('top_k_cpu', index)
            rows.append({'request_sha256': self.fingerprint(req), 'temperature_milli': at('temperature_cpu', index, True),
                         'top_p_milli': at('top_p_cpu', index, True), 'top_k': _top_k(top_k, batch.get('vocab_size')),
                         'logprobs': (cache._uint(logprobs.get(req)) or 0) if type(logprobs) is dict else None,
                         'allowed_tokens': restricted,
                         'bad_words': len(bad.get(index) or ()) if type(bad) is dict else None})
        return rows

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        if point == 'InputProcessor.process_inputs':
            return {} if returned else {'request_sha256': self.fingerprint(args[1] if len(args) > 1 else kwargs.get('request_id'))}
        if point == 'InputProcessor._validate_model_input':
            if returned:
                return {}
            prompt = args[1] if len(args) > 1 else kwargs.get('prompt_input')
            length, top = _prompt(prompt.get('prompt_token_ids') if type(prompt) is dict else None)
            return {'request_sha256': self._parent_request(), 'prompt_tokens': length, 'max_token_id': top}
        if point == 'InputProcessor._validate_params':
            if returned:
                return {}
            return {'request_sha256': self._parent_request(), 'params': params_summary(args[1] if len(args) > 1 else kwargs.get('params'))}
        if point == 'GPUModelRunner._update_states':
            if returned:
                return {}
            output = cache.base.attrs(args[1] if len(args) > 1 else kwargs.get('scheduler_output'))
            new = output.get('scheduled_new_reqs')
            if type(new) is not list or len(new) > MAX_BATCH:
                self.gaps.add('CONSUMPTION_CALLBACK_BATCH_SHAPE_UNAVAILABLE'); return {'received': None}
            rows = []
            for item in new:
                fields = cache.base.attrs(item)
                length, top = _prompt(fields.get('prompt_token_ids'))
                rows.append({'request_sha256': self.fingerprint(fields.get('req_id')), 'prompt_tokens': length, 'max_token_id': top,
                             'params': params_summary(fields.get('sampling_params'))})
            return {'received': rows}
        if point == 'GPUModelRunner._sample':
            return {} if returned else {'applied': self._batch(receiver)}
        return {}
