"""Portable evidence contracts: snapshots, byte preservation and rejected corruption."""
import json
from datetime import timedelta
from pathlib import Path
import sqlite3
import zipfile

import pytest
from typer.testing import CliRunner

from guard.cli import app
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.models import load_pack, sha256_hex
from guard.models.objects import Artifact
from guard.store import Store, IntegrityError
from guard.store import bundle as bundle_module
from guard.store.bundle import export_bundle, restore_bundle, verify_complete
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
    restored = Store.read_only(destination)
    assert restored.verify() == []
    with zipfile.ZipFile(output) as archive:
        for name in manifest["files"]:
            assert archive.read(name) == (destination / name).read_bytes()
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


@pytest.mark.parametrize("defect", ["missing_objects", "missing_trigger"])
def test_restore_never_repairs_database_after_checking_bundle(source, tmp_path, defect):
    store, _ = source
    archive = tmp_path / "broken.zip"
    database = tmp_path / "foreign.sqlite"
    with sqlite3.connect(database) as con:
        if defect == "missing_objects":
            con.execute("CREATE TABLE unrelated (note TEXT)")
        else:
            with sqlite3.connect(store.db_path) as orig:
                orig.backup(con)
            con.execute("DROP TRIGGER objects_no_delete")
    original = database.read_bytes()
    manifest = {"format": "guard-store-bundle/1", "files": {
        "store.sqlite": {"sha256": sha256_hex(original), "bytes": len(original)},
    }}
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("store.sqlite", original)
    destination = tmp_path / "restored"
    with pytest.raises(IntegrityError, match="schema or append-only triggers"):
        restore_bundle(archive, destination)
    assert not destination.exists()
    assert database.read_bytes() == original


def test_export_failure_does_not_publish_partial_bundle(source, tmp_path, monkeypatch):
    store, _ = source
    output = tmp_path / "evidence.zip"

    def write_failed(*args, **kwargs):
        raise OSError("synthetic zip write failure")

    with monkeypatch.context() as patch:
        patch.setattr(zipfile.ZipFile, "write", write_failed)
        with pytest.raises(OSError, match="synthetic zip write failure"):
            export_bundle(store.root, output)
    assert not output.exists()
    assert not list(tmp_path.glob(".guard-export-*"))
    assert export_bundle(store.root, output)["files"]


def test_restore_does_not_replace_directory_created_during_verification(source, tmp_path, monkeypatch):
    store, _ = source
    archive = tmp_path / "evidence.zip"
    destination = tmp_path / "restored"
    export_bundle(store.root, archive)
    publish = bundle_module._publish_directory

    def competing_writer(staged, path):
        path.mkdir()
        publish(staged, path)

    monkeypatch.setattr(bundle_module, "_publish_directory", competing_writer)
    with pytest.raises(FileExistsError):
        restore_bundle(archive, destination)
    assert not list(destination.iterdir())


def test_diagnostic_attachment_is_required_without_becoming_evidence(source):
    store, plan = source
    blob = store.put_artifact(b"separate diagnostic")
    report = build_report(store, load_pack(PACK_DIR), plan, T0)
    attachment = Artifact(name="diagnostic.json", path=blob.path, sha256=blob.sha256,
                          bytes=blob.bytes, collected_at=T0, collector="test")
    report = report.model_copy(update={"diagnostic_artifacts": [attachment]}).seal()
    store.put(report)
    verify_complete(Store.read_only(store.root))
    (store.root / blob.path).unlink()
    with pytest.raises(IntegrityError, match="artifact .* missing or changed"):
        verify_complete(Store.read_only(store.root))


def test_report_export_copies_exact_closure_and_required_history_only(source, tmp_path):
    store, plan = source
    pack = load_pack(PACK_DIR)
    old_evidence = next(store.iter_kind("EVD"))
    evidence = old_evidence.model_copy(update={"revision": old_evidence.revision + 1}).seal()
    store.put(evidence)
    judgment = judge_plan(store, pack, plan, T0)[0]
    assert judgment.revision == 2

    def with_diagnostic(report, data):
        blob = store.put_artifact(data)
        attachment = Artifact(name="diagnostic.json", path=blob.path, sha256=blob.sha256,
                              bytes=blob.bytes, collected_at=T0, collector="test")
        report = report.model_copy(update={"diagnostic_artifacts": [attachment]}).seal()
        store.put(report)
        return report, attachment.path

    wanted, wanted_diagnostics = with_diagnostic(build_report(store, pack, plan, T0), b"selected run")
    other_plan = plan.model_copy(update={"id": "PLAN-UNRELATED"}).seal()
    store.put(other_plan)
    _, other_diagnostics = with_diagnostic(build_report(store, pack, other_plan, T0), b"other run")
    orphan = store.put_artifact(b"no report refers here")
    source_before = store.db_path.read_bytes()

    archive = tmp_path / "one-report.zip"
    manifest = export_bundle(store.root, archive, report_ref=wanted.ref())
    assert wanted_diagnostics in manifest["files"]
    assert other_diagnostics not in manifest["files"] and orphan.path not in manifest["files"]
    assert store.db_path.read_bytes() == source_before
    destination = tmp_path / "scoped-store"
    restore_bundle(archive, destination)
    restored = Store.read_only(destination)
    assert [record.ref() for record in restored.iter_kind("RPT")] == [wanted.ref()]
    assert [record.ref() for record in restored.iter_kind("PLAN")] == [plan.ref()]
    assert restored.resolve(old_evidence.ref()) == old_evidence
    assert restored.resolve(evidence.ref()) == evidence
    assert restored.revisions(judgment.id) == [1, 2]
    verify_complete(restored)

    bad_archive = tmp_path / "bad-report.zip"
    wrong_hash = wanted.ref().model_copy(update={"sha256": "0" * 64})
    with pytest.raises(IntegrityError, match="ref identity or sha256 mismatch"):
        export_bundle(store.root, bad_archive, report_ref=wrong_hash)
    assert not bad_archive.exists()
    full = export_bundle(store.root, tmp_path / "whole-store.zip")
    assert other_diagnostics in full["files"] and orphan.path in full["files"]
