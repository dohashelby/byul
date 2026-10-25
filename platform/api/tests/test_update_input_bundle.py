"""BYUL 입력 import 묶음의 읽기 전용 재검증. 모든 기업 자료는 EXAMPLE이다."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import socket
import subprocess

import pytest

from byul_platform.contracts import canonical_json, canonical_sha256
from guard.models import Environment, Policy, load_pack


ROOT = Path(__file__).resolve().parents[3]
PACK_PATH = ROOT / "update/packs/PACK-BASELINE"
T0 = datetime(2026, 10, 26, tzinfo=timezone.utc)
spec = importlib.util.spec_from_file_location("byul_bundle_boundary", ROOT / "update/tools/import_byul_inputs.py")
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)


@pytest.fixture(scope="module")
def current_pack():
    return load_pack(PACK_PATH)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sources(tmp_path, current_pack, *, suffix="", mutation=None):
    examples = ROOT / "contracts/examples"
    env = _json(examples / "environment.json")
    pol = _json(examples / "policy.json")
    pol["verification"]["pack"] = {"id": current_pack.ref().id, "revision": current_pack.ref().revision}
    if mutation:
        mutation(env, pol)
    pol["environment_ref"] = {"id": env["environment_id"], "revision": env["revision"],
                              "sha256": canonical_sha256(env)}
    env_path, pol_path = tmp_path / f"env{suffix}.json", tmp_path / f"pol{suffix}.json"
    env_path.write_bytes((json.dumps(env, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode())
    pol_path.write_bytes(canonical_json(pol))
    return env_path, pol_path


def _bundle(tmp_path, current_pack, *, suffix="", mutation=None, checked_at=T0):
    env, pol = _sources(tmp_path, current_pack, suffix=suffix, mutation=mutation)
    output = tmp_path / f"bundle{suffix}"
    boundary.import_inputs(env, pol, PACK_PATH, output, as_of=checked_at)
    return output


def _manifest(bundle, edit):
    path = bundle / "provenance.json"
    mapping = _json(path)
    edit(mapping)
    path.write_bytes(canonical_json(mapping))


def _refresh_file_record(bundle, name):
    raw = (bundle / name).read_bytes()
    def edit(mapping):
        item = next(item for item in mapping["files"] if item["path"] == name)
        item.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    _manifest(bundle, edit)


def _tree(bundle):
    return {path.name: (path.read_bytes(), path.stat().st_mtime_ns) for path in bundle.iterdir()}


def test_verify_is_read_only_and_reports_static_gaps_without_authorizing_execution(tmp_path, current_pack, monkeypatch):
    bundle = _bundle(tmp_path, current_pack)
    before = _tree(bundle)

    def forbidden(*args, **kwargs):
        raise AssertionError("verify-import must not write, create a process, or connect")

    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    report = boundary.verify_import(bundle, PACK_PATH, as_of=T0 + timedelta(hours=1))
    assert report["verified"] is True and report["engine_executed"] is False
    assert report["execution_ready"] is None
    assert report["time_basis"] == "EXPLICIT_AS_OF"
    assert report["import_checked_at"] == T0.isoformat()
    assert report["verified_at"] == (T0 + timedelta(hours=1)).isoformat()
    assert {"READ_ONLY_VERIFICATION_NOT_EXECUTION_AUTHORIZATION", "ENGINE_AND_TARGET_NOT_PROBED",
            "LOCAL_PROVENANCE_NOT_AUTHENTICATED", "ADAPTER_UNDECLARED", "RUNTIME_KIND_UNDECLARED",
            "ENGINE_IMAGE_DIGEST_UNDECLARED", "MODEL_REVISION_UNDECLARED", "MODEL_INVENTORY_UNDECLARED"} <= set(report["readiness_reasons"])
    mapping = _json(bundle / "provenance.json")
    for key in ("environment_ref", "byul_policy_ref", "guard_environment_ref", "guard_policy_ref", "pack_ref"):
        assert report[key] == mapping[key]
    assert report["provenance_sha256"] == hashlib.sha256((bundle / "provenance.json").read_bytes()).hexdigest()
    assert _tree(bundle) == before


@pytest.mark.parametrize("gpu_count,reason", [(None, "RUNTIME_KIND_UNDECLARED"), (0, "CPU_PROFILE_UNDECLARED")])
def test_verification_does_not_infer_cpu_or_repository(tmp_path, current_pack, gpu_count, reason):
    def edit(env, pol):
        env["target"]["engine"]["image_digest"] = "sha256:" + "a" * 64
        env["target"]["runtime"]["gpu_count"] = gpu_count
    bundle = _bundle(tmp_path, current_pack, mutation=edit)
    report = boundary.verify_import(bundle, PACK_PATH, as_of=T0)
    assert reason in report["readiness_reasons"]
    assert "REPOSITORY_PIN_REQUIRES_EXPLICIT_TARGET" in report["readiness_reasons"]
    env = Environment.model_validate(_json(bundle / "environment.json"))
    assert env.runtime.cpu is None and env.adapter is None
    assert env.engine.image_digest == "sha256:" + "a" * 64


@pytest.mark.parametrize("name", ["source-environment.json", "source-policy.json", "environment.json", "policy.json"])
def test_original_bytes_are_verified_even_when_only_whitespace_changes(tmp_path, current_pack, name):
    bundle = _bundle(tmp_path, current_pack)
    path = bundle / name
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_FILE_HASH_MISMATCH$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


@pytest.mark.parametrize("field,value", [("bytes", True), ("bytes", -1), ("sha256", "z" * 64)])
def test_invalid_file_index_is_not_trusted(tmp_path, current_pack, field, value):
    bundle = _bundle(tmp_path, current_pack)
    _manifest(bundle, lambda mapping: mapping["files"][0].update({field: value}))
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_FILE_INDEX_INVALID$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


@pytest.mark.parametrize("path", ["../outside.json", "/absolute.json", "C:\\outside.json", "files/../policy.json"])
def test_manifest_paths_cannot_redirect_reads(tmp_path, current_pack, path):
    bundle = _bundle(tmp_path, current_pack)
    _manifest(bundle, lambda mapping: mapping["files"][0].update(path=path))
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_FILE_PATH_UNSAFE$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_links_are_rejected_without_platform_symlink_permissions(tmp_path, current_pack, monkeypatch):
    bundle = _bundle(tmp_path, current_pack)
    linked = bundle / "source-environment.json"
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda path: path == linked or original(path))
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_FILE_PATH_UNSAFE$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


@pytest.mark.parametrize("kind,code", [("missing", "IMPORT_FILE_MISSING"),
                                      ("extra", "IMPORT_BUNDLE_FILES_MISMATCH"),
                                      ("duplicate", "IMPORT_FILE_INDEX_INVALID"),
                                      ("incomplete", "IMPORT_FILE_INDEX_INCOMPLETE")])
def test_missing_extra_and_ambiguous_bundle_members_are_rejected(tmp_path, current_pack, kind, code):
    bundle = _bundle(tmp_path, current_pack)
    if kind == "missing":
        (bundle / "policy.json").unlink()
    elif kind == "extra":
        (bundle / "other-policy.json").write_bytes(b"{}")
    elif kind == "duplicate":
        _manifest(bundle, lambda mapping: mapping["files"].append(deepcopy(mapping["files"][0])))
    else:
        _manifest(bundle, lambda mapping: mapping["files"].pop())
    with pytest.raises(boundary.InputBoundaryError, match=f"^{code}$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_resealed_guard_policy_cannot_change_the_source_policy_meaning(tmp_path, current_pack):
    bundle = _bundle(tmp_path, current_pack)
    data = _json(bundle / "policy.json")
    data["values"]["REQ-01"]["allowed_callers"] = ["unapproved-extra-caller"]
    changed = Policy.model_validate(data).seal()
    (bundle / "policy.json").write_bytes(canonical_json(changed.to_dict()))
    _refresh_file_record(bundle, "policy.json")
    _manifest(bundle, lambda mapping: mapping.update(guard_policy_ref=changed.ref().model_dump(mode="json")))
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_CONVERSION_OUTPUT_MISMATCH$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_mixed_valid_guard_objects_are_not_accepted_as_an_original_conversion(tmp_path, current_pack):
    bundle = _bundle(tmp_path, current_pack)
    def edit(env, pol):
        env["target"]["engine"]["version"] = "9.9.9"
    other = _bundle(tmp_path, current_pack, suffix="-other", mutation=edit)
    copied = (other / "environment.json").read_bytes()
    (bundle / "environment.json").write_bytes(copied)
    _refresh_file_record(bundle, "environment.json")
    changed = Environment.model_validate(json.loads(copied))
    _manifest(bundle, lambda mapping: mapping.update(guard_environment_ref=changed.ref().model_dump(mode="json")))
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_CONVERSION_OUTPUT_MISMATCH$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_own_hash_and_ref_are_checked_before_comparing_source_values(tmp_path, current_pack):
    bundle = _bundle(tmp_path, current_pack)
    data = _json(bundle / "environment.json")
    data["engine"]["version"] = "not-original"
    (bundle / "environment.json").write_bytes(canonical_json(data))
    _refresh_file_record(bundle, "environment.json")
    with pytest.raises(boundary.InputBoundaryError, match="^GUARD_OBJECT_HASH_INVALID$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


@pytest.mark.parametrize("field,code", [("guard_environment_ref", "GUARD_OBJECT_REF_MISMATCH"),
                                      ("environment_ref", "IMPORT_SOURCE_MAPPING_MISMATCH")])
def test_boolean_revision_is_not_equal_to_integer_revision(tmp_path, current_pack, field, code):
    bundle = _bundle(tmp_path, current_pack)
    _manifest(bundle, lambda mapping: mapping[field].update(revision=True))
    with pytest.raises(boundary.InputBoundaryError, match=f"^{code}$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_numeric_overflow_is_not_accepted_as_canonical_json(tmp_path):
    source = tmp_path / "overflow.json"
    source.write_bytes(b'{"value":1e999}')
    with pytest.raises(boundary.InputBoundaryError, match="^JSON_NONFINITE_NUMBER$"):
        boundary.read_document(source)


def test_utc_time_range_overflow_is_normalized_to_fixed_input_error():
    with pytest.raises(boundary.InputBoundaryError, match="^AS_OF_REQUIRES_TIMEZONE$"):
        boundary._time("0001-01-01T00:00:00+01:00")


@pytest.mark.parametrize("field,value", [("target_ref", "different-target"),
                                        ("guard_policy_ref", {"kind": "POL", "id": "POL-WRONG", "revision": 1, "sha256": "0" * 64}),
                                        ("pack_ref", {"kind": "PACK", "id": "PACK-WRONG", "revision": 1, "sha256": "0" * 64}),
                                        ("limitations", [])])
def test_mapping_does_not_override_refs_target_pack_or_limitations(tmp_path, current_pack, field, value):
    bundle = _bundle(tmp_path, current_pack)
    _manifest(bundle, lambda mapping: mapping.update({field: value}))
    code = "GUARD_OBJECT_REF_MISMATCH" if field == "guard_policy_ref" else "IMPORT_SOURCE_MAPPING_MISMATCH"
    with pytest.raises(boundary.InputBoundaryError, match=f"^{code}$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


@pytest.mark.parametrize("phase", ["import-time", "verification-time", "future-import"])
def test_import_time_and_current_policy_validity_are_separate(tmp_path, current_pack, phase):
    bundle = _bundle(tmp_path, current_pack)
    if phase == "import-time":
        _manifest(bundle, lambda mapping: mapping.update(checked_at="2025-01-01T00:00:00+00:00"))
        current, code = T0, "POLICY_NOT_YET_VALID"
    elif phase == "verification-time":
        current, code = datetime(2027, 1, 1, tzinfo=timezone.utc), "POLICY_EXPIRED"
    else:
        _manifest(bundle, lambda mapping: mapping.update(checked_at=(T0 + timedelta(days=1)).isoformat()))
        current, code = T0, "IMPORT_CHECKED_AT_IN_FUTURE"
    with pytest.raises(boundary.InputBoundaryError, match=f"^{code}$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=current)


def test_default_verification_checks_actual_utc_time(tmp_path, current_pack):
    before = datetime.now(timezone.utc)
    def edit(env, pol):
        pol["validity"] = {"valid_from": (before - timedelta(days=1)).isoformat(),
                           "valid_until": (before + timedelta(days=1)).isoformat()}
    bundle = _bundle(tmp_path, current_pack, mutation=edit, checked_at=before - timedelta(minutes=1))
    report = boundary.verify_import(bundle, PACK_PATH)
    after = datetime.now(timezone.utc)
    assert before <= datetime.fromisoformat(report["verified_at"]) <= after
    assert report["time_basis"] == "CURRENT_UTC"


def test_bundle_changed_during_verification_is_not_reported_as_verified(tmp_path, current_pack, monkeypatch):
    bundle = _bundle(tmp_path, current_pack)
    selected = bundle / "source-environment.json"
    original, reads = Path.read_bytes, 0

    def changed_on_recheck(path):
        nonlocal reads
        raw = original(path)
        if path == selected:
            reads += 1
            return raw + b"\n" if reads > 1 else raw
        return raw

    monkeypatch.setattr(Path, "read_bytes", changed_on_recheck)
    with pytest.raises(boundary.InputBoundaryError, match="^IMPORT_BUNDLE_CHANGED_DURING_VERIFICATION$"):
        boundary.verify_import(bundle, PACK_PATH, as_of=T0)


def test_verification_cli_is_explicit_and_legacy_import_still_works(tmp_path, current_pack, capsys):
    env, pol = _sources(tmp_path, current_pack)
    output = tmp_path / "cli-import"
    assert boundary.main(["--environment", str(env), "--policy", str(pol), "--pack", str(PACK_PATH),
                          "--output", str(output), "--as-of", T0.isoformat()]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert imported["imported"] is True and imported["engine_executed"] is False
    before = _tree(output)
    assert boundary.main(["--verify-import", str(output), "--pack", str(PACK_PATH), "--as-of", T0.isoformat()]) == 0
    verified = json.loads(capsys.readouterr().out)
    assert verified["verified"] is True and verified["execution_ready"] is None
    assert verified["guard_policy_ref"] == imported["guard_policy_ref"]
    assert _tree(output) == before
    with pytest.raises(SystemExit) as error:
        boundary.main(["--verify-import", str(output), "--output", str(tmp_path / "must-not-exist"), "--pack", str(PACK_PATH)])
    assert error.value.code == 2 and not (tmp_path / "must-not-exist").exists()


def test_cli_failure_exposes_only_fixed_code_and_does_not_repair_inputs(tmp_path, current_pack, capsys):
    bundle = _bundle(tmp_path, current_pack)
    (bundle / "environment.json").write_bytes(b"{}")
    before = _tree(bundle)
    assert boundary.main(["--verify-import", str(bundle), "--pack", str(PACK_PATH), "--as-of", T0.isoformat()]) == 2
    captured = capsys.readouterr()
    assert captured.err.strip() == "IMPORT_FILE_HASH_MISMATCH" and not captured.out
    assert _tree(bundle) == before


def test_verified_byul_inputs_reach_example_guard_report_and_platform_get(
        tmp_path, current_pack, contracts, monkeypatch):
    """입력→선정→가짜 실행→판정→원본 RPT→플랫폼 조회. 실제 엔진 실증이 아니다."""
    from fastapi.testclient import TestClient
    from byul_platform.config import Settings
    from byul_platform.main import create_app
    from byul_platform.sources.update import rpt_to_verification_result
    from guard.collectors.file import load_environment, load_policy
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.runtime.docker import Docker
    from guard.store import Store

    def example_scope(env, pol):
        pol["verification"]["scope"] = {"include": ["REQ-01"], "exclude": []}
        pol["verification"]["values"] = {"REQ-01": {"allowed_callers": ["EXAMPLE-client"]}}

    bundle = _bundle(tmp_path, current_pack, mutation=example_scope)
    original = _tree(bundle)
    verified = boundary.verify_import(bundle, PACK_PATH, as_of=T0)
    env = load_environment(bundle / "environment.json")
    pol = load_policy(bundle / "policy.json", current_pack)
    assert env.ref().model_dump(mode="json") == verified["guard_environment_ref"]
    assert pol.ref().model_dump(mode="json") == verified["guard_policy_ref"]
    store = Store(tmp_path / "example-store")
    store.put_many([env, pol])
    plan = select(current_pack, [env], pol)
    store.put(plan)

    def forbidden(*args, **kwargs):
        raise AssertionError("example bundle pipeline must not instantiate Docker")

    with monkeypatch.context() as patch:
        patch.setattr(Docker, "__init__", forbidden)
        (evd,) = run_plan(store, current_pack, plan, RunConfig(
            executors={"direct_route": "fake"}, adapter="fake"))
    (result,) = judge_plan(store, current_pack, plan, as_of=T0)
    report = build_report(store, current_pack, plan, T0)
    converted = rpt_to_verification_result(report.to_dict())
    assert contracts.errors("verification-result", converted) == []
    assert evd.data_origin.value == result.data_origin.value == report.data_origin.value == "EXAMPLE"
    assert converted["official"] is False and converted["byul_policy_ref"] is None
    assert converted["policy_ref"] == pol.ref().model_dump(mode="json")
    assert verified["byul_policy_ref"]["sha256"] != converted["policy_ref"]["sha256"]
    assert result.evidence == evd.ref()
    reports = tmp_path / "reports"
    reports.mkdir()
    path = reports / "report.json"
    raw = canonical_json(report.to_dict())
    path.write_bytes(raw)
    with TestClient(create_app(Settings(update_reports_dir=reports, web_dist=tmp_path / "no-web"))) as client:
        response = client.get(f"/api/update/results/{report.id}")
        assert response.status_code == 200
        assert response.json()["item"]["report_ref"] == report.ref().model_dump(mode="json")
        assert response.json()["item"]["official"] is False
    assert path.read_bytes() == raw and _tree(bundle) == original
