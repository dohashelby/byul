"""Preparation integrates advisory support without engine or store side effects."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import tomllib

from guard import application
from guard.adapters.vllm import CHAT_REVIEWED_IMAGE
from guard.cli import app
from tests.builders import policy
from tests.test_windows_cpu_launch import setup_inputs
from typer.testing import CliRunner

ROOT = Path(__file__).resolve().parents[1]


def _local(monkeypatch):
    calls = []
    class Local:
        def __init__(self, *args): pass
        def daemon_info(self, **kwargs):
            calls.append("daemon-info")
            return {"os": "linux", "architecture": "amd64"}
        def daemon_resources(self, **kwargs):
            calls.append("daemon-resources")
            return {"memory_bytes": 16 * 1024**3, "cpus": 4}
        def image_info(self, image, **kwargs):
            calls.append("image-info")
            return {"os": "linux", "architecture": "amd64",
                    "digests": [image if "@" in image else image+"@sha256:"+"c"*64],
                    "entrypoint": ["vllm", "serve"]}
    def prohibited(*args, **kwargs):
        raise AssertionError("prepare must not start a process, engine or network")
    monkeypatch.setattr(application, "Docker", Local)
    monkeypatch.setattr(subprocess, "run", prohibited)
    monkeypatch.setattr(subprocess, "Popen", prohibited)
    monkeypatch.setattr(socket, "create_connection", prohibited)
    monkeypatch.setattr(application.sandbox, "up", prohibited)
    return calls


def test_update_support_is_side_specific_and_does_not_remove_other_checks(tmp_path, monkeypatch):
    pack, env, _, base = setup_inputs(tmp_path)
    cfg = tomllib.loads((ROOT/"examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    cfg["vllm"]["models_dir"] = base["vllm"]["models_dir"]
    cfg.pop("validation", None)
    cfg["update"] = {"mode": "managed"}
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": CHAT_REVIEWED_IMAGE})}).seal()
    candidate = env.model_copy(update={"id": "ENV-CANDIDATE",
        "engine": env.engine.model_copy(update={"version": "0.32.0",
            "image_digest": "cpu@sha256:"+"b"*64})}).seal()
    pol = policy(pack.ref(), scope={"include": ["REQ-09", "REQ-11"]}, values={
        "REQ-09": {"max_input_tokens": 127, "rejection_codes": ["400", "422"],
                   "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]},
        "REQ-11": {"result_binding": "originating_connection", "client_request_id_trust": "untrusted_correlation"}})
    calls = _local(monkeypatch)
    data = application.update_readiness(pack, [env, candidate], pol, cfg)
    assert data["ready"] is True, data
    rows = {(row["side"], row["check_id"]): row for row in data["check_support"]}
    assert set(rows) == {(side, check) for side in ("baseline", "candidate") for check in ("REQ-09", "REQ-11")}
    assert rows["baseline", "REQ-11"]["status"] == "SUPPORTED"
    assert rows["candidate", "REQ-11"]["status"] == "UNSUPPORTED"
    assert rows["candidate", "REQ-09"]["status"] == "SUPPORTED"
    assert "independent" in " ".join(rows["baseline", "REQ-11"]["limitations"])
    assert calls and set(calls) <= {"daemon-info", "daemon-resources", "image-info"}
    assert not list(tmp_path.rglob("guard.db")) and not list(tmp_path.rglob("target.json"))


def test_valid_unsupported_policy_is_advisory_but_invalid_launch_budget_still_fails(tmp_path, monkeypatch):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    _local(monkeypatch)
    vals = deepcopy(pol.values)
    vals["REQ-09"]["allowed_input_formats"] = ["text"]
    pol = pol.model_copy(update={"values": vals}).seal()
    before = deepcopy(pol.to_dict())
    data = application.validation_readiness(pack, env, pol, cfg)
    assert data["ready"] is True, data
    row, = data["check_support"]
    assert row["side"] == "single" and row["status"] == "UNSUPPORTED"
    assert row["policy_valid"] is True and row["policy_supported"] is False
    assert pol.to_dict() == before
    bad = deepcopy(cfg)
    bad["input_validation"]["max_probe_tokens"] = 1
    data = application.validation_readiness(pack, env, pol, bad)
    assert data["ready"] is False
    assert any(row["check"] == "inputs" and not row["ok"] for row in data["checks"])


def test_prepare_cli_exposes_support_without_creating_store(tmp_path, monkeypatch):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    import yaml
    _local(monkeypatch)
    env_path, pol_path = tmp_path/"env.yaml", tmp_path/"policy.yaml"
    env_path.write_text(yaml.safe_dump(env.to_dict()), encoding="utf-8")
    pol_path.write_text(yaml.safe_dump(pol.to_dict()), encoding="utf-8")
    # Inject only parsed config; exercise the real CLI, loaders and preparation.
    monkeypatch.setattr("guard.cli._config", lambda _: cfg)
    result = CliRunner().invoke(app, ["prepare-validation", "--pack", str(pack.path),
        "--env", str(env_path), "--policy", str(pol_path)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["ready"] is True and data["check_support"][0]["check_id"] == "REQ-09"
    assert not list(tmp_path.rglob("target.json")) and not list(tmp_path.rglob("guard.db"))
