"""EXAMPLE native proof stays in memory; persisted input evidence omits content."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.executors.input_validation import InputValidationExecutor, ARTIFACT_PROFILE, _capture_projection, _log_projection
from guard.models import Verdict, canonical_json, sha256_hex
from tests import test_input_response_semantics as semantic
from tests.builders import completion_response

PRIVATE = "EXAMPLE_PRIVATE_RESPONSE_CONTENT_NOT_A_CONFIGURED_KEY"


def private_success(request):
    value = completion_response(request)
    body = json.loads(value["body"])
    body["choices"][0]["text"] = PRIVATE
    body["prompt_token_ids"] = [987654, 876543]
    value.update(body=json.dumps(body), body_preview=PRIVATE, detail=PRIVATE, headers={"Authorization": PRIVATE})
    return value


@pytest.mark.parametrize("mode", ["reviewed", "accepted", "unrelated", "duplicate"])
def test_product_evidence_omits_raw_content_but_preserves_proof_hashes_and_verdicts(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(semantic, "completion_response", private_success)
    def challenge(req, case):
        if mode == "accepted":return private_success(req)
        if mode == "duplicate":
            raw='{"error":"'+PRIVATE+'","error":2}'
            return {"outcome":400,"body":raw,"body_preview":PRIVATE,"detail":PRIVATE}
        payload=semantic.error("Token id 100 is out of vocabulary") if case=="token_id" else semantic.length_error()
        if mode == "unrelated":payload=semantic.error(PRIVATE)
        return {**semantic.response(payload),"body_preview":PRIVATE,"detail":PRIVATE,"headers":{"Authorization":PRIVATE}}
    store,evd,result,sent=semantic._pipeline(tmp_path,monkeypatch,challenge)
    assert len(sent)==5 and evd.normal_control.succeeded
    assert result.verdicts.security==(Verdict.PASS if mode=="reviewed" else Verdict.UNKNOWN)
    assert result.verdicts.functional==Verdict.PASS
    assert evd.observations["challenge_consumption"].value=={"token_id":"unknown","length":"unknown"}
    assert evd.observations["confirmed_input_violations"].value==[]
    for artifact in evd.artifacts:
        text=(store.root/artifact.path).read_text(encoding="utf-8")
        assert PRIVATE not in text and "987654" not in text and "876543" not in text
        assert '"body":' not in text and '"prompt":' not in text and '"prompt_token_ids":' not in text
    ref=next(a for a in evd.artifacts if a.name=="input_response_samples.json")
    payload=json.loads((store.root/ref.path).read_text(encoding="utf-8"))["capture"]
    assert payload["artifact_profile"]==ARTIFACT_PROFILE and len(payload["samples"])==5
    sample=payload["samples"][0]
    assert sample["request_fixture_sha256"]==sha256_hex(canonical_json(sent[0]["body"]))
    raw=private_success(sent[0])["body"].encode()
    assert sample["capture"]["original_body_sha256"]==hashlib.sha256(raw).hexdigest()
    assert sample["capture"]["original_body_bytes"]==len(raw)
    assert sample["capture"]["stored_body_is_verbatim"] is False
    assert sample["capture"]["stored_body_representation"]=="omitted_hash_and_parse_state_only"
    fixture=payload["summary"]["fixture"]
    assert "cases" not in fixture and "control" not in fixture
    assert fixture["cases_sha256"]==sha256_hex(canonical_json({"token_id":[100],"length":[0]*9}))
    if mode=="duplicate":assert payload["samples"][1]["capture"]["original_json_state"]=="duplicate_keys"
    assert store.verify()==[]


def test_length_cause_reviewer_still_receives_complete_transient_samples(tmp_path, monkeypatch):
    from tests.test_input_length_cause import make_ctx, context, rejected
    ctx=make_ctx(tmp_path);sent=[];seen=[]
    def probe(network, requests, timeout):
        req,=requests;sent.append(deepcopy(req))
        if req["body"]["prompt"]==[50272]:
            return [{**semantic.response(semantic.error("Token id 50272 is out of vocabulary")),"body_preview":PRIVATE,"detail":PRIVATE}]
        if len(req["body"]["prompt"])==128:return [{**rejected(),"body_preview":PRIVATE,"detail":PRIVATE}]
        return [private_success(req)]
    ctx.cprobe=probe
    class Reviewer:
        def start(self):assert not sent
        def finish(self, samples):
            assert len(samples)==len(sent)==5
            assert PRIVATE in samples[0]["response"]["body"]
            assert samples[0]["request"]["body"]["prompt"]==[0]
            assert samples[1]["response"]["outcome"]==400 and samples[3]["response"]["body"]
            seen.extend(samples)
            return context()
    monkeypatch.setattr(ctx.adapter,"input_length_cause_collector",lambda _:Reviewer())
    executor=InputValidationExecutor({"vocab_size":50272,"vocab_source":"EXAMPLE source","max_probe_tokens":128,"length_cause_evidence":True})
    draft=executor._finish(ctx)
    assert seen and draft.normal_control.succeeded
    assert draft.observations["challenge_rejection_results"].value=={"token_id":"verified","length":"verified"}
    assert draft.observations["challenge_consumption"].value=={"token_id":"unknown","length":"unknown"}
    for artifact in draft.artifacts:assert PRIVATE not in artifact.path.read_text(encoding="utf-8")
    # The privacy reducer must not mutate data already handed to the reviewer.
    assert PRIVATE in seen[0]["response"]["body"] and seen[0]["request"]["body"]["prompt"]==[0]


@pytest.mark.parametrize("elapsed", [True, float("nan"), float("inf"), -1, 10**400, PRIVATE])
def test_untrusted_log_metadata_cannot_reintroduce_content_or_abort_projection(elapsed):
    projected=_log_projection({"outcome":200,"elapsed_ms":elapsed,"body_preview":PRIVATE,"detail":PRIVATE})
    assert projected=={"outcome":200,"detail":"RAW_INPUT_RESPONSE_OMITTED"}


def test_capture_metadata_failure_is_fixed_diagnostic_without_source_text():
    value=_capture_projection({"body":"\ud800"})
    assert value["original_json_state"]=="unavailable"
    assert value["diagnostic"]=="INPUT_CAPTURE_METADATA_UNAVAILABLE"
    assert value["stored_body_is_verbatim"] is False and "body" not in value
