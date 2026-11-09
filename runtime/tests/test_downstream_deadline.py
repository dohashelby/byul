"""Real socketpair slow readers; upstream transport is a controlled fixture."""
import json
import socket
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from serving_runtime.mvp.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.mvp.backend import BackendConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import load_revocations
from serving_runtime.mvp.relay import Relay
from serving_runtime.mvp.request_flow import RequestFlow, ConcurrencyLimiter
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.response_timeout import write_response_before
from serving_runtime.mvp.validator import validate_request
from test_mvp_request_boundaries import BackendResponse, BackendConnection, POLICY, body, RequestIO


def sockets():
    sender, receiver = socket.socketpair()
    sender.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
    sender.settimeout(.5)
    return sender, receiver


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize('stall_headers', [False, True])
@pytest.mark.parametrize('watcher_first', [False, True])
def test_blocked_downstream_stream_is_interrupted_and_preserves_started(route, stall_headers, watcher_first):
    sender, receiver = sockets()
    class Writer:
        connection = (SimpleNamespace(settimeout=lambda _timeout: None,
                                     shutdown=sender.shutdown, close=sender.close)
                      if watcher_first else sender)
        def start_stream_response(self, _request_id):
            if stall_headers:
                sender.sendall(b'x' * 100000)
        def write_stream_chunk(self, chunk):
            sender.sendall(chunk)
    request = body(stream=True)
    allowed = validate_request(request, 'application/json', load_policy(POLICY))
    document = json.loads(request)
    if route == COMPLETIONS_ROUTE:
        document.pop('messages')
        document['prompt'] = 'hello'
    chunks = ([b'data: {"choices":[{"text":"' + b'x' * 60000 + b'"}]}\n\n'] * 100
              if route == COMPLETIONS_ROUTE else [b'data: {"choices":[{"delta":{"content":"' + b'x' * 60000 + b'"}}]}\n\n'] * 100)
    upstream = BackendConnection(BackendResponse(chunks, stream=True))
    started = time.monotonic()
    try:
        with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
            decision, outcome, response_started = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, .05).relay_stream(
                Writer(), 'a' * 32, document, allowed, started, lambda: None, route=route)
        # The watcher-first wrapper deliberately ignores timeout updates. Its
        # purpose is classification after shutdown; the native socket cases
        # above exercise the real client's absolute write budget.
        assert time.monotonic() - started < (.7 if watcher_first else .25)
        assert (decision.reason_code, outcome, response_started) == ('STREAM_DEADLINE', 'deadline_exceeded', True)
        assert upstream.closed
    finally:
        sender.close(); receiver.close()


def test_buffered_slow_write_is_failed_and_releases_request_slot(tmp_path):
    sender, receiver = sockets()
    policy = load_policy(POLICY)
    records = RequestRecords(tmp_path, 'session', lambda: policy, 'mock')
    limiter = ConcurrencyLimiter(1)
    io = RequestIO(body())
    io.connection = sender
    writes = []
    def send_response(*_args, **_kwargs):
        writes.append('body')
        sender.sendall(b'x' * 100000)
    io.send_response_body = send_response
    relay = SimpleNamespace(request_deadline_s=.05,
                            relay=lambda _id, _doc, allowed, **_kwargs: (allowed, b'x' * 100000))
    engine = SimpleNamespace(receipt_not_queried=lambda: 'not_queried', backend_receipt=lambda _id: 'found')
    flow = RequestFlow(BackendConfig.mock(('127.0.0.1', 1)), lambda: policy,
                       lambda: load_revocations(None), records, engine, relay, limiter)
    began = time.monotonic()
    try:
        flow.chat(io, 'a' * 32)
        assert time.monotonic() - began < .25
        rows, _ = records.store.read_all()
        assert rows[-1]['final_outcome'] == 'ENGINE_DEADLINE'
        assert rows[-1]['delivery']['response'] == 'failed'
        assert io.sent is None and writes == ['body']
        assert limiter.status()['in_flight'] == 0
    finally:
        sender.close(); receiver.close()


def test_successful_buffered_delivery_restores_socket_timeout():
    sender, receiver = sockets()
    try:
        io = SimpleNamespace(connection=sender)
        write_response_before(io, time.monotonic() + .05, lambda: sender.sendall(b'ok'))
        assert receiver.recv(2) == b'ok'
        assert sender.gettimeout() == .5
    finally:
        sender.close(); receiver.close()
