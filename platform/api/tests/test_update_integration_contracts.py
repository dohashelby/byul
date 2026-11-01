"""현재 guard 생산 RPT와 공용 계약의 교차 회귀. 자료는 전부 EXAMPLE이다.

guard는 CI의 `uv run --with ../../update` overlay로 설치한다.
제품 변환기·스키마를 복제하거나 실제 엔진을 시작하지 않는다.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from byul_platform.config import Settings
from byul_platform.contracts import canonical_sha256
from byul_platform.main import create_app
from byul_platform.sources.update import rpt_to_verification_result, report_view
from guard.collectors.file import load_environment, load_policy
from guard.core.judge import judge_plan
from guard.core.report import build_report
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry as executor_registry
from guard.executors.container_config import ContainerConfigExecutor
from guard.models import Environment, Evidence, Policy, Result, load_pack, new_id
from guard.store import Store


ROOT = Path(__file__).resolve().parents[3]
PACK_PATH = ROOT / "update" / "packs" / "PACK-BASELINE"
T0 = datetime(2026, 10, 25, tzinfo=timezone.utc)
spec = importlib.util.spec_from_file_location("byul_input_boundary", ROOT / "update/tools/import_byul_inputs.py")
boundary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boundary)


@pytest.fixture(scope="module")
def current_pack():
    return load_pack(PACK_PATH)


def _documents(current_pack):
    examples = ROOT / "contracts" / "examples"
    env = json.loads((examples / "environment.json").read_text(encoding="utf-8"))
    pol = json.loads((examples / "policy.json").read_text(encoding="utf-8"))
    # 새 EXAMPLE 입력을 만든다. 계약 원본 예시·정책 값·승인을 덮어쓰지 않는다.
    pol["verification"]["pack"] = {"id": current_pack.ref().id, "revision": current_pack.ref().revision}
    pol["environment_ref"]["sha256"] = canonical_sha256(env)
    return env, pol


def test_input_boundary_preserves_origins_refs_and_unknowns(contracts, current_pack):
    source_env, source_pol = _documents(current_pack)
    before = deepcopy((source_env, source_pol))
    env, pol, mapping = boundary.convert_documents(source_env, source_pol, current_pack, contracts, as_of=T0)
    assert (source_env, source_pol) == before
    assert env.hash_ok() and pol.hash_ok()
    assert env.id.startswith("ENV-") and pol.id.startswith("POL-")
    assert env.id != source_env["environment_id"] and pol.id != source_pol["policy_id"]
    assert pol.pack == current_pack.ref()
    assert pol.values == source_pol["verification"]["values"]
    assert pol.scope.model_dump() == source_pol["verification"]["scope"]
    assert env.data_origin.value == pol.data_origin.value == "EXAMPLE"
    assert env.engine.image_digest is None and env.adapter is None
    assert env.runtime.gpu_count is None and env.runtime.cpu is None
    assert env.model.revision is None and env.model.inventory_sha256 is None
    assert env.facts["dynamic_import"].value is None
    assert env.facts["dynamic_import"].origin is None
    assert env.facts["shared_or_remote_weights"].absence_evidence is None
    assert mapping["environment_ref"] == source_pol["environment_ref"]
    assert mapping["byul_policy_ref"]["sha256"] == canonical_sha256(source_pol)
    assert mapping["guard_policy_ref"] == pol.ref().model_dump(mode="json")
    assert mapping["byul_policy_ref"]["sha256"] != pol.content_sha256
    assert "POLICY_VALIDITY_MUST_BE_RECHECKED_AT_EXECUTION" in mapping["limitations"]


@pytest.mark.parametrize("mutation,code", [
    ("environment-hash", "POLICY_ENVIRONMENT_REF_MISMATCH"),
    ("environment-null", "POLICY_ENVIRONMENT_REF_MISMATCH"),
    ("environment-revision", "POLICY_ENVIRONMENT_REF_MISMATCH"),
    ("target", "POLICY_TARGET_REF_MISMATCH"),
    ("pack", "POLICY_PACK_REF_MISMATCH"),
    ("topology", "RUNTIME_TOPOLOGY_UNDECLARED"),
    ("extra", "ENVIRONMENT_SCHEMA_INVALID"),
    ("confirmed-without-approval", "POLICY_SCHEMA_INVALID"),
    ("expired", "POLICY_EXPIRED"),
    ("future", "POLICY_NOT_YET_VALID"),
])
def test_input_boundary_rejects_unproven_mapping(contracts, current_pack, mutation, code):
    env, pol = _documents(current_pack)
    if mutation == "environment-hash":
        pol["environment_ref"]["sha256"] = "0" * 64
    elif mutation == "environment-null":
        pol["environment_ref"] = None
    elif mutation == "environment-revision":
        pol["environment_ref"]["revision"] += 1
    elif mutation == "target":
        pol["target_ref"] = "different-target"
    elif mutation == "pack":
        pol["verification"]["pack"]["revision"] += 1
    elif mutation == "topology":
        env["target"]["runtime"].pop("nodes")
        pol["environment_ref"]["sha256"] = canonical_sha256(env)
    elif mutation == "extra":
        env["invented_field"] = "not-in-contract"
    elif mutation == "confirmed-without-approval":
        pol["approval"] = None
    elif mutation == "expired":
        pol["validity"]["valid_until"] = "2026-10-25T00:00:00Z"
    else:
        pol["validity"]["valid_from"] = "2026-10-26T00:00:00Z"
    with pytest.raises(boundary.InputBoundaryError, match=f"^{code}$"):
        boundary.convert_documents(env, pol, current_pack, contracts, as_of=T0)


def test_source_only_fields_are_preserved_without_inventing_guard_values(contracts, current_pack):
    env, pol = _documents(current_pack)
    env["target"]["model"]["tokenizer_revision"] = "tokenizer-only-revision"
    env["target"]["runtime"]["gpu_count"] = 0
    pol["environment_ref"]["sha256"] = canonical_sha256(env)
    converted, _, mapping = boundary.convert_documents(env, pol, current_pack, contracts, as_of=T0)
    assert converted.model.revision is None
    assert converted.runtime.gpu_count == 0 and converted.runtime.cpu is None
    assert "TOKENIZER_REVISION_RETAINED_IN_SOURCE_ONLY" in mapping["limitations"]
    assert "RUNTIME_POLICY_RETAINED_IN_SOURCE_ONLY" in mapping["limitations"]
    assert env["target"]["model"]["tokenizer_revision"] == "tokenizer-only-revision"


def test_import_retains_exact_source_bytes_and_loadable_guard_objects(tmp_path, current_pack):
    env, pol = _documents(current_pack)
    env_path, pol_path = tmp_path / "byul-environment.json", tmp_path / "byul-policy.json"
    env_raw = (json.dumps(env, ensure_ascii=False, indent=2).replace("\n", "\r\n") + "\r\n").encode()
    pol_raw = json.dumps(pol, ensure_ascii=False, separators=(",", ":")).encode()
    env_path.write_bytes(env_raw)
    pol_path.write_bytes(pol_raw)
    output = tmp_path / "new-import"
    mapping = boundary.import_inputs(env_path, pol_path, PACK_PATH, output, as_of=T0)
    assert (output / "source-environment.json").read_bytes() == env_raw
    assert (output / "source-policy.json").read_bytes() == pol_raw
    loaded_env = load_environment(output / "environment.json")
    loaded_pol = load_policy(output / "policy.json", current_pack)
    assert loaded_env.ref().model_dump(mode="json") == mapping["guard_environment_ref"]
    assert loaded_pol.ref().model_dump(mode="json") == mapping["guard_policy_ref"]
    assert mapping["environment_ref"]["sha256"] == canonical_sha256(env)
    for item in mapping["files"]:
        raw = (output / item["path"]).read_bytes()
        assert item["bytes"] == len(raw) and item["sha256"] == hashlib.sha256(raw).hexdigest()
    assert mapping["files"][0]["sha256"] != mapping["environment_ref"]["sha256"]
    before = {p.name: p.read_bytes() for p in output.iterdir()}
    with pytest.raises(boundary.InputBoundaryError, match="OUTPUT_ALREADY_EXISTS"):
        boundary.import_inputs(env_path, pol_path, PACK_PATH, output, as_of=T0)
    assert {p.name: p.read_bytes() for p in output.iterdir()} == before


@pytest.mark.parametrize("raw,code", [
    (b'{"a":1,"a":2}', "JSON_DUPLICATE_KEY"),
    (b'{"a":NaN}', "JSON_NONFINITE_NUMBER"),
    (b'[]', "INPUT_DOCUMENT_NOT_OBJECT"),
])
def test_ambiguous_json_is_rejected_without_writing_output(tmp_path, raw, code):
    source = tmp_path / "input.json"
    source.write_bytes(raw)
    with pytest.raises(boundary.InputBoundaryError, match=code):
        boundary.read_document(source)


def test_cli_rejects_mismatch_before_creating_output(tmp_path, current_pack, capsys):
    env, pol = _documents(current_pack)
    pol["environment_ref"]["sha256"] = "0" * 64
    env_path, pol_path = tmp_path / "environment.json", tmp_path / "policy.json"
    env_path.write_text(json.dumps(env), encoding="utf-8")
    pol_path.write_text(json.dumps(pol), encoding="utf-8")
    output = tmp_path / "must-not-exist"
    result = boundary.main(["--environment", str(env_path), "--policy", str(pol_path),
                            "--pack", str(PACK_PATH), "--output", str(output),
                            "--as-of", T0.isoformat()])
    assert result == 2 and not output.exists()
    captured = capsys.readouterr()
    assert captured.err.strip() == "POLICY_ENVIRONMENT_REF_MISMATCH" and not captured.out


def _current_report(tmp_path, pack, *, carried=False):
    """소스의 select·Store·build_report로 만든 EXAMPLE RPT. 실제 엔진 실측 아님."""
    store = Store(tmp_path / "guard-store")
    env = Environment(id=new_id("ENV"), created_at=T0, data_origin="EXAMPLE",
                      engine={"name": "example-engine", "version": "1.0.0", "image_digest": "sha256:" + "a" * 64},
                      facts={"gateway": {"value": True, "origin": "EXAMPLE", "source": "test"}}).seal()
    pol = Policy(id=new_id("POL"), created_at=T0, data_origin="EXAMPLE", status="DRAFT", pack=pack.ref(),
                 values={"REQ-01": {"allowed_callers": ["allowed"]}},
                 scope={"include": ["REQ-01"], "exclude": []}).seal()
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    if carried:
        evd = Evidence(id=new_id("EVD"), created_at=T0, data_origin="EXAMPLE", refs=[plan.ref(), env.ref()],
                       plan=plan.ref(), environment=env.ref(), check_id="REQ-01", side="single",
                       execution={"status": "COMPLETED", "started_at": T0, "finished_at": T0},
                       normal_control={"attempted": True, "succeeded": True}).seal()
        store.put(evd)
        result = Result(id=new_id("RES"), created_at=T0, data_origin="EXAMPLE", refs=[evd.ref()],
                        evidence=evd.ref(), check_id="REQ-01", side="single",
                        judge={"name": "example-contract-test", "version": "1", "params_sha256": "a" * 64},
                        verdicts={"security": "PASS", "functional": "UNKNOWN", "performance": "UNKNOWN"},
                        verification_level="EXAMPLE").seal()
        store.put(result)
        data = plan.to_dict()
        row = next(item for item in data["items"] if item["check_id"] == "REQ-01")
        row.update(selected=False, not_selected_reason="CARRIED_OVER", triggers=[{"type": "INPUT", "refs": []}],
                   carried_over={"result": result.ref().model_dump(mode="json"),
                                 "evidence": evd.ref().model_dump(mode="json"),
                                 "image_digest": env.engine.image_digest, "judged_at": T0.isoformat(),
                                 "reason": "EXAMPLE exact-reference regression"})
        data.update(id=new_id("PLAN"), change_type="INPUT")
        data["inputs"]["previous_plan"] = plan.ref().model_dump(mode="json")
        data["refs"].extend(ref.model_dump(mode="json") for ref in (plan.ref(), evd.ref(), result.ref()))
        plan = type(plan).model_validate(data).seal()
        store.put(plan)
    return build_report(store, pack, plan, T0 + timedelta(days=1))


def test_current_guard_generated_rpt_obeys_platform_contract_without_rejudging(tmp_path, contracts, current_pack):
    report = _current_report(tmp_path, current_pack)
    rpt = report.to_dict()
    before = deepcopy(rpt)
    converted = rpt_to_verification_result(rpt)
    assert contracts.errors("verification-result", converted) == []
    assert rpt == before and report.hash_ok()
    assert converted["report_ref"] == report.ref().model_dump(mode="json")
    assert converted["official"] is False and converted["data_origin"] == "EXAMPLE"
    assert converted["pack"]["revision"] == current_pack.ref().revision
    assert converted["byul_policy_ref"] is None
    row = next(c for c in converted["checks"] if c["check_id"] == "REQ-01")
    original = next(c for c in rpt["by_check"] if c["check_id"] == "REQ-01")
    assert row["sides"]["single"]["security"] == original["single"]["security"] == "UNKNOWN"
    assert row["sides"]["single"]["functional"] is None
    assert row["sides"]["single"]["performance"] is None
    assert row["sides"]["single"]["result_ref"] is None
    assert converted["inputs_manifest"] == rpt["inputs_manifest"]


def test_u1_carried_details_remain_in_original_and_are_not_new_execution(tmp_path, contracts, current_pack):
    report = _current_report(tmp_path, current_pack, carried=True)
    rpt = report.to_dict()
    before = deepcopy(rpt)
    original = next(c for c in rpt["by_check"] if c["check_id"] == "REQ-01")
    assert original["carried_over"] and original["previous_result"] and original["triggers"]
    assert rpt["change_summary"]["carried_results"] and rpt["carry_counts"]["executed"] == 0
    converted = rpt_to_verification_result(rpt)
    assert contracts.errors("verification-result", converted) == []
    row = next(c for c in converted["checks"] if c["check_id"] == "REQ-01")
    assert row["selected"] is False and row["not_selected_reason"] == "CARRIED_OVER"
    assert row["sides"]["single"]["result_ref"] == original["carried_over"]["result"]
    assert row["sides"]["single"]["verification_level"] == "EXAMPLE"
    assert converted["summary"]["evidence_counts"] == {}
    assert converted["limitations"] == rpt["limitations"]
    assert not {"triggers", "carried_over", "previous_result"} & row.keys()
    assert "change_summary" not in converted and "carry_counts" not in converted
    assert report_view(converted)["counts"]["selected"] == 0
    assert rpt == before and report.hash_ok()

    # 0.2는 비교 enum은 지원하지만 이월 상세의 임의 필드 추가는 거부한다.
    candidate = deepcopy(converted)
    next(c for c in candidate["checks"] if c["check_id"] == "REQ-01")["comparison"] = "CARRIED_OVER"
    assert contracts.errors("verification-result", candidate) == []
    next(c for c in candidate["checks"] if c["check_id"] == "REQ-01")["carried_over"] = original["carried_over"]
    assert contracts.errors("verification-result", candidate)


def test_platform_reads_current_rpt_and_keeps_u1_raw_detail(tmp_path, current_pack):
    report = _current_report(tmp_path, current_pack, carried=True)
    reports = tmp_path / "reports"
    reports.mkdir()
    path = reports / "report.json"
    raw = json.dumps(report.to_dict(), ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes(raw)
    app = create_app(Settings(update_reports_dir=reports, web_dist=tmp_path / "absent-web"))
    with TestClient(app) as client:
        listing = client.get("/api/update/results")
        assert listing.status_code == 200
        item = listing.json()["items"][0]
        assert item["report_ref"] == report.ref().model_dump(mode="json")
        response = client.get(f"/api/update/results/{report.id}")
        assert response.status_code == 200
        detail = response.json()
        carried = next(row for row in report.by_check if row.check_id == "REQ-01").carried_over
        assert detail["raw_checks"]["REQ-01"]["carried_over"]["result"] == carried.result.model_dump(mode="json")
        assert detail["report"]["counts"]["selected"] == 0
        assert detail["item"]["official"] is False
    assert path.read_bytes() == raw


@pytest.mark.parametrize("approved_matches,expected", [
    pytest.param(False, "FAIL", id="unapproved-running-image"),
    pytest.param(True, "UNKNOWN", id="approved-image-partial-supply-scope"),
])
def test_sup04_executor_to_rpt_preserves_contract_verdicts_and_exact_refs(
        tmp_path, monkeypatch, contracts, current_pack, approved_matches, expected):
    """제품 실행·판정·보고 경로를 대역으로 통과시킨다. 실제 Docker/vLLM 실증 아님."""
    pinned = "example.invalid/cpu-engine@sha256:" + "a" * 64
    other = "example.invalid/cpu-engine@sha256:" + "c" * 64
    image_id, container, run_id = "sha256:" + "b" * 64, "guard-contract-example-engine", "example-contract-run"
    target = {"lab": True, "run_id": run_id, "probe_image": "unused-example-probe",
              "origins": {"trusted": {"network": "example-trusted"}},
              "gateway": {"url": "http://gateway:8080"},
              "engine": {"container": container, "image_digest": pinned,
                         "trusted_url": "http://engine:8000", "ready_timeout": 1}}
    target_path = tmp_path / "target.json"
    class ExampleDocker:
        def inspect_json(self, name, *, timeout):
            assert name == container and timeout > 0
            return {"Id": "d" * 64, "Name": "/" + container, "Image": image_id,
                    "State": {"Running": True, "StartedAt": "2026-10-26T00:00:00Z"},
                    "Config": {"Image": pinned, "Labels": {"guard.run": run_id}}}

        def image_info(self, identifier, *, timeout):
            assert identifier == image_id and timeout > 0
            return {"digests": [pinned], "os": "linux", "architecture": "amd64"}

        def exec(self, *args, **kwargs):
            raise AssertionError("running image profile must not execute sentinel or package commands")

    def example_http(network, requests, timeout):
        assert network == "example-trusted" and timeout > 0
        responses = []
        for request in requests:
            response = {"id": request["id"], "method": request["method"],
                        "url": request["url"], "outcome": 200}
            if request["method"] == "POST":
                response["body"] = json.dumps({
                    "model": request["body"]["model"],
                    "choices": [{"text": "EXAMPLE", "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                })
            responses.append(response)
        return responses

    monkeypatch.setitem(executor_registry.EXECUTORS, "container_config:contract-sim", lambda raw:
                        ContainerConfigExecutor({"target": str(target_path)},
                                                container_probe=example_http, docker=ExampleDocker()))
    store = Store(tmp_path / "supply-store")
    # BYUL environment와 같은 bare manifest 해시를 쓰며 repository는 명시 target에서만 얻는다.
    env = Environment(id=new_id("ENV"), created_at=T0, data_origin="EXAMPLE",
                      engine={"name": "vllm", "version": "0.32.0", "image_digest": pinned.partition("@")[2]},
                      model={"id": "example-model"}).seal()
    target["environment_ref"] = env.ref().model_dump(mode="json")
    target_path.write_text(json.dumps(target), encoding="utf-8")
    pol = Policy(id=new_id("POL"), created_at=T0, data_origin="EXAMPLE", status="DRAFT",
                 pack=current_pack.ref(), scope={"include": ["SUP-04"], "exclude": []},
                 values={"SUP-04": {"approved_image_digests": [pinned if approved_matches else other]}}).seal()
    store.put_many([env, pol])
    plan = select(current_pack, [env], pol)
    store.put(plan)
    (evidence,) = run_plan(store, current_pack, plan,
                          RunConfig(executors={"container_config": "contract-sim"}, adapter="vllm"))
    (result,) = judge_plan(store, current_pack, plan, as_of=T0)
    assert evidence.execution.status.value == "COMPLETED"
    assert evidence.normal_control.succeeded is True
    assert any(not step.attempted for step in evidence.challenges)
    assert evidence.observations["running_image_identity_verified"].value is True
    assert evidence.observations["supply_execution_scope_complete"].value is False
    assert result.verdicts.security.value == expected
    assert result.verdicts.functional.value == result.verdicts.performance.value == "UNKNOWN"
    originals = {obj.id: obj.to_dict() for obj in (env, pol, plan, evidence, result)}
    report = build_report(store, current_pack, plan, T0 + timedelta(days=1))
    rpt = report.to_dict()
    before = deepcopy(rpt)
    converted = rpt_to_verification_result(rpt)
    assert contracts.errors("verification-result", converted) == []
    check = next(row for row in converted["checks"] if row["check_id"] == "SUP-04")
    assert check["selected"] is True and check["sides"]["single"]["security"] == expected
    assert check["sides"]["single"]["result_ref"] == result.ref().model_dump(mode="json")
    assert check["sides"]["single"]["verification_level"] == "EXAMPLE"
    assert check["sides"]["single"]["functional"] == check["sides"]["single"]["performance"] == "UNKNOWN"
    assert result.evidence == evidence.ref()
    assert evidence.ref().model_dump(mode="json") in converted["inputs_manifest"]
    assert result.ref().model_dump(mode="json") in converted["inputs_manifest"]
    assert converted["report_ref"] == report.ref().model_dump(mode="json")
    assert converted["data_origin"] == evidence.data_origin.value == result.data_origin.value == "EXAMPLE"
    assert converted["official"] is False and converted["policy_status"] == "DRAFT"
    assert any("running-image-approval-v1" in reason for reason in converted["limitations"])
    assert rpt == before and report.hash_ok()
    for obj in (env, pol, plan, evidence, result):
        assert store.resolve(obj.ref()).to_dict() == originals[obj.id]


@pytest.mark.parametrize("approved", [True, False])
def test_sup03_saved_observation_projects_unknown_without_consumption_claim(
        tmp_path, contracts, current_pack, approved):
    """수집기는 guard suite에서 검증한다. 여기서는 EXAMPLE 저장 EVD의 경계를 확인한다."""
    store = Store(tmp_path / "observation-store")
    env = Environment(id=new_id("ENV"), created_at=T0, data_origin="EXAMPLE",
                      engine={"name": "vllm", "version": "0.31.0"}, model={"id": "EXAMPLE-model"}).seal()
    pol = Policy(id=new_id("POL"), created_at=T0, data_origin="EXAMPLE", status="DRAFT",
                 pack=current_pack.ref(), scope={"include": ["SUP-03"], "exclude": []},
                 values={"SUP-03": {"allowed_template_sha256": ["a" * 64] if approved else []}}).seal()
    store.put_many([env, pol])
    plan = select(current_pack, [env], pol)
    store.put(plan)
    check = current_pack.checks["SUP-03"]
    observations = {
        "selected_template_sha256": "a" * 64, "template_identity_verified": True,
        "selected_template_approved": approved, "input_projection_exact_match": True,
        "template_consumer_verified": False, "supply_scope_complete": False,
        "functional_probes_planned": ["POST /v1/chat/completions"],
        "functional_probe_results": {"POST /v1/chat/completions": "pass"},
    }
    evidence = Evidence(
        id=new_id("EVD"), created_at=T0, data_origin="EXAMPLE",
        refs=[env.ref(), pol.ref(), plan.ref()], plan=plan.ref(), check_id="SUP-03", side="single",
        environment=env.ref(), execution={"status": "COMPLETED", "executor": {
            "name": "example-contract-fixture", "version": "1", "config_sha256": "b" * 64}},
        normal_control={"attempted": True, "succeeded": True},
        challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
        observations={key: {"value": value, "collected": True} for key, value in observations.items()},
    ).seal()
    store.put(evidence)
    (result,) = judge_plan(store, current_pack, plan, as_of=T0)
    report = build_report(store, current_pack, plan, T0)
    before = deepcopy(report.to_dict())
    converted = rpt_to_verification_result(before)
    assert contracts.errors("verification-result", converted) == []
    row = next(item for item in converted["checks"] if item["check_id"] == "SUP-03")
    side = row["sides"]["single"]
    assert side["security"] == side["functional"] == side["performance"] == "UNKNOWN"
    assert side["result_ref"] == result.ref().model_dump(mode="json")
    assert result.evidence == evidence.ref()
    assert evidence.ref().model_dump(mode="json") in converted["inputs_manifest"]
    assert converted["official"] is False and converted["data_origin"] == "EXAMPLE"
    assert any("API" in limitation and "projection" in limitation for limitation in converted["limitations"])
    assert report.to_dict() == before and report.hash_ok()
    assert store.resolve(evidence.ref()).observations["selected_template_approved"].value is approved
