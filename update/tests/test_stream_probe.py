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
from types import SimpleNamespace

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


def _slow_profile(**changes):
    # Technical test controls, never enterprise memory/time limits or engine evidence.
    return {"profile": "slow-reader-v1", "chunk_bytes": 1, "interval_ms": 40,
            "close_after_reads": 3, **changes}


def test_slow_reader_pauses_between_each_read_and_closes_peer_while_control_completes():
    arrived = threading.Barrier(2)
    peer_eof = threading.Event()
    body = b"data: " + b"x" * 512 + b"\n\ndata: [DONE]\n\n"

    def handler(conn, request, index, stop):
        arrived.wait(timeout=1)
        label = json.loads(request.partition(b"\r\n\r\n")[2])["prompt"]
        if label == "normal":
            _response(conn, b'{"ok":true}')
        else:
            # Advertise a longer stream but send only the three observed bytes.
            # No unread payload is queued at close, so peer EOF is deterministic
            # rather than depending on an OS's reset handling for discarded data.
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: "
                         + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body[:3])
            if conn.recv(1) == b"":
                peer_eof.set()

    with _server(handler) as (url, accepted):
        slow, normal = stream_probe.collect([
            _request(url, "slow", mode="sse", read_profile=_slow_profile()),
            _request(url, "normal")], 2)
        assert peer_eof.wait(0.5), "the test-owned peer did not observe the client's explicit close"
    assert len(accepted) == 2
    assert slow["read_count"] == 3 and slow["read_pause_count"] == 2
    observations = slow["read_observations"]
    assert [row["seq"] for row in observations] == [0, 1, 2]
    assert all(set(row) == {"seq", "started_ms", "finished_ms", "bytes"} and row["bytes"] == 1 for row in observations)
    assert all(row["finished_ms"] >= row["started_ms"] for row in observations)
    assert all(later["started_ms"] >= earlier["finished_ms"] + 35
               for earlier, later in zip(observations, observations[1:]))
    assert slow["body_bytes"] == 3 and slow["body"] == body[:3].decode()
    assert slow["client_closed"] and slow["stop_reason"] == "client_close"
    assert not slow["capture_complete"] and not slow["eof"] and not slow["terminal_seen"]
    assert slow["client_close_ms"] >= observations[-1]["finished_ms"]
    assert normal["capture_complete"] and normal["eof"] and normal["body"] == '{"ok":true}'
    assert normal["end_ms"] < slow["client_close_ms"]
    assert not {"read_count", "read_pause_count", "client_closed", "client_close_ms", "read_observations"} & set(normal)
    assert slow["timing_basis"] == "batch_monotonic_client_connection_only"


def test_slow_reader_retains_the_legacy_one_time_initial_delay_separately():
    body = b"data: " + b"x" * 64 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse", read_delay_ms=100,
            read_profile=_slow_profile(close_after_reads=2))], 2)
    observations = value["read_observations"]
    assert observations[0]["started_ms"] >= value["headers_ms"] + 90
    assert observations[1]["started_ms"] >= observations[0]["finished_ms"] + 35
    assert value["read_pause_count"] == 1 and value["read_count"] == 2 and value["client_closed"]


@pytest.mark.parametrize("body,terminal", [(b"data: [DONE]\n\n", True), (b"data: 1\n\n", False)])
def test_slow_reader_real_eof_before_close_threshold_keeps_transport_terminal_distinction(body, terminal):
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(chunk_bytes=4096, close_after_reads=32))], 1)
    assert value["capture_complete"] and value["eof"] and value["stop_reason"] == "eof"
    assert value["terminal_seen"] is terminal
    assert not value["client_closed"] and value["client_close_ms"] is None
    assert value["read_count"] == 1 and value["read_pause_count"] == 0
    assert value["read_observations"][0]["bytes"] == len(body)


