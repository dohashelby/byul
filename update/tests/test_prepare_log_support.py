"""OBS-03 preparation separates valid unsupported policy from malformed inputs."""
from copy import deepcopy
from pathlib import Path
import tomllib

import pytest
import yaml

from guard import application
from tests.builders import policy
from tests.test_prepare_support_integration import _local
from tests.test_windows_cpu_launch import setup_inputs


ROOT = Path(__file__).resolve().parents[1]


def inputs(tmp_path):
    pack, env, _, base = setup_inputs(tmp_path)
    cfg = tomllib.loads((ROOT / "examples/windows-cpu-binding-logs.toml").read_text(encoding="utf-8"))
    cfg["vllm"]["models_dir"] = base["vllm"]["models_dir"]
    raw = yaml.safe_load((ROOT / "examples/automation-binding-logs-policy.yaml").read_text(encoding="utf-8"))
    pol = policy(pack.ref(), scope={"include": ["OBS-03", "REQ-09"]},
                 values={key: raw["values"][key] for key in ("OBS-03", "REQ-09")})
    return pack, env, pol, cfg


@pytest.mark.parametrize(("key", "value"), [
    ("required_branches", ["normal", "error", "exception_dump", "audit"]),
    ("forbidden_marker_contexts", ["caller_input", "enterprise_context"]),
])
def test_prepare_log_unsupported_scope_is_advisory_and_keeps_other_checks(tmp_path, monkeypatch, key, value):
    pack, env, pol, cfg = inputs(tmp_path)
    calls = _local(monkeypatch)
    pol.values["OBS-03"][key] = value
    pol = pol.seal()
    before = deepcopy(pol.to_dict())
    data = application.validation_readiness(pack, env, pol, cfg)
    assert data["ready"] is True, data
    rows = {row["check_id"]: row for row in data["check_support"]}
    assert set(rows) == {"OBS-03", "REQ-09"}
    assert rows["OBS-03"]["status"] == "UNSUPPORTED"
    assert rows["OBS-03"]["policy_valid"] is True and rows["OBS-03"]["policy_supported"] is False
    assert rows["REQ-09"]["status"] == "SUPPORTED"
    assert pol.to_dict() == before
    assert set(calls) <= {"daemon-info", "daemon-resources", "image-info"}
    assert not list(tmp_path.rglob("guard.db")) and not list(tmp_path.rglob("target.json"))


def test_prepare_log_malformed_policy_is_an_input_error(tmp_path, monkeypatch):
    pack, env, pol, cfg = inputs(tmp_path)
    _local(monkeypatch)
    pol.values["OBS-03"]["required_branches"] = []
    pol = pol.seal()
    data = application.validation_readiness(pack, env, pol, cfg)
    assert data["ready"] is False
    row = next(row for row in data["check_support"] if row["check_id"] == "OBS-03")
    assert row["status"] == "UNKNOWN" and row["policy_valid"] is False
    check, = [row for row in data["checks"] if row["check"] == "inputs"]
    assert check["ok"] is False and "required_branches" in check["detail"]


@pytest.mark.parametrize(("settings", "detail"), [
    ({"request_timeout": 0}, "request_timeout"),
    ({"log_sources": []}, "log_sources"),
    ({"log_sources": [{"id": "engine", "container": "@engine", "kind": "docker_logs", "role": "copy"}]},
     "local engine source"),
])
def test_prepare_log_unsupported_policy_keeps_settings_and_inventory_errors(tmp_path, monkeypatch, settings, detail):
    pack, env, pol, cfg = inputs(tmp_path)
    _local(monkeypatch)
    pol.values["OBS-03"]["required_branches"].append("audit")
    pol = pol.seal()
    cfg["log_analysis"].update(settings)
    data = application.validation_readiness(pack, env, pol, cfg)
    assert data["ready"] is False
    rows = {row["check_id"]: row for row in data["check_support"]}
    assert set(rows) == {"OBS-03", "REQ-09"}
    assert rows["OBS-03"]["status"] == "UNSUPPORTED"
    assert rows["OBS-03"]["policy_valid"] is True and rows["OBS-03"]["policy_supported"] is False
    assert rows["REQ-09"]["status"] == "SUPPORTED"
    check, = [row for row in data["checks"] if row["check"] == "inputs"]
    assert check["ok"] is False and detail in check["detail"]
