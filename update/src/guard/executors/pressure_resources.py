"""Bounded read-only owned-container resource capture; produces no verdict.

The execution client may have a different cleanup run ID from the owned target.
Only the target's explicit run label is used for source ownership checks.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import time

from . import pressure_resource_probe as probe


MAX_PACKET_BYTES = 512 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_PIN = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,440}@sha256:[0-9a-f]{64}")
_CODES = probe.DIAGNOSTICS | frozenset({
    "RESOURCE_TARGET_UNVERIFIED", "RESOURCE_OWNER_UNVERIFIED", "RESOURCE_HOST_NAMESPACE_UNSUPPORTED",
    "RESOURCE_PROC_MOUNT_UNSUPPORTED", "RESOURCE_IMAGE_UNVERIFIED", "RESOURCE_CAPTURE_UNAVAILABLE",
    "RESOURCE_TRANSPORT_INCOMPLETE", "RESOURCE_PACKET_INVALID", "RESOURCE_OWNER_CHANGED",
    "RESOURCE_PACKET_INCOMPLETE",
    "RESOURCE_CGROUP_NAMESPACE_UNSUPPORTED",
})


def _integer(value, minimum=0, maximum=2**63 - 1):
    return type(value) is int and minimum <= value <= maximum


def _keys(value, expected):
    return type(value) is dict and set(value) == set(expected)


def _timestamp(value):
    if type(value) is not str or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _namespaces(value):
    return _keys(value, probe.NAMESPACES) and all(_integer(inode, 1) for inode in value.values())


def _process_identity(value):
    return (_keys(value, ("pid", "start_time_ticks")) and _integer(value["pid"], 1, 2**31 - 1)
            and _integer(value["start_time_ticks"], 1))


def _queue_metadata(row):
    state, kind = row["state"], row["queue_kind"]
    if state == 1:
        return (kind == "established_sequence_bytes" and _integer(row["tx_queue_bytes"])
                and _integer(row["rx_queue_bytes"]) and row["listen_pending_connections"] is None)
    if state == 10:
        return (kind == "listen_backlog_connections" and row["tx_queue_bytes"] is None
                and row["rx_queue_bytes"] is None and _integer(row["listen_pending_connections"]))
    return (kind == "uninterpreted" and row["tx_queue_bytes"] is None
            and row["rx_queue_bytes"] is None and row["listen_pending_connections"] is None)


def _decode(raw):
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_PACKET_BYTES:
        raise ValueError("resource packet byte bound")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("resource packet ambiguity")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("resource packet number")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("RESOURCE_PACKET_INVALID") from None


def validate_packet(packet, *, port):
    """Project only typed bounded metadata, retaining independent valid rows.

    Invalid/missing components prevent completeness; arbitrary keys and strings
    never enter the returned observations or diagnostic vocabulary.
    """
    observations, errors = probe.empty_observations(port), set()
    if not _keys(packet, ("schema", "profile", "timestamp", "observations", "complete", "diagnostic_codes")):
        errors.add("RESOURCE_PACKET_INVALID")
    if type(packet) is not dict or packet.get("schema") != probe.SCHEMA or packet.get("profile") != probe.PROFILE:
        return observations, None, False, ["RESOURCE_PACKET_INVALID"]
    timestamp = packet.get("timestamp") if _timestamp(packet.get("timestamp")) else None
    if timestamp is None or type(packet.get("complete")) is not bool:
        errors.add("RESOURCE_PACKET_INVALID")
    codes = packet.get("diagnostic_codes")
    if type(codes) is not list or len(codes) > len(_CODES):
        errors.add("RESOURCE_PACKET_INVALID")
    else:
        for code in codes:
            if type(code) is str and code in probe.DIAGNOSTICS:
                errors.add(code)
            else:
                errors.add("RESOURCE_PACKET_INVALID")
    values = packet.get("observations")
    if not _keys(values, observations):
        errors.add("RESOURCE_PACKET_INVALID")
    if type(values) is not dict:
        return observations, timestamp, False, sorted(errors)
    for field in ("memory_scope", "queue_scope", "tuple_hash_scheme", "tcp_port"):
        if values.get(field) != observations[field] or type(values.get(field)) is not type(observations[field]):
            errors.add("RESOURCE_PACKET_INVALID")
    clock = values.get("clock")
    if (_keys(clock, ("basis", "time_namespace_inode", "sample_started_monotonic_ns", "sample_finished_monotonic_ns"))
            and clock["basis"] == probe.CLOCK_BASIS and type(clock["basis"]) is str
            and all(_integer(clock[key], 1) for key in ("time_namespace_inode", "sample_started_monotonic_ns", "sample_finished_monotonic_ns"))
            and 0 <= clock["sample_finished_monotonic_ns"] - clock["sample_started_monotonic_ns"] <= probe.MAX_SAMPLE_DURATION_NS):
        observations["clock"] = dict(clock)
    else:
        errors.add("RESOURCE_CLOCK_UNAVAILABLE")
        if clock is not None:
            errors.add("RESOURCE_PACKET_INVALID")
    collector = values.get("collector")
    if (_keys(collector, ("pid", "start_time_ticks", "namespaces"))
            and _process_identity({key: collector[key] for key in ("pid", "start_time_ticks")})
            and _namespaces(collector["namespaces"])):
        observations["collector"] = collector
    else:
        errors.add("RESOURCE_PACKET_INCOMPLETE" if collector is None else "RESOURCE_PACKET_INVALID")
    collector = observations["collector"]
    rows = values.get("processes")
    if type(rows) is not list:
        errors.add("RESOURCE_PACKET_INVALID")
        rows = []
    if len(rows) > probe.MAX_PROCESSES:
        errors.add("RESOURCE_PROCESS_BOUND")
    identities, duplicate_pids = {}, set()
    for row in rows[:probe.MAX_PROCESSES]:
        if (not _keys(row, ("pid", "start_time_ticks", "rss_bytes", "namespaces", "identity_stable"))
                or not _process_identity({key: row.get(key) for key in ("pid", "start_time_ticks")})
                or type(row.get("identity_stable")) is not bool
                or row.get("rss_bytes") is not None and not _integer(row["rss_bytes"])
                or row.get("namespaces") is not None and not _namespaces(row["namespaces"])):
            errors.add("RESOURCE_PACKET_INVALID")
            continue
        if collector is not None and row["pid"] == collector.get("pid"):
            errors.add("RESOURCE_PACKET_INVALID")
            continue
        if row["pid"] in identities:
            duplicate_pids.add(row["pid"])
            errors.add("RESOURCE_PACKET_INVALID")
            continue
        projected = dict(row)
        if not projected["identity_stable"]:
            projected.update(rss_bytes=None, namespaces=None)
            errors.add("RESOURCE_PROCESS_CHANGED")
        if projected["rss_bytes"] is None or projected["namespaces"] is None:
            errors.add("RESOURCE_PACKET_INCOMPLETE")
        elif observations["collector"] is not None and projected["namespaces"] != observations["collector"]["namespaces"]:
            errors.add("RESOURCE_NAMESPACE_DIFFERENT")
        identities[row["pid"]] = projected
    observations["processes"] = [row for pid, row in sorted(identities.items()) if pid not in duplicate_pids]
    identities = {row["pid"]: row for row in observations["processes"] if row["identity_stable"]}
    memory = values.get("cgroup_memory_current_bytes")
    if _integer(memory) and values.get("cgroup_version") == "v2":
        observations.update(cgroup_memory_current_bytes=memory, cgroup_version="v2")
    elif memory is None and values.get("cgroup_version") is None:
        errors.add("RESOURCE_CGROUP_UNAVAILABLE")
    else:
        errors.add("RESOURCE_PACKET_INVALID")
    sockets = values.get("tcp_connections")
    if type(sockets) is not list:
        errors.add("RESOURCE_PACKET_INVALID")
        sockets = []
    if len(sockets) > probe.MAX_CONNECTIONS:
        errors.add("RESOURCE_CONNECTION_BOUND")
    seen = set()
    for row in sockets[:probe.MAX_CONNECTIONS]:
        if (not _keys(row, ("family", "tuple_sha256", "state", "queue_kind", "tx_queue_bytes", "rx_queue_bytes",
                            "listen_pending_connections", "inode",
                            "owners", "identity_stable", "ownership_complete"))
                or type(row.get("family")) is not str or row["family"] not in {"ipv4", "ipv6"}
                or type(row.get("tuple_sha256")) is not str or not _SHA.fullmatch(row["tuple_sha256"])
                or not _integer(row.get("state"), 1, 12)
                or not _integer(row.get("inode")) or not _queue_metadata(row)
                or type(row.get("identity_stable")) is not bool or type(row.get("ownership_complete")) is not bool
                or type(row.get("owners")) is not list or len(row["owners"]) > probe.MAX_PROCESSES):
            errors.add("RESOURCE_PACKET_INVALID")
            continue
        key = (row["family"], row["tuple_sha256"], row["inode"])
        if key in seen:
            errors.add("RESOURCE_PACKET_INVALID")
            continue
        seen.add(key)
        if row["queue_kind"] == "uninterpreted":
            errors.add("RESOURCE_QUEUE_STATE_UNSUPPORTED")
        owners, owner_seen = [], set()
        for owner in row["owners"]:
            if (not _process_identity(owner) or owner["pid"] in owner_seen
                    or owner["pid"] not in identities
                    or identities[owner["pid"]]["start_time_ticks"] != owner["start_time_ticks"]):
                errors.add("RESOURCE_PACKET_INVALID")
                continue
            owner_seen.add(owner["pid"])
            owners.append(owner)
        stable = row["identity_stable"]
        ownership = stable and bool(owners) and len(owners) == len(row["owners"]) and row["ownership_complete"]
        if not stable:
            errors.add("RESOURCE_SOCKET_CHANGED")
            owners = []
        if not ownership:
            errors.add("RESOURCE_SOCKET_OWNER_UNAVAILABLE")
        observations["tcp_connections"].append({**row, "owners": owners, "ownership_complete": ownership})
    if packet.get("complete") is not True:
        errors.add("RESOURCE_PACKET_INCOMPLETE")
    return observations, timestamp, not errors, sorted(errors)


def owned_resource_snapshot(docker, target, env, timeout):
    """Return the exact six-field owner snapshot or a fixed validation error.

    Private cgroup namespaces are required explicitly: an omitted/default mode
    cannot establish that memory.current describes this container's domain.
    """
    engine = target.get("engine")
    if type(engine) is not dict:
        raise ValueError("RESOURCE_TARGET_UNVERIFIED")
    container, image, run_id = engine.get("container"), engine.get("image_digest"), target.get("run_id")
    declared = getattr(getattr(env, "engine", None), "image_digest", None)
    if (type(container) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container)
            or type(image) is not str or not _PIN.fullmatch(image)
            or declared not in {image, image.partition("@")[2]}
            or type(run_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id)):
        raise ValueError("RESOURCE_TARGET_UNVERIFIED")
    raw = docker.inspect_json(container, timeout=timeout)
    if type(raw) is not dict or type(raw.get("Config")) is not dict or type(raw.get("State")) is not dict:
        raise ValueError("RESOURCE_OWNER_UNVERIFIED")
    config, state, host = raw["Config"], raw["State"], raw.get("HostConfig")
    if (type(config.get("Labels")) is not dict or config["Labels"].get("guard.run") != run_id
            or config.get("Image") != image or state.get("Running") is not True
            or not _timestamp(state.get("StartedAt")) or not _integer(state.get("Pid"), 1, 2**31 - 1)
            or type(raw.get("Id")) is not str or not _SHA.fullmatch(raw["Id"])
            or raw.get("Name") != "/" + container
            or type(raw.get("Image")) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", raw["Image"])):
        raise ValueError("RESOURCE_OWNER_UNVERIFIED")
    if (type(host) is not dict or host.get("PidMode") not in {"", "private"}
            or type(host.get("NetworkMode")) is not str or not host["NetworkMode"]
            or host["NetworkMode"] == "host" or host["NetworkMode"].startswith(("container:", "service:"))):
        raise ValueError("RESOURCE_HOST_NAMESPACE_UNSUPPORTED")
    if host.get("CgroupnsMode") != "private":
        raise ValueError("RESOURCE_CGROUP_NAMESPACE_UNSUPPORTED")
    mounts = raw.get("Mounts")
    if type(mounts) is not list or any(type(mount) is not dict or type(mount.get("Type")) is not str
                                     or mount["Type"] not in {"bind", "volume", "tmpfs", "npipe", "cluster", "image"}
                                     or type(mount.get("Destination")) is not str
                                     or not mount["Destination"].startswith("/")
                                     or ".." in mount["Destination"].split("/") for mount in mounts):
        raise ValueError("RESOURCE_OWNER_UNVERIFIED")
    if any(mount["Destination"] == root or mount["Destination"].startswith(root + "/")
           or root.startswith(mount["Destination"].rstrip("/") + "/")
           for mount in mounts for root in ("/proc", "/sys/fs/cgroup")):
        raise ValueError("RESOURCE_PROC_MOUNT_UNSUPPORTED")
    return {"container_id": raw["Id"], "image_id": raw["Image"], "started_at": state["StartedAt"],
            "image": image, "run_id": run_id, "host_pid": state["Pid"]}


def capture_resource_sample(docker, target, env, *, deadline, port):
    """No daemon start, process signals, resource cleanup or security judgment."""
    result = {"owner": None, "observations": probe.empty_observations(port if _integer(port, 1, 65535) else None),
              "timestamp": None, "capture_hashes": {"helper_sha256": None, "packet_sha256": None,
                "retained_bytes": None, "transport_complete": False}, "complete": False, "diagnostic_codes": []}
    errors = set()
    if not _integer(port, 1, 65535) or type(deadline) not in (int, float) or not math.isfinite(deadline):
        result["diagnostic_codes"] = ["RESOURCE_TARGET_UNVERIFIED"]
        return result
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ValueError("RESOURCE_DEADLINE")
        return min(10, value)
    def diagnostic(exc, fallback):
        code = str(exc) if type(exc) is ValueError else ""
        errors.add(code if code in _CODES else fallback)
    try:
        before = owned_resource_snapshot(docker, target, env, remaining())
        result["owner"] = before
        image = docker.image_info(before["image_id"], timeout=remaining())
        digests = image.get("digests") if type(image) is dict else None
        if (type(digests) is not list or not 1 <= len(digests) <= 128
                or any(type(value) is not str or not _PIN.fullmatch(value) for value in digests)
                or before["image"] not in digests):
            raise ValueError("RESOURCE_IMAGE_UNVERIFIED")
    except Exception as exc:
        diagnostic(exc, "RESOURCE_OWNER_UNVERIFIED")
        result["diagnostic_codes"] = sorted(errors)
        return result
    try:
        script = Path(probe.__file__).read_bytes()
        result["capture_hashes"]["helper_sha256"] = hashlib.sha256(script).hexdigest()
        captured = docker.exec_bounded(target["engine"]["container"], ["python3", "-c", script.decode("utf-8"), str(port)],
                                       timeout=remaining(), max_bytes=MAX_PACKET_BYTES)
        raw = captured.stdout_bytes if type(getattr(captured, "stdout_bytes", None)) is bytes else captured.stdout.encode("utf-8")
        result["capture_hashes"].update(packet_sha256=hashlib.sha256(raw).hexdigest(), retained_bytes=len(raw))
        transport = captured.capture
        transport_complete = (captured.returncode == 0 and type(transport) is dict
            and transport.get("complete") is True and transport.get("bounded_at_source") is True
            and transport.get("truncated") is False and transport.get("timed_out") is False)
        result["capture_hashes"]["transport_complete"] = transport_complete
        if not transport_complete:
            errors.add("RESOURCE_TRANSPORT_INCOMPLETE")
        packet = _decode(raw)
        observations, timestamp, complete, packet_errors = validate_packet(packet, port=port)
        result.update(observations=observations, timestamp=timestamp)
        errors.update(packet_errors)
        if not complete:
            errors.add("RESOURCE_PACKET_INCOMPLETE")
    except Exception as exc:
        diagnostic(exc, "RESOURCE_CAPTURE_UNAVAILABLE")
    try:
        if owned_resource_snapshot(docker, target, env, remaining()) != before:
            errors.add("RESOURCE_OWNER_CHANGED")
            result["owner"] = None
    except Exception as exc:
        diagnostic(exc, "RESOURCE_OWNER_UNVERIFIED")
        result["owner"] = None
    result.update(complete=not errors, diagnostic_codes=sorted(errors))
    return result
