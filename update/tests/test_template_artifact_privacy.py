"""EXAMPLE transports: prove in memory, store hashes and bounded metadata only."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.models import Verdict, canonical_json, sha256_hex
from guard.executors.template_validation import ARTIFACT_PROFILE, SAFE_OVERRIDE
from tests import test_template_validation as req
from tests import test_template_supply_approval as supply

PRIVATE_INPUT = "EXAMPLE_PRIVATE_TEMPLATE_INPUT"
PRIVATE_OUTPUT = "EXAMPLE_PRIVATE_GENERATED_TEXT_AND_DIAGNOSTIC"


class PrivateWorld(req.World):
    @staticmethod
    def tokens(body):
        return [987654 + value for value in req.World.tokens(body)]

    def probe(self, network, requests, timeout):
        rows = super().probe(network, requests, timeout)
        for row in rows:
            value = json.loads(row["body"])
            if "choices" in value:
                value["choices"][0]["message"]["content"] = PRIVATE_OUTPUT
                value["choices"][0]["token_ids"] = [876543]
            row.update(body=json.dumps(value), body_preview=PRIVATE_OUTPUT, detail=PRIVATE_OUTPUT,
                       phase=PRIVATE_OUTPUT, elapsed_ms=PRIVATE_OUTPUT, headers={"Authorization": PRIVATE_OUTPUT})
        return rows


def private_check():
    raw = req.check().model_dump()
    raw["functional_probes"][0]["body"]["messages"][0]["content"] = PRIVATE_INPUT
    return req.CheckDef.model_validate(raw)


def assert_no_content(draft):
    for artifact in draft.artifacts:
        text = artifact.path.read_text(encoding="utf-8")
        assert PRIVATE_INPUT not in text and PRIVATE_OUTPUT not in text
        assert SAFE_OVERRIDE.strip() not in text and '"prompt_token_ids":' not in text
        assert '"tokens":' not in text and '"messages":' not in text and '"body":' not in text
        assert "876543" not in text and "987663" not in text


@pytest.mark.parametrize("mode", ["rejected", "accepted_override", "later_failure", "unrelated", "late_exception"])
def test_private_content_is_transient_and_native_proofs_keep_their_verdicts(tmp_path, mode):
    options = {"allow_override": mode == "accepted_override", "after_error": mode == "later_failure",
               "unrelated_400": mode == "unrelated", "raises": ["after"] if mode == "late_exception" else []}
    world = PrivateWorld(context=64 if mode == "later_failure" else 32, **options)
    draft, _ = req.run(tmp_path, world, definition=private_check())
    assert_no_content(draft)
    expected = (Verdict.FAIL if mode in {"accepted_override", "later_failure"}
                else Verdict.PASS if mode == "rejected" else Verdict.UNKNOWN)
    assert req.judge(draft).verdicts["security"] == expected
    assert draft.normal_control.succeeded is (mode not in {"later_failure", "late_exception"})
    artifact = next(a for a in draft.artifacts if a.name == "template_samples.json")
    value = json.loads(artifact.path.read_text(encoding="utf-8"))["capture"]
    assert value["artifact_profile"] == ARTIFACT_PROFILE
    first = value["samples"][0]
    assert first["request_fixture_sha256"] == sha256_hex(canonical_json(world.calls[0]["body"]))
    raw = json.dumps({"count": 11, "tokens": PrivateWorld.tokens(world.calls[0]["body"]), "max_model_len": world.context}).encode()
    assert first["capture"]["original_body_sha256"] == hashlib.sha256(raw).hexdigest()
    assert first["capture"]["original_body_bytes"] == len(raw)
    assert first["capture"]["stored_body_is_verbatim"] is False
    assert value["summary"]["fixture"]["normal_sha256"] == sha256_hex(canonical_json(
        private_check().functional_probes[0].body["messages"]))
    if mode == "rejected":
        assert value["summary"]["enforcement_proof"]["phase"] == "pre_generation"
        assert draft.observations["max_consumed_prompt_tokens"].value == 31


@pytest.mark.parametrize("outcome", [True, 200.0, 99, 600, PRIVATE_OUTPUT, {"code": PRIVATE_OUTPUT}, [PRIVATE_OUTPUT], None],
                         ids=["bool", "float", "low", "high", "text", "object", "array", "null"])
def test_malformed_outcomes_cannot_be_persisted_or_count_as_rejection(tmp_path, outcome):
    class InvalidWorld(PrivateWorld):
        def probe(self, network, requests, timeout):
            rows = super().probe(network, requests, timeout)
            if requests[0]["id"] == "overbound":
                rows[0]["outcome"] = outcome
            return rows
    draft, _ = req.run(tmp_path, InvalidWorld(), definition=private_check())
    assert_no_content(draft)
    assert req.judge(draft).verdicts["security"] == Verdict.UNKNOWN
    value = json.loads(next(a for a in draft.artifacts if a.name == "template_samples.json").path.read_text(encoding="utf-8"))["capture"]
    rejected = next(row for row in value["samples"] if row["case"] == "overbound")
    assert rejected["outcome"] == "probe_error"
    assert draft.observations["enforcement_observed"].value is False


def test_exception_class_name_and_message_never_escape_into_evidence(tmp_path):
    error = type(PRIVATE_OUTPUT, (Exception,), {})
    class RaisingWorld(PrivateWorld):
        def probe(self, network, requests, timeout):
            if requests[0]["id"] == "after":
                raise error(PRIVATE_OUTPUT)
            return super().probe(network, requests, timeout)
    draft, _ = req.run(tmp_path, RaisingWorld(context=64), definition=private_check())
    assert_no_content(draft)
    assert req.judge(draft).verdicts["security"] == Verdict.FAIL


def test_supply_projection_omits_fixed_request_and_retains_unverified_consumer(tmp_path):
    draft, _ = supply.run(tmp_path)
    text = "\n".join(a.path.read_text(encoding="utf-8") for a in draft.artifacts)
    assert supply.supply.NORMAL_TEXT not in text and '"messages":' not in text and '"body":' not in text
    assert "EXAMPLE-private-model-content" not in text
    assert draft.normal_control.succeeded and draft.observations["input_projection_exact_match"].value is True
    assert draft.observations["template_consumer_verified"].value is False
    assert supply.judge(draft).verdicts["security"] == Verdict.UNKNOWN


@pytest.mark.parametrize("profile,expected", [
    ("REQ-07", "91dbf877451d68c8a80be68685bbf47cb7643672b43336d866b25ec5fb006932"),
    ("SUP-03", "1ff0d6e031a18cb5eb151d406f140d4cb75f72b23a8a25e7e2037227c9dcba25"),
])
def test_fixture_fingerprint_matches_pinned_pre_projection_contract(tmp_path, profile, expected):
    # Derived from the fixture expressions in dd5b4b8; no collector was run to choose these hashes.
    draft, _ = req.run(tmp_path) if profile == "REQ-07" else supply.run(tmp_path)
    assert draft.comparability.fixture_sha256 == expected


def test_product_pipeline_preserves_hashes_refs_and_fail_after_late_collection_gap(tmp_path, monkeypatch):
    from guard.core.run import RunConfig, run_plan
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors import registry
    from guard.executors.template_validation import TemplateValidationExecutor
    from guard.models import load_pack, new_id
    from guard.store import Store
    from guard.store.packs import put_pack
    from guard.store.bundle import export_bundle, restore_bundle
    from tests.builders import PACK_DIR, environment, policy, T0
    world = PrivateWorld(context=64, after_error=True)
    target = tmp_path / "target.json"
    target.write_text(json.dumps(req.target(64)), encoding="utf-8")
    monkeypatch.setitem(registry.EXECUTORS, "template_validation:privacy-sim", lambda cfg:
        TemplateValidationExecutor({**req.settings(), "target": str(target)}, docker=world,
                                   container_probe=world.probe, environ={}))
    pack = load_pack(PACK_DIR)
    store = Store(tmp_path / "store")
    put_pack(store, PACK_DIR)
    env = environment(id=new_id("ENV"), engine={"name": "vllm", "version": "0.31.0", "image_digest": req.IMAGE},
                      model={"id": "fixture-model"})
    pol = policy(pack.ref(), id=new_id("POL"), scope={"include": ["REQ-07"], "exclude": []}, values={"REQ-07": deepcopy(req.VALUES)})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={"template_validation": "privacy-sim"}, adapter="vllm"))
    (result,) = judge_plan(store, pack, plan, as_of=T0)
    assert result.verdicts.security == Verdict.FAIL and result.verdicts.functional == Verdict.UNKNOWN
    assert evd.data_origin.value == "EXAMPLE" and evd.execution.executor.version == "0.4.0"
    for artifact in evd.artifacts:
        assert PRIVATE_OUTPUT not in (store.root / artifact.path).read_text(encoding="utf-8")
    rpt = build_report(store, pack, plan, as_of=T0)
    store.put(rpt)
    before = evd.to_dict()
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert store.verify() == restored.verify() == []
    assert restored.resolve(evd.ref()).to_dict() == before
    assert restored.resolve(rpt.ref()).to_dict() == rpt.to_dict()
    (replayed,) = judge_plan(restored, pack, restored.resolve(plan.ref()), as_of=T0)
    assert replayed.ref() == result.ref()
