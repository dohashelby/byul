"""BYUL 계약 입력을 guard ENV/POL로 변환하는 명시적인 오프라인 경계 도구.

BYUL 루트에서 실행:
    uv run --project platform/api --with ./update python update/tools/import_byul_inputs.py \
        --environment <environment.json> --policy <policy.json> \
        --pack update/packs/PACK-BASELINE --output <새 외부 폴더>
    uv run --project platform/api --with ./update python update/tools/import_byul_inputs.py \
        --verify-import <기존 import 폴더> --pack update/packs/PACK-BASELINE

기존 platform의 계약 검증기·정규 해시를 사용한다. guard의 제품 의존성이나
공용 스키마를 바꾸지 않으며 엔진·네트워크·객체 저장소를 실행하지 않는다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any

from pydantic import ValidationError
import yaml

from byul_platform.contracts import Contracts, canonical_json, canonical_sha256
from guard.models import Environment, PackIntegrityError, Policy, load_pack, new_id


REPO_ROOT = Path(__file__).resolve().parents[2]
_BUNDLE_FILES = ("source-environment.json", "source-policy.json", "environment.json", "policy.json")
_MAPPING_KEYS = frozenset({"format", "checked_at", "target_ref", "environment_ref", "byul_policy_ref",
                           "guard_environment_ref", "guard_policy_ref", "pack_ref", "validity", "limitations", "files"})


class InputBoundaryError(ValueError):
    """값이나 원문을 포함하지 않는 고정 코드 입력 오류."""


def _load_input_pack(path: Path):
    """팩 입력 오류만 정규화한다. 공용 로더의 예상 밖 실패는 숨기지 않는다."""
    try:
        return load_pack(path)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, PackIntegrityError) as error:
        # 기존 팩 읽기/검증 실패의 CLI 코드를 유지한다. 파서 문맥은 출력하지 않는다.
        raise InputBoundaryError("INPUT_IMPORT_FAILED") from error
    except KeyError as error:
        # 매니페스트 checks에는 있으나 files에 없는 정의를 공용 로더가 조회한 경우다.
        # 다른 KeyError나 RuntimeError 등은 프로그래밍 오류일 수 있으므로 전파한다.
        missing = error.args[0] if len(error.args) == 1 else None
        if isinstance(missing, str) and missing.startswith("checks/") and missing.endswith(".yaml"):
            raise InputBoundaryError("INPUT_IMPORT_FAILED") from error
        raise


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InputBoundaryError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise InputBoundaryError("JSON_NONFINITE_NUMBER")


def _float(value):
    number = float(value)
    if not math.isfinite(number):
        raise InputBoundaryError("JSON_NONFINITE_NUMBER")
    return number


def _parse_document(raw: bytes) -> dict[str, Any]:
    try:
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except InputBoundaryError:
        raise
    except (UnicodeError, ValueError) as error:
        raise InputBoundaryError("INPUT_JSON_UNREADABLE") from error
    if not isinstance(doc, dict):
        raise InputBoundaryError("INPUT_DOCUMENT_NOT_OBJECT")
    return doc


def read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise InputBoundaryError("INPUT_JSON_UNREADABLE") from error
    return _parse_document(raw), raw


def _time(value: str | datetime) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError, OverflowError) as error:
        raise InputBoundaryError("AS_OF_REQUIRES_TIMEZONE") from error


def convert_documents(environment: dict, policy: dict, pack, contracts: Contracts, *, as_of: datetime):
    """계약·정확한 원본 연결을 검증한 뒤 새 guard 객체와 내부 매핑 기록을 반환한다.

    입력 문서를 수정하지 않는다. 문서 판본과 guard 객체 판본은 서로 다른 ID 공간이다.
    정책 유효 기간은 이 import 시점에 확인하며 이후 실행 시점의 재검사를 대신하지 않는다.
    """
    for name, document in (("environment", environment), ("policy", policy)):
        errors = contracts.errors(name, document)
        if errors:
            raise InputBoundaryError(f"{name.upper()}_SCHEMA_INVALID")

    env_ref = {
        "id": environment["environment_id"],
        "revision": environment["revision"],
        "sha256": canonical_sha256(environment),
    }
    if policy["environment_ref"] != env_ref:
        raise InputBoundaryError("POLICY_ENVIRONMENT_REF_MISMATCH")
    if policy["target_ref"] != environment["target"]["target_ref"]:
        raise InputBoundaryError("POLICY_TARGET_REF_MISMATCH")
    wanted = policy["verification"]["pack"]
    if wanted != {"id": pack.ref().id, "revision": pack.ref().revision}:
        raise InputBoundaryError("POLICY_PACK_REF_MISMATCH")

    # guard RuntimeInfo는 누락 시 1을 기본값으로 쓰므로, 계약 입력에는 추정하지 않는다.
    runtime = environment["target"].get("runtime")
    if runtime is None or any(key not in runtime for key in ("nodes", "tp_groups")):
        raise InputBoundaryError("RUNTIME_TOPOLOGY_UNDECLARED")

    checked_at = _time(as_of)
    validity = policy["validity"]
    start = _time(validity["valid_from"]) if validity["valid_from"] is not None else None
    until = _time(validity["valid_until"]) if validity["valid_until"] is not None else None
    if start is not None and until is not None and start >= until:
        raise InputBoundaryError("POLICY_VALIDITY_INVALID")
    if start is not None and checked_at < start:
        raise InputBoundaryError("POLICY_NOT_YET_VALID")
    if until is not None and checked_at >= until:
        raise InputBoundaryError("POLICY_EXPIRED")

    approval = policy["approval"]
    confirmed = policy["status"] == "CONFIRMED"
    target = environment["target"]
    # tokenizer_revision/deployment/runtime 정책은 guard에 대응 필드가 없다.
    # 원본 bytes와 아래 기록에 보존하며 모델 revision 등으로 바꾸지 않는다.
    model = {key: target["model"].get(key) for key in ("id", "revision", "inventory_sha256")}
    try:
        env = Environment(
            id=new_id("ENV"), created_at=environment["created_at"],
            data_origin=environment["data_origin"], engine=target["engine"],
            runtime=runtime, model=model, facts=environment["facts"],
        ).seal()
        pol = Policy(
            id=new_id("POL"), created_at=policy["created_at"], data_origin=policy["data_origin"],
            status=policy["status"], pack=pack.ref(), values=policy["verification"]["values"],
            scope=policy["verification"]["scope"], exceptions=policy["verification"]["exceptions"],
            confirmed_by=approval["approved_by"] if confirmed else None,
            confirmed_at=approval["approved_at"] if confirmed else None,
        ).seal()
    except ValueError as error:
        raise InputBoundaryError("GUARD_INPUT_MODEL_INVALID") from error

    limitations = [
        "NO_ENGINE_EXECUTED", "APPROVAL_IS_DECLARATION_NOT_AUTHENTICATION",
        "POLICY_VALIDITY_MUST_BE_RECHECKED_AT_EXECUTION", "ADAPTER_NOT_IN_ENVIRONMENT_CONTRACT",
        "CPU_PROFILE_NOT_IN_ENVIRONMENT_CONTRACT", "DEPLOYMENT_RETAINED_IN_SOURCE_ONLY",
        "RUNTIME_POLICY_RETAINED_IN_SOURCE_ONLY",
    ]
    if target["model"].get("tokenizer_revision") is not None:
        limitations.append("TOKENIZER_REVISION_RETAINED_IN_SOURCE_ONLY")
    if start is None or until is None:
        limitations.append("POLICY_VALIDITY_PARTLY_UNDECLARED")
    mapping = {
        "format": "guard-byul-input-import/1",
        "checked_at": checked_at.isoformat(),
        "target_ref": target["target_ref"],
        "environment_ref": env_ref,
        "byul_policy_ref": {
            "policy_id": policy["policy_id"], "policy_version": policy["policy_version"],
            "sha256": canonical_sha256(policy),
        },
        "guard_environment_ref": env.ref().model_dump(mode="json"),
        "guard_policy_ref": pol.ref().model_dump(mode="json"),
        "pack_ref": pack.ref().model_dump(mode="json"),
        "validity": dict(validity),
        "limitations": limitations,
    }
    return env, pol, mapping


def import_inputs(environment_path: Path, policy_path: Path, pack_path: Path, output: Path,
                  *, contracts_dir: Path = REPO_ROOT / "contracts", as_of: datetime | None = None):
    """출력 경로는 새 디렉터리여야 한다. 모든 입력 검증 전에는 출력하지 않는다."""
    environment, env_raw = read_document(environment_path)
    policy, pol_raw = read_document(policy_path)
    pack = _load_input_pack(pack_path)
    env, pol, mapping = convert_documents(environment, policy, pack, Contracts(contracts_dir),
                                           as_of=as_of or datetime.now(timezone.utc))
    payloads = {
        "source-environment.json": env_raw,
        "source-policy.json": pol_raw,
        "environment.json": canonical_json(env.to_dict()),
        "policy.json": canonical_json(pol.to_dict()),
    }
    mapping["files"] = [
        {"path": name, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        for name, raw in payloads.items()
    ]
    output = Path(output)
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise InputBoundaryError("OUTPUT_ALREADY_EXISTS") from error
    for name, raw in payloads.items():
        with (output / name).open("xb") as stream:
            stream.write(raw)
    with (output / "provenance.json").open("xb") as stream:
        stream.write(canonical_json(mapping))
    return mapping


def _bundle_file(root: Path, name: str) -> Path:
    """명시된 import 묶음 밖의 파일이나 링크를 따라가지 않는다."""
    path = root / name
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
        raise InputBoundaryError("IMPORT_FILE_PATH_UNSAFE")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise InputBoundaryError("IMPORT_FILE_MISSING") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise InputBoundaryError("IMPORT_FILE_PATH_UNSAFE")
    return resolved


def _file_index(mapping: dict) -> dict:
    files = mapping.get("files")
    if type(files) is not list:
        raise InputBoundaryError("IMPORT_FILE_INDEX_INVALID")
    index = {}
    for item in files:
        if type(item) is not dict or set(item) != {"path", "bytes", "sha256"}:
            raise InputBoundaryError("IMPORT_FILE_INDEX_INVALID")
        name = item["path"]
        if type(name) is not str or name not in _BUNDLE_FILES:
            raise InputBoundaryError("IMPORT_FILE_PATH_UNSAFE")
        if (name in index or type(item["bytes"]) is not int or item["bytes"] < 0
                or type(item["sha256"]) is not str or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None):
            raise InputBoundaryError("IMPORT_FILE_INDEX_INVALID")
        index[name] = item
    if set(index) != set(_BUNDLE_FILES):
        raise InputBoundaryError("IMPORT_FILE_INDEX_INCOMPLETE")
    return index


def _readiness_reasons(env: Environment, pol: Policy, pack, mapping: dict) -> list[str]:
    """파일 입력의 미확인 조건 안내다. 호스트·target·엔진 지원 여부를 판정하지 않는다."""
    reasons = ["READ_ONLY_VERIFICATION_NOT_EXECUTION_AUTHORIZATION", "ENGINE_AND_TARGET_NOT_PROBED",
               "LOCAL_PROVENANCE_NOT_AUTHENTICATED", "APPROVAL_IS_DECLARATION_NOT_AUTHENTICATION"]
    if env.adapter is None:
        reasons.append("ADAPTER_UNDECLARED")
    digest = env.engine.image_digest
    if digest is None:
        reasons.append("ENGINE_IMAGE_DIGEST_UNDECLARED")
    elif "@" not in digest:
        reasons.append("REPOSITORY_PIN_REQUIRES_EXPLICIT_TARGET")
    if env.engine.version is None:
        reasons.append("ENGINE_VERSION_UNDECLARED")
    if env.engine.source_commit is None:
        reasons.append("ENGINE_SOURCE_COMMIT_UNDECLARED")
    for field, code in (("id", "MODEL_ID_UNDECLARED"), ("revision", "MODEL_REVISION_UNDECLARED"),
                        ("inventory_sha256", "MODEL_INVENTORY_UNDECLARED")):
        if getattr(env.model, field) is None:
            reasons.append(code)
    if env.runtime.gpu_count is None:
        reasons.append("RUNTIME_KIND_UNDECLARED")
    elif env.runtime.gpu_count == 0 and env.runtime.cpu is None:
        reasons.append("CPU_PROFILE_UNDECLARED")
    elif env.runtime.gpu_count > 0:
        if env.runtime.gpu_model is None:
            reasons.append("GPU_MODEL_UNDECLARED")
        if env.runtime.driver is None:
            reasons.append("GPU_DRIVER_UNDECLARED")
    if pol.status.value == "DRAFT":
        reasons.append("POLICY_DRAFT")
    if not pack.reviewed:
        reasons.append("PACK_DRAFT")
    if "POLICY_VALIDITY_PARTLY_UNDECLARED" in mapping["limitations"]:
        reasons.append("POLICY_VALIDITY_PARTLY_UNDECLARED")
    return reasons


def verify_import(bundle: Path, pack_path: Path, *, contracts_dir: Path = REPO_ROOT / "contracts",
                  as_of: datetime | None = None) -> dict:
    """import 출력의 일관성을 읽기 전용으로 확인한다. 진위·승인·실행 허가는 아니다.

    로컬 관리자가 원본·출력·provenance를 함께 다시 쓰는 것은 서명 없는 이 묶음으로
    탐지할 수 없다. 반환 Ref·해시는 이번에 읽은 snapshot의 식별 정보다.
    """
    bundle = Path(bundle)
    if bundle.is_symlink() or getattr(bundle, "is_junction", lambda: False)():
        raise InputBoundaryError("IMPORT_FILE_PATH_UNSAFE")
    try:
        root = bundle.resolve(strict=True)
        if not root.is_dir():
            raise InputBoundaryError("IMPORT_BUNDLE_UNAVAILABLE")
    except OSError as error:
        raise InputBoundaryError("IMPORT_BUNDLE_UNAVAILABLE") from error
    provenance_path = _bundle_file(root, "provenance.json")
    mapping, provenance_raw = read_document(provenance_path)
    if set(mapping) != _MAPPING_KEYS or mapping.get("format") != "guard-byul-input-import/1":
        raise InputBoundaryError("IMPORT_MAPPING_FORMAT_INVALID")
    index = _file_index(mapping)
    actual_files = {path.name for path in root.iterdir()}
    required_files = {*_BUNDLE_FILES, "provenance.json"}
    if required_files - actual_files:
        raise InputBoundaryError("IMPORT_FILE_MISSING")
    if actual_files - required_files:
        raise InputBoundaryError("IMPORT_BUNDLE_FILES_MISMATCH")

    documents, payloads = {}, {}
    for name in _BUNDLE_FILES:
        path = _bundle_file(root, name)
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise InputBoundaryError("IMPORT_FILE_UNAVAILABLE") from error
        if len(raw) != index[name]["bytes"] or hashlib.sha256(raw).hexdigest() != index[name]["sha256"]:
            raise InputBoundaryError("IMPORT_FILE_HASH_MISMATCH")
        documents[name], payloads[name] = _parse_document(raw), raw

    try:
        env = Environment.model_validate(documents["environment.json"])
        pol = Policy.model_validate(documents["policy.json"])
    except ValueError as error:
        raise InputBoundaryError("GUARD_INPUT_MODEL_INVALID") from error
    if not env.hash_ok() or not pol.hash_ok():
        raise InputBoundaryError("GUARD_OBJECT_HASH_INVALID")
    contracts = Contracts(contracts_dir)
    id_pattern = contracts.schemas["common"]["$defs"]["object_ref"]["properties"]["id"]["pattern"]
    if (re.fullmatch(id_pattern, env.id) is None or not env.id.startswith("ENV-")
            or re.fullmatch(id_pattern, pol.id) is None or not pol.id.startswith("POL-")):
        raise InputBoundaryError("GUARD_OBJECT_ID_INVALID")
    if (canonical_json(mapping["guard_environment_ref"]) != canonical_json(env.ref().model_dump(mode="json"))
            or canonical_json(mapping["guard_policy_ref"]) != canonical_json(pol.ref().model_dump(mode="json"))):
        raise InputBoundaryError("GUARD_OBJECT_REF_MISMATCH")

    checked_at, verified_at = _time(mapping["checked_at"]), _time(as_of or datetime.now(timezone.utc))
    if checked_at > verified_at:
        raise InputBoundaryError("IMPORT_CHECKED_AT_IN_FUTURE")
    pack = _load_input_pack(pack_path)
    source_env, source_pol = documents["source-environment.json"], documents["source-policy.json"]
    expected_env, expected_pol, expected_mapping = convert_documents(source_env, source_pol, pack, contracts,
                                                                      as_of=checked_at)
    # 원래 생성 ID는 provenance와 정확히 연결한다. 값·revision·시각·출처는 바꾸지 않는다.
    expected_env = expected_env.model_copy(update={"id": env.id}).seal()
    expected_pol = expected_pol.model_copy(update={"id": pol.id}).seal()
    expected_mapping["guard_environment_ref"] = expected_env.ref().model_dump(mode="json")
    expected_mapping["guard_policy_ref"] = expected_pol.ref().model_dump(mode="json")
    if any(canonical_json(mapping[key]) != canonical_json(value) for key, value in expected_mapping.items()
           if key not in {"guard_environment_ref", "guard_policy_ref"}):
        raise InputBoundaryError("IMPORT_SOURCE_MAPPING_MISMATCH")
    if (payloads["environment.json"] != canonical_json(expected_env.to_dict())
            or payloads["policy.json"] != canonical_json(expected_pol.to_dict())):
        raise InputBoundaryError("IMPORT_CONVERSION_OUTPUT_MISMATCH")
    # import 당시의 유효성만으로 충분하지 않다. 이번 명시 시각/현재 UTC에서도 다시 확인한다.
    convert_documents(source_env, source_pol, pack, contracts, as_of=verified_at)
    # 검증 중 파일 교체를 성공으로 보고하지 않는다. 검증 후 변경을 막는다는 보장은 아니다.
    for name, raw in {**payloads, "provenance.json": provenance_raw}.items():
        if _bundle_file(root, name).read_bytes() != raw:
            raise InputBoundaryError("IMPORT_BUNDLE_CHANGED_DURING_VERIFICATION")
    return {
        "verified": True, "engine_executed": False, "execution_ready": None,
        "time_basis": "EXPLICIT_AS_OF" if as_of is not None else "CURRENT_UTC",
        "import_checked_at": checked_at.isoformat(), "verified_at": verified_at.isoformat(),
        "provenance_sha256": hashlib.sha256(provenance_raw).hexdigest(),
        "environment_ref": mapping["environment_ref"], "byul_policy_ref": mapping["byul_policy_ref"],
        "guard_environment_ref": env.ref().model_dump(mode="json"),
        "guard_policy_ref": pol.ref().model_dump(mode="json"), "pack_ref": pack.ref().model_dump(mode="json"),
        "readiness_reasons": _readiness_reasons(env, pol, pack, mapping),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-import", type=Path, help="기존 import 묶음을 읽기 전용으로 검증")
    parser.add_argument("--contracts-dir", type=Path, default=REPO_ROOT / "contracts")
    parser.add_argument("--as-of", help="시간대가 포함된 import/verify 유효 기간 확인 시각")
    args = parser.parse_args(argv)
    if args.verify_import is not None:
        if any(value is not None for value in (args.environment, args.policy, args.output)):
            parser.error("--verify-import cannot be combined with import input/output options")
    elif any(value is None for value in (args.environment, args.policy, args.output)):
        parser.error("import requires --environment, --policy and --output")
    try:
        if args.verify_import is not None:
            verified = verify_import(args.verify_import, args.pack, contracts_dir=args.contracts_dir,
                                     as_of=_time(args.as_of) if args.as_of else None)
            print(json.dumps(verified, ensure_ascii=False))
            return 0
        mapping = import_inputs(args.environment, args.policy, args.pack, args.output,
                                contracts_dir=args.contracts_dir,
                                as_of=_time(args.as_of) if args.as_of else None)
    except (OSError, ValueError) as error:
        code = str(error) if isinstance(error, InputBoundaryError) else "INPUT_IMPORT_FAILED"
        print(code, file=sys.stderr)
        return 2
    print(json.dumps({"imported": True, "engine_executed": False,
                      "guard_environment_ref": mapping["guard_environment_ref"],
                      "guard_policy_ref": mapping["guard_policy_ref"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
