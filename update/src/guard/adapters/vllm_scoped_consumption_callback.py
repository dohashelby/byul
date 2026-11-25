"""Source-qualified rank and closed native worker admission observations."""
from __future__ import annotations

from functools import wraps
import os
import sys
from types import FunctionType, ModuleType

from .vllm_observer_pin_rows import ROWS, candidate_sources

from . import vllm_cache_callback as cache, vllm_consumption_callback as v1, vllm_consumption_v2_callback as v2
from .vllm_worker_shard_callback import _tp_rank, _start_ticks
from .observer_runtime import QualifiedObserver


class _Scoped(QualifiedObserver):
    def _emit(self, row):
        with self.lock:
            row = dict(row)
            if row.get('point', '').startswith('GPUModelRunner.'):
                if not hasattr(self, '_admissions'):
                    self._admissions = {}
                    self._incoming = {}
                if row['point'] in ('GPUModelRunner._update_states', 'GPUModelRunner.add_requests'):
                    if row['phase'] == 'CALL':
                        self._incoming[row['call_sha256']] = [r['request_sha256'] for r in (row.get('fields', {}).get('received') or [])]
                    else:
                        incoming = self._incoming.pop(row['call_sha256'], ())
                        if row['phase'] == 'RETURN':
                            for request in incoming:
                                if len(self._admissions) >= 256 and request not in self._admissions:
                                    self._admissions.pop(next(iter(self._admissions)))
                                    self.gaps.add('SCOPED_ADMISSION_WINDOW_EXCEEDED')
                                self._admissions[request] = self.fingerprint('native-admission:' + row['call_sha256'] + ':' + request)
                row['consumption_scope'] = {'rank': _tp_rank(), 'admissions': dict(self._admissions), 'pid': os.getpid(), 'start_ticks': _start_ticks()}
            elif row.get('schema') not in {ASSIGNMENT_SCHEMA, COMPLETION_SELECTION_SCHEMA}:
                row['consumption_scope'] = {'pid': os.getpid(), 'start_ticks': _start_ticks()}
            # The prepared sensor subclasses route to a bounded file sink; the
            # standalone observer keeps the normal bounded in-memory report.
            super()._emit(row)


ASSIGNMENT_SCHEMA = 'guard-vllm-scoped-request-assignment/1'
_ASSIGNMENT = 'InputProcessor.assign_request_id'
_CALLER_MODULE = 'vllm.v1.engine.async_llm'
_CALLER = 'AsyncLLM.add_request'
_CALLER_BY_INPUT_PIN = {
    ROWS[v1.INPUT]['sha256']: '52cbf404f1dbc99c6bebf4a50a7eb18ff842a1f53106a45f365523192d816be1',
    cache.base.SOURCE_SHA[v1.INPUT]: cache.base.SOURCE_SHA[_CALLER_MODULE],
}
_MISSING = object()


def _qualified_caller(frame, request, request_type, pin):
    module = sys.modules.get(_CALLER_MODULE)
    if type(module) is not ModuleType or frame.f_globals is not vars(module):
        return None
    owner = vars(module).get('AsyncLLM')
    method = vars(owner).get('add_request') if isinstance(owner, type) else None
    if (type(method) is not FunctionType or method.__code__ is not frame.f_code
            or method.__globals__ is not frame.f_globals or frame.f_locals.get('request') is not request
            or type(request) is not request_type or vars(module).get('EngineCoreRequest') is not request_type):
        return None
    expected = cache._codes(module, {_CALLER_MODULE: (_CALLER,)}, {_CALLER_MODULE: pin}).get(_CALLER)
    return expected if expected == cache._digest(frame.f_code, _CALLER_MODULE) else None


