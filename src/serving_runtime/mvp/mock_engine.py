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


class MockEngine:
    """A local-only mock engine with a thread-safe receipt journal."""

    def __init__(
        self,
        port: int = 8088,
        response_delay_s: float = 0,
        receipts_path: str | Path | None = None,
    ) -> None:
        if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
            raise ValueError("port must be an integer from 0 through 65535")
        if response_delay_s < 0:
            raise ValueError("response_delay_s must not be negative")
        self.port = port
        self.response_delay_s = response_delay_s
        self._receipts: list[dict[str, Any]] = []
        self._receipts_lock = threading.Lock()
        self._receipts_store = JsonlStore(receipts_path) if receipts_path is not None else None
        self._receipts_store_error: str | None = None
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

    def start(self) -> "MockEngine":
        if self._server is not None:
            raise RuntimeError("mock engine is already started")
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

            def _known_path(self) -> bool:
                return urlsplit(self.path).path in {
                    "/healthz",
                    "/_mock/receipts",
                    "/v1/chat/completions",
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
                    self._send_json(200, {"status": "ok", "receipts_store": store})
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

            def do_POST(self) -> None:
                parsed = urlsplit(self.path)
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
                }
                with owner._receipts_lock:
                    owner._receipts.append(receipt)
                if owner._receipts_store is not None:
                    try:
                        owner._receipts_store.append(receipt)
                    except OSError as error:
                        with owner._receipts_lock:
                            owner._receipts_store_error = str(error)
                if owner.response_delay_s:
                    time.sleep(owner.response_delay_s)
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
