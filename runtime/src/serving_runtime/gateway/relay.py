"""Buffered and streaming chat-completion relay behavior."""

from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
import json
import re
from collections import OrderedDict
import socket
import threading
import time
from typing import Any, Callable, Protocol
from urllib.error import URLError

from serving_runtime.gateway.backend import BackendConfig, upstream_request_id
from serving_runtime.gateway.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.gateway.response_timeout import DeadlineWatchdog, set_response_timeout
from serving_runtime.gateway.validator import Decision
from serving_runtime.gateway.response_privacy import sanitize_response_metadata
from serving_runtime.policy.policy import _object_without_duplicates, _reject_json_constant


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
_RETRY_AFTER_SECONDS = re.compile(r"[0-9]{1,10}")
_CHAT_SSE_LINE_LIMIT = 1024 * 1024
_CHAT_SSE_EVENT_LIMIT = 4 * 1024 * 1024
_COMPLETION_STREAM_BODY_LIMIT = 64 * 1024 * 1024
# A buffered response stays resident until JSON validation and client write.
# Match the existing completion stream cap to bound that retained body.
_COMPLETION_BODY_LIMIT = _COMPLETION_STREAM_BODY_LIMIT


class _RelayDeadline(TimeoutError):
    pass


class _RelayOversize(ValueError):
    pass


class _RelayBadEvent(ValueError):
    pass


def _requested_response_fields(document: dict, allowed: Decision) -> frozenset[str]:
    return frozenset(
        name
        for name, _mode in allowed.extra_fields
        if name in document and document[name] is not None and document[name] is not False
    )


def _sanitize_response_json(body: bytes, requested: frozenset[str], *, reject_error: bool = False) -> bytes:
    value = json.loads(
        body.decode("utf-8"), object_pairs_hook=_object_without_duplicates, parse_constant=_reject_json_constant
    )
    if not isinstance(value, dict):
        raise ValueError("successful response must be an object")
    if reject_error and "error" in value:
        # HTTP 200 does not make an upstream SSE error a successful chunk.
        # Keep raw engine error contents out of client output and records.
        raise ValueError("upstream stream error envelope")
    safe = sanitize_response_metadata(value, requested_fields=requested)
    encoded = body if safe == value else json.dumps(safe, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > _COMPLETION_BODY_LIMIT:
        raise ValueError("sanitized response exceeds byte limit")
    return encoded


def _sanitize_sse_event(
    event: bytes, requested: frozenset[str], *, chat: bool = False, allow_initial_bom: bool = False
) -> tuple[bytes, bool]:
    # EventSource discards a leading UTF-8 BOM before interpreting field names.
    if allow_initial_bom:
        event = event.removeprefix(b"\xef\xbb\xbf")
    if event.startswith(b"\xef\xbb\xbf"):
        raise _RelayBadEvent()
    lines = event.splitlines(keepends=True)
    data = [line[5:].strip() for line in lines if line.startswith(b"data:")]
    if not data:
        return event, False
    joined = b"\n".join(data)
    if joined == b"[DONE]":
        return event, True
    try:
        safe = _sanitize_response_json(joined, requested, reject_error=True)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise _RelayBadEvent() from error
    if safe == joined:
        return event, False
    output = []
    replaced = False
    for line in lines:
        if line.startswith(b"data:"):
            if not replaced:
                ending = b"\r\n" if line.endswith(b"\r\n") else b"\r" if line.endswith(b"\r") else b"\n"
                output.append(b"data: " + safe + ending)
                replaced = True
        else:
            output.append(line)
    encoded = b"".join(output)
    if chat:
        try:
            _advance_chat_sse(b"", encoded, 0)
        except _RelayOversize as error:
            raise _RelayBadEvent() from error
    return encoded, False


def _completion_choices(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(choice, dict) and isinstance(choice.get("text"), str) for choice in value)
    )


