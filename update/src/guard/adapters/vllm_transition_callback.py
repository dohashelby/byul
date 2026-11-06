"""#196 generation-transition 생산자의 엔진 쪽 관측(개발 담당, 2단계 P1).

vLLM 0.31.0 EngineCore(`vllm.v1.engine.core`, 원 소스 해시 고정)의 세대 전환 진입점을 감싼다.
- `collective_rpc`: 메서드 이름이 `reload_weights`일 때 가중치 세대 전환. 다른 메서드는 이름 지문만 남기고 전환으로 세지 않는다.
- `add_lora`·`remove_lora`: adapter 세대 전환
- `reset_prefix_cache`: cache 세대 전환
- `pause_scheduler`·`resume_scheduler`·`sleep`·`wake_up`: 접수 차단(drain)과 재개(routing). `EngineCoreProc`가 덮어쓴
  `pause_scheduler`도 함께 감싼다.
- `set_weight_version`: 운영자가 붙인 가중치 판본 이름(지문만)

진입 시점에 scheduler가 가진 요청 ID(진행 중 요청)를 지문으로 남긴다. 요청별로 어느 세대를 소비했는지는
request-attempt 관측의 단계 배정 기록과 시간 순서로 맞춘다(같은 EngineCore 프로세스, 같은 시계).
예외 원문·LoRA 경로·가중치 경로는 남기지 않는다. 이 관측은 생산자 인증이 아니다.
"""
from __future__ import annotations

from . import vllm_cache_callback as cache

CORE = 'vllm.v1.engine.core'
TARGETS = {CORE: ('EngineCore.collective_rpc', 'EngineCore.add_lora', 'EngineCore.remove_lora', 'EngineCore.reset_prefix_cache',
                  'EngineCore.pause_scheduler', 'EngineCore.resume_scheduler', 'EngineCore.sleep', 'EngineCore.wake_up',
                  'EngineCore.set_weight_version', 'EngineCoreProc.pause_scheduler')}
SCHEMA = 'guard-vllm-transition-callback/1'
RPC_TRANSITIONS = frozenset({'reload_weights'})
MAX_IN_FLIGHT = 256


class TransitionObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    schema = SCHEMA
    profile = 'source-bound-transition-callback-candidate-v1'

    def _in_flight(self, receiver):
        requests = cache.base.attrs(cache.base.attrs(receiver).get('scheduler')).get('requests')
        if type(requests) is not dict or len(requests) > MAX_IN_FLIGHT:
            self.gaps.add('TRANSITION_CALLBACK_IN_FLIGHT_UNAVAILABLE'); return None
        return sorted(v for v in (self.fingerprint(k) for k in requests) if v)

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        row = {}
        if point == 'EngineCore.collective_rpc':
            method = args[1] if len(args) > 1 else kwargs.get('method')
            name = method if type(method) is str and len(method) <= 64 else None
            row['rpc_transition'] = name in RPC_TRANSITIONS
            row['rpc_method_sha256'] = self.fingerprint(name) if name else None
        elif point == 'EngineCore.add_lora':
            lora = cache.base.attrs(args[1] if len(args) > 1 else kwargs.get('lora_request'))
            row['adapter_sha256'] = self.fingerprint(lora.get('lora_name'))
        elif point == 'EngineCore.remove_lora':
            row['adapter_id_sha256'] = self.fingerprint(cache._uint(args[1] if len(args) > 1 else kwargs.get('lora_id')))
        elif point == 'EngineCore.set_weight_version':
            value = args[1] if len(args) > 1 else kwargs.get('weight_version')
            row['weight_version_sha256'] = self.fingerprint(value) if type(value) is str else None
        elif point.endswith('pause_scheduler') or point == 'EngineCore.sleep':
            mode = args[1] if len(args) > 1 and point.endswith('pause_scheduler') else kwargs.get('mode')
            row['mode'] = mode if mode in ('abort', 'keep', 'wait') else None
        if not returned and (point != 'EngineCore.collective_rpc' or row.get('rpc_transition')):
            row['in_flight_request_sha256'] = self._in_flight(receiver)
        if returned:
            row['result'] = result if type(result) is bool else None
        return row
