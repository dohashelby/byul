"""Prepared in-pod installer for the source-bound cache and distributed callbacks.

Ships as a self-contained package beside the callback modules it installs. A
target that does not match its pins stays unwrapped and leaves a gap row; the
engine keeps its original behaviour. Rows hold keyed fingerprints only.
"""
from __future__ import annotations

from .vllm_observer_pin_rows import candidate_sources, layout

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
from . import vllm_graph_replay_callback as graph_replay
from . import vllm_weights_lifetime_callback as weights_lifetime
from . import vllm_comm_buffer_callback as comm_buffer
from . import vllm_attempt_callback as attempt
from . import vllm_transition_callback as transition
from . import vllm_health_callback as health
from . import vllm_profiler_callback as profiler
from . import vllm_consumption_callback as consumption
from . import vllm_consumption_v2_callback as consumption_v2
from . import vllm_supply_read_callback as supply_read
from . import vllm_worker_shard_callback as worker_shard
from . import vllm_transition_dispatch_callback as transition_dispatch
from . import vllm_execution_link_callback as execution_link
from . import vllm_auxiliary_link_callback as auxiliary_link
from . import vllm_scoped_consumption_callback as scoped_consumption

PROFILE = 'vllm-engine-callback-metadata/1'
SCHEMA = 'guard-engine-callback-events/1'
ROOT = '/guard-lifecycle/engine-callbacks'
DATA_ROOT = '/guard-lifecycle/data'
SLOT_MODULE = 'vllm.v1.worker.gpu_input_batch'
SLOT_TARGETS = {SLOT_MODULE: ('InputBatch.add_request', 'InputBatch.remove_request')}
SLOT_SOURCE = candidate_sources({SLOT_MODULE: 'e7e57887b396014a84d64cab26bf678f982844fea15361b09d95f36bd78bc58d'}, SLOT_TARGETS)
# V2 model runner (vLLM 0.31.0 기본 GPU runner). 자리 배정·회수와 sampler가 실제로
# 받은 요청별 상태를 CPU 쪽 배열에서만 읽는다. GPU tensor를 읽거나 동기화하지 않는다.
SLOT_V2_STATES = 'vllm.v1.worker.gpu.states'
SLOT_V2_SAMPLER = 'vllm.v1.worker.gpu.sample.sampler'
SLOT_V2_TARGETS = {SLOT_V2_STATES: ('RequestState.add_request', 'RequestState.remove_request'),
                   SLOT_V2_SAMPLER: ('Sampler.add_request', 'Sampler.__call__')}
SLOT_V2_SOURCE = candidate_sources({SLOT_V2_STATES: '8c899b8ef63d95f450876d3f01e56772f9534938a95383a46659fddf85573fa3',
                  SLOT_V2_SAMPLER: 'ddbcdab46010bf38a5fab9b9b6a2deed2ec5e883ebfe81e83f6d22f41e546771'}, SLOT_V2_TARGETS)
MAX_SAMPLED_SLOTS = 256
# Block-level lookups feed no projection. Wrapping them made every cache hit
# slower in proportion to its length and multiplied the session rows.
CACHE_TARGETS = {'vllm.v1.core.kv_cache_manager': cache.TARGETS['vllm.v1.core.kv_cache_manager']}
MODULES = {**CACHE_TARGETS, **distributed.TARGETS, **SLOT_TARGETS, **SLOT_V2_TARGETS, **kernel_bounds.TARGETS, **graph_replay.TARGETS, **weights_lifetime.TARGETS, **comm_buffer.TARGETS}
# #196 request-attempt(P1): settings의 request_attempts가 참일 때만 감싼다. KV 모듈은 cache 관측과 같은 모듈이라 지점별로 관측기를 고른다.
MODULES = {**MODULES, attempt.SCHEDULER: attempt.TARGETS[attempt.SCHEDULER],
           attempt.KV: MODULES.get(attempt.KV, ()) + attempt.TARGETS[attempt.KV]}
# #196 generation-transition(P1): settings의 generation_transitions가 참일 때만 감싼다(request_attempts와 함께 쓴다).
MODULES = {**MODULES, transition.CORE: transition.TARGETS[transition.CORE]}
# #196 health-worker-causal(P1): settings의 health_causal이 참일 때만 감싼다(request_attempts와 함께 쓴다).
MODULES = {**MODULES, health.ASYNC_LLM: health.TARGETS[health.ASYNC_LLM]}
# #196 profiler-control(P1): settings의 profiler_control이 참일 때만 감싼다. 같은 모듈의 다른 선택 관측과 지점별로 나눈다.
for _name, _points in profiler.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
# P2 runner 최종 소비: settings의 runner_consumption이 참일 때만 감싼다.
for _name, _points in consumption.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
# 같은 설정의 V2 runner 경로: V2 runner 모듈이 불릴 때만 감싼다(lab kernel 대조 세션은 graph replay가 같은 지점을 쓴다).
for _name, _points in consumption_v2.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
# SUP-06 worker shard(B 의존 반환 5): settings의 worker_shards가 참일 때만 감싼다. multiproc 모듈은 분산 관측과 같은 모듈이다.
for _name, _points in worker_shard.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
for _name, _points in transition_dispatch.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
for _name, _points in execution_link.TARGETS.items():
    MODULES[_name] = tuple(dict.fromkeys(MODULES.get(_name, ()) + _points))
