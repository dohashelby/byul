"""Exact read-only SOCK_DIAG for a TCP connection registered by its owner."""

from __future__ import annotations

import errno
import ipaddress
import os
import socket
import stat
import struct
import time
from dataclasses import asdict, dataclass


SO_COOKIE = getattr(socket, "SO_COOKIE", 57)
NETLINK_SOCK_DIAG = 4
SOCK_DIAG_BY_FAMILY = 20
NO_COOKIE = (1 << 64) - 1
HEADER = struct.Struct("=IHHII")
MEMORY_NAMES = (
    "rmem_alloc", "rcvbuf", "wmem_alloc", "sndbuf", "fwd_alloc",
    "wmem_queued", "optmem", "backlog", "drops",
)
SKMEM_FIELDS = {
    "rmem_alloc": "receive allocation in bytes", "rcvbuf": "configured receive capacity in bytes",
    "wmem_alloc": "write allocation in bytes", "sndbuf": "configured send capacity in bytes",
    "fwd_alloc": "forward allocation reserve in bytes", "wmem_queued": "queued write allocation in bytes",
    "optmem": "socket option allocation in bytes", "backlog": "backlog accounting in bytes",
    "drops": "dropped packet counter",
}
TCP_STATES = {
    1: "ESTABLISHED", 2: "SYN_SENT", 3: "SYN_RECV", 4: "FIN_WAIT_1",
    5: "FIN_WAIT_2", 6: "TIME_WAIT", 7: "CLOSE", 8: "CLOSE_WAIT",
    9: "LAST_ACK", 10: "LISTEN", 11: "CLOSING", 12: "NEW_SYN_RECV",
}
CLOSING_FULL_SOCKETS = frozenset(("FIN_WAIT_1", "FIN_WAIT_2", "CLOSE", "CLOSE_WAIT", "LAST_ACK", "CLOSING"))


class DiagnosticProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class SocketIdentity:
    netns: int
    local_address: str
    local_port: int
    peer_address: str
    peer_port: int
    cookie: int
    inode: int
    ifindex: int = 0

    def __post_init__(self):
        a = ipaddress.ip_address(self.local_address)
        b = ipaddress.ip_address(self.peer_address)
        if (a.version != b.version or a.is_unspecified or b.is_unspecified
                or a.is_multicast or b.is_multicast or "%" in self.local_address
                or "%" in self.peer_address or self.netns <= 0 or self.inode <= 0
                or not 1 <= self.local_port <= 65535 or not 1 <= self.peer_port <= 65535
                or not 0 < self.cookie < NO_COOKIE or not 0 <= self.ifindex <= (1 << 32) - 1):
            raise ValueError("socket identity is not an exact connected endpoint")
        object.__setattr__(self, "local_address", str(a))
        object.__setattr__(self, "peer_address", str(b))

    def json(self):
        return asdict(self)

    @property
    def family(self):
        return socket.AF_INET6 if ":" in self.local_address else socket.AF_INET


def own_netns():
    return os.stat("/proc/self/ns/net").st_ino


def identify_open_socket(sock):
    """Read the existing descriptor twice. The caller retains sole ownership."""
    fd = sock.fileno()
    first = os.fstat(fd)
    if not stat.S_ISSOCK(first.st_mode):
        raise ValueError("descriptor does not refer to a socket")
    local = sock.getsockname()
    peer = sock.getpeername()
    cookie, = struct.unpack("=Q", sock.getsockopt(socket.SOL_SOCKET, SO_COOKIE, 8))
    second = os.fstat(fd)
    if (first.st_dev, first.st_ino, fd) != (second.st_dev, second.st_ino, sock.fileno()):
        raise ValueError("descriptor identity changed during registration")
    ifindex = (peer[3] or local[3]) if len(local) == 4 else 0
    return SocketIdentity(own_netns(), local[0], local[1], peer[0], peer[1], cookie, first.st_ino, ifindex)


