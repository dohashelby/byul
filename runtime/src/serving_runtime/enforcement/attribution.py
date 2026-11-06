"""Bind operational events to fresh, node-scoped container/process observations."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

SCHEMA = "runtime-tetragon-identities/1.0"
KERNEL_DATA = [
    {"index": 0, "type": "int", "source": "current_task", "resolve": "tgid", "label": "runtime_pid"},
    {"index": 1, "type": "uint64", "source": "current_task", "resolve": "group_leader.start_boottime", "label": "runtime_start_ns"},
    {"index": 2, "type": "uint32", "source": "current_task", "resolve": "nsproxy.pid_ns_for_children.ns.inum", "label": "runtime_pidns"},
]


def timestamp(value):
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at if at.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def read_identities(path: str | Path, *, context: str, now=None) -> dict:
    unavailable = {"state": "error", "records": [], "policies": [], "context": context}
    try:
        path = Path(path)
        if path.stat().st_size > 2 * 1024 * 1024:
            return unavailable
        value = json.loads(path.read_text())
        at = timestamp(value.get("generated_at"))
        age = ((now or datetime.now(timezone.utc)) - at).total_seconds() if at else None
        if (value.get("schema_version") != SCHEMA or value.get("context") != context
            or age is None or not -5 <= age <= 20 or value.get("state") != "ok"
            or not isinstance(value.get("records"), list) or not isinstance(value.get("policies"), list)):
            return unavailable
        required = ("node", "namespace", "pod_name", "pod_uid", "container_id", "container_name", "binary")
        if any(not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not row[k] for k in required)
               for row in value["records"]):
            return unavailable
        for row in value["records"]:
            if (not re.fullmatch(r"[a-f0-9]{64}",row["container_id"]) or not row["binary"].startswith("/")
                or row.get("stable") is not True or not isinstance(row.get("labels",{}),dict)
                or any(type(row.get(key)) is not int or row[key] <= 0
                       for key in ("node_pid","ns_pid","pidns","start_ticks","clock_ticks"))):
                return unavailable
        if any(not isinstance(p, dict) or not isinstance(p.get("selector", {}), dict) for p in value["policies"]):
            return unavailable
        return value
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return unavailable


def kernel_task(event: dict) -> dict | None:
    try:
        data = {arg["label"]: arg for arg in event.get("data") or []}
        pid = data["runtime_pid"]["int_arg"]
        start = data["runtime_start_ns"]["size_arg"]
        ns = data["runtime_pidns"]["uint_arg"]
        if (type(pid) is not int or pid <= 0 or type(ns) is not int or ns <= 0
            or not isinstance(start, str) or not start.isascii() or not start.isdecimal()
            or len(start) > 20 or not 0 < int(start) < 2**64):
            return None
        return {"pid": pid, "start_ns": start, "pidns": ns}
    except (KeyError, TypeError, ValueError):
        return None


def bind_event(root: dict, index: dict | None) -> tuple[dict, dict]:
    """Do not invent attribution from a policy name, path or PID alone."""
    names = [name for name in ("process_kprobe", "process_exec") if isinstance(root.get(name), dict)]
    result = {"state": "unavailable", "source": None, "reason": "identity_snapshot_unavailable"}
    if len(names) != 1 or not index or index.get("state") != "ok":
        return root, result
    event = root[names[0]]
    process = event.get("process") or {}
    pod = process.get("pod") or {}
    if not isinstance(process, dict) or not isinstance(pod, dict):
        return root, {**result, "reason": "malformed_native_identity"}
    at, sampled = timestamp(root.get("time")), timestamp(index.get("generated_at"))
    if at is None or sampled is None or not -30 <= (at - sampled).total_seconds() <= 5:
        return root, {**result, "reason": "event_outside_identity_window"}
    node = root.get("node_name")
    rows = [r for r in index.get("records", []) if isinstance(r, dict) and r.get("node") == node]
    task = kernel_task(event) if names[0] == "process_kprobe" else None
    candidates = []
    if task:
        for row in rows:
            ticks, hz = row.get("start_ticks"), row.get("clock_ticks")
            if (type(ticks) is not int or type(hz) is not int or min(ticks, hz) <= 0
                or row.get("pidns") != task["pidns"] or process.get("pid") != task["pid"]):
                continue
            if not ticks * 1_000_000_000 <= int(task["start_ns"]) * hz < (ticks + 1) * 1_000_000_000:
                continue
            policies = [p for p in index.get("policies", []) if p.get("name") == event.get("policy_name")
                        and p.get("namespace") == row.get("namespace") and p.get("kernel_identity") is True
                        and all(row.get("labels", {}).get(k) == v for k, v in p.get("selector", {}).items())]
            if len(policies) == 1 and row.get("stable") is True:
                candidates.append(row)
        source = "kernel_task_procfs"
    else:
        cid = process.get("docker")
        if isinstance(cid, str) and re.fullmatch(r"[a-f0-9]{12,64}", cid):
            # Process IDs in a nested node need not be kernel-global IDs.
            # A native exec_id + binary + container ID still identifies the
            # native actor; only fill its workload, never a guessed host PID.
            if (names[0] == "process_exec" and type(process.get("pid")) is int
                and process["pid"] > 0 and process.get("exec_id") and process.get("binary")):
                by_container = {r.get("container_id"): r for r in rows
                                if str(r.get("container_id", "")).startswith(cid)}
                candidates = list(by_container.values())
        source = "native_exec_cri_container"
    if len(candidates) != 1:
        return root, {**result, "state": "ambiguous" if candidates else "unavailable",
                      "reason": "multiple_identity_matches" if candidates else "no_identity_match"}
    row = candidates[0]
    conflict = None
    if any(pod.get(key) not in (None, "", row.get(other)) for key, other in
           (("namespace", "namespace"), ("uid", "pod_uid"), ("name", "pod_name"), ("pod_name", "pod_name"))):
        conflict = "native_workload_conflict"
    raw_container = pod.get("container") or {}
    if not isinstance(raw_container, dict) or raw_container.get("name") not in (None, "", row.get("container_name")):
        conflict = "native_container_conflict"
    if task and process.get("binary") not in (None, "", row.get("binary")):
        conflict = "native_binary_conflict"
    if conflict:
        cleared = copy.deepcopy(root)
        cleared[names[0]]["process"]["pod"] = None
        return cleared, {**result, "state": "conflict", "reason": conflict}
    enriched = copy.deepcopy(root)
    actor = enriched[names[0]]["process"]
    if task:
        actor["binary"] = row["binary"]
    actor["pod"] = {**pod, "namespace": row["namespace"], "name": row["pod_name"],
                    "uid": row["pod_uid"], "pod_labels": row.get("labels", {}),
                    "container": {**raw_container, "name": row["container_name"],
                                  **({"pid": row["ns_pid"]} if task else {})}}
    result = {"state": "confirmed", "source": source, "reason": None,
              "context": index.get("context"),
              "node": node, "pod_uid": row["pod_uid"], "container_id": row["container_id"],
              "container_name": row["container_name"], "pid": process.get("pid"),
              "exec_id": process.get("exec_id"), "sampled_at": index["generated_at"]}
    if task:
        result.update(task, ns_pid=row.get("ns_pid"), node_pid=row.get("node_pid"))
    return enriched, result
