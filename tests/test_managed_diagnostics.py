"""Managed diagnostics preserve lifecycle failures without becoming verdict evidence."""
from hashlib import sha256
import json

import pytest

from guard import application
from guard.executors import registry
from guard.executors.fake import FakeExecutor
from guard.models import utcnow
from guard.runtime import sandbox
from guard.runtime.docker import CmdResult, Docker, DockerError
from guard.store import Store
from tests.builders import policy
from tests.test_windows_cpu_launch import setup_inputs


def test_diagnostic_docker_reads_only_owned_state_and_bounded_tail():
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "inspect":
            return CmdResult(0, json.dumps({"owner": "diag-test", "state": {
                "Status": "exited", "Running": False, "OOMKilled": False, "ExitCode": 1,
                "Error": "authorization=private-error", "Pid": 123, "StartedAt": "private extra field"}}), "")
        return CmdResult(0, "tail", "")
    docker = Docker("diag-test", runner=runner)
    state = docker.diagnostic_state("engine", timeout=5)
    assert state == {"status": "exited", "running": False, "oom_killed": False, "exit_code": 1, "error_present": True}
    assert "private-error" not in json.dumps(state) and "Config.Env" not in calls[0][3]
    assert docker.diagnostic_logs("engine", timeout=5).stdout == "tail"
    assert calls[-1] == ["docker", "logs", "--timestamps", "--tail", "80", "guard-diag-test-engine"]
    with pytest.raises(ValueError):
        docker.diagnostic_state("other-container", timeout=5)


def test_diagnostic_owner_mismatch_prevents_log_collection(tmp_path):
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return CmdResult(0, json.dumps({"owner": "another-run", "state": {}}), "")
    saved = sandbox.capture_diagnostics(Docker("diag-test", runner=runner), tmp_path,
                                        secret_values=[], phase="startup-failure")
    doc = json.loads((tmp_path / saved["path"]).read_text(encoding="utf-8"))
    assert all(row == {"state_error_type": "DockerError"} for row in doc["containers"].values())
    assert len(calls) == 2 and all(argv[1] == "inspect" for argv in calls)


def test_diagnostic_redacts_before_byte_cap_keeps_sha_and_never_overwrites_startup_capture(tmp_path):
    known = "gateway-private-key"
    class Local:
        def diagnostic_state(self, role, **kwargs):
            return {"status": "exited", "running": False, "oom_killed": False, "exit_code": 1, "error_present": False}
        def diagnostic_logs(self, role, **kwargs):
            if role == "gw": return CmdResult(7, "", "private daemon error")
            return CmdResult(0, "x" * 40000 + "\nAuthorization: Bearer labelled-private\n" + known + "\n"
                                + '{"api_key":"truncated-private', "")
    saved = sandbox.capture_diagnostics(Local(), tmp_path, secret_values=[known], phase="startup-failure")
    data = (tmp_path / saved["path"]).read_bytes()
    text = data.decode("utf-8")
    doc = json.loads(text)
    assert saved["sha256"] == sha256(data).hexdigest()
    assert saved["sha256"] in (tmp_path / "managed-diagnostic.json.sha256").read_text(encoding="ascii")
    assert doc["phase"] == "startup-failure" and doc["containers"]["engine"]["logs_truncated"]
    assert len(doc["containers"]["engine"]["logs"].encode("utf-8")) <= 32768
    assert all(secret not in text for secret in [known, "labelled-private", "truncated-private", "private daemon error"])
    assert doc["containers"]["gw"]["logs_error_type"] == "DockerCommandFailure"
    assert "logs" not in doc["containers"]["gw"]
    class NoFurtherReads:
        def diagnostic_state(self, *args, **kwargs): raise AssertionError("capture must be preserved")
    assert sandbox.capture_diagnostics(NoFurtherReads(), tmp_path, secret_values=[], phase="run-final") == saved
    assert (tmp_path / saved["path"]).read_bytes() == data


def test_startup_capture_retains_original_exception_and_only_public_failure_type(tmp_path, monkeypatch):
    original = DockerError("private daemon startup error")
    class Local:
        def diagnostic_state(self, *args, **kwargs): raise DockerError("private diagnostic error")
    def fails(*args, **kwargs):
        tmp_path.mkdir(exist_ok=True)
        raise original
    monkeypatch.setattr(sandbox, "_up", fails)
    with pytest.raises(DockerError) as caught:
        sandbox.up(Local(), "isolated", tmp_path, "private gateway key")
    assert caught.value is original
    saved = (tmp_path / "managed-diagnostic.json").read_text(encoding="utf-8")
    assert "DockerError" in saved and "private" not in saved


@pytest.mark.parametrize("diagnostic_fails", [False, True])
def test_application_captures_before_cleanup_and_diagnostic_failure_keeps_completed_violation(tmp_path, monkeypatch, diagnostic_fails):
    pack, env, _, cfg = setup_inputs(tmp_path)
    cfg["executors"] = {"direct_route": "real"}
    pol = policy(pack.ref(), scope={"include": ["REQ-01"]}, values={"REQ-01": {"allowed_callers": ["allowed"]}})
    events = []
    class Local:
        def __init__(self, run_id): self.run_id = run_id
        def __enter__(self): return self
        def __exit__(self, *args): events.append("cleanup")
    def up(docker, topology, folder, *args, **kwargs):
        folder.mkdir()
        return {"observed_version": env.engine.version, "images": {env.engine.image_digest: env.engine.image_digest}}
    def capture(*args, **kwargs):
        events.append("capture")
        if diagnostic_fails: raise DockerError("private diagnostic error")
        return {"path": "managed-diagnostic.json", "sha256": "a" * 64}
    monkeypatch.setattr(application, "Docker", Local)
    monkeypatch.setattr(sandbox, "up", up)
    monkeypatch.setattr(sandbox, "capture_diagnostics", capture)
    monkeypatch.setitem(registry.EXECUTORS, "direct_route:real", lambda raw: FakeExecutor("direct_200"))
    store = Store(tmp_path / "store")
    report, failed = application.execute_validation(store, pack, env, pol, cfg, tmp_path / "run", utcnow())
    assert events == ["capture", "cleanup"]
    assert failed is False
    evidence, = list(store.iter_kind("EVD"))
    result, = list(store.iter_kind("RES"))
    assert evidence.execution.status.value == "COMPLETED" and result.verdicts.security.value == "FAIL"
    assert "private diagnostic error" not in json.dumps(report.to_dict())
    assert any("separate run diagnostic" in row for row in report.limitations)
    assert not store.verify()
