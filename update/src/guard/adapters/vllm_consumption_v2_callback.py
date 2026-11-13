"""P2 runner 최종 소비의 V2 runner 경로 관측(개발 담당, 2단계). REQ-08·09·10.

vLLM 0.31.0 V2 model runner(`vllm.v1.worker.gpu.model_runner`, GPU 기본 경로)의 두 지점을 원 소스 해시로 고정해 감싼다.
API 쪽(InputProcessor) 관측은 V1 관측기(`vllm_consumption_callback`)를 그대로 쓴다.
- `GPUModelRunner.add_requests`: 새 요청이 runner에 도착한 시점의 프롬프트·옵션(V1 `_update_states`와 같은 값)
- `GPUModelRunner.sample`: sampler가 이번 단계에 쓰는 요청별 값. `Sampler.sampling_states`와 logits processor 상태의
  CPU 원본 배열(`UvaBackedTensor.np`)을 `input_batch.idx_mapping_np` 자리로 읽는다. GPU 동기화를 하지 않으며
  GPU kernel이 그 값을 소비했다는 증명은 아니다.

V1과 다른 점:
- logit bias 개수(`LogitBiasState.num_logit_bias`)를 sampler 단계에서 읽을 수 있다.
- 허용 token 목록은 GPU 쪽 배열에만 있고 CPU에는 개수(중복 포함)만 있다. runner 도착 때 본 같은 요청의
  (원 개수, 중복 제외 개수)를 기억했다가, sampler 자리의 개수가 원 개수와 같을 때만 중복 제외 개수로 낸다.
  다르면 읽은 원 개수를 그대로 내어 비교에서 드러나게 한다.
- `--lab-kernel-checks`의 graph replay 관측이 같은 `GPUModelRunner.sample`을 감싸므로, 그 세션에서는 이 관측을 설치하지 않는다.
"""
from __future__ import annotations

from .vllm_observer_pin_rows import candidate_sources, layout

from . import vllm_cache_callback as cache
from . import vllm_consumption_callback as consumption

RUNNER = 'vllm.v1.worker.gpu.model_runner'
TARGETS = {RUNNER: ('GPUModelRunner.add_requests', 'GPUModelRunner.sample')}
# REQ-18 V2·graph replay 관측과 같은 검토 소스(vLLM 0.31.0 이미지, 107,597 bytes).
SOURCES = {RUNNER: '725c47114e148ade3a26b4cc9c5f7295588fdd387afc3cb5fdb2d16513a1bb5c'}
SCHEMA = 'guard-vllm-consumption-v2-callback/1'
MAX_REMEMBERED = 4096
NO_LOGPROBS = -1


def _at(holder, index):
    """UvaBackedTensor의 CPU numpy 배열에서 한 자리. 없거나 읽지 못하면 None."""
    try:
        value = getattr(holder, 'np')[index]
        number = float(value)
    except Exception:
        return None
    return number


def _int_at(holder, index):
    number = _at(holder, index)
    return int(number) if number is not None and -2**31 <= number < 2**63 and number == int(number) else None


class ConsumptionV2Observer(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-consumption-v2-callback-candidate-v1'

    def _remember(self, request, allowed):
        memory = self.__dict__.setdefault('_allowed', {})
        if type(allowed) in (list, tuple) and all(type(v) is int for v in allowed):
            if len(memory) >= MAX_REMEMBERED:
                memory.clear(); self.gaps.add('CONSUMPTION_V2_ALLOWED_MEMORY_RESET')
            memory[request] = (len(allowed), len(set(allowed)))

    def _received(self, output):
        new = cache.base.attrs(output).get('scheduled_new_reqs')
        if type(new) is not list or len(new) > consumption.MAX_BATCH:
            self.gaps.add('CONSUMPTION_CALLBACK_BATCH_SHAPE_UNAVAILABLE'); return None
        rows = []
        for item in new:
            fields = cache.base.attrs(item)
            ids = fields.get('prompt_token_ids')
            length, top = consumption._prompt(ids if ids is not None else fields.get('prefill_token_ids'))
            request = self.fingerprint(fields.get('req_id'))
            params = fields.get('sampling_params')
            self._remember(request, cache.base.attrs(params).get('allowed_token_ids'))
            rows.append({'request_sha256': request, 'prompt_tokens': length, 'max_token_id': top,
                         'params': consumption.params_summary(params)})
        return rows

    def _applied(self, runner, input_batch):
        fields = cache.base.attrs(runner)
        sampler = cache.base.attrs(fields.get('sampler'))
        states = sampler.get('sampling_states')
        mapping = cache.base.attrs(fields.get('req_states')).get('index_to_req_id')
        slots = cache.base.attrs(input_batch).get('idx_mapping_np')
        try:
            slots = [int(v) for v in slots]
        except Exception:
            slots = None
        if states is None or type(mapping) is not dict or slots is None or len(slots) > consumption.MAX_BATCH:
            self.gaps.add('CONSUMPTION_CALLBACK_BATCH_SHAPE_UNAVAILABLE'); return None
        processors = sampler.get('logits_processors') or ()
        profile = layout(self, RUNNER)
        bias = (sampler.get(profile['bias_attribute']) if 'bias_attribute' in profile else
                next((p for p in processors if hasattr(p, 'num_allowed_token_ids')), None))
        bad = (sampler.get(profile['bad_words_attribute']) if 'bad_words_attribute' in profile else
               next((p for p in processors if hasattr(p, 'num_bad_words')), None))
        vocab = getattr(states, 'vocab_size', None)
        memory = self.__dict__.get('_allowed', {})
        rows = []
        for slot in slots:
            req = mapping.get(slot)
            if req is None:
                continue
            request = self.fingerprint(req)
            temperature, top_p = _at(getattr(states, 'temperature', None), slot), _at(getattr(states, 'top_p', None), slot)
            top_k = _int_at(getattr(states, 'top_k', None), slot)
            try:
                logprobs = int(getattr(states, 'num_logprobs')[slot])
            except Exception:
                logprobs = None
            if logprobs == NO_LOGPROBS:
                logprobs = 0
            elif logprobs is not None and type(vocab) is int and logprobs >= vocab:
                logprobs = None  # 전체 vocabulary(-1) 요청: API 쪽도 개수로 정규화하지 않는다.
            allowed = _int_at(getattr(bias, 'num_allowed_token_ids', None), slot) if bias is not None else None
            if allowed and request in memory and memory[request][0] == allowed:
                allowed = memory[request][1]
            rows.append({'request_sha256': request,
                         'temperature_milli': consumption._milli(temperature) if temperature is not None else None,
                         'top_p_milli': consumption._milli(top_p) if top_p is not None else None,
                         'top_k': consumption._top_k(top_k, vocab) if top_k is not None else None,
                         'logprobs': cache._uint(logprobs) if logprobs is not None else None,
                         'allowed_tokens': cache._uint(allowed) if allowed is not None else None,
                         'bad_words': cache._uint(_int_at(getattr(bad, 'num_bad_words', None), slot)) if bad is not None else None,
                         'logit_bias': cache._uint(_int_at(getattr(bias, 'num_logit_bias', None), slot)) if bias is not None else None})
        return rows

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        if returned:
            return {}
        receiver = args[0] if args else None
        if point == 'GPUModelRunner.add_requests':
            return {'received': self._received(args[1] if len(args) > 1 else kwargs.get('scheduler_output'))}
        if point == 'GPUModelRunner.sample':
            return {'applied': self._applied(receiver, args[2] if len(args) > 2 else kwargs.get('input_batch'))}
        return {}
