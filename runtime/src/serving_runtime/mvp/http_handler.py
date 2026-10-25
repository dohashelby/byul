"""HTTP handler, route dispatch, and response helpers for the local gateway."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, ClassVar
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from serving_runtime.enforcement.events import ObservationQuery

from .mock_engine import _utc_now
from .query import QueryError, QueryService, RequestNotFoundError
from .request_flow import ChatIO, transport_decision
from .validator import Decision


_REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STATIC_DIR = Path(__file__).with_name("static")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
    # Vendored Chart.js 4.5.1 (MIT); see docs/외부기능_활용목록.md for origin and hash.
    "/static/vendor/chart.umd.min.js": ("vendor/chart.umd.min.js", "text/javascript; charset=utf-8"),
}


class _DeadlineReader:
    """Socket reader whose timeout is an absolute monotonic deadline."""

    def __init__(self, connection: Any, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._connection = connection
        self._clock = clock
        self._deadline: float | None = None
        self._buffer = bytearray()
        self.closed = False

    def set_deadline(self, timeout_s: float) -> None:
        self._deadline = self._clock() + timeout_s

    def clear_deadline(self) -> None:
        self._deadline = None

    def _receive(self) -> bytes:
        if self._deadline is None:
            raise RuntimeError("request read deadline is not configured")
        remaining = self._deadline - self._clock()
        if remaining <= 0:
            raise TimeoutError("request read deadline exceeded")
        self._connection.settimeout(remaining)
        return self._connection.recv(64 * 1024)

    def readline(self, limit: int = -1) -> bytes:
        if limit == 0:
            return b""
        while True:
            search_limit = len(self._buffer) if limit < 0 else min(len(self._buffer), limit)
            newline = self._buffer.find(b"\n", 0, search_limit)
            if newline >= 0:
                end = newline + 1
                result = bytes(self._buffer[:end])
                del self._buffer[:end]
                return result
            if limit >= 0 and len(self._buffer) >= limit:
                result = bytes(self._buffer[:limit])
                del self._buffer[:limit]
                return result
            chunk = self._receive()
            if not chunk:
                result = bytes(self._buffer)
                self._buffer.clear()
                return result
            self._buffer.extend(chunk)

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        if size < 0:
            while True:
                chunk = self._receive()
                if not chunk:
                    result = bytes(self._buffer)
                    self._buffer.clear()
                    return result
                self._buffer.extend(chunk)
        while len(self._buffer) < size:
            chunk = self._receive()
            if not chunk:
                break
            self._buffer.extend(chunk)
        result = bytes(self._buffer[:size])
        del self._buffer[:size]
        return result

    def close(self) -> None:
        self.closed = True
        self._buffer.clear()


@dataclass(frozen=True)
class HandlerDependencies:
    chat: Callable[[ChatIO, str], None]
    status: Callable[[], dict[str, Any]]
    policy_status: Callable[[], dict[str, Any]]
    baseline_status: Callable[[], dict[str, Any]]
    collectors_status: Callable[[], dict[str, Any]]
    revalidation_status: Callable[[], dict[str, Any]]
    queries: QueryService
    observations: ObservationQuery
    receipt_lookup: Callable[[str], dict[str, Any]]
    observation_session_id: str
    lab_report: Callable[[], dict[str, Any]]
    record_transport_error: Callable[[str, Decision], None]
    request_header_timeout_s: float
    request_body_timeout_s: float
    target_status: Callable[[], dict[str, Any]] = lambda: {"registration": {"state": "not_configured"}}
    authorize_read: Callable[[str | None], tuple[int, str, str] | None] = lambda _header: None


class Handler(BaseHTTPRequestHandler):
    """Serve the dashboard, query APIs, and chat completion endpoint."""

    dependencies: ClassVar[HandlerDependencies]
    server_version = "ServingRuntimeGateway/1.0"
    protocol_version = "HTTP/1.0"

    def setup(self) -> None:
        super().setup()
        original_reader = self.rfile
        self.rfile = _DeadlineReader(self.connection)
        original_reader.close()
        self.connection.settimeout(self.dependencies.request_header_timeout_s)

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(
        self,
        status: int,
        content_type: str,
        body: bytes,
        *,
        request_id: str | None = None,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        if close_connection:
            self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if request_id is not None:
            self.send_header("X-Request-ID", request_id)
        if close_connection:
            self.send_header("Connection", "close")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_read_refusal(self, status: int, code: str, message: str) -> None:
        body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Bearer realm="serving-runtime"')
        self.end_headers()
        self.wfile.write(body)

    def _send_json(
        self,
        status: int,
        payload: object,
        *,
        request_id: str | None = None,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send(
            status,
            "application/json; charset=utf-8",
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            request_id=request_id,
            close_connection=close_connection,
            extra_headers=extra_headers,
        )

    def _send_error_decision(
        self,
        decision: Decision,
        request_id: str,
        *,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_json(
            decision.http_status,
            {
                "error": {
                    "code": decision.reason_code,
                    "message": decision.message,
                    "result": decision.result,
                    "source": decision.source,
                    "request_id": request_id,
                }
            },
            request_id=request_id,
            close_connection=close_connection,
            extra_headers=extra_headers,
        )

    def request_header(self, name: str) -> str | None:
        if name.casefold() == "authorization":
            values = self.headers.get_all(name, [])
            if len(values) > 1:
                # Avoid accepting a credential according to one of several
                # incompatible duplicate-header joining rules.
                return "duplicate-authorization-header"
        return self.headers.get(name)

    def read_request_body(self, length: int) -> bytes:
        self._reading_request_body = True
        self.rfile.set_deadline(self.dependencies.request_body_timeout_s)
        try:
            return self.rfile.read(length)
        except TimeoutError:
            self._body_read_timed_out = True
            raise
        except (ConnectionResetError, OSError):
            # Treat a reset during upload as an incomplete body. The request
            # flow records INVALID_CONTENT_LENGTH and never relays it.
            return b""
        finally:
            self._reading_request_body = False
            self.rfile.clear_deadline()
            try:
                self.connection.settimeout(self.dependencies.request_header_timeout_s)
            except OSError:
                pass

    def handle_one_request(self) -> None:
        """Bound header/body reads and persist timeout outcomes."""

        self._transport_request_id = uuid4().hex
        self._request_parsed = False
        self._body_read_timed_out = False
        self.requestline = ""
        self.request_version = "HTTP/1.0"
        self.command = ""
        self.rfile.set_deadline(self.dependencies.request_header_timeout_s)
        try:
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ""
                self.request_version = ""
                self.command = ""
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                return
            self._request_parsed = True
            self.rfile.clear_deadline()
            self.connection.settimeout(self.dependencies.request_header_timeout_s)
            method_name = "do_" + self.command
            if not hasattr(self, method_name):
                self.send_error(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "Unsupported method (%r)" % self.command,
                )
                return
            getattr(self, method_name)()
            self.wfile.flush()
        except TimeoutError:
            if self._body_read_timed_out:
                code = "REQUEST_BODY_TIMEOUT"
                message = "request body read timed out"
            elif not self._request_parsed:
                code = "REQUEST_HEADER_TIMEOUT"
                message = "request header read timed out"
            else:
                code = "CONNECTION_IO_TIMEOUT"
                message = "connection I/O timed out"
            request_id = getattr(self, "_post_request_id", self._transport_request_id)
            decision = transport_decision(408, code, message)
            self.dependencies.record_transport_error(request_id, decision)
            self.close_connection = True
            try:
                self.rfile.clear_deadline()
                self.connection.settimeout(self.dependencies.request_header_timeout_s)
                self._send_error_decision(
                    decision, request_id, close_connection=True
                )
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    def send_error_decision(
        self,
        decision: Decision,
        request_id: str,
        *,
        close_connection: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._send_error_decision(
            decision,
            request_id,
            close_connection=close_connection,
            extra_headers=extra_headers,
        )

    def send_response_body(
        self,
        status: int,
        content_type: str,
        body: bytes,
        *,
        request_id: str | None = None,
    ) -> None:
        self._send(status, content_type, body, request_id=request_id)

    def start_stream_response(self, request_id: str) -> None:
        self.close_connection = True
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Request-ID", request_id)
        self.end_headers()
        self.wfile.flush()

    def write_stream_chunk(self, chunk: bytes) -> None:
        self.wfile.write(chunk)
        self.wfile.flush()

    def _known_path(self) -> bool:
        path = urlsplit(self.path).path
        return (
            path in _STATIC_FILES
            or path
            in {
                "/api/status",
                "/api/summary",
                "/api/requests",
                "/api/policy",
                "/api/baseline",
                "/api/collectors",
                "/api/revalidation",
                "/api/observations",
                "/api/lab-report",
                "/api/target",
                "/v1/chat/completions",
            }
            or path.startswith("/api/requests/")
        )

    def _unsupported_method(self) -> None:
        request_id = getattr(self, "_post_request_id", None)
        close_connection = request_id is not None
        if self._known_path():
            self._send_json(
                405,
                {"error": {"code": "METHOD_NOT_ALLOWED"}},
                request_id=request_id,
                close_connection=close_connection,
            )
        else:
            self._send_json(
                404,
                {"error": {"code": "NOT_FOUND"}},
                request_id=request_id,
                close_connection=close_connection,
            )

    def _send_static(self, path: str) -> None:
        name, content_type = _STATIC_FILES[path]
        try:
            body = (_STATIC_DIR / name).read_bytes()
        except OSError:
            self._send_json(500, {"error": {"code": "STATIC_UNAVAILABLE"}})
            return
        self._send(200, content_type, body)

    def _send_query_error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _requests_list(self, query: str) -> None:
        params = parse_qs(query)
        result = params.get("result", [None])[0] or None
        raw_limit = params.get("limit", ["200"])[0]
        if not raw_limit.isdigit():
            self._send_query_error(400, "INVALID_QUERY", "limit must be an integer")
            return
        try:
            items = self.dependencies.queries.requests(
                result=result, limit=int(raw_limit)
            )
        except QueryError as error:
            self._send_query_error(400, "INVALID_QUERY", str(error))
            return
        self._send_json(200, {"requests": items, "generated_at": _utc_now()})

    def _observation_list(self, query: str) -> None:
        params = parse_qs(query, keep_blank_values=True)
        if any(len(values) != 1 for values in params.values()):
            self._send_query_error(
                400, "INVALID_QUERY", "observation filters must not repeat"
            )
            return
        filters = {name: values[0] for name, values in params.items()}
        if any(not value for value in filters.values()):
            self._send_query_error(
                400, "INVALID_QUERY", "observation filters must not be empty"
            )
            return
        try:
            payload = self.dependencies.observations.query(filters)
        except ValueError as error:
            self._send_query_error(400, "INVALID_QUERY", str(error))
            return
        self._send_json(200, payload)

    def _request_detail(self, rest: str) -> None:
        evidence = rest.endswith("/evidence")
        request_id = rest[: -len("/evidence")] if evidence else rest
        if not _REQUEST_ID_PATTERN.fullmatch(request_id):
            self._send_query_error(400, "INVALID_REQUEST_ID", "invalid request ID")
            return
        try:
            detail = self.dependencies.queries.detail(
                request_id,
                self.dependencies.receipt_lookup,
                self.dependencies.observation_session_id,
            )
        except RequestNotFoundError:
            self._send_query_error(404, "REQUEST_NOT_FOUND", "request ID not found")
            return
        if not evidence:
            self._send_json(200, detail)
            return
        body = json.dumps(detail, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header(
            "Content-Disposition", f'attachment; filename="evidence-{request_id}.json"'
        )
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path
        if path in _STATIC_FILES:
            self._send_static(path)
            return
        if path.startswith("/api/"):
            refusal = self.dependencies.authorize_read(self.request_header("Authorization"))
            if refusal is not None:
                status, code, message = refusal
                self._send_read_refusal(status, code, message)
                return
        if path == "/api/status":
            self._send_json(200, self.dependencies.status())
            return
        if path == "/api/policy":
            self._send_json(200, self.dependencies.policy_status())
            return
        if path == "/api/baseline":
            self._send_json(200, self.dependencies.baseline_status())
            return
        if path == "/api/collectors":
            self._send_json(200, self.dependencies.collectors_status())
            return
        if path == "/api/revalidation":
            self._send_json(200, self.dependencies.revalidation_status())
            return
        if path == "/api/observations":
            self._observation_list(parsed.query)
            return
        if path == "/api/lab-report":
            self._send_json(200, self.dependencies.lab_report())
            return
        if path == "/api/target":
            self._send_json(200, self.dependencies.target_status())
            return
        if path == "/api/summary":
            self._send_json(200, self.dependencies.queries.summary())
            return
        if path == "/api/requests":
            self._requests_list(parsed.query)
            return
        if path.startswith("/api/requests/"):
            self._request_detail(path[len("/api/requests/") :])
            return
        if path == "/v1/chat/completions":
            self._send_json(405, {"error": {"code": "METHOD_NOT_ALLOWED"}})
            return
        self._send_json(404, {"error": {"code": "NOT_FOUND"}})

    def do_POST(self) -> None:
        request_id = getattr(self, "_transport_request_id", uuid4().hex)
        self._post_request_id = request_id
        if urlsplit(self.path).path == "/v1/chat/completions":
            self.dependencies.chat(self, request_id)
            return
        self._unsupported_method()

    do_DELETE = _unsupported_method
    do_CONNECT = _unsupported_method
    do_HEAD = _unsupported_method
    do_OPTIONS = _unsupported_method
    do_PATCH = _unsupported_method
    do_PUT = _unsupported_method
    do_TRACE = _unsupported_method


def create_handler(
    dependencies: HandlerDependencies,
) -> type[Handler]:
    """Bind one app's dependencies without sharing mutable handler state."""

    class ConfiguredHandler(Handler):
        pass

    ConfiguredHandler.dependencies = dependencies
    return ConfiguredHandler


