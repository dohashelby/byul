"""Actual loopback SSE collector + exact journal API; no model or GPU."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from uuid import uuid4
import pytest
from tools import selected_sse_separation as tool


@contextmanager
def server(mode='normal'):
    state = {'records': {}, 'posts': [], 'alpha_marker': None}
    scope = {'policy': {'sha256': 'policy', 'target_id': 'target'}, 'run_id': 'run',
             'target': {'instance': {'current': {'instance_id': 'generation'}}},
             'auth': {'protected_routes': ['GET /api/*']}}
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'
        def log_message(self, *args): pass
        def do_GET(self):
            if self.path == '/api/status': value = scope
            elif self.path == '/api/policy': value = {'current': {'sha256': 'policy', 'target_id': 'target', 'output_max_tokens': 256}}
            elif self.path.startswith('/api/requests/'):
                rid = self.path.rsplit('/', 1)[1]; value = {'gateway_records': state['records'].get(rid, [])}
            else:
                # Existing concurrent_precondition reads these existing APIs.
                if self.path == '/api/requests?limit=200': value = {'requests': []}
                else: value = {'concurrency': {'max_inflight': 2, 'inflight': 0}}
            raw = json.dumps(value).encode(); self.send_response(200)
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            prompt = body['messages'][0]['content']; marker = prompt.rsplit(': ', 1)[1]
            alpha = marker.startswith('ALPHA'); rid = uuid4().hex
            state['posts'].append({'alpha': alpha, 'client_label': self.headers.get('X-Request-ID'), 'body': body})
            if alpha: state['alpha_marker'] = marker
            base = {'request_id': rid, 'run_id': 'run', 'target_id': 'target',
                    'policy': {'sha256': 'policy'}, 'target': {'instance_id': 'generation'},
                    'auth': {'state': 'authenticated', 'principal_id': 'client-a'},
                    'decision': {'result': 'ALLOWED', 'http_status': 200}, 'delivery': {'attempted': True}}
            state['records'][rid] = [{**base, 'phase': 'started'}]
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
            self.send_header('X-Request-ID', rid); self.end_headers()
            def event(piece):
                return b'data: ' + json.dumps({'choices': [{'delta': {'content': piece}}]}).encode() + b'\n\n'
            try:
                if alpha and mode == 'terminal':
                    self.wfile.write(event(marker * 12) + b'data: [DONE]\n\n'); self.wfile.flush()
                elif alpha and mode in ('short', 'non_echo'):
                    self.wfile.write(event(marker if mode == 'short' else 'hello')); self.wfile.flush()
                    time.sleep(.1)
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                elif alpha and (mode.startswith('nonfinite:') or mode == 'cross_choice'):
                    if mode == 'cross_choice':
                        value = {'choices':[{'index':0,'delta':{'content':marker[:5]}},{'index':1,'delta':{'content':marker[5:]}}]}
                    else:
                        value = {'choices':[{'delta':{'content':marker}}],'extension':float(mode.split(':',1)[1])}
                    self.wfile.write(b'data: '+json.dumps(value).encode()+b'\n\n'); self.wfile.flush(); time.sleep(.1)
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                elif alpha:
                    if mode == 'invalid': self.wfile.write(b'data: {invalid-json}\n\n'); self.wfile.flush()
                    for piece in marker * 12:
                        self.wfile.write(event(piece)); self.wfile.flush(); time.sleep(.002)
                    self.wfile.write(b'data: [DONE]\n\n'); self.wfile.flush()
                else:
                    value = marker + (' ' + state['alpha_marker'] if mode == 'crossed' else '')
                    self.wfile.write(event(value) + b'data: [DONE]\n\n'); self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError): pass
            finally:
                state['records'][rid].append({**base, 'phase': 'final', 'final_outcome': 'completed'})
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler); http.daemon_threads = True
    thread = threading.Thread(target=http.serve_forever); thread.start()
    try: yield 'http://127.0.0.1:' + str(http.server_port), state
    finally: http.shutdown(); http.server_close(); thread.join()


@pytest.mark.parametrize('mode,expected', [('normal', 'passed'), ('short', 'passed'), ('crossed', 'failed'), ('terminal', 'unknown'), ('invalid', 'unknown'), ('non_echo', 'unknown')])
def test_original_collector_verdict_on_loopback(mode, expected, monkeypatch):
    # Capacity itself has existing focused tests; response/journal paths here are real HTTP.
    monkeypatch.setattr(tool, 'concurrent_precondition', lambda *a: (True, {}))
    with server(mode) as (url, state):
        result = tool.run(url, 'test-model', 'EXAMPLE_INFERENCE', 'EXAMPLE_VIEWER', timeout=3,
                          chunk_bytes=512 if mode == 'terminal' else 128)
    assert result['cases'][0]['result'] == expected
    assert result['cases'][1]['result'] == 'passed'
    assert [p['alpha'] for p in state['posts']] == [True, False]
    assert {p['client_label'] for p in state['posts']} == {'same-synthetic-client-id'}
    assert result['cases'][0]['facts']['alpha_read_profile']['interval_ms'] == 1
    assert result['cases'][0]['facts']['profile_fixed_before_dispatch'] is True
    assert result['raw_generated_text_saved'] is False and result['engine_termination_claim'] is False
    if mode in ('normal', 'short'):
        assert result['cases'][0]['facts']['alpha_own_marker_observed'] is True
        assert result['cases'][0]['facts']['alpha_closed_before_terminal'] is True
        assert result['cases'][0]['facts']['semantic_marker_close_observed'] is True
    if mode == 'terminal':
        assert result['cases'][0]['facts']['alpha_own_marker_observed'] is True
        assert result['cases'][0]['facts']['alpha_closed_before_terminal'] is False


def test_unsupported_zero_interval_never_dispatches(monkeypatch):
    monkeypatch.setattr(tool, 'request', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not dispatch')))
    result = tool.run('http://127.0.0.1:1', 'test-model', 'a', 'b', interval_ms=0)
    assert result['execution'] == 'not_run' and result['cases'][0]['result'] == 'unknown'


def test_existing_read_count_mode_remains_available_without_semantic_upgrade(monkeypatch):
    monkeypatch.setattr(tool, 'concurrent_precondition', lambda *a: (True, {}))
    with server('short') as (url, _):
        result = tool.run(url, 'test-model', 'EXAMPLE_INFERENCE', 'EXAMPLE_VIEWER', timeout=3, close_mode='reads')
    facts = result['cases'][0]['facts']
    assert result['cases'][0]['result'] == 'unknown'
    assert facts['alpha_read_profile']['profile'] == 'slow-reader-v1'
    assert facts['semantic_marker_close_observed'] is None


@pytest.mark.parametrize('mode',['nonfinite:NaN','nonfinite:Infinity','nonfinite:-Infinity','cross_choice'])
def test_semantic_only_valid_json_and_single_choice_marker_witness(mode,monkeypatch):
    monkeypatch.setattr(tool,'concurrent_precondition',lambda *a:(True,{}))
    with server(mode) as (url,_):
        result=tool.run(url,'test-model','EXAMPLE_INFERENCE','EXAMPLE_VIEWER',timeout=3,chunk_bytes=512)
    case=result['cases'][0]
    assert case['result']=='unknown' and case['facts']['semantic_marker_json_valid'] is False
    assert case['facts']['semantic_marker_close_observed'] is False


@pytest.mark.parametrize('kwargs', [{'chunk_bytes': 0}, {'close_after_reads': 33}, {'timeout': 31}, {'interval_ms': -1}])
def test_bounds_reject_before_dispatch(kwargs, monkeypatch):
    monkeypatch.setattr(tool, 'request', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not dispatch')))
    with pytest.raises(ValueError): tool.run('http://127.0.0.1:1', 'test-model', 'a', 'b', **kwargs)
