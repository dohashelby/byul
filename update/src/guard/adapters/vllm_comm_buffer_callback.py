"""REQ-21: TP custom all-reduce 통신 buffer의 등록·사용·완료·해제·재등록 세대.

`vllm.distributed.device_communicators.custom_all_reduce`의
`CustomAllreduce.__init__`·`register_graph_buffers`·`all_reduce`·`close`를 판본별 원 소스 해시
(`PIN_ROWS`)로 고정해 감싼다. 통신기 객체를 세대로 보고, 등록한 handle·buffer 주소는 keyed 요약으로만
남긴다(주소 원값·tensor 비저장). 완료 fence는 lab 설정에서만 GPU 동기화로 확인한다.

해제된 주소가 다음 세대에 다시 나오는 것은 CUDA의 정상 재사용일 수 있어 위반으로 보지 않는다.
"""
from __future__ import annotations

from . import vllm_cache_callback as cache

MODULE = 'vllm.distributed.device_communicators.custom_all_reduce'
TARGETS = {MODULE: ('CustomAllreduce.__init__', 'CustomAllreduce.register_graph_buffers',
                    'CustomAllreduce.all_reduce', 'CustomAllreduce.close')}
# 판본별 원 소스 고정값(판본 데이터). 감싸는 코드는 같고, 파일 bytes가 이 중 하나와 정확히
# 같을 때만 감싼다. 관측 속성(`_ptr`·`buffer_ptrs`·`disabled`·`rank`·`world_size`)과
# `all_reduce(..., registered=)` 서명은 세 소스 모두에서 함수 단위로 확인했다(M-A6,
# `tools/engine_pin_compare.py`, docs/dev-1/ma6-version-applicability.md).
PIN_ROWS = {
    # 이전 판본(GPU 실측 소스 db9527a4…)
    '50adb01de32bb910fd3c86e13125e36c9d7f3eb5efb36fba99f15c3675760cb9': 'reviewed',
    # MVP 후보 판본 태그 소스 ced6857a…: MNNVL multimem 경로 삭제만 다름. 이미지 bytes 대조 전 후보
    'a048c1327c23580805600c0c951831bec333b1ffc4eb26a0e3d34119c4f0c445': 'candidate',
    # MVP 기준 판본 태그 소스 5204ff5c…: MNNVL 없음, 생성자 인자 max_size만. 이미지 bytes 대조 전 후보
    'f3e7b346e31eeab398c62c9a4e94a5aeab8a6936460b365ba60ceed91f0ecbf2': 'candidate',
}
SOURCES = {MODULE: tuple(PIN_ROWS)}
SCHEMA = 'guard-vllm-comm-buffer-callback/1'
MAX_RANKS = 16


def _synchronize(tensor):
    try:
        if getattr(getattr(tensor, 'device', None), 'type', None) != 'cuda':
            return True
        import torch
        torch.cuda.current_stream(tensor.device).synchronize()
        return True
    except Exception:
        return False


class CommBufferObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-comm-buffer-callback-candidate-v1'

    def __init__(self, session, *, fence=False, **options):
        super().__init__(session, **options)
        self.fence = fence  # 완료 확인용 GPU 동기화는 lab 설정에서만

    def _pointers(self, values):
        if type(values) is not list or len(values) > MAX_RANKS or any(cache._uint(v) is None for v in values):
            return None
        return [self.fingerprint(v) for v in values]

    def _state(self, receiver):
        fields = cache.base.attrs(receiver)
        rank = cache._uint(fields.get('rank'))
        pointers = self._pointers(fields.get('buffer_ptrs'))
        return {'communicator_token': self.token(receiver), 'handle_sha256': self.fingerprint(cache._uint(fields.get('_ptr'))),
                'live': cache._uint(fields.get('_ptr')) not in (None, 0) and fields.get('disabled') is False,
                'disabled': fields.get('disabled') if type(fields.get('disabled')) is bool else None,
                'rank': rank, 'world_size': cache._uint(fields.get('world_size')),
                'buffer_sha256': pointers[rank] if pointers is not None and rank is not None and rank < len(pointers) else None,
                'buffer_set_sha256': pointers}

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        if point == 'CustomAllreduce.__init__':
            # 생성 전에는 읽을 상태가 없다. 반환 뒤 등록 세대를 남긴다.
            return self._state(receiver) if returned else {'communicator_token': None}
        row = self._state(receiver)
        if point == 'CustomAllreduce.all_reduce' and not returned:
            registered = kwargs.get('registered', False)  # 원 서명에서 keyword 전용
            row['registered_input'] = registered if type(registered) is bool else None
        if point == 'CustomAllreduce.all_reduce' and returned:
            row['completion_fence'] = _synchronize(result) if self.fence else None
        return row


def instrument(module, observer):
    name = vars(module).get('__name__')
    changes = [(cache._function(module, point), observer.wrap(module, point)) for point in TARGETS[name]]
    for (owner, method, _), wrapped in changes:
        setattr(owner, method, wrapped)
    return True
