"""등록 producer 함수 출력 → 실제 B factory/collect/_finish → 정규화/저장/기존 judge.

모든 전송은 CPU fixture다. 등록 SHA를 수정하거나 engine/모델을 실행하지 않는다.
"""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.adapters import vllm_consumption_v2_callback as v2_callback
from guard.collectors import gpu_input_producers as producers, runner_consumption_v2_producer as producer_v2
from guard.core.judge import judge_evidence
from guard.core.run import _normalize
from guard.executors import common_consumption as bridge, registry
from guard.executors.base import EvidenceDraft
from guard.executors.http_common import Ctx
from guard.models import Evidence, DataOrigin, canonical_json, load_pack, sha256_hex, utcnow
from guard.models.objects import Artifact
from guard.store import Store
from tests.builders import completion_response, dummy_ref, environment, policy
from tests.test_gpu_input_producers import POD, SCOPE, PARAMS, api_rows, crow

PACK = load_pack(Path(__file__).parents[1] / "packs/PACK-BASELINE")


def make_runner(scope=SCOPE, cases=None, *, v2=False, rejected=(), received_rejections=(), max_id=None):
    cases = cases or {"normal": "1" * 64}
    api, worker = [], []
    for index, (name, fingerprint) in enumerate(cases.items()):
        for row in deepcopy(api_rows(fingerprint, reject=name in rejected)):
            row["call_sha256"] = str(index) + row["call_sha256"]
            row["operation_started_ns"] += index * 100
            row["operation_finished_ns"] += index * 100
            api.append(row)
        if name in rejected and name not in received_rejections:
            continue
        received = {"request_sha256": fingerprint, "prompt_tokens": 5, "max_token_id": max_id if name == "token_id" and max_id is not None else 99, "params": PARAMS}
        worker.append(crow(10000 + index * 100, "GPUModelRunner._update_states", "CALL", received=[received]))
        worker.append(crow(10001 + index * 100, "GPUModelRunner._sample", "CALL", applied=[{
            "request_sha256": fingerprint, "temperature_milli": 0, "top_p_milli": 1000, "top_k": 0,
            "logprobs": 0, "allowed_tokens": 3, "bad_words": 2}]))
    if v2:
        for row in worker:
            row.update(schema=v2_callback.SCHEMA, point={"GPUModelRunner._update_states": "GPUModelRunner.add_requests",
                "GPUModelRunner._sample": "GPUModelRunner.sample"}[row["point"]])
    function = producer_v2.runner_consumption_v2_record if v2 else producers.runner_consumption_record
    return function([{"pid": 1, "start_ticks": 1, "events": api}, {"pid": 112, "start_ticks": 2, "events": worker}], pod=POD, **scope)


def pinned(tmp_path, record, scope, requests=None):
    path = tmp_path / "common-record.json"; path.write_bytes(canonical_json(record))
    result = {"path": str(path), "sha256": sha256_hex(path.read_bytes()), "scope": scope}
    if requests is not None:
        result["requests"] = requests
    return result


def judged(tmp_path, cid, draft, env, values):
    check = PACK.checks[cid]; store = Store(tmp_path / "stored")
    artifacts = []
    for item in draft.artifacts:
        value = store.put_artifact(item.path.read_bytes())
        artifacts.append(Artifact(name=item.name, path=value.path, bytes=value.bytes, sha256=value.sha256,
                                  collected_at=utcnow(), collector=item.collector))
    evd = Evidence(id="EVD-B12-RUNNER", data_origin=draft.data_origin, check_id=cid, environment=env.ref(),
        plan=dummy_ref("PLAN"), side="single", execution={"status": draft.status}, artifacts=artifacts,
        **_normalize(check, draft)).seal()
    pol = policy(PACK.ref(), values={cid: values}); store.put(evd); store.put(pol)
    result = judge_evidence(check, evd, pol, utcnow()); store.put(result)
    for artifact in evd.artifacts:
        assert sha256_hex((store.root / artifact.path).read_bytes()) == artifact.sha256
    return evd, result