def test_slow_reader_done_at_close_threshold_never_claims_eof_or_complete_capture():
    terminal = b"data: [DONE]\n\n"
    body = terminal + b"data: unobserved\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(chunk_bytes=len(terminal), close_after_reads=1))], 1)
    assert value["terminal_seen"] and value["events"][0]["data"] == "[DONE]"
    assert value["client_closed"] and value["stop_reason"] == "client_close"
    assert not value["capture_complete"] and not value["eof"]
    assert value["read_count"] == 1 and value["read_pause_count"] == 0
    assert value["body"] == terminal.decode() and "unobserved" not in json.dumps(value)


def test_slow_reader_deadline_interrupts_inter_read_pause_and_is_not_client_close():
    body = b"data: " + b"x" * 512 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        started = time.monotonic()
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(interval_ms=250, close_after_reads=32))], 0.18)
        elapsed = time.monotonic() - started
    assert elapsed < 0.7
    assert value["read_count"] == 1 and value["read_pause_count"] == 1
    assert not value["client_closed"] and value["client_close_ms"] is None
    assert value["stop_reason"] in {"deadline", "timeout"}
    assert not value["eof"] and not value["capture_complete"]
    assert len(value["read_observations"]) == 1


def test_slow_reader_still_preserves_sse_limits_instead_of_relabelling_them_as_client_close():
    body = b"data: " + b"x" * stream_probe.MAX_LINE + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(chunk_bytes=4096, interval_ms=1, close_after_reads=32))], 2)
    assert value["stop_reason"] == "line_limit" and value["body_truncated"]
    assert not value["client_closed"] and not value["capture_complete"]
    assert 1 <= value["read_count"] <= 32
    assert len(value["read_observations"]) == value["read_count"]
    assert all(0 < row["bytes"] <= 4096 for row in value["read_observations"])


def test_slow_reader_read_observations_and_bytes_are_bounded_at_the_maximum_read_count():
    body = b":" + b"x" * 128 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(interval_ms=1, close_after_reads=32))], 2)
    assert value["read_count"] == len(value["read_observations"]) == 32
    assert value["read_pause_count"] == 31 and value["body_bytes"] == 32
    assert value["client_closed"] and value["stop_reason"] == "client_close"


def test_slow_reader_requires_an_actual_sse_response_without_retaining_other_media():
    with _server(lambda conn, *_: _response(conn, b'{"private":"not-SSE"}')) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse", read_profile=_slow_profile())], 1)
    assert value["stop_reason"] == "unsupported_read_profile_content_type"
    assert value["read_count"] == value["read_pause_count"] == 0
    assert not value["client_closed"] and not value["capture_complete"] and not value["eof"]
    assert "not-SSE" not in json.dumps(value)


@pytest.mark.parametrize("profile", [None, False, [], {},
    {"profile": "slow-reader-v1"}, {**_slow_profile(), "extra": 1},
    _slow_profile(profile="other"), _slow_profile(profile=1),
    _slow_profile(chunk_bytes=0), _slow_profile(chunk_bytes=4097), _slow_profile(chunk_bytes=True),
    _slow_profile(interval_ms=0), _slow_profile(interval_ms=251), _slow_profile(interval_ms=1.0),
    _slow_profile(close_after_reads=0), _slow_profile(close_after_reads=33),
    _slow_profile(close_after_reads=True), _slow_profile(close_after_reads="2"),
])
def test_invalid_slow_reader_profiles_never_connect(monkeypatch, profile):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("invalid slow profile connected"))
    with pytest.raises(ValueError):
        stream_probe.collect([_request("http://127.0.0.1:1", mode="sse", read_profile=profile)], 1)


def test_slow_reader_profile_is_not_silently_used_for_a_json_request(monkeypatch):
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("JSON slow profile connected"))
    with pytest.raises(ValueError):
        stream_probe.collect([_request("http://127.0.0.1:1", read_profile=_slow_profile())], 1)


