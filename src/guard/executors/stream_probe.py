"""Bounded HTTP/1.1 JSON/SSE collector, also runnable in an isolated probe image.

This module uses only the standard library. It records connection-local bytes
and timing; it never decides which principal or engine worker owns a result.
Authorization and request bodies enter through stdin, never command arguments.
The existing serial ``http_probe`` protocol is intentionally unchanged.
"""
from __future__ import annotations

import errno
import hashlib
import http.client
import json
import math
import re
import secrets
import socket
import sys
import threading
import time
from urllib.parse import urlsplit

MAX_REQUESTS = 2
MAX_BODY = 128 * 1024
MAX_EVENTS = 64
MAX_LINE = 16 * 1024
MAX_HEADERS = 8 * 1024
MAX_REQUEST_BODY = 16 * 1024
MAX_DELAY_MS = 250
MAX_TIMEOUT = 120
MAX_SPEC = 128 * 1024
_LABEL = re.compile(r"[A-Za-z0-9_.:|/-]{1,160}\Z")
_HEADER = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")
_RESPONSE_HEADERS = {"content-type", "content-length", "transfer-encoding", "content-encoding", "x-request-id"}


class CaptureLimit(Exception):
    """A response exceeded a fixed collector boundary."""


class InvalidUTF8(Exception):
    """A complete SSE line was not strict UTF-8."""


class _BoundedReader:
    """Cap header and chunk metadata without replacing http.client framing."""

    def __init__(self, inner, *, headers: bool):
        self.inner = inner
        self.headers = headers
        self.metadata_bytes = 0

    def readline(self, size=-1):
        limit = MAX_HEADERS if self.headers else MAX_LINE
        remaining = MAX_HEADERS - self.metadata_bytes
        maximum = min(limit, remaining)
        if maximum < 0:
            raise CaptureLimit("metadata")
        # One extra byte distinguishes an exact boundary from truncation.
        requested = maximum + 1 if size < 0 else min(size, maximum + 1)
        value = self.inner.readline(requested)
        self.metadata_bytes += len(value)
        if len(value) > limit or self.metadata_bytes > MAX_HEADERS:
            raise CaptureLimit("metadata")
        return value

    def __getattr__(self, name):
        return getattr(self.inner, name)


class _Response(http.client.HTTPResponse):
    def begin(self):
        original = self.fp
        self.fp = _BoundedReader(original, headers=True)
        super().begin()
        # Chunk size lines and trailers have their own cumulative bound. Payload
        # is read through read1(n), so advertised chunk lengths cannot allocate n.
        if self.fp is not None:
            self.fp = _BoundedReader(original, headers=False)


class _SSE:
    def __init__(self, base: float):
        self.base = base
        self.buffer = bytearray()
        self.fields: dict = {}
        self.data: list[str] = []
        self.events: list[dict] = []
        self.first_line = True
        self.terminal = False
        self.post_terminal_events = 0

    def _line(self, value: bytes):
        try:
            line = value.decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise InvalidUTF8 from exc
        if self.first_line:
            line = line.removeprefix("\ufeff")
            self.first_line = False
        if not line:
            if self.data:
                if len(self.events) >= MAX_EVENTS:
                    raise CaptureLimit("events")
                payload = "\n".join(self.data)
                if self.terminal:
                    self.post_terminal_events += 1
                event = {"seq": len(self.events), "data": payload, **self.fields,
                         "received_ms": int((time.monotonic() - self.base) * 1000)}
                self.events.append(event)
                if payload == "[DONE]":
                    self.terminal = True
            self.data, self.fields = [], {}
            return
        if line.startswith(":"):
            return
        name, separator, text = line.partition(":")
        if separator and text.startswith(" "):
            text = text[1:]
        if name == "data":
            self.data.append(text)
        elif name == "event":
            self.fields["event"] = text
        elif name == "id" and "\x00" not in text:
            self.fields["id"] = text
        elif name == "retry" and text.isascii() and text.isdigit() and len(text) <= 9:
            self.fields["retry"] = int(text)

    def feed(self, value: bytes, *, final=False):
        self.buffer.extend(value)
        while self.buffer:
            newline = next((index for index, byte in enumerate(self.buffer) if byte in (10, 13)), None)
            if newline is None:
                if len(self.buffer) > MAX_LINE:
                    raise CaptureLimit("line")
                break
            if newline > MAX_LINE:
                raise CaptureLimit("line")
            if self.buffer[newline] == 13 and newline + 1 == len(self.buffer) and not final:
                break  # CRLF may be split across socket reads.
            width = 2 if self.buffer[newline:newline + 2] == b"\r\n" else 1
            line = bytes(self.buffer[:newline])
            del self.buffer[:newline + width]
            self._line(line)

    @property
    def incomplete(self):
        # EOF does not dispatch an unfinished EventSource data block.
        return bool(self.buffer or self.data or self.fields)