def assignment_change(observer, module):
    """Observe one static assignment in the exact same-release API invocation."""
    owner = vars(module).get('InputProcessor')
    descriptor = vars(owner).get('assign_request_id') if isinstance(owner, type) else None
    original = descriptor.__func__ if type(descriptor) is staticmethod else None
    engine = sys.modules.get('vllm.v1.engine')
    request_type = vars(module).get('EngineCoreRequest')
    if (type(original) is not FunctionType or original.__globals__ is not vars(module)
            or type(engine) is not ModuleType or not isinstance(request_type, type)
            or vars(engine).get('EngineCoreRequest') is not request_type):
        return None
    input_pin = observer._source_pins.get(v1.INPUT)
    caller_pin = _CALLER_BY_INPUT_PIN.get(input_pin)
    if caller_pin is None:
        return None
    sources = {v1.INPUT: input_pin}
    expected = cache._codes(module, observer.targets, sources).get(_ASSIGNMENT)
    if expected != cache._digest(original.__code__, v1.INPUT):
        return None

    def qualified():
        installed = vars(owner).get('assign_request_id')
        return (os.getpid() == observer.pid and type(installed) is staticmethod
                and installed.__func__ is wrapped
                and cache._codes(module, observer.targets, sources).get(_ASSIGNMENT) == expected)

    def opaque(value):
        return cache.CacheCallbackObserver.fingerprint(observer, value) if type(value) is str and value else None

    @wraps(original)
    def wrapped(*args, **kwargs):
        witness = None
        if len(args) == 1 and not kwargs:
            try:
                frame = sys._getframe(1)
                request = args[0]
                if qualified():
                    caller_code = _qualified_caller(frame, request, request_type, caller_pin)
                    if caller_code:
                        fields = cache.base.attrs(request)
                        outer = opaque(fields.get('request_id'))
                        incoming = opaque(frame.f_locals.get('request_id'))
                        if outer and incoming == outer and fields.get('external_req_id', _MISSING) is None:
                            ticks = _start_ticks()
                            if type(ticks) is int and ticks > 0:
                                witness = caller_code, outer, ticks, observer._now()
            except Exception:
                pass  # Missing observation never changes the native request.
        result = original(*args, **kwargs)
        if witness is not None:
            try:
                finished = observer._now()
                caller_code, outer, ticks, started = witness
                fields = cache.base.attrs(request)
                native = opaque(fields.get('request_id'))
                if (result is None and native and finished >= started and opaque(fields.get('external_req_id')) == outer
                        and opaque(frame.f_locals.get('request_id')) == outer and _start_ticks() == ticks and qualified()
                        and _qualified_caller(frame, request, request_type, caller_pin) == caller_code):
                    with observer.lock:
                        observer.sequence += 1
                        call = opaque('call:' + str(observer.sequence))
                    observer._emit({
                        'schema': ASSIGNMENT_SCHEMA, 'phase': 'RETURN', 'point': _ASSIGNMENT,
                        'session_sha256': cache.base.sha(observer.session.encode()), 'process_sha256': observer.process_sha256,
                        'call_sha256': call, 'source_file_sha256': input_pin, 'entry_code_sha256': expected,
                        'caller_source_file_sha256': caller_pin, 'caller_code_sha256': caller_code,
                        'operation_started_ns': started, 'operation_finished_ns': finished,
                        'consumption_scope': {'pid': observer.pid, 'start_ticks': ticks},
                        'entry_qualified_before': True, 'entry_qualified_after': True,
                        'runtime_context_injected': observer.injected_context,
                        'fields': {'outer_request_sha256': outer, 'native_request_sha256': native},
                    })
            except Exception:
                pass  # Preserve the already completed native assignment.
        return result

    return (owner, 'assign_request_id', original), staticmethod(wrapped)


COMPLETION_MODULE = 'vllm.entrypoints.openai.completion.serving'
COMPLETION_SELECTION_SCHEMA = 'guard-vllm-scoped-completion-selection/1'
_COMPLETION_HANDLER = 'OpenAIServingCompletion._create_completion'
_SERVING_BASE = 'vllm.entrypoints.serve.engine.serving'
_COMPLETION_LOG = 'BaseServing._log_inputs'
_COMPLETION_SOURCES = {
    '75d05ddddf303734a078a3e42fcb173fef58ea56da09bc0b5c7525cb9bbbf527':
        '70143311acc2230b925e7f3603b8b9314f029ff5e98d5af990b3bdca691331f1',
    cache.base.SOURCE_SHA[COMPLETION_MODULE]: cache.base.SOURCE_SHA[_SERVING_BASE],
}


def _ordinary_completion(frame, module, owner, handler, args):
    if (frame.f_code is not handler.__code__ or frame.f_globals is not vars(module)
            or len(args) < 2 or type(args[0]) is not owner):
        return None
    local = frame.f_locals
    if local.get('self') is not args[0] or local.get('request_id_item') is not args[1]:
        return None
    request, raw_request = local.get('request'), local.get('raw_request')
    if (type(request) is not vars(module).get('CompletionRequest')
            or type(raw_request) is not vars(module).get('Request')):
        return None
    fields = cache.base.attrs(request)
    if (fields.get('stream') is not False or fields.get('use_beam_search') is not False
            or type(fields.get('n')) is not int or fields.get('n') != 1):
        return None
    scope = cache.base.attrs(raw_request).get('scope')
    inputs = local.get('engine_inputs')
    if (type(scope) is not dict or scope.get('type') != 'http' or scope.get('method') != 'POST'
            or scope.get('path') != '/v1/completions' or type(inputs) is not list or len(inputs) != 1):
        return None
    return local.get('request_id'), args[1]


