"""Gateway resource ordering, slow-client limits, and connection cap tests."""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock
from urllib.request import Request, urlopen

from serving_runtime.mvp.app import GatewayApp
from serving_runtime.mvp.auth import AuthResult, Principal
from serving_runtime.mvp.backend import BackendConfig
from serving_runtime.mvp.config import MVPConfig
from serving_runtime.enforcement.events import ObservationQuery
from serving_runtime.mvp.http_handler import (
    HandlerDependencies,
    LimitedThreadingHTTPServer,
    _DeadlineReader,
    create_handler,
)
from serving_runtime.mvp.mock_engine import MockEngine
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.policy_state import load_revocations
from serving_runtime.mvp.request_flow import ConcurrencyLimiter, RequestFlow
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.store import JsonlStore


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "examples/mvp/policy-v1.1-context.json"


def body(*, stream: bool = False) -> bytes:
    return json.dumps(
        {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hello"}],
            "max_tokens": 64,
            "stream": stream,
        }
    ).encode("utf-8")


class _Response:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.sent = None

    def request_header(self, name: str) -> str | None:
        return {
            "Content-Length": str(len(self.payload)),
            "Content-Type": "application/json",
        }.get(name)

    def read_request_body(self, length: int) -> bytes:
        return self.payload[:length]

    def send_error_decision(self, decision, request_id, **_kwargs) -> None:
        self.sent = (decision, request_id)

    def send_response_body(self, *_args, **_kwargs) -> None:
        raise AssertionError("request must not be relayed")

    def start_stream_response(self, _request_id: str) -> None:
        raise AssertionError("request must not be relayed")

    def write_stream_chunk(self, _chunk: bytes) -> None:
        raise AssertionError("request must not be relayed")


class _Engine:
    def __init__(self) -> None:
        self.tokenize_calls = 0

    def count_input_tokens(self, *_args) -> int:
        self.tokenize_calls += 1
        return 1

    @staticmethod
    def receipt_not_queried() -> str:
        return "not_queried"


class _Relay:
    def relay(self, *_args):
        raise AssertionError("request must not be relayed")


class _AuthState:
    state = "ok"

    def __init__(self, result: AuthResult) -> None:
        self.result = result

    def authenticate(self, _authorization: str | None) -> AuthResult:
        return self.result

    @staticmethod
    def roles_for(_route: str) -> tuple[str, ...]:
        return ("inference",)


class ResourceOrderingTests(unittest.TestCase):
    @staticmethod
    def auth_state(result: AuthResult) -> _AuthState:
        return _AuthState(result)

    def flow(
        self, temporary: str, *, auth: AuthResult, target_hold
    ) -> tuple[RequestFlow, RequestRecords]:
        records = RequestRecords(
            temporary,
            "session",
            lambda: load_policy(POLICY),
            "mock",
        )
        flow = RequestFlow(
            BackendConfig.mock(("127.0.0.1", 1)),
            lambda: load_policy(POLICY),
            lambda: load_revocations(None),
            records,
            _Engine(),
            _Relay(),
            ConcurrencyLimiter(1),
            auth_state=lambda: self.auth_state(auth),
            target_hold=target_hold,
        )
        return flow, records

    def test_authentication_failure_precedes_target_hold_and_body_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_hold = Mock(
                return_value=("TARGET_MISMATCH", "target registration mismatch")
            )
            flow, _records = self.flow(
                temporary,
                auth=AuthResult("missing"),
                target_hold=target_hold,
            )
            response = _Response(body())
            response.read_request_body = lambda _length: (_ for _ in ()).throw(
                AssertionError("body must not be read after failed authentication")
            )
            flow.chat(response, "b" * 32)
            self.assertEqual(response.sent[0].http_status, 401)
            self.assertEqual(response.sent[0].reason_code, "AUTH_REQUIRED")
            target_hold.assert_not_called()

    def test_authenticated_target_hold_precedes_body_and_records_principal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            principal = Principal("client-a", ("inference",), "")
            target_hold = Mock(
                return_value=("TARGET_MISMATCH", "target registration mismatch")
            )
            flow, records = self.flow(
                temporary,
                auth=AuthResult("authenticated", principal),
                target_hold=target_hold,
            )
            response = _Response(body())
            response.read_request_body = lambda _length: (_ for _ in ()).throw(
                AssertionError("body must not be read before target hold")
            )
            flow.chat(response, "c" * 32)
            self.assertEqual(response.sent[0].http_status, 503)
            self.assertEqual(response.sent[0].reason_code, "TARGET_MISMATCH")
            target_hold.assert_called_once_with("mock-engine-001")
            stored, corrupt = records.store.read_all()
            self.assertEqual(corrupt, [])
            self.assertEqual(stored[-1]["auth"]["principal_id"], "client-a")

    def test_full_concurrency_returns_429_without_tokenize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            limiter = ConcurrencyLimiter(1)
            self.assertTrue(limiter.try_acquire())
            backend = BackendConfig.mock(("127.0.0.1", 1))
            records = RequestRecords(
                temporary,
                "session",
                lambda: load_policy(POLICY),
                "mock",
            )
            engine = _Engine()
            flow = RequestFlow(
                backend,
                lambda: load_policy(POLICY),
                lambda: load_revocations(None),
                records,
                engine,
                _Relay(),
                limiter,
            )
            response = _Response(body())
            flow.chat(response, "a" * 32)
            self.assertEqual(response.sent[0].http_status, 429)
            self.assertEqual(response.sent[0].reason_code, "CONCURRENCY_LIMIT")
            self.assertEqual(engine.tokenize_calls, 0)
            self.assertEqual(limiter.status(), {"in_flight": 1, "limit": 1})
            limiter.release()


