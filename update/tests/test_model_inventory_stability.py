"""기존 inventory/snapshot 제품 경로의 로컬 파일 교체 회귀. worker 소비 실증은 아니다."""
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters import model_inventory as module
from guard.collectors.model_manifest import build_manifest
from guard.collectors.model_snapshot import inspect_snapshot
from guard.models import canonical_json, sha256_hex


@pytest.mark.parametrize("scope", ["all", "vllm"])
def test_stable_files_keep_the_existing_manifest_bytes_and_hash(tmp_path, scope):
    (tmp_path / "config.json").write_bytes(b"{}")
    (tmp_path / "model.bin").write_bytes(b"EXAMPLE-weights")
    (tmp_path / "other.txt").write_bytes(b"EXAMPLE-other")
    names = ["config.json", "model.bin"] + (["other.txt"] if scope == "all" else [])
    expected = [{"path": name, "bytes": len((tmp_path / name).read_bytes()),
                 "sha256": hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()} for name in names]
    result = module.inventory(tmp_path, scope)
    assert result == {"scope": scope, "file_count": len(expected), "total_bytes": sum(r["bytes"] for r in expected),
                      "files": expected, "inventory_sha256": sha256_hex(canonical_json(expected))}


@pytest.mark.parametrize("mode", ["same-size-write", "replace", "earlier-file", "add", "remove", "root-replace"])
def test_change_after_hash_cannot_be_reported_as_a_complete_matching_snapshot(tmp_path, monkeypatch, mode):
    root = tmp_path / "snapshot"
    root.mkdir()
    for name in ("a.bin", "z.bin"):
        (root / name).write_bytes(b"EXAMPLE-old")
    reference = build_manifest(root)
    original = module._file_sha256
    changed = False
    def mutate(path, **kwargs):
        nonlocal changed
        digest = original(path, **kwargs)
        trigger = path.name == ("z.bin" if mode in {"earlier-file", "root-replace"} else "a.bin")
        if trigger and not changed:
            changed = True
            if mode in {"same-size-write", "earlier-file"}:
                changed_path = root / "a.bin"
                previous = changed_path.stat()
                changed_path.write_bytes(b"EXAMPLE-new")
                # Windows may coalesce rapid LastWriteTime updates. Select a
                # deterministic observed metadata change without sleeping.
                os.utime(changed_path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10**9))
            elif mode == "replace":
                replacement = tmp_path / "replacement"
                replacement.write_bytes(b"EXAMPLE-old")
                replacement.replace(root / "a.bin")
            elif mode == "add":
                (root / "new.bin").write_bytes(b"EXAMPLE-new")
            elif mode == "remove":
                (root / "z.bin").unlink()
            else:
                root.rename(tmp_path / "old-snapshot")
                root.mkdir()
                for name in ("a.bin", "z.bin"):
                    (root / name).write_bytes(b"EXAMPLE-old")
        return digest
    monkeypatch.setattr(module, "_file_sha256", mutate)
    result = inspect_snapshot(root, reference)
    assert changed and result["status"] == "UNAVAILABLE" and result["observed_manifest"] is None
    assert result["diagnostic_codes"] == ["MODEL_SNAPSHOT_UNAVAILABLE"]
    assert not result["worker_consumption_verified"]
    assert reference["inventory_sha256"] == sha256_hex(canonical_json(reference["files"]))


@pytest.mark.parametrize("retarget", [False, True])
def test_symlink_resolution_is_rechecked_without_changing_existing_follow_semantics(tmp_path, monkeypatch, retarget):
    root = tmp_path / "snapshot"
    root.mkdir()
    first, second = tmp_path / "first", tmp_path / "second"
    first.write_bytes(b"EXAMPLE-first")
    second.write_bytes(b"EXAMPLE-other")
    link = root / "model.bin"
    chosen = [first]
    if os.name == "nt":
        # 기존 Windows resolver 경계 대역; Linux에서는 실제 symlink를 바꾼다.
        link.write_bytes(b"EXAMPLE-placeholder")
        resolve = Path.resolve
        def mapped(path, *args, **kwargs):
            return chosen[0] if path == link else resolve(path, *args, **kwargs)
        monkeypatch.setattr(Path, "resolve", mapped)
    else:
        link.symlink_to(first)
    reference = build_manifest(root)
    original = module._file_sha256
    def hash_then_retarget(path, **kwargs):
        digest = original(path, **kwargs)
        if retarget:
            if os.name == "nt":
                chosen[0] = second
            else:
                link.unlink()
                link.symlink_to(second)
        return digest
    monkeypatch.setattr(module, "_file_sha256", hash_then_retarget)
    result = inspect_snapshot(root, reference)
    assert result["status"] == ("UNAVAILABLE" if retarget else "MATCH")
    assert not result["worker_consumption_verified"]


