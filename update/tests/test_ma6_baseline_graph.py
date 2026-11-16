"""Exact 0.7 PiecewiseBackend body into bounded REQ-20 partial projection."""
from __future__ import annotations

import copy
from contextlib import ExitStack, nullcontext
import hashlib
import json
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import vllm_baseline_graph_callback as graph


FIXTURE = Path(__file__).parent / 'fixtures/vllm-070-graph/backends.py'


class Entry:
    pass


class Result:
    secret = 'EXAMPLE-private-output-value'


class Graph:
    def __init__(self, *, raises=False):
        self.calls = 0
        self.raises = raises

    def replay(self):
        self.calls += 1
        if self.raises:
            raise RuntimeError('EXAMPLE-native-replay-error')


def exact_module(tmp_path):
    assert len(FIXTURE.read_bytes()) == graph.SOURCE_BYTES
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == graph.SOURCE_SHA256
    path = tmp_path / 'backends.py'
    path.write_bytes(FIXTURE.read_bytes())
    module = types.ModuleType(graph.MODULE)
    module.__file__ = str(path)
    root = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=0)
    found = []
    def visit(code):
        if code.co_qualname == graph.POINT:
            found.append(code)
        for item in code.co_consts:
            if type(item) is types.CodeType:
                visit(item)
    visit(root)
    assert len(found) == 1
    cls = type('PiecewiseBackend', (), {})
    cls.__call__ = types.FunctionType(found[0], vars(module))
    module.PiecewiseBackend = cls
    return module


def world(tmp_path, *, raises=False):
    module = exact_module(tmp_path)
    rows = []
    observer = graph.BaselineGraphObserver('a' * 32, sink=rows.append,
        context_provider=lambda: {'fixture': True})
    original = module.PiecewiseBackend.__call__
    assert graph.instrument(module, observer)
    assert module.PiecewiseBackend.__call__ is not original
    output, replay = Result(), Graph(raises=raises)
    entry = Entry()
    vars(entry).update(need_to_compile=False, compiled=True, use_cudagraph=True,
        num_finished_warmup=0, cudagraph=replay, output=output, runnable=lambda *a: output)
    owner = module.PiecewiseBackend()
    general = []
    def general_shape(*args):
        general.append(args)
        return output
    vars(owner).update(first_run_finished=True, is_debugging_mode=False, sym_shape_indices=[0],
        concrete_size_entries={4: entry}, compilation_config=SimpleNamespace(cudagraph_num_of_warmups=0),
        compiled_graph_for_general_shape=general_shape)
    return SimpleNamespace(module=module, observer=observer, rows=rows, owner=owner,
        output=output, replay=replay, general=general)


def capture_world(tmp_path):
    item = world(tmp_path)
    entry = item.owner.concrete_size_entries[4]
    entry.cudagraph = None
    entry.output = None
    item.owner.is_first_graph = True
    item.owner.is_last_graph = False
    item.owner.graph_pool = object()
    item.module.ExitStack = ExitStack
    item.module.logger = SimpleNamespace(debug=lambda *args: None)
    item.module.weak_ref_tensors = lambda value: value
    item.module.compilation_counter = SimpleNamespace(num_cudagraph_caputured=0)
    item.module.torch = SimpleNamespace(Tensor=type('Tensor', (), {}), cuda=SimpleNamespace(
        CUDAGraph=lambda: item.replay, graph=lambda value, pool: nullcontext()))
    return item


def test_actual_replay_returns_cached_entry_output_and_partial_consumer(tmp_path):
    item = world(tmp_path)
    assert item.owner(4) is item.output
    assert item.replay.calls == 1 and item.general == []
    report = item.observer.report()
    result = graph.project_piecewise(report)
    assert result['qualified_calls'] == 1 and result['qualified_returns'] == 1
    assert result['replay_returns_cached_output'] == 1 and result['replay_shapes'] == [4]
    assert result['violation'] is None and result['full_scope'] is False
    assert {'graph_input_refresh_unobserved', 'case_graph_consumption_unobserved',
        'graph_completion_unobserved', 'independent_output_control_unavailable'} <= set(result['missing'])
    assert Result.secret not in json.dumps(report) and 'EXAMPLE-native-replay-error' not in json.dumps(report)
    assert all(row['source_file_sha256'] == graph.SOURCE_SHA256 and row['entry_qualified_before']
        for row in report['events'])


