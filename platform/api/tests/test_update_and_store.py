"""update 보고서 변환, 환경·정책 판본 저장."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.contracts import canonical_sha256
from byul_platform.diff import json_diff
from byul_platform.main import create_app
from byul_platform.sources.update import rpt_to_verification_result
from byul_platform.store import FileDocumentStore, RevisionExists
from conftest import EXAMPLES, FIXTURES, load_json

RPT = FIXTURES / "update" / "RPT-01M598RR3HPSJNHA1MENFA6P74.json"


# --- update ------------------------------------------------------------------


def test_rpt_conversion_matches_contract_example(contracts):
    converted = rpt_to_verification_result(load_json(RPT))
    assert contracts.errors("verification-result", converted) == []
    example = load_json(EXAMPLES / "verification-result.json")
    # 예시는 RPT를 점검 1개로 줄인 것이다. 나머지 필드와 첫 점검이 같아야 한다.
    for key in example:
        if key in ("checks", "summary", "limitations"):
            continue
        assert converted[key] == example[key], key
    assert converted["checks"][0] == example["checks"][0]
    assert {a: v["security"] for a, v in converted["summary"]["by_area"].items()} == {
        a: v["security"] for a, v in example["summary"]["by_area"].items()
    }
    assert set(example["limitations"]) <= set(converted["limitations"])


def test_rpt_verdicts_are_not_recomputed():
    rpt = load_json(RPT)
    rpt["by_area"]["REQ"]["security"] = "PASS"  # 원본 값을 바꾸면 그대로 따라가야 한다.
    rpt["by_check"][0]["single"]["security"] = "FAIL"
    converted = rpt_to_verification_result(rpt)
    assert converted["summary"]["by_area"]["REQ"]["security"] == "PASS"
    assert converted["checks"][0]["sides"]["single"]["security"] == "FAIL"


def test_reports_dir_source(tmp_path):
    reports = tmp_path / "reports"
    (reports / "nested").mkdir(parents=True)
    shutil.copy(RPT, reports / "nested" / RPT.name)
    (reports / "observed.json").write_text('{"kind": "not-a-report"}')
    broken = load_json(RPT)
    broken["id"] = "RPT-BROKEN"
    (reports / "RPT-broken.json").write_text(json.dumps(broken))
    app = create_app(Settings(update_reports_dir=reports, web_dist=tmp_path / "x"))
    with TestClient(app) as c:
        body = c.get("/api/update/results").json()
        assert body["source"]["mode"] == "live"
        assert [r["report_ref"]["id"] for r in body["items"]] == ["RPT-01M598RR3HPSJNHA1MENFA6P74"]
        assert len(body["invalid"]) == 1  # 형식이 틀린 보고서는 버리지 않고 형식 오류로 낸다
        detail = c.get("/api/update/results/RPT-01M598RR3HPSJNHA1MENFA6P74").json()
        assert detail["raw_checks"]["SUP-01"]["question"]
        assert len(detail["item"]["checks"]) == 77


# --- store -------------------------------------------------------------------


def _env() -> dict:
    env = load_json(EXAMPLES / "environment.json")
    env["environment_id"] = "corp-env"
    env["data_origin"] = "DECLARED"
    return env


def _policy() -> dict:
    pol = load_json(EXAMPLES / "policy.json")
    pol["policy_id"] = "corp-policy"
    pol["status"] = "DRAFT"
    pol["approval"] = None
    return pol


def test_store_never_overwrites(tmp_path):
    store = FileDocumentStore(tmp_path, "environments")
    first = store.append("e1", 1, {"a": 1}, status="DRAFT", label=None)
    path = tmp_path / "environments" / "e1" / "000001.json"
    before = path.read_bytes()
    with pytest.raises(RevisionExists):
        store.append("e1", 1, {"a": 2}, status="DRAFT", label=None)
    assert path.read_bytes() == before
    assert store.next_revision("e1") == 2
    assert first.sha256 == canonical_sha256({"a": 1})


def test_saving_twice_creates_new_revisions(store_client: TestClient, tmp_path: Path):
    env = _env()
    r1 = store_client.post("/api/environments", json=env)
    assert r1.status_code == 201, r1.text
    assert r1.json()["saved"]["meta"]["revision"] == 1
    rev1 = store_client.get("/api/environments/corp-env/revisions/1").json()

    env2 = copy.deepcopy(env)
    env2["target"]["engine"]["version"] = "0.31.1"
    env2["revision"] = 1  # 클라이언트가 같은 판본을 보내도 새 판본으로 저장된다
    r2 = store_client.post("/api/environments", json=env2)
    assert r2.status_code == 201
    assert r2.json()["saved"]["meta"]["revision"] == 2
    assert r2.json()["saved"]["document"]["revision"] == 2

    assert store_client.get("/api/environments/corp-env/revisions/1").json() == rev1
    revs = store_client.get("/api/environments/corp-env/revisions").json()["items"]
    assert [r["revision"] for r in revs] == [1, 2]
    files = sorted(p.name for p in (tmp_path / "data" / "environments" / "corp-env").iterdir())
    assert files == ["000001.json", "000002.json"]

    diff = store_client.get("/api/environments/corp-env/diff", params={"from_revision": 1, "to_revision": 2}).json()
    paths = {e["path"]: e for e in diff["entries"]}
    assert paths["/target/engine/version"]["before"] == "0.31.0"
    assert paths["/target/engine/version"]["after"] == "0.31.1"
    assert "/revision" in paths


def test_environment_schema_errors_point_at_fields(store_client):
    env = _env()
    env["target"]["engine"]["image_digest"] = "not-a-digest"
    del env["target"]["model"]
    r = store_client.post("/api/environments", json=env)
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "SCHEMA_INVALID"
    paths = {e["path"] for e in body["errors"]}
    assert "/target/engine/image_digest" in paths
    assert "/target/model" in paths
    assert store_client.get("/api/environments").json()["items"] == []


def test_false_fact_without_absence_evidence_warns(store_client):
    r = store_client.post("/api/environments", json=_env())
    warnings = r.json()["warnings"]
    assert warnings == [
        {"path": "/facts/shared_or_remote_weights/absence_evidence", "message": warnings[0]["message"]}
    ]


def test_policy_saved_as_draft_only(store_client):
    env = store_client.post("/api/environments", json=_env()).json()["saved"]
    pol = _policy()
    pol["environment_ref"] = {"id": "corp-env", "revision": 1, "sha256": ""}
    r = store_client.post("/api/policies", json=pol)
    assert r.status_code == 201, r.text
    saved = r.json()["saved"]
    assert saved["document"]["status"] == "DRAFT"
    assert saved["meta"]["status"] == "DRAFT"
    assert saved["document"]["environment_ref"]["sha256"] == env["meta"]["sha256"]
    assert saved["meta"]["sha256"] == canonical_sha256(saved["document"])

    confirmed = _policy()
    confirmed["status"] = "CONFIRMED"
    r = store_client.post("/api/policies", json=confirmed)
    assert r.status_code == 422
    assert r.json()["code"] == "STATUS_NOT_ALLOWED"

    wrong = _policy()
    wrong["environment_ref"] = {"id": "corp-env", "revision": 1, "sha256": "0" * 64}
    r = store_client.post("/api/policies", json=wrong)
    assert r.status_code == 422
    assert r.json()["errors"][0]["path"] == "/environment_ref/sha256"


def test_bad_doc_ids_are_rejected(store_client):
    assert store_client.get("/api/environments/..%2F..%2Fetc/revisions").status_code == 404
    env = _env()
    env["environment_id"] = "../escape"
    assert store_client.post("/api/environments", json=env).status_code == 422


def test_json_diff():
    assert json_diff({"a": 1, "b": [1, 2]}, {"a": 1, "b": [1, 3], "c": None}) == [
        {"path": "/b/1", "change": "changed", "before": 2, "after": 3},
        {"path": "/c", "change": "added", "before": None, "after": None},
    ]
    assert json_diff({"x": 1}, {"x": True}) == [{"path": "/x", "change": "changed", "before": 1, "after": True}]
    assert json_diff({"a/b": 1}, {}) == [{"path": "/a~1b", "change": "removed", "before": 1, "after": None}]
