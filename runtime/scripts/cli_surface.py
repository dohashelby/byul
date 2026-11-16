#!/usr/bin/env python3
"""serving-runtime CLI의 겉모습(하위 명령, 인자, 기본값, 도움말)을 JSON으로 뽑고, 두 개를 비교한다.

쓰임: ③ 묶음(WP3)에서 `cli.py`를 `cli/` 패키지로 나눌 때 "명령줄이 그대로인가"를 기계적으로 확인한다.

  # 현재 코드의 CLI 표면을 뽑기
  python cli_surface.py dump --src RUNTIME/src --out before.json
  # 사람이 읽는 표(마크다운)로 보기
  python cli_surface.py table before.json > table.md
  # 두 개 비교 (같으면 종료 코드 0)
  python cli_surface.py compare before.json after.json
  # 요약 숫자(하위 명령 수, 인자 수, 명령별 인자 수)
  python cli_surface.py summary before.json

작동 방식: `serving_runtime.cli.main`이 부르는 첫 `ArgumentParser.parse_args`를 가로채 루트 파서를 얻고,
거기서 모든 하위 파서를 따라 내려가며 각 인자의 옵션 이름·기본값·타입·선택지·필수 여부·도움말·상호 배타 묶음과
`format_help()` 전체 글을 기록한다. 명령을 실제로 실행하지 않는다.
비교는 "겉모습이 완전히 같음"을 요구한다. 의도한 변경(인자 삭제·추가)이 있으면 `--allow-removed`/`--allow-added`로
예외를 명시해야 하고, 그 예외는 출력에 따로 표시된다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


class _Captured(Exception):
    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__("captured")
        self.parser = parser


def _type_name(action: argparse.Action) -> str | None:
    kind = getattr(action, "type", None)
    if kind is None:
        return None
    return getattr(kind, "__name__", None) or repr(kind)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return repr(value)


def _dump_parser(parser: argparse.ArgumentParser, path: str, out: dict[str, Any]) -> None:
    group_of: dict[int, int] = {}
    for index, group in enumerate(getattr(parser, "_mutually_exclusive_groups", [])):
        for action in group._group_actions:
            group_of[id(action)] = index
    actions = []
    subparsers: list[tuple[str, argparse.ArgumentParser]] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                subparsers.append((name, sub))
            actions.append(
                {
                    "kind": "subparsers",
                    "dest": action.dest,
                    "choices": sorted(action.choices),
                    "required": bool(action.required),
                }
            )
            continue
        actions.append(
            {
                "kind": type(action).__name__,
                "options": list(action.option_strings),
                "dest": action.dest,
                "nargs": _jsonable(action.nargs),
                "default": _jsonable(action.default),
                "const": _jsonable(action.const),
                "type": _type_name(action),
                "choices": _jsonable(list(action.choices)) if action.choices is not None else None,
                "required": bool(action.required),
                "metavar": _jsonable(action.metavar),
                "help": action.help,
                "exclusive_group": group_of.get(id(action)),
            }
        )
    os.environ["COLUMNS"] = "100"
    out[path] = {
        "prog": parser.prog,
        "description": parser.description,
        "actions": actions,
        "help_text": parser.format_help(),
    }
    for name, sub in subparsers:
        _dump_parser(sub, f"{path} {name}".strip(), out)


def dump(src: Path) -> dict[str, Any]:
    sys.path.insert(0, str(src))
    os.environ["COLUMNS"] = "100"
    original = argparse.ArgumentParser.parse_args

    def capture(self: argparse.ArgumentParser, *_args: Any, **_kwargs: Any) -> Any:
        raise _Captured(self)

    argparse.ArgumentParser.parse_args = capture  # type: ignore[method-assign]
    # 설치하지 않은 소스 트리에서도 `--version` 등록이 실패하지 않게 한다(버전 값은 표면 비교에 쓰이지 않음).
    import importlib.metadata as metadata  # noqa: PLC0415

    real_version = metadata.version

    def fake_version(name: str) -> str:
        try:
            return real_version(name)
        except metadata.PackageNotFoundError:
            return "0+source"

    metadata.version = fake_version  # type: ignore[assignment]
    try:
        from serving_runtime import cli  # noqa: PLC0415

        try:
            cli.main([])
        except _Captured as captured:
            root = captured.parser
        else:
            raise SystemExit("cli.main이 ArgumentParser.parse_args를 부르지 않았습니다")
    finally:
        argparse.ArgumentParser.parse_args = original  # type: ignore[method-assign]
    out: dict[str, Any] = {}
    _dump_parser(root, "", out)
    return {"parsers": out}


def _arg_count(parser: dict[str, Any]) -> int:
    return sum(1 for a in parser["actions"] if a["kind"] not in {"subparsers", "_HelpAction", "_VersionAction"})


def summary(data: dict[str, Any]) -> dict[str, Any]:
    parsers = data["parsers"]
    leaves = {k: _arg_count(v) for k, v in parsers.items() if k and not any(a["kind"] == "subparsers" for a in v["actions"])}
    return {
        "parsers": len(parsers),
        "leaf_commands": len(leaves),
        "arguments_total": sum(leaves.values()),
        "arguments_by_command": dict(sorted(leaves.items(), key=lambda kv: -kv[1])),
        "top_level_commands": next(
            (a["choices"] for a in parsers[""]["actions"] if a["kind"] == "subparsers"), []
        ),
    }


def table(data: dict[str, Any]) -> str:
    rows = ["| 하위 명령 | 옵션 | dest | 기본값 | 필수 | 타입 | 선택지 | 배타묶음 |", "|---|---|---|---|---|---|---|---|"]
    for path, parser in data["parsers"].items():
        for a in parser["actions"]:
            if a["kind"] in {"subparsers", "_HelpAction", "_VersionAction"}:
                continue
            opts = ", ".join(a["options"]) or f"(위치) {a['dest']}"
            rows.append(
                "| {c} | `{o}` | {d} | {df} | {r} | {t} | {ch} | {g} |".format(
                    c=path or "(루트)",
                    o=opts,
                    d=a["dest"],
                    df="" if a["default"] is None else f"`{a['default']}`",
                    r="예" if a["required"] else "",
                    t=a["type"] or ("flag" if a["kind"] in {"_StoreTrueAction", "_StoreFalseAction"} else ""),
                    ch=",".join(map(str, a["choices"])) if a["choices"] else "",
                    g="" if a["exclusive_group"] is None else str(a["exclusive_group"]),
                )
            )
    return "\n".join(rows)


def _index(parser: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {(",".join(a.get("options", [])) or a["dest"]): a for a in parser["actions"]}


def compare(
    before: dict[str, Any],
    after: dict[str, Any],
    allow_removed: set[str],
    allow_added: set[str],
    allow_changed: set[str] | None = None,
) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    allowed: list[str] = []
    bp, ap = before["parsers"], after["parsers"]
    for path in sorted(set(bp) | set(ap)):
        label = path or "(루트)"
        if path not in ap:
            problems.append(f"하위 명령이 사라짐: {label}")
            continue
        if path not in bp:
            problems.append(f"하위 명령이 새로 생김: {label}")
            continue
        bi, ai = _index(bp[path]), _index(ap[path])
        changed_surface = False
        for key in sorted(set(bi) | set(ai)):
            tag = f"{label} {key}"
            if key not in ai:
                (allowed if tag in allow_removed else problems).append(f"인자 삭제: {tag}" + (" (허용됨)" if tag in allow_removed else ""))
                changed_surface = True
            elif key not in bi:
                (allowed if tag in allow_added else problems).append(f"인자 추가: {tag}" + (" (허용됨)" if tag in allow_added else ""))
                changed_surface = True
            elif bi[key] != ai[key]:
                diff = {k: (bi[key].get(k), ai[key].get(k)) for k in bi[key] if bi[key].get(k) != ai[key].get(k)}
                if tag in (allow_changed or set()):
                    allowed.append(f"인자 정의 변경(허용됨): {tag}: {diff}")
                else:
                    problems.append(f"인자 정의 변경: {tag}: {diff}")
                changed_surface = True
        if bp[path]["description"] != ap[path]["description"]:
            problems.append(f"설명 변경: {label}")
        if bp[path]["help_text"] != ap[path]["help_text"] and not changed_surface:
            problems.append(f"도움말 글 변경(인자 정의는 같음): {label}")
    return problems, allowed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--src", required=True, type=Path, help="runtime/src 경로")
    d.add_argument("--out", required=True, type=Path)
    t = sub.add_parser("table")
    t.add_argument("json", type=Path)
    s = sub.add_parser("summary")
    s.add_argument("json", type=Path)
    c = sub.add_parser("compare")
    c.add_argument("before", type=Path)
    c.add_argument("after", type=Path)
    c.add_argument("--allow-removed", default="", help="'하위 명령 옵션' 형식을 쉼표로 구분. 예: 'mvp --foo,enforce sync --bar'")
    c.add_argument("--allow-added", default="")
    c.add_argument("--allow-changed", default="", help="정의(기본값·도움말 등)가 바뀌어도 되는 인자. 같은 형식")
    args = ap.parse_args()
    if args.cmd == "dump":
        data = dump(args.src)
        args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        s_ = summary(data)
        print(f"파서 {s_['parsers']}개, 말단 명령 {s_['leaf_commands']}개, 인자 {s_['arguments_total']}개 → {args.out}")
        return 0
    if args.cmd == "table":
        print(table(json.loads(args.json.read_text(encoding="utf-8"))))
        return 0
    if args.cmd == "summary":
        print(json.dumps(summary(json.loads(args.json.read_text(encoding="utf-8"))), ensure_ascii=False, indent=1))
        return 0
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    rem = {x.strip() for x in args.allow_removed.split(",") if x.strip()}
    add = {x.strip() for x in args.allow_added.split(",") if x.strip()}
    chg = {x.strip() for x in args.allow_changed.split(",") if x.strip()}
    problems, allowed = compare(before, after, rem, add, chg)
    for line in allowed:
        print("허용된 변경:", line)
    for line in problems:
        print("문제:", line)
    print("통과" if not problems else f"실패: 문제 {len(problems)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
