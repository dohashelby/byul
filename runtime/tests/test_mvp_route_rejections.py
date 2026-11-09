"""Out-of-list HTTP attempts have a bounded, separate audit journal."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import socket
import threading
import unittest
from unittest.mock import Mock

from serving_runtime.mvp.http_handler import Handler, create_handler
from serving_runtime.mvp.store import JsonlStore
from serving_runtime.mvp.route_rejections import RouteRejections


class RouteRejectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.audit = RouteRejections(self.root, limit=2)

    def handler(self, method: str, path: str) -> tuple[Handler, Mock]:
        handler = object.__new__(Handler)
        handler.command = method
        handler.path = path
        handler._transport_request_id = "a" * 32
        handler.rfile = Mock()
        handler.dependencies = SimpleNamespace(
            record_rejected=self.audit.record,
            chat=Mock(),
            completions=Mock(),
            authorize_read=lambda _header: None,
        )
        sender = Mock()
        handler._send_json = sender
        return handler, sender

    def records(self) -> list[dict[str, object]]:
        records, corrupt = JsonlStore(self.root / "gateway_route_rejections.jsonl").read_all()
        self.assertEqual(corrupt, [])
        return records

    def test_unknown_methods_and_get_record_path_status_and_no_engine_arrival(self) -> None:
        self.audit = RouteRejections(self.root, limit=10)
        for method, path in (("POST", "/v1/realtime"), ("GET", "/missing"), ("DELETE", "/outside"), ("PATCH", "/outside")):
            with self.subTest(method=method):
                handler, sender = self.handler(method, path)
                if method == "GET":
                    handler.do_GET()
                elif method == "POST":
                    handler.do_POST()
                else:
                    handler._unsupported_method()
                self.assertEqual(sender.call_args.args[0], 404)
                self.assertEqual(sender.call_args.kwargs.get("close_connection", False), method == "POST")
                handler.dependencies.chat.assert_not_called()
                handler.dependencies.completions.assert_not_called()
                handler.rfile.read.assert_not_called()
        records = self.records()
        self.assertEqual([(r["method"], r["path"], r["response_code"]) for r in records], [("POST", "/v1/realtime", 404), ("GET", "/missing", 404), ("DELETE", "/outside", 404), ("PATCH", "/outside", 404)])
        self.assertTrue(all(r["request_id"] == "a" * 32 and r["reason"] == "NOT_FOUND" and r["forwarded_to_engine"] is False for r in records))
        self.assertFalse((self.root / "gateway_records.jsonl").exists())

    def test_all_sglang_native_entrypoints_are_rejected_and_audited(self) -> None:
        paths = ("/generate", "/v1/responses", "/v1/messages", "/api/chat", "/api/generate",
                 "/invocations", "/vertex_generate", "/v1/realtime", "/v1/score",
                 "/encode", "/classify", "/v1/audio/transcriptions")
        self.audit = RouteRejections(self.root, limit=len(paths))
        for path in paths:
            with self.subTest(path=path):
                handler, sender = self.handler("POST", path)
                handler.do_POST()
                self.assertIn(sender.call_args.args[0], {404, 405})
                handler.dependencies.chat.assert_not_called()
                handler.dependencies.completions.assert_not_called()
        records = self.records()
        self.assertEqual([row["path"] for row in records], list(paths))
        self.assertTrue(all(row["forwarded_to_engine"] is False for row in records))

    def test_query_headers_body_and_control_characters_are_never_recorded(self) -> None:
        handler, _ = self.handler("GET", "/v1/realtime%0Aevil?token=query-secret")
        handler.headers = {"Authorization": "Bearer header-secret"}
        handler.do_GET()
        raw = (self.root / "gateway_route_rejections.jsonl").read_text()
        self.assertNotIn("query-secret", raw)
        self.assertNotIn("header-secret", raw)
        self.assertNotIn("Authorization", raw)
        self.assertNotIn("token", raw)
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertLessEqual(len(self.records()[0]["path"]), 256)

    def test_long_and_control_character_path_is_sanitized(self) -> None:
        self.audit.record("b" * 32, "GET", "/x\n" + "y" * 300, 404, "NOT_FOUND")
        record = self.records()[0]
        self.assertEqual(len(record["path"]), 256)
        self.assertNotIn("\n", record["path"])
        self.assertTrue(record["path"].startswith("/x?"))

    def test_unauthenticated_unknown_api_path_records_actual_response(self) -> None:
        handler, _ = self.handler("GET", "/api/missing?password=query-secret")
        handler.headers = {}
        handler.request_header = Mock(return_value=None)
        handler.dependencies.authorize_read = lambda _header: (401, "AUTH_REQUIRED", "authentication required")
        handler._send_read_refusal = Mock()
        handler.do_GET()
        handler._send_read_refusal.assert_called_once_with(401, "AUTH_REQUIRED", "authentication required")
        record = self.records()[0]
        self.assertEqual((record["path"], record["response_code"], record["reason"]), ("/api/missing", 401, "AUTH_REQUIRED"))
        self.assertNotIn("query-secret", (self.root / "gateway_route_rejections.jsonl").read_text())

    def test_known_path_405_returns_exact_server_id_and_audit_without_body_or_engine(self) -> None:
        self.audit = RouteRejections(self.root, limit=100)
        cases = [(method, path, allow) for method in ("GET", "PUT", "HEAD", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT")
                 for path, allow in (("/v1/completions", "POST"), ("/v1/chat/completions", "POST"))]
        cases += [(method, "/api/status", "GET") for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS")]
        for index, (method, path, allow) in enumerate(cases):
            with self.subTest(method=method, path=path):
                handler, sender = self.handler(method, path + "?token=PRIVATE_QUERY")
                handler._transport_request_id = f"{index:032x}"
                handler.headers = {"Authorization": "Bearer PRIVATE_TOKEN", "X-Request-ID": "PRIVATE_CLIENT_ID"}
                if method == "GET":
                    handler.do_GET()
                elif method == "POST":
                    handler.do_POST()
                else:
                    handler._unsupported_method()
                self.assertEqual(sender.call_args.args[0], 405)
                self.assertEqual(sender.call_args.kwargs["request_id"], f"{index:032x}")
                self.assertEqual(sender.call_args.kwargs["extra_headers"], {"Allow": allow})
                self.assertEqual(sender.call_args.kwargs["close_connection"], method == "POST")
                handler.rfile.read.assert_not_called()
                handler.dependencies.chat.assert_not_called()
                handler.dependencies.completions.assert_not_called()
        records = self.records()
        self.assertEqual(len(records), len(cases))
        self.assertEqual([r["request_id"] for r in records], [f"{i:032x}" for i in range(len(cases))])
        self.assertTrue(all(r["response_code"] == 405 and r["reason"] == "METHOD_NOT_ALLOWED" and r["forwarded_to_engine"] is False for r in records))
        self.assertNotIn("PRIVATE_", (self.root / "gateway_route_rejections.jsonl").read_text())
        self.assertFalse((self.root / "gateway_records.jsonl").exists())

    def test_405_write_failure_keeps_response_id_and_reports_audit_failure(self) -> None:
        self.audit.store.append = Mock(side_effect=OSError("PRIVATE_STORAGE_ERROR"))
        handler, sender = self.handler("GET", "/v1/chat/completions")
        handler.do_GET()
        self.assertEqual(sender.call_args.args[0], 405)
        self.assertEqual(sender.call_args.kwargs["request_id"], "a" * 32)
        self.assertEqual(self.audit.status()["write_failure_count"], 1)
        self.assertEqual(self.records(), [])

    def test_405_overflow_retains_bounded_audit_and_exact_response_id(self) -> None:
        for index in range(3):
            handler, sender = self.handler("GET", "/v1/completions")
            handler._transport_request_id = f"{index:032x}"
            handler.do_GET()
            self.assertEqual(sender.call_args.kwargs["request_id"], f"{index:032x}")
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.audit.overflow_count, 1)

    def test_supported_api_get_keeps_authorization_boundary(self) -> None:
        handler, sender = self.handler("GET", "/api/status")
        handler.request_header = Mock(return_value=None)
        handler.dependencies.authorize_read = lambda _: (401, "AUTH_REQUIRED", "required")
        handler._send_read_refusal = Mock()
        handler.do_GET()
        handler._send_read_refusal.assert_called_once_with(401, "AUTH_REQUIRED", "required")
        sender.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_wire_405_header_matches_durable_audit_and_ignores_client_id(self) -> None:
        for method, path, allowed in (("GET", "/v1/chat/completions", "POST"), ("POST", "/api/status", "GET")):
            with self.subTest(method=method):
                handler, _ = self.handler(method, path)
                deps = handler.dependencies
                deps.request_header_timeout_s = deps.request_body_timeout_s = 1
                deps.record_transport_error = Mock()
                configured = create_handler(deps)
                client, server_socket = socket.socketpair()
                def serve():
                    try:
                        configured(server_socket, ("local", 0), object())
                    finally:
                        server_socket.close()
                thread = threading.Thread(target=serve)
                thread.start()
                try:
                    body = b'PRIVATE_BODY'
                    client.sendall(f"{method} {path}?token=PRIVATE_QUERY HTTP/1.0\r\nAuthorization: Bearer PRIVATE_TOKEN\r\nX-Request-ID: CLIENT_CHOSEN\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body)
                    client.settimeout(2)
                    raw = b''
                    while True:
                        try: chunk = client.recv(4096)
                        except ConnectionResetError: break
                        if not chunk: break
                        raw += chunk
                finally:
                    client.close(); thread.join(timeout=2)
                self.assertFalse(thread.is_alive())
                headers = raw.split(b'\r\n\r\n', 1)[0].decode()
                self.assertIn(' 405 ', headers)
                self.assertIn('Allow: ' + allowed, headers)
                request_id, = [line.split(': ', 1)[1] for line in headers.split('\r\n') if line.startswith('X-Request-ID: ')]
                self.assertRegex(request_id, r'^[0-9a-f]{32}$')
                self.assertEqual(self.records()[-1]['request_id'], request_id)
                self.assertNotIn('PRIVATE_', (self.root / 'gateway_route_rejections.jsonl').read_text())
                self.assertNotIn('CLIENT_CHOSEN', (self.root / 'gateway_route_rejections.jsonl').read_text())
                deps.chat.assert_not_called(); deps.completions.assert_not_called()

    def test_overflow_is_counted_without_more_records(self) -> None:
        for index in range(5):
            handler, _ = self.handler("PUT", f"/missing-{index}")
            handler._unsupported_method()
        self.assertEqual(len(self.records()), 2)
        self.assertEqual(self.audit.overflow_count, 3)

