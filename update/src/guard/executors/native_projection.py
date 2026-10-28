"""OBS-09 predicate transcript; original native parsing is not replayed.

The unchanged analyzer reads a canonical transcript of eligible parsed events.
Source parser gaps, event references and original byte digests remain separate
from the transcript's offsets/digests. No original message/ID/body is persisted.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import re

from .log_integrity_contract import PLANNED_CASES, analyze_integrity_capture
from .log_integrity_parser import parse_native_info
from .log_projection import fingerprint

PROFILE = "guard-native-predicate-projection/1"
ENCODING = "UTF-8 native predicate transcript; original values omitted"
SCHEMA = "guard-log-integrity-capture/3"
PUBLIC_IDS = {case: "GNI_" + case for case in PLANNED_CASES}
MAX_BYTES = 16 * 1024**2
_HEX = re.compile(r"[0-9a-f]{64}")
_DOCKER_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
_NATIVE_TIME = re.compile(r"[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}")
_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_PARSER_GAPS = {
    "decoded CLI capture reached the 128 KiB bound",
    "UTF-8 decoding replacement makes original bytes unavailable",
    "decoded CLI capture ends inside a physical line",
    "native continuation has no matching source prefix",
    "native event count reached its bound",
    "trial/native-looking physical line has an unsupported prefix",
    "capture ends with an unfinished native continuation",
}
_INITIAL_GAPS = {
    "owned native INFO engine identity, actual logging controls or reviewed profile unavailable",
    "requested collector or request-ID trust policy is outside the reference profile",
    "required enterprise trusted fields are absent from this native logger",
    *[f"{case}: identity or bounded dispatch budget prevented request" for case in PLANNED_CASES],
    *[f"{case}: unexpected correlation header would hide the body trial" for case in PLANNED_CASES],
}
_OUTSIDE = "native event is outside or has an invalid capture timestamp"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(ok):
    if not ok:
        raise ValueError("NATIVE_PROJECTION_INVALID")


def _hash(value):
    return type(value) is str and _HEX.fullmatch(value) is not None


def _integer(value, low=0, high=2**63-1):
    return type(value) is int and low <= value <= high


def _message(case):
    return "Received request cmpl-" + PUBLIC_IDS[case] + "-0: params: GNI_PARAMS, lora_request: None."


def _identity(value):
    if value is None:
        return None
    # Infrastructure metadata from the already checked snapshot, no Env/argv.
    return {"container_id_sha256": fingerprint(value["container_id"]),
            "started_at_sha256": fingerprint(value["started_at"]),
            **{key: value[key] for key in ("image", "image_id", "image_repo_digest_verified",
                                          "logging_profile", "logging_controls_verified")}}


def decision(analysis):
    return {key: value for key, value in analysis.items() if key != "collection"}


def project_native(capture, trials, before, after, *, initial_gaps=(), max_events=128,
                   analysis=None):
    """Keep exact-match/equality predicates, not native message or ID values."""
    converted = []
    for trial in trials:
        case = trial["case"]
        response_id = trial.get("response_id")
        expected = "cmpl-" + trial["request_id"]
        converted.append({"case": case, "request_id": PUBLIC_IDS[case],
            "request_id_trust": "untrusted_correlation", "request_id_sha256": fingerprint(trial["request_id"]),
            "request_fixture_sha256": trial["request_fixture_sha256"],
            "expected_response_id_sha256": fingerprint(expected),
            "response_id_sha256": fingerprint(response_id) if type(response_id) is str else None,
            "response_id": "cmpl-" + PUBLIC_IDS[case] if response_id == expected else None,
            "http_outcome": trial["http_outcome"], "normal_generation": trial["normal_generation"],
            "response_id_matches_body": trial["response_id_matches_body"]})
    result = {"returncode": capture.get("returncode"),
              **{key: bool(capture.get(key)) for key in ("truncated", "timed_out", "stderr_present")},
              "source_complete": capture.get("source_complete") is not False,
              "window": capture["window"]}
    source, source_gaps = {}, set()
    for stream, key in (("stdout", "text"), ("stderr", "stderr_text")):
        text = capture.get(key, "") if stream == "stdout" or capture.get("returncode") == 0 else ""
        parsed = parse_native_info(text, markers=tuple(row["request_id"] for row in trials), max_events=max_events)
        source_gaps.update(parsed["gaps"])
        raw, lines, refs = text.encode("utf-8"), [], []
        for event in parsed["events"]:
            terminated = raw[event["start_offset"]:event["end_offset"]].endswith(b"\n")
            inside = False
            if terminated:
                try:
                    point, start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in
                        (event["docker_timestamp"], capture["window"]["since"], capture["window"]["until"]))
                    inside = point.tzinfo is not None and start <= point <= end
                except (ValueError, TypeError, KeyError):
                    pass
                if not inside:
                    source_gaps.add(_OUTSIDE)
            refs.append({**{name: event[name] for name in ("event_index", "start_offset", "end_offset",
                           "physical_lines", "raw_decoded_sha256", "message_sha256")},
                         "terminated": terminated, "inside_window": inside if terminated else None,
                         "eligible": bool(terminated and inside)})
            if not terminated or not inside:
                continue
            matches = [row["case"] for row in trials if event["message"].startswith(
                "Received request cmpl-" + row["request_id"] + "-0: params: ")
                and event["message"].endswith(", lora_request: None.")]
            _require(len(matches) <= 1)
            message = _message(matches[0]) if matches else "GNI_OTHER"
            source_name = "request_logger.py" if event["source"] == "request_logger.py" else "GS_" + fingerprint(event["source"]) + ".py"
            lines.append(f"{event['docker_timestamp']} ({fingerprint(event['process'])} pid={event['pid']}) "
                f"{event['level']} {event['native_timestamp']} [{source_name}:{event['source_line']}] {message}\n")
        result[key] = "".join(lines)
        result["persisted_" + ("decoded" if stream == "stdout" else "stderr") + "_sha256"] = _sha(result[key])
        result["persisted_" + ("decoded" if stream == "stdout" else "stderr") + "_bytes"] = len(result[key].encode())
        source[stream] = {"decoded_sha256": _sha(text), "decoded_bytes": len(raw),
                          "ignored_other_format_lines": parsed["ignored_other_format_lines"],
                          "parser_gaps": parsed["gaps"], "events": refs}
    # Preserve the presence of an unparseable/non-event stderr stream.
    if capture.get("returncode") == 0 and capture.get("stderr_text") and not result["stderr_text"]:
        result["stderr_text"] = "GNI_IGNORED\n"
        result["persisted_stderr_sha256"] = _sha(result["stderr_text"])
        result["persisted_stderr_bytes"] = len(result["stderr_text"].encode())
    replay_gaps = sorted(set(initial_gaps) | source_gaps)
    first, last = _identity(before), _identity(after)
    replay = analyze_integrity_capture(result, converted, first, last,
        initial_gaps=replay_gaps, max_events=max_events)
    original = analysis or analyze_integrity_capture(capture, trials, before, after,
        initial_gaps=initial_gaps, max_events=max_events)
    _require(decision(replay) == decision(original))
    return {"schema": SCHEMA, "projection_profile": PROFILE, "capture": result, "trials": converted,
            "source_summary": source, "source_identity_before": first, "source_identity_after": last,
            "initial_gaps": list(initial_gaps), "replay_initial_gaps": replay_gaps,
            "source_transport": {key: capture.get(key) for key in ("decoded_stdout_bytes", "decoded_stdout_sha256",
                "raw_retained_bytes", "raw_retained_sha256")},
            "max_events": max_events, "analysis": decision(replay)}


def replay_native(sample):
    """Strictly allow typed metadata and public predicate strings only."""
    _require(set(sample) == {"schema", "projection_profile", "capture", "trials", "source_summary",
        "source_identity_before", "source_identity_after", "initial_gaps", "replay_initial_gaps",
        "max_events", "analysis", "policy_values", "native_contract_sha256", "source_display_sha256",
        "collection_sha256", "display_sha256", "display_verified", "source_transport"})
    _require(sample["schema"] == SCHEMA and sample["projection_profile"] == PROFILE)
    _require(_integer(sample["max_events"], 1, 128))
    for key in ("native_contract_sha256", "source_display_sha256", "collection_sha256", "display_sha256"):
        _require(_hash(sample[key]))
    _require(type(sample["display_verified"]) is bool)
    for identity in (sample["source_identity_before"], sample["source_identity_after"]):
        if identity is None:
            continue
        _require(type(identity) is dict and set(identity) == {"container_id_sha256", "started_at_sha256", "image",
            "image_id", "image_repo_digest_verified", "logging_profile", "logging_controls_verified"})
        _require(all(_hash(identity[k]) for k in ("container_id_sha256", "started_at_sha256")))
        _require(type(identity["image"]) is str and len(identity["image"]) <= 512
            and re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", identity["image"]) is not None)
        _require(type(identity["image_id"]) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", identity["image_id"]) is not None)
        _require(identity["image_repo_digest_verified"] is True and identity["logging_controls_verified"] is True
            and identity["logging_profile"] == "request-info-v1")
    transport = sample["source_transport"]
    _require(type(transport) is dict and set(transport) == {"decoded_stdout_bytes", "decoded_stdout_sha256", "raw_retained_bytes", "raw_retained_sha256"})
    for key, value in transport.items():
        _require(value is None or (_hash(value) if key.endswith("sha256") else _integer(value)))
    trials = sample["trials"]
    _require(type(trials) is list and len(trials) <= 7)
    _require(len({t["case"] for t in trials}) == len(trials))
    for trial in trials:
        _require(set(trial) == {"case", "request_id", "request_id_trust", "request_id_sha256", "request_fixture_sha256",
            "expected_response_id_sha256", "response_id_sha256", "response_id", "http_outcome", "normal_generation", "response_id_matches_body"})
        case = trial["case"]
        _require(case in PLANNED_CASES and trial["request_id"] == PUBLIC_IDS[case]
            and trial["request_id_trust"] == "untrusted_correlation")
        _require(all(_hash(trial[key]) for key in ("request_id_sha256", "request_fixture_sha256", "expected_response_id_sha256")))
        _require(trial["response_id_sha256"] is None or _hash(trial["response_id_sha256"]))
        same = trial["response_id_sha256"] == trial["expected_response_id_sha256"]
        _require(trial["response_id"] == ("cmpl-" + PUBLIC_IDS[case] if same else None))
        _require(trial["normal_generation"] in {"pass", "fail", "no_response"})
        _require(_integer(trial["http_outcome"], 100, 599) or trial["http_outcome"] in {
            "refused", "timeout", "unreachable", "dns_error", "error", "probe_error"})
        _require(type(trial["response_id_matches_body"]) is bool and trial["response_id_matches_body"] ==
            (trial["normal_generation"] == "pass" and trial["http_outcome"] == 200 and same))
    capture = sample["capture"]
    _require(set(capture) == {"returncode", "truncated", "timed_out", "stderr_present", "source_complete", "window",
        "text", "stderr_text", "persisted_decoded_sha256", "persisted_decoded_bytes", "persisted_stderr_sha256", "persisted_stderr_bytes"})
    _require(capture["returncode"] is None or _integer(capture["returncode"], -2**31, 2**31-1))
    _require(all(type(capture[key]) is bool for key in ("truncated", "timed_out", "stderr_present", "source_complete")))
    _require(type(capture["window"]) is dict and set(capture["window"]) == {"since", "until"})
    for value in capture["window"].values():
        _require(type(value) is str and len(value) <= 40 and re.fullmatch(r"[0-9T:.+Z-]+", value) is not None)
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require(type(sample["initial_gaps"]) is list and set(sample["initial_gaps"]) <= _INITIAL_GAPS)
    expected_gaps = set(sample["initial_gaps"])
    _require(type(sample["source_summary"]) is dict and set(sample["source_summary"]) == {"stdout", "stderr"})
    for stream, key in (("stdout", "text"), ("stderr", "stderr_text")):
        summary = sample["source_summary"][stream]
        _require(set(summary) == {"decoded_sha256", "decoded_bytes", "ignored_other_format_lines", "parser_gaps", "events"})
        _require(_hash(summary["decoded_sha256"]) and _integer(summary["decoded_bytes"])
            and _integer(summary["ignored_other_format_lines"]))
        _require(type(summary["parser_gaps"]) is list and set(summary["parser_gaps"]) <= _PARSER_GAPS)
        expected_gaps.update(summary["parser_gaps"])
        _require(type(summary["events"]) is list and len(summary["events"]) <= sample["max_events"])
        for index, event in enumerate(summary["events"]):
            _require(set(event) == {"event_index", "start_offset", "end_offset", "physical_lines", "raw_decoded_sha256", "message_sha256", "terminated", "inside_window", "eligible"})
            _require(type(event["event_index"]) is int and event["event_index"] == index
                and _integer(event["start_offset"]) and _integer(event["end_offset"], event["start_offset"], summary["decoded_bytes"])
                and _integer(event["physical_lines"], 1) and type(event["eligible"]) is bool
                and _hash(event["raw_decoded_sha256"]) and _hash(event["message_sha256"]))
            _require(type(event["terminated"]) is bool and
                (type(event["inside_window"]) is bool if event["terminated"] else event["inside_window"] is None)
                and event["eligible"] == bool(event["terminated"] and event["inside_window"]))
            if event["terminated"] and not event["inside_window"]:
                expected_gaps.add(_OUTSIDE)
        text = capture[key]
        _require(type(text) is str and len(text.encode()) < MAX_BYTES)
        prefix = "decoded" if stream == "stdout" else "stderr"
        _require(capture["persisted_" + prefix + "_sha256"] == _sha(text)
            and type(capture["persisted_" + prefix + "_bytes"]) is int
            and capture["persisted_" + prefix + "_bytes"] == len(text.encode()))
        parsed = parse_native_info(text, max_events=sample["max_events"])
        _require(not parsed["gaps"] and len(parsed["events"]) == sum(event["eligible"] for event in summary["events"]))
        _require(parsed["ignored_other_format_lines"] == (1 if text == "GNI_IGNORED\n" and stream == "stderr" else 0))
        for event in parsed["events"]:
            _require(_hash(event["process"]) and event["level"] in _LEVELS and
                (event["source"] == "request_logger.py" or re.fullmatch(r"GS_[0-9a-f]{64}\.py", event["source"]) is not None)
                and _DOCKER_TIME.fullmatch(event["docker_timestamp"]) is not None
                and _NATIVE_TIME.fullmatch(event["native_timestamp"]) is not None
                and event["physical_lines"] == 1 and event["message"] in {"GNI_OTHER", *map(_message, PLANNED_CASES)})
        # Require byte-exact canonical serialization, rejecting whitespace or extra raw lines.
        canonical = "".join(f"{e['docker_timestamp']} ({e['process']} pid={e['pid']}) {e['level']} "
            f"{e['native_timestamp']} [{e['source']}:{e['source_line']}] {e['message']}\n" for e in parsed["events"])
        _require(text == canonical or (stream == "stderr" and text == "GNI_IGNORED\n"))
    _require(type(sample["replay_initial_gaps"]) is list and sample["replay_initial_gaps"] == sorted(set(sample["replay_initial_gaps"]))
        and set(sample["replay_initial_gaps"]) == expected_gaps)
    analysis = analyze_integrity_capture(capture, trials, sample["source_identity_before"], sample["source_identity_after"],
        initial_gaps=sample["replay_initial_gaps"], max_events=sample["max_events"])
    _require(sample["analysis"] == decision(analysis))
    return analysis


def envelope(sample):
    return {"capture": sample, "redaction": PROFILE, "encoding": ENCODING}


def render_projection(collection):
    """Label the separate reference artifact without changing the old renderer."""
    from guard.report.log_integrity import render_log_integrity
    document = render_log_integrity(collection)
    return document.replace("</h1>", "</h1>\n<p>저장용 판정 표식 투영입니다. 원문 메시지·요청 ID·줄 경계를 "
        "복원하거나 재검증한 표시가 아닙니다. 투영 행 해시는 저장 투영의 해시이며, "
        "수집 당시 원문 지문은 별도 source_summary에 있습니다.</p>", 1).replace(
        "<th>비신뢰 메시지</th>", "<th>시험 일치 표식</th>").replace("<th>원본 참조</th>", "<th>투영 행 해시</th>")
