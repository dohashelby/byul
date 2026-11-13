"""vLLM 관측기 고정값을 두 판본 소스에서 함수 단위로 비교한다 (M-A6 판본 고정값 갱신 도구).

엔진을 import하거나 실행하지 않는다. 관측기 모듈(`guard.adapters.vllm_*`)의 고정값 표를 읽어
감싼 모듈·대상 함수 목록을 만들고, 각 판본 소스 트리에서 같은 함수의 code 객체 지문을 계산해
고정값 판본(reference)과 비교한다.

- 판본 트리는 vLLM 저장소 태그 소스이거나 이미지에서 꺼낸 설치 파일(`.../site-packages`)이다.
  `--side name=ROOT[:image]`의 `:image`가 없으면 결과 고정값은 모두 후보(`candidate_pin`)다.
- 기계 상태만 낸다. 분류 (a)~(d)는 제안(`suggested`)이며 (d)는 내지 않는다(사람이 판단).
- code 지문은 CPython minor 판본에 묶여 3.12에서만 실행한다.

예:
  uv run python -m tools.engine_pin_compare inventory
  uv run python -m tools.engine_pin_compare compare --reference /src/prev \
      --side base=/src/base --side candidate=/src/cand --out report.json
"""
from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import importlib
import json
import pkgutil
import re
import sys
import types
from pathlib import Path

SCHEMA = "guard-engine-pin-compare/1"
HEX = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
ENGINE_PREFIX = "vllm."
PATH_PREFIX = "vllm/"
STATUSES = ("FILE_IDENTICAL", "CODE_IDENTICAL", "CODE_EQUAL_LINES_SHIFTED", "DOCSTRING_ONLY", "CHANGED",
            "MOVED_EQUAL", "MOVED_CHANGED", "ABSENT", "MODULE_ABSENT", "REFERENCE_MISSING")
SUGGESTED = {"FILE_IDENTICAL": "a", "CODE_IDENTICAL": "a", "CODE_EQUAL_LINES_SHIFTED": "a", "DOCSTRING_ONLY": "a",
             "MOVED_EQUAL": "b", "CHANGED": "review", "MOVED_CHANGED": "review",
             "ABSENT": "c", "MODULE_ABSENT": "c", "REFERENCE_MISSING": "review"}


def _module_name(key):
    """관측기 표 키를 엔진 모듈 이름으로 바꾼다. 엔진 모듈이 아니면 None."""
    if type(key) is not str:
        return None
    if key.startswith(ENGINE_PREFIX) and NAME.fullmatch(key):
        return key
    # 경로 키는 패키지 안 상대 경로(`v1/engine/x.py`)만 받는다. 슬래시 없는 파일 이름은
    # 다른 라이브러리 파일(예: safetensors `torch.py`)이나 guard 주입 파일이라 제외한다.
    if key.endswith(".py") and "/" in key and not key.startswith("/") and ".." not in key.split("/"):
        stem = key[:-3].removeprefix(PATH_PREFIX).replace("/", ".")
        if NAME.fullmatch(stem):
            return ENGINE_PREFIX + stem
    return None


def _qualname(value):
    return isinstance(value, str) and NAME.fullmatch(value) is not None and HEX.fullmatch(value) is None


def _points(value):
    """표 값에서 대상 함수 qualname을 꺼낸다. ('C.m', 'f') 또는 {'C': ('m',)}."""
    if isinstance(value, str):
        return {value} if _qualname(value) else set()
    if isinstance(value, (tuple, list, frozenset, set)):
        return {v for v in value if _qualname(v)}
    if isinstance(value, dict):
        out = set()
        for owner, methods in value.items():
            if _qualname(owner) and isinstance(methods, (tuple, list, frozenset, set)):
                out |= {f"{owner}.{m}" for m in methods if _qualname(m)}
        return out
    return set()


