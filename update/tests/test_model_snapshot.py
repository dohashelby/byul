"""기존 inventory의 실제 파일 내용 해시·scope·경로 규칙을 소비한다."""
from copy import deepcopy
from pathlib import Path
import os
import pytest

from guard.adapters.model_inventory import inventory
from guard.collectors.model_manifest import build_manifest
from guard.collectors.model_snapshot import inspect_snapshot


@pytest.fixture
def snapshot(tmp_path):
    root = tmp_path / "snapshot"; root.mkdir()
    (root / "config.json").write_text('{"model_type":"EXAMPLE"}', encoding="utf-8")
    (root / "tokenizer_config.json").write_text('{}', encoding="utf-8")
    (root / "model.safetensors").write_bytes(b"EXAMPLE-not-a-loadable-model")
    (root / ".byul-model-ready.json").write_text('{"example":true}', encoding="utf-8")
    return root, build_manifest(root, model_id="EXAMPLE/model")


def test_snapshot_reuses_the_existing_whole_mount_format_and_hash(snapshot):
    root, reference = snapshot
    original = deepcopy(reference)
    result = inspect_snapshot(root, reference, expected_root=root)
    assert result["status"] == "MATCH" and result["observed_manifest"] == reference
    existing = inventory(root, "all")
    assert result["observed_manifest"]["inventory_sha256"] == existing["inventory_sha256"]
    assert ".byul-model-ready.json" in [f["path"] for f in existing["files"]]
    assert result["worker_consumption_verified"] is False and reference == original


@pytest.mark.parametrize("change", ["content", "missing", "partial", "extra", "root"])
def test_changes_missing_partial_and_wrong_root_are_never_ready(snapshot, tmp_path, change):
    root, reference = snapshot
    if change == "content": (root / "model.safetensors").write_bytes(b"changed")
    if change in {"missing", "partial"}: (root / "model.safetensors").unlink()
    if change == "partial": (root / "tokenizer_config.json").unlink()
    if change == "extra": (root / "new.bin").write_bytes(b"extra")
    expected = tmp_path / "another" if change == "root" else root
    result = inspect_snapshot(root, reference, expected_root=expected)
    assert result["status"] != "MATCH" and not result["worker_consumption_verified"]
    if change == "content": assert result["changed"] == ["model.safetensors"]
    if change in {"missing", "partial"}: assert "model.safetensors" in result["missing"]
    if change == "extra": assert result["unexpected"] == ["new.bin"]
    if change == "root": assert result["diagnostic_codes"] == ["MODEL_SNAPSHOT_ROOT_MISMATCH"]


def test_absent_snapshot_cannot_produce_an_empty_complete_inventory(snapshot, tmp_path):
    _, reference = snapshot
    result = inspect_snapshot(tmp_path / "absent", reference)
    assert result["status"] == "UNAVAILABLE" and result["observed_manifest"] is None


@pytest.mark.parametrize("reference", [{"model": "example", "revision": "a" * 40, "files": []},
    {"schema": "guard.model-manifest/1", "scope": "all", "files": []}])
def test_aws_handoff_or_partial_reference_is_not_guard_inventory(snapshot, reference):
    root, _ = snapshot
    with pytest.raises(ValueError, match="^MODEL_SNAPSHOT_REFERENCE_INVALID$"):
        inspect_snapshot(root, reference)


@pytest.mark.parametrize("broken", [False, True])
def test_resolved_file_content_uses_existing_symlink_semantics(tmp_path, monkeypatch, broken):
    root = tmp_path / "snap"; root.mkdir()
    blob = tmp_path / "blob"; blob.write_bytes(b"EXAMPLE-blob")
    link = root / "model.bin"
    if os.name == "nt":
        # Windows test host lacks symlink privilege. Exercise the same existing
        # resolver boundary explicitly; Linux CI uses a real filesystem symlink.
        link.write_bytes(b"EXAMPLE-link-placeholder")
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == link:
                if broken: raise FileNotFoundError("EXAMPLE-private-path")
                return blob
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", resolve)
    else:
        link.symlink_to(blob)
        if broken: blob.unlink()
    reference = {"schema": "guard.model-manifest/1", "model_id": "EXAMPLE/model", "settings": {},
        **inventory(root, "all")} if not broken else None
    if broken:
        # Keep an independent valid reference without repairing the broken link.
        safe = tmp_path / "reference"; safe.mkdir(); (safe / "model.bin").write_bytes(b"EXAMPLE-blob")
        reference = build_manifest(safe, model_id="EXAMPLE/model")
    result = inspect_snapshot(root, reference)
    assert result["status"] == ("UNAVAILABLE" if broken else "MATCH")
    assert not result["worker_consumption_verified"]
    assert "EXAMPLE-private-path" not in str(result)