def test_actual_capture_then_replay_retains_one_graph_and_output_generation(tmp_path):
    item = capture_world(tmp_path)
    assert item.owner(4) is item.output
    assert item.module.compilation_counter.num_cudagraph_caputured == 1
    assert item.owner(4) is item.output and item.replay.calls == 1
    result = graph.project_piecewise(item.observer.report())
    assert result['capture_returns'] == 1 and result['replay_returns_cached_output'] == 1
    assert result['replay_shapes'] == [4] and result['full_scope'] is False


@pytest.mark.parametrize('damage', [
    'missing-entry', 'entry-swap', 'shape-swap', 'source-change', 'code-change',
    'missing-graph', 'missing-output', 'preexisting-graph', 'reused-output',
])
def test_actual_capture_requires_bound_new_generation(tmp_path, damage):
    item = capture_world(tmp_path)
    assert item.owner(4) is item.output
    report = copy.deepcopy(item.observer.report())
    call, terminal = report['events']
    if damage == 'missing-entry':
        call['fields']['entry_token'] = terminal['fields']['entry_token'] = None
    elif damage == 'entry-swap':
        terminal['fields']['entry_token'] = '0' * 64
    elif damage == 'shape-swap':
        terminal['fields']['shape'] = 8
    elif damage == 'source-change':
        terminal['source_file_sha256'] = '0' * 64
    elif damage == 'code-change':
        terminal['entry_code_sha256'] = '0' * 64
    elif damage == 'missing-graph':
        terminal['fields']['graph_token'] = None
    elif damage == 'missing-output':
        terminal['fields']['output_token'] = None
    elif damage == 'preexisting-graph':
        call['fields']['graph_present'] = True
    else:
        call['fields']['output_token'] = terminal['fields']['output_token']
    result = graph.project_piecewise(report)
    assert result['capture_returns'] == 0 and result['violation'] is None


def test_actual_general_and_unsupported_shapes_do_not_claim_replay(tmp_path):
    item = world(tmp_path)
    assert item.owner(9) is item.output
    assert item.owner('unreviewed-shape') is item.output
    item.owner.concrete_size_entries[4].use_cudagraph = False
    assert item.owner(4) is item.output
    assert item.replay.calls == 0 and item.general == [(9,), ('unreviewed-shape',)]
    result = graph.project_piecewise(item.observer.report())
    assert result['replay_returns_cached_output'] == 0 and result['replay_shapes'] == []
    assert 'baseline_graph_replay_unobserved' in result['missing']
    assert any(row['fields'].get('branch') == 'runnable' for row in item.rows if row['phase'] == 'CALL')


def test_actual_native_raise_retains_raise_and_no_completed_replay(tmp_path):
    item = world(tmp_path, raises=True)
    with pytest.raises(RuntimeError, match='EXAMPLE-native-replay-error'):
        item.owner(4)
    assert item.replay.calls == 1
    result = graph.project_piecewise(item.observer.report())
    assert result['replay_returns_cached_output'] == 0
    assert 'baseline_graph_call_raised' in result['missing']
    assert 'EXAMPLE-native-replay-error' not in json.dumps(item.observer.report())


def test_changed_source_or_live_code_never_installs(tmp_path):
    module = exact_module(tmp_path)
    original = module.PiecewiseBackend.__call__
    observer = graph.BaselineGraphObserver('a' * 32, context_provider=lambda: {'fixture': True})
    path = Path(module.__file__)
    path.write_bytes(path.read_bytes() + b'\n# altered\n')
    with pytest.raises(ValueError):
        graph.instrument(module, observer)
    assert module.PiecewiseBackend.__call__ is original
    path.write_bytes(FIXTURE.read_bytes())
    module.PiecewiseBackend.__call__ = lambda self, *args: None
    changed = module.PiecewiseBackend.__call__
    with pytest.raises(ValueError):
        graph.instrument(module, observer)
    assert module.PiecewiseBackend.__call__ is changed


