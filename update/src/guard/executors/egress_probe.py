"""명시 all-process 정책용 단일 TCP 송신 helper. worker/SDK 송신 증명이 아니다.

standalone stdlib 전용이다. 본문·marker·환경·예외 원문을 출력하지 않는다.
"""
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
import sys
import time


SCHEMA = "guard-owned-egress-probe/1"
MAX_PACKET_BYTES = 65536
MAX_TIMEOUT = 2.0
MARKER_BYTES = 32
ACK_BYTES = 65
_HEX = re.compile(rb"[0-9a-f]{32}")
_IDENTITY_KEYS = {"pid", "start_time_ticks", "uid", "net_namespace_inode"}


class SourceGuardError(Exception):
    """원문을 포함하지 않는 고정 source-clock 실패 코드."""


def time_namespace_inode(*, proc_root="/proc", readlink=None):
    link = (os.readlink if readlink is None else readlink)(Path(proc_root) / "self/ns/time")
    match = re.fullmatch(r"time:\[([1-9][0-9]{0,19})\]", link)
    if match is None or int(match[1]) > 2**64 - 1:
        raise ValueError("EGRESS_SOURCE_CLOCK_UNAVAILABLE")
    return int(match[1])


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
    """같은 process의 PID/start·실효 UID·net namespace만 읽는다."""
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


def endpoint(value):
    if (type(value) not in (tuple, list) or len(value) != 2 or type(value[0]) is not str
            or type(value[1]) is not int or not 1 <= value[1] <= 65535):
        raise ValueError("EGRESS_ENDPOINT_UNAVAILABLE")
    return {"ip": str(ipaddress.IPv4Address(value[0])), "port": value[1]}


