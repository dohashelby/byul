"""CI가 건너뛰는 문서 변경에 테스트가 읽는 문서가 섞이지 않는지 대조한다."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github/workflows/update.yml"
DOC_REF = re.compile(r"(?:update/)?(docs/[A-Za-z0-9_./-]+\.md)")


def _referenced_docs() -> set[str]:
    """테스트·도구·제품 코드가 이름으로 읽는 저장소 내 docs 마크다운."""
    found: set[str] = set()
    for base in ("tests", "tools", "src"):
        for path in (ROOT / base).rglob("*.py"):
            for ref in DOC_REF.findall(path.read_text(encoding="utf-8")):
                if (ROOT / ref).is_file():
                    found.add(f"update/{ref}")
    return found


def test_docs_read_by_code_still_trigger_ci():
    triggers = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))[True]
    referenced = _referenced_docs()
    assert referenced
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        skip = paths.index("!update/docs/**/*.md")
        assert referenced <= set(paths[skip + 1:]), event
