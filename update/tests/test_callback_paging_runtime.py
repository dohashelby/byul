"""Actual local file/queue behavior; events are explicit EXAMPLE data."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
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


@pytest.mark.parametrize(('invalid_reply', 'invalid_page'), [
    pytest.param(None, None, id='matching'),
    pytest.param('missing', 0, id='missing'),
    pytest.param('different', 0, id='different-first'),
    pytest.param('different', 1, id='different-later'),
])
def test_callback_http_keeps_only_pages_for_the_selected_capture(tmp_path, monkeypatch, invalid_reply, invalid_page):
    from guard.models import canonical_json, sha256_hex
    from guard.runtime.engine_http import ENGINE_CALLBACK_PROFILE, KubernetesHTTP

    source = tmp_path / 'source'
    source.mkdir()
    (source / 'callbacks-11.jsonl').write_text(''.join(
        json.dumps({'EXAMPLE': index, 'padding': 'x' * 100000}) + '\n' for index in range(6)))
    expected = list(pages(source))
    assert len(expected) > 1

    transport = KubernetesHTTP.__new__(KubernetesHTTP)
    transport.profile = ENGINE_CALLBACK_PROFILE
    transport.binding = {'engine': {'namespace': 'example', 'pod': 'example', 'container': 'engine'}}
    transport.callback_cursors = {}
    transport.callback_trial_gaps = set()
    attempts, checks, replies = [], [], []

    def read_page(argv, *, cap, budget):
        index = len(attempts)
        spec = json.loads(argv[-1])
        attempts.append(spec)
        assert spec['session'] == '1' * 32 and spec['seek_end'] is False
        assert spec['capture_id'] == attempts[0]['capture_id']
        assert cap == 1024**2 and budget == 30
        if index:
            assert spec['snapshot'] == expected[index - 1]['snapshot']
            assert spec['cursor'] == expected[index - 1]['cursor']
        value = {**expected[index], 'capture_id': spec['capture_id']}
        if index == invalid_page:
            if invalid_reply == 'missing':
                del value['capture_id']
            else:
                value['capture_id'] = '0' * 32 if spec['capture_id'] != '0' * 32 else 'f' * 32
        replies.append(value)
        return value

    monkeypatch.setattr(transport, '_call', read_page)
    monkeypatch.setattr(transport, '_verify', lambda: checks.append(True))
    archive = tmp_path / 'archive'
    result = transport.engine_attempt_events('1' * 32, archive=archive)
    kept = len(expected) if invalid_page is None else invalid_page
    assert len(attempts) == (len(expected) if invalid_reply is None else kept + 1)
    assert len(checks) == len(attempts) * 2
    files = sorted(archive.glob('*.json'))
    assert len(files) == len(result['page_refs']) == kept
    assert result['source_events'] == sum(len(page['rows']) for page in expected[:kept])
    for index, file in enumerate(files):
        assert file.read_bytes() == canonical_json(replies[index])
        assert result['page_refs'][index] == {'path': file.name, 'sha256': sha256_hex(file.read_bytes())}
    if invalid_reply is None:
        assert not result['truncated'] and transport.callback_cursors['1' * 32] == expected[-1]['cursor']
    else:
        assert result['truncated'] and transport.callback_cursors == {}
        assert result['diagnostic_codes'] == ['CALLBACK_PAGE_READ_FAILED', 'CALLBACK_PREFIX_INCOMPLETE']

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


def test_normal_worker_journal_archives_terminal_after_the_writer_exits(tmp_path, monkeypatch):
    from guard.models import canonical_json

    source, archive = tmp_path / 'source', tmp_path / 'archive'
    source.mkdir()
    session, capture_id = 'a' * 32, 'b' * 32
    child = '''
import atexit
import json
import sys
from guard.adapters import observer_runtime

context = observer_runtime.cache.base._context()
observer_runtime.JournalSink.SEGMENT_BYTES = 128
sink = observer_runtime.JournalSink(sys.argv[1], terminal_session=sys.argv[2])
atexit.register(sink.close)
for index in range(10):
    sink({'EXAMPLE': index, 'padding': 'x' * 20})
print(json.dumps(context))
'''
    result = subprocess.run([sys.executable, '-c', child, str(source), session],
                            env={**os.environ, 'PYTHONPATH': str(Path(observer_runtime.__file__).parents[2])},
                            check=True, capture_output=True, text=True)
    context = json.loads(result.stdout)
    pid, ticks = context['pid'], context['start_ticks']
    marker = source / f'callbacks-{pid}.terminal.json'
    raw = marker.read_bytes()
    value = json.loads(raw)
    assert value['schema'] == 'guard-observer-journal-terminal/1'
    assert value['pid'] == pid and value['start_ticks'] == ticks
    assert value['process_sha256'] == hashlib.sha256(canonical_json(context)).hexdigest()
    assert value['session_sha256'] == hashlib.sha256(session.encode()).hexdigest()
    assert value['accepted'] == value['processed'] == value['written'] == 10
    assert len(value['segments']) > 1
    for item in value['segments']:
        data = (source / item['name']).read_bytes()
        assert item['bytes'] == len(data) and item['sha256'] == hashlib.sha256(data).hexdigest()

    read = reader()
    monkeypatch.setitem(read.__globals__, 'MAX_PAGE', 100)

    def capture():
        snapshot, cursor = None, None
        for _ in range(10):
            page = read(source, snapshot, cursor, capture_id, session=session)
            yield page
            if page['done']:
                return
            snapshot, cursor = page['snapshot'], page['cursor']
        raise AssertionError('EXAMPLE capture did not reach the end of the snapshot')

    sequence = list(capture())
    assert len(sequence) > 1
    assert sequence[0]['journal_terminals'] == [{
        'raw_sha256': hashlib.sha256(raw).hexdigest(), 'raw_b64': base64.b64encode(raw).decode('ascii'),
    }]
    assert all('journal_terminals' not in page for page in sequence[1:])
    assert all(item['pid'] == pid and item['start_ticks'] == ticks for item in sequence[0]['snapshot'].values())
    projection = assemble(sequence, archive=archive)
    assert projection['truncated'] and projection['source_events'] == 10
    assert projection['diagnostic_codes'] == ['CALLBACK_CHECKPOINT_UNAVAILABLE']
    assert projection['processes'][0]['start_ticks'] == ticks
    assert (archive / 'page-0000.json').read_bytes() == canonical_json(sequence[0])

    other_session = read(source, capture_id='c' * 32, session='d' * 32)
    assert 'journal_terminals' not in other_session and other_session['rows']

    segment = source / value['segments'][0]['name']
    original = segment.read_bytes()
    altered = original.replace(b'"EXAMPLE":0', b'"EXAMPLE":9', 1)
    assert altered != original and len(altered) == len(original)
    segment.write_bytes(altered)
    changed = read(source, capture_id='e' * 32, session=session)
    assert 'journal_terminals' not in changed and changed['rows']
    assert marker.read_bytes() == raw


def test_open_normal_worker_writer_keeps_its_checkpoint_without_a_terminal(tmp_path):
    session, capture = '1' * 32, '2' * 32
    sink = observer_runtime.JournalSink(tmp_path, terminal_session=session)
    marker = tmp_path / f'callbacks-{sink.pid}.terminal.json'
    try:
        sink({'EXAMPLE': 'owned open writer'})
        page = reader()(tmp_path, capture_id=capture, session=session)
        assert page['done'] and not page['gaps'] and len(page['rows']) == 1
        assert (tmp_path / f'checkpoint-{sink.pid}-{capture}.json').is_file()
        assert sink.thread.is_alive() and 'journal_terminals' not in page and not marker.exists()
    finally:
        sink.close()
    assert marker.is_file()


@pytest.mark.parametrize('reason', ['unselected', 'no-native-context', 'dropped', 'flush-failed'])
def test_optional_terminal_requires_clean_native_drain(tmp_path, monkeypatch, reason):
    def unavailable(*_):
        raise OSError('EXAMPLE local reader or writer unavailable')

    session = None if reason == 'unselected' else '1' * 32
    if reason == 'no-native-context':
        monkeypatch.setattr(observer_runtime.cache.base, '_context', unavailable)
    elif reason == 'dropped':
        monkeypatch.setattr(observer_runtime.JournalSink, 'MAX_BYTES', 0)
    elif reason == 'flush-failed':
        monkeypatch.setattr(observer_runtime.os, 'fsync', unavailable)
    sink = observer_runtime.JournalSink(tmp_path, terminal_session=session)
    sink({'EXAMPLE': 'single callback'})
    sink.close()
    state = json.loads(sink.state.read_text())
    assert state['closed'] is True and state['accepted'] == state['processed'] == 1
    assert state['dropped'] == int(reason == 'dropped')
    assert state['errors'] == int(reason == 'flush-failed')
    assert not list(tmp_path.glob('callbacks-*.terminal.json'))


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
    previous = path.stat()
    path.write_text('EXAMPLE = 2\n')
    # A same-size rewrite can land on the same timestamp tick in this fixture.
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
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
    report = transport.report()
    assert report['principal_authenticity_verified'] is False
    assert all('retained_normal_response' not in row for row in report['requests'])
