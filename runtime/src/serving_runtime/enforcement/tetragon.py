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
        # Reuse TG-DENY-01's -EPERM: L-05 already measures this errno, so the
        # first read-denial trial can compare the same kernel override behavior.
        actions.append({"action": "Override", "argError": -1})
    description = (
        ("kernel override support and effect unverified; probe path only"
         if rule_id == "TG-DENY-01" else "kernel override support and effect unverified; exact files only")
        if override else "monitor only"
    )
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


def _secret_enforcement_policy(intent: EnforcementIntent) -> dict[str, Any]:
    """New explicit methods start in monitor mode with their action loaded."""
    method = intent.tetragon_enforce_method
    exact = list(intent.protected_paths.enforce_exact_files)
    if method == "override_lsm":
        manifest = _policy(intent, "TG-SECRET-01", exact, 4, exact_path=True, override=True)
        probe = manifest["spec"]["kprobes"][0]
        probe["selectors"][0]["matchActions"].append({"action": "Sigkill"})
        limit = "Override + Sigkill; requires injectable security_file_permission; process terminates"
    elif method == "sigkill_fd_install":
        manifest = _policy(intent, "TG-SECRET-01", exact, 4, exact_path=True)
        manifest["spec"]["kprobes"] = [{
            "call": "fd_install", "syscall": False,
            "args": [{"index": 0, "type": "int"}, {"index": 1, "type": "file"}],
            "selectors": [{"matchArgs": [{"index": 1, "operator": "Equal", "values": exact}],
                           "matchActions": [{"action": "Sigkill"}]}],
        }]
        limit = "Sigkill at fd_install; process terminates; operation prevention not guaranteed"
    elif method == "override_syscall":
        manifest = _policy(intent, "TG-SECRET-01", exact, 4, exact_path=True)
        manifest["spec"]["kprobes"] = [{
            # Tetragon 1.7.1 resolves the architecture prefix for syscall:true.
            "call": "sys_openat", "syscall": True,
            "args": [{"index": 0, "type": "int"}, {"index": 1, "type": "string"},
                     {"index": 2, "type": "int"}],
            "selectors": [{"matchArgs": [{"index": 1, "operator": "Equal",
                                          "values": [path + "\u0000" for path in exact]}],
                           "matchActions": [{"action": "Post"}, {"action": "Override", "argError": -1},
                                            {"action": "Sigkill"}]}],
        }]
        manifest["metadata"]["annotations"]["serving-runtime.local/openat-symbol"] = intent.tetragon_openat_symbol
        limit = "path string only; relative paths, symlinks and TOCTOU can bypass; process terminates"
    else:
        raise ValueError("unknown TG-SECRET-01 enforcement method")
    manifest["spec"]["options"] = [{"name": "policy-mode", "value": "monitor"}]
    manifest["metadata"]["annotations"]["serving-runtime.local/enforce-method"] = method
    manifest["metadata"]["annotations"]["serving-runtime.local/limit"] = limit
    manifest["metadata"]["annotations"]["serving-runtime.local/serving-impact"] = (
        "engine process termination -> Gateway target check fails -> 503 without engine delivery -> Kubernetes restart"
    )
    return manifest


def render_manifests(intent: EnforcementIntent) -> dict[str, dict[str, Any]]:
    """Render file policies; TG-EXEC-01 is intentionally adapter-only."""

    paths = intent.protected_paths
    assets = intent.assets
    # With 1.1 assets the cache policy watches the whole cache root so the
    # adapter can tell current-generation writes from other generations (CACHE-02).
    # When only the current generation is mounted (cache_mount_path), the engine
    # cannot see other generations; the policy watches that mount instead.
    cache_prefix = assets.engine_cache_watch_root if assets is not None else paths.cache_dir
    manifests = {
        "tetragon-tg-asset-01.json": _policy(
            intent, "TG-ASSET-01", [paths.model_dir], 2
        ),
        "tetragon-tg-cache-01.json": _policy(
            intent, "TG-CACHE-01", [cache_prefix], 2
        ),
    }
    if assets is not None and assets.tokenizer_dir is not None:
        manifests["tetragon-tg-asset-02.json"] = _policy(
            intent, "TG-ASSET-02", [assets.tokenizer_dir], 2
        )
    if paths.secret_paths:
        secret_enforce = intent.tetragon_rule_mode("TG-SECRET-01") == "enforce"
        if secret_enforce and intent.tetragon_enforce_method:
            manifests["tetragon-tg-secret-01.json"] = _secret_enforcement_policy(intent)
        else:
            manifests["tetragon-tg-secret-01.json"] = _policy(
                intent, "TG-SECRET-01", list(paths.secret_paths), 4,
                exact_path=secret_enforce,
                override=secret_enforce,
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
