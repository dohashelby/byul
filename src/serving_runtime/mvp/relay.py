"""Buffered and streaming chat-completion relay behavior."""

from __future__ import annotations

from http.client import HTTPException
import json
import socket
import time
from typing import Any, Callable, Protocol
from urllib.error import URLError

from .backend import BackendConfig
from .validator import Decision


class StreamResponse(Protocol):
    """The HTTP operations the stream relay is allowed to perform."""

    def start_stream_response(self, request_id: str) -> None: ...

    def write_stream_chunk(self, chunk: bytes) -> None: ...


def engine_error(
    status: int, code: str, message: str, allowed: Decision
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
) -> bytes:
    relay_document: dict[str, Any] = {}
    # Test-only extra fields keep the client's original value; the Gateway-owned
    # fields below are written last so they always win.
    for name, _mode in allowed.extra_fields:
        relay_document[name] = document[name]
    relay_document.update(
        {
            "model": backend.mapped_model(allowed.model),
            "messages": document["messages"],
            "max_tokens": allowed.max_tokens,
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
        self, request_id: str, document: dict[str, Any], allowed: Decision
    ) -> tuple[Decision, bytes | None]:
        connection, request_path = self._backend.connection(
            "/v1/chat/completions", self._relay_timeout_s
        )
        try:
            connection.request(
                "POST",
                request_path,
                body=relay_payload(self._backend, document, allowed, stream=False),
                headers=self._backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            status = engine_response.status
            body = engine_response.read()
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
    ) -> tuple[Decision, str, bool]:
        connection, request_path = self._backend.connection(
            "/v1/chat/completions",
            min(self._relay_timeout_s, self._request_deadline_s),
        )
        response_started = False
        try:
            connection.request(
                "POST",
                request_path,
                body=relay_payload(self._backend, document, allowed, stream=True),
                headers=self._backend.request_headers(
                    request_id=request_id, content_type="application/json"
                ),
            )
            engine_response = connection.getresponse()
            content_type = engine_response.getheader("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
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
                engine_error(200, code, outcome.replace("_", " "), allowed),
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
