"""Read-only replay of pinned OBS-03/09 artifacts, never a new engine run.

An accepted audit means the saved redacted capture, observations and fixed
rule-table verdicts agree. It does not mean security PASS, authenticate a host,
recover redacted source bytes or amend any original result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sqlite3
from types import SimpleNamespace

from guard.core.judge import pass_gate
from guard.executors import kubernetes_log_analysis, passive_api_hop, run_binding
from guard.executors.log_analysis_contract import LEGACY_JSON_PROFILE, STRICT_JSON_PROFILE, analyze_log_captures, strict_json_loads
from guard.executors.log_integrity_contract import PLANNED_CASES, analyze_integrity_capture
from guard.executors.log_integrity_parser import PROFILE, TRUSTED_FIELDS
from guard.executors.log_projection import PROFILE as LOG_PROJECTION_PROFILE, validate_projection
from guard.executors.native_projection import (SCHEMA as NATIVE_SCHEMA, PROFILE as NATIVE_PROJECTION_PROFILE,
    ENCODING as NATIVE_ENCODING, replay_native, render_projection)
from guard.judges.rule_table import RuleTableJudge
from guard.models import Ref, canonical_json, sha256_hex, weakest_origin
from guard.models.objects import object_from_dict
from guard.report.log_integrity import render_log_integrity, verify_log_display
from guard.validation_evidence import _ARTIFACT, _DIGEST, _directory, _unlinked, inventory_tree, read_objects, verify_store_files

SCHEMA = "guard-log-evidence-audit/1"
CHECKS = {"OBS-03": "log_analysis", "OBS-09": "log_integrity"}
_NATIVE_PROJECTION_VERSIONS = frozenset({"0.3.0", "0.3.1", "0.3.1+k8s.1", "0.3.1+k8s.2"})
MAX_JSON_BYTES = 16 * 1024**2


def _key(ref):
    return ref.kind, ref.id, ref.revision, ref.sha256


def _artifact(store, evd, name):
    """Return the exact sealed attachment bytes for replay."""
    matches = [item for item in evd.artifacts if item.name == name]
    if len(matches) != 1:
        raise ValueError("one pinned named log artifact is required")
    item = matches[0]
    if type(item.bytes) is not int or not 0 <= item.bytes <= MAX_JSON_BYTES:
        raise ValueError("log replay artifact exceeds its read budget")
    if _ARTIFACT.fullmatch(item.path) is None or item.path != f"artifacts/{item.sha256}":
        raise ValueError("log replay artifact reference differs")
    path = store / item.path
    _directory(path.parent)
    _unlinked(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_size != item.bytes
                or getattr(before, "st_file_attributes", 0) & 0x400):
            raise ValueError("log replay artifact bytes differ from pinned evidence")
        data = stream.read(item.bytes + 1)
        after = os.fstat(stream.fileno())
    if (len(data) != item.bytes or sha256_hex(data) != item.sha256
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("log replay artifact bytes differ from pinned evidence")
    return data


def _normal_obs03_metadata(store, evd, values):
    producer = kubernetes_log_analysis
    artifacts = evd.artifacts
    if len(artifacts) != 1 or artifacts[0].collector != f"log_analysis/{producer.KubernetesLogAnalysisExecutor.version}":
        raise ValueError("saved ordinary log attachment differs from its writer")
    try:
        summary = strict_json_loads(_artifact(store, evd, "probe_log.jsonl").decode("utf-8"))
    except (ValueError, RecursionError):
        raise ValueError("saved ordinary log summary is invalid") from None
    fixture = {"profile": producer.PROFILE, "target_source": "owned-engine-pod-stdout",
               "logical_request": producer.LOGICAL_ID, "policy_sha256": sha256_hex(canonical_json(values))}
    if (type(summary) is not dict or set(summary) != {
            "summary", "topology", "lab", "profile", "source", "fixture", "collection_status", "gaps", "violations"}
            or summary["summary"] is not True or summary["topology"] != "kubernetes-owned-direct"
            or summary["lab"] is not False or summary["profile"] != producer.PROFILE or summary["fixture"] != fixture
            or evd.comparability.fixture_sha256 != sha256_hex(canonical_json(fixture))):
        raise ValueError("saved ordinary log summary differs from its pinned fixture")
    source = summary["source"]
    if (type(source) is not dict or set(source) != {"available", "payload_retained", "byte_count", "sha256"}
            or source["available"] is not True or source["payload_retained"] is not False
            or type(source["byte_count"]) is not int or not 0 <= source["byte_count"] <= producer.LIMIT_BYTES
            or type(source["sha256"]) is not str or _DIGEST.fullmatch(source["sha256"]) is None):
        raise ValueError("saved ordinary log source metadata differs from its writer")
    observations = evd.observations
    gaps, coverage = observations.get("log_coverage_gaps"), observations.get("log_coverage_complete")
    if (gaps is None or not gaps.collected or type(summary["gaps"]) is not list or summary["gaps"] != gaps.value
            or coverage is None or not coverage.collected or coverage.value is not False
            or type(summary["violations"]) is not list or not evd.normal_control.attempted
            or any(step.attempted for step in evd.challenges)):
        raise ValueError("saved ordinary log scope differs from its pinned observations")
    status = summary["collection_status"]
    if type(status) is not dict or set(status) != {"normal_control", "engine_stdout"}:
        raise ValueError("saved ordinary log collection status is invalid")
    control, engine = status["normal_control"], status["engine_stdout"]
    if type(control) is not dict or type(engine) is not dict:
        raise ValueError("saved ordinary log collection status is invalid")
    if control == {"stage": "binding_or_capture_unavailable"}:
        if evd.normal_control.succeeded is not False:
            raise ValueError("saved ordinary log control differs from pinned evidence")
    elif (set(control) != {"stage", "binding_verified_before_after", "capture_complete", "http_status"}
            or control["stage"] not in ("response_received", "response_id_observed")
            or control["binding_verified_before_after"] is not True
            or type(control["capture_complete"]) not in (bool, type(None))
            or (control["http_status"] is not None and (type(control["http_status"]) is not int
                                                        or not 100 <= control["http_status"] <= 599))
            or (control["stage"] == "response_id_observed" and evd.normal_control.succeeded is not True)):
        raise ValueError("saved ordinary log control differs from its writer")
    flags = (*producer._PIPE_FLAGS, "child_reap_unverified")
    if (set(engine) != {"stage", "returncode", *flags} or type(engine["returncode"]) is not int
            or engine["returncode"] != 0 or engine["bounded_at_source"] is not True
            or any(type(engine[key]) not in (bool, type(None)) for key in flags)):
        raise ValueError("saved ordinary log pipe status differs from its writer")
    complete = (engine["complete"] is True and engine["timed_out"] is False and engine["truncated"] is not True
                and source["byte_count"] < producer.LIMIT_BYTES)
    if engine["stage"] != ("bounded_capture_complete" if complete else "bounded_capture_partial"):
        raise ValueError("saved ordinary log pipe status differs from its writer")
    return {"artifact_sha256": artifacts[0].sha256, "source": source, "collection_status": status}


def _passive_hop_source_ref(evd, plan, evidence_refs, resolve):
    """Accept the one extra reference emitted by the same-attempt PLT-08 projection."""
    execution = evd.execution
    binding = execution.run_binding
    if (evd.check_id != passive_api_hop.CHECK or execution.status.value != "COMPLETED"
            or execution.executor is None or execution.executor.name != passive_api_hop.EXECUTOR
            or execution.executor.version != passive_api_hop.VERSION or evd.artifacts or not isinstance(binding, dict)):
        raise ValueError("saved evidence refs differ from exact PLAN/environment")
    values = binding.get("values")
    if (type(values) is not dict or set(values) != {"profile", "source_evidence", "source_artifact_sha256", "target_sha256"}
            or values["profile"] != passive_api_hop.PROFILE or binding.get("schema") != run_binding.SCHEMA_2
            or run_binding.verify(binding, evd)):
        raise ValueError("saved passive API hop binding differs from exact evidence scope")
    files = {key: values[key] for key in ("source_artifact_sha256", "target_sha256")}
    if binding.get("files") != files or any(type(digest) is not str or _DIGEST.fullmatch(digest) is None
                                            for digest in files.values()):
        raise ValueError("saved passive API hop binding differs from exact evidence files")
    ref = Ref.model_validate(values["source_evidence"])
    if _key(ref) not in evidence_refs:
        raise ValueError("saved passive API hop source is absent from the original report")
    source, environment, _ = passive_api_hop._source(
        SimpleNamespace(resolve=resolve), plan.ref(), evd.side, execution.attempt_id, ref)
    if (environment.ref() != evd.environment or source.data_origin != evd.data_origin
            or source.comparability != evd.comparability
            or source.execution.finished_at is None or execution.started_at is None
            or source.execution.finished_at > execution.started_at
            or [artifact.sha256 for artifact in source.artifacts if artifact.name == "probe_log.jsonl"]
            != [files["source_artifact_sha256"]]):
        raise ValueError("saved passive API hop source differs from exact evidence scope")
    return ref


def _persisted_text(capture):
    text = capture.get("text")
    if not isinstance(text, str):
        raise ValueError("saved log capture text is unavailable")
    raw = text.encode("utf-8")
    # Raw pipe caps are enforced at collection, before decoding/redaction.
    # Replacement characters and redaction can expand retained text. The exact
    # persisted artifact has already passed MAX_JSON_BYTES, so do not confuse
    # the raw source limit with the saved decoded representation budget.
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("saved decoded capture exceeds the artifact read budget")
    if (capture.get("persisted_decoded_sha256") != hashlib.sha256(raw).hexdigest()
            or capture.get("persisted_decoded_bytes") != len(raw)):
        raise ValueError("persisted decoded capture hash/byte count differs")
    if "stderr_text" in capture:
        stderr = capture["stderr_text"].encode("utf-8")
        if (len(stderr) > MAX_JSON_BYTES or capture.get("persisted_stderr_sha256") != hashlib.sha256(stderr).hexdigest()
                or capture.get("persisted_stderr_bytes") != len(stderr)):
            raise ValueError("persisted stderr capture hash/byte count differs")


def _replay_native_for_environment(sample, environment):
    from guard.adapters.engine_provider import get_provider

    try:
        provider = get_provider(environment.engine.name)
    except ValueError:
        provider = None
    profile = (provider.evidence_contract("native_log_replay_profile", environment.engine.image_digest,
                                         environment.engine.version) if provider is not None else None)
    if profile is None:
        return replay_native(sample)
    if sample.get("native_contract_sha256") != sha256_hex(canonical_json(profile["contract"])):
        raise ValueError("saved native contract differs from the reviewed environment")
    reviewed_images = profile.get("image_spellings", (environment.engine.image_digest,))
    for identity in (sample["source_identity_before"], sample["source_identity_after"]):
        if identity is not None and identity["image"] not in reviewed_images:
            raise ValueError("saved native identity differs from the pinned environment")
    return replay_native(sample, request_logger_line=profile["request_logger_line"])


def _obs09(store, evd, sample, values, environment):
    capture, trials = sample["capture"], sample["trials"]
    projected = sample.get("schema") == NATIVE_SCHEMA
    version = evd.execution.executor.version
    if projected:
        if version not in _NATIVE_PROJECTION_VERSIONS:
            raise ValueError("saved native projection differs from executor version")
        analysis = _replay_native_for_environment(sample, environment)
    elif version == "0.2.1":
        _persisted_text(capture)
    else:
        raise ValueError("saved native profile differs from executor version")
    if (not isinstance(trials, list) or len(trials) > 12
            or len({trial["case"] for trial in trials}) != len(trials)):
        raise ValueError("invalid bounded native trial inventory")
    for trial in trials:
        if (trial["case"] not in PLANNED_CASES or not isinstance(trial["request_id"], str)
                or len(trial["request_id"].encode("utf-8")) > 512):
            raise ValueError("invalid native trial identity")
        response_match = (trial.get("normal_generation") == "pass"
                          and trial.get("http_outcome") == 200
                          and trial.get("response_id") == "cmpl-" + trial["request_id"])
        if trial.get("response_id_matches_body") is not response_match:
            raise ValueError("native response and exact body request ID differ")
    if not projected:
        analysis = analyze_integrity_capture(capture, trials, sample.get("source_identity_before"),
                                             sample.get("source_identity_after"),
                                             initial_gaps=sample["initial_gaps"], max_events=sample["max_events"])
    collection = analysis["collection"]
    collection["capture_window"] = capture.get("window")
    collection["redaction"] = (NATIVE_PROJECTION_PROFILE if projected else
        "configured authorization secrets and labelled credential values; source digests precede redaction")
    events_raw = _artifact(store, evd, "log_integrity_events.json")
    if projected:
        events_envelope = strict_json_loads(events_raw.decode("utf-8"))
        if (set(events_envelope) != {"capture", "redaction", "encoding"}
                or events_envelope["redaction"] != NATIVE_PROJECTION_PROFILE
                or events_envelope["encoding"] != NATIVE_ENCODING):
            raise ValueError("saved native events envelope differs")
    else:
        events_envelope = json.loads(events_raw)
    saved_collection = events_envelope["capture"]
    if saved_collection != collection:
        raise ValueError("saved native events differ from capture replay (redaction may limit reconstruction)")
    document = _artifact(store, evd, "log_integrity_display.html").decode("utf-8")
    displayed = verify_log_display(document, collection)
    if (hashlib.sha256(document.encode()).hexdigest() != sample.get("display_sha256")
            or document != (render_projection(collection) if projected else render_log_integrity(collection)) or not displayed):
        raise ValueError("saved reference HTML does not map exactly to native events")
    if (hashlib.sha256(json.dumps(collection, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            != sample.get("collection_sha256")):
        raise ValueError("native collection digest differs")
    gaps = analysis["gaps"]
    obs = {"log_collector_profile": PROFILE, "log_client_request_id_trust": "untrusted_correlation",
           "log_trusted_fields": list(TRUSTED_FIELDS), "log_identity_verified": analysis["stable"],
           "log_required_trusted_fields": [field for field in values["required_trusted_fields"] if field in TRUSTED_FIELDS],
           "log_collection_complete": not gaps, "log_boundaries_preserved": analysis["boundaries"],
           "log_trusted_fields_preserved": analysis["fields_preserved"], "log_display_verified": sample["display_verified"] if projected else displayed,
           "log_trials_planned": list(PLANNED_CASES), "log_trials_reached": analysis["reached"],
           "log_security_violations": analysis["violations"], "log_coverage_gaps": gaps,
           "normal_controls": analysis["normal_inference"] and analysis["normal_logs"],
           "functional_probes_planned": ["before", "after"], "functional_probe_results": analysis["controls"]}
    return obs, not gaps


def _obs03(evd, sample, values):
    version = evd.execution.executor.version
    projected = False
    if (sample.get("schema") == "guard-log-analysis-capture/2" and version == "0.2.0"
            and "analysis_profile" not in sample):
        profile = LEGACY_JSON_PROFILE
    elif (sample.get("schema") == "guard-log-analysis-capture/3" and version == "0.2.1"
            and sample.get("analysis_profile") == STRICT_JSON_PROFILE):
        profile = STRICT_JSON_PROFILE
    elif (sample.get("schema") == "guard-log-analysis-capture/4" and version in {"0.3.0", "0.3.1"}
            and sample.get("analysis_profile") == STRICT_JSON_PROFILE
            and sample.get("projection_profile") == LOG_PROJECTION_PROFILE):
        validate_projection(sample, values)
        profile, projected = STRICT_JSON_PROFILE, True
    else:
        raise ValueError("saved log analysis profile differs from executor version")
    captures = sample["captures"]
    if not isinstance(captures, list) or len(captures) > 16:
        raise ValueError("invalid bounded source inventory")
    for capture in captures:
        if not projected:
            _persisted_text(capture)
        if capture["window"] != sample["window"]:
            raise ValueError("source capture window differs from trial window")
        source = capture["source"]
        if (source.get("role", "engine") == "engine"
                and source.get("container") != sample.get("source_target")):
            raise ValueError("captured engine differs from the recorded trial target")
    correlation = sample["correlation"]
    if (not isinstance(correlation, dict) or set(correlation) != {"normal", "error"}
            or any(not isinstance(value, str) or not value for value in correlation.values())
            or correlation["normal"] == correlation["error"]):
        raise ValueError("exact normal/error trial correlation is required")
    for request in sample["requests"]:
        branch = "error" if request["case"] == "safe_error" else "normal"
        if projected:
            if (set(request) != {"case", "request_fixture_sha256", "request_id_sha256", "trial", "outcome"}
                    or request["case"] not in {"normal", "safe_error", "after"}
                    or request["trial"] != branch
                    or request["request_id_sha256"] != sample["correlation_fingerprints"][branch]
                    or not isinstance(request["request_fixture_sha256"], str)
                    or len(request["request_fixture_sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in request["request_fixture_sha256"])):
                raise ValueError("saved projection request binding differs")
        elif request["request_id"] != correlation[branch]:
            raise ValueError("saved request differs from its capture trial identity")
    obs = analyze_log_captures(captures, values, sample["markers"], correlation,
                               initial_gaps=sample["initial_gaps"], profile=profile)
    if obs != sample.get("analysis"):
        raise ValueError("recorded log analysis differs from persisted capture replay (redaction may limit reconstruction)")
    obs.update({"functional_probes_planned": ["before", "after"],
                "functional_probe_results": sample["controls"]})
    return obs, obs["log_coverage_complete"], profile


def analyze_saved_logs(store: Path, report_id: str | None = None) -> dict:
    """Public read-only boundary: unreadable saved inputs are validation errors.

    Never copy OS messages (which can contain paths or supplied material) into
    the public failure reason. No repair or source write is attempted.
    """
    try:
        return _analyze_saved_logs(store, report_id)
    except (OSError, sqlite3.Error) as exc:
        raise ValueError("saved evidence input could not be read (" + type(exc).__name__ + ")") from exc


def _analyze_saved_logs(store: Path, report_id: str | None = None) -> dict:
    """Audit an exact RPT manifest. Ambiguous reports require id@revision.

    Source SQLite is immutable/read-only. No Store is constructed, no judgment
    is written, and no executable or payload path/URL is followed.
    """
    store = Path(store).absolute()
    for path in [store, *store.parents]:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("saved source path must not contain symlinks or reparse points")
    before = inventory_tree(store)
    parsed = [object_from_dict(value) for value in read_objects(store)]
    if any(not value.hash_ok() for value in parsed):
        raise ValueError("stored object hash differs")
    index = {_key(value.ref()): value for value in parsed}
    if len(index) != len(parsed):
        raise ValueError("duplicate saved object identity")
    reports = [value for value in parsed if value.kind == "RPT"]
    if report_id:
        ident, sep, revision = report_id.rpartition("@")
        if sep:
            if not revision.isdigit() or int(revision) < 1:
                raise ValueError("report revision must be a positive integer")
            reports = [value for value in reports if value.id == ident and value.revision == int(revision)]
        else:
            reports = [value for value in reports if value.id == report_id]
    if len(reports) != 1:
        raise ValueError("select one exact saved report using --report RPT-ID@REVISION")
    report = reports[0]
    manifest_keys = [_key(ref) for ref in report.inputs_manifest]
    if len(set(manifest_keys)) != len(manifest_keys) or set(manifest_keys) != {_key(ref) for ref in report.refs}:
        raise ValueError("report manifest and refs differ or contain duplicates")
    plans = [index.get(_key(ref)) for ref in report.inputs_manifest if ref.kind == "PLAN"]
    if len(plans) != 1 or plans[0] is None:
        raise ValueError("report must pin one original PLAN")
    plan = plans[0]
    # The helper verifies all artifact bytes, but loads only the report's exact
    # original PLAN pack. Other plans/history in the store are not selected.
    pack = verify_store_files(store, [value.to_dict() for value in parsed
                                     if value.kind != "PLAN" or value.ref() == plan.ref()])

    def resolve(ref):
        if ref.kind == "PACK":
            if ref != pack.ref():
                raise ValueError("pinned original pack differs")
            return pack
        value = index.get(_key(ref))
        if value is None:
            raise ValueError("broken exact saved reference")
        return value

    if [ref for ref in report.inputs_manifest if ref.kind == "PACK"] != [pack.ref()]:
        raise ValueError("report must pin its one original pack")
    fixed = [resolve(ref) for ref in report.inputs_manifest if ref.kind != "PACK"]
    if plan.inputs.pack != pack.ref():
        raise ValueError("PLAN and saved pack differ")
    policy = resolve(plan.inputs.policy)
    if policy not in fixed or policy.pack != pack.ref():
        raise ValueError("report must pin the original policy and pack")
    for value in fixed:
        for ref in getattr(value, "refs", []):
            resolve(ref)
    environments = [resolve(ref) for ref in plan.inputs.environments]
    evidence = [value for value in fixed if value.kind == "EVD"]
    results = [value for value in fixed if value.kind == "RES"]
    required_refs = [pack.ref(), *plan.inputs.environments, policy.ref(), plan.ref(),
                     *(value.ref() for value in [*evidence, *results])]
    plan_refs = [pack.ref(), *plan.inputs.environments, policy.ref()]
    if plan.inputs.change:
        resolve(plan.inputs.change)
        required_refs.append(plan.inputs.change)
        plan_refs.append(plan.inputs.change)
    if set(manifest_keys) != {_key(ref) for ref in required_refs}:
        raise ValueError("report manifest differs from its original PLAN graph")
    if (len(plan.refs) != len(plan_refs) or {_key(ref) for ref in plan.refs} != {_key(ref) for ref in plan_refs}):
        raise ValueError("PLAN refs differ from its exact original inputs")
    if (report.policy_status != policy.status
            or report.pack_review_status.value != ("REVIEWED" if pack.reviewed else "DRAFT")):
        raise ValueError("report policy/pack review status differs from saved sources")
    origins = [value.data_origin for value in environments] + [policy.data_origin, plan.data_origin]
    origins += [value.data_origin for value in [*evidence, *results]]
    official = policy.status.value == "CONFIRMED" and pack.reviewed and all(value.value != "EXAMPLE" for value in origins)
    if report.official != official or report.data_origin != weakest_origin(origins):
        raise ValueError("report official/origin differs from its pinned original sources")
    evidence_refs = {_key(evd.ref()) for evd in evidence}
    for evd in evidence:
        expected_refs = [plan.ref(), evd.environment]
        executor = evd.execution.executor
        if len(evd.refs) == 3 or (evd.check_id == passive_api_hop.CHECK and executor is not None
                                  and executor.version == passive_api_hop.VERSION):
            expected_refs.append(_passive_hop_source_ref(evd, plan, evidence_refs, resolve))
        if (evd.plan != plan.ref() or evd.environment not in plan.inputs.environments
                or len(evd.refs) != len(expected_refs) or {_key(ref) for ref in evd.refs} != {_key(ref) for ref in expected_refs}):
            raise ValueError("saved evidence refs differ from exact PLAN/environment")
    for res in results:
        evd = resolve(res.evidence)
        if (evd not in evidence or res.check_id != evd.check_id or res.side != evd.side
                or len(res.refs) != 2 or {_key(ref) for ref in res.refs} != {_key(evd.ref()), _key(policy.ref())}):
            raise ValueError("saved result refs differ from exact evidence/policy")
        item = plan.item(evd.check_id)
        level = weakest_origin([evd.data_origin if evd.execution.status.value == "COMPLETED" else None,
                                *(condition.origin for condition in item.conditions)])
        origin = weakest_origin([evd.data_origin, policy.data_origin, plan.data_origin])
        if res.verification_level != level or res.data_origin != origin:
            raise ValueError("saved result origin/verification level differs from original evidence")
    problems, limitations, rows = [], [], []
    evds = [value for value in fixed if value.kind == "EVD" and value.check_id in CHECKS]
    if not evds:
        problems.append("the selected report contains no pinned automated OBS-03/OBS-09 capture")
    if len({(value.check_id, value.side) for value in evds}) != len(evds):
        raise ValueError("report pins multiple evidence revisions for one log check side")
    for evd in evds:
        row = {"check_id": evd.check_id, "side": evd.side.value, "evidence": evd.ref().model_dump(mode="json"),
               "data_origin": evd.data_origin.value, "replayed": False, "coverage_complete": False}
        rows.append(row)
        try:
            if (evd.plan != plan.ref() or evd.environment not in plan.inputs.environments
                    or not plan.item(evd.check_id).selected):
                raise ValueError("log evidence PLAN, environment or selection differs")
            expected_env = plan.inputs.environments[0 if evd.side.value in {"single", "baseline"} else 1]
            if evd.environment != expected_env or (plan.mode.value == "audit") != (evd.side.value == "single"):
                raise ValueError("log evidence side and PLAN environment differ")
            executor = evd.execution.executor
            if (evd.execution.operator is not None or executor is None or executor.name != CHECKS[evd.check_id]
                    or evd.execution.status.value != "COMPLETED"):
                raise ValueError("completed automated log evidence is required")
            results = [value for value in fixed if value.kind == "RES" and value.evidence == evd.ref()]
            if len(results) != 1:
                raise ValueError("report must pin one exact result for each log evidence")
            res = results[0]
            check = pack.checks[evd.check_id]
            if res.check_id != check.id or res.side != evd.side:
                raise ValueError("result differs from log evidence check/side")
            report_rows = [value for value in report.by_check if value.check_id == check.id and value.selected]
            side_row = getattr(report_rows[0], evd.side.value) if len(report_rows) == 1 else None
            if side_row is None or side_row.result != res.ref():
                raise ValueError("report row does not pin the saved log result")
            if side_row.verification_level != res.verification_level:
                raise ValueError("report verification level differs from pinned result")
            if any(getattr(side_row, axis) != getattr(res.verdicts, axis)
                   for axis in ("security", "functional", "performance")):
                raise ValueError("report verdicts differ from the pinned result")
            values = policy.values.get(check.id, {})
            if check.id == "OBS-03" and executor.version == kubernetes_log_analysis.KubernetesLogAnalysisExecutor.version:
                row.update(status="NORMAL_METADATA_VALIDATED", replay_basis="stored_normal_capture_metadata",
                           raw_source_replayed=False, normal_capture=_normal_obs03_metadata(store, evd, values),
                           result=res.ref().model_dump(mode="json"))
                limitations.append(f"{check.id}/{evd.side.value}: the sealed ordinary capture summary omits original stdout and response bytes. Metadata was checked without replay or re-judgment.")
                continue
            name = "log_samples.json" if check.id == "OBS-03" else "log_integrity_samples.json"
            raw = _artifact(store, evd, name)
            envelope = json.loads(raw)
            if not isinstance(envelope, dict) or not isinstance(envelope.get("capture"), dict):
                raise ValueError("saved log capture envelope differs")
            sample = envelope.get("capture", {})
            if sample.get("schema") in {"guard-log-analysis-capture/4", NATIVE_SCHEMA}:
                try:
                    exact = strict_json_loads(raw.decode("utf-8"))
                except (ValueError, RecursionError):
                    raise ValueError("saved projection envelope JSON is ambiguous") from None
                native = sample.get("schema") == NATIVE_SCHEMA
                if (set(exact) != {"capture", "encoding", "redaction"}
                        or exact["redaction"] != (NATIVE_PROJECTION_PROFILE if native else LOG_PROJECTION_PROFILE)
                        or exact["encoding"] != (NATIVE_ENCODING if native else "UTF-8 predicate projection; original values omitted")):
                    raise ValueError("saved projection envelope profile differs")
            expected_schemas = ({"guard-log-analysis-capture/2", "guard-log-analysis-capture/3", "guard-log-analysis-capture/4"}
                                if check.id == "OBS-03" else {"guard-log-integrity-capture/2", NATIVE_SCHEMA})
            if sample.get("schema") not in expected_schemas:
                row["status"] = "LEGACY_UNVERIFIABLE"
                limitations.append(f"{check.id}/{evd.side.value}: legacy capture retained; new pipe/nofollow/correlation guarantees cannot be reconstructed")
                continue
            if sample.get("policy_values") != values:
                raise ValueError("saved capture policy differs from pinned policy")
            if check.id == "OBS-03":
                obs, complete, profile = _obs03(evd, sample, values)
                row["analysis_profile"] = profile
                row["strict_json_profile_applied"] = profile == STRICT_JSON_PROFILE
                if sample.get("schema") == "guard-log-analysis-capture/4":
                    row["replay_basis"] = "stored_predicate_projection"
                    row["raw_source_replayed"] = False
                    limitations.append("OBS-03: stored predicate projection was parsed again; original text/values are omitted, and source digests do not reconstruct or authenticate them.")
                if profile == LEGACY_JSON_PROFILE:
                    limitations.append("OBS-03: historical JSON parser retained; duplicate-key/non-finite rejection is not retroactively claimed")
            else:
                obs, complete = _obs09(store, evd, sample, values, resolve(expected_env))
                if sample.get("schema") == NATIVE_SCHEMA:
                    row["replay_basis"] = "stored_native_predicate_transcript"
                    row["raw_source_replayed"] = False
                    row["original_native_parser_replayed"] = False
                    limitations.append("OBS-09: only the stored native predicate transcript was parsed again. Original native line boundaries, excluded-event eligibility and source parser gaps are retained predicates, not re-parsed original text or authenticated source facts.")
            # run_plan persists only the CheckDef projection. Auxiliary analysis
            # diagnostics are checked against sample.analysis inside _obs03,
            # while every required reconstructed/stored observation is mandatory.
            for required in check.required_observations:
                key = required.key
                if key not in obs:
                    raise ValueError("saved required observation cannot be reconstructed: " + key)
                observation = evd.observations.get(key)
                if observation is None or not observation.collected or observation.value != obs[key]:
                    raise ValueError("saved observation differs from capture replay: " + key)
            plugin = RuleTableJudge()
            if (check.judge is None or check.judge.plugin != "rule_table" or res.judge.name != plugin.name
                    or res.judge.version != plugin.version
                    or res.judge.params_sha256 != sha256_hex(canonical_json(check.judge.params))):
                row["status"] = "JUDGE_VERSION_UNAVAILABLE"
                limitations.append(f"{check.id}/{evd.side.value}: exact historical judge version is unavailable; no substituted re-judgment")
                continue
            judged = plugin.judge(check, evd, check.judge.params, values)
            expected = {axis: value.value for axis, value in judged.verdicts.items()}
            if pass_gate(check, evd):
                expected = {axis: "UNKNOWN" if value == "PASS" else value for axis, value in expected.items()}
            if evd.comparability.cpu is not None:
                expected["performance"] = "UNKNOWN"
            if expected != res.verdicts.model_dump(mode="json"):
                raise ValueError("pinned result differs from fixed-rule replay")
            row.update(status="PROJECTION_REPLAY_MATCH" if row.get("replay_basis") else "REPLAY_MATCH",
                       replayed=True, coverage_complete=complete,
                       result=res.ref().model_dump(mode="json"), verdicts=res.verdicts.model_dump(mode="json"))
            if not complete:
                limitations.append(f"{check.id}/{evd.side.value}: capture remains incomplete; saved UNKNOWN/independent FAIL is preserved")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            row["status"] = "REPLAY_MISMATCH"
            # Only our fixed ValueErrors are shown; parser/payload exception
            # messages can contain input material and are not copied.
            message = str(exc) if type(exc) is ValueError and str(exc).startswith((
                "saved ", "persisted ", "source ", "captured ", "exact ", "completed ", "log ",
                "result ", "report ", "pinned ", "one ", "invalid ", "native ", "recorded ")) else type(exc).__name__
            problems.append(f"{evd.check_id}/{evd.side.value}: {message}")
    preserved = inventory_tree(store) == before
    if not preserved:
        raise ValueError("source inventory changed during read-only log analysis")
    return {"schema": SCHEMA, "accepted": bool(rows) and not problems and all(row["replayed"] for row in rows),
            "evidence_contract_valid": not problems, "checks": rows, "problems": problems,
            "limitations": limitations + ["Replay agreement is not security PASS or a new MEASURED execution.",
                "Source/collector authenticity and redacted original bytes are not authenticated.",
                "Normal inference remains the original saved observation; no request was sent."],
            "report": report.ref().model_dump(mode="json"), "plan": plan.ref().model_dump(mode="json"),
            "pack": pack.ref().model_dump(mode="json"), "official": report.official,
            "policy_status": report.policy_status.value, "pack_review_status": report.pack_review_status.value,
            "original_inventory_preserved": preserved}


def main(argv=None):
    parser = argparse.ArgumentParser(description="저장 OBS-03/09 로그 증거의 읽기 전용 재검증; 엔진을 기동하지 않습니다.")
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--report", help="정확한 RPT-ID@revision; 생략은 RPT가 하나일 때만 허용")
    parser.add_argument("--output", type=Path, required=True, help="원본과 분리된 새 결과 폴더")
    args = parser.parse_args(argv)
    try:
        from guard.update_validation import reserve_output
        output = reserve_output(args.output, protected=[args.store])
        result = analyze_saved_logs(args.store, args.report)
        text = ("저장 로그 재검증 일치" if result["accepted"] else "저장 로그 재검증 미확인 또는 불일치")
        text += "\n보안 PASS·새 실측·출처 인증을 뜻하지 않습니다. 원래 판정과 원본은 변경하지 않습니다.\n"
        text += "\n".join(f"{row['check_id']}/{row['side']}: {row['status']}" for row in result["checks"]) + "\n"
        for name, content in (("summary.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n"),
                              ("summary.txt", text)):
            with (output / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        print(text, end="")
        return 0 if result["accepted"] else 1
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("저장 로그 재검증 실패: " + type(exc).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
