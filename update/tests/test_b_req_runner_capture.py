"""원 native 발송→응답ID→공식HMAC→A producer→B 준비/소비와 원 객체 경계. 전송은 EXAMPLE이다."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from guard.adapters.vllm import VllmAdapter
from guard.adapters.vllm_runner_capture import profile as capture_profile
from guard.collectors.engine_callbacks import prepare_assets, load_secret
from guard.core.select import select
from guard.executors import registry, req_runner_capture as link
from guard.models import DataOrigin, canonical_json, sha256_hex, load_pack
from guard.runtime import kubernetes_http as kube
from guard.store import Store
from guard.store.packs import put_pack
from tests.builders import environment, policy, completion_response
from tests.test_gpu_input_producers import api_rows, crow, PARAMS
from tests.test_kubernetes_sampling import World as PodWorld, inputs as target_inputs
from tests.req_response_id_source import response_id
from tools import b_req_runner_capture as driver

CAPTURE_PROFILE = VllmAdapter().runner_capture_profile("0.31.0")

from tests.published_pack38 import original as published_pack38
PACK_DIR = published_pack38().path


def baseline_pack():
    return load_pack(PACK_DIR)


class World(PodWorld):
    def __init__(self, target, prepared, *, damage=None, consume_reused=False, profile=CAPTURE_PROFILE,
                 default_temperature=0.0):
        super().__init__(target)
        self.profile = profile
        self.default_temperature = default_temperature
        self.prepared, self.damage, self.consume_reused = prepared, damage, consume_reused
        self.api, self.worker, self.sent = [], [], []
        self.serial = 0
        self.duplicate_response_ids = {}
        self.fingerprinter = profile.fingerprinter(load_secret(Path(prepared["_directory"])))
        self.expected = {(0, name): digest for name, digest in profile.sources.items()}
        self.expected.update({(1, name): digest for name, digest in prepared["assets"].items()})

    def runner(self, argv, **kwargs):
        if "exec" not in argv:
            result = super().runner(argv, **kwargs)
            value = json.loads(result["stdout_bytes"])
            if "namespaces" in argv:
                for row in value["items"]:
                    row["kind"] = "Namespace"
            else:
                value["kind"] = "Pod"
                if value["metadata"]["name"] == self.target["kubernetes"]["engine"]["pod"]:
                    container = value["spec"]["containers"][0]
                    container["env"] += [{"name": "VLLM_DISABLE_REQUEST_ID_RANDOMIZATION", "value": "0" if self.damage == "flag" else "1"},
                        {"name": "VLLM_USE_V2_MODEL_RUNNER", "value": "0"}, {"name": "PYTHONPATH", "value": self.profile.callback_root}]
                    container["volumeMounts"] = [{"name": "callbacks", "mountPath": self.profile.callback_root, "readOnly": self.damage != "mount"}]
            return {**result, "stderr_bytes": b"", "stdout_bytes": canonical_json(value)}
        if "--container=engine" in argv:
            if "input_bytes" in kwargs:
                spec = json.loads(kwargs["input_bytes"])
                rows = []
                for row in spec["files"]:
                    relative = row["relative"]
                    old_worker = self.damage == "old-worker" and relative == "v1/worker/gpu_model_runner.py"
                    missing_batch = self.damage == "missing-batch" and relative == "v1/worker/gpu_input_batch.py"
                    digest = ("f" * 64 if self.damage == "source" else
                        "bb903dffcb5a6e5b8a477daf0854b3812e135349d61fc20c6133f91cd3c9752a" if old_worker else
                        None if missing_batch else self.expected[(row["root"], relative)])
                    rows.append({**row, "sha256": digest, "size": 0 if missing_batch else 100,
                                 "state": "missing" if missing_batch else "hashed"})
                value = {"files": rows}
            else:
                value = {"processes": [{"pid": 1, "start_ticks": 11, "events": deepcopy(self.api)},
                                       {"pid": 112, "start_ticks": 22, "events": deepcopy(self.worker)}], "truncated": self.damage == "truncated" and bool(self.sent)}
            return {"returncode": 0, "stdout_bytes": canonical_json(value), "stderr_bytes": b"", "capture": {"complete": True}}
        spec = json.loads(kwargs["input_bytes"])
        request, = spec["requests"]
        with self.lock:
            self.sent.append(deepcopy(request))
            self.serial += 1
            serial = self.serial
            body = request.get("body", {})
            if request["method"] == "GET":
                reply = {"id": request["id"], "outcome": 200, "body": "{}"}
            else:
                bad = ("prompt_token_ids" in body and not self.consume_reused or body.get("prompt") == [100]
                       or type(body.get("prompt")) is list and len(body["prompt"]) > 8
                       or body.get("temperature", 0) < 0 or body.get("top_p", 1) == 0)
                if bad:
                    reply = {"id": request["id"], "outcome": 400, "body": json.dumps({"error": {"message": "EXAMPLE bounded rejection", "type": "BadRequestError", "code": 400}})}
                else:
                    chat = request["url"].endswith("/v1/chat/completions")
                    # The 0.31 fixture executes its reviewed producer; the
                    # candidate mock supplies a bounded ID of the pinned shape.
                    rid = response_id(chat) if self.profile is CAPTURE_PROFILE else (
                        ("chatcmpl-" if chat else "cmpl-") + format(serial, "016x"))
                    if self.damage == "duplicate":
                        rid = self.duplicate_response_ids.setdefault(chat, rid)
                    reply = completion_response(request)
                    payload = json.loads(reply["body"])
                    payload.update(id=rid, model="fixture-model", prompt_token_ids=body.get("prompt_token_ids", [5, 6]))
                    reply.update(id=request["id"], body=json.dumps(payload))
                    internal = rid if chat else rid + "-0"
                    fingerprint = self.fingerprinter.fingerprint(internal)
                    params = {**PARAMS,
                        "temperature_milli": round(body.get("temperature", self.default_temperature) * 1000),
                        "top_p_milli": round(body.get("top_p", 1.0) * 1000)}
                    for row in deepcopy(api_rows(fingerprint)):
                        if row["point"] == "InputProcessor._validate_params" and row["phase"] == "CALL":
                            row["fields"]["params"] = params
                        row["call_sha256"] = sha256_hex((str(serial) + row["call_sha256"]).encode())
                        row["operation_started_ns"] += serial*100
                        row["operation_finished_ns"] += serial*100
                        self.api.append(row)
                    worker_fp = self.fingerprinter.fingerprint(internal + "-deadbeef") if self.damage == "suffix" else fingerprint
                    self.worker += [crow(10000 + serial*100, "GPUModelRunner._update_states", "CALL", received=[{
                        "request_sha256": worker_fp, "prompt_tokens": 5, "max_token_id": 99, "params": params}]),
                        crow(10001 + serial*100, "GPUModelRunner._sample", "CALL", applied=[{
                            "request_sha256": worker_fp,
                            "temperature_milli": 777 if self.damage == "sampler-value" and serial == 1 else params["temperature_milli"],
                            "top_p_milli": params["top_p_milli"], "top_k": 0,
                            "logprobs": 0, "allowed_tokens": 3, "bad_words": 2}])]
            reply.update(method=request["method"], body_bytes=len(reply.get("body", "").encode()))
        return {"returncode": 0, "stdout_bytes": canonical_json([reply]), "stderr_bytes": b"", "capture": {"complete": True}}


def case(tmp_path, *, damage=None, consume_reused=False, selected=("REQ-08", "REQ-09", "REQ-10"), version="0.31.0"):
    selected_profile = capture_profile(version)
    env = environment(engine={"name": "vllm", "version": version, "image_digest": selected_profile.image},
                      model={"id": "EXAMPLE/model", "revision": "a"*40, "inventory_sha256": "b"*64},
                      runtime={"gpu_count": 1, "tp_groups": 1})
    target = target_inputs()
    target.update(schema="guard-kubernetes-request-binding-target/1", environment_ref=env.ref().model_dump(mode="json"),
        observed_version=version, model_mapping={"schema": "guard.model-request-mapping/1", "environment_ref": env.ref().model_dump(mode="json"),
            "repository_id": env.model.id, "revision": env.model.revision, "served_model_name": "fixture-model",
            "inventory_sha256": env.model.inventory_sha256, "host_snapshot": "/EXAMPLE/model", "container_mount": "/models/fixture"})
    target["engine"]["image_digest"] = selected_profile.image
    target["kubernetes"]["engine"].update(image=selected_profile.image, image_id=selected_profile.image)
    prepared_dir = tmp_path / "prepared"
    prepared = prepare_assets(prepared_dir, runner_consumption=True)
    env_path = tmp_path / "environment.json"; env_path.write_bytes(canonical_json(env.to_dict()))
    target_path = tmp_path / "target.json"; target_path.write_bytes(canonical_json(target))
    capture = {"environment": {"path": str(env_path), "file_sha256": sha256_hex(env_path.read_bytes())},
        "prepared_dir": str(prepared_dir), "prepared_sha256": sha256_hex(canonical_json(prepared)), "vllm_source_root": "/EXAMPLE/site-packages/vllm"}
    values = {"REQ-08": {"reused_token_field": "prompt_token_ids", "reused_token_allowed": False, "rejection_codes": ["400"]},
        "REQ-09": {"max_input_tokens": 8, "rejection_codes": ["400"], "allowed_endpoints": ["POST /v1/completions"], "allowed_input_formats": ["token_ids"]},
        "REQ-10": {"allowed_sampling_options": ["temperature", "top_p"], "rejection_codes": ["400", "422"]}}
    config = {"adapter": "vllm", "executors": {"input_validation": "kubernetes", "sampling_validation": "kubernetes"},
        "input_validation": {"target": str(target_path), "vocab_size": 100, "vocab_source": "EXAMPLE fixed vocabulary", "reused_token_ids": [11, 12, 13],
            "max_probe_tokens": 64, "max_duration_seconds": 120, "runner_capture": capture},
        "sampling_validation": {"target": str(target_path), "constraint_source": "EXAMPLE source", "constraint_engine_version": version,
            "max_output_tokens": 1, "max_total_requests": 11, "max_probe_tokens": 64, "max_duration_seconds": 120, "runner_capture": capture}}
    config_path = tmp_path / "config.json"; config_path.write_bytes(canonical_json(config))
    pack = baseline_pack()
    pol = policy(pack.ref(), values={cid: values[cid] for cid in selected}, scope={"include": list(selected)})
    source = Store(tmp_path / "source"); put_pack(source, PACK_DIR); source.put_many([env, pol])
    plan = select(pack, [env], pol); source.put(plan)
    world = World(target, {**prepared, "_directory": str(prepared_dir)}, damage=damage, consume_reused=consume_reused,
                  profile=selected_profile, default_temperature=1.0 if version == "0.30.0" else 0.0)
    return {"env": env, "target": target, "config": config, "path": config_path, "source": source, "plan": plan, "values": values, "world": world}


def test_same_native_requests_reach_original_pipeline_and_exact_restored_report(tmp_path, monkeypatch):
    data = case(tmp_path)
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert summary["selected"] == ["REQ-08", "REQ-09", "REQ-10"] and summary["exact_refs_restored"] and summary["saved_pack_rejudge_equal"]
    assert len(summary["evidence"]) == len(summary["results"]) == 3 and summary["report"]["kind"] == "RPT"
    store = Store.read_only(tmp_path / "run/restored")
    evidence = list(store.iter_kind("EVD"))
    assert all(e.data_origin == DataOrigin.EXAMPLE for e in evidence)
    assert all(any(a.name == "req_runner_capture.json" for a in e.artifacts) for e in evidence)
    assert all(any(a.name == "req_runner_callback_received.json" for a in e.artifacts) for e in evidence)
    proofs = [json.loads((store.root / next(a.path for a in e.artifacts if a.name == "req_runner_capture.json")).read_bytes()) for e in evidence]
    assert all(p["capture_origin"] == "EXAMPLE" and not p["native_request_mapping_verified"] for p in proofs)
    assert all(any(row["same_native_request_api_join"] for row in p["cases"]) for p in proofs)
    assert all(any(row["diagnostic"] == "B_REQ_HTTP_REJECTION_WITHOUT_ID" for row in p["cases"]) for p in proofs)
    assert all(not p["runtime_link_complete"] and not p["gpu_ready"] for p in proofs)
    sent = data["world"].sent
    assert len(sent) == 19 and all(r.get("headers", {}) == {} and "request_id" not in r.get("body", {}) for r in sent)
    assert len({r["id"] for r in sent if r["id"].startswith("sampling-")}) == 11
    for obj in evidence:
        for artifact in obj.artifacts:
            raw = (store.root / artifact.path).read_bytes()
            assert sha256_hex(raw) == artifact.sha256 and data["world"].fingerprinter.secret.hex().encode() not in raw


def test_candidate_req10_exact_runner_path_keeps_values_and_response_ids_private(tmp_path, monkeypatch):
    data = case(tmp_path, version="0.30.0", selected=("REQ-10",))
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert summary["selected"] == ["REQ-10"] and summary["exact_refs_restored"] and summary["saved_pack_rejudge_equal"]
    store = Store.read_only(tmp_path / "run/restored")
    evd, = store.iter_kind("EVD")
    assert evd.data_origin == DataOrigin.EXAMPLE
    artifact = next(a for a in evd.artifacts if a.name == "req_runner_capture.json")
    raw = (store.root / artifact.path).read_bytes()
    proof = json.loads(raw)
    assert proof["source_commit"] == capture_profile("0.30.0").source_commit
    assert proof["sampling_values_equal_in_record"] is True
    receipt = json.loads((store.root / next(a.path for a in evd.artifacts
                                    if a.name == "req_runner_prepare_receipt.json")).read_bytes())
    assert receipt["capture_sampling_match"] is True
    assert receipt["consumer_projection"] == proof["consumer_projection"]
    before = next(row for row in proof["cases"] if row["case"] == "before")
    assert before["expected_sampling"] == {} and before["sampling_values_equal_in_record"] is True
    common = json.loads((store.root / next(a.path for a in evd.artifacts
                                if a.name == "b_common_consumption.json")).read_bytes())
    before_events = [row for row in common["record"]["events"]
                     if row["request_sha256"] == before["request_sha256"]]
    assert [row["event"] for row in before_events] == ["api_validated", "runner_received", "sampler_applied"]
    assert all(row["temperature_milli"] == 1000 and row["top_p_milli"] == 1000 for row in before_events)
    assert not proof["runtime_link_complete"] and not proof["gpu_ready"]
    assert all(row["sampling_values_equal_in_record"] for row in proof["cases"]
               if row["case"] not in {"temperature", "top_p"})
    rid = "cmpl-0000000000000001"
    assert sha256_hex(rid.encode()).encode() not in raw
    assert any(row["response_id_sha256"] == data["world"].fingerprinter.fingerprint(rid)
               for row in proof["cases"])
    assert data["world"].fingerprinter.secret.hex().encode() not in raw


def test_candidate_sampler_value_mismatch_reaches_common_consumer_gap(tmp_path, monkeypatch):
    data = case(tmp_path, version="0.30.0", selected=("REQ-10",), damage="sampler-value")
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    store = Store.read_only(tmp_path / "run/restored")
    evd, = store.iter_kind("EVD")
    proof = json.loads((store.root / next(a.path for a in evd.artifacts
                                  if a.name == "req_runner_capture.json")).read_bytes())
    projection = json.loads((store.root / next(a.path for a in evd.artifacts
                                       if a.name == "b_common_consumption.json")).read_bytes())["projection"]
    receipt = json.loads((store.root / next(a.path for a in evd.artifacts
                                    if a.name == "req_runner_prepare_receipt.json")).read_bytes())
    assert proof["sampling_values_equal_in_record"] is False
    assert receipt["capture_sampling_match"] is False
    assert receipt["consumer_projection"] == proof["consumer_projection"]
    assert set(receipt["consumer_projection"]["gaps"]) <= set(projection["gaps"])
    assert "B_RUNNER_SAMPLING_VALUES_UNVERIFIED" in proof["consumer_projection"]["gaps"]
    assert "B_RUNNER_SAMPLING_VALUES_UNVERIFIED" in projection["gaps"]
    assert receipt["consumer_projection"]["complete"] is False and projection["complete"] is False
    assert evd.observations["runner_consumption_verified"].value is False
    assert evd.execution.status.value == "ABORTED" and summary["exact_refs_restored"]


@pytest.mark.parametrize("damage", ["flag", "source", "mount", "old-worker", "missing-batch"])
def test_candidate_preflight_failure_dispatches_no_req10_request(tmp_path, monkeypatch, damage):
    data = case(tmp_path, version="0.30.0", selected=("REQ-10",), damage=damage)
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert data["world"].sent == []
    assert summary["evidence"][0]["status"] == "ABORTED" and summary["exact_refs_restored"]


@pytest.mark.parametrize("damage", ["flag", "source", "mount"])
def test_runtime_preflight_gate_fails_before_any_native_dispatch(tmp_path, monkeypatch, damage):
    data = case(tmp_path, damage=damage, selected=("REQ-08",))
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert data["world"].sent == []
    assert summary["evidence"][0]["status"] == "ABORTED" and summary["exact_refs_restored"]


@pytest.mark.parametrize("damage", ["suffix", "duplicate", "truncated"])
def test_no_suffix_search_or_duplicate_mapping_can_complete_the_consumer(tmp_path, monkeypatch, damage):
    data = case(tmp_path, damage=damage, selected=("REQ-08",))
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert summary["evidence"][0]["status"] == "ABORTED" and not summary["gpu_ready"]
    store = Store.read_only(tmp_path / "run/restored")
    evd, = store.iter_kind("EVD")
    proof = json.loads((store.root / next(a.path for a in evd.artifacts if a.name == "req_runner_capture.json")).read_bytes())
    assert not proof["runtime_link_complete"]
    if damage == "suffix":
        assert "GPU_INPUT_SOURCE_INCOMPLETE" in proof["consumer_projection"]["gaps"]


def test_later_capture_gap_keeps_existing_native_independent_fail(tmp_path, monkeypatch):
    data = case(tmp_path, damage="truncated", consume_reused=True, selected=("REQ-08",))
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert summary["evidence"][0]["status"] == "ABORTED" and summary["results"][0]["security"] == "FAIL"
    assert summary["exact_refs_restored"]


@pytest.mark.parametrize("cid,requests", [("REQ-08", 3), ("REQ-09", 5), ("REQ-10", 11)])
def test_disabled_capture_preserves_original_dispatches_and_no_new_artifact(tmp_path, cid, requests):
    data = case(tmp_path, selected=(cid,))
    name = "sampling_validation" if cid == "REQ-10" else "input_validation"
    cfg = deepcopy(data["config"][name]); cfg.pop("runner_capture")
    executor = registry.resolve_executor(name, "kubernetes")({name: cfg})
    executor._kubectl_runner = data["world"].runner
    draft = executor.run(baseline_pack().checks[cid], data["env"], data["values"][cid], VllmAdapter(), tmp_path / "off")
    assert len(data["world"].sent) == requests and not any(a.name.startswith("req_runner") for a in draft.artifacts)
    assert not any(r.get("headers") for r in data["world"].sent)


def test_prepare_mode_does_not_call_transport_and_pins_the_original_plan(tmp_path, monkeypatch):
    data = case(tmp_path)
    monkeypatch.setattr(kube, "capture_pipes", lambda *a, **k: pytest.fail("no native I/O in preparation"))
    summary = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "prepared-driver")
    assert summary["plan"] == data["plan"].ref().model_dump(mode="json") and not summary["collect_invoked"]
    assert not summary["gpu_ready"] and data["world"].sent == []


@pytest.mark.parametrize("damage", ["n", "path"])
def test_native_bounds_do_not_admit_other_choice_counts_or_endpoint(damage):
    request = {"method": "POST", "url": "http://unused/v1/chat/completions", "headers": {},
        "body": {"model": "fixture-model", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1}}
    if damage == "n":
        request["body"]["n"] = 2
    else:
        request["url"] = "http://unused/v1/completions"
    with pytest.raises(ValueError, match="OUTSIDE_SELECTED_SCOPE"):
        CAPTURE_PROFILE.validate_request(request, "REQ-08")


@pytest.mark.parametrize("late", ["source", "generation", "source-cleanup"])
def test_received_callback_survives_late_failure_and_exact_export(tmp_path, monkeypatch, late):
    data = case(tmp_path, consume_reused=True, selected=("REQ-08",))
    monkeypatch.setattr(kube, "capture_pipes", data["world"].runner)
    original_snapshot = link.Capture._snapshot

    def snapshot(capture):
        value = original_snapshot(capture)
        if hasattr(capture, "before_events"):
            if late == "generation":
                return {**value, "restart_count": value["restart_count"] + 1}
            raise ValueError("B_REQ_RUNTIME_SOURCE_OR_ASSET_MISMATCH")
        return value

    monkeypatch.setattr(link.Capture, "_snapshot", snapshot)
    if late == "source-cleanup":
        import os
        unlink = os.unlink
        def failing_staging_cleanup(path, *args, **kwargs):
            if Path(path).name.startswith("callback-received-") and str(path).endswith(".tmp"):
                raise OSError("EXAMPLE staging cleanup failure")
            return unlink(path, *args, **kwargs)
        monkeypatch.setattr(os, "unlink", failing_staging_cleanup)
    result = driver.run(data["source"].root, data["plan"].id, data["path"], tmp_path / "run", collect=True)
    assert result["exact_refs_restored"] and result["saved_pack_rejudge_equal"]
    assert result["results"][0]["security"] == "FAIL" and result["evidence"][0]["status"] == "ABORTED"
    store = Store.read_only(tmp_path / "run/restored")
    evd, = store.iter_kind("EVD")
    artifact = next(a for a in evd.artifacts if a.name == "req_runner_callback_received.json")
    raw = (store.root / artifact.path).read_bytes()
    value = json.loads(raw)
    assert sha256_hex(raw) == artifact.sha256 and raw == canonical_json(value)
    assert value["processes"][0]["events"] == data["world"].api
    assert value["processes"][1]["events"] == data["world"].worker
    proof = json.loads((store.root / next(a.path for a in evd.artifacts if a.name == "req_runner_capture.json")).read_bytes())
    expected = "B_REQ_RUNTIME_GENERATION_OR_SOURCE_CHANGED" if late == "generation" else "B_REQ_RUNTIME_SOURCE_OR_ASSET_MISMATCH"
    assert proof["error"] == {"type": "ValueError", "code": expected}
    assert proof["callback_payload"]["preserved"] and not proof["callback_payload"]["authenticated"]
    assert not proof["native_request_mapping_verified"] and not proof["runtime_link_complete"] and not proof["gpu_ready"]
    if late == "source-cleanup":
        assert "B_REQ_CALLBACK_STAGING_CLEANUP_UNAVAILABLE" in evd.execution.cleanup_errors