@pytest.mark.parametrize("v2", [False, True])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_real_registered_producer_roundtrip_and_request_coverage(v2):
    record, original = make_runner(v2=v2)
    result = bridge.runner(record, SCOPE, check_id="REQ-10", requests={"normal": "1" * 64}, required_cases=["normal"])
    assert original["supported"] and result["complete"] and not result["producer_authenticated"]
    assert not result["token_identity_verified"] and not result["whole_worker_scope_verified"]
    missing = bridge.runner(record, SCOPE, check_id="REQ-10", requests={"normal": "1" * 64}, required_cases=["missing"])
    assert not missing["complete"] and "B_RUNNER_CASE_COVERAGE_UNAVAILABLE" in missing["gaps"]


@pytest.mark.parametrize("damage", ["unsupported", "truncated", "session", "restarted"])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_source_and_generation_gates_are_not_lifted(damage):
    record, _ = make_runner(); record = deepcopy(record)
    if damage == "unsupported": record["producer"]["source_sha256"] = "f" * 64
    elif damage == "truncated": record["completeness"]["source_ended"] = False
    elif damage == "session": record["session"] = "0" * 32
    else: record["bindings"][1]["restart_count"] += 1
    if damage == "session":
        with pytest.raises(ValueError): bridge.runner(record, SCOPE, check_id="REQ-10", requests={"normal": "1" * 64}, required_cases=["normal"])
    elif damage == "restarted":
        # Record generation is explicit; this input cannot establish that both
        # records belong to one pod generation merely from session/time alone.
        result = bridge.runner(record, SCOPE, check_id="REQ-10", requests={"normal": "1" * 64}, required_cases=["normal"])
        assert result["complete"] is False
    else:
        assert not bridge.runner(record, SCOPE, check_id="REQ-10", requests={"normal": "1" * 64}, required_cases=["normal"])["complete"]


