"""Token issuance never emits plaintext or replaces an existing filesystem entry."""
import hashlib
import json
import os
import subprocess
import sys

import pytest

from byul_platform.config import Settings
from byul_platform.operation_tokens import main
from byul_platform.operations import authenticate, load_tokens


@pytest.mark.parametrize("role", ["viewer", "runtime_operator", "approver", "agent"])
def test_issue_private_token_and_authenticate(tmp_path, capsys, role):
    path = tmp_path / "token"
    args = ["new", "--id", "role-01", "--role", role, "--token-file", str(path)]
    if role == "agent":
        args += ["--server", "server-01"]
    previous = os.umask(0)
    try:
        assert main(args) == 0
    finally:
        os.umask(previous)
    captured = capsys.readouterr()
    token = path.read_text().strip()
    assert len(token) >= 43
    assert path.stat().st_mode & 0o777 == 0o600
    assert token not in captured.out + captured.err
    assert captured.err == ""
    entry = json.loads(captured.out)
    assert entry["sha256"] == hashlib.sha256(token.encode()).hexdigest()
    assert set(entry) == {"id", "role", "sha256"} | ({"server"} if role == "agent" else set())
    settings = Settings(operation_tokens=load_tokens(json.dumps([entry])))
    assert authenticate(settings, "Bearer " + token) == {k: v for k, v in entry.items() if k != "sha256"}


@pytest.mark.parametrize("kind", ["file", "symlink", "dangling", "directory"])
def test_refuse_existing_entries(tmp_path, capsys, kind):
    path, target = tmp_path / "token", tmp_path / "target"
    target.write_text("test-only-existing-value")
    if kind == "file":
        path.write_text("test-only-existing-value")
    elif kind == "directory":
        path.mkdir()
    else:
        path.symlink_to(target if kind == "symlink" else tmp_path / "missing")
    assert main(["new", "--id", "viewer-01", "--role", "viewer", "--token-file", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "test-only-existing-value" not in captured.err
    assert target.read_text() == "test-only-existing-value"
    if kind == "file":
        assert path.read_text() == "test-only-existing-value"
    elif kind in {"symlink", "dangling"}:
        assert path.is_symlink()


@pytest.mark.parametrize("args", [
    ["--id", "bad/id", "--role", "viewer"],
    ["--id", "agent-01", "--role", "agent"],
    ["--id", "agent-01", "--role", "agent", "--server", "bad/server"],
    ["--id", "viewer-01", "--role", "viewer", "--server", "server-01"],
])
def test_invalid_config_creates_no_token(tmp_path, capsys, args):
    path = tmp_path / "token"
    assert main(["new", *args, "--token-file", str(path)]) == 1
    assert not path.exists()
    assert capsys.readouterr().out == ""


def test_module_cli_and_production_defaults(tmp_path, monkeypatch):
    path = tmp_path / "token"
    result = subprocess.run([sys.executable, "-m", "byul_platform.operation_tokens", "new",
        "--id", "viewer-01", "--role", "viewer", "--token-file", str(path)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert json.loads(result.stdout)["role"] == "viewer"
    assert path.read_text().strip() not in result.stdout + result.stderr
    for key in ("BYUL_OPERATION_MONITOR_SECONDS", "BYUL_OPERATION_LAB_SELF_APPROVAL"):
        monkeypatch.delenv(key, raising=False)
    assert Settings().operation_monitor_seconds == Settings.from_env().operation_monitor_seconds == 86400
    assert Settings().operation_lab_self_approval is Settings.from_env().operation_lab_self_approval is False


def test_write_failure_emits_no_hash_or_token(tmp_path, monkeypatch, capsys):
    path = tmp_path / "token"
    def fail_sync(fd):
        raise OSError("test-only-write-failure")
    monkeypatch.setattr(os, "fsync", fail_sync)
    assert main(["new", "--id", "viewer-01", "--role", "viewer", "--token-file", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_text().strip() not in captured.err
