"""Small EXAMPLE-only log store for installed-wheel smoke verification.

This prepares synthetic objects with installed guard APIs. It never executes an
engine, imports repository tests, marks evidence MEASURED, or authenticates logs.
The CI harness copies this file outside the checkout before loading it.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def build(root: Path, pack_dir: Path, *, projection=False):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors.log_analysis_contract import analyze_log_captures
    from guard.models import Environment, Evidence, Policy, load_pack, canonical_json, sha256_hex
    from guard.store import Store
    from guard.store.packs import put_pack

    root = Path(root)
    if root.exists():
        raise ValueError("EXAMPLE fixture requires a new store path")
    store = Store(root)
    pack = load_pack(pack_dir)
    put_pack(store, pack_dir)
    timestamp = datetime(2026, 10, 24, tzinfo=timezone.utc)
    env = Environment(id="ENV-EXAMPLE-WHEEL-LOG", created_at=timestamp, data_origin="EXAMPLE",
                      engine={"name": "engine-x", "version": "EXAMPLE"}).seal()
    values = {"allowed_log_fields": ["request_id", "event", "exception"],
              "forbidden_log_fields": ["prompt", "credential"],
              "forbidden_marker_contexts": ["caller_input", "error_input"],
              "required_sources": ["EXAMPLE-engine"],
              "required_branches": ["normal", "error", "exception_dump"]}
    policy = Policy(id="POL-EXAMPLE-WHEEL-LOG", created_at=timestamp, data_origin="EXAMPLE", status="DRAFT",
                    pack=pack.ref(), scope={"include": ["OBS-03"]}, values={"OBS-03": values}).seal()
    store.put_many([env, policy])
    plan = select(pack, [env], policy)
    store.put(plan)
    marks = {"caller_input": "EXAMPLE_MARK_U", "private_system": "EXAMPLE_MARK_S", "error_input": "EXAMPLE_MARK_E"}
    correlation = {"normal": "EXAMPLE_TRIAL_NORMAL", "error": "EXAMPLE_TRIAL_ERROR"}
    window = {"since": "2026-10-24T00:00:00+00:00", "until": "2026-10-24T00:01:00+00:00"}
    events = [{"request_id": correlation["normal"], "event": "normal"},
              {"request_id": correlation["error"], "event": "error", "exception": "EXAMPLE bounded validation traceback"},
              {"request_id": correlation["normal"], "event": "normal"}]
    text = "\n".join(json.dumps(event) for event in events) + "\n"
    raw = text.encode()
    captures = [{"source": {"id": "EXAMPLE-engine", "kind": "docker_logs", "container": "EXAMPLE-owned"},
                 "identity": {"container_id": "EXAMPLE-owned-id", "started_at": "EXAMPLE-start", "image_id": "EXAMPLE-image"},
                 "identity_stable": True, "source_complete": True, "returncode": 0, "truncated": False,
                 "window": window, "text": text, "persisted_decoded_bytes": len(raw),
                 "persisted_decoded_sha256": hashlib.sha256(raw).hexdigest()}]
    analysis = analyze_log_captures(captures, values, marks, correlation)
    controls = {"before": "pass", "after": "pass"}
    requests = [{"case": case, "request_id": correlation["error" if case == "safe_error" else "normal"], "outcome": outcome}
                for case, outcome in (("normal", 200), ("safe_error", 400), ("after", 200))]
    sample = {"schema": "guard-log-analysis-capture/2", "captures": captures, "requests": requests,
              "markers": marks, "correlation": correlation, "initial_gaps": [], "coverage_gaps": [],
              "analysis": analysis, "controls": controls, "policy_values": values,
              "source_target": "EXAMPLE-owned", "window": window}
    envelope = {"capture": sample, "redaction": "EXAMPLE has no credentials; no real source bytes", "encoding": "UTF-8"}
    version = "0.2.0"
    if projection:
        from guard.executors.log_projection import PROFILE, fingerprint, project_log_captures, validate_projection
        converted = project_log_captures(captures, values, marks, correlation)
        sample = {"schema": "guard-log-analysis-capture/4", "analysis_profile": "guard-log-json/2",
                  "projection_profile": PROFILE, **converted,
                  "requests": [{"case": row["case"], "trial": "error" if row["case"] == "safe_error" else "normal",
                      "request_id_sha256": fingerprint(row["request_id"]),
                      "request_fixture_sha256": sha256_hex(canonical_json({"EXAMPLE_case": row["case"]})),
                      "outcome": row["outcome"]} for row in requests],
                  "initial_gaps": [], "coverage_gaps": converted["analysis"]["log_coverage_gaps"], "controls": controls,
                  "policy_values": values, "source_target": "EXAMPLE-owned", "window": window}
        validate_projection(sample, values)
        analysis = converted["analysis"]
        envelope = {"capture": sample, "redaction": PROFILE, "encoding": "UTF-8 predicate projection; original values omitted"}
        version = "0.3.0"
    content = json.dumps(envelope, ensure_ascii=False).encode()
    saved = store.put_artifact(content)
    probe = "".join(json.dumps({"via": "log-analysis", "route": req["case"], "outcome": req["outcome"]}) + "\n" for req in requests)
    saved_probe = store.put_artifact(probe.encode())
    observations = {**analysis, "functional_probes_planned": ["before", "after"], "functional_probe_results": controls}
    definition = pack.checks["OBS-03"]
    evd = Evidence(id="EVD-EXAMPLE-WHEEL-LOG", created_at=timestamp, data_origin="EXAMPLE",
        plan=plan.ref(), environment=env.ref(), check_id="OBS-03", side="single", refs=[plan.ref(), env.ref()],
        execution={"status": "COMPLETED", "executor": {"name": "log_analysis", "version": version, "config_sha256": "0" * 64}},
        normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE synthetic transport"},
        challenges=[{"step": step, "attempted": True} for step in definition.challenge_steps],
        observations={key: {"value": value, "collected": True} for key, value in observations.items()},
        artifacts=[{"name": name, "path": item.path, "sha256": item.sha256, "bytes": item.bytes,
                    "collected_at": timestamp, "collector": "EXAMPLE-wheel-fixture"}
                   for name, item in (("log_samples.json", saved), ("probe_log.jsonl", saved_probe))]).seal()
    store.put(evd)
    judge_plan(store, pack, plan, timestamp)
    report = build_report(store, pack, plan, timestamp)
    store.put(report)
    return root, f"{report.id}@{report.revision}"


def build_native(root: Path, pack_dir: Path, *, projection=True):
    """Native parser/HTML compatibility fixture, never a source authenticity proof."""
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors.log_integrity_contract import PLANNED_CASES, analyze_integrity_capture
    from guard.executors.log_integrity_parser import PROFILE, TRUSTED_FIELDS
    from guard.executors.native_projection import project_native, replay_native, envelope, render_projection, PROFILE as PROJECTION
    from guard.models import Environment, Evidence, Policy, load_pack, canonical_json, sha256_hex
    from guard.report.log_integrity import render_log_integrity, verify_log_display
    from guard.store import Store
    from guard.store.packs import put_pack

    root = Path(root)
    if root.exists():
        raise ValueError("EXAMPLE fixture requires a new store path")
    store = Store(root)
    pack = load_pack(pack_dir)
    put_pack(store, pack_dir)
    timestamp = datetime(2026, 10, 24, tzinfo=timezone.utc)
    image = "vllm/vllm-openai-cpu@sha256:" + "a" * 64
    env = Environment(id="ENV-EXAMPLE-NATIVE", created_at=timestamp, data_origin="EXAMPLE",
        engine={"name": "vllm", "version": "0.31.0", "image_digest": image}, model={"id": "EXAMPLE-model"}).seal()
    values = {"collector_profile": PROFILE, "required_trusted_fields": list(TRUSTED_FIELDS),
              "client_request_id_trust": "untrusted_correlation"}
    pol = Policy(id="POL-EXAMPLE-NATIVE", created_at=timestamp, data_origin="EXAMPLE", status="DRAFT",
        pack=pack.ref(), scope={"include": ["OBS-09"]}, values={"OBS-09": values}).seal()
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    identity = {"container_id": "EXAMPLE-owned", "started_at": "EXAMPLE-start", "image": image,
        "image_id": "sha256:" + "b" * 64, "image_repo_digest_verified": True,
        "logging_profile": "request-info-v1", "logging_controls_verified": True}
    window = {"since": "2026-10-24T00:00:00+00:00", "until": "2026-10-24T00:01:00+00:00"}
    prefix = "(EngineCore pid=123) INFO 10-24 00:00:01 [request_logger.py:65] "
    trials, lines = [], []
    for case in PLANNED_CASES:
        rid = "EXAMPLE_" + case + ({"multiline": "\nEXAMPLE_SECOND", "cr": "\rEXAMPLE_CR",
            "crlf": "\r\nEXAMPLE_CRLF", "html": "<script>EXAMPLE</script>"}.get(case, ""))
        trials.append({"case": case, "request_id": rid, "request_id_trust": "untrusted_correlation",
            "request_fixture_sha256": sha256_hex(canonical_json({"EXAMPLE_request_id": rid})),
            "http_outcome": 200, "response_id": "cmpl-" + rid,
            "normal_generation": "pass", "response_id_matches_body": True})
        message = "Received request cmpl-" + rid + "-0: params: EXAMPLE_PARAMS, lora_request: None."
        physical = (prefix + message.replace("\n", "\r\n" + prefix) + "\n").split("\n")
        lines.extend("2026-10-24T00:00:01Z " + line + "\n" for line in physical[:-1])
    text = "".join(lines)
    capture = {"text": text, "stderr_text": "", "returncode": 0, "truncated": False,
        "timed_out": False, "source_complete": True, "stderr_present": False, "window": window,
        "persisted_decoded_sha256": hashlib.sha256(text.encode()).hexdigest(), "persisted_decoded_bytes": len(text.encode()),
        "persisted_stderr_sha256": hashlib.sha256(b"").hexdigest(), "persisted_stderr_bytes": 0}
    analysis = analyze_integrity_capture(capture, trials, identity, identity)
    original_collection = analysis["collection"]
    original_collection["capture_window"] = window
    original_collection["redaction"] = "configured authorization secrets and labelled credential values; source digests precede redaction"
    original_html = render_log_integrity(original_collection)
    displayed = verify_log_display(original_html, original_collection)
    if projection:
        sample = project_native(capture, trials, identity, identity, analysis=analysis)
        replay = analyze_integrity_capture(sample["capture"], sample["trials"], sample["source_identity_before"],
            sample["source_identity_after"], initial_gaps=sample["replay_initial_gaps"])
        collection = replay["collection"]
        collection.update(capture_window=window, redaction=PROJECTION)
        document = render_projection(collection)
        sample.update(policy_values=values, native_contract_sha256=sha256_hex(canonical_json({"EXAMPLE": True})),
            source_display_sha256=hashlib.sha256(original_html.encode()).hexdigest(),
            collection_sha256=hashlib.sha256(json.dumps(collection, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            display_sha256=hashlib.sha256(document.encode()).hexdigest(), display_verified=displayed)
        replay_native(sample)
        samples, events = envelope(sample), envelope(collection)
        version = "0.3.0"
    else:
        collection, document = original_collection, original_html
        sample = {"schema": "guard-log-integrity-capture/2", "capture": capture, "trials": trials,
            "initial_gaps": [], "max_events": 128, "policy_values": values,
            "source_identity_before": identity, "source_identity_after": identity, "native_contract": {},
            "collection_sha256": hashlib.sha256(json.dumps(collection, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "display_sha256": hashlib.sha256(document.encode()).hexdigest(), "display_verified": displayed,
            "coverage_gaps": analysis["gaps"], "violations": analysis["violations"]}
        samples, events = {"capture": sample}, {"capture": collection}
        version = "0.2.1"
    obs = {"log_collector_profile": PROFILE, "log_client_request_id_trust": "untrusted_correlation",
        "log_trusted_fields": list(TRUSTED_FIELDS), "log_identity_verified": analysis["stable"],
        "log_required_trusted_fields": list(TRUSTED_FIELDS), "log_collection_complete": not analysis["gaps"],
        "log_boundaries_preserved": analysis["boundaries"], "log_trusted_fields_preserved": analysis["fields_preserved"],
        "log_display_verified": displayed, "log_trials_planned": list(PLANNED_CASES), "log_trials_reached": analysis["reached"],
        "log_security_violations": analysis["violations"], "log_coverage_gaps": analysis["gaps"],
        "normal_controls": analysis["normal_inference"] and analysis["normal_logs"],
        "functional_probes_planned": ["before", "after"], "functional_probe_results": analysis["controls"]}
    artifacts = []
    for name, content in (("log_integrity_samples.json", json.dumps(samples, ensure_ascii=False).encode()),
            ("log_integrity_events.json", json.dumps(events, ensure_ascii=False).encode()),
            ("log_integrity_display.html", document.encode())):
        item = store.put_artifact(content)
        artifacts.append({"name": name, "path": item.path, "sha256": item.sha256, "bytes": item.bytes,
            "collected_at": timestamp, "collector": "EXAMPLE-native-wheel-fixture"})
    definition = pack.checks["OBS-09"]
    evd = Evidence(id="EVD-EXAMPLE-NATIVE", created_at=timestamp, data_origin="EXAMPLE",
        plan=plan.ref(), environment=env.ref(), check_id="OBS-09", side="single", refs=[plan.ref(), env.ref()],
        execution={"status": "COMPLETED", "executor": {"name": "log_integrity", "version": version, "config_sha256": "0" * 64}},
        normal_control={"attempted": True, "succeeded": True, "detail": "EXAMPLE synthetic native log"},
        challenges=[{"step": step, "attempted": True} for step in definition.challenge_steps],
        observations={key: {"value": value, "collected": True} for key, value in obs.items()}, artifacts=artifacts).seal()
    store.put(evd)
    judge_plan(store, pack, plan, timestamp)
    report = build_report(store, pack, plan, timestamp)
    store.put(report)
    return root, f"{report.id}@{report.revision}"
