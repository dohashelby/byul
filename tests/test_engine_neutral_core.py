"""코어(core/, models/, store/)에 엔진 이름을 쓰지 않는다 (CLAUDE.md 3, SPEC §7.1)."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src/guard"
ENGINE_NAMES = re.compile(r"vllm|sglang|tensorrt|trt-llm", re.IGNORECASE)


def test_no_engine_names_in_core():
    hits = []
    for sub in ("core", "models", "store"):
        for p in (SRC / sub).rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if ENGINE_NAMES.search(line):
                    hits.append(f"{p.relative_to(SRC)}:{i}")
    assert hits == []
