"""../contracts/schemas 로딩·검증과 정규 JSON 해시.

화면에 내보내는 계약 문서는 모두 여기서 검증한다. OpenAPI 문서에도 같은 스키마를
components로 넣어, 화면의 TypeScript 타입이 계약 스키마에서 나오게 한다.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

# 스키마 파일 이름 → OpenAPI component 이름
COMPONENT_NAMES = {
    "common": "Common",
    "environment": "Environment",
    "policy": "Policy",
    "policy-bundle": "PolicyBundle",
    "run-context": "RunContext",
    "verification-result": "VerificationResult",
    "runtime-event": "RuntimeEvent",
    "runtime-status": "RuntimeStatus",
    "runtime-lab-report": "RuntimeLabReport",
    "runtime-request-detail": "RuntimeRequestDetail",
    "revalidation-request": "RevalidationRequest",
    "evidence-index": "EvidenceIndex",
}


def canonical_json(document: object) -> bytes:
    """정렬된 키·UTF-8·공백 없는 JSON (serving-guard SPEC §2.3)."""
    return json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def canonical_sha256(document: object) -> str:
    return hashlib.sha256(canonical_json(document)).hexdigest()


@dataclass(frozen=True)
class SchemaError:
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


class Contracts:
    def __init__(self, contracts_dir: Path) -> None:
        self.dir = contracts_dir
        schema_dir = contracts_dir / "schemas"
        self.schemas: dict[str, dict[str, Any]] = {
            p.name.removesuffix(".schema.json"): json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(schema_dir.glob("*.schema.json"))
        }
        if not self.schemas:
            raise FileNotFoundError(f"계약 스키마가 없습니다: {schema_dir}")
        registry = Registry().with_resources(
            (s["$id"], Resource.from_contents(s)) for s in self.schemas.values()
        )
        self._validators = {
            name: Draft202012Validator(schema, registry=registry, format_checker=FormatChecker())
            for name, schema in self.schemas.items()
        }

    @cached_property
    def version(self) -> str:
        ids = {s.get("$id", "") for s in self.schemas.values()}
        versions = {i.split("/")[-2] for i in ids if i.count("/") >= 2}
        return ",".join(sorted(versions)) or "unknown"

    def errors(self, schema: str, document: object) -> list[SchemaError]:
        validator = self._validators[schema]
        found = sorted(validator.iter_errors(document), key=lambda e: list(map(str, e.absolute_path)))
        out: list[SchemaError] = []
        for e in found:
            base = "/" + "/".join(str(p) for p in e.absolute_path)
            # 필수 필드 누락은 화면에서 그 필드 옆에 보이도록 누락된 필드 경로로 바꾼다.
            if e.validator == "required" and isinstance(e.instance, dict):
                for name in e.validator_value:
                    if name not in e.instance:
                        out.append(SchemaError(base.rstrip("/") + "/" + name, f"필수 필드 '{name}'가 없습니다"))
                continue
            out.append(SchemaError(base, e.message))
        return out

    def is_valid(self, schema: str, document: object) -> bool:
        return self._validators[schema].is_valid(document)

    # --- OpenAPI ---------------------------------------------------------

    def openapi_components(self) -> dict[str, Any]:
        """계약 스키마를 OpenAPI 3.1 components.schemas 형태로 바꾼다.

        파일 최상위는 `<Name>`, `$defs/x`는 `<Name>_x`가 된다. `$ref`는 그에 맞게 고친다.
        """
        out: dict[str, Any] = {}
        for file_name, schema in self.schemas.items():
            name = COMPONENT_NAMES.get(file_name, file_name)
            body = copy.deepcopy(schema)
            defs = body.pop("$defs", {})
            for key in ("$schema", "$id"):
                body.pop(key, None)
            if file_name != "common":
                out[name] = _rewrite_refs(body, name)
            for def_name, def_schema in defs.items():
                out[f"{name}_{def_name}"] = _rewrite_refs(def_schema, name)
        return out


def _rewrite_refs(node: Any, local: str) -> Any:
    if isinstance(node, dict):
        result = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                result[key] = _rewrite_ref(value, local)
            else:
                result[key] = _rewrite_refs(value, local)
        return result
    if isinstance(node, list):
        return [_rewrite_refs(v, local) for v in node]
    return node


def _rewrite_ref(ref: str, local: str) -> str:
    target, _, pointer = ref.partition("#")
    if target:
        file_name = target.removesuffix(".schema.json")
        owner = COMPONENT_NAMES.get(file_name, file_name)
    else:
        owner = local
    if pointer.startswith("/$defs/"):
        return f"#/components/schemas/{owner}_{pointer.removeprefix('/$defs/')}"
    return f"#/components/schemas/{owner}"
