"""vLLM 소스의 원본 바이트와 경로 규칙으로 CHG를 추출한다. 대상 코드는 실행하지 않는다."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from guard.models.checkdef import Pack
from guard.models.enums import DataOrigin
from guard.models.hashing import canonical_json, sha256_hex
from guard.models.objects import Change, ChangeEvidence, ChangeFact, Extractor, VersionPoint
from guard.models.ulid import new_id
from guard.runtime.git import changed_paths, read_blobs, resolve_commit

RULES_PATH = Path(__file__).with_name("assets") / "vllm_change_rules.yaml"


def prepare_source_repository(options: dict) -> Path:
    """어댑터가 엔진 소스 캐시의 기본값과 git 취득을 소유한다."""
    from guard.runtime.git import ensure_repository
    return ensure_repository(Path(options.get("cache", ".guard/cache/engine-src/vllm")),
                             options.get("url", "https://github.com/vllm-project/vllm.git"))


@dataclass(frozen=True)
class ExtractedChange:
    change: Change
    artifact_bytes: bytes
    artifact_name: str = "engine-source-diff.json"


def classify_path(path: str, rules: dict[str, Any]) -> tuple[list[str], bool]:
    exclusion = rules.get("exclude", {})
    ignored = (any(path.startswith(v) for v in exclusion.get("prefixes", []))
               or any(v in PurePosixPath(path).parts for v in exclusion.get("components", []))
               or any(path.endswith(v) for v in exclusion.get("suffixes", [])))
    if ignored:
        return [], True
    categories = set()
    for rule in rules.get("rules", []):
        if (("prefix" in rule and path.startswith(rule["prefix"]))
                or ("path" in rule and path == rule["path"])
                or ("contains" in rule and rule["contains"] in path)):
            categories.update(rule["categories"])
    return sorted(categories), False


def _entrypoint(path: str) -> str | None:
    if path.startswith("rust/src/server/"):
        return "native"
    if path.startswith("vllm/entrypoints/"):
        if "grpc" in path:
            return "grpc"
        return "http"
    return None


def _literal_routes(raw: bytes, path: str) -> list[dict]:
    """문자열 decorator만 관측한다. 동적 등록·include_router·인증 의미는 추정하지 않는다."""
    if not path.endswith(".py"):
        return []
    try:
        tree = ast.parse(raw)
    except (SyntaxError, ValueError, UnicodeError):
        return []
    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in node.decorator_list:
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.args
                    and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                continue
            method = call.func.attr.upper()
            methods = [method] if method in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"} else []
            if method in {"ROUTE", "API_ROUTE"}:
                for kw in call.keywords:
                    if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                        methods = [v.value.upper() for v in kw.value.elts
                                   if isinstance(v, ast.Constant) and isinstance(v.value, str)]
            for verb in methods:
                rows.append({"method": verb, "path": call.args[0].value, "source_path": path,
                             "line": call.lineno, "registration_only": True})
    return sorted(rows, key=lambda row: (row["path"], row["method"], row["line"]))


def extract_git_change(repo_path: Path, baseline: str, candidate: str, *, rules_path: Path | None = None,
                       baseline_env=None, candidate_env=None, pack: Pack | None = None) -> ExtractedChange:
    repo = Path(repo_path)
    before_commit, after_commit = resolve_commit(repo, baseline), resolve_commit(repo, candidate)
    rules_bytes = Path(rules_path or RULES_PATH).read_bytes()
    rules = yaml.safe_load(rules_bytes)
    rows = changed_paths(repo, before_commit, after_commit)
    requests = [(commit, row[key]) for row in rows
                for commit, key in ((before_commit, "before_path"), (after_commit, "after_path")) if row[key]]
    # 제외 경로도 근거의 전체 변경 목록·바이트 해시에 포함한다.
    blobs = read_blobs(repo, requests)
    category_checks = (pack.change_selection or {}).get("engine_categories", {}) if pack else {}
    classified = []
    excluded = []
    entrypoints = []
    category_files: dict[str, set[str]] = {}
    for index, row in enumerate(rows, 1):
        path_classifications = []
        for key, side in (("before_path", "baseline"), ("after_path", "candidate")):
            if row[key] is None:
                continue
            values, ignored_path = classify_path(row[key], rules)
            path_classifications.append(dict(side=side, path=row[key], categories=values,
                excluded=ignored_path, unmapped=not ignored_path and not values))
        # A known side of a rename must never conceal the other, unknown side.
        categories = sorted({value for path in path_classifications for value in path["categories"]})
        ignored = all(path["excluded"] for path in path_classifications)
        unmapped = any(path["unmapped"] for path in path_classifications)
        record = dict(row, categories=categories, excluded=ignored, unmapped=unmapped,
                      path_classifications=path_classifications)
        for commit, key, sha_key in ((before_commit, "before_path", "before_sha256"),
                                     (after_commit, "after_path", "after_sha256")):
            record[sha_key] = sha256_hex(blobs[(commit, row[key])]) if row[key] else None
        if ignored:
            excluded.append(record)
            continue
        endpoints = []
        for commit, key, side in ((before_commit, "before_path", "baseline"),
                                  (after_commit, "after_path", "candidate")):
            path = row[key]
            kind = _entrypoint(path) if path else None
            if kind and not next(p["excluded"] for p in path_classifications if p["side"] == side):
                metadata = {"source_path": path, "kind": kind, "side": side,
                            "routes": [dict(route, source_side=side) for route in _literal_routes(blobs[(commit, path)], path)],
                            "limitation": "Static registration only; runtime use and handler reach are unverified."}
                endpoints.append(metadata)
                entrypoints.append(metadata)
        for path in path_classifications:
            if not path["excluded"]:
                for category in path["categories"] or ["OTHER"]:
                    category_files.setdefault(category, set()).add(path["path"])
        classified.append((index, record, endpoints))
    evidence_data = {"schema": "guard.engine-source-diff/2", "baseline": {"version": baseline, "commit": before_commit},
                     "candidate": {"version": candidate, "commit": after_commit},
                     "rules_sha256": sha256_hex(rules_bytes), "changes": [r for _, r, _ in classified],
                     "excluded": excluded}
    artifact_bytes = canonical_json(evidence_data)
    artifact_sha = sha256_hex(artifact_bytes)
    facts = []
    for index, record, endpoints in classified:
        categories = [*record["categories"], *(["OTHER"] if record["unmapped"] else [])]
        for category in categories:
            matching = [p for p in record["path_classifications"] if not p["excluded"]
                        and (p["unmapped"] if category == "OTHER" else category in p["categories"])]
            target = matching[-1]["path"]
            matching_paths = {p["path"] for p in matching}
            category_endpoints = [endpoint for endpoint in endpoints if endpoint["source_path"] in matching_paths]
            facts.append(ChangeFact(id=f"source-{index:05d}-{category.lower()}", category=category,
                target=target,
                before={"path": record["before_path"], "sha256": record["before_sha256"]} if record["before_path"] else None,
                after={"path": record["after_path"], "sha256": record["after_sha256"]} if record["after_path"] else None,
                certainty="UNKNOWN" if category == "OTHER" else "PARTIAL", unmapped=category == "OTHER",
                evidence=[ChangeEvidence(path="engine-source-diff.json", sha256=artifact_sha)],
                related_checks=list(category_checks.get(category, [])),
                details={"status": record["status"], "entrypoint": bool(category_endpoints), "entrypoints": category_endpoints,
                         "entrypoint_kind": category_endpoints[-1]["kind"] if category_endpoints else None,
                         "source_path": target, "path_classifications": record["path_classifications"],
                         "routes": [route for endpoint in category_endpoints for route in endpoint["routes"]]}))
    gap_paths = sorted(category_files.get("OUTPUT_PARSING", set())) if not category_checks.get("OUTPUT_PARSING") else []
    all_records = [record for _, record, _ in classified] + excluded
    # Normal modifications have two sides at one path; rename has two paths.
    expanded = [p for record in all_records for p in
                {p["path"]: p for p in record["path_classifications"]}.values()]
    path_counts = dict(changed_paths=len(expanded), excluded=sum(p["excluded"] for p in expanded),
                       non_excluded=sum(not p["excluded"] for p in expanded),
                       recognized=sum(not p["excluded"] and not p["unmapped"] for p in expanded),
                       unmapped=sum(p["unmapped"] for p in expanded))
    analysis = {"rules_sha256": sha256_hex(rules_bytes), "counts": {"changed_paths": len(rows),
                "excluded": len(excluded), "classified": len(classified),
                "non_excluded": len(classified),
                "recognized": sum(not r["unmapped"] for _, r, _ in classified),
                "partially_recognized": sum(r["unmapped"] and bool(r["categories"]) for _, r, _ in classified),
                "unmapped": sum(r["unmapped"] for _, r, _ in classified)},
                "path_counts": path_counts,
                "category_files": {key: sorted(value) for key, value in sorted(category_files.items())},
                "excluded": [r["after_path"] or r["before_path"] for r in excluded],
                "unmapped_files": sorted({p["path"] for p in expanded if p["unmapped"]}),
                "checklist_gap_candidates": gap_paths, "entrypoints": entrypoints,
                "source_image_relation": "UNVERIFIED", "limitations": ["Path rules do not prove behavior or dynamic reach.",
                "Image digest and version alone do not prove the analyzed source is present in the image."]}
    change = Change(id=new_id("CHG"), data_origin=DataOrigin.MEASURED,
        baseline=VersionPoint(version=baseline.removeprefix("v"), source_commit=before_commit,
            image_digest=baseline_env.engine.image_digest if baseline_env else None),
        candidate=VersionPoint(version=candidate.removeprefix("v"), source_commit=after_commit,
            image_digest=candidate_env.engine.image_digest if candidate_env else None),
        extractor=Extractor(name="git_diff", version="0.2.0"), facts=facts, analysis=analysis).seal()
    return ExtractedChange(change, artifact_bytes)
