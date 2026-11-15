"""Actual local file/queue behavior; events are explicit EXAMPLE data."""
from copy import deepcopy
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from guard.adapters import observer_runtime
from guard.runtime.callback_pages import assemble


def reader():
    source = Path(observer_runtime.__file__).parents[1] / 'runtime/assets/callback_page.py.txt'
    return runpy.run_path(str(source))['page']


def pages(root):
    read, snapshot, cursor = reader(), None, None
    for _ in range(20):
        value = read(root, snapshot, cursor)
        yield value
        if value['done']:
            return
        snapshot, cursor = value['snapshot'], value['cursor']
    raise AssertionError('EXAMPLE pages did not terminate')


def test_multi_file_pages_preserve_each_raw_offset_and_event(tmp_path):
    source, archive = tmp_path / 'source', tmp_path / 'archive'
    source.mkdir()
    for pid in (11, 12):
        path = source / f'callbacks-{pid}.jsonl'
        path.write_text(''.join(json.dumps({'EXAMPLE_pid': pid, 'seq': n, 'padding': 'x' * 20000}) + '\n' for n in range(24)))
    sequence = list(pages(source))
    assert len(sequence) > 1
    result = assemble(sequence, archive=archive)
    assert not result['truncated'] and result['source_events'] == 48
    assert [p['pid'] for p in result['processes']] == [11, 12]
    assert all(len(p['events']) == 24 for p in result['processes'])
    assert len(result['page_refs']) == len(sequence) == len(list(archive.glob('*.json')))
    with pytest.raises(FileExistsError):
        assemble(sequence, archive=archive)


def test_late_read_error_and_projection_limit_keep_earlier_rows(tmp_path):
    path = tmp_path / 'callbacks-11.jsonl'
    path.write_text(''.join(json.dumps({'EXAMPLE': n, 'padding': 'x' * 100000}) + '\n' for n in range(6)))
    first = next(pages(tmp_path))
    def interrupted():
        yield first
        raise OSError('EXAMPLE unavailable next page')
    result = assemble(interrupted())
    assert result['truncated'] and result['processes'][0]['events']
    assert 'CALLBACK_PAGE_READ_FAILED' in result['diagnostic_codes']
    limited = assemble(pages(tmp_path), max_events=2)
    assert limited['truncated'] and limited['source_events'] == 6
    assert len(limited['processes'][0]['events']) == 2


def test_repeated_page_and_replaced_file_cannot_be_complete(tmp_path):
    path = tmp_path / 'callbacks-11.jsonl'
    path.write_text(''.join(json.dumps({'EXAMPLE': n, 'padding': 'x' * 100000}) + '\n' for n in range(6)))
    first = next(pages(tmp_path))
    repeated = assemble([first, first])
    assert repeated['truncated'] and repeated['source_events'] == len(first['rows'])
    assert 'CALLBACK_PAGE_INVALID' in repeated['diagnostic_codes']
    path.unlink()
    path.write_text('EXAMPLE replacement\n')
    with pytest.raises(ValueError, match='REPLACED'):
        reader()(tmp_path, first['snapshot'], first['cursor'])


def test_partial_last_row_is_explicit_loss(tmp_path):
    (tmp_path / 'callbacks-11.jsonl').write_bytes(b'{"EXAMPLE":1}\n{"EXAMPLE":')
    result = assemble(pages(tmp_path))
    assert result['truncated'] and result['source_events'] == 1
    assert result['processes'][0]['events'] == [{'EXAMPLE': 1}]


def test_journal_rotates_retains_all_rows_and_reports_cost_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(observer_runtime.JournalSink, 'SEGMENT_BYTES', 512)
    sink = observer_runtime.JournalSink(tmp_path)
    for index in range(30):
        sink({'EXAMPLE': index, 'padding': 'x' * 40})
    sink.close()
    assert not sink.thread.is_alive()
    result = assemble(pages(tmp_path))
    assert not result['truncated'] and result['source_events'] == 30
    assert len(list(tmp_path.glob('callbacks-*.jsonl'))) > 1
    state = json.loads(sink.state.read_text())
    assert state['accepted'] == state['written'] == 30
    assert state['measurement_scope'] == 'functional_security' and state['timing_qualified'] is False


