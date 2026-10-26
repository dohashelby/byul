"""Pure OBS-03 capture analysis shared by collection and saved replay.

Fresh opaque IDs are correlation, not authenticated identities. Only exact ID
leaf values link an event; a substring in an unrelated traceback is insufficient.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import re

from .content_common import leaves


LEGACY_JSON_PROFILE = "guard-log-json/1"
STRICT_JSON_PROFILE = "guard-log-json/2"
REJECTED_JSON_LINE = "GUARD_LOG_JSON_UNVERIFIABLE"


def strict_json_loads(text: str):
    """Reject ambiguous JSON locally without importing collection/runtime code."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("ambiguous log JSON")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("ambiguous log JSON")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("ambiguous log JSON")
        return number

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant, parse_float=finite_float)


def events(text: str, *, profile: str = LEGACY_JSON_PROFILE) -> tuple[list[dict], bool]:
    # The default preserves the historical helper contract. Fresh collections
    # explicitly choose the strict profile, also recorded for saved replay.
    if profile not in {LEGACY_JSON_PROFILE, STRICT_JSON_PROFILE}:
        raise ValueError("unsupported log analysis profile")
    loads = strict_json_loads if profile == STRICT_JSON_PROFILE else json.loads
    found, complete = [], True
    for line in text.splitlines():
        if not line.strip():
            continue
        prefix = re.match(r"^(\d{4}-\d{2}-\d{2}T[^\s]+)\s+(\{.*)$", line)
        try:
            value = loads(prefix.group(2) if prefix else line)
        except (ValueError, RecursionError):
            complete = False
            continue
        if not isinstance(value, dict):
            complete = False
            continue
        found.append({"event": value, "timestamp": prefix.group(1) if prefix else None})
    return found, complete


def persistable_log_text(text: str) -> tuple[str, int]:
    """Discard rejected raw rows before generic JSON redaction can normalize them.

    A fixed invalid-JSON placeholder preserves the same coverage gap on replay.
    A decoding-replacement signal is retained without copying source content.
    The caller keeps original source digests separate from persisted text hashes.
    """
    lines, rejected = [], 0
    for line in text.splitlines(keepends=True):
        _, valid = events(line, profile=STRICT_JSON_PROFILE)
        if valid:
            lines.append(line)
        else:
            rejected += 1
            lines.append(REJECTED_JSON_LINE + ("\ufffd" if "\ufffd" in line else "") + "\n")
    return "".join(lines), rejected


def _inside(timestamp, window):
    if timestamp is None:
        return True  # Exact fresh request IDs remain the stated window basis.
    try:
        point, start, end = (datetime.fromisoformat(value.replace("Z", "+00:00"))
                             for value in (timestamp, window["since"], window["until"]))
        return point.tzinfo is not None and start <= point <= end
    except (TypeError, ValueError, KeyError):
        return False


def analyze_log_captures(captures: list[dict], values: dict, marks: dict,
                         correlation: dict, *, initial_gaps: list[str] = (),
                         profile: str = LEGACY_JSON_PROFILE) -> dict:
    """Reconstruct semantic log observations without I/O or verdict changes."""
    if profile not in {LEGACY_JSON_PROFILE, STRICT_JSON_PROFILE}:
        raise ValueError("unsupported log analysis profile")
    gaps, violations, coverage = list(initial_gaps), [], {}
    fields_seen, contexts_seen = set(), set()
    required = values["required_sources"]
    by_id = {item.get("source", {}).get("id"): item for item in captures}
    if len(by_id) != len(captures):
        gaps.append("duplicate captured source IDs")
    if not any(item.get("source", {}).get("role", "engine") == "engine"
               and item.get("source", {}).get("id") in required for item in captures):
        gaps.append("required local engine source was not captured")
    for source_id in required:
        capture = by_id.get(source_id)
        if capture is None:
            gaps.append(f"{source_id}: required source not captured")
            continue
        if (capture.get("returncode") != 0 or capture.get("truncated")
                or capture.get("timed_out")
                or capture.get("source_complete") is False):
            gaps.append(f"{source_id}: failed, truncated or incomplete source read")
        if (not capture.get("identity_stable")
                or capture.get("file_identity_stable") is False):
            gaps.append(f"{source_id}: changed or unverified source identity")
            continue
        # Parse independently: splitting a JSON object across CLI pipes must
        # never manufacture one event. Successful Docker stderr is a container
        # stream; failed CLI stderr is excluded from the capture entirely.
        texts = [capture.get("text", "")]
        if capture.get("source", {}).get("kind") == "docker_logs" and capture.get("returncode") == 0:
            texts.append(capture.get("stderr_text", ""))
        parsed, structured = [], True
        for text in texts:
            rows, valid = events(text, profile=profile)
            parsed.extend(rows)
            structured = structured and valid
        if any("\ufffd" in text for text in texts):
            gaps.append(f"{source_id}: UTF-8 decoding replacement makes original text incomplete")
        if not structured or not parsed:
            gaps.append(f"{source_id}: JSON field inventory unavailable or empty; plain text absence is not PASS")
        branches = set()
        for row in parsed:
            event = row["event"]
            event_fields = leaves(event)
            scalar_strings = [value for group in event_fields.values() for value in group if isinstance(value, str)]
            correlation_values = [value for field, group in event_fields.items()
                                  if '["' not in field and (field == "request_id" or field.endswith(".request_id"))
                                  for value in group if isinstance(value, str)]
            normal = correlation.get("normal") in correlation_values
            error = correlation.get("error") in correlation_values
            if not normal and not error:
                continue  # Historical/unrelated events cannot establish a trial FAIL.
            if not _inside(row["timestamp"], capture.get("window", {})):
                gaps.append(f"{source_id}: exact trial event lies outside or has an invalid capture timestamp")
                continue
            fields_seen.update(event_fields)
            for field in sorted(event_fields):
                if field in values["forbidden_log_fields"] or field not in values["allowed_log_fields"]:
                    violations.append(f"{source_id}: nonpermitted log field {field}")
            for context, marker in marks.items():
                if any(re.search(r"(?<![A-Za-z0-9_])" + re.escape(marker) + r"(?![A-Za-z0-9_])", value)
                       for value in scalar_strings):
                    contexts_seen.add(context)
                    if context in values["forbidden_marker_contexts"]:
                        violations.append(f"{source_id}: forbidden {context} marker present")
            if normal:
                branches.add("normal")
            if error:
                branches.add("error")
                for field, items in event_fields.items():
                    if '["' in field:
                        continue
                    names = {part.lower() for part in field.replace("[]", "").split(".")}
                    if any(isinstance(value, str) and value.strip() for value in items):
                        if names & {"exception", "traceback", "exception_dump", "dump"}:
                            branches.add("exception_dump")
                        branches.update(names & {"debug", "trace"})
        coverage[source_id] = sorted(branches)
        missing = set(values["required_branches"]) - branches
        if missing:
            gaps.append(f"{source_id}: no trial-linked events for {', '.join(sorted(missing))}")
    return {"log_violations": sorted(set(violations)), "log_coverage_complete": not gaps,
            "log_coverage_gaps": sorted(set(gaps)), "log_sources_tested": coverage,
            "log_fields_seen": sorted(fields_seen), "log_marker_contexts_seen": sorted(contexts_seen),
            "log_source_ids_tested": [sid for sid in required if sid in coverage],
            "log_required_branches_tested": [branch for branch in values["required_branches"]
                 if len(coverage) == len(required) and coverage and all(branch in rows for rows in coverage.values())]}