def input_finish(tmp_path, *, unsupported=False, consumed=False):
    cid = "REQ-08"; env = environment(model={"id": "test"})
    target = {"lab": False, "engine": {"trusted_url": "http://unused", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    requests = {name: sha256_hex(name.encode()) for name in ("before", "after-0", "reused_input")}
    record, _ = make_runner(scope, requests, rejected=() if consumed else ("reused_input",))
    if unsupported: record["producer"]["source_sha256"] = "f" * 64
    cfg = {"reused_token_ids": [11, 12, 13], "vocab_size": 100, "vocab_source": "EXAMPLE",
           "runner_input": pinned(tmp_path, record, scope, requests)}
    values = {"reused_token_field": "prompt_token_ids", "reused_token_allowed": False, "rejection_codes": ["400"]}
    def probe(network, batch, timeout):
        out = []
        for request in batch:
            body = request["body"]
            if "prompt_token_ids" in body and not consumed:
                out.append({"outcome": 400, "body": ""})
            else:
                reply = completion_response(request); payload = json.loads(reply["body"])
                payload["prompt_token_ids"] = body.get("prompt_token_ids", [5, 6]); reply["body"] = json.dumps(payload); out.append(reply)
        return out
    executor = registry.resolve_executor("input_validation", "real")({"input_validation": cfg})
    executor.validate_policy(PACK.checks[cid], values)
    ctx = Ctx(check=PACK.checks[cid], env=env, adapter=VllmAdapter(), target=target, policy_values=values,
        timeout=1, allow_mutating=False, keys={}, secrets=[], cprobe=probe, workdir=tmp_path / "work", started=utcnow())
    draft = executor._finish(ctx)
    return draft, *judged(tmp_path, cid, draft, env, values)


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_registered_input_collect_finish_survives_normalization_and_stored_artifact(tmp_path):
    draft, evd, result = input_finish(tmp_path)
    assert draft.data_origin == DataOrigin.EXAMPLE and evd.data_origin == DataOrigin.EXAMPLE
    assert result.data_origin == DataOrigin.EXAMPLE
    assert result.verdicts.security.value == "PASS"
    assert evd.observations["reused_input_trials"].value[0]["runner_metadata"]["complete"]
    assert any(a.name == "b_common_consumption.json" for a in evd.artifacts)


@pytest.mark.parametrize("consumed,verdict", [(False, "UNKNOWN"), (True, "FAIL")])
def test_unregistered_input_caps_pass_and_keeps_independent_fail(tmp_path, consumed, verdict):
    draft, evd, result = input_finish(tmp_path, unsupported=True, consumed=consumed)
    assert draft.status.value == "ABORTED" and result.verdicts.security.value == verdict
    if consumed: assert evd.observations["reused_input_violations"].value


def test_supplied_scope_and_file_hash_are_independent_inputs(tmp_path):
    record, _ = make_runner(); cfg = pinned(tmp_path, record, SCOPE)
    bridge.load_input(cfg, environment_ref=SCOPE["environment_ref"], target_sha256=SCOPE["target_sha256"])
    Path(cfg["path"]).write_bytes(b"{}")
    with pytest.raises(ValueError, match="BYTES_MISMATCH"):
        bridge.load_input(cfg, environment_ref=SCOPE["environment_ref"], target_sha256=SCOPE["target_sha256"])


def test_supply_record_is_sup03_only_and_not_native_tensor_consumption():
    from guard.adapters import vllm_supply_read_callback as callback
    rows = [{"schema": callback.SCHEMA, "phase": "OPEN", "root": 0, "relative": "config.json", "write": False,
             "regular": True, "size": 2, "opened_ns": 1}]
    hashes = [{"root": 0, "relative": "config.json", "sha256": "a" * 64, "size": 2, "state": "hashed"}]
    record, _ = producers.supply_read_record([{"pid": 1, "start_ticks": 1, "events": rows}], hashes, pod=POD, **SCOPE)
    projected = bridge.supply_reads(record, SCOPE)
    assert projected["files"] and not projected["actual_consumer_verified"] and not projected["approval_authority_verified"]
    with pytest.raises(ValueError, match="CHECK_UNSUPPORTED"):
        bridge.supply_reads(record, SCOPE, check_id="SUP-05")


@pytest.mark.usefixtures("example_registered_callback_producers")
def test_registered_req09_collect_maps_observed_runner_range_to_original_fail(tmp_path):
    from guard.adapters.vllm import SAFE_PROBE_REVIEWED_IMAGES
    from tests.test_input_response_semantics import error, length_error, response
    env = environment(model={"id": "test"}, engine={"name": "vllm", "version": "0.31.0",
                                                   "image_digest": SAFE_PROBE_REVIEWED_IMAGES["0.31.0"]})
    target = {"lab": True, "engine": {"trusted_url": "http://unused", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    requests = {name: sha256_hex(name.encode()) for name in ("before", "after_token_id", "after_length", "token_id", "length")}
    record, _ = make_runner(scope, requests, rejected=("token_id", "length"), received_rejections=("token_id",), max_id=100)
    cfg = {"vocab_size": 100, "vocab_source": "EXAMPLE", "max_probe_tokens": 16,
           "runner_input": pinned(tmp_path, record, scope, requests)}
    values = {"max_input_tokens": 8, "rejection_codes": ["400"], "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]}
    def probe(network, batch, timeout):
        out = []
        for request in batch:
            prompt = request["body"]["prompt"]
            if prompt == [100]: out.append(response(error("Token id 100 is out of vocabulary", param="prompt")))
            elif len(prompt) > 8: out.append(response(length_error()))
            else: out.append(completion_response(request))
        return out
    executor = registry.resolve_executor("input_validation", "real")({"input_validation": cfg})
    executor.validate_policy(PACK.checks["REQ-09"], values)
    ctx = Ctx(check=PACK.checks["REQ-09"], env=env, adapter=VllmAdapter(), target=target, policy_values=values,
        timeout=1, allow_mutating=False, keys={}, secrets=[], cprobe=probe, workdir=tmp_path / "work", started=utcnow())
    draft = executor._finish(ctx)
    evd, result = judged(tmp_path, "REQ-09", draft, env, values)
    assert "runner_input_range_exceeded:token_id" in evd.observations["confirmed_input_violations"].value
    assert result.verdicts.security.value == "FAIL" and evd.data_origin == DataOrigin.EXAMPLE


@pytest.mark.parametrize("include_min_tokens,expected", [(False, "PASS"), (True, "UNKNOWN")])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_registered_sampling_collect_requires_all_selected_native_fields(tmp_path, include_min_tokens, expected):
    env = environment(model={"id": "test"}, engine={"name": "vllm", "version": "0.31.0"})
    target = {"lab": False, "engine": {"trusted_url": "http://unused", "container": "engine"},
              "origins": {"trusted": {"network": "inside"}}}
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    options = ["temperature", "top_p"] + (["min_tokens"] if include_min_tokens else [])
    labels = ["before", *(f"{option}:{phase}" for option in options for phase in ("boundary", "concurrent", "after")), *options]
    requests = {label: sha256_hex(label.encode()) for label in labels}
    record, _ = make_runner(scope, requests, rejected=options)
    cfg = {"constraint_source": "EXAMPLE pinned fixture", "constraint_engine_version": "0.31.0", "max_output_tokens": 4,
           "max_total_requests": 1 + 5 * len(options), "max_probe_tokens": 64, "max_duration_seconds": 30,
           "runner_input": pinned(tmp_path, record, scope, requests)}
    values = {"allowed_sampling_options": options, "rejection_codes": ["400", "422"]}
    def probe(network, batch, timeout):
        out = []
        for request in batch:
            body = request.get("body") or {}
            bad = body.get("temperature", 0) < 0 or body.get("top_p", 1) == 0 or body.get("min_tokens", 0) > 4
            reply = {"outcome": 400, "body": ""} if bad else completion_response(request)
            reply["id"] = request["id"]; out.append(reply)
        return out
    executor = registry.resolve_executor("sampling_validation", "real")({"sampling_validation": cfg})
    executor.validate_policy(PACK.checks["REQ-10"], values)
    ctx = Ctx(check=PACK.checks["REQ-10"], env=env, adapter=VllmAdapter(), target=target, policy_values=values,
        timeout=1, allow_mutating=False, keys={}, secrets=[], cprobe=probe, workdir=tmp_path / "work", started=utcnow())
    draft = executor._finish(ctx)
    evd, result = judged(tmp_path, "REQ-10", draft, env, values)
    assert result.verdicts.security.value == expected
    assert evd.data_origin == DataOrigin.EXAMPLE and result.data_origin == DataOrigin.EXAMPLE
    if include_min_tokens:
        source = next(a for a in draft.artifacts if a.name == "b_common_consumption.json")
        assert "B_RUNNER_FIELD_UNSUPPORTED:min_tokens" in json.loads(source.path.read_bytes())["projection"]["gaps"]


def test_incomplete_common_source_preserves_existing_failure_and_artifact_error(tmp_path):
    env = environment(); target = {"lab": True}
    draft = EvidenceDraft(status="COMPLETED", data_origin="MEASURED", normal_control={"attempted": True, "succeeded": True},
                          observations={"reused_input_violations": {"collected": True, "value": ["independent_fail"]}})
    blocker = tmp_path / "blocked"; blocker.write_text("not a directory")
    ctx = SimpleNamespace(check=PACK.checks["REQ-08"], env=env, target=target, policy_values={}, workdir=blocker)
    attached = bridge.attach(draft, ctx, {"runner_input": {}}, kind="runner-consumption")
    assert attached.observations["reused_input_violations"].value == ["independent_fail"]
    assert attached.status.value == "ABORTED" and attached.data_origin.value == "EXAMPLE"
    assert "B_COMMON_ARTIFACT_WRITE_UNAVAILABLE" in attached.cleanup_errors


def test_sup03_real_collect_finish_keeps_projection_artifact_and_unknown(tmp_path):
    from tests.test_template_supply_approval import SupplyWorld, run as run_supply, VALUES, IMAGE
    from tests.test_template_validation import target as template_target
    from guard.adapters import vllm_supply_read_callback as callback
    world = SupplyWorld()
    target = template_target(world.context); target["comparison_config"]["launch"]["model"] = "fixture-model"
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": IMAGE}, model={"id": "fixture-model"})
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    opened = [{"schema": callback.SCHEMA, "phase": "OPEN", "root": 0, "relative": "config.json", "write": False,
               "regular": True, "size": 2, "opened_ns": 1}]
    record, _ = producers.supply_read_record([{"pid": 1, "start_ticks": 1, "events": opened}],
        [{"root": 0, "relative": "config.json", "sha256": "a" * 64, "size": 2, "state": "hashed"}], pod=POD, **scope)
    draft, _ = run_supply(tmp_path, world=world, check=PACK.checks["SUP-03"],
                          cfg={"supply_reads_input": pinned(tmp_path, record, scope)})
    evd, result = judged(tmp_path, "SUP-03", draft, env, VALUES)
    source = next(a for a in draft.artifacts if a.name == "b_common_consumption.json")
    projection = json.loads(source.path.read_bytes())["projection"]
    assert "files" in projection and projection["files"]
    assert projection["actual_consumer_verified"] is False
    assert evd.observations["template_consumer_verified"].value is False
    assert result.verdicts.security.value == "UNKNOWN"


def test_input_exception_is_preserved_without_erasing_independent_failure(tmp_path):
    env = environment(); target = {"lab": True}
    record, _ = make_runner()
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    settings = pinned(tmp_path, record, scope); settings["sha256"] = "0" * 64
    draft = EvidenceDraft(status="COMPLETED", data_origin="EXAMPLE", normal_control={"attempted": True, "succeeded": True},
                          observations={"reused_input_violations": {"collected": True, "value": ["independent_fail"]}})
    ctx = SimpleNamespace(check=PACK.checks["REQ-08"], env=env, target=target, policy_values={}, workdir=tmp_path)
    bridge.attach(draft, ctx, {"runner_input": settings}, kind="runner-consumption")
    projection = json.loads((tmp_path / "b_common_consumption.json").read_bytes())
    assert projection["record"] is None
    assert projection["projection"]["input_error"] == {"type": "ValueError", "message": "B_COMMON_INPUT_BYTES_MISMATCH"}
    assert draft.observations["reused_input_violations"].value == ["independent_fail"]
    assert draft.status.value == "ABORTED"


@pytest.mark.parametrize("damage", ["generation", "pid_reused"])
@pytest.mark.parametrize("independent", [False, True])
@pytest.mark.usefixtures("example_registered_callback_producers")
def test_req09_unbound_process_cannot_create_confirmed_fail(tmp_path, damage, independent):
    env = environment(); target = {"lab": True}
    scope = {**SCOPE, "environment_ref": env.ref().model_dump(mode="json"), "target_sha256": sha256_hex(canonical_json(target))}
    requests = {"token_id": "1" * 64}
    record, _ = make_runner(scope, requests, rejected=("token_id",), received_rejections=("token_id",), max_id=100)
    if damage == "generation":
        record["bindings"][1]["restart_count"] += 1
    else:
        record["bindings"].append({**record["bindings"][1], "start_ticks": record["bindings"][1]["start_ticks"] + 1})
        next(row for row in record["events"] if row["event"] == "sampler_applied")["binding"] = 2
    existing = ["independent_fail"] if independent else []
    draft = EvidenceDraft(status="COMPLETED", data_origin="EXAMPLE", normal_control={"attempted": True, "succeeded": True},
        observations={"challenges_planned": {"collected": True, "value": ["token_id"]},
                      "challenge_outcomes": {"collected": True, "value": {"token_id": "400"}},
                      "confirmed_input_violations": {"collected": True, "value": existing}})
    values = {"max_input_tokens": 8, "rejection_codes": ["400"]}
    ctx = SimpleNamespace(check=PACK.checks["REQ-09"], env=env, target=target, policy_values=values, workdir=tmp_path)
    bridge.attach(draft, ctx, {"vocab_size": 100, "runner_input": pinned(tmp_path, record, scope, requests)}, kind="runner-consumption")
    projection = json.loads((tmp_path / "b_common_consumption.json").read_bytes())["projection"]
    assert projection["supported"] and not projection["complete"]
    assert not projection["cases"]["token_id"]["identity_binding_verified"]
    gap = "B_RUNNER_PROCESS_GENERATION_MISMATCH" if damage == "generation" else "B_RUNNER_PROCESS_ID_REUSED"
    assert gap in projection["gaps"]
    assert draft.observations["confirmed_input_violations"].value == existing
    _, result = judged(tmp_path, "REQ-09", draft, env, values)
    assert result.verdicts.security.value == ("FAIL" if independent else "UNKNOWN")
