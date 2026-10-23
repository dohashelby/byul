"""Transport contracts: bounded real loopback sockets, no Docker or engine."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

import pytest

from guard.executors import stream_probe
from guard.executors.http_common import Ctx, docker_stream_probe
from guard.runtime.docker import CmdResult, DockerError


def _read_request(conn):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        value = conn.recv(4096)
        if not value:
            return bytes(data)
        data.extend(value)
        if len(data) > 32 * 1024:
            raise RuntimeError("test request exceeded its bound")
    head, _, payload = bytes(data).partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        if line.lower().startswith(b"content-length:"):
            length = int(line.partition(b":")[2].strip())
    while len(payload) < length:
        value = conn.recv(min(4096, length - len(payload)))
        if not value:
            break
        payload += value
    return head + b"\r\n\r\n" + payload


@contextmanager
def _server(handler):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    listener.settimeout(0.05)
    stop = threading.Event()
    accepted, workers, errors = [], [], []

    def serve_one(conn, index):
        try:
            with conn:
                conn.settimeout(1)
                request = _read_request(conn)
                handler(conn, request, index, stop)
        except (OSError, TimeoutError):
            pass  # The collector deliberately closes capped/deadline sockets.
        except Exception as exc:
            errors.append(exc)

    def accept():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            index = len(accepted)
            accepted.append(conn)
            thread = threading.Thread(target=serve_one, args=(conn, index), daemon=True)
            workers.append(thread)
            thread.start()

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}", accepted
    finally:
        stop.set()
        listener.close()
        for conn in accepted:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        thread.join(timeout=0.3)
        for worker in workers:
            worker.join(timeout=0.3)
        assert not errors


def _response(conn, body, *, content_type="application/json", extra=b""):
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: " + content_type.encode("ascii")
                 + b"\r\nContent-Length: " + str(len(body)).encode("ascii") + b"\r\n" + extra
                 + b"Connection: close\r\n\r\n" + body)


def _request(url, label="a", mode="json", **extra):
    return {"id": label, "mode": mode, "method": "POST", "url": url + "/test",
            "body": {"prompt": label}, **extra}


def test_two_distinct_connections_release_together_and_keep_local_labels():
    # Both handlers must be active before either sends its deliberately
    # identical server response ID. Collector labels never use that ID.
    arrived = threading.Barrier(2)

    def handler(conn, request, index, stop):
        arrived.wait(timeout=1)
        payload = json.loads(request.partition(b"\r\n\r\n")[2])
        _response(conn, json.dumps({"id": "same-server-id", "prompt": payload["prompt"]}).encode())

    with _server(handler) as (url, accepted):
        values = stream_probe.collect([_request(url, "a"), _request(url, "b", read_delay_ms=120)], 2)
    assert len(accepted) == 2
    assert [value["id"] for value in values] == ["a", "b"]
    assert len({value["connection_id"] for value in values}) == 2
    assert [json.loads(value["body"])["prompt"] for value in values] == ["a", "b"]
    assert all(value["outcome"] == 200 and value["capture_complete"] and value["eof"] for value in values)
    assert values[1]["first_byte_ms"] >= values[1]["headers_ms"] + 90
    assert max(value["dispatch_ms"] for value in values) <= min(value["end_ms"] for value in values)
    assert all(value["timing_basis"] == "batch_monotonic_client_connection_only" for value in values)


def test_chunked_sse_split_utf8_crlf_comments_and_multiline_fields():
    body = (": keepalive\r\nevent: message\r\nid: server-local\r\nretry: 12\r\n"
            "data: {\"text\":\"한글\"}\r\ndata: second\r\n\r\ndata: [DONE]\r\n\r\n").encode("utf-8")

    def handler(conn, request, index, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
        for byte in body:
            conn.sendall(b"1\r\n" + bytes([byte]) + b"\r\n")
        conn.sendall(b"0\r\n\r\n")

    with _server(handler) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 2)
    assert value["capture_complete"] and value["eof"] and value["terminal_seen"] and value["utf8_valid"]
    assert value["body"] == body.decode("utf-8")
    assert [event["seq"] for event in value["events"]] == [0, 1]
    assert value["events"][0]["data"] == '{"text":"한글"}\nsecond'
    assert value["events"][0]["event"] == "message"
    assert value["events"][0]["id"] == "server-local"
    assert value["events"][0]["retry"] == 12
    assert value["events"][1]["data"] == "[DONE]"
    assert value["post_terminal_events"] == 0


def test_missing_done_is_explicit_even_when_http_capture_completed():
    def handler(conn, request, index, stop):
        _response(conn, b'data: {"value":1}\n\n', content_type="text/event-stream")

    with _server(handler) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 1)
    assert value["capture_complete"] and value["eof"]
    assert not value["terminal_seen"]
    assert len(value["events"]) == 1


def test_events_after_done_are_preserved_for_independent_protocol_checks():
    body = b'data: [DONE]\n\ndata: {"foreign":true}\n\n'
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 1)
    assert value["capture_complete"] and value["terminal_seen"]
    assert value["post_terminal_events"] == 1
    assert value["events"][1]["data"] == '{"foreign":true}'


def test_unterminated_sse_frame_is_not_dispatched_at_eof():
    body = b'data: {"complete":true}\n\ndata: [DONE]'
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 1)
    assert value["eof"] and not value["capture_complete"] and not value["terminal_seen"]
    assert value["stop_reason"] == "incomplete_sse_event"
    assert len(value["events"]) == 1


def test_invalid_utf8_preserves_previous_complete_sse_events_without_replace():
    body = b'data: {"foreign":true}\n\ndata: \xff\n\n'
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 1)
    assert not value["utf8_valid"] and not value["capture_complete"]
    assert value["stop_reason"] == "invalid_utf8" and "body" not in value
    assert value["events"][0]["data"] == '{"foreign":true}'


def test_non_utf8_json_is_not_silently_replaced_or_complete():
    with _server(lambda conn, *_: _response(conn, b'{"value":"\xff"}')) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert value["outcome"] == 200 and value["eof"]
    assert not value["utf8_valid"] and not value["capture_complete"] and "body" not in value


def test_truncated_content_length_does_not_establish_complete_capture():
    def handler(conn, request, index, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 30\r\n\r\n{}")

    with _server(handler) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert value["outcome"] == 200 and value["body"] == "{}"
    assert not value["capture_complete"] and not value["eof"]
    assert value["stop_reason"] == "incomplete_body"


def test_total_deadline_interrupts_a_drip_response_not_only_idle_reads():
    def handler(conn, request, index, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
        conn.sendall(b'data: {"first":true}\n\n')
        while not stop.wait(0.02):
            conn.sendall(b": heartbeat\n\n")

    with _server(handler) as (url, _):
        started = time.monotonic()
        value, = stream_probe.collect([_request(url, mode="sse")], 0.18)
        elapsed = time.monotonic() - started
    assert elapsed < 0.7
    assert not value["capture_complete"] and not value["terminal_seen"]
    assert value["stop_reason"] in {"deadline", "timeout"}
    assert value["events"][0]["data"] == '{"first":true}'


def test_body_cap_preserves_only_bounded_bytes_and_reports_truncation():
    body = b"x" * (stream_probe.MAX_BODY + 50)
    with _server(lambda conn, *_: _response(conn, body)) as (url, _):
        value, = stream_probe.collect([_request(url)], 2)
    assert value["body_bytes"] == stream_probe.MAX_BODY
    assert len(value["body"].encode()) == stream_probe.MAX_BODY
    assert value["body_truncated"] and not value["capture_complete"] and not value["eof"]
    assert value["stop_reason"] == "body_limit"


def test_exact_body_cap_with_real_eof_is_distinct_from_one_extra_byte():
    body = b"x" * stream_probe.MAX_BODY
    with _server(lambda conn, *_: _response(conn, body)) as (url, _):
        value, = stream_probe.collect([_request(url)], 2)
    assert value["body_bytes"] == stream_probe.MAX_BODY
    assert value["capture_complete"] and value["eof"] and not value["body_truncated"]


@pytest.mark.parametrize("boundary,body,reason", [
    ("events", b"data: 1\n\n" * (stream_probe.MAX_EVENTS + 1), "events_limit"),
    ("line", b"data: " + b"x" * stream_probe.MAX_LINE + b"\n\n", "line_limit"),
])
def test_sse_event_and_line_caps(boundary, body, reason):
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse")], 2)
    assert not value["capture_complete"] and value["body_truncated"] and value["stop_reason"] == reason
    assert len(value["events"]) <= stream_probe.MAX_EVENTS


def test_response_header_bound_is_enforced_before_body_capture():
    def handler(conn, request, index, stop):
        conn.sendall(b"HTTP/1.1 200 OK\r\nX-Large: " + b"x" * stream_probe.MAX_HEADERS + b"\r\n\r\nsecret")

    with _server(handler) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert not value["capture_complete"] and value["body_bytes"] == 0
    assert value["stop_reason"] == "metadata_limit"
    assert "secret" not in json.dumps(value)


@pytest.mark.parametrize("extra,expected", [
    (b"Content-Encoding: gzip\r\n", "unsupported_content_encoding"),
    (b"Transfer-Encoding: chunked\r\n", "ambiguous_http_framing"),
    (b"Content-Length: 2\r\n", "transport_error"),
])
def test_unsupported_or_ambiguous_framing_is_not_complete(extra, expected):
    with _server(lambda conn, *_: _response(conn, b"{}", extra=extra)) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert not value["capture_complete"] and value["stop_reason"] == expected


@pytest.mark.parametrize("timeout", [0, -1, 121, True, float("nan"), float("inf")])
def test_invalid_timeout_never_connects(monkeypatch, timeout):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("invalid spec connected"))
    with pytest.raises(ValueError):
        stream_probe.collect([_request("http://127.0.0.1:1")], timeout)


@pytest.mark.parametrize("change", [
    {"read_delay_ms": -1}, {"read_delay_ms": 251}, {"read_delay_ms": True}, {"mode": "tcp"},
    {"method": "DELETE"}, {"url": "https://127.0.0.1/test"}, {"url": "http://user:secret@127.0.0.1/test"},
    {"headers": {"X-ID": "a\nb"}}, {"headers": {"Connection": "keep-alive"}},
    {"headers": {"Host": "elsewhere"}}, {"headers": {"Authorization": "x" * stream_probe.MAX_HEADERS}},
    {"body": {"text": "x" * stream_probe.MAX_REQUEST_BODY}}, {"body": {"n": float("nan")}},
    {"unexpected": "ignored?"},
])
def test_invalid_request_bounds_are_rejected_before_network(monkeypatch, change):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("invalid spec connected"))
    with pytest.raises(ValueError):
        stream_probe.collect([{**_request("http://127.0.0.1:1"), **change}], 1)


@pytest.mark.parametrize("requests", [[], [_request("http://127.0.0.1:1")] * 2,
                                       [_request("http://127.0.0.1:1", str(index)) for index in range(3)]])
def test_empty_duplicate_labels_and_more_than_two_requests_are_invalid(requests):
    with pytest.raises(ValueError):
        stream_probe.collect(requests, 1)


def test_dns_dependency_cannot_hold_batch_past_deadline(monkeypatch):
    release = threading.Event()

    def stalled(*a, **kw):
        release.wait(0.5)
        raise socket.gaierror

    monkeypatch.setattr(socket, "create_connection", stalled)
    started = time.monotonic()
    value, = stream_probe.collect([_request("http://unused.invalid")], 0.05)
    elapsed = time.monotonic() - started
    release.set()
    assert elapsed < 0.4
    assert value["id"] == "a" and not value["capture_complete"] and value["stop_reason"] == "deadline"


def test_transport_failure_does_not_publish_exception_text_or_credentials(monkeypatch):
    def failed(*a, **kw):
        raise ConnectionError("Authorization: Bearer secret-sensitive")

    monkeypatch.setattr(socket, "create_connection", failed)
    value, = stream_probe.collect([_request("http://unused.invalid", headers={"Authorization": "Bearer secret-sensitive"})], 1)
    assert value["detail"] == "ConnectionError"
    assert "secret-sensitive" not in json.dumps(value)


def test_standalone_cli_needs_no_guard_import_and_keeps_keys_off_output():
    def handler(conn, request, index, stop):
        assert b"Authorization: Bearer hidden-key" in request
        _response(conn, b'{"ok":true}')

    with _server(handler) as (url, _):
        source = {"timeout": 2, "requests": [_request(url, headers={"Authorization": "Bearer hidden-key"})]}
        process = subprocess.run([sys.executable, "-I", str(Path(stream_probe.__file__))],
                                 input=json.dumps(source).encode(), capture_output=True, timeout=4, check=False)
    assert process.returncode == 0 and process.stderr == b""
    value, = json.loads(process.stdout)
    assert value["capture_complete"] and value["body"] == '{"ok":true}'
    assert b"hidden-key" not in process.stdout


def test_docker_factory_uses_owned_lifecycle_unique_names_and_stdin_only():
    calls = []

    class OwnedDocker:
        def run_once(self, image, suffix, **kwargs):
            calls.append((image, suffix, kwargs))
            spec = json.loads(kwargs["input"])
            return CmdResult(0, json.dumps([{"id": item["id"]} for item in spec["requests"]]), "")

    probe = docker_stream_probe(OwnedDocker(), "python:bounded")
    requests = [_request("http://owned-engine", headers={"Authorization": "Bearer private"})]
    assert probe("owned-net", requests, 120) == [{"id": "a"}]
    probe("owned-net", requests, 1)
    assert [call[1] for call in calls] == ["streamprobe1", "streamprobe2"]
    assert calls[0][2]["timeout"] == 180
    assert calls[0][2]["network"] == "owned-net"
    assert calls[0][2]["command"] == ["python", "/probe/stream_probe.py"]
    assert "private" not in repr(calls[0][:2]) + repr(calls[0][2]["command"])
    assert b"private" in calls[0][2]["input"]


@pytest.mark.parametrize("result", [CmdResult(2, "", "secret raw diagnostic"),
                                    CmdResult(0, "secret raw malformed", ""),
                                    CmdResult(0, '[{"id":"wrong"}]', "")])
def test_docker_factory_rejects_failure_and_mislabelled_results_without_raw_data(result):
    class OwnedDocker:
        def run_once(self, *a, **kw):
            return result

    with pytest.raises(DockerError) as exc:
        docker_stream_probe(OwnedDocker(), "python:bounded")("net", [_request("http://engine")], 1)
    assert "secret" not in str(exc.value)


def test_ctx_stream_probe_is_optional_and_legacy_log_position_is_preserved():
    fields = list(Ctx.__dataclass_fields__)
    assert fields.index("sprobe") > fields.index("log")
    assert Ctx.__dataclass_fields__["sprobe"].default is None
