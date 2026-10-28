"""EXAMPLE process/file seams and real test-owned sockets; no Docker or engine.

Linux production identity/nofollow contracts are separate from portable EXAMPLE
metadata seams. No synthetic process identity is reported as an engine proof.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from guard.executors import egress_probe as sender, egress_receiver as receiver


IDENTITY = {"pid": 123, "start_time_ticks": 456, "uid": 1000, "net_namespace_inode": 789}
CORRELATION = "0123456789abcdef" * 2


def example_identity():
    return dict(IDENTITY)


def example_inode(sock):
    # EXAMPLE inode only. The transport still uses a real test-owned socket.
    return sock.fileno() + 10000


def example_log(path):
    # Portable file seam: no claim that Windows implements Linux O_NOFOLLOW.
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
    return receiver.BoundedLog(descriptor)


@contextmanager
def owned_receiver(tmp_path, *, max_connections=1, identity_reader=example_identity, socket_factory=None):
    ready = threading.Event()
    startup, results, errors = [], [], []
    path = tmp_path / "EXAMPLE-egress.jsonl"
    def on_ready(row):
        startup.append(row)
        ready.set()
    def serve():
        try:
            results.append(receiver.serve(0, path=path, bind_ip="127.0.0.1", listen_seconds=1,
                max_connections=max_connections, identity_reader=identity_reader,
                socket_factory=socket_factory, socket_inode_reader=example_inode, log_factory=example_log, ready=on_ready))
        except Exception as error:
            errors.append(error)
            ready.set()
    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    require_ready = ready.wait(1)
    try:
        assert require_ready and startup and not errors
        yield startup[0]["local"]["port"], path, startup[0], results
    finally:
        thread.join(timeout=2)
        assert not thread.is_alive() and not errors


def probe(port, **kwargs):
    return sender.send_probe("127.0.0.1", port, CORRELATION, kwargs.pop("timeout", 1),
        kwargs.pop("expected_uid", IDENTITY["uid"]), kwargs.pop("expected_netns", IDENTITY["net_namespace_inode"]),
        identity_reader=kwargs.pop("identity_reader", example_identity),
        socket_inode_reader=kwargs.pop("socket_inode_reader", example_inode), **kwargs)


def test_real_owned_sender_receiver_metadata_join_without_raw_marker(tmp_path):
    factories = []
    def socket_factory(*args):
        factories.append(args)
        return socket.socket(*args)
    with owned_receiver(tmp_path) as (port, path, startup, results):
        packet = probe(port, socket_factory=socket_factory)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert results == [{"accepted_connections": 1, "receipts": 1}]
    assert factories == [(socket.AF_INET, socket.SOCK_STREAM)]
    assert len(rows) == 2 and rows[0] == startup
    receipt = rows[1]
    assert packet["schema"] == sender.SCHEMA and receipt["schema"] == receiver.SCHEMA
    assert packet["outcome"] == "completed"
    assert all(packet[key] is True for key in ("connected", "marker_sent", "ack_verified", "process_identity_stable"))
    assert packet["identity_before"] == packet["identity_after"] == receipt["identity"] == IDENTITY
    assert packet["sent_bytes"] == receipt["received_bytes"] == 32 and packet["received_bytes"] == 65
    assert packet["connection_guard_verified"] is False  # Unguarded function seam is EXAMPLE only.
    assert datetime.fromisoformat(packet["connected_at"]) <= datetime.fromisoformat(packet["marker_sent_at"])
    assert datetime.fromisoformat(packet["marker_sent_at"]) <= datetime.fromisoformat(packet["observed_at"])
    assert receipt["ack_sent"] is True
    assert packet["local"] == receipt["peer"] and packet["peer"] == receipt["local"]
    assert packet["correlation_sha256"] == receipt["correlation_sha256"] == hashlib.sha256(CORRELATION.encode()).hexdigest()
    assert packet["socket_inode"] > 0 and receipt["socket_inode"] > 0 and startup["socket_inode"] > 0
    assert startup["socket_inode"] != receipt["socket_inode"]
    assert startup["kind"] == "startup" and receipt["kind"] == "receipt"
    assert startup["peer"] is startup["correlation_sha256"] is None
    assert startup["received_bytes"] == 0 and startup["ack_sent"] is False
    assert CORRELATION not in json.dumps(packet) and CORRELATION not in path.read_text(encoding="utf-8")
    assert len(json.dumps(packet).encode()) < sender.MAX_PACKET_BYTES
    assert path.stat().st_size <= receiver.MAX_LOG_BYTES


@pytest.mark.parametrize("field,value", [("expected_uid", 1001), ("expected_netns", 790)])
def test_expected_identity_mismatch_never_constructs_a_network_socket(field, value):
    def forbidden(*args):
        pytest.fail("identity mismatch constructed a socket")
    packet = probe(1, socket_factory=forbidden, **{field: value})
    assert packet["outcome"] == "identity_mismatch" and packet["process_identity_stable"]
    assert packet["identity_before"] == packet["identity_after"] == IDENTITY
    assert not packet["connected"] and not packet["marker_sent"] and not packet["ack_verified"]
    assert packet["local"] is packet["peer"] is packet["socket_inode"] is None
    assert packet["sent_bytes"] == packet["received_bytes"] == 0


def test_unavailable_identity_never_connects_or_reflects_error_text():
    def unavailable():
        raise ValueError("EXAMPLE-private-error-text")
    packet = probe(1, identity_reader=unavailable,
                   socket_factory=lambda *_: pytest.fail("missing identity constructed a socket"))
    assert packet["outcome"] == "identity_unavailable" and not packet["process_identity_stable"]
    assert packet["identity_before"] is packet["identity_after"] is None
    assert "private-error" not in json.dumps(packet)


def test_identity_work_consumes_the_same_whole_probe_deadline_without_network():
    clock = {"now": 10.0}
    def identity():
        clock["now"] += 3
        return example_identity()
    packet = probe(1, timeout=2, identity_reader=identity, monotonic=lambda: clock["now"],
                   socket_factory=lambda *_: pytest.fail("expired deadline constructed a socket"))
    assert packet["outcome"] == "timeout" and not packet["connected"] and packet["sent_bytes"] == 0


@pytest.mark.parametrize("namespace,current_ns,outcome", [
    (789, 3_000_000_000, "source_deadline"),
    (790, 1_000_000_000, "source_clock_mismatch"),
    (None, 1_000_000_000, "source_clock_unavailable"),
    (True, 1_000_000_000, "source_clock_unavailable"),
    (789, True, "source_clock_unavailable"),
])
def test_source_clock_guard_blocks_expired_startup_or_invalid_namespace_before_socket(namespace, current_ns, outcome):
    # EXAMPLE source clock seam: a fresh helper-relative 2s cannot extend an old source cutoff.
    packet = probe(1, timeout=2, expected_time_namespace_inode=789, deadline_monotonic_ns=2_000_000_000,
        time_namespace_reader=lambda: namespace, monotonic_ns=lambda: current_ns,
        socket_factory=lambda *_: pytest.fail("source guard failure constructed a socket"))
    assert packet["outcome"] == outcome and packet["deadline_guard_verified"] is False
    assert not packet["connected"] and not packet["marker_sent"]
    assert packet["sent_bytes"] == packet["received_bytes"] == 0


def test_missing_source_clock_does_not_reflect_error_or_connect():
    def unavailable():
        raise OSError("EXAMPLE private clock diagnostic")
    packet = probe(1, expected_time_namespace_inode=789, deadline_monotonic_ns=2_000_000_000,
        time_namespace_reader=unavailable, monotonic_ns=lambda: 1_000_000_000,
        socket_factory=lambda *_: pytest.fail("unavailable source clock constructed a socket"))
    assert packet["outcome"] == "source_clock_unavailable" and not packet["deadline_guard_verified"]
    assert "private clock" not in json.dumps(packet)


@pytest.mark.parametrize("fields", [
    {"expected_time_namespace_inode": 789}, {"deadline_monotonic_ns": 2_000_000_000},
    {"expected_time_namespace_inode": True, "deadline_monotonic_ns": 2_000_000_000},
    {"expected_time_namespace_inode": 789, "deadline_monotonic_ns": True},
    {"expected_time_namespace_inode": 0, "deadline_monotonic_ns": 2_000_000_000},
    {"expected_time_namespace_inode": 789, "deadline_monotonic_ns": 0},
    {"expected_time_namespace_inode": 789, "deadline_monotonic_ns": 2**63},
])
def test_source_guard_inputs_are_strict_and_paired(fields):
    with pytest.raises(ValueError):
        probe(1, **fields, socket_factory=lambda *_: pytest.fail("invalid source guard constructed a socket"))


def test_exact_time_namespace_parser_and_expired_actual_clock_do_not_connect(tmp_path):
    assert sender.time_namespace_inode(proc_root=tmp_path, readlink=lambda _: "time:[789]") == 789
    for value in ("net:[789]", "time:[0]", "time:[789]\n", "time:[18446744073709551616]"):
        with pytest.raises(ValueError):
            sender.time_namespace_inode(proc_root=tmp_path, readlink=lambda _, value=value: value)
    # Linux reads the actual own namespace; other platforms explicitly use the EXAMPLE proc seam.
    namespace_reader = sender.time_namespace_inode if sys.platform == "linux" else (
        lambda: sender.time_namespace_inode(proc_root=tmp_path, readlink=lambda _: "time:[789]"))
    actual_namespace = namespace_reader()
    packet = probe(1, timeout=2, expected_time_namespace_inode=actual_namespace, deadline_monotonic_ns=1,
        time_namespace_reader=namespace_reader, monotonic_ns=time.monotonic_ns,
        socket_factory=lambda *_: pytest.fail("expired actual monotonic cutoff constructed a socket"))
    assert packet["outcome"] == "source_deadline" and not packet["deadline_guard_verified"]
    assert not packet["connected"] and packet["sent_bytes"] == 0


def test_same_source_clock_guard_permits_only_owned_socket_metadata(tmp_path):
    with owned_receiver(tmp_path) as (port, _, _, results):
        packet = probe(port, expected_time_namespace_inode=789, deadline_monotonic_ns=time.monotonic_ns() + 1_000_000_000,
            time_namespace_reader=lambda: 789, monotonic_ns=time.monotonic_ns)
    assert packet["outcome"] == "completed" and packet["deadline_guard_verified"] is True
    assert packet["connection_guard_verified"] is True and packet["connected_at"] and packet["marker_sent_at"]
    assert results == [{"accepted_connections": 1, "receipts": 1}]


def test_source_cutoff_expires_during_connect_and_stops_before_marker(tmp_path):
    ticks = iter([1_000_000_000, 1_000_000_000, 3_000_000_000])
    with owned_receiver(tmp_path) as (port, _, _, results):
        packet = probe(port, expected_time_namespace_inode=789, deadline_monotonic_ns=2_000_000_000,
            time_namespace_reader=lambda: 789, monotonic_ns=lambda: next(ticks))
    assert packet["connected"] and not packet["marker_sent"] and packet["sent_bytes"] == 0
    assert packet["outcome"] == "source_deadline" and packet["deadline_guard_verified"] is False
    assert results == [{"accepted_connections": 1, "receipts": 0}]


def test_peer_eof_enotconn_does_not_erase_the_connected_socket_identity(tmp_path):
    sockets = []
    class PeerEOFNoPeer(socket.socket):
        peer_eof = False
        post_eof_peer_lookups = 0
        def recv(self, *args):
            value = super().recv(*args)
            self.peer_eof = not value
            return value
        def getpeername(self):
            if self.peer_eof:
                self.post_eof_peer_lookups += 1
                raise OSError(errno.ENOTCONN, "EXAMPLE peer completed FIN")
            return super().getpeername()
    def factory(*args):
        value = PeerEOFNoPeer(*args)
        sockets.append(value)
        return value
    with owned_receiver(tmp_path) as (port, _, _, results):
        packet = probe(port, socket_factory=factory, expected_time_namespace_inode=789,
            deadline_monotonic_ns=time.monotonic_ns() + 1_000_000_000,
            time_namespace_reader=lambda: 789, monotonic_ns=time.monotonic_ns)
    assert packet["outcome"] == "completed" and packet["socket_inode"] > 0
    assert packet["connection_guard_verified"] and packet["ack_verified"]
    assert sockets[0].peer_eof and sockets[0].post_eof_peer_lookups == 0
    assert sockets[0].fileno() == -1 and results == [{"accepted_connections": 1, "receipts": 1}]


def test_actual_receipt_precedes_auxiliary_ack_failure_and_keeps_connection_evidence(tmp_path):
    ack_attempts = []
    class AckFailureConnection:
        def __init__(self, connection):
            self.connection = connection
            self.ack_failed = False
        def __getattr__(self, name):
            return getattr(self.connection, name)
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            self.connection.close()
        def send(self, _value):
            ack_attempts.append(datetime.now(timezone.utc))
            self.ack_failed = True
            raise OSError("EXAMPLE private ACK failure")
        def getpeername(self):
            if self.ack_failed:
                raise OSError(errno.ENOTCONN, "EXAMPLE peer teardown after ACK")
            return self.connection.getpeername()
    class AckFailureListener(socket.socket):
        def accept(self):
            connection, peer = super().accept()
            return AckFailureConnection(connection), peer
    with owned_receiver(tmp_path, socket_factory=AckFailureListener) as (port, path, _, results):
        packet = probe(port, expected_time_namespace_inode=789,
            deadline_monotonic_ns=time.monotonic_ns() + 1_000_000_000,
            time_namespace_reader=lambda: 789, monotonic_ns=time.monotonic_ns)
    receipt = json.loads(path.read_text(encoding="utf-8").splitlines()[1])
    assert results == [{"accepted_connections": 1, "receipts": 1}] and ack_attempts
    assert packet["connected"] and packet["marker_sent"] and packet["connection_guard_verified"]
    assert packet["outcome"] == "ack_mismatch" and not packet["ack_verified"]
    assert packet["connected_at"] and packet["marker_sent_at"] and packet["socket_inode"] > 0
    assert receipt["received_bytes"] == 32 and receipt["ack_sent"] is False
    assert receipt["correlation_sha256"] == packet["correlation_sha256"]
    assert datetime.fromisoformat(receipt["observed_at"]) <= ack_attempts[0]
    assert "private ACK" not in json.dumps(packet) + json.dumps(receipt)


def test_source_cutoff_after_marker_does_not_erase_the_guarded_connection(tmp_path):
    ticks = iter([1_000_000_000] * 4 + [3_000_000_000])
    with owned_receiver(tmp_path) as (port, path, _, results):
        packet = probe(port, expected_time_namespace_inode=789, deadline_monotonic_ns=2_000_000_000,
            time_namespace_reader=lambda: 789, monotonic_ns=lambda: next(ticks))
    assert packet["connected"] and packet["marker_sent"] and packet["sent_bytes"] == 32
    assert packet["connection_guard_verified"] and packet["connected_at"] and packet["marker_sent_at"]
    assert packet["outcome"] == "source_deadline" and packet["deadline_guard_verified"] is False
    assert packet["socket_inode"] > 0 and not packet["ack_verified"]
    # A receiver can independently commit the marker even when ACK collection stops.
    assert results == [{"accepted_connections": 1, "receipts": 1}]
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[1])["received_bytes"] == 32


def test_changed_identity_after_connect_does_not_send_the_marker(tmp_path):
    states = iter([IDENTITY, {**IDENTITY, "uid": 1001}, IDENTITY])
    with owned_receiver(tmp_path) as (port, path, _, results):
        packet = probe(port, identity_reader=lambda: deepcopy(next(states)))
    assert packet["connected"] and packet["outcome"] == "identity_mismatch"
    assert not packet["marker_sent"] and packet["sent_bytes"] == 0
    assert results == [{"accepted_connections": 1, "receipts": 0}]
    assert len(path.read_text().splitlines()) == 1


@pytest.mark.parametrize("raw", [b"", b"a" * 31, b"g" * 32, b"A" * 32, b"a" * 33, b"a" * 32 + b"\n"])
def test_receiver_rejects_non_exact_marker_frames_without_persisting_raw_bytes(tmp_path, raw):
    with owned_receiver(tmp_path) as (port, path, _, results):
        with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
            connection.sendall(raw)
            connection.shutdown(socket.SHUT_WR)
            assert connection.recv(66) == b""
    assert results == [{"accepted_connections": 1, "receipts": 0}]
    assert len(path.read_text().splitlines()) == 1


def test_receiver_accept_count_is_bounded_even_for_invalid_connections(tmp_path):
    with owned_receiver(tmp_path, max_connections=2) as (port, path, _, results):
        for _ in range(2):
            with socket.create_connection(("127.0.0.1", port), timeout=1) as connection:
                connection.shutdown(socket.SHUT_WR)
                assert connection.recv(66) == b""
    assert results == [{"accepted_connections": 2, "receipts": 0}]
    assert len(path.read_text().splitlines()) == 1


@contextmanager
def owned_ack_server(reply, *, delay=0):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(1)
    errors = []
    def run():
        try:
            with listener.accept()[0] as connection:
                connection.settimeout(1)
                marker = bytearray()
                while len(marker) <= 32:
                    value = connection.recv(33 - len(marker))
                    if not value:
                        break
                    marker.extend(value)
                connection.sendall(reply)
                if delay:
                    threading.Event().wait(delay)
        except OSError:
            pass  # The owned probe may deliberately close a bounded/expired capture.
        except Exception as error:
            errors.append(error)
        finally:
            listener.close()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        thread.join(timeout=2)
        assert not thread.is_alive() and not errors


@pytest.mark.parametrize("reply", [b"0" * 64 + b"\n", b"f" * 64, b"x" * 1000])
def test_sender_requires_the_exact_bounded_digest_ack(reply):
    with owned_ack_server(reply) as port:
        packet = probe(port)
    assert packet["connected"] and packet["marker_sent"] and not packet["ack_verified"]
    assert packet["outcome"] == "ack_mismatch" and 0 <= packet["received_bytes"] <= 66
    assert "x" * 20 not in json.dumps(packet)


def test_sender_preserves_partial_ack_count_on_whole_deadline_timeout():
    with owned_ack_server(b"a" * 10, delay=0.2) as port:
        started = time.monotonic()
        packet = probe(port, timeout=0.08)
        elapsed = time.monotonic() - started
    assert elapsed < 0.5 and packet["outcome"] == "timeout"
    assert packet["marker_sent"] and not packet["ack_verified"] and packet["received_bytes"] == 10


@pytest.mark.parametrize("field,value", [
    ("ip", "example.invalid"), ("ip", "::1"), ("ip", "0.0.0.0"), ("ip", "224.0.0.1"), ("ip", True),
    ("port", 0), ("port", 65536), ("port", True), ("correlation", "a" * 31), ("correlation", "A" * 32),
    ("timeout", 0), ("timeout", 2.01), ("timeout", True), ("timeout", float("nan")),
    ("expected_uid", -1), ("expected_uid", True), ("expected_netns", 0), ("expected_netns", True),
])
def test_invalid_sender_inputs_are_rejected_before_any_socket(field, value):
    args = {"ip": "127.0.0.1", "port": 1, "correlation": CORRELATION, "timeout": 1,
            "expected_uid": IDENTITY["uid"], "expected_netns": IDENTITY["net_namespace_inode"], field: value}
    with pytest.raises(ValueError):
        sender.send_probe(**args, identity_reader=example_identity,
                          socket_factory=lambda *_: pytest.fail("invalid input constructed a socket"))


@pytest.mark.parametrize("module", [sender, receiver])
def test_proc_fixture_identity_and_socket_link_parsing_is_bounded_and_typed(tmp_path, module):
    (tmp_path / "self/ns").mkdir(parents=True)
    fields = ["S", *("0" for _ in range(18)), "456"]
    (tmp_path / "self/stat").write_text("123 (EXAMPLE proc ) name) " + " ".join(fields), encoding="utf-8")
    identity = module.process_identity(proc_root=tmp_path, pid=123, uid=1000, readlink=lambda _: "net:[789]")
    assert identity == IDENTITY
    assert module.socket_inode(SimpleNamespace(fileno=lambda: 9), proc_root=tmp_path,
                               readlink=lambda _: "socket:[987]") == 987
    with pytest.raises(ValueError):
        module.process_identity(proc_root=tmp_path, pid=123, uid=1000, readlink=lambda _: "net:[0]")
    with pytest.raises(ValueError):
        module.socket_inode(SimpleNamespace(fileno=lambda: 9), proc_root=tmp_path,
                            readlink=lambda _: "socket:[0]")
    (tmp_path / "self/stat").write_bytes(b"x" * 4097)
    with pytest.raises(ValueError):
        module.process_identity(proc_root=tmp_path, pid=123, uid=1000, readlink=lambda _: "net:[789]")


def test_receiver_log_creation_uses_exclusive_nofollow_components_and_mode_0600(tmp_path, monkeypatch):
    calls, closed = [], []
    def open_file(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = 100 + len(calls)
        calls.append((path, flags, mode, dir_fd, descriptor))
        return descriptor
    monkeypatch.setattr(receiver.os, "open", open_file)
    monkeypatch.setattr(receiver.os, "close", closed.append)
    monkeypatch.setattr(receiver.os, "fstat", lambda _: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_size=0))
    log = receiver.create_log(tmp_path / "EXAMPLE-new.jsonl")
    try:
        leaf = calls[-1]
        assert leaf[1] & os.O_EXCL and leaf[1] & receiver.NOFOLLOW and leaf[1] & os.O_APPEND
        assert leaf[2] == 0o600 and leaf[3] == calls[-2][4]
        assert all(flags & receiver.NOFOLLOW and flags & receiver.DIRECTORY for _, flags, _, _, _ in calls[:-1])
        assert closed == [call[4] for call in reversed(calls[:-1])]
    finally:
        log.close()


def test_exclusive_log_failure_never_replaces_existing_bytes(tmp_path, monkeypatch):
    path = tmp_path / "EXAMPLE-existing.jsonl"
    original = b"EXAMPLE original evidence\n"
    path.write_bytes(original)
    closed, calls = [], []
    def open_file(part, flags, mode=0o777, *, dir_fd=None):
        calls.append((part, flags))
        if part == path.name:
            assert flags & os.O_EXCL and flags & receiver.NOFOLLOW
            raise FileExistsError
        return 100 + len(calls)
    monkeypatch.setattr(receiver.os, "open", open_file)
    monkeypatch.setattr(receiver.os, "close", closed.append)
    with pytest.raises(FileExistsError):
        receiver.create_log(path)
    assert path.read_bytes() == original and len(closed) == len(calls) - 1


def test_receiver_append_limit_preserves_already_recorded_metadata(tmp_path, monkeypatch):
    path = tmp_path / "EXAMPLE-bounded.jsonl"
    log = example_log(path)
    try:
        log.append({"kind": "startup", "correlation_sha256": None})
        original = path.read_bytes()
        monkeypatch.setattr(receiver, "MAX_LOG_BYTES", len(original))
        with pytest.raises(ValueError, match="LOG_BOUND"):
            log.append({"kind": "receipt", "correlation_sha256": "a" * 64})
        assert path.read_bytes() == original
    finally:
        log.close()


@pytest.mark.parametrize("module,code", [(sender, "EGRESS_PROBE_UNSUPPORTED_PLATFORM"),
                                        (receiver, "EGRESS_RECEIVER_UNSUPPORTED_PLATFORM")])
def test_unsupported_platform_main_never_creates_socket_or_file(monkeypatch, capsys, module, code):
    monkeypatch.setattr(module.sys, "platform", "EXAMPLE-unsupported")
    monkeypatch.setattr(module.socket, "socket", lambda *_: pytest.fail("unsupported platform created socket"))
    if module is receiver:
        monkeypatch.setattr(module, "create_log", lambda *_: pytest.fail("unsupported platform created log"))
    assert module.main(["EXAMPLE-invalid-input"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == code + "\n"


def test_sender_main_requires_all_eight_guarded_arguments(monkeypatch, capsys):
    monkeypatch.setattr(sender.sys, "platform", "linux")
    monkeypatch.setattr(sender, "send_probe", lambda *_args, **_kwargs: pytest.fail("unguarded argv reached sender"))
    assert sender.main(["127.0.0.1", "18001", CORRELATION, "1", "1000", "789"]) == 2
    assert capsys.readouterr().err == "EGRESS_PROBE_INPUT_INVALID\n"


@pytest.mark.parametrize("module", [sender, receiver])
def test_standalone_python_c_source_uses_no_guard_import_and_fixed_diagnostics(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    process = subprocess.run([sys.executable, "-I", "-u", "-c", source, "EXAMPLE-invalid-input"],
                             capture_output=True, timeout=3, check=False)
    expected = ("EGRESS_PROBE_INPUT_INVALID" if module is sender else "EGRESS_RECEIVER_FAILED") if sys.platform == "linux" else (
        "EGRESS_PROBE_UNSUPPORTED_PLATFORM" if module is sender else "EGRESS_RECEIVER_UNSUPPORTED_PLATFORM")
    assert process.returncode == 2 and process.stdout == b""
    assert process.stderr.decode().strip() == expected
    assert b"EXAMPLE-invalid-input" not in process.stderr and b"Traceback" not in process.stderr
