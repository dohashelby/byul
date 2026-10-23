"""Normalize Hubble and Tetragon JSON Lines into runtime observations."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
from typing import Any, Iterable

from serving_runtime.mvp.mock_engine import _utc_now
from serving_runtime.mvp.store import JsonlStore


SCHEMA = "runtime-observation/1.0"
_RULE_ID = re.compile(r"(?i)(TG-[A-Z]+-\d+)")


class EventError(ValueError):
    """One source event is malformed or unsupported."""


def _object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EventError(f"{path} must be an object")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise EventError(f"{path} must be a non-empty string")
    return value


def _pod(value: Any) -> dict[str, str | None]:
    pod = value if isinstance(value, dict) else {}
    namespace = pod.get("namespace")
    name = pod.get("pod_name", pod.get("name"))
    return {
        "namespace": namespace if isinstance(namespace, str) else None,
        "pod": name if isinstance(name, str) else None,
    }


def _base(observed_at: str, source: str, kind: str, origin: str) -> dict[str, Any]:
    if origin not in {"synthetic", "real"}:
        raise ValueError("data_origin must be synthetic or real")
    return {
        "schema": SCHEMA,
        "observed_at": observed_at,
        "source": source,
        "kind": kind,
        "data_origin": origin,
    }


def parse_hubble(document: Any, *, data_origin: str) -> dict[str, Any]:
    root = _object(document, "$")
    flow = _object(root.get("flow"), "$.flow")
    observed_at = _string(flow.get("time"), "$.flow.time")
    verdict = _string(flow.get("verdict"), "$.flow.verdict")
    ip = _object(flow.get("IP"), "$.flow.IP")
    source = _object(flow.get("source", {}), "$.flow.source")
    destination = _object(flow.get("destination", {}), "$.flow.destination")
    l4 = _object(flow.get("l4"), "$.flow.l4")
    protocols = [name for name in ("TCP", "UDP") if name in l4]
    if len(protocols) != 1:
        raise EventError("$.flow.l4 must contain exactly one of TCP or UDP")
    protocol = protocols[0]
    layer4 = _object(l4[protocol], f"$.flow.l4.{protocol}")
    port = layer4.get("destination_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise EventError(f"$.flow.l4.{protocol}.destination_port is invalid")
    reason = flow.get("drop_reason_desc")
    if reason is not None and not isinstance(reason, str):
        raise EventError("$.flow.drop_reason_desc must be a string or null")
    direction = flow.get("traffic_direction")
    if direction is not None and not isinstance(direction, str):
        raise EventError("$.flow.traffic_direction must be a string or null")
    normalized = _base(observed_at, "hubble", "network_flow", data_origin)
    normalized.update(
        {
            "verdict": {"FORWARDED": "allowed", "DROPPED": "dropped"}.get(
                verdict, verdict
            ),
            "src": {
                "namespace": source.get("namespace") if isinstance(source.get("namespace"), str) else None,
                "pod": source.get("pod_name") if isinstance(source.get("pod_name"), str) else None,
            },
            "dst": {
                "namespace": destination.get("namespace") if isinstance(destination.get("namespace"), str) else None,
                "pod": destination.get("pod_name") if isinstance(destination.get("pod_name"), str) else None,
                "ip": ip.get("destination") if isinstance(ip.get("destination"), str) else None,
                "port": port,
                "protocol": protocol,
            },
            "traffic_direction": direction,
            "reason": reason,
        }
    )
    return normalized


def _event_time(root: dict[str, Any]) -> str:
    return _string(root.get("time"), "$.time")


def _binary_and_pod(event: dict[str, Any], path: str) -> tuple[str, dict[str, Any]]:
    process = _object(event.get("process"), f"{path}.process")
    binary = _string(process.get("binary"), f"{path}.process.binary")
    return binary, _pod(process.get("pod"))


def parse_tetragon(
    document: Any,
    *,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    root = _object(document, "$")
    observed_at = _event_time(root)
    event_names = [name for name in ("process_exec", "process_kprobe") if name in root]
    if len(event_names) != 1:
        raise EventError("event must contain exactly one supported Tetragon event")
    event_name = event_names[0]
    event = _object(root[event_name], f"$.{event_name}")
    binary, pod = _binary_and_pod(event, f"$.{event_name}")
    if event_name == "process_exec":
        normalized = _base(observed_at, "tetragon", "process_exec", data_origin)
        allowlist = tuple(allowed_engine_binaries or ())
        normalized.update(
            {
                "binary": binary,
                "pod": pod,
                "path": None,
                "access": None,
                "rule_id": "TG-EXEC-01",
                "action": "observed",
                "allowed_binary": binary in allowlist if allowlist else None,
            }
        )
        return normalized

    function_name = _string(event.get("function_name"), "$.process_kprobe.function_name")
    if function_name != "security_file_permission":
        raise EventError(f"unsupported kprobe function: {function_name}")
    policy_name = _string(event.get("policy_name"), "$.process_kprobe.policy_name")
    match = _RULE_ID.search(policy_name)
    if match is None:
        raise EventError("policy_name does not contain a TG rule ID")
    raw_args = event.get("args")
    if not isinstance(raw_args, list):
        raise EventError("$.process_kprobe.args must be an array")
    file_path: str | None = None
    mask: int | None = None
    for raw_arg in raw_args:
        arg = _object(raw_arg, "$.process_kprobe.args[]")
        file_arg = arg.get("file_arg")
        if isinstance(file_arg, dict) and isinstance(file_arg.get("path"), str):
            file_path = file_arg["path"]
        if "int_arg" in arg:
            value = arg["int_arg"]
            try:
                mask = int(value)
            except (TypeError, ValueError) as error:
                raise EventError("int_arg is not an integer") from error
    if not file_path:
        raise EventError("process_kprobe has no file_arg.path")
    if mask is None:
        raise EventError("process_kprobe has no int_arg")
    access = "write" if mask & 2 else "read" if mask & 4 else "other"
    action = _string(event.get("action"), "$.process_kprobe.action")
    normalized = _base(observed_at, "tetragon", "file_access", data_origin)
    normalized.update(
        {
            "binary": binary,
            "pod": pod,
            "path": file_path,
            "access": access,
            "rule_id": match.group(1).upper(),
            "action": action,
        }
    )
    return normalized


def transform_lines(
    lines: Iterable[str],
    *,
    kind: str,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Transform lines while retaining only line numbers and safe error reasons."""

    if kind not in {"hubble", "tetragon"}:
        raise ValueError("kind must be hubble or tetragon")
    if data_origin not in {"synthetic", "real"}:
        raise ValueError("data_origin must be synthetic or real")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            document = json.loads(line)
            if kind == "hubble":
                item = parse_hubble(document, data_origin=data_origin)
            else:
                item = parse_tetragon(
                    document,
                    data_origin=data_origin,
                    allowed_engine_binaries=allowed_engine_binaries,
                )
        except (json.JSONDecodeError, EventError, RecursionError) as error:
            rejected.append({"line": line_number, "reason": str(error)})
            continue
        accepted.append(item)
    return accepted, rejected