def _prepare(requests, timeout):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT:
        raise ValueError("timeout must be finite, positive and at most 120 seconds")
    if type(requests) is not list or not 1 <= len(requests) <= MAX_REQUESTS:
        raise ValueError("stream collector needs one or two requests")
    prepared, labels = [], set()
    for request in requests:
        if type(request) is not dict or set(request) - {"id", "mode", "method", "url", "headers", "body", "read_delay_ms"}:
            raise ValueError("unsupported stream request fields")
        label = request.get("id")
        if type(label) is not str or not _LABEL.fullmatch(label) or label in labels:
            raise ValueError("collector labels must be unique bounded strings")
        labels.add(label)
        mode = request.get("mode", "json")
        method = request.get("method", "POST")
        delay = request.get("read_delay_ms", 0)
        if type(mode) is not str or type(method) is not str or mode not in {"json", "sse"} or method not in {"GET", "POST"}:
            raise ValueError("unsupported bounded HTTP mode or method")
        if type(delay) is not int or not 0 <= delay <= MAX_DELAY_MS:
            raise ValueError("read_delay_ms must be an integer from zero to 250")
        url = request.get("url")
        if type(url) is not str or any(ord(char) < 32 or ord(char) == 127 for char in url):
            raise ValueError("invalid bounded HTTP URL")
        try:
            if len(url.encode("utf-8")) > MAX_HEADERS:
                raise ValueError("invalid bounded HTTP URL")
            parts = urlsplit(url)
            port = parts.port if parts.port is not None else 80
        except ValueError as exc:
            raise ValueError("invalid bounded HTTP URL") from exc
        if (parts.scheme != "http" or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.fragment or not 1 <= port <= 65535):
            raise ValueError("only plain HTTP target URLs without credentials are supported")
        headers = request.get("headers", {})
        if type(headers) is not dict:
            raise ValueError("headers must be a string mapping")
        clean = {}
        lowered = set()
        for name, value in headers.items():
            if (type(name) is not str or not _HEADER.fullmatch(name) or type(value) is not str
                    or any(ord(char) < 32 or ord(char) == 127 for char in value)
                    or name.lower() in lowered or name.lower() in {"host", "content-length", "transfer-encoding", "connection", "accept-encoding"}):
                raise ValueError("invalid or reserved request header")
            try:
                value.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise ValueError("request header must be Latin-1 encodable") from exc
            lowered.add(name.lower())
            clean[name] = value
        body = request.get("body")
        try:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") if body is not None else None
        except (TypeError, ValueError, UnicodeEncodeError) as exc:
            raise ValueError("request body must be finite UTF-8 JSON") from exc
        if data is not None and len(data) > MAX_REQUEST_BODY:
            raise ValueError("request body exceeds the fixed capture profile")
        if data is not None and "content-type" not in lowered:
            clean["Content-Type"] = "application/json"
        clean.update({"Connection": "close", "Accept-Encoding": "identity"})
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        try:
            header_size = len((method + " " + path + " HTTP/1.1\r\n").encode("ascii")) + sum(
                len(name.encode("ascii")) + len(value.encode("latin-1")) + 4 for name, value in clean.items()) + 256
        except UnicodeEncodeError as exc:
            raise ValueError("request target must be ASCII encoded") from exc
        if header_size > MAX_HEADERS:
            raise ValueError("request headers exceed the fixed capture profile")
        prepared.append({"id": label, "mode": mode, "method": method, "host": parts.hostname, "port": port,
                         "path": path, "headers": clean, "data": data, "delay": delay})
    return prepared


def _connect_outcome(exc):
    if isinstance(exc, socket.gaierror):
        return "dns_error"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, ConnectionRefusedError):
        return "refused"
    if isinstance(exc, OSError) and exc.errno in {errno.EHOSTUNREACH, errno.ENETUNREACH}:
        return "unreachable"
    return "error"