def test_projector_refuses_foreign_or_unqualified_rows(tmp_path):
    item = world(tmp_path)
    item.owner(4)
    report = item.observer.report()
    changed = copy.deepcopy(report)
    changed['events'][0]['entry_qualified_before'] = False
    assert graph.project_piecewise(changed)['replay_returns_cached_output'] == 0
    changed = copy.deepcopy(report)
    for row in changed['events']:
        row['source_file_sha256'] = '0' * 64
    assert graph.project_piecewise(changed)['replay_returns_cached_output'] == 0


@pytest.mark.parametrize('damage', [
    'missing-terminal', 'duplicate-call', 'duplicate-terminal', 'reordered',
    'source-change', 'code-change', 'schema-change', 'cross-session', 'cross-process',
    'top-session', 'top-process', 'parent-change', 'missing-call-id', 'bad-time', 'missing-entry', 'missing-graph',
    'missing-output', 'graph-swap', 'output-swap', 'entry-swap',
    'shape-swap', 'returned-other-output',
])
def test_actual_replay_rows_require_one_owned_generation_and_order(tmp_path, damage):
    item = world(tmp_path)
    assert item.owner(4) is item.output
    report = copy.deepcopy(item.observer.report())
    call, terminal = report['events']
    if damage == 'missing-terminal':
        report['events'].pop()
    elif damage == 'duplicate-call':
        report['events'].insert(1, copy.deepcopy(call))
    elif damage == 'duplicate-terminal':
        report['events'].append(copy.deepcopy(terminal))
    elif damage == 'reordered':
        report['events'].reverse()
    elif damage == 'source-change':
        terminal['source_file_sha256'] = '0' * 64
    elif damage == 'code-change':
        terminal['entry_code_sha256'] = '0' * 64
    elif damage == 'schema-change':
        terminal['schema'] = 'unreviewed'
    elif damage == 'cross-session':
        terminal['session_sha256'] = '0' * 64
    elif damage == 'cross-process':
        terminal['process_sha256'] = '0' * 64
    elif damage == 'top-session':
        report['session_sha256'] = '0' * 64
    elif damage == 'top-process':
        report['process_sha256'] = '0' * 64
    elif damage == 'parent-change':
        terminal['parent_call_sha256'] = '0' * 64
    elif damage == 'missing-call-id':
        terminal['call_sha256'] = None
    elif damage == 'bad-time':
        terminal['native_started_ns'] = call['operation_started_ns'] - 1
    elif damage == 'missing-entry':
        call['fields']['entry_token'] = terminal['fields']['entry_token'] = None
    elif damage == 'missing-graph':
        call['fields']['graph_token'] = terminal['fields']['graph_token'] = None
    elif damage == 'missing-output':
        call['fields']['output_token'] = terminal['fields']['output_token'] = None
    elif damage == 'graph-swap':
        terminal['fields']['graph_token'] = '0' * 64
    elif damage == 'output-swap':
        terminal['fields']['output_token'] = '0' * 64
    elif damage == 'entry-swap':
        terminal['fields']['entry_token'] = '0' * 64
    elif damage == 'shape-swap':
        terminal['fields']['shape'] = 8
    else:
        terminal['fields']['returned_entry_output'] = False
    result = graph.project_piecewise(report)
    assert result['replay_returns_cached_output'] == 0 and result['replay_shapes'] == []
    assert result['violation'] is None and result['full_scope'] is False


def test_object_capacity_gap_never_counts_replay(tmp_path, monkeypatch):
    item = world(tmp_path)
    monkeypatch.setattr(graph.cache, 'MAX_OBJECTS', 0)
    assert item.owner(4) is item.output and item.replay.calls == 1
    report = item.observer.report()
    assert 'CACHE_CALLBACK_OBJECT_LIMIT' in report['missing']
    assert graph.project_piecewise(report)['replay_returns_cached_output'] == 0