def exact_request(identity, *, sequence, cookie):
    # A tuple-only retry is still an exact operation. No netlink dump flag.
    a = ipaddress.ip_address(identity.local_address).packed.ljust(16, b"\0")
    b = ipaddress.ip_address(identity.peer_address).packed.ljust(16, b"\0")
    extensions = (1 << (1 - 1)) | (1 << (7 - 1))
    req = (struct.pack("=BBBBI", identity.family, socket.IPPROTO_TCP, extensions, 0, 0xFFFFFFFF)
           + struct.pack("!HH", identity.local_port, identity.peer_port) + a + b
           + struct.pack("=III", identity.ifindex, cookie & 0xFFFFFFFF, cookie >> 32))
    return HEADER.pack(HEADER.size + len(req), SOCK_DIAG_BY_FAMILY, 1, sequence, 0) + req


def decode_message(payload):
    if len(payload) < 72:
        raise DiagnosticProtocolError("short inet_diag_msg")
    family, state = payload[:2]
    if family not in (socket.AF_INET, socket.AF_INET6):
        raise DiagnosticProtocolError("unexpected address family")
    n = 4 if family == socket.AF_INET else 16
    local_port, peer_port = struct.unpack_from("!HH", payload, 4)
    interface, cookie_low, cookie_high = struct.unpack_from("=III", payload, 40)
    expires_ms, rq, wq, _, inode = struct.unpack_from("=IIIII", payload, 52)
    attrs = {}
    offset = 72
    while offset < len(payload):
        if len(payload) - offset < 4:
            raise DiagnosticProtocolError("short netlink attribute")
        size, kind = struct.unpack_from("=HH", payload, offset)
        if size < 4 or offset + size > len(payload):
            raise DiagnosticProtocolError("invalid netlink attribute size")
        if kind in attrs:
            raise DiagnosticProtocolError("duplicate netlink attribute")
        attrs[kind] = payload[offset + 4:offset + size]
        offset += (size + 3) & ~3
    memory = None
    if 7 in attrs:
        if len(attrs[7]) % 4:
            raise DiagnosticProtocolError("invalid socket memory attribute width")
        words = struct.unpack(f"={len(attrs[7]) // 4}I", attrs[7])
        memory = dict(zip(MEMORY_NAMES, words))
    legacy = None
    if 1 in attrs:
        if len(attrs[1]) != 16:
            raise DiagnosticProtocolError("invalid legacy memory attribute width")
        legacy = dict(zip(("rmem", "wmem", "fmem", "tmem"), struct.unpack("=4I", attrs[1])))
    return {
        "family": family, "state": TCP_STATES.get(state, f"STATE_{state}"),
        "local_address": socket.inet_ntop(family, payload[8:8 + n]), "local_port": local_port,
        "peer_address": socket.inet_ntop(family, payload[24:24 + n]), "peer_port": peer_port,
        "interface": interface, "cookie": (cookie_high << 32) | cookie_low, "inode": inode,
        "expires_ms": expires_ms, "raw_receive_queue": rq, "raw_send_queue": wq,
        "skmem": memory, "meminfo": legacy,
    }


def decode_reply(data, sequence):
    if len(data) < HEADER.size:
        raise DiagnosticProtocolError("short netlink envelope")
    length, kind, flags, received_sequence, _ = HEADER.unpack_from(data)
    if (length < HEADER.size or length > len(data) or len(data) > ((length + 3) & ~3)
            or received_sequence != sequence or flags & 2):
        raise DiagnosticProtocolError("unexpected multipart or unmatched netlink reply")
    payload = data[HEADER.size:length]
    if kind == 2 and len(payload) >= 4:
        code, = struct.unpack_from("=i", payload)
        if code < 0:
            raise OSError(-code, os.strerror(-code))
    if kind != SOCK_DIAG_BY_FAMILY:
        raise DiagnosticProtocolError("unexpected netlink reply kind")
    return decode_message(payload)


