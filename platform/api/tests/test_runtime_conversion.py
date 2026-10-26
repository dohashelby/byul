"""runtime 기록 → 계약 변환이 contracts README의 표와 같은지."""

from __future__ import annotations

import copy

import pytest

from byul_platform.sources.runtime import (
    gateway_event,
    gateway_outcome,
    grade,
    observation_event,
    pick_gateway_record,
    request_row,
    revalidation_request,
    runtime_status,
)
from conftest import FIXTURES, example_events, load_json


def _record(result: str | None, attempted: bool | None = None, phase: str = "final") -> dict:
    return {
        "schema_version": "runtime-mvp-record/1.0",
        "phase": phase,
        "request_id": "ab" * 16,
        "observation_session_id": "s1",
        "target_id": "mock-engine-001",
        "recorded_at": "2026-10-25T01:00:00+00:00",
        "decision": {"result": result, "reason_code": None, "rule_id": None, "http_status": 200, "source": "gateway", "engine_http_status": None},
        "delivery": {"attempted": attempted, "backend_receipt": None, "response": None, "error_code": None},
        "data_origin": "real",
    }


# contracts README "runtime Gateway 판정 → outcome" 표
@pytest.mark.parametrize(
    ("result", "attempted", "phase", "outcome"),
    [
        ("ALLOWED", True, "final", "allowed"),
        ("DENIED", False, "final", "denied"),
        ("DENIED", True, "final", "error"),
        ("INVALID", False, "final", "invalid"),
        ("ENGINE_REJECTED", True, "final", "engine_rejected"),
        ("ERROR", True, "final", "error"),
        ("ALLOWED", False, "started", "unknown"),
    ],
)
def test_gateway_outcome_table(contracts, result, attempted, phase, outcome):
    record = _record(result, attempted, phase)
    assert gateway_outcome(record) == outcome
    event = gateway_event(record)
    assert event["outcome"] == outcome
    assert event["enforcer"] == "gateway"
    assert event["native"]["record"] is record
    assert contracts.errors("runtime-event", event) == []


def test_denied_with_unknown_delivery_is_not_confirmed_denial():
    # attempted가 false로 확인되지 않으면 거부 확인이 아니다.
    assert gateway_outcome(_record("DENIED", None)) == "error"


def test_final_record_wins_over_started():
    started, final = _record("ALLOWED", False, "started"), _record("DENIED", False, "final")
    assert pick_gateway_record([started, final]) is final
    assert pick_gateway_record([started]) is started
    assert pick_gateway_record([]) is None


def test_contract_example_events_round_trip():
    """예시 사건의 native 원본을 다시 변환하면 outcome·enforcer·event_id가 같다."""
    for example in example_events():
        record = example["native"]["record"]
        if example["source"] == "gateway":
            converted = gateway_event(record)
        else:
            converted = observation_event(record)
            assert converted["event_id"] == example["event_id"]
        for key in ("outcome", "enforcer", "source", "category", "data_origin", "origin_detail", "rule_ref", "phase"):
            assert converted[key] == example[key], (example["event_id"], key)


@pytest.mark.parametrize("verdict", ["DROPPED", "dropped", "FORWARDED", None])
def test_hubble_is_never_denied(contracts, verdict):
    record = {"schema_version": "runtime-observation/1.0", "observed_at": "2026-10-25T01:03:05+00:00", "source": "hubble",
              "kind": "flow", "data_origin": "real", "verdict": verdict, "drop_reason": "POLICY_DENIED"}
    event = observation_event(record)
    assert event["outcome"] == "observed"
    assert event["enforcer"] is None
    assert contracts.errors("runtime-event", event) == []
    # 스키마도 Hubble 거부 확인을 막는다.
    assert contracts.errors("runtime-event", {**event, "outcome": "denied", "enforcer": "cilium"})


@pytest.mark.parametrize(
    ("exec_verdict", "action_mode", "outcome"),
    [
        ("allowed_in_phase", "monitor", "observed"),
        ("exception", "monitor", "observed"),
        ("outside_allowlist", "monitor", "detected"),
        ("suspect_binary", "monitor", "detected"),
        ("exec_from_cache", "monitor", "detected"),
        ("suspect_binary", "enforce", "enforce_attempted"),
        ("phase_unknown", "monitor", "unknown"),
        ("phase_signal_unverified", "monitor", "unknown"),
        (None, "monitor", "unknown"),
    ],
)
def test_tetragon_outcome_table(contracts, exec_verdict, action_mode, outcome):
    record = {"schema_version": "runtime-observation/1.0", "observed_at": "2026-10-25T01:03:00+00:00", "source": "tetragon",
              "kind": "process_exec", "data_origin": "synthetic", "action_mode": action_mode, "exec_verdict": exec_verdict}
    event = observation_event(record)
    assert event["outcome"] == outcome
    assert event["enforcer"] == "tetragon"
    assert contracts.errors("runtime-event", event) == []


