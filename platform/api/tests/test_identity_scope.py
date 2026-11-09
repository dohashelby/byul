import json
from datetime import datetime, timezone

from byul_platform.sources.runtime import observation_event
from serving_runtime.mvp.identity_scope import identity_scope_ref
from serving_runtime.mvp.phase_timeline import read_phase_timeline


def test_source_scope_is_shared_without_exposing_raw_timeline_identity(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    collector = dict(state="running", context="cluster-a", namespace="serving-lab", pod_uid="private-pod-uid",
                     pod_name="private-pod-name", container_id="containerd://" + "a" * 64,
                     written_at=now, last_log_at=None, log_delivery_lag_seconds=None)
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(dict(schema_version="runtime-phase-timeline/1.0", entries=[], collector=collector)))
    timeline = read_phase_timeline(str(path))
    expected = identity_scope_ref("cluster-a", "serving-lab", "private-pod-uid", "a" * 64)
    assert timeline["scope_ref"] == expected and timeline["collector"]["state"] == "running"
    assert "private-pod" not in json.dumps(timeline) and "containerd://" not in json.dumps(timeline)
    raw = {"source": "tetragon", "pod": {"namespace": "serving-lab"}, "identity": {
        "state": "confirmed", "context": "cluster-a", "pod_uid": "private-pod-uid", "container_id": "a" * 64}}
    assert observation_event(raw)["observation"]["scope_ref"] == expected
    raw["identity"]["container_id"] = "b" * 64
    assert observation_event(raw)["observation"]["scope_ref"] != expected
    raw["identity"]["state"] = "conflict"
    assert observation_event(raw)["observation"]["scope_ref"] is None
    assert identity_scope_ref("cluster-a", "serving-lab", "private-pod-uid", "partial") is None


def test_legacy_and_invalid_timeline_identity_never_claim_a_scope(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    collector = dict(state="running", pod_uid="private", pod_name="private", written_at=now,
                     last_log_at=None, log_delivery_lag_seconds=None)
    path = tmp_path / "timeline.json"
    value = dict(schema_version="runtime-phase-timeline/1.0", entries=[], collector=collector)
    path.write_text(json.dumps(value))
    assert read_phase_timeline(str(path))["scope_ref"] is None
    collector["container_id"] = {"unexpected": "object"}
    path.write_text(json.dumps(value))
    assert read_phase_timeline(str(path))["collector"]["state"] == "error"
