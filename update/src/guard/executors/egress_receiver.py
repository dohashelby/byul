"""소유 시험 전용 TCP 수신처. stdlib만 쓰며 marker 원문을 보존하지 않는다."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time


SCHEMA = "guard-owned-egress-receiver/1"
OUTPUT_PATH = "/guard-observations/egress.jsonl"
READY = "EGRESS_RECEIVER_READY"
MAX_LISTEN_SECONDS = 120
MAX_CONNECTIONS = 8
MAX_LOG_BYTES = 16384
MAX_RECORD_BYTES = 2048
MARKER_BYTES = 32
ACK_BYTES = 65
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0o400000)
DIRECTORY = getattr(os, "O_DIRECTORY", 0o200000)
_HEX = re.compile(rb"[0-9a-f]{32}")
_IDENTITY_KEYS = {"pid", "start_time_ticks", "uid", "net_namespace_inode"}


def _identity(value):
    if (type(value) is not dict or set(value) != _IDENTITY_KEYS
            or any(type(value[key]) is not int for key in _IDENTITY_KEYS)
            or not 0 < value["pid"] <= 2**31 - 1
            or not 0 < value["start_time_ticks"] <= 2**63 - 1
            or not 0 <= value["uid"] <= 2**32 - 1
            or not 0 < value["net_namespace_inode"] <= 2**64 - 1):
        raise ValueError("EGRESS_IDENTITY_UNAVAILABLE")
    return dict(value)


def process_identity(*, proc_root="/proc", pid=None, uid=None, readlink=None):
    current_pid = os.getpid() if pid is None else pid
    current_uid = os.geteuid() if uid is None else uid
    root = Path(proc_root)
    def start():
        with (root / "self/stat").open("rb") as stream:
            raw = stream.read(4097)
        if not 0 < len(raw) <= 4096:
            raise ValueError("EGRESS_IDENTITY_UNAVAILABLE")
        text = raw.decode("utf-8", "strict")
        prefix, tail = text.split("(", 1)[0].strip(), text.rsplit(")", 1)[1].split()
        if not prefix.isascii() or not prefix.isdigit() or int(prefix) != current_pid:
            raise ValueError("EGRESS_IDENTITY_UNAVAILABLE")
        value = tail[19]
        if not value.isascii() or not value.isdigit():
            raise ValueError("EGRESS_IDENTITY_UNAVAILABLE")
        return int(value)
    before = start()
    link = (os.readlink if readlink is None else readlink)(root / "self/ns/net")
    match = re.fullmatch(r"net:\[([1-9][0-9]{0,19})\]", link)
    if match is None or before != start():
        raise ValueError("EGRESS_IDENTITY_UNAVAILABLE")
    return _identity({"pid": current_pid, "start_time_ticks": before, "uid": current_uid,
                      "net_namespace_inode": int(match[1])})


def socket_inode(sock, *, proc_root="/proc", readlink=None):
    descriptor = sock.fileno()
    if type(descriptor) is not int or descriptor < 0:
        raise ValueError("EGRESS_SOCKET_IDENTITY_UNAVAILABLE")
    link = (os.readlink if readlink is None else readlink)(Path(proc_root) / "self/fd" / str(descriptor))
    match = re.fullmatch(r"socket:\[([1-9][0-9]{0,19})\]", link)
    if match is None or int(match[1]) > 2**64 - 1:
        raise ValueError("EGRESS_SOCKET_IDENTITY_UNAVAILABLE")
    return int(match[1])


def endpoint(value, *, listening=False):
    if (type(value) not in (tuple, list) or len(value) != 2 or type(value[0]) is not str
            or type(value[1]) is not int or not 1 <= value[1] <= 65535):
        raise ValueError("EGRESS_ENDPOINT_UNAVAILABLE")
    address = ipaddress.IPv4Address(value[0])
    if not listening and address.is_unspecified:
        raise ValueError("EGRESS_ENDPOINT_UNAVAILABLE")
    return {"ip": str(address), "port": value[1]}


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class BoundedLog:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.bytes = 0

    def append(self, record):
        raw = (json.dumps(record, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode()
        before = os.fstat(self.descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_size != self.bytes
                or len(raw) > MAX_RECORD_BYTES or self.bytes + len(raw) > MAX_LOG_BYTES):
            raise ValueError("EGRESS_RECEIVER_LOG_BOUND")
        written = 0
        while written < len(raw):
            count = os.write(self.descriptor, raw[written:])
            if count <= 0:
                raise OSError("EGRESS_RECEIVER_LOG_UNAVAILABLE")
            written += count
        self.bytes += written
        os.fsync(self.descriptor)

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None


def create_log(path=OUTPUT_PATH):
    """O_EXCL·0600·각 경로 구성요소 O_NOFOLLOW로 새 regular file만 연다."""
    destination = Path(path)
    if not destination.is_absolute() or ".." in destination.parts or not destination.name:
        raise ValueError("EGRESS_RECEIVER_LOG_PATH_INVALID")
    if str(destination) == OUTPUT_PATH:
        try:
            os.mkdir(destination.parent, 0o700)
        except FileExistsError:
            pass
    descriptors, descriptor = [], None
    try:
        current = os.open(destination.anchor, os.O_RDONLY | DIRECTORY | NOFOLLOW)
        descriptors.append(current)
        for part in destination.parent.parts[1:]:
            current = os.open(part, os.O_RDONLY | DIRECTORY | NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(destination.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | NOFOLLOW
                             | getattr(os, "O_BINARY", 0),
                             0o600, dir_fd=current)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != 0:
            raise ValueError("EGRESS_RECEIVER_LOG_INVALID")
        result = BoundedLog(descriptor)
        descriptor = None
        return result
    finally:
        if descriptor is not None:
            os.close(descriptor)
        for item in reversed(descriptors):
            os.close(item)


def _record(kind, identity, sock, inode_reader, *, digest=None, received=0, ack=False, observed_at=None):
    return {"schema": SCHEMA, "kind": kind, "identity": _identity(identity),
            "local": endpoint(sock.getsockname(), listening=kind == "startup"),
            "peer": None if kind == "startup" else endpoint(sock.getpeername()),
            "socket_inode": inode_reader(sock), "observed_at": _timestamp() if observed_at is None else observed_at,
            "correlation_sha256": digest, "received_bytes": received, "ack_sent": ack}


def serve(port, *, path=OUTPUT_PATH, listen_seconds=MAX_LISTEN_SECONDS, max_connections=MAX_CONNECTIONS,
          bind_ip="0.0.0.0", identity_reader=None, socket_factory=None, socket_inode_reader=None,
          log_factory=None, ready=None, monotonic=None):
    """범위가 고정된 수신 metadata만 기록한다. port=0 등 함수 seam은 EXAMPLE 시험용이다."""
    address = ipaddress.IPv4Address(bind_ip)
    if (type(port) is not int or not 0 <= port <= 65535
            or type(listen_seconds) not in (int, float) or not math.isfinite(listen_seconds)
            or not 0 < listen_seconds <= MAX_LISTEN_SECONDS
            or type(max_connections) is not int or not 1 <= max_connections <= MAX_CONNECTIONS
            or address.is_multicast):
        raise ValueError("EGRESS_RECEIVER_INPUT_INVALID")
    identity_reader = process_identity if identity_reader is None else identity_reader
    socket_factory = socket.socket if socket_factory is None else socket_factory
    inode_reader = socket_inode if socket_inode_reader is None else socket_inode_reader
    log_factory = create_log if log_factory is None else log_factory
    clock = time.monotonic if monotonic is None else monotonic
    deadline = clock() + listen_seconds
    before = _identity(identity_reader())
    log, listener = None, None
    accepted, receipts = 0, 0
    try:
        log = log_factory(path)
        listener = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind((str(address), port))
        listener.listen(MAX_CONNECTIONS)
        if _identity(identity_reader()) != before:
            raise ValueError("EGRESS_RECEIVER_IDENTITY_CHANGED")
        startup = _record("startup", before, listener, inode_reader)
        if type(startup["socket_inode"]) is not int or not 0 < startup["socket_inode"] <= 2**64 - 1:
            raise ValueError("EGRESS_RECEIVER_IDENTITY_UNAVAILABLE")
        log.append(startup)
        if ready is not None:
            ready(dict(startup))
        while accepted < max_connections and clock() < deadline:
            listener.settimeout(min(0.1, max(0.001, deadline - clock())))
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            accepted += 1
            with connection:
                connection_deadline = min(deadline, clock() + 2)
                def remaining():
                    value = connection_deadline - clock()
                    if value <= 0:
                        raise TimeoutError
                    return value
                try:
                    if _identity(identity_reader()) != before:
                        raise ValueError("EGRESS_RECEIVER_IDENTITY_CHANGED")
                    initial = _record("receipt", before, connection, inode_reader)
                    if type(initial["socket_inode"]) is not int or not 0 < initial["socket_inode"] <= 2**64 - 1:
                        raise ValueError("EGRESS_RECEIVER_IDENTITY_UNAVAILABLE")
                    marker = bytearray()
                    while len(marker) <= MARKER_BYTES:
                        connection.settimeout(remaining())
                        value = connection.recv(MARKER_BYTES + 1 - len(marker))
                        if not value:
                            break
                        marker.extend(value)
                    if _HEX.fullmatch(bytes(marker)) is None:
                        continue
                    received_at = _timestamp()  # The receipt precedes all auxiliary ACK work.
                    digest = hashlib.sha256(marker).hexdigest()
                    received_identity = _identity(identity_reader())
                    receipt = _record("receipt", received_identity, connection, inode_reader, digest=digest,
                                      received=MARKER_BYTES, observed_at=received_at)
                    if (received_identity != before
                            or any(receipt[key] != initial[key] for key in ("local", "peer", "socket_inode"))):
                        continue
                    receipt_descriptor = connection.fileno()
                    ack = digest.encode("ascii") + b"\n"
                    written = 0
                    try:
                        while written < ACK_BYTES:
                            connection.settimeout(remaining())
                            count = connection.send(ack[written:])
                            if not count:
                                raise OSError
                            written += count
                    except (OSError, TimeoutError):
                        pass  # Received bytes remain an observation if sending the ACK failed.
                    after = _identity(identity_reader())
                    # ACK failure/peer teardown can remove getpeername's live
                    # tuple. The receive-side tuple was checked on both sides
                    # of reading the marker; retain that historical metadata.
                    if (after != before or connection.fileno() != receipt_descriptor
                            or inode_reader(connection) != receipt["socket_inode"]):
                        continue
                    receipt["ack_sent"] = written == ACK_BYTES
                    log.append(receipt)
                    receipts += 1
                except (OSError, ValueError, TypeError, IndexError, UnicodeError):
                    continue
        return {"accepted_connections": accepted, "receipts": receipts}
    finally:
        if listener is not None:
            listener.close()
        if log is not None:
            log.close()


def main(argv=None):
    if sys.platform != "linux":
        print("EGRESS_RECEIVER_UNSUPPORTED_PLATFORM", file=sys.stderr)
        return 2
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 1 or not 1 <= int(args[0]) <= 65535:
            raise ValueError("EGRESS_RECEIVER_INPUT_INVALID")
        serve(int(args[0]), ready=lambda _: print(READY, flush=True))
    except (OSError, ValueError, TypeError, IndexError, UnicodeError):
        print("EGRESS_RECEIVER_FAILED", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
