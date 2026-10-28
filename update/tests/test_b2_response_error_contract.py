"""EXAMPLE transport fixtures; actual handler execution is an opt-in CPU tool."""
import json

import pytest

from guard.executors.response_error_contract import observe_error_log, observe_error_response

MARKS = {"caller_input": "EXAMPLE_CALLER", "error_input": "EXAMPLE_INVALID",
         "private_system": "EXAMPLE_PRIVATE", "internal_path": "/srv/EXAMPLE/internal.py"}
FIELDS = ["error.message", "error.type", "error.param", "error.code"]


def response(message="invalid EXAMPLE_INVALID", **changes):
    text = json.dumps({"error": {"message": message, "type": "Bad Request", "param": "body.max_tokens", "code": 400}})
    return {"outcome": 400, "body": text, "body_bytes": len(text.encode()), **changes}


def observe(result, **kwargs):
    return observe_error_response(result, marks=MARKS, allowed_fields=FIELDS,
                                  forbidden_contexts=["private_system", "internal_path"], **kwargs)


def test_callers_error_input_is_not_private_system_disclosure():
    result = observe(response())
    assert result["complete"] and result["violations"] == []
    assert result["marker_contexts_seen"] == ["error_input"]


def test_policy_unset_still_collects_facts_without_inventing_allowed_values():
    result = observe_error_response(response(), marks=MARKS)
    assert result["marker_contexts_seen"] == ["error_input"]
    assert result["coverage_gaps"] == ["FIELD_POLICY_UNSET", "MARKER_POLICY_UNSET"]
    assert not result["complete"] and not result["violations"]


@pytest.mark.parametrize("changes", [{"body_bytes": 1_000_000}, {"outcome": 500}, {"body": "EXAMPLE_PRIVATE{"}])
def test_confirmed_private_marker_survives_truncation_error_and_missing_json(changes):
    result = observe(response("EXAMPLE_PRIVATE", **changes))
    assert "FORBIDDEN_MARKER:private_system" in result["violations"]
    assert not result["complete"]


@pytest.mark.parametrize("result", [None, {}, {"outcome": True}, {"outcome": "private exception"}])
def test_missing_response_cannot_complete_coverage_or_save_transport_diagnostics(result):
    report = observe(result)
    assert not report["complete"] and report["violations"] == []
    assert "private exception" not in json.dumps(report)


def test_ambiguous_json_is_gap_without_erasing_independent_marker_violation():
    result = observe({"outcome": 400, "body": '{"error":"EXAMPLE_PRIVATE","error":null}'})
    assert "JSON_AMBIGUOUS" in result["coverage_gaps"]
    assert result["violations"] == ["FORBIDDEN_MARKER:private_system"]


def test_response_and_logger_are_separate_and_no_payload_is_returned():
    returned = observe(response())
    logged = observe_error_log("EXAMPLE_PRIVATE", marks=MARKS,
                              forbidden_contexts=["private_system"], source_complete=True)
    assert returned["violations"] == []
    assert logged["violations"] == ["FORBIDDEN_MARKER:private_system"]
    for report in (returned, logged):
        assert all(value not in json.dumps(report) for value in MARKS.values())


def test_arbitrary_response_keys_are_hashed_before_becoming_evidence():
    result = observe({"outcome": 400, "body": '{"opaque-private-key":"value"}'})
    assert result["violations"] and "opaque-private-key" not in json.dumps(result)


def test_unicode_escaping_does_not_hide_private_marker():
    result = observe({"outcome": 400, "body": '{"error":{"message":"\\u0045XAMPLE_PRIVATE"}}'})
    assert result["violations"] == ["FORBIDDEN_MARKER:private_system"]


def test_marker_in_escaped_literal_field_key_is_detected_without_saving_the_key():
    result = observe_error_response({"outcome": 400, "body": '{"\\u0045XAMPLE_PRIVATE":null}'},
                                    marks=MARKS, forbidden_contexts=["private_system"])
    assert result["violations"] == ["FORBIDDEN_MARKER:private_system"]
    assert MARKS["private_system"] not in json.dumps(result)


@pytest.mark.parametrize("body", ['{"error":"\\u0045XAMPLE_PRIVATE","error":null}',
                                 '{"error":"\\u0045XAMPLE_PRIVATE","later":'])
def test_complete_escaped_string_retains_independent_violation_when_structure_is_missing(body):
    result = observe({"outcome": 400, "body": body})
    assert result["violations"] == ["FORBIDDEN_MARKER:private_system"]
    assert not result["complete"] and result["coverage_gaps"]
    assert result["field_path_sha256"] == []


def test_literal_backslash_escape_is_not_recursively_decoded_into_a_marker():
    result = observe({"outcome": 400, "body": '{"error":{"message":"\\\\u0045XAMPLE_PRIVATE"}}'})
    assert result["complete"] and not result["violations"]


def test_later_log_failure_does_not_erase_observed_violation():
    result = observe_error_log("EXAMPLE_PRIVATE", marks=MARKS,
                              forbidden_contexts=["private_system"], source_complete=False)
    assert result["violations"] and result["coverage_gaps"] and not result["complete"]
