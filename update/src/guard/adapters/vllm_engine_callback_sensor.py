"""Prepared in-pod installer for the source-bound cache and distributed callbacks.

Ships as a self-contained package beside the callback modules it installs. A
target that does not match its pins stays unwrapped and leaves a gap row; the
engine keeps its original behaviour. Rows hold keyed fingerprints only.
"""
from __future__ import annotations

import atexit
import functools
import hashlib
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import re
import sys
import threading

from . import vllm_cache_callback as cache
from . import vllm_distributed_callback as distributed
from . import vllm_kernel_bounds_callback as kernel_bounds

PROFILE = 'vllm-engine-callback-metadata/1'
SCHEMA = 'guard-engine-callback-events/1'
ROOT = '/guard-lifecycle/engine-callbacks'
DATA_ROOT = '/guard-lifecycle/data'
SLOT_MODULE = 'vllm.v1.worker.gpu_input_batch'
SLOT_TARGETS = {SLOT_MODULE: ('InputBatch.add_request', 'InputBatch.remove_request')}
SLOT_SOURCE = {SLOT_MODULE: 'e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d'}
# V2 model runner (vLLM 0.31.0 기본 GPU runner). 자리 배정·회수와 sampler가 실제로
# 받은 요청별 상태를 CPU 쪽 배열에서만 읽는다. GPU tensor를 읽거나 동기화하지 않는다.
SLOT_V2_STATES = 'vllm.v1.worker.gpu.states'
SLOT_V2_SAMPLER = 'vllm.v1.worker.gpu.sample.sampler'
SLOT_V2_TARGETS = {SLOT_V2_STATES: ('RequestState.add_request', 'RequestState.remove_request'),
                   SLOT_V2_SAMPLER: ('Sampler.add_request', 'Sampler.__call__')}
SLOT_V2_SOURCE = {SLOT_V2_STATES: '8c899b8ef63d95f450876d3f01e56772f9534938a95383a46659fddf85573fa3',
                  SLOT_V2_SAMPLER: 'ddbcdab46010bf38a5fab9b9b6a2deed2ec5e883ebfe81e83f6d22f41e546771'}
MAX_SAMPLED_SLOTS = 256
# Block-level lookups feed no projection. Wrapping them made every cache hit
# slower in proportion to its length and multiplied the session rows.
CACHE_TARGETS = {'vllm.v1.core.kv_cache_manager': cache.TARGETS['vllm.v1.core.kv_cache_manager']}
MODULES = {**CACHE_TARGETS, **distributed.TARGETS, **SLOT_TARGETS, **SLOT_V2_TARGETS, **kernel_bounds.TARGETS}
# Engine request ids carry generated prefixes and suffixes around this tag.
TAG = re.compile(r'byul-cb-[0-9a-f]{32}')
MAX_FILE_BYTES = 16 * 1024 ** 2
FAULT_POINT = '_execute_worker_rpc'
FAULT_FILE = 'lab-fault-exception'


class _Keyed:
    """Share one prepared key so the parent can match its own request tags."""
    def fingerprint(self, value):
        if type(value) is str:
            found = TAG.search(value)
            if found: value = found.group()
        return super().fingerprint(value)

    def _emit(self, row):
        """오래 도는 엔진에서 메모리 상한이 기록을 끊지 않게 행을 파일로만 보낸다."""
        try:
            self.sink(row)
        except BaseException:
            with self.lock:
                self.gaps.add('CACHE_CALLBACK_SINK_UNAVAILABLE')


class CacheObserver(_Keyed, cache.CacheCallbackObserver):
    targets = CACHE_TARGETS


class DistributedObserver(_Keyed, distributed.DistributedCallbackObserver):
    pass


class SlotObserver(_Keyed, cache.CacheCallbackObserver):
    """요청이 받은 batch 자리와 제한 여부만 남긴다. 값 자체는 남기지 않는다."""
    targets = SLOT_TARGETS
    sources = SLOT_SOURCE
    schema = 'guard-vllm-slot-callback/1'
    profile = 'source-bound-slot-callback-candidate-v1'

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        value = args[1] if len(args) > 1 else kwargs.get('request', kwargs.get('req_id'))
        if point == 'InputBatch.add_request':
            fields = cache.base.attrs(value)
            allowed = cache.base.attrs(fields.get('sampling_params')).get('allowed_token_ids')
            row = {'request_sha256': self.fingerprint(fields.get('req_id')), 'restricted': type(allowed) is list and bool(allowed)}
        else:
            row = {'request_sha256': self.fingerprint(value)}
        if returned:
            row['slot'] = cache._uint(result)
        return row


