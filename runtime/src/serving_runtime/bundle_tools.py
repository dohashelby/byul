"""Build and apply same-host policy bundles; approval records remain self assertions."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
import ctypes
from pathlib import Path
import re
import selectors
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .mvp.policy_bundle import _strict_json, verify_policy_bundle
from .mvp.policy import load_policy
from .mvp.target import load_target
from .mvp.approval import _parse_entries
from .enforcement.intent import parse_intent

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_REPLACE = re.compile(r"\bREPLACE(?:_[A-Z0-9]+)*\b")


def _read(path: str | Path) -> dict:
    value = _strict_json(Path(path).read_bytes(), str(path))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: JSON 객체가 필요합니다")
    return value


def _bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _rename_new(source: Path, destination: Path) -> None:
    """Linux renameat2: publish a complete directory without replacing an existing ID."""
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result != 0:
        error = ctypes.get_errno()
        if error == 17:
            raise FileExistsError(f"묶음 ID가 이미 있습니다: {destination}")
        raise OSError(error, os.strerror(error), str(destination))


def _check_no_replace(value: object, where: str) -> None:
    if _REPLACE.search(json.dumps(value, ensure_ascii=False)):
        raise ValueError(f"{where}: REPLACE 자리값이 남았습니다")


def _fields(value: object, required: set[str], optional: set[str], where: str) -> dict:
    if not isinstance(value, dict) or required - value.keys() or value.keys() - required - optional:
        raise ValueError(f"{where}: 필수/허용 필드가 맞지 않습니다")
    return value


def build_bundle(policy_path: str, policy_sha256: str, server_values_path: str,
                 intent_path: str, output_parent: str, bundle_id: str):
    if not _ID.fullmatch(bundle_id):
        raise ValueError("bundle_id 형식이 잘못되었습니다")
    policy = _read(policy_path)
    _fields(policy, {"schema_version", "policy_id", "policy_version", "created_at", "data_origin",
                     "status", "target_ref", "environment_ref", "validity", "approval",
                     "verification", "runtime"}, {"framework_values"}, "플랫폼 정책")
    if policy["schema_version"] != "byul-policy/0.1" or policy["status"] != "CONFIRMED":
        raise ValueError("byul-policy/0.1 CONFIRMED 원본만 받을 수 있습니다")
    if not isinstance(policy["data_origin"], str) or policy["data_origin"] not in {"MEASURED", "DECLARED", "EXAMPLE"}:
        raise ValueError("플랫폼 data_origin이 유효하지 않습니다")
    _fields(policy["verification"], {"pack", "values", "scope", "exceptions"}, set(), "verification")
    if not isinstance(policy_sha256, str) or not _SHA.fullmatch(policy_sha256) or _sha(_canonical(policy)) != policy_sha256:
        raise ValueError("플랫폼 정책 정규 JSON SHA-256이 입력 해시와 다릅니다")
    if not isinstance(policy["policy_version"], str) or not 1 <= len(policy["policy_version"]) <= 64:
        raise ValueError("policy_version이 유효하지 않습니다")
    for key in ("policy_id", "target_ref"):
        if not isinstance(policy[key], str) or not _ID.fullmatch(policy[key]):
            raise ValueError(f"{key}가 유효하지 않습니다")
    approval = _fields(policy["approval"], {"approved_by", "approved_at", "approval_ref"}, set(), "approval")
    validity = _fields(policy["validity"], {"valid_from", "valid_until"}, set(), "validity")
    runtime = _fields(policy["runtime"], {"gateway", "enforcement", "exec_exceptions"}, set(), "runtime")
    gateway = _fields(runtime["gateway"], {"allowed_models", "output_default_tokens", "output_max_tokens", "max_body_bytes"},
                      {"max_context_tokens", "profile", "max_n", "extra_fields"}, "runtime.gateway")
    if runtime["exec_exceptions"]:
        raise ValueError("runtime.exec_exceptions 비어 있지 않음: 예외 파일 변환은 미지원")
    server = _read(server_values_path)
    _fields(server, {"schema_version", "gateway_schema_version", "target"}, {"gateway_cache_isolation"}, "서버 값")
    if server["schema_version"] != "runtime-bundle-server-values/1.0" or server["gateway_schema_version"] not in {"runtime-mvp/1.1", "runtime-mvp/1.2"}:
        raise ValueError("서버 값 schema_version 또는 gateway_schema_version 미지원")
    schema = server["gateway_schema_version"]
    if schema == "runtime-mvp/1.1" and set(gateway) & {"profile", "max_n", "extra_fields"}:
        raise ValueError("1.1로 변환할 수 없는 gateway 1.2 필드가 있습니다")
    if schema == "runtime-mvp/1.1" and "gateway_cache_isolation" in server:
        raise ValueError("gateway_cache_isolation은 1.2 전용입니다")
    if schema == "runtime-mvp/1.2" and server.get("gateway_cache_isolation") not in {"off", "per_principal"}:
        raise ValueError("1.2에는 서버 값 gateway_cache_isolation(off/per_principal)이 필요합니다")
    target = _fields(server["target"], {"schema_version", "target_id", "engine", "model", "kubernetes"},
                     {"target_ref", "baseline_ref"}, "서버 target")
    if target["schema_version"] != "runtime-target/1.0" or target["target_id"] != policy["target_ref"]:
        raise ValueError("서버 target_id가 플랫폼 target_ref와 다릅니다")
    if not isinstance(target["engine"], dict) or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(target["engine"].get("image_digest"))):
        raise ValueError("서버 engine.image_digest에 실제 SHA-256이 필요합니다")
    if not isinstance(target["model"], dict) or target["model"].get("id") not in gateway["allowed_models"]:
        raise ValueError("서버 model.id가 허용 모델과 다르거나 모호합니다")
    intent = _read(intent_path)
    if intent.get("schema") != "runtime-enforcement-intent/1.1":
        raise ValueError("intent 원본은 runtime-enforcement-intent/1.1이어야 합니다")
    parse_intent(intent)
    enforcement = runtime["enforcement"]
    if enforcement is not None:
        _fields(enforcement, {"intent_schema", "intent"}, set(), "runtime.enforcement")
        if enforcement["intent_schema"] != intent["schema"] or _canonical(enforcement["intent"]) != _canonical(intent):
            raise ValueError("플랫폼 enforcement와 서버 intent 원본이 다릅니다")
    if not isinstance(target["kubernetes"], dict) or intent.get("namespace") != target["kubernetes"].get("namespace"):
        raise ValueError("intent namespace와 target namespace가 다릅니다")
    generation = intent.get("assets", {}).get("cache_generation", {})
    if generation.get("engine") != target["engine"].get("kind") or generation.get("image_id") != target["engine"]["image_digest"].removeprefix("sha256:"):
        raise ValueError("intent 캐시 세대의 엔진·이미지 해시가 target과 다릅니다")
    _check_no_replace(policy, "플랫폼 정책")
    _check_no_replace(server, "서버 값")
    _check_no_replace(intent, "intent")
    doc = {"schema_version": schema, "policy_id": policy["policy_id"],
           "policy_version": policy["policy_version"], "target_id": policy["target_ref"]}
    for name in ("allowed_models", "output_default_tokens", "output_max_tokens", "max_body_bytes"):
        doc[name] = gateway[name]
    doc.update({"valid_from": validity["valid_from"], "valid_until": validity["valid_until"],
                "approval_ref": approval["approval_ref"]})
    if "max_context_tokens" in gateway:
        doc["max_context_tokens"] = gateway["max_context_tokens"]
    if schema == "runtime-mvp/1.2":
        for name in ("profile", "max_n", "extra_fields"):
            if name in gateway:
                doc[name] = gateway[name]
        doc["cache_isolation"] = server["gateway_cache_isolation"]
    policy_raw = _bytes(doc)
    entry = {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
             "sha256": _sha(policy_raw), "approval_ref": approval["approval_ref"],
             "approved_at": approval["approved_at"].replace("Z", "+00:00")}
    if validity["valid_until"] is not None:
        entry["valid_until"] = validity["valid_until"]
    roles = [("gateway_policy", "runtime-policy.json", schema, policy_raw),
             ("policy_approvals", "policy-approvals.json", "runtime-policy-approvals/1.0",
              _bytes({"schema_version": "runtime-policy-approvals/1.0", "entries": [entry]})),
             ("enforcement_intent", "intent.json", intent["schema"], _bytes(intent)),
             ("target", "target.json", "runtime-target/1.0", _bytes(target))]
    parent = Path(output_parent)
    parent.mkdir(parents=True, exist_ok=True)
    destination = parent / bundle_id
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"묶음 ID가 이미 있습니다: {destination}")
    with tempfile.TemporaryDirectory(prefix=".bundle-build-", dir=parent) as temporary:
        temporary_path = Path(temporary)
        files = []
        for role, name, role_schema, raw in roles:
            (temporary_path / name).write_bytes(raw)
            files.append({"role": role, "path": name, "schema_version": role_schema,
                          "sha256": _sha(raw), "bytes": len(raw)})
        manifest = {"schema_version": "byul-policy-bundle/0.1", "bundle_id": bundle_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "policy_ref": {"policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
                                   "sha256": policy_sha256}, "target_ref": policy["target_ref"],
                    "approval": approval, "validity": validity, "files": files}
        (temporary_path / "bundle.json").write_bytes(_bytes(manifest))
        verify_policy_bundle(temporary_path)
        loaded = load_policy(temporary_path / "runtime-policy.json")
        if not loaded.ok:
            raise ValueError(f"Gateway 정책 로드 실패: {loaded.error_message}")
        _parse_entries((temporary_path / "policy-approvals.json").read_bytes())
        loaded_target = load_target(temporary_path / "target.json")
        if loaded_target.state != "ok":
            raise ValueError(f"target 로드 실패: {loaded_target.error_message}")
        _rename_new(temporary_path, destination)
    return verify_policy_bundle(destination)


def _run(command: list[str]) -> None:
    # Keep kubectl progress visible without contaminating --operation-result JSON.
    subprocess.run(command, check=True, stdout=sys.stderr)


class StatusUnavailable(RuntimeError):
    """No trustworthy status response was obtained after rollout."""


class StatusMismatch(RuntimeError):
    """A valid status response disagreed with the intended bundle."""


class BundleApplyError(RuntimeError):
    """Failed apply with a non-secret record of the actual recovery outcome."""

    def __init__(self, message: str, outcome: dict):
        super().__init__(message)
        self.outcome = outcome


def _kubectl(context: str, namespace: str, kubeconfig: str | None) -> list[str]:
    command = ["kubectl"]
    if kubeconfig:
        command += ["--kubeconfig", kubeconfig]
    return command + ["--context", context, "-n", namespace]


@contextmanager
def _port_forward(context: str, namespace: str, kubeconfig: str | None, remote_port: int):
    command = _kubectl(context, namespace, kubeconfig) + [
        "port-forward", "deployment/serving-gateway", f":{remote_port}", "--address", "127.0.0.1"
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    try:
        assert process.stdout is not None
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            deadline = time.monotonic() + 10
            local_port = None
            while time.monotonic() < deadline:
                if not selector.select(timeout=min(0.5, max(0, deadline - time.monotonic()))):
                    if process.poll() is not None:
                        break
                    continue
                line = process.stdout.readline()
                match = re.search(r"Forwarding from 127\.0\.0\.1:([0-9]+) ->", line)
                if match:
                    local_port = int(match.group(1))
                    break
                if not line or process.poll() is not None:
                    break
        if local_port is None:
            raise StatusUnavailable("새 Gateway port-forward를 열 수 없습니다")
        yield f"http://127.0.0.1:{local_port}"
    finally:
        process.terminate() if process.poll() is None else None
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if process.stdout is not None:
            process.stdout.close()


def _status(url: str, token: str | None) -> dict:
    def request(auth: str | None):
        headers = {"Authorization": "Bearer " + auth} if auth else {}
        with urlopen(Request(url + "/api/status", headers=headers), timeout=5) as response:
            return json.load(response)

    try:
        document = request(None)
    except HTTPError as error:
        if error.code not in (401, 403):
            raise
        if token is None:
            raise StatusUnavailable("/api/status 조회 인증 필요; RUNTIME_VIEWER_TOKEN 없음") from error
        document = request(token)
    if not isinstance(document, dict):
        raise StatusUnavailable("/api/status 응답이 JSON 객체가 아닙니다")
    return document


def _token(path: str | None, name: str = "RUNTIME_VIEWER_TOKEN") -> str | None:
    if path is None:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    matches = [line.partition("=")[2].strip().strip('"\'') for line in raw.splitlines()
               if line.startswith(name + "=")]
    if len(matches) > 1 or (matches and not matches[0]):
        raise ValueError(f"env 파일의 {name}이 중복되거나 비었습니다")
    return matches[0] if matches else None


def _gateway_status(context: str, namespace: str, kubeconfig: str | None,
                    remote_port: int, token: str | None, *,
                    status_url: str | None = None, timeout: float = 90) -> dict:
    """Read this deployment through a fresh forward; a local URL is opt-in."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            connection = (nullcontext(status_url) if status_url is not None else
                          _port_forward(context, namespace, kubeconfig, remote_port))
            with connection as url:
                return _status(url, token)
        except (URLError, TimeoutError, OSError, ValueError, StatusUnavailable) as error:
            if time.monotonic() >= deadline:
                raise StatusUnavailable(type(error).__name__) from None
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def _normal_canary(url: str, token: str | None, model: str, max_tokens: int) -> dict:
    """One bounded ordinary chat request through the deployed Gateway."""
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    raw = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply OK."}],
                      "max_tokens": max_tokens, "stream": False}).encode("utf-8")
    try:
        with urlopen(Request(url + "/v1/chat/completions", data=raw, headers=headers), timeout=20) as response:
            if response.status != 200:
                raise StatusMismatch("normal canary returned a non-success status")
            body = response.read(1024 * 1024 + 1)
            request_id = response.headers.get("X-Request-ID")
    except HTTPError as error:
        raise StatusMismatch(f"normal canary rejected (HTTP {error.code})") from error
    if len(body) > 1024 * 1024:
        raise StatusMismatch("normal canary response exceeded its byte limit")
    try:
        value = _strict_json(body, "normal canary")
        choices = value["choices"]
        content = choices[0]["message"]["content"]
        if (not isinstance(choices, list) or len(choices) != 1 or not isinstance(content, str)
                or not content.strip() or choices[0].get("finish_reason") not in {"stop", "length"}):
            raise ValueError("normal generation missing")
    except (ValueError, KeyError, TypeError, IndexError) as error:
        raise StatusMismatch("normal canary did not produce a valid completed chat response") from error
    return {"state": "verified", "model": model, "max_tokens": max_tokens,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "request_id": request_id if isinstance(request_id, str) and _ID.fullmatch(request_id) else None,
            "response_sha256": _sha(body)}


