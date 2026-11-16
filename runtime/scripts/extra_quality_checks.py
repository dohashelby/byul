#!/usr/bin/env python3
"""BYUL runtime 추가 품질 점검 두 가지 (표준 라이브러리만, 검토용).

quality_metrics.py 가 "겉모양"(함수 길이 등)을 본다면, 이 도구는 두 가지 구조 문제를 센다.

  1. 의존 방향 위반: (a) 핵심 코드가 개발용 도구(devtools)나 업데이트 연동(bridge_update)을 가져오는 곳,
     (b) 아래층이 위층을 가져오는 곳(common < config < policy·target·observation·enforcement < gateway < cli·operations).
  2. 예외를 삼키는 곳: except 블록이 pass / continue / 상수 반환만 하고 넘어가는 곳 (오류를 조용히 무시).

목표값이 아니라 **지금보다 나빠지지 않게** 막는 용도다(quality_metrics.py 와 같은 방식).
"예외를 삼키는 곳"은 정당한 경우도 있어서(예: 지울 파일이 없으면 무시) 0 을 요구하지 않는다.
다만 `except Exception` 처럼 넓게 잡고 삼키는 곳은 오류를 감추기 쉬워 따로 센다.

사용:
  python extra_quality_checks.py --root runtime [--map runtime/scripts/module_map.json]
  python extra_quality_checks.py --root runtime --map ... --write baseline.json
  python extra_quality_checks.py --root runtime --map ... --check baseline.json   # 나빠지면 종료 코드 1

--map 을 주면 이동 전 이름(serving_runtime.mvp.x)을 새 이름으로 바꿔서 센다. 그래서 이동 전후 숫자가 같아야 한다.
측정 대상: <root>/src/**/*.py (WP1 의 옛 경로 껍데기는 제외). tests 와 tools 는 제외한다.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

PACKAGE = "serving_runtime"
LEGACY_SHIM_MARKERS = ('"""Legacy import path.', '"""Legacy package.')

# 의존 규칙: 가져오면 안 되는 대상 층 -> 그것을 가져와서는 안 되는 쪽인지 판정하는 함수.
# devtools(개발·시험용)는 devtools 밖 어디서도 가져오면 안 되고,
# bridge_update(업데이트 연동)는 핵심 층(gateway/policy/target/observation/config)에서 가져오면 안 된다.
CORE_LAYERS = {"gateway", "policy", "target", "observation", "config"}
RULES = {
    "devtools": lambda src: src != "devtools",
    "bridge_update": lambda src: src in CORE_LAYERS,
}


# 층 순서: 숫자가 작을수록 아래층. 아래층이 위층을 가져오면 위반(upward)이다.
RANK = {
    "common": 0,
    "config": 1,
    "policy": 2,
    "target": 2,
    "observation": 2,
    "enforcement": 2,
    "gateway": 3,
    "operations": 4,
    "cli": 4,
    "(top)": 4,
}


def is_legacy_shim(path: Path) -> bool:
    with path.open(encoding="utf-8") as handle:
        return handle.readline().startswith(LEGACY_SHIM_MARKERS)


def source_files(root: Path):
    base = root / "src"
    return [p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts and not is_legacy_shim(p)]


def module_info(path: Path, root: Path):
    parts = list(path.relative_to(root / "src").with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


def map_fqn(fqn: str, modules: dict, attrs: dict) -> str:
    best = None
    for table in (attrs, modules):
        for old, new in table.items():
            if fqn == old or fqn.startswith(old + "."):
                if best is None or len(old) > len(best[0]):
                    best = (old, new)
    return fqn if best is None else best[1] + fqn[len(best[0]) :]


def resolve_from(name: str, is_pkg: bool, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    pkg = name.split(".") if is_pkg else name.split(".")[:-1]
    base = pkg[: len(pkg) - (node.level - 1)]
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


def layer_of(fqn: str) -> str | None:
    parts = fqn.split(".")
    if parts[0] != PACKAGE:
        return None
    return parts[1] if len(parts) > 1 else "(top)"


def imported_names(tree: ast.AST, name: str, is_pkg: bool):
    """파일 안 모든 import(함수 안쪽 포함)가 가리키는 전체 이름을 하나씩 낸다."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            base = resolve_from(name, is_pkg, node)
            for alias in node.names:
                yield (base if alias.name == "*" else base + "." + alias.name), node.lineno


