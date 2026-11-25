"""TG-EXEC-01: explicit deny paths with ordered caller exemptions."""

from .exec_intent import VALUES_PER_SELECTOR


def execution_policy(intent):
    callers = intent.exec_deny_exempt_callers
    selectors = [
        {
            "matchBinaries": [{"operator": "In", "values": list(callers[i : i + VALUES_PER_SELECTOR])}],
            "matchActions": [{"action": "NoPost"}],
        }
        for i in range(0, len(callers), VALUES_PER_SELECTOR)
    ]
    selectors.append(
        {
            "matchArgs": [{"index": 0, "operator": "Equal", "values": list(intent.exec_deny_binaries)}],
            "matchActions": [{"action": "Post"}, {"action": "Override", "argError": -13}],
        }
    )
    return {
        "apiVersion": "cilium.io/v1alpha1",
        "kind": "TracingPolicyNamespaced",
        "metadata": {
            "name": "runtime-tg-exec-01",
            "namespace": intent.namespace,
            "annotations": {
                "serving-runtime.local/rule-id": "TG-EXEC-01",
                "serving-runtime.local/status": "candidate-cluster-unapplied-unverified",
                "serving-runtime.local/syntax-target": "Tetragon 1.7.1",
                "serving-runtime.local/enforce-method": "lsm_bprm_check",
                "serving-runtime.local/limit": (
                    "resolved path deny list only; copied shells, Python internal/socket/memory code bypass; "
                    "exempt callers are path-based, not hash-verified; new startup callers may be denied"
                ),
                "serving-runtime.local/serving-impact": "exec denied; process survives; startup scripts require caller review",
            },
        },
        "spec": {
            "podSelector": {"matchLabels": dict(intent.engine.labels)},
            "options": [{"name": "policy-mode", "value": "monitor"}],
            "lsmhooks": [
                {"hook": "bprm_check_security", "args": [{"index": 0, "type": "linux_binprm"}], "selectors": selectors}
            ],
        },
    }