def _checked_status(context: str, namespace: str, kubeconfig: str | None,
                    remote_port: int, token: str | None, bundle_id: str,
                    *, timeout: float = 90, expected_target_id: str | None = None,
                    expected_bundle_sha256: str | None = None, canary_model: str | None = None,
                    canary_token: str | None = None, canary_max_tokens: int = 8) -> dict | None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        try:
            with _port_forward(context, namespace, kubeconfig, remote_port) as url:
                status = _status(url, token)
                bundle_status = status.get("policy_bundle")
                policy_status = status.get("policy")
                if not isinstance(bundle_status, dict) or not isinstance(policy_status, dict) or not isinstance(policy_status.get("approval"), dict):
                    raise StatusUnavailable("/api/status 묶음·승인 필드가 없어 확인할 수 없습니다")
                actual_id = bundle_status.get("bundle_id")
                approval = policy_status["approval"]
                if actual_id is None or approval.get("state") is None:
                    raise StatusUnavailable("/api/status 묶음·승인 값이 없어 확인할 수 없습니다")
                if actual_id != bundle_id or approval.get("state") != "verified":
                    raise StatusMismatch(f"/api/status 불일치: bundle_id={actual_id!r}, approval.state={approval.get('state')!r}")
                if expected_bundle_sha256 is not None:
                    if bundle_status.get("sha256") is None:
                        raise StatusUnavailable("/api/status 정확 묶음 해시가 없습니다")
                    if bundle_status["sha256"] != expected_bundle_sha256:
                        raise StatusMismatch("/api/status 묶음 해시가 승인 판본과 다릅니다")
                if expected_target_id is not None:
                    target = status.get("target")
                    if not isinstance(target, dict) or target.get("target_id") is None:
                        raise StatusUnavailable("/api/status 대상 확인 필드가 없습니다")
                    if target["target_id"] != expected_target_id:
                        raise StatusMismatch("/api/status 등록 대상이 승인 target과 다릅니다")
                    verification = target.get("verification")
                    if (target.get("registration") != "ok" or target.get("gateway_hold") is not None
                            or not isinstance(verification, dict) or verification.get("state") != "ok"):
                        raise StatusUnavailable("/api/status 현재 실행 대상 확인이 완료되지 않았습니다")
                    if policy_status.get("gateway_active") is not True:
                        raise StatusMismatch("/api/status 정책이 현재 요청을 허용하지 않습니다")
                    backend = status.get("backend")
                    if not isinstance(backend, dict) or backend.get("healthy") is not True:
                        raise StatusUnavailable("/api/status 실행 엔진의 정상 상태가 확인되지 않았습니다")
                if canary_model is not None:
                    canary = _normal_canary(url, canary_token, canary_model, canary_max_tokens)
                    return {"bundle_id": bundle_id, "bundle_sha256": expected_bundle_sha256,
                            "target_id": expected_target_id, "canary": canary}
                return None
        except (URLError, TimeoutError, OSError, ValueError, StatusUnavailable) as error:
            last_error = str(error) if isinstance(error, StatusUnavailable) else type(error).__name__
        if time.monotonic() >= deadline:
            raise StatusUnavailable(f"/api/status 조회 불가({last_error}); 새 링크 유지·수동 확인 필요")
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def _check_deployed_permissions(directory: Path) -> None:
    # 기존 묶음은 복사·chmod 없이 그대로 재사용하므로 Gateway 읽기 권한을 엄격히 확인한다.
    for path in [directory, *directory.rglob("*")]:
        info = path.lstat()
        expected_mode = 0o750 if stat.S_ISDIR(info.st_mode) else 0o640
        if (not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
                or info.st_uid != 0 or info.st_gid != 10000
                or stat.S_IMODE(info.st_mode) != expected_mode):
            raise ValueError(f"기존 묶음 소유권·권한 불일치(root:10000, 폴더 0750, 파일 0640): {path}")