def ingest_file(
    input_path: str | Path,
    data_dir: str | Path,
    *,
    kind: str,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    with Path(input_path).open("r", encoding="utf-8") as source:
        accepted, rejected = transform_lines(
            source,
            kind=kind,
            data_origin=data_origin,
            allowed_engine_binaries=allowed_engine_binaries,
        )
    store = JsonlStore(Path(data_dir) / "observations.jsonl")
    for item in accepted:
        store.append(item)
    return {
        "processed": len(accepted) + len(rejected),
        "accepted": len(accepted),
        "rejected": rejected,
    }


class ObservationQuery:
    """Read-only aggregation for normalized observations."""

    FILTERS = {"source", "kind", "verdict", "rule_id", "data_origin"}

    def __init__(self, path: str | Path) -> None:
        self.store = JsonlStore(path)

    def query(self, filters: dict[str, str] | None = None) -> dict[str, Any]:
        active = filters or {}
        unknown = set(active) - self.FILTERS
        if unknown:
            raise ValueError(f"unknown observation filters: {', '.join(sorted(unknown))}")
        records, corrupt = self.store.read_all()
        filtered = [
            item
            for item in records
            if all(item.get(name) == value for name, value in active.items())
        ]
        filtered.sort(key=lambda item: str(item.get("observed_at", "")), reverse=True)
        dimensions = ("source", "kind", "verdict", "rule_id", "data_origin")
        counts = {
            name: dict(Counter(str(item[name]) for item in filtered if item.get(name) is not None))
            for name in dimensions
        }
        counts["total"] = len(filtered)
        origins_by_kind: dict[str, dict[str, int]] = {}
        for item in filtered:
            kind = item.get("kind")
            origin = item.get("data_origin")
            if not isinstance(kind, str) or not isinstance(origin, str):
                continue
            origin_counts = origins_by_kind.setdefault(kind, {})
            origin_counts[origin] = origin_counts.get(origin, 0) + 1
        return {
            "counts": counts,
            "origins_by_kind": origins_by_kind,
            "observations": filtered[:50],
            "corrupt_lines": len(corrupt),
            "corrupt_details": corrupt[:20],
            "generated_at": _utc_now(),
        }
