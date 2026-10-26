"""Verify and map a LAB policy bundle from a same-host directory."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
from typing import Any, Iterable, Iterator

from .policy import (
    DuplicateKeyError,
    _object_without_duplicates,
    _reject_json_constant,
)


SCHEMA_VERSION = "byul-policy-bundle/0.1"
_ROLES = {
    "gateway_policy",
    "policy_approvals",
    "enforcement_intent",
    "exec_exceptions",
    "target",
}
_TOP_FIELDS = {
    "schema_version",
    "bundle_id",
    "created_at",
    "policy_ref",
    "target_ref",
    "approval",
    "validity",
    "files",
}
_FILE_FIELDS = {"role", "path", "schema_version", "sha256", "bytes"}
_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FILE_PATH = re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9._/-]+$")
_INNER_SCHEMA = re.compile(r"^runtime-[a-z-]+/[0-9]+\.[0-9]+$")
_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)


class PolicyBundleError(ValueError):
    """A bundle cannot be trusted or applied."""


@dataclass(frozen=True)
class VerifiedBundleFile:
    role: str
    relative_path: str
    path: str
    schema_version: str
    sha256: str
    bytes: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class VerifiedPolicyBundle:
    bundle_id: str
    sha256: str
    policy_ref: dict[str, str]
    target_ref: str
    files: tuple[VerifiedBundleFile, ...]
    verified_at: str

    def role_paths(self) -> dict[str, str]:
        return {item.role: item.path for item in self.files}

    def status(self, applied_roles: Iterable[str] = ()) -> dict[str, Any]:
        applied = set(applied_roles)
        return {
            "bundle_id": self.bundle_id,
            "sha256": self.sha256,
            "policy_ref": dict(self.policy_ref),
            "files": [
                {
                    "role": item.role,
                    "path": item.relative_path,
                    "sha256": item.sha256,
                    "applied": item.role in applied,
                }
                for item in self.files
            ],
            "verified_at": self.verified_at,
        }

    @contextmanager
    def materialized_files(self) -> Iterator[dict[str, str]]:
        """Expose only the already verified bytes for the lifetime of one run."""

        try:
            with tempfile.TemporaryDirectory(prefix="serving-runtime-policy-bundle-") as temporary:
                snapshot_root = Path(temporary)
                paths: dict[str, str] = {}
                written: dict[str, bytes] = {}
                for item in self.files:
                    target = snapshot_root / item.relative_path
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    previous = written.get(item.relative_path)
                    if previous is None:
                        with target.open("xb") as output:
                            output.write(item.content)
                        target.chmod(0o600)
                        written[item.relative_path] = item.content
                    elif previous != item.content:
                        raise PolicyBundleError(
                            f"{item.relative_path}: 같은 경로에 서로 다른 검증 바이트가 있습니다"
                        )
                    paths[item.role] = str(target)
                yield paths
        except PolicyBundleError:
            raise
        except OSError as error:
            raise PolicyBundleError(
                f"bundle.json: 검증 파일의 시작 스냅샷을 만들 수 없습니다({type(error).__name__})"
            ) from error

    def mvp_options(
        self,
        observation_feeds: Iterable[tuple[str, str]],
        *,
        role_paths: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], dict[str, Any]]:
        """Map verified roles to existing MVP inputs and record applicability."""

        by_role = {item.role: item for item in self.files}
        paths = (
            role_paths
            if role_paths is not None
            else {role: item.path for role, item in by_role.items()}
        )
        if set(paths) != set(by_role):
            raise ValueError("role_paths must contain every verified bundle role")
        options = {"policy_path": paths["gateway_policy"]}
        applied = {"gateway_policy"}
        direct = {
            "policy_approvals": "policy_approvals_path",
            "target": "target_path",
        }
        for role, option in direct.items():
            if role in paths:
                options[option] = paths[role]
                applied.add(role)

        has_tetragon = any(kind == "tetragon" for kind, _path in observation_feeds)
        if has_tetragon and "enforcement_intent" in paths:
            options["observation_intent_path"] = paths["enforcement_intent"]
            applied.add("enforcement_intent")
        if (
            "enforcement_intent" in applied
            and by_role["enforcement_intent"].schema_version
            != "runtime-enforcement-intent/1.0"
            and "exec_exceptions" in paths
        ):
            options["observation_exceptions_path"] = paths["exec_exceptions"]
            applied.add("exec_exceptions")
        return options, self.status(applied)


def _error(file_name: str, reason: str) -> PolicyBundleError:
    return PolicyBundleError(f"{file_name}: {reason}")


def _strict_json(raw: bytes, file_name: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_object_without_duplicates,
            parse_constant=_reject_json_constant,
        )
    except UnicodeDecodeError as error:
        raise _error(file_name, "UTF-8 JSON이 아닙니다") from error
    except DuplicateKeyError as error:
        raise _error(file_name, f"중복 JSON 키가 있습니다({error})") from error
    except (json.JSONDecodeError, ValueError, RecursionError) as error:
        raise _error(file_name, "유효한 JSON이 아닙니다") from error


def _exact_object(value: object, fields: set[str], file_name: str, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(file_name, f"{where}는 객체여야 합니다")
    missing = sorted(fields - set(value))
    unknown = sorted(set(value) - fields)
    if missing:
        raise _error(file_name, f"{where} 필수 필드가 없습니다: {', '.join(missing)}")
    if unknown:
        raise _error(file_name, f"{where}에 알 수 없는 필드가 있습니다: {', '.join(unknown)}")
    return value


def _local_id(value: object, file_name: str, where: str) -> str:
    if not isinstance(value, str) or _LOCAL_ID.fullmatch(value) is None:
        raise _error(file_name, f"{where}가 local_id 형식이 아닙니다")
    return value


def _timestamp(value: object, file_name: str, where: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise _error(file_name, f"{where}가 시간대 포함 RFC 3339 시각이 아닙니다")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _error(file_name, f"{where}가 유효한 시각이 아닙니다") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise _error(file_name, f"{where}에 시간대가 없습니다")
    return parsed


def _nullable_timestamp(value: object, file_name: str, where: str) -> datetime | None:
    return None if value is None else _timestamp(value, file_name, where)


def _read_file(path: Path, display_name: str) -> bytes:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise _error(display_name, "일반 파일이 아닙니다")
        return path.read_bytes()
    except PolicyBundleError:
        raise
    except FileNotFoundError as error:
        raise _error(display_name, "파일이 없습니다") from error
    except OSError as error:
        raise _error(display_name, f"파일을 읽을 수 없습니다({type(error).__name__})") from error


def verify_policy_bundle(
    bundle_dir: str | Path, *, now: datetime | None = None
) -> VerifiedPolicyBundle:
    """Validate one immutable-at-startup directory against policy-bundle 0.1."""

    directory_text = str(bundle_dir)
    try:
        root = Path(bundle_dir).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise _error(directory_text, "묶음 디렉터리를 찾을 수 없습니다") from error
    if not root.is_dir():
        raise _error(directory_text, "묶음 경로가 디렉터리가 아닙니다")

    manifest_name = "bundle.json"
    try:
        manifest_path = (root / manifest_name).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise _error(manifest_name, "파일이 없습니다") from error
    if not manifest_path.is_relative_to(root):
        raise _error(manifest_name, "심볼릭 링크가 묶음 디렉터리 밖을 가리킵니다")
    raw_manifest = _read_file(manifest_path, manifest_name)
    document = _strict_json(raw_manifest, manifest_name)
    bundle = _exact_object(document, _TOP_FIELDS, manifest_name, "최상위")
    if bundle["schema_version"] != SCHEMA_VERSION:
        raise _error(manifest_name, f"schema_version은 {SCHEMA_VERSION}이어야 합니다")

    bundle_id = _local_id(bundle["bundle_id"], manifest_name, "bundle_id")
    _timestamp(bundle["created_at"], manifest_name, "created_at")
    target_ref = _local_id(bundle["target_ref"], manifest_name, "target_ref")

    policy_ref = _exact_object(
        bundle["policy_ref"], {"policy_id", "policy_version", "sha256"}, manifest_name, "policy_ref"
    )
    policy_id = _local_id(policy_ref["policy_id"], manifest_name, "policy_ref.policy_id")
    policy_version = policy_ref["policy_version"]
    policy_sha = policy_ref["sha256"]
    if not isinstance(policy_version, str) or not 1 <= len(policy_version) <= 64:
        raise _error(manifest_name, "policy_ref.policy_version 길이는 1~64여야 합니다")
    if not isinstance(policy_sha, str) or _SHA256.fullmatch(policy_sha) is None:
        raise _error(manifest_name, "policy_ref.sha256이 SHA-256 형식이 아닙니다")

    approval = _exact_object(
        bundle["approval"], {"approved_by", "approved_at", "approval_ref"}, manifest_name, "approval"
    )
    approved_by = approval["approved_by"]
    if not isinstance(approved_by, str) or not 1 <= len(approved_by) <= 128:
        raise _error(manifest_name, "approval.approved_by 길이는 1~128이어야 합니다")
    _timestamp(approval["approved_at"], manifest_name, "approval.approved_at")
    _local_id(approval["approval_ref"], manifest_name, "approval.approval_ref")

    validity = _exact_object(
        bundle["validity"], {"valid_from", "valid_until"}, manifest_name, "validity"
    )
    valid_from = _nullable_timestamp(validity["valid_from"], manifest_name, "validity.valid_from")
    valid_until = _nullable_timestamp(validity["valid_until"], manifest_name, "validity.valid_until")
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        raise _error(manifest_name, "validity.valid_from이 valid_until보다 늦습니다")
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        raise ValueError("now must include a timezone")
    checked_at = checked_at.astimezone(timezone.utc)
    if valid_from is not None and checked_at < valid_from:
        raise _error(manifest_name, "아직 유효 기간이 시작되지 않았습니다")
    if valid_until is not None and checked_at > valid_until:
        raise _error(manifest_name, "유효 기간이 만료되었습니다")

    entries = bundle["files"]
    if not isinstance(entries, list) or not entries:
        raise _error(manifest_name, "files는 하나 이상의 항목이 있는 배열이어야 합니다")
    normalized_entries: list[dict[str, Any]] = []
    roles: set[str] = set()
    for index, raw_entry in enumerate(entries):
        where = f"files[{index}]"
        entry = _exact_object(raw_entry, _FILE_FIELDS, manifest_name, where)
        role = entry["role"]
        if not isinstance(role, str) or role not in _ROLES:
            raise _error(manifest_name, f"{where}.role이 허용된 역할이 아닙니다")
        if role in roles:
            raise _error(manifest_name, f"{where}.role이 중복되었습니다: {role}")
        roles.add(role)
        relative = entry["path"]
        if (
            not isinstance(relative, str)
            or "\\" in relative
            or _FILE_PATH.fullmatch(relative) is None
            or PurePosixPath(relative).is_absolute()
            or ".." in PurePosixPath(relative).parts
        ):
            raise _error(manifest_name, f"{where}.path가 안전한 상대 경로가 아닙니다")
        inner_schema = entry["schema_version"]
        if not isinstance(inner_schema, str) or _INNER_SCHEMA.fullmatch(inner_schema) is None:
            raise _error(manifest_name, f"{where}.schema_version 형식이 잘못되었습니다")
        digest = entry["sha256"]
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            raise _error(manifest_name, f"{where}.sha256이 SHA-256 형식이 아닙니다")
        byte_count = entry["bytes"]
        if not isinstance(byte_count, int) or isinstance(byte_count, bool) or byte_count < 1:
            raise _error(manifest_name, f"{where}.bytes는 1 이상의 정수여야 합니다")
        normalized_entries.append(entry)
    if "gateway_policy" not in roles:
        raise _error(manifest_name, "gateway_policy 역할 파일이 필요합니다")

    verified_files: list[VerifiedBundleFile] = []
    gateway_document: dict[str, Any] | None = None
    for entry in normalized_entries:
        relative = entry["path"]
        try:
            path = (root / relative).resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise _error(relative, "파일이 없습니다") from error
        if not path.is_relative_to(root):
            raise _error(relative, "심볼릭 링크가 묶음 디렉터리 밖을 가리킵니다")
        raw = _read_file(path, relative)
        if len(raw) != entry["bytes"]:
            raise _error(relative, f"bytes가 다릅니다(기대 {entry['bytes']}, 실제 {len(raw)})")
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != entry["sha256"]:
            raise _error(relative, "SHA-256이 bundle.json과 다릅니다")
        inner = _strict_json(raw, relative)
        if not isinstance(inner, dict):
            raise _error(relative, "내부 문서는 JSON 객체여야 합니다")
        if inner.get("schema_version") != entry["schema_version"]:
            raise _error(relative, "내부 schema_version이 bundle.json 항목과 다릅니다")
        if entry["role"] == "gateway_policy":
            gateway_document = inner
        verified_files.append(
            VerifiedBundleFile(
                role=entry["role"],
                relative_path=relative,
                path=str(path),
                schema_version=entry["schema_version"],
                sha256=entry["sha256"],
                bytes=entry["bytes"],
                content=raw,
            )
        )

    assert gateway_document is not None
    if gateway_document.get("target_id") != target_ref:
        gateway_path = next(
            item.relative_path for item in verified_files if item.role == "gateway_policy"
        )
        raise _error(gateway_path, "target_id가 bundle.json의 target_ref와 다릅니다")

    return VerifiedPolicyBundle(
        bundle_id=bundle_id,
        sha256=hashlib.sha256(raw_manifest).hexdigest(),
        policy_ref={
            "policy_id": policy_id,
            "policy_version": policy_version,
            "sha256": policy_sha,
        },
        target_ref=target_ref,
        files=tuple(verified_files),
        verified_at=checked_at.isoformat(),
    )
