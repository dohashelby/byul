"""Resolve the exact Guard inputs needed by the report display."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..contracts import canonical_sha256


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


def enrich_report(report, vr, store: Path | None):
    if store is None or not store.is_file():
        return
    try:
        with closing(sqlite3.connect(store.resolve().as_uri() + "?mode=ro", uri=True)) as conn:
            _enrich(conn, report, vr)
    except sqlite3.Error:
        # Old reports remain readable when the optional object store is unavailable.
        return


def _enrich(conn, report, vr):
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
            if check["check_id"] == "SUP-03":
                observations = evd.get("observations", {})
                view["unverified_observations"] = [
                    key for key in ("template_consumer_verified", "supply_scope_complete")
                    if observations.get(key, {}).get("collected") is True
                    and observations[key].get("value") is False
                ]
            if check["check_id"] == "SUP-08" and view["security"] == "FAIL":
                view["finding"] = _import_violation(evd)
