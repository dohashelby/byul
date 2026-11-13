#!/usr/bin/env python3
"""WP1 이동 도구 (표준 라이브러리만). module_map.json 대로 파일을 옮기고 import·문자열 경로를 고치고 껍데기를 만든다.

사용 (저장소 루트에서):
  python runtime/scripts/apply_module_map.py --map runtime/scripts/module_map.json --runtime runtime --dry-run
  python runtime/scripts/apply_module_map.py --map runtime/scripts/module_map.json --runtime runtime

하는 일
  1. src/serving_runtime 아래 파일을 `git mv` 로 옮긴다(내용은 아직 그대로).
  2. runtime/ 아래 모든 .py (src, tools, tests, deploy)의 import 문을 새 경로로 다시 쓴다.
     - 이동한 파일 안의 상대 import 는 절대 import 로 바꾼다(폴더가 바뀌므로).
     - `from serving_runtime.mvp import native_receipts` 처럼 이름이 하위 모듈이면 새 위치로 쪼갠다.
  3. 문자열·주석의 `serving_runtime.mvp.<이름>` 과 `serving_runtime/mvp/<이름>.py` 를 새 경로로 바꾼다.
  4. 옛 경로를 위한 **껍데기 모듈**을 옛 위치에 만든다(내용: 새 모듈을 가리킬 뿐, 로직 없음).
  5. 사람이 판단해야 하는 곳(`import serving_runtime.mvp.x` 형태 등)은 고치지 않고 보고한다.

이 도구는 문자열 `project-context/` 아래(과거 기록)는 건드리지 않는다.
결과가 맞는지는 verify_move_only.py 가 따로 증명한다.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".venv", "project-context", "build", "dist"}
TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".toml", ".service", ".sh", ".txt", ".cfg", ".ini"}
SHIM_DOC = (
    '"""Legacy import path. Points at the moved module; contains no logic. '
    'Delete with src/serving_runtime/mvp/ (see LEGACY_SHIMS.md)."""'
)