def install_completion_selection(observer, module):
    """Record the response-base candidate and selected opaque child without observing a response."""
    owner = vars(module).get('OpenAIServingCompletion')
    base = sys.modules.get(_SERVING_BASE)
    if (type(base) is not ModuleType or not isinstance(owner, type)
            or sys.modules.get(COMPLETION_MODULE) is not module or '_log_inputs' in vars(owner)):
        return False
    base_owner = vars(base).get('BaseServing')
    handler = vars(owner).get('_create_completion')
    if not isinstance(base_owner, type) or base_owner not in owner.__mro__:
        return False
    logger_owner = next((parent for parent in owner.__mro__ if '_log_inputs' in vars(parent)), None)
    original = vars(base_owner).get('_log_inputs')
    if (logger_owner is not base_owner or type(original) is not FunctionType or original.__globals__ is not vars(base)
            or type(handler) is not FunctionType or handler.__globals__ is not vars(module)):
        return False
    source = cache.source_pin(module, {COMPLETION_MODULE: tuple(_COMPLETION_SOURCES)})
    base_pin = _COMPLETION_SOURCES[source]
    handler_targets = {COMPLETION_MODULE: (_COMPLETION_HANDLER,)}
    log_targets = {_SERVING_BASE: (_COMPLETION_LOG,)}

    def source_codes():
        return (cache._codes(module, handler_targets, {COMPLETION_MODULE: source}).get(_COMPLETION_HANDLER),
                cache._codes(base, log_targets, {_SERVING_BASE: base_pin}).get(_COMPLETION_LOG))

    expected_codes = (cache._digest(handler.__code__, COMPLETION_MODULE), cache._digest(original.__code__, _SERVING_BASE))
    if source_codes() != expected_codes:
        return False

    def qualified():
        return (os.getpid() == observer.pid and vars(owner).get('_log_inputs') is wrapped
                and vars(owner).get('_create_completion') is handler and vars(base_owner).get('_log_inputs') is original
                and sys.modules.get(COMPLETION_MODULE) is module and sys.modules.get(_SERVING_BASE) is base
                and source_codes() == expected_codes)

    def fingerprints(frame, args):
        selection = _ordinary_completion(frame, module, owner, handler, args)
        if selection is None:
            return None
        fingerprints = tuple(cache.CacheCallbackObserver.fingerprint(observer, value)
                             for value in selection if type(value) is str and value)
        return fingerprints if len(fingerprints) == 2 and all(fingerprints) else None

    @wraps(original)
    def wrapped(*args, **kwargs):
        witness = None
        try:
            frame = sys._getframe(1)
            selected = fingerprints(frame, args)
            if selected is not None and qualified():
                ticks = _start_ticks()
                if type(ticks) is int and ticks > 0:
                    witness = selected, ticks, observer._now()
        except Exception:
            pass  # This passive witness cannot affect the original call.
        result = original(*args, **kwargs)
        if witness is not None:
            try:
                finished = observer._now()
                selected, ticks, started = witness
                if (result is None and started <= finished and _start_ticks() == ticks
                        and fingerprints(frame, args) == selected and qualified()):
                    with observer.lock:
                        observer.sequence += 1
                        call = cache.CacheCallbackObserver.fingerprint(observer, 'call:' + str(observer.sequence))
                    observer._emit({
                        'schema': COMPLETION_SELECTION_SCHEMA, 'phase': 'RETURN', 'point': _COMPLETION_LOG,
                        'session_sha256': cache.base.sha(observer.session.encode()), 'process_sha256': observer.process_sha256,
                        'call_sha256': call, 'source_file_sha256': base_pin, 'entry_code_sha256': expected_codes[1],
                        'caller_source_file_sha256': source, 'caller_code_sha256': expected_codes[0],
                        'operation_started_ns': started, 'operation_finished_ns': finished,
                        'consumption_scope': {'pid': observer.pid, 'start_ticks': ticks},
                        'entry_qualified_before': True, 'entry_qualified_after': True,
                        'runtime_context_injected': observer.injected_context,
                        'fields': {'response_request_sha256': selected[0], 'outer_request_sha256': selected[1]},
                    })
            except Exception:
                pass
        return result

    owner._log_inputs = wrapped
    return True


class ScopedConsumptionObserver(_Scoped, v1.ConsumptionObserver):
    sources = candidate_sources(v1.ConsumptionObserver.sources, v1.TARGETS)


class ScopedConsumptionV2Observer(_Scoped, v2.ConsumptionV2Observer):
    sources = candidate_sources(v2.SOURCES, v2.TARGETS)
