"""Local gateway, request relay, and single-process MVP launcher."""

from http.client import HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .config import MVPConfig
from .mock_engine import MockEngine, _utc_now
from .policy import PolicyState, load_policy
from .query import QueryError, QueryService, RequestNotFoundError
from .store import JsonlStore
from .validator import Decision, validate_request


_INVALID_POLICY_BODY_LIMIT = 1024 * 1024
_REQUEST_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_STATIC_DIR = Path(__file__).with_name("static")
_STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/static/app.css": ("app.css", "text/css; charset=utf-8"),
    "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
}


def _transport_decision(status: int, code: str, message: str) -> Decision:
    return Decision(
        result="INVALID",
        http_status=status,
        reason_code=code,
        rule_id=None,
        message=message,
        model=None,
        max_tokens=None,
    )


def _engine_error(status: int, code: str, message: str, allowed: Decision) -> Decision:
    return Decision(
        result="ERROR",
        http_status=status,
        reason_code=code,
        rule_id=None,
        message=message,
        model=allowed.model,
        max_tokens=allowed.max_tokens,
    )


def _is_timeout(error: BaseException) -> bool:
    if isinstance(error, (socket.timeout, TimeoutError)):
        return True
    if isinstance(error, URLError):
        if isinstance(error.reason, BaseException):
            return _is_timeout(error.reason)
        return "timed out" in str(error.reason).lower()
    return False


