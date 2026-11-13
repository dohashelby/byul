"""입력→저장→기존 guard 변환 경계. 합성 Pack은 GPU 실측·정본 Pack 34가 아니다."""
from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
import pytest
import yaml

from byul_platform.config import Settings
from byul_platform.contracts import canonical_sha256
from byul_platform.inputs import MVP_PACK
from byul_platform.main import create_app
from conftest import EXAMPLES, load_json

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def prepared_client(tmp_path):
    update = tmp_path / "update"
    pack = update / "packs/PACK-BASELINE"
    shutil.copytree(ROOT / "update/packs/PACK-BASELINE", pack)
    path = pack / "pack.yaml"
    manifest = yaml.safe_load(path.read_text())
    manifest["revision"] = 34  # 이 시험 안에서만 사용하는 합성 정의
    sup = pack / "checks/SUP-08.yaml"
    check = yaml.safe_load(sup.read_text())
    check["policy_params"].append({"key": "approved_code_revisions", "type": "list[str]", "required": True})
    sup.write_text(yaml.safe_dump(check, allow_unicode=True), encoding="utf-8")
    for item in manifest["files_sha256"]:
        item["sha256"] = hashlib.sha256((pack / item["path"]).read_bytes()).hexdigest()
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8")
    profiles = update / "src/guard/adapters/assets"
    profiles.mkdir(parents=True)
    # 명시 비적용과 지원 정보 누락은 다른 상태다.
    (profiles / "engine_profiles.json").write_text(json.dumps({"profiles": [
        {"id": "test-old", "engine": "vllm", "versions": ["old"], "features": [], "input_fields_not_applicable": ["placement.runner"]},
        {"id": "test-new", "engine": "vllm", "versions": ["new"], "features": ["runner_capture"]},
    ]}))
    app = create_app(Settings(update_dir=update, platform_data_dir=tmp_path / "data", web_dist=tmp_path / "none"))
    with TestClient(app) as client:
        yield client


def policy():
    doc = load_json(EXAMPLES / "policy.json")
    doc.update(policy_id="mvp-policy", status="DRAFT", approval=None, data_origin="EXAMPLE")
    doc["verification"].update(pack=dict(MVP_PACK), values={})
    doc["framework_values"] = {"catalog_version": "byul-framework-input/0.1", "framework_checklist_sha256": None,
                               "requirements": {}, "approval_terms": {"decision": None, "scope": None, "conditions": None}}
    return doc


def answer(values=None, *, decision="change", numbers=None):
    return {"decision": decision, "values": values or {}, "numbers": numbers or {}, "basis": "합성 시험 정책", "note": None}


def test_missing_pack_preserves_draft_but_cannot_claim_readiness(store_client):
    doc = policy()
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": [], "require_non_root": False})
    res = store_client.post("/api/policies", json=doc)
    assert res.status_code == 201, res.text
    saved = res.json()["saved"]["document"]
    assert saved["verification"]["pack"] == MVP_PACK
    assert saved["framework_values"] == doc["framework_values"]
    assert saved["status"] == "DRAFT" and saved["data_origin"] == "EXAMPLE"
    assert any(w["path"] == "/verification/pack" for w in res.json()["warnings"])
    preview = store_client.post("/api/inputs/policy-preview", json={"document": saved}).json()
    assert not preview["pack_available"] and not preview["engine_executed"]


def test_backend_rejects_new_pack_override_and_keeps_legacy_revisions(store_client):
    doc = policy()
    doc["verification"]["pack"]["revision"] = 33
    assert store_client.post("/api/policies", json=doc).status_code == 422
    state = store_client.app.state.byul
    state.policies.append(doc["policy_id"], 1, doc, status="DRAFT", label="1")
    before = store_client.get("/api/policies/mvp-policy/revisions/1").json()
    res = store_client.post("/api/policies", json=doc)
    assert res.status_code == 201
    assert res.json()["saved"]["document"]["verification"]["pack"]["revision"] == 33
    assert store_client.get("/api/policies/mvp-policy/revisions/1").json() == before