def _slot_index(value):
    """numpy 정수도 받되 bool·음수·거대값은 버린다."""
    try:
        if isinstance(value, bool):
            return None
        index = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return index if 0 <= index < 2**31 and index == value else None


def _array_at(array, index):
    try:
        return array[index]
    except Exception:
        return None


class SlotV2Observer(_Keyed, cache.CacheCallbackObserver):
    """V2 runner의 자리 배정과 sampler가 실제로 쓴 자리별 제한 상태.

    값(허용 token 목록·logit)은 남기지 않고 개수·플래그만 남긴다. 상태는 sampler가
    호출되는 순간의 CPU 원본 배열이며, GPU kernel이 그 값을 소비했다는 증명은 아니다.
    """
    targets = SLOT_V2_TARGETS
    sources = SLOT_V2_SOURCE
    schema = 'guard-vllm-slot-v2-callback/1'
    profile = 'source-bound-slot-v2-callback-candidate-v1'

    def _request_at(self, states, slot):
        mapping = cache.base.attrs(states).get('index_to_req_id')
        return self.fingerprint(mapping.get(slot)) if type(mapping) is dict else None

    def _slot_state(self, sampler, slot):
        """Sampler의 logit-bias 상태에서 이 자리의 허용 token 개수와 사용 여부."""
        fields = cache.base.attrs(sampler)
        bias = next((p for p in (fields.get('logits_processors') or ()) if hasattr(p, 'num_allowed_token_ids')), None)
        allowed = _array_at(getattr(getattr(bias, 'num_allowed_token_ids', None), 'np', None), slot)
        used = _array_at(getattr(bias, 'use_logit_bias', None), slot)
        needs = _array_at(fields.get('needs_logits_processing'), slot)
        return {'allowed_token_count': _slot_index(allowed) if allowed is not None else None,
                'logit_bias_in_use': bool(used) if used is not None else None,
                'needs_logits_processing': bool(needs) if needs is not None else None}

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        receiver = args[0] if args else None
        if point == 'RequestState.add_request':
            req_id = args[1] if len(args) > 1 else kwargs.get('req_id')
            row = {'request_sha256': self.fingerprint(req_id)}
            if returned:
                mapping = cache.base.attrs(receiver).get('req_id_to_index')
                row['slot'] = _slot_index(mapping.get(req_id)) if type(mapping) is dict else None
            return row
        if point == 'RequestState.remove_request':
            req_id = args[1] if len(args) > 1 else kwargs.get('req_id')
            row = {'request_sha256': self.fingerprint(req_id)}
            if returned:
                row['slot'] = _slot_index(result)
            return row
        states = cache.base.attrs(receiver).get('req_states')
        if point == 'Sampler.add_request':
            slot = _slot_index(args[1] if len(args) > 1 else kwargs.get('req_idx'))
            params = args[2] if len(args) > 2 else kwargs.get('sampling_params')
            allowed = cache.base.attrs(params).get('allowed_token_ids')
            row = {'slot': slot, 'request_sha256': self._request_at(states, slot),
                   'restricted': type(allowed) is list and bool(allowed)}
            if returned and slot is not None:
                row.update(self._slot_state(receiver, slot))
            return row
        # Sampler.__call__: 진입 시점의 batch 자리와 그 자리의 상태만 읽는다.
        if returned:
            return {}
        batch = args[2] if len(args) > 2 else kwargs.get('input_batch')
        mapping = cache.base.attrs(batch).get('idx_mapping_np')
        try:
            slots = [_slot_index(v) for v in list(mapping)] if mapping is not None and len(mapping) <= MAX_SAMPLED_SLOTS else None
        except Exception:
            slots = None
        if slots is None or None in slots:
            self.gaps.add('SLOT_V2_SAMPLED_SLOTS_UNAVAILABLE')
            return {'sampled': None}
        return {'sampled': [{'slot': s, 'request_sha256': self._request_at(states, s), **self._slot_state(receiver, s)}
                            for s in slots]}


