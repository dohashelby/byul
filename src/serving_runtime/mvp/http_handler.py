"""HTTP handler, route dispatch, and response helpers for the local gateway."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
import json
from pathlib import Path
import re
from typing import Any, Callable, ClassVar
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from serving_runtime.enforcement.events import ObservationQuery

from .mock_engine import _utc_now
from .query import QueryError, QueryService, RequestNotFoundError
from .request_flow import ChatIO
from .validator import Decision


_REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STATIC_DIR = Path(__file__).with_name("static")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


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


class Handler(BaseHTTPRequestHandler):
    """Serve the dashboard, query APIs, and chat completion endpoint."""

    dependencies: ClassVar[HandlerDependencies]
    server_version = "ServingRuntimeGateway/1.0"
    protocol_version = "HTTP/1.0"

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
                    "request_id": request_id,
                }
            },
            request_id=request_id,
            close_connection=close_connection,
            extra_headers=extra_headers,
        )

    def request_header(self, name: str) -> str | None:
        return self.headers.get(name)

    def read_request_body(self, length: int) -> bytes:
        return self.rfile.read(length)

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
        request_id = uuid4().hex
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
