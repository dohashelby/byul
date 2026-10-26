"""Buffered and streaming chat-completion relay behavior."""

from __future__ import annotations

from http.client import HTTPException
import json
import re
import socket
import time
from typing import Any, Callable, Protocol
from urllib.error import URLError

from .backend import BackendConfig
from .auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from .validator import Decision


class StreamResponse(Protocol):
    """The HTTP operations the stream relay is allowed to perform."""

    def start_stream_response(self, request_id: str) -> None: ...

    def write_stream_chunk(self, chunk: bytes) -> None: ...


def engine_error(
    status: int,
    code: str,
    message: str,
    allowed: Decision,
    *,
    source: str = "engine",
) -> Decision:
    return Decision(
        result="ERROR",
        http_status=status,
        reason_code=code,
        rule_id=None,
        message=message,
        model=allowed.model,
        max_tokens=allowed.max_tokens,
        # Keep what was (or would have been) relayed so failure records stay accurate.
        extra_fields=allowed.extra_fields,
        n=allowed.n,
        source=source,
    )


_ENGINE_ERROR_BODY_LIMIT = 64 * 1024
_ENGINE_MESSAGE_LIMIT = 256
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,10}")


def _engine_message(body: bytes, *, truncated: bool) -> str | None:
    """Extract one bounded JSON error message without retaining the body."""

    if truncated:
        return None
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return None
    candidates: list[object] = []
    if isinstance(document, dict):
        error = document.get("error")
        if isinstance(error, dict):
            candidates.extend((error.get("message"), error.get("detail")))
        else:
            candidates.append(error)
        candidates.extend((document.get("message"), document.get("detail")))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        cleaned = " ".join(_CONTROL_CHARACTERS.sub(" ", candidate).split())
        if cleaned:
            return cleaned[:_ENGINE_MESSAGE_LIMIT]
    return None


def retry_after_seconds(value: str | None) -> str | None:
    """Return a canonical bounded delta-seconds value, never an HTTP date."""

    if value is None:
        return None
    stripped = value.strip()
    if _RETRY_AFTER_SECONDS.fullmatch(stripped) is None:
        return None
    return str(int(stripped))


def engine_rejection(
    status: int,
    body: bytes,
    allowed: Decision,
    *,
    truncated: bool,
    retry_after: str | None = None,
) -> Decision:
    if status in {401, 403}:
        # This is the Gateway's upstream credential/configuration failure, not
        # a client authentication challenge. Preserve the engine status only in
        # the record and do not expose its response body to the client.
        return Decision(
            result="ERROR",
            http_status=502,
            reason_code="ENGINE_AUTHORIZATION_FAILED",
            rule_id=None,
            message="engine authorization failed",
            model=allowed.model,
            max_tokens=allowed.max_tokens,
            extra_fields=allowed.extra_fields,
            n=allowed.n,
            source="engine",
            engine_http_status=status,
        )
    if status == 429:
        code = "ENGINE_CAPACITY_REJECTED"
        classification = "engine capacity rejection"
        client_retry_after = retry_after_seconds(retry_after)
    else:
        code = "ENGINE_REJECTED_REQUEST"
        classification = "engine request rejection"
        client_retry_after = None
    summary = _engine_message(body, truncated=truncated)
    message = f"{classification} (HTTP {status})"
    if summary is not None:
        message = f"{message}: {summary}"
    return Decision(
        result="ENGINE_REJECTED",
        http_status=status,
        reason_code=code,
        rule_id=None,
        message=message,
        model=allowed.model,
        max_tokens=allowed.max_tokens,
        extra_fields=allowed.extra_fields,
        n=allowed.n,
        source="engine",
        engine_http_status=status,
        retry_after=client_retry_after,
    )


def is_timeout(error: BaseException) -> bool:
    if isinstance(error, (socket.timeout, TimeoutError)):
        return True
    if isinstance(error, URLError):
        if isinstance(error.reason, BaseException):
            return is_timeout(error.reason)
        return "timed out" in str(error.reason).lower()
    return False


def relay_payload(
    backend: BackendConfig,
    document: dict[str, Any],
    allowed: Decision,
    *,
    stream: bool,
    route: str = CHAT_ROUTE,
) -> bytes:
    relay_document: dict[str, Any] = {}
    # Test-only extra fields keep the client's original value; the Gateway-owned
    # fields below are written last so they always win.
    for name, _mode in allowed.extra_fields:
        relay_document[name] = document[name]
    input_field = "messages" if route == CHAT_ROUTE else "prompt"
    if route not in {CHAT_ROUTE, COMPLETIONS_ROUTE}:
        raise ValueError("unsupported relay route")
    relay_document.update(
        {
            "model": backend.mapped_model(allowed.model),
            input_field: document[input_field],
            # Each engine gets its own name for the output limit (SP2: the vLLM
            # schema is not applied to SGLang as-is).
            backend.output_tokens_field: allowed.max_tokens,
            "stream": stream,
        }
    )
    if allowed.n is not None and allowed.n > 1:
        relay_document["n"] = allowed.n
    return json.dumps(relay_document, ensure_ascii=False).encode("utf-8")


def sse_done(buffer: bytes, chunk: bytes) -> tuple[bytes, bool]:
    pending = buffer + chunk
    lines = pending.split(b"\n")
    remainder = lines.pop()
    done = any(
        line.rstrip(b"\r").startswith(b"data:")
        and line.rstrip(b"\r")[5:].strip() == b"[DONE]"
        for line in lines
    )
    return remainder, done


