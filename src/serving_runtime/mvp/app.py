"""Local gateway, request relay, and single-process MVP launcher."""

from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import socket
import threading
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from .backend import BackendConfig, load_backend_config, parse_prometheus_metrics
from .collectors import (
    Collector,
    CollectorManager,
    MODEL_ID_LIMIT_NOTE,
    compare_baseline,
    load_declaration,
)
from .config import MVPConfig
from .mock_engine import MockEngine, _utc_now
from .policy import PolicyState, load_policy
from .policy_state import PolicyHistory, evaluate_policy, load_revocations
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
        engine_address: tuple[str, int] | None = None,
        backend_config: BackendConfig | None = None,
    ) -> None:
        if config.host != "127.0.0.1":
            raise ValueError("the MVP gateway must bind to 127.0.0.1")
        self.config = config
        self.policy_state = policy_state
        if config.backend == "mock":
            if backend_config is not None:
                raise ValueError("backend_config must not be supplied in mock mode")
            if engine_address is None:
                raise ValueError("engine_address is required in mock mode")
            self.backend = BackendConfig.mock(engine_address)
        else:
            self.backend = backend_config or load_backend_config(
                str(config.backend_config_path)
            )
            if self.backend.kind != "vllm":
                raise ValueError("vllm mode requires a vllm backend config")
        self.engine_address = (self.backend.target.host, self.backend.target.port)
        self.relay_timeout_s = self.backend.timeouts.get(
            "relay_timeout_s", config.relay_timeout_s
        )
        self.stream_idle_timeout_s = self.backend.timeouts.get(
            "stream_idle_timeout_s", config.stream_idle_timeout_s
        )
        self.request_deadline_s = self.backend.timeouts.get(
            "request_deadline_s", config.request_deadline_s
        )
        # One ID per GatewayApp instance: a restart in the same Python process
        # is still a distinct observation session.
        self.observation_session_id = uuid4().hex
        self.revocation_state = load_revocations(config.revocations_path)
        self._baseline = load_declaration(config.baseline_path, kind="baseline")
        self._manifest = load_declaration(config.manifest_path, kind="manifest")
        self._policy_history = PolicyHistory(Path(config.data_dir) / "policy_history.jsonl")
        self._policy_history.append_startup(policy_state, self.observation_session_id)
        self._record_store = JsonlStore(Path(config.data_dir) / "gateway_records.jsonl")
        self._engine_store = JsonlStore(Path(config.data_dir) / "engine_receipts.jsonl")
        self._record_state_lock = threading.Lock()
        self._record_store_state = "unknown"
        self._last_record_error: str | None = None
        self._last_record_error_at: str | None = None
        self._check_record_store()
        self._in_flight_lock = threading.Lock()
        self._in_flight = 0
        self._queries = QueryService(
            self._record_store,
            self._engine_store,
            self._record_store_status,
            self.observation_session_id,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._collectors = CollectorManager(
            [
                Collector(
                    "engine_health",
                    self._collect_engine_health,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
                Collector(
                    "engine_info",
                    self._collect_engine_info,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
                Collector(
                    "metrics",
                    self._collect_metrics if self.backend.metrics_target is not None else None,
                    config.collector_interval_s,
                    config.collector_stale_after_s,
                ),
            ]
        )

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None:
            raise RuntimeError("gateway is not started")
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    def _engine_url(self, path: str) -> str:
        return self.backend.target.url(path)

    @property
    def backend_kind(self) -> str:
        return self.backend.kind

    def _backend_label(self) -> str:
        return "mock engine" if self.backend_kind == "mock" else "backend"

    def _request_backend(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        request_id: str | None = None,
        content_type: str | None = None,
        metrics: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        if metrics:
            connection, request_path = self.backend.metrics_connection(
                self.relay_timeout_s
            )
        else:
            connection, request_path = self.backend.connection(
                path, self.relay_timeout_s
            )
        headers = self.backend.request_headers(
            request_id=request_id, content_type=content_type
        )
        try:
            connection.request(method, request_path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def _fetch_engine_json(self, path: str) -> dict[str, Any]:
        status, _, body = self._request_backend("GET", path)
        if status != 200:
            raise OSError(f"backend returned HTTP status {status}")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("backend response must be a JSON object")
        return payload

    def _collect_engine_health(self) -> tuple[dict[str, Any], str | None]:
        if self.backend_kind == "mock":
            payload = self._fetch_engine_json(self.backend.health_path)
            if payload.get("status") != "ok":
                raise ValueError("engine health response is not ok")
            return payload, payload.get("data_origin")
        status, _, _ = self._request_backend("GET", self.backend.health_path)
        if status != 200:
            raise OSError(f"backend returned HTTP status {status}")
        return (
            {
                "reachable": True,
                "http_status": status,
                "inference_verified": False,
                "meaning": "health reachability does not prove successful inference",
            },
            "external_backend",
        )

    def _collect_engine_info(self) -> tuple[dict[str, Any], str | None]:
        version = self._fetch_engine_json(self.backend.version_path)
        models = self._fetch_engine_json(self.backend.models_path)
        entries = models.get("data")
        model_id = None
        max_model_len = None
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            model_id = entries[0].get("id")
            max_model_len = entries[0].get("max_model_len")
        if self.backend_kind == "mock":
            value = {
                "engine.kind": version.get("engine.kind"),
                "engine.version": version.get("engine.version"),
                "model.id": model_id,
                "option.max_model_len": max_model_len,
            }
            value = {key: item for key, item in value.items() if item is not None}
            origins = {version.get("data_origin"), models.get("data_origin")}
            origins.discard(None)
            data_origin = origins.pop() if len(origins) == 1 else ("mixed" if origins else None)
            return value, data_origin

        available_ids = sorted(
            {
                item.get("id")
                for item in entries
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        ) if isinstance(entries, list) else []
        mapped_ids = list(dict.fromkeys(self.backend.model_map.values()))
        mapped_availability = {
            policy_model: {
                "id": backend_model,
                "available": backend_model in available_ids,
            }
            for policy_model, backend_model in self.backend.model_map.items()
        }
        value = {
            "engine.kind": "vllm",
            "engine.version": version.get("version", version.get("engine.version")),
            "model.ids": available_ids,
            "model.map": mapped_availability,
        }
        if len(mapped_ids) == 1 and mapped_ids[0] in available_ids:
            value["model.id"] = mapped_ids[0]
        value = {key: item for key, item in value.items() if item is not None}
        return value, "external_backend"

    def _collect_metrics(self) -> tuple[dict[str, Any], str | None]:
        status, _, body = self._request_backend("GET", "", metrics=True)
        if status != 200:
            raise OSError(f"metrics endpoint returned HTTP status {status}")
        return parse_prometheus_metrics(body, self.backend.metrics_map), "external_backend"

    def _engine_status(self) -> dict[str, Any]:
        checked_at = _utc_now()
        url = self._engine_url(self.backend.health_path)
        try:
            status, _, body = self._request_backend("GET", self.backend.health_path)
            if self.backend_kind == "mock":
                payload = json.loads(body.decode("utf-8"))
                healthy = (
                    status == 200
                    and isinstance(payload, dict)
                    and payload.get("status") == "ok"
                )
                receipts_store = (
                    payload.get("receipts_store") if isinstance(payload, dict) else None
                )
            else:
                payload = None
                healthy = status == 200
                receipts_store = {
                    "state": "not_available",
                    "ok": None,
                    "last_error": None,
                }
            return {
                "url": url,
                "healthy": healthy,
                "reachable": healthy,
                "inference_verified": False,
                "health": payload,
                "checked_at": checked_at,
                "error": None,
                "receipts_store": receipts_store,
                "credential": self.backend.credential_status,
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
                "reachable": False,
                "inference_verified": False,
                "health": None,
                "checked_at": checked_at,
                "error": f"{type(error).__name__}: backend health check failed",
                "receipts_store": {
                    "state": "unknown" if self.backend_kind == "mock" else "not_available",
                    "ok": None,
                    "last_error": None,
                },
                "credential": self.backend.credential_status,
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
        stream: bool = False,
        http_start_status: int | None = None,
        final_outcome: str | None = None,
        response_started: bool = False,
        backend_cancel: str | None = None,
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
                "schema_version": policy.schema_version if policy else None,
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
            "stream": stream,
            "http_start_status": http_start_status,
            "final_outcome": final_outcome,
            "response_started": response_started,
            "backend_cancel": (
                backend_cancel
                if backend_cancel is not None
                else ("not_applicable" if self.backend_kind == "mock" else "not_observed")
            ),
            "backend_kind": self.backend_kind,
            "data_origin": (
                "actual_test" if self.backend_kind == "mock" else "external_backend"
            ),
        }

    def _try_acquire_request(self) -> bool:
        with self._in_flight_lock:
            if self._in_flight >= self.config.max_concurrent_requests:
                return False
            self._in_flight += 1
            return True

    def _release_request(self) -> None:
        with self._in_flight_lock:
            if self._in_flight <= 0:
                raise RuntimeError("in-flight request counter underflow")
            self._in_flight -= 1

    def _concurrency_status(self) -> dict[str, int]:
        with self._in_flight_lock:
            return {
                "in_flight": self._in_flight,
                "limit": self.config.max_concurrent_requests,
            }

    def _relay_payload(
        self, document: dict[str, Any], allowed: Decision, *, stream: bool
    ) -> bytes:
        relay_document = {
            "model": self.backend.mapped_model(allowed.model),
            "messages": document["messages"],
            "max_tokens": allowed.max_tokens,
            "stream": stream,
        }
        return json.dumps(relay_document, ensure_ascii=False).encode("utf-8")

    def _relay(
        self, request_id: str, document: dict[str, Any], allowed: Decision
    ) -> tuple[Decision, bytes | None]:
        connection, request_path = self.backend.connection(
            "/v1/chat/completions", self.relay_timeout_s
        )
        try:
            connection.request(
                "POST",
                request_path,
                body=self._relay_payload(document, allowed, stream=False),
                headers=self.backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            status = engine_response.status
            body = engine_response.read()
            if status != 200:
                return (
                    _engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned HTTP status {status}",
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
                        f"{self._backend_label()} returned invalid JSON",
                        allowed,
                    ),
                    None,
                )
            return allowed, body
        except (TimeoutError, OSError, HTTPException) as error:
            if _is_timeout(error):
                return (
                    _engine_error(
                        504,
                        "ENGINE_TIMEOUT",
                        f"{self._backend_label()} request timed out",
                        allowed,
                    ),
                    None,
                )
            return (
                _engine_error(
                    502,
                    "ENGINE_UNREACHABLE",
                    f"{self._backend_label()} is unreachable",
                    allowed,
                ),
                None,
            )
        finally:
            connection.close()

    @staticmethod
    def _sse_done(buffer: bytes, chunk: bytes) -> tuple[bytes, bool]:
        pending = buffer + chunk
        lines = pending.split(b"\n")
        remainder = lines.pop()
        done = any(
            line.rstrip(b"\r").startswith(b"data:")
            and line.rstrip(b"\r")[5:].strip() == b"[DONE]"
            for line in lines
        )
        return remainder, done

    def _relay_stream(
        self,
        handler: BaseHTTPRequestHandler,
        request_id: str,
        document: dict[str, Any],
        allowed: Decision,
        started_at: float,
    ) -> tuple[Decision, str, bool]:
        connection, request_path = self.backend.connection(
            "/v1/chat/completions",
            min(self.relay_timeout_s, self.request_deadline_s),
        )
        response_started = False
        try:
            connection.request(
                "POST",
                request_path,
                body=self._relay_payload(document, allowed, stream=True),
                headers=self.backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            content_type = engine_response.getheader("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if engine_response.status != 200 or media_type != "text/event-stream":
                return (
                    _engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned an invalid streaming response",
                        allowed,
                    ),
                    "ENGINE_BAD_RESPONSE",
                    False,
                )

            handler.close_connection = True
            try:
                handler.send_response(200)
                handler.send_header("Content-Type", "text/event-stream")
                handler.send_header("Cache-Control", "no-cache")
                handler.send_header("Connection", "close")
                handler.send_header("X-Request-ID", request_id)
                handler.end_headers()
                handler.wfile.flush()
                response_started = True
            except (BrokenPipeError, ConnectionResetError):
                return (
                    _engine_error(
                        200,
                        "CLIENT_DISCONNECTED",
                        "client disconnected during streaming",
                        allowed,
                    ),
                    "client_disconnected",
                    response_started,
                )

            self._append_record(
                self._record(
                    "response_started",
                    request_id,
                    allowed,
                    attempted=True,
                    backend_receipt=self._receipt_not_queried(),
                    response="streaming",
                    error_code=None,
                    stream=True,
                    http_start_status=200,
                    response_started=True,
                )
            )

            line_buffer = b""
            saw_done = False
            last_chunk_at = time.monotonic()
            response_socket = connection.sock
            if response_socket is None:
                response_file = getattr(engine_response, "fp", None)
                response_raw = getattr(response_file, "raw", None)
                response_socket = getattr(response_raw, "_sock", None)
            while True:
                now = time.monotonic()
                deadline_remaining = self.request_deadline_s - (now - started_at)
                idle_remaining = self.stream_idle_timeout_s - (now - last_chunk_at)
                if deadline_remaining <= 0:
                    code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
                    break
                if idle_remaining <= 0:
                    code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
                    break
                if response_socket is not None:
                    response_socket.settimeout(
                        max(0.001, min(deadline_remaining, idle_remaining))
                    )
                try:
                    chunk = engine_response.read1(1024)
                except (socket.timeout, TimeoutError):
                    if time.monotonic() - started_at >= self.request_deadline_s:
                        code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
                    else:
                        code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
                    break
                except (OSError, HTTPException):
                    code, outcome = "STREAM_RELAY_ERROR", "relay_error"
                    break
                if not chunk:
                    if saw_done:
                        return allowed, "completed", True
                    code, outcome = "STREAM_TRUNCATED", "backend_truncated"
                    break
                last_chunk_at = time.monotonic()
                try:
                    handler.wfile.write(chunk)
                    handler.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    code, outcome = "CLIENT_DISCONNECTED", "client_disconnected"
                    break
                line_buffer, found_done = self._sse_done(line_buffer, chunk)
                saw_done = saw_done or found_done

            return _engine_error(200, code, outcome.replace("_", " "), allowed), outcome, True
        except (TimeoutError, OSError, HTTPException) as error:
            if _is_timeout(error):
                return (
                    _engine_error(
                        504,
                        "ENGINE_TIMEOUT",
                        f"{self._backend_label()} request timed out",
                        allowed,
                    ),
                    "ENGINE_TIMEOUT",
                    False,
                )
            return (
                _engine_error(
                    502,
                    "ENGINE_UNREACHABLE",
                    f"{self._backend_label()} is unreachable",
                    allowed,
                ),
                "ENGINE_UNREACHABLE",
                False,
            )
        finally:
            connection.close()

    def _engine_receipt_lookup(self, request_id: str) -> dict[str, Any]:
        queried_at = _utc_now()
        if self.backend_kind != "mock":
            return {
                "status": "not_available",
                "queried_at": queried_at,
                "error": None,
                "evidence_kind": "not_connected",
            }
        query = urlencode({"request_id": request_id})
        try:
            with urlopen(
                self._engine_url(f"/_mock/receipts?{query}"),
                timeout=self.relay_timeout_s,
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
            receipts = payload.get("receipts")
            receipts = receipts if isinstance(receipts, list) else []
            final_receipt = next(
                (
                    item
                    for item in reversed(receipts)
                    if isinstance(item, dict) and isinstance(item.get("stream_end"), str)
                ),
                {},
            )
            return {
                "status": "confirmed" if payload["found"] else "not_found",
                "queried_at": (
                    payload["queried_at"]
                    if isinstance(payload.get("queried_at"), str)
                    else queried_at
                ),
                "error": None,
                "stream_end": final_receipt.get("stream_end"),
                "evidence_kind": final_receipt.get("evidence_kind"),
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
        if self.backend_kind != "mock":
            return "not_available"
        return str(self._engine_receipt_lookup(request_id)["status"])

    def _receipt_not_queried(self) -> str:
        return "not_queried" if self.backend_kind == "mock" else "not_available"

    def _mock_cancel_evidence(self, request_id: str, *, wait: bool) -> str:
        if self.backend_kind != "mock":
            return "not_observed"
        stop_at = time.monotonic() + (0.3 if wait else 0)
        while True:
            evidence = self._engine_receipt_lookup(request_id)
            if (
                evidence.get("stream_end") == "client_disconnected"
                and evidence.get("evidence_kind") == "mock"
            ):
                return "mock_engine_recorded"
            if time.monotonic() >= stop_at:
                return "not_observed"
            time.sleep(0.01)

    def policy_status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        validity = evaluate_policy(self.policy_state, self.revocation_state)
        history = self._policy_history.latest(50)
        history_storage = self._policy_history.status()
        current = {
            "path": self.policy_state.path,
            "schema_version": policy.schema_version if policy else None,
            "policy_id": policy.policy_id if policy else None,
            "policy_version": policy.policy_version if policy else None,
            "target_id": policy.target_id if policy else None,
            "sha256": self.policy_state.sha256,
            "loaded_at": self.policy_state.loaded_at,
            "valid_from": policy.valid_from if policy else None,
            "valid_until": policy.valid_until if policy else None,
            "approval_ref": policy.approval_ref if policy else None,
            "error_code": self.policy_state.error_code,
            "error_message": self.policy_state.error_message,
        }
        return {
            **validity,
            "current": current,
            "policy": current,
            "revocations": {
                "state": self.revocation_state.state,
                "path": self.revocation_state.path,
                "loaded_at": self.revocation_state.loaded_at,
                "sha256": self.revocation_state.sha256,
                "error_code": self.revocation_state.error_code,
                "error_message": self.revocation_state.error_message,
            },
            "history_storage": history_storage,
            "history": history,
            "implemented_validity_items": [
                "policy schema and fields",
                "valid_from",
                "valid_until",
                "revocation snapshot",
            ],
            "unimplemented_validity_items": [
                "signature verification",
                "approval service integration",
                "hot reload",
                "external enforcement confirmation",
            ],
        }

    def baseline_status(self) -> dict[str, Any]:
        return compare_baseline(self._baseline, self._manifest, self._collectors)

    def collectors_status(self) -> dict[str, Any]:
        return {
            "collectors": self._collectors.snapshots(),
            "generated_at": _utc_now(),
        }

    def status(self) -> dict[str, Any]:
        policy = self.policy_state.policy
        engine = self._engine_status()
        record_store = self._record_store_status()
        policy_runtime = self.policy_status()
        baseline = self.baseline_status()
        collectors = self.collectors_status()
        return {
            "status": (
                "ok"
                if policy_runtime["gateway_active"]
                and engine["healthy"]
                and record_store["ok"]
                and policy_runtime["history_storage"]["ok"]
                else "degraded"
            ),
            "mode": (
                "mock_engine" if self.backend_kind == "mock" else "external_vllm_backend"
            ),
            "backend_kind": self.backend_kind,
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
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
            },
            "policy_state": {
                "loaded": policy_runtime["loaded"],
                "gateway_active": policy_runtime["gateway_active"],
                "external_enforcement": policy_runtime["external_enforcement"],
                "reason_code": policy_runtime["reason_code"],
            },
            "policy_history_store": policy_runtime["history_storage"],
            "record_store": record_store,
            "concurrency": self._concurrency_status(),
            "backend": {
                **engine,
                **self.backend.safe_status(),
                "timeouts": {
                    "relay_timeout_s": self.relay_timeout_s,
                    "stream_idle_timeout_s": self.stream_idle_timeout_s,
                    "request_deadline_s": self.request_deadline_s,
                },
            },
            **({"mock_engine": engine} if self.backend_kind == "mock" else {}),
            "baseline": {
                "configured": baseline["baseline"]["state"] != "not_configured",
                "states": {
                    name: sum(1 for row in baseline["rows"] if row["state"] == name)
                    for name in ("match", "drift", "missing", "stale", "error", "not_collected")
                },
                "model_id_limit": MODEL_ID_LIMIT_NOTE,
            },
            "collectors": {
                item["source"]: item["state"] for item in collectors["collectors"]
            },
        }

    def start(self) -> "GatewayApp":
        if self._server is not None:
            raise RuntimeError("gateway is already started")
        owner = self

        class Handler(BaseHTTPRequestHandler):
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

            def _known_path(self) -> bool:
                path = urlsplit(self.path).path
                return (
                    path in _STATIC_FILES
                    or path in {
                        "/api/status",
                        "/api/summary",
                        "/api/requests",
                        "/api/policy",
                        "/api/baseline",
                        "/api/collectors",
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
                if path == "/api/policy":
                    self._send_json(200, owner.policy_status())
                    return
                if path == "/api/baseline":
                    self._send_json(200, owner.baseline_status())
                    return
                if path == "/api/collectors":
                    self._send_json(200, owner.collectors_status())
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
                stream: bool = False,
                extra_headers: dict[str, str] | None = None,
            ) -> None:
                owner._append_record(
                    owner._record(
                        "final",
                        request_id,
                        decision,
                        attempted=False,
                        backend_receipt=owner._receipt_not_queried(),
                        response="none",
                        error_code=None,
                        stream=stream,
                        http_start_status=decision.http_status,
                        final_outcome=decision.reason_code,
                    )
                )
                self._send_error_decision(
                    decision,
                    request_id,
                    close_connection=close_connection,
                    extra_headers=extra_headers,
                )

            def _chat(self, request_id: str) -> None:
                policy_snapshot = owner.policy_state
                policy_runtime = evaluate_policy(
                    policy_snapshot, owner.revocation_state
                )
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

                if not policy_runtime["gateway_active"]:
                    unavailable = Decision(
                        result="ERROR",
                        http_status=503,
                        reason_code=str(policy_runtime["reason_code"]),
                        rule_id=None,
                        message=str(policy_runtime["reason_message"]),
                        model=None,
                        max_tokens=None,
                    )
                    self._finish_without_relay(
                        request_id, unavailable, close_connection=True
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
                    body, self.headers.get("Content-Type"), policy_snapshot
                )
                stream = False
                document: dict[str, Any] | None = None
                try:
                    parsed_document = json.loads(body.decode("utf-8"))
                    if isinstance(parsed_document, dict):
                        document = parsed_document
                        stream = parsed_document.get("stream") is True
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                    pass
                if decision.result != "ALLOWED":
                    self._finish_without_relay(
                        request_id,
                        decision,
                        close_connection=incomplete,
                        stream=stream,
                    )
                    return
                if document is None:
                    self._finish_without_relay(
                        request_id,
                        _transport_decision(400, "MALFORMED_JSON", "invalid JSON"),
                    )
                    return

                if owner.backend_kind == "vllm":
                    if owner.backend.credential_status == "missing":
                        self._finish_without_relay(
                            request_id,
                            _engine_error(
                                503,
                                "BACKEND_CREDENTIAL_MISSING",
                                "backend credential is configured but unavailable",
                                decision,
                            ),
                            stream=stream,
                        )
                        return
                    if owner.backend.mapped_model(decision.model) is None:
                        self._finish_without_relay(
                            request_id,
                            _engine_error(
                                503,
                                "BACKEND_MODEL_UNMAPPED",
                                "allowed policy model has no backend mapping",
                                decision,
                            ),
                            stream=stream,
                        )
                        return

                if not owner._try_acquire_request():
                    limited = Decision(
                        result="DENIED",
                        http_status=429,
                        reason_code="CONCURRENCY_LIMIT",
                        rule_id="max_concurrent_requests",
                        message="maximum concurrent requests reached",
                        model=decision.model,
                        max_tokens=decision.max_tokens,
                    )
                    self._finish_without_relay(
                        request_id,
                        limited,
                        stream=stream,
                        extra_headers={"Retry-After": "1"},
                    )
                    return

                acquired_at = time.monotonic()
                try:
                    started = owner._record(
                        "started",
                        request_id,
                        decision,
                        attempted=False,
                        backend_receipt=owner._receipt_not_queried(),
                        response="none",
                        error_code=None,
                        stream=stream,
                    )
                    if not owner._append_record(started):
                        unavailable = _engine_error(
                            503,
                            "STORE_UNAVAILABLE",
                            "request record store is unavailable",
                            decision,
                        )
                        owner._append_record(
                            owner._record(
                                "final",
                                request_id,
                                unavailable,
                                attempted=False,
                                backend_receipt=owner._receipt_not_queried(),
                                response="failed",
                                error_code="STORE_UNAVAILABLE",
                                stream=stream,
                                http_start_status=503,
                                final_outcome="STORE_UNAVAILABLE",
                            )
                        )
                        self._send_error_decision(unavailable, request_id)
                        return

                    if stream:
                        final_decision, final_outcome, response_started = owner._relay_stream(
                            self,
                            request_id,
                            document,
                            decision,
                            acquired_at,
                        )
                        backend_receipt = owner._backend_receipt(request_id)
                        abnormal_started = response_started and final_outcome not in {
                            "completed",
                            "backend_truncated",
                        }
                        if owner.backend_kind == "vllm":
                            backend_cancel = "not_observed"
                        else:
                            backend_cancel = (
                                owner._mock_cancel_evidence(
                                    request_id,
                                    wait=final_outcome == "client_disconnected",
                                )
                                if abnormal_started
                                else "not_applicable"
                            )
                        completed = final_outcome == "completed"
                        owner._append_record(
                            owner._record(
                                "final",
                                request_id,
                                final_decision,
                                attempted=True,
                                backend_receipt=backend_receipt,
                                response="completed" if completed else "failed",
                                error_code=None if completed else final_decision.reason_code,
                                stream=True,
                                http_start_status=(200 if response_started else final_decision.http_status),
                                final_outcome=final_outcome,
                                response_started=response_started,
                                backend_cancel=backend_cancel,
                            )
                        )
                        if not response_started:
                            self._send_error_decision(final_decision, request_id)
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
                            stream=False,
                            http_start_status=final_decision.http_status,
                            final_outcome=("completed" if completed else final_decision.reason_code),
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
                finally:
                    owner._release_request()

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
        self._collectors.start()
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="mvp-gateway", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        if server is None:
            self._collectors.stop()
            return
        self._collectors.stop()
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
    """Run the gateway and, only in mock mode, its bundled mock engine."""

    if config.host != "127.0.0.1":
        raise ValueError("the MVP servers must bind to 127.0.0.1")
    policy_state = load_policy(config.policy_path)
    data_dir = Path(config.data_dir)
    engine: MockEngine | None = None
    gateway: GatewayApp | None = None
    try:
        if config.backend == "mock":
            engine = MockEngine(
                port=config.engine_port,
                receipts_path=data_dir / "engine_receipts.jsonl",
            ).start()
            gateway = GatewayApp(config, policy_state, engine.address).start()
        else:
            gateway = GatewayApp(config, policy_state).start()
        gateway_url = f"http://{gateway.address[0]}:{gateway.address[1]}"
        print(f"Gateway: {gateway_url}", flush=True)
        if engine is not None:
            engine_url = f"http://{engine.address[0]}:{engine.address[1]}"
            print(f"Mock engine: {engine_url}", flush=True)
        else:
            print(f"External vLLM backend: {gateway.backend.base_url}", flush=True)
        print(f"Data directory: {data_dir}", flush=True)
        print(f"Browser: {gateway_url}/", flush=True)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            print("Stopping MVP servers.", flush=True)
    finally:
        if gateway is not None:
            gateway.stop()
        if engine is not None:
            engine.stop()
