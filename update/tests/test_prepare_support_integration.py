"""Preparation integrates advisory support without engine or store side effects."""
from copy import deepcopy
import json
from pathlib import Path
import socket
import subprocess
import tomllib

import pytest

from guard import application
from guard.adapters.vllm import CHAT_REVIEWED_IMAGE
from guard.cli import app
from tests.builders import policy
from tests.test_managed_gpu_launch import setup_inputs
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


def _managed_update_inputs(tmp_path):
    pack, env, _, base = setup_inputs(tmp_path)
    cfg = tomllib.loads((ROOT/"examples/gpu-bounded-binding-logs.toml").read_text(encoding="utf-8"))
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
    return pack, [env, candidate], pol, cfg


def test_update_support_is_side_specific_and_does_not_remove_other_checks(tmp_path, monkeypatch):
    pack, envs, pol, cfg = _managed_update_inputs(tmp_path)
    calls = _local(monkeypatch)
    data = application.update_readiness(pack, envs, pol, cfg)
    assert data["ready"] is True, data
    rows = {(row["side"], row["check_id"]): row for row in data["check_support"]}
    assert set(rows) == {(side, check) for side in ("baseline", "candidate") for check in ("REQ-09", "REQ-11")}
    assert rows["baseline", "REQ-11"]["status"] == "SUPPORTED"
    assert rows["candidate", "REQ-11"]["status"] == "UNSUPPORTED"
    assert rows["candidate", "REQ-09"]["status"] == "SUPPORTED"
    assert "independent" in " ".join(rows["baseline", "REQ-11"]["limitations"])
    assert calls and set(calls) <= {"daemon-info", "daemon-resources", "image-info"}
    assert not list(tmp_path.rglob("guard.db")) and not list(tmp_path.rglob("target.json"))


@pytest.mark.parametrize("fail_support", [False, True])
def test_update_readiness_reports_independent_failures_for_both_sides(tmp_path, monkeypatch, fail_support):
    from guard import readiness_support
    from guard.preparation_guidance import annotate

    pack, envs, pol, cfg = _managed_update_inputs(tmp_path)
    _local(monkeypatch)
    cfg["sides"] = {
        "baseline": {"check_config": {"REQ-09": {"max_probe_tokens": 1}}},
        "candidate": {"check_config": {"REQ-11": {"request_timeout": 0}}},
    }
    assess = readiness_support.assess_check_support

    def assess_support(pack, env, *args, **kwargs):
        if fail_support and env is envs[0]:
            raise ValueError("EXAMPLE support evaluation unavailable")
        return assess(pack, env, *args, **kwargs)

    monkeypatch.setattr(readiness_support, "assess_check_support", assess_support)

    data = application.update_readiness(pack, envs, pol, cfg)

    assert data["ready"] is False
    failures = {row["check"]: row["detail"] for row in data["checks"] if not row["ok"]}
    expected = {
        "inputs:baseline/REQ-09": "max_probe_tokens must be an integer of at least two tokens",
        "inputs:candidate/REQ-11": "request_timeout must be finite, positive and at most 120 seconds",
    }
    identities = {("baseline", "REQ-09"), ("candidate", "REQ-11")}
    expected_sides = {"baseline", "candidate"}
    if fail_support:
        expected["inputs:baseline"] = "EXAMPLE support evaluation unavailable"
        identities.add(("baseline", None))
        expected_sides.remove("baseline")
    assert failures == {**expected, "inputs": f"{len(expected)} preparation input errors are listed below"}
    assert {(issue["side"], issue["check_id"]) for issue in annotate(data)["guidance"]["issues"]
            if issue["blocking"]} == identities
    assert {(row["side"], row["check_id"]) for row in data["check_support"]} == {
        (side, check) for side in expected_sides for check in ("REQ-09", "REQ-11")
    }


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


@pytest.mark.parametrize("override, reason, supported", [
    ({"max_probe_tokens": 64}, None, False),
    ({"max_probe_tokens": 1}, "max_probe_tokens must be an integer of at least two tokens", False),
    ("malformed", "CHECK_CONFIG_INVALID", None),
])
def test_prepare_validation_uses_the_check_budget_for_support_and_admission(
        tmp_path, monkeypatch, override, reason, supported):
    pack, env, pol, cfg = setup_inputs(tmp_path)
    _local(monkeypatch)
    cfg["check_config"] = {"REQ-09": override}

    data = application.validation_readiness(pack, env, pol, cfg)

    support, = data["check_support"]
    assert support["policy_supported"] is supported
    inputs = next(row for row in data["checks"] if row["check"] == "inputs")
    assert inputs["ok"] is (reason is None)
    if reason is not None:
        assert inputs["detail"] == reason
    if supported is None:
        assert "CHECK_CONFIG_INVALID" in support["reasons"]


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
