"""Transactional, monitor-first reconciliation. Only allowlisted summaries escape."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4

from .cilium import render_manifests as render_cilium
from .tetragon import comparable_spec, render_monitor_manifests

RESOURCES = {"tetragon": "tracingpolicynamespaced", "cilium": "ciliumnetworkpolicies"}
NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,252}")


class SyncFailure(Exception):
    def __init__(self, code):
        self.code = code


def digest(spec, component):
    value = comparable_spec(spec) if component == "tetragon" else spec
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def restorable_in_monitor(spec):
    """A live set-mode is not persisted in the CR: reloading must stay safe."""
    modes = [o.get("value") for o in spec.get("options", []) if o.get("name") == "policy-mode"]
    if modes:
        return modes == ["monitor"]
    if set(spec) - {"podSelector", "containerSelector", "kprobes", "lsmhooks", "tracepoints", "uprobes", "options"}:
        return False
    return all(action.get("action") == "Post"
               for kind in ("kprobes", "lsmhooks", "tracepoints", "uprobes")
               for probe in spec.get(kind, []) for selector in probe.get("selectors", [])
               for action in selector.get("matchActions", []))


def sync(intent, *, context, namespace, run, read_mode, verify_gateway,
         components=("tetragon",), cilium_apply=False, execute=False, before_apply=lambda: None):
    """Dry-run every desired object before any mutation; never replay mutations.

    read_mode verifies all scheduled Tetragon daemons, not a cached snapshot.
    Rollback uses optimistic resource versions and verifies spec and live mode.
    An ambiguous write is restored only when its live spec matches our candidate.
    """
    report = {"state": "failed", "code": "VALIDATION_FAILED", "dry_run": not execute,
              "steps": [], "policies": [], "components": []}
    changed = []
    transaction = uuid4().hex
    marker = "serving-runtime.local/sync-transaction"
    stage = "validation"
    base = ["kubectl", "--context", context, "-n", namespace]

    def command(args):
        result = run(base + args)
        if result.returncode:
            raise SyncFailure("EXECUTION_FAILED")
        return result

    def objects(component):
        result = command(["get", RESOURCES[component], "-o", "json"])
        return {o["metadata"]["name"]: o for o in json.loads(result.stdout)["items"]}

    def write(path, value):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(value, out)
            out.flush()
            os.fsync(out.fileno())

    def clean(obj):
        result = copy.deepcopy(obj)
        result.pop("status", None)
        for key in ("managedFields", "creationTimestamp", "generation", "uid"):
            result["metadata"].pop(key, None)
        return result

    def same(left, right, component):
        return left is not None and digest(left["spec"], component) == digest(right["spec"], component)

    def check_loaded(name, mode):
        observed = read_mode(name)
        if observed not in {"monitor", "monitor_only", "enforce"}:
            raise SyncFailure("SAFETY_REJECTED")
        if mode is not None and observed != mode:
            raise SyncFailure("SAFETY_REJECTED")
        return observed

    def passed():
        report["steps"].append({"stage": stage, "state": "passed"})

    with tempfile.TemporaryDirectory(prefix="runtime-enforcement-") as temporary:
        root = Path(temporary)
        try:
            if (list(components) not in [["tetragon"], ["tetragon", "cilium"]]
                    or type(cilium_apply) is not bool or (cilium_apply and "cilium" not in components)
                    or intent.namespace != namespace):
                raise SyncFailure("VALIDATION_FAILED")
            passed()
            stage = "render"
            desired = {"tetragon": render_monitor_manifests(intent)}
            if "cilium" in components:
                desired["cilium"] = render_cilium(intent)
            passed()
            stage = "dry-run"
            plan = []
            for component in components:
                if component == "cilium":
                    crd = command(["get", "crd", "ciliumnetworkpolicies.cilium.io", "--ignore-not-found=true", "-o", "json"])
                    if not crd.stdout.strip():
                        report["components"].append({"component": component, "state": "not_installed"})
                        for obj in desired[component].values():
                            report["policies"].append({"name": obj["metadata"]["name"], "component": component,
                                "action": "skipped", "mode": None, "spec_sha256": digest(obj["spec"], component)})
                        continue
                report["components"].append({"component": component, "state": "apply" if execute and (component == "tetragon" or cilium_apply) else "dry_run"})
                previous = objects(component)
                names = set()
                for obj in desired[component].values():
                    name = obj["metadata"]["name"]
                    if not NAME.fullmatch(name):
                        raise SyncFailure("VALIDATION_FAILED")
                    names.add(name)
                    old = previous.get(name)
                    action = "unchanged" if same(old, obj, component) else "replaced" if old else "created"
                    mode = check_loaded(name, None) if component == "tetragon" and old else "monitor" if component == "tetragon" else None
                    if (action == "replaced" and component == "tetragon"
                            and (mode == "enforce" or not restorable_in_monitor(old["spec"]))):
                        raise SyncFailure("SAFETY_REJECTED")
                    candidate = copy.deepcopy(obj)
                    if action != "unchanged":
                        candidate["metadata"].setdefault("annotations", {})[marker] = transaction
                    if old:
                        candidate["metadata"]["resourceVersion"] = old["metadata"]["resourceVersion"]
                    path = root / f"candidate-{len(plan)}.json"
                    backup = root / f"backup-{len(plan)}.json"
                    write(path, candidate)
                    if old:
                        write(backup, old)
                    verb = "replace" if old else "create"
                    # Server validates admission/schema even for unchanged objects.
                    if component == "cilium":
                        command(["apply", "--server-side=true", "--field-manager=runtime-enforcement", "--dry-run=server", "-f", str(path), "-o", "json"])
                        diff = run(base + ["diff", "--server-side=true", "--field-manager=runtime-enforcement", "-f", str(path)])
                        if diff.returncode not in {0, 1}:
                            raise SyncFailure("EXECUTION_FAILED")
                    command([verb, "--dry-run=server", "-f", str(path), "-o", "json"])
                    row = {"name": name, "component": component, "action": action, "mode": mode,
                           "spec_sha256": digest(obj["spec"], component)}
                    report["policies"].append(row)
                    plan.append((component, name, obj, old, mode, path, backup, row))
                for name, obj in sorted(previous.items()):
                    if name not in names and NAME.fullmatch(name):
                        report["policies"].append({"name": name, "component": component, "action": "extra",
                            "mode": None, "spec_sha256": digest(obj["spec"], component)})
            if len(report["policies"]) > 512:
                report["policies"] = report["policies"][:512]
                raise SyncFailure("VALIDATION_FAILED")
            passed()
            stage = "apply"
            for entry in plan:
                component, name, obj, old, mode, path, backup, row = entry
                if row["action"] == "unchanged" or not execute or (component == "cilium" and not cilium_apply):
                    continue
                before_apply()
                live = objects(component).get(name)
                if (live is None) != (old is None) or (old and (live["metadata"].get("uid"), live["metadata"]["resourceVersion"]) != (old["metadata"].get("uid"), old["metadata"]["resourceVersion"])):
                    raise SyncFailure("STATE_CHANGED")
                if component == "tetragon" and old:
                    check_loaded(name, mode)
                # Register before the call: timeout/nonzero can still have committed.
                changed.append(entry)
                command(["replace" if old else "create", "-f", str(path), "-o", "json"])
            report["steps"].append({"stage": stage, "state": "passed" if changed else "skipped"})
            stage = "verify"
            for component, name, obj, old, mode, path, backup, row in changed:
                if not same(objects(component).get(name), obj, component):
                    raise SyncFailure("EXECUTION_FAILED")
                if component == "tetragon":
                    # Loading is asynchronous; query again, never repeat a write.
                    deadline = time.monotonic() + 10
                    observed = read_mode(name)
                    while observed not in {"monitor", "monitor_only"}:
                        if time.monotonic() >= deadline:
                            raise SyncFailure("EXECUTION_FAILED")
                        time.sleep(0.2)
                        observed = read_mode(name)
                    row["mode"] = observed
            if changed:
                verify_gateway(any(e[0] == "cilium" for e in changed))
            report["steps"].append({"stage": stage, "state": "passed" if changed else "skipped"})
            report.update(state="succeeded" if execute else "checked", code="OK" if execute else "DRY_RUN")
        except Exception as error:
            report["steps"].append({"stage": stage, "state": "failed"})
            report["code"] = error.code if isinstance(error, SyncFailure) else "VALIDATION_FAILED"
            if changed:
                restored = True
                for component, name, obj, old, mode, path, backup, row in reversed(changed):
                    try:
                        live = objects(component).get(name)
                        if (same(live, old, component) and live["metadata"].get("uid") == old["metadata"].get("uid") if old else live is None):
                            if component == "tetragon" and old:
                                check_loaded(name, mode)
                            continue
                        if (not same(live, obj, component) or live["metadata"].get("annotations", {}).get(marker) != transaction):
                            raise SyncFailure("STATE_CHANGED")
                        if component == "tetragon":
                            if check_loaded(name, None) not in {"monitor", "monitor_only"}:
                                raise SyncFailure("SAFETY_REJECTED")
                        if old:
                            previous = clean(json.loads(backup.read_text()))
                            previous["metadata"]["resourceVersion"] = live["metadata"]["resourceVersion"]
                            restore = root / f"restore-{len(list(root.glob('restore-*')))}.json"
                            write(restore, previous)
                            command(["replace", "-f", str(restore), "-o", "json"])
                        else:
                            # Raw DELETE carries atomic UID/resourceVersion preconditions.
                            deletion = root / f"delete-{len(list(root.glob('delete-*')))}.json"
                            write(deletion, {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {
                                "uid": live["metadata"]["uid"], "resourceVersion": live["metadata"]["resourceVersion"]}})
                            version = "v1alpha1" if component == "tetragon" else "v2"
                            resource = "tracingpoliciesnamespaced" if component == "tetragon" else RESOURCES[component]
                            command(["delete", "--raw", f"/apis/cilium.io/{version}/namespaces/{namespace}/{resource}/{name}", "-f", str(deletion)])
                        actual = objects(component).get(name)
                        if not (same(actual, old, component) and actual["metadata"].get("uid") == old["metadata"].get("uid") if old else actual is None):
                            raise SyncFailure("EXECUTION_FAILED")
                        if component == "tetragon" and old:
                            check_loaded(name, mode)
                    except Exception:
                        restored = False
                try:
                    verify_gateway(any(e[0] == "cilium" for e in changed))
                except Exception:
                    restored = False
                report.update(state="rolled_back" if restored else "unknown", code="ROLLED_BACK" if restored else "STATUS_UNKNOWN")
                report["steps"].append({"stage": "rollback", "state": "passed" if restored else "unknown"})
    for name in ("validation", "render", "dry-run", "apply", "verify"):
        if not any(s["stage"] == name for s in report["steps"]):
            report["steps"].append({"stage": name, "state": "skipped"})
    return report
