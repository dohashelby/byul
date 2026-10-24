"""Reference parser/display contracts; all fixtures are synthetic EXAMPLEs."""
import hashlib
import json

import pytest

from guard.executors.log_integrity_parser import MAX_CAPTURE_BYTES, parse_native_info
from guard.report.log_integrity import render_log_integrity, verify_log_display


PREFIX = "(APIServer pid=42) INFO 10-23 12:00:00 [request_logger.py:65] "
STAMP = "2026-10-23T03:00:00.123456789Z "


def native(message, prefix=PREFIX):
    # Source-shaped fixture, never a measurement of a running engine.
    formatted = prefix + message.replace("\n", "\r\n" + prefix) + "\n"
    return "".join(STAMP + segment + "\n" for segment in formatted.split("\n")[:-1])


@pytest.mark.parametrize("message", ["normal", "line one\nline two", "one\rCR", "one\r\nCRLF",
    'opaque|level=ERROR|principal=guard-probe|source=guard-probe|status=probe',
    '<script>guard</script><tr data-event-index="fake">opaque</tr>', "한글\n메시지"])
def test_native_emission_preserves_opaque_message_and_original_span(message):
    text = native(message)
    parsed = parse_native_info(text)
    assert parsed["complete"] is True and len(parsed["events"]) == 1
    event = parsed["events"][0]
    assert event["message"] == message
    assert event["message_trust"] == "untrusted_opaque"
    assert event["process"] == "APIServer" and event["level"] == "INFO"
    assert event["source"] == "request_logger.py"
    assert event["start_offset"] == 0 and event["end_offset"] == len(text.encode())
    assert event["raw_decoded_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert set(parsed["trusted_fields"]) == {"process", "level", "source"}
    assert "principal" not in event and "status" not in event


def test_same_prefix_next_emission_remains_a_separate_event():
    parsed = parse_native_info(native("first") + native("second"))
    assert parsed["complete"] and [event["message"] for event in parsed["events"]] == ["first", "second"]
    first, second = parsed["events"]
    assert first["end_offset"] == second["start_offset"]


@pytest.mark.parametrize("text", [
    STAMP + PREFIX + "trailing\r\n",
    STAMP + PREFIX + "start\r\n" + STAMP + PREFIX.replace("INFO", "ERROR") + "wrong\n",
    STAMP + PREFIX + "incomplete",
    STAMP + PREFIX + "replacement \ufffd\n",
    STAMP + "(unexpected) GUARD_PREFIX\n",
    "GUARD_PREFIX no Docker timestamp\n",
])
def test_ambiguous_incomplete_or_unknown_trial_prefix_cannot_be_complete(text):
    parsed = parse_native_info(text, markers=("GUARD_PREFIX",))
    assert parsed["complete"] is False and parsed["gaps"]


def test_unrelated_other_log_format_is_counted_without_inventing_native_metadata():
    parsed = parse_native_info(STAMP + "INFO: HTTP /v1/completions 200\n" + native("native"))
    assert parsed["complete"] and parsed["ignored_other_format_lines"] == 1
    assert len(parsed["events"]) == 1


def test_event_and_capture_limits_withhold_completeness():
    parsed = parse_native_info(native("one") + native("two"), max_events=1)
    assert not parsed["complete"] and len(parsed["events"]) == 1
    parsed = parse_native_info("x" * MAX_CAPTURE_BYTES + "\n")
    assert not parsed["complete"] and any("128 KiB" in gap for gap in parsed["gaps"])


@pytest.mark.parametrize("maximum", [0, 129, True, 1.5, "2"])
def test_parser_does_not_accept_unbounded_or_coerced_event_limits(maximum):
    with pytest.raises(ValueError):
        parse_native_info("", max_events=maximum)


def test_reference_html_displays_exact_escaped_rows_and_crlf_as_visible_json():
    message = '<script>guard</script><tr data-event-index="fake">x</tr>\r\nlevel=ERROR'
    parsed = parse_native_info(native(message))
    html = render_log_integrity(parsed)
    assert verify_log_display(html, parsed)
    assert "<script>guard" not in html and "&lt;script&gt;" in html
    assert html.count('data-event-index="') == 1
    assert "\\r\\n" in html and json.dumps(message, ensure_ascii=False) not in html
    assert not verify_log_display(html.replace("APIServer", "different"), parsed)
    assert not verify_log_display(html.replace("</body>", "<script>bad</script></body>"), parsed)
    assert not verify_log_display(html.replace('data-event-index="0"', 'data-event-index="fake"'), parsed)
