"""Threaded local mock of the OpenAI chat-completions endpoint."""

from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .store import JsonlStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def simulated_token_count(messages: object) -> int:
    """Deterministic stand-in for a tokenizer; NOT a real token count.

    Counts whitespace-separated words plus a fixed overhead per message so the
    Gateway's context check can be exercised without a real model tokenizer.
    """

    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    total = 2
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise ValueError("message content must be a string")
        total += len(content.split()) + 4
    return total


class MockEngine:
    """A local-only mock engine with a thread-safe receipt journal."""

    def __init__(
        self,
        port: int = 8088,
        response_delay_s: float = 0,
        receipts_path: str | Path | None = None,
        *,
        stream_events: int = 3,
        stream_interval_s: float = 0.05,
        header_delay_s: float = 0,
        stall_after_events: int | None = None,
        truncate_after_events: int | None = None,
        split_writes: bool = False,
    ) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer from 0 through 65535")
        if response_delay_s < 0:
            raise ValueError("response_delay_s must not be negative")
        if not isinstance(stream_events, int) or isinstance(stream_events, bool) or stream_events <= 0:
            raise ValueError("stream_events must be a positive integer")
        if stream_interval_s < 0:
            raise ValueError("stream_interval_s must not be negative")
        if header_delay_s < 0:
            raise ValueError("header_delay_s must not be negative")
        for name, value in (
            ("stall_after_events", stall_after_events),
            ("truncate_after_events", truncate_after_events),
        ):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        self.port = port
        self.response_delay_s = response_delay_s
        self.stream_events = stream_events
        self.stream_interval_s = stream_interval_s
        self.header_delay_s = header_delay_s
        self.stall_after_events = stall_after_events
        self.truncate_after_events = truncate_after_events
        self.split_writes = split_writes
        self._receipts: list[dict[str, Any]] = []
        self._receipts_lock = threading.Lock()
        self.tokenize_calls = 0
        self._stream_transmissions: dict[str, bytearray] = {}
        self._receipts_store = JsonlStore(receipts_path) if receipts_path is not None else None
        self._receipts_store_error: str | None = None
        self._stop_event = threading.Event()
        self._stall_release = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("mock engine is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def receipts(self) -> list[dict[str, Any]]:
        with self._receipts_lock:
            return [dict(receipt) for receipt in self._receipts]

    @property
    def receipts_store_error(self) -> str | None:
        with self._receipts_lock:
            return self._receipts_store_error

    @property
    def stream_transmissions(self) -> dict[str, bytes]:
        """Return ephemeral bytes written by each stream; these are never persisted."""

        with self._receipts_lock:
            return {
                request_id: bytes(payload)
                for request_id, payload in self._stream_transmissions.items()
            }

    def release_stalled_streams(self) -> None:
        """Release every test stream waiting at ``stall_after_events``."""

        self._stall_release.set()

    def start(self) -> "MockEngine":
        if self._server is not None:
            raise RuntimeError("mock engine is already started")
        self._stop_event.clear()
        self._stall_release.clear()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ServingRuntimeMock/1.0"

            def log_message(self, format: str, *args: object) -> None:
                return

            def _send_json(self, status: int, payload: object) -> None:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    return

            def _persist_receipt(self, receipt: dict[str, Any]) -> None:
                if owner._receipts_store is None:
                    return
                try:
                    owner._receipts_store.append(dict(receipt))
                except OSError as error:
                    with owner._receipts_lock:
                        owner._receipts_store_error = str(error)

            def _finish_stream(
                self, receipt: dict[str, Any], stream_end: str
            ) -> None:
                with owner._receipts_lock:
                    receipt["stream_end"] = stream_end
                    receipt["evidence_kind"] = "mock"
                    snapshot = dict(receipt)
                self._persist_receipt(snapshot)

            def _stream_parts(self, event: bytes) -> list[bytes]:
                if not owner.split_writes or len(event) < 4:
                    return [event]
                unicode_start = event.find("한".encode("utf-8"))
                cuts = {1, len(event) // 2, len(event) - 1}
                if unicode_start >= 0:
                    cuts.add(unicode_start + 1)
                    cuts.add(unicode_start + 2)
                ordered = [cut for cut in sorted(cuts) if 0 < cut < len(event)]
                parts: list[bytes] = []
                start = 0
                for cut in ordered:
                    parts.append(event[start:cut])
                    start = cut
                parts.append(event[start:])
                return parts

            def _write_stream_event(self, request_id: str, event: bytes) -> bool:
                try:
                    for part in self._stream_parts(event):
                        written = self.wfile.write(part)
                        self.wfile.flush()
                        with owner._receipts_lock:
                            owner._stream_transmissions[request_id].extend(
                                part if written is None else part[:written]
                            )
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False

            def _wait_for_stall_release(self) -> None:
                while not owner._stall_release.wait(0.05):
                    if owner._stop_event.is_set():
                        return

            def _send_stream(
                self,
                request_id: str,
                model: object,
                receipt: dict[str, Any],
            ) -> None:
                if owner.response_delay_s and owner._stop_event.wait(owner.response_delay_s):
                    self._finish_stream(receipt, "truncated")
                    return
                if owner.header_delay_s and owner._stop_event.wait(owner.header_delay_s):
                    self._finish_stream(receipt, "truncated")
                    return
                self.close_connection = True
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    self._finish_stream(receipt, "client_disconnected")
                    return

                with owner._receipts_lock:
                    owner._stream_transmissions[request_id] = bytearray()

                sent = 0
                if owner.stall_after_events == 0:
                    self._wait_for_stall_release()
                if owner.truncate_after_events == 0:
                    self._finish_stream(receipt, "truncated")
                    return
                for index in range(owner.stream_events):
                    content = "한글✓" if owner.split_writes and index == 0 else f"mock-{index + 1}"
                    chunk = {
                        "id": f"mock-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": content},
                                "finish_reason": None,
                            }
                        ],
                    }
                    event = (
                        "data: " + json.dumps(chunk, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    ).encode("utf-8")
                    if not self._write_stream_event(request_id, event):
                        self._finish_stream(receipt, "client_disconnected")
                        return
                    sent += 1
                    if owner.stall_after_events == sent:
                        self._wait_for_stall_release()
                    if owner.truncate_after_events == sent:
                        self._finish_stream(receipt, "truncated")
                        return
                    if owner.stream_interval_s:
                        if owner._stop_event.wait(owner.stream_interval_s):
                            self._finish_stream(receipt, "truncated")
                            return
                if not self._write_stream_event(request_id, b"data: [DONE]\n\n"):
                    self._finish_stream(receipt, "client_disconnected")
                    return
                self._finish_stream(receipt, "completed")

            def _known_path(self) -> bool:
                return urlsplit(self.path).path in {
                    "/healthz",
                    "/version",
                    "/v1/models",
                    "/_mock/receipts",
                    "/v1/chat/completions",
                    "/tokenize",
                }

            def _unsupported_method(self) -> None:
                if self._known_path():
                    self._send_json(405, {"error": {"code": "METHOD_NOT_ALLOWED"}})
                else:
                    self._send_json(404, {"error": {"code": "NOT_FOUND"}})

            def do_GET(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/healthz":
                    with owner._receipts_lock:
                        store_error = owner._receipts_store_error
                    if owner._receipts_store is None:
                        store = {"state": "disabled", "ok": None, "last_error": None}
                    else:
                        store = {
                            "state": "error" if store_error else "ok",
                            "ok": store_error is None,
                            "last_error": store_error,
                        }
                    self._send_json(
                        200,
                        {
                            "status": "ok",
                            "receipts_store": store,
                            "data_origin": "mock",
                        },
                    )
                    return
                if parsed.path == "/version":
                    self._send_json(
                        200,
                        {
                            "engine.kind": "mock",
                            "engine.version": "mock-1.0",
                            "data_origin": "mock",
                        },
                    )
                    return
                if parsed.path == "/v1/models":
                    self._send_json(
                        200,
                        {
                            "object": "list",
                            "data": [{"id": "test-model", "max_model_len": 2048}],
                            "data_origin": "mock",
                        },
                    )
                    return
                if parsed.path == "/_mock/receipts":
                    request_ids = parse_qs(parsed.query, keep_blank_values=True).get(
                        "request_id", [""]
                    )
                    request_id = request_ids[0]
                    with owner._receipts_lock:
                        matches = [
                            dict(receipt)
                            for receipt in owner._receipts
                            if receipt["request_id"] == request_id
                        ]
                    self._send_json(
                        200,
                        {
                            "request_id": request_id,
                            "found": bool(matches),
                            "receipts": matches,
                            "queried_at": _utc_now(),
                        },
                    )
                    return
                if parsed.path == "/v1/chat/completions":
                    self._send_json(405, {"error": {"code": "METHOD_NOT_ALLOWED"}})
                    return
                self._send_json(404, {"error": {"code": "NOT_FOUND"}})

            def _tokenize(self) -> None:
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    document = json.loads(self.rfile.read(content_length).decode("utf-8"))
                    count = simulated_token_count(
                        document.get("messages") if isinstance(document, dict) else None
                    )
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._send_json(400, {"error": {"code": "MALFORMED_REQUEST"}})
                    return
                with owner._receipts_lock:
                    owner.tokenize_calls += 1
                self._send_json(200, {"count": count, "data_origin": "mock"})

            def do_POST(self) -> None:
                parsed = urlsplit(self.path)
                if parsed.path == "/tokenize":
                    self._tokenize()
                    return
                if parsed.path != "/v1/chat/completions":
                    self._unsupported_method()
                    return
                request_id = self.headers.get("X-Request-ID", "").strip()
                if not request_id:
                    self._send_json(
                        400, {"error": {"code": "MISSING_REQUEST_ID", "message": "X-Request-ID is required"}}
                    )
                    return
                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                    if content_length < 0:
                        raise ValueError
                    body = self.rfile.read(content_length)
                    document = json.loads(body.decode("utf-8"))
                    if not isinstance(document, dict):
                        raise ValueError
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                    self._send_json(400, {"error": {"code": "MALFORMED_REQUEST"}})
                    return
                model = document.get("model")
                max_tokens = document.get("max_tokens")
                receipt = {
                    "request_id": request_id,
                    "received_at": _utc_now(),
                    "model": model,
                    "max_tokens": max_tokens,
                    "stream": document.get("stream") is True,
                }
                with owner._receipts_lock:
                    owner._receipts.append(receipt)
                if document.get("stream") is True:
                    self._send_stream(request_id, model, receipt)
                    return
                self._persist_receipt(receipt)
                if owner.response_delay_s and owner._stop_event.wait(owner.response_delay_s):
                    return
                if owner.header_delay_s and owner._stop_event.wait(owner.header_delay_s):
                    return
                self._send_json(
                    200,
                    {
                        "id": f"mock-{request_id}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "mock response"},
                                "finish_reason": "stop",
                            }
                        ],
                    },
                )

            do_DELETE = _unsupported_method
            do_CONNECT = _unsupported_method
            do_HEAD = _unsupported_method
            do_OPTIONS = _unsupported_method
            do_PATCH = _unsupported_method
            do_PUT = _unsupported_method
            do_TRACE = _unsupported_method

        self._server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mvp-mock-engine", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            return
        self._stop_event.set()
        self._stall_release.set()
        server.shutdown()
        server.server_close()
        if thread is not None:
            thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "MockEngine":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
