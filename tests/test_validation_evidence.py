"""Read-only saved-evidence contracts; all records are synthetic EXAMPLE fixtures."""
from copy import deepcopy
import json
import sqlite3
import stat
import subprocess
import zipfile

import pytest

from guard.core.select import select
from guard.models import Evidence, canonical_json, load_pack
from guard.store import Store
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from guard.validation_evidence import (
    file_sha256, inventory_tree, read_objects, verify_saved_bundle, verify_store_files,
)
from tests.builders import PACK_DIR, T0, environment, evidence, policy


@pytest.fixture
def saved(tmp_path):
    store = Store(tmp_path / "source")
    pack_ref = put_pack(store, PACK_DIR)
    env = environment({"gateway": {"value": True, "origin": "EXAMPLE", "source": "unit fixture"}})
    pol = policy(pack_ref, scope={"include": ["REQ-01"]})
    store.put_many([env, pol])
    plan = select(load_pack(PACK_DIR), [env], pol)
    store.put(plan)
    artifact = store.put_artifact(b"EXAMPLE captured bytes\r\n")
    raw = evidence(artifacts=[{"name": "EXAMPLE", "path": artifact.path, "sha256": artifact.sha256,
                               "bytes": artifact.bytes, "collected_at": T0, "collector": "unit-fixture"}]).model_dump()
    evd = Evidence(**{**raw, "plan": plan.ref(), "environment": env.ref(),
                      "refs": [plan.ref(), env.ref()]}).seal()
    store.put(evd)
    return store.root, plan, evd


def _state(folder):
    files = inventory_tree(folder)
    mtimes = {path.relative_to(folder).as_posix(): path.stat().st_mtime_ns
              for path in folder.rglob("*") if path.is_file()}
    return files, mtimes


def _forbid_writes_and_processes(monkeypatch):
    monkeypatch.setattr(Store, "__init__", lambda *_a, **_k: pytest.fail("analysis must not construct Store"))
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("analysis must not launch a process"))


def test_reads_original_pack_and_artifact_without_writes_or_database_sidecars(saved, monkeypatch):
    root, plan, evd = saved
    before = _state(root)
    _forbid_writes_and_processes(monkeypatch)
    objects = read_objects(root)
    pack = verify_store_files(root, objects)
    assert pack.ref() == plan.inputs.pack
    assert evd.to_dict() in objects
    assert file_sha256(root / evd.artifacts[0].path) == evd.artifacts[0].sha256
    assert _state(root) == before
    assert not any(root.glob("store.sqlite-*"))


@pytest.mark.parametrize("suffix", ["-wal", "-journal"])
def test_uncheckpointed_database_is_rejected_without_ignoring_or_repairing_it(saved, suffix):
    root, _, _ = saved
    pending = root / ("store.sqlite" + suffix)
    pending.write_bytes(b"pending data must not be ignored")
    before = _state(root)
    with pytest.raises(ValueError, match="close/checkpoint"):
        read_objects(root)
    assert _state(root) == before


def test_missing_database_does_not_create_store_or_artifact_directory(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    with pytest.raises(ValueError, match="not created"):
        read_objects(root)
    assert list(root.iterdir()) == []


def test_database_index_must_match_stored_object_identity(saved):
    root, _, _ = saved
    with sqlite3.connect(root / "store.sqlite") as con:
        con.execute("DROP TRIGGER objects_no_update")
        con.execute("UPDATE objects SET content_sha256=? WHERE kind='ENV'", ("0" * 64,))
    before = _state(root)
    with pytest.raises(ValueError, match="index and stored object identity"):
        read_objects(root)
    assert _state(root) == before


@pytest.mark.parametrize("damage", ["changed-artifact", "missing-artifact", "noncanonical-path", "wrong-size",
                                   "changed-pack", "missing-pack", "unaddressed-file", "extra-plan"])
def test_saved_artifact_and_fixed_pack_damage_is_rejected_without_repair(saved, damage):
    root, plan, evd = saved
    objects = read_objects(root)
    if damage == "changed-artifact":
        (root / evd.artifacts[0].path).write_bytes(b"changed")
    elif damage == "missing-artifact":
        (root / evd.artifacts[0].path).unlink()
    elif damage == "changed-pack":
        (root / "artifacts" / plan.inputs.pack.sha256).write_bytes(b"changed")
    elif damage == "missing-pack":
        (root / "artifacts" / plan.inputs.pack.sha256).unlink()
    elif damage == "unaddressed-file":
        (root / "artifacts" / "unexpected").write_bytes(b"not content addressed")
    elif damage == "extra-plan":
        objects.append(deepcopy(next(obj for obj in objects if obj["kind"] == "PLAN")))
    else:
        item = next(obj for obj in objects if obj["kind"] == "EVD")["artifacts"][0]
        item["path" if damage == "noncanonical-path" else "bytes"] = "../outside" if damage == "noncanonical-path" else 0
    before = _state(root)
    with pytest.raises((OSError, ValueError, LookupError)):
        verify_store_files(root, objects)
    assert _state(root) == before


@pytest.fixture
def portable(saved, tmp_path):
    root, _, _ = saved
    bundle, restored = tmp_path / "evidence.zip", tmp_path / "restored"
    export_bundle(root, bundle)
    restore_bundle(bundle, restored)
    return bundle, restored


def test_bundle_matches_exact_restore_and_does_not_modify_any_bytes(portable, monkeypatch):
    bundle, restored = portable
    before = _state(bundle.parent)
    _forbid_writes_and_processes(monkeypatch)
    verify_saved_bundle(bundle, restored)
    assert read_objects(restored)
    assert _state(bundle.parent) == before


@pytest.mark.parametrize("damage", ["restored-bytes", "restored-extra", "bad-checksum", "missing-db",
                                   "invalid-size", "unexpected-path", "archive-link"])
def test_bundle_or_restore_damage_is_rejected_without_repair(portable, damage):
    bundle, restored = portable
    if damage == "restored-bytes":
        next((restored / "artifacts").iterdir()).write_bytes(b"corrupt")
    elif damage == "restored-extra":
        (restored / "not-in-bundle.txt").write_bytes(b"unarchived")
    else:
        with zipfile.ZipFile(bundle) as archive:
            entries = {name: archive.read(name) for name in archive.namelist()}
        manifest = json.loads(entries["manifest.json"])
        name = "store.sqlite"
        if damage == "bad-checksum":
            manifest["files"][name]["sha256"] = "0" * 64
        elif damage == "missing-db":
            manifest["files"].pop(name)
            entries.pop(name)
        elif damage == "invalid-size":
            manifest["files"][name]["bytes"] = True
        elif damage == "unexpected-path":
            entries["../outside"] = b"no"
            manifest["files"]["../outside"] = {"bytes": 2, "sha256": "0" * 64}
        entries["manifest.json"] = canonical_json(manifest)
        with zipfile.ZipFile(bundle, "w") as archive:
            for entry, data in entries.items():
                if damage == "archive-link" and entry == name:
                    info = zipfile.ZipInfo(entry)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(info, data)
                else:
                    archive.writestr(entry, data)
    before = _state(bundle.parent)
    with pytest.raises(ValueError):
        verify_saved_bundle(bundle, restored)
    assert _state(bundle.parent) == before
    assert not (bundle.parent / "outside").exists()


def test_directory_links_are_rejected_before_following_outside_evidence(saved, tmp_path):
    root, _, _ = saved
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks requires host permission")
    with pytest.raises(ValueError, match="symlinks or reparse"):
        read_objects(linked)
    with pytest.raises(ValueError, match="symlinks or reparse"):
        inventory_tree(linked)
