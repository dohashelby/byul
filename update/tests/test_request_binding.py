"""REQ-11 connection/consumer contracts using explicitly EXAMPLE transports."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from guard.adapters.vllm import VllmAdapter
from guard.executors.request_binding import CASES, RequestBindingExecutor, rendered_text
from guard.judges.rule_table import RuleTableJudge
from guard.models import CheckDef, Verdict
from tests.builders import environment, evidence


ROOT = Path(__file__).resolve().parents[1]
SHA = "e5b96cc7595d53305284a40f579747da846f01b566ad571ad2cb67526bdadc2e"
IMAGE = "vllm/vllm-openai-cpu@sha256:8024248339dc6878daa5349344ed29d49c8a6732f6bdf7400fda32ce33e4b30b"
TEMPLATE_PATH = "/guard-assets/chat/guard-text-chat-v1.jinja"
VALUES = {"result_binding": "originating_connection", "client_request_id_trust": "untrusted_correlation"}


def check():
    raw = yaml.safe_load((ROOT / "reference/pack-seeds/PACK-BASELINE-31/checks/REQ-11.yaml").read_text(encoding="utf-8"))
    raw.update(yaml.safe_load((ROOT / "tools/overrides/REQ-11.yaml").read_text(encoding="utf-8")))
    return CheckDef.model_validate(raw)


def target():
    return {"lab": True, "run_id": "EXAMPLE-run", "observed_version": "0.31.0",
        "engine": {"trusted_url": "http://engine:8000", "container": "EXAMPLE-engine", "image_digest": IMAGE},
        "origins": {"trusted": {"network": "EXAMPLE-trusted"}},
        "comparison_config": {"launch": {"profile": "cpu-bounded-v1",
            "argv": ["/models", "--chat-template", TEMPLATE_PATH], "max_model_len": 128,
            "chat_template": {"profile": "guard-text-chat-v1", "version": "1", "sha256": SHA,
                "container_path": TEMPLATE_PATH, "content_format": "string", "request_overrides": False}}}}


class World:
    def __init__(self, **changes):
        self.changes, self.calls, self.timeouts = changes, [], []
        self.inspections = 0

    def inspect_json(self, container, *, timeout):
        assert container == "EXAMPLE-engine" and 0 < timeout <= 5
        self.inspections += 1
        return {"Id": "EXAMPLE-owned-id", "State": {"Running": True,
            "StartedAt": "changed" if self.changes.get("restart") and self.inspections > 1 else "EXAMPLE-start"},
            "Mounts": [{"Type": "bind", "RW": bool(self.changes.get("writable")), "Destination": TEMPLATE_PATH,
                        "Source": "EXAMPLE-private-host-path"}],
            "Config": {"Labels": {"guard.run": "foreign-owner" if self.changes.get("foreign_owner") else "EXAMPLE-run"},
                "Image": IMAGE, "Cmd": target()["comparison_config"]["launch"]["argv"],
                "Env": ["API_KEY=EXAMPLE-hidden-inspect-secret"]}}

    def exec(self, container, argv, *, timeout):
        assert container == "EXAMPLE-engine" and argv[-1] == TEMPLATE_PATH
        return SimpleNamespace(returncode=0, stdout=json.dumps({"sha256": SHA, "bytes": 100}))

    @staticmethod
    def tokens(body):
        return list(rendered_text(body["messages"][0]["content"]).encode())

    def probe(self, network, requests, timeout):
        assert network == "EXAMPLE-trusted" and len(requests) == 1
        self.calls.extend(deepcopy(requests))
        self.timeouts.append(timeout)
        body = requests[0]["body"]
        value = {"tokens": self.tokens(body), "count": len(self.tokens(body)), "max_model_len": 128}
        if self.changes.get("same_fixtures"):
            value["tokens"] = [1]
            value["count"] = 1
        raw = json.dumps(value)
        return [{"id": requests[0]["id"], "outcome": 200, "body": raw, "body_bytes": len(raw)}]

    def stream(self, network, requests, timeout):
        assert network == "EXAMPLE-trusted" and 1 <= len(requests) <= 2 and 0 < timeout <= 120
        self.timeouts.append(timeout)
        self.calls.extend(deepcopy(requests))
        if any(request["id"] == self.changes.get("raises") for request in requests):
            raise RuntimeError("API_KEY=EXAMPLE-do-not-persist-raw-exception")
        results = []
        for index, request in enumerate(requests):
            label, body = request["id"], request["body"]
            rid = "chatcmpl-" + request.get("headers", {}).get("X-Request-Id", body.get("request_id", "EXAMPLE-" + label))
            consumed = requests[1 - index]["body"] if len(requests) == 2 and label == self.changes.get("foreign") else body
            ids, text = self.tokens(consumed), rendered_text(consumed["messages"][0]["content"])
            usage = {"prompt_tokens": len(ids), "completion_tokens": 1, "total_tokens": len(ids) + 1}
            mode = request["mode"]
            common = {"id": rid, "model": "fixture-model",
                      "object": "chat.completion" if mode == "json" else "chat.completion.chunk"}
            if mode == "json":
                value = {**common, "prompt_token_ids": ids, "prompt_text": text, "choices": [{"index": 0,
                    "message": {"role": "assistant", "content": "EXAMPLE-generated-without-input-marker"},
                    "token_ids": [55], "finish_reason": "length"}], "usage": usage}
                if label == self.changes.get("missing_consumer"):
                    value.pop("prompt_token_ids")
                    value.pop("prompt_text")
                if label == self.changes.get("different_consumer"):
                    value["prompt_token_ids"] = [999]
                    value["prompt_text"] = "unrecognized input"
                data, events = json.dumps(value), []
            else:
                first = {**common, "prompt_token_ids": ids, "prompt_text": text,
                         "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""}, "finish_reason": None}]}
                last = {**common, "choices": [{"index": 0, "delta": {"content": "EXAMPLE"},
                            "token_ids": [55], "finish_reason": "length"}]}
                if label == self.changes.get("stream_id_change"):
                    last["id"] = "chatcmpl-EXAMPLE-other-request"
                frames = [json.dumps(first), json.dumps(last), json.dumps({**common, "choices": [], "usage": usage}), "[DONE]"]
                if label == self.changes.get("post_done"):
                    frames.append(json.dumps(last))
                if label == self.changes.get("missing_done"):
                    frames.pop()
                if label == self.changes.get("bad_sse_json"):
                    frames[1] = "{bad-json"
                if label == self.changes.get("duplicate_done"):
                    frames.append("[DONE]")
                events = [{"seq": i, "data": frame, "received_ms": 3 + i} for i, frame in enumerate(frames)]
                data = "".join("data: " + frame + "\n\n" for frame in frames)
            failed = label == self.changes.get("after_error")
            result = {"id": label, "outcome": 500 if failed else 200, "body": data,
                "body_bytes": len(data.encode()), "body_sha256": hashlib.sha256(data.encode()).hexdigest(),
                "body_truncated": label == self.changes.get("truncated"), "utf8_valid": True,
                "capture_complete": label != self.changes.get("incomplete"), "eof": True,
                "content_type": "text/event-stream" if mode == "sse" else "application/json",
                "terminal_seen": mode == "sse" and label != self.changes.get("missing_done"), "events": events,
                "connection_id": "EXAMPLE-connection-" + ("same" if self.changes.get("same_connection") else label),
                "dispatch_ms": 0, "request_sent_ms": 30 if index and self.changes.get("no_overlap") else 1,
                "headers_ms": 2, "first_byte_ms": 3, "end_ms": 20}
            if self.changes.get("wrong_label"):
                result["id"] = "EXAMPLE-unrelated"
            results.append(result)
        return list(reversed(results)) if self.changes.get("reorder_results") else results


def run(tmp_path, world=None, values=None, cfg=None, *, target_change=None, env_version="0.31.0"):
    world = world or World()
    data = target()
    if target_change:
        target_change(data)
    path = tmp_path / "target.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    env = environment(engine={"name": "vllm", "version": env_version, "image_digest": IMAGE}, model={"id": "fixture-model"})
    executor = RequestBindingExecutor({"target": str(path), "request_timeout": 120.0, **(cfg or {})},
        docker=world, container_probe=world.probe, stream_probe=world.stream, environ={})
    return executor.run(check(), env, deepcopy(VALUES if values is None else values), VllmAdapter(), tmp_path / "work"), world


def judge(draft, values=None):
    definition = check()
    evd = evidence(check_id="REQ-11", normal=draft.normal_control.model_dump(),
        observations={key: value.model_dump() for key, value in draft.observations.items()})
    return RuleTableJudge().judge(definition, evd, definition.judge.params, VALUES if values is None else values)


def summary(draft):
    path = next(artifact.path for artifact in draft.artifacts if artifact.name == "request_binding_samples.json")
    return json.loads(path.read_text(encoding="utf-8"))["capture"]["summary"]


def test_two_consumer_fixtures_cover_all_json_sse_id_cases_without_generated_echo(tmp_path):
    draft, world = run(tmp_path)
    result = judge(draft)
    assert draft.data_origin.value == "EXAMPLE"
    assert result.verdicts["security"] == Verdict.UNKNOWN
    assert result.verdicts["functional"] == Verdict.PASS
    assert result.verdicts["performance"] == Verdict.UNKNOWN
    assert draft.observations["cases_verified"].value == list(CASES)
    assert draft.observations["input_projection_verified"].value is True
    assert draft.observations["output_binding_verified"].value is False
    assert "not independently verified" in draft.observations["output_binding_gap"].value
    assert draft.observations["collection_complete"].value is True
    assert summary(draft)["verification_scope"]["output_binding_verified"] is False
    assert len(world.calls) == 20 and all(0 < timeout <= 120 for timeout in world.timeouts)
    assert all(challenge.attempted for challenge in draft.challenges)
    assert "EXAMPLE-private-host-path" not in json.dumps(summary(draft))
    assert "EXAMPLE-hidden-inspect-secret" not in json.dumps(summary(draft))


@pytest.mark.parametrize("label", ["json-distinct_body-A", "json-collision_body-A", "sse-generated-B", "sse-collision_header-A"])
def test_foreign_consumer_input_is_fail_and_later_error_does_not_erase_it(tmp_path, label):
    draft, _ = run(tmp_path, World(foreign=label, raises="after"))
    assert judge(draft).verdicts["security"] == Verdict.FAIL
    assert draft.observations["security_violations"].value
    assert draft.observations["collection_complete"].value is False
    for artifact in draft.artifacts:
        assert "EXAMPLE-do-not-persist-raw-exception" not in artifact.path.read_text(encoding="utf-8")


def test_stream_id_changes_independently_fail(tmp_path):
    draft, _ = run(tmp_path, World(stream_id_change="sse-distinct_body-A"))
    assert judge(draft).verdicts["security"] == Verdict.FAIL


@pytest.mark.parametrize("change,label", [
    ("missing_consumer", "json-generated-A"), ("different_consumer", "json-generated-A"),
    ("truncated", "sse-generated-A"), ("incomplete", "sse-generated-A"),
    ("missing_done", "sse-distinct_body-A"), ("bad_sse_json", "sse-generated-A"),
    ("post_done", "sse-generated-A"), ("duplicate_done", "sse-generated-A"),
    ("raises", "json-collision_body-A"), ("after_error", "after"),
])
def test_inconclusive_capture_transport_or_unknown_consumer_is_unknown(tmp_path, change, label):
    draft, _ = run(tmp_path, World(**{change: label}))
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert draft.observations["collection_complete"].value is False


@pytest.mark.parametrize("change", ["no_overlap", "same_connection", "wrong_label", "same_fixtures"])
def test_missing_overlapping_independent_connections_cannot_pass(tmp_path, change):
    draft, _ = run(tmp_path, World(**{change: True}))
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_collector_output_order_does_not_replace_explicit_request_labels(tmp_path):
    draft, _ = run(tmp_path, World(reorder_results=True))
    assert draft.observations["input_projection_verified"].value is True
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("key,value", [("result_binding", "tenant_principal"),
                                       ("client_request_id_trust", "trusted_authority")])
def test_valid_other_enterprise_policy_is_unsupported_and_sends_nothing(tmp_path, key, value):
    draft, world = run(tmp_path, values={**VALUES, key: value})
    assert world.calls == []
    assert judge(draft, {**VALUES, key: value}).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("change", ["foreign_owner", "writable", "restart"])
def test_owned_readonly_template_identity_required_before_and_after(tmp_path, change):
    draft, world = run(tmp_path, World(**{change: True}))
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    if change != "restart":
        assert world.calls == []


def test_unsupported_consumer_source_version_sends_no_requests(tmp_path):
    draft, world = run(tmp_path, env_version="0.30.0")
    assert world.calls == []
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_request_budget_exhaustion_leaves_the_remaining_cases_unknown(tmp_path):
    draft, world = run(tmp_path, cfg={"max_total_requests": 8})
    assert len(world.calls) <= 8
    assert judge(draft).verdicts["security"] == Verdict.UNKNOWN
    assert not all(challenge.attempted for challenge in draft.challenges)


@pytest.mark.parametrize("cfg", [{"max_output_tokens": 17}, {"max_total_requests": 33},
    {"max_duration_seconds": 301}, {"read_delay_ms": 251}, {"request_timeout": float("inf")},
    {"request_timeout": 121}, {"endpoint": "/abort"}, {"tokenize_endpoint": "/arbitrary"},
    {"max_total_requests": True}])
def test_invalid_bounds_or_routes_are_rejected_before_any_probe(tmp_path, cfg):
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, cfg=cfg)
    assert not world.calls and world.inspections == 0


@pytest.mark.parametrize("key,value", [("result_binding", ""), ("result_binding", 1),
                                       ("client_request_id_trust", "  "), ("client_request_id_trust", None)])
def test_invalid_policy_shapes_are_rejected_before_any_probe(tmp_path, key, value):
    world = World()
    with pytest.raises(ValueError):
        run(tmp_path, world, values={**VALUES, key: value})
    assert not world.calls and world.inspections == 0