def apply_bundle(bundle_dir: str | None, bundle_root: str, context: str, namespace: str,
                 status_url: str, token_env_file: str | None, *, kubeconfig: str | None = None,
                 dry_run: bool = False, existing: str | None = None,
                 deployment_guard_path: str | None = None) -> dict:
    parsed_url = urlparse(status_url)
    if parsed_url.scheme != "http" or parsed_url.hostname not in {"127.0.0.1", "localhost"} or not parsed_url.port or parsed_url.path not in {"", "/"} or parsed_url.query or parsed_url.fragment:
        raise ValueError("--status-url은 로컬 http://127.0.0.1:PORT 형식이어야 합니다")
    if kubeconfig is not None and not Path(kubeconfig).is_file():
        raise ValueError("--kubeconfig 파일이 없습니다")
    if (bundle_dir is None) == (existing is None):
        raise ValueError("--bundle-dir 또는 --existing 중 하나만 지정해야 합니다")
    root = Path(bundle_root)
    current = root / "current"
    if root.is_dir() and not os.access(root, os.R_OK | os.X_OK):
        raise PermissionError("묶음 루트 조회 권한이 없습니다: --dry-run도 sudo로 실행해야 합니다")
    try:
        if existing is not None:
            if not _ID.fullmatch(existing):
                raise ValueError("--existing bundle_id 형식이 잘못되었습니다")
            destination = root / existing
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError("--existing 대상은 루트의 실제 묶음 폴더여야 합니다")
            source = verify_policy_bundle(destination)
            if source.bundle_id != existing:
                raise ValueError("--existing ID와 bundle.json의 bundle_id가 다릅니다")
            _check_deployed_permissions(destination)
        else:
            source = verify_policy_bundle(bundle_dir)
        if not current.is_symlink():
            raise ValueError("current 상대 심볼릭 링크가 필요합니다")
        previous = os.readlink(current)
        if not _ID.fullmatch(previous) or not (root / previous).is_dir():
            raise ValueError("이전 current 대상이 안전한 묶음 폴더가 아닙니다")
        verify_policy_bundle(root / previous)
        if existing is None and ((root / source.bundle_id).exists() or (root / source.bundle_id).is_symlink()):
            raise FileExistsError("적용 대상 bundle_id가 이미 존재합니다")
        if (root / "current.next").exists() or (root / "current.next").is_symlink():
            raise FileExistsError("current.next가 이미 있습니다")
        if (root / "current.rollback").exists() or (root / "current.rollback").is_symlink():
            raise FileExistsError("current.rollback이 이미 있습니다")
    except PermissionError as error:
        raise PermissionError("묶음 루트 조회 권한이 없습니다: --dry-run도 sudo로 실행해야 합니다") from error
    def guarded(candidate):
        if deployment_guard_path is not None:
            from .mvp.deployment_guard import verify_deployment_guard
            targets = [item for item in candidate.files if item.role == "target"]
            if len(targets) != 1:
                raise ValueError("guarded deployment requires one target registration")
            target = _strict_json(targets[0].content, "target")
            scope = target.get("kubernetes")
            if (not isinstance(scope, dict) or scope.get("context") != context
                    or scope.get("namespace") != namespace):
                raise ValueError("적용 context/namespace가 승인 target과 다릅니다")
            result = verify_deployment_guard(candidate, deployment_guard_path)
            result["deployment_scope"] = {"context": context, "namespace": namespace}
            return result
        return None

    guard_result = guarded(source)
    if dry_run:
        return {"bundle_id": source.bundle_id, "previous": previous, "dry_run": True,
                "deployment_guard": guard_result}
    token = _token(token_env_file)
    inference_token = _token(token_env_file, "RUNTIME_INFERENCE_TOKEN") if deployment_guard_path else None
    if existing is None:
        with tempfile.TemporaryDirectory(prefix=".bundle-apply-", dir=root) as temporary:
            staging = Path(temporary)
            try:
                for item in source.files:
                    dest = staging / item.relative_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(item.content)
                manifest_raw = (Path(bundle_dir) / "bundle.json").read_bytes()
                if _sha(manifest_raw) != source.sha256:
                    raise ValueError("원본 bundle.json이 검증 뒤 변경되었습니다")
                (staging / "bundle.json").write_bytes(manifest_raw)
                for folder in [staging, *(p for p in staging.rglob("*") if p.is_dir())]:
                    os.chown(folder, 0, 10000)
                    folder.chmod(0o750)
                for file in (p for p in staging.rglob("*") if p.is_file()):
                    os.chown(file, 0, 10000)
                    file.chmod(0o640)
            except PermissionError as error:
                raise PermissionError("root:10000 소유권·권한 설정 실패: sudo로 실행해야 합니다") from error
            _rename_new(staging, root / source.bundle_id)
        verify_policy_bundle(root / source.bundle_id)
    def guarded_status(candidate):
        target_file = next(item for item in candidate.files if item.role == "target")
        policy_file = next(item for item in candidate.files if item.role == "gateway_policy")
        target_document = _strict_json(target_file.content, "target")
        policy_document = _strict_json(policy_file.content, "gateway policy")
        return _checked_status(context, namespace, kubeconfig, parsed_url.port, token, candidate.bundle_id,
                               expected_target_id=candidate.target_ref, expected_bundle_sha256=candidate.sha256,
                               canary_model=target_document["model"]["id"], canary_token=inference_token,
                               canary_max_tokens=min(8, policy_document["output_max_tokens"]))

    switched = False
    try:
        # Re-read current external authority after staging and immediately
        # before changing the serving link.
        staged = verify_policy_bundle(root / source.bundle_id)
        if staged.sha256 != source.sha256:
            raise ValueError("배치된 묶음이 최초 검증 판본과 다릅니다")
        guard_result = guarded(staged)
        os.symlink(source.bundle_id, root / "current.next")
        os.replace(root / "current.next", current)
        switched = True
        if os.readlink(current) != source.bundle_id:
            raise RuntimeError("current 링크 대상 재확인 실패")
        base = _kubectl(context, namespace, kubeconfig) + ["rollout"]
        _run(base + ["restart", "deployment/serving-gateway"])
        _run(base + ["status", "deployment/serving-gateway", "--timeout=180s"])
        if deployment_guard_path is not None:
            final_verification = guarded_status(source)
        else:
            _checked_status(context, namespace, kubeconfig, parsed_url.port, token, source.bundle_id)
            final_verification = None
        return {"bundle_id": source.bundle_id, "previous": previous, "dry_run": False,
                "deployment_guard": guard_result, "final_verification": final_verification}
    except StatusUnavailable:
        raise
    except Exception as error:
        if switched:
            try:
                # Rollback is a fresh use of the previous deployment. An old
                # in-bundle approval cannot override today's revocation.
                if (root / previous).is_symlink():
                    raise ValueError("previous bundle directory became a symbolic link")
                previous_bundle = verify_policy_bundle(root / previous)
                previous_guard = guarded(previous_bundle)
                os.symlink(previous, root / "current.rollback")
                os.replace(root / "current.rollback", current)
                if os.readlink(current) != previous:
                    raise RuntimeError("되돌린 링크 대상 불일치")
                base = _kubectl(context, namespace, kubeconfig) + ["rollout"]
                _run(base + ["restart", "deployment/serving-gateway"])
                _run(base + ["status", "deployment/serving-gateway", "--timeout=180s"])
                rollback_verification = guarded_status(previous_bundle) if deployment_guard_path is not None else None
            except Exception as rollback_error:
                raise BundleApplyError(f"적용 실패; 되돌리기도 실패: {rollback_error}",
                                       {"state": "rollback_failed", "candidate_bundle_id": source.bundle_id,
                                        "previous_bundle_id": previous, "current_link": os.readlink(current),
                                        "rollback_verified": False}) from error
        if switched:
            raise BundleApplyError(f"적용 실패; 이전 링크 복원 및 재시작 완료: {error}",
                                   {"state": "rolled_back", "bundle_id": previous,
                                    "deployment_guard": previous_guard,
                                    "final_verification": rollback_verification}) from error
        raise RuntimeError(f"적용 전 검증 실패; current 링크 유지: {error}") from error
