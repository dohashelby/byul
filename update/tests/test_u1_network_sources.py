"""공개 소스만 읽는 명시적 검증. 기본 suite에서는 새 network 마커로 분리한다."""
from pathlib import Path
import os

import pytest

from guard.adapters.git_diff import extract_git_change
from guard.runtime.git import ensure_repository
from tests.builders import baseline_pack


@pytest.mark.network
def test_pinned_official_source_diff_is_repeatable(tmp_path):
    cache = os.environ.get("GUARD_U1_SOURCE_REPOSITORY")
    source = Path(cache) if cache else ensure_repository(tmp_path / "source", "https://github.com/vllm-project/vllm.git")
    pack = baseline_pack()
    first = extract_git_change(source, "v0.31.0", "v0.32.0", pack=pack)
    second = extract_git_change(source, "v0.31.0", "v0.32.0", pack=pack)
    assert first.change.baseline.source_commit == second.change.baseline.source_commit
    assert first.change.candidate.source_commit == second.change.candidate.source_commit
    assert len(first.change.baseline.source_commit) == 40
    assert len(first.change.candidate.source_commit) == 40
    assert first.artifact_bytes == second.artifact_bytes
    assert any(f.category == "OTHER" and f.unmapped for f in first.change.facts)
    assert all(f.certainty != "CONFIRMED" for f in first.change.facts)
