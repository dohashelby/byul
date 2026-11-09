"""B4 로컬 파일 bytes와 선언 결합만 확인한다. 실행·수집·권한 검증은 하지 않는다."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat

from guard.models import Environment, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json

SCHEMA = "guard-b-tp2-local-verification/1"
MAX_BYTES = 2 * 1024 * 1024


class InputFileError(ValueError):
    pass


def _read(root, pointer):
    """지정한 로컬 루트 안의 일반 파일만 제한된 크기로 읽는다."""
    text = pointer["path"]
    path = Path(text)
    if text.startswith(("\\\\", "//")) or "://" in text or path.drive and not path.is_absolute():
        raise InputFileError("LOCAL_PATH_REQUIRED")
    path = Path(os.path.abspath(root / path))
    try:
        relative = path.relative_to(root)
    except ValueError:
        raise InputFileError("INPUT_OUTSIDE_ROOT") from None
    current = root
    for component in relative.parts:
        current = current / component
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InputFileError("INPUT_LINK_UNSUPPORTED")
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise InputFileError("INPUT_NOT_REGULAR")
    if not 0 < before.st_size <= MAX_BYTES:
        raise InputFileError("INPUT_SIZE_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)
    if (not stat.S_ISREG(opened.st_mode) or identity(before) != identity(opened)
            or identity(opened) != identity(after) or identity(after) != identity(path.lstat())
            or len(raw) != before.st_size):
        raise InputFileError("INPUT_CHANGED_DURING_READ")
    digest = hashlib.sha256(raw).hexdigest()
    return raw, digest, relative.as_posix()


def verify_local_inputs(inputs, root):
    """기존 JSON 선언을 대조한다. 원 파일 내용은 보고서에 복사하지 않는다."""
    root = Path(root)
    if str(root).startswith(("\\\\", "//")):
        raise ValueError("LOCAL_INPUT_ROOT_REQUIRED")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("LOCAL_INPUT_ROOT_REQUIRED")
    report = {"schema": SCHEMA, "file_checks": [], "binding_checks": [], "errors": [], "unresolved": [],
              "prepared_assets_verified": False, "runtime_binding_verified": False,
              "execution_authorized": False, "ready_for_execution": False,
              "GPU_or_AWS_operations": 0, "subprocesses_started": 0}

    def diagnostic(collection, cid, field, code):
        report[collection].append({"check_id": cid, "field": field, "code": code})

    def match(cid, field, actual, expected):
        state = "UNRESOLVED" if actual is None or expected is None else "MATCH" if actual == expected else "MISMATCH"
        report["binding_checks"].append({"check_id": cid, "field": field, "status": state})
        if state != "MATCH":
            diagnostic("errors" if state == "MISMATCH" else "unresolved", cid, field,
                       "DECLARED_BINDING_MISMATCH" if state == "MISMATCH" else "DECLARED_BINDING_UNAVAILABLE")

    for cid, section in inputs["checks"].items():
        payloads = {}
        for group in ("runtime_refs", "controls"):
            for key, pointer in section[group].items():
                field = group + "." + key
                if pointer is None:
                    diagnostic("unresolved", cid, field, "POINTER_NOT_PROVIDED")
                    continue
                check = {"check_id": cid, "field": field, "declared_file_sha256": pointer["file_sha256"],
                         "bytes_verified": False, "json_object_verified": False}
                report["file_checks"].append(check)
                try:
                    raw, digest, relative = _read(root, pointer)
                    check.update(bytes_read=True, file_sha256=digest, bytes=len(raw), path=relative)
                    if digest != pointer["file_sha256"]:
                        raise InputFileError("INPUT_FILE_SHA_MISMATCH")
                    check["bytes_verified"] = True
                    value = strict_json(raw, limit=MAX_BYTES)
                    if type(value) is not dict:
                        raise InputFileError("JSON_OBJECT_REQUIRED")
                    check["json_object_verified"] = True
                    payloads[field] = value
                except InputFileError as exc:
                    diagnostic("errors", cid, field, str(exc))
                except FileNotFoundError:
                    diagnostic("errors", cid, field, "INPUT_FILE_NOT_FOUND")
                except OSError:
                    diagnostic("errors", cid, field, "INPUT_FILE_UNAVAILABLE")
                except (ValueError, TypeError, UnicodeError, RecursionError):
                    diagnostic("errors", cid, field, "INPUT_JSON_INVALID")
        env = None
        if "runtime_refs.ENV" in payloads:
            try:
                env = Environment.model_validate(payloads["runtime_refs.ENV"])
                if not env.hash_ok():
                    raise ValueError
            except (ValueError, TypeError):
                env = None
                diagnostic("errors", cid, "runtime_refs.ENV", "SEALED_ENV_INVALID")
        target = payloads.get("runtime_refs.target")
        prepared = payloads.get("runtime_refs.prepared")
        env_ref = env.ref().model_dump(mode="json") if env is not None else None
        hashes = {key: sha256_hex(canonical_json(value)) for key, value in payloads.items()}
        values = section["policy_values"]
        prefix = "rank" if cid == "LIF-08" else "recovery" if cid == "LIF-11" else "supply"
        match(cid, "policy_values." + prefix + "_environment_sha256",
              env.ref().sha256 if env is not None else None, values.get(prefix + "_environment_sha256"))
        if target is not None:
            match(cid, "target.environment_ref", target.get("environment_ref"), env_ref)
        session = prepared.get("session") if prepared is not None else None
        if prepared is not None and (prepared.get("schema") != "guard-lifecycle-assets/1"
                                     or type(session) is not str or not re.fullmatch(r"[0-9a-f]{32}", session)):
            diagnostic("errors", cid, "runtime_refs.prepared", "PREPARED_METADATA_INVALID")
        if cid == "SUP-06":
            candidates = []
            if target is not None and prepared is not None:
                candidates = [role for role in ("normal", "challenge")
                              if hashes["runtime_refs.target"] == values.get("supply_" + role + "_target_sha256")
                              and hashes["runtime_refs.prepared"] == values.get("supply_" + role + "_prepared_sha256")]
                if not candidates:
                    diagnostic("errors", cid, "runtime_refs", "SUPPLY_ROLE_PAIR_MISMATCH")
            report["binding_checks"].append({"check_id": cid, "field": "supply_role_pair", "matching_roles": candidates,
                                              "role_binding_verified": False})
            diagnostic("unresolved", cid, "supply_role", "CURRENT_ROLE_ASSIGNMENT_UNVERIFIED")
        else:
            for key in ("target", "prepared") + (("selection", "scenario") if cid == "LIF-11" else ()):
                match(cid, "policy_values." + prefix + "_" + key + "_sha256", hashes.get("runtime_refs." + key),
                      values.get(prefix + "_" + key + "_sha256"))
        # 알려진 결합 키만 대조한다. 제어 절차·자산 지원 전체를 승인하지 않는다.
        for field, payload in payloads.items():
            if field == "runtime_refs.ENV":
                continue
            for value in (payload, payload.get("model_read_scope")):
                if type(value) is not dict:
                    continue
                for key, expected in (("environment_ref", env_ref), ("target_sha256", hashes.get("runtime_refs.target")),
                                      ("prepared_sha256", hashes.get("runtime_refs.prepared")), ("session", session)):
                    if key in value and not (value is payload and (
                            field == "runtime_refs.target" and key == "environment_ref"
                            or field == "runtime_refs.prepared" and key == "session")):
                        match(cid, field + "." + key, value[key], expected)
        diagnostic("unresolved", cid, "prepared_assets", "PREPARED_ASSET_AND_RUNTIME_CHECKS_REQUIRED")
        diagnostic("unresolved", cid, "controls", "CONTROL_EXECUTION_SEMANTICS_UNVERIFIED")
    checks = report["file_checks"]
    report["all_declared_files_verified"] = bool(checks) and all(v["bytes_verified"] for v in checks)
    report["all_declared_json_objects_verified"] = bool(checks) and all(v["json_object_verified"] for v in checks)
    report["status"] = "FAILED" if report["errors"] else "INCOMPLETE" if report["unresolved"] else "VERIFIED_DECLARATIONS"
    return report
