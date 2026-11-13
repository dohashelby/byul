#!/usr/bin/env python3
"""이동 전용 커밋 검증기 (표준 라이브러리만). apply_module_map.py 와 **따로 구현**했다.

옛 트리(이동 전)와 새 트리(이동 후)의 runtime/ 을 비교해서 **파일이 옮겨졌고 import 경로만 바뀌었는지** 확인한다.

비교 방법
  - 이동표(module_map.json)로 옛 파일이 새 어디로 갔는지 찾아, 파일마다 AST 를 비교한다.
  - import 문은 `절대 이름 하나당 한 줄` 표지로 풀어서 비교한다. 상대/절대 import, 문장 쪼개기, 줄 모양은 무시하지만
    **무엇을 어디서 가져오는지(모듈·이름·as)** 가 같아야 한다. 옛 트리의 이름은 이동표로 새 이름으로 바꿔서 비교한다.
  - 문자열·docstring 안의 옛 경로(`serving_runtime.mvp.x`, `serving_runtime/mvp/x.py`)도 새 경로로 바꿔서 비교한다.
  - 그 밖의 코드(함수 본문, 상수, 조건 등)가 다르면 실패한다.
  - 새 트리에만 있는 파일은 (a) 껍데기 모듈(양식 고정), (b) 새 폴더의 `__init__.py`(docstring 만), (c) 허용 목록 파일뿐이어야 한다.

사용: python verify_move_only.py --map module_map.json OLD_RUNTIME NEW_RUNTIME
종료 코드: 0 = 이동만 일어남, 1 = 차이 있음, 2 = 사용 오류
"""

from __future__ import annotations

import argparse
import ast
import inspect
import json
import re
import sys
from pathlib import Path

SKIP_PARTS = {"__pycache__", ".git", "node_modules", ".venv", "project-context"}


def load(map_path: Path):
    data = json.loads(map_path.read_text(encoding="utf-8"))
    return data["modules"], data.get("attrs", {}), data


def map_fqn(fqn: str, modules, attrs) -> str:
    best = None
    for table in (attrs, modules):
        for old, new in table.items():
            if fqn == old or fqn.startswith(old + "."):
                if best is None or len(old) > len(best[0]):
                    best = (old, new)
    return fqn if best is None else best[1] + fqn[len(best[0]) :]


def py_files(root: Path) -> dict[str, Path]:
    return {
        str(p.relative_to(root)): p for p in root.rglob("*.py") if not (SKIP_PARTS & set(p.relative_to(root).parts))
    }


def mod_info(rel: str):
    parts = Path(rel).with_suffix("").parts
    if not parts or parts[0] != "src":
        return None
    parts = list(parts[1:])
    is_pkg = parts[-1] == "__init__"
    if is_pkg:
        parts = parts[:-1]
    return ".".join(parts), is_pkg


def resolve_base(cur, node: ast.ImportFrom):
    if node.level == 0:
        return node.module or ""
    if cur is None:
        return "?" * node.level + (node.module or "")
    name, is_pkg = cur
    pkg = name.split(".") if is_pkg else name.split(".")[:-1]
    base = pkg[: len(pkg) - (node.level - 1)]
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


class Flatten(ast.NodeTransformer):
    """import 문을 표지 문장으로 풀고, 문자열·속성 경로를 (옛 트리만) 새 경로로 바꾼다."""

    def __init__(self, cur, modules, attrs, forward: bool, text_sub):
        self.cur, self.modules, self.attrs, self.forward, self.text_sub = cur, modules, attrs, forward, text_sub

    def m(self, fqn):
        return map_fqn(fqn, self.modules, self.attrs) if self.forward else fqn

    @staticmethod
    def marker(text, node):
        return ast.copy_location(ast.Expr(value=ast.Constant(value="IMPORT " + text)), node)

    def visit_ImportFrom(self, node):
        base = resolve_base(self.cur, node)
        out = []
        for alias in node.names:
            fqn = base + "." + alias.name if alias.name != "*" else base + ".*"
            out.append(self.marker(f"{self.m(fqn)} AS {alias.asname or ''}", node))
        return out

    def visit_Import(self, node):
        return [self.marker(f"{self.m(a.name)} AS {a.asname or ''}", node) for a in node.names]

    def visit_Constant(self, node):
        if isinstance(node.value, str) and self.forward:
            node = ast.copy_location(ast.Constant(value=self.text_sub(node.value)), node)
        return node

    def visit_Attribute(self, node):
        self.generic_visit(node)
        if not self.forward:
            return node
        parts = []
        cur = node
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if not isinstance(cur, ast.Name):
            return node
        dotted = ".".join([cur.id] + parts[::-1])
        new = map_fqn(dotted, self.modules, self.attrs)
        if new == dotted:
            return node
        names = new.split(".")
        rebuilt = ast.Name(id=names[0], ctx=ast.Load())
        for n in names[1:]:
            rebuilt = ast.Attribute(value=rebuilt, attr=n, ctx=node.ctx)
        return ast.copy_location(rebuilt, node)


def docstrings_clean(tree):
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


