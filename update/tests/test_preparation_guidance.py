"""Preparation advice is actionable, bounded in scope and never an engine trial."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from guard import cli, update_validation
from guard.preparation_guidance import annotate, contract_issue, input_failure, read_inputs


@pytest.mark.parametrize("check,code,side", [
    ("docker", "DOCKER_UNAVAILABLE", None),
    ("image:PRIVATE-IMAGE", "LOCAL_IMAGE_REQUIREMENTS", None),
    ("entrypoint:PRIVATE-IMAGE", "LOCAL_IMAGE_REQUIREMENTS", None),
    ("image-pair", "IMAGE_PAIR", None),
    ("hardware:baseline", "HARDWARE_PROFILE", "baseline"),
    ("resources:candidate", "DOCKER_CAPACITY_REQUIREMENTS", "candidate"),
    ("resources", "DOCKER_CAPACITY_REQUIREMENTS", None),
])
def test_existing_failure_has_safe_cause_action_and_identity(check, code, side):
    data = {"ready": False, "checks": [{"check": check, "ok": False, "detail": "PRIVATE-SECRET"}], "check_support": []}
    before = deepcopy(data)
    result = annotate(data)
    issue = result["guidance"]["issues"][0]
    assert issue["code"] == code and issue["side"] == side
    assert issue["cause"] and issue["action"]
    assert "PRIVATE" not in json.dumps(result["guidance"])
    assert data == before and result["checks"] == data["checks"] and not result["ready"]


@pytest.mark.parametrize("message,code", [
    ("model inventory differs from ENV", "MODEL_INVENTORY"),
    ("model snapshot is missing", "MODEL_SNAPSHOT"),
    ("two distinct image digests are required", "IMAGE_PAIR"),
    ("explicit Linux CPU profile", "CPU_PROFILE"),
    ("meaningful side execution conditions differ", "COMPARISON_CONDITIONS"),
    ("scope must select exactly five minimum checks", "POLICY_SCOPE"),
    ("real executor is required", "POLICY_OR_EXECUTOR"),
])
def test_contract_classification_never_copies_values(message, code):
    issue = contract_issue(ValueError(message + " PRIVATE-SECRET"))
    assert issue["code"] == code
    assert "PRIVATE" not in json.dumps(issue)


def test_ready_is_not_changed_by_advisory_unsupported_check_or_unknown_free_memory():
    data = {"ready": True, "checks": [{"check": "resources:baseline", "ok": True}],
        "check_support": [{"side": "candidate", "check_id": "REQ-11", "status": "UNSUPPORTED", "reasons": ["private"]}]}
    result = annotate(data)
    assert result["ready"] is True and result["check_support"] == data["check_support"]
    issue, memory = result["guidance"]["issues"]
    assert issue["code"] == "CHECK_UNSUPPORTED" and issue["check_id"] == "REQ-11" and issue["side"] == "candidate"
    assert not issue["blocking"]
    assert memory["code"] == "FREE_CAPACITY_UNMEASURED" and memory["status"] == "UNKNOWN" and not memory["blocking"]
    assert not result["guidance"]["engine_started"] and not result["guidance"]["downloads_performed"]
    assert result["guidance"]["scope"] == "preparation-only"


def test_independent_file_errors_are_all_reported_and_dependent_policy_is_unknown():
    calls = []
    def absent(path):
        calls.append(path)
        raise FileNotFoundError("PRIVATE-SECRET")
    loaded, issues = read_inputs({key: Path(key) for key in ("pack", "baseline", "candidate", "policy", "config")},
        pack_loader=absent, environment_loader=absent, policy_loader=lambda *a: pytest.fail("requires valid pack"), config_loader=absent)
    assert loaded == {} and set(calls) == {Path(key) for key in ("pack", "baseline", "candidate", "config")}
    assert [row["side"] for row in issues] == [None, "baseline", "candidate", None, None]
    assert issues[-1]["code"] == "POLICY_NOT_CHECKED" and issues[-1]["status"] == "UNKNOWN"
    data = input_failure(issues)
    assert not data["ready"] and any(row["code"] == "HOST_NOT_CHECKED" for row in data["guidance"]["issues"])
    assert "PRIVATE" not in json.dumps(data)


def test_prepare_cli_missing_files_has_structured_advice_without_host_or_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "update_readiness", lambda *a: pytest.fail("host must not be contacted for invalid files"))
    result = CliRunner().invoke(cli.app, ["prepare-update", "--pack", "absent", "--baseline", "a", "--candidate", "b", "--policy", "p"])
    assert result.exit_code == 1
    data = json.loads(result.output)
    assert not data["ready"] and data["guidance"]["schema"] == "guard-preparation-guidance/1"
    assert {row["side"] for row in data["guidance"]["issues"]} >= {"baseline", "candidate"}
    assert list(tmp_path.iterdir()) == []


def test_update_harness_missing_candidate_has_structured_stderr_and_keeps_exit_code(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(update_validation, "update_readiness", lambda *a: pytest.fail("no host query"))
    monkeypatch.setattr(update_validation, "_command", lambda *a: pytest.fail("no command execution"))
    assert update_validation.main(["--output", str(tmp_path / "new")]) == 1
    data = json.loads(capsys.readouterr().err)
    assert data["status"] == "error" and not data["accepted"]
    assert any(issue["code"] == "INPUT_MISSING" and issue["side"] == "candidate" for issue in data["guidance"]["issues"])
    assert not (tmp_path / "new").exists()