def validate_completion_response(body: bytes) -> bool:
    """Validate the minimal non-stream vLLM completions response shape."""

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        return False
    if not isinstance(document, dict) or document.get("object") != "text_completion":
        return False
    if not _completion_choices(document.get("choices")):
        return False
    usage = document.get("usage")
    if not isinstance(usage, dict):
        return False
    return all(
        isinstance(usage.get(name), int) and not isinstance(usage.get(name), bool)
        for name in ("prompt_tokens", "completion_tokens", "total_tokens")
    )


def validate_completion_stream(body: bytes) -> bool:
    """Validate completion SSE data chunks and require a terminal [DONE]."""

    saw_chunk = False
    saw_done = False
    for raw_line in body.splitlines():
        line = raw_line.rstrip(b"\r")
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            if saw_done:
                return False
            saw_done = True
            continue
        if saw_done:
            return False
        try:
            document = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
            return False
        if not isinstance(document, dict) or not _completion_choices(document.get("choices")):
            return False
        saw_chunk = True
    return saw_chunk and saw_done


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
    # Upstream text can contain private prompts, paths, or credentials. Keep
    # the status and origin, but never copy that detail into the client error.
    message = f"{classification} (HTTP {status})"
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
    cache_salt: str | None = None,
) -> bytes:
    relay_document: dict[str, Any] = {}
    # Policy-approved extra fields keep the client's original value; the
    # Gateway-owned fields below are written last so they always win.
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
            backend.output_tokens_field(route): allowed.max_tokens,
            "stream": stream,
        }
    )
    if allowed.n is not None and allowed.n > 1:
        relay_document["n"] = allowed.n
    # The client cannot supply this field in per-principal mode. Write the
    # derived Gateway value after every client-controlled field.
    if cache_salt is not None:
        relay_document["cache_salt"] = cache_salt
    return json.dumps(relay_document, ensure_ascii=False).encode("utf-8")


def _advance_chat_sse(buffer: bytes, chunk: bytes, event_bytes: int) -> tuple[bytes, bool, int]:
    """Bound physical lines and events while retaining only a partial line.

    event_bytes counts completed lines, including LF. A partial line is
    checked against it on every read; a blank LF/CRLF line ends an event.
    """

    pending = buffer + chunk
    lines = pending.split(b"\n")
    remainder = lines.pop()
    done = False
    for line in lines:
        event_bytes += len(line) + 1
        if len(line) > _CHAT_SSE_LINE_LIMIT or event_bytes > _CHAT_SSE_EVENT_LIMIT:
            raise _RelayOversize()
        normalized = line.rstrip(b"\r")
        if not normalized:
            event_bytes = 0
        elif normalized.startswith(b"data:") and normalized[5:].strip() == b"[DONE]":
            done = True
    if len(remainder) > _CHAT_SSE_LINE_LIMIT or event_bytes + len(remainder) > _CHAT_SSE_EVENT_LIMIT:
        raise _RelayOversize()
    return remainder, done, event_bytes


def sse_done(buffer: bytes, chunk: bytes) -> tuple[bytes, bool]:
    remainder, done, _ = _advance_chat_sse(buffer, chunk, 0)
    return remainder, done


def response_id_receipt(request_id: str, value: object, *, backend_kind: str = "unknown") -> str:
    """Classify the engine response ``id`` against the forwarded X-Request-ID.

    vLLM's OpenAI server names responses ``chatcmpl-<X-Request-Id>`` /
    ``cmpl-<X-Request-Id>`` when the header is honored; other engines may use
    their own IDs. Only exact forms count as a match; the ID itself is not kept.
    """
    if not isinstance(value, str) or not value:
        return "engine_response_id_absent"
    # Keep exact untagged echo forms for existing engines and recognize the
    # canonical vLLM callback ID actually forwarded by BackendConfig.
    forwarded_ids = (request_id,)
    if backend_kind == "vllm":
        forwarded_ids += (upstream_request_id("vllm", request_id),)
    for forwarded in forwarded_ids:
        if value in {forwarded, f"chatcmpl-{forwarded}", f"cmpl-{forwarded}"}:
            return "engine_response_id_matched"
    return "engine_response_id_mismatch"


