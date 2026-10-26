"""실험 입력 준비와 실제 실행·판정·기업 승인을 혼동하지 않는 계약."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import tempfile

import pytest
import yaml

from guard.models import load_pack
from tools.prepare_checklist_cases import ORDER, REPO, ROOT, build_cases, main, prepare_cases


@pytest.fixture
def external_path():
    with tempfile.TemporaryDirectory(prefix="guard-case-preparation-") as folder:
        path = Path(folder).resolve()
        assert not path.is_relative_to(REPO.resolve())
        yield path


@pytest.fixture(scope="module")
def pack():
    return load_pack(ROOT / "packs/PACK-BASELINE")


def test_preparation_preserves_current_definitions_and_does_not_make_results(pack):
    before = pack.model_dump(mode="json")
    plan, payloads = build_cases(pack)
    assert pack.model_dump(mode="json") == before
    assert plan["pack_ref"] == pack.ref().model_dump(mode="json")
    assert plan["data_origin"] == "EXAMPLE" and plan["planning_only"] is True
    assert plan["engine_executed"] is plan["execution_authorized"] is False
    assert [item["check_id"] for item in plan["checks"]] == list(ORDER)
    assert len(payloads) == 15
    scopes = {item["check_id"]: item["scope"] for item in plan["checks"]}
    assert scopes["REQ-02"] == "CONTRACT_ONLY"
    assert scopes["SUP-03"] == "OBSERVATION_ONLY"
    assert sum(value == "COLLECTOR_CONNECTED" for value in scopes.values()) == 13
    cases = plan["cases"]
    assert len(cases) == len({case["case_id"] for case in cases}) == 93
    assert all(case["ready_to_execute"] is False and case["verdict"] is None
               and case["evidence_refs"] == case["result_refs"] == []
               and "separate_execution_authorization" in case["pending_inputs"] for case in cases)
    for item in plan["checks"]:
        definition = pack.checks[item["check_id"]]
        assert item["normal_control"] == definition.normal_control
        assert item["fail_criteria"] == definition.fail_criteria
        assert item["unknown_criteria"] == definition.unknown_criteria
        assert item["not_exercised"] == definition.challenge_steps
        assert item["required_observations"] == [o.key for o in definition.required_observations]
    for case in cases:
        if case["check_id"] == "REQ-02":
            assert case["command_template"] is None
        else:
            assert case["command_template"][:4] == ["uv", "run", "guard", "validate"]
            assert f'<NEW-STORE-{case["case_id"]}>' in case["command_template"]
            assert f'<NEW-OUTPUT-{case["case_id"]}>' in case["command_template"]


def test_branch_gaps_and_explicit_empty_candidate_are_separate_cases(pack):
    plan, _ = build_cases(pack)
    branches = {cid: {c["branch"] for c in plan["cases"] if c["check_id"] == cid} for cid in ORDER}
    assert all({"normal", "negative", "missing", "unsupported"} <= values for values in branches.values())
    assert {"candidate-omitted", "candidate-empty", "candidate-absent", "candidate-denied",
            "candidate-readable", "subject-mismatch"} <= branches["PLT-04"]
    assert {"default-source", "opt-in-source", "http-error-object"} <= branches["REQ-09"]
    assert {"approved", "unapproved", "cached-template", "restarted"} <= branches["SUP-03"]
    assert {"auth-trace-missing", "handler-trace-missing", "client-id-swapped"} <= branches["REQ-02"]
    assert {"output-oracle-missing", "output-swapped"} <= branches["REQ-11"]
    assert {"dump-missing", "copy-missing", "truncated"} <= branches["OBS-03"]


def test_templates_are_blank_draft_policy_inputs_and_generation_is_deterministic(pack):
    first, files = build_cases(pack)
    assert (first, files) == build_cases(pack)
    for cid in ORDER:
        value = yaml.safe_load(files[f"{cid}/policy-template.yaml"])
        assert value["data_origin"] == "DECLARED" and value["status"] == "DRAFT"
        assert value["pack"] == {"id": pack.manifest.pack_id, "revision": pack.manifest.revision}
        assert value["values"] == ({cid: {}} if pack.checks[cid].policy_params else {})
        assert value["scope"] == {"include": [cid], "exclude": []}
        assert value["exceptions"] == [] and value["confirmed_by"] is value["confirmed_at"] is None
        assert "env" not in value and "config" not in value


def test_writing_external_plan_preserves_pack_and_never_launches_resources(pack, external_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("preparation invoked process/network/engine")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    paths = list(pack.path.rglob("*"))
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in paths if p.is_file()}
    output = external_path / "casebook"
    plan = prepare_cases(pack, output)
    assert json.loads((output / "case-plan.json").read_text(encoding="utf-8")) == plan
    assert {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in paths if p.is_file()} == before
    assert len(list(output.rglob("*"))) == 31  # 15 dirs, 15 YAML, one diagnostic plan
    for item in plan["files"]:
        raw = (output / item["path"]).read_bytes()
        assert item["bytes"] == len(raw) and item["sha256"] == hashlib.sha256(raw).hexdigest()
        assert b"\r\n" not in raw


@pytest.mark.parametrize("ids", [[], ["REQ-01", "REQ-01"], ["REQ-99"], ["../REQ-01"], ["req-01"], [{}]])
def test_bad_selection_creates_no_output(pack, external_path, ids):
    output = external_path / "invalid"
    with pytest.raises(ValueError, match="CASE_CHECK_SELECTION_INVALID"):
        prepare_cases(pack, output, ids)
    assert not output.exists()


def test_retired_check_is_not_silently_prepared(pack, external_path):
    changed = deepcopy(pack)
    changed.checks["REQ-01"].retired = True
    with pytest.raises(ValueError, match="CASE_CHECK_SELECTION_INVALID"):
        prepare_cases(changed, external_path / "retired", ["REQ-01"])
    assert not (external_path / "retired").exists()


@pytest.mark.parametrize("where", [REPO / "out/unapproved", ROOT / "packs/PACK-BASELINE/new-case"])
def test_source_paths_rejected_before_creation(pack, where):
    assert not where.exists()
    with pytest.raises(ValueError, match="CASE_OUTPUT_MUST_BE_OUTSIDE_SOURCE"):
        prepare_cases(pack, where)
    assert not where.exists()


def test_existing_output_is_preserved_and_explicit_subset_is_supported(pack, external_path):
    output = external_path / "casebook"
    output.mkdir()
    sentinel = output / "existing.json"
    sentinel.write_bytes(b"original")
    before = sentinel.stat().st_mtime_ns
    with pytest.raises(FileExistsError):
        prepare_cases(pack, output)
    assert list(output.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"original" and sentinel.stat().st_mtime_ns == before
    plan = prepare_cases(pack, external_path / "subset", ["SUP-03", "REQ-02"])
    assert [c["check_id"] for c in plan["checks"]] == ["SUP-03", "REQ-02"]
    assert len(plan["cases"]) == 15


def test_cli_has_no_execute_path_and_errors_are_fixed_codes(pack, external_path, capsys):
    output = external_path / "output"
    with pytest.raises(SystemExit) as error:
        main(["--pack", str(pack.path), "--output", str(output), "--execute"])
    assert error.value.code == 2 and not output.exists()
    capsys.readouterr()
    assert main(["--pack", str(pack.path), "--output", str(output), "--checks", "REQ-99"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "CASE_PREPARATION_FAILED\n" and not output.exists()


@pytest.mark.parametrize("missing_id", ["REQ-01", "EXAMPLE-private-invalid-id"])
def test_cli_normalizes_missing_declared_check_but_does_not_swallow_programming_errors(pack, external_path, capsys, monkeypatch, missing_id):
    broken = external_path / "private-pack-path"
    broken.mkdir()
    manifest = pack.manifest.model_dump(mode="json")
    manifest["files_sha256"] = []
    manifest["checks"] = [missing_id]
    (broken / "pack.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    output = external_path / "output"
    args = ["--pack", str(broken), "--output", str(output)]
    assert main(args) == 2 and not output.exists()
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == "CASE_PREPARATION_FAILED\n"
    def unexpected(_):
        raise KeyError("private-programming-error")
    monkeypatch.setattr("tools.prepare_checklist_cases.load_pack", unexpected)
    with pytest.raises(KeyError, match="private-programming-error"):
        main(args)
