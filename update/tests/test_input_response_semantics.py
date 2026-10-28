"""Response meaning and stored automatic evidence; every transport is EXAMPLE."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from guard.adapters.fake import FakeAdapter
from guard.adapters.vllm import SAFE_PROBE_REVIEWED_IMAGES, VllmAdapter
from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.input_response_semantics import OBSERVATIONS, contract_state, rejection_proof
from guard.executors.input_validation import InputValidationExecutor
from guard.models import ExecStatus, Verdict
from guard.store import Store
from tests.builders import baseline_pack, completion_response, environment, policy


def error(message, *, param=None, code=400, kind="BadRequestError"):
    return {"error": {"message": message, "type": kind, "param": param, "code": code}}


def response(payload, status=400):
    raw = json.dumps(payload, ensure_ascii=False)
    return {"outcome": status, "body": raw, "body_bytes": len(raw.encode("utf-8"))}


def request(case):
    return {"model": "EXAMPLE-model", "prompt": [100] if case == "token_id" else [0] * 9, "max_tokens": 1}


def review(payload, *, case="token_id", version="0.31.0", status=400, image=None, body=None, adapter=None):
    env = SimpleNamespace(engine=SimpleNamespace(version=version,
        image_digest=SAFE_PROBE_REVIEWED_IMAGES.get(version) if image is None else image))
    return rejection_proof(adapter or VllmAdapter(), env, case=case, method="POST", path="/v1/completions",
                           request_body=request(case) if body is None else body, response=response(payload, status))


def length_error(version="0.31.0"):
    if version == "0.31.0":
        message = ("This model's maximum context length is 9 tokens. However, you requested 1 output tokens "
                   "and your prompt contains at least 9 input tokens, for a total of at least 10 tokens. "
                   "Please reduce the length of the input prompt or the number of requested output tokens. "
                   "(parameter=input_tokens, value=9)")
    else:
        message = ("This model's maximum context length is 8 tokens. However, your prompt contains at least "
                   "9 input tokens, which exceeds this limit even without any output tokens. "
                   "Please reduce the length of the input prompt. (parameter=input_tokens, value=9)")
    return error(message, param="input_tokens")


@pytest.mark.parametrize("version", ["0.31.0", "0.32.0"])
@pytest.mark.parametrize("case", ["token_id", "length"])
def test_only_exact_reviewed_input_rejection_is_verified(version, case):
    payload = error("Token id 100 is out of vocabulary") if case == "token_id" else length_error(version)
    proof = review(payload, case=case, version=version)
    assert proof["state"] == "verified"
    assert proof["reason"] == "REVIEWED_INPUT_REJECTION" and proof["sources"]
    assert all(len(source["sha256"]) == 64 and source["path"].startswith("vllm/") for source in proof["sources"])
    assert len(proof["response_sha256"]) == 64


@pytest.mark.parametrize("payload", [error("generation failed"), {}, {"choices": [], "usage": {}}])
@pytest.mark.parametrize("status", [200, 201, 206])
def test_http_success_never_establishes_consumption_or_damage(payload, status):
    proof = review(payload, status=status)
    assert proof["state"] == "unverified"
    assert proof["reason"] == "HTTP_SUCCESS_DOES_NOT_ESTABLISH_CONSUMPTION"


@pytest.mark.parametrize("change", [
    lambda value: value["error"].update(message="Token id 101 is out of vocabulary"),
    lambda value: value["error"].update(param="model"),
    lambda value: value["error"].update(type="NotFoundError"),
    lambda value: value["error"].update(code="400"),
    lambda value: value.update(choices=[{"text": "ambiguous mixed response"}]),
])
def test_wrong_rejection_cause_shape_or_submitted_value_is_unverified(change):
    payload = error("Token id 100 is out of vocabulary")
    change(payload)
    assert review(payload)["state"] == "unverified"


@pytest.mark.parametrize("version,image", [
    ("0.31.0", "vllm/unreviewed@sha256:" + "f" * 64),
    ("0.32.0", SAFE_PROBE_REVIEWED_IMAGES["0.31.0"]),
    ("0.32.0+cpu", SAFE_PROBE_REVIEWED_IMAGES["0.32.0"]),
    ("future", SAFE_PROBE_REVIEWED_IMAGES["0.32.0"]),
])
def test_digest_version_mismatch_never_inherits_review(version, image):
    assert review(error("Token id 100 is out of vocabulary"), version=version, image=image)["state"] == "unverified"


def test_zero_output_budget_is_not_silently_relabelled_as_input_length_rejection():
    payload = error("max_tokens must be at least 1, got 0. (parameter=max_tokens, value=0)", param="max_tokens")
    assert review(payload, case="length", version="0.32.0")["state"] == "unverified"


@pytest.mark.parametrize("replacement", ["value=8", "contains at least 8 input", "total of at least 11", "parameter=prompt"])
def test_length_proof_matches_numeric_request_and_exact_validation_parameter(replacement):
    payload = length_error()
    original = ("value=9" if replacement.startswith("value") else
                "contains at least 9 input" if replacement.startswith("contains") else
                "total of at least 10" if replacement.startswith("total") else "parameter=input_tokens")
    payload["error"]["message"] = payload["error"]["message"].replace(original, replacement)
    assert review(payload, case="length")["state"] == "unverified"


@pytest.mark.parametrize("raw", [None, "", "not JSON", '{"error":{},"error":{}}', '{"error":NaN}', '[]'])
def test_incomplete_or_ambiguous_capture_cannot_become_verified(raw):
    env = SimpleNamespace(engine=SimpleNamespace(version="0.31.0", image_digest=SAFE_PROBE_REVIEWED_IMAGES["0.31.0"]))
    proof = rejection_proof(VllmAdapter(), env, case="token_id", method="POST", path="/v1/completions",
        request_body=request("token_id"), response={"outcome": 400, "body": raw})
    assert proof["state"] == "unverified"


def test_declared_truncation_byte_mismatch_and_adapter_errors_are_collection_gaps():
    env = SimpleNamespace(engine=SimpleNamespace(version="0.31.0", image_digest=SAFE_PROBE_REVIEWED_IMAGES["0.31.0"]))
    valid = response(error("Token id 100 is out of vocabulary"))
    for changes in ({"body_truncated": True}, {"body_bytes": valid["body_bytes"] + 1}):
        proof = rejection_proof(VllmAdapter(), env, case="token_id", method="POST", path="/v1/completions",
            request_body=request("token_id"), response={**valid, **changes})
        assert proof["state"] == "unverified"

    class BrokenAdapter:
        def input_rejection_proof(self, **kwargs):
            raise RuntimeError("PRIVATE_AUTHORIZATION_VALUE")

    for adapter in (BrokenAdapter(), FakeAdapter(), object()):
        proof = rejection_proof(adapter, env, case="token_id", method="POST", path="/v1/completions",
            request_body=request("token_id"), response=valid)
        assert proof["state"] == "unverified"
        assert "PRIVATE_AUTHORIZATION_VALUE" not in json.dumps(proof)


def _pipeline(tmp_path, monkeypatch, challenge_response):
    pack = baseline_pack()
    check = pack.checks["REQ-09"]
    assert contract_state(check) == "current"
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"lab": True, "engine": {"trusted_url": "http://EXAMPLE-engine:8000"},
        "origins": {"trusted": {"network": "EXAMPLE-inside"}}}), encoding="utf-8")
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": SAFE_PROBE_REVIEWED_IMAGES["0.31.0"]},
                      model={"id": "EXAMPLE-model"})
    pol = policy(pack.ref(), scope={"include": ["REQ-09"]}, values={"REQ-09": {
        "max_input_tokens": 8, "rejection_codes": ["400"],
        "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]}})
    sent = []

    def probe(_network, requests, _timeout):
        results = []
        for req in requests:
            sent.append(deepcopy(req))
            prompt = req["body"]["prompt"]
            if prompt == [0]:
                results.append(completion_response(req))
            else:
                results.append(challenge_response(req, "token_id" if prompt == [100] else "length"))
        return results

    executor = InputValidationExecutor({"target": str(target), "vocab_size": 100,
        "vocab_source": "EXAMPLE fixture vocabulary"}, container_probe=probe)
    monkeypatch.setitem(registry.EXECUTORS, "input_validation:response-semantics", lambda _cfg: executor)
    store = Store(tmp_path / "store")
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    (evd,) = run_plan(store, pack, plan, RunConfig(executors={"input_validation": "response-semantics"}, adapter="vllm"))
    (result,) = judge_plan(store, pack, plan, env.created_at)
    return store, evd, result, sent


@pytest.mark.parametrize("payload", [error("generation failed"), {}, None])
def test_healthy_controls_with_only_challenge_http200_error_are_security_unknown(tmp_path, monkeypatch, payload):
    def challenge(req, _case):
        return completion_response(req) if payload is None else response(payload, 200)

    store, evd, result, sent = _pipeline(tmp_path, monkeypatch, challenge)
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin.value == "EXAMPLE"
    assert evd.execution.executor.version == "0.7.0" and evd.execution.adapter.version == "0.4.2"
    assert result.verdicts.security == Verdict.UNKNOWN and result.verdicts.functional == Verdict.PASS
    assert evd.normal_control.succeeded is True and len(sent) == 5
    assert evd.observations["challenge_outcomes"].value == {"token_id": "200", "length": "200"}
    assert evd.observations["accepted_challenges"].value == ["token_id", "length"]
    assert evd.observations["challenge_rejection_results"].value == dict.fromkeys(["token_id", "length"], "unverified")
    assert evd.observations["challenge_consumption"].value == dict.fromkeys(["token_id", "length"], "unknown")
    assert evd.observations["confirmed_input_violations"].value == []
    samples = next(artifact for artifact in evd.artifacts if artifact.name == "input_response_samples.json")
    payload = json.loads((store.root / samples.path).read_text(encoding="utf-8"))
    assert len(payload["capture"]["samples"]) == 5
    assert store.verify() == []


def test_reviewed_rejections_allow_bounded_pass_without_inventing_consumption(tmp_path, monkeypatch):
    store, evd, result, _sent = _pipeline(tmp_path, monkeypatch, lambda _req, case:
        response(error("Token id 100 is out of vocabulary") if case == "token_id" else length_error()))
    assert result.verdicts.security == result.verdicts.functional == Verdict.PASS
    assert evd.observations["challenge_rejection_results"].value == dict.fromkeys(["token_id", "length"], "verified")
    assert evd.observations["confirmed_input_violations"].value == []
    assert evd.observations["challenge_consumption"].value == dict.fromkeys(["token_id", "length"], "unknown")
    assert evd.observations["accepted_challenges"].value == []
    assert store.verify() == []


def test_unrelated_http400_rejection_remains_unknown_with_healthy_controls(tmp_path, monkeypatch):
    _store, evd, result, _sent = _pipeline(tmp_path, monkeypatch, lambda _req, _case:
        response(error("The model does not exist", param="model")))
    assert evd.normal_control.succeeded is True
    assert result.verdicts.security == Verdict.UNKNOWN and result.verdicts.functional == Verdict.PASS


def test_later_collection_error_preserves_earlier_verified_rejection(tmp_path, monkeypatch):
    def challenge(_req, case):
        if case == "length":
            raise TimeoutError("PRIVATE_DAEMON_OR_AUTHORIZATION_TEXT")
        return response(error("Token id 100 is out of vocabulary"))

    store, evd, result, _sent = _pipeline(tmp_path, monkeypatch, challenge)
    assert evd.execution.status == ExecStatus.COMPLETED
    assert evd.observations["challenge_rejection_results"].value == {"token_id": "verified", "length": "unverified"}
    assert evd.observations["challenge_outcomes"].value == {"token_id": "400", "length": "probe_error"}
    assert evd.observations["confirmed_input_violations"].value == []
    assert result.verdicts.security == Verdict.UNKNOWN and result.verdicts.functional == Verdict.PASS
    for artifact in evd.artifacts:
        assert "PRIVATE_DAEMON_OR_AUTHORIZATION_TEXT" not in (store.root / artifact.path).read_text(encoding="utf-8")


def test_response_contract_never_partially_activates():
    check = baseline_pack().checks["REQ-09"]
    legacy = check.model_copy(update={"required_observations": [obs for obs in check.required_observations
        if obs.key not in OBSERVATIONS]})
    assert contract_state(legacy) == "legacy"
    partial = check.model_copy(update={"required_observations": [obs for obs in check.required_observations
        if obs.key != "challenge_consumption"]})
    assert contract_state(partial) == "incomplete"
    with pytest.raises(ValueError, match="response evidence contract is incomplete"):
        InputValidationExecutor({}).validate_policy(partial, {})
@pytest.mark.parametrize("body,state", [(' {"error":1,"error":2}', "duplicate_keys"),
                                      ('{"error":NaN}', "invalid_or_nonfinite"),
                                      ('{"error":1}', "valid")])
def test_sample_capture_describes_original_parse_state_before_json_redaction(body, state):
    from guard.executors.input_response_semantics import response_capture_metadata
    import hashlib
    captured = response_capture_metadata({"outcome": 400, "body": body})
    assert captured["original_json_state"] == state
    assert captured["original_body_sha256"] == hashlib.sha256(body.encode()).hexdigest()
    assert captured["original_body_bytes"] == len(body.encode())
    assert captured["stored_body_is_verbatim"] is False
