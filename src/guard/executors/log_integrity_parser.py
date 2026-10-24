"""Bounded reference consumer of a byte-reviewed native INFO log profile.

This is not an enterprise collector. Docker CLI UTF-8 text is its input; offsets
and digests refer to re-encoded decoded CLI text, never a daemon log file. The
caller-controlled message remains opaque and never supplies trusted fields.
"""
from __future__ import annotations

import hashlib
import re


PROFILE = "guard-vllm-native-info-v1"
MAX_CAPTURE_BYTES = 131072
MAX_EVENTS = 128
TRUSTED_FIELDS = ("process", "level", "source")
_DOCKER = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z) ")
_PREFIX = re.compile(
    r"^(?P<prefix>\((?P<process>[A-Za-z0-9_. -]{1,64}) pid=(?P<pid>[1-9][0-9]{0,10})\) "
    r"(?P<level>DEBUG|INFO|WARNING|ERROR|CRITICAL) "
    r"(?P<timestamp>[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}) "
    r"\[(?P<source>[A-Za-z0-9_./-]{1,180}):(?P<line>[1-9][0-9]{0,7})\] )(?P<message>.*)$",
    re.DOTALL,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_native_info(text: str, *, markers: tuple[str, ...] = (), max_events: int = MAX_EVENTS) -> dict:
    """Consume only the fixed uncolored native prefix, preserving CR vs LF.

    Source-reviewed LF formatting inserts CRLF plus the *same* native prefix.
    A pending continuation may join only that prefix. Unknown/missing prefixes,
    replacement characters, bounded capture limits and ambiguous terminal CR
    are evidence gaps, not proof of successful event isolation.
    """
    if type(text) is not str or type(max_events) is not int or not 1 <= max_events <= MAX_EVENTS:
        raise ValueError("bounded decoded text and event count required")
    encoded = text.encode("utf-8")
    gaps, events, ignored = [], [], 0
    if len(encoded) >= MAX_CAPTURE_BYTES:
        gaps.append("decoded CLI capture reached the 128 KiB bound")
    if "\ufffd" in text:
        gaps.append("UTF-8 decoding replacement makes original bytes unavailable")
    if text and not text.endswith("\n"):
        gaps.append("decoded CLI capture ends inside a physical line")
    pending = None
    offset = 0

    def finish(event):
        event.pop("prefix", None)
        event.pop("continued", None)
        event["raw_decoded_sha256"] = _sha(text.encode("utf-8")[event["start_offset"]:event["end_offset"]].decode("utf-8"))
        event["message_sha256"] = _sha(event["message"])
        event["event_index"] = len(events)
        events.append(event)

    # splitlines() would erase the CR used by the reviewed formatter.
    physical = text.split("\n")
    for number, segment in enumerate(physical):
        if number == len(physical) - 1 and segment == "":
            continue
        terminated = number < len(physical) - 1
        raw = segment + ("\n" if terminated else "")
        end = offset + len(raw.encode("utf-8"))
        docker = _DOCKER.match(segment)
        native = segment[docker.end():] if docker else segment
        continued = bool(terminated and native.endswith("\r"))
        line = native[:-1] if continued else native
        matched = _PREFIX.match(line) if docker else None
        if pending is not None:
            if matched and matched["prefix"] == pending["prefix"]:
                pending["message"] += "\n" + matched["message"]
                pending["end_offset"] = end
                pending["physical_lines"] += 1
                pending["continued"] = continued
                if not continued:
                    finish(pending)
                    pending = None
                offset = end
                continue
            gaps.append("native continuation has no matching source prefix")
            finish(pending)
            pending = None
        if matched:
            if len(events) >= max_events:
                gaps.append("native event count reached its bound")
                break
            event = {"prefix": matched["prefix"], "process": matched["process"],
                     "pid": int(matched["pid"]), "level": matched["level"],
                     "source": matched["source"], "source_line": int(matched["line"]),
                     "native_timestamp": matched["timestamp"], "docker_timestamp": docker["timestamp"],
                     "message": matched["message"], "message_trust": "untrusted_opaque",
                     "start_offset": offset, "end_offset": end, "physical_lines": 1,
                     "continued": continued}
            if continued:
                pending = event
            else:
                finish(event)
        elif native.strip():
            ignored += 1
            if native.startswith("(") or any(marker in native for marker in markers):
                gaps.append("trial/native-looking physical line has an unsupported prefix")
        offset = end
    if pending is not None:
        gaps.append("capture ends with an unfinished native continuation")
        finish(pending)
    return {"schema": "guard-native-log-events/1", "collector_profile": PROFILE,
            "parser_version": "0.1.0", "capture_encoding": "UTF-8 decoded Docker CLI text; not daemon bytes",
            "decoded_capture_sha256": _sha(text), "decoded_capture_bytes": len(encoded),
            "trusted_fields": list(TRUSTED_FIELDS), "events": events,
            "ignored_other_format_lines": ignored, "gaps": sorted(set(gaps)), "complete": not gaps}
