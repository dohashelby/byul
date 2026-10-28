"""Only our safe result boundary; real source tests run in the isolated CPU tool."""
import json

import pytest

from tools.b2_message_cpu import PREFIX, safe_summary


def line(**changes):
    return PREFIX + json.dumps({"tests": 3, "passed": 3, "failed": 0, "error": 0, "skipped": 0,
                                "seconds": 1.0, **changes})


def test_exact_success_keeps_only_counters_and_timing():
    report = safe_summary("private fixture/exception\n" + line(), 0)
    assert report["status"] == "PASSED" and report["passed"] == 3
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize("output,rc", [(line(passed=2, failed=1), 1), (line(error=1), 1),
    (line(passed=2, skipped=1), 0), (line(), 2)])
def test_failure_skip_and_later_process_error_never_become_success(output, rc):
    result = safe_summary(output, rc)
    assert result["status"] == "NOT_PASSED"
    assert result["passed"] is not None  # Earlier observed counters are kept.


@pytest.mark.parametrize("output", ["", "private diagnostic", PREFIX + "{", line(passed=True), line(seconds=float("inf"))])
def test_missing_or_malformed_summary_is_unknown_not_zero(output):
    result = safe_summary(output, 0)
    assert result["status"] == "SUMMARY_UNAVAILABLE"
    assert result["passed"] is result["failed"] is None
    assert "private" not in json.dumps(result)
