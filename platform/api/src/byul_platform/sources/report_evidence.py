"""Resolve the exact Guard inputs needed by the report display."""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path

from ..contracts import canonical_sha256
from .report_rules import archived_check, archived_manifest, explain_rules


def _object(conn, ref, kind):
    if not isinstance(ref, dict) or ref.get("kind") != kind:
        return {}
    oid, revision, sha = (ref.get(key) for key in ("id", "revision", "sha256"))
    if not isinstance(oid, str) or type(revision) is not int or not isinstance(sha, str):
        return {}
    row = conn.execute(
        "SELECT body FROM objects WHERE id=? AND revision=? AND kind=? AND content_sha256=?",
        (oid, revision, kind, sha),
    ).fetchone()
    if not row:
        return {}
    try:
        body = json.loads(row[0])
    except ValueError:
        return {}
    if not isinstance(body, dict) or body.pop("content_sha256", None) != sha:
        return {}
    if (body.get("id"), body.get("revision"), body.get("kind")) != (oid, revision, kind):
        return {}
    return body if canonical_sha256(body) == sha else {}


def _import_violation(evd):
    """Require the structured import witness, including a matching module digest."""
    if evd.get("data_origin") != "MEASURED" or evd.get("execution", {}).get("status") != "COMPLETED":
        return None
    obs = evd.get("observations", {}).get("code_selection_confirmed_violations", {})
    if obs.get("collected") is not True or not isinstance(obs.get("value"), list):
        return None
    for trial in obs["value"]:
        if not isinstance(trial, dict):
            continue
        revision, field = trial.get("submitted_revision"), trial.get("field")
        if not isinstance(revision, str) or not isinstance(field, str):
            continue
        markers, modules = trial.get("execution_markers"), trial.get("module_files")
        if not isinstance(markers, dict) or not isinstance(modules, dict):
            continue
        marker, files = markers.get(revision), modules.get(revision)
        if not isinstance(marker, dict) or not isinstance(files, dict):
            continue
        digest = marker.get("module_sha256")
        if (marker.get("schema") == "guard-code-import-marker/1" and marker.get("revision") == revision
                and isinstance(digest, str) and len(digest) == 64
                and any(isinstance(f, dict) and f.get("sha256") == digest for f in files.values())):
            return {"code": "forbidden_code_revision_imported", "field": field, "revision": revision}
    return None


# These observations correspond to explicit failure rules in the check definitions.
_SECURITY_FINDINGS = {
    "PLT-02": ("unix_ipc_violations", "forbidden_ipc_connection"),
    "PLT-07": ("forbidden_egress_successes", "forbidden_egress_succeeded"),
    "PLT-08": ("forbidden_plaintext_successes", "forbidden_plaintext_succeeded"),
    "SUP-13": ("forbidden_template_type_reference_verified", "forbidden_template_reference"),
    "SUP-04": ("running_image_unapproved", "unapproved_running_image"),
    "OBS-09": ("log_security_violations", "untrusted_input_changed_log"),
}

_SCOPE_OBSERVATIONS = {
    "SUP-03": ("template_consumer_verified", "supply_scope_complete"),
    "SUP-04": ("supply_execution_scope_complete",),
    "SUP-13": ("template_render_witness_verified", "template_sandbox_scope_complete"),
    "PLT-02": ("ipc_scope_complete",),
    "PLT-07": ("egress_scope_complete",),
    "PLT-08": ("api_transport_scope_complete",),
}


def _observed(evd, key):
    obs = evd.get("observations", {}).get(key, {})
    return obs.get("value") if obs.get("collected") is True else None


def _finding(check_id, view, result, evd):
    if evd.get("execution", {}).get("status") != "COMPLETED" or evd.get("execution", {}).get("operator") is not None:
        return None
    verdicts = result.get("verdicts", {})
    if view["security"] == verdicts.get("security") == "FAIL":
        if check_id == "SUP-08":
            return _import_violation(evd)
        if rule := _SECURITY_FINDINGS.get(check_id):
            observation, code = rule
            value = _observed(evd, observation)
            if check_id in {"SUP-04", "SUP-13"}:
                if value is True:
                    return {"code": code}
            elif isinstance(value, list) and value and all(isinstance(v, str) and v for v in value):
                return {"code": code}

    # The functional finding describes the recorded normal probes, within their measured scope.
    planned = _observed(evd, "functional_probes_planned")
    outcomes = _observed(evd, "functional_probe_results")
    if not isinstance(planned, list) or not planned or not isinstance(outcomes, dict):
        return None
    if not all(isinstance(key, str) for key in planned):
        return None
    functional = view["functional"]
    if functional != verdicts.get("functional"):
        return None
    if functional == "PASS" and all(outcomes.get(key) == "pass" for key in planned):
        return {"code": "normal_probes_passed"}
    if functional == "FAIL" and any(outcomes.get(key) == "fail" for key in planned):
        return {"code": "normal_probe_failed"}
    return None