@pytest.mark.parametrize("field", ["st_ino", "st_size", "st_mtime_ns"])
def test_open_descriptor_must_match_the_selected_file_identity(tmp_path, monkeypatch, field):
    path = tmp_path / "model.bin"
    path.write_bytes(b"EXAMPLE-data")
    expected = module._file_identity(path.stat())
    original = module.os.fstat
    def different(fd):
        value = original(fd)
        fields = {key: getattr(value, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}
        fields[field] += 1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(module.os, "fstat", different)
    with pytest.raises(RuntimeError, match="^MODEL_INVENTORY_SOURCE_CHANGED$"):
        module._file_sha256(path, expected_identity=expected)


@pytest.mark.parametrize("field", ["st_ino", "st_size", "st_mtime_ns", "st_ctime_ns"])
def test_descriptor_change_during_read_is_rejected_including_its_own_ctime(tmp_path, monkeypatch, field):
    path = tmp_path / "model.bin"
    path.write_bytes(b"EXAMPLE-data")
    original = module.os.fstat
    calls = 0
    def changing(fd):
        nonlocal calls
        calls += 1
        value = original(fd)
        fields = {key: getattr(value, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}
        if calls == 2:
            fields[field] += 1
        return SimpleNamespace(**fields)
    monkeypatch.setattr(module.os, "fstat", changing)
    with pytest.raises(RuntimeError, match="^MODEL_INVENTORY_SOURCE_CHANGED$"):
        module._file_sha256(path)


def test_path_and_descriptor_ctime_domains_do_not_manufacture_a_change(tmp_path, monkeypatch):
    path = tmp_path / "model.bin"
    path.write_bytes(b"EXAMPLE-data")
    original = module.os.fstat
    def separate_clock(fd):
        value = original(fd)
        fields = {key: getattr(value, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")}
        fields["st_ctime_ns"] += 100
        return SimpleNamespace(**fields)
    monkeypatch.setattr(module.os, "fstat", separate_clock)
    value = module.inventory(tmp_path)
    assert value["files"][0]["sha256"] == hashlib.sha256(b"EXAMPLE-data").hexdigest()


def test_read_failure_keeps_snapshot_unavailable_and_does_not_store_exception_text(tmp_path, monkeypatch):
    path = tmp_path / "model.bin"
    path.write_bytes(b"EXAMPLE-data")
    reference = build_manifest(tmp_path)
    def unavailable(*args, **kwargs):
        raise PermissionError("NEVER_PERSIST_SOURCE_EXCEPTION")
    monkeypatch.setattr(module, "_file_sha256", unavailable)
    result = inspect_snapshot(tmp_path, reference)
    assert result["status"] == "UNAVAILABLE" and result["observed_manifest"] is None
    assert "NEVER_PERSIST" not in json.dumps(result)


def test_absent_root_cannot_be_an_empty_complete_inventory(tmp_path):
    with pytest.raises(FileNotFoundError):
        module.inventory(tmp_path / "absent")


@pytest.mark.parametrize("replace", [False, True])
def test_existing_inventory_cli_publishes_only_a_complete_result(tmp_path, monkeypatch, capsys, replace):
    root = tmp_path / "snapshot"
    root.mkdir()
    path = root / "model.bin"
    path.write_bytes(b"EXAMPLE-old")
    output = tmp_path / "inventory.json"
    monkeypatch.setattr(module, "__name__", "guard.adapters.model_inventory")
    monkeypatch.setattr("sys.argv", ["model_inventory", str(root), "--json", str(output)])
    if replace:
        original = module._file_sha256
        def hash_then_write(value, **kwargs):
            digest = original(value, **kwargs)
            previous = value.stat()
            value.write_bytes(b"EXAMPLE-new")
            os.utime(value, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10**9))
            return digest
        monkeypatch.setattr(module, "_file_sha256", hash_then_write)
        with pytest.raises(RuntimeError, match="^MODEL_INVENTORY_SOURCE_CHANGED$"):
            module.main()
        assert not output.exists() and "inventory_sha256=" not in capsys.readouterr().out
    else:
        module.main()
        value = json.loads(output.read_text(encoding="utf-8"))
        assert value == module.inventory(root) and "inventory_sha256=" in capsys.readouterr().out
