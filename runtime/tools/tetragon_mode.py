"""Tetragon 1.7.1 mode switch and policy replacement without a monitoring gap."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Callable, Any


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=70, check=False)


def _kubectl(context: str, namespace: str) -> list[str]:
    return ["kubectl", "--context", context, "-n", namespace]


def _tetra(context: str, tetragon_namespace: str, *args: str) -> list[str]:
    return _kubectl(context, tetragon_namespace) + ["exec", "daemonset/tetragon", "-c", "tetragon", "--", "tetra", *args]


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
        if values["name"] == name and values["namespace"] == namespace:
            return values
    return None


def _read_policy(run: Callable[[list[str]], Any], context: str, tetragon_namespace: str,
                 policy_name: str, policy_namespace: str) -> dict[str, str] | None:
    result = run(_tetra(context, tetragon_namespace, "tracingpolicy", "list"))
    if result.returncode:
        return None
    return parse_policy_row(result.stdout, policy_name, policy_namespace)


def switch_mode(context: str, policy_namespace: str, policy_name: str, target_mode: str,
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
    changed = run(command)
    after = _read_policy(run, context, tetragon_namespace, policy_name, policy_namespace)
    if changed.returncode == 0 and after is not None and after["id"] == before["id"] and after["state"] == "enabled" and after["mode"] == target_mode:
        return {"ok": True, "reason": "mode verified", "before": before, "after": after}
    rollback = run(_tetra(context, tetragon_namespace, "tp", "set-mode", "--namespace", policy_namespace,
                          policy_name, before["mode"]))
    restored = _read_policy(run, context, tetragon_namespace, policy_name, policy_namespace)
    rollback_ok = (rollback.returncode == 0 and restored is not None and restored["id"] == before["id"]
                   and restored["state"] == "enabled" and restored["mode"] == before["mode"])
    return {"ok": False, "reason": "mode switch failed; rollback verified" if rollback_ok else "mode switch and rollback unverified",
            "before": before, "after": after, "rollback": restored, "rollback_ok": rollback_ok}


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
    applied = run(_kubectl(context, policy_namespace) + ["apply", "-f", str(new_manifest)])
    new = _read_policy(run, context, tetragon_namespace, new_name, policy_namespace)
    if applied.returncode or new is None or new["state"] != "enabled":
        run(_kubectl(context, policy_namespace) + ["delete", "tracingpolicynamespaced", new_name, "--ignore-not-found=true"])
        return {"ok": False, "reason": "new policy load failed; old policy kept", "old": old, "new": new}
    deleted = run(_kubectl(context, policy_namespace) + ["delete", "tracingpolicynamespaced", old_name])
    return {"ok": deleted.returncode == 0, "reason": "new policy enabled before old removal" if deleted.returncode == 0 else "old removal failed; both policies may be active",
            "old": old, "new": new}