def _observation_summary(evd, key):
    value = _observed(evd, key)
    if value is None:
        return None
    if type(value) is bool:
        return {"key": key, "kind": "boolean", "value": value}
    if isinstance(value, (list, dict)):
        return {"key": key, "kind": "count", "value": len(value)}
    if type(value) is int or (type(value) is float and math.isfinite(value)):
        return {"key": key, "kind": "number", "value": value}
    return {"key": key, "kind": "recorded"}


def _decisions(view, result, evd, traces=None):
    if traces is None:
        traces = result.get("decision_trace")
    if not isinstance(traces, dict) or evd.get("execution", {}).get("operator") is not None:
        return []
    verdicts = result.get("verdicts", {})
    decisions = []
    for axis in ("security", "functional", "performance"):
        trace = traces.get(axis)
        verdict = view.get(axis)
        if not isinstance(trace, dict) or verdict not in {"PASS", "FAIL", "UNKNOWN"} or verdict != verdicts.get(axis):
            continue

        def keys(name):
            values = trace.get(name, [])
            return list(dict.fromkeys(k for k in values if isinstance(k, str))) if isinstance(values, list) else []

        observations = [
            summary for key in keys("observations")
            if (summary := _observation_summary(evd, key)) is not None
        ] if verdict != "UNKNOWN" else []
        operators = trace.get("operators", {})
        if isinstance(operators, dict):
            for observation in observations:
                matched = operators.get(observation["key"])
                if isinstance(matched, list):
                    observation["operators"] = list(dict.fromkeys(op for op in matched if isinstance(op, str)))
        if verdict != "UNKNOWN" and not observations:
            continue
        decisions.append({
            "axis": axis, "verdict": verdict, "observations": observations,
            "criterion": trace.get("criterion") if verdict != "UNKNOWN" else None,
            **{key: keys(key) for key in ("missing", "unmet", "policy_missing", "limits")},
        })
    return decisions


def enrich_report(report, vr, store: Path | None):
    if store is None or not store.is_file():
        return
    try:
        with closing(sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            _enrich(conn, report, vr, store.parent / "artifacts")
    except sqlite3.Error:
        # Old reports remain readable when the optional object store is unavailable.
        return


def _enrich(conn, report, vr, artifacts):
    from functools import lru_cache

    @lru_cache(maxsize=16)
    def manifest(pack_id, revision, sha):
        return archived_manifest(artifacts, {"kind": "PACK", "id": pack_id, "revision": revision, "sha256": sha})
    sides = ("baseline", "candidate") if report["mode"] == "update" else ("single",)
    plan = _object(conn, vr.get("plan_ref"), "PLAN")
    refs = plan.get("inputs", {}).get("environments", [])
    if len(refs) == len(sides):
        for side, ref in zip(sides, refs, strict=True):
            if ref not in vr["environment_refs"]:
                continue
            engine = _object(conn, ref, "ENV").get("engine", {})
            if isinstance(engine.get("name"), str) and isinstance(engine.get("version"), str):
                report["targets"][side] = {"name": engine["name"], "version": engine["version"]}
    for check, source in zip(report["checks"], vr["checks"], strict=True):
        for side in sides:
            view = check[side]
            if view is None:
                continue
            ref = source["sides"][side].get("result_ref")
            result = _object(conn, ref, "RES")
            if result.get("check_id") != check["check_id"] or result.get("side") != side:
                continue
            missing = result.get("missing_observations", [])
            view["missing_observations"] = [key for key in missing if isinstance(key, str)]
            evd_ref = result.get("evidence")
            evd = _object(conn, evd_ref, "EVD")
            if evd.get("check_id") != check["check_id"] or evd.get("side") != side:
                continue
            view["evidence_ref"] = evd_ref
            view["unverified_observations"] = [
                key for key in _SCOPE_OBSERVATIONS.get(check["check_id"], ())
                if _observed(evd, key) is False
            ]
            view["finding"] = _finding(check["check_id"], view, result, evd)
            evidence_plan = _object(conn, evd.get("plan"), "PLAN")
            inputs = evidence_plan.get("inputs", {})
            ref = inputs.get("pack") or {}
            policy_ref = inputs.get("policy")
            traces = None
            if ref.get("kind") == "PACK" and policy_ref in result.get("refs", []):
                files = manifest(ref.get("id"), ref.get("revision"), ref.get("sha256"))
                definition = archived_check(artifacts, files, check["check_id"])
                policy = _object(conn, policy_ref, "POL")
                if definition and policy:
                    traces = explain_rules(definition, result, evd, policy)
            view["decisions"] = _decisions(view, result, evd, traces)
