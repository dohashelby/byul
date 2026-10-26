"""BYUL 계약 입력을 guard ENV/POL로 변환하는 명시적인 오프라인 경계 도구.

BYUL 루트에서 실행:
    uv run --project platform/api --with ./update python update/tools/import_byul_inputs.py \
        --environment <environment.json> --policy <policy.json> \
        --pack update/packs/PACK-BASELINE --output <새 외부 폴더>

기존 platform의 계약 검증기·정규 해시를 사용한다. guard의 제품 의존성이나
공용 스키마를 바꾸지 않으며 엔진·네트워크·객체 저장소를 실행하지 않는다.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from byul_platform.contracts import Contracts, canonical_json, canonical_sha256
from guard.models import Environment, Policy, load_pack, new_id


REPO_ROOT = Path(__file__).resolve().parents[2]


class InputBoundaryError(ValueError):
    """값이나 원문을 포함하지 않는 고정 코드 입력 오류."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise InputBoundaryError("JSON_DUPLICATE_KEY")
        result[key] = value
    return result


def _constant(_value):
    raise InputBoundaryError("JSON_NONFINITE_NUMBER")


def read_document(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = Path(path).read_bytes()
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except InputBoundaryError:
        raise
    except (OSError, UnicodeError, ValueError) as error:
        raise InputBoundaryError("INPUT_JSON_UNREADABLE") from error
    if not isinstance(doc, dict):
        raise InputBoundaryError("INPUT_DOCUMENT_NOT_OBJECT")
    return doc, raw


def _time(value: str | datetime) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("naive")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, AttributeError) as error:
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
    pack = load_pack(pack_path)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--contracts-dir", type=Path, default=REPO_ROOT / "contracts")
    parser.add_argument("--as-of", help="시간대가 포함된 import 유효 기간 확인 시각")
    args = parser.parse_args(argv)
    try:
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