class _ResponseIds:
    """Bounded per-request classification; read once by the final record."""

    def __init__(self, limit: int = 4096, *, backend_kind: str = "unknown") -> None:
        self._backend_kind = backend_kind
        self._items: OrderedDict[str, str] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def note(self, request_id: str, value: object) -> None:
        with self._lock:
            if request_id in self._items:
                return  # The first chunk decides for streams.
            self._items[request_id] = response_id_receipt(request_id, value, backend_kind=self._backend_kind)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def pop(self, request_id: str) -> str:
        with self._lock:
            return self._items.pop(request_id, "not_available")


class _StreamResponseId:
    """Inspect the first chat SSE data line in memory, with bounded buffering."""

    LIMIT = 64 * 1024

    def __init__(self) -> None:
        self.pending = b""
        self.finished = False

    def feed(self, chunk: bytes) -> tuple[bool, object]:
        if self.finished:
            return False, None
        self.pending += chunk
        while b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            if len(line) > self.LIMIT:
                break
            if not line.startswith(b"data:"):
                continue
            try:
                value = json.loads(line[5:].strip())
            except (ValueError, UnicodeDecodeError, RecursionError):
                continue
            if isinstance(value, dict):
                self.finished, self.pending = True, b""
                return True, value.get("id")
        else:
            if len(self.pending) <= self.LIMIT:
                return False, None
        # An oversized first event cannot supply trustworthy ID evidence.
        self.finished, self.pending = True, b""
        return False, None


@dataclass(frozen=True)
class _StreamCall:
    response: StreamResponse
    request_id: str
    document: dict[str, Any]
    allowed: Decision
    started_at: float
    route: str
    cache_salt: str | None


