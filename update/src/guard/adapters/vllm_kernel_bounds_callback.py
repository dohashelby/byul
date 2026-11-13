"""REQ-19: V2 입력 준비 kernel의 실제 호출 범위·초기화·완료·독립 대조.

vLLM 0.31.0 `vllm.v1.worker.gpu.input_batch`의 `prepare_prefill_inputs`와
`prepare_pos_seq_lens`를 원 소스 해시로 고정해 감싼다. 호출마다 실제 인자에서
kernel이 쓰고 읽는 주소 범위를 kernel 자신의 식으로 계산해 buffer 크기와 비교하고,
원 함수가 끝난 뒤 GPU 완료를 기다려 출력이 독립 계산과 같은지, padding 영역이
초기화됐는지 확인한다. GPU 동기화와 작은 복사가 필요하므로 소유 lab 엔진에서만 켠다.

값은 남기지 않는다(개수·범위·일치 여부와 keyed 요약만). 출력 불일치만으로 메모리
결함을 확정하지 않으며, 하드웨어 메모리 검사기를 실행한 것이 아니다.
"""
from __future__ import annotations

from .vllm_observer_pin_rows import candidate_sources, layout

import functools
import hashlib
import hmac
import os
import types
import weakref

from . import vllm_cache_callback as cache

MODULE = 'vllm.v1.worker.gpu.input_batch'
TARGETS = {MODULE: ('prepare_prefill_inputs', 'prepare_pos_seq_lens')}
SOURCES = ({MODULE: '96163c486368f2cf0e539fe68eaa06f4df3ac814da8f7a92af2b04242e01bc6d'})
SCHEMA = 'guard-vllm-kernel-bounds-callback/1'
# 원 kernel launch 상수. 다른 값이면 해석하지 않는다.
BLOCK_SIZE = 1024
MAX_REQS = 256
MAX_TOKENS = 1 << 16


def _ints(tensor, limit):
    """작은 1차원 정수 tensor를 CPU 정수 목록으로. 크면 읽지 않는다."""
    try:
        if tensor.dim() != 1 or tensor.shape[0] > limit:
            return None
        values = tensor.detach().cpu().tolist()
    except Exception:
        return None
    return values if all(type(v) is int for v in values) else None


def _rows(tensor, rows, columns):
    """2차원 tensor의 선택한 행·열 구간. (행, 시작, 끝) 목록만 읽는다."""
    try:
        return [tensor[r, a:b].detach().cpu().tolist() for r, a, b in rows] if columns else None
    except Exception:
        return None


def _numel(tensor):
    try:
        return int(tensor.numel())
    except Exception:
        return None


def _synchronize(tensors):
    """원 함수가 enqueue한 작업의 완료를 기다린다. CUDA가 아니면 완료로 본다."""
    try:
        device = next((t.device for t in tensors if getattr(getattr(t, 'device', None), 'type', None) == 'cuda'), None)
        if device is None:
            return True
        import torch
        torch.cuda.current_stream(device).synchronize()
        return True
    except Exception:
        return False


class KernelBoundsObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = candidate_sources(SOURCES, TARGETS)
    schema = SCHEMA
    profile = 'source-bound-kernel-bounds-callback-candidate-v1'
    last_positions = None

    def digest(self, values):
        return hmac.new(self.secret, repr(values).encode(), hashlib.sha256).hexdigest() if values is not None else None

    # ------------------------------------------------------------- 범위 계산
    def _prefill_plan(self, args):
        input_ids, lookahead, idx_mapping, query_start_loc, all_token_ids, prefill_len, computed = args
        slots = _ints(idx_mapping, MAX_REQS)
        starts = _ints(query_start_loc, MAX_REQS + 1)
        prefill = _ints(prefill_len, MAX_REQS * 64)
        done = _ints(computed, MAX_REQS * 64)
        try:
            row_length = int(all_token_ids.shape[1]); rows = int(all_token_ids.shape[0])
            num_lookahead = int(lookahead.shape[0]); lookahead_columns = int(lookahead.shape[1])
        except Exception:
            return None
        capacity = _numel(input_ids)
        if None in (slots, starts, prefill, done, capacity) or len(starts) < len(slots) + 1:
            return None
        items = []
        for batch, slot in enumerate(slots):
            if not (0 <= slot < min(rows, len(prefill), len(done))):
                items.append({'batch': batch, 'slot_in_range': False}); continue
            start, end = starts[batch], starts[batch + 1]
            query = end - start
            item = {'batch': batch, 'slot': slot, 'slot_in_range': True, 'prefill': done[slot] < prefill[slot],
                    'query_len': query}
            if item['prefill']:
                # kernel: store input_ids[start + block] (block < query), load all_token_ids[slot, computed + block]
                item.update(write_range_ok=0 <= start <= end <= capacity,
                            read_range_ok=0 <= done[slot] and done[slot] + query <= row_length,
                            lookahead_write_ok=0 <= slot < lookahead_columns,
                            # pos < prefill_len로 읽기를 막는다. 막힌 칸은 0이다.
                            lookahead_read_ok=prefill[slot] <= row_length,
                            reference=(slot, done[slot], done[slot] + query, start, end))
            items.append(item)
        return {'kernel': '_prepare_prefill_inputs_kernel', 'grid': [len(slots)], 'block_size': BLOCK_SIZE,
                'input_capacity': capacity, 'row_length': row_length, 'num_lookahead': num_lookahead, 'items': items}

    def _pos_plan(self, args):
        idx_mapping, query_start_loc, computed, positions, seq_lens = args
        slots = _ints(idx_mapping, MAX_REQS)
        starts = _ints(query_start_loc, MAX_REQS + 1)
        done = _ints(computed, MAX_REQS * 64)
        capacity = _numel(positions); seq_capacity = _numel(seq_lens)
        if None in (slots, starts, done, capacity, seq_capacity) or len(starts) < len(slots) + 1:
            return None
        items = []
        for batch, slot in enumerate(slots):
            if not 0 <= slot < len(done):
                items.append({'batch': batch, 'slot_in_range': False}); continue
            start, end = starts[batch], starts[batch + 1]
            items.append({'batch': batch, 'slot': slot, 'slot_in_range': True, 'query_len': end - start,
                          'write_range_ok': 0 <= start <= end <= capacity and batch < seq_capacity,
                          'reference': (start, end, done[slot])})
        return {'kernel': '_prepare_pos_seq_lens_kernel', 'grid': [len(slots) + 1], 'block_size': BLOCK_SIZE,
                'position_capacity': capacity, 'seq_lens_capacity': seq_capacity,
                'padding_range_ok': len(slots) <= seq_capacity, 'items': items}

    # ------------------------------------------------------------- 사후 대조
    def _prefill_check(self, args, plan):
        input_ids, _, _, _, all_token_ids, _, _ = args
        result = []
        for item in plan['items']:
            if not item.get('prefill') or not (item.get('write_range_ok') and item.get('read_range_ok')):
                continue
            slot, a, b, start, end = item['reference']
            expected = _rows(all_token_ids, [(slot, a, b)], True)
            try:
                written = input_ids[start:end].detach().cpu().tolist()
            except Exception:
                written = None
            result.append({'batch': item['batch'], 'reference_matches': None if expected is None or written is None else written == expected[0],
                           'output_sha256': self.digest(written)})
        return result

    def _pos_check(self, args, plan):
        _, _, _, positions, seq_lens = args
        result = []
        try:
            lens = seq_lens.detach().cpu().tolist()
        except Exception:
            lens = None
        for item in plan['items']:
            if not item.get('write_range_ok'):
                continue
            start, end, done = item['reference']
            try:
                written = positions[start:end].detach().cpu().tolist()
            except Exception:
                written = None
            expected = list(range(done, done + end - start))
            result.append({'batch': item['batch'],
                           'reference_matches': None if written is None or lens is None else written == expected and lens[item['batch']] == done + end - start,
                           'output_sha256': self.digest(written)})
        padding = None if lens is None or not plan['padding_range_ok'] else all(v == 0 for v in lens[len(plan['items']):])
        self._remember_positions(positions, plan, result)
        return result, padding

    def _remember_positions(self, positions, plan, result):
        """REQ-20이 graph replay 직전 위치 buffer가 이번 step 값인지 대조할 수 있게 남긴다."""
        self.last_positions = None
        items = plan['items']
        if not items or not all(i.get('write_range_ok') for i in items) or not all(r['reference_matches'] for r in result):
            return
        refs = sorted(i['reference'] for i in items)
        if refs[0][0] != 0 or any(a[1] != b[0] for a, b in zip(refs, refs[1:])):
            return
        expected = [p for start, end, done in refs for p in range(done, done + end - start)]
        try:
            self.last_positions = {'tensor': weakref.ref(positions), 'length': refs[-1][1],
                                   'expected_sha256': hmac.new(self.secret, repr(expected).encode(), hashlib.sha256).hexdigest()}
        except TypeError:
            self.last_positions = None

    # ------------------------------------------------------------- 감싸기
    def wrap(self, module, point):
        name = vars(module).get('__name__')
        if point not in self.targets.get(name, ()):
            raise ValueError('KERNEL_BOUNDS_POINT_UNSUPPORTED')
        original = vars(module).get(point)
        if type(original) is not types.FunctionType or original.__globals__ is not vars(module):
            raise ValueError('KERNEL_BOUNDS_ENTRY_UNSUPPORTED')
        expected = cache._codes(module, self.targets, self.sources).get(point)
        if expected is None or cache._digest(original.__code__, name) != expected:
            raise ValueError('KERNEL_BOUNDS_ENTRY_UNSUPPORTED')

        def qualified():
            if os.getpid() != self.pid:
                self.gaps.add('KERNEL_BOUNDS_PROCESS_CHANGED'); return False
            return (vars(module).get(point) in (original, wrapped) and cache._digest(original.__code__, name) == expected
                    and cache._codes(module, self.targets, self.sources).get(point) == expected)

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            plan = None; before = False
            try:
                before = bool(qualified()) and not kwargs
                if before:
                    plan = (self._prefill_plan if point == 'prepare_prefill_inputs' else self._pos_plan)(args)
                    if plan is None:
                        self.gaps.add('KERNEL_BOUNDS_PLAN_UNAVAILABLE')
            except BaseException:
                self.gaps.add('KERNEL_BOUNDS_PLAN_UNAVAILABLE'); plan = None
            with self.lock:
                self.sequence += 1; call = self.fingerprint('call:' + str(self.sequence))
            common = {'schema': self.schema, 'session_sha256': cache.base.sha(self.session.encode()),
                      'process_sha256': self.process_sha256, 'call_sha256': call, 'point': point,
                      'source_file_sha256': self.sources[name], 'entry_code_sha256': expected,
                      'entry_qualified_before': before, 'hardware_memory_checker': False}
            self._emit({**common, 'phase': 'CALL', 'fields': {'plan': self._public(plan)}})
            state = 'CALL'
            try:
                result = original(*args, **kwargs); state = 'RETURN'; return result
            except BaseException:
                state = 'RAISE'; raise
            finally:
                fields = {}; after = False
                try:
                    after = bool(qualified())
                    if before and after and state == 'RETURN' and plan is not None:
                        fields['gpu_completion'] = _synchronize([a for a in args if hasattr(a, 'device')])
                        if fields['gpu_completion']:
                            if point == 'prepare_prefill_inputs':
                                fields['outputs'] = self._prefill_check(args, plan)
                            else:
                                fields['outputs'], fields['padding_initialized'] = self._pos_check(args, plan)
                except BaseException:
                    self.gaps.add('KERNEL_BOUNDS_TERMINAL_UNAVAILABLE')
                self._emit({**common, 'phase': state, 'entry_qualified_after': after, 'fields': fields})
        return wrapped

    @staticmethod
    def _public(plan):
        if plan is None:
            return None
        return {**plan, 'items': [{k: v for k, v in item.items() if k != 'reference'} for item in plan['items']]}


def instrument(module, observer):
    """모든 지점을 검증한 뒤에만 바꾼다. 하나라도 맞지 않으면 원래대로 둔다."""
    changes = [(point, observer.wrap(module, point)) for point in TARGETS[vars(module).get('__name__')]]
    for point, wrapped in changes:
        setattr(module, point, wrapped)
    return True