def inventory(package="guard.adapters", prefix="vllm"):
    """관측기 모듈의 module-level 표에서 엔진 모듈별 고정 SHA·대상 함수·사용 관측기를 모은다."""
    pkg = importlib.import_module(package)
    modules, other_pins = {}, {}
    for info in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda i: i.name):
        if not info.name.startswith(prefix):
            continue
        mod = importlib.import_module(f"{package}.{info.name}")
        for var, table in sorted(vars(mod).items()):
            if not isinstance(table, dict) or not table:
                continue
            for key, value in table.items():
                name = _module_name(key)
                if name is None:
                    if type(key) is str and type(value) is str and HEX.fullmatch(value) and "." in key \
                            and NAME.fullmatch(key) and not key.startswith(ENGINE_PREFIX):
                        other_pins.setdefault(key, {}).setdefault(value, set()).add(f"{info.name}.{var}")
                    continue
                row = modules.setdefault(name, {"pins": {}, "points": {}})
                if type(value) is str and HEX.fullmatch(value):
                    row["pins"].setdefault(value, set()).add(f"{info.name}.{var}")
                for point in _points(value):
                    row["points"].setdefault(point, set()).add(f"{info.name}.{var}")
    def plain(d):
        return {k: sorted(v) for k, v in sorted(d.items())}
    return {"modules": {n: {"pins": plain(r["pins"]), "points": plain(r["points"])} for n, r in sorted(modules.items())},
            "other_pins": {n: plain(v) for n, v in sorted(other_pins.items())}}


