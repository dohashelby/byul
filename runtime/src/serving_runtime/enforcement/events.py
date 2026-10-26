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
HUBBLE_LOSS_SCHEMA = "runtime-hubble-loss/1.0"
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


def _pod(value: Any) -> dict[str, str] | None:
    pod = value if isinstance(value, dict) else {}
    namespace = pod.get("namespace")
    name = pod.get("pod_name", pod.get("name"))
    if not isinstance(namespace, str) or not namespace:
        return None
    if not isinstance(name, str) or not name:
        return None
    return {"namespace": namespace, "pod": name}


def _scope_value(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 128
        or any(ord(character) < 0x20 for character in value)
    ):
        raise ValueError(f"{name} must be a non-empty printable string up to 128 characters")
    return value


def _base(
    observed_at: str,
    source: str,
    kind: str,
    origin: str,
    *,
    environment: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    if origin not in {"synthetic", "real"}:
        raise ValueError("data_origin must be synthetic or real")
    environment = _scope_value(environment, "environment")
    run_id = _scope_value(run_id, "run_id")
    record = {
        "schema": SCHEMA,
        "observed_at": observed_at,
        "source": source,
        "kind": kind,
        "data_origin": origin,
    }
    if environment is not None:
        record["environment"] = environment
    if run_id is not None:
        record["run_id"] = run_id
    return record


def parse_hubble(
    document: Any,
    *,
    data_origin: str,
    environment: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
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
    normalized = _base(
        observed_at,
        "hubble",
        "network_flow",
        data_origin,
        environment=environment,
        run_id=run_id,
    )
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


def parse_hubble_loss(document: Any) -> dict[str, Any] | None:
    """Parse a Hubble GetFlowsResponse lost-events line, or return ``None``."""

    root = _object(document, "$")
    if "lost_events" not in root:
        return None
    lost = _object(root.get("lost_events"), "$.lost_events")
    source = _string(lost.get("source"), "$.lost_events.source")
    raw_count = lost.get("num_events_lost")
    if isinstance(raw_count, bool):
        raise EventError("$.lost_events.num_events_lost must be a non-negative integer")
    try:
        count = int(raw_count)
    except (TypeError, ValueError) as error:
        raise EventError(
            "$.lost_events.num_events_lost must be a non-negative integer"
        ) from error
    if count < 0 or (isinstance(raw_count, str) and str(count) != raw_count):
        raise EventError("$.lost_events.num_events_lost must be a non-negative integer")
    observed_at = _string(root.get("time"), "$.time")
    cpu = lost.get("cpu")
    if cpu is not None:
        if isinstance(cpu, bool):
            raise EventError("$.lost_events.cpu must be an integer or null")
        try:
            cpu = int(cpu)
        except (TypeError, ValueError) as error:
            raise EventError("$.lost_events.cpu must be an integer or null") from error
        if cpu < 0:
            raise EventError("$.lost_events.cpu must be an integer or null")
    node_name = root.get("node_name")
    if node_name is not None and not isinstance(node_name, str):
        raise EventError("$.node_name must be a string or null")
    return {
        "schema": HUBBLE_LOSS_SCHEMA,
        "observed_at": observed_at,
        "source": source,
        "num_events_lost": count,
        "cpu": cpu,
        "node_name": node_name,
    }


def _event_time(root: dict[str, Any]) -> str:
    return _string(root.get("time"), "$.time")


def _process(
    event: dict[str, Any], path: str, *, require_binary: bool
) -> tuple[str | None, dict[str, str] | None]:
    process = _object(event.get("process"), f"{path}.process")
    raw_binary = process.get("binary")
    if require_binary:
        binary = _string(raw_binary, f"{path}.process.binary")
    elif raw_binary is None or raw_binary == "":
        binary = None
    elif isinstance(raw_binary, str):
        binary = raw_binary
    else:
        raise EventError(f"{path}.process.binary must be a string or null")
    return binary, _pod(process.get("pod"))


def _image_digest(process: Any) -> str | None:
    """Return the sha256 image digest Tetragon reports for the container, if any."""

    pod = process.get("pod") if isinstance(process, dict) else None
    container = pod.get("container") if isinstance(pod, dict) else None
    image = container.get("image") if isinstance(container, dict) else None
    image_id = image.get("id") if isinstance(image, dict) else None
    if not isinstance(image_id, str):
        return None
    match = re.search(r"sha256:[0-9a-f]{64}", image_id)
    return match.group(0) if match else None


def parse_tetragon(
    document: Any,
    *,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
    environment: str | None = None,
    run_id: str | None = None,
    exec_policy: Any = None,
) -> dict[str, Any]:
    root = _object(document, "$")
    observed_at = _event_time(root)
    event_names = [name for name in ("process_exec", "process_kprobe") if name in root]
    if len(event_names) != 1:
        raise EventError("event must contain exactly one supported Tetragon event")
    event_name = event_names[0]
    event = _object(root[event_name], f"$.{event_name}")
    if event_name == "process_exec":
        binary, pod = _process(event, "$.process_exec", require_binary=True)
        assert binary is not None
        normalized = _base(
            observed_at,
            "tetragon",
            "process_exec",
            data_origin,
            environment=environment,
            run_id=run_id,
        )
        allowlist = tuple(allowed_engine_binaries or ())
        normalized.update(
            {
                "binary": binary,
                "process_attribution": "available",
                "pod": pod,
                "pod_attribution": "available" if pod is not None else "unavailable",
                "path": None,
                "access": None,
                "rule_id": "TG-EXEC-01",
                "action": "observed",
                "action_raw": "observed",
                "action_mode": "monitor",
                "allowed_binary": binary in allowlist if allowlist else None,
            }
        )
        if exec_policy is not None:
            parent = event.get("parent")
            parent_binary = (
                parent.get("binary")
                if isinstance(parent, dict) and isinstance(parent.get("binary"), str) and parent.get("binary")
                else None
            )
            normalized.update(
                exec_policy.classify_exec(
                    binary=binary,
                    parent=parent_binary,
                    image=_image_digest(event.get("process")),
                    observed_at=observed_at,
                )
            )
        return normalized

    binary, pod = _process(event, "$.process_kprobe", require_binary=False)
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
    action_mode = {
        "KPROBE_ACTION_OVERRIDE": "enforce",
        "KPROBE_ACTION_POST": "monitor",
    }.get(action.upper(), "unknown")
    normalized = _base(
        observed_at,
        "tetragon",
        "file_access",
        data_origin,
        environment=environment,
        run_id=run_id,
    )
    normalized.update(
        {
            "binary": binary,
            "process_attribution": (
                "available" if binary is not None else "unavailable"
            ),
            "pod": pod,
            "pod_attribution": "available" if pod is not None else "unavailable",
            "path": file_path,
            "access": access,
            "policy_name": policy_name,
            "rule_id": match.group(1).upper(),
            "action": action,
            "action_raw": action,
            "action_mode": action_mode,
        }
    )
    if exec_policy is not None and normalized["rule_id"] == "TG-CACHE-01":
        normalized.update(exec_policy.classify_cache_write(file_path))
    return normalized


def transform_lines(
    lines: Iterable[str],
    *,
    kind: str,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
    environment: str | None = None,
    run_id: str | None = None,
    exec_policy: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted, rejected, _losses = transform_lines_with_loss(
        lines,
        kind=kind,
        data_origin=data_origin,
        allowed_engine_binaries=allowed_engine_binaries,
        environment=environment,
        run_id=run_id,
        exec_policy=exec_policy,
    )
    return accepted, rejected


def transform_lines_with_loss(
    lines: Iterable[str],
    *,
    kind: str,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
    environment: str | None = None,
    run_id: str | None = None,
    exec_policy: Any = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Transform lines while retaining only line numbers and safe error reasons."""

    if kind not in {"hubble", "tetragon"}:
        raise ValueError("kind must be hubble or tetragon")
    if data_origin not in {"synthetic", "real"}:
        raise ValueError("data_origin must be synthetic or real")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            document = json.loads(line)
            if kind == "hubble":
                loss = parse_hubble_loss(document)
                if loss is not None:
                    losses.append(loss)
                    continue
                item = parse_hubble(
                    document,
                    data_origin=data_origin,
                    environment=environment,
                    run_id=run_id,
                )
            else:
                item = parse_tetragon(
                    document,
                    data_origin=data_origin,
                    allowed_engine_binaries=allowed_engine_binaries,
                    environment=environment,
                    run_id=run_id,
                    exec_policy=exec_policy,
                )
        except (json.JSONDecodeError, EventError, RecursionError) as error:
            rejected.append({"line": line_number, "reason": str(error)})
            continue
        accepted.append(item)
    return accepted, rejected, losses


def ingest_file(
    input_path: str | Path,
    data_dir: str | Path,
    *,
    kind: str,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
    environment: str | None = None,
    run_id: str | None = None,
    exec_policy: Any = None,
) -> dict[str, Any]:
    with Path(input_path).open("r", encoding="utf-8") as source:
        accepted, rejected, losses = transform_lines_with_loss(
            source,
            kind=kind,
            data_origin=data_origin,
            allowed_engine_binaries=allowed_engine_binaries,
            environment=environment,
            run_id=run_id,
            exec_policy=exec_policy,
        )
    store = JsonlStore(Path(data_dir) / "observations.jsonl")
    for item in accepted:
        store.append(item)
    by_source: Counter[str] = Counter()
    for loss in losses:
        by_source[loss["source"]] += loss["num_events_lost"]
    return {
        "processed": len(accepted) + len(rejected) + len(losses),
        "accepted": len(accepted),
        "rejected": rejected,
        "lost_events_records": len(losses),
        "lost_events_total": (
            sum(item["num_events_lost"] for item in losses)
            if kind == "hubble"
            else None
        ),
        "lost_events_by_source": (
            dict(by_source)
            if kind == "hubble"
            else None
        ),
        "last_lost_at": (
            max((item["observed_at"] for item in losses), default=None)
            if kind == "hubble"
            else None
        ),
    }


class ObservationQuery:
    """Read-only aggregation for normalized observations."""

    FILTERS = {
        "source",
        "kind",
        "verdict",
        "rule_id",
        "data_origin",
        "environment",
        "run_id",
    }

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
        dimensions = (
            "source",
            "kind",
            "verdict",
            "rule_id",
            "data_origin",
            "environment",
            "run_id",
        )
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
        real_hubble_dropped = sum(
            1
            for item in filtered
            if item.get("source") == "hubble"
            and item.get("verdict") == "dropped"
            and item.get("data_origin") == "real"
        )
        hubble_items = [item for item in filtered if item.get("source") == "hubble"]
        hubble_dropped = [
            item for item in hubble_items if item.get("verdict") == "dropped"
        ][:25]
        hubble_other = [
            item for item in hubble_items if item.get("verdict") != "dropped"
        ]
        hubble_observations = (
            hubble_dropped + hubble_other[: 50 - len(hubble_dropped)]
        )
        return {
            "counts": counts,
            "origins_by_kind": origins_by_kind,
            "verification": {"real_hubble_dropped": real_hubble_dropped},
            "hubble_observations": hubble_observations,
            "observations": filtered[:50],
            "corrupt_lines": len(corrupt),
            "corrupt_details": corrupt[:20],
            "generated_at": _utc_now(),
        }
