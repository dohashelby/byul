"""REQ-20: V2 FULL CUDA graph replay의 입력 갱신·graph key·출력 소비·완료·buffer 세대.

vLLM 0.31.0 V2 runner에서 FULL graph는 `ModelCudaGraphManager.run_fullgraph`로
replay되고, 출력은 매 replay마다 같은 영구 buffer(`hidden_states`)의 앞부분 view로
돌아온다. 그 view를 `GPUModelRunner.sample`이 소비한다. 두 지점을 원 소스 해시로
고정해 감싸고 소유 lab 엔진에서만 다음을 본다.

- replay 진입: graph key(desc)와 캡처 여부, 앞 replay 출력이 소비되기 전에 새 replay가
  시작됐는지, 위치 입력 buffer가 이번 step의 계산값인지(REQ-19 관측기의 마지막 기록).
- replay 반환: GPU 완료를 기다린 뒤 출력 keyed 요약과 buffer 주소.
- sample 진입: 받은 hidden_states가 그 replay 출력과 같은 buffer인지, 값이 그대로인지,
  어느 요청인지.

값은 남기지 않는다. GPU 동기화와 복사가 필요하므로 kernel 검사와 같은 lab 설정에서만 켠다.
"""
from __future__ import annotations

import hashlib
import hmac

from . import vllm_cache_callback as cache

GRAPH_MODULE = 'vllm.v1.worker.gpu.cudagraph_utils'
RUNNER_MODULE = 'vllm.v1.worker.gpu.model_runner'
TARGETS = {GRAPH_MODULE: ('ModelCudaGraphManager.run_fullgraph',), RUNNER_MODULE: ('GPUModelRunner.sample',)}
SOURCES = {GRAPH_MODULE: '8e6ac6f655dd013dd2a3d3561bc88fe05ceeea2b060e5cb6bc85decb0bb2f813',
           RUNNER_MODULE: '725c47114e148ade3a26b4cc9c5f7295588fdd387afc3cb5fdb2d16513a1bb5c'}
SCHEMA = 'guard-vllm-graph-replay-callback/1'
MAX_REQUESTS = 256


def _bytes(tensor):
    """dtype과 무관하게 tensor 원 bytes. torch가 없으면(시험 대역) 목록 표현."""
    try:
        import torch  # noqa: F401
        return tensor.detach().contiguous().view(-1).view(__import__('torch').uint8).cpu().numpy().tobytes()
    except ImportError:
        return repr(tensor.tolist()).encode()


def _synchronize(tensor):
    try:
        if getattr(getattr(tensor, 'device', None), 'type', None) != 'cuda':
            return True
        import torch
        torch.cuda.current_stream(tensor.device).synchronize()
        return True
    except Exception:
        return False


def _ptr(tensor):
    try:
        return int(tensor.data_ptr()), int(tensor.shape[0])
    except Exception:
        return None


class GraphReplayObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-graph-replay-callback-candidate-v1'

    def __init__(self, session, *, kernel=None, **options):
        super().__init__(session, **options)
        self.kernel = kernel
        self.replays = {}  # manager token -> 마지막 replay 출력

    def digest(self, tensor):
        try:
            return hmac.new(self.secret, _bytes(tensor), hashlib.sha256).hexdigest()
        except Exception:
            return None

    @staticmethod
    def _desc(desc):
        fields = cache.base.attrs(desc)
        mode = fields.get('cg_mode')
        return {'cg_mode': getattr(mode, 'name', None), 'num_tokens': cache._uint(fields.get('num_tokens')),
                'num_reqs': cache._uint(fields.get('num_reqs')), 'uniform_token_count': cache._uint(fields.get('uniform_token_count')),
                'num_active_loras': cache._uint(fields.get('num_active_loras')), 'num_ubatches': cache._uint(fields.get('num_ubatches'))}

    def _input_refresh(self):
        """위치 buffer의 앞부분이 이번 step에 계산된 값 그대로인지. 기록이 없으면 None."""
        last = getattr(self.kernel, 'last_positions', None) if self.kernel is not None else None
        if not last:
            return None
        tensor = last['tensor']()
        if tensor is None or not _synchronize(tensor):
            return None
        try:
            current = tensor[:last['length']].detach().cpu().tolist()
        except Exception:
            return None
        return hmac.new(self.secret, repr(current).encode(), hashlib.sha256).hexdigest() == last['expected_sha256']

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        manager = self.token(receiver)
        if point == 'ModelCudaGraphManager.run_fullgraph':
            desc = args[1] if len(args) > 1 else kwargs.get('desc')
            if not returned:
                graphs = cache.base.attrs(receiver).get('graphs')
                prior = self.replays.get(manager)
                return {'manager_token': manager, 'desc': self._desc(desc),
                        'captured': type(graphs) is dict and desc in graphs,
                        'graph_token': self.token(graphs.get(desc)) if type(graphs) is dict else None,
                        'pending_unconsumed': prior is not None and prior['consumed'] is False,
                        'input_refresh': self._input_refresh()}
            output = result[0] if type(result) is tuple else result
            done = _synchronize(output)
            row = {'manager_token': manager, 'gpu_completion': done}
            if done:
                row['output_sha256'] = self.digest(output)
                where = _ptr(output)
                self.replays[manager] = {'output_sha256': row['output_sha256'], 'ptr': where, 'consumed': False}
            return row
        # GPUModelRunner.sample(hidden_states, input_batch, grammar_output)
        if returned:
            return {}
        hidden = args[1] if len(args) > 1 else kwargs.get('hidden_states')
        batch = args[2] if len(args) > 2 else kwargs.get('input_batch')
        ids = cache.base.attrs(batch).get('req_ids')
        requests = [self.fingerprint(i) for i in ids] if type(ids) is list and len(ids) <= MAX_REQUESTS else None
        where = _ptr(hidden)
        source = next((t for t, r in self.replays.items() if r['ptr'] is not None and where is not None
                       and r['ptr'][0] == where[0] and where[1] <= r['ptr'][1] and r['consumed'] is False), None)
        row = {'native_request_sha256': requests, 'graph_output': source is not None, 'manager_token': source}
        if source is not None:
            replay = self.replays[source]
            replay['consumed'] = True
            row['output_intact'] = _synchronize(hidden) and self.digest(hidden) == replay['output_sha256'] if where[1] == replay['ptr'][1] else None
        return row


def instrument(module, observer):
    """모든 지점을 검증한 뒤에만 바꾼다."""
    name = vars(module).get('__name__')
    changes = [(cache._function(module, point), observer.wrap(module, point)) for point in TARGETS[name]]
    for (owner, method, _), wrapped in changes:
        setattr(owner, method, wrapped)
    return True
