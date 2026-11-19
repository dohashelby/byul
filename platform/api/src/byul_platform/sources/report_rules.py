"""Explain recorded verdicts against the exact archived check and policy.

This read-only projection never produces or changes a verdict. Keep operators in
sync with rule_table 0.5.0 before adding support for a new judge version.
"""

from __future__ import annotations

import hashlib
import json
import operator
import re
from pathlib import Path

import yaml

from ..contracts import canonical_sha256

_AXES = ("security", "functional", "performance")
_MISSING = object()
_ORDER = {"lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}


def _artifact(directory: Path, digest):
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        return None
    path = directory / digest
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            return None
        data = path.read_bytes()
        return data if hashlib.sha256(data).hexdigest() == digest else None
    except OSError:
        return None


def archived_manifest(directory: Path, ref):
    if not isinstance(ref, dict) or ref.get("kind") != "PACK":
        return {}
    data = _artifact(directory, ref.get("sha256"))
    try:
        manifest = json.loads(data) if data else {}
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(manifest, dict) or (manifest.get("pack_id"), manifest.get("revision")) != (ref.get("id"), ref.get("revision")):
        return {}
    return {row["path"]: row["sha256"] for row in manifest.get("files_sha256", [])
            if isinstance(row, dict) and isinstance(row.get("path"), str) and "sha256" in row}


def archived_check(directory: Path, files, check_id):
    data = _artifact(directory, files.get(f"checks/{check_id}.yaml"))
    try:
        check = yaml.safe_load(data) if data else {}
    except yaml.YAMLError:
        return {}
    return check if isinstance(check, dict) and check.get("id") == check_id else {}


def _same(a, b):
    return (not isinstance(a, bool) and not isinstance(b, bool) or type(a) is type(b)) and a == b


def _number(value):
    return type(value) in (int, float)


def _elements(value):
    if isinstance(value, dict):
        return value.values()
    return value if isinstance(value, list) else [value]


def _matches(op, actual, expected, other):
    """Same scalar/list semantics as the archived rule table for JSON observations."""
    try:
        if op in {"eq", "ne"}:
            return _same(actual, expected) if op == "eq" else actual is not None and not _same(actual, expected)
        if op in {"in", "not_in"}:
            return (actual is not None or op == "in") and any(_same(actual, value) for value in expected) == (op == "in")
        if op in _ORDER:
            return _number(actual) and _ORDER[op](actual, expected)
        if op in {"empty", "nonempty"}:
            return isinstance(actual, (list, dict, str)) and bool(actual) == (op == "nonempty")
        if op in {"all_in", "any_in", "none_in", "all_between", "any_between"}:
            if actual is None:
                return False
            if op.endswith("_between"):
                lo, hi = expected
                hits = [_number(value) and lo <= value <= hi for value in _elements(actual)]
            else:
                hits = [any(_same(value, allowed) for allowed in expected) for value in _elements(actual)]
            return all(hits) if op.startswith("all") else any(hits) != (op == "none_in")
        if op == "superset_of":
            return set(actual) >= set(other)
        if op in {"subset_of", "not_subset_of"}:
            return (set(actual) <= set(expected)) == (op == "subset_of")
    except (TypeError, ValueError):
        return False
    return None


def explain_rules(check, result, evd, policy):
    public_judge = result.get("judge", {})
    definition = check.get("judge", {})
    params = definition.get("params") or {}
    if (definition.get("plugin"), public_judge.get("name"), public_judge.get("version")) != ("rule_table", "rule_table", "0.5.0"):
        return {}
    if not isinstance(params, dict) or canonical_sha256(params) != public_judge.get("params_sha256"):
        return {}

    values = policy.get("values", {}).get(check["id"], {})
    evidence = evd.get("observations", {})
    verdicts = result.get("verdicts", {})
    traces = {}
    for axis in _AXES:
        verdict = verdicts.get(axis)
        table = params if axis == "security" else (params.get("axes") or {}).get(axis, {})
        pass_rules = table.get("pass_when_all", [])
        fail_rules = table.get("fail_when_any", [])
        trace = {"observations": [], "operators": {}, "missing": [], "unmet": [], "policy_missing": [], "limits": []}

        def observed(key):
            row = evidence.get(key, {})
            if row.get("collected") is True:
                return row.get("value")
            trace["missing"].append(key)
            return _MISSING

        def evaluate(rule):
            expected = rule.get("value")
            if "value_policy" in rule:
                key = rule["value_policy"]
                if key not in values:
                    trace["policy_missing"].append(key)
                    return None
                expected = values[key]
            actual = observed(rule["obs"])
            other = observed(rule["value_obs"]) if "value_obs" in rule else None
            if actual is _MISSING or other is _MISSING:
                return None
            return _matches(rule["op"], actual, expected, other)

        def premise(rule):
            if "when_policy" in rule:
                key = rule["when_policy"]
                if key not in values:
                    trace["policy_missing"].append(key)
                    return None
                if values[key] is not True:
                    return False
            return evaluate(rule["when"]) if rule.get("when") else True

        def match(rule):
            key = rule["obs"]
            trace["observations"].append(key)
            trace["operators"].setdefault(key, []).append(rule["op"])

        hits = [rule for rule in fail_rules if premise(rule) is True and evaluate(rule) is True]
        if verdict == "FAIL":
            for rule in hits:
                match(rule)
            if hits:
                trace["criterion"] = check.get("fail_criteria") if axis == "security" else None
                traces[axis] = trace
            continue
        if hits:
            continue
        complete = bool(pass_rules)
        for rule in pass_rules:
            precondition = premise(rule)
            if precondition is False:
                continue
            outcome = evaluate(rule) if precondition is True else None
            if outcome is True:
                match(rule)
            else:
                complete = False
                if outcome is False:
                    trace["unmet"].append(rule["obs"])
        if verdict == "PASS" and complete:
            trace["criterion"] = check.get("satisfied_if") if axis == "security" else None
            traces[axis] = trace
        elif verdict == "UNKNOWN":
            if axis == "performance" and evd.get("comparability", {}).get("cpu") is not None:
                trace["limits"].append("cpu_performance_unverified")
            elif not pass_rules:
                trace["limits"].append("no_pass_rule" if fail_rules or table else "no_rule")
            elif complete:
                if evd.get("execution", {}).get("status") != "COMPLETED":
                    trace["limits"].append("execution_incomplete")
                normal = evd.get("normal_control", {})
                if not normal.get("attempted") or normal.get("succeeded") is not True:
                    trace["limits"].append("normal_control_unverified")
                attempted = {row["step"] for row in evd.get("challenges", []) if row.get("attempted")}
                if set(check.get("challenge_steps") or []) - attempted:
                    trace["limits"].append("challenge_incomplete")
            traces[axis] = trace
    return traces
