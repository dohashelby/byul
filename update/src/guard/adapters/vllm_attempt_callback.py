"""#196 request-attempt 생산자의 엔진 쪽 관측(개발 담당, 2단계 P1).

vLLM 0.31.0 EngineCore 프로세스의 scheduler와 KV cache manager를 원 소스 해시로 고정해 감싼다.
- `Scheduler.add_request`: 접수(admitted)·대기열 진입(queued)
- `Scheduler.schedule`: 단계별 배정 토큰(stage cost)과 단계 예산(max_num_scheduled_tokens)
- `KVCacheManager.allocate_slots`: KV 블록 소유량. None 반환은 한도 집행(limit_enforced)
- `Scheduler._preempt_request`: 선점 뒤 재계산. 같은 요청의 새 attempt로 본다(retry, 원인 preempted)
- `Scheduler.update_from_output`: worker 단계 출력이 host에 도착함. 그 단계 GPU 작업 완료 근거(gpu_fence)
- `Scheduler._free_request`: 종료·취소(finished / abort, 요청 상태로 구분)
- `KVCacheManager.free`: KV 블록 반환(kv_released)

모든 지점이 EngineCore 한 프로세스라 프로세스 사이 결합이 없다. 요청 ID는 session 키 HMAC 지문으로만 남긴다.
GPU 메모리 bytes는 읽지 않는다(gpu_bytes는 None). 이 관측은 생산자 인증이 아니다.
"""
from __future__ import annotations

from . import vllm_cache_callback as cache

SCHEDULER = 'vllm.v1.core.sched.scheduler'
KV = 'vllm.v1.core.kv_cache_manager'
TARGETS = {SCHEDULER: ('Scheduler.add_request', 'Scheduler.schedule', 'Scheduler._preempt_request',
                       'Scheduler.update_from_output', 'Scheduler._free_request'),
           KV: ('KVCacheManager.allocate_slots', 'KVCacheManager.free')}
SCHEMA = 'guard-vllm-attempt-callback/1'
MAX_BATCH = 256


def _status(request):
    value = cache.base.attrs(request).get('status')
    name = getattr(value, 'name', None)
    return name if type(name) is str and len(name) <= 64 else None


def _block_count(blocks):
    groups = cache.base.attrs(blocks).get('blocks')
    if type(groups) not in (tuple, list) or len(groups) > 16 or any(type(g) not in (tuple, list) for g in groups):
        return None
    return sum(len(g) for g in groups)


class AttemptObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    schema = SCHEMA
    profile = 'source-bound-attempt-callback-candidate-v1'

    def _request(self, args, kwargs):
        request = args[1] if len(args) > 1 else kwargs.get('request')
        fields = cache.base.attrs(request)
        return request, {'request_sha256': self.fingerprint(fields.get('request_id')),
                         'num_preemptions': cache._uint(fields.get('num_preemptions')),
                         'num_computed_tokens': cache._uint(fields.get('num_computed_tokens'))}

    def _ids(self, values):
        if type(values) not in (list, tuple, set, frozenset) or len(values) > MAX_BATCH:
            self.gaps.add('ATTEMPT_CALLBACK_BATCH_SHAPE_UNAVAILABLE'); return None
        return sorted(v for v in (self.fingerprint(x) for x in values) if v)

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        if point == 'Scheduler.schedule':
            row = {'budget_tokens': cache._uint(cache.base.attrs(receiver).get('max_num_scheduled_tokens'))}
            if returned:
                fields = cache.base.attrs(result)
                scheduled = fields.get('num_scheduled_tokens')
                if type(scheduled) is dict and len(scheduled) <= MAX_BATCH:
                    row['scheduled'] = sorted(({'request_sha256': self.fingerprint(k), 'tokens': cache._uint(v)}
                                               for k, v in scheduled.items()), key=lambda r: r['request_sha256'] or '')
                else:
                    self.gaps.add('ATTEMPT_CALLBACK_BATCH_SHAPE_UNAVAILABLE')
                row['total_tokens'] = cache._uint(fields.get('total_num_scheduled_tokens'))
            return row
        if point == 'Scheduler.update_from_output':
            if not returned:
                return {}
            output = args[2] if len(args) > 2 else kwargs.get('model_runner_output')
            return {'output_request_sha256': self._ids(cache.base.attrs(output).get('req_ids'))}
        request, row = self._request(args, kwargs)
        if point == 'Scheduler._free_request':
            row['status'] = _status(request)
        elif point == 'KVCacheManager.allocate_slots':
            row['num_new_tokens'] = cache._uint(args[2] if len(args) > 2 else kwargs.get('num_new_tokens'))
            if returned:
                row['allocated'] = result is not None
                row['new_blocks'] = _block_count(result) if result is not None else 0
        return row
