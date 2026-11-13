#!/usr/bin/env python3
"""tools/ 폴더 이동(WP1b) 검증기 (표준 라이브러리만, 검토용).

verify_move_only.py 는 src/ 모듈 이동만 다룬다. 이 도구는 `runtime/tools/NAME.py` 몇 개를 같은 깊이의 다른 폴더
(예: `runtime/labtools/NAME.py`)로 옮긴 커밋을 검증한다.

방법
  1. 옮기는 이름 목록(--names)으로 옛 트리의 모든 텍스트에서 아래 세 가지 표기만 새 이름으로 바꾼다.
       tools.NAME  ->  NEWPKG.NAME        tools/NAME  ->  NEWDIR/NAME
       한 줄짜리 `from tools import A, B` 는 A·B 가 모두 옮긴 모듈일 때만 `from NEWPKG import A, B` 로 바꾼다.
       옮긴 모듈과 안 옮긴 모듈이 한 import 에 섞였거나 여러 줄이면 실패(코덱스가 문장을 나누고 사람이 확인).
  2. 그렇게 바꾼 옛 파일이 새 트리의 대응 파일과 같은지 비교한다.
       .py        : AST 비교 (서식·줄바꿈 무시)
       그 밖 파일 : 글자 그대로 비교
     64자리 해시는 양쪽 모두 자리표시자로 바꿔 비교하고, 해시가 달라진 파일은 따로 보고한다(find_hash_pins.py 로 확인).
  3. 새 트리에만 있는 파일은 --allow-new 로 적은 것뿐이어야 한다.
  4. 새 트리에 옮긴 모듈의 옛 표기(tools.NAME, tools/NAME)가 남아 있으면 실패. 그 밖의 의심 표기(`"tools"` 문자열)는 경고로 낸다.

사용:
  python verify_tools_move.py --names names.txt --new-dir labtools OLD_RUNTIME NEW_RUNTIME [--allow-new a,b] [--show-diff]
  names.txt: 한 줄에 모듈 이름 하나(확장자 없이). `#` 로 시작하면 주석.
종료 코드: 0 = 이동만 일어남, 1 = 차이 있음, 2 = 사용 오류
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import sys
from pathlib import Path

SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".venv", "project-context", ".pytest_cache", ".ruff_cache"}
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
OLD_DIR = "tools"


def files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*")
        if p.is_file() and not (SKIP_PARTS & set(p.relative_to(root).parts)) and not p.name.endswith(".pyc")
    }


def read(path: Path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def make_sub(names: list[str], new_dir: str):
    alt = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    dotted = re.compile(rf"(?<![A-Za-z0-9_])tools\.({alt})\b")
    slashed = re.compile(rf"(?<![A-Za-z0-9_])tools/({alt})\b")

    from_line = re.compile(r"(?m)^(\s*)from tools import ([^\n(\\]+?)\s*$")

    def from_repl(match: re.Match) -> str:
        wanted = [part.strip().split(" as ")[0].strip() for part in match.group(2).split(",")]
        if wanted and all(name in names for name in wanted):
            return f"{match.group(1)}from {new_dir} import {match.group(2)}"
        return match.group(0)

    def sub(text: str) -> str:
        text = from_line.sub(from_repl, text)
        text = dotted.sub(rf"{new_dir}.\1", text)
        return slashed.sub(rf"{new_dir}/\1", text)

    return sub


def from_tools_import_hits(tree: ast.AST, names: set[str]) -> list[str]:
    """자동 변환이 안 되는 `from tools import` 만 낸다: 섞였거나 여러 줄인 문장."""
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tools" and node.level == 0:
            imported = {a.name for a in node.names}
            moved = imported & names
            if moved and (moved != imported or node.end_lineno != node.lineno):
                hits.append(f"line {node.lineno}: from tools import {', '.join(sorted(imported))}")
    return hits


def ast_text(text: str) -> str:
    return ast.dump(ast.parse(text), include_attributes=False)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("old", type=Path)
    ap.add_argument("new", type=Path)
    ap.add_argument("--names", type=Path, required=True)
    ap.add_argument("--new-dir", required=True, help="옮겨 간 폴더 이름 (runtime 바로 아래, tools 와 같은 깊이)")
    ap.add_argument("--allow-new", default="", help="새 트리에만 있어도 되는 파일(runtime 기준 경로), 쉼표 구분")
    ap.add_argument("--show-diff", action="store_true")
    args = ap.parse_args()

    names = [
        line.strip()
        for line in args.names.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    nameset = set(names)
    new_dir = args.new_dir
    if "/" in new_dir or new_dir == OLD_DIR:
        print("--new-dir 는 tools 가 아닌 한 단계 폴더 이름이어야 합니다", file=sys.stderr)
        return 2
    old_files, new_files = files(args.old), files(args.new)
    for n in names:
        if f"{OLD_DIR}/{n}.py" not in old_files:
            print(f"옛 트리에 {OLD_DIR}/{n}.py 가 없습니다", file=sys.stderr)
            return 2

    sub = make_sub(names, new_dir)
    allow_new = {p for p in args.allow_new.split(",") if p}
    problems: list[str] = []
    hash_changed: list[str] = []
    mapped_new: set[str] = set()

    for rel, old_path in sorted(old_files.items()):
        parts = Path(rel).parts
        if len(parts) == 2 and parts[0] == OLD_DIR and parts[1].endswith(".py") and parts[1][:-3] in nameset:
            new_rel = f"{new_dir}/{parts[1]}"
        else:
            new_rel = rel
        mapped_new.add(new_rel)
        if new_rel not in new_files:
            problems.append(f"새 트리에 없음: {new_rel} (옛: {rel})")
            continue
        old_text, new_text = read(old_path), read(new_files[new_rel])
        if old_text is None or new_text is None:
            if old_path.read_bytes() != new_files[new_rel].read_bytes():
                problems.append(f"바이너리 파일이 달라짐: {new_rel}")
            continue
        expected = sub(old_text)
        if new_rel.endswith(".py"):
            try:
                old_tree = ast.parse(old_text)
                hits = from_tools_import_hits(old_tree, nameset)
                if hits:
                    problems.append(
                        f"{rel}: 옮긴 모듈과 안 옮긴 모듈이 섞였거나 여러 줄인 import {hits} (문장을 나눠서 `from {new_dir} import` 로 바꿔야 함)"
                    )
                same = ast_text(HEX64.sub("<HEX>", expected)) == ast_text(HEX64.sub("<HEX>", new_text))
            except SyntaxError as exc:
                problems.append(f"구문 오류: {new_rel}: {exc}")
                continue
        else:
            same = HEX64.sub("<HEX>", expected) == HEX64.sub("<HEX>", new_text)
        if not same:
            problems.append(f"내용이 달라짐: {new_rel}" + (f" (옛: {rel})" if rel != new_rel else ""))
            if args.show_diff:
                diff = difflib.unified_diff(
                    HEX64.sub("<HEX>", expected).splitlines(),
                    HEX64.sub("<HEX>", new_text).splitlines(),
                    "기대",
                    "실제",
                    lineterm="",
                    n=1,
                )
                problems.extend("    " + line for line in list(diff)[:40])
        elif HEX64.findall(expected) != HEX64.findall(new_text):
            hash_changed.append(new_rel)

    for rel in sorted(set(new_files) - mapped_new):
        if rel not in allow_new:
            problems.append(f"새 트리에만 있는 파일(허용 목록에 없음): {rel}")

    # 새 트리에 옛 표기가 남았는지, 의심 표기가 있는지
    stale = re.compile(
        rf"(?<![A-Za-z0-9_])tools[./]({'|'.join(sorted(map(re.escape, names), key=len, reverse=True))})\b"
    )
    warn: list[str] = []
    for rel, path in sorted(new_files.items()):
        text = read(path)
        if text is None:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if stale.search(line) or re.match(
                rf"\s*from tools import ({'|'.join(sorted(map(re.escape, names), key=len, reverse=True))})\b", line
            ):
                problems.append(f"옛 표기 남음: {rel}:{lineno}: {line.strip()[:110]}")
            elif rel.endswith(".py") and re.search(r"""["']tools["']""", line):
                warn.append(f"{rel}:{lineno}: {line.strip()[:110]}")

    print(f"옮긴 모듈 {len(names)}개 → {new_dir}/ , 옛 파일 {len(old_files)}개 / 새 파일 {len(new_files)}개 비교")
    if hash_changed:
        print("\n64자리 해시 값이 달라진 파일(find_hash_pins.py 로 고정 해시 확인 필요):")
        print("\n".join("  " + p for p in hash_changed))
    if warn:
        print('\n경고: `"tools"` 문자열이 남은 곳(옮긴 모듈과 무관한지 눈으로 확인):')
        print("\n".join("  " + w for w in warn))
    if problems:
        print(f"\n실패 {len(problems)}건:")
        print("\n".join("  " + p for p in problems))
        return 1
    print("\n통과: 지정한 모듈이 옮겨졌고, 이름·경로 표기 외의 변경은 없습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
