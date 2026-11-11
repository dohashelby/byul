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


class UnsupportedTetragonEvent(EventError):
    """A valid Tetragon envelope for an event this adapter does not analyze."""


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


def _matches_engine(event: dict[str, Any], intent: Any) -> bool:
    process = event.get("process")
    pod = process.get("pod") if isinstance(process, dict) else None
    labels = pod.get("pod_labels") if isinstance(pod, dict) else None
    container = pod.get("container") if isinstance(pod, dict) else None
    container_name = container.get("name") if isinstance(container, dict) else None
    return (
        isinstance(pod, dict)
        and pod.get("namespace") == intent.namespace
        and isinstance(labels, dict)
        and all(labels.get(key) == value for key, value in intent.engine.labels.items())
        and (intent.engine.container_name is None or not isinstance(container_name, str)
             or not container_name or container_name == intent.engine.container_name)
    )


def parse_tetragon(
    document: Any,
    *,
    data_origin: str,
    allowed_engine_binaries: tuple[str, ...] | list[str] | None = None,
    environment: str | None = None,
    run_id: str | None = None,
    exec_policy: Any = None,
    identity_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    identity = None
    if identity_index is not None:
        from .attribution import bind_event
        try:
            document, identity = bind_event(_object(document, "$"), identity_index)
        except (TypeError, AttributeError, KeyError, ValueError):
            identity = {"state":"unavailable","source":None,"reason":"identity_evidence_invalid"}
    root = _object(document, "$")
    observed_at = _event_time(root)
    event_names = [name for name in root if name.startswith("process_")]
    if len(event_names) != 1:
        raise EventError("event must contain exactly one process_* Tetragon event")
    event_name = event_names[0]
    event = _object(root[event_name], f"$.{event_name}")
    if event_name not in {"process_exec", "process_kprobe", "process_lsm"}:
        raise UnsupportedTetragonEvent(f"unsupported Tetragon event: {event_name}")
    if event_name == "process_exec":
        raw_process = event.get("process")
        if not isinstance(raw_process, dict) and (exec_policy is not None or identity_index is not None):
            normalized = _base(observed_at, "tetragon", "process_exec", data_origin,
                               environment=environment, run_id=run_id)
            normalized.update({"binary": None, "process_attribution": "unavailable",
                               "pod": None, "pod_attribution": "unavailable",
                               "container_attribution": "unavailable", "path": None,
                               "access": None, "rule_id": "TG-EXEC-01", "action": "observed",
                               "action_raw": "observed", "action_mode": "monitor", "allowed_binary": None})
            if exec_policy is not None:
                normalized.update(exec_policy.classify_exec(
                    binary="", parent=None, image=None, observed_at=observed_at, pod_uid=None))
            if identity is not None:
                normalized["identity"] = identity
            return normalized
        binary, pod = _process(event, "$.process_exec", require_binary=identity_index is None)
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
                "process_attribution": "available" if binary is not None else "unavailable",
                "pod": pod,
                "pod_attribution": "available" if pod is not None else "unavailable",
                "container_attribution": (
                    "available" if isinstance(raw_process.get("pod"), dict)
                    and isinstance(raw_process["pod"].get("container"), dict)
                    and isinstance(raw_process["pod"]["container"].get("name"), str)
                    and raw_process["pod"]["container"]["name"] else "unavailable"
                ),
                "path": None,
                "access": None,
                "rule_id": "TG-EXEC-01",
                "action": "observed",
                "action_raw": "observed",
                "action_mode": "monitor",
                "allowed_binary": (
                    binary in allowlist
                    if binary is not None and allowlist and (exec_policy is None or _matches_engine(event, exec_policy.intent))
                    else None
                ),
            }
        )
        # Suspect paths are suspicious in every Pod. Engine phases, exceptions,
        # and the engine cache root require positive selector attribution.
        if exec_policy is not None and _matches_engine(event, exec_policy.intent):
            parent = event.get("parent")
            parent_binary = (
                parent.get("binary")
                if isinstance(parent, dict) and isinstance(parent.get("binary"), str) and parent.get("binary")
                else None
            )
            normalized.update(
                exec_policy.classify_exec(
                    binary=binary or "",
                    parent=parent_binary,
                    image=_image_digest(event.get("process")),
                    observed_at=observed_at,
                    pod_uid=(raw_process["pod"].get("uid")
                             if isinstance(raw_process.get("pod"), dict) else None),
                )
            )
        elif exec_policy is not None and "pod" in raw_process and not isinstance(raw_process["pod"], dict):
            normalized.update(exec_policy.classify_exec(
                binary=binary, parent=None, image=_image_digest(raw_process),
                observed_at=observed_at, pod_uid=None))
        elif exec_policy is not None and binary in exec_policy.intent.suspect_binaries:
            parent = event.get("parent")
            parent_binary = parent.get("binary") if isinstance(parent, dict) else None
            normalized.update(exec_policy.classify_suspect(
                binary, parent=parent_binary if isinstance(parent_binary, str) and parent_binary else None,
                image=_image_digest(event.get("process")),
            ))
        if identity is not None:
            normalized["identity"] = identity
        return normalized

    binary, pod = _process(event, "$.process_kprobe", require_binary=False)
    function_name = _string(event.get("function_name"), "$.process_kprobe.function_name")
    if (event_name == "process_lsm" and function_name != "file_open") or (event_name == "process_kprobe" and function_name not in {"security_file_permission", "security_file_open", "fd_install", "sys_openat", "__x64_sys_openat", "__arm64_sys_openat"}):
        raise UnsupportedTetragonEvent(f"unsupported kprobe function: {function_name}")
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
        if function_name.endswith("sys_openat") and isinstance(arg.get("string_arg"), str):
            file_path = arg["string_arg"].rstrip("\x00")
        if "int_arg" in arg:
            value = arg["int_arg"]
            try:
                mask = int(value)
            except (TypeError, ValueError) as error:
                raise EventError("int_arg is not an integer") from error
    code_rule = match.group(1).upper() == "TG-CODE-01"
    if not file_path and not code_rule:
        raise EventError("process_kprobe has no file_arg.path")
    if mask is None and function_name == "security_file_permission":
        raise EventError("process_kprobe has no int_arg")
    access = ("write" if mask & 2 else "read" if mask & 4 else "other") if function_name == "security_file_permission" else "open"
    action = _string(event.get("action"), "$.process_kprobe.action")
    action_mode = {
        "KPROBE_ACTION_OVERRIDE": "enforce",
        "KPROBE_ACTION_SIGKILL": "enforce",
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
    if code_rule:
        from .codeload import classify_code_open
        classify_code_open(normalized, event, exec_policy, identity)
    if identity is not None:
        normalized["identity"] = identity
    if (normalized["rule_id"] == "TG-HEALTH-01" and file_path == "/run/byul-runtime-sentinel/read.txt"
        and identity is not None and identity.get("state") == "confirmed"
        and identity.get("container_name") == "sentinel" and action.upper() == "KPROBE_ACTION_POST"):
        normalized["observation_purpose"] = "health_check"
    return normalized


def parse_management_access(document: Any, *, data_origin: str, environment=None, run_id=None) -> dict[str, Any]:
    """Consume the local listener journal; actor identity remains unattributed."""
    from datetime import datetime, timezone
    document = _object(document, "management access")
    if document.get("schema") != "runtime-management-access/1.0":
        raise EventError("management access schema is invalid")
    event_id = document.get("event_id")
    if not isinstance(event_id, str) or re.fullmatch(r"[0-9a-f]{32}", event_id) is None:
        raise EventError("management event_id is invalid")
    stamp = document.get("recorded_at")
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if observed.tzinfo is None or (observed.astimezone(timezone.utc)-datetime.now(timezone.utc)).total_seconds() > 60:
            raise ValueError
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise EventError("management recorded_at must be a timezone timestamp within clock tolerance") from None
    scope = _object(document.get("scope"), "management scope")
    cleaned = {}
    for name in ("target_ref", "policy_ref", "environment", "run_id"):
        value = scope.get(name)
        if value is not None and (not isinstance(value, str) or re.fullmatch(r"[\w.:-]{1,128}", value) is None):
            raise EventError("management scope reference is invalid")
        cleaned[name] = value
    for name, configured in (("environment", environment), ("run_id", run_id)):
        if configured is not None and cleaned[name] != configured:
            raise EventError("management scope differs from configured feed")
    method, path, status = document.get("method"), document.get("path"), document.get("status_code")
    if not isinstance(method,str) or re.fullmatch(r"[A-Z]{1,32}",method) is None:
        raise EventError("management method is invalid")
    if not isinstance(path,str) or not path or len(path)>2048 or any(ord(c)<32 or ord(c)==127 for c in path) or "?" in path or "#" in path:
        raise EventError("management path is invalid")
    # Journal paths are already redacted; reject external authorities to avoid
    # surfacing userinfo from an untrusted or older writer.
    if "://" in path and "@" in path.split("://",1)[1].split("/",1)[0]:
        raise EventError("management authority contains userinfo")
    if type(status) is not int or not 100 <= status <= 599:
        raise EventError("management status_code is invalid")
    allowed = document.get("allowed")
    if allowed is not None and type(allowed) is not bool:
        raise EventError("management allowed is invalid")
    for field in ("allowed_count", "denied_count"):
        if type(document.get(field)) is not int or document[field]<0:
            raise EventError("management count is invalid")
    recording = _object(document.get("recording"), "management recording")
    if type(recording.get("write_failure_count")) is not int or recording["write_failure_count"]<0:
        raise EventError("management write failure count is invalid")
    item = _base(stamp, "management_listener", "management_access", data_origin,
                 environment=cleaned["environment"], run_id=cleaned["run_id"])
    item.update({"event_id":event_id,"target_ref":cleaned["target_ref"],"policy_ref":cleaned["policy_ref"],
                 "actor":{"state":"unattributed","principal_id":None},"method":method,"path":path,
                 "status_code":status,"allowed":allowed,"allowed_count":document["allowed_count"],
                 "denied_count":document["denied_count"],"write_failure_count":recording["write_failure_count"],
                 "verdict":"allowed" if allowed is True else "denied" if allowed is False else "unknown"})
    return item


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
    identity_index: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Transform lines while retaining only line numbers and safe error reasons."""

    if kind not in {"hubble", "tetragon", "management_listener"}:
        raise ValueError("kind must be hubble, tetragon or management_listener")
    if data_origin not in {"synthetic", "real"}:
        raise ValueError("data_origin must be synthetic or real")
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    losses: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        try:
            document = json.loads(line)
            if kind == "management_listener":
                item = parse_management_access(document, data_origin=data_origin, environment=environment, run_id=run_id)
            elif kind == "hubble":
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
                    identity_index=identity_index,
                )
        except UnsupportedTetragonEvent:
            continue
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
        "rule_ids",
        "data_origin",
        "environment",
        "run_id",
    }

    def __init__(self, path: str | Path) -> None:
        self.store = JsonlStore(path)

    def query(self, filters: dict[str, str] | None = None) -> dict[str, Any]:
        active = dict(filters or {})
        unknown = set(active) - self.FILTERS
        if unknown:
            raise ValueError(f"unknown observation filters: {', '.join(sorted(unknown))}")
        rule_ids = active.pop("rule_ids", None)
        rules = None
        if rule_ids is not None:
            rules = rule_ids.split(",")
            if (active.get("source") != "tetragon" or "rule_id" in active
                    or not 1 <= len(rules) <= 5 or len(set(rules)) != len(rules)
                    or any(not re.fullmatch(r"TG-[A-Z]+-[0-9]{2}", rule) for rule in rules)):
                raise ValueError("rule_ids requires source=tetragon and 1 through 5 distinct TG rule IDs; cannot combine with rule_id")
        records, corrupt, history_truncated = self.store.read_recent()
        filtered = [
            item
            for item in records
            if all(item.get(name) == value for name, value in active.items())
            and (rules is None or item.get("rule_id") in rules)
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
        observations = filtered[:50]
        if rules is not None:
            # One protected tail read for the whole group; reserve ten rows for
            # each rule before applying the existing 50-row response ceiling.
            selected = Counter()
            observations = []
            for item in filtered:
                rule = item["rule_id"]
                if selected[rule] < 10:
                    observations.append(item)
                    selected[rule] += 1
        return {
            "counts": counts,
            "origins_by_kind": origins_by_kind,
            "verification": {"real_hubble_dropped": real_hubble_dropped},
            "hubble_observations": hubble_observations,
            "observations": observations,
            "corrupt_lines": len(corrupt),
            "corrupt_details": corrupt[:20],
            "window":{"truncated":history_truncated,"counts_lower_bound":history_truncated,
                      "max_bytes":4*1024*1024,"max_records":4096,"scope":"recent_journal_tail",
                      "rule_groups":{"max_rules":5,"per_rule_limit":10}},
            "generated_at": _utc_now(),
        }