def test_catalog_uses_real_pack_parameters_and_explicit_field_rules(prepared_client):
    catalog = prepared_client.get("/api/inputs/catalog").json()
    assert catalog["available"]
    assert len(catalog["parameters"]) == 69
    assert "SUP-01" not in catalog["parameters"]
    assert any(b["key"] == "approved_code_revisions" for b in catalog["bindings"])
    versions = {v["version"]: v for v in catalog["versions"]}
    assert versions["old"]["hidden_fields"] == ["placement.runner"]
    assert versions["new"]["hidden_fields"] == []


def test_mapped_values_survive_save_reload_and_guard_conversion(prepared_client):
    env = load_json(EXAMPLES / "environment.json")
    env["target"]["runtime"].update(nodes=1, tp_groups=1)
    saved_env = prepared_client.post("/api/environments", json=env).json()["saved"]
    doc = policy()
    doc["target_ref"] = env["target"]["target_ref"]
    doc["environment_ref"] = {"id": env["environment_id"], "revision": 1, "sha256": saved_env["meta"]["sha256"]}
    doc["framework_values"]["requirements"] = {
        "1.4": answer({"allowed_devices": [], "require_non_root": False}),
        "3.1": answer(numbers={"max_input_tokens": {"value": 4096, "unit": "token", "scope": "요청별", "condition": None, "status": "confirmed"}}),
        "3.2": answer({"approved_code_revisions": ["approved-fixture"]}),
    }
    response = prepared_client.post("/api/policies", json=doc)
    assert response.status_code == 201, response.text
    saved = response.json()["saved"]["document"]
    assert saved["verification"]["values"]["PLT-01"] == {"allowed_devices": [], "require_non_root": False}
    assert saved["verification"]["values"]["REQ-09"]["max_input_tokens"] == 4096
    assert saved["verification"]["values"]["SUP-08"]["approved_code_revisions"] == ["approved-fixture"]
    assert saved["verification"]["values"]["OBS-05"]["usage_allowed"] is False
    assert prepared_client.get("/api/policies/mvp-policy/revisions/1").json()["document"] == saved
    spec = importlib.util.spec_from_file_location("mvp_input_boundary", ROOT / "update/tools/import_byul_inputs.py")
    boundary = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(boundary)
    from guard.models import load_pack
    pack = load_pack(prepared_client.app.state.byul.settings.update_dir / "packs/PACK-BASELINE")
    _, converted, _ = boundary.convert_documents(saved_env["document"], saved, pack,
        prepared_client.app.state.byul.contracts, as_of=datetime(2026, 11, 12, tzinfo=timezone.utc))
    assert converted.values == saved["verification"]["values"]
    # 저장 시 생성된 연결값은 이후 카드 편집을 따라 갱신된다.
    saved["framework_values"]["requirements"]["3.1"]["numbers"]["max_input_tokens"]["value"] = 2048
    edited = prepared_client.post("/api/policies", json=saved)
    assert edited.status_code == 201, edited.text
    assert edited.json()["saved"]["document"]["verification"]["values"]["REQ-09"]["max_input_tokens"] == 2048


@pytest.mark.parametrize("bad", [None, 3, True])
def test_unknown_is_not_false_and_wrong_types_are_rejected(prepared_client, bad):
    doc = policy()
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": bad})
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    if bad is None:
        assert not preview["errors"]
        assert "allowed_devices" not in preview["document"]["verification"]["values"].get("PLT-01", {})
        assert any("미확인" in g["message"] for g in preview["gaps"])
    else:
        assert preview["errors"]
        assert prepared_client.post("/api/policies", json=doc).status_code == 422


def test_manual_json_conflict_is_not_overwritten(prepared_client):
    doc = policy()
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": []})
    doc["verification"]["values"] = {"PLT-01": {"allowed_devices": ["/dev/gpu"]}}
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    assert preview["errors"]
    assert preview["document"]["verification"]["values"]["PLT-01"]["allowed_devices"] == ["/dev/gpu"]
    assert prepared_client.post("/api/policies", json=doc).status_code == 422


def test_json_boolean_is_not_silently_replaced_with_an_equal_integer(prepared_client):
    doc = policy()
    doc["framework_values"]["requirements"]["3.1"] = answer(numbers={"max_input_tokens": {
        "value": 1, "unit": "token", "scope": "요청별", "condition": None, "status": "confirmed"}})
    doc["verification"]["values"] = {"REQ-09": {"max_input_tokens": True}}
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    assert preview["errors"]
    assert preview["document"]["verification"]["values"]["REQ-09"]["max_input_tokens"] is True