def _code_item(code, *, lines):
    def item(value):
        if isinstance(value, types.CodeType):
            out = {"name": value.co_name, "qualname": value.co_qualname,
                   "args": [value.co_argcount, value.co_posonlyargcount, value.co_kwonlyargcount],
                   "flags": value.co_flags, "stack": value.co_stacksize, "locals": value.co_nlocals,
                   "bytecode": value.co_code.hex(), "exceptions": value.co_exceptiontable.hex(),
                   "names": value.co_names, "vars": value.co_varnames, "free": value.co_freevars,
                   "cells": value.co_cellvars, "constants": [item(v) for v in value.co_consts]}
            if lines:
                out.update(firstlineno=value.co_firstlineno, lines=value.co_linetable.hex())
            return out
        if isinstance(value, (tuple, frozenset)):
            children = [item(v) for v in value]
            if isinstance(value, frozenset):
                children.sort(key=lambda v: json.dumps(v, sort_keys=True))
            return {"tuple" if isinstance(value, tuple) else "frozenset": children}
        if isinstance(value, (bytes, float, complex)) or value is Ellipsis:
            return {type(value).__name__: repr(value)}
        return value
    return hashlib.sha256(json.dumps(item(code), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def exact_digest(code, filename):
    """관측기와 같은 규칙(상대 파일 이름으로 바꾼 code 객체의 base.code_digest)."""
    from guard.adapters import vllm_lifecycle_sensor as base
    from guard.adapters.vllm_worker_generation_contract import relative_code
    return base.code_digest(relative_code(code, filename))


def _codes(raw, filename):
    result = {}
    def visit(code):
        result.setdefault(code.co_qualname, code)
        for child in code.co_consts:
            if isinstance(child, types.CodeType):
                visit(child)
    visit(compile(raw, filename, "exec", dont_inherit=True, optimize=0))
    return result


class Tree:
    """한 판본의 엔진 소스 트리(`ROOT/vllm/...`). 파일·code 객체 계산을 캐시한다."""

    def __init__(self, root, *, image=False):
        self.root = Path(root)
        if not (self.root / "vllm" / "__init__.py").is_file():
            raise ValueError(f"PIN_COMPARE_TREE_INVALID: {root}")
        self.image = image
        self._files, self._index = {}, None

    @staticmethod
    def relpath(module):
        return module.replace(".", "/") + ".py"

    def file(self, module):
        if module not in self._files:
            path = self.root / self.relpath(module)
            if not path.is_file():
                package = self.root / module.replace(".", "/") / "__init__.py"
                path = package if package.is_file() else None
            if path is None:
                self._files[module] = None
            else:
                raw = path.read_bytes()
                rel = path.relative_to(self.root).as_posix()
                try:
                    codes = _codes(raw, rel)
                except SyntaxError:
                    codes = None
                self._files[module] = {"path": rel, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw),
                                       "codes": codes}
        return self._files[module]

    def find(self, qualname):
        """같은 qualname을 정의한 모듈 목록(AST 정적 탐색). 옮겨진 대상 찾기용."""
        if self._index is None:
            self._index = {}
            for path in sorted((self.root / "vllm").rglob("*.py")):
                try:
                    tree = ast.parse(path.read_bytes())
                except (SyntaxError, ValueError):
                    continue
                module = path.relative_to(self.root).with_suffix("").as_posix().replace("/", ".")
                module = module.removesuffix(".__init__")
                for name in _defined(tree):
                    self._index.setdefault(name, []).append(module)
        return self._index.get(qualname, [])


def _defined(tree):
    out = []
    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out.append(prefix + node.name)
            elif isinstance(node, ast.ClassDef):
                walk(node.body, prefix + node.name + ".")
    walk(tree.body, "")
    return out


def function_source(tree, module, qualname):
    """대상 함수의 소스 조각(검토용 diff). 없으면 None."""
    entry = tree.file(module)
    if entry is None:
        return None
    text = (tree.root / entry["path"]).read_text(encoding="utf-8")
    def walk(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and prefix + node.name == qualname:
                return ast.get_source_segment(text, node, padded=True)
            if isinstance(node, ast.ClassDef):
                found = walk(node.body, prefix + node.name + ".")
                if found is not None:
                    return found
        return None
    return walk(ast.parse(text).body, "")


def _strip_docstrings(node):
    for child in ast.walk(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.body:
            first = child.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                child.body = child.body[1:] or [ast.Pass()]
    return node


def logic_shape(source):
    """docstring·줄 위치를 뺀 AST 모양. 같으면 실행 의미가 같은 함수로 본다(검토 보조)."""
    if source is None:
        return None
    import textwrap
    tree = ast.parse(textwrap.dedent(source))
    return ast.dump(_strip_docstrings(tree), annotate_fields=True, include_attributes=False)


def signature(source):
    """인자 목록과 sync/async 종류. 감싸기 대상의 호출 형태가 유지되는지 보는 보조 지표."""
    if source is None:
        return None
    import textwrap
    node = ast.parse(textwrap.dedent(source)).body[0]
    return type(node).__name__, ast.dump(node.args, include_attributes=False)


def point_diff(reference, side, module, qualname, status):
    """CHANGED·MOVED_CHANGED 대상의 함수 단위 unified diff."""
    if status["status"] == "CHANGED":
        target = module
    elif status["status"] == "MOVED_CHANGED":
        target = status["moved"][0]["module"]
    else:
        return None
    old, new = function_source(reference, module, qualname), function_source(side, target, qualname)
    if old is None or new is None:
        return None
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"reference/{module}:{qualname}", f"side/{target}:{qualname}", n=2))


def _function(tree, module, qualname):
    entry = tree.file(module)
    if entry is None or entry["codes"] is None or qualname not in entry["codes"]:
        return None
    code = entry["codes"][qualname]
    return {"exact": exact_digest(code, entry["path"]), "shape": _code_item(code, lines=False)}


def compare_point(reference, side, module, qualname):
    ref = _function(reference, module, qualname)
    if ref is None:
        return {"status": "REFERENCE_MISSING"}
    ref_file, side_file = reference.file(module), side.file(module)
    here = _function(side, module, qualname)
    if here is not None:
        if side_file["sha256"] == ref_file["sha256"]:
            status = "FILE_IDENTICAL"
        elif here["exact"] == ref["exact"]:
            status = "CODE_IDENTICAL"
        elif here["shape"] == ref["shape"]:
            status = "CODE_EQUAL_LINES_SHIFTED"
        elif logic_shape(function_source(side, module, qualname)) == logic_shape(function_source(reference, module, qualname)):
            status = "DOCSTRING_ONLY"
        else:
            status = "CHANGED"
        result = {"status": status, "module": module, "code_sha256": here["exact"]}
        if status == "CHANGED":
            result["signature_equal"] = (signature(function_source(side, module, qualname))
                                         == signature(function_source(reference, module, qualname)))
        return result
    moved = []
    for other in side.find(qualname):
        if other == module:
            continue
        found = _function(side, other, qualname)
        if found is not None:
            moved.append({"module": other, "equal": found["shape"] == ref["shape"], "code_sha256": found["exact"]})
    if any(m["equal"] for m in moved):
        return {"status": "MOVED_EQUAL", "moved": moved}
    if moved:
        return {"status": "MOVED_CHANGED", "moved": moved}
    return {"status": "MODULE_ABSENT" if side_file is None else "ABSENT"}


def compare(inv, reference, sides):
    rows = []
    for module, row in inv["modules"].items():
        ref_file = reference.file(module)
        pins = sorted(row["pins"])
        record = {"module": module, "observers": sorted({o for v in (*row["pins"].values(), *row["points"].values()) for o in v}),
                  "reference": None if ref_file is None else {k: ref_file[k] for k in ("path", "sha256", "size")},
                  "reference_matches_pin": ref_file is not None and ref_file["sha256"] in pins,
                  "pins": pins, "sides": {}}
        for name, tree in sides.items():
            entry = tree.file(module)
            points = {q: compare_point(reference, tree, module, q) for q in sorted(row["points"])}
            for value in points.values():
                value["suggested"] = SUGGESTED[value["status"]]
            record["sides"][name] = {
                "file": None if entry is None else {k: entry[k] for k in ("path", "sha256", "size")},
                "file_matches_pin": entry is not None and entry["sha256"] in pins,
                "pin_kind": None if entry is None else ("image_pin" if tree.image else "candidate_pin"),
                "points": points}
        rows.append(record)
    summary = {name: {s: sum(1 for r in rows for p in r["sides"][name]["points"].values() if p["status"] == s)
                      for s in STATUSES} for name in sides}
    module_summary = {name: {"modules": len(rows),
                             "module_absent": sum(1 for r in rows if r["sides"][name]["file"] is None),
                             "file_identical": sum(1 for r in rows if r["sides"][name]["file_matches_pin"])}
                      for name in sides}
    return {"schema": SCHEMA, "python": ".".join(map(str, sys.version_info[:2])), "modules": rows,
            "point_summary": summary, "module_summary": module_summary,
            "by_observer": by_observer(inv, rows, list(sides)), "other_pins": inv["other_pins"]}


def by_observer(inv, rows, names):
    """관측기 파일별로 자기 표에 적힌 대상 함수만 모은 상태 요약."""
    index = {r["module"]: r for r in rows}
    out = {}
    for module, row in inv["modules"].items():
        for owners in row["pins"].values():
            for owner in owners:
                view = out.setdefault(owner.split(".")[0], {"modules": set(), "points": {n: {} for n in names}})
                view["modules"].add(module)
        for point, owners in row["points"].items():
            for owner in owners:
                view = out.setdefault(owner.split(".")[0], {"modules": set(), "points": {n: {} for n in names}})
                view["modules"].add(module)
                for name in names:
                    status = index[module]["sides"][name]["points"][point]["status"]
                    view["points"][name][f"{module}:{point}"] = status
    result = {}
    for owner, view in sorted(out.items()):
        result[owner] = {"modules": sorted(view["modules"]),
                         "module_absent": {n: sorted(m for m in view["modules"] if index[m]["sides"][n]["file"] is None)
                                           for n in names},
                         "status_counts": {n: {s: list(view["points"][n].values()).count(s) for s in STATUSES
                                               if s in view["points"][n].values()} for n in names},
                         "points": {n: dict(sorted(view["points"][n].items())) for n in names}}
    return result


def _side(text):
    name, sep, root = text.partition("=")
    if not sep or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", name) or not root:
        raise argparse.ArgumentTypeError("--side name=ROOT[:image]")
    image = root.endswith(":image")
    return name, Tree(root.removesuffix(":image"), image=image)


def main(argv=None):
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("engine_pin_compare: CPython 3.12 required (code digests are minor-version bound)")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("inventory")
    cmp = sub.add_parser("compare")
    cmp.add_argument("--reference", required=True, help="현재 고정값 판본 소스 트리")
    cmp.add_argument("--side", action="append", type=_side, required=True)
    cmp.add_argument("--out", type=Path)
    cmp.add_argument("--diff-dir", type=Path, help="바뀐 대상 함수의 diff를 판본별 파일로 쓴다")
    args = parser.parse_args(argv)
    inv = inventory()
    if args.command == "inventory":
        result = inv
    else:
        sides = dict(args.side)
        if len(sides) != len(args.side):
            parser.error("duplicate --side name")
        reference = Tree(args.reference)
        result = compare(inv, reference, sides)
        if args.diff_dir:
            args.diff_dir.mkdir(parents=True, exist_ok=True)
            for name, tree in sides.items():
                parts = []
                for row in result["modules"]:
                    for qualname, status in row["sides"][name]["points"].items():
                        diff = point_diff(reference, tree, row["module"], qualname, status)
                        if diff:
                            parts.append(diff)
                (args.diff_dir / f"{name}.diff").write_text("\n".join(parts), encoding="utf-8")
    text = json.dumps(result, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    if getattr(args, "out", None):
        args.out.write_text(text, encoding="utf-8")
        print(json.dumps({"point_summary": result["point_summary"], "module_summary": result["module_summary"]},
                         indent=1, ensure_ascii=False))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
