"""TG-CODE-01 synthetic evidence, kernel filters and unchanged platform contract."""
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from serving_runtime.enforcement.codeload import apply_collection_evidence, watch_prefixes
from serving_runtime.enforcement.events import parse_tetragon
from serving_runtime.enforcement.execpolicy import ExecPolicy
from serving_runtime.enforcement.intent import IntentError, parse_intent
from serving_runtime.enforcement.tetragon import render_manifests, render_monitor_manifests
from serving_runtime.enforcement.attribution import KERNEL_DATA
from tools.tetragon_identities import IdentityCollector
from serving_runtime.mvp.collectors import Collector
from serving_runtime.mvp.observation_feed import HEARTBEAT_SCHEMA, ObservationFeed
from byul_platform.config import DEFAULT_CONTRACTS_DIR
from byul_platform.contracts import Contracts
from byul_platform.sources.checklist_links import checks_for_record, coverage
from byul_platform.sources.runtime import observation_event

ROOT = Path(__file__).resolve().parents[2]
AT = datetime.now(timezone.utc)


def intent_document(engine="gpu-lab"):
    return json.loads((ROOT / "runtime/deploy" / engine / "intent.aws-lab.json").read_text())


def raw_event(path="/cache/hf/modules/demo.py"):
    return {"time": AT.isoformat(), "process_kprobe": {
        "function_name": "security_file_open", "policy_name": "runtime-tg-code-01",
        "action": "KPROBE_ACTION_POST",
        "process": {"pid": 42, "binary": "/usr/bin/python3", "pod": {
            "namespace": "serving-lab", "name": "serving-engine-demo", "uid": "pod-1",
            "pod_labels": {"app": "serving-engine"}, "container": {"name": "engine"}}},
        "args": [{"file_arg": {"path": path}}, {"file_arg": {"path": path}}]}}


def phase_policy(tmp_path, phase="inferring", *, basis="engine_signal", collector=None, engine="gpu-lab"):
    document = {"schema_version": "runtime-phase-timeline/1.0", "entries": [{
        "phase": phase, "basis": basis, "from": (AT - timedelta(seconds=10)).isoformat(), "until": None}]}
    if collector is not None:
        document["collector"] = collector
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(document))
    return ExecPolicy(parse_intent(intent_document(engine)), timeline_path=path)


def parse(raw, policy=None, **kwargs):
    return parse_tetragon(raw, data_origin="synthetic", exec_policy=policy, **kwargs)


@pytest.mark.parametrize("engine", ["gpu-lab", "gpu-lab-sglang"])
@pytest.mark.parametrize("kernel_identity", [False, True])
def test_render_kernel_scope_and_suffix_conjunction_monitor_only(engine, kernel_identity):
    doc = intent_document(engine)
    doc.update(tetragon_kernel_identity=kernel_identity, code_load_watch_paths=["/download/hf_modules"])
    intent = parse_intent(doc)
    for render in (render_manifests, render_monitor_manifests):
        manifest = render(intent)["tetragon-tg-code-01.json"]
        spec = manifest["spec"]
        assert spec["podSelector"] == {"matchLabels": {"app": "serving-engine"}}
        assert spec["options"] == [{"name": "policy-mode", "value": "monitor"}]
        probe = spec["kprobes"][0]
        assert probe["data"] == KERNEL_DATA
        assert manifest["metadata"]["annotations"]["serving-runtime.local/kernel-identity"] == "v2"
        assert probe["call"] == "security_file_open" and probe["syscall"] is False
        assert len(probe["args"]) + len(probe["data"]) == 5
        assert [arg["index"] for arg in probe["args"]] == [0, 0]
        selectors = probe["selectors"]
        assert len(selectors) <= 5
        for selector in selectors:
            prefix, suffix = selector["matchArgs"]
            assert (prefix["args"], prefix["operator"]) == ([0], "Prefix")
            assert len(prefix["values"]) <= 8
            assert (suffix["args"], suffix["operator"]) == ([1], "Postfix")
            assert suffix["values"] == [".py", ".pyc", ".pyo", ".so"]
            assert selector["matchActions"] == [{"action": "Post"}]
        def kernel_match(path):
            return any(any(path.startswith(p) for p in s["matchArgs"][0]["values"])
                       and any(path.endswith(p) for p in s["matchArgs"][1]["values"]) for s in selectors)
        for path in ("/cache/hf/modules/a.py", "/tmp/b.pyc", "/models/c.so", "/home/user/.cache/huggingface/modules/a.py", "/download/hf_modules/a.py"):
            assert kernel_match(path)
        for path in ("/cache/a.safetensors", "/usr/local/lib/python3.12/site-packages/a.py", "/tmp-other/a.py"):
            assert not kernel_match(path)