def test_absent_slow_profile_preserves_legacy_output_keys_and_one_initial_pause():
    body = b"data: [DONE]\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse", read_delay_ms=80)], 1)
    assert value["capture_complete"] and value["eof"] and value["terminal_seen"]
    assert value["first_byte_ms"] >= value["headers_ms"] + 65
    assert not {"read_count", "read_pause_count", "client_closed", "client_close_ms", "read_observations"} & set(value)


def test_standalone_slow_profile_works_without_guard_imports_or_credential_output():
    body = b"data: [DONE]\n\ndata: unobserved\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        source = {"timeout": 2, "requests": [_request(url, mode="sse",
            headers={"Authorization": "Bearer hidden-slow-key"},
            read_profile=_slow_profile(chunk_bytes=3, interval_ms=1, close_after_reads=2))]}
        process = subprocess.run([sys.executable, "-I", str(Path(stream_probe.__file__))],
                                 input=json.dumps(source).encode(), capture_output=True, timeout=4, check=False)
    assert process.returncode == 0 and process.stderr == b""
    value, = json.loads(process.stdout)
    assert value["client_closed"] and value["read_count"] == 2 and value["read_pause_count"] == 1
    assert value["body_bytes"] == 6 and value["stop_reason"] == "client_close"
    assert not value["capture_complete"] and b"hidden-slow-key" not in process.stdout


def test_slow_reader_clock_binds_both_clients_to_the_existing_batch_monotonic_base(monkeypatch):
    original_readlink, original_clock = stream_probe.os.readlink, stream_probe._clock
    calls = []
    def readlink(path, *args, **kwargs):
        return "time:[4026531843]" if path == "/proc/self/ns/time" else original_readlink(path, *args, **kwargs)
    def capture_base(base):
        calls.append(base)
        return original_clock(base)
    monkeypatch.setattr(stream_probe.os, "readlink", readlink)
    monkeypatch.setattr(stream_probe, "_clock", capture_base)
    body = b"data: " + b"x" * 64 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        values = stream_probe.collect([_request(url, label, mode="sse", read_profile=_slow_profile(close_after_reads=1))
                                       for label in ("a", "b")], 1)
    assert len(calls) == 1
    expected = {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 4026531843,
                "batch_started_monotonic_ns": round(calls[0] * 1_000_000_000)}
    assert all(row["clock"] == expected and row["client_closed"] for row in values)
    assert all(row["relative_clock"] == "monotonic" for row in values)
    assert values[0]["clock"] is not values[1]["clock"]
    assert all(set(row["clock"]) == set(expected) for row in values)


@pytest.mark.parametrize("link", ["time:[0]", "time:[-1]", "net:[123]", "time:[01]",
                                  "time:[18446744073709551616]", "PRIVATE_INVALID_NAMESPACE"])
def test_unsupported_time_namespace_metadata_is_not_promoted_to_a_clock(monkeypatch, link):
    monkeypatch.setattr(stream_probe.os, "readlink", lambda *_: link)
    assert stream_probe._clock(1.25) is None


def test_missing_time_namespace_keeps_the_slow_read_procedure_and_reports_clock_none(monkeypatch):
    original = stream_probe.os.readlink
    def unavailable(path, *args, **kwargs):
        if path == "/proc/self/ns/time":
            raise FileNotFoundError("PRIVATE_NAMESPACE_ERROR")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(stream_probe.os, "readlink", unavailable)
    body = b"data: " + b"x" * 64 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse", read_profile=_slow_profile(close_after_reads=2))], 1)
    assert value["clock"] is None and value["client_closed"]
    assert value["relative_clock"] == "perf_counter"
    assert value["read_count"] == 2 and value["read_pause_count"] == 1
    assert value["stop_reason"] == "client_close" and "PRIVATE_" not in json.dumps(value)


def test_legacy_collection_does_not_read_or_publish_time_namespace_metadata(monkeypatch):
    monkeypatch.setattr(stream_probe, "_clock", lambda *_: pytest.fail("legacy collector read a new clock"))
    with _server(lambda conn, *_: _response(conn, b'{"ok":true}')) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert value["capture_complete"] and value["body"] == '{"ok":true}'
    assert "clock" not in value
    assert "relative_clock" not in value


