"""Build and apply same-host policy bundles; approval records remain self assertions."""

from __future__ import annotations

from contextlib import contextmanager
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
    subprocess.run(command, check=True)


class StatusUnavailable(RuntimeError):
    """No trustworthy status response was obtained after rollout."""


class StatusMismatch(RuntimeError):
    """A valid status response disagreed with the intended bundle."""


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


def _token(path: str | None) -> str | None:
    if path is None:
        return None
    raw = Path(path).read_text(encoding="utf-8")
    matches = [line.partition("=")[2].strip().strip('"\'') for line in raw.splitlines()
               if line.startswith("RUNTIME_VIEWER_TOKEN=")]
    if len(matches) > 1 or (matches and not matches[0]):
        raise ValueError("env 파일의 RUNTIME_VIEWER_TOKEN이 중복되거나 비었습니다")
    return matches[0] if matches else None


def _checked_status(context: str, namespace: str, kubeconfig: str | None,
                    remote_port: int, token: str | None, bundle_id: str,
                    *, timeout: float = 90) -> None:
    # Recreate 배치에서 이전 Pod 종료 유예(보통 최대 30초)와 새 Pod 연결 시간을 함께 둔다.
    deadline = time.monotonic() + timeout
    last_error = ""
    while True:
        try:
            with _port_forward(context, namespace, kubeconfig, remote_port) as url:
                # 연결 실패 후에는 오래된 Pod에 붙은 포워드를 닫고 새로 연다.
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
            return
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
                 dry_run: bool = False, existing: str | None = None) -> dict:
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
    if dry_run:
        return {"bundle_id": source.bundle_id, "previous": previous, "dry_run": True}
    token = _token(token_env_file)
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
    switched = False
    try:
        os.symlink(source.bundle_id, root / "current.next")
        os.replace(root / "current.next", current)
        switched = True
        if os.readlink(current) != source.bundle_id:
            raise RuntimeError("current 링크 대상 재확인 실패")
        base = _kubectl(context, namespace, kubeconfig) + ["rollout"]
        _run(base + ["restart", "deployment/serving-gateway"])
        _run(base + ["status", "deployment/serving-gateway", "--timeout=180s"])
        _checked_status(context, namespace, kubeconfig, parsed_url.port, token, source.bundle_id)
        return {"bundle_id": source.bundle_id, "previous": previous, "dry_run": False}
    except StatusUnavailable:
        raise
    except Exception as error:
        if switched:
            try:
                os.symlink(previous, root / "current.rollback")
                os.replace(root / "current.rollback", current)
                if os.readlink(current) != previous:
                    raise RuntimeError("되돌린 링크 대상 불일치")
                base = _kubectl(context, namespace, kubeconfig) + ["rollout"]
                _run(base + ["restart", "deployment/serving-gateway"])
                _run(base + ["status", "deployment/serving-gateway", "--timeout=180s"])
            except Exception as rollback_error:
                raise RuntimeError(f"적용 실패; 되돌리기도 실패: {rollback_error}") from error
        raise RuntimeError(f"적용 실패; 이전 링크 복원 및 재시작 완료: {error}") from error
