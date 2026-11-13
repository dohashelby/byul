#!/usr/bin/env python3
"""포맷 전용 커밋 검증기 (표준 라이브러리만).

두 디렉터리(이전/이후)의 .py 파일을 AST로 비교해서 **모양만 바뀌었는지** 확인한다.
공백·따옴표·줄바꿈·괄호는 AST에 나타나지 않으므로 같게 나온다.
docstring 은 들여쓰기·끝 공백 차이를 무시하고 비교한다.
import 순서가 바뀌거나 코드가 추가/삭제되면 AST 가 달라져서 실패한다.

사용:
  python verify_format_only.py OLD_DIR NEW_DIR
  # 예: git worktree 또는 `git archive <기준커밋> runtime | tar -x -C /tmp/old` 로 OLD_DIR 준비

종료 코드: 0 = 모든 .py 가 AST 동일, 1 = 차이 있음, 2 = 사용 오류
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}


def py_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p
        for p in root.rglob("*.py")
        if not (SKIP_DIRS & set(p.parts))
    }


def normalize(tree: ast.AST) -> ast.AST:
    """docstring 의 공백 차이만 정규화한다."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body[0].value.value = inspect.cleandoc(body[0].value.value)
    return tree


def dump(path: Path) -> str:
    return ast.dump(normalize(ast.parse(path.read_text(encoding="utf-8"))), include_attributes=False)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    old_root, new_root = Path(sys.argv[1]), Path(sys.argv[2])
    if not old_root.is_dir() or not new_root.is_dir():
        print("두 인자는 모두 디렉터리여야 합니다", file=sys.stderr)
        return 2
    old, new = py_files(old_root), py_files(new_root)
    problems = []
    for name in sorted(old.keys() - new.keys()):
        problems.append(f"삭제됨: {name}")
    for name in sorted(new.keys() - old.keys()):
        problems.append(f"추가됨: {name}")
    changed_files = 0
    for name in sorted(old.keys() & new.keys()):
        if old[name].read_bytes() != new[name].read_bytes():
            changed_files += 1
        try:
            same = dump(old[name]) == dump(new[name])
        except SyntaxError as error:
            problems.append(f"구문 오류: {name}: {error}")
            continue
        if not same:
            problems.append(f"AST 다름: {name}")
    print(f"비교한 .py 파일 {len(old.keys() & new.keys())}개, 내용이 바뀐 파일 {changed_files}개")
    if problems:
        print(f"문제 {len(problems)}건:")
        for line in problems[:50]:
            print("  " + line)
        return 1
    print("AST 동일: 모양만 바뀌었습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
