"""Export an approved policy through the runtime CLI and record the export."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ..contracts import canonical_sha256
from ..state import AppState, get_state
from .documents import _get, _reject

router = APIRouter(prefix="/api", tags=["policies"])


class BundleExportRequest(BaseModel):
    environment_id: str
    environment_revision: int = Field(ge=1)
    requested_by_role: Literal["runtime_operator", "policy_operator"]
    server_values_override: dict[str, Any] | None = None
    bundle_id: str | None = None


def _merge(base: dict[str, Any], override: dict[str, Any], prefix: str = "") -> tuple[dict[str, Any], list[str]]:
    result = dict(base)
    paths: list[str] = []
    for key, value in override.items():
        path = f"{prefix}/{key}"
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key], subpaths = _merge(result[key], value, path)
            paths.extend(subpaths)
        else:
            result[key] = value
            paths.append(path)
    return result, paths


def _server_values(environment: dict[str, Any]) -> dict[str, Any]:
    target = environment.get("target") or {}
    engine = target.get("engine") or {}
    model = target.get("model") or {}
    placement = environment.get("deployment") or {}
    server: dict[str, Any] = {
        "schema_version": "runtime-bundle-server-values/1.0",
        "gateway_schema_version": placement.get("gateway_schema_version"),
        "target": {
            "schema_version": "runtime-target/1.0",
            "target_id": target.get("target_ref"),
            "engine": {"kind": engine.get("runtime_kind"), "version": engine.get("version"),
                       "image_digest": engine.get("image_digest")},
            "model": {"id": model.get("id"), "revision": model.get("revision")},
            "kubernetes": {"context": placement.get("kubernetes_context"),
                           "namespace": placement.get("namespace"),
                           "selector": placement.get("engine_labels"),
                           "container": placement.get("engine_container")},
        },
    }
    if placement.get("gateway_cache_isolation") is not None:
        server["gateway_cache_isolation"] = placement["gateway_cache_isolation"]
    if environment.get("schema_version") == "byul-environment/0.2" and "launch_settings" in placement:
        server["target"]["launch_settings"] = placement["launch_settings"]
    return server


def _missing(server: dict[str, Any], intent: Any) -> list[str]:
    required = [
        ("/target/engine/runtime_kind", server["target"]["engine"].get("kind")),
        ("/target/engine/image_digest", server["target"]["engine"].get("image_digest")),
        ("/target/model/id", server["target"]["model"].get("id")),
        ("/deployment/gateway_schema_version", server.get("gateway_schema_version")),
        ("/deployment/kubernetes_context", server["target"]["kubernetes"].get("context")),
        ("/deployment/namespace", server["target"]["kubernetes"].get("namespace")),
        ("/deployment/engine_labels", server["target"]["kubernetes"].get("selector")),
        ("/deployment/engine_container", server["target"]["kubernetes"].get("container")),
    ]
    if server.get("gateway_schema_version") == "runtime-mvp/1.2":
        required.append(("/deployment/gateway_cache_isolation", server.get("gateway_cache_isolation")))
    if intent is None:
        required.append(("/deployment/enforcement_intent", None))
    return [path for path, value in required if value is None or value == "" or value == {}]


def _json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@router.post("/policies/{doc_id}/revisions/{revision}/bundle")
def export_bundle(doc_id: str, revision: int, body: BundleExportRequest,
                  state: AppState = Depends(get_state)) -> Response:
    policy = _get(state.policies, doc_id, revision)
    if policy.status != "CONFIRMED" or policy.document.get("status") != "CONFIRMED":
        return _reject("STATUS_NOT_ALLOWED", [{"path": "/status", "message": "CONFIRMED 판본만 내보낼 수 있습니다"}], 409)
    if canonical_sha256(policy.document) != policy.sha256:
        return _reject("HASH_MISMATCH", [{"path": "/policy", "message": "저장된 정책 해시와 판본 내용이 다릅니다"}], 409)
    environment = _get(state.environments, body.environment_id, body.environment_revision)
    if canonical_sha256(environment.document) != environment.sha256:
        return _reject("HASH_MISMATCH", [{"path": "/environment", "message": "저장된 환경 해시와 판본 내용이 다릅니다"}], 409)
    ref = policy.document.get("environment_ref")
    if ref is not None and (ref.get("id"), ref.get("revision"), ref.get("sha256")) != (
            body.environment_id, body.environment_revision, environment.sha256):
        return _reject("ENVIRONMENT_MISMATCH", [{"path": "/environment_ref", "message": "정책이 참조하는 환경 판본과 다릅니다"}], 409)
    if policy.document.get("target_ref") != environment.document.get("target", {}).get("target_ref"):
        return _reject("TARGET_MISMATCH", [{"path": "/target_ref", "message": "정책과 환경의 대상 ID가 다릅니다"}], 409)

    server = _server_values(environment.document)
    override_paths: list[str] = []
    if body.server_values_override:
        server, override_paths = _merge(server, body.server_values_override)
        if any(path.startswith("/target/launch_settings") for path in override_paths):
            return _reject("LAUNCH_SETTINGS_OVERRIDE_FORBIDDEN", [{
                "path": "/deployment/launch_settings",
                "message": "승인 실행 설정은 환경 판본에서만 가져옵니다. 환경 판본을 먼저 갱신하세요",
            }])
    enforcement = (policy.document.get("runtime") or {}).get("enforcement")
    intent = enforcement.get("intent") if isinstance(enforcement, dict) else None
    if intent is None:
        intent = (environment.document.get("deployment") or {}).get("enforcement_intent")
    try:
        missing = _missing(server, intent)
    except (AttributeError, KeyError, TypeError):
        return _reject("SERVER_VALUES_INVALID", [{"path": "/server_values_override", "message": "서버 값 객체 구조가 맞지 않습니다"}])
    if missing:
        return _reject("BUNDLE_FIELDS_MISSING", [{"path": path, "message": "묶음 생성에 필요한 값이 없습니다"} for path in missing])
    if not isinstance(intent, dict):
        return _reject("INTENT_INVALID", [{"path": "/deployment/enforcement_intent", "message": "intent 객체가 필요합니다"}])
    bundle_id = body.bundle_id or f"bundle-{uuid4().hex}"
    runtime_dir = state.settings.bundle_runtime_dir
    python = state.settings.bundle_runtime_python or runtime_dir / ".venv" / "bin" / "python"
    if not python.is_file():
        return _reject("BUNDLE_RUNTIME_UNAVAILABLE", [{"path": "/", "message": "runtime Python 실행 파일을 찾을 수 없습니다"}], 503)

    with tempfile.TemporaryDirectory(prefix="byul-bundle-export-") as temporary:
        root = Path(temporary)
        _json(root / "policy.json", policy.document)
        _json(root / "server.json", server)
        _json(root / "intent.json", intent)
        command = [str(python), "-B", "-m", "serving_runtime", "bundle", "build",
                   "--policy", str(root / "policy.json"), "--policy-sha256", policy.sha256,
                   "--server-values", str(root / "server.json"), "--intent", str(root / "intent.json"),
                   "--output-parent", str(root / "out"), "--bundle-id", bundle_id]
        try:
            built = subprocess.run(command, cwd=runtime_dir, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return _reject("BUNDLE_BUILD_FAILED", [{"path": "/", "message": "runtime 묶음 생성 명령을 실행할 수 없습니다"}], 503)
        if built.returncode:
            return _reject("BUNDLE_BUILD_FAILED", [{"path": "/", "message": "runtime 묶음 검증에 실패했습니다. 서버 값과 intent를 확인하세요"}])
        bundle_dir = root / "out" / bundle_id
        manifest_raw = (bundle_dir / "bundle.json").read_bytes()
        manifest = json.loads(manifest_raw)
        bundle_sha = hashlib.sha256(manifest_raw).hexdigest()
        files = manifest["files"]
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as tar:
            for name in ("bundle.json", "runtime-policy.json", "policy-approvals.json", "intent.json", "target.json"):
                tar.add(bundle_dir / name, arcname=f"{bundle_id}/{name}", recursive=False)
        metadata = {"bundle_id": bundle_id, "bundle_sha256": bundle_sha, "policy_sha256": policy.sha256,
                    "files": files, "environment": {"id": body.environment_id, "revision": body.environment_revision,
                    "sha256": environment.sha256}, "server_values_source": "human_input" if override_paths else "environment_revision",
                    "human_input_paths": override_paths}
        record = {**metadata, "requested_by_role": body.requested_by_role,
                  "created_at": datetime.now(UTC).isoformat(), "policy": {"id": doc_id, "revision": revision}}
        audit_dir = state.policies.dir.parent / "bundle_exports"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_path = audit_dir / f"{uuid4().hex}.json"
        with audit_path.open("x", encoding="utf-8") as output:
            json.dump(record, output, ensure_ascii=False, indent=2)
            output.write("\n")
        encoded = base64.urlsafe_b64encode(json.dumps(metadata, ensure_ascii=False).encode()).decode()
        return Response(content=archive.getvalue(), media_type="application/gzip", headers={
            "Content-Disposition": f'attachment; filename="{bundle_id}.tar.gz"',
            "X-Byul-Bundle-Metadata": encoded,
        })
