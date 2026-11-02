"""REQ-07 fixed-template contracts; synthetic transports are EXAMPLE evidence."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.executors.template_validation import TemplateValidationExecutor, SAFE_OVERRIDE
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from tests.builders import environment, evidence


ROOT = Path(__file__).resolve().parents[1]
SHA = "e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e"
IMAGE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
TEMPLATE_PATH = "/guard-assets/chat/guard-text-chat-v1.jinja"
VALUES = {"allowed_template_sha256": [SHA], "max_rendered_input_tokens": 31,
          "request_template_override_allowed": False}


def check():
    raw = yaml.safe_load((ROOT / "reference/pack-seeds/PACK-BASELINE-31/checks/REQ-07.yaml").read_text(encoding="utf-8"))
    raw.update(yaml.safe_load((ROOT / "tools/overrides/REQ-07.yaml").read_text(encoding="utf-8")))
    return CheckDef.model_validate(raw)


def settings():
    return {"max_output_tokens": 1, "max_total_requests": 48, "max_probe_tokens": 256,
            "max_total_probe_tokens": 4096, "max_input_bytes": 256,
            "max_duration_seconds": 30, "request_timeout": 1.0}


def target(context=32):
    argv = ["/models", "--chat-template", TEMPLATE_PATH]
    return {"lab": True, "run_id": "EXAMPLE-run", "observed_version": "0.31.0",
            "engine": {"trusted_url": "http://engine:8000", "container": "EXAMPLE-engine", "image_digest": IMAGE},
            "origins": {"trusted": {"network": "EXAMPLE-trusted"}},
            "comparison_config": {"launch": {"argv": argv, "max_model_len": context,
                "chat_template": {"profile": "guard-text-chat-v1", "version": "1", "sha256": SHA,
                    "container_path": TEMPLATE_PATH, "content_format": "string", "request_overrides": False}}}}


class World:
    def __init__(self, context=32, **changes):
        self.context = context
        self.changes = changes
        self.calls = []
        self.timeouts = []
        self.inspections = 0

    def inspect_json(self, container, *, timeout):
        assert container == "EXAMPLE-engine" and 0 < timeout <= 5
        self.inspections += 1
        result = {"Id": "EXAMPLE-id", "State": {"Running": True, "StartedAt": "EXAMPLE-time"},
                  "Mounts": [{"Type": "bind", "RW": False, "Destination": TEMPLATE_PATH,
                              "Source": "EXAMPLE-private-host-path"}],
                  "Config": {"Labels": {"guard.run": "EXAMPLE-run"}, "Image": IMAGE,
                             "Cmd": target(self.context)["comparison_config"]["launch"]["argv"],
                             "Env": ["API_KEY=EXAMPLE-hidden-inspect-value"]}}
        if self.changes.get("readonly") is False:
            result["Mounts"][0]["RW"] = True
        if self.changes.get("wrong_owner"):
            result["Config"]["Labels"]["guard.run"] = "someone-else"
        if self.changes.get("restart") and self.inspections > 1:
            result["State"]["StartedAt"] = "changed-time"
        return result

    def exec(self, container, argv, *, timeout):
        assert container == "EXAMPLE-engine" and argv[0] == "python3" and argv[-1] == TEMPLATE_PATH
        sha = self.changes.get("file_sha", SHA)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"sha256": sha, "bytes": 100}))

    @staticmethod
    def tokens(body):
        if "prompt" in body:
            return [100 + index for index, _ in enumerate(body["prompt"].split())]
        if "chat_template" in body or "chat_template" in body.get("chat_template_kwargs", {}):
            return [777, 888]
        words = body["messages"][0]["content"].split()
        return [9] * 10 + [100 + index for index, _ in enumerate(words)]

    @staticmethod
    def error(message, param=None):
        return {"error": {"type": "BadRequestError", "code": 400, "param": param, "message": message}}

    def probe(self, network, requests, timeout):
        assert network == "EXAMPLE-trusted" and len(requests) == 1 and 0 < timeout <= self.changes.get("timeout_limit", 1)
        self.timeouts.append(timeout)
        request = deepcopy(requests[0])
        self.calls.append(request)
        rid, body = request["id"], request["body"]
        if rid in self.changes.get("raises", []):
            raise RuntimeError("API_KEY=EXAMPLE-never-persist-transport-secret")
        tokens = self.tokens(body)
        override = "chat_template" in body or "chat_template" in body.get("chat_template_kwargs", {})
        status = 200
        if request["url"].endswith("/tokenize"):
            value = {"count": len(tokens), "tokens": tokens, "max_model_len": self.context}
            if self.changes.get("bad_count"):
                value["count"] += 1
        else:
            if override and not self.changes.get("allow_override") and not self.changes.get("ignore_override"):
                status = 400
                value = self.error("Chat template is passed with request, but --trust-request-chat-template is not set. "
                                   "Refused request with untrusted chat template.")
            elif len(tokens) > self.context - body["max_tokens"]:
                status = 400
                captured = self.context - body["max_tokens"] + 1
                value = self.error(f"This model's maximum context length is {self.context} tokens. However, you requested "
                    f"{body['max_tokens']} output tokens and your prompt contains at least {captured} input tokens, "
                    f"for a total of at least {captured + body['max_tokens']} tokens. Please reduce the length of the "
                    "input prompt or the number of requested output tokens."
                    f" (parameter=input_tokens, value={captured})", "input_tokens")
            else:
                if override and self.changes.get("ignore_override"):
                    clean = {key: value for key, value in body.items() if key not in {"chat_template", "chat_template_kwargs"}}
                    tokens = self.tokens(clean)
                value = {"model": "fixture-model", "choices": [{"message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "length", "token_ids": [5]}], "prompt_token_ids": tokens,
                    "usage": {"prompt_tokens": len(tokens), "completion_tokens": 1, "total_tokens": len(tokens) + 1}}
                if self.changes.get("missing_consumed_ids"):
                    value.pop("prompt_token_ids")
                if self.changes.get("different_consumed_ids"):
                    value["prompt_token_ids"] = [token + 1 for token in tokens]
            if rid == "overbound" and self.changes.get("unrelated_400"):
                status, value = 400, self.error("wrong model", "model")
            if rid == "after" and self.changes.get("after_error"):
                status, value = 500, self.error("engine exception")
        raw = json.dumps(value)
        return [{"id": "unrelated-id" if self.changes.get("wrong_id") else rid,
                 "outcome": status, "body": raw, "body_bytes": 1_000_000 if self.changes.get("truncated") else len(raw)}]


def run(tmp_path, world=None, values=None, cfg=None, definition=None, target_change=None, env_version="0.31.0"):
    world = world or World()
    data = target(world.context)
    if target_change:
        target_change(data)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    env = environment(engine={"name": "vllm", "version": env_version, "image_digest": IMAGE}, model={"id": "fixture-model"})
    executor = TemplateValidationExecutor({**settings(), **(cfg or {}), "target": str(path)},
                                          docker=world, container_probe=world.probe, environ={})
    draft = executor.run(definition or check(), env, deepcopy(VALUES if values is None else values),
                         VllmAdapter(), tmp_path / "work")
    return draft, world


def judge(draft, values=None):
    definition = check()
    evd = evidence(check_id="REQ-07", normal=draft.normal_control.model_dump(),
                   observations={key: value.model_dump() for key, value in draft.observations.items()})
    return RuleTableJudge().judge(definition, evd, definition.judge.params, VALUES if values is None else values)


def summary(draft):
    return json.loads(draft.artifacts[0].path.read_text(encoding="utf-8").splitlines()[-1])


def test_exact_render_consumer_and_source_specific_rejection_support_limited_pass(tmp_path):
    draft, world = run(tmp_path)
    assert draft.data_origin == "EXAMPLE" and draft.normal_control.succeeded is True
    assert all(step.attempted for step in draft.challenges)
    assert judge(draft).verdicts == {"security": Verdict.PASS, "functional": Verdict.PASS, "performance": Verdict.UNKNOWN}
    assert draft.observations["rendered_input_token_counts"].value["boundary"] == 31
    assert draft.observations["rendered_input_token_counts"].value["overbound"] == 32
    assert draft.observations["rendered_input_token_counts"].value["overbound-raw"] <= 31
    assert summary(draft)["enforcement_proof"]["phase"] == "pre_generation"
    assert len(world.calls) <= settings()["max_total_requests"]


def test_consumed_render_over_policy_is_fail_and_later_collection_failure_does_not_erase_it(tmp_path):
    draft, _ = run(tmp_path, World(context=64, after_error=True))
    assert judge(draft).verdicts["security"] == Verdict.FAIL
    assert judge(draft).verdicts["functional"] == Verdict.UNKNOWN
    assert any("above the policy" in message for message in draft.observations["security_violations"].value)


def test_observed_unapproved_template_is_independent_fail(tmp_path):
    values = {**VALUES, "allowed_template_sha256": ["c" * 64]}
    draft, _ = run(tmp_path, values=values)
    assert judge(draft, values).verdicts["security"] == Verdict.FAIL


@pytest.mark.parametrize("change", [{"missing_consumed_ids": True}, {"different_consumed_ids": True},
                                    {"bad_count": True}, {"wrong_id": True}, {"truncated": True},
                                    {"unrelated_400": True}, {"after_error": True}])
def test_counts_status_and_unrelated_errors_do_not_establish_template_security(tmp_path, change):
    draft, _ = run(tmp_path, World(**change))
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("change", [{"readonly": False}, {"wrong_owner": True}, {"file_sha": "c" * 64}])
def test_unverified_or_writable_template_is_not_rendered_and_never_passes(tmp_path, change):
    draft, world = run(tmp_path, World(**change))
    assert world.calls == []
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert not any(step.attempted for step in draft.challenges)


def test_template_or_engine_restart_after_trial_withholds_pass(tmp_path):
    draft, _ = run(tmp_path, World(restart=True))
    assert draft.observations["template_identity_verified"].value is False
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_actual_override_consumption_is_fail_but_ignored_override_requires_exact_ids(tmp_path):
    first = tmp_path / "applied"; first.mkdir()
    applied, _ = run(first, World(allow_override=True))
    assert judge(applied).verdicts["security"] == Verdict.FAIL
    second = tmp_path / "ignored"; second.mkdir()
    ignored, _ = run(second, World(ignore_override=True))
    assert judge(ignored).verdicts["security"] == Verdict.PASS
    assert all(proof["consumed_server_render"] for proof in summary(ignored)["override_proofs"])


def test_allowing_overrides_is_a_valid_policy_but_unsupported_profile_scope(tmp_path):
    values = {**VALUES, "request_template_override_allowed": True}
    draft, _ = run(tmp_path, values=values)
    assert judge(draft, values).verdicts["security"] == Verdict.UNKNOWN
    assert "outside this restricted profile" in " ".join(summary(draft)["gaps"])


def test_large_enterprise_limit_is_not_reduced_to_fixture_budget(tmp_path):
    values = {**VALUES, "max_rendered_input_tokens": 100000}
    draft, _ = run(tmp_path, values=values)
    assert judge(draft, values).verdicts["security"] == Verdict.UNKNOWN
    assert draft.observations["enforcement_observed"].value is False


@pytest.mark.parametrize("key,value", [("max_output_tokens", 33), ("max_output_tokens", True),
    ("max_total_requests", 65), ("max_probe_tokens", 4097), ("max_total_probe_tokens", 65537),
    ("max_input_bytes", 4097), ("max_duration_seconds", 301), ("request_timeout", 0),
    ("request_timeout", 121), ("request_timeout", float("nan")), ("request_timeout", float("inf")),
    ("request_timeout", float("-inf")), ("request_timeout", True), ("request_timeout", False),
    ("request_timeout", -1), ("request_timeout", "120"),
    ("endpoint", "/v1/completions"), ("tokenize_endpoint", "/wrong")])
def test_unsafe_or_unimplemented_execution_settings_stop_before_io(tmp_path, key, value):
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, cfg={key: value})
    assert not world.calls and world.inspections == 0


def test_small_request_budget_is_exhaustion_unknown_and_never_overrun(tmp_path):
    draft, world = run(tmp_path, cfg={"max_total_requests": 3})
    assert len(world.calls) == 3
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_explicit_longer_cpu_first_generation_timeout_is_supported_without_retries(tmp_path):
    draft, world = run(tmp_path, World(timeout_limit=120),
                       cfg={"request_timeout": 120.0, "max_duration_seconds": 300})
    assert judge(draft).verdicts["security"] == Verdict.PASS
    assert world.timeouts and max(world.timeouts) == 120.0
    assert all(0 < timeout <= 120.0 for timeout in world.timeouts)
    assert sum(request["id"] == "before" for request in world.calls) == 1
    assert settings()["max_output_tokens"] == 1
    assert summary(draft)["fixture"]["settings"]["max_duration_seconds"] == 300


def test_long_request_timeout_is_clamped_to_remaining_dispatch_deadline(tmp_path, monkeypatch):
    from guard.executors import template_validation
    clock = [100.0]
    monkeypatch.setattr(template_validation, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    world = World(timeout_limit=120)
    probe = world.probe

    def advancing_probe(network, requests, timeout):
        result = probe(network, requests, timeout)
        clock[0] += 1.0
        return result

    world.probe = advancing_probe
    draft, _ = run(tmp_path, world, cfg={"request_timeout": 120.0, "max_duration_seconds": 5})
    assert world.timeouts == [5.0, 4.0, 3.0, 2.0, 1.0]
    assert len(world.calls) == 5
    assert summary(draft)["elapsed_seconds"] == 5.0
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_private_inspect_and_raw_transport_errors_never_persist(tmp_path):
    draft, _ = run(tmp_path, World(raises=["after"]))
    text = "\n".join(artifact.path.read_text(encoding="utf-8") for artifact in draft.artifacts)
    assert "EXAMPLE-private-host-path" not in text
    assert "EXAMPLE-hidden-inspect-value" not in text
    assert "EXAMPLE-never-persist-transport-secret" not in text


def test_probe_body_is_only_bounded_text_chat_and_two_fixed_literal_overrides(tmp_path):
    _, world = run(tmp_path)
    for request in world.calls:
        assert request["method"] == "POST" and request["url"].split(":8000")[-1] in {"/tokenize", "/v1/chat/completions"}
        body = request["body"]
        if "max_tokens" in body:
            assert body["max_tokens"] <= 32
        if "messages" in body:
            assert len(body["messages"]) == 1
            assert len(body["messages"][0]["content"].encode()) <= settings()["max_input_bytes"]
        if "chat_template" in body:
            assert body["chat_template"] == SAFE_OVERRIDE
        if "chat_template_kwargs" in body:
            assert body["chat_template_kwargs"] == {"chat_template": SAFE_OVERRIDE}


def test_source_specific_rejection_requires_pinned_observed_version(tmp_path):
    draft, _ = run(tmp_path, env_version="different")
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert draft.observations["enforcement_observed"].value is False


def test_numeric_consumption_and_measured_test_boundary_constrain_saved_evidence_evaluation(tmp_path):
    draft, _ = run(tmp_path)
    original = draft.model_dump(mode="json")
    assert draft.observations["max_consumed_prompt_tokens"].value == 31
    assert draft.observations["tested_rendered_input_limit"].value == 31
    assert judge(draft, {**VALUES, "max_rendered_input_tokens": 1}).verdicts["security"] == Verdict.FAIL
    assert judge(draft, {**VALUES, "max_rendered_input_tokens": 63}).verdicts["security"] == Verdict.UNKNOWN
    assert draft.model_dump(mode="json") == original


@pytest.mark.parametrize("name", ["max_consumed_prompt_tokens", "tested_rendered_input_limit"])
def test_missing_numeric_scope_cannot_be_replaced_by_boolean_claims(tmp_path, name):
    draft, _ = run(tmp_path)
    draft.observations[name].collected = False
    draft.observations[name].value = None
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_untested_boundary_is_not_a_measured_copy_of_policy(tmp_path):
    draft, _ = run(tmp_path, cfg={"max_total_requests": 3})
    assert draft.observations["tested_rendered_input_limit"].collected is False
    assert draft.observations["tested_rendered_input_limit"].value is None


def test_observed_token_budget_stops_further_dispatch_after_bounded_capture(tmp_path):
    draft, world = run(tmp_path, cfg={"max_total_probe_tokens": 1})
    assert len(world.calls) == 1
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert "observed token count exceeds collection budget" in " ".join(summary(draft)["gaps"])


@pytest.mark.parametrize("change", [{"via": "gateway"}, {"path": "/v1/completions"}, {"method": "GET"},
    {"body": {"model": "{model}", "messages": [{"role": "system", "content": "fixture"}], "max_tokens": 1}},
    {"body": {"model": "{model}", "messages": [None], "max_tokens": 1}}])
def test_unsupported_functional_scope_is_not_silently_rewritten(tmp_path, change):
    raw = check().model_dump()
    raw["functional_probes"][0].update(change)
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, definition=CheckDef.model_validate(raw))
    assert not world.calls and world.inspections == 0
