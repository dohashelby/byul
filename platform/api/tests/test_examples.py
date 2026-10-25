"""계약 예시 전부가 API 응답 검증을 통과하고, 예시 모드로 모든 화면 API가 데이터를 낸다."""

from __future__ import annotations

from conftest import EXAMPLES, example_events, load_json


def test_all_contract_examples_pass_schema(contracts):
    cases = {
        "environment.json": "environment",
        "policy.json": "policy",
        "bundle/bundle.json": "policy-bundle",
        "run-context.json": "run-context",
        "verification-result.json": "verification-result",
        "runtime-status.json": "runtime-status",
        "revalidation-request.json": "revalidation-request",
        "evidence-index.json": "evidence-index",
    }
    for name, schema in cases.items():
        assert contracts.errors(schema, load_json(EXAMPLES / name)) == [], name
    for event in example_events():
        assert contracts.errors("runtime-event", event) == []


def test_example_mode_serves_every_example_without_invalid_rows(example_client):
    c = example_client
    meta = c.get("/api/meta").json()
    assert meta["example_mode"] is True
    assert meta["runtime_connection"] == "example"
    assert {s["name"]: s["mode"] for s in meta["sources"]} == {
        "runtime": "example", "update": "example", "evidence": "example", "store": "example",
    }

    events = c.get("/api/runtime/events").json()
    assert events["invalid"] == []
    assert sorted(e["event_id"] for e in events["items"]) == sorted(e["event_id"] for e in example_events())

    requests = c.get("/api/runtime/requests").json()
    assert requests["invalid"] == []
    assert {r["outcome"] for r in requests["items"]} == {"allowed", "denied"}

    status = c.get("/api/runtime/status").json()
    assert status["invalid"] is None
    assert status["item"] == load_json(EXAMPLES / "runtime-status.json")

    results = c.get("/api/update/results").json()
    assert results["invalid"] == []
    assert results["items"] == [load_json(EXAMPLES / "verification-result.json")]

    reval = c.get("/api/update/revalidation").json()
    assert reval["invalid"] == []
    assert reval["items"] == [load_json(EXAMPLES / "revalidation-request.json")]

    evidence = c.get("/api/evidence").json()
    assert evidence["invalid"] == []
    assert [e["item"] for e in evidence["items"]] == load_json(EXAMPLES / "evidence-index.json")["items"]

    envs = c.get("/api/environments").json()["items"]
    pols = c.get("/api/policies").json()["items"]
    assert [e["doc_id"] for e in envs] == ["lab-env-001"]
    assert [p["doc_id"] for p in pols] == ["local-policy-001"]
    env_doc = c.get("/api/environments/lab-env-001/revisions/1").json()["document"]
    assert env_doc == load_json(EXAMPLES / "environment.json")


def test_request_detail_links_evidence(example_client):
    detail = example_client.get("/api/runtime/requests/0123456789abcdef0123456789abcdef").json()
    assert detail["event"]["outcome"] == "allowed"
    assert [e["evidence_id"] for e in detail["evidence"]] == ["ev-request-0123"]
    assert example_client.get("/api/runtime/requests/ffffffffffffffffffffffffffffffff").status_code == 404


def test_overview(example_client):
    o = example_client.get("/api/overview").json()
    assert o["requests"]["counts"]["allowed"] == 1
    assert o["requests"]["counts"]["denied"] == 1
    assert o["open_revalidation"] == 1
    assert o["latest_verification"]["report_ref"]["id"] == "RPT-01M598RR3HPSJNHA1MENFA6P74"
    assert o["status"]["item"]["policy"]["approval_state"] == "verified"


def test_invalid_items_become_format_error_rows(example_client, contracts):
    from byul_platform.state import partition

    good = example_events()[0]
    bad = {**good, "outcome": "blocked"}
    valid, invalid = partition(contracts, "runtime-event", [good, bad])
    assert valid == [good]
    assert invalid[0]["index"] == 1 and invalid[0]["raw"] == bad
    assert any("/outcome" in e for e in invalid[0]["errors"])


def test_openapi_uses_contract_schemas(example_client):
    doc = example_client.get("/openapi.json").json()
    schemas = doc["components"]["schemas"]
    for name in ("RuntimeEvent", "RuntimeStatus", "VerificationResult", "Environment", "Policy", "EvidenceItem", "Common_policy_ref"):
        assert name in schemas
    assert schemas["RuntimeEventList"]["properties"]["items"]["items"] == {"$ref": "#/components/schemas/RuntimeEvent"}
    assert "x-contract" not in str(doc)


def test_web_not_built_and_unknown_api(example_client):
    assert example_client.get("/dashboard").status_code == 503
    assert example_client.get("/api/nope").status_code == 404
