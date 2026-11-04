"""PLT-06 real mapper: overlapping names cannot invent a second worker identity."""
from types import SimpleNamespace

from guard.executors import ipc_trust_scope as scope


def row(pid, comm, uid="1000", shared=("/memfd:EXAMPLE",)):
    return {"pid": pid, "comm": comm, "uid": uid, "gid": uid,
            "namespaces": {"pid": "pid:[123]"}, "shared": list(shared), "fd_complete": True}


def test_one_pid_matching_two_markers_is_incomplete_without_false_fail(monkeypatch):
    values = {"worker_isolation_requirement": "independent-uid",
              "worker_trust_group_markers": ["VLLM", "Worker"]}
    snapshot = {"processes": [row(101, "VLLM::Worker")]}
    projected = scope.project(snapshot, values)
    assert projected["overlapping_marker_pids"] == [101]
    assert projected["missing"] == ["VLLM", "Worker"]
    assert projected["mapping"] == []
    assert projected["violations"] == []
    assert projected["shared"] == []
    monkeypatch.setattr(scope, "run_helper", lambda *args, **kwargs: snapshot)
    ctx = SimpleNamespace(policy_values=values, target={"engine": {"container": "EXAMPLE"}},
                          check=SimpleNamespace(challenge_steps=[]))
    observations, _, _, summary = scope.collect(ctx, object())
    assert observations["worker_isolation_violations"]["value"] == []
    assert observations["worker_trust_map_complete"]["value"] is False
    assert "WORKER_TRUST_GROUP_MARKERS_OVERLAP" in summary["diagnostic_codes"]


def test_two_overlapping_pids_do_not_invent_distinct_trust_groups(monkeypatch):
    values = {"worker_isolation_requirement": "independent-uid",
              "worker_trust_group_markers": ["VLLM", "Worker"]}
    snapshot = {"processes": [row(101, "VLLM::Worker"), row(102, "VLLM::Worker")]}
    projected = scope.project(snapshot, values)
    assert projected["overlapping_marker_pids"] == [101, 102]
    assert projected["mapping"] == [] and projected["missing"] == ["VLLM", "Worker"]
    assert projected["violations"] == [] and projected["shared"] == []
    monkeypatch.setattr(scope, "run_helper", lambda *args, **kwargs: snapshot)
    ctx = SimpleNamespace(policy_values=values, target={"engine": {"container": "EXAMPLE"}},
                          check=SimpleNamespace(challenge_steps=[]))
    observations, _, _, summary = scope.collect(ctx, object())
    assert observations["worker_isolation_violations"]["value"] == []
    assert observations["worker_trust_map_complete"]["value"] is False
    assert summary["overlapping_marker_pids"] == [101, 102]


def test_overlap_does_not_hide_exclusive_pid_violation_with_later_missing(monkeypatch):
    values = {"worker_isolation_requirement": "independent-uid",
              "worker_trust_group_markers": ["VLLM", "Worker", "Missing"]}
    snapshot = {"processes": [row(101, "VLLM::Worker"), row(102, "VLLM-core"), row(103, "Worker-A")]}
    projected = scope.project(snapshot, values)
    assert projected["overlapping_marker_pids"] == [101]
    assert projected["missing"] == ["Missing"]
    assert projected["violations"] == [{"groups": ["VLLM", "Worker"], "shared_uid": ["1000"]}]
    assert len(projected["shared"]) == 1
    assert [entry["pids"] for entry in projected["mapping"]] == [[102], [103]]
    monkeypatch.setattr(scope, "run_helper", lambda *args, **kwargs: snapshot)
    ctx = SimpleNamespace(policy_values=values, target={"engine": {"container": "EXAMPLE"}},
                          check=SimpleNamespace(challenge_steps=[]))
    observations, _, _, _ = scope.collect(ctx, object())
    assert observations["worker_isolation_violations"]["value"] == projected["violations"]
    assert observations["worker_trust_groups_missing"]["value"] == ["Missing"]
    assert observations["worker_trust_map_complete"]["value"] is False


def test_exclusive_distinct_pids_with_different_uid_and_resources_are_normal():
    values = {"worker_isolation_requirement": "independent-uid",
              "worker_trust_group_markers": ["Core", "Worker"]}
    snapshot = {"processes": [row(201, "Core", "1000", ("/memfd:EXAMPLE-A",)),
                              row(202, "Worker", "2000", ("/memfd:EXAMPLE-B",))]}
    projected = scope.project(snapshot, values)
    assert projected["overlapping_marker_pids"] == [] and projected["missing"] == []
    assert projected["violations"] == [] and projected["shared"] == []


def test_distinct_pids_same_uid_preserve_fail_even_when_later_group_missing(monkeypatch):
    values = {"worker_isolation_requirement": "independent-uid",
              "worker_trust_group_markers": ["Core", "Worker", "Missing"]}
    snapshot = {"processes": [row(201, "Core"), row(202, "Worker")]}
    projected = scope.project(snapshot, values)
    assert projected["violations"] == [{"groups": ["Core", "Worker"], "shared_uid": ["1000"]}]
    assert projected["missing"] == ["Missing"]
    monkeypatch.setattr(scope, "run_helper", lambda *args, **kwargs: snapshot)
    ctx = SimpleNamespace(policy_values=values, target={"engine": {"container": "EXAMPLE"}},
                          check=SimpleNamespace(challenge_steps=[]))
    observations, _, _, _ = scope.collect(ctx, object())
    assert observations["worker_isolation_violations"]["value"] == projected["violations"]
    assert observations["worker_trust_map_complete"]["value"] is False
