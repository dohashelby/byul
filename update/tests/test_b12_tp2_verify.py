"""새 로컬 사전검증 경계만 시험한다. 모든 입력은 EXAMPLE metadata다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from guard.models import canonical_json, sha256_hex
from tests.builders import environment
from tools import b12_tp2_inputs as tool
from tools import b12_tp2_verify as verify


def write(root, name, data):
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode()
    (root / name).write_bytes(raw)
    return {"path": name, "file_sha256": hashlib.sha256(raw).hexdigest()}


def inputs(tmp_path, cid="LIF-08"):
    root = tmp_path / "inputs"
    root.mkdir()
    env = environment()
    target = {"schema": "EXAMPLE-target", "environment_ref": env.ref().model_dump(mode="json")}
    prepared = {"schema": "guard-lifecycle-assets/1", "profile": "EXAMPLE-metadata-only",
                "session": "a" * 32, "assets": {}}
    values = {("rank" if cid == "LIF-08" else "recovery") + "_" + key + "_sha256": value for key, value in (
        ("environment", env.ref().sha256), ("target", sha256_hex(canonical_json(target))),
        ("prepared", sha256_hex(canonical_json(prepared))))}
    section = {"runtime_refs": {"ENV": write(root, "env.json", env.model_dump(mode="json")),
                               "target": write(root, "target.json", target),
                               "prepared": write(root, "prepared.json", prepared)}, "policy_values": values}
    return root, {"schema": tool.INPUT_SCHEMA, "checks": {cid: section}}, env, target, prepared


def run(tmp_path, root, supplied):
    result = tool.prepare(tmp_path / "output", supplied, verify_inputs_root=root)
    return result, json.loads((tmp_path / "output/local-verification.json").read_bytes())


def codes(result):
    return {r["code"] for r in result["errors"]}


def test_file_sha_is_distinct_from_ENV_object_ref_and_no_execution(tmp_path, monkeypatch):
    root, supplied, env, _, _ = inputs(tmp_path)
    before = deepcopy(supplied)
    assert supplied["checks"]["LIF-08"]["runtime_refs"]["ENV"]["file_sha256"] != env.ref().sha256
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unexpected child process"))
    manifest, result = run(tmp_path, root, supplied)
    assert supplied == before and not result["errors"]
    assert result["all_declared_files_verified"] and result["all_declared_json_objects_verified"]
    assert result["status"] == "INCOMPLETE" and not result["runtime_binding_verified"]
    assert not result["prepared_assets_verified"] and not manifest["ready_for_execution"]
    assert manifest["GPU_or_AWS_operations"] == result["subprocesses_started"] == 0
    assert manifest["file_pointers_opened_or_verified"]
    artifact = next(p for p in manifest["artifacts"] if p["path"] == "local-verification.json")
    assert artifact["file_sha256"] == hashlib.sha256((tmp_path / "output/local-verification.json").read_bytes()).hexdigest()
    assert all("value" not in r for r in result["binding_checks"])


@pytest.mark.parametrize("fault,code", [
    ("missing", "INPUT_FILE_NOT_FOUND"), ("sha", "INPUT_FILE_SHA_MISMATCH"),
    ("outside", "INPUT_OUTSIDE_ROOT"), ("directory", "INPUT_NOT_REGULAR"),
    ("oversize", "INPUT_SIZE_INVALID"), ("duplicate", "INPUT_JSON_INVALID"),
    ("unsealed", "SEALED_ENV_INVALID"), ("array", "JSON_OBJECT_REQUIRED"),
])
def test_file_failures_preserve_originals_and_report_fixed_codes(tmp_path, fault, code):
    root, supplied, _, _, _ = inputs(tmp_path)
    refs = supplied["checks"]["LIF-08"]["runtime_refs"]
    if fault == "missing":
        refs["ENV"]["path"] = "absent.json"
    elif fault == "sha":
        refs["ENV"]["file_sha256"] = "b" * 64
    elif fault == "outside":
        (tmp_path / "outside.json").write_text("{}")
        refs["ENV"]["path"] = "../outside.json"
    elif fault == "directory":
        refs["ENV"]["path"] = "."
    elif fault in ("oversize", "duplicate"):
        raw = b" " * (verify.MAX_BYTES + 1) if fault == "oversize" else b'{"kind":"ENV","kind":"ENV"}'
        (root / "env.json").write_bytes(raw)
        refs["ENV"]["file_sha256"] = hashlib.sha256(raw).hexdigest()
    else:
        refs["ENV"] = write(root, "env.json", {"kind": "ENV", "secret_marker": "not-for-report"} if fault == "unsealed" else [])
    originals = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    _, result = run(tmp_path, root, supplied)
    assert result["status"] == "FAILED" and code in codes(result)
    assert originals == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    assert "not-for-report" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["target_env", "policy_object_vs_file", "nested_scope", "control_session"])
def test_cross_environment_policy_and_session_mismatch(tmp_path, fault):
    root, supplied, _, target, prepared = inputs(tmp_path)
    section = supplied["checks"]["LIF-08"]
    if fault == "target_env":
        target["environment_ref"] = environment(id="ENV-OTHER").ref().model_dump(mode="json")
        section["runtime_refs"]["target"] = write(root, "target.json", target)
        section["policy_values"]["rank_target_sha256"] = sha256_hex(canonical_json(target))
    elif fault == "policy_object_vs_file":
        section["policy_values"]["rank_environment_sha256"] = section["runtime_refs"]["ENV"]["file_sha256"]
    elif fault == "nested_scope":
        prepared["model_read_scope"] = {"environment_ref": environment(id="ENV-OTHER").ref().model_dump(mode="json")}
        section["runtime_refs"]["prepared"] = write(root, "prepared.json", prepared)
        section["policy_values"]["rank_prepared_sha256"] = sha256_hex(canonical_json(prepared))
    else:
        section["controls"] = {"normal": write(root, "normal.json", {"session": "b" * 32})}
    _, result = run(tmp_path, root, supplied)
    assert "DECLARED_BINDING_MISMATCH" in codes(result)


@pytest.mark.parametrize("mixed", [False, True])
def test_supply_target_and_prepared_must_match_the_same_role(tmp_path, mixed):
    root, supplied, env, target, prepared = inputs(tmp_path)
    section = supplied["checks"].pop("LIF-08")
    section["policy_values"] = {"supply_environment_sha256": env.ref().sha256}
    for role in ("normal", "challenge"):
        section["policy_values"].update({"supply_" + role + "_target_sha256": sha256_hex(canonical_json(target)) if role == "normal" else "b" * 64,
                                         "supply_" + role + "_prepared_sha256": sha256_hex(canonical_json(prepared)) if role == ("challenge" if mixed else "normal") else "c" * 64})
    supplied["checks"]["SUP-06"] = section
    _, result = run(tmp_path, root, supplied)
    assert ("SUPPLY_ROLE_PAIR_MISMATCH" in codes(result)) is mixed
    assert not result["runtime_binding_verified"] and not result["ready_for_execution"]


def test_local_root_is_required_and_existing_output_remains_untouched(tmp_path):
    root, supplied, *_ = inputs(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    marker = output / "keep"
    marker.write_bytes(b"original")
    with pytest.raises(ValueError, match="NEW_B_TP2_OUTPUT_DIRECTORY_REQUIRED"):
        tool.prepare(output, supplied, verify_inputs_root=root)
    assert marker.read_bytes() == b"original" and list(output.iterdir()) == [marker]
    with pytest.raises(FileNotFoundError):
        tool.prepare(tmp_path / "uncreated", supplied, verify_inputs_root=tmp_path / "absent")
    assert not (tmp_path / "uncreated").exists()


@pytest.mark.parametrize("bad", [False, True])
def test_recovery_selection_and_scenario_content_hashes(tmp_path, bad):
    root, supplied, env, _, prepared = inputs(tmp_path, "LIF-11")
    section = supplied["checks"]["LIF-11"]
    for key in ("selection", "scenario"):
        data = {"environment_ref": env.ref().model_dump(mode="json"), "session": prepared["session"]}
        section["runtime_refs"][key] = write(root, key + ".json", data)
        section["policy_values"]["recovery_" + key + "_sha256"] = sha256_hex(canonical_json(data))
    if bad:
        section["policy_values"]["recovery_scenario_sha256"] = "b" * 64
    _, result = run(tmp_path, root, supplied)
    assert bool(result["errors"]) is bad
    assert not result["ready_for_execution"]


def test_sealed_environment_content_tamper_is_not_accepted_as_file_hash_success(tmp_path):
    root, supplied, env, *_ = inputs(tmp_path)
    data = env.model_dump(mode="json")
    data["id"] = "ENV-ALTERED"
    supplied["checks"]["LIF-08"]["runtime_refs"]["ENV"] = write(root, "env.json", data)
    _, result = run(tmp_path, root, supplied)
    assert "SEALED_ENV_INVALID" in codes(result) and result["all_declared_files_verified"]


def test_reparse_file_is_rejected_before_read(tmp_path, monkeypatch):
    root, supplied, *_ = inputs(tmp_path)
    original = Path.lstat
    def lstat(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        return SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400) if path.name == "env.json" else result
    monkeypatch.setattr(Path, "lstat", lstat)
    _, result = run(tmp_path, root, supplied)
    assert "INPUT_LINK_UNSUPPORTED" in codes(result)


def test_isolated_cli_reports_failed_file_verification_and_never_executes(tmp_path):
    root, supplied, *_ = inputs(tmp_path)
    supplied["checks"]["LIF-08"]["runtime_refs"]["ENV"]["path"] = "missing.json"
    path = tmp_path / "inputs.json"
    path.write_text(json.dumps(supplied), encoding="utf-8")
    out = tmp_path / "output"
    result = subprocess.run([sys.executable, "-I", "-B", str(Path(tool.__file__).resolve()), "--inputs", str(path),
                             "--output", str(out), "--verify-inputs-root", str(root)],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 2, result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["local_verification"]["status"] == "FAILED" and not manifest["execution_authorized"]
    assert (out / "local-verification.json").exists()
