"""Tetragon 1.7.1 TracingPolicyNamespaced candidate renderer."""

from __future__ import annotations

from typing import Any

from .intent import EnforcementIntent


_STATUS = "candidate-cluster-unapplied-unverified"


def _policy(
    intent: EnforcementIntent,
    rule_id: str,
    path_values: list[str],
    access_mask: int,
    *,
    exact_path: bool = False,
    override: bool = False,
) -> dict[str, Any]:
    actions: list[dict[str, Any]] = [{"action": "Post"}]
    if override:
        actions.append({"action": "Override", "argError": -1})
    description = "kernel override support and effect unverified; probe path only" if override else "monitor only"
    return {
        "apiVersion": "cilium.io/v1alpha1",
        "kind": "TracingPolicyNamespaced",
        "metadata": {
            "name": f"runtime-{rule_id.lower()}",
            "namespace": intent.namespace,
            "annotations": {
                "serving-runtime.local/rule-id": rule_id,
                "serving-runtime.local/status": _STATUS,
                "serving-runtime.local/syntax-target": "Tetragon 1.7.1",
                "serving-runtime.local/limit": description,
            },
        },
        "spec": {
            "podSelector": {"matchLabels": dict(intent.engine.labels)},
            "kprobes": [
                {
                    "call": "security_file_permission",
                    "syscall": False,
                    "return": True,
                    "args": [
                        {"index": 0, "type": "file"},
                        {"index": 1, "type": "int"},
                    ],
                    "returnArg": {"index": 0, "type": "int"},
                    "selectors": [
                        {
                            "matchArgs": [
                                {
                                    "index": 0,
                                    "operator": "Equal" if exact_path else "Prefix",
                                    "values": path_values,
                                },
                                {
                                    "index": 1,
                                    "operator": "Equal",
                                    "values": [str(access_mask)],
                                },
                            ],
                            "matchActions": actions,
                        }
                    ],
                }
            ],
        },
    }


def render_manifests(intent: EnforcementIntent) -> dict[str, dict[str, Any]]:
    """Render file policies; TG-EXEC-01 is intentionally adapter-only."""

    paths = intent.protected_paths
    manifests = {
        "tetragon-tg-asset-01.json": _policy(
            intent, "TG-ASSET-01", [paths.model_dir], 2
        ),
        "tetragon-tg-cache-01.json": _policy(
            intent, "TG-CACHE-01", [paths.cache_dir], 2
        ),
    }
    if paths.secret_paths:
        manifests["tetragon-tg-secret-01.json"] = _policy(
            intent, "TG-SECRET-01", list(paths.secret_paths), 4
        )
    if intent.tetragon_mode == "enforce_probe":
        manifests["tetragon-tg-deny-01.json"] = _policy(
            intent,
            "TG-DENY-01",
            [intent.probe_path or ""],
            2,
            exact_path=True,
            override=True,
        )
    return manifests
