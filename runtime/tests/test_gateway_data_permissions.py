"""Explicit offline migration, with no automatic permission changes."""
import os
import stat

import pytest

from tools.migrate_gateway_data_permissions import main


def test_audit_names_only_then_explicit_fix_preserves_contents(tmp_path, capsys):
    path = tmp_path / "observations.jsonl"
    secret = b'{"private":"never print contents"}\n'
    path.write_bytes(secret)
    path.chmod(0o644)
    private = tmp_path / "gateway_records.jsonl"
    private.write_bytes(b"UNCHANGED")
    private.chmod(0o600)
    receipt = tmp_path / "tetragon-health-receipts.json"
    receipt.write_bytes(b"RECEIPT")
    receipt.chmod(0o640)
    args = ["--data-dir", str(tmp_path), "--owner-uid", str(os.geteuid())]
    assert main(args) == 1
    assert capsys.readouterr().out == 'excess_permissions: "observations.jsonl"\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert main([*args, "--fix-permissions"]) == 0
    assert capsys.readouterr().out == 'fixed: "observations.jsonl"\n'
    assert path.read_bytes() == secret and private.read_bytes() == b"UNCHANGED"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o640
    assert main(args) == 0 and capsys.readouterr().out == ""


@pytest.mark.parametrize("kind", ["symlink", "fifo", "directory", "hardlink", "owner"])
def test_fix_refuses_unsafe_entries(tmp_path, capsys, kind):
    path = tmp_path / "unsafe.jsonl"
    target = tmp_path / "target"
    target.write_bytes(b"UNCHANGED")
    target.chmod(0o644)
    uid = os.geteuid()
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path, 0o644)
    elif kind == "directory":
        path.mkdir()
    elif kind == "hardlink":
        os.link(target, path)
    else:
        path.write_bytes(b"UNCHANGED")
        uid += 1
        # Parent-owner validation must also refuse the wrong Gateway UID.
    assert main(["--data-dir", str(tmp_path), "--owner-uid", str(uid), "--fix-permissions"]) == (2 if kind == "owner" else 1)
    output = capsys.readouterr()
    assert str(tmp_path) not in output.out + output.err
    assert "UNCHANGED" not in output.out + output.err
    assert target.read_bytes() == b"UNCHANGED"
    assert stat.S_IMODE(target.stat().st_mode) == 0o644


def test_fix_refuses_writable_directory(tmp_path, capsys):
    path = tmp_path / "old.jsonl"
    path.write_bytes(b"UNCHANGED")
    path.chmod(0o644)
    tmp_path.chmod(0o777)
    assert main(["--data-dir", str(tmp_path), "--owner-uid", str(os.geteuid()), "--fix-permissions"]) == 2
    assert "data_directory_unavailable" in capsys.readouterr().err
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
