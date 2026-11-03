"""Socket-free regression tests for relay bounds and incremental SSE delivery."""

from http.client import HTTPResponse
import io
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from serving_runtime.mvp.collectors import Collector, CollectorManager
from serving_runtime.mvp.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.mvp.backend import BackendConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import load_revocations
from serving_runtime.mvp.relay import Relay
from serving_runtime.mvp.request_flow import ConcurrencyLimiter, RequestFlow
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.validator import Decision


ALLOWED = Decision("ALLOWED", 200, "REQUEST_ALLOWED", None, "allowed", "model", 8)
EVENT = b'data: {"choices":[{"text":"first"}]}\n\n'
DONE = b"data: [DONE]\n\n"


class FakeResponse:
    status = 200

    def __init__(self, chunks, *, stream=False):
        self.chunks = iter(chunks)
        self.stream = stream

    def getheader(self, name, default=None):
        return "text/event-stream" if self.stream and name == "Content-Type" else default

    def read1(self, _size):
        item = next(self.chunks, b"")
        return item() if callable(item) else item


class ClosedResponseSocket:
    """Model an HTTP/1.0 response whose retained raw socket is closed."""

    def __init__(self, wire: bytes = b""):
        self.wire = wire
        self.timeout_attempts = 0

    def makefile(self, _mode):
        file = io.BytesIO(self.wire)
        file.raw = SimpleNamespace(_sock=self)
        return file

    def settimeout(self, _timeout):
        self.timeout_attempts += 1
        raise OSError(9, "Bad file descriptor")


class FakeConnection:
    sock = None

    def __init__(self, response):
        self.response = response
        self.closed = False

    def request(self, *_args, **_kwargs):
        pass

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class DrippingHeaderSocket:
    def __init__(self):
        self.closed = threading.Event()

    def shutdown(self, _how):
        self.closed.set()

    def close(self):
        self.closed.set()


class DrippingHeaderConnection(FakeConnection):
    def __init__(self):
        super().__init__(None)
        self.sock = DrippingHeaderSocket()
        self.header_parts = 0

    def getresponse(self):
        while not self.sock.closed.wait(0.005):
            self.header_parts += 1
        raise OSError("header socket closed")


class StalledStreamSocket:
    def __init__(self):
        self.closed = threading.Event()

    def settimeout(self, _timeout):
        pass

    def shutdown(self, _how):
        self.closed.set()

    def close(self):
        self.closed.set()


class StalledStreamResponse(FakeResponse):
    def __init__(self, socket, chunks):
        super().__init__(chunks, stream=True)
        self.socket = socket

    def read1(self, size):
        if self.chunks is not None:
            item = next(self.chunks, None)
            if item is not None:
                return item
            self.chunks = None
        self.socket.closed.wait(1)
        raise OSError("watchdog closed response socket")


class FakeBackend:
    kind = "mock"

    def __init__(self, response):
        self.conn = FakeConnection(response)

    def connection(self, path, _timeout):
        return self.conn, path

    def mapped_model(self, model):
        return model

    def output_tokens_field(self, _route):
        return "max_tokens"

    def request_headers(self, **_kwargs):
        return {}


class GatewayIO:
    def __init__(self, document):
        self.request_body = json.dumps(document).encode("utf-8")
        self.sent = None

    def request_header(self, name):
        return {"Content-Length": str(len(self.request_body)),
                "Content-Type": "application/json"}.get(name)

    def read_request_body(self, length):
        return self.request_body[:length]

    def send_error_decision(self, decision, _request_id, **_kwargs):
        self.sent = (decision.http_status, decision.reason_code, _kwargs.get("close_connection"))

    def send_response_body(self, status, _content_type, body, **_kwargs):
        self.sent = (status, None, body)


class ClientStream:
    def __init__(self, *, disconnect=False):
        self.started = False
        self.chunks = []
        self.disconnect = disconnect
        self.first_chunk = threading.Event()

    def start_stream_response(self, _request_id):
        self.started = True

    def write_stream_chunk(self, chunk):
        if self.disconnect:
            raise BrokenPipeError()
        self.chunks.append(chunk)
        self.first_chunk.set()


def relay(stream, chunks, *, deadline=1, disconnect=False):
    backend = FakeBackend(FakeResponse(chunks, stream=stream))
    response = ClientStream(disconnect=disconnect)
    engine = Relay(backend, 0.3, 0.3, deadline)
    return backend, response, engine