class ConnectionCapUnitTests(unittest.TestCase):
    def test_cap_rejects_without_starting_another_thread(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.payload = b""

            def settimeout(self, _timeout: float) -> None:
                pass

            def sendall(self, payload: bytes) -> None:
                self.payload = payload

        server = object.__new__(LimitedThreadingHTTPServer)
        server._connection_slots = threading.BoundedSemaphore(1)
        self.assertTrue(server._connection_slots.acquire(blocking=False))
        server._connection_lock = threading.Lock()
        server._active_connections = 1
        server._rejected_connections = 0
        server._max_connections = 1
        rejected = []
        server._on_connection_rejected = lambda request_id, decision: rejected.append(
            (request_id, decision)
        )
        server.shutdown_request = lambda _request: None
        request = FakeSocket()
        server.process_request(request, ("127.0.0.1", 1))
        self.assertIn(b"503 Service Unavailable", request.payload)
        self.assertEqual(rejected[0][1].reason_code, "CONNECTION_LIMIT")
        self.assertEqual(server.connection_status()["rejected"], 1)


class AbsoluteDeadlineReaderTests(unittest.TestCase):
    def test_one_byte_drip_cannot_extend_the_absolute_deadline(self) -> None:
        class Clock:
            value = 0.0

            def __call__(self) -> float:
                return self.value

        class DripSocket:
            def __init__(self, clock: Clock) -> None:
                self.clock = clock
                self.timeout = 0.0
                self.calls = 0

            def settimeout(self, timeout: float) -> None:
                self.timeout = timeout

            def recv(self, _size: int) -> bytes:
                self.calls += 1
                delay = 0.4
                if delay >= self.timeout:
                    self.clock.value += self.timeout
                    raise TimeoutError("simulated deadline")
                self.clock.value += delay
                return b"x"

        clock = Clock()
        connection = DripSocket(clock)
        reader = _DeadlineReader(connection, clock=clock)
        reader.set_deadline(1.0)
        with self.assertRaises(TimeoutError):
            reader.read(4)
        self.assertEqual(connection.calls, 3)
        self.assertAlmostEqual(clock.value, 1.0)


class HandlerSocketPairTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.transport_errors = []

    def exchange(self, payload: bytes, chat, *, delay: float = 0) -> bytes:
        client, server_socket = socket.socketpair()
        self.addCleanup(client.close)
        self.addCleanup(server_socket.close)
        dependencies = HandlerDependencies(
            chat=chat,
            status=lambda: {},
            policy_status=lambda: {},
            baseline_status=lambda: {},
            collectors_status=lambda: {},
            revalidation_status=lambda: {},
            queries=None,
            observations=ObservationQuery(Path(self.temporary.name) / "observations.jsonl"),
            receipt_lookup=lambda _request_id: {},
            observation_session_id="session",
            lab_report=lambda: {"state": "not_configured"},
            record_transport_error=lambda request_id, decision: self.transport_errors.append(
                (request_id, decision)
            ),
            request_header_timeout_s=0.03,
            request_body_timeout_s=0.03,
        )
        handler = create_handler(dependencies)

        def serve() -> None:
            try:
                handler(server_socket, ("local", 0), object())
            finally:
                server_socket.close()

        thread = threading.Thread(target=serve)
        thread.start()
        try:
            client.sendall(payload)
        except PermissionError as error:
            client.close()
            thread.join(timeout=1)
            self.skipTest(f"socket I/O is unavailable: {error}")
        if delay:
            time.sleep(delay)
        chunks = []
        client.settimeout(1)
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        return b"".join(chunks)

    def test_header_and_body_timeouts_return_408_and_are_recorded(self) -> None:
        header = self.exchange(
            b"POST /v1/chat/completions HTTP/1.0\r\nHost: local\r\n",
            lambda _io, _request_id: None,
            delay=0.06,
        )
        self.assertIn(b"408 Request Timeout", header)
        self.assertEqual(self.transport_errors[-1][1].reason_code, "REQUEST_HEADER_TIMEOUT")

        def read_body(io, _request_id: str) -> None:
            io.read_request_body(100)

        body_timeout = self.exchange(
            b"POST /v1/chat/completions HTTP/1.0\r\nHost: local\r\nContent-Length: 100\r\n\r\n{",
            read_body,
            delay=0.06,
        )
        self.assertIn(b"408 Request Timeout", body_timeout)
        self.assertEqual(self.transport_errors[-1][1].reason_code, "REQUEST_BODY_TIMEOUT")

    def test_normal_and_stream_responses_still_work(self) -> None:
        request = (
            b"POST /v1/chat/completions HTTP/1.0\r\nHost: local\r\n"
            b"Content-Length: 2\r\n\r\n{}"
        )

        def normal(io, request_id: str) -> None:
            self.assertEqual(io.read_request_body(2), b"{}")
            io.send_response_body(
                200,
                "application/json; charset=utf-8",
                b"{}",
                request_id=request_id,
            )

        self.assertIn(b"HTTP/1.0 200 OK", self.exchange(request, normal))

        def stream(io, request_id: str) -> None:
            self.assertEqual(io.read_request_body(2), b"{}")
            io.start_stream_response(request_id)
            io.write_stream_chunk(b"data: [DONE]\n\n")

        streamed = self.exchange(request, stream)
        self.assertIn(b"HTTP/1.0 200 OK", streamed)
        self.assertIn(b"data: [DONE]", streamed)

    def test_header_and_body_one_byte_drips_hit_absolute_deadlines(self) -> None:
        def drip(prefix: bytes, payload: bytes, chat) -> tuple[bytes, bool]:
            client, server_socket = socket.socketpair()
            self.addCleanup(client.close)
            self.addCleanup(server_socket.close)
            dependencies = HandlerDependencies(
                chat=chat,
                status=lambda: {},
                policy_status=lambda: {},
                baseline_status=lambda: {},
                collectors_status=lambda: {},
                revalidation_status=lambda: {},
                queries=None,
                observations=ObservationQuery(
                    Path(self.temporary.name) / "observations.jsonl"
                ),
                receipt_lookup=lambda _request_id: {},
                observation_session_id="session",
                lab_report=lambda: {"state": "not_configured"},
                record_transport_error=lambda request_id, decision: self.transport_errors.append(
                    (request_id, decision)
                ),
                request_header_timeout_s=0.06,
                request_body_timeout_s=0.06,
            )
            handler = create_handler(dependencies)

            def serve() -> None:
                try:
                    handler(server_socket, ("local", 0), object())
                finally:
                    server_socket.close()

            thread = threading.Thread(target=serve)
            thread.start()
            response_may_be_lost = False
            try:
                if prefix:
                    client.sendall(prefix)
                for byte in payload:
                    client.sendall(bytes([byte]))
                    time.sleep(0.015)
            except PermissionError as error:
                client.close()
                thread.join(timeout=1)
                self.skipTest(f"socket I/O is unavailable: {error}")
            except (BrokenPipeError, ConnectionResetError):
                response_may_be_lost = True
            client.settimeout(1)
            chunks = []
            while True:
                try:
                    chunk = client.recv(4096)
                except ConnectionResetError:
                    response_may_be_lost = True
                    break
                if not chunk:
                    break
                chunks.append(chunk)
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            return b"".join(chunks), response_may_be_lost

        header, header_reset = drip(
            b"",
            b"POST /v1/chat/completions HTTP/1.0\r\nHost: local\r\n\r\n",
            lambda _io, _request_id: None,
        )
        self.assertEqual(
            self.transport_errors[-1][1].reason_code, "REQUEST_HEADER_TIMEOUT"
        )
        if not header_reset:
            self.assertIn(b"408 Request Timeout", header)

        def read_body(io, _request_id: str) -> None:
            io.read_request_body(20)

        body, body_reset = drip(
            b"POST /v1/chat/completions HTTP/1.0\r\nHost: local\r\nContent-Length: 20\r\n\r\n",
            b"x" * 20,
            read_body,
        )
        self.assertEqual(
            self.transport_errors[-1][1].reason_code, "REQUEST_BODY_TIMEOUT"
        )
        if not body_reset:
            self.assertIn(b"408 Request Timeout", body)


class SlowClientHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name)
        try:
            self.engine = MockEngine(
                port=0,
                receipts_path=self.data_dir / "engine_receipts.jsonl",
            ).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)

    def start_gateway(self, **updates: object) -> GatewayApp:
        options: dict[str, object] = {
            "gateway_port": 0,
            "engine_port": 0,
            "policy_path": str(POLICY),
            "data_dir": str(self.data_dir),
            "request_header_timeout_s": 0.08,
            "request_body_timeout_s": 0.08,
            "max_connections": 8,
        }
        options.update(updates)
        gateway = GatewayApp(
            MVPConfig(**options), load_policy(POLICY), self.engine.address
        ).start()
        self.addCleanup(gateway.stop)
        return gateway

    @staticmethod
    def raw_response(gateway: GatewayApp, payload: bytes, *, wait: float = 0) -> bytes:
        sock = socket.create_connection(gateway.address, timeout=2)
        with closing(sock):
            sock.sendall(payload)
            if wait:
                time.sleep(wait)
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

    def final_codes(self) -> list[str]:
        records, _ = JsonlStore(self.data_dir / "gateway_records.jsonl").read_all()
        return [
            record.get("decision", {}).get("reason_code")
            for record in records
            if record.get("phase") == "final"
        ]

    def test_slow_header_slow_body_and_incomplete_body_are_recorded(self) -> None:
        gateway = self.start_gateway()
        slow_header = self.raw_response(
            gateway,
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: local\r\n",
            wait=0.12,
        )
        self.assertIn(b"408", slow_header)
        slow_body = self.raw_response(
            gateway,
            b"POST /v1/chat/completions HTTP/1.1\r\nHost: local\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{",
            wait=0.12,
        )
        self.assertIn(b"408", slow_body)

        sock = socket.create_connection(gateway.address, timeout=2)
        with closing(sock):
            sock.sendall(
                b"POST /v1/chat/completions HTTP/1.1\r\nHost: local\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{}"
            )
            sock.shutdown(socket.SHUT_WR)
            incomplete = sock.recv(4096)
        self.assertIn(b"400", incomplete)
        stop = time.monotonic() + 1
        while time.monotonic() < stop and len(self.final_codes()) < 3:
            time.sleep(0.01)
        self.assertIn("REQUEST_HEADER_TIMEOUT", self.final_codes())
        self.assertIn("REQUEST_BODY_TIMEOUT", self.final_codes())
        self.assertIn("INVALID_CONTENT_LENGTH", self.final_codes())

    def test_connection_cap_is_separate_and_normal_and_stream_still_work(self) -> None:
        gateway = self.start_gateway(max_connections=1, request_header_timeout_s=1)
        held = socket.create_connection(gateway.address, timeout=2)
        self.addCleanup(held.close)
        held.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: local\r\n")
        time.sleep(0.05)
        rejected = self.raw_response(gateway, b"GET /api/status HTTP/1.0\r\n\r\n")
        self.assertIn(b"503 Service Unavailable", rejected)
        held.close()
        # Wait for the server to release the held slot; a fixed short sleep
        # raced the handler thread when the whole suite ran under load.
        deadline = time.monotonic() + 3
        while gateway._server.connection_status()["active"] and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(gateway._server.connection_status()["active"], 0)

        for stream in (False, True):
            request = Request(
                f"http://{gateway.address[0]}:{gateway.address[1]}/v1/chat/completions",
                data=body(stream=stream),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with closing(urlopen(request, timeout=2)) as response:
                payload = response.read()
            self.assertEqual(response.status, 200)
            if stream:
                self.assertIn(b"[DONE]", payload)


if __name__ == "__main__":
    unittest.main()