@dataclass(frozen=True)
class _StreamTransport:
    connection: Any
    watchdog: Any
    start_response: Callable[[], None]
    response_started: Callable[[], bool]


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
        self._response_ids = _ResponseIds(backend_kind=backend.kind)

    def response_id_receipt(self, request_id: str) -> str:
        """Return and forget the engine response ID classification (not_available if none)."""
        return self._response_ids.pop(request_id)

    def _backend_label(self) -> str:
        return "mock engine" if self._backend.kind == "mock" else "backend"

    @property
    def request_deadline_s(self) -> float:
        return self._request_deadline_s

    def relay(
        self,
        request_id: str,
        document: dict[str, Any],
        allowed: Decision,
        *,
        route: str = CHAT_ROUTE,
        cache_salt: str | None = None,
        started_at: float | None = None,
    ) -> tuple[Decision, bytes | None]:
        started_at = time.monotonic() if started_at is None else started_at
        deadline_at = started_at + self._request_deadline_s
        remaining = deadline_at - time.monotonic()
        if remaining <= 0:
            return engine_error(504, "ENGINE_DEADLINE", "deadline exceeded", allowed), None
        path = "/v1/chat/completions" if route == CHAT_ROUTE else "/v1/completions"
        connection, request_path = self._backend.connection(path, min(self._relay_timeout_s, remaining))
        watchdog = DeadlineWatchdog(connection, deadline_at)
        watchdog.start()
        try:
            watchdog.connect()
            connection.request(
                "POST",
                request_path,
                body=relay_payload(
                    self._backend,
                    document,
                    allowed,
                    stream=False,
                    route=route,
                    cache_salt=cache_salt,
                ),
                headers=self._backend.request_headers(request_id=request_id, content_type="application/json"),
            )
            engine_response = connection.getresponse()
            watchdog.watch_response(engine_response)
            if watchdog.expired():
                raise _RelayDeadline()
            response_socket = connection.sock or getattr(
                getattr(getattr(engine_response, "fp", None), "raw", None), "_sock", None
            )

            def read_body(limit: int) -> bytes:
                nonlocal response_socket
                chunks: list[bytes] = []
                size = 0
                while True:
                    remaining = deadline_at - time.monotonic()
                    if remaining <= 0 or watchdog.expired():
                        raise _RelayDeadline()
                    response_socket = set_response_timeout(response_socket, min(self._relay_timeout_s, remaining))
                    try:
                        chunk = engine_response.read1(min(4096, limit + 1 - size))
                    except (socket.timeout, TimeoutError, OSError) as error:
                        if watchdog.expired():
                            raise _RelayDeadline() from error
                        raise
                    if watchdog.expired():
                        raise _RelayDeadline()
                    if not chunk:
                        return b"".join(chunks)
                    size += len(chunk)
                    if size > limit:
                        raise _RelayOversize()
                    chunks.append(chunk)

            status = engine_response.status
            if 400 <= status < 500:
                try:
                    body = read_body(_ENGINE_ERROR_BODY_LIMIT)
                    truncated = False
                except _RelayOversize:
                    body = b""
                    truncated = True
                return (
                    engine_rejection(
                        status,
                        body[:_ENGINE_ERROR_BODY_LIMIT],
                        allowed,
                        truncated=truncated,
                        retry_after=engine_response.getheader("Retry-After"),
                    ),
                    None,
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
            body = read_body(_COMPLETION_BODY_LIMIT)
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                return (
                    engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned invalid JSON",
                        allowed,
                    ),
                    None,
                )
            if route == CHAT_ROUTE and (
                not isinstance(parsed, dict)
                or "error" in parsed
                or not isinstance(parsed.get("choices"), list)
                or not parsed["choices"]
                or not all(isinstance(choice, dict) for choice in parsed["choices"])
            ):
                return engine_error(
                    502, "ENGINE_BAD_RESPONSE", "backend returned an invalid chat response", allowed
                ), None
            if route == COMPLETIONS_ROUTE and not validate_completion_response(body):
                return (
                    engine_error(
                        502,
                        "ENGINE_BAD_RESPONSE",
                        f"{self._backend_label()} returned an invalid completion response",
                        allowed,
                    ),
                    None,
                )
            try:
                body = _sanitize_response_json(body, _requested_response_fields(document, allowed))
            except (UnicodeError, ValueError, RecursionError):
                return engine_error(
                    502, "ENGINE_BAD_RESPONSE", "backend returned invalid response metadata", allowed
                ), None
            if watchdog.finish():
                raise _RelayDeadline()
            self._response_ids.note(request_id, parsed.get("id") if isinstance(parsed, dict) else None)
            return allowed, body
        except _RelayOversize:
            return (engine_error(502, "ENGINE_BAD_RESPONSE", "backend returned an oversized response", allowed), None)
        except _RelayDeadline:
            return (engine_error(504, "ENGINE_DEADLINE", "deadline exceeded", allowed), None)
        except (TimeoutError, OSError, HTTPException) as error:
            if watchdog.expired():
                return (engine_error(504, "ENGINE_DEADLINE", "deadline exceeded", allowed), None)
            if is_timeout(error):
                if time.monotonic() >= deadline_at:
                    return (engine_error(504, "ENGINE_DEADLINE", "deadline exceeded", allowed), None)
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
            watchdog.cancel()
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
        cache_salt: str | None = None,
    ) -> tuple[Decision, str, bool]:
        path = "/v1/chat/completions" if route == CHAT_ROUTE else "/v1/completions"
        remaining = self._request_deadline_s - (time.monotonic() - started_at)
        if remaining <= 0:
            decision = engine_error(504, "ENGINE_TIMEOUT", f"{self._backend_label()} request timed out", allowed)
            return decision, "ENGINE_TIMEOUT", False
        connection, request_path = self._backend.connection(path, min(self._relay_timeout_s, remaining))
        deadline_at = started_at + self._request_deadline_s
        watchdog = DeadlineWatchdog(connection, deadline_at)
        watchdog.start()
        response_started = False

        def start_response() -> None:
            nonlocal response_started
            if watchdog.expired():
                raise _RelayDeadline()
            downstream = getattr(response, "connection", None)
            watchdog.watch_socket(downstream)
            set_response_timeout(downstream, deadline_at - time.monotonic())
            # Headers can be partially written before start_stream_response raises.
            response_started = True
            response.start_stream_response(request_id)
            on_response_started()

        call = _StreamCall(response, request_id, document, allowed, started_at, route, cache_salt)
        transport = _StreamTransport(connection, watchdog, start_response, lambda: response_started)
        try:
            opened = self._open_stream(call, transport, request_path)
            if len(opened) == 3:
                return opened
            (engine_response,) = opened
            if route == COMPLETIONS_ROUTE:
                return self._completion_stream(call, transport, engine_response)
            try:
                start_response()
            except (BrokenPipeError, ConnectionResetError):
                expired = watchdog.expired()
                return (
                    engine_error(
                        200,
                        "STREAM_DEADLINE" if expired else "CLIENT_DISCONNECTED",
                        "deadline exceeded" if expired else "client disconnected during streaming",
                        allowed,
                        source="gateway" if expired else "client",
                    ),
                    "deadline_exceeded" if expired else "client_disconnected",
                    response_started,
                )

            return self._chat_stream(call, transport, engine_response)
        except (TimeoutError, OSError, HTTPException) as error:
            return self._stream_exception(error, allowed, response_started, watchdog)
        finally:
            watchdog.cancel()
            connection.close()

    def _open_stream(self, call, transport, request_path):
        request_id, document, allowed, route, cache_salt = (
            call.request_id,
            call.document,
            call.allowed,
            call.route,
            call.cache_salt,
        )
        connection, watchdog = (transport.connection, transport.watchdog)
        watchdog.connect()
        connection.request(
            "POST",
            request_path,
            body=relay_payload(
                self._backend,
                document,
                allowed,
                stream=True,
                route=route,
                cache_salt=cache_salt,
            ),
            headers=self._backend.request_headers(request_id=request_id, content_type="application/json"),
        )
        engine_response = connection.getresponse()
        watchdog.watch_response(engine_response)
        if watchdog.expired():
            return (
                engine_error(504, "ENGINE_TIMEOUT", f"{self._backend_label()} request timed out", allowed),
                "ENGINE_TIMEOUT",
                False,
            )
        content_type = engine_response.getheader("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if 400 <= engine_response.status < 500:
            body = engine_response.read(_ENGINE_ERROR_BODY_LIMIT + 1)
            if watchdog.expired():
                return (
                    engine_error(504, "ENGINE_TIMEOUT", f"{self._backend_label()} request timed out", allowed),
                    "ENGINE_TIMEOUT",
                    False,
                )
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
        return (engine_response,)

    def _read_completion_chunk(self, call, transport, engine_response, last_chunk_at, response_socket):
        started_at = call.started_at
        watchdog = transport.watchdog
        now = time.monotonic()
        deadline_remaining = self._request_deadline_s - (now - started_at)
        idle_remaining = self._stream_idle_timeout_s - (now - last_chunk_at)
        if deadline_remaining <= 0 or watchdog.expired():
            code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            return None, response_socket, code, outcome
        if idle_remaining <= 0:
            code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
            return None, response_socket, code, outcome
        response_socket = set_response_timeout(response_socket, min(deadline_remaining, idle_remaining))
        try:
            chunk = engine_response.read1(1024)
        except (socket.timeout, TimeoutError):
            if watchdog.expired():
                code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            else:
                code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
            return None, response_socket, code, outcome
        except (OSError, HTTPException):
            code, outcome = (
                ("STREAM_DEADLINE", "deadline_exceeded")
                if watchdog.expired()
                else ("STREAM_RELAY_ERROR", "relay_error")
            )
            return None, response_socket, code, outcome
        if watchdog.expired():
            code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            return None, response_socket, code, outcome
        return (chunk, response_socket)

    def _read_chat_chunk(self, call, transport, engine_response, last_chunk_at, response_socket):
        started_at = call.started_at
        watchdog = transport.watchdog
        now = time.monotonic()
        deadline_remaining = self._request_deadline_s - (now - started_at)
        idle_remaining = self._stream_idle_timeout_s - (now - last_chunk_at)
        if deadline_remaining <= 0 or watchdog.expired():
            code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            return None, response_socket, code, outcome
        if idle_remaining <= 0:
            code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
            return None, response_socket, code, outcome
        response_socket = set_response_timeout(response_socket, min(deadline_remaining, idle_remaining))
        try:
            chunk = engine_response.read1(1024)
        except (socket.timeout, TimeoutError):
            if watchdog.expired():
                code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            else:
                code, outcome = "STREAM_IDLE_TIMEOUT", "idle_timeout"
            return None, response_socket, code, outcome
        except (OSError, HTTPException):
            code, outcome = (
                ("STREAM_DEADLINE", "deadline_exceeded")
                if watchdog.expired()
                else ("STREAM_RELAY_ERROR", "relay_error")
            )
            return None, response_socket, code, outcome
        if watchdog.expired():
            code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
            return None, response_socket, code, outcome
        return (chunk, response_socket)

    def _write_completion_comment(self, call, transport, event, emitted_size, stop_stream):
        response = call.response
        watchdog = transport.watchdog
        try:
            emitted_size += len(event)
            if emitted_size > _COMPLETION_STREAM_BODY_LIMIT:
                raise _RelayBadEvent()
            response.write_stream_chunk(event)
        except _RelayBadEvent:
            code, outcome = "ENGINE_BAD_RESPONSE", "oversized_completion_stream"
            stop_stream = True
            return emitted_size, (code, outcome), stop_stream
        except (BrokenPipeError, ConnectionResetError):
            code, outcome = (
                ("STREAM_DEADLINE", "deadline_exceeded")
                if watchdog.expired()
                else ("CLIENT_DISCONNECTED", "client_disconnected")
            )
            stop_stream = True
            return emitted_size, (code, outcome), stop_stream
        return (emitted_size, None, stop_stream)

    def _check_completion_data(self, request_id, data_lines, saw_done, saw_chunk, stop_stream):
        for data in data_lines:
            if data == b"[DONE]":
                if saw_done or not saw_chunk:
                    code, outcome = "ENGINE_BAD_RESPONSE", "invalid_completion_stream"
                    stop_stream = True
                    break
                saw_done = True
            elif saw_done:
                code, outcome = "ENGINE_BAD_RESPONSE", "invalid_completion_stream"
                stop_stream = True
                break
            else:
                try:
                    item = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                    item = None
                if not isinstance(item, dict) or not _completion_choices(item.get("choices")):
                    code, outcome = "ENGINE_BAD_RESPONSE", "invalid_completion_stream"
                    stop_stream = True
                    break
                if not saw_chunk:
                    self._response_ids.note(request_id, item.get("id"))
                saw_chunk = True
        if stop_stream:
            return saw_done, saw_chunk, stop_stream, (code, outcome)
        return (saw_done, saw_chunk, stop_stream, None)

    def _write_completion_event(self, call, transport, event, emitted_size, stop_stream):
        response, document, allowed = (call.response, call.document, call.allowed)
        watchdog, start_response = (transport.watchdog, transport.start_response)
        _response_started = transport.response_started
        try:
            safe_event, _ = _sanitize_sse_event(event, _requested_response_fields(document, allowed))
            emitted_size += len(safe_event)
            if emitted_size > _COMPLETION_STREAM_BODY_LIMIT:
                raise _RelayBadEvent()
            if not _response_started():
                start_response()
            response.write_stream_chunk(safe_event)
        except _RelayBadEvent:
            code, outcome = "ENGINE_BAD_RESPONSE", "invalid_completion_stream"
            stop_stream = True
            return emitted_size, (code, outcome), stop_stream
        except (BrokenPipeError, ConnectionResetError):
            code, outcome = (
                ("STREAM_DEADLINE", "deadline_exceeded")
                if watchdog.expired()
                else ("CLIENT_DISCONNECTED", "client_disconnected")
            )
            stop_stream = True
            return emitted_size, (code, outcome), stop_stream
        return (emitted_size, None, stop_stream)

    def _completion_events(self, call, transport, progress):
        request_id = call.request_id
        pending, emitted_size, first_sse_event, saw_chunk, saw_done, stop_stream = progress
        _response_started = transport.response_started
        while True:
            # Forward only complete, validated SSE events. A partial
            # JSON event is never exposed to the client.
            match = re.search(rb"\r\n\r\n|\r\n\n|\n\r\n|\n\n|\r\r", pending)
            if match is None:
                break
            event, pending = pending[: match.end()], pending[match.end() :]
            if first_sse_event:
                event = event.removeprefix(b"\xef\xbb\xbf")
                first_sse_event = False
            if event.startswith(b"\xef\xbb\xbf"):
                code, outcome = "ENGINE_BAD_RESPONSE", "invalid_completion_stream"
                stop_stream = True
                break
            data_lines = [line[5:].strip() for line in event.splitlines() if line.startswith(b"data:")]
            if not data_lines:
                if _response_started():
                    emitted_size, failure, stop_stream = self._write_completion_comment(
                        call, transport, event, emitted_size, stop_stream
                    )
                    if failure is not None:
                        code, outcome = failure
                        break
                continue
            saw_done, saw_chunk, stop_stream, failure = self._check_completion_data(
                request_id, data_lines, saw_done, saw_chunk, stop_stream
            )
            if failure is not None:
                code, outcome = failure
            if stop_stream:
                break
            emitted_size, failure, stop_stream = self._write_completion_event(
                call, transport, event, emitted_size, stop_stream
            )
            if failure is not None:
                code, outcome = failure
                break
        if stop_stream:
            return pending, emitted_size, first_sse_event, saw_chunk, saw_done, stop_stream, (code, outcome)
        return (pending, emitted_size, first_sse_event, saw_chunk, saw_done, stop_stream, None)

    def _completion_result(self, call, transport, code, outcome):
        allowed = call.allowed
        _response_started = transport.response_started
        if not _response_started():
            if code == "CLIENT_DISCONNECTED":
                return engine_error(200, code, "client disconnected", allowed, source="client"), outcome, False
            status = 504 if code in {"STREAM_DEADLINE", "STREAM_IDLE_TIMEOUT"} else 502
            return engine_error(status, code, outcome.replace("_", " "), allowed), outcome, False
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

    def _completion_stream(self, call, transport, engine_response):
        allowed = call.allowed
        connection, watchdog = (transport.connection, transport.watchdog)
        _response_started = transport.response_started
        pending = b""
        total_size = 0
        emitted_size = 0
        first_sse_event = True
        saw_chunk = False
        saw_done = False
        last_chunk_at = time.monotonic()
        response_socket = connection.sock or getattr(
            getattr(getattr(engine_response, "fp", None), "raw", None), "_sock", None
        )
        while True:
            read = self._read_completion_chunk(call, transport, engine_response, last_chunk_at, response_socket)
            if len(read) == 4:
                _, response_socket, code, outcome = read
                break
            chunk, response_socket = read
            if not chunk:
                if saw_done and not pending:
                    if watchdog.finish():
                        code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
                        break
                    return allowed, "completed", _response_started()
                code, outcome = "STREAM_TRUNCATED", "backend_truncated"
                break
            last_chunk_at = time.monotonic()
            total_size += len(chunk)
            if total_size > _COMPLETION_STREAM_BODY_LIMIT:
                code, outcome = "ENGINE_BAD_RESPONSE", "oversized_completion_stream"
                break
            pending += chunk
            stop_stream = False
            pending, emitted_size, first_sse_event, saw_chunk, saw_done, stop_stream, failure = self._completion_events(
                call, transport, (pending, emitted_size, first_sse_event, saw_chunk, saw_done, stop_stream)
            )
            if failure is not None:
                code, outcome = failure
            if stop_stream:
                break
        return self._completion_result(call, transport, code, outcome)

    def _chat_events(self, call, transport, chunk, pending_event, first_sse_event, saw_done, saw_chunk):
        response, document, allowed = (call.response, call.document, call.allowed)
        watchdog = transport.watchdog
        pending_event += chunk
        try:
            while True:
                boundary = re.search(rb"\r\n\r\n|\r\n\n|\n\r\n|\n\n|\r\r", pending_event)
                if boundary is None:
                    break
                event, pending_event = pending_event[: boundary.end()], pending_event[boundary.end() :]
                safe_event, terminal = _sanitize_sse_event(
                    event,
                    _requested_response_fields(document, allowed),
                    chat=True,
                    allow_initial_bom=first_sse_event,
                )
                first_sse_event = False
                has_data = any(line.startswith(b"data:") for line in safe_event.splitlines())
                if (terminal and (saw_done or not saw_chunk)) or (saw_done and has_data):
                    raise _RelayBadEvent()
                response.write_stream_chunk(safe_event)
                saw_done = saw_done or terminal
                saw_chunk = saw_chunk or (has_data and not terminal)
        except _RelayBadEvent:
            code, outcome = "ENGINE_BAD_RESPONSE", "invalid_chat_stream"
            return pending_event, first_sse_event, saw_done, saw_chunk, (code, outcome)
        except (BrokenPipeError, ConnectionResetError):
            code, outcome = (
                ("STREAM_DEADLINE", "deadline_exceeded")
                if watchdog.expired()
                else ("CLIENT_DISCONNECTED", "client_disconnected")
            )
            return pending_event, first_sse_event, saw_done, saw_chunk, (code, outcome)
        return (pending_event, first_sse_event, saw_done, saw_chunk, None)

    def _chat_result(self, allowed, code, outcome):
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

    def _chat_stream(self, call, transport, engine_response):
        request_id, allowed = (call.request_id, call.allowed)
        connection, watchdog = (transport.connection, transport.watchdog)
        line_buffer = b""
        stream_id = _StreamResponseId()
        pending_event = b""
        first_sse_event = True
        event_bytes = 0
        saw_done = False
        saw_chunk = False
        last_chunk_at = time.monotonic()
        response_socket = connection.sock
        if response_socket is None:
            response_file = getattr(engine_response, "fp", None)
            response_raw = getattr(response_file, "raw", None)
            response_socket = getattr(response_raw, "_sock", None)
        while True:
            read = self._read_chat_chunk(call, transport, engine_response, last_chunk_at, response_socket)
            if len(read) == 4:
                _, response_socket, code, outcome = read
                break
            chunk, response_socket = read
            if not chunk:
                if saw_done and not pending_event:
                    if watchdog.finish():
                        code, outcome = "STREAM_DEADLINE", "deadline_exceeded"
                        break
                    return allowed, "completed", True
                code, outcome = "STREAM_TRUNCATED", "backend_truncated"
                break
            last_chunk_at = time.monotonic()
            found_id, value = stream_id.feed(chunk)
            if found_id:
                self._response_ids.note(request_id, value)
            try:
                line_buffer, found_done, event_bytes = _advance_chat_sse(line_buffer, chunk, event_bytes)
            except _RelayOversize:
                code, outcome = "ENGINE_BAD_RESPONSE", "oversized_chat_stream"
                break
            pending_event, first_sse_event, saw_done, saw_chunk, failure = self._chat_events(
                call, transport, chunk, pending_event, first_sse_event, saw_done, saw_chunk
            )
            if failure is not None:
                code, outcome = failure
                break
        return self._chat_result(allowed, code, outcome)

    def _stream_exception(self, error, allowed, response_started, watchdog):
        if watchdog.expired():
            return (
                engine_error(
                    200 if response_started else 504,
                    "STREAM_DEADLINE" if response_started else "ENGINE_TIMEOUT",
                    "deadline exceeded",
                    allowed,
                ),
                "deadline_exceeded" if response_started else "ENGINE_TIMEOUT",
                response_started,
            )
        if is_timeout(error):
            return (
                engine_error(
                    200 if response_started else 504,
                    "STREAM_WRITE_TIMEOUT" if response_started else "ENGINE_TIMEOUT",
                    f"{self._backend_label()} request timed out",
                    allowed,
                ),
                "write_timeout" if response_started else "ENGINE_TIMEOUT",
                response_started,
            )
        return (
            engine_error(
                200 if response_started else 502,
                "CLIENT_DISCONNECTED" if response_started else "ENGINE_UNREACHABLE",
                "client disconnected during streaming"
                if response_started
                else f"{self._backend_label()} is unreachable",
                allowed,
                source="client" if response_started else "engine",
            ),
            "client_disconnected" if response_started else "ENGINE_UNREACHABLE",
            response_started,
        )
