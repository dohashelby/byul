#!/usr/bin/env python3
"""BYUL runtime 코드 품질 지표 측정·ratchet 도구 (검토용, 표준 라이브러리만 사용).

사용:
  python quality_metrics.py --root runtime                    # 지표 출력
  python quality_metrics.py --root runtime --write baseline.json   # 기준선 저장
  python quality_metrics.py --root runtime --check baseline.json   # 기준선보다 나빠지면 종료 코드 1

"지금보다 나빠지지 않게" 막는 방식이다. 목표값이 아니라 현재값을 기준으로 삼는다.
측정 대상: <root>/src/**/*.py 와 <root>/tools/**/*.py (제품 코드). tests 는 제외한다.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from pathlib import Path

LONG_FN = 80  # 긴 함수 기준(줄)
VERY_LONG_FN = 150  # 매우 긴 함수 기준(줄)
MANY_ARGS = 8  # 인자 과다 기준
BIG_FILE = 800  # 큰 파일 기준(줄)
# 제품 코드(src, tools)에 있으면 안 되는 랩 값·개인 경로 패턴
FORBIDDEN = {
    "personal_path": r"/Users/[A-Za-z0-9_.-]+",
    "lab_context_kind": r"kind-runtime-lab",
    "lab_context_aws": r"byul-aws-lab",
    "lab_namespace": r"serving-lab",
    "lab_model_name": r"test-model",
}
# 이름에 남아 있으면 안 되는 이력·단계 표지
BAD_NAME_PARTS = ("mvp", "selected_", "aws_lab", "s3b", "aws20")
# 같은 일을 하는 복붙 구현을 찾는 단서
ATOMIC_WRITE_HINTS = ("os.replace(", ".replace(tmp", "renameat2", "os.rename(")


LEGACY_SHIM_MARKERS = ('"""Legacy import path.', '"""Legacy package.')


def is_legacy_shim(path: Path) -> bool:
    """WP1 이 만든 옛 import 경로 껍데기(로직 없음)는 지표에서 뺀다."""
    with path.open(encoding="utf-8") as handle:
        return handle.readline().startswith(LEGACY_SHIM_MARKERS)


def py_files(root: Path):
    for sub in ("src", "tools"):
        base = root / sub
        if base.is_dir():
            yield from (p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts and not is_legacy_shim(p))


def measure(root: Path) -> dict:
    long_fn, very_long_fn, many_args, big_files = [], [], [], []
    private_imports, dual_imports, bad_names = [], [], []
    forbidden = {k: [] for k in FORBIDDEN}
    atomic = []
    cli_args = 0
    total_lines = 0
    for path in py_files(root):
        rel = str(path.relative_to(root))
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        total_lines += len(lines)
        if len(lines) > BIG_FILE:
            big_files.append((rel, len(lines)))
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                size = node.end_lineno - node.lineno + 1
                a = node.args
                nargs = len(a.args) + len(a.kwonlyargs) + len(a.posonlyargs)
                ident = f"{rel}:{node.name}"
                if size > LONG_FN:
                    long_fn.append((ident, size))
                if size > VERY_LONG_FN:
                    very_long_fn.append((ident, size))
                if nargs > MANY_ARGS:
                    many_args.append((ident, nargs))
            if isinstance(node, ast.ImportFrom) and node.module != "__future__":
                if any(n.name.startswith("_") and not n.name.startswith("__") for n in node.names):
                    private_imports.append(rel)
            if isinstance(node, ast.Try):
                for h in node.handlers:
                    t = h.type
                    if isinstance(t, ast.Name) and t.id == "ModuleNotFoundError":
                        dual_imports.append(rel)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
                cli_args += 1
        for key, pattern in FORBIDDEN.items():
            if re.search(pattern, text):
                forbidden[key].append(rel)
        if any(h in text for h in ATOMIC_WRITE_HINTS):
            atomic.append(rel)
        stem = path.stem
        if any(part in stem or part in "/".join(path.parts) for part in BAD_NAME_PARTS):
            bad_names.append(rel)
    return {
        "files": len(list(py_files(root))),
        "lines": total_lines,
        "functions_over_80_lines": len(long_fn),
        "functions_over_150_lines": len(very_long_fn),
        "functions_over_8_args": len(many_args),
        "files_over_800_lines": len(big_files),
        "private_name_imports": len(private_imports),
        "dual_import_try_except": len(set(dual_imports)),
        "cli_add_argument_calls": cli_args,
        "files_with_atomic_write_hints": len(set(atomic)),
        "bad_name_files": len(set(bad_names)),
        **{f"forbidden_{k}_files": len(set(v)) for k, v in forbidden.items()},
        "_details": {
            "very_long_functions": sorted(very_long_fn, key=lambda x: -x[1])[:15],
            "big_files": sorted(big_files, key=lambda x: -x[1])[:10],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="runtime", help="runtime 디렉터리 (src, tools 를 포함)")
    ap.add_argument("--write", help="현재 값을 기준선 JSON 으로 저장")
    ap.add_argument("--check", help="기준선 JSON 과 비교해 나빠진 항목이 있으면 종료 코드 1")
    args = ap.parse_args()
    root = Path(args.root)
    if not (root / "src").is_dir():
        print(f"{root}/src 가 없습니다", file=sys.stderr)
        return 2
    now = measure(root)
    for key, value in now.items():
        if key != "_details":
            print(f"{key:40s} {value}")
    print("\n[매우 긴 함수 상위]")
    for ident, size in now["_details"]["very_long_functions"]:
        print(f"  {size:5d}  {ident}")
    print("\n[큰 파일 상위]")
    for rel, size in now["_details"]["big_files"]:
        print(f"  {size:5d}  {rel}")
    if args.write:
        Path(args.write).write_text(
            json.dumps({k: v for k, v in now.items() if k != "_details"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"\n기준선 저장: {args.write}")
    if args.check:
        base = json.loads(Path(args.check).read_text(encoding="utf-8"))
        worse = {k: (base[k], now[k]) for k in base if k in now and k not in ("files", "lines") and now[k] > base[k]}
        if worse:
            print("\n기준선보다 나빠진 항목:")
            for k, (b, n) in sorted(worse.items()):
                print(f"  {k}: {b} -> {n}")
            return 1
        print("\n기준선 대비 악화 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
