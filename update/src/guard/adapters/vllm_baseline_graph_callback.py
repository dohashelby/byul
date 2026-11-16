"""Bounded 0.7 V1 PiecewiseBackend graph branch observation for REQ-20.

This explicit candidate uses the existing exact-source callback wrapper. It
never replaces the backend's ``copy_and_call`` closure, reads tensor values,
or asserts GPU completion or request-level output consumption. The default
0.7 engine-sensor gate remains closed.
"""
from __future__ import annotations

import re

from . import vllm_cache_callback as cache


MODULE = 'vllm.compilation.backends'
POINT = 'PiecewiseBackend.__call__'
SOURCE_SHA256 = 'afd9a1a182409ab289cde20fb0dd0e8c31efc4bb04890fcd58b1f870339a5fb5'
SOURCE_BYTES = 35385
SCHEMA = 'guard-vllm-baseline-piecewise-graph/1'
TARGETS = {MODULE: (POINT,)}
SOURCES = {MODULE: SOURCE_SHA256}
MAX_SHAPE_INDEX = 16
MAX_SHAPES = 256
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_COMMON = frozenset({'schema', 'session_sha256', 'process_sha256', 'call_sha256',
    'parent_call_sha256', 'point', 'source_file_sha256', 'entry_code_sha256',
    'entry_qualified_before', 'runtime_context_injected', 'authenticated_identity_verified',
    'actual_tensor_consumption_verified', 'transitive_dependencies_verified',
    'native_profile_installed', 'phase', 'operation_started_ns',
    'operation_finished_ns', 'fields'})
_TERMINAL = frozenset({'entry_qualified_after', 'native_started_ns', 'native_finished_ns'})
_ENTRY = frozenset({'branch', 'shape', 'entry_token', 'graph_present',
    'graph_token', 'output_token', 'returned_entry_output'})


def _uint(value):
    return value if type(value) is int and 0 <= value < 2**63 else None


class BaselineGraphObserver(cache.CacheCallbackObserver):
    targets = TARGETS
    sources = SOURCES
    schema = SCHEMA
    profile = 'source-bound-baseline-piecewise-graph-candidate-v1'

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        if point != POINT or not args:
            self.gaps.add('BASELINE_GRAPH_CALL_SHAPE_UNAVAILABLE')
            return {'branch': 'unsupported'}
        owner = args[0]
        fields = cache.base.attrs(owner)
        first_finished = fields.get('first_run_finished')
        if type(first_finished) is not bool:
            self.gaps.add('BASELINE_GRAPH_FIRST_RUN_STATE_UNAVAILABLE')
            return {'branch': 'unsupported'}
        if not first_finished:
            return {'branch': 'first-general', 'shape': None, 'entry_token': None,
                'graph_present': False, 'graph_token': None, 'output_token': None,
                'returned_entry_output': None}
        indices = fields.get('sym_shape_indices')
        if (type(indices) not in (list, tuple) or not 1 <= len(indices) <= MAX_SHAPE_INDEX
                or type(indices[0]) is not int or indices[0] < 0 or indices[0] + 1 >= len(args)):
            self.gaps.add('BASELINE_GRAPH_SHAPE_INDEX_UNAVAILABLE')
            return {'branch': 'unsupported'}
        shape = _uint(args[indices[0] + 1])
        entries = fields.get('concrete_size_entries')
        if (shape is None or type(entries) is not dict or len(entries) > MAX_SHAPES
                or any(type(key) is not int or _uint(key) is None for key in entries)):
            self.gaps.add('BASELINE_GRAPH_ENTRY_SHAPE_UNAVAILABLE')
            return {'branch': 'unsupported'}
        if shape not in entries:
            return {'branch': 'general', 'shape': shape, 'entry_token': None,
                'graph_present': False, 'graph_token': None, 'output_token': None,
                'returned_entry_output': None}
        entry = entries[shape]
        state = cache.base.attrs(entry)
        token = self.token(entry)
        graph = state.get('cudagraph')
        if (token is None or type(state.get('use_cudagraph')) is not bool
                or type(state.get('need_to_compile')) is not bool
                or type(state.get('compiled')) is not bool
                or _uint(state.get('num_finished_warmup')) is None):
            self.gaps.add('BASELINE_GRAPH_ENTRY_STATE_UNAVAILABLE')
            return {'branch': 'unsupported'}
        config = cache.base.attrs(fields.get('compilation_config'))
        warmups = _uint(config.get('cudagraph_num_of_warmups'))
        if warmups is None:
            self.gaps.add('BASELINE_GRAPH_WARMUP_STATE_UNAVAILABLE')
            return {'branch': 'unsupported'}
        branch = ('compile' if state.get('need_to_compile') and not state.get('compiled')
            else 'runnable' if not state.get('use_cudagraph')
            else 'replay' if graph is not None
            else 'warmup' if state.get('num_finished_warmup') < warmups else 'capture')
        return {'branch': branch, 'shape': shape, 'entry_token': token,
            'graph_present': graph is not None, 'graph_token': self.token(graph),
            'output_token': self.token(state.get('output')),
            'returned_entry_output': (result is not None and state.get('output') is not None
                and result is state.get('output')) if returned and branch == 'replay' else None}


