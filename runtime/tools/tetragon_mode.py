"""Tetragon 1.7.1 mode switch and policy replacement without a monitoring gap."""

from __future__ import annotations

import json
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import time
from urllib.parse import quote
from typing import Callable, Any

from serving_runtime.enforcement.tetragon import comparable_spec


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


def _read_manifest(run, context, namespace, name):
    result = _try_run(run, _kubectl(context, namespace) +
                      ["get", "tracingpolicynamespaced", name, "-o", "json", "--ignore-not-found=true"])
    if result is None or result.returncode:
        return None
    if not result.stdout.strip():
        return {}  # confirmed absence, distinct from a failed read
    try:
        document = json.loads(result.stdout)
        return document if isinstance(document, dict) else None
    except (ValueError, TypeError):
        return None


def _delete_owned(run, context, namespace, name, uid, *, resource_version=None):
    options = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": uid}}
    if resource_version is not None:
        options["preconditions"]["resourceVersion"] = resource_version
    # kubectl delete --raw accepts a local DeleteOptions body; preconditions are
    # enforced atomically by the API server, including concurrent name reuse.
    with tempfile.TemporaryDirectory() as temporary:
        body = Path(temporary) / "delete-options.json"
        body.write_text(json.dumps(options), encoding="utf-8")
        uri = ("/apis/cilium.io/v1alpha1/namespaces/" + quote(namespace, safe="")
               + "/tracingpoliciesnamespaced/" + quote(name, safe=""))
        return _try_run(run, _kubectl(context, namespace) + ["delete", "--raw=" + uri, "-f", str(body)])


def replace_after_load(context: str, policy_namespace: str, old_name: str, new_manifest: str | Path,
                       *, expected_mode: str, verify_effect: Callable[[dict], bool],
                       tetragon_namespace: str = "kube-system", run: Callable[[list[str]], Any] = _run,
                       load_timeout_s: float = 10.0, poll_interval_s: float = 0.2,
                       sleep: Callable[[float], Any] = time.sleep,
                       clock: Callable[[], float] = time.monotonic) -> dict:
    """Remove the old policy only after exact spec, MODE and a caller's live probe.

    verify_effect receives the loaded policy row and must return True only for
    the required effect on this new policy. Reuse the existing trial/effect
    evaluator for enforcement. Exceptions or missing evidence keep the old one.
    This helper verifies the selected single agent, like switch_mode.
    """
    if not (0 < load_timeout_s <= 30 and 0 < poll_interval_s <= 1):
        raise ValueError("replacement load wait must be bounded to at most 30 seconds")
    document = json.loads(Path(new_manifest).read_text(encoding="utf-8"))
    metadata = document.get("metadata") or {}
    new_name, spec = metadata.get("name"), document.get("spec")
    if (document.get("kind") != "TracingPolicyNamespaced" or not isinstance(new_name, str)
        or not new_name or new_name == old_name or metadata.get("namespace") != policy_namespace
        or not isinstance(spec, dict) or not spec.get("kprobes")):
        raise ValueError("replacement requires a new namespaced policy with an explicit spec")
    if expected_mode not in {"monitor", "enforce"} or not callable(verify_effect):
        raise ValueError("replacement requires an expected MODE and a live effect verifier")
    modes = [o.get("value") for o in spec.get("options", []) if o.get("name") == "policy-mode"]
    if modes != [expected_mode]:
        raise ValueError("replacement spec must declare the expected policy-mode exactly once")
    old = _read_policy(run, context, tetragon_namespace, old_name, policy_namespace)
    if old is None or old["state"] != "enabled":
        return {"ok": False, "reason": "old policy not enabled"}
    old_document = _read_manifest(run, context, policy_namespace, old_name)
    old_metadata = (old_document or {}).get("metadata") or {}
    old_uid, old_version = old_metadata.get("uid"), old_metadata.get("resourceVersion")
    if (old_metadata.get("name") != old_name or old_metadata.get("namespace") != policy_namespace
        or not isinstance(old_uid, str) or not old_uid or not isinstance(old_version, str) or not old_version):
        return {"ok": False, "reason": "old policy object identity unavailable; old policy kept", "old": old}
    existing = _read_manifest(run, context, policy_namespace, new_name)
    if existing is None or existing:
        return {"ok": False, "reason": "new policy name must be confirmed absent; old policy kept", "old": old}
    created = _try_run(run, _kubectl(context, policy_namespace) + ["create", "-f", str(new_manifest), "-o", "json"])
    # A failed/timed-out create can leave an object, but ownership is unknown.
    # Never delete by name after such a failure.
    created_uid = None
    if created is not None and created.returncode == 0:
        try:
            created_document = json.loads(created.stdout)
            created_metadata = created_document.get("metadata") or {}
            if created_metadata.get("name") == new_name and created_metadata.get("namespace") == policy_namespace:
                created_uid = created_metadata.get("uid")
        except (ValueError, TypeError, AttributeError):
            pass
    if not isinstance(created_uid, str) or not created_uid:
        return {"ok": False, "reason": "new policy create unverified; old kept and no unowned cleanup attempted",
                "old": old, "cleanup_ok": False}
    # Kubernetes create acknowledgment precedes the node-local Tetragon load.
    # Wait only for an absent/loading row; explicit disabled/error/wrong MODE
    # states remain failures and never authorize removing the old policy.
    deadline = clock() + load_timeout_s
    new = None
    for _ in range(int(load_timeout_s / poll_interval_s) + 2):
        new = _read_policy(run, context, tetragon_namespace, new_name, policy_namespace)
        if new is not None and new["state"] != "loading":
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep(min(poll_interval_s, remaining))

    def exact_spec():
        actual = _read_manifest(run, context, policy_namespace, new_name)
        try:
            return (bool(actual) and actual.get("kind") == document["kind"]
                    and (actual.get("metadata") or {}).get("name") == new_name
                    and (actual.get("metadata") or {}).get("namespace") == policy_namespace
                    and (actual.get("metadata") or {}).get("uid") == created_uid
                    and comparable_spec(actual.get("spec") or {}) == comparable_spec(spec))
        except (ValueError, TypeError, AttributeError):
            return False

    valid = (new is not None and new["state"] == "enabled" and new["mode"] == expected_mode and exact_spec())
    effect_ok = False
    if valid:
        try:
            effect_ok = verify_effect(dict(new)) is True
        except Exception:
            effect_ok = False
    # A probe may change a MODE, replace an object or encounter an agent reload.
    after = _read_policy(run, context, tetragon_namespace, new_name, policy_namespace) if effect_ok else None
    valid = (effect_ok and after == new and exact_spec())
    if not valid:
        cleanup = _delete_owned(run, context, policy_namespace, new_name, created_uid)
        return {"ok": False, "reason": "new policy MODE/spec/effect unverified; old policy kept", "old": old, "new": new,
                "effect_verified": effect_ok, "cleanup_ok": cleanup is not None and cleanup.returncode == 0}
    current_old = _read_policy(run, context, tetragon_namespace, old_name, policy_namespace)
    if current_old != old:
        return {"ok": False, "reason": "old policy changed during verification; both policies kept", "old": old, "new": after}
    deleted = _delete_owned(run, context, policy_namespace, old_name, old_uid, resource_version=old_version)
    deleted_ok = deleted is not None and deleted.returncode == 0
    return {"ok": deleted_ok, "reason": "new policy MODE/spec/effect verified before old removal" if deleted_ok
            else "old removal failed; both policies may be active", "old": old, "new": after, "effect_verified": True}
