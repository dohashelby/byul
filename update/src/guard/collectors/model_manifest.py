"""엔진 중립 모델 snapshot manifest와 파일·지정 설정 키 기반 변경 추출."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
from typing import Any

from guard.adapters.model_inventory import inventory
from guard.models.checkdef import Pack
from guard.models.enums import DataOrigin
from guard.models.hashing import canonical_json, sha256_hex
from guard.models.objects import Change, ChangeEvidence, ChangeFact, Extractor, VersionPoint
from guard.models.ulid import new_id

CATEGORIES = ("MODEL_WEIGHTS", "MODEL_CODE", "MODEL_TEMPLATE", "MODEL_TOKENIZER", "MODEL_ARCH")
SETTING_KEYS = ("auto_map", "chat_template", "architectures", "model_type", "vocab_size", "max_position_embeddings")
SETTINGS_FILES = ("config.json", "tokenizer_config.json", "processor_config.json", "preprocessor_config.json")


@dataclass(frozen=True)
class ExtractedChange:
    change: Change
    artifact_bytes: bytes
    artifact_name: str = "model-manifest-diff.json"


def build_manifest(snapshot: Path, *, model_id: str | None = None) -> dict:
    snapshot = Path(snapshot)
    result = inventory(snapshot, "all")
    settings = {}
    for name in SETTINGS_FILES:
        path = snapshot / name
        if path.is_file():
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError("model_settings_must_be_object")
            settings[name] = {key: value[key] for key in SETTING_KEYS if key in value}
    return {"schema": "guard.model-manifest/1", "model_id": model_id, **result, "settings": settings}


def _load_manifest(value: Path | dict, model_id: str | None) -> dict:
    if isinstance(value, dict):
        result = dict(value)
    else:
        path = Path(value)
        result = build_manifest(path, model_id=model_id) if path.is_dir() else json.loads(path.read_bytes())
    if not isinstance(result, dict) or result.get("scope") != "all" or not isinstance(result.get("files"), list):
        raise ValueError("invalid_model_manifest")
    seen = set()
    for row in result["files"]:
        path = row.get("path")
        sha = row.get("sha256")
        if (not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
                or ".." in PurePosixPath(path).parts or "\\" in path or ":" in path or path in seen
                or not isinstance(sha, str) or len(sha) != 64 or any(v not in "0123456789abcdef" for v in sha)
                or not isinstance(row.get("bytes"), int) or row["bytes"] < 0):
            raise ValueError("invalid_model_manifest_file")
        seen.add(path)
    result["files"] = sorted(result["files"], key=lambda row: row["path"])
    if result.get("inventory_sha256") != sha256_hex(canonical_json(result["files"])):
        raise ValueError("model_manifest_inventory_hash_mismatch")
    if model_id is not None:
        result["model_id"] = model_id
    return result


def _categories(path: str, before: dict, after: dict) -> set[str]:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(path).suffix
    categories = set()
    if suffix in {".safetensors", ".bin", ".pt", ".gguf"} or name.endswith(".index.json"):
        return {"MODEL_WEIGHTS"}
    if suffix == ".py":
        return {"MODEL_CODE"}
    if name in {"chat_template.jinja", "chat_template.json"}:
        return {"MODEL_TEMPLATE"}
    if name in {"tokenizer.json", "tokenizer.model", "special_tokens_map.json", "added_tokens.json",
                "merges.txt", "generation_config.json"} or name.startswith("vocab.") or suffix == ".tiktoken":
        return {"MODEL_TOKENIZER"}
    if name in SETTINGS_FILES:
        left = before.get("settings", {}).get(path)
        right = after.get("settings", {}).get(path)
        if left is None or right is None:
            return set(CATEGORIES)
        for key, category in (("auto_map", "MODEL_CODE"), ("chat_template", "MODEL_TEMPLATE"),
                              ("architectures", "MODEL_ARCH"), ("model_type", "MODEL_ARCH"),
                              ("vocab_size", "MODEL_TOKENIZER"), ("max_position_embeddings", "MODEL_TOKENIZER")):
            if (key in left, left.get(key)) != (key in right, right.get(key)):
                categories.add(category)
        # 파일이 바뀌었지만 지정 키로 의미를 확정할 수 없는 변경도 보수적으로 남긴다.
        return categories or set(CATEGORIES)
    return set(CATEGORIES)


def extract_model_change(baseline: Path | dict, candidate: Path | dict, *, baseline_id: str | None = None,
                         candidate_id: str | None = None, pack: Pack | None = None) -> ExtractedChange:
    left, right = _load_manifest(baseline, baseline_id), _load_manifest(candidate, candidate_id)
    before = {r["path"]: r for r in left["files"]}
    after = {r["path"]: r for r in right["files"]}
    paths = sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))
    rows = [{"path": path, "before": before.get(path), "after": after.get(path),
             "categories": sorted(_categories(path, left, right))} for path in paths]
    if left.get("model_id") != right.get("model_id"):
        rows.append({"path": "model.id", "before": left.get("model_id"), "after": right.get("model_id"),
                     "categories": list(CATEGORIES)})
    artifact_bytes = canonical_json({"schema": "guard.model-manifest-diff/1", "baseline": left,
                                     "candidate": right, "changes": rows})
    artifact_sha = sha256_hex(artifact_bytes)
    category_checks = (pack.change_selection or {}).get("model_categories", {}) if pack else {}
    facts = [ChangeFact(id=f"model-{index:05d}-{category.lower()}", category=category, target=row["path"],
             before=row["before"], after=row["after"], certainty="PARTIAL",
             evidence=[ChangeEvidence(path="model-manifest-diff.json", sha256=artifact_sha)],
             related_checks=list(category_checks.get(category, [])))
             for index, row in enumerate(rows, 1) for category in row["categories"]]
    change = Change(id=new_id("CHG"), data_origin=DataOrigin.MEASURED,
        baseline=VersionPoint(version=left.get("model_id") or left["inventory_sha256"], source_commit=left["inventory_sha256"]),
        candidate=VersionPoint(version=right.get("model_id") or right["inventory_sha256"], source_commit=right["inventory_sha256"]),
        extractor=Extractor(name="model_manifest", version="0.1.0"), facts=facts,
        analysis={"counts": {"changed_paths": len(paths)}, "model_categories": sorted({f.category for f in facts}),
                  "baseline_inventory_sha256": left["inventory_sha256"],
                  "candidate_inventory_sha256": right["inventory_sha256"],
                  "limitations": ["File-level comparison; LoRA and adapter updates are outside this contract."]}).seal()
    return ExtractedChange(change, artifact_bytes)
