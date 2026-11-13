#!/usr/bin/env python3
"""파일 전체의 sha256 으로 **고정(pin)된 값**을 찾는다 (표준 라이브러리만).

저장소 안 모든 텍스트 파일에서 64자리 16진수를 찾아, 기준 저장소의 어떤 파일 전체 해시와 같으면
"그 파일을 이 곳이 고정하고 있다"로 본다. 기준 대비 현재 저장소에서 **고정 대상 파일의 바이트가 바뀌었는데
고정값이 그대로인 곳**을 보여 준다. 포맷·이동으로 파일 내용이 바뀌면 고정값도 같이 고쳐야 하기 때문이다.

사용:
  python find_hash_pins.py --base <기준 저장소 루트(예: a5fd55be 사본)> --root <현재 저장소 루트>
  # --root 를 생략하면 기준 저장소만 훑어 고정 목록을 보여 준다.

한계: 여러 파일을 합쳐서 계산한 해시, 파일 일부의 해시, 이동한 파일은 찾지 못한다. 목록은 점검의 출발점이다.
종료 코드: 0 = 어긋난 고정 없음, 1 = 어긋난 고정 있음
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

SKIP = {".git", ".venv", "node_modules", "__pycache__", "build", "dist"}
HEX = re.compile(r"\b[0-9a-f]{64}\b")
TEXT_SUFFIXES = {".json", ".yaml", ".yml", ".py", ".md", ".toml", ".txt", ".service", ".sh", ".cfg", ".ini"}


def walk(root: Path):
    for path in root.rglob("*"):
        parts = path.relative_to(root).parts
        if path.is_file() and not (SKIP & set(parts)) and not any(p.endswith(".egg-info") for p in parts):
            yield path


def file_hashes(root: Path) -> dict[str, str]:
    table = {}
    for path in walk(root):
        try:
            table[hashlib.sha256(path.read_bytes()).hexdigest()] = str(path.relative_to(root))
        except OSError:
            continue
    return table


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, type=Path)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()

    base_hashes = file_hashes(args.base)
    pins: dict[tuple[str, str], set[str]] = {}
    for path in walk(args.base):
        if path.suffix not in TEXT_SUFFIXES or path.stat().st_size > 3_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for value in HEX.findall(text):
            if value in base_hashes:
                pins.setdefault((base_hashes[value], value), set()).add(str(path.relative_to(args.base)))

    print(f"기준 저장소에서 '파일 전체 해시와 일치하는 값'을 담은 곳: {len(pins)}개 대상")
    broken = 0
    for (target, value), where in sorted(pins.items()):
        status = ""
        if args.root:
            current = args.root / target
            if not current.exists():
                status = "  [대상 파일 없음/이동]"
                broken += 1
            elif hashlib.sha256(current.read_bytes()).hexdigest() != value:
                status = "  [어긋남: 대상 바이트가 바뀜]"
                broken += 1
        print(f"  {target}{status}\n    <- {', '.join(sorted(where)[:4])}")
    if args.root:
        print(f"어긋난 고정: {broken}건")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