class GatewayApp:
    """A startable local gateway used by both tests and the CLI."""

    def __init__(
        self,
        config: MVPConfig,
        policy_state: PolicyState,
        engine_address: tuple[str, int],
    ) -> None:
        if config.host != "127.0.0.1":
            raise ValueError("the MVP gateway must bind to 127.0.0.1")
        self.config = config
        self.policy_state = policy_state
        self.engine_address = engine_address
        # One ID per GatewayApp instance: a restart in the same Python process
        # is still a distinct observation session.
        self.observation_session_id = uuid4().hex
        self._record_store = JsonlStore(Path(config.data_dir) / "gateway_records.jsonl")
        self._engine_store = JsonlStore(Path(config.data_dir) / "engine_receipts.jsonl")
        self._record_state_lock = threading.Lock()
        self._record_store_state = "unknown"
        self._last_record_error: str | None = None
        self._last_record_error_at: str | None = None
        self._check_record_store()
        self._queries = QueryService(
            self._record_store,
            self._engine_store,
            self._record_store_status,
            self.observation_session_id,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("gateway is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def _engine_url(self, path: str) -> str:
        return f"http://{self.engine_address[0]}:{self.engine_address[1]}{path}"

    def _engine_status(self) -> dict[str, Any]:
        checked_at = _utc_now()
        url = self._engine_url("/healthz")
        try:
            with urlopen(url, timeout=self.config.relay_timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
                healthy = (
                    response.status == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                )
            return {
                "url": url,
                "healthy": healthy,
                "health": payload,
                "checked_at": checked_at,
                "error": None,
                "receipts_store": (
                    payload.get("receipts_store") if isinstance(payload, dict) else None
                ),
            }
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            return {
                "url": url,
                "healthy": False,
                "health": None,
                "checked_at": checked_at,
                "error": str(error),
                "receipts_store": {
                    "state": "unknown",
                    "ok": None,
                    "last_error": None,
                },
            }

    def _check_record_store(self) -> None:
        try:
            self._record_store.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with self._record_store.path.open("ab"):
                pass
        except OSError as error:
            with self._record_state_lock:
                self._record_store_state = "error"
                self._last_record_error = str(error)
                self._last_record_error_at = _utc_now()
            return
        with self._record_state_lock:
            self._record_store_state = "ok"

    def _record_store_status(self) -> dict[str, Any]:
        with self._record_state_lock:
            return {
                "data_dir": str(self.config.data_dir),
                "state": self._record_store_state,
                "ok": self._record_store_state == "ok",
                "last_error": self._last_record_error,
                "last_error_at": self._last_record_error_at,
            }

    def _append_record(self, record: dict[str, Any]) -> bool:
        try:
            self._record_store.append(record)
        except OSError as error:
            with self._record_state_lock:
                self._record_store_state = "error"
                self._last_record_error = str(error)
                self._last_record_error_at = _utc_now()
            return False
        with self._record_state_lock:
            self._record_store_state = "ok"
        return True

    def _record(
        self,
        phase: str,
        request_id: str,
        decision: Decision,
        *,
        attempted: bool,
        backend_receipt: str,
        response: str,
        error_code: str | None,
    ) -> dict[str, Any]:
        policy = self.policy_state.policy
        return {
            "schema_version": "runtime-mvp-record/1.0",
            "phase": phase,
            "request_id": request_id,
            "observation_session_id": self.observation_session_id,
            "target_id": policy.target_id if policy else None,
            "recorded_at": _utc_now(),
            "policy": {
                "policy_id": policy.policy_id if policy else None,
                "policy_version": policy.policy_version if policy else None,
                "sha256": self.policy_state.sha256,
                "path": self.policy_state.path,
            },
            "decision": {
                "result": decision.result,
                "reason_code": decision.reason_code,
                "rule_id": decision.rule_id,
                "message": decision.message,
                "http_status": decision.http_status,
            },
            "request": {
                "model": decision.model,
                "max_tokens": decision.max_tokens,
            },
            "delivery": {
                "attempted": attempted,
                "backend_receipt": backend_receipt,
                "response": response,
                "error_code": error_code,
            },
            "backend_kind": "mock",
            "data_origin": "actual_test",
        }

    def _relay(
        self, request_id: str, document: dict[str, Any], allowed: Decision
    ) -> tuple[Decision, bytes | None]:
        relay_document = {
            "model": allowed.model,
            "messages": document["messages"],
            "max_tokens": allowed.max_tokens,
        }
        request = Request(
            self._engine_url("/v1/chat/completions"),
            data=json.dumps(relay_document, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.config.relay_timeout_s) as engine_response:
                status = engine_response.status
                body = engine_response.read()
            if status != 200:
                return (
                    _engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"mock engine returned HTTP status {status}",
                        allowed,
                    ),
                    None,
                )
            try:
                json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return (
                    _engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        "mock engine returned invalid JSON",
                        allowed,
                    ),
                    None,
                )
            return allowed, body
        except HTTPError as error:
            error.close()
            return (
                _engine_error(
                    502,
                    "ENGINE_BAD_RESPONSE",
                    f"mock engine returned HTTP status {error.code}",
                    allowed,
                ),
                None,
            )
        except (URLError, TimeoutError, OSError, HTTPException) as error:
            if _is_timeout(error):
                return (
                    _engine_error(
                        504,
                        "ENGINE_TIMEOUT",
                        "mock engine request timed out",
                        allowed,
                    ),
                    None,
                )
            return (
                _engine_error(
                    502,
                    "ENGINE_UNREACHABLE",
                    "mock engine is unreachable",
                    allowed,
                ),
                None,
            )

    def _engine_receipt_lookup(self, request_id: str) -> dict[str, Any]:
        query = urlencode({"request_id": request_id})
        queried_at = _utc_now()
        try:
            with urlopen(
                self._engine_url(f"/_mock/receipts?{query}"),
                timeout=self.config.relay_timeout_s,
            ) as response:
                if response.status != 200:
                    return {
                        "status": "unknown",
                        "queried_at": queried_at,
                        "error": f"mock engine returned HTTP status {response.status}",
                    }
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("found"), bool):
                return {
                    "status": "unknown",
                    "queried_at": queried_at,
                    "error": "mock engine returned an invalid receipt response",
                }
            return {
                "status": "confirmed" if payload["found"] else "not_found",
                "queried_at": (
                    payload["queried_at"]
                    if isinstance(payload.get("queried_at"), str)
                    else queried_at
                ),
                "error": None,
            }
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            HTTPException,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            return {
                "status": "unknown",
                "queried_at": queried_at,
                "error": str(error),
            }

    def _backend_receipt(self, request_id: str) -> str:
        return str(self._engine_receipt_lookup(request_id)["status"])

    def status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        engine = self._engine_status()
        record_store = self._record_store_status()
        return {
            "status": (
                "ok"
                if self.policy_state.ok and engine["healthy"] and record_store["ok"]
                else "degraded"
            ),
            "mode": "mock_engine",
            "observation_session_id": self.observation_session_id,
            "gateway": {"host": self.address[0], "port": self.address[1]},
            "policy": {
                "path": self.policy_state.path,
                "valid": self.policy_state.ok,
                "schema_version": policy.schema_version if policy else None,
                "policy_id": policy.policy_id if policy else None,
                "policy_version": policy.policy_version if policy else None,
                "target_id": policy.target_id if policy else None,
                "sha256": self.policy_state.sha256,
                "loaded_at": self.policy_state.loaded_at,
                "error_code": self.policy_state.error_code,
                "error_message": self.policy_state.error_message,
            },
            "record_store": record_store,
            "mock_engine": engine,
        }

    def start(self) -> "GatewayApp":
        if self._server is not None:
            raise RuntimeError("gateway is already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "ServingRuntimeGateway/1.0"

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
            ) -> None:
                self._send(
                    status,
                    "application/json; charset=utf-8",
                    json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    request_id=request_id,
                    close_connection=close_connection,
                )

            def _send_error_decision(
                self,
                decision: Decision,
                request_id: str,
                *,
                close_connection: bool = False,
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
                )

            def _known_path(self) -> bool:
                path = urlsplit(self.path).path
                return (
                    path in _STATIC_FILES
                    or path in {"/api/status", "/api/summary", "/api/requests", "/v1/chat/completions"}
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
                    items = owner._queries.requests(result=result, limit=int(raw_limit))
                except QueryError as error:
                    self._send_query_error(400, "INVALID_QUERY", str(error))
                    return
                self._send_json(200, {"requests": items, "generated_at": _utc_now()})

            def _request_detail(self, rest: str) -> None:
                evidence = rest.endswith("/evidence")
                request_id = rest[: -len("/evidence")] if evidence else rest
                if not _REQUEST_ID_PATTERN.fullmatch(request_id):
                    self._send_query_error(400, "INVALID_REQUEST_ID", "invalid request ID")
                    return
                try:
                    detail = owner._queries.detail(
                        request_id,
                        owner._engine_receipt_lookup,
                        owner.observation_session_id,
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
                    self._send_json(200, owner.status())
                    return
                if path == "/api/summary":
                    self._send_json(200, owner._queries.summary())
                    return
                if path == "/api/requests":
                    self._requests_list(parsed.query)
                    return
                if path.startswith("/api/requests/"):
                    self._request_detail(path[len("/api/requests/"):])
                    return
                if path == "/v1/chat/completions":
                    self._send_json(405, {"error": {"code": "METHOD_NOT_ALLOWED"}})
                    return
                self._send_json(404, {"error": {"code": "NOT_FOUND"}})

            def _finish_without_relay(
                self,
                request_id: str,
                decision: Decision,
                *,
                close_connection: bool = False,
            ) -> None:
                owner._append_record(
                    owner._record(
                        "final",
                        request_id,
                        decision,
                        attempted=False,
                        backend_receipt="not_queried",
                        response="none",
                        error_code=None,
                    )
                )
                self._send_error_decision(
                    decision,
                    request_id,
                    close_connection=close_connection,
                )

            def _chat(self, request_id: str) -> None:
                if self.headers.get("Transfer-Encoding") is not None:
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(
                            400,
                            "UNSUPPORTED_TRANSFER_ENCODING",
                            "Transfer-Encoding is not supported",
                        ),
                        close_connection=True,
                    )
                    return

                raw_length = self.headers.get("Content-Length")
                if raw_length is None or not raw_length or any(
                    character not in "0123456789" for character in raw_length
                ):
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(
                            400,
                            "INVALID_CONTENT_LENGTH",
                            "Content-Length must be a non-negative integer",
                        ),
                        close_connection=True,
                    )
                    return
                try:
                    content_length = int(raw_length)
                except ValueError:
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(
                            400,
                            "INVALID_CONTENT_LENGTH",
                            "Content-Length must be a non-negative integer",
                        ),
                        close_connection=True,
                    )
                    return

                policy = owner.policy_state.policy
                body_limit = (
                    policy.max_body_bytes
                    if owner.policy_state.ok and policy is not None
                    else _INVALID_POLICY_BODY_LIMIT
                )
                if content_length > body_limit:
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(
                            413,
                            "BODY_TOO_LARGE",
                            "request body exceeds the policy limit",
                        ),
                        close_connection=True,
                    )
                    return

                body = self.rfile.read(content_length)
                incomplete = len(body) != content_length
                if incomplete:
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(
                            400,
                            "INVALID_CONTENT_LENGTH",
                            "request body is shorter than Content-Length",
                        ),
                        close_connection=True,
                    )
                    return
                decision = validate_request(
                    body, self.headers.get("Content-Type"), owner.policy_state
                )
                if decision.result != "ALLOWED":
                    self._finish_without_relay(
                        request_id,
                        decision,
                        close_connection=incomplete,
                    )
                    return

                document = json.loads(body.decode("utf-8"))
                started = owner._record(
                    "started",
                    request_id,
                    decision,
                    attempted=False,
                    backend_receipt="not_queried",
                    response="none",
                    error_code=None,
                )
                if not owner._append_record(started):
                    unavailable = _engine_error(
                        503,
                        "STORE_UNAVAILABLE",
                        "request record store is unavailable",
                        decision,
                    )
                    self._send_error_decision(unavailable, request_id)
                    return

                final_decision, engine_body = owner._relay(request_id, document, decision)
                backend_receipt = owner._backend_receipt(request_id)
                completed = final_decision.result == "ALLOWED" and engine_body is not None
                owner._append_record(
                    owner._record(
                        "final",
                        request_id,
                        final_decision,
                        attempted=True,
                        backend_receipt=backend_receipt,
                        response="completed" if completed else "failed",
                        error_code=None if completed else final_decision.reason_code,
                    )
                )
                if completed:
                    self._send(
                        200,
                        "application/json; charset=utf-8",
                        engine_body,
                        request_id=request_id,
                    )
                else:
                    self._send_error_decision(final_decision, request_id)

            def do_POST(self) -> None:
                request_id = uuid4().hex
                self._post_request_id = request_id
                if urlsplit(self.path).path == "/v1/chat/completions":
                    self._chat(request_id)
                    return
                self._unsupported_method()

            do_DELETE = _unsupported_method
            do_CONNECT = _unsupported_method
            do_HEAD = _unsupported_method
            do_OPTIONS = _unsupported_method
            do_PATCH = _unsupported_method
            do_PUT = _unsupported_method
            do_TRACE = _unsupported_method

        self._server = ThreadingHTTPServer((self.config.host, self.config.gateway_port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mvp-gateway", daemon=True
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

    def __enter__(self) -> "GatewayApp":
        return self.start()

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def run(config: MVPConfig) -> None:
    """Run the gateway and mock engine until interrupted."""

    if config.host != "127.0.0.1":
        raise ValueError("the MVP servers must bind to 127.0.0.1")
    policy_state = load_policy(config.policy_path)
    data_dir = Path(config.data_dir)
    engine = MockEngine(
        port=config.engine_port,
        receipts_path=data_dir / "engine_receipts.jsonl",
    )
    gateway: GatewayApp | None = None
    try:
        engine.start()
        gateway = GatewayApp(config, policy_state, engine.address).start()
        gateway_url = f"http://{gateway.address[0]}:{gateway.address[1]}"
        engine_url = f"http://{engine.address[0]}:{engine.address[1]}"
        print(f"Gateway: {gateway_url}", flush=True)
        print(f"Mock engine: {engine_url}", flush=True)
        print(f"Data directory: {data_dir}", flush=True)
        print(f"Browser: {gateway_url}/", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("Stopping MVP servers.", flush=True)
    finally:
        if gateway is not None:
            gateway.stop()
        engine.stop()
