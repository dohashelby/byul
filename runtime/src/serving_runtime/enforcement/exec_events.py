"""An LSM exec attempt is evidence of an action, never proof of denial."""


def execution_attempt(event, normalized, intent):
    process = event.get("process") or {}
    pod = process.get("pod") or {}
    container = pod.get("container") or {}
    labels = pod.get("pod_labels") or {}
    attributed = bool(
        intent
        and isinstance(labels, dict)
        and pod.get("namespace") == intent.namespace
        and all(labels.get(k) == v for k, v in intent.engine.labels.items())
        and isinstance(container, dict)
        and container.get("name")
        and (intent.engine.container_name is None or container.get("name") == intent.engine.container_name)
    )
    paths = [
        arg["linux_binprm_arg"].get("path")
        for arg in event.get("args", [])
        if isinstance(arg, dict) and isinstance(arg.get("linux_binprm_arg"), dict)
    ]
    if len(paths) != 1 or not isinstance(paths[0], str) or not paths[0].startswith("/"):
        raise ValueError("exec LSM event requires one linux_binprm path")
    action = event.get("action")
    if not isinstance(action, str):
        raise ValueError("exec LSM event requires an action")
    attempted = action.upper() == "KPROBE_ACTION_OVERRIDE"
    normalized.update(
        {
            "binary": paths[0],
            "path": paths[0],
            "access": "exec",
            "operation": "exec_attempt",
            "caller_binary": process.get("binary"),
            "pod": {"namespace": pod.get("namespace"), "pod": pod.get("name", pod.get("pod_name"))},
            "policy_name": event["policy_name"],
            "rule_id": "TG-EXEC-01",
            "action": action,
            "action_raw": action,
            "action_mode": "enforce" if attempted else "monitor",
            "exec_verdict": "deny_attempted" if attempted else "observed",
            "reason": "exec_denied_by_deny_list" if attempted else "exec_monitor_observation",
            "engine_attribution": "confirmed" if attributed else "engine_attribution_unavailable",
            "process_attribution": "available" if process.get("binary") else "unavailable",
            "pod_attribution": "available"
            if pod.get("namespace") and pod.get("name", pod.get("pod_name"))
            else "unavailable",
            "container_attribution": "available" if container.get("name") else "unavailable",
        }
    )
    return normalized
