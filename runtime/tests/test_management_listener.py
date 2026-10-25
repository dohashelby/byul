"""Unit and HTTP tests for the read-only engine management adapter."""

from __future__ import annotations

from contextlib import closing
from http.client import HTTPConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest

from serving_runtime.mvp.management_listener import (
    DEFAULT_ALLOWED_PATHS,
    ManagementListener,
    ManagementListenerConfig,
    ManagementRequestHandler,
)


class EngineServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []
        super().__init__(("127.0.0.1", 0), EngineHandler)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)

    def start(self) -> "EngineServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class EngineHandler(BaseHTTPRequestHandler):
    server: EngineServer

    def do_GET(self) -> None:
        self._respond(head=False)

    def do_HEAD(self) -> None:
        self._respond(head=True)

    def _respond(self, *, head: bool) -> None:
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
            }
        )
        if self.path == "/slow":
            time.sleep(0.2)
        body = b"x" * 64 if self.path == "/large" else b"engine-management"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

    def log_message(self, format: str, *args: object) -> None:
        return


class ManagementConfigTests(unittest.TestCase):
    def test_defaults_are_loopback_and_expected_paths(self) -> None:
        config = ManagementListenerConfig.build(engine_url="http://127.0.0.1:8000")
        self.assertEqual(config.listen_host, "127.0.0.1")
        self.assertEqual(config.allowed_paths, frozenset(DEFAULT_ALLOWED_PATHS))

    def test_noncanonical_allowed_paths_are_rejected(self) -> None:
        for path in (
            "health",
            "//health",
            "/health?full=1",
            "/health/../metrics",
            "/%2e/health",
            "/health\\extra",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                ManagementListenerConfig.build(
                    engine_url="http://127.0.0.1:8000",
                    allowed_paths=(path,),
                )
        with self.assertRaises(ValueError):
            ManagementListenerConfig.build(
                engine_url="http://127.0.0.1:8000/base/../admin"
            )

    def test_credential_file_uses_backend_rules_and_is_read_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            credential = Path(temporary) / "engine-token"
            credential.write_bytes(b"engine-secret\r\n")
            credential.chmod(0o600)
            config = ManagementListenerConfig.build(
                engine_url="http://127.0.0.1:8000",
                credential_file=credential,
            )
            credential.write_text("changed", encoding="utf-8")
            self.assertEqual(config.authorization, "Bearer engine-secret")
            self.assertNotIn("engine-secret", repr(config))

    def test_original_request_target_is_used_before_base_handler_normalization(self) -> None:
        handler = object.__new__(ManagementRequestHandler)
        handler.command = "GET"
        for requestline, target, recorded in (
            ("GET //health HTTP/1.1", "//health", "//health"),
            (
                "GET http://adapter.invalid/health HTTP/1.1",
                "http://adapter.invalid/health",
                "http://adapter.invalid/health",
            ),
            ("GET /health?token=hidden HTTP/1.1", "/health?token=hidden", "/health"),
        ):
            with self.subTest(requestline=requestline):
                handler.requestline = requestline
                self.assertEqual(handler._request_target(), target)
                self.assertEqual(handler._record_path(), recorded)


class ManagementListenerHTTPTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.engine = EngineServer().start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.engine.stop)
        self.records = io.StringIO()
        host, port = self.engine.server_address
        config = ManagementListenerConfig.build(
            listen_port=0,
            engine_url=f"http://{host}:{port}",
            allowed_paths=("/health", "/large", "/slow"),
            timeout_s=0.05,
            max_response_bytes=32,
        )
        try:
            self.listener = ManagementListener(config, record_stream=self.records).start()
        except PermissionError as error:
            self.skipTest(f"local sockets are unavailable: {error}")
        self.addCleanup(self.listener.stop)
        self.host, self.port = self.listener.address

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = HTTPConnection(self.host, self.port, timeout=2)
        self.addCleanup(connection.close)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()

    def raw_request(self, target: str) -> int:
        with closing(socket.create_connection((self.host, self.port), timeout=2)) as client:
            client.sendall(
                f"GET {target} HTTP/1.1\r\nHost: test\r\nConnection: close\r\n\r\n".encode(
                    "ascii"
                )
            )
            response = HTTPResponse(client)
            response.begin()
            response.read()
            return response.status

    def parsed_records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.records.getvalue().splitlines()]

    def test_allowed_get_and_head_are_relayed(self) -> None:
        status, headers, body = self.request("GET", "/health")
        head_status, _, head_body = self.request("HEAD", "/health")
        self.assertEqual((status, body), (200, b"engine-management"))
        self.assertEqual(headers["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual((head_status, head_body), (200, b""))
        self.assertEqual(
            [(item["method"], item["path"]) for item in self.engine.requests],
            [("GET", "/health"), ("HEAD", "/health")],
        )

    def test_method_body_and_path_variants_are_not_relayed(self) -> None:
        cases = [
            ("POST", "/health", None, 405),
            ("GET", "/health", b"body", 400),
            ("GET", "/HEALTH", None, 404),
            ("GET", "/health?full=1", None, 404),
            ("GET", "/%2e/health", None, 404),
            ("GET", "/health/", None, 404),
            ("GET", "/a/../health", None, 404),
        ]
        for method, path, body, expected in cases:
            with self.subTest(method=method, path=path):
                status, _, _ = self.request(method, path, body=body)
                self.assertEqual(status, expected)
        self.assertEqual(self.raw_request("//health"), 404)
        self.assertEqual(self.raw_request("http://adapter.invalid/health"), 404)
        recorded_paths = [item["path"] for item in self.parsed_records()]
        self.assertEqual(recorded_paths[-2:], ["//health", "http://adapter.invalid/health"])
        self.assertEqual(self.engine.requests, [])

    def test_client_headers_are_not_forwarded(self) -> None:
        status, _, _ = self.request(
            "GET",
            "/health",
            headers={
                "Authorization": "Bearer client-secret",
                "X-Client-Secret": "do-not-forward",
                "Cookie": "session=do-not-forward",
            },
        )
        self.assertEqual(status, 200)
        received = self.engine.requests[-1]["headers"]
        self.assertNotIn("Authorization", received)
        self.assertNotIn("X-Client-Secret", received)
        self.assertNotIn("Cookie", received)

    def test_engine_timeout_and_connection_failure_are_distinct(self) -> None:
        timeout_status, _, timeout_body = self.request("GET", "/slow")
        self.assertEqual(timeout_status, 504)
        self.assertEqual(json.loads(timeout_body)["error"]["code"], "ENGINE_TIMEOUT")

        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        unused_port = probe.getsockname()[1]
        probe.close()
        failed_config = ManagementListenerConfig.build(
            listen_port=0,
            engine_url=f"http://127.0.0.1:{unused_port}",
            allowed_paths=("/health",),
            timeout_s=0.05,
        )
        failed = ManagementListener(failed_config, record_stream=io.StringIO()).start()
        self.addCleanup(failed.stop)
        connection = HTTPConnection(*failed.address, timeout=2)
        self.addCleanup(connection.close)
        connection.request("GET", "/health")
        response = connection.getresponse()
        failed_body = response.read()
        self.assertEqual(response.status, 502)
        self.assertEqual(
            json.loads(failed_body)["error"]["code"],
            "ENGINE_CONNECTION_FAILED",
        )

    def test_response_size_limit_is_enforced_without_partial_body(self) -> None:
        status, _, body = self.request("GET", "/large")
        self.assertEqual(status, 502)
        self.assertEqual(
            json.loads(body)["error"]["code"],
            "ENGINE_RESPONSE_TOO_LARGE",
        )
        self.assertNotIn(b"x" * 8, body)

    def test_records_contain_only_schema_counts_path_and_status(self) -> None:
        self.request(
            "GET",
            "/health",
            headers={"Authorization": "Bearer hidden-client-secret"},
        )
        self.request("GET", "/health?token=hidden-query-secret")
        records = self.parsed_records()
        self.assertEqual(records[0]["allowed_count"], 1)
        self.assertEqual(records[0]["denied_count"], 0)
        self.assertEqual(records[1]["allowed_count"], 1)
        self.assertEqual(records[1]["denied_count"], 1)
        self.assertEqual(records[1]["path"], "/health")
        self.assertEqual(
            set(records[0]),
            {"schema", "allowed_count", "denied_count", "path", "status_code"},
        )
        serialized = self.records.getvalue()
        self.assertNotIn("hidden-client-secret", serialized)
        self.assertNotIn("hidden-query-secret", serialized)


if __name__ == "__main__":
    unittest.main()
