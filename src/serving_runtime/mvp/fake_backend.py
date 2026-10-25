"""Test-only fake external backend; it is not a vLLM implementation or evidence source."""

from __future__ import annotations

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from .mock_engine import simulated_token_count


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FakeBackend:
    """Exercise the external adapter without claiming behavior of a real vLLM server."""

    _MODES = frozenset(
        {
            "normal",
            "http_400",
            "http_401",
            "http_403",
            "http_429",
            "http_500",
            "invalid_json",
            "sse_truncated",
            "disconnect",
            "redirect",
            "header_timeout",
        }
    )

    def __init__(
        self,
        port: int = 0,
        *,
        host: str = "127.0.0.1",
        mode: str = "normal",
        model_id: str = "backend-test-model",
        version: str = "fake-vllm-test-0",
        required_authorization: str | None = None,
        path_prefix: str = "",
        header_delay_s: float = 0.2,
        stream_interval_s: float = 0.01,
        flavor: str = "vllm",
    ) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer from 0 through 65535")
        if not isinstance(host, str) or not host:
            raise ValueError("host must be a non-empty string")
        if mode not in self._MODES:
            raise ValueError("invalid fake backend mode")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError("model_id must be a non-empty string")
        if path_prefix and (not path_prefix.startswith("/") or path_prefix.endswith("/")):
            raise ValueError("path_prefix must be empty or start with / without a trailing /")
        if header_delay_s < 0 or stream_interval_s < 0:
            raise ValueError("delays must not be negative")
        if flavor not in {"vllm", "sglang"}:
            raise ValueError("flavor must be vllm or sglang")
        self.port = port
        self.host = host
        self.mode = mode
        self.model_id = model_id
        self.version = version
        self.path_prefix = path_prefix
        self.header_delay_s = header_delay_s
        self.stream_interval_s = stream_interval_s
        # Test-only imitation of each engine's routes and metric names; it is not
        # the real engine and proves nothing about a real deployment.
        self.flavor = flavor
        self._required_authorization = required_authorization
        self.metrics_failure: str | None = None
        self.tokenize_failure = False
        self.tokenize_response_body: bytes | None = None
        self.tokenize_calls = 0
        self._requests: list[dict[str, Any]] = []
        self._redirect_follow_count = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("fake backend is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def base_url(self) -> str:
        host, port = self.address
        return f"http://{host}:{port}{self.path_prefix}"

    @property
    def requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._requests]

    @property
    def request_count(self) -> int:
        with self._lock:
            return len(self._requests)

    @property
    def redirect_follow_count(self) -> int:
        with self._lock:
            return self._redirect_follow_count

    def set_mode(self, mode: str) -> None:
        if mode not in self._MODES:
            raise ValueError("invalid fake backend mode")
        self.mode = mode

    def set_metrics_failure(self, failure: str | None) -> None:
        if failure not in {None, "http_500", "disconnect"}:
            raise ValueError("invalid metrics failure")
        self.metrics_failure = failure

    def start(self) -> "FakeBackend":
        if self._server is not None:
            raise RuntimeError("fake backend is already started")
        self._stop.clear()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ServingRuntimeFakeBackendTest/1.0"
            protocol_version = "HTTP/1.0"

            def log_message(self, format: str, *args: object) -> None:
                return

            def _send_json(
                self,
                status: int,
                document: dict[str, Any],
                *,
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                payload = {
                    **document,
                    "data_origin": "fake_backend_test",
                    "test_only": True,
                }
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                for name, value in (extra_headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def _disconnect(self) -> None:
                try:
                    self.connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.connection.close()
                self.close_connection = True

            def _route(self) -> str | None:
                path = urlsplit(self.path).path
                if owner.path_prefix:
                    if not path.startswith(owner.path_prefix + "/"):
                        return None
                    return path[len(owner.path_prefix):]
                return path

            def _authorized(self) -> bool:
                required = owner._required_authorization
                return required is None or self.headers.get("Authorization") == required

            def _record(self, document: dict[str, Any]) -> dict[str, Any]:
                request_id = self.headers.get("X-Request-ID", "")
                record = {
                    "request_id": request_id,
                    "received_at": _utc_now(),
                    "model": document.get("model"),
                    "max_tokens": document.get("max_tokens"),
                    "max_completion_tokens": document.get("max_completion_tokens"),
                    "stream": document.get("stream") is True,
                    "n": document.get("n"),
                    # Test-only visibility into which request fields reached
                    # the backend, and the original values of non-Gateway fields.
                    "field_names": sorted(document),
                    "extra_values": {
                        key: value
                        for key, value in document.items()
                        if key
                        not in {
                            "model",
                            "messages",
                            "max_tokens",
                            "max_completion_tokens",
                            "stream",
                            "n",
                        }
                    },
                    "host": self.headers.get("Host"),
                    "header_names": sorted(name.lower() for name in self.headers),
                    "authorization_present": self.headers.get("Authorization") is not None,
                    "x_forwarded_host": self.headers.get("X-Forwarded-Host"),
                    "data_origin": "fake_backend_test",
                    "test_only": True,
                }
                with owner._lock:
                    owner._requests.append(record)
                return record

            def _send_stream(self, request_id: str) -> None:
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for index in range(2):
                    event = {
                        "id": f"fake-{request_id}",
                        "object": "chat.completion.chunk",
                        "model": owner.model_id,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": f"fake-{index + 1}"},
                                "finish_reason": None,
                            }
                        ],
                        "data_origin": "fake_backend_test",
                        "test_only": True,
                    }
                    encoded = (
                        "data: " + json.dumps(event, separators=(",", ":")) + "\n\n"
                    ).encode("utf-8")
                    try:
                        self.wfile.write(encoded)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    if owner.stream_interval_s and owner._stop.wait(owner.stream_interval_s):
                        return
                if owner.mode == "sse_truncated":
                    return
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return

            def do_GET(self) -> None:
                route = self._route()
                if route == "/redirect-target":
                    with owner._lock:
                        owner._redirect_follow_count += 1
                    self._send_json(200, {"unexpected_redirect_follow": True})
                    return
                if not self._authorized():
                    self._send_json(401, {"error": {"code": "FAKE_AUTH_REQUIRED"}})
                    return
                if route == "/health":
                    self._send_json(
                        200,
                        {
                            "status": "reachable",
                            "meaning": "test-only reachability; inference is not verified",
                        },
                    )
                    return
                if route == "/v1/models":
                    self._send_json(
                        200,
                        {"object": "list", "data": [{"id": owner.model_id}]},
                    )
                    return
                if route == "/version" and owner.flavor == "vllm":
                    # SGLang v0.5.21 has no /version route.
                    self._send_json(200, {"version": owner.version})
                    return
                if route == "/metrics":
                    if owner.metrics_failure == "disconnect":
                        self._disconnect()
                        return
                    if owner.metrics_failure == "http_500":
                        self._send_json(500, {"error": {"code": "FAKE_METRICS_FAILURE"}})
                        return
                    if owner.flavor == "sglang":
                        # Names from SGLang v0.5.21 metrics_collector.py.
                        families = (
                            "# TYPE sglang:num_running_reqs gauge\n"
                            "sglang:num_running_reqs{model_name=\"m\"} 1\n"
                            "# TYPE sglang:num_queue_reqs gauge\n"
                            "sglang:num_queue_reqs{model_name=\"m\"} 0\n"
                            "# TYPE sglang:token_usage gauge\n"
                            "sglang:token_usage{model_name=\"m\"} 0.25\n"
                            "# TYPE sglang:generation_tokens_total counter\n"
                            "sglang:generation_tokens_total{model_name=\"m\"} 42\n"
                        )
                    else:
                        families = (
                            "# TYPE vllm:num_requests_running gauge\n"
                            "vllm:num_requests_running 1\n"
                            "# TYPE vllm:num_requests_waiting gauge\n"
                            "vllm:num_requests_waiting 0\n"
                        )
                    encoded = (
                        "# test-only fake backend metrics; data_origin=fake_backend_test\n"
                        + families
                        + "# TYPE unrelated_metric counter\n"
                        "unrelated_metric 99\n"
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                if route == "/_fake/state":
                    self._send_json(
                        200,
                        {
                            "request_count": owner.request_count,
                            "requests": owner.requests,
                            "redirect_follow_count": owner.redirect_follow_count,
                        },
                    )
                    return
                self._send_json(404, {"error": {"code": "FAKE_NOT_FOUND"}})

            def _tokenize(self) -> None:
                if not self._authorized():
                    self._send_json(401, {"error": {"code": "FAKE_AUTH_REQUIRED"}})
                    return
                with owner._lock:
                    owner.tokenize_calls += 1
                if owner.tokenize_failure:
                    self._send_json(500, {"error": {"code": "FAKE_TOKENIZE_FAILURE"}})
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    document = json.loads(self.rfile.read(content_length).decode("utf-8"))
                    count = simulated_token_count(
                        document.get("messages") if isinstance(document, dict) else None
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._send_json(400, {"error": {"code": "FAKE_MALFORMED"}})
                    return
                if owner.tokenize_response_body is not None:
                    encoded = owner.tokenize_response_body
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                self._send_json(200, {"count": count})

            def do_POST(self) -> None:
                route = self._route()
                if route == "/tokenize" or (
                    owner.flavor == "sglang" and route == "/v1/tokenize"
                ):
                    self._tokenize()
                    return
                if route != "/v1/chat/completions":
                    self._send_json(404, {"error": {"code": "FAKE_NOT_FOUND"}})
                    return
                if not self._authorized():
                    self._send_json(401, {"error": {"code": "FAKE_AUTH_REQUIRED"}})
                    return
                if owner.mode == "disconnect":
                    self._disconnect()
                    return
                if owner.mode == "header_timeout":
                    if owner._stop.wait(owner.header_delay_s):
                        return
                if owner.mode == "redirect":
                    self.send_response(302)
                    self.send_header(
                        "Location", f"{owner.path_prefix}/redirect-target"
                    )
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if owner.mode == "http_500":
                    self._send_json(500, {"error": {"code": "FAKE_CHAT_FAILURE"}})
                    return
                rejected = {
                    "http_400": (400, "bad\u0000 request field"),
                    "http_401": (401, "backend credential rejected"),
                    "http_403": (403, "backend credential lacks permission"),
                    "http_429": (429, "backend queue is full"),
                }.get(owner.mode)
                if rejected is not None:
                    self._send_json(
                        rejected[0],
                        {"error": {"message": rejected[1]}},
                        extra_headers=(
                            {"Retry-After": "3"}
                            if owner.mode == "http_429"
                            else None
                        ),
                    )
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    body = self.rfile.read(content_length)
                    document = json.loads(body.decode("utf-8"))
                    if not isinstance(document, dict):
                        raise ValueError
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._send_json(400, {"error": {"code": "FAKE_MALFORMED"}})
                    return
                record = self._record(document)
                if owner.mode == "invalid_json":
                    payload = b"{not-json"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if document.get("stream") is True:
                    self._send_stream(str(record["request_id"]))
                    return
                self._send_json(
                    200,
                    {
                        "id": f"fake-{record['request_id']}",
                        "object": "chat.completion",
                        "model": owner.model_id,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "fake response"},
                                "finish_reason": "stop",
                            }
                        ],
                    },
                )

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="mvp-fake-backend-test",
            daemon=True,
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return
        self._stop.set()
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "FakeBackend":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
