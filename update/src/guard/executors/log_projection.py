"""OBS-03 decision-preserving storage projection, never original log text.

The existing strict parser/analyzer reads both the transient source and the
stored projection. Source byte digests are provenance, not source authentication.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import itertools
import json
import re

from .log_analysis_contract import REJECTED_JSON_LINE, STRICT_JSON_PROFILE, analyze_log_captures, events

PROFILE = "guard-log-predicate-projection/1"
PUBLIC_MARKERS = {"caller_input": "GP_CALLER", "private_system": "GP_SYSTEM", "error_input": "GP_ERROR"}
PUBLIC_TRIALS = {"normal": "GP_TRIAL_NORMAL", "error": "GP_TRIAL_ERROR"}
NONEMPTY = "GP_STRING"
INVALID_TIME = "2000-01-01TINVALID"
_SEMANTIC_KEYS = {"request_id", "exception", "traceback", "exception_dump", "dump", "debug", "trace"}
_HEX = re.compile(r"[0-9a-f]{64}")
MAX_ARTIFACT_BYTES = 16 * 1024**2  # Existing saved-log artifact read budget.


def fingerprint(value: str) -> str:
    """Logical string fingerprint; surrogatepass handles rejected Unicode keys."""
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()


def _parts(path):
    result, index = [], 0
    decoder = json.JSONDecoder()
    while index < len(path):
        if path.startswith("[]", index):
            result.append(None)
            index += 2
        elif path.startswith('["', index):
            name, size = decoder.raw_decode(path[index + 1:])
            index += size + 1
            if index >= len(path) or path[index] != "]" or not isinstance(name, str):
                raise ValueError("LOG_PROJECTION_PATH_INVALID")
            result.append(name)
            index += 1
        else:
            if path[index] == ".":
                index += 1
            start = index
            while index < len(path) and path[index] not in ".[]":
                index += 1
            if start == index:
                raise ValueError("LOG_PROJECTION_PATH_INVALID")
            result.append(path[start:index])
    return result


def _join(prefix, name):
    if not name or any(c in name for c in ".[]"):
        return prefix + "[" + json.dumps(name, ensure_ascii=False) + "]"
    return prefix + "." + name if prefix else name


class FieldProjection:
    def __init__(self, values):
        fields = [*values["allowed_log_fields"], *values["forbidden_log_fields"]]
        self.known = set()
        for path in fields:
            try:
                self.known.update(name for name in _parts(path) if name is not None)
            except (ValueError, TypeError):
                # An unmatchable policy string does not grant any extra field.
                continue
        self.namespace = "GF_"
        while any(self.namespace in field for field in fields):
            self.namespace = "_" + self.namespace

    def key(self, name):
        if name in self.known or name.lower() in _SEMANTIC_KEYS:
            return name
        special = not name or any(c in name for c in ".[]")
        return self.namespace + fingerprint(name) + ("\ufffd" if "\ufffd" in name else "") + ("." if special else "")

    def path(self, path):
        result = ""
        for name in _parts(path):
            result = result + "[]" if name is None else _join(result, self.key(name))
        return result

    def valid_key(self, name):
        return (name in self.known or name.lower() in _SEMANTIC_KEYS
                or re.fullmatch(re.escape(self.namespace) + r"[0-9a-f]{64}\ufffd?\.?", name) is not None)


def protect_analysis(analysis, values):
    """Retain policy-named fields; hash unlisted field names without granting them."""
    rules = FieldProjection(values)
    mapping = {path: rules.path(path) for path in analysis["log_fields_seen"]}
    aliases = {f"{source}: nonpermitted log field {path}": f"{source}: nonpermitted log field {alias}"
               for source in values["required_sources"] for path, alias in mapping.items()}
    return {**analysis, "log_fields_seen": sorted(set(mapping.values())),
            "log_violations": sorted({aliases.get(item, item) for item in analysis["log_violations"]})}


def _matches(marker, value):
    return re.search(r"(?<![A-Za-z0-9_])" + re.escape(marker) + r"(?![A-Za-z0-9_])", value) is not None


def _binding(marks, correlation):
    if (set(marks) != set(PUBLIC_MARKERS) or set(correlation) != set(PUBLIC_TRIALS)
            or any(not isinstance(v, str) or not v.strip() for v in [*marks.values(), *correlation.values()])
            or correlation["normal"] == correlation["error"]):
        raise ValueError("LOG_PROJECTION_BINDING_INVALID")
    trials = {branch: " ".join([base, *[PUBLIC_MARKERS[role] for role in PUBLIC_MARKERS
                                      if _matches(marks[role], correlation[branch])]])
              + ("\ufffd" if "\ufffd" in correlation[branch] else "")
              for branch, base in PUBLIC_TRIALS.items()}
    return trials


def _string(value, marks, correlation, public_trials):
    for branch, original in correlation.items():
        if value == original:
            return public_trials[branch]
    roles = [PUBLIC_MARKERS[role] for role in PUBLIC_MARKERS if _matches(marks[role], value)]
    result = " ".join(roles) if roles else NONEMPTY if value.strip() else ""
    return result + ("\ufffd" if "\ufffd" in value else "")


def _tree(value, rules, marks, correlation, public_trials):
    if isinstance(value, dict):
        return {rules.key(key): _tree(child, rules, marks, correlation, public_trials) for key, child in value.items()}
    if isinstance(value, list):
        # Arrays convey leaf paths/membership, never original token values/counts.
        unique = {}
        for child in value:
            projected = _tree(child, rules, marks, correlation, public_trials)
            unique.setdefault(json.dumps(projected, ensure_ascii=True, sort_keys=True), projected)
        return list(unique.values())
    if isinstance(value, str):
        return _string(value, marks, correlation, public_trials)
    return None


def _timestamp(value):
    if value is None:
        return None
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if point.tzinfo is not None:
            return point.isoformat()
    except (TypeError, ValueError):
        pass
    return INVALID_TIME + ("\ufffd" if isinstance(value, str) and "\ufffd" in value else "")


def _json_line(value, original_line):
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    # Python splitlines also recognizes these characters inside JSON strings.
    # Escape them; retain the source's actual decoding-replacement signal.
    return "".join((f"\\u{ord(c):04x}" if c in "\x85\u2028\u2029" or 0xd800 <= ord(c) <= 0xdfff
                    or (c == "\ufffd" and "\ufffd" not in original_line) else c) for c in text)


def _text(text, rules, marks, correlation, public_trials):
    lines, rejected = [], 0
    for line in text.splitlines():
        if not line.strip():
            continue
        rows, valid = events(line, profile=STRICT_JSON_PROFILE)
        if not valid:
            rejected += 1
            lines.append(REJECTED_JSON_LINE + ("\ufffd" if "\ufffd" in line else ""))
            continue
        for row in rows:
            projected = _tree(row["event"], rules, marks, correlation, public_trials)
            stamp = _timestamp(row["timestamp"])
            lines.append((stamp + " " if stamp else "") + _json_line(projected, line))
    return "\n".join(lines) + ("\n" if lines else ""), rejected


def _identifier(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", value) else None


def _capture(capture, rules, marks, correlation, public_trials):
    source = capture["source"]
    projected = {"source": {key: source.get(key) for key in ("id", "kind", "role", "container")},
                 "window": {key: (capture["window"].get(key) if isinstance(capture["window"].get(key), str)
                    and not _timestamp(capture["window"][key]).startswith(INVALID_TIME) else INVALID_TIME)
                    for key in ("since", "until")},
                 "returncode": capture.get("returncode") if type(capture.get("returncode")) is int else None,
                 "stored_capture_is_verbatim": False, "projection_profile": PROFILE}
    projected["source"]["role"] = source.get("role", "engine")
    projected["source"]["container"] = _identifier(source.get("container"))
    if "path" in source:
        projected["source"]["path_sha256"] = fingerprint(source["path"])
    identity = capture.get("identity", {})
    projected["identity"] = {key: _identifier(identity.get(key)) for key in ("container_id", "image_id")}
    stamp = _timestamp(identity.get("started_at"))
    projected["identity"]["started_at"] = None if stamp and stamp.startswith(INVALID_TIME) else stamp
    for key in ("identity_stable", "file_identity_stable", "source_complete", "truncated", "timed_out"):
        if key in capture:
            projected[key] = capture[key] if type(capture[key]) is bool else None
    for key in ("raw_retained_sha256", "unredacted_source_capture_sha256"):
        value = capture.get(key)
        projected[key] = value if isinstance(value, str) and _HEX.fullmatch(value) else None
    value = capture.get("raw_retained_bytes")
    projected["raw_retained_bytes"] = value if type(value) is int and value >= 0 else None
    projected["rejected_json_lines"] = {}
    for field in ("text", "stderr_text"):
        if field not in capture:
            continue
        value = capture[field]
        text, rejected = _text(value, rules, marks, correlation, public_trials)
        projected["rejected_json_lines"][field] = rejected
        projected[field] = text
        projected[field + "_projection_sha256"] = fingerprint(text)
        projected[field + "_projection_bytes"] = len(text.encode("utf-8"))
        projected[field + "_original_sha256"] = fingerprint(value)
        projected[field + "_original_bytes"] = len(value.encode("utf-8"))
    return projected


def project_log_captures(captures, values, marks, correlation, *, initial_gaps=(), analysis=None):
    """Project every decision input, then check it with the original analyzer."""
    rules = FieldProjection(values)
    public_trials = _binding(marks, correlation)
    original = analysis if analysis is not None else analyze_log_captures(
        captures, values, marks, correlation, initial_gaps=initial_gaps, profile=STRICT_JSON_PROFILE)
    projected = [_capture(capture, rules, marks, correlation, public_trials) for capture in captures]
    replayed = analyze_log_captures(projected, values, PUBLIC_MARKERS, public_trials,
                                   initial_gaps=initial_gaps, profile=STRICT_JSON_PROFILE)
    if protect_analysis(original, values) != replayed:
        raise ValueError("LOG_PROJECTION_PREDICATES_DIFFER")
    return {"captures": projected, "analysis": replayed, "markers": dict(PUBLIC_MARKERS),
            "correlation": public_trials, "field_namespace": rules.namespace,
            "marker_fingerprints": {role: fingerprint(value) for role, value in marks.items()},
            "correlation_fingerprints": {role: fingerprint(value) for role, value in correlation.items()}}


def validate_projection(sample, values):
    """Validate stored projection grammar/bytes; never claim raw-source replay."""
    try:
        _validate_projection(sample, values)
    except (TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise ValueError("saved log projection shape differs") from None


def _validate_projection(sample, values):
    rules = FieldProjection(values)
    expected = {"schema", "analysis_profile", "projection_profile", "captures", "analysis", "markers", "correlation",
                "field_namespace", "marker_fingerprints", "correlation_fingerprints", "requests", "initial_gaps",
                "coverage_gaps", "controls", "policy_values", "source_target", "window"}
    if (set(sample) != expected or sample.get("schema") != "guard-log-analysis-capture/4"
            or sample.get("analysis_profile") != STRICT_JSON_PROFILE
            or sample.get("projection_profile") != PROFILE or sample.get("field_namespace") != rules.namespace
            or sample.get("markers") != PUBLIC_MARKERS):
        raise ValueError("saved log projection profile differs")
    if (set(sample["window"]) != {"since", "until"}
            or any(not isinstance(value, str) or (_timestamp(value).startswith(INVALID_TIME)
                   and value not in {INVALID_TIME, INVALID_TIME+"\ufffd"}) for value in sample["window"].values())
            or sample["source_target"] is not None and sample["source_target"] != _identifier(sample["source_target"])):
        raise ValueError("saved log projection window or target differs")
    if (not isinstance(sample["controls"], dict) or "before" not in sample["controls"]
            or set(sample["controls"]) - {"before", "after"}
            or any(state not in {"pass", "fail", "no_response"} for state in sample["controls"].values())
            or not isinstance(sample["requests"], list) or not 1 <= len(sample["requests"]) <= 3
            or len({row["case"] for row in sample["requests"]}) != len(sample["requests"])):
        raise ValueError("saved log projection request inventory differs")
    roles = list(PUBLIC_MARKERS.values())
    combinations = [" ".join(group) for length in range(1, len(roles) + 1)
                    for group in itertools.combinations(roles, length)]
    strings = {"", NONEMPTY, *combinations}
    correlation = sample.get("correlation", {})
    if set(correlation) != set(PUBLIC_TRIALS):
        raise ValueError("saved log projection trial inventory differs")
    for branch, base in PUBLIC_TRIALS.items():
        allowed = {base, *[base + " " + group for group in combinations]}
        allowed |= {value + "\ufffd" for value in allowed}
        if correlation[branch] not in allowed:
            raise ValueError("saved log projection trial identity differs")
    strings.update(correlation.values())
    strings |= {value + "\ufffd" for value in strings}
    for field, expected in (("marker_fingerprints", set(PUBLIC_MARKERS)),
                            ("correlation_fingerprints", set(PUBLIC_TRIALS))):
        fingerprints = sample.get(field, {})
        if set(fingerprints) != expected or any(not isinstance(v, str) or not _HEX.fullmatch(v) for v in fingerprints.values()):
            raise ValueError("saved log projection binding fingerprint differs")
    for request in sample["requests"]:
        branch = "error" if request.get("case") == "safe_error" else "normal"
        outcome = request.get("outcome")
        if (set(request) != {"case", "trial", "request_fixture_sha256", "request_id_sha256", "outcome"}
                or request.get("case") not in {"normal", "safe_error", "after"} or request["trial"] != branch
                or request["request_id_sha256"] != sample["correlation_fingerprints"][branch]
                or not isinstance(request["request_fixture_sha256"], str) or not _HEX.fullmatch(request["request_fixture_sha256"])
                or not (type(outcome) is int and 100 <= outcome <= 599 or type(outcome) is str and outcome in {
                    "refused", "timeout", "unreachable", "dns_error", "error", "probe_error"})):
            raise ValueError("saved log projection request binding differs")
    def validate_tree(value):
        if isinstance(value, dict):
            if any(not rules.valid_key(key) for key in value):
                raise ValueError("saved log projection has unlisted field text")
            for child in value.values(): validate_tree(child)
        elif isinstance(value, list):
            for child in value: validate_tree(child)
        elif value is not None and (not isinstance(value, str) or value not in strings):
            raise ValueError("saved log projection has unlisted scalar text")
    captures = sample.get("captures")
    if not isinstance(captures, list) or len(captures) > 16:
        raise ValueError("saved log projection source inventory differs")
    for capture in captures:
        required = {"source", "identity", "window", "returncode", "stored_capture_is_verbatim", "projection_profile",
                    "raw_retained_sha256", "unredacted_source_capture_sha256", "raw_retained_bytes", "rejected_json_lines"}
        for field in ("text", "stderr_text"):
            if field == "text" or field in capture:
                required.update({field, field+"_projection_sha256", field+"_projection_bytes",
                                 field+"_original_sha256", field+"_original_bytes"})
        flags = {"identity_stable", "file_identity_stable", "source_complete", "truncated", "timed_out"}
        if (set(capture) - flags != required or capture.get("projection_profile") != PROFILE
                or capture.get("stored_capture_is_verbatim") is not False
                or any(capture[key] is not None and type(capture[key]) is not bool for key in flags & set(capture))
                or capture["returncode"] is not None and type(capture["returncode"]) is not int):
            raise ValueError("saved log projection capture profile differs")
        if capture["window"] != sample["window"]:
            raise ValueError("saved projection capture window differs")
        if (set(capture["rejected_json_lines"]) != {field for field in ("text", "stderr_text") if field in capture}
                or any(type(count) is not int or count < 0 for count in capture["rejected_json_lines"].values())):
            raise ValueError("saved log projection rejected-row inventory differs")
        source = capture["source"]
        if (set(source) != {"id", "kind", "role", "container"} | ({"path_sha256"} if source.get("kind") == "container_file" else set())
                or source.get("kind") not in {"container_file", "docker_logs"} or source.get("role") not in {"engine", "copy"}
                or not isinstance(source.get("id"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", source["id"])
                or source.get("container") is not None and source["container"] != _identifier(source["container"])
                or source.get("kind") == "container_file" and (not isinstance(source.get("path_sha256"), str)
                    or not _HEX.fullmatch(source["path_sha256"]))):
            raise ValueError("saved log projection source metadata differs")
        identity = capture["identity"]
        if (set(identity) != {"container_id", "image_id", "started_at"}
                or any(value is not None and value != _identifier(value) for key, value in identity.items() if key != "started_at")
                or identity["started_at"] is not None and identity["started_at"] != _timestamp(identity["started_at"])):
            raise ValueError("saved log projection identity metadata differs")
        for key in ("raw_retained_sha256", "unredacted_source_capture_sha256"):
            if capture[key] is not None and (not isinstance(capture[key], str) or not _HEX.fullmatch(capture[key])):
                raise ValueError("saved log projection source fingerprint differs")
        if capture["raw_retained_bytes"] is not None and (type(capture["raw_retained_bytes"]) is not int or capture["raw_retained_bytes"] < 0):
            raise ValueError("saved log projection source byte count differs")
        for field in ("text", "stderr_text"):
            if field not in capture: continue
            text = capture[field]
            if (not isinstance(text, str) or fingerprint(text) != capture.get(field + "_projection_sha256")
                    or len(text.encode("utf-8")) != capture.get(field + "_projection_bytes")
                    or not isinstance(capture[field + "_original_sha256"], str)
                    or not _HEX.fullmatch(capture[field + "_original_sha256"])
                    or type(capture[field + "_original_bytes"]) is not int or capture[field + "_original_bytes"] < 0):
                raise ValueError("saved log projection bytes differ")
            rejected = 0
            for line in text.splitlines():
                if line in {REJECTED_JSON_LINE, REJECTED_JSON_LINE + "\ufffd"}:
                    rejected += 1
                    continue
                rows, valid = events(line, profile=STRICT_JSON_PROFILE)
                if not valid or len(rows) != 1:
                    raise ValueError("saved log projection row differs")
                stamp = rows[0]["timestamp"]
                if stamp is not None and stamp != _timestamp(stamp):
                    raise ValueError("saved log projection timestamp differs")
                validate_tree(rows[0]["event"])
            if rejected != capture["rejected_json_lines"][field]:
                raise ValueError("saved log projection rejected-row count differs")
