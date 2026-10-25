"""contracts/examples가 스키마와 서로의 해시 참조에 맞는지 확인한다.

실행: uv run --with jsonschema python contracts/tools/check_contracts.py
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "schemas"
EXAMPLES = ROOT / "examples"

# 예시 파일 → 스키마
CASES = {
    "environment.json": "environment",
    "policy.json": "policy",
    "bundle/bundle.json": "policy-bundle",
    "run-context.json": "run-context",
    "verification-result.json": "verification-result",
    "runtime-events.jsonl": "runtime-event",
    "runtime-status.json": "runtime-status",
    "revalidation-request.json": "revalidation-request",
    "evidence-index.json": "evidence-index",
    "runtime-lab-report.json": "runtime-lab-report",
    "runtime-request-detail.json": "runtime-request-detail",
}


def canonical_sha256(document: object) -> str:
    """정렬된 키·UTF-8·공백 없는 JSON의 SHA-256 (serving-guard SPEC §2.3)."""
    data = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_documents(path: Path) -> list[object]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(path.read_text(encoding="utf-8"))]


def main() -> int:
    schemas = {p.name.removesuffix(".schema.json"): json.loads(p.read_text(encoding="utf-8")) for p in SCHEMAS.glob("*.schema.json")}
    registry = Registry().with_resources((s["$id"], Resource.from_contents(s)) for s in schemas.values())
    errors: list[str] = []

    for name, schema in schemas.items():
        Draft202012Validator.check_schema(schema)

    for example, schema_name in CASES.items():
        validator = Draft202012Validator(schemas[schema_name], registry=registry, format_checker=FormatChecker())
        for index, document in enumerate(load_documents(EXAMPLES / example)):
            for error in validator.iter_errors(document):
                location = "/".join(str(p) for p in error.absolute_path)
                errors.append(f"{example}[{index}] {location}: {error.message}")

    # 문서끼리의 해시 참조
    environment = json.loads((EXAMPLES / "environment.json").read_text(encoding="utf-8"))
    policy = json.loads((EXAMPLES / "policy.json").read_text(encoding="utf-8"))
    bundle = json.loads((EXAMPLES / "bundle/bundle.json").read_text(encoding="utf-8"))
    if policy["environment_ref"]["sha256"] != canonical_sha256(environment):
        errors.append("policy.environment_ref.sha256 != environment.json 정규 해시")
    if bundle["policy_ref"]["sha256"] != canonical_sha256(policy):
        errors.append("bundle.policy_ref.sha256 != policy.json 정규 해시")
    for item in bundle["files"]:
        path = EXAMPLES / "bundle" / item["path"]
        if not path.is_file():
            errors.append(f"bundle 파일 없음: {item['path']}")
        elif file_sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            errors.append(f"bundle 파일 해시·크기 불일치: {item['path']}")
    index = json.loads((EXAMPLES / "evidence-index.json").read_text(encoding="utf-8"))
    for item in index["items"]:
        path = EXAMPLES / "files" / item["path"]
        if not path.is_file() or file_sha256(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            errors.append(f"증거 파일 해시·크기 불일치: {item['path']}")

    for line in errors:
        print(f"FAIL {line}")
    print(f"schemas={len(schemas)} examples={len(CASES)} errors={len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