class ExactDiagnostic:
    def __init__(self, timeout=0.08):
        self.netns = own_netns()
        sock = socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_SOCK_DIAG)
        try:
            sock.bind((0, 0))
            sock.settimeout(timeout)
        except BaseException:
            sock.close()
            raise
        self.socket = sock
        self.sequence = 0

    def close(self):
        self.socket.close()

    def query(self, identity, cookie):
        if identity.netns != self.netns or own_netns() != self.netns:
            raise PermissionError(errno.EXDEV, "diagnostic namespace mismatch")
        self.sequence += 1
        self.socket.sendto(exact_request(identity, sequence=self.sequence, cookie=cookie), (0, 0))
        until = time.monotonic() + (self.socket.gettimeout() or 0.08)
        while time.monotonic() < until:
            data, _, flags, peer = self.socket.recvmsg(65536)
            if peer != (0, 0) or flags & socket.MSG_TRUNC or len(data) < HEADER.size:
                raise DiagnosticProtocolError("invalid diagnostic sender or truncated packet")
            if HEADER.unpack_from(data)[3] == self.sequence:
                return decode_reply(data, self.sequence)
        raise TimeoutError("exact diagnostic reply timed out")


def _same_tuple(identity, row):
    return (row["family"] == identity.family
            and (row["local_address"], row["local_port"], row["peer_address"], row["peer_port"])
            == (identity.local_address, identity.local_port, identity.peer_address, identity.peer_port)
            and (identity.ifindex == 0 or row["interface"] == identity.ifindex))


def _inspect_match(identity, row):
    if row["state"] == "LISTEN":
        return {"status": "listener_reply"}
    if not _same_tuple(identity, row):
        return {"status": "different_tuple"}
    if row["cookie"] != identity.cookie:
        return {"status": "time_wait_identity_changed" if row["state"] == "TIME_WAIT" else "identity_changed"}
    if row["state"] == "TIME_WAIT":
        if row["inode"] != 0:
            return {"status": "identity_uncertain"}
        # Linux fills the TIME_WAIT diagnostic inode/rqueue/wqueue with
        # constants. An implementation-supplied extension is not live skmem.
        return {"status": "time_wait", "state": "TIME_WAIT", "expiry_ms": row["expires_ms"],
                "receive_queue_bytes": None, "send_queue_bytes": None, "skmem_bytes": None,
                "skmem_capacity_bytes": None, "skmem_drops_packets": None, "meminfo_bytes": None}
    # A full TCP socket can outlive the file which was attached at
    # registration. Linux sock_i_ino then reports zero. Preserve the verified
    # tuple and matching cookie, and distinguish this from a different inode.
    detached = row["inode"] == 0 and row["state"] in CLOSING_FULL_SOCKETS
    if not detached and row["inode"] != identity.inode:
        return {"status": "identity_uncertain", "reason": "unexpected_full_socket_inode", "state": row["state"]}
    memory = row["skmem"]
    capacities = {"rcvbuf", "sndbuf"}
    return {"status": "present", "state": row["state"], "receive_queue_bytes": row["raw_receive_queue"],
            "send_queue_bytes": row["raw_send_queue"],
            "skmem_bytes": {k: v for k, v in memory.items() if k not in capacities | {"drops"}} if memory is not None else None,
            "skmem_capacity_bytes": {k: v for k, v in memory.items() if k in capacities} if memory is not None else None,
            "skmem_drops_packets": memory.get("drops") if memory is not None else None, "meminfo_bytes": row["meminfo"],
            "fd_association": "detached" if detached else "registered_inode"}


def _error(error):
    if isinstance(error, DiagnosticProtocolError):
        return {"status": "protocol_error", "detail": str(error)}
    if isinstance(error, TimeoutError):
        return {"status": "timeout"}
    if error.errno in (errno.EACCES, errno.EPERM, errno.EXDEV):
        return {"status": "access_error", "errno": error.errno}
    return {"status": "query_error", "errno": error.errno}