def instrument(module, observer):
    """Install only after the existing wrapper verifies file and live code."""
    if vars(module).get('__name__') != MODULE or not isinstance(observer, BaselineGraphObserver):
        raise ValueError('BASELINE_GRAPH_SOURCE_UNSUPPORTED')
    owner, method, _ = cache._function(module, POINT)
    wrapped = observer.wrap(module, POINT)
    setattr(owner, method, wrapped)
    return True


def project_piecewise(report):
    """Project actual qualified branches; REQ-20's V2 verdict is untouched."""
    if (type(report) is not dict or report.get('profile') != BaselineGraphObserver.profile
            or type(report.get('events')) is not list or len(report['events']) > cache.MAX_EVENTS
            or type(report.get('missing')) is not list or len(report['missing']) > 256
            or any(type(gap) is not str or len(gap) > 128 for gap in report['missing'])
            or report.get('native_profile_installed') is not False
            or type(report.get('session_sha256')) is not str or _SHA.fullmatch(report['session_sha256']) is None
            or type(report.get('process_sha256')) is not str or _SHA.fullmatch(report['process_sha256']) is None):
        raise ValueError('BASELINE_GRAPH_REPORT_INVALID')
    groups, completed, captures, replays, shapes = {}, 0, 0, 0, set()
    gaps = set(report['missing'])
    for position, event in enumerate(report['events']):
        if type(event) is not dict:
            gaps.add('baseline_graph_row_unqualified')
            continue
        identity = (event.get('session_sha256'), event.get('process_sha256'), event.get('call_sha256'))
        if any(type(value) is not str or _SHA.fullmatch(value) is None for value in identity):
            gaps.add('baseline_graph_call_identity_unavailable')
            continue
        groups.setdefault(identity, []).append((position, event))
    qualified = 0
    for identity, pair in groups.items():
        if (len(pair) != 2 or pair[0][1].get('phase') != 'CALL'
                or type(pair[1][1].get('phase')) is not str
                or pair[1][1]['phase'] not in {'RETURN', 'RAISE'}):
            gaps.add('baseline_graph_pair_unavailable')
            continue
        (_, call), (_, terminal) = pair
        start, end = call.get('operation_started_ns'), terminal.get('operation_finished_ns')
        native_start = terminal.get('native_started_ns')
        if (set(call) != _COMMON or set(terminal) != _COMMON | _TERMINAL
                or call.get('schema') != SCHEMA or terminal.get('schema') != SCHEMA
                or any(event.get('point') != POINT or event.get('source_file_sha256') != SOURCE_SHA256
                       or event.get('entry_qualified_before') is not True for event in (call, terminal))
                or terminal.get('entry_qualified_after') is not True
                or identity[:2] != (report['session_sha256'], report['process_sha256'])
                or call.get('entry_code_sha256') != terminal.get('entry_code_sha256')
                or type(call.get('entry_code_sha256')) is not str
                or _SHA.fullmatch(call['entry_code_sha256']) is None
                or (call.get('parent_call_sha256') is not None and
                    (type(call['parent_call_sha256']) is not str
                     or _SHA.fullmatch(call['parent_call_sha256']) is None))
                or any(call.get(field) != terminal.get(field) for field in (
                    'parent_call_sha256', 'runtime_context_injected', 'authenticated_identity_verified',
                    'actual_tensor_consumption_verified', 'transitive_dependencies_verified', 'native_profile_installed'))
                or any(type(event.get('runtime_context_injected')) is not bool for event in (call, terminal))
                or any(event.get('authenticated_identity_verified') is not False
                       or event.get('actual_tensor_consumption_verified') is not False
                       or event.get('transitive_dependencies_verified') is not False
                       or event.get('native_profile_installed') is not False for event in (call, terminal))
                or type(start) is not int or type(end) is not int or type(native_start) is not int
                or not 0 <= start <= native_start <= end < 2**63
                or call.get('operation_finished_ns') != start
                or terminal.get('operation_started_ns') != start
                or terminal.get('native_finished_ns') != end
                or type(call.get('fields')) is not dict or type(terminal.get('fields')) is not dict):
            gaps.add('baseline_graph_pair_unqualified')
            continue
        qualified += 1
        if terminal['phase'] == 'RAISE':
            gaps.add('baseline_graph_call_raised')
            continue
        completed += 1
        prior, fields = call['fields'], terminal['fields']
        if type(prior.get('branch')) is not str or prior['branch'] not in {'capture', 'replay'}:
            continue
        if set(prior) != _ENTRY or set(fields) != _ENTRY:
            gaps.add('baseline_graph_entry_fields_unavailable')
            continue
        shape, entry = prior.get('shape'), prior.get('entry_token')
        if (type(shape) is not int or not 0 <= shape < 2**63
                or type(entry) is not str or _SHA.fullmatch(entry) is None
                or fields.get('shape') != shape or fields.get('entry_token') != entry):
            gaps.add('baseline_graph_entry_identity_unavailable')
            continue
        graph_before, graph_after = prior.get('graph_token'), fields.get('graph_token')
        output_before, output_after = prior.get('output_token'), fields.get('output_token')
        if prior['branch'] == 'capture':
            if (prior.get('graph_present') is False and prior.get('graph_token') is None
                    and fields.get('branch') == 'replay' and fields.get('graph_present') is True
                    and type(graph_after) is str and _SHA.fullmatch(graph_after) is not None
                    and type(output_after) is str and _SHA.fullmatch(output_after) is not None
                    and (output_before is None or output_before != output_after)):
                captures += 1
            else:
                gaps.add('baseline_graph_capture_generation_unavailable')
        else:
            if (fields.get('branch') != 'replay' or prior.get('graph_present') is not True
                    or fields.get('graph_present') is not True
                    or any(type(token) is not str or _SHA.fullmatch(token) is None
                           for token in (graph_before, graph_after, output_before, output_after))):
                gaps.add('baseline_graph_object_generation_unavailable')
            elif graph_before != graph_after:
                gaps.add('baseline_graph_generation_changed')
            elif output_before != output_after:
                gaps.add('baseline_graph_output_generation_changed')
            elif fields.get('returned_entry_output') is True:
                replays += 1
                shapes.add(shape)
            else:
                gaps.add('baseline_graph_cached_output_identity_unavailable')
    if replays == 0:
        gaps.add('baseline_graph_replay_unobserved')
    gaps.update({'graph_input_refresh_unobserved', 'case_graph_consumption_unobserved',
        'graph_completion_unobserved', 'independent_output_control_unavailable'})
    return {'qualified_calls': qualified, 'qualified_returns': completed,
        'capture_returns': captures, 'replay_returns_cached_output': replays,
        'replay_shapes': sorted(shapes), 'missing': sorted(gaps),
        'producer_authenticated': False, 'violation': None, 'full_scope': False}
