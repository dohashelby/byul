"""SP1 Pod security candidate: read-only supply mounts, accounts, cache generation.

The output is a reviewable fragment for the engine and acquisition containers,
not a complete Kubernetes manifest. It is not applied or validated here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .intent import EnforcementIntent


SCHEMA = "runtime-pod-security-candidate/1.0"


def _security_context(uid: int) -> dict[str, Any]:
    return {
        "runAsUser": uid,
        "runAsGroup": uid,
        "runAsNonRoot": True,
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }


def render_pod_security(intent: EnforcementIntent) -> dict[str, Any] | None:
    assets = intent.assets
    if assets is None:
        return None
    paths = intent.protected_paths
    generation = assets.generation_path
    engine_mounts = [
        {"name": "serving-model", "mountPath": paths.model_dir, "readOnly": True},
    ]
    if assets.tokenizer_dir is not None:
        engine_mounts.append(
            {"name": "serving-tokenizer", "mountPath": assets.tokenizer_dir, "readOnly": True}
        )
    engine_mounts.append(
        {
            "name": "serving-approved-manifest",
            "mountPath": str(Path(assets.manifest_path).parent),
            "readOnly": True,
        }
    )
    engine_mounts.append(
        {
            "name": "serving-cache",
            "mountPath": assets.engine_cache_path,
            "subPath": str(Path(generation).relative_to(assets.cache_root)),
            "readOnly": False,
        }
    )
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "status": "candidate-cluster-unapplied-unverified",
        "rule_refs": ["ASSET-01", "ASSET-02", "CACHE-01", "CACHE-02"],
        "cache_generation": {
            "engine": assets.cache_generation.engine,
            "image_id": assets.cache_generation.image_id,
            "model_revision": assets.cache_generation.model_revision,
            "compile_profile": assets.cache_generation.compile_profile,
            "path": generation,
        },
        "engine": {
            "podLabels": dict(intent.engine.labels),
            "securityContext": _security_context(assets.engine_uid),
            "volumeMounts": engine_mounts,
            "cacheEnvironmentCandidates": (
                [{"name": "VLLM_CACHE_ROOT", "value": assets.engine_cache_path}]
                if assets.cache_generation.engine == "vllm"
                else []
            ),
        },
        "acquire": None,
        "limits": [
            "Fragment only: merge into the real Pod spec and confirm the image's actual cache paths.",
            "One environment variable does not move every dependency cache (Inductor, Triton, FlashInfer).",
            "A read-only mount blocks writes through the kernel; record that as a mount result, not a Tetragon result.",
            "Same-UID code inside the engine can still write its own cache generation.",
        ],
    }
    if intent.acquire is not None:
        document["acquire"] = {
            "podLabels": dict(intent.acquire.labels),
            "securityContext": (
                _security_context(assets.acquire_uid) if assets.acquire_uid is not None else None
            ),
            "volumeMounts": [
                {"name": "serving-model", "mountPath": paths.model_dir, "readOnly": False},
                *(
                    [{"name": "serving-tokenizer", "mountPath": assets.tokenizer_dir, "readOnly": False}]
                    if assets.tokenizer_dir is not None
                    else []
                ),
            ],
        }
    return document