def is_broad(handler_type) -> bool:
    broad = {"Exception", "BaseException"}
    if handler_type is None:
        return True
    if isinstance(handler_type, ast.Name):
        return handler_type.id in broad
    if isinstance(handler_type, ast.Tuple):
        return any(isinstance(e, ast.Name) and e.id in broad for e in handler_type.elts)
    return False


def is_trivial(stmt: ast.stmt) -> bool:
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return True  # `...` 또는 문자열
    if isinstance(stmt, ast.Return):
        return stmt.value is None or isinstance(stmt.value, ast.Constant)
    return False


def measure(root: Path, modules: dict, attrs: dict) -> dict:
    violations = {rule: set() for rule in RULES}
    detail = {rule: [] for rule in RULES}
    violations["upward"] = set()
    detail["upward"] = []
    swallowed = broad_swallowed = 0
    swallow_where: list[str] = []
    for path in source_files(root):
        rel = str(path.relative_to(root))
        tree = ast.parse(path.read_text(encoding="utf-8"))
        name, is_pkg = module_info(path, root)
        src_layer = layer_of(map_fqn(name, modules, attrs)) or "(outside)"
        seen = set()
        for target, lineno in imported_names(tree, name, is_pkg):
            mapped = map_fqn(target, modules, attrs)
            dst_layer = layer_of(mapped)
            if dst_layer in RULES and RULES[dst_layer](src_layer) and (rel, mapped) not in seen:
                seen.add((rel, mapped))
                violations[dst_layer].add(rel)
                detail[dst_layer].append(f"{rel}:{lineno} ({src_layer}) -> {mapped}")
            elif (
                src_layer in RANK
                and dst_layer in RANK
                and src_layer != dst_layer
                and RANK[dst_layer] > RANK[src_layer]
                and (rel, mapped) not in seen
            ):
                seen.add((rel, mapped))
                violations["upward"].add(rel)
                detail["upward"].append(f"{rel}:{lineno} ({src_layer}) -> {mapped}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if all(is_trivial(stmt) for stmt in node.body):
                swallowed += 1
                broad = is_broad(node.type)
                broad_swallowed += broad
                swallow_where.append(f"{rel}:{node.lineno}{' [넓게 잡음]' if broad else ''}")
    return {
        "layer_violation_files_devtools": len(violations["devtools"]),
        "layer_violation_files_bridge_update": len(violations["bridge_update"]),
        "layer_violation_files_upward": len(violations["upward"]),
        "swallowed_except_handlers": swallowed,
        "swallowed_broad_except_handlers": broad_swallowed,
        "_details": {"imports": detail, "swallowed": swallow_where},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="runtime", type=Path)
    parser.add_argument("--map", type=Path, help="module_map.json (이동 전 트리를 새 이름으로 바꿔 세기)")
    parser.add_argument("--write", type=Path)
    parser.add_argument("--check", type=Path)
    parser.add_argument("--details", action="store_true", help="위반 위치 전체 출력")
    args = parser.parse_args()
    if not (args.root / "src").is_dir():
        print(f"{args.root}/src 가 없습니다", file=sys.stderr)
        return 2
    modules, attrs = {}, {}
    if args.map:
        data = json.loads(args.map.read_text(encoding="utf-8"))
        modules, attrs = data["modules"], data.get("attrs", {})

    now = measure(args.root, modules, attrs)
    for key, value in now.items():
        if key != "_details":
            print(f"{key:40s} {value}")
    if args.details:
        for rule, rows in now["_details"]["imports"].items():
            print(f"\n[{rule} 를 가져오는 곳]")
            print("\n".join("  " + row for row in rows) or "  (없음)")
        print("\n[예외를 삼키는 곳]")
        print("\n".join("  " + row for row in now["_details"]["swallowed"]) or "  (없음)")
    if args.write:
        args.write.write_text(
            json.dumps({k: v for k, v in now.items() if k != "_details"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\n기준선 저장: {args.write}")
    if args.check:
        base = json.loads(args.check.read_text(encoding="utf-8"))
        worse = {k: (base[k], now[k]) for k in base if k in now and now[k] > base[k]}
        if worse:
            print("\n기준선보다 나빠진 항목:")
            for k, (before, after) in sorted(worse.items()):
                print(f"  {k}: {before} -> {after}")
            return 1
        print("\n기준선 대비 악화 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