def test_default_code_policy_identity_index_can_classify_kernel_only_event(tmp_path):
    document = intent_document()
    assert document.get("tetragon_kernel_identity", False) is False
    manifests = render_manifests(parse_intent(document))
    # Exercise the collector's actual spec-based decision, without a cluster.
    def runner(command):
        items = list(manifests.values()) if "tracingpoliciesnamespaced" in command else []
        return SimpleNamespace(returncode=0, stdout=json.dumps({"items": items}))
    index = IdentityCollector("test", "serving-lab", "app=serving-engine",
                              tmp_path / "identities.json", runner=runner).collect()
    assert index["state"] == "ok"
    code = next(p for p in index["policies"] if p["name"] == "runtime-tg-code-01")
    assert code["kernel_identity"] is True
    assert all(not p["kernel_identity"] for p in index["policies"] if p != code)
    index["generated_at"] = AT.isoformat()
    index["records"] = [{"node": "node", "namespace": "serving-lab", "pod_name": "serving-engine-demo",
        "pod_uid": "pod-1", "container_name": "engine", "container_id": "a" * 64,
        "binary": "/usr/bin/python3", "node_pid": 77, "ns_pid": 12, "pidns": 123,
        "start_ticks": 1000, "clock_ticks": 100, "stable": True, "labels": {"app": "serving-engine"}}]
    raw = raw_event()
    raw["node_name"] = "node"
    event = raw["process_kprobe"]
    event["process"] = {"pid": 42, "flags": "unknown"}
    event["data"] = [{"label": "runtime_pid", "int_arg": 42},
        {"label": "runtime_start_ns", "size_arg": "10005000000"},
        {"label": "runtime_pidns", "uint_arg": 123}]
    result = parse(raw, phase_policy(tmp_path), identity_index=index)
    assert result["identity"]["state"] == "confirmed"
    assert result["identity"]["source"] == "kernel_task_procfs"
    assert result["code_load_verdict"] == "detected"
    event.pop("data")
    assert parse(raw, phase_policy(tmp_path), identity_index=index)["code_load_verdict"] == "unknown"


@pytest.mark.parametrize("engine", ["gpu-lab", "gpu-lab-sglang"])
@pytest.mark.parametrize("suffix", [".py", ".pyc", ".so"])
def test_inferring_code_open_maps_to_sup_008_and_contract(tmp_path, engine, suffix):
    item = parse(raw_event("/cache/hf/modules/demo" + suffix), phase_policy(tmp_path, engine=engine))
    assert item["code_load_verdict"] == "detected"
    assert item["phase"] == ["inferring"] and item["phase_basis"] == ["engine_signal"]
    assert item["phase_trusted"] == []  # observation signal is not an approval
    assert (item["pod_uid"], item["container_name"]) == ("pod-1", "engine")
    event = observation_event(item)
    assert (event["source"], event["category"], event["outcome"], event["phase"]) == ("tetragon", "file", "detected", "inferring")
    assert event["related_checks"] == ["SUP-08"]
    assert Contracts(DEFAULT_CONTRACTS_DIR).errors("runtime-event", event) == []
    row = next(r for r in coverage([event])["items"] if r["framework_id"] == "CHK-SUP-008")
    assert row["observed_events"] == 1 and row["observation_state"] == "observed"
    assert row["rules"] == ["request_code_selection", "TG-CODE-01", "launch_settings_drift"]
    assert row["sources"] == ["gateway", "tetragon", "target", "revalidation"]
    assert checks_for_record({"rule_id": "TG-CODE-01"}, "gateway") == ["OBS-01"]


@pytest.mark.parametrize("phase,path", [("loading", "/tmp/demo.py"), ("compiling", "/cache/jit.so"),
    ("inferring", "/usr/local/lib/python3.12/site-packages/lazy.py"),
    ("inferring", "/opt/venv/lib/python3.12/site-packages/lazy.py"),
    ("inferring", "/cache/weights.safetensors"), ("inferring", "/cache-other/demo.py")])
def test_startup_or_image_lazy_import_is_observed(tmp_path, phase, path):
    item = parse(raw_event(path), phase_policy(tmp_path, phase))
    assert item["code_load_verdict"] == "observed"
    assert observation_event(item)["outcome"] == "observed"


def test_unknown_phase_and_attribution_are_unknown(tmp_path):
    item = parse(raw_event())
    assert item["phase"] == ["unknown"] and observation_event(item)["outcome"] == "unknown"
    policy = ExecPolicy(parse_intent(intent_document()))
    assert observation_event(parse(raw_event(), policy))["outcome"] == "unknown"
    policy = phase_policy(tmp_path)
    for key, value in (("namespace", "other"), ("pod_labels", {}), ("container", {}),
                       ("container", {"name": "sidecar"}), ("uid", None)):
        raw = raw_event()
        raw["process_kprobe"]["process"]["pod"][key] = value
        item = parse(raw, policy)
        assert item["reason"] == "engine_attribution_unavailable"
        assert observation_event(item)["outcome"] == "unknown"
    item = parse(raw_event(), policy, identity_index={"state": "error"})
    assert item["identity"]["state"] == "unavailable"
    assert observation_event(item)["outcome"] == "unknown"


