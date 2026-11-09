"""Multiple offline indexes, bounded reads, and the files/ trust boundary."""
import copy
import json
from pathlib import Path

from byul_platform.config import DEFAULT_CONTRACTS_DIR, Settings
from byul_platform.sources.evidence import EvidenceSource
from byul_platform.state import build_state
from byul_platform.routes.evidence import _entries


def make_index(root: Path, path="run/log.txt", count=1):
    root.mkdir(parents=True, exist_ok=True)
    document = json.loads((DEFAULT_CONTRACTS_DIR / "examples/evidence-index.json").read_text())
    template = document["items"][0]
    document["items"] = [{**copy.deepcopy(template), "path": path, "evidence_id": f"ev-{i}"} for i in range(count)]
    index = root / "evidence-index.json"
    index.write_text(json.dumps(document))
    return index


def test_multiple_indexes_and_many_rows_are_validated_once(tmp_path):
    indexes = [make_index(tmp_path / str(i), count=1500) for i in range(2)]
    for index in indexes:
        target = index.parent / "files/run/log.txt"
        target.parent.mkdir(parents=True)
        target.write_text("metadata listing must not read this")
    state = build_state(Settings(evidence_index_paths=tuple(indexes + [indexes[0]])))
    try:
        count = 0
        original = state.contracts.errors

        def errors(schema, doc):
            nonlocal count
            count += 1
            return original(schema, doc)

        state.contracts.errors = errors
        entries, invalid = _entries(state)
        assert len(entries) == 3000 and invalid == []
        assert count == 3000
        assert state.source("evidence")["mode"] == "file"
        assert state.example_mode is True  # the other example sources stay visible
    finally:
        state.close()


def test_invalid_paths_links_missing_and_special_files(tmp_path):
    index = make_index(tmp_path / "index")
    source = EvidenceSource([index], "file")
    files = index.parent / "files"
    files.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("private")
    (files / "link").symlink_to(outside)
    (files / "inside").write_text("safe")
    (files / "internal-link").symlink_to(files / "inside")
    (files / "dir").mkdir()
    for path in ("../outside", "/absolute", "files/../../outside", "a//b", "./inside", "a\\b", "link", "internal-link", "dir", "missing"):
        assert source.path_error(str(index), path), path
    assert source.path_error(str(index), "inside") is None
    assert source.path_error(str(index), "files/inside") == "EVIDENCE_UNAVAILABLE"
    files.rename(index.parent / "actual")
    files.symlink_to(index.parent / "actual", target_is_directory=True)
    assert source.path_error(str(index), "inside") == "EVIDENCE_SYMLINK"


def test_path_error_is_reported_and_other_rows_preserved(tmp_path):
    index = make_index(tmp_path / "index", count=2)
    document = json.loads(index.read_text())
    document["items"][1]["path"] = "missing"
    index.write_text(json.dumps(document))
    target = index.parent / "files/run/log.txt"
    target.parent.mkdir(parents=True)
    target.write_text("safe")
    state = build_state(Settings(evidence_index_paths=(index,)))
    try:
        entries, invalid = _entries(state)
        assert len(entries) == len(invalid) == 1
        assert invalid[0]["errors"] == ["EVIDENCE_UNAVAILABLE"]
    finally:
        state.close()


def test_bounded_index_read_and_malformed_index(tmp_path):
    index = make_index(tmp_path / "index")
    source = EvidenceSource([index, tmp_path / "missing"], "file")
    source.MAX_INDEX_BYTES = 10
    results = list(source.indexes())
    assert results[0].document == {"_error": "INDEX_SIZE_LIMIT"}
    assert results[1].document == {"_error": "FileNotFoundError"}
    index.write_bytes(b"not JSON")
    assert list(source.indexes())[0].document == {"_error": "JSONDecodeError"}
    # Python 3.12's C JSON decoder uses a separate recursion budget.
    depth = 10000
    source.MAX_INDEX_BYTES = depth * 2
    index.write_bytes(b"[" * depth + b"]" * depth)
    assert list(source.indexes())[0].document == {"_error": "RecursionError"}


def test_example_missing_payload_does_not_hide_example_rows():
    index = DEFAULT_CONTRACTS_DIR / "examples/evidence-index.json"
    assert EvidenceSource([index], "example").path_error(str(index), "example.log") is None
