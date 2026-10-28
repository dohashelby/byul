"""EXAMPLE JSON/SSE proof stays transient; storage never contains raw projections."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.executors import request_binding as binding
from guard.models import Verdict, canonical_json, sha256_hex
from tests import test_request_binding as req
from tests.test_request_binding_scope import OutputWorld

PRIVATE_INPUT = "EXAMPLE_PRIVATE_BINDING_INPUT"
PRIVATE_OUTPUT = "EXAMPLE_PRIVATE_GENERATION_AND_DIAGNOSTIC"


class PrivateWorld(OutputWorld):
    def stream(self, network, requests, timeout):
        rows = super().stream(network, requests, timeout)
        for row in rows:
            if row["content_type"] == "application/json":
                value = json.loads(row["body"])
                value["choices"][0]["message"]["content"] = PRIVATE_OUTPUT
                raw = json.dumps(value)
            else:
                for event in row["events"]:
                    if event["data"] == "[DONE]":
                        continue
                    value = json.loads(event["data"])
                    for choice in value.get("choices", []):
                        if choice.get("delta", {}).get("content"):
                            choice["delta"]["content"] = PRIVATE_OUTPUT
                    event["data"] = json.dumps(value)
                raw = "".join("data: " + event["data"] + "\n\n" for event in row["events"])
            row.update(body=raw, body_bytes=len(raw.encode()), body_sha256=hashlib.sha256(raw.encode()).hexdigest(),
                       body_preview=PRIVATE_OUTPUT, detail=PRIVATE_OUTPUT, phase=PRIVATE_OUTPUT, elapsed_ms=PRIVATE_OUTPUT,
                       headers={"Authorization": PRIVATE_OUTPUT}, connection_id=PRIVATE_OUTPUT + row["id"])
        return rows


@pytest.fixture(autouse=True)
def fixed_private_fixture(monkeypatch):
    monkeypatch.setattr(binding.secrets, "token_hex", lambda _: PRIVATE_INPUT)


def assert_no_content(draft):
    for artifact in draft.artifacts:
        text = artifact.path.read_text(encoding="utf-8")
        assert PRIVATE_INPUT not in text and PRIVATE_OUTPUT not in text
        for key in ("body", "request", "response", "events", "tokens", "consumer_ids", "consumer_texts", "api_ids", "headers"):
            assert json.dumps(key) + ":" not in text


@pytest.mark.parametrize("mode", ["normal", "foreign-json", "foreign-sse", "stream-id", "missing-consumer", "missing-done", "output-swap"])
def test_json_sse_hash_projection_preserves_scope_and_independent_fail(tmp_path, monkeypatch, mode):
    seen = []
    original = binding.analyze_response
    def review(response, **kwargs):
        result = original(response, **kwargs)
        if response.get("outcome") == 200:
            if mode == "missing-consumer" and response["id"] == "json-generated-A":
                assert not result.get("consumer_ids") and not result["verified"]
                assert "prompt_text" not in response["body"] and result["gaps"]
            else:
                assert PRIVATE_INPUT in response["body"] and result.get("consumer_ids")
            seen.append((response, result))
        return result
    monkeypatch.setattr(binding, "analyze_response", review)
    options = {}
    if mode.startswith("foreign"):
        options = {"foreign": "json-generated-A" if mode == "foreign-json" else "sse-generated-A", "raises": "after"}
    if mode == "stream-id": options = {"stream_id_change": "sse-distinct_body-A"}
    if mode == "missing-consumer": options = {"missing_consumer": "json-generated-A"}
    if mode == "missing-done": options = {"missing_done": "sse-distinct_body-A"}
    draft, world = req.run(tmp_path, PrivateWorld(swap=mode == "output-swap", **options))
    assert_no_content(draft)
    assert req.judge(draft).verdicts["security"] == (Verdict.FAIL if mode in {"foreign-json", "foreign-sse", "stream-id"} else Verdict.UNKNOWN)
    assert draft.observations["output_binding_verified"].value is False
    assert draft.observations["input_projection_verified"].value is (mode in {"normal", "output-swap"})
    assert seen and PRIVATE_INPUT in seen[0][0]["body"] and seen[0][1]["consumer_texts"]
    value = json.loads(next(a for a in draft.artifacts if a.name == "request_binding_samples.json").path.read_text(encoding="utf-8"))["capture"]
    assert value["artifact_profile"] == binding.ARTIFACT_PROFILE
    assert value["samples"][0]["request_fixture_sha256"] == sha256_hex(canonical_json(world.calls[0]["body"]))
    first_raw = json.dumps({"tokens": world.tokens(world.calls[0]["body"]), "count": len(world.tokens(world.calls[0]["body"])), "max_model_len":128}).encode()
    assert value["samples"][0]["capture"]["original_body_sha256"] == hashlib.sha256(first_raw).hexdigest()
    for pair in value["summary"]["pairs"]:
        for timing in pair["timings"]:
            if timing["connection_id_sha256"]:
                assert timing["request_sent_ms"] == 1 and timing["end_ms"] == 20
                assert timing["connection_id_sha256"] == sha256_hex(canonical_json(PRIVATE_OUTPUT + timing["id"]))


@pytest.mark.parametrize("outcome", [True, 200.0, 99, 600, PRIVATE_OUTPUT, {"code":PRIVATE_OUTPUT}, [PRIVATE_OUTPUT], None],
                         ids=["bool", "float", "low", "high", "text", "object", "array", "null"])
def test_one_malformed_response_cannot_erase_other_connection_violation(tmp_path, outcome):
    class InvalidWorld(PrivateWorld):
        def stream(self, network, requests, timeout):
            rows = super().stream(network, requests, timeout)
            for row in rows:
                if row["id"] == "json-generated-B": row["outcome"] = outcome
            return rows
    draft, _ = req.run(tmp_path, InvalidWorld(foreign="json-generated-A", raises="after"))
    assert_no_content(draft)
    assert req.judge(draft).verdicts["security"] == Verdict.FAIL
    assert draft.observations["security_violations"].value
    assert draft.observations["collection_complete"].value is False


@pytest.mark.parametrize("value", [PRIVATE_OUTPUT, True, float("inf")], ids=["text","bool","infinite"])
def test_invalid_unused_timing_metadata_is_omitted_without_changing_overlap(tmp_path, value):
    class TimingWorld(PrivateWorld):
        def stream(self, network, requests, timeout):
            rows = super().stream(network, requests, timeout)
            for row in rows: row["headers_ms"] = value
            return rows
    draft, _ = req.run(tmp_path, TimingWorld())
    assert_no_content(draft)
    data = req.summary(draft)
    assert all(pair["verified"] and pair["overlap"] for pair in data["pairs"])
    assert all(timing["headers_ms"] is None and timing["request_sent_ms"] == 1
               for pair in data["pairs"] for timing in pair["timings"])
    assert req.judge(draft).verdicts["security"] == Verdict.UNKNOWN


def test_dynamic_exception_type_and_message_are_not_evidence(tmp_path):
    error = type(PRIVATE_OUTPUT, (Exception,), {})
    class RaisingWorld(PrivateWorld):
        def stream(self, network, requests, timeout):
            if requests[0]["id"] == "after": raise error(PRIVATE_OUTPUT)
            return super().stream(network, requests, timeout)
    draft, _ = req.run(tmp_path, RaisingWorld(foreign="json-generated-A"))
    assert_no_content(draft)
    assert req.judge(draft).verdicts["security"] == Verdict.FAIL


def test_fixture_fingerprint_matches_the_pinned_pre_projection_contract(tmp_path):
    draft, _ = req.run(tmp_path, PrivateWorld())
    assert draft.comparability.fixture_sha256 == "6f22d621745e1b4a9f703327a75987edcb24f1b74d66b3a293c42118cbba5f63"


def test_product_saved_evidence_and_report_replay_preserve_refs(tmp_path, monkeypatch):
    from guard.core.run import RunConfig, run_plan
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors import registry
    from guard.models import load_pack, new_id
    from guard.store import Store
    from guard.store.packs import put_pack
    from guard.store.bundle import export_bundle, restore_bundle
    from tests.builders import PACK_DIR, environment, policy, T0
    world = PrivateWorld(foreign="sse-generated-A", raises="after")
    target = tmp_path / "target.json"
    target.write_text(json.dumps(req.target()), encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS, "request_binding:privacy-sim", lambda cfg:
        binding.RequestBindingExecutor({"target":str(target)}, docker=world, container_probe=world.probe,
                                       stream_probe=world.stream, environ={}))
    pack = load_pack(PACK_DIR)
    store = Store(tmp_path / "store")
    put_pack(store, PACK_DIR)
    env = environment(id=new_id("ENV"), engine={"name":"vllm","version":"0.31.0","image_digest":req.IMAGE}, model={"id":"fixture-model"})
    pol = policy(pack.ref(),id=new_id("POL"),scope={"include":["REQ-11"],"exclude":[]},values={"REQ-11":deepcopy(req.VALUES)})
    store.put_many([env,pol])
    plan = select(pack,[env],pol)
    store.put(plan)
    (evd,) = run_plan(store,pack,plan,RunConfig(executors={"request_binding":"privacy-sim"},adapter="vllm"))
    (result,) = judge_plan(store,pack,plan,as_of=T0)
    assert result.verdicts.security == Verdict.FAIL and evd.data_origin.value == "EXAMPLE"
    assert evd.execution.executor.version == "0.3.3"
    for artifact in evd.artifacts:
        text = (store.root / artifact.path).read_text(encoding="utf-8")
        assert PRIVATE_INPUT not in text and PRIVATE_OUTPUT not in text
    rpt = build_report(store,pack,plan,as_of=T0)
    store.put(rpt)
    export_bundle(store.root,tmp_path/"evidence.zip")
    restore_bundle(tmp_path/"evidence.zip",tmp_path/"restored")
    restored = Store(tmp_path/"restored")
    assert store.verify() == restored.verify() == []
    assert restored.resolve(evd.ref()).to_dict() == evd.to_dict()
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
    (replayed,) = judge_plan(restored,pack,restored.resolve(plan.ref()),as_of=T0)
    assert replayed.ref() == result.ref()