for _name in auxiliary_link.MODULES:
    MODULES.setdefault(_name, ())
# 선택 관측(이름, 대상): 같은 모듈에 여러 개가 있을 수 있어 지점별로 관측기를 고른다.
OPTIONAL = (('link', execution_link.TARGETS), ('attempt', attempt.TARGETS), ('transition', transition.TARGETS), ('health', health.TARGETS), ('profiler', profiler.TARGETS),
            ('consumption', consumption.TARGETS), ('consumption_v2', consumption_v2.TARGETS), ('shard', worker_shard.TARGETS),
            ('dispatch', transition_dispatch.TARGETS))
OPTIONAL_MODULES = frozenset(name for _, targets in OPTIONAL for name in targets)
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
        profile = layout(self, SLOT_V2_SAMPLER)
        bias = (fields.get(profile['bias_attribute']) if 'bias_attribute' in profile else
                next((p for p in (fields.get('logits_processors') or ()) if hasattr(p, 'num_allowed_token_ids')), None))
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
            position = layout(self, SLOT_V2_SAMPLER).get('sampling_params_position', 2)
            params = args[position] if len(args) > position else kwargs.get('sampling_params')
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


class KernelObserver(_Keyed, kernel_bounds.KernelBoundsObserver):
    pass


class GraphObserver(_Keyed, graph_replay.GraphReplayObserver):
    pass


class WeightsObserver(_Keyed, weights_lifetime.WeightsLifetimeObserver):
    pass


class CommObserver(_Keyed, comm_buffer.CommBufferObserver):
    pass


class AttemptObserver(_Keyed, attempt.AttemptObserver):
    pass


class TransitionObserver(_Keyed, transition.TransitionObserver):
    pass


class HealthObserver(_Keyed, health.HealthObserver):
    pass


class ProfilerObserver(_Keyed, profiler.ProfilerObserver):
    pass


class ConsumptionObserver(_Keyed, consumption.ConsumptionObserver):
    pass


class ConsumptionV2Observer(_Keyed, consumption_v2.ConsumptionV2Observer):
    pass


class WorkerShardObserver(_Keyed, worker_shard.WorkerShardObserver):
    pass


class TransitionDispatchObserver(_Keyed, transition_dispatch.TransitionDispatchObserver):
    pass


class ExecutionLinkObserver(execution_link.ExecutionLinkObserver):
    # Preserve exact IDs across the actual transport; do not use _Keyed.fingerprint.
    _emit = _Keyed._emit


class BoundedExecutionLinkObserver(ExecutionLinkObserver):
    """Default connection observation never hashes model parameter contents."""
    def _cpu_parameter(self, model):
        self.gaps.add('EXECUTION_LINK_PARAMETER_CONTENT_NOT_SELECTED')
        return None


class ScopedConsumptionObserver(scoped_consumption._Scoped, _Keyed, consumption.ConsumptionObserver):
    pass


