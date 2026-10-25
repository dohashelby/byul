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


def build(root: Path, pack_dir: Path):
    from guard.core.judge import judge_plan
    from guard.core.report import build_report
    from guard.core.select import select
    from guard.executors.log_analysis_contract import analyze_log_captures
    from guard.models import Environment, Evidence, Policy, load_pack
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
    content = json.dumps(envelope, ensure_ascii=False).encode()
    saved = store.put_artifact(content)
    probe = "".join(json.dumps({"via": "log-analysis", "route": req["case"], "outcome": req["outcome"]}) + "\n" for req in requests)
    saved_probe = store.put_artifact(probe.encode())
    observations = {**analysis, "functional_probes_planned": ["before", "after"], "functional_probe_results": controls}
    definition = pack.checks["OBS-03"]
    evd = Evidence(id="EVD-EXAMPLE-WHEEL-LOG", created_at=timestamp, data_origin="EXAMPLE",
        plan=plan.ref(), environment=env.ref(), check_id="OBS-03", side="single", refs=[plan.ref(), env.ref()],
        execution={"status": "COMPLETED", "executor": {"name": "log_analysis", "version": "0.2.0", "config_sha256": "0" * 64}},
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
