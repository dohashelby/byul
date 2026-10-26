"""Bounded EXAMPLE-only template event correlation; no engine or security judge.

The input is synthetic, allowlisted metadata, never raw logs. Consistent records
do not authenticate their producer, attest a host, or establish actual template
consumption. This tool does not create POL/PLAN/EVD/RES/RPT objects.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re


SCHEMA = "guard-template-consumption-example/1"
ANALYSIS_SCHEMA = "guard-template-consumption-analysis/1"
MAX_BYTES = 65536
MAX_EVENTS = 64
MAX_REQUESTS = 4
MAX_DURATION_MS = 300000
_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"EXAMPLE-[A-Za-z0-9_.-]{1,64}")
_BINDING = {"nonce", "request_id", "subrequest_id", "input_sha256"}
_CONTEXT = {"container_sha256", "image_sha256", "process_id", "process_start",
            "model_snapshot_sha256", "tokenizer_snapshot_sha256", "model_generation",
            "tokenizer_generation", "renderer_generation"}
_KINDS = {
    "template_read": ("loader", {"load_id", "template_generation", "raw_sha256", "decoded_sha256",
                                  "raw_bytes", "decoded_bytes", "decode", "outcome", "source_kind"}),
    "template_selected": ("renderer", _BINDING | {"load_id", "selection_id", "selected_sha256",
                                                  "cache_status", "cache_generation", "source_kind"}),
    "template_applied": ("renderer", _BINDING | {"selection_id", "applied_sha256", "render_result",
                                                 "rendered_input_sha256"}),
    "engine_handoff": ("handoff", _BINDING | {"selection_id", "rendered_input_sha256"}),
}
_COMMON = {"event_id", "source_id", "seq", "elapsed_ms", "kind", "context"}


def _require(condition, code):
    if not condition:
        # Never echo untrusted keys, values, paths, or exception bodies.
        raise ValueError(code)


def _keys(value, expected):
    _require(type(value) is dict and set(value) == expected, "unexpected_or_missing_fields")


def _integer(value, maximum, minimum=0):
    _require(type(value) is int and minimum <= value <= maximum, "invalid_bounded_integer")


def _identifier(value):
    _require(type(value) is str and _ID.fullmatch(value) is not None, "invalid_example_identifier")


def _digest(value):
    _require(type(value) is str and _SHA.fullmatch(value) is not None, "invalid_digest")


def _choice(value, choices):
    _require(type(value) is str and value in choices, "unsupported_enum")


def _context(value):
    _keys(value, _CONTEXT)
    for key in _CONTEXT:
        if key.endswith("sha256"):
            _digest(value[key])
        elif key == "process_id":
            _integer(value[key], 2**31 - 1, 1)
        else:
            _identifier(value[key])


def _binding(value):
    for key in _BINDING:
        (_digest if key == "input_sha256" else _identifier)(value[key])


def _unique_json_object(pairs):
    value = {}
    for key, item in pairs:
        _require(key not in value, "duplicate_json_key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("invalid_json_constant")


def parse_example(data: bytes) -> dict:
    """Decode one bounded document, rejecting duplicate keys and unknown fields."""
    _require(type(data) is bytes and 0 < len(data) <= MAX_BYTES, "input_byte_limit")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_json_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_utf8_json") from None
    _validate(value)
    return value


def _validate(value):
    _keys(value, {"schema", "data_origin", "source_review", "collection", "instance",
                  "approval", "requests", "events"})
    _require(value["schema"] == SCHEMA and value["data_origin"] == "EXAMPLE", "example_only_schema")
    review = value["source_review"]
    _keys(review, {"source_commit", "image_sha256", "loader_sha256", "renderer_sha256", "handoff_sha256"})
    _require(review["source_commit"] == "UNVERIFIED", "unverified_source_commit_required")
    for key in set(review) - {"source_commit"}:
        _digest(review[key])
    _context(value["instance"])
    _require(value["instance"]["image_sha256"] == review["image_sha256"], "source_image_mismatch")
    collection = value["collection"]
    _keys(collection, {"session_id", "start_ms", "end_ms", "closed", "sources"})
    _identifier(collection["session_id"])
    _integer(collection["start_ms"], MAX_DURATION_MS)
    _integer(collection["end_ms"], MAX_DURATION_MS)
    _require(collection["start_ms"] <= collection["end_ms"], "invalid_time_window")
    _require(type(collection["closed"]) is bool, "invalid_collection_closed")
    sources = collection["sources"]
    _require(type(sources) is list and len(sources) == 3, "three_declared_sources_required")
    for source in sources:
        _keys(source, {"id", "role", "complete", "first_seq", "last_seq"})
        _identifier(source["id"])
        _choice(source["role"], {"loader", "renderer", "handoff"})
        _require(type(source["complete"]) is bool, "invalid_source_complete")
        _integer(source["first_seq"], MAX_EVENTS, 1)
        _integer(source["last_seq"], MAX_EVENTS)
    _require(len({source["id"] for source in sources}) == 3
             and {source["role"] for source in sources} == {"loader", "renderer", "handoff"},
             "duplicate_source_or_role")
    approval = value["approval"]
    _keys(approval, {"basis", "allowed_raw_sha256"})
    _require(approval["basis"] == "EXAMPLE-explicit-policy", "explicit_example_approval_required")
    hashes = approval["allowed_raw_sha256"]
    _require(type(hashes) is list and len(hashes) <= 16, "approval_list_limit")
    for digest in hashes:
        _digest(digest)
    _require(len(hashes) == len(set(hashes)), "duplicate_approval_digest")
    requests = value["requests"]
    _require(type(requests) is list and 1 <= len(requests) <= MAX_REQUESTS, "request_limit")
    for request in requests:
        _keys(request, _BINDING)
        _binding(request)
    for key in ("nonce", "request_id", "subrequest_id"):
        _require(len({request[key] for request in requests}) == len(requests), "duplicate_request_binding")
    events = value["events"]
    _require(type(events) is list and len(events) <= MAX_EVENTS, "event_limit")
    for event in events:
        _require(type(event) is dict, "invalid_event")
        kind = event.get("kind")
        _choice(kind, set(_KINDS))
        _keys(event, _COMMON | _KINDS[kind][1])
        for key in ("event_id", "source_id"):
            _identifier(event[key])
        _integer(event["seq"], MAX_EVENTS, 1)
        _integer(event["elapsed_ms"], MAX_DURATION_MS)
        _context(event["context"])
        if kind != "template_read":
            _binding(event)
        if kind == "template_read":
            for key in ("load_id", "template_generation"):
                _identifier(event[key])
            for key in ("raw_sha256", "decoded_sha256"):
                _digest(event[key])
            for key in ("raw_bytes", "decoded_bytes"):
                _integer(event[key], 8192, 1)
            _require(event["decoded_bytes"] <= event["raw_bytes"], "decoded_bytes_exceed_utf8_buffer")
            if event["raw_sha256"] == event["decoded_sha256"]:
                _require(event["raw_bytes"] == event["decoded_bytes"], "same_digest_different_byte_count")
            _choice(event["decode"], {"utf8-universal-newlines"})
            _choice(event["outcome"], {"loaded", "read_error", "parse_error"})
        elif kind == "template_selected":
            for key in ("load_id", "selection_id", "cache_generation"):
                _identifier(event[key])
            _digest(event["selected_sha256"])
            _choice(event["cache_status"], {"hit", "miss"})
        elif kind == "template_applied":
            _identifier(event["selection_id"])
            _digest(event["applied_sha256"])
            _choice(event["render_result"], {"success", "parse_error", "bypassed"})
            if event["rendered_input_sha256"] is not None:
                _digest(event["rendered_input_sha256"])
        else:
            _identifier(event["selection_id"])
            _digest(event["rendered_input_sha256"])
        if kind in {"template_read", "template_selected"}:
            _choice(event["source_kind"], {"managed_file"})


def analyze_example(value: dict) -> dict:
    """Correlate synthetic declarations only. No field can enable a real verdict."""
    _validate(value)
    gaps = set()
    collection, events = value["collection"], value["events"]
    sources = {source["id"]: source for source in collection["sources"]}
    by_request = {request["nonce"]: [] for request in value["requests"]}
    planned = {request["nonce"]: request for request in value["requests"]}
    seqs = {source_id: [] for source_id in sources}
    seen_ids, loads, selections = set(), {}, set()
    prior_ms = collection["start_ms"]
    if not collection["closed"]:
        gaps.add("COLLECTION_NOT_CLOSED")
    for index, event in enumerate(events):
        if event["event_id"] in seen_ids:
            gaps.add("DUPLICATE_EVENT")
        seen_ids.add(event["event_id"])
        source = sources.get(event["source_id"])
        if source is None or source["role"] != _KINDS[event["kind"]][0]:
            gaps.add("SOURCE_ROLE_MISMATCH")
        else:
            seqs[source["id"]].append(event["seq"])
        if event["context"] != value["instance"]:
            gaps.add("INSTANCE_OR_GENERATION_CHANGED")
        if not collection["start_ms"] <= event["elapsed_ms"] <= collection["end_ms"]:
            gaps.add("EVENT_OUTSIDE_WINDOW")
        if event["elapsed_ms"] < prior_ms:
            gaps.add("EVENT_OUT_OF_ORDER")
        prior_ms = event["elapsed_ms"]
        if event["kind"] == "template_read":
            if event["load_id"] in loads:
                gaps.add("DUPLICATE_LOAD")
            loads[event["load_id"]] = (index, event)
        else:
            nonce = event["nonce"]
            if nonce not in planned or any(event[key] != planned[nonce][key] for key in _BINDING):
                gaps.add("REQUEST_BINDING_MISMATCH")
            if nonce in by_request:
                by_request[nonce].append((index, event))
            if event["kind"] == "template_selected":
                if event["selection_id"] in selections:
                    gaps.add("DUPLICATE_SELECTION")
                selections.add(event["selection_id"])
    for source_id, source in sources.items():
        expected = list(range(source["first_seq"], source["last_seq"] + 1))
        if not source["complete"] or source["first_seq"] != 1 or not seqs[source_id] or seqs[source_id] != expected:
            gaps.add("SOURCE_GAP_OR_SEQUENCE")
    results = []
    for request_index, request in enumerate(value["requests"]):
        rows = by_request[request["nonce"]]
        chain_gaps = set()
        membership = None
        for _, event in rows:
            if any(event[key] != request[key] for key in _BINDING):
                chain_gaps.add("REQUEST_BINDING_MISMATCH")
            if event["context"] != value["instance"]:
                chain_gaps.add("INSTANCE_OR_GENERATION_CHANGED")
        if [event["kind"] for _, event in rows] != ["template_selected", "template_applied", "engine_handoff"]:
            chain_gaps.add("REQUEST_CHAIN_INCOMPLETE_OR_REORDERED")
        else:
            (selected_index, selected), (_, applied), (_, handoff) = rows
            loaded = loads.get(selected["load_id"])
            if loaded is None or loaded[0] >= selected_index:
                chain_gaps.add("SUCCESSFUL_READ_NOT_BEFORE_SELECTION")
            else:
                read = loaded[1]
                if read["context"] != value["instance"]:
                    chain_gaps.add("INSTANCE_OR_GENERATION_CHANGED")
                if read["outcome"] != "loaded":
                    chain_gaps.add("READ_OR_PARSE_FAILED")
                if read["decoded_sha256"] != selected["selected_sha256"]:
                    chain_gaps.add("READ_SELECTION_DIGEST_MISMATCH")
                if read["template_generation"] != selected["cache_generation"]:
                    chain_gaps.add("CACHE_GENERATION_MISMATCH")
                if read["context"] != selected["context"]:
                    chain_gaps.add("LOAD_SELECTION_INSTANCE_MISMATCH")
                membership = read["raw_sha256"] in value["approval"]["allowed_raw_sha256"]
            if not (selected["selection_id"] == applied["selection_id"] == handoff["selection_id"]):
                chain_gaps.add("SELECTION_HANDOFF_MISMATCH")
            if selected["selected_sha256"] != applied["applied_sha256"]:
                chain_gaps.add("SELECTED_APPLIED_DIGEST_MISMATCH")
            if applied["render_result"] != "success":
                chain_gaps.add("TEMPLATE_APPLY_NOT_SUCCESSFUL")
            if applied["rendered_input_sha256"] is None or applied["rendered_input_sha256"] != handoff["rendered_input_sha256"]:
                chain_gaps.add("RENDER_HANDOFF_DIGEST_MISMATCH")
        gaps.update(chain_gaps)
        results.append({"request_index": request_index, "chain_consistent": not chain_gaps,
                        "declared_raw_approval_membership": membership,
                        "gaps": sorted(chain_gaps)})
    return {"schema": ANALYSIS_SCHEMA, "data_origin": "EXAMPLE", "official": False,
            "data_consistency": "consistent" if not gaps else "inconsistent",
            "gaps": sorted(gaps), "events_seen": len(events), "requests": results,
            "source_commit": "UNVERIFIED", "independent_sensor_verified": False,
            "actual_consumption_measured": False, "template_consumer_verified": False,
            "challenge_exercised": False, "security": "UNKNOWN", "functional": "UNKNOWN",
            "performance": "UNKNOWN"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="bounded EXAMPLE JSON; no raw logs")
    args = parser.parse_args(argv)
    try:
        # Limit the read itself, not only the decoded object. Never copy input.
        with args.input.open("rb") as handle:
            result = analyze_example(parse_example(handle.read(MAX_BYTES + 1)))
    except (OSError, ValueError):
        print(json.dumps({"schema": ANALYSIS_SCHEMA, "data_origin": "EXAMPLE", "error": "invalid_example_input"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    # Exit zero means record consistency only; security is always UNKNOWN.
    return 0 if result["data_consistency"] == "consistent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
