"""Pure source/correlation contracts; every subject is an EXAMPLE."""
from copy import deepcopy
import json

import pytest

from guard.executors.log_analysis_contract import analyze_log_captures
from guard.executors.log_integrity_contract import analyze_integrity_capture
from tests.test_log_analysis import VALUES
from tests.test_log_integrity_parser import native, PREFIX


def source(events=None, **changes):
    rows = events if events is not None else [{"request_id": "EXAMPLE-N", "event": "normal"},
        {"request_id": "EXAMPLE-X", "event": "error", "exception": "EXAMPLE traceback"}]
    return {"source": {"id": "engine", "kind": "docker_logs"}, "identity_stable": True,
            "returncode": 0, "truncated": False, "source_complete": True,
            "text": "\n".join(json.dumps(event) for event in rows),
            "window": {"since": "2026-10-23T00:00:00Z", "until": "2026-10-23T23:59:00Z"}, **changes}


MARKS = {"caller_input": "EXAMPLE-MARK-U", "private_system": "EXAMPLE-MARK-S", "error_input": "EXAMPLE-MARK-X"}
CORRELATION = {"normal": "EXAMPLE-N", "error": "EXAMPLE-X"}


def analyze(capture, values=VALUES):
    return analyze_log_captures([capture], values, MARKS, CORRELATION)


def test_exact_ids_cover_normal_error_and_actual_exception():
    assert analyze(source())["log_coverage_complete"]


@pytest.mark.parametrize("row", [
    {"request_id": "EXAMPLE-X-more", "event": "historical", "exception": "EXAMPLE-X traceback"},
    {"unrelated": "EXAMPLE-X", "event": "past", "exception": "EXAMPLE traceback"},
    {"request_id": "other", "exception": "EXAMPLE-X trace"},
])
def test_substring_and_unrelated_traceback_cannot_cover_error(row):
    # Only a literal request ID leaf links the fixed request protocol. A value
    # in a random field is not a registered correlation key.
    result = analyze(source([{"request_id": "EXAMPLE-N", "event": "normal"}, row]))
    assert not result["log_coverage_complete"]


def test_unrelated_historical_secret_field_is_not_a_trial_failure():
    capture = source()
    capture["text"] += '\n' + json.dumps({"request_id": "other", "prompt": MARKS["caller_input"]})
    result = analyze(capture)
    assert result["log_coverage_complete"] and not result["log_violations"]


@pytest.mark.parametrize("changes", [{"truncated": True}, {"returncode": 1}, {"timed_out": True},
                                      {"source_complete": False}, {"stderr_present": True}])
def test_valid_leak_remains_fail_material_when_other_collection_is_incomplete(changes):
    capture = source([{"request_id": "EXAMPLE-N", "prompt": MARKS["caller_input"]}], **changes)
    result = analyze(capture)
    assert not result["log_coverage_complete"] and result["log_violations"]


@pytest.mark.parametrize("changes", [{"identity_stable": False}, {"file_identity_stable": False}])
def test_replaced_source_cannot_establish_engine_failure(changes):
    capture = source([{"request_id": "EXAMPLE-N", "prompt": MARKS["caller_input"]}], **changes)
    result = analyze(capture)
    assert not result["log_coverage_complete"] and not result["log_violations"]


def test_event_outside_actual_capture_window_cannot_establish_coverage_or_fail():
    capture = source()
    capture["text"] = '2020-01-01T00:00:00Z ' + json.dumps({"request_id": "EXAMPLE-X", "prompt": MARKS["caller_input"]})
    result = analyze(capture)
    assert not result["log_coverage_complete"] and not result["log_violations"]


def test_collector_only_cannot_substitute_the_local_engine():
    capture = source()
    capture["source"]["role"] = "copy"
    assert not analyze(capture)["log_coverage_complete"]


def test_successful_docker_stderr_is_a_separate_log_stream_and_can_cover_trials():
    capture = source()
    capture["stderr_text"], capture["text"] = capture["text"], ""
    capture["stderr_present"] = True
    result = analyze(capture)
    assert result["log_coverage_complete"] and not result["log_violations"]


def test_pipes_cannot_complete_each_others_partial_json_or_use_failed_cli_diagnostics():
    capture = source()
    capture["text"] = '{"request_id":"EXAMPLE-'
    capture["stderr_text"] = 'X", "exception":"EXAMPLE traceback"}'
    assert not analyze(capture)["log_coverage_complete"]
    capture = source()
    capture.update(text="", stderr_text=capture["text"], returncode=1)
    assert not analyze(capture)["log_sources_tested"]["engine"]


def test_stderr_leak_survives_missing_other_required_source():
    capture = source()
    capture.update(stderr_text=json.dumps({"request_id": "EXAMPLE-X", "credential": "EXAMPLE"}))
    result = analyze(capture, {**VALUES, "required_sources": ["engine", "collector"]})
    assert result["log_violations"] and not result["log_coverage_complete"]


def test_literal_exception_like_field_does_not_create_a_dump_branch():
    capture = source([{"request_id": "EXAMPLE-N", "event": "normal"},
                      {"request_id": "EXAMPLE-X", "exception.fake": "EXAMPLE"}])
    assert "exception_dump" not in analyze(capture)["log_sources_tested"]["engine"]


def native_trial(case="before", request_id="EXAMPLE-normal"):
    return {"case": case, "request_id": request_id, "normal_generation": "pass", "response_id_matches_body": True}


def native_analysis(text, trials, *, stable=True, **changes):
    capture = {"text": text, "returncode": 0, "truncated": False, **changes}
    return analyze_integrity_capture(capture, trials, {"id": "EXAMPLE-engine"},
                                     {"id": "EXAMPLE-engine" if stable else "replaced"})


def test_complete_duplicate_events_preserve_violation_despite_truncated_later_capture():
    message = "Received request cmpl-EXAMPLE-normal-0: params: SamplingParams(), lora_request: None."
    result = native_analysis(native(message) * 2 + "incomplete", [native_trial()], truncated=True)
    assert result["violations"] and result["gaps"]
    assert not result["collection"]["complete"]


def test_unstable_source_or_partial_duplicate_cannot_manufacture_violation():
    message = "Received request cmpl-EXAMPLE-normal-0: params: SamplingParams(), lora_request: None."
    assert not native_analysis(native(message) * 2, [native_trial()], stable=False)["violations"]
    assert not native_analysis(native(message) + native(message).rstrip("\n"), [native_trial()])["violations"]


def test_similar_body_request_id_is_not_the_exact_trial():
    message = "Received request cmpl-EXAMPLE-normal-extra-0: params: SamplingParams(), lora_request: None."
    result = native_analysis(native(message), [native_trial()])
    assert not result["reached"] and not result["violations"]


def test_native_stderr_preserves_duplicate_failure_without_completing_stdout_profile():
    message = "Received request cmpl-EXAMPLE-normal-0: params: SamplingParams(), lora_request: None."
    result = native_analysis(native(message), [native_trial()], stderr_text=native(message), stderr_present=True)
    assert result["violations"] and not result["collection"]["complete"]
    assert result["collection"]["stderr_collection"]["events"]
