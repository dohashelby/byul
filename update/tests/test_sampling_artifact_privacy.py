"""EXAMPLE sampling storage boundaries; no Docker, Kubernetes or engine startup."""
from copy import deepcopy
import hashlib
import json

import pytest

from guard.models import Verdict, canonical_json, sha256_hex
from guard.executors.sampling_validation import ARTIFACT_PROFILE
from tests import test_sampling_validation as sample
from tests import test_kubernetes_sampling as kube

INPUT = "EXAMPLE_PRIVATE_CONTROL_PROMPT"
OUTPUT = "EXAMPLE_PRIVATE_GENERATION_AND_DIAGNOSTIC"


class PrivateWorld(sample.World):
    def probe(self, network, requests, timeout):
        rows=super().probe(network,requests,timeout)
        for row in rows:
            body=json.loads(row["body"])
            body["choices"][0]["text"]=OUTPUT
            body["prompt_token_ids"]=[987654,876543]
            row.update(body=json.dumps(body),body_preview=OUTPUT,detail=OUTPUT,phase=OUTPUT,
                       headers={"Authorization":OUTPUT},elapsed_ms=OUTPUT,body_bytes=OUTPUT)
        return rows


def private_check():
    raw=sample.check().model_dump()
    raw["functional_probes"][0]["body"]["prompt"]=INPUT
    return sample.CheckDef.model_validate(raw)


def assert_content_absent(draft):
    for artifact in draft.artifacts:
        text=artifact.path.read_text(encoding="utf-8")
        assert INPUT not in text and OUTPUT not in text
        assert '"prompt_token_ids":' not in text and '"prompt":' not in text
        assert '"body":' not in text and '"headers":' not in text


@pytest.mark.parametrize("mode",["rejected","accepted","before_failure","later_failure","late_exception"])
def test_docker_projection_keeps_content_transient_and_preserves_prior_observations(tmp_path,mode):
    overrides={}
    if mode in {"accepted","later_failure","late_exception"}:overrides["sampling-temperature-challenge"]=200
    if mode=="before_failure":overrides["sampling-before"]=500
    if mode=="later_failure":overrides["sampling-temperature-after"]=500
    if mode=="late_exception":overrides["sampling-top_p-challenge"]="raise"
    world=PrivateWorld(overrides)
    draft,_=sample.run(tmp_path,world,definition=private_check())
    assert_content_absent(draft)
    result=sample.judge(draft)
    assert result.verdicts["security"]==Verdict.UNKNOWN
    assert result.verdicts["functional"]==(Verdict.UNKNOWN if mode in {"before_failure","later_failure"} else Verdict.PASS)
    assert draft.observations["runner_consumption_verified"].value is False
    if mode in {"accepted","later_failure","late_exception"}:
        assert "temperature" in draft.observations["sampling_accepted_options"].value
    data=sample.summary(draft)
    assert data["artifact_profile"]==ARTIFACT_PROFILE
    assert "control" not in data["fixture"]
    assert data["fixture"]["control_sha256"]==sha256_hex(canonical_json(world.calls[0]["body"]))
    assert len(data["response_projections"])==len(world.calls)
    first=data["response_projections"][0]["capture"]
    # Confirm that content was inspected in memory, and its exact UTF-8 hash is retained.
    body={"model":"fixture-model","choices":[{"text":OUTPUT,"finish_reason":"length"}],
          "usage":{"completion_tokens":1},"prompt_token_ids":[987654,876543]}
    assert first["original_body_sha256"]==hashlib.sha256(json.dumps(body).encode()).hexdigest()
    assert first["stored_body_representation"]=="omitted_hash_and_parse_state_only"


@pytest.mark.parametrize("outcome",[True,200.0,99,600,OUTPUT,{"code":OUTPUT},[OUTPUT],None],
                         ids=["bool","float","low","high","text","object","array","null"])
def test_malformed_transport_outcome_is_fixed_gap_without_source_content(tmp_path,outcome):
    world=PrivateWorld({"sampling-temperature-challenge":outcome})
    draft,_=sample.run(tmp_path,world,definition=private_check())
    assert_content_absent(draft)
    assert draft.observations["sampling_challenge_outcomes"].value["temperature"]=="incomplete_probe_result"
    assert "temperature" not in draft.observations["sampling_accepted_options"].value
    assert sample.judge(draft).verdicts["security"]==Verdict.UNKNOWN


def test_valid_http_method_metadata_survives_without_preview_or_free_text(tmp_path):
    draft,_=sample.run(tmp_path,PrivateWorld(),definition=private_check())
    records=[json.loads(line) for line in draft.artifacts[0].path.read_text(encoding="utf-8").splitlines()[:-1]]
    assert {r["method"] for r in records}=={"GET","POST"}
    assert all(r["detail"]=="RAW_INPUT_RESPONSE_OMITTED" and r["body_preview"] is None for r in records)
    assert all(r["elapsed_ms"] is None and r["phase"] is None for r in records)


def test_kubernetes_uses_same_projection_without_reintroducing_control_content(tmp_path):
    draft,world=kube.run(tmp_path)
    text=draft.artifacts[0].path.read_text(encoding="utf-8")
    assert kube.PRIVATE not in text and "guard sampling control" not in text
    data=kube.summary(draft)
    assert data["artifact_profile"]==ARTIFACT_PROFILE
    assert len(data["response_projections"])==len(world.execs)==16
    assert all(row["capture"]["stored_body_is_verbatim"] is False for row in data["response_projections"])
    assert "control" not in data["fixture"]
    assert kube.judge(draft).verdicts=={"security":Verdict.UNKNOWN,"functional":Verdict.PASS,"performance":Verdict.UNKNOWN}
    assert draft.observations["runner_consumption_verified"].value is False


def test_kubernetes_fixture_fingerprint_matches_the_pinned_pre_privacy_contract(tmp_path):
    draft,_=kube.run(tmp_path)
    assert draft.comparability.fixture_sha256 == "4703cf633155da142282657b355ec2205a7d34bb38e35c17fe1b84dad49f669f"
