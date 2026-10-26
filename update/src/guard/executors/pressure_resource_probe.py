"""Standalone Linux read-only resource sampler; emits bounded metadata only.

RSS and cgroup memory are aggregate observations, never per-connection buffers.
The connection digest sorts normalized IP/port endpoints and includes the network
namespace inode, so either endpoint in that namespace uses the same digest.
No endpoint addresses, paths, file contents, environment values or exception text
are emitted. Alternate roots are an explicit file-fixture seam for offline tests.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
from itertools import islice
import json
import os
from pathlib import Path
import re
import sys
import time


SCHEMA = "guard-pressure-resource-packet/1"
PROFILE = "linux-proc-cgroup-v2-tcp/1"
NAMESPACES = ("pid", "net", "mnt", "user", "cgroup")
MAX_PROCESSES = 64
MAX_FDS = 128
MAX_TCP_ROWS = 256
MAX_CONNECTIONS = 64
MEMORY_SCOPE = "process_rss_and_container_cgroup_aggregate"
QUEUE_SCOPE = "kernel_per_socket_queues_at_sample_time"
TUPLE_SCHEME = "sha256-unordered-normalized-ip-port-pair-with-netns-v1"
CLOCK_BASIS = "linux-monotonic-time-namespace-v1"
MAX_SAMPLE_DURATION_NS = 6_000_000_000  # Five-second sampling budget plus bounded final metadata reads.
DIAGNOSTICS = frozenset({
    "RESOURCE_PLATFORM_UNSUPPORTED", "RESOURCE_SOURCE_UNAVAILABLE", "RESOURCE_DEADLINE",
    "RESOURCE_PROCESS_BOUND", "RESOURCE_PROCESS_IDENTITY_UNAVAILABLE", "RESOURCE_PROCESS_CHANGED",
    "RESOURCE_RSS_UNAVAILABLE", "RESOURCE_NAMESPACE_UNAVAILABLE", "RESOURCE_NAMESPACE_DIFFERENT",
    "RESOURCE_FD_BOUND", "RESOURCE_FD_UNAVAILABLE", "RESOURCE_CGROUP_UNAVAILABLE",
    "RESOURCE_TCP_BOUND", "RESOURCE_TCP_UNAVAILABLE", "RESOURCE_TCP_ROW_INVALID",
    "RESOURCE_CONNECTION_BOUND", "RESOURCE_SOCKET_CHANGED", "RESOURCE_SOCKET_OWNER_UNAVAILABLE",
    "RESOURCE_CLOCK_UNAVAILABLE", "RESOURCE_QUEUE_STATE_UNSUPPORTED",
})


def empty_observations(port):
    return {"collector": None, "clock": None, "processes": [], "cgroup_memory_current_bytes": None,
            "cgroup_version": None, "tcp_port": port, "tcp_connections": [],
            "memory_scope": MEMORY_SCOPE, "queue_scope": QUEUE_SCOPE, "tuple_hash_scheme": TUPLE_SCHEME}


def _read(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("bounded source")
    return raw.decode("ascii")


def process_start(proc_root, pid):
    text = _read(proc_root / str(pid) / "stat", 8192)
    if not text.startswith(str(pid) + " (") or ")" not in text:
        raise ValueError("process identity")
    fields = text.rsplit(")", 1)[1].split()
    if len(fields) < 22 or not re.fullmatch(r"[0-9]{1,19}", fields[19]):
        raise ValueError("process identity")
    start = int(fields[19])
    if not 0 < start < 2**63:
        raise ValueError("process identity")
    return start


def process_namespaces(proc_root, pid):
    result = {}
    for name in NAMESPACES:
        value = os.readlink(proc_root / str(pid) / "ns" / name)
        match = re.fullmatch(name + r":\[([0-9]{1,19})\]", value)
        if not match or not 0 < int(match[1]) < 2**63:
            raise ValueError("namespace identity")
        result[name] = int(match[1])
    return result


def time_namespace(proc_root):
    value = os.readlink(proc_root / "self" / "ns" / "time")
    match = re.fullmatch(r"time:\[([0-9]{1,19})\]", value)
    if not match or not 0 < int(match[1]) < 2**63:
        raise ValueError("time namespace identity")
    return int(match[1])


def process_rss(proc_root, pid):
    text = _read(proc_root / str(pid) / "status", 65536)
    rows = [line for line in text.splitlines() if line.startswith("VmRSS:")]
    match = re.fullmatch(r"VmRSS:\s+([0-9]{1,16})\s+kB", rows[0]) if len(rows) == 1 else None
    if not match or int(match[1]) * 1024 >= 2**63:
        raise ValueError("RSS unavailable")
    return int(match[1]) * 1024


def endpoint(value, family):
    address, port = value.split(":")
    digits = 8 if family == "ipv4" else 32
    if not re.fullmatch(r"[0-9A-Fa-f]{" + str(digits) + "}", address) or not re.fullmatch(r"[0-9A-Fa-f]{4}", port):
        raise ValueError("TCP endpoint")
    raw = bytes.fromhex(address)
    # Linux prints each host-order 32-bit word separately for IPv6 too.
    network = b"".join(raw[index:index + 4][::-1] for index in range(0, len(raw), 4)) if sys.byteorder == "little" else raw
    ip = ipaddress.ip_address(network)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return [str(ip), int(port, 16)]


def tuple_digest(first, second, net_namespace):
    encoded = json.dumps({"endpoints": sorted([first, second]), "net_namespace": net_namespace},
                         sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def tcp_rows(proc_root, port, net_namespace, errors):
    connections = []
    for family, name in (("ipv4", "tcp"), ("ipv6", "tcp6")):
        try:
            lines = _read(proc_root / "net" / name, 131072).splitlines()
            if not lines or "local_address" not in lines[0]:
                raise ValueError("TCP header")
            data = [line for line in lines[1:] if line.strip()]
            if len(data) > MAX_TCP_ROWS:
                errors.add("RESOURCE_TCP_BOUND")
            for line in data[:MAX_TCP_ROWS]:
                try:
                    parts = line.split()
                    if not 10 <= len(parts) <= 32:
                        raise ValueError("TCP row")
                    local, remote = endpoint(parts[1], family), endpoint(parts[2], family)
                    if local[1] != port:
                        continue
                    if (not re.fullmatch(r"[0-9A-Fa-f]{2}", parts[3])
                            or not 1 <= int(parts[3], 16) <= 12
                            or not re.fullmatch(r"[0-9A-Fa-f]{1,16}:[0-9A-Fa-f]{1,16}", parts[4])
                            or not re.fullmatch(r"[0-9]{1,19}", parts[9])):
                        raise ValueError("TCP metadata")
                    tx, rx = (int(value, 16) for value in parts[4].split(":"))
                    inode = int(parts[9])
                    if max(tx, rx, inode) >= 2**63:
                        raise ValueError("TCP range")
                    if len(connections) >= MAX_CONNECTIONS:
                        errors.add("RESOURCE_CONNECTION_BOUND")
                        continue
                    state = int(parts[3], 16)
                    # LISTEN rx is an accept backlog count, not received bytes.
                    # Other transitional states can include control sequence
                    # space, so this profile reports bytes only for ESTABLISHED.
                    queue_kind = ("established_sequence_bytes" if state == 1 else
                                  "listen_backlog_connections" if state == 10 else "uninterpreted")
                    if queue_kind == "uninterpreted":
                        errors.add("RESOURCE_QUEUE_STATE_UNSUPPORTED")
                    connections.append({"family": family, "tuple_sha256": tuple_digest(local, remote, net_namespace),
                        "state": state, "queue_kind": queue_kind,
                        "tx_queue_bytes": tx if state == 1 else None,
                        "rx_queue_bytes": rx if state == 1 else None,
                        "listen_pending_connections": rx if state == 10 else None,
                        "inode": inode, "owners": [], "identity_stable": False, "ownership_complete": False})
                except (ValueError, IndexError):
                    errors.add("RESOURCE_TCP_ROW_INVALID")
        except (OSError, ValueError, UnicodeError):
            errors.add("RESOURCE_TCP_UNAVAILABLE")
    return connections


def cgroup_memory(proc_root, cgroup_root):
    # The cgroup-v2 membership must map beneath the container's cgroup mount.
    membership = _read(proc_root / "self" / "cgroup", 4096).splitlines()
    paths = [line[3:] for line in membership if line.startswith("0::")]
    if len(paths) != 1 or not paths[0].startswith("/") or len(paths[0]) > 512 or ".." in paths[0].split("/"):
        raise ValueError("cgroup unavailable")
    _read(cgroup_root / "cgroup.controllers", 4096)
    value = _read(cgroup_root / paths[0].lstrip("/") / "memory.current", 64).strip()
    if not re.fullmatch(r"[0-9]{1,19}", value) or int(value) >= 2**63:
        raise ValueError("cgroup memory")
    return int(value)


def collect(port, *, proc_root=Path("/proc"), cgroup_root=Path("/sys/fs/cgroup"), self_pid=None, budget_seconds=5.0):
    """File-fixture roots never become output metadata or production arguments."""
    started_monotonic_ns = time.monotonic_ns()
    proc_root, cgroup_root = Path(proc_root), Path(cgroup_root)
    collector_pid = os.getpid() if self_pid is None else self_pid
    deadline = time.monotonic() + budget_seconds
    errors, observations, owners = set(), empty_observations(port), {}
    clock_namespace = None
    try:
        clock_namespace = time_namespace(proc_root)
    except (OSError, ValueError):
        errors.add("RESOURCE_CLOCK_UNAVAILABLE")
    try:
        collector_start = process_start(proc_root, collector_pid)
        collector_ns = process_namespaces(proc_root, collector_pid)
        observations["collector"] = {"pid": collector_pid, "start_time_ticks": collector_start, "namespaces": collector_ns}
    except (OSError, ValueError, UnicodeError):
        errors.add("RESOURCE_NAMESPACE_UNAVAILABLE")
        collector_ns = None
    connections = tcp_rows(proc_root, port, collector_ns["net"] if collector_ns else 0, errors)
    try:
        with os.scandir(proc_root) as entries:
            bounded = list(islice(entries, 4097))
        if len(bounded) > 4096:
            errors.add("RESOURCE_PROCESS_BOUND")
        pids = sorted(int(entry.name) for entry in bounded[:4096] if re.fullmatch(r"[1-9][0-9]{0,9}", entry.name)
                      and int(entry.name) != collector_pid and int(entry.name) < 2**31)
        if len(pids) > MAX_PROCESSES:
            errors.add("RESOURCE_PROCESS_BOUND")
        for pid in pids[:MAX_PROCESSES]:
            if time.monotonic() >= deadline:
                errors.add("RESOURCE_DEADLINE")
                break
            try:
                start = process_start(proc_root, pid)
            except (OSError, ValueError, UnicodeError):
                errors.add("RESOURCE_PROCESS_IDENTITY_UNAVAILABLE")
                continue
            row = {"pid": pid, "start_time_ticks": start, "rss_bytes": None, "namespaces": None, "identity_stable": False}
            socket_inodes = set()
            try:
                row["rss_bytes"] = process_rss(proc_root, pid)
            except (OSError, ValueError, UnicodeError):
                errors.add("RESOURCE_RSS_UNAVAILABLE")
            try:
                row["namespaces"] = process_namespaces(proc_root, pid)
                if collector_ns is None or row["namespaces"] != collector_ns:
                    errors.add("RESOURCE_NAMESPACE_DIFFERENT")
            except (OSError, ValueError, UnicodeError):
                errors.add("RESOURCE_NAMESPACE_UNAVAILABLE")
            try:
                with os.scandir(proc_root / str(pid) / "fd") as descriptors:
                    fds = list(islice(descriptors, MAX_FDS + 1))
                if len(fds) > MAX_FDS:
                    errors.add("RESOURCE_FD_BOUND")
                for fd in fds[:MAX_FDS]:
                    if time.monotonic() >= deadline:
                        errors.add("RESOURCE_DEADLINE")
                        break
                    try:
                        target = os.readlink(fd.path)
                        match = re.fullmatch(r"socket:\[([0-9]{1,19})\]", target)
                        if match and 0 < int(match[1]) < 2**63:
                            socket_inodes.add(int(match[1]))
                    except OSError:
                        errors.add("RESOURCE_FD_UNAVAILABLE")
            except OSError:
                errors.add("RESOURCE_FD_UNAVAILABLE")
            try:
                row["identity_stable"] = process_start(proc_root, pid) == start
            except (OSError, ValueError, UnicodeError):
                pass
            if row["identity_stable"]:
                for inode in socket_inodes:
                    owners.setdefault(inode, []).append({"pid": pid, "start_time_ticks": start})
            else:
                row.update(rss_bytes=None, namespaces=None)
                errors.add("RESOURCE_PROCESS_CHANGED")
            observations["processes"].append(row)
    except OSError:
        errors.add("RESOURCE_SOURCE_UNAVAILABLE")
    try:
        observations["cgroup_memory_current_bytes"] = cgroup_memory(proc_root, cgroup_root)
        observations["cgroup_version"] = "v2"
    except (OSError, ValueError, UnicodeError):
        errors.add("RESOURCE_CGROUP_UNAVAILABLE")
    after = tcp_rows(proc_root, port, collector_ns["net"] if collector_ns else 0, errors)
    stable = {(row["family"], row["tuple_sha256"], row["inode"]) for row in after}
    for row in connections:
        row["identity_stable"] = (row["family"], row["tuple_sha256"], row["inode"]) in stable
        if not row["identity_stable"]:
            errors.add("RESOURCE_SOCKET_CHANGED")
        row["owners"] = sorted(owners.get(row["inode"], []), key=lambda owner: owner["pid"]) if row["identity_stable"] else []
        row["ownership_complete"] = bool(row["owners"]) and row["identity_stable"]
        if not row["ownership_complete"]:
            errors.add("RESOURCE_SOCKET_OWNER_UNAVAILABLE")
    observations["tcp_connections"] = connections
    try:
        if collector_ns is None or process_start(proc_root, collector_pid) != collector_start or process_namespaces(proc_root, collector_pid) != collector_ns:
            errors.add("RESOURCE_PROCESS_CHANGED")
    except (OSError, ValueError, UnicodeError):
        errors.add("RESOURCE_PROCESS_CHANGED")
    try:
        if clock_namespace is None or time_namespace(proc_root) != clock_namespace:
            raise ValueError("time namespace changed")
        finished_monotonic_ns = time.monotonic_ns()
        if not 0 < started_monotonic_ns <= finished_monotonic_ns <= started_monotonic_ns + MAX_SAMPLE_DURATION_NS:
            raise ValueError("sample clock bound")
        observations["clock"] = {"basis": CLOCK_BASIS, "time_namespace_inode": clock_namespace,
            "sample_started_monotonic_ns": started_monotonic_ns,
            "sample_finished_monotonic_ns": finished_monotonic_ns}
    except (OSError, ValueError):
        errors.add("RESOURCE_CLOCK_UNAVAILABLE")
    return {"schema": SCHEMA, "profile": PROFILE,
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
            "observations": observations, "complete": not errors, "diagnostic_codes": sorted(errors)}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not re.fullmatch(r"[0-9]{1,5}", args[0]) or not 1 <= int(args[0]) <= 65535:
        return 2
    if sys.platform != "linux":
        result = {"schema": SCHEMA, "profile": PROFILE,
                  "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                  "observations": empty_observations(int(args[0])), "complete": False,
                  "diagnostic_codes": ["RESOURCE_PLATFORM_UNSUPPORTED"]}
    else:
        try:
            result = collect(int(args[0]))
        except Exception:
            result = {"schema": SCHEMA, "profile": PROFILE,
                      "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                      "observations": empty_observations(int(args[0])), "complete": False,
                      "diagnostic_codes": ["RESOURCE_SOURCE_UNAVAILABLE"]}
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
