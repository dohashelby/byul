"""Admission context remains attached to its request during concurrent reloads."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import threading

from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.request_records import RequestRecords
from serving_runtime.mvp.validator import Decision
from serving_runtime.mvp.backend import BackendConfig

POLICY = Path(__file__).resolve().parents[1] / "examples/mvp/policy.json"
DECISION = Decision("ALLOWED", 200, "ALLOWED", None, "ok", "test-model", 1)


def record(records, request_id="a"):
    return records.build("final", request_id, DECISION, attempted=True,
                         backend_receipt="not_available", response="completed", error_code=None)


def test_final_keeps_admitted_policy_target_and_bundle(tmp_path):
    before = load_policy(POLICY)
    live = [before]
    target = {"instance_id": "generation-one", "container_id": "container-one"}
    bundle = {"manifest_sha256": "b" * 64}
    records = RequestRecords(tmp_path, "session", lambda: live[0], "vllm",
                             target_context=lambda: target, policy_bundle=bundle)
    records.clear_context("/v1/completions")
    records.bind_context(before)
    target["instance_id"] = "generation-two"
    bundle["manifest_sha256"] = "c" * 64
    live[0] = replace(before, sha256="changed", policy=replace(before.policy, policy_version="next"))
    final = record(records)
    assert final["policy"]["sha256"] == before.sha256
    assert final["target"]["instance_id"] == "generation-one"
    assert final["policy_bundle"]["manifest_sha256"] == "b" * 64
    final["target"]["instance_id"] = "mutated-output"
    assert record(records)["target"]["instance_id"] == "generation-one"
    records.clear_context("/v1/chat/completions")
    records.bind_context(live[0])
    assert record(records)["target"]["instance_id"] == "generation-two"
    assert record(records)["policy"]["sha256"] == "changed"


def test_simultaneous_requests_keep_distinct_policy_snapshots(tmp_path):
    policy = load_policy(POLICY)
    barrier = threading.Barrier(2)
    records = RequestRecords(tmp_path, "session", lambda: policy, "vllm")
    def execute(number):
        records.clear_context("/v1/completions")
        records.bind_context(replace(policy, sha256=str(number)))
        barrier.wait(timeout=3)
        return record(records, str(number))
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(execute, range(2)))
    assert {(r["request_id"], r["policy"]["sha256"]) for r in rows} == {("0", "0"), ("1", "1")}


def test_vllm_native_callback_alias_matches_record_and_keeps_public_uuid(tmp_path):
    from dataclasses import replace
    backend = replace(BackendConfig.mock(('127.0.0.1', 1)), kind='vllm')
    policy = load_policy(POLICY)
    records = RequestRecords(tmp_path, 'session', lambda: policy, 'vllm')
    rid = 'a' * 32
    final = record(records, rid)
    assert final['request_id'] == rid
    assert final['upstream_request_id'] == backend.request_headers(request_id=rid)['X-Request-ID'] == 'byul-cb-' + rid
    assert BackendConfig.mock(('127.0.0.1', 1)).request_headers(request_id=rid)['X-Request-ID'] == rid
    assert replace(backend, kind='sglang').request_headers(request_id=rid)['X-Request-ID'] == rid