@pytest.mark.parametrize("state,age,pod", [("log_error", 0, "pod-1"), ("running", 30, "pod-1"), ("running", 0, "other")])
def test_phase_collector_gap_stale_and_restart_are_unknown(tmp_path, state, age, pod):
    collector = {"state": state, "pod_uid": pod, "pod_name": "serving-engine-demo",
                 "written_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(),
                 "last_log_at": None, "log_delivery_lag_seconds": None}
    item = parse(raw_event(), phase_policy(tmp_path, collector=collector))
    assert item["phase"] == ["unknown"] and item["phase_source_error"]
    assert observation_event(item)["outcome"] == "unknown"


@pytest.mark.parametrize("path", ["/tmp/bad\n.py", "/tmp/bad\x00.py", "/tmp/bad\x7f.py", "/tmp/\u202e.py", "/tmp/../cache/a.py", "relative.py", "/tmp/" + "a" * 5000 + ".py"])
def test_paths_are_bounded_sanitized_and_not_approved(tmp_path, path):
    item = parse(raw_event(path), phase_policy(tmp_path))
    assert len(item["path"]) <= 4096
    assert all(c not in item["path"] for c in ("\n", "\x00", "\x7f", "\u202e"))
    assert item["reason"] == "path_evidence_invalid"
    assert observation_event(item)["outcome"] == "unknown"


def test_missing_unresolved_or_inconsistent_path_is_unknown(tmp_path):
    policy = phase_policy(tmp_path)
    for args in ([], [{"file_arg": {"path": "/tmp/a.py", "flags": "unresolvedPathComponents"}}],
                 [{"file_arg": {"path": "/tmp/a.py"}}, {"file_arg": {"path": "/tmp/b.py"}}]):
        raw = raw_event()
        raw["process_kprobe"]["args"] = args
        item = parse(raw, policy)
        assert observation_event(item)["outcome"] == "unknown"


def test_custom_hf_modules_cache_and_invalid_configuration(tmp_path):
    policy = phase_policy(tmp_path)
    doc = intent_document()
    doc["code_load_watch_paths"] = ["/download/modules"]
    policy.intent = parse_intent(doc)
    assert parse(raw_event("/download/modules/hf/a.py"), policy)["code_load_verdict"] == "detected"
    assert "/download/modules/" in watch_prefixes(policy.intent)
    for paths in (["relative"], ["/a/../b"], ["/a\n"], ["/a\u202e"], ["/"], ["/a"] * 9, ["/" + "한" * 100]):
        doc["code_load_watch_paths"] = paths
        with pytest.raises(IntentError):
            parse_intent(doc)


def test_overlapping_phases_cannot_suppress_detection_as_startup(tmp_path):
    policy = phase_policy(tmp_path)
    path = tmp_path / "timeline.json"
    document = json.loads(path.read_text())
    document["entries"].append({**document["entries"][0], "phase": "compiling"})
    path.write_text(json.dumps(document))
    item = parse(raw_event(), policy)
    assert item["phase"] == ["compiling", "inferring"]
    assert item["reason"] == "phase_ambiguous"
    assert observation_event(item)["outcome"] == "unknown"


def test_code_rule_cannot_be_configured_to_enforce():
    doc = intent_document()
    doc["tetragon_allowed_modes"] = {"TG-CODE-01": ["monitor", "enforce"]}
    with pytest.raises(IntentError):
        parse_intent(doc)


def test_collection_gap_is_unavailable_and_feed_keeps_status(tmp_path):
    policy = phase_policy(tmp_path)
    item = parse(raw_event(), policy)
    for heartbeat in ({}, {"configured": True, "state": "reconnecting"},
                      {"configured": True, "state": "running", "open_gap": {"gap_id": "g1"}}):
        event = copy.deepcopy(item)
        apply_collection_evidence(event, heartbeat)
        assert observation_event(event)["outcome"] == "unknown"
    feed_path = tmp_path / "feed.jsonl"
    feed_path.write_text(json.dumps(raw_event()) + "\n")
    heartbeat_path = tmp_path / "heartbeat.json"
    heartbeat = {"schema": HEARTBEAT_SCHEMA, "kind": "tetragon", "feed_path": str(feed_path),
                 "started_at": (AT - timedelta(seconds=1)).isoformat(), "updated_at": AT.isoformat(),
                 "state": "reconnecting", "open_gap": {"gap_id": "g1", "opened_at": AT.isoformat()},
                 "processes": [{"name": "tetragon", "running": False, "returncode": 1}]}
    heartbeat_path.write_text(json.dumps(heartbeat))
    feed = ObservationFeed(kind="tetragon", path=feed_path, data_dir=tmp_path / "data", data_origin="synthetic",
                           environment="lab", run_id=None, exec_policy=policy, heartbeat_path=heartbeat_path)
    collector = Collector("observation_feed_tetragon", feed.collect)
    collector.collect()
    status = collector.snapshot()
    assert status["state"] == "error" and status["last_success_at"] is None
    assert status["last_value"]["writer"]["open_gap"]["gap_id"] == "g1"
    heartbeat.update(state="running", open_gap=None)
    heartbeat["processes"][0].update(running=True, returncode=None)
    heartbeat_path.write_text(json.dumps(heartbeat))
    collector.collect()
    assert collector.snapshot()["state"] == "ok"
    records, errors = feed.store.read_all()
    assert not errors and len(records) == 1 and records[0]["code_load_verdict"] == "detected"