def test_slow_reader_uses_high_resolution_relative_time_when_monotonic_is_coarse(monkeypatch):
    real_time = stream_probe.time
    counter_calls = []
    def high_resolution():
        counter_calls.append(True)
        return real_time.perf_counter()
    proxy = SimpleNamespace(
        monotonic=lambda: int(real_time.monotonic() / 0.015625) * 0.015625,
        perf_counter=high_resolution)
    monkeypatch.setattr(stream_probe, "time", proxy)
    monkeypatch.setattr(stream_probe, "_clock", lambda *_: None)
    body = b"data: " + b"x" * 64 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse",
            read_profile=_slow_profile(interval_ms=10, close_after_reads=3))], 1)
    assert counter_calls and value["relative_clock"] == "perf_counter" and value["clock"] is None
    assert value["client_closed"] and value["read_count"] == 3 and value["read_pause_count"] == 2
    rows = value["read_observations"]
    assert all(later["started_ms"] - earlier["finished_ms"] >= 9
               for earlier, later in zip(rows, rows[1:]))
    assert value["client_close_ms"] >= rows[-1]["finished_ms"]


def test_valid_linux_clock_never_uses_a_separate_relative_epoch(monkeypatch):
    real_time = stream_probe.time
    monkeypatch.setattr(stream_probe, "time", SimpleNamespace(monotonic=real_time.monotonic,
        perf_counter=lambda: pytest.fail("valid Linux clock used a different epoch")))
    bases = []
    def clock(base):
        bases.append(base)
        return {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 123,
                "batch_started_monotonic_ns": round(base * 1_000_000_000)}
    monkeypatch.setattr(stream_probe, "_clock", clock)
    body = b"data: " + b"x" * 64 + b"\n\n"
    with _server(lambda conn, *_: _response(conn, body, content_type="text/event-stream")) as (url, _):
        value, = stream_probe.collect([_request(url, mode="sse", read_profile=_slow_profile(close_after_reads=1))], 1)
    assert len(bases) == 1 and value["relative_clock"] == "monotonic"
    assert value["clock"]["batch_started_monotonic_ns"] == round(bases[0] * 1_000_000_000)
    assert value["client_closed"]


def test_legacy_collection_never_consults_the_opt_in_high_resolution_counter(monkeypatch):
    real_time = stream_probe.time
    monkeypatch.setattr(stream_probe, "time", SimpleNamespace(monotonic=real_time.monotonic,
        perf_counter=lambda: pytest.fail("legacy collector used a new counter")))
    with _server(lambda conn, *_: _response(conn, b'{"ok":true}')) as (url, _):
        value, = stream_probe.collect([_request(url)], 1)
    assert value["capture_complete"] and value["body"] == '{"ok":true}'
    assert "clock" not in value and "relative_clock" not in value


def _deadline_guard(deadline=None):
    return {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 777,
            "deadline_monotonic_ns": time.monotonic_ns() + 2_000_000_000 if deadline is None else deadline}


@pytest.mark.parametrize("change", [
    lambda value: value.update(extra="PRIVATE_GUARD_INPUT"),
    lambda value: value.update(basis="PRIVATE_GUARD_INPUT"),
    lambda value: value.update(time_namespace_inode=True),
    lambda value: value.update(deadline_monotonic_ns=0),
    lambda value: value.update(deadline_monotonic_ns=2**63),
])
def test_invalid_deadline_guard_is_rejected_before_any_connection(monkeypatch, change):
    guard = _deadline_guard()
    change(guard)
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("invalid guard connected"))
    with pytest.raises(ValueError, match="source deadline guard"):
        stream_probe.collect([_request("http://127.0.0.1:1")], 1, deadline_guard=guard)


@pytest.mark.parametrize("problem,reason", [("unavailable", "source_clock_unavailable"),
                                           ("different", "source_clock_mismatch"),
                                           ("expired", "source_deadline")])
