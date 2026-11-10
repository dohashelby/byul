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
    request_detail_doc,
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


def test_gateway_pod_suspect_exec_is_displayed_as_detected(contracts):
    record = {"schema_version": "runtime-observation/1.0", "observed_at": "2026-11-02T10:00:00Z",
              "source": "tetragon", "kind": "process_exec", "data_origin": "real",
              "action_mode": "monitor", "exec_verdict": "suspect_binary", "binary": "/bin/sh",
              "phase": ["unknown"], "pod": {"namespace": "serving-lab", "pod": "serving-gateway-1"}}
    event = observation_event(record)
    assert event["outcome"] == "detected"
    assert event["observation"]["pod_name"] == "serving-gateway-1"
    assert event["observation"]["exec_verdict"] == "suspect_binary"
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


def test_runtime_status_preserves_unverified_target_without_hold(contracts):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    status = capture["endpoints"]["status"]
    status["status"] = "degraded"
    status["target"]["verification"] = {
        "state": "unavailable", "last_confirmed_at": "2026-10-26T00:00:00+00:00"
    }
    endpoints = capture["endpoints"]
    doc = runtime_status(status, endpoints["policy"], endpoints["collectors"],
        target=endpoints["target"], summary=endpoints["summary"],
        revalidation=endpoints["revalidation"], observations=endpoints["observations"],
        baseline=endpoints["baseline"])
    assert doc["overall"] == "degraded"
    assert doc["target"]["gateway_hold"] is None
    assert doc["target"]["verification"] == status["target"]["verification"]
    assert contracts.errors("runtime-status", doc) == []


_MISSING = object()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"mode": "off", "ready": True, "unavailable_reason_code": None}, {"mode": "off", "ready": True, "unavailable_reason_code": None}),
        ({"mode": "per_principal", "ready": True, "unavailable_reason_code": None}, {"mode": "per_principal", "ready": True, "unavailable_reason_code": None}),
        (
            {"mode": "per_principal", "ready": False, "unavailable_reason_code": "CACHE_ISOLATION_KEY_MISSING"},
            {"mode": "per_principal", "ready": False, "unavailable_reason_code": "CACHE_ISOLATION_KEY_MISSING"},
        ),
        (_MISSING, {"mode": None, "ready": False, "unavailable_reason_code": None}),
        ({"mode": "per_principal", "ready": "yes", "unavailable_reason_code": None}, {"mode": None, "ready": False, "unavailable_reason_code": None}),
    ],
)
def test_runtime_status_cache_isolation_is_explicit_or_unknown(contracts, raw, expected):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    endpoints = capture["endpoints"]
    if raw is not _MISSING:
        endpoints["status"]["policy"]["cache_isolation"] = raw
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
    assert doc["policy"]["cache_isolation"] == expected
    assert contracts.errors("runtime-status", doc) == []


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"mode": "off", "applied": False}, {"mode": "off", "applied": False}),
        ({"mode": "per_principal", "applied": True}, {"mode": "per_principal", "applied": True}),
        (_MISSING, {"mode": None, "applied": None}),
        ({"mode": "per_principal", "applied": "yes"}, {"mode": None, "applied": None}),
    ],
)
def test_request_detail_cache_isolation_is_explicit_or_unknown(contracts, raw, expected):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    detail = next(iter(capture["details"].values()))
    final = pick_gateway_record(detail["gateway_records"])
    assert final is not None
    if raw is not _MISSING:
        final["request"]["cache_isolation"] = raw
    doc = request_detail_doc(detail)
    assert doc["request"]["cache_isolation"] == expected
    assert contracts.errors("runtime-request-detail", doc) == []


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


def test_hubble_loss_status_passes_through_and_validates(contracts):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    endpoints = capture["endpoints"]
    hubble = {
        "source": "observation_feed_hubble",
        "state": "partial",
        "last_success_at": "2026-10-25T01:04:58Z",
        "last_attempt_at": "2026-10-25T01:04:58Z",
        "last_error": None,
        "interval_s": 2.0,
        "stale_after_s": 30.0,
        "data_origin": "real",
        "last_value": {
            "processed": 1,
            "accepted": 0,
            "duplicates": 0,
            "rejected": 0,
            "rejected_details": [],
            "corrupt_lines_total": 0,
            "skipped_unsupported_total": 0,
            "out_of_window": 0,
            "out_of_window_total": 0,
            "idle": False,
            "writer": {
                "configured": True,
                "state": "running",
                "updated_at": "2026-10-25T01:04:58Z",
                "reconnect_count": 0,
                "last_gap": None,
            },
            "lost_events_total": 9,
            "lost_events_by_source": {
                "HUBBLE_RING_BUFFER": 7,
                "OBSERVER_EVENTS_QUEUE": 2,
            },
            "last_lost_at": "2026-10-25T01:04:58Z",
            "loss_detected": True,
        },
    }
    endpoints["collectors"]["collectors"].append(hubble)
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
    converted = next(
        item for item in doc["collectors"] if item["source"] == "observation_feed_hubble"
    )
    assert converted["state"] == "partial"
    assert converted["detail"]["lost_events_total"] == 9
    assert converted["detail"]["lost_events_by_source"]["HUBBLE_RING_BUFFER"] == 7
    assert converted["detail"]["loss_detected"] is True
    assert contracts.errors("runtime-status", doc) == []


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