class Relay:
    """Relay validated completion requests to one fixed backend."""

    def __init__(
        self,
        backend: BackendConfig,
        relay_timeout_s: float,
        stream_idle_timeout_s: float,
        request_deadline_s: float,
    ) -> None:
        self._backend = backend
        self._relay_timeout_s = relay_timeout_s
        self._stream_idle_timeout_s = stream_idle_timeout_s
        self._request_deadline_s = request_deadline_s

    def _backend_label(self) -> str:
        return "mock engine" if self._backend.kind == "mock" else "backend"

    def relay(
        self,
        request_id: str,
        document: dict[str, Any],
        allowed: Decision,
        *,
        route: str = CHAT_ROUTE,
    ) -> tuple[Decision, bytes | None]:
        path = "/v1/chat/completions" if route == CHAT_ROUTE else "/v1/completions"
        connection, request_path = self._backend.connection(
            path, self._relay_timeout_s
        )
        try:
            connection.request(
                "POST",
                request_path,
                body=relay_payload(
                    self._backend, document, allowed, stream=False, route=route
                ),
                headers=self._backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            status = engine_response.status
            if 400 <= status < 500:
                body = engine_response.read(_ENGINE_ERROR_BODY_LIMIT + 1)
                return (
                    engine_rejection(
                        status,
                        body[:_ENGINE_ERROR_BODY_LIMIT],
                        allowed,
                        truncated=len(body) > _ENGINE_ERROR_BODY_LIMIT,
                        retry_after=engine_response.getheader("Retry-After"),
                    ),
                    None,
                )
            body = (
                engine_response.read()
                if status == 200
                else engine_response.read(_ENGINE_ERROR_BODY_LIMIT + 1)
            )
            if status != 200:
                return (
                    engine_error(
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
                    engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned invalid JSON",
                        allowed,
                    ),
                    None,
                )
            return allowed, body
        except (TimeoutError, OSError, HTTPException) as error:
            if is_timeout(error):
                return (
                    engine_error(
                        504,
                        "ENGINE_TIMEOUT",
                        f"{self._backend_label()} request timed out",
                        allowed,
                    ),
                    None,
                )
            return (
                engine_error(
                    502,
                    "ENGINE_UNREACHABLE",
                    f"{self._backend_label()} is unreachable",
                    allowed,
                ),
                None,
            )
        finally:
            connection.close()

    def relay_stream(
        self,
        response: StreamResponse,
        request_id: str,
        document: dict[str, Any],
        allowed: Decision,
        started_at: float,
        on_response_started: Callable[[], None],
        *,
        route: str = CHAT_ROUTE,
    ) -> tuple[Decision, str, bool]:
        path = "/v1/chat/completions" if route == CHAT_ROUTE else "/v1/completions"
        connection, request_path = self._backend.connection(
            path,
            min(self._relay_timeout_s, self._request_deadline_s),
        )
        response_started = False
        try:
            connection.request(
                "POST",
                request_path,
                body=relay_payload(
                    self._backend, document, allowed, stream=True, route=route
                ),
                headers=self._backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            content_type = engine_response.getheader("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if 400 <= engine_response.status < 500:
                body = engine_response.read(_ENGINE_ERROR_BODY_LIMIT + 1)
                rejected = engine_rejection(
                    engine_response.status,
                    body[:_ENGINE_ERROR_BODY_LIMIT],
                    allowed,
                    truncated=len(body) > _ENGINE_ERROR_BODY_LIMIT,
                    retry_after=engine_response.getheader("Retry-After"),
                )
                return rejected, rejected.reason_code, False
            if engine_response.status != 200 or media_type != "text/event-stream":
                return (
                    engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned an invalid streaming response",
                        allowed,
                    ),
                    "ENGINE_BAD_RESPONSE",
                    False,
                )

            try:
                response.start_stream_response(request_id)
                response_started = True
            except (BrokenPipeError, ConnectionResetError):
                return (
                    engine_error(
                        200,
                        "CLIENT_DISCONNECTED",
                        "client disconnected during streaming",
                        allowed,
                        source="client",
                    ),
                    "client_disconnected",
                    response_started,
                )

            on_response_started()

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
                deadline_remaining = self._request_deadline_s - (now - started_at)
                idle_remaining = self._stream_idle_timeout_s - (now - last_chunk_at)
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
                    if time.monotonic() - started_at >= self._request_deadline_s:
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
                    response.write_stream_chunk(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    code, outcome = "CLIENT_DISCONNECTED", "client_disconnected"
                    break
                line_buffer, found_done = sse_done(line_buffer, chunk)
                saw_done = saw_done or found_done

            return (
                engine_error(
                    200,
                    code,
                    outcome.replace("_", " "),
                    allowed,
                    source=("client" if code == "CLIENT_DISCONNECTED" else "engine"),
                ),
                outcome,
                True,
            )
        except (TimeoutError, OSError, HTTPException) as error:
            if is_timeout(error):
                return (
                    engine_error(
                        504,
                        "ENGINE_TIMEOUT",
                        f"{self._backend_label()} request timed out",
                        allowed,
                    ),
                    "ENGINE_TIMEOUT",
                    False,
                )
            return (
                engine_error(
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