def test_source_clock_guard_prevents_connecting_after_startup_delay_or_namespace_gap(monkeypatch, problem, reason):
    guard = _deadline_guard(1 if problem == "expired" else None)
    monkeypatch.setattr(stream_probe, "_clock", lambda base: None if problem == "unavailable" else {
        "basis": guard["basis"], "time_namespace_inode": 778 if problem == "different" else 777,
        "batch_started_monotonic_ns": round(base * 1_000_000_000)})
    monkeypatch.setattr(socket, "create_connection", lambda *a, **kw: pytest.fail("failed source guard connected"))
    value, = stream_probe.collect([_request("http://127.0.0.1:1")], 1, deadline_guard=guard)
    assert value["outcome"] == "probe_error" and value["stop_reason"] == reason
    assert value["deadline_guard_verified"] is False and value["capture_complete"] is False
    assert "request_sent_ms" not in value and value["body_bytes"] == 0


@pytest.mark.parametrize("problem,reason", [("expired", "source_deadline"), ("namespace", "source_clock_mismatch")])
def test_source_deadline_is_rechecked_after_connect_before_http_request(monkeypatch, problem, reason):
    now = {"ns": 10_000_000_000, "namespace": 777}
    calls = []
    guard = _deadline_guard(12_000_000_000)
    monkeypatch.setattr(stream_probe.time, "monotonic_ns", lambda: now["ns"])
    monkeypatch.setattr(stream_probe, "_clock", lambda base: {
        "basis": guard["basis"], "time_namespace_inode": now["namespace"],
        "batch_started_monotonic_ns": round(base * 1_000_000_000)})
    class Connection:
        def __init__(self, *args, **kwargs):
            self.sock = self
        def connect(self):
            calls.append("connect")
            if problem == "expired":
                now["ns"] = 13_000_000_000
            else:
                now["namespace"] = 778
        def settimeout(self, value):
            pass
        def request(self, *args, **kwargs):
            pytest.fail("HTTP started after its source cutoff")
        def close(self):
            calls.append("close")
    monkeypatch.setattr(stream_probe.http.client, "HTTPConnection", Connection)
    value, = stream_probe.collect([_request("http://127.0.0.1:1")], 10, deadline_guard=guard)
    assert "connect" in calls and "close" in calls
    assert value["stop_reason"] == reason and value["deadline_guard_verified"] is False
    assert "request_sent_ms" not in value


def test_verified_source_guard_allows_owned_http_and_is_acknowledged(monkeypatch):
    guard = _deadline_guard()
    monkeypatch.setattr(stream_probe, "_clock", lambda base: {
        "basis": guard["basis"], "time_namespace_inode": 777,
        "batch_started_monotonic_ns": round(base * 1_000_000_000)})
    with _server(lambda conn, *_: _response(conn, b'{"ok":true}')) as (url, _):
        value, = stream_probe.collect([_request(url)], 1, deadline_guard=guard)
    assert value["deadline_guard_verified"] is True
    assert value["capture_complete"] is True and value["body"] == '{"ok":true}'


def test_standalone_source_guard_protocol_refuses_http_and_keeps_fixed_safe_metadata():
    source = {"timeout": 1, "deadline_guard": _deadline_guard(1),
              "requests": [_request("http://127.0.0.1:1", headers={"Authorization": "Bearer PRIVATE_GUARD_AUTH"})]}
    process = subprocess.run([sys.executable, "-I", str(Path(stream_probe.__file__))],
                             input=json.dumps(source).encode(), capture_output=True, timeout=4, check=False)
    assert process.returncode == 0 and process.stderr == b""
    value, = json.loads(process.stdout)
    assert value["stop_reason"] in {"source_deadline", "source_clock_mismatch", "source_clock_unavailable"}
    assert value["deadline_guard_verified"] is False and "request_sent_ms" not in value
    assert b"PRIVATE_" not in process.stdout