@pytest.mark.parametrize("error_code,result", [
    (None, "not_revoked"),
    ("REVOCATIONS_NOT_FOUND", "REVOCATIONS_NOT_FOUND"),
    ("REVOCATIONS_READ_ERROR", "REVOCATIONS_READ_ERROR"),
])
def test_revocations_empty_missing_and_read_error_reach_dashboard(contracts, error_code, result):
    capture = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))
    endpoints = capture["endpoints"]
    endpoints["policy"]["validity"] = [{"item": "revocation", "state": "unknown" if error_code else "ok",
                                        "result": "unknown" if error_code else "not_revoked", "error_code": error_code}]
    doc = runtime_status(endpoints["status"], endpoints["policy"], endpoints["collectors"])
    assert doc["policy"]["validity_items"][0]["result"] == result
    assert contracts.errors("runtime-status", doc) == []


def test_missing_baseline_and_unreadable_manifest_have_visible_notes():
    from byul_platform.sources.runtime import comparisons
    result = comparisons({"baseline": {"state": "not_configured", "error_code": "BASELINE_NOT_FOUND"},
                          "manifest": {"state": "error", "error_code": "MANIFEST_READ_ERROR"}}, {})
    assert result["baseline"]["state"] == "not_configured"
    assert any("미설정/미승인" in note for note in result["baseline"]["notes"])
    assert any("읽기/검증 오류" in note for note in result["baseline"]["notes"])


def _feed_status_document(collector):
    endpoints = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json"))["endpoints"]
    endpoints["collectors"]["collectors"].append(collector)
    return runtime_status(
        endpoints["status"], endpoints["policy"], endpoints["collectors"],
        target=endpoints["target"], summary=endpoints["summary"],
        revalidation=endpoints["revalidation"], observations=endpoints["observations"],
        baseline=endpoints["baseline"],
    )


def _tetragon_feed_collector():
    return {
        "source": "observation_feed_tetragon", "state": "partial",
        "data_origin": "real", "last_value": {
            "dropped_lines_total": 3,
            "last_redaction_drop_reason": "INVALID_JSON",
            "last_redaction_error_class": "OSError",
            "last_redaction_drop_at": "2026-11-06T00:00:00Z",
            "redaction_loss_detected": True,
            "writer": {"configured": True, "state": "running", "reconnect_count": 2,
                       "last_gap": {"gap_id": "gap-fixture", "opened_at": "2026-11-06T00:00:00Z",
                                    "closed_at": "2026-11-06T00:00:02Z", "backfill": "requested_unverified", "attempts": 2}},
        },
    }


def test_tetragon_drop_and_backfill_survive_status_contract(contracts):
    collector = _tetragon_feed_collector()
    doc = _feed_status_document(collector)
    converted = next(c for c in doc["collectors"] if c["source"] == collector["source"])
    assert converted["state"] == "partial"
    assert converted["data_origin"] == "MEASURED"
    assert converted["origin_detail"] == "real"
    detail = converted["detail"]
    for key in ("dropped_lines_total", "last_redaction_drop_reason", "last_redaction_error_class", "last_redaction_drop_at", "redaction_loss_detected"):
        assert detail[key] == collector["last_value"][key]
    assert detail["writer"]["last_gap"] == collector["last_value"]["writer"]["last_gap"]
    assert contracts.errors("runtime-status", doc) == []


@pytest.mark.parametrize("source", ["observation_feed_tetragon", "observation_feed_hubble"])
def test_missing_feed_loss_metadata_stays_unknown(contracts, source):
    doc = _feed_status_document({"source": source, "state": "ok", "last_value": {"writer": {"last_gap": {}}}})
    detail = next(c["detail"] for c in doc["collectors"] if c["source"] == source)
    for key in ("dropped_lines_total", "last_redaction_drop_reason", "last_redaction_error_class", "last_redaction_drop_at", "redaction_loss_detected"):
        assert detail[key] is None
    assert detail["writer"]["last_gap"] == dict.fromkeys(("gap_id", "opened_at", "closed_at", "backfill", "attempts"))
    assert contracts.errors("runtime-status", doc) == []


