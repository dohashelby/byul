"""Pure native INFO trial analysis; collection and saved replay share this.

Independent violations in complete parsed events survive later collection gaps.
An unverified/replaced source cannot establish those violations.
"""
from __future__ import annotations

from datetime import datetime

from .log_integrity_parser import parse_native_info

PLANNED_CASES = ("before", "multiline", "cr", "crlf", "reserved", "html", "after")


def analyze_integrity_capture(capture: dict, trials: list[dict], before_identity,
                              after_identity, *, initial_gaps=(), max_events=128) -> dict:
    gaps, violations, anchors, reached = list(initial_gaps), [], {}, []
    stable = bool(before_identity and before_identity == after_identity)
    if not stable:
        gaps.append("engine identity or logging controls changed or could not be rechecked")
    if (capture.get("returncode") != 0 or capture.get("truncated") or capture.get("stderr_present")
            or capture.get("timed_out") or capture.get("source_complete") is False):
        gaps.append("native source read failed, reached its bound or included a separate stderr stream")
    text = capture.get("text", "")
    ids = [trial.get("request_id", "") for trial in trials]
    collection = parse_native_info(text, markers=tuple(ids), max_events=max_events)
    gaps.extend(collection["gaps"])
    complete_events = []
    streams = [("stdout", text, collection)]
    if capture.get("returncode") == 0 and capture.get("stderr_text"):
        secondary = parse_native_info(capture["stderr_text"], markers=tuple(ids), max_events=max_events)
        collection["stderr_collection"] = secondary
        streams.append(("stderr", capture["stderr_text"], secondary))
        gaps.extend(secondary["gaps"])
        # The reviewed profile writes INFO to stdout. Stderr events may retain
        # independent duplicate/metadata failures, but never complete stdout
        # coverage or silently establish a different supported logger profile.
        gaps.append("reviewed native INFO stdout profile included a separate stderr stream")
    for stream, stream_text, parsed in streams:
        raw = stream_text.encode("utf-8")
        for event in parsed["events"]:
            if not raw[event["start_offset"]:event["end_offset"]].endswith(b"\n"):
                continue
            window = capture.get("window")
            if window is not None:
                try:
                    point, start, end = (datetime.fromisoformat(value.replace("Z", "+00:00")) for value in
                                         (event["docker_timestamp"], window["since"], window["until"]))
                    inside = point.tzinfo is not None and start <= point <= end
                except (TypeError, KeyError, ValueError):
                    inside = False
                if not inside:
                    gaps.append("native event is outside or has an invalid capture timestamp")
                    continue
            complete_events.append({**event, "_stream": stream})
    for trial in trials:
        label = trial["case"]
        expected = "Received request cmpl-" + trial["request_id"] + "-0: params: "
        matches = [event for event in complete_events if event["message"].startswith(expected)
                   and event["message"].endswith(", lora_request: None.")]
        source_matches = [event for event in matches if event["level"] == "INFO"
                          and event["source"] == "request_logger.py" and event["source_line"] == 65
                          and event["_stream"] == "stdout"]
        if stable and len(matches) > 1:
            violations.append(f"{label}: duplicate trial-linked request events in the reference collector")
        baseline = anchors.get("before")
        if stable and baseline and len(matches) == 1:
            if any(matches[0][key] != baseline[key] for key in ("process", "pid", "level", "source", "source_line")):
                violations.append(f"{label}: exact request event overwrites the selected native logger metadata")
        if len(source_matches) != 1 or not trial.get("response_id_matches_body"):
            gaps.append(f"{label}: one exact body-ID request event and successful linked response are unconfirmed")
            continue
        anchors[label] = source_matches[0]
        reached.append(label)
    baseline = anchors.get("before")
    fields_preserved = bool(baseline and len(reached) == len(PLANNED_CASES))
    if baseline and stable:
        for label, event in anchors.items():
            if any(event[key] != baseline[key] for key in ("process", "pid", "level", "source", "source_line")):
                violations.append(f"{label}: selected native logger metadata differs from the normal source")
                fields_preserved = False
    normal_logs = bool("before" in anchors and "after" in anchors)
    controls = {trial["case"]: trial.get("normal_generation") if trial.get("response_id_matches_body") else "no_response"
                for trial in trials if trial["case"] in {"before", "after"}}
    for label in ("before", "after"):
        controls.setdefault(label, "no_response")
    normal_inference = all(value == "pass" for value in controls.values())
    if not normal_logs:
        gaps.append("normal before/after request events did not reach the reference collector")
    if [trial.get("case") for trial in trials] != list(PLANNED_CASES):
        gaps.append("planned native INFO trial inventory is incomplete or reordered")
    collection["gaps"] = sorted(set(gaps))
    collection["complete"] = not gaps
    collection["source_identity"] = before_identity
    return {"collection": collection, "stable": stable, "violations": sorted(set(violations)),
            "gaps": sorted(set(gaps)), "reached": reached, "fields_preserved": fields_preserved,
            "boundaries": bool(stable and not gaps and len(reached) == len(PLANNED_CASES)),
            "normal_logs": normal_logs, "normal_inference": normal_inference, "controls": controls}
