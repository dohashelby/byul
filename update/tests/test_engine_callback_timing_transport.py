"""CPU/EXAMPLE contracts between the real HTTP producer and timing consumer."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from guard.executors.engine_sensor import _callback_elapsed_ms
from guard.executors.stream_probe import collect


def sample(**changes):
    return dict(outcome=200, capture_complete=True, timing_basis='batch_monotonic_client_connection_only',
                dispatch_ms=3, request_sent_ms=5, headers_ms=8, end_ms=13, **changes)


def test_actual_http_producer_reaches_timing_consumer_without_changing_probe():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        row, = collect([{'id': 'cb-1', 'mode': 'json', 'method': 'POST',
            'url': f'http://127.0.0.1:{server.server_port}/v1/chat/completions', 'body': {'public': 'EXAMPLE'}}], 5)
        assert row['outcome'] == 200 and row['capture_complete'] is True
        assert 'elapsed_ms' not in row
        assert _callback_elapsed_ms(row) == row['end_ms'] - row['dispatch_ms']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('change', [
    {'dispatch_ms': True}, {'dispatch_ms': -1}, {'end_ms': 120001},
    {'end_ms': 2}, {'headers_ms': 4}, {'request_sent_ms': None},
    {'capture_complete': False}, {'outcome': 500}, {'timing_basis': 'unknown'},
])
def test_invalid_or_incomplete_clock_is_not_a_sample(change):
    row = sample()
    row.update(change)
    assert _callback_elapsed_ms(row) is None


def test_unknown_probe_clock_does_not_fall_back_to_elapsed():
    row = sample()
    row.update(timing_basis='unknown', elapsed_ms=10)
    assert _callback_elapsed_ms(row) is None


@pytest.mark.parametrize('value', [False, -1, float('inf'), float('nan'), '5'])
def test_bad_cpu_transport_elapsed_is_not_a_sample(value):
    assert _callback_elapsed_ms({'outcome': 200, 'capture_complete': True, 'elapsed_ms': value}) is None


def test_existing_cpu_transport_keeps_its_request_duration():
    assert _callback_elapsed_ms({'outcome': 200, 'capture_complete': True, 'elapsed_ms': 4.5}) == 4.5
