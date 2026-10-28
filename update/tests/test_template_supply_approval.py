"""SUP-03 관측 전용 회귀. 모든 Docker/HTTP 관측은 EXAMPLE 모의 자료다."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.executors import template_supply_approval as supply
from guard.executors.reuse import preview_inputs
from guard.executors.template_validation import TemplateValidationExecutor
from guard.judges.declared import DeclaredJudge
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Evidence, Verdict
from tests.builders import T0, environment, evidence
from tests.test_template_validation import IMAGE, SHA, World, target


ROOT = Path(__file__).resolve().parents[1]
CID = "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
VALUES = {"allowed_template_sha256": [SHA]}


def definition():
    raw = yaml.safe_load((ROOT / "packs/PACK-BASELINE/checks/SUP-03.yaml").read_text(encoding="utf-8"))
    raw.update(yaml.safe_load((ROOT / "tools/overrides/SUP-03.yaml").read_text(encoding="utf-8")))
    return CheckDef.model_validate(raw)


class SupplyWorld(World):
    def inspect_json(self, container, *, timeout):
        result = super().inspect_json(container, timeout=timeout)
        result.update(Id=CID, Name="/EXAMPLE-engine", Image=IMAGE_ID)
        result["State"]["StartedAt"] = "2026-10-25T00:00:00Z"
        if self.changes.get("restart") and self.inspections > 2:
            result["State"]["StartedAt"] = "2026-10-25T00:01:00Z"
        if self.changes.get("empty_identity"):
            result["Id"] = ""
        if self.changes.get("invalid_started_at"):
            result["State"]["StartedAt"] = "EXAMPLE-not-a-timestamp"
        if self.changes.get("wrong_name"):
            result["Name"] = "/unrelated-engine"
        if self.changes.get("not_running"):
            result["State"]["Running"] = 1
        if self.changes.get("local_id_as_manifest"):
            result["Config"]["Image"] = IMAGE_ID
        return result

    def exec(self, container, argv, *, timeout):
        result = super().exec(container, argv, timeout=timeout)
        if self.changes.get("file_changes") and self.inspections > 2:
            result.stdout = json.dumps({"sha256": "d" * 64, "bytes": 100})
        return result

    def probe(self, network, requests, timeout):
        rows = super().probe(network, requests, timeout)
        for row in rows:
            row["body_preview"] = "EXAMPLE-private-response-preview"
            row["detail"] = "EXAMPLE-private-response-detail"
            value = json.loads(row["body"])
            if "choices" in value:
                value["choices"][0]["message"]["content"] = "EXAMPLE-private-model-content"
            if self.changes.get("wrong_model") and "model" in value:
                value["model"] = "unrelated-model"
            if self.changes.get("bool_count") and "count" in value:
                value["count"] = True
            row["body"] = json.dumps(value)
            if self.changes.get("invalid_capture_bytes"):
                row["body_bytes"] = True
        return rows


def run(tmp_path, world=None, values=None, cfg=None, target_change=None, env_change=None, check=None):
    world = world or SupplyWorld()
    data = target(world.context)
    data["comparison_config"]["launch"]["model"] = "fixture-model"
    if target_change:
        target_change(data)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    env_values = {"engine": {"name": "vllm", "version": "0.31.0", "image_digest": IMAGE},
                  "model": {"id": "fixture-model"}}
    if env_change:
        env_change(env_values)
    env = environment(**env_values)
    executor = TemplateValidationExecutor({"target": str(path), "request_timeout": 1,
                                          **(cfg or {})}, docker=world, container_probe=world.probe, environ={})
    draft = executor.run(check or definition(), env, deepcopy(VALUES if values is None else values),
                         VllmAdapter(), tmp_path / "work")
    return draft, world


def as_evidence(draft):
    return evidence(check_id="SUP-03", attempted=False, normal=draft.normal_control.model_dump(),
                    observations={key: value.model_dump() for key, value in draft.observations.items()})


def judge(draft, values=None):
    check = definition()
    return RuleTableJudge().judge(check, as_evidence(draft), check.judge.params, VALUES if values is None else values)


def summary(draft):
    return json.loads(draft.artifacts[1].path.read_text(encoding="utf-8"))["capture"]["summary"]


@pytest.mark.parametrize("approved,expected", [([SHA], True), (["c" * 64], False), ([], False)])
def test_api_projection_and_approval_are_observations_without_supply_consumption_verdict(tmp_path, approved, expected):
    values = {"allowed_template_sha256": approved}
    draft, world = run(tmp_path, values=values)
    assert draft.data_origin == "EXAMPLE"
    assert draft.normal_control.succeeded is True
    assert draft.observations["template_identity_verified"].value is True
    assert draft.observations["selected_template_approved"].value is expected
    assert draft.observations["input_projection_exact_match"].value is True
    assert draft.observations["template_consumer_verified"].value is False
    assert draft.observations["supply_scope_complete"].value is False
    assert judge(draft, values).verdicts == {"security": Verdict.UNKNOWN, "functional": Verdict.UNKNOWN,
                                            "performance": Verdict.UNKNOWN}
    assert not any(challenge.attempted for challenge in draft.challenges)
    assert [request["id"] for request in world.calls] == ["supply-tokenize", "supply-inference"]
    assert all(request["body"].get("max_tokens", 1) == 1 for request in world.calls)
    assert summary(draft)["requests_sent"] == 2


@pytest.mark.parametrize("change", [{"missing_consumed_ids": True}, {"different_consumed_ids": True},
    {"bad_count": True}, {"bool_count": True}, {"wrong_id": True}, {"truncated": True},
    {"wrong_model": True}, {"invalid_capture_bytes": True}, {"raises": ["supply-inference"]}])
def test_missing_or_count_only_unrelated_projection_is_not_consumption(tmp_path, change):
    draft, _ = run(tmp_path, SupplyWorld(**change))
    assert draft.observations["input_projection_exact_match"].value is False
    assert draft.observations["template_consumer_verified"].value is False
    assert draft.normal_control.succeeded is False
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("change", [{"readonly": False}, {"wrong_owner": True}, {"file_sha": "c" * 64},
    {"empty_identity": True}, {"invalid_started_at": True}, {"wrong_name": True},
    {"not_running": True}, {"local_id_as_manifest": True}])
def test_unverified_file_or_owned_instance_stops_before_http(tmp_path, change):
    draft, world = run(tmp_path, SupplyWorld(**change))
    assert world.calls == []
    assert draft.observations["template_identity_verified"].value is False
    assert draft.observations["selected_template_approved"].collected is False
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_restart_after_projection_does_not_bind_approval_to_another_instance(tmp_path):
    draft, world = run(tmp_path, SupplyWorld(restart=True), values={"allowed_template_sha256": []})
    assert len(world.calls) == 2
    assert draft.observations["template_identity_verified"].value is False
    assert draft.observations["input_projection_exact_match"].value is False
    assert draft.observations["selected_template_approved"].value is None
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_readonly_mount_does_not_make_changing_host_bytes_a_consumption_proof(tmp_path):
    draft, _ = run(tmp_path, SupplyWorld(file_changes=True))
    assert draft.observations["template_identity_verified"].value is False
    assert draft.observations["input_projection_exact_match"].value is False
    assert draft.observations["selected_template_approved"].collected is False


def test_later_http_failure_keeps_observed_policy_mismatch_without_inventing_fail(tmp_path):
    draft, _ = run(tmp_path, SupplyWorld(raises=["supply-inference"]), values={"allowed_template_sha256": []})
    assert draft.observations["template_identity_verified"].value is True
    assert draft.observations["selected_template_approved"].collected is True
    assert draft.observations["selected_template_approved"].value is False
    assert draft.observations["template_consumer_verified"].value is False
    assert judge(draft, {"allowed_template_sha256": []}).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("mutate", [
    lambda data: data["comparison_config"]["launch"].update(model="other-model"),
    lambda data: data.update(observed_version="unrelated-version"),
    lambda data: data["engine"].update(image_digest=IMAGE_ID),
    lambda data: data["comparison_config"]["launch"]["argv"].extend(["--chat-template", "other"]),
    lambda data: data["comparison_config"]["launch"]["argv"].extend(["--served-model-name", "other"]),
])
def test_target_model_image_and_selection_conflicts_do_not_send_requests(tmp_path, mutate):
    draft, world = run(tmp_path, target_change=mutate)
    assert not world.calls and world.inspections == 0
    assert draft.observations["template_identity_verified"].value is False


def test_bare_environment_digest_is_explicitly_unsupported_not_repository_guessed(tmp_path):
    draft, world = run(tmp_path, env_change=lambda values: values["engine"].update(image_digest=IMAGE.split("@")[1]))
    assert world.inspections == 0 and not world.calls
    assert draft.observations["template_identity_verified"].value is False


@pytest.mark.parametrize("values", [{}, {"allowed_template_sha256": None}, {"allowed_template_sha256": SHA},
    {"allowed_template_sha256": [SHA, SHA]}, {"allowed_template_sha256": [True]},
    {"allowed_template_sha256": ["A" * 64]}])
def test_explicit_independent_approval_policy_is_required_before_io(tmp_path, values):
    world = SupplyWorld()
    with pytest.raises(ValueError):
        run(tmp_path, world, values=values)
    assert world.inspections == 0 and not world.calls


@pytest.mark.parametrize("cfg", [{"max_output_tokens": 2}, {"max_total_requests": 4},
    {"max_probe_tokens": True}, {"max_input_bytes": 1}, {"max_duration_seconds": 301},
    {"request_timeout": float("inf")}, {"request_timeout": True}, {"tokenize_endpoint": "/other"}])
def test_unsafe_settings_are_not_silently_rewritten(tmp_path, cfg):
    world = SupplyWorld()
    with pytest.raises(ValueError):
        run(tmp_path, world, cfg=cfg)
    assert not world.calls and world.inspections == 0


@pytest.mark.parametrize("mutate", [
    lambda raw: raw["functional_probes"][0].update(via="gateway"),
    lambda raw: raw["functional_probes"][0]["body"].update(max_tokens=True),
    lambda raw: raw["functional_probes"][0]["body"]["messages"][0].update(content="other fixture"),
    lambda raw: raw["functional_probes"][0]["body"].update(chat_template="arbitrary-template"),
])
def test_nonfixed_or_stateful_fixture_is_not_rewritten_into_supported_request(tmp_path, mutate):
    raw = definition().model_dump()
    mutate(raw)
    world = SupplyWorld()
    with pytest.raises(ValueError):
        run(tmp_path, world, check=CheckDef.model_validate(raw))
    assert not world.calls and world.inspections == 0


def test_token_and_request_budget_stop_dispatch_without_weakening_policy(tmp_path):
    draft, world = run(tmp_path, cfg={"max_total_probe_tokens": 1, "max_total_requests": 1})
    assert len(world.calls) == 1
    assert draft.observations["input_projection_exact_match"].value is False
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert "TEMPLATE_SUPPLY_BUDGET_EXHAUSTED" in summary(draft)["collection_errors"]
    assert summary(draft)["accepted_tokens"] <= 1
    assert summary(draft)["tokenize_response_token_count"] > 1


def test_dispatch_timeout_never_exceeds_remaining_deadline(tmp_path, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(supply, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    world = SupplyWorld()
    probe = world.probe

    def advancing_probe(network, requests, timeout):
        result = probe(network, requests, timeout)
        clock[0] += 2
        return result

    world.probe = advancing_probe
    draft, _ = run(tmp_path, world, cfg={"request_timeout": 1, "max_duration_seconds": 1})
    assert world.timeouts == [1]
    assert len(world.calls) == 1
    assert draft.observations["template_identity_verified"].value is False
    assert draft.observations["input_projection_exact_match"].value is False


def test_raw_response_tokens_inspect_and_transport_secrets_are_not_persisted(tmp_path):
    draft, _ = run(tmp_path, SupplyWorld(raises=["supply-inference"]))
    text = "\n".join(artifact.path.read_text(encoding="utf-8") for artifact in draft.artifacts)
    for forbidden in ("EXAMPLE-private-host-path", "EXAMPLE-hidden-inspect-value",
                      "EXAMPLE-never-persist-transport-secret", "EXAMPLE-private-response-preview",
                      "EXAMPLE-private-response-detail", "EXAMPLE-private-model-content", "prompt_token_ids", '"tokens":'):
        assert forbidden not in text
    assert "independent_worker_template_read" in text


@pytest.mark.parametrize("claim,expected", [("PASS", Verdict.UNKNOWN), ("FAIL", Verdict.FAIL),
                                           ("UNKNOWN", Verdict.UNKNOWN)])
def test_manual_pass_is_unsupported_while_declared_fail_unknown_survive(tmp_path, claim, expected):
    draft, _ = run(tmp_path)
    check = definition()
    raw = as_evidence(draft).to_dict()
    raw["observations"]["declared_verdicts"] = {"collected": True, "value": {
        "security": claim, "functional": "UNKNOWN", "performance": "UNKNOWN"}}
    raw["observations"]["declared_observation_notes"] = {"collected": True, "value": {
        item: "EXAMPLE operator report" for item in check.required_observations_prose}}
    raw["artifacts"] = [{"name": "EXAMPLE-attachment", "path": "EXAMPLE-attachment", "sha256": "e" * 64,
                         "bytes": 1, "collected_at": T0, "collector": "EXAMPLE"}]
    claimed = Evidence.model_validate(raw).seal()
    out = DeclaredJudge().judge(check, claimed, {}, VALUES)
    assert out.verdicts["security"] == expected
    if claim == "PASS":
        assert any("AUTOMATION_MANUAL_POLICY_UNRESOLVED" in reason for reason in out.reasons)


def test_saved_observation_rejudging_does_not_mutate_evidence_or_promote_to_pass(tmp_path):
    draft, _ = run(tmp_path, values={"allowed_template_sha256": []})
    evd, check = as_evidence(draft), definition()
    before = evd.to_dict()
    original = RuleTableJudge().judge(check, evd, check.judge.params, {"allowed_template_sha256": []})
    revised = RuleTableJudge().judge(check, evd, check.judge.params, VALUES)
    assert original.verdicts == revised.verdicts == {axis: Verdict.UNKNOWN for axis in ("security", "functional", "performance")}
    assert evd.to_dict() == before


def test_static_executor_version_and_no_dynamic_fixture_preview(tmp_path):
    _, _ = run(tmp_path)
    path = tmp_path / "target.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["lab"] = False
    path.write_text(json.dumps(data), encoding="utf-8")
    cfg = {"adapter": {"name": "vllm"}, "executors": {"template_validation": "real"},
           "template_validation": {"target": str(path)}}
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})
    assert TemplateValidationExecutor.version == "0.4.0"
    assert TemplateValidationExecutor(cfg["template_validation"]).version == "0.4.0"
    assert preview_inputs(definition(), env, VALUES, cfg) is None


def test_original_checklist_challenges_are_preserved_and_never_claimed_complete():
    original = yaml.safe_load((ROOT / "packs/PACK-BASELINE/checks/SUP-03.yaml").read_text(encoding="utf-8"))
    assert definition().challenge_steps == original["challenge_steps"]
    assert definition().judge.params["pass_when_all"] == []
    assert definition().judge.params["fail_when_any"] == []
    assert definition().policy_params[0].key == "allowed_template_sha256"
    assert len(definition().policy_params) == 1


def test_product_pipeline_and_portable_saved_pack_preserve_observation_only_contract(tmp_path, monkeypatch):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.run import RunConfig, run_plan
    from guard.core.select import select
    from guard.executors import registry
    from guard.models import ExecStatus, load_pack, new_id
    from guard.store import Store
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack
    from tests.builders import PACK_DIR, policy

    # 실제 생성된 팩을 읽는다. 테스트에서 CheckDef나 판정 룰을 대체하지 않는다.
    pack = load_pack(PACK_DIR)
    check = pack.checks["SUP-03"]
    assert check.executor == "template_validation" and check.collection_profile == supply.PROFILE
    assert check.judge.params["pass_when_all"] == check.judge.params["fail_when_any"] == []
    world = SupplyWorld()
    data = target(world.context)
    data["comparison_config"]["launch"]["model"] = "fixture-model"
    source = tmp_path / "target.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS, "template_validation:supply-sim", lambda raw:
                        TemplateValidationExecutor({"target": str(source), "request_timeout": 1},
                                                   docker=world, container_probe=world.probe, environ={}))
    store = Store(tmp_path / "store")
    assert put_pack(store, PACK_DIR) == pack.ref()
    env = environment(id=new_id("ENV"), engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE},
                      model={"id": "fixture-model"})
    pol = policy(pack.ref(), id=new_id("POL"), scope={"include": ["SUP-03"], "exclude": []},
                 values={"SUP-03": {"allowed_template_sha256": []}})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={"template_validation": "supply-sim"}, adapter="vllm"))
    (result,) = judge_plan(store, pack, plan, as_of=T0)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.execution.executor.name == "template_validation" and evd.execution.executor.version == "0.4.0"
    assert evd.data_origin.value == "EXAMPLE" and result.data_origin.value == "EXAMPLE"
    assert evd.observations["input_projection_exact_match"].value is True
    assert evd.observations["selected_template_approved"].value is False
    assert evd.observations["template_consumer_verified"].value is False
    assert evd.normal_control.succeeded is True
    assert evd.challenges and not any(item.attempted for item in evd.challenges)
    assert result.verdicts.security == result.verdicts.functional == Verdict.UNKNOWN
    assert len(world.calls) == 2
    rpt = build_report(store, pack, plan, as_of=T0)
    store.put(rpt)
    row = next(item for item in rpt.by_check if item.check_id == "SUP-03")
    assert rpt.official is False and rpt.data_origin.value == "EXAMPLE"
    assert row.selected is True and row.evidence_source == "automated"
    assert row.single.result == result.ref()
    assert row.single.security == row.single.functional == Verdict.UNKNOWN
    original = evd.to_dict()
    report_original = rpt.to_dict()
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert restored.verify() == []
    saved_pack = get_pack(restored, pack.ref())
    assert saved_pack.checks["SUP-03"].model_dump() == check.model_dump()
    saved_plan = restored.resolve(plan.ref())
    (replayed,) = judge_plan(restored, saved_pack, saved_plan, as_of=T0)
    assert replayed.ref() == result.ref()
    assert replayed.verdicts.security == replayed.verdicts.functional == Verdict.UNKNOWN
    assert restored.resolve(evd.ref()).to_dict() == original
    assert store.resolve(evd.ref()).to_dict() == original
    assert restored.resolve(rpt.ref()).to_dict() == report_original