def collect(requests: list[dict], timeout: float) -> list[dict]:
    """Collect at most two connections under one monotonic batch deadline.

    ``eof`` means HTTP message framing ended (including content-length/chunked
    termination), not necessarily a physical TCP FIN. Timing proves only the
    client connection intervals, never concurrent execution in engine workers.
    """
    prepared = _prepare(requests, timeout)
    base = time.monotonic()
    deadline = base + timeout
    barrier = threading.Barrier(len(prepared))
    stop = threading.Event()
    lock = threading.Lock()
    sockets = {}
    results = [None] * len(prepared)
    latest = [{"id": request["id"], "method": request["method"], "mode": request["mode"],
               "connection_id": secrets.token_hex(12), "outcome": "error", "phase": "dispatch",
               "capture_complete": False, "body_truncated": False, "body_bytes": 0,
               "utf8_valid": True, "terminal_seen": False, "eof": False, "events": [],
               "stop_reason": "not_started", "response_headers": {},
               "body_sha256": hashlib.sha256(b"").hexdigest(),
               "timing_basis": "batch_monotonic_client_connection_only"} for request in prepared]

    def milliseconds():
        return int((time.monotonic() - base) * 1000)

    def close_sockets():
        with lock:
            active = list(sockets.values())
        for sock in active:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def watchdog():
        if not stop.wait(max(0, deadline - time.monotonic())):
            stop.set()
            close_sockets()

    def worker(index, request):
        output = dict(latest[index])
        raw = bytearray()
        parser = None
        conn = None
        response = None

        def publish():
            # A late DNS/OS call must not replace a result frozen at the batch
            # deadline. Mutable captures are never shared with the caller.
            snapshot = {**output, "events": [dict(item) for item in output["events"]],
                        "body_bytes": len(raw), "body_sha256": hashlib.sha256(raw).hexdigest()}
            try:
                snapshot["body"] = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                snapshot["utf8_valid"] = False
            with lock:
                if results[index] is None:
                    latest[index] = snapshot

        def remaining():
            value = deadline - time.monotonic()
            if value <= 0 or stop.is_set():
                raise TimeoutError
            return value

        try:
            barrier.wait(timeout=remaining())
            output.update(dispatch_ms=milliseconds(), phase="connect", stop_reason="collecting")
            publish()
            conn = http.client.HTTPConnection(request["host"], request["port"], timeout=remaining())
            conn.response_class = _Response
            conn.connect()
            tracked = conn.sock
            with lock:
                sockets[index] = tracked
            if stop.is_set():
                raise TimeoutError
            tracked.settimeout(remaining())
            output["phase"] = "headers"
            conn.request(request["method"], request["path"], body=request["data"], headers=request["headers"])
            output["request_sent_ms"] = milliseconds()
            publish()
            tracked.settimeout(remaining())
            response = conn.getresponse()
            output.update(outcome=response.status, headers_ms=milliseconds(), phase="body",
                          http_version={10: "HTTP/1.0", 11: "HTTP/1.1"}.get(response.version, "unsupported"))
            response_headers = {}
            seen = set()
            for name, value in response.getheaders():
                key = name.lower()
                if key in {"content-length", "transfer-encoding", "content-type", "content-encoding"} and key in seen:
                    raise http.client.HTTPException("ambiguous response headers")
                seen.add(key)
                if key in _RESPONSE_HEADERS:
                    response_headers[key] = value
            output["response_headers"] = response_headers
            content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
            output["content_type"] = content_type
            if response_headers.get("content-encoding", "identity").strip().lower() not in {"", "identity"}:
                output["stop_reason"] = "unsupported_content_encoding"
                return
            transfer = response_headers.get("transfer-encoding", "").strip().lower()
            if transfer and transfer != "chunked":
                output["stop_reason"] = "unsupported_transfer_encoding"
                return
            if transfer and "content-length" in response_headers:
                output["stop_reason"] = "ambiguous_http_framing"
                return
            content_length = response_headers.get("content-length")
            if content_length is not None and (not content_length.isascii() or not content_length.isdigit()):
                output["stop_reason"] = "invalid_content_length"
                return
            if request["mode"] == "sse" and content_type == "text/event-stream":
                parser = _SSE(base)
                output["events"] = parser.events
            if request["delay"] and stop.wait(min(request["delay"] / 1000, remaining())):
                raise TimeoutError
            publish()
            while True:
                if response.isclosed():
                    # Content-length completion closes HTTPResponse's file. On
                    # Connection: close that also releases the final socket FD;
                    # touching its timeout again would turn success into EBADF.
                    value = b""
                else:
                    tracked.settimeout(remaining())
                    value = response.read1(min(4096, MAX_BODY - len(raw) + 1))
                if not value:
                    # A watchdog shutdown can look exactly like peer EOF. Never
                    # turn our deadline cancellation into a complete capture.
                    remaining()
                    if response.length not in (0, None):
                        output["stop_reason"] = "incomplete_body"
                        break
                    output["eof"] = True
                    if parser is not None:
                        parser.feed(b"", final=True)
                    output["stop_reason"] = "incomplete_sse_event" if parser is not None and parser.incomplete else "eof"
                    output["capture_complete"] = output["stop_reason"] == "eof"
                    break
                output.setdefault("first_byte_ms", milliseconds())
                accepted = value[:MAX_BODY - len(raw)]
                raw.extend(accepted)
                if parser is not None:
                    parser.feed(accepted)
                    output["terminal_seen"] = parser.terminal
                if len(accepted) != len(value):
                    output.update(body_truncated=True, stop_reason="body_limit")
                    break
                output["body_bytes"] = len(raw)
                publish()
        except CaptureLimit as exc:
            output.update(stop_reason=f"{exc.args[0]}_limit", body_truncated=True)
        except InvalidUTF8:
            output.update(stop_reason="invalid_utf8", utf8_valid=False)
        except Exception as exc:
            if output["phase"] == "connect":
                output["outcome"] = _connect_outcome(exc)
            output["detail"] = type(exc).__name__
            output["stop_reason"] = "deadline" if stop.is_set() or time.monotonic() >= deadline else (
                "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) else "transport_error")
        finally:
            if response is not None:
                response.close()
            if conn is not None:
                conn.close()
            with lock:
                sockets.pop(index, None)
            output.update(body_bytes=len(raw), body_sha256=hashlib.sha256(raw).hexdigest(), end_ms=milliseconds())
            try:
                output["body"] = raw.decode("utf-8", "strict")
            except UnicodeDecodeError:
                output.update(utf8_valid=False, capture_complete=False)
                if output["stop_reason"] == "eof":
                    output["stop_reason"] = "invalid_utf8"
            if parser is not None:
                output.update(terminal_seen=parser.terminal, post_terminal_events=parser.post_terminal_events)
            with lock:
                if results[index] is None:
                    results[index] = output

    timer = threading.Thread(target=watchdog, daemon=True, name="guard-stream-deadline")
    workers = [threading.Thread(target=worker, args=(index, request), daemon=True,
                                name=f"guard-stream-{index}") for index, request in enumerate(prepared)]
    timer.start()
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    if any(thread.is_alive() for thread in workers):
        stop.set()
        close_sockets()
        # Socket shutdown normally releases reads immediately. This small,
        # fixed cleanup margin also bounds a stalled DNS/OS dependency.
        cleanup_deadline = time.monotonic() + 0.1
        for thread in workers:
            thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
    stop.set()
    with lock:
        for index, request in enumerate(prepared):
            if results[index] is None:
                results[index] = {**latest[index], "id": request["id"], "capture_complete": False,
                                  "stop_reason": "deadline", "detail": "TimeoutError", "end_ms": milliseconds()}
        # Copy before leaving: lingering daemon workers cannot mutate returned
        # partial SSE event lists or nested metadata.
        return json.loads(json.dumps(results, ensure_ascii=False))


def main():
    try:
        source = sys.stdin.buffer.read(MAX_SPEC + 1)
        if len(source) > MAX_SPEC:
            raise ValueError("collector specification exceeds the fixed profile")
        spec = json.loads(source)
        if type(spec) is not dict or set(spec) - {"timeout", "requests"}:
            raise ValueError("invalid collector specification")
        result = collect(spec["requests"], spec.get("timeout", 30))
    except Exception as exc:
        # Raw parser/transport errors can contain credentials or caller input.
        sys.stderr.write(type(exc).__name__)
        raise SystemExit(2) from None
    sys.stdout.write(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
