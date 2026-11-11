"""Bridge UPDATE's complete inventory to the existing local runtime checker.

Pod API declarations bind a check to a deployment instance. They do not attest
which bytes a worker loaded or the effective mount flags inside its namespace.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .assets import SCHEMA_VERSION, _safe_relative
from ..enforcement.intent import EnforcementIntent


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def manifest_from_update_inventory(document: dict[str, Any], root: str) -> dict[str, Any]:
    """Preserve the all-scope file set and validate its original digest."""
    fields = {"scope", "file_count", "total_bytes", "files", "inventory_sha256"}
    if not isinstance(document, dict) or set(document) != fields or document["scope"] != "all":
        raise ValueError("complete UPDATE scope=all inventory required")
    if not Path(root).is_absolute() or "\x00" in root:
        raise ValueError("root must be an absolute visible directory")
    files = document["files"]
    if not isinstance(files, list) or not files:
        raise ValueError("inventory files must be non-empty")
    converted = []
    paths = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != {"path", "bytes", "sha256"}:
            raise ValueError("inventory file must contain path, bytes, sha256")
        relative = _safe_relative(entry["path"])
        size, digest = entry["bytes"], entry["sha256"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("inventory file bytes must be a nonnegative integer")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("inventory file sha256 must be lowercase SHA-256")
        paths.append(relative)
        converted.append({"path": relative, "size": size, "sha256": digest})
    if paths != sorted(set(paths)):
        raise ValueError("inventory paths must be unique and sorted")
    if (type(document["file_count"]) is not int or document["file_count"] != len(files)
            or type(document["total_bytes"]) is not int
            or document["total_bytes"] != sum(item["size"] for item in converted)):
        raise ValueError("inventory count/total does not match files")
    if hashlib.sha256(_canonical(files)).hexdigest() != document["inventory_sha256"]:
        raise ValueError("UPDATE inventory_sha256 does not match original file set")
    return {"schema_version": SCHEMA_VERSION, "root": root, "files": converted}


def check_execution_binding(pod: dict[str, Any], target: dict[str, Any],
                            intent: EnforcementIntent, *, model_host_path: str,
                            expected_command: list[str], expected_args: list[str]) -> dict[str, Any]:
    """Compare a raw Kubernetes Pod with approved launch inputs and cache intent."""
    for values in (expected_command, expected_args):
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError("approved command/args must be string arrays")
    host_root = Path(model_host_path)
    if not host_root.is_absolute() or ".." in host_root.parts:
        raise ValueError("model_host_path must be an absolute normalized host path")
    assets = intent.assets
    if assets is None:
        raise ValueError("execution binding requires intent assets")
    kube = target["kubernetes"]
    metadata, spec, status = (pod.get(key, {}) for key in ("metadata", "spec", "status"))
    issues: list[str] = []
    if metadata.get("deletionTimestamp") or status.get("phase") != "Running":
        issues.append("pod_not_running")
    if metadata.get("namespace") != kube["namespace"] or intent.namespace != kube["namespace"]:
        issues.append("namespace_mismatch")
    if (intent.engine.container_name != kube["container"]
            or any(metadata.get("labels", {}).get(k) != v for k, v in intent.engine.labels.items())):
        issues.append("intent_engine_target_mismatch")
    if any(metadata.get("labels", {}).get(k) != v for k, v in kube["selector"].items()):
        issues.append("selector_mismatch")
    containers = [c for c in spec.get("containers", []) if c.get("name") == kube["container"]]
    states = [c for c in status.get("containerStatuses", []) if c.get("name") == kube["container"]]
    container = containers[0] if len(containers) == 1 else {}
    state = states[0] if len(states) == 1 else {}
    if len(containers) != 1 or len(states) != 1:
        issues.append("container_missing_or_ambiguous")
    digest = target["engine"]["image_digest"]
    # imageID may contain registry/repository and a transport prefix.
    actual_image = state.get("imageID", "")
    if not isinstance(actual_image, str) or actual_image.rsplit("@", 1)[-1].removeprefix("docker-pullable://") != digest:
        issues.append("running_image_mismatch")
    if container.get("command", []) != expected_command or container.get("args", []) != expected_args:
        issues.append("launch_mismatch")
    generation = assets.cache_generation
    if (generation.engine != target["engine"]["kind"] or generation.image_id != digest.removeprefix("sha256:")
            or target["model"].get("revision") != generation.model_revision):
        issues.append("cache_generation_target_mismatch")
    volumes = {v.get("name"): v for v in spec.get("volumes", [])}
    mounts = container.get("volumeMounts", [])

    def mount_matches(destination: str, source: str, read_only: bool) -> bool:
        matching = [m for m in mounts if m.get("mountPath") == destination]
        if len(matching) != 1:
            return False
        mount = matching[0]
        volume = volumes.get(mount.get("name"), {})
        return (not mount.get("subPath") and not mount.get("subPathExpr")
                and mount.get("readOnly", False) is read_only
                and volume.get("hostPath", {}).get("path") == source
                and not any(m is not mount and (Path(destination).is_relative_to(str(m.get("mountPath", "/")))
                    or Path(str(m.get("mountPath", "/"))).is_relative_to(destination)) for m in mounts))

    if not mount_matches(intent.protected_paths.model_dir, model_host_path, True):
        issues.append("model_mount_mismatch")
    if not mount_matches(assets.engine_cache_path, assets.generation_path, False):
        issues.append("cache_mount_mismatch")
    if not metadata.get("uid") or not state.get("containerID") or not state.get("state", {}).get("running", {}).get("startedAt"):
        issues.append("instance_identity_incomplete")
    return {
        "state": "match" if not issues else "mismatch", "issues": issues,
        "target_id": target["target_id"], "pod_uid": metadata.get("uid"),
        "container_id": state.get("containerID"), "restart_count": state.get("restartCount"),
        "started_at": state.get("state", {}).get("running", {}).get("startedAt"),
        "image_digest": digest, "launch_sha256": hashlib.sha256(_canonical({"command": expected_command, "args": expected_args})).hexdigest(),
        "model_host_path": model_host_path, "model_container_path": intent.protected_paths.model_dir,
        "cache_host_path": assets.generation_path, "cache_container_path": assets.engine_cache_path,
        "limits": ["Pod API declarations; effective mount flags and worker consumption require runtime evidence.",
                   "Visible filesystem root to host path mapping is supplied by the operator."],
    }
