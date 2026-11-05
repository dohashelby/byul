"""#196 health-worker-causal 생산자의 API 쪽 관측(개발 담당, 2단계 P1).

vLLM 0.31.0 `AsyncLLM.check_health`(`vllm.v1.engine.async_llm`, 원 소스 해시 고정)는 worker에 묻지 않는다.
클라이언트 쪽 표식(`engine_core.resources.engine_dead`)과 출력 처리 task가 살아 있는지만 본다.
이 관측은 그 두 값을 health 판단 시점에 그대로 남긴다. coroutine 함수라 반환은 coroutine 생성 시점이다.
판단 값은 진입 시점의 표식에서 결정되므로 진입 행의 값을 판단 근거로 쓴다.

worker 단계 상태는 request-attempt 관측의 `Scheduler.update_from_output`(EngineCore)에서 온다.
API health와 worker 상태 사이에는 vLLM 안에 공유되는 인과 키가 없다. 그래서 두 쪽의 causal 지문은 서로 다르게 만든다.
시간 근접만으로 둘을 합치지 않는다(#196 OBS-02 거부 조건).
"""
from __future__ import annotations

from . import vllm_cache_callback as cache

ASYNC_LLM = 'vllm.v1.engine.async_llm'
TARGETS = {ASYNC_LLM: ('AsyncLLM.check_health',)}
SCHEMA = 'guard-vllm-health-callback/1'


class HealthObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    schema = SCHEMA
    profile = 'source-bound-health-callback-candidate-v1'

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        if returned:
            return {}
        receiver = args[0] if args else None
        resources = cache.base.attrs(cache.base.attrs(cache.base.attrs(receiver).get('engine_core')).get('resources'))
        dead = resources.get('engine_dead')
        handler = cache.base.attrs(receiver).get('output_handler')
        try:
            running = handler is not None and handler.done() is False
        except Exception:
            running = None
        return {'engine_dead': dead if type(dead) is bool else None, 'output_handler_running': running}