class Sink:
    def __init__(self, directory):
        self.path = Path(directory) / f'callbacks-{os.getpid()}.jsonl'
        self.size = 0; self.fd = None; self.lock = threading.Lock()

    def __call__(self, row):
        raw = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
        with self.lock:
            if self.size + len(raw) > MAX_FILE_BYTES:
                raise ValueError('ENGINE_CALLBACK_FILE_LIMIT')
            if self.fd is None:
                self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            os.write(self.fd, raw); self.size += len(raw)


class EngineCallbacks:
    def __init__(self, session, secret, sink, *, context_provider=None, clock=None, fault_file=None, kernel_checks=False):
        self.sink = sink; self.fault_file = fault_file
        options = {'sink': sink, 'context_provider': context_provider, 'clock': clock}
        self.cache = CacheObserver(session, **options)
        self.distributed = DistributedObserver(session, **options)
        self.slot = SlotObserver(session, **options)
        self.slot_v2 = SlotV2Observer(session, **options)
        # REQ-19 kernel 대조는 GPU 동기화가 필요하다. 소유 lab 엔진에서 명시했을 때만 만든다.
        self.kernel = kernel_bounds.KernelBoundsObserver(session, **options) if kernel_checks else None
        self.cache.secret = self.distributed.secret = self.slot.secret = self.slot_v2.secret = secret
        if self.kernel is not None:
            self.kernel.secret = secret

    def instrument(self, module):
        """Validate every point of a module before changing any of them."""
        name = vars(module).get('__name__')
        if name in kernel_bounds.TARGETS:
            if self.kernel is None:
                return False
            try:
                return kernel_bounds.instrument(module, self.kernel)
            except Exception:
                self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
                return False
        observer = (self.cache if name in CACHE_TARGETS else self.slot if name in SLOT_TARGETS
                    else self.slot_v2 if name in SLOT_V2_TARGETS else self.distributed)
        try:
            changes = [(cache._function(module, point), observer.wrap(module, point)) for point in MODULES[name]]
        except Exception:
            self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
            return False
        for (owner, method, _), wrapped in changes:
            setattr(owner, method, self._fault(wrapped) if method == FAULT_POINT and self.fault_file else wrapped)
        return True

    def _fault(self, wrapped):
        """Lab only: raise once in the worker when the parent placed the fault file."""
        path = Path(self.fault_file)
        @functools.wraps(wrapped)
        def faulted(*args, **kwargs):
            try:
                path.unlink()
            except OSError:
                return wrapped(*args, **kwargs)
            raise RuntimeError('GUARD_LAB_FAULT')
        wrapped.guard_outer = faulted
        return faulted

    def gap(self, code, detail=None):
        try:
            self.sink({'schema': SCHEMA, 'phase': 'GAP', 'code': code, 'process_sha256': self.cache.process_sha256,
                'detail_sha256': hashlib.sha256(str(detail).encode()).hexdigest()})
        except Exception:
            pass

    def flush_gaps(self):
        kernel = self.kernel.gaps if self.kernel is not None else set()
        for code in sorted(self.cache.gaps | self.distributed.gaps | self.slot.gaps | self.slot_v2.gaps | kernel):
            self.gap(code)


class _Loader:
    def __init__(self, loader, callbacks):
        self.loader = loader; self.callbacks = callbacks

    def create_module(self, spec):
        return self.loader.create_module(spec)

    def exec_module(self, module):
        self.loader.exec_module(module)
        self.callbacks.instrument(module)

    def __getattr__(self, name):
        return getattr(self.loader, name)


class Finder(importlib.abc.MetaPathFinder):
    def __init__(self, callbacks):
        self.callbacks = callbacks

    def find_spec(self, name, path=None, target=None):
        if name not in MODULES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path, target)
        if spec is None or spec.loader is None:
            return None
        spec.loader = _Loader(spec.loader, self.callbacks)
        return spec


def install(root=ROOT, data_root=DATA_ROOT, *, context_provider=None):
    """Never let a missing or broken sensor stop the engine from starting."""
    try:
        settings = json.loads((Path(root) / 'settings.json').read_text(encoding='utf-8'))
        directory = Path(data_root) / settings['session']
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        callbacks = EngineCallbacks(settings['session'], bytes.fromhex(settings['secret']), Sink(directory),
            context_provider=context_provider,
            fault_file=str(directory / FAULT_FILE) if settings.get('lab_faults') is True else None,
            kernel_checks=settings.get('lab_kernel_checks') is True)
        sys.meta_path.insert(0, Finder(callbacks))
        atexit.register(callbacks.flush_gaps)
        return True
    except Exception:
        return False