def _timestamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def send_probe(ip, port, correlation, timeout, expected_uid, expected_netns, *, identity_reader=None,
               socket_factory=None, socket_inode_reader=None, monotonic=None,
               expected_time_namespace_inode=None, deadline_monotonic_ns=None,
               time_namespace_reader=None, monotonic_ns=None):
    """고유 TCP 송신만 관측한다. guard 생략 함수 seam은 EXAMPLE 시험 전용이다."""
    address = ipaddress.IPv4Address(ip)
    if (type(ip) is not str or address.is_unspecified or address.is_multicast or str(address) == "255.255.255.255"
            or type(port) is not int or not 1 <= port <= 65535
            or type(correlation) is not str or _HEX.fullmatch(correlation.encode("ascii", "strict")) is None
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= MAX_TIMEOUT
            or type(expected_uid) is not int or not 0 <= expected_uid <= 2**32 - 1
            or type(expected_netns) is not int or not 0 < expected_netns <= 2**64 - 1):
        raise ValueError("EGRESS_PROBE_INPUT_INVALID")
    source_guard_enabled = expected_time_namespace_inode is not None or deadline_monotonic_ns is not None
    if source_guard_enabled and (type(expected_time_namespace_inode) is not int
            or not 0 < expected_time_namespace_inode <= 2**64 - 1
            or type(deadline_monotonic_ns) is not int or not 0 < deadline_monotonic_ns <= 2**63 - 1):
        raise ValueError("EGRESS_PROBE_INPUT_INVALID")
    identity_reader = process_identity if identity_reader is None else identity_reader
    socket_factory = socket.socket if socket_factory is None else socket_factory
    socket_inode_reader = socket_inode if socket_inode_reader is None else socket_inode_reader
    clock = time.monotonic if monotonic is None else monotonic
    source_namespace = time_namespace_inode if time_namespace_reader is None else time_namespace_reader
    source_clock = time.monotonic_ns if monotonic_ns is None else monotonic_ns
    marker = correlation.encode("ascii")
    digest = hashlib.sha256(marker).hexdigest()
    result = {"schema": SCHEMA, "identity_before": None, "identity_after": None,
              "local": None, "peer": None, "socket_inode": None, "correlation_sha256": digest,
              "connected": False, "marker_sent": False, "ack_verified": False,
              "sent_bytes": 0, "received_bytes": 0, "outcome": "identity_unavailable",
              "observed_at": None, "process_identity_stable": False, "deadline_guard_verified": False,
              "connected_at": None, "marker_sent_at": None, "connection_guard_verified": False}
    deadline = clock() + timeout
    sock, initial_socket = None, None
    def remaining():
        if source_guard_enabled:
            result["deadline_guard_verified"] = False
            try:
                actual_namespace = source_namespace()
                actual_ns = source_clock()
                if (type(actual_namespace) is not int or not 0 < actual_namespace <= 2**64 - 1
                        or type(actual_ns) is not int or not 0 <= actual_ns <= 2**63 - 1):
                    raise ValueError("EGRESS_SOURCE_CLOCK_UNAVAILABLE")
            except (OSError, ValueError, TypeError, IndexError, UnicodeError, AttributeError):
                raise SourceGuardError("source_clock_unavailable") from None
            if actual_namespace != expected_time_namespace_inode:
                raise SourceGuardError("source_clock_mismatch")
            if actual_ns >= deadline_monotonic_ns:
                raise SourceGuardError("source_deadline")
            result["deadline_guard_verified"] = True
        value = deadline - clock()
        if value <= 0:
            raise TimeoutError
        if source_guard_enabled:
            value = min(value, (deadline_monotonic_ns - actual_ns) / 1_000_000_000)
        return value
    try:
        result["identity_before"] = _identity(identity_reader())
        before = result["identity_before"]
        if before["uid"] != expected_uid or before["net_namespace_inode"] != expected_netns:
            result["outcome"] = "identity_mismatch"
            return result
        remaining()
        sock = socket_factory(socket.AF_INET, socket.SOCK_STREAM)
        inode = socket_inode_reader(sock)
        descriptor = sock.fileno()
        if (type(inode) is not int or not 0 < inode <= 2**64 - 1
                or type(descriptor) is not int or descriptor < 0):
            raise ValueError("EGRESS_SOCKET_IDENTITY_UNAVAILABLE")
        sock.settimeout(remaining())
        sock.connect((str(address), port))  # Numeric IPv4 only: no DNS or fallback connection.
        result.update(connected=True, local=endpoint(sock.getsockname()), peer=endpoint(sock.getpeername()),
                      socket_inode=inode, outcome="transport_error")
        initial_socket = (descriptor, inode)
        if result["peer"] != {"ip": str(address), "port": port}:
            raise ValueError("EGRESS_ENDPOINT_UNAVAILABLE")
        if _identity(identity_reader()) != before:
            result["outcome"] = "identity_mismatch"
            return result
        if (sock.fileno(), socket_inode_reader(sock)) != initial_socket:
            raise ValueError("EGRESS_SOCKET_IDENTITY_UNAVAILABLE")
        remaining()
        result["connected_at"] = _timestamp()
        result["connection_guard_verified"] = source_guard_enabled and result["deadline_guard_verified"]
        while result["sent_bytes"] < MARKER_BYTES:
            sock.settimeout(remaining())
            count = sock.send(marker[result["sent_bytes"]:])
            if not count:
                raise OSError
            result["sent_bytes"] += count
        result["marker_sent"] = True
        result["marker_sent_at"] = _timestamp()
        sock.shutdown(socket.SHUT_WR)
        received = bytearray()
        while True:
            sock.settimeout(remaining())
            value = sock.recv(ACK_BYTES + 1 - len(received))
            if not value:
                break
            received.extend(value)
            result["received_bytes"] = len(received)
            if len(received) > ACK_BYTES:
                break
        result["received_bytes"] = len(received)
        remaining()
        result["ack_verified"] = bytes(received) == digest.encode("ascii") + b"\n"
        result["outcome"] = "completed" if result["ack_verified"] else "ack_mismatch"
    except SourceGuardError as error:
        result["outcome"] = str(error)
    except (TimeoutError, socket.timeout):
        result["outcome"] = "timeout"
    except ConnectionRefusedError:
        result["outcome"] = "connect_refused"
    except (OSError, ValueError, TypeError, IndexError, UnicodeError):
        result["outcome"] = "identity_unavailable" if not result["connected"] else "transport_error"
    finally:
        if sock is not None:
            try:
                # Linux may return ENOTCONN from getpeername after the peer's
                # final FIN. The historical connected tuple remains evidence;
                # only the still-open descriptor/inode is checked here.
                final_socket = (sock.fileno(), socket_inode_reader(sock))
                if final_socket != initial_socket:
                    result["socket_inode"] = None
            except (OSError, ValueError, TypeError):
                result["socket_inode"] = None
            try:
                sock.close()
            except OSError:
                pass
        try:
            result["identity_after"] = _identity(identity_reader())
        except (OSError, ValueError, TypeError, IndexError, UnicodeError):
            result["identity_after"] = None
        result["process_identity_stable"] = (result["identity_before"] is not None
            and result["identity_before"] == result["identity_after"])
        result["observed_at"] = _timestamp()
    return result


def main(argv=None):
    if sys.platform != "linux":
        print("EGRESS_PROBE_UNSUPPORTED_PLATFORM", file=sys.stderr)
        return 2
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) != 8:
            raise ValueError("EGRESS_PROBE_INPUT_INVALID")
        value = send_probe(args[0], int(args[1]), args[2], float(args[3]), int(args[4]), int(args[5]),
                           expected_time_namespace_inode=int(args[6]), deadline_monotonic_ns=int(args[7]))
        raw = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_PACKET_BYTES:
            raise ValueError("EGRESS_PROBE_PACKET_BOUND")
    except (OSError, ValueError, TypeError, IndexError, UnicodeError):
        print("EGRESS_PROBE_INPUT_INVALID", file=sys.stderr)
        return 2
    print(raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
