"""Bounded REQ-10 API observations; these are synthetic contracts, not engine measurements."""
from copy import deepcopy
import json
from pathlib import Path
import threading

import pytest
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.executors.sampling_validation import SamplingValidationExecutor
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from tests.builders import environment, evidence


ROOT = Path(__file__).resolve().parents[1]
VALUES = {"allowed_sampling_options": ["temperature", "top_p", "min_tokens"], "rejection_codes": ["400", "422"]}


def check():
    return CheckDef.model_validate(yaml.safe_load((ROOT / "packs/PACK-BASELINE/checks/REQ-10.yaml").read_text(encoding="utf-8")))


def cfg():
    return {"constraint_source": "EXAMPLE pinned implementation fixture", "constraint_engine_version": "0.31.0",
            "max_output_tokens": 2, "max_total_requests": 32, "max_probe_tokens": 64,
            "max_duration_seconds": 30, "request_timeout": 1.0}


class World:
    def __init__(self, overrides=None, malformed=(), parallel=False):
        self.overrides = overrides or {}
        self.malformed = set(malformed)
        self.calls = []
        self.lock = threading.Lock()
        self.barriers = {o: threading.Barrier(2) for o in VALUES["allowed_sampling_options"]} if parallel else {}

    def probe(self, network, requests, timeout):
        assert network == "trusted-net"
        assert 0 < timeout <= 1.0
        assert len(requests) == 1
        request = requests[0]
        with self.lock:
            self.calls.append(deepcopy(request))
        rid = request["id"]
        if rid.endswith(("-challenge", "-concurrent")) and self.barriers:
            self.barriers[rid.split("-")[1]].wait(timeout=5)
        outcome = self.overrides.get(rid, 400 if rid.endswith("-challenge") else 200)
        if outcome == "raise":
            raise RuntimeError("private upstream exception must never be persisted")
        body = {"model": "fixture-model", "choices": [{"text": "ok", "finish_reason": "length"}],
                "usage": {"completion_tokens": 1}}
        return [{"id": rid, "outcome": outcome,
                 "body": "malformed" if rid in self.malformed else json.dumps(body), "method": request["method"]}]


def run(tmp_path, world=None, values=None, settings=None, env_version="0.31.0", definition=None):
    world = world or World()
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "origins": {"trusted": {"network": "trusted-net"}},
                                  "engine": {"trusted_url": "http://engine:8000"}}), encoding="utf-8")
    settings = {**cfg(), **(settings or {}), "target": str(target)}
    env = environment(engine={"name": "vllm", "version": env_version}, model={"id": "fixture-model"})
    draft = SamplingValidationExecutor(settings, container_probe=world.probe).run(
        definition or check(), env, deepcopy(values or VALUES), VllmAdapter(), tmp_path / "work")
    return draft, world


def judge(draft, values=None):
    definition = check()
    evd = evidence(check_id="REQ-10", normal=draft.normal_control.model_dump(),
                   observations={k: v.model_dump() for k, v in draft.observations.items()})
    return RuleTableJudge().judge(definition, evd, definition.judge.params, values or VALUES)


def summary(draft):
    return json.loads(draft.artifacts[0].path.read_text(encoding="utf-8").splitlines()[-1])


