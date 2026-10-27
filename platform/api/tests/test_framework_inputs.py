"""프레임워크 입력(environment.framework_profile, policy.framework_values) 저장과 비사용 근거 경고."""

from __future__ import annotations

import copy

from fastapi.testclient import TestClient

from byul_platform.routes.documents import environment_snapshot_sha256
from conftest import EXAMPLES, load_json

CHECKLIST = "745e18640bc0c405baee1883dee53d1f35b644f0bc6116705a880b91893c590f"


def _env() -> dict:
    env = load_json(EXAMPLES / "environment.json")
    env["environment_id"] = "fw-env"
    env["data_origin"] = "DECLARED"
    env["framework_profile"] = {
        "catalog_version": "byul-framework-input/0.1",
        "framework_checklist_sha256": CHECKLIST,
        "role": "current",
        "profile_status": "observed",
        "conditions": {
            "entry": {"values": {"call_path": "gateway", "active_routes": ["chat", "metrics"], "identity_verifier": None}, "origin": "DECLARED", "source": "helm values"},
            "engine": {"values": {"dependencies": {"torch": "2.9.0", "cuda": None}, "package_list": True}, "origin": None, "source": None},
            "placement": {"values": {"tp_size": 2}, "origin": "MEASURED", "source": None},
        },
        "absence_claims": {},
    }
    return env


def _claim(env: dict) -> dict:
    engine = env["target"]["engine"]
    return {
        "status": "reviewed",
        "reviewer": "ops-kim",
        "reviewed_at": "2026-10-27T10:00:00+09:00",
        "disabled": True,
        "nonexposed": True,
        "references": ["deploy/values.yaml#L40"],
        "target": {
            "engine": engine["name"],
            "version": engine["version"],
            "source_commit": engine["source_commit"],
            "image_digest": engine["image_digest"],
            "environment_snapshot_sha256": environment_snapshot_sha256(env),
        },
    }


def _policy() -> dict:
    pol = load_json(EXAMPLES / "policy.json")
    pol["policy_id"] = "fw-policy"
    pol["status"] = "DRAFT"
    pol["approval"] = None
    pol["framework_values"] = {
        "catalog_version": "byul-framework-input/0.1",
        "framework_checklist_sha256": CHECKLIST,
        "requirements": {
            "3.1": {
                "decision": "change",
                "values": {"endpoints": ["chat"], "structured_output": "forbidden"},
                "numbers": {
                    "max_output_tokens": {"value": 128, "unit": "token", "scope": "모델별", "condition": None, "status": "confirmed"},
                    "schema_depth": {"value": None, "unit": "단계", "scope": "요청별", "condition": None, "status": "undecided"},
                },
                "basis": "보안정책 v3 §4.2",
                "note": None,
            },
            "6.1": {
                "decision": "needs_confirmation",
                "values": {},
                "numbers": {"max_concurrency": {"value": None, "unit": "요청", "scope": "전체", "condition": "피크 부하 측정", "status": "after_measurement"}},
                "basis": None,
                "note": None,
            },
        },
        "approval_terms": {"decision": "conditional", "scope": "현재 환경", "conditions": "측정 후 6.1 확정"},
    }
    return pol


def test_environment_with_framework_profile_saves(store_client: TestClient):
    env = _env()
    res = store_client.post("/api/environments", json=env)
    assert res.status_code == 201, res.text
    saved = res.json()["saved"]["document"]
    assert saved["framework_profile"]["conditions"]["placement"]["values"]["tp_size"] == 2
    # 근거가 없으면 근거 경고도 없다.
    assert not [w for w in res.json()["warnings"] if w["path"].startswith("/framework_profile")]


def test_environment_without_framework_profile_still_valid(store_client: TestClient):
    env = _env()
    del env["framework_profile"]
    assert store_client.post("/api/environments", json=env).status_code == 201
    env["framework_profile"] = None
    assert store_client.post("/api/environments", json=env).status_code == 201


def test_unknown_condition_and_bad_values_rejected(store_client: TestClient):
    env = _env()
    env["framework_profile"]["conditions"]["nope"] = {"values": {}, "origin": None, "source": None}
    res = store_client.post("/api/environments", json=env)
    assert res.status_code == 422
    env = _env()
    env["framework_profile"]["role"] = "production"
    assert store_client.post("/api/environments", json=env).status_code == 422
    env = _env()
    env["framework_profile"]["conditions"]["entry"]["values"]["Bad-Key"] = "x"
    assert store_client.post("/api/environments", json=env).status_code == 422


def test_reviewed_claim_requires_complete_fields(store_client: TestClient):
    env = _env()
    env["facts"]["shared_or_remote_weights"] = {"value": False, "origin": "DECLARED", "source": None, "absence_evidence": "claim"}
    claim = _claim(env)
    claim["references"] = []
    env["framework_profile"]["absence_claims"]["shared_or_remote_weights"] = claim
    res = store_client.post("/api/environments", json=env)
    assert res.status_code == 422
    assert any("absence_claims/shared_or_remote_weights" in e["path"] for e in res.json()["errors"])

    claim["status"] = "draft"  # 작성 중이면 빈 항목을 허용한다
    assert store_client.post("/api/environments", json=env).status_code == 201


def test_claim_snapshot_binding_and_warnings(store_client: TestClient):
    env = _env()
    env["facts"]["shared_or_remote_weights"] = {"value": False, "origin": "DECLARED", "source": None, "absence_evidence": "claim"}
    env["framework_profile"]["absence_claims"]["shared_or_remote_weights"] = _claim(env)
    res = store_client.post("/api/environments", json=env)
    assert res.status_code == 201, res.text
    assert not [w for w in res.json()["warnings"] if "absence_claims" in w["path"]]

    changed = copy.deepcopy(env)
    changed["target"]["engine"]["version"] = "0.31.1"  # 근거를 기록한 뒤 입력이 바뀜
    changed["framework_profile"]["profile_status"] = "planned"
    warnings = store_client.post("/api/environments", json=changed).json()["warnings"]
    paths = {w["path"] for w in warnings}
    assert "/framework_profile/absence_claims/shared_or_remote_weights/target/environment_snapshot_sha256" in paths
    assert "/framework_profile/absence_claims/shared_or_remote_weights/status" in paths


def test_policy_framework_values_save_and_reject(store_client: TestClient):
    pol = _policy()
    res = store_client.post("/api/policies", json=pol)
    assert res.status_code == 201, res.text
    assert res.json()["saved"]["document"]["framework_values"]["requirements"]["3.1"]["decision"] == "change"

    bad = _policy()
    bad["framework_values"]["requirements"]["9.9"] = bad["framework_values"]["requirements"]["3.1"]
    assert store_client.post("/api/policies", json=bad).status_code == 422
    bad = _policy()
    bad["framework_values"]["requirements"]["6.1"]["numbers"]["max_concurrency"]["status"] = "maybe"
    assert store_client.post("/api/policies", json=bad).status_code == 422