@pytest.mark.parametrize(
    ("rule_id", "action_mode", "cache_generation", "outcome"),
    [
        ("TG-ASSET-01", "monitor", None, "detected"),
        ("TG-ASSET-02", "monitor", None, "detected"),
        ("TG-SECRET-01", "monitor", None, "detected"),
        ("TG-CACHE-01", "monitor", "current", "observed"),
        ("TG-CACHE-01", "monitor", "other", "detected"),
        ("TG-CACHE-01", "monitor", "outside", "detected"),
        ("TG-CACHE-01", "monitor", None, "unknown"),
        ("TG-FUTURE-01", "monitor", None, "unknown"),
        ("TG-ASSET-01", "unknown", None, "unknown"),
        ("TG-ASSET-01", "enforce", None, "enforce_attempted"),
    ],
)
def test_tetragon_file_access_outcome_table(
    contracts, rule_id, action_mode, cache_generation, outcome
):
    record = {
        "schema_version": "runtime-observation/1.0",
        "observed_at": "2026-10-25T01:03:00+00:00",
        "source": "tetragon",
        "kind": "file_access",
        "data_origin": "real",
        "action_mode": action_mode,
        "rule_id": rule_id,
        "cache_generation": cache_generation,
    }
    event = observation_event(record)
    assert event["outcome"] == outcome
    assert event["enforcer"] == "tetragon"
    assert contracts.errors("runtime-event", event) == []


def test_feed_event_key_becomes_event_id():
    record = {"source": "hubble", "kind": "flow", "observed_at": "2026-10-25T01:00:00Z", "data_origin": "real", "event_key": "c" * 64}
    assert observation_event(record)["event_id"] == "feed:" + "c" * 64


def test_data_origin_grades():
    for raw in ("real", "external_backend", "kubernetes_api", "local_file"):
        assert grade(raw) == "MEASURED"
    assert grade("declared") == "DECLARED"
    for raw in ("mock", "synthetic", "actual_test", "fake_backend_test", None, "something-new"):
        assert grade(raw) == "EXAMPLE"


def test_request_row_keeps_null_not_zero():
    record = _record("INVALID", False)
    record["request"] = {"model": None, "max_tokens": None}
    record["decision"]["http_status"] = None
    row = request_row(gateway_event(record))
    assert row["max_tokens"] is None
    assert row["model"] is None
    assert row["http_status"] is None
    assert row["stream"] is None
    # bool은 정수로 바꾸지 않는다.
    record["request"]["max_tokens"] = True
    assert request_row(gateway_event(record))["max_tokens"] is None


def test_captured_runtime_details_convert_to_valid_events(contracts):
    from byul_platform.sources.runtime import request_detail_doc

    capture = load_json(FIXTURES / "runtime" / "capture.json")
    seen = set()
    for detail in capture["details"].values():
        event = gateway_event(pick_gateway_record(detail["gateway_records"]))
        assert contracts.errors("runtime-event", event) == []
        assert event["data_origin"] == "EXAMPLE"  # actual_test(모의 엔진)는 EXAMPLE로 낮춘다
        seen.add(event["outcome"])
        doc = request_detail_doc(detail)
        assert contracts.errors("runtime-request-detail", doc) == []
        assert doc["outcome"] == event["outcome"]
    assert seen == {"allowed", "denied", "invalid"}


def test_captured_real_observation_shapes(contracts):
    """runtime enforce ingest가 만든 실제 정규화 기록(합성 입력)을 계약으로 바꾼다."""
    capture = load_json(FIXTURES / "runtime" / "capture.json")
    records = capture["endpoints"]["observations"]["observations"]
    for record in records:
        event = observation_event(record)
        assert contracts.errors("runtime-event", event) == [], record
        obs = event["observation"]
        if record["source"] == "hubble":
            assert event["outcome"] == "observed" and obs["dst_port"] == record["dst"]["port"]
            assert obs["verdict"] == record["verdict"]
        else:
            assert obs["binary"] == record.get("binary")
            assert obs["phases"] == (record["phase"] if isinstance(record.get("phase"), list) else [])
    assert any(r["source"] == "tetragon" and r["kind"] == "file_access" for r in records)