class LimitedThreadingHTTPServer(ThreadingHTTPServer):
    """Thread-per-connection server with a non-blocking hard connection cap."""

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        *,
        max_connections: int,
        on_connection_rejected: Callable[[str, Decision], None],
    ) -> None:
        self._connection_slots = threading.BoundedSemaphore(max_connections)
        self._connection_lock = threading.Lock()
        self._active_connections = 0
        self._rejected_connections = 0
        self._max_connections = max_connections
        self._on_connection_rejected = on_connection_rejected
        super().__init__(server_address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        if not self._connection_slots.acquire(blocking=False):
            request_id = uuid4().hex
            decision = Decision(
                result="ERROR",
                http_status=503,
                reason_code="CONNECTION_LIMIT",
                rule_id="max_connections",
                message="maximum client connections reached",
                model=None,
                max_tokens=None,
            )
            with self._connection_lock:
                self._rejected_connections += 1
            body = json.dumps(
                {
                    "error": {
                        "code": decision.reason_code,
                        "message": decision.message,
                        "result": decision.result,
                        "source": decision.source,
                        "request_id": request_id,
                    }
                }
            ).encode("utf-8")
            response = (
                b"HTTP/1.0 503 Service Unavailable\r\n"
                b"Content-Type: application/json; charset=utf-8\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + f"X-Request-ID: {request_id}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            try:
                request.settimeout(0.5)
                request.sendall(response)
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            self._on_connection_rejected(request_id, decision)
            return
        with self._connection_lock:
            self._active_connections += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._connection_lock:
                self._active_connections -= 1
            self._connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._connection_lock:
                self._active_connections -= 1
            self._connection_slots.release()

    def connection_status(self) -> dict[str, int]:
        with self._connection_lock:
            return {
                "active": self._active_connections,
                "limit": self._max_connections,
                "rejected": self._rejected_connections,
            }