def test_journal_budget_exhaustion_is_visible_and_cannot_look_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(observer_runtime.JournalSink, 'MAX_BYTES', 96)
    sink = observer_runtime.JournalSink(tmp_path)
    for index in range(4):
        sink({'EXAMPLE': index, 'padding': 'x' * 32})
    sink.close()
    state = json.loads(sink.state.read_text())
    assert state['dropped'] > 0
    result = assemble(pages(tmp_path))
    assert result['truncated'] and 'CALLBACK_JOURNAL_INCOMPLETE' in result['diagnostic_codes']


def test_loss_before_the_first_event_file_is_still_visible(tmp_path, monkeypatch):
    monkeypatch.setattr(observer_runtime.JournalSink, 'MAX_BYTES', 0)
    sink = observer_runtime.JournalSink(tmp_path)
    sink({'EXAMPLE': 'all dropped'})
    sink.close()
    assert not list(tmp_path.glob('*.jsonl'))
    result = assemble(pages(tmp_path))
    assert result['source_events'] == 0 and result['truncated']
    assert 'CALLBACK_JOURNAL_INCOMPLETE' in result['diagnostic_codes']


def test_startup_code_qualification_is_cached_but_replacement_is_detected(tmp_path, monkeypatch):
    import threading
    path = tmp_path / 'EXAMPLE.py'
    path.write_text('EXAMPLE = 1\n')
    module = SimpleNamespace(__name__='EXAMPLE', __file__=str(path))
    from guard.models import sha256_hex
    pin = sha256_hex(path.read_bytes())
    calls = []
    monkeypatch.setattr(observer_runtime.cache, '_codes', lambda *args: calls.append(True) or {'entry': 'EXAMPLE-code'})
    observer = observer_runtime.QualifiedObserver()
    observer.lock = threading.RLock()
    observer.gaps = set()
    assert observer._qualified_codes(module, {}, {'EXAMPLE': pin}) == {'entry': 'EXAMPLE-code'}
    assert observer._qualified_codes(module, {}, {'EXAMPLE': pin}) == {'entry': 'EXAMPLE-code'}
    assert calls == [True]
    path.write_text('EXAMPLE = 2\n')
    with pytest.raises(ValueError, match='REPLACED'):
        observer._qualified_codes(module, {}, {'EXAMPLE': pin})
    observer.verify_sources()
    assert 'OBSERVER_SOURCE_POSTCHECK_FAILED' in observer.gaps


def test_default_connection_observer_does_not_read_large_parameter_bytes():
    from guard.adapters.vllm_engine_callback_sensor import BoundedExecutionLinkObserver
    observer = BoundedExecutionLinkObserver.__new__(BoundedExecutionLinkObserver)
    observer.gaps = set()
    class Model:
        def named_parameters(self):
            raise AssertionError('EXAMPLE parameter bytes must not be visited')
    assert observer._cpu_parameter(Model()) is None
    assert 'EXECUTION_LINK_PARAMETER_CONTENT_NOT_SELECTED' in observer.gaps


def test_profiled_gateway_keeps_both_owned_generations_and_uses_page_reader():
    from guard.runtime.engine_callback_gateway import KubernetesCallbackGatewayHTTP
    from guard.runtime.engine_http import KubernetesHTTP
    from tests.test_kubernetes_callback_gateway import configured, request
    env, base, full, world, runner = configured()
    transport = KubernetesCallbackGatewayHTTP(base, full, env, duration=30, runner=runner)
    response, = transport('kubernetes-probe', [request(full['gateway']['url'])], 10)
    assert response['outcome'] == 200 and isinstance(transport, KubernetesHTTP)
    assert type(transport).engine_callback_events is KubernetesHTTP.engine_callback_events
    assert transport.report()['principal_authenticity_verified'] is False
