"""Failed watch polls must not accumulate original native capture files."""
import importlib.util
from pathlib import Path

import pytest


def load_tool():
    source = Path(__file__).resolve().parents[1] / "tools" / "update_engine_link.py"
    spec = importlib.util.spec_from_file_location("native_watch_cleanup_tool", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeated_failed_capture_and_parse_leave_no_partial_files(tmp_path):
    tool = load_tool()
    original = tmp_path / "sealed-original.json"
    original.write_bytes(b"preserved")
    for _ in range(12):
        with pytest.raises(ValueError):
            with tool._temporary_capture(tmp_path) as capture:
                capture.write_bytes(b'{"partial":')
                raise ValueError("capture or strict JSON parse failed")
        assert list(tmp_path.glob("capture-*.json")) == []
    assert original.read_bytes() == b"preserved"


def test_successful_archive_survives_temporary_cleanup(tmp_path):
    tool = load_tool()
    archived = tmp_path / "live-original.json"
    with tool._temporary_capture(tmp_path) as capture:
        capture.write_bytes(b"original capture")
        capture.rename(archived)
    assert archived.read_bytes() == b"original capture"
    assert list(tmp_path.glob("capture-*.json")) == []


def test_archive_publish_preserves_original_inode_and_only_removes_temporary(tmp_path):
    tool = load_tool()
    archived = tmp_path / "sealed-original.json"
    with tool._temporary_capture(tmp_path) as capture:
        capture.write_bytes(b"original capture")
        inode = capture.stat().st_ino
        tool._archive_capture(capture, archived, b"original capture", 10010, 1024)
    assert archived.stat().st_ino == inode
    assert archived.stat().st_nlink == 1
    assert archived.read_bytes() == b"original capture"


def test_archive_creation_race_preserves_concurrent_original(tmp_path, monkeypatch):
    tool = load_tool()
    from serving_runtime.mvp import native_receipts
    archived = tmp_path / "sealed-original.json"
    link = tool.os.link
    def concurrent_create(source, target, **kwargs):
        Path(target).write_bytes(b"concurrent original")
        return link(source, target, **kwargs)
    monkeypatch.setattr(tool.os, 'link', concurrent_create)
    monkeypatch.setattr(native_receipts, 'read_protected', lambda path, *a, **k: Path(path).read_bytes())
    with pytest.raises(ValueError, match='ARCHIVE_CONFLICT'):
        with tool._temporary_capture(tmp_path) as capture:
            capture.write_bytes(b"different original")
            tool._archive_capture(capture, archived, b"different original", 10010, 1024)
    assert archived.read_bytes() == b"concurrent original"
    assert list(tmp_path.glob("capture-*.json")) == []