def test_zero_and_false_loss_metadata_are_preserved(contracts):
    collector = _tetragon_feed_collector()
    collector["data_origin"] = "synthetic"
    collector["state"] = "ok"
    collector["last_value"].update(dropped_lines_total=0, redaction_loss_detected=False,
                                  last_redaction_error_class=None, last_redaction_drop_at=None)
    collector["last_value"]["writer"]["last_gap"]["attempts"] = 0
    doc = _feed_status_document(collector)
    converted = next(c for c in doc["collectors"] if c["source"] == collector["source"])
    assert converted["data_origin"] == "EXAMPLE" and converted["origin_detail"] == "synthetic"
    assert converted["detail"]["dropped_lines_total"] == 0
    assert converted["detail"]["redaction_loss_detected"] is False
    assert converted["detail"]["writer"]["last_gap"]["attempts"] == 0
    assert contracts.errors("runtime-status", doc) == []


def test_feed_contract_still_accepts_older_documents(contracts):
    doc = _feed_status_document(_tetragon_feed_collector())
    detail = next(c["detail"] for c in doc["collectors"] if c["source"] == "observation_feed_tetragon")
    for key in ("dropped_lines_total", "last_redaction_drop_reason", "last_redaction_error_class", "last_redaction_drop_at", "redaction_loss_detected"):
        detail.pop(key)
    for key in ("gap_id", "backfill", "attempts"):
        detail["writer"]["last_gap"].pop(key)
    assert contracts.errors("runtime-status", doc) == []


def test_feed_failure_metadata_and_open_gap_survive_contract(contracts):
    collector = _tetragon_feed_collector()
    collector["state"] = "error"
    value = collector["last_value"]
    value.update(storage_errors_total=2, last_storage_error_class="OSError",
                 last_storage_error_at="2026-11-06T00:00:03Z", last_storage_operation="checkpoint",
                 checkpoint_pending=True)
    value["writer"].update(state="reconnecting", open_gap={"gap_id": "open1", "opened_at": "2026-11-06T00:00:00Z",
                                                          "closed_at": None, "backfill": None, "attempts": 2})
    doc = _feed_status_document(collector)
    converted = next(c for c in doc["collectors"] if c["source"] == collector["source"])
    assert converted["state"] == "error"
    detail = converted["detail"]
    assert detail["writer"]["state"] == "reconnecting"
    assert detail["writer"]["open_gap"] == value["writer"]["open_gap"]
    for key in ("storage_errors_total", "last_storage_error_class", "last_storage_error_at", "last_storage_operation", "checkpoint_pending"):
        assert detail[key] == value[key]
    assert contracts.errors("runtime-status", doc) == []


@pytest.mark.parametrize("case_id", ["L-05b", "L-05c"])
def test_optional_lab_probes_survive_platform_contract(contracts, case_id):
    from byul_platform.sources.runtime import lab_report_doc
    payload = dict(state="ok", generated_at="2026-11-06T00:00:00Z", report=dict(
        run_id="probe-run", context="kind-runtime-lab", namespace="serving-lab", started_at=None,
        finished_at=None, overall="partial", cases=[dict(id=case_id, title=case_id, status="unknown",
        summary="fixture", evidence=["probe.json"], limitation="test Pod scope", note=None)]))
    doc = lab_report_doc(payload)
    assert contracts.errors("runtime-lab-report", doc) == []
    assert doc["report"]["cases"][0]["id"] == case_id
    assert doc["report"]["cases"][0]["status"] == "unknown"
    assert doc["report"]["cases"][0]["limitation"] == "test Pod scope"


def test_runtime_event_check_and_evidence_links_stay_with_source(contracts):
    record = copy.deepcopy(load_json(FIXTURES / "runtime" / "capture.json")["details"])
    detail = next(iter(record.values()))
    raw = next(r for r in detail["gateway_records"] if r["phase"] == "final")
    raw["decision"]["rule_id"] = "authentication"
    raw["evidence_refs"] = [{"evidence_id": "ev1", "sha256": "a" * 64}, {"evidence_id": "invalid", "sha256": "oops"}]
    event = gateway_event(raw)
    assert "REQ-04" in event["related_checks"] and "OBS-01" in event["related_checks"]
    assert event["evidence_refs"] == [{"evidence_id": "ev1", "sha256": "a" * 64}]
    assert contracts.errors("runtime-event", event) == []


@pytest.mark.parametrize("source_schema", ["serving-runtime-k8s-lab-report/1.0", "serving-runtime-aws-lab-report/1.0"])
def test_lab_report_conversion_preserves_source_schema(contracts, source_schema):
    from byul_platform.sources.runtime import lab_report_doc
    from conftest import EXAMPLES

    payload = load_json(EXAMPLES / "runtime-lab-report.json")
    payload["report"]["schema"] = source_schema
    if source_schema == "serving-runtime-aws-lab-report/1.0":
        payload["report"]["cases"][0]["id"] = "AWS-01"
    document = lab_report_doc(payload)
    assert document["report"]["schema"] == source_schema
    assert contracts.errors("runtime-lab-report", document) == []