def test_keep_materializes_approved_values_and_pending_stays_pending(prepared_client):
    doc = policy()
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": ["/dev/gpu"]})
    assert prepared_client.post("/api/policies", json=doc).status_code == 201
    approved = prepared_client.post("/api/policies/mvp-policy/confirm", json={"revision": 1, "approved_by": "fixture-user"})
    assert approved.status_code == 201
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": []}, decision="keep")
    doc["framework_values"]["requirements"]["5.6"] = answer({"cache_timing_max_auc_percent": 90}, decision="needs_confirmation")
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    assert preview["previous_policy"]["revision"] == 2
    kept = preview["document"]["framework_values"]["requirements"]["1.4"]
    assert kept["values"]["allowed_devices"] == ["/dev/gpu"]
    assert "sha256:" in kept["note"]
    assert "REQ-17" not in preview["document"]["verification"]["values"]
    assert any("확인" in g["message"] for g in preview["gaps"])


def test_keep_without_base_does_not_use_submitted_values(prepared_client):
    doc = policy()
    doc["framework_values"]["requirements"]["1.4"] = answer({"allowed_devices": []}, decision="keep")
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    assert not preview["previous_policy"]
    assert "PLT-01" not in preview["document"]["verification"]["values"]
    assert any("이전 승인" in g["message"] for g in preview["gaps"])


def test_explicit_unknown_does_not_become_fixed_mvp_default(prepared_client):
    doc = policy()
    doc["framework_values"]["requirements"]["5.6"] = answer({"cache_timing_max_auc_percent": None})
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": doc}).json()
    assert "cache_timing_max_auc_percent" not in preview["document"]["verification"]["values"].get("REQ-17", {})


def test_pack_is_assigned_by_backend_without_client_catalog(store_client):
    doc = policy()
    doc["verification"]["pack"] = {"id": "", "revision": 0}
    response = store_client.post("/api/policies", json=doc)
    assert response.status_code == 201, response.text
    assert response.json()["saved"]["document"]["verification"]["pack"] == MVP_PACK


def test_fixed_policy_file_reuses_exact_values_and_excludes_sensor_hashes(prepared_client, tmp_path):
    source = policy()
    source["data_origin"] = "DECLARED"
    source["verification"]["values"] = {"REQ-03": {"general_allowed_kinds": ["inference"]},
        "LIF-13": {"sensor_target_sha256": "f" * 64}}
    path = tmp_path / "mvp-policy.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    state = prepared_client.app.state.byul
    state.settings = replace(state.settings, mvp_policy_file=path)
    preview = prepared_client.post("/api/inputs/policy-preview", json={"document": policy()}).json()
    assert preview["document"]["verification"]["values"]["REQ-03"]["general_allowed_kinds"] == ["inference"]
    assert "LIF-13" not in preview["document"]["verification"]["values"]
    assert preview["document"]["data_origin"] == "EXAMPLE"
    source["data_origin"] = "EXAMPLE"
    path.write_text(json.dumps(source), encoding="utf-8")
    catalog = prepared_client.get("/api/inputs/catalog").json()
    assert "REQ-03" not in catalog["fixed_values"]
    assert any("확인하지 못" in message for message in catalog["limitations"])


def test_environment_contradiction_and_snapshot_mismatch(store_client):
    env = load_json(EXAMPLES / "environment.json")
    env["framework_profile"] = {"catalog_version": "byul-framework-input/0.1", "framework_checklist_sha256": None,
        "role": None, "profile_status": None, "absence_claims": {}, "conditions": {
        "entry": {"values": {"direct_block": ["none", "firewall"]}, "origin": None, "source": None}}}
    assert store_client.post("/api/environments", json=env).status_code == 422
    env["framework_profile"]["conditions"]["entry"]["values"]["direct_block"] = ["firewall"]
    saved = store_client.post("/api/environments", json=env).json()["saved"]
    doc = policy()
    doc["environment_ref"] = {"id": env["environment_id"], "revision": 1, "sha256": "f" * 64}
    assert store_client.post("/api/policies", json=doc).status_code == 422
