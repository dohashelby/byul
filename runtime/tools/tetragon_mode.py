"""Tetragon 1.7.1 mode switch and policy replacement without a monitoring gap."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from typing import Callable, Any


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=70, check=False)


def _kubectl(context: str, namespace: str) -> list[str]:
    return ["kubectl", "--context", context, "-n", namespace]


def _tetra(context: str, tetragon_namespace: str, *args: str) -> list[str]:
    return _kubectl(context, tetragon_namespace) + ["exec", "daemonset/tetragon", "-c", "tetragon", "--", "tetra", *args]


def _try_run(run: Callable[[list[str]], Any], command: list[str]) -> Any | None:
    try:
        return run(command)
    except (OSError, subprocess.TimeoutExpired):
        # A timed-out set-mode may already have changed the kernel policy.
        # Treat the result as unknown and continue to verification/rollback.
        return None


def parse_policy_row(output: str, name: str, namespace: str) -> dict[str, str] | None:
    lines = [line.split() for line in output.splitlines() if line.strip()]
    if not lines:
        return None
    header = [column.upper() for column in lines[0]]
    needed = {"ID", "NAME", "STATE", "NAMESPACE", "MODE"}
    if not needed <= set(header):
        return None
    index = {column: header.index(column) for column in needed}
    for row in lines[1:]:
        if len(row) <= max(index.values()):
            continue
        values = {column.lower(): row[pos] for column, pos in index.items()}
        # 1.7.1 prints memory as e.g. "2.00 MB", adding a token before MODE.
        # Never treat the unit as a policy mode.
        modes = [token for token in row[index["MODE"]:] if token in {"monitor", "enforce", "monitor_only"}]
        if len(modes) != 1:
            continue
        values["mode"] = modes[0]
        if values["name"] == name and values["namespace"] == namespace:
            return values
    return None


def _read_policy(run: Callable[[list[str]], Any], context: str, tetragon_namespace: str,
                 policy_name: str, policy_namespace: str) -> dict[str, str] | None:
    result = _try_run(run, _tetra(context, tetragon_namespace, "tracingpolicy", "list"))
    if result is None or result.returncode:
        return None
    return parse_policy_row(result.stdout, policy_name, policy_namespace)


def _switch_mode(context: str, policy_namespace: str, policy_name: str, target_mode: str,
                *, tetragon_namespace: str = "kube-system", run: Callable[[list[str]], Any] = _run) -> dict:
    """Switch an already loaded policy and verify both success and rollback."""
    if target_mode not in {"monitor", "enforce"}:
        raise ValueError("Tetragon 1.7.1 mode must be monitor or enforce")
    before = _read_policy(run, context, tetragon_namespace, policy_name, policy_namespace)
    if before is None or before["state"] != "enabled" or before["mode"] not in {"monitor", "enforce"}:
        return {"ok": False, "reason": "current policy state or mode unavailable", "before": before, "after": None}
    if before["mode"] == target_mode:
        return {"ok": True, "reason": "already in target mode", "before": before, "after": before}
    command = _tetra(context, tetragon_namespace, "tp", "set-mode", "--namespace", policy_namespace,
                     policy_name, target_mode)
    changed = _try_run(run, command)
    after = _read_policy(run, context, tetragon_namespace, policy_name, policy_namespace)
    if changed is not None and changed.returncode == 0 and after is not None and after["id"] == before["id"] and after["state"] == "enabled" and after["mode"] == target_mode:
        return {"ok": True, "reason": "mode verified", "before": before, "after": after}
    rollback = _try_run(run, _tetra(context, tetragon_namespace, "tp", "set-mode", "--namespace", policy_namespace,
                                   policy_name, before["mode"]))
    restored = _read_policy(run, context, tetragon_namespace, policy_name, policy_namespace)
    rollback_ok = (rollback is not None and rollback.returncode == 0 and restored is not None and restored["id"] == before["id"]
                   and restored["state"] == "enabled" and restored["mode"] == before["mode"])
    return {"ok": False, "reason": "mode switch failed; rollback verified" if rollback_ok else "mode switch and rollback unverified",
            "before": before, "after": after, "rollback": restored, "rollback_ok": rollback_ok}


def switch_mode(context: str, policy_namespace: str, policy_name: str, target_mode: str,
                *, tetragon_namespace: str = "kube-system", run: Callable[[list[str]], Any] = _run,
                history_path: str | Path | None = None) -> dict:
    result = _switch_mode(context, policy_namespace, policy_name, target_mode,
                          tetragon_namespace=tetragon_namespace, run=run)
    record = {"at": datetime.now(timezone.utc).isoformat(), "context": context,
              "namespace": policy_namespace, "policy": policy_name, "requested_mode": target_mode,
              **result}
    if history_path is not None:
        path = Path(history_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as output:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            result["history_error"] = "transition history could not be persisted"
    return result


def replace_after_load(context: str, policy_namespace: str, old_name: str, new_manifest: str | Path,
                       *, tetragon_namespace: str = "kube-system", run: Callable[[list[str]], Any] = _run) -> dict:
    """Add a new name, wait until enabled, then remove the old name."""
    document = json.loads(Path(new_manifest).read_text(encoding="utf-8"))
    metadata = document.get("metadata") or {}
    new_name = metadata.get("name")
    if not isinstance(new_name, str) or new_name == old_name or metadata.get("namespace") != policy_namespace:
        raise ValueError("replacement requires a new policy name in the same namespace")
    old = _read_policy(run, context, tetragon_namespace, old_name, policy_namespace)
    if old is None or old["state"] != "enabled":
        return {"ok": False, "reason": "old policy not enabled"}
    applied = _try_run(run, _kubectl(context, policy_namespace) + ["apply", "-f", str(new_manifest)])
    new = _read_policy(run, context, tetragon_namespace, new_name, policy_namespace)
    if applied is None or applied.returncode or new is None or new["state"] != "enabled":
        cleanup = _try_run(run, _kubectl(context, policy_namespace) + ["delete", "tracingpolicynamespaced", new_name, "--ignore-not-found=true"])
        return {"ok": False, "reason": "new policy load failed; old policy kept", "old": old, "new": new,
                "cleanup_ok": cleanup is not None and cleanup.returncode == 0}
    deleted = _try_run(run, _kubectl(context, policy_namespace) + ["delete", "tracingpolicynamespaced", old_name])
    deleted_ok = deleted is not None and deleted.returncode == 0
    return {"ok": deleted_ok, "reason": "new policy enabled before old removal" if deleted_ok else "old removal failed; both policies may be active",
            "old": old, "new": new}