def observe(query, identity, *, now=time.monotonic_ns):
    """ENOENT can mean cookie mismatch, so retry on only the registered tuple."""
    started = now()
    try:
        result = _inspect_match(identity, query(identity, identity.cookie))
    except (OSError, DiagnosticProtocolError) as first:
        first_errno = getattr(first, "errno", None)
        if first_errno not in (errno.ENOENT, errno.ESTALE):
            result = _error(first)
        else:
            try:
                retry = _inspect_match(identity, query(identity, NO_COOKIE))
            except (OSError, DiagnosticProtocolError) as second:
                if first_errno == errno.ENOENT and getattr(second, "errno", None) == errno.ENOENT:
                    result = {"status": "absent", "cookie_errno": first_errno, "tuple_errno": errno.ENOENT}
                else:
                    result = _error(second)
                    if getattr(second, "errno", None) in (errno.ENOENT, errno.ESTALE):
                        result["status"] = "identity_uncertain"
                    result["cookie_errno"] = first_errno
            else:
                result = {"status": retry["status"], "cookie_errno": first_errno}
                if result["status"] in ("present", "time_wait"):
                    result["status"] = "identity_uncertain"
    return {"start_ns": started, "end_ns": now(), **result}


ENDPOINT_POSITIVE = frozenset(("present", "time_wait"))
ENDPOINT_OBSERVED = ENDPOINT_POSITIVE | {"absent"}


def assess(records, origin, budget_ms):
    """Summarize sampled observations with their time bounds and known errors."""
    if origin is None:
        return {"status": "close_origin_unavailable", "positive_control": False}
    lower, upper = origin["lower_ns"], origin["upper_ns"]
    if type(lower) is not int or type(upper) is not int or upper < lower or budget_ms <= 0:
        raise ValueError("invalid close interval or budget")
    budget = budget_ms * 1_000_000
    deadline = {"earliest_ns": lower + budget, "latest_ns": upper + budget}
    # The last positive used to start a sampled close bracket is itself a
    # positive control. Its response can arrive inside that bracket.
    control = any(row["status"] == "present" and row["start_ns"] <= lower and row["end_ns"] <= upper
                  for row in records)
    within = [row for row in records if row["end_ns"] >= lower and row["start_ns"] <= deadline["latest_ns"]]
    positive = [row for row in within if row["status"] in ENDPOINT_POSITIVE]
    absent = [row for row in within if row["status"] == "absent" and row["start_ns"] >= upper]
    first_absent = absent[0] if absent else None
    failures = sorted({row["status"] for row in within if row["status"] not in ENDPOINT_OBSERVED})
    after_deadline = next((row for row in records if row["start_ns"] >= deadline["latest_ns"]), None)
    reappeared = bool(first_absent and any(row["start_ns"] > first_absent["end_ns"] for row in positive))
    all_positive = [row for row in records if row["start_ns"] <= deadline["latest_ns"] and row["status"] in ENDPOINT_POSITIVE]
    result = {"origin": origin, "deadline": deadline, "positive_control": control, "last_positive_in_window": positive[-1] if positive else None,
              "last_positive_observed": all_positive[-1] if all_positive else None,
              "first_absent_after_origin": first_absent, "first_observation_at_or_after_deadline": after_deadline,
              "failure_kinds_in_window": failures, "reappeared_after_absence": reappeared}
    if not control:
        result["status"] = "positive_control_unavailable"
    elif failures or reappeared:
        result["status"] = "observation_uncertain"
    elif after_deadline and after_deadline["status"] in ENDPOINT_POSITIVE:
        result["status"] = "control_block_at_or_after_deadline" if after_deadline["status"] == "time_wait" else "socket_at_or_after_deadline"
    elif (first_absent and first_absent["end_ns"] <= deadline["earliest_ns"]
          and after_deadline and after_deadline["status"] == "absent"):
        result["status"] = "absent_observed_within_window"
    else:
        result["status"] = "deadline_observation_inconclusive"
    return result
