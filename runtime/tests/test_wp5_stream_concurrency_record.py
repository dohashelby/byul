"""5-3 review follow-up: a rejected stream still records stream=True."""

from pathlib import Path
import runpy

import pytest

observe = runpy.run_path(str(Path(__file__).with_name("test_wp5_handle_contract.py")))["observe"]


@pytest.mark.parametrize("kind", ["chat", "completions"])
def test_stream_concurrency_rejection_records_stream_true(kind):
    result = observe({"kind": kind, "stream": True, "errors": ["concurrency"]})
    finals = [event[1][0] for event in result["events"] if event[0] == "append:final"]
    assert len(finals) == 1
    assert finals[0]["stream"] is True
    assert finals[0]["decision"]["http_status"] == 429
    assert finals[0]["decision"]["reason_code"] == "CONCURRENCY_LIMIT"
    errors = [event for event in result["events"] if event[0] == "send_error"]
    assert len(errors) == 1
    assert errors[0][2]["extra_headers"] == {"Retry-After": "1"}
    assert not any(event[0] in {"relay", "relay_stream"} for event in result["events"])
    assert result["acquired"] == result["released"] == result["in_flight"] == 0