def build_text_sub(modules):
    dotted = sorted(modules, key=len, reverse=True)
    dotted_re = re.compile(r"(?<![\w.])(" + "|".join(re.escape(k) for k in dotted) + r")(?![\w])")
    path_map = {"/".join(o.split(".")) + ".py": "/".join(n.split(".")) + ".py" for o, n in modules.items()}
    path_re = re.compile(r"serving_runtime/(?:mvp/)?(\w+)\.py")

    def sub(text: str) -> str:
        text = dotted_re.sub(lambda m: modules[m.group(1)], text)
        return path_re.sub(lambda m: path_map.get(m.group(0), m.group(0)), text)

    return sub


def dump(path: Path, rel: str, modules, attrs, forward: bool, text_sub) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree = Flatten(mod_info(rel), modules, attrs, forward, text_sub).visit(tree)
    ast.fix_missing_locations(tree)
    return ast.dump(docstrings_clean(tree), include_attributes=False)


def new_rel_for(rel: str, modules) -> str:
    info = mod_info(rel)
    if info and info[0] in modules and not info[1]:
        new_mod = modules[info[0]]
        return str(Path("src", *new_mod.split(".")).with_suffix(".py"))
    return rel


def shim_expected(new_mod: str) -> str:
    return (
        '"""Legacy import path. Points at the moved module; contains no logic. '
        'Delete with src/serving_runtime/mvp/ (see LEGACY_SHIMS.md)."""\n\n'
        "import sys\nfrom importlib import import_module\n\n"
        f'sys.modules[__name__] = import_module("{new_mod}")\n'
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", required=True, type=Path)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    args = parser.parse_args()
    if not args.old.is_dir() or not args.new.is_dir() or not args.map.is_file():
        print("인자 오류: 두 runtime 폴더와 이동표가 필요하다", file=sys.stderr)
        return 2

    modules, attrs, data = load(args.map)
    text_sub = build_text_sub(modules)
    old, new = py_files(args.old), py_files(args.new)
    problems: list[str] = []
    expected_new: dict[str, str] = {}  # 새 트리의 예상 위치

    compared = moved_count = 0
    for rel, path in sorted(old.items()):
        target_rel = new_rel_for(rel, modules)
        info = mod_info(rel)
        is_legacy_location = bool(info and info[0] in modules and not info[1])
        if target_rel not in new:
            problems.append(f"새 위치에 파일 없음: {rel} -> {target_rel}")
            continue
        expected_new[target_rel] = rel
        if target_rel != rel:
            moved_count += 1
        if rel == "src/serving_runtime/mvp/__init__.py":
            # 옛 패키지 __init__ 은 껍데기로 바뀐다. 양식을 정확히 확인한다.
            expected_text = (
                '"""Legacy package. Delete with src/serving_runtime/mvp/ (see LEGACY_SHIMS.md)."""\n\n'
                "from serving_runtime.config.config import MVPConfig\n\n"
                '__all__ = ["MVPConfig"]\n'
            )
            if new[target_rel].read_text(encoding="utf-8") != expected_text:
                problems.append(f"껍데기 양식 불일치: {rel}")
            continue
        try:
            a = dump(path, rel, modules, attrs, True, text_sub)
            b = dump(new[target_rel], target_rel, modules, attrs, False, text_sub)
        except SyntaxError as error:
            problems.append(f"구문 오류: {rel}: {error}")
            continue
        compared += 1
        if a != b:
            problems.append(f"내용 다름(import·경로 외 변경): {rel} -> {target_rel}")

    # 새 트리에만 있는 파일
    allowed_extra_init = {f"src/{'/'.join(p.split('.'))}/__init__.py" for p in data.get("new_packages", [])}
    for rel in sorted(new.keys() - expected_new.keys()):
        info = mod_info(rel)
        text = new[rel].read_text(encoding="utf-8")
        if info and info[0] in modules and not info[1]:
            if text != shim_expected(modules[info[0]]):
                problems.append(f"껍데기 양식 불일치: {rel}")
            continue
        if rel in allowed_extra_init:
            try:
                body = ast.parse(text).body
            except SyntaxError:
                body = [None]
            if any(not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)) for n in body):
                problems.append(f"새 패키지 __init__ 에 docstring 외 내용: {rel}")
            continue
        if rel in {"src/serving_runtime/mvp/__init__.py"}:
            continue
        problems.append(f"허용되지 않은 새 파일: {rel}")

    # 껍데기 존재 검사 (옛 위치에 모두 있어야 함)
    for old_mod, new_mod in modules.items():
        rel = str(Path("src", *old_mod.split(".")).with_suffix(".py"))
        if rel not in new:
            problems.append(f"껍데기 파일 없음: {rel}")

    print(f"옛 .py {len(old)}개 / 새 .py {len(new)}개, 비교 {compared}개, 위치가 바뀐 파일 {moved_count}개")
    if problems:
        print(f"문제 {len(problems)}건:")
        for line in problems[:60]:
            print("  " + line)
        return 1
    print("이동만 일어났다: 모든 파일이 import·경로 외에는 AST 동일, 새 파일은 껍데기와 빈 패키지뿐")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