def load_map(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["modules"], data.get("attrs", {}), data


def map_fqn(fqn: str, modules: dict[str, str], attrs: dict[str, str]) -> str:
    """가장 긴 접두어 기준으로 이름을 새 경로로 바꾼다."""
    best = None
    for table in (attrs, modules):
        for old, new in table.items():
            if fqn == old or fqn.startswith(old + "."):
                if best is None or len(old) > len(best[0]):
                    best = (old, new)
    if best is None:
        return fqn
    return best[1] + fqn[len(best[0]) :]


def module_name(runtime: Path, path: Path) -> tuple[str, bool] | None:
    """src/serving_runtime 아래 파일의 (모듈 이름, 패키지 여부)."""
    try:
        rel = path.relative_to(runtime / "src")
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


def resolve_from(cur: tuple[str, bool] | None, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module or ""
    if cur is None:
        return None
    name, is_pkg = cur
    pkg = name.split(".") if is_pkg else name.split(".")[:-1]
    cut = len(pkg) - (node.level - 1)
    if cut < 1:
        return None
    base = pkg[:cut]
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


def rewrite_source(
    source: str, cur_old: tuple[str, bool] | None, moved: bool, modules, attrs, report: list, label: str
):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    # 줄 시작 바이트 오프셋 → 문자 오프셋 변환 없이 줄 단위로 처리한다(들여쓰기·한 줄 문장만 지원).
    edits: list[tuple[int, int, str]] = []  # (시작줄, 끝줄, 새 텍스트) 1-based inclusive
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = resolve_from(cur_old, node)
            if base is None:
                report.append((label, node.lineno, "상대 import 해석 실패"))
                continue
            relative = node.level > 0
            groups: list[tuple[str, list[ast.alias]]] = []
            changed = relative and moved
            for alias in node.names:
                if alias.name == "*":
                    new_mod, new_name = map_fqn(base, modules, attrs), "*"
                else:
                    new_full = map_fqn(base + "." + alias.name, modules, attrs)
                    new_mod, _, new_name = new_full.rpartition(".")
                if alias.name == "*":
                    changed = changed or new_mod != base
                else:
                    changed = changed or (new_mod + "." + new_name) != (base + "." + alias.name)
                if groups and groups[-1][0] == new_mod:
                    groups[-1][1].append((new_name, alias.asname))
                else:
                    groups.append((new_mod, [(new_name, alias.asname)]))
            if not changed:
                continue
            first = lines[node.lineno - 1]
            indent = first[: len(first) - len(first.lstrip())]
            if first.strip() != "" and not first[len(indent) :].startswith(("from ", "from\t")):
                report.append((label, node.lineno, "한 줄에 다른 문장이 섞인 import (수동 처리)"))
                continue
            text = ""
            for new_mod, aliases in groups:
                names = ", ".join(n if a is None else f"{n} as {a}" for n, a in aliases)
                text += f"{indent}from {new_mod} import {names}\n"
            edits.append((node.lineno, node.end_lineno, text))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                new = map_fqn(alias.name, modules, attrs)
                if new != alias.name:
                    if alias.asname is None:
                        report.append(
                            (
                                label,
                                node.lineno,
                                f"`import {alias.name}` (as 없음) — 이후 속성 접근 확인 필요, 수동 처리",
                            )
                        )
                    else:
                        first = lines[node.lineno - 1]
                        indent = first[: len(first) - len(first.lstrip())]
                        names = ", ".join(
                            (map_fqn(a.name, modules, attrs) if a.asname else a.name)
                            + (f" as {a.asname}" if a.asname else "")
                            for a in node.names
                        )
                        edits.append((node.lineno, node.end_lineno, f"{indent}import {names}\n"))
                        break
    # 뒤에서부터 적용
    for start, end, text in sorted(edits, key=lambda e: -e[0]):
        lines[start - 1 : end] = [text]
    return "".join(lines), len(edits)


def build_text_patterns(modules):
    dotted = sorted((k for k in modules if k.startswith("serving_runtime.")), key=len, reverse=True)
    dotted_re = re.compile(r"(?<![\w.])(" + "|".join(re.escape(k) for k in dotted) + r")(?![\w])")
    path_re = re.compile(r"serving_runtime/(?:mvp/)?(\w+)\.py")
    return dotted_re, path_re


def rewrite_text(text: str, modules, dotted_re, path_re) -> tuple[str, int]:
    count = 0

    def dotted_sub(m):
        nonlocal count
        count += 1
        return modules[m.group(1)]

    text = dotted_re.sub(dotted_sub, text)
    path_map = {}
    for old, new in modules.items():
        o_parts, n_parts = old.split("."), new.split(".")
        path_map["/".join(o_parts[:-1] + [o_parts[-1]]) + ".py"] = "/".join(n_parts) + ".py"

    def path_sub(m):
        nonlocal count
        key = m.group(0)
        if key in path_map:
            count += 1
            return path_map[key]
        return key

    text = path_re.sub(path_sub, text)
    return text, count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path, help="runtime/ 폴더")
    parser.add_argument("--dry-run", action="store_true", help="아무것도 바꾸지 않고 계획만 출력")
    parser.add_argument("--no-git", action="store_true", help="git mv 대신 일반 이동 (시험용)")
    args = parser.parse_args()

    modules, attrs, data = load_map(args.map)
    runtime: Path = args.runtime.resolve()
    src = runtime / "src"

    def old_path(mod: str) -> Path:
        return src / Path(*mod.split(".")).with_suffix(".py")

    # 0. 점검
    for old, new in modules.items():
        if not old_path(old).exists():
            print(f"오류: 이동표의 옛 모듈 파일이 없다: {old}", file=sys.stderr)
            return 2
        if old_path(new).exists():
            print(f"오류: 새 위치에 이미 파일이 있다: {new}", file=sys.stderr)
            return 2
    mvp_files = {p.stem for p in (src / "serving_runtime" / "mvp").glob("*.py") if p.stem != "__init__"}
    mapped = {k.rsplit(".", 1)[1] for k in modules if k.startswith("serving_runtime.mvp.")}
    if mvp_files != mapped:
        print(
            f"오류: mvp 파일과 이동표가 다르다. 표에 없음: {sorted(mvp_files - mapped)} / 파일 없음: {sorted(mapped - mvp_files)}",
            file=sys.stderr,
        )
        return 2

    # 1. 파일 목록과 옛 모듈 이름
    old_names = {}  # 파일(옛 위치) -> (모듈, 패키지 여부)
    for path in src.rglob("*.py"):
        if SKIP_PARTS & set(path.relative_to(runtime).parts):
            continue
        info = module_name(runtime, path)
        if info:
            old_names[path] = info

    all_py = [p for p in runtime.rglob("*.py") if not (SKIP_PARTS & set(p.relative_to(runtime).parts))]
    report: list = []
    plans = {}
    for path in all_py:
        cur = old_names.get(path)
        moved = bool(cur and cur[0] in modules)
        new_text, n = rewrite_source(
            path.read_text(encoding="utf-8"), cur, moved, modules, attrs, report, str(path.relative_to(runtime))
        )
        plans[path] = (new_text, n, moved)

    dotted_re, path_re = build_text_patterns(modules)
    print(f"이동할 모듈 {len(modules)}개, 검사한 .py {len(all_py)}개")
    changed_py = sum(1 for _, (_, n, moved) in plans.items() if n or moved)
    print(f"import 를 고칠 .py: {changed_py}개")

    if args.dry_run:
        for label, line, why in report:
            print(f"  수동 확인: {label}:{line}: {why}")
        print("(dry-run: 변경 없음)")
        return 0

    # 2. git mv
    def run(cmd):
        subprocess.run(cmd, check=True, cwd=runtime)

    for old, new in modules.items():
        dst = old_path(new)
        dst.parent.mkdir(parents=True, exist_ok=True)
        init = dst.parent / "__init__.py"
        if not init.exists():
            init.write_text('"""Package created by WP1 module move."""\n', encoding="utf-8")
        if args.no_git:
            old_path(old).rename(dst)
        else:
            run(["git", "mv", str(old_path(old)), str(dst)])

    # 3. 내용 다시 쓰기 (옛 경로 기준으로 계획했으므로 새 위치로 옮겨 쓴다)
    text_changes = 0
    for path, (new_text, n, moved) in plans.items():
        target = path
        cur = old_names.get(path)
        if cur and cur[0] in modules:
            target = old_path(modules[cur[0]])
        new_text, tc = rewrite_text(new_text, modules, dotted_re, path_re)
        text_changes += tc
        target.write_text(new_text, encoding="utf-8")

    # 4. 껍데기 (옛 위치)
    pkg_init = src / "serving_runtime" / "mvp" / "__init__.py"
    pkg_init.write_text(
        '"""Legacy package. Delete with src/serving_runtime/mvp/ (see LEGACY_SHIMS.md)."""\n\n'
        "from serving_runtime.config.config import MVPConfig\n\n"
        '__all__ = ["MVPConfig"]\n',
        encoding="utf-8",
    )
    for old, new in modules.items():
        shim = old_path(old)
        shim.write_text(
            f"{SHIM_DOC}\n\nimport sys\nfrom importlib import import_module\n\n"
            f'sys.modules[__name__] = import_module("{new}")\n',
            encoding="utf-8",
        )
    legacy = src / "serving_runtime" / "LEGACY_SHIMS.md"
    legacy.write_text(
        "# 옛 import 경로 껍데기 (임시)\n\n"
        "platform·update 시험이 옛 경로(`serving_runtime.mvp.*`, `serving_runtime.bundle_tools`, `serving_runtime.file_info`)를\n"
        "쓰는 동안만 둔다. 각 파일은 새 모듈을 가리킬 뿐 로직이 없다.\n\n"
        "삭제 방법: `src/serving_runtime/mvp/` 폴더, `src/serving_runtime/bundle_tools.py`,\n"
        "`src/serving_runtime/file_info.py`, `tests/test_legacy_import_shim.py`, 이 파일을 지운다.\n"
        "그 전에 platform·update 시험의 import 줄을 새 경로로 바꾼다. runtime 코드는 껍데기를 쓰지 않는다.\n",
        encoding="utf-8",
    )
    # 3b. .py 가 아닌 문서·배포 파일의 옛 경로 (project-context/ 는 과거 기록이라 제외)
    changed_text_files = []
    for path in runtime.rglob("*"):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        rel_parts = path.relative_to(runtime).parts
        if path.resolve() == args.map.resolve():
            continue
        if SKIP_PARTS & set(rel_parts) or any(part.endswith(".egg-info") for part in rel_parts):
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        updated, tc = rewrite_text(original, modules, dotted_re, path_re)
        if tc and path.name != "LEGACY_SHIMS.md":
            path.write_text(updated, encoding="utf-8")
            text_changes += tc
            changed_text_files.append(str(path.relative_to(runtime)))
    if changed_text_files:
        print("문서·배포 파일에서 옛 경로를 고친 파일 (해시로 고정된 파일이 아닌지 find_hash_pins.py 로 확인할 것):")
        for name in changed_text_files:
            print(f"  {name}")
    print(f"문자열·경로 치환 {text_changes}건")
    for label, line, why in report:
        print(f"  수동 확인: {label}:{line}: {why}")
    print("완료. 다음: ruff format, verify_move_only.py, 시험")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