def test_valid_api_rejection_keeps_full_security_unknown_and_limited_functional_pass(tmp_path):
    draft, world = run(tmp_path)
    result = judge(draft)
    assert result.verdicts == {"security": Verdict.UNKNOWN, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert draft.normal_control.succeeded is True
    assert draft.data_origin == "EXAMPLE"
    assert len(world.calls) == 16
    assert summary(draft)["requests_sent"] == 16
    assert draft.observations["runner_consumption_verified"].value is False
    assert summary(draft)["fixture"]["constraint_origin"] == "DECLARED"


def test_invalid_and_normal_requests_are_actually_dispatched_concurrently(tmp_path):
    draft, _ = run(tmp_path, World(parallel=True))
    assert draft.normal_control.succeeded is True
    assert all(p["dispatch"] == "two_threads" and p["engine_execution_overlap_verified"] is False
               for p in summary(draft)["concurrent_pairs"].values())


def test_each_boundary_changes_one_option_and_no_gpu_sized_arrays_are_sent(tmp_path):
    _, world = run(tmp_path)
    by_id = {r["id"]: r for r in world.calls}
    for name, valid, invalid in (("temperature", 0.0, -1.0), ("top_p", 1.0, 0.0), ("min_tokens", 0, 3)):
        before = by_id[f"sampling-{name}-boundary"]["body"]
        after = by_id[f"sampling-{name}-challenge"]["body"]
        assert {key for key in set(before) | set(after) if before.get(key) != after.get(key)} == {name}
        assert before[name] == valid and after[name] == invalid
        assert set(after) == {"model", "prompt", "max_tokens", name}
    assert not any("allowed_token_ids" in r.get("body", {}) or "logit_bias" in r.get("body", {}) for r in world.calls)


@pytest.mark.parametrize("outcome", [200, 201, 500, 503, "timeout", "refused", "error", True])
def test_challenge_acceptance_or_transport_error_does_not_claim_memory_corruption(tmp_path, outcome):
    draft, _ = run(tmp_path, World({"sampling-temperature-challenge": outcome}))
    result = judge(draft)
    assert result.verdicts["security"] == Verdict.UNKNOWN
    assert result.verdicts["functional"] == Verdict.PASS
    assert "memory" not in " ".join(result.reasons).lower()


@pytest.mark.parametrize("phase", ["before", "temperature-boundary", "temperature-concurrent", "temperature-after"])
@pytest.mark.parametrize("outcome", [400, 500, "timeout", "raise"])
def test_inference_failure_is_unknown_not_shared_engine_damage(tmp_path, phase, outcome):
    draft, _ = run(tmp_path, World({f"sampling-{phase}": outcome}))
    assert draft.normal_control.succeeded is False
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    assert "private upstream exception" not in draft.artifacts[0].path.read_text(encoding="utf-8")


def test_health_only_and_malformed_200_cannot_establish_inference_recovery(tmp_path):
    draft, world = run(tmp_path, World(malformed={"sampling-before"}))
    assert len(world.calls) == 1
    assert draft.normal_control.succeeded is False
    assert all(not c.attempted for c in draft.challenges)
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN


def test_health_failure_is_not_hidden_by_successful_inference(tmp_path):
    draft, _ = run(tmp_path, World({"sampling-temperature-health": "timeout"}))
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN


def test_missing_or_wrong_probe_id_is_unknown(tmp_path):
    world = World()
    original = world.probe

    def probe(network, requests, timeout):
        result = original(network, requests, timeout)
        result[0]["id"] = "unrelated"
        return result

    world.probe = probe
    draft, _ = run(tmp_path, world)
    assert draft.normal_control.succeeded is False
    assert len(world.calls) == 1


@pytest.mark.parametrize("key,value", [
    ("constraint_source", "unknown"), ("constraint_engine_version", ""),
    ("max_output_tokens", True), ("max_output_tokens", 33),
    ("max_total_requests", 15), ("max_total_requests", 65),
    ("max_probe_tokens", 0), ("max_probe_tokens", 4097),
    ("max_duration_seconds", 0), ("max_duration_seconds", 301),
    ("request_timeout", True), ("request_timeout", 31), ("request_timeout", float("nan")),
])
def test_bad_execution_settings_stop_before_any_request(tmp_path, key, value):
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, settings={key: value})
    assert world.calls == []


@pytest.mark.parametrize("values", [
    {"allowed_sampling_options": [], "rejection_codes": ["400"]},
    {"allowed_sampling_options": ["temperature", "temperature"], "rejection_codes": ["400"]},
    {"allowed_sampling_options": ["allowed_token_ids"], "rejection_codes": ["400"]},
    {"allowed_sampling_options": ["temperature"], "rejection_codes": ["500"]},
    {"allowed_sampling_options": ["temperature"], "rejection_codes": [400]},
    {"allowed_sampling_options": ["temperature"]},
])
def test_policy_is_not_coerced_reduced_or_completed(tmp_path, values):
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, values=values)
    assert world.calls == []


def test_source_version_mismatch_stops_before_request(tmp_path):
    world = World()
    with pytest.raises(ValueError, match="constraint_engine_version"):
        run(tmp_path, world, env_version="different")
    assert world.calls == []


@pytest.mark.parametrize("change", [{"path": "/v1/chat/completions"}, {"via": "gateway"}, {"method": "GET"},
                                     {"body": {"model": "{model}", "prompt": [0], "max_tokens": 1}}])
def test_unsupported_endpoint_or_format_is_not_silently_replaced(tmp_path, change):
    definition = check()
    raw = definition.model_dump()
    raw["functional_probes"][0].update(change)
    definition = CheckDef.model_validate(raw)
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, definition=definition)
    assert world.calls == []


def test_policy_subset_is_fully_tested_in_declared_order(tmp_path):
    values = {"allowed_sampling_options": ["min_tokens", "temperature"], "rejection_codes": ["400"]}
    draft, world = run(tmp_path, values=values)
    assert draft.observations["sampling_options_tested"].value == values["allowed_sampling_options"]
    assert len(world.calls) == 11
    assert judge(draft, values).verdicts["functional"] == Verdict.PASS


def test_boolean_http_success_and_missing_completion_tokens_are_unknown(tmp_path):
    world = World({"sampling-before": True})
    draft, _ = run(tmp_path, world)
    assert draft.normal_control.succeeded is False


def test_measured_flag_does_not_authenticate_constraint_source(tmp_path):
    draft, _ = run(tmp_path)
    assert summary(draft)["fixture"]["constraint_origin"] == "DECLARED"
    assert any("not independently authenticated" in line for line in summary(draft)["limitations"])


def test_duration_budget_stops_without_claiming_unattempted_challenges(tmp_path, monkeypatch):
    from guard.executors import sampling_validation

    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(sampling_validation.time, "monotonic", lambda: next(ticks, 2.0))
    draft, world = run(tmp_path, settings={"max_duration_seconds": 1})
    assert len(world.calls) == 1
    assert summary(draft)["requests_sent"] == 1
    assert draft.observations["sampling_options_tested"].value == []
    assert all(not c.attempted for c in draft.challenges)
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN


def test_text_fixture_budget_is_enforced_before_http(tmp_path):
    world = World()
    with pytest.raises(ValueError, match="input budget"):
        run(tmp_path, world, settings={"max_probe_tokens": 1})
    assert world.calls == []
