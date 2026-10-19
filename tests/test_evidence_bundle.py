"""Portable evidence contracts: snapshots, byte preservation and rejected corruption."""
import json
from datetime import timedelta
from pathlib import Path
import sqlite3
import zipfile

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.models import sha256_hex
from guard.store import Store, IntegrityError
from guard.store.bundle import export_bundle, restore_bundle
from guard.store.packs import put_pack
from tests.builders import PACK_DIR, T0
from tests.report_setup import pipeline


@pytest.fixture
def source(tmp_path):
    store, pack, plan = pipeline(tmp_path, include=["REQ-01"], exceptions=[{
        "check_id":"REQ-01", "reason":"synthetic expiry fixture", "approved_by":"test-fixture",
        "approved_at":T0, "expires_at":T0 + timedelta(days=1)}])
    put_pack(store, PACK_DIR)
    (store.root / "work").mkdir(exist_ok=True)
    (store.root / "work" / "secret.env").write_text("DO_NOT_EXPORT=secret", encoding="utf-8")
    return store, plan


def test_export_restore_and_offline_rejudge(source, tmp_path):
    store, plan = source
    before = sha256_hex(store.db_path.read_bytes())
    output = tmp_path / "evidence.zip"
    manifest = export_bundle(store.root, output)
    assert sha256_hex(store.db_path.read_bytes()) == before
    assert not any("work" in name or ".env" in name for name in manifest["files"])
    destination = tmp_path / "restored"
    restore_bundle(output, destination)
    restored = Store(destination)
    assert restored.verify() == []
    for name in manifest["files"]:
        if name != "store.sqlite":
            assert (store.root / name).read_bytes() == (destination / name).read_bytes()
    # SQLite backup may change physical database header counters. Logical object
    # payloads/hashes/revisions and artifact bytes must remain exactly equivalent.
    for kind in ("ENV", "POL", "CHG", "PLAN", "EVD", "RES", "RPT"):
        assert [o.to_dict() for o in store.iter_kind(kind)] == [o.to_dict() for o in restored.iter_kind(kind)]
    old_revisions = {r.id: restored.revisions(r.id) for r in restored.iter_kind("RES")}
    runner = CliRunner()
    result = runner.invoke(app, ["judge", "--plan", plan.id, "--store", str(destination),
                                 "--pack", str(tmp_path / "absent-pack"), "--as-of", T0.isoformat()])
    assert result.exit_code == 0, result.output + repr(result.exception)
    for ident, revisions in old_revisions.items():
        assert restored.revisions(ident) == revisions  # identical judgment is idempotent
    previous = {r.id: r.ref() for r in restored.iter_kind("RES")}
    result = runner.invoke(app, ["judge", "--plan", plan.id, "--store", str(destination),
        "--pack", str(tmp_path / "absent-pack"), "--as-of", (T0 + timedelta(days=2)).isoformat()])
    assert result.exit_code == 0, result.output + repr(result.exception)
    for ident, revisions in old_revisions.items():
        assert restored.revisions(ident) == [*revisions, revisions[-1] + 1]
        assert restored.resolve(previous[ident]).ref() == previous[ident]
    result = runner.invoke(app, ["report", "--plan", plan.id, "--store", str(destination),
        "--pack", str(tmp_path / "absent-pack"), "--json", str(tmp_path / "report.json")])
    assert result.exit_code == 0, result.output + repr(result.exception)
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["official"] is False
    assert restored.verify() == []


@pytest.mark.parametrize("damage", ["missing", "changed", "pack-missing"])
def test_export_rejects_incomplete_store(source, tmp_path, damage):
    store, plan = source
    evidence = next(store.iter_kind("EVD"))
    path = store.root / ("artifacts/" + plan.inputs.pack.sha256 if damage == "pack-missing" else evidence.artifacts[0].path)
    if damage == "changed":
        path.write_bytes(b"corrupt")
    else:
        path.unlink()
    output = tmp_path / "rejected.zip"
    with pytest.raises((ValueError, LookupError, IntegrityError)):
        export_bundle(store.root, output)
    assert not output.exists()


@pytest.mark.parametrize("attack", ["traversal", "duplicate", "checksum", "budget", "db-tamper"])
def test_restore_rejects_bad_archive_before_publication(source, tmp_path, attack):
    store, _ = source
    output = tmp_path / "original.zip"
    export_bundle(store.root, output)
    with zipfile.ZipFile(output) as src:
        entries = {name: src.read(name) for name in src.namelist()}
    if attack == "traversal":
        entries["../outside"] = b"no"
    elif attack == "checksum":
        name = next(n for n in entries if n.startswith("artifacts/"))
        entries[name] = b"x" * len(entries[name])
    elif attack == "db-tamper":
        # Matching transport checksum must not bypass the object/store verifier.
        entries["store.sqlite"] = b"not sqlite"
        manifest = json.loads(entries["manifest.json"])
        manifest["files"]["store.sqlite"] = {"sha256": sha256_hex(entries["store.sqlite"]), "bytes": 10}
        entries["manifest.json"] = json.dumps(manifest).encode()
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(changed, "w") as dst:
        for name, data in entries.items():
            dst.writestr(name, data)
        if attack == "duplicate":
            with pytest.warns(UserWarning):
                dst.writestr("manifest.json", entries["manifest.json"])
    destination = tmp_path / "rejected"
    with pytest.raises((IntegrityError, ValueError, sqlite3.DatabaseError)):
        restore_bundle(changed, destination, max_bytes=1 if attack == "budget" else 2 * 1024**3)
    assert not destination.exists() and not (tmp_path / "outside").exists()


def test_existing_outputs_are_preserved(source, tmp_path):
    store, _ = source
    output = tmp_path / "existing.zip"
    output.write_bytes(b"keep")
    with pytest.raises(ValueError):
        export_bundle(store.root, output)
    assert output.read_bytes() == b"keep"
    with pytest.raises(ValueError):
        restore_bundle(output, store.root)
