"""Keep file denial, process termination and service recovery as separate claims."""

from __future__ import annotations

from datetime import datetime
from typing import Any

KERNEL_IDENTITY_DATA = [
    {"index": 0, "type": "int", "source": "current_task", "resolve": "tgid", "label": "proof_host_pid"},
    {"index": 1, "type": "uint64", "source": "current_task", "resolve": "start_boottime", "label": "proof_start_ns"},
    {"index": 2, "type": "uint32", "source": "current_task", "resolve": "nsproxy.pid_ns_for_children.ns.inum", "label": "proof_pidns"},
]


def task_matches_identity(task: dict, identity: dict | None) -> bool:
    """Bind a kernel task to CRI's container init, without relying on the exec cache."""
    if not identity or identity.get("ns_pid") != 1:
        return False
    try:
        ticks, hz = identity["start_ticks"], identity["clock_ticks"]
        start_ns = task["start_ns"]
        if isinstance(start_ns, str) and start_ns.isascii() and start_ns.isdecimal() and len(start_ns) <= 20:
            start_ns = int(start_ns)
        return (all(type(v) is int and v > 0 for v in
                    (ticks, hz, task["pid"], start_ns, task["pidns"]))
                and task["pidns"] == identity["pidns"]
                and ticks * 1_000_000_000 <= start_ns * hz < (ticks + 1) * 1_000_000_000)
    except (KeyError, TypeError):
        return False