def test_runtime_status_preserves_unknown_asset_and_policy_counts(contracts):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    endpoints = capture["endpoints"]
    endpoints["status"]["policy"]["passthrough_count"] = None
    endpoints["status"]["policy"]["validate_count"] = None
    asset_collector = next(
        item
        for item in endpoints["collectors"]["collectors"]
        if item["source"] == "asset_integrity"
    )
    asset_collector.update(
        {
            "state": "ok",
            "last_success_at": "2026-10-25T01:04:58Z",
            "last_attempt_at": "2026-10-25T01:04:58Z",
            "last_error": None,
            "interval_s": 2.0,
            "stale_after_s": 30.0,
            "data_origin": "local_file",
            "last_value": {
                "intact": False,
                "total": 2,
                "counts": {
                    "match": 0,
                    "changed": 0,
                    "missing": 2,
                    "unreadable": 0,
                    "symlink": 0,
                },
                "extra_file_count": None,
                "extra_symlink_count": None,
                "walk_error_count": None,
                "limit": "asset root was not walked",
            },
        }
    )
    doc = runtime_status(
        endpoints["status"],
        endpoints["policy"],
        endpoints["collectors"],
        target=endpoints["target"],
        summary=endpoints["summary"],
        revalidation=endpoints["revalidation"],
        observations=endpoints["observations"],
        baseline=endpoints["baseline"],
    )
    asset = next(c["detail"] for c in doc["collectors"] if c["source"] == "asset_integrity")
    assert doc["policy"]["validate_count"] is None
    assert asset["extra_files"] is None
    assert asset["extra_symlinks"] is None
    assert asset["walk_errors"] is None
    assert contracts.errors("runtime-status", doc) == []

    other_counter_null = copy.deepcopy(doc)
    next(
        c["detail"]
        for c in other_counter_null["collectors"]
        if c["source"] == "asset_integrity"
    )["match"] = None
    assert contracts.errors("runtime-status", other_counter_null)


def test_tetragon_phase_list_and_action_fallback():
    record = {"source": "tetragon", "kind": "process_exec", "observed_at": "2026-10-25T01:00:00Z", "data_origin": "real",
              "phase": ["loading", "compiling"], "exec_verdict": "suspect_binary", "action_raw": "KPROBE_ACTION_OVERRIDE"}
    event = observation_event(record)
    assert event["phase"] is None and event["observation"]["phases"] == ["loading", "compiling"]
    assert event["outcome"] == "enforce_attempted" and event["observation"]["action_mode"] == "enforce"
    record.update(phase=["inferring"], action_raw="KPROBE_ACTION_POST")
    event = observation_event(record)
    assert event["phase"] == "inferring" and event["outcome"] == "detected"


def test_revalidation_conversion(contracts):
    item = {
        "revalidation_id": "reval-1", "status": "open", "trigger": "config_drift", "fingerprint": "a" * 64,
        "opened_at": "2026-10-25T01:00:00+00:00", "updated_at": "2026-10-25T02:00:00+00:00",
        "target_id": "mock-engine-001", "policy": {"policy_id": "p", "policy_version": "1", "sha256": "b" * 64},
        "data_origin": "mock", "last_event": "opened", "superseded_by": None,
        "evidence": {"field": "engine.version", "expected": "0.31.0", "observed": "0.31.1", "source": "engine_api"},
    }
    item.update(current_evidence_present=True)
    doc = revalidation_request(item)
    assert contracts.errors("revalidation-request", doc) == []
    assert doc["opened_at"] == "2026-10-25T01:00:00+00:00" and doc["current_evidence_present"] is True
    assert doc["changed_fields"] == [{"field": "engine.version", "expected": "0.31.0", "observed": "0.31.1"}]
    assert doc["created_at"] == "2026-10-25T02:00:00+00:00"

    asset = copy.deepcopy(item)
    asset.update(trigger="asset_changed", evidence={"paths": {"changed": ["model/a.bin"], "missing": []}, "manifest_sha256": "d" * 64})
    doc = revalidation_request(asset)
    assert contracts.errors("revalidation-request", doc) == []
    assert {"field": "asset:model/a.bin", "expected": None, "observed": "changed"} in doc["changed_fields"]
