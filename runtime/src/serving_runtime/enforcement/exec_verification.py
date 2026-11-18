"""Recompute execution-denial proof; an action flag alone never passes."""

from datetime import datetime
import hashlib
import json
from types import SimpleNamespace

from .exec_policy_render import execution_policy

NAMES = {"absolute", "path", "bash", "allowed", "os_system"}


def _cases(probe, monitor=False):
    rows = probe.get("cases", [])
    cases = {row.get("name"): row for row in rows}
    if len(rows) != 5 or set(cases) != NAMES or probe.get("alive") is not True:
        return False
    if monitor:
        return all(type(row.get("returncode")) is int and row["returncode"] == 0 for row in rows)
    return (
        all(type(cases[n].get("errno")) is int and cases[n]["errno"] in {1, 13} for n in ("absolute", "path", "bash"))
        and type(cases["os_system"].get("returncode")) is int
        and cases["os_system"]["returncode"] != 0
        and type(cases["allowed"].get("returncode")) is int
        and cases["allowed"]["returncode"] == 0
    )


def exemption_check(record):
    if "exempt" in record:
        value = record["exempt"].get("returncode")
        return type(value) is int and value == 0, True
    candidate = record["empty_exemption_candidate"]
    rows = candidate["engine_labels"]
    labels = {row["key"]: row["value"] for row in rows}
    denied = tuple(row["path"] for row in candidate["deny_paths"])
    if not labels or len(labels) != len(rows) or not denied:
        return False, False
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        return False, False
    intent = SimpleNamespace(
        namespace=record["namespace"],
        engine=SimpleNamespace(labels=labels),
        exec_deny_binaries=denied,
        exec_deny_exempt_callers=(),
    )
    spec = execution_policy(intent)["spec"]
    sha = hashlib.sha256(json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return sha == record["policy_spec_sha256"] and record["exemption_test"]["status"] == "not_configured", False


def reassess_execution(record):
    facts = {}
    try:
        probe, baseline = record["probe"], record["monitor_probe"]
        before, after = record["pod_before"], record["pod_after"]
        steps = record["steps"]
        facts["enforce_active"] = bool(
            isinstance(record.get("policy_id"), str)
            and bool(record["policy_id"])
            and len(steps) == 2
            and [s["target_mode"] for s in steps] == ["enforce", "monitor"]
            and all(
                s["ok"] is True
                and s["after"]["id"] == record["policy_id"]
                and s["before"]["id"] == record["policy_id"]
                and s["after"]["mode"] == s["target_mode"]
                for s in steps
            )
        )
        start, end = datetime.fromisoformat(probe["started_at"]), datetime.fromisoformat(probe["finished_at"])
        facts["event_bound"] = any(
            start <= datetime.fromisoformat(e["time"].replace("Z", "+00:00")) <= end
            and e["pod_uid"] == before["uid"] == record["pod_uid"]
            and e["policy"] == record["policy"]
            and e["function"] == "bprm_check_security"
            and e["action"] == "KPROBE_ACTION_OVERRIDE"
            and e["path"] in {"/bin/sh", "/usr/bin/sh", "/bin/dash", "/usr/bin/dash", "/usr/bin/bash"}
            for e in record["events"]
        )
        facts["exec_denied"] = _cases(probe)
        facts["monitor_exec_ok"] = _cases(baseline, monitor=True)
        facts["exemption_check"], applicable = exemption_check(record)
        if applicable:
            facts["exempt_allowed"] = facts["exemption_check"]
        facts["pod_stable"] = bool(
            before["uid"] == after["uid"]
            and before["containers"] == after["containers"]
            and before["containers"]
            and all(c["running"] is True for c in after["containers"])
        )
        facts["cleanup_ok"] = all(record["cleanup"].get(k) is True for k in ("monitor_restored", "namespace_deleted"))
        passed = bool(all(facts.values()) and record.get("kernel"))
    except (KeyError, ValueError, TypeError, AttributeError):
        passed = False
    return {
        **record,
        "facts": facts,
        "verification": {
            "status": "passed" if passed else "failed",
            "expected_effect": "exec_denial",
            "exec_denial": "confirmed" if passed else "unknown",
            "file_read_denial": "unknown",
            "process_termination": "unknown",
            "service_recovery": "unknown",
            "monitor_restored": record.get("cleanup", {}).get("monitor_restored") is True,
        },
    }