def _time(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else None
    except ValueError:
        return None


def match_probe_events(lines: list[str], *, policy: str, namespace: str, pod_uid: str,
                       path: str, since: str, until: str | None = None,
                       main_process: bool = False, pid: int | None = None,
                       task_identity: dict | None = None) -> list[dict[str, Any]]:
    """Match a bounded trial window and exact workload; never export command args."""
    import json
    start, end = _time(since), _time(until) if until else None
    if start is None:
        return []
    matched = []
    for line in lines:
        try:
            raw = json.loads(line)
            names = [name for name in ("process_kprobe", "process_lsm") if name in raw]
            if len(names) != 1:
                continue
            event = raw[names[0]]
            process = event.get("process") or {}
            pod = process.get("pod") or {}
            at = _time(raw.get("time"))
            nspid = process.get("ns_pid", process.get("nspid", (pod.get("container") or {}).get("pid")))
            args = event.get("args") or []
            data = {arg.get("label"): arg for arg in event.get("data") or []}
            task = {}
            try:
                task = {"pid": int(data["proof_host_pid"]["int_arg"]),
                        "start_ns": int(data["proof_start_ns"]["size_arg"]),
                        "pidns": int(data["proof_pidns"]["uint_arg"])}
            except (KeyError, ValueError, TypeError):
                pass
            kernel_bound = (task_identity is not None and task_identity.get("pod_uid") == pod_uid
                            and task_identity.get("namespace") == namespace
                            and task_matches_identity(task, task_identity)
                            and process.get("pid") == task.get("pid")
                            and pod.get("namespace") in (None, "", namespace)
                            and pod.get("uid") in (None, "", pod_uid))
            attributed = pod.get("namespace") == namespace and pod.get("uid") == pod_uid
            if kernel_bound:
                nspid = 1  # independently observed CRI init + namespace + start tick
            exact = any(isinstance(arg, dict) and (
                (arg.get("file_arg") or {}).get("path") == path
                or str(arg.get("string_arg", "")).rstrip("\x00") == path
            ) for arg in args)
            if (event.get("policy_name") != policy or not (attributed or kernel_bound)
                or (task_identity is not None and not kernel_bound) or at is None or at < start
                or (end is not None and at > end) or not exact
                or (pid is not None and process.get("pid") != pid)
                or (main_process and nspid != 1 and pid is None)):
                continue
            matched.append({"time": raw["time"], "policy": policy, "pod_uid": pod_uid,
                            "path": path,
                            "pid": process.get("pid"), "exec_id": process.get("exec_id"),
                            "ns_pid": nspid, "function": event.get("function_name"),
                            "identity_source": "kernel_task" if kernel_bound else "tetragon_process_cache",
                            "kernel_task": {**task, "start_ns": str(task["start_ns"])} if kernel_bound else None,
                            "action": event.get("action"),
                            "access_mask": next((arg.get("int_arg") for arg in args if isinstance(arg,dict) and "int_arg" in arg),None)
                                           if event.get("function_name") == "security_file_permission" else None,
                            "return_value": (event.get("return") or {}).get("int_arg")})
        except (ValueError, TypeError, AttributeError):
            continue
    return matched


def assess_effects(method: str, facts: dict[str, Any]) -> dict[str, Any]:
    """Require direct errno or a matching kernel permission return; death is separate."""
    bound = (facts.get("event_bound") is True and facts.get("collector_complete") is True
             and facts.get("enforce_active") is True)
    userspace_denied = (method in {"lsm_file_open", "override_lsm", "override_syscall"}
                        and facts.get("read_denied") is True and facts.get("override_event") is True)
    kernel_denied = (method == "override_lsm" and facts.get("kernel_read_denied") is True
                     and facts.get("override_configured") is True)
    denied = bound and facts.get("monitor_read_ok") is True and (userspace_denied or kernel_denied)
    terminated = (bound and facts.get("sigkill_event") is True
                  and facts.get("process_terminated") is True)
    file_state = "confirmed" if denied else "failed" if facts.get("read_succeeded") is True else "unknown"
    process_state = "confirmed" if terminated else "unknown"
    restored = all(facts.get(key) is True for key in
                   ("monitor_read_ok", "restore_read_ok", "same_policy_id", "cleanup_ok"))
    lifecycle = all(facts.get(key) is True for key in
                    ("service_unavailable", "container_restarted", "service_recovered"))
    expected = "process_termination" if method == "sigkill_fd_install" else "file_read_denial"
    confirmed = terminated if expected == "process_termination" else denied
    if any(facts.get(key) is False for key in ("monitor_read_ok", "restore_read_ok", "same_policy_id", "cleanup_ok")):
        status = "failed"
    elif facts.get("unsupported") is True:
        status = "unsupported"
    elif file_state == "failed" and expected == "file_read_denial":
        status = "failed"
    elif confirmed and restored and (not facts.get("main_process") or not terminated or lifecycle):
        status = "passed"
    else:
        status = "unknown"
    return {"expected_effect": expected, "file_read_denial": file_state,
            "file_denial_basis": ("userspace_errno" if userspace_denied else "kernel_permission_return") if denied else None,
            "process_termination": process_state,
            "service_recovery": "confirmed" if terminated and lifecycle else "unknown",
            "monitor_restored": restored, "status": status}


def reassess_record(record: dict[str, Any]) -> dict[str, Any]:
    """Rebuild displayed claims from bounded evidence instead of trusting a label."""
    facts = dict(record.get("facts") or {})
    if (facts.get("agent_changed") is True or (record.get("agent_before") is not None
        and record.get("agent_after") is not None and record["agent_before"] != record["agent_after"])):
        facts["collector_complete"] = False
    start, end = _time(record.get("started_at")), _time(record.get("finished_at"))
    transitions = [s for s in record.get("steps") or [] if s.get("ok") and s.get("after")]
    enables = [s for s in transitions if s.get("target_mode") == "enforce"
               and (s.get("before") or {}).get("mode") == "monitor"
               and s["after"].get("mode") == "enforce"
               and s["after"].get("state") == "enabled"
               and s["after"].get("id") == record.get("policy_id")]
    enabled_times = [at for step in enables if (at := _time(step.get("at"))) is not None]
    enabled_at = min(enabled_times) if enabled_times else None
    restores = [s for s in transitions if s.get("target_mode") == "monitor"
                and s["after"].get("mode") == "monitor"
                and s["after"].get("state") == "enabled"
                and s["after"].get("id") == record.get("policy_id")]
    restore_times = [at for s in restores if (at := _time(s.get("at"))) is not None
                     and enabled_at is not None and at >= enabled_at]
    restored_at = min(restore_times) if restore_times else end
    functions = {
        "lsm_file_open": {"file_open"},
        "override_syscall": {"sys_openat", "__x64_sys_openat", "__arm64_sys_openat"},
        "override_lsm": {"security_file_permission"},
        "sigkill_fd_install": {"fd_install"},
    }.get(record.get("method"), set())
    events = []
    for event in record.get("events") or []:
        at = _time(event.get("time"))
        needs_task = record.get("task_identity") is not None or event.get("identity_source") == "kernel_task"
        task_valid = (not needs_task or (event.get("identity_source") == "kernel_task"
            and record.get("identity_probe_data") == KERNEL_IDENTITY_DATA
            and (record.get("task_identity") or {}).get("pod_uid") == record.get("pod_uid")
            and (record.get("task_identity") or {}).get("namespace") == record.get("namespace")
            and task_matches_identity(event.get("kernel_task") or {}, record.get("task_identity"))
            and (event.get("kernel_task") or {}).get("pid") == event.get("pid")))
        if (start and end and at and enabled_at and restored_at
            and start <= enabled_at <= at <= restored_at <= end
            and task_valid and event.get("policy") == record.get("policy")
            and event.get("function") in functions
            and event.get("pod_uid") == record.get("pod_uid")
            and record.get("probe_path") is not None and event.get("path") == record["probe_path"]
            and (not facts.get("main_process") or (isinstance(record.get("main_pid"),int)
                 and record["main_pid"] > 0 and event.get("pid") == record["main_pid"] and event.get("ns_pid") == 1))):
            events.append(event)
    facts["event_bound"] = bool(events)
    facts["override_event"] = any(e.get("action") == "KPROBE_ACTION_OVERRIDE" for e in events)
    facts["sigkill_event"] = any(e.get("action") == "KPROBE_ACTION_SIGKILL" for e in events)
    # Accept permission errors only; a policy load or action alone is no proof
    # of an actual denial. LSM events in 1.7.1 do not expose a return value.
    errors = {a.get("argError") for a in record.get("policy_actions") or []
              if a.get("action") == "Override" and type(a.get("argError")) is int
              and a["argError"] in (-1, -13)}
    facts["override_configured"] = bool(errors)
    facts["kernel_read_denied"] = any(e.get("function") == "security_file_permission"
                                      and e.get("access_mask") == 4 and e.get("return_value") in errors for e in events)
    read = record.get("read_result") or {}
    expected_errnos = {-error for error in errors}
    if record.get("method") == "lsm_file_open" and errors:
        expected_errnos.add(1)  # 1.7.1 BPF LSM observed EPERM even with argError=-13.
    facts["read_denied"] = (read.get("read") == "denied" and type(read.get("errno")) is int
                            and read["errno"] in expected_errnos
                            and facts["override_configured"])
    facts["read_succeeded"] = read.get("read") == "ok"
    terminated = record.get("termination") or {}
    death = _time(terminated.get("finishedAt"))
    facts["process_terminated"] = (terminated.get("exitCode") == 137 and terminated.get("reason") != "OOMKilled"
                                    and enabled_at is not None and restored_at is not None and death is not None
                                    and enabled_at.timestamp()-1 <= death.timestamp() <= restored_at.timestamp())
    facts["enforce_active"] = enabled_at is not None
    facts["same_policy_id"] = (bool(enables and restore_times) and all(
        s["after"].get("id") == record.get("policy_id")
        and (s.get("before") or {}).get("id") == record.get("policy_id") for s in transitions)) if transitions else None
    return {**record, "events": events, "facts": facts, "verification": assess_effects(record.get("method", ""), facts)}