class RelayDeadlineTests(unittest.TestCase):
    def test_gateway_flow_forwards_closing_http_1_0_responses_for_both_routes(self):
        policy_path = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"
        cases = (
            ("chat", {"model": "test-model", "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 8}, {"id": "chat-1", "choices": []}),
            ("completions", {"model": "test-model", "prompt": "hi", "max_tokens": 8},
             {"object": "text_completion", "choices": [{"text": "ok"}],
              "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}}),
        )
        for request_kind, document, engine_document in cases:
            with self.subTest(request_kind=request_kind), tempfile.TemporaryDirectory() as directory:
                body = json.dumps(engine_document).encode("utf-8")
                wire = (b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                        + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
                upstream = HTTPResponse(ClosedResponseSocket(wire))
                upstream.begin()
                backend = FakeBackend(upstream)
                records = RequestRecords(directory, "session", lambda: load_policy(policy_path), "mock")
                engine_client = SimpleNamespace(
                    receipt_not_queried=lambda: "not_queried",
                    backend_receipt=lambda _request_id: "found",
                )
                flow = RequestFlow(
                    BackendConfig.mock(("127.0.0.1", 1)),
                    lambda: load_policy(policy_path),
                    lambda: load_revocations(None),
                    records, engine_client, Relay(backend, 0.3, 0.3, 1),
                    ConcurrencyLimiter(1),
                )
                response = GatewayIO(document)
                getattr(flow, request_kind)(response, "a" * 32)
                self.assertEqual(response.sent, (200, None, body))
                stored, corrupt = records.store.read_all()
                self.assertEqual(corrupt, [])
                self.assertEqual(stored[-1]["final_outcome"], "completed")

    def test_http_1_0_closing_response_relayed_as_200(self):
        body = json.dumps({"ok": True}).encode("utf-8")
        wire = (b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        socket = ClosedResponseSocket(wire)
        upstream = HTTPResponse(socket)
        upstream.begin()
        self.assertTrue(upstream.will_close)
        backend = FakeBackend(upstream)
        decision, forwarded = Relay(backend, 0.3, 0.3, 1).relay(
            "id", {"messages": []}, ALLOWED)
        self.assertEqual((decision.http_status, decision.reason_code, forwarded),
                         (200, "REQUEST_ALLOWED", body))
        self.assertEqual(socket.timeout_attempts, 1)
        self.assertTrue(backend.conn.closed)

    def test_closed_response_socket_does_not_break_either_stream(self):
        for route, document in ((CHAT_ROUTE, {"messages": []}),
                                (COMPLETIONS_ROUTE, {"prompt": "hi"})):
            with self.subTest(route=route):
                backend, response, engine = relay(True, [EVENT, DONE])
                socket = ClosedResponseSocket()
                backend.conn.response.fp = SimpleNamespace(raw=SimpleNamespace(_sock=socket))
                decision, outcome, started = engine.relay_stream(
                    response, "id", document, ALLOWED, time.monotonic(),
                    lambda: None, route=route)
                self.assertEqual((decision.reason_code, outcome, started),
                                 ("REQUEST_ALLOWED", "completed", True))
                self.assertEqual(socket.timeout_attempts, 1)

    def test_engine_poll_does_not_stall_security_and_target_collectors(self):
        blocked = threading.Event()
        release = threading.Event()
        counts = {"observation_feed_hubble": 0, "target_pods": 0}

        def health():
            blocked.set()
            release.wait(0.4)
            return {"status": "ok"}

        def fetch(source):
            counts[source] += 1
            return {"count": counts[source]}

        manager = CollectorManager([
            Collector("engine_health", health, 0.02, 0.2, engine_group=True),
            *[Collector(name, lambda name=name: fetch(name), 0.02, 0.2)
              for name in counts],
        ], startup_timeout_s=0.02)
        manager.start()
        self.addCleanup(manager.stop)
        self.addCleanup(release.set)
        self.assertTrue(blocked.wait(0.2))
        first = {name: manager.get(name)["last_success_at"] for name in counts}
        time.sleep(0.08)
        second = {name: manager.get(name)["last_success_at"] for name in counts}
        self.assertTrue(all(second[name] != first[name] for name in counts))
        self.assertTrue(all(counts[name] >= 2 for name in counts))

    def test_nonstream_deadline_covers_dripping_body(self):
        backend, _, engine = relay(False, [b"{", lambda: (time.sleep(0.04), b'"ok":true')[1]] * 10,
                                   deadline=0.07)
        socket = ClosedResponseSocket()
        backend.conn.response.fp = SimpleNamespace(raw=SimpleNamespace(_sock=socket))
        decision, body = engine.relay("id", {"messages": []}, ALLOWED)
        self.assertEqual((decision.http_status, decision.reason_code, body),
                         (504, "ENGINE_DEADLINE", None))
        self.assertEqual(socket.timeout_attempts, 1)
        self.assertTrue(backend.conn.closed)

    def test_deadline_interrupts_dripping_headers_for_buffered_and_both_streams(self):
        for route, document in ((CHAT_ROUTE, {"messages": []}),
                                (COMPLETIONS_ROUTE, {"prompt": "hi"})):
            for stream in (False, True):
                with self.subTest(route=route, stream=stream):
                    backend = FakeBackend(FakeResponse([]))
                    backend.conn = DrippingHeaderConnection()
                    engine = Relay(backend, 0.5, 0.5, 0.03)
                    started = time.monotonic()
                    if stream:
                        decision, outcome, response_started = engine.relay_stream(
                            ClientStream(), "id", document, ALLOWED, started,
                            lambda: None, route=route)
                        self.assertEqual((outcome, response_started), ("ENGINE_TIMEOUT", False))
                    else:
                        decision, body = engine.relay(
                            "id", document, ALLOWED, route=route, started_at=started)
                        self.assertIsNone(body)
                    self.assertEqual((decision.http_status, decision.reason_code),
                                     (504, "ENGINE_TIMEOUT" if stream else "ENGINE_DEADLINE"))
                    self.assertGreater(backend.conn.header_parts, 0)
                    self.assertTrue(backend.conn.sock.closed.is_set())

    def test_stream_deadline_exhausted_before_headers_keeps_engine_timeout(self):
        for route, document in ((CHAT_ROUTE, {"messages": []}),
                                (COMPLETIONS_ROUTE, {"prompt": "hi"})):
            with self.subTest(route=route):
                backend, response, engine = relay(True, [], deadline=0.01)
                decision, outcome, started = engine.relay_stream(
                    response, "id", document, ALLOWED, time.monotonic() - 0.02,
                    lambda: None, route=route)
                self.assertEqual((decision.http_status, decision.reason_code, outcome, started),
                                 (504, "ENGINE_TIMEOUT", "ENGINE_TIMEOUT", False))
                self.assertFalse(response.started)

    def test_nonstream_body_limit(self):
        backend, _, engine = relay(False, [b"12345", b"67890"])
        with patch("serving_runtime.mvp.relay._COMPLETION_BODY_LIMIT", 8):
            decision, body = engine.relay("id", {"messages": []}, ALLOWED)
        self.assertEqual((decision.http_status, decision.reason_code, body),
                         (502, "ENGINE_BAD_RESPONSE", None))
        self.assertTrue(backend.conn.closed)

    def test_token_count_is_inside_shared_deadline(self):
        flow = object.__new__(RequestFlow)
        flow._backend = FakeBackend(FakeResponse([]))
        flow._engine_client = SimpleNamespace(count_input_tokens=lambda *_args, **_kwargs: (
            time.sleep(0.02), 1
        )[1])
        flow._records = SimpleNamespace(set_context=lambda *_args: None)
        decision = flow._context_decision(
            "id", {"messages": []}, ALLOWED,
            SimpleNamespace(policy=SimpleNamespace(max_context_tokens=100)),
            "chat", time.monotonic() + 0.01,
        )
        self.assertEqual((decision.http_status, decision.reason_code),
                         (504, "ENGINE_DEADLINE"))

    def test_token_count_timeout_after_shared_deadline_closes_client_response(self):
        policy_path = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"
        with tempfile.TemporaryDirectory() as directory:
            records = RequestRecords(directory, "session", lambda: load_policy(policy_path), "mock")
            engine_client = SimpleNamespace(
                count_input_tokens=lambda *_args, **_kwargs: (
                    time.sleep(0.03), (_ for _ in ()).throw(OSError("watchdog closed"))
                )[1],
                receipt_not_queried=lambda: "not_queried",
            )
            flow = object.__new__(RequestFlow)
            flow._backend = FakeBackend(FakeResponse([]))
            flow._engine_client = engine_client
            flow._records = records
            response = GatewayIO({"messages": [{"role": "user", "content": "hi"}]})
            decision = flow._context_decision(
                "a" * 32, {"messages": [{"role": "user", "content": "hi"}]}, ALLOWED,
                SimpleNamespace(policy=SimpleNamespace(max_context_tokens=100)),
                "chat", time.monotonic() + 0.01)
            flow.finish_without_relay(response, "a" * 32, decision,
                                      close_connection=decision.reason_code == "ENGINE_DEADLINE")
            self.assertEqual(response.sent, (504, "ENGINE_DEADLINE", True))
            stored, _ = records.store.read_all()
            self.assertEqual(stored[-1]["delivery"]["error_code"], "ENGINE_DEADLINE")

    def test_token_count_timeout_before_shared_deadline_remains_unavailable(self):
        flow = object.__new__(RequestFlow)
        flow._backend = FakeBackend(FakeResponse([]))
        flow._engine_client = SimpleNamespace(
            count_input_tokens=lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
        flow._records = SimpleNamespace(set_context=lambda *_args: None)
        decision = flow._context_decision(
            "id", {"messages": []}, ALLOWED,
            SimpleNamespace(policy=SimpleNamespace(max_context_tokens=100)),
            "chat", time.monotonic() + 1)
        self.assertEqual((decision.http_status, decision.reason_code),
                         (503, "CONTEXT_CHECK_UNAVAILABLE"))

    def test_completion_first_event_arrives_before_generation_finishes(self):
        release = threading.Event()
        waiting = threading.Event()

        def delayed_done():
            waiting.set()
            release.wait(0.5)
            return DONE

        backend, response, engine = relay(True, [EVENT, delayed_done])
        result = []
        worker = threading.Thread(target=lambda: result.append(engine.relay_stream(
            response, "id", {"prompt": "hi"}, ALLOWED, time.monotonic(), lambda: None,
            route=COMPLETIONS_ROUTE)))
        worker.start()
        self.addCleanup(release.set)
        worker.join(0.01)
        self.assertTrue(response.first_chunk.wait(0.2))
        self.assertTrue(waiting.wait(0.2))
        self.assertTrue(worker.is_alive())
        release.set()
        worker.join(1)
        self.assertEqual(result[0][1:], ("completed", True))
        self.assertEqual(response.chunks, [EVENT, DONE])
        self.assertTrue(backend.conn.closed)

    def test_completion_midstream_error_disconnect_and_deadline(self):
        cases = [
            ([EVENT, b"data: {broken}\n\n"], False, 1, "ENGINE_BAD_RESPONSE"),
            ([EVENT, DONE], True, 1, "CLIENT_DISCONNECTED"),
            ([EVENT, lambda: (time.sleep(0.06), b"")[1]], False, 0.03, "STREAM_DEADLINE"),
            ([EVENT], False, 1, "STREAM_TRUNCATED"),
        ]
        for chunks, disconnect, deadline, expected in cases:
            with self.subTest(expected=expected):
                backend, response, engine = relay(True, chunks, deadline=deadline,
                                                  disconnect=disconnect)
                decision, outcome, started = engine.relay_stream(
                    response, "id", {"prompt": "hi"}, ALLOWED, time.monotonic(),
                    lambda: None, route=COMPLETIONS_ROUTE)
                self.assertEqual(decision.reason_code, expected)
                if expected == "STREAM_DEADLINE":
                    self.assertEqual((decision.http_status, outcome),
                                     (200, "deadline_exceeded"))
                self.assertTrue(started)
                self.assertTrue(backend.conn.closed)

    def test_watchdog_closed_stream_socket_keeps_stream_deadline_code(self):
        for route, document, chunks, expected_started in (
            (CHAT_ROUTE, {"messages": []}, [], True),
            (COMPLETIONS_ROUTE, {"prompt": "hi"}, [], False),
            (COMPLETIONS_ROUTE, {"prompt": "hi"}, [EVENT], True),
        ):
            with self.subTest(route=route, expected_started=expected_started):
                socket = StalledStreamSocket()
                backend = FakeBackend(StalledStreamResponse(socket, chunks))
                backend.conn.sock = socket
                response = ClientStream()
                engine = Relay(backend, 0.5, 0.5, 0.03)
                decision, outcome, started = engine.relay_stream(
                    response, "id", document, ALLOWED, time.monotonic(),
                    lambda: None, route=route)
                self.assertTrue(socket.closed.is_set())
                self.assertEqual((decision.reason_code, outcome, started),
                                 ("STREAM_DEADLINE", "deadline_exceeded", expected_started))
                self.assertEqual(decision.http_status, 200 if expected_started else 504)
                self.assertEqual(response.started, expected_started)

    def test_completion_accepts_comment_and_crlf_events(self):
        chunks = [b": keepalive\r\n\r\n",
                  b'data: {"choices":[{"text":"ok"}]}\r\n\r\n',
                  b"data: [DONE]\r\n\r\n"]
        _, response, engine = relay(True, chunks)
        decision, outcome, started = engine.relay_stream(
            response, "id", {"prompt": "hi"}, ALLOWED, time.monotonic(),
            lambda: None, route=COMPLETIONS_ROUTE)
        self.assertEqual((decision.reason_code, outcome, started),
                         ("REQUEST_ALLOWED", "completed", True))
        self.assertEqual(response.chunks, chunks[1:])


if __name__ == "__main__":
    unittest.main()
