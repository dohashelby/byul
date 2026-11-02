"""CI가 건너뛰는 문서 변경에 테스트가 읽는 문서가 섞이지 않는지 대조한다."""
from __future__ import annotations

import re
import runpy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT.parent / ".github/workflows/update.yml"
RELEVANCE = runpy.run_path(str(ROOT.parent / ".github/scripts/update_ci_relevance.py"))
RELEVANCE = type("Relevance", (), {k: staticmethod(v) for k, v in RELEVANCE.items() if k in ("patterns", "relevant")})
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
    for paths in (triggers["push"]["paths"], RELEVANCE.patterns()):
        skip = paths.index("!update/docs/**/*.md")
        assert referenced <= set(paths[skip + 1:])


def test_pull_request_runs_always_and_job_filter_matches_push_filter():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert "paths" not in (workflow[True]["pull_request"] or {})
    assert RELEVANCE.patterns() == workflow[True]["push"]["paths"]
    jobs = workflow["jobs"]
    gated = [name for name in jobs if name not in ("changes", "update-ci-ok")]
    assert gated and all(jobs[name]["needs"] == "changes" for name in gated)
    assert all(jobs[name]["if"] == "needs.changes.outputs.relevant == 'true'" for name in gated)
    assert jobs["update-ci-ok"]["if"] == "always()" and set(jobs["update-ci-ok"]["needs"]) == {"changes", *gated}


@pytest.mark.parametrize("files,expected", [
    (["update/docs/gpu-a/notes.md"], False),
    (["update/docs/UPDATE_SELECTION.md"], True),
    (["update/docs/evidence/gpu-a-01.json"], True),
    (["update/src/guard/cli.py"], True),
    (["runtime/README.md"], False),
    (["update/docs/gpu-b/notes.md", "contracts/schemas/common.schema.json"], True),
    ([".github/workflows/update.yml"], True),
    ([".github/update-ci-paths.txt"], True),
])
def test_relevance_follows_last_matching_rule(files, expected):
    assert RELEVANCE.relevant(files, RELEVANCE.patterns()) is expected