class ScopedConsumptionV2Observer(scoped_consumption._Scoped, _Keyed, consumption_v2.ConsumptionV2Observer):
    pass


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
    def __init__(self, session, secret, sink, *, context_provider=None, clock=None, fault_file=None, kernel_checks=False,
                 request_attempts=False, generation_transitions=False, health_causal=False, profiler_control=False,
                 runner_consumption=False, worker_shards=False, transition_dispatches=False, execution_links=False,
                 auxiliary_roots=None, scoped_runner_consumption=False, execution_link_content_hashes=False):
        self.sink = sink; self.fault_file = fault_file
        options = {'sink': sink, 'context_provider': context_provider, 'clock': clock}
        self.cache = CacheObserver(session, **options)
        self.distributed = DistributedObserver(session, **options)
        self.slot = SlotObserver(session, **options)
        self.slot_v2 = SlotV2Observer(session, **options)
        self.weights = WeightsObserver(session, **options)
        self.comm = CommObserver(session, fence=kernel_checks, **options)
        # REQ-19 kernel 대조는 GPU 동기화가 필요하다. 소유 lab 엔진에서 명시했을 때만 만든다.
        self.kernel = KernelObserver(session, **options) if kernel_checks else None
        self.graph = GraphObserver(session, kernel=self.kernel, **options) if kernel_checks else None
        self.cache.secret = self.distributed.secret = self.slot.secret = self.slot_v2.secret = self.weights.secret = self.comm.secret = secret
        if self.kernel is not None:
            self.kernel.secret = self.graph.secret = secret
        # 매 단계 scheduler를 감싸므로 명시한 세션에서만 만든다(다른 점검의 시간 측정에 부담을 주지 않게).
        self.attempt = AttemptObserver(session, **options) if request_attempts else None
        if self.attempt is not None:
            self.attempt.secret = secret
        self.transition = TransitionObserver(session, **options) if generation_transitions else None
        if self.transition is not None:
            self.transition.secret = secret
        self.health = HealthObserver(session, **options) if health_causal else None
        if self.health is not None:
            self.health.secret = secret
        self.profiler = ProfilerObserver(session, **options) if profiler_control else None
        if self.profiler is not None:
            self.profiler.secret = secret
        self.consumption = (ScopedConsumptionObserver if scoped_runner_consumption else ConsumptionObserver)(session, **options) if runner_consumption or scoped_runner_consumption else None
        if self.consumption is not None:
            self.consumption.secret = secret
        self.consumption_v2 = (ScopedConsumptionV2Observer if scoped_runner_consumption else ConsumptionV2Observer)(session, **options) if runner_consumption or scoped_runner_consumption else None
        if self.consumption_v2 is not None:
            self.consumption_v2.secret = secret
        self.shard = WorkerShardObserver(session, **options) if worker_shards else None
        if self.shard is not None:
            self.shard.secret = secret
        self.dispatch = TransitionDispatchObserver(session, **options) if transition_dispatches else None
        if self.dispatch is not None:
            self.dispatch.secret = secret
        self.link = (ExecutionLinkObserver if execution_link_content_hashes else BoundedExecutionLinkObserver)(session, **options) if execution_links else None
        if self.link is not None:
            self.link.secret = secret
        self.auxiliary = auxiliary_link.AuxiliaryLinkObserver(auxiliary_roots, session=session, link=self.link, sink=sink) if self.link is not None and auxiliary_roots else None

    def instrument(self, module):
        """Validate every point of a module before changing any of them."""
        name = vars(module).get('__name__')
        if name in auxiliary_link.MODULES:
            if self.auxiliary is None:
                return False
            try:
                return self.auxiliary.install(module)
            except Exception:
                self.gap('AUXILIARY_LINK_INSTALL_UNAVAILABLE', name)
                return False
        if name in kernel_bounds.TARGETS or name in graph_replay.TARGETS:
            if self.kernel is None:
                # kernel 대조를 끈 세션: V2 runner 모듈에서는 켜진 선택 관측(P2 V2 소비)만 감쌀 수 있다.
                return self._instrument_optional(module, name) if name in OPTIONAL_MODULES else False
            if name in OPTIONAL_MODULES and getattr(self, 'consumption_v2', None) is not None:
                self.gap('CONSUMPTION_V2_SKIPPED_KERNEL_CHECKS', name)
            try:
                if name in kernel_bounds.TARGETS:
                    return kernel_bounds.instrument(module, self.kernel)
                return graph_replay.instrument(module, self.graph)
            except Exception:
                self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
                return False
        if name in OPTIONAL_MODULES:
            return self._instrument_optional(module, name)
        observer = self._default_observer(name)
        try:
            changes = [(cache._function(module, point), observer.wrap(module, point)) for point in MODULES[name]]
        except Exception:
            self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
            return False
        for (owner, method, _), wrapped in changes:
            setattr(owner, method, self._fault(wrapped) if method == FAULT_POINT and self.fault_file else wrapped)
        return True

    def _default_observer(self, name):
        return (self.cache if name in CACHE_TARGETS else self.slot if name in SLOT_TARGETS
                else self.slot_v2 if name in SLOT_V2_TARGETS else self.weights if name in weights_lifetime.TARGETS
                else self.comm if name in comm_buffer.TARGETS
                else self.distributed)

    def _instrument_optional(self, module, name):
        """선택 관측이 있는 모듈: 지점마다 켜진 관측기를 고른다. 꺼진 관측기의 지점은 건드리지 않는다.

        선택 관측에 속하지 않는 지점(예: multiproc 모듈의 분산 관측 지점)은 그 모듈의 기본 관측기가 그대로 감싼다.
        """
        plan = []
        for point in MODULES[name]:
            chosen = None; optional = False
            for attribute, targets in OPTIONAL:
                if point in targets.get(name, ()):
                    chosen = getattr(self, attribute); optional = True
                    if chosen is not None:
                        break
            if chosen is None and name in CACHE_TARGETS and point in CACHE_TARGETS[name]:
                chosen = self.cache
            elif chosen is None and not optional and name in distributed.TARGETS and point in distributed.TARGETS[name]:
                chosen = self._default_observer(name)
            if chosen is not None:
                plan.append((chosen, point))
        try:
            changes = [((module.InputProcessor, 'assign_request_id', None), observer.wrap_assignment(module))
                       if observer is getattr(self, 'link', None) and point == 'InputProcessor.assign_request_id'
                       else ((module, 'apply_grammar_bitmask', None), observer.wrap_grammar(module))
                       if observer is getattr(self, 'link', None) and point == 'apply_grammar_bitmask'
                       else (cache._function(module, point), observer.wrap(module, point)) for observer, point in plan]
        except Exception:
            self.gap('ENGINE_CALLBACK_INSTALL_UNAVAILABLE', name)
            return False
        for (owner, method, _), wrapped in changes:
            # multiproc 모듈이 선택 관측 경로로 와도 기존 lab fault 감싸기를 그대로 유지한다.
            setattr(owner, method, self._fault(wrapped) if method == FAULT_POINT and self.fault_file else wrapped)
        return bool(changes)

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
        for observer in (self.consumption, getattr(self, 'consumption_v2', None)):
            verify = getattr(observer, 'verify_sources', None)
            if verify:
                try: verify()
                except Exception: observer.gaps.add('OBSERVER_SOURCE_POSTCHECK_FAILED')
        kernel = self.kernel.gaps | self.graph.gaps if self.kernel is not None else set()
        extra = set().union(*(o.gaps for o in (self.attempt, self.transition, self.health, self.profiler, self.consumption, getattr(self, 'consumption_v2', None), getattr(self, 'shard', None), getattr(self, 'dispatch', None), getattr(self, 'link', None), getattr(self, 'auxiliary', None), getattr(self, 'supply', None), getattr(self, 'supply_watch', None)) if o is not None))
        for code in sorted(self.cache.gaps | self.distributed.gaps | self.slot.gaps | self.slot_v2.gaps | self.weights.gaps | self.comm.gaps | kernel | extra):
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
        from .observer_runtime import JournalSink
        sink = JournalSink(directory) if settings.get('scoped_runner_consumption') is True else Sink(directory)
        callbacks = EngineCallbacks(settings['session'], bytes.fromhex(settings['secret']), sink,
            context_provider=context_provider,
            fault_file=str(directory / FAULT_FILE) if settings.get('lab_faults') is True else None,
            kernel_checks=settings.get('lab_kernel_checks') is True,
            request_attempts=settings.get('request_attempts') is True,
            generation_transitions=settings.get('generation_transitions') is True,
            health_causal=settings.get('health_causal') is True,
            profiler_control=settings.get('profiler_control') is True,
            runner_consumption=settings.get('runner_consumption') is True,
            worker_shards=settings.get('worker_shards') is True,
            transition_dispatches=settings.get('transition_dispatches') is True,
            execution_links=settings.get('execution_links') is True,
            execution_link_content_hashes=settings.get('execution_link_content_hashes') is True,
            scoped_runner_consumption=settings.get('scoped_runner_consumption') is True,
            auxiliary_roots=settings.get('supply_roots'))
        sys.meta_path.insert(0, Finder(callbacks))
        if settings.get('supply_roots'):
            # SUP-03(P2): 선언한 공급 루트 아래에서 실제로 연 파일. audit hook은 원 코드를 바꾸지 않는다.
            callbacks.supply = supply_read.SupplyReadObserver(settings['supply_roots'], sink=callbacks.sink,
                                                              process_sha256=callbacks.cache.process_sha256)
            sys.addaudithook(callbacks.supply.hook)
            if settings.get('supply_watch') is True:
                # native 읽기(inotify): Pod 안에서 잠금 파일을 먼저 만든 프로세스 하나만 지킨다.
                try:
                    lock = os.open(directory / 'supply-watch.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    os.write(lock, str(os.getpid()).encode()); os.close(lock)
                    callbacks.supply_watch = supply_read.SupplyWatcher(settings['supply_roots'], sink=callbacks.sink,
                                                                       process_sha256=callbacks.cache.process_sha256)
                    callbacks.supply_watch.start()
                except FileExistsError:
                    pass
        if hasattr(sink, 'close'):
            atexit.register(sink.close)
        atexit.register(callbacks.flush_gaps)
        return True
    except Exception:
        return False
