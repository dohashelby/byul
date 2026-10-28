"""Pod spec의 모델 mount 선언만 분석한다. 실제 worker 접근/보안 판정은 아님."""
from copy import deepcopy
import json
import pytest

from guard.collectors.model_mounts import analyze_model_mount
from tests.test_model_mapping import declared_mapping
from tests.test_kubernetes_sampling import inputs, IMAGE


@pytest.fixture
def case():
    env, mapping = declared_mapping()
    env = env.model_copy(update={"engine": env.engine.model_copy(update={"image_digest": IMAGE})}).seal()
    mapping["environment_ref"] = env.ref().model_dump(mode="json")
    role = deepcopy(inputs()["kubernetes"]["engine"])
    target = {"schema": "guard-kubernetes-lifecycle-target/1", "lab": True, "run_id": "guard-fixture",
        "environment_ref": env.ref().model_dump(mode="json"),
        "kubernetes": {"context": "fixture-context", "engine": role}}
    snapshot = {"namespace": {"kind": "Namespace", "metadata": {"name": role["namespace"], "uid": role["namespace_uid"]}},
        "pod": {"kind": "Pod", "metadata": {"name": role["pod"], "namespace": role["namespace"],
            "uid": role["pod_uid"], "labels": {"guard.run": target["run_id"]},
            "annotations": {"private": "EXAMPLE_PRIVATE_POD_METADATA"}},
        "spec": {"nodeName": role["node"], "containers": [{"name": role["container"], "image": role["image"],
            "env": [{"name": "TOKEN", "value": "EXAMPLE_PRIVATE_TOKEN"}],
            "volumeMounts": [{"name": "model", "mountPath": "/models", "readOnly": True}]}],
            "volumes": [{"name": "model", "hostPath": {"path": mapping["host_snapshot"], "type": "Directory"}}]},
        "status": {"phase": "Running", "podIP": role["pod_ip"], "conditions": [{"type": "Ready", "status": "True"}],
            "containerStatuses": [{"name": role["container"], "containerID": role["container_id"],
                "imageID": role["image_id"], "restartCount": role["restart_count"], "ready": True,
                "state": {"running": {"startedAt": role["started_at"]}}}]}}}
    return env, mapping, target, snapshot


@pytest.mark.parametrize("read_only", [True, False])
def test_bound_mount_setting_is_collected_without_becoming_actual_access_or_a_verdict(case, read_only):
    env, mapping, target, snapshot = case
    snapshot["pod"]["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = read_only
    before = deepcopy(snapshot)
    result = analyze_model_mount(env, target, mapping, snapshot, deepcopy(snapshot))
    assert result["collected"] and result["observations"]["declared_read_only"] is read_only
    assert result["observations"]["host_snapshot_matches"] is True
    assert result["worker_access_verified"] is result["producer_authenticated"] is False
    assert result["interface_status"] == "PROPOSED_CONSUMER"
    assert "verdict" not in result and "security" not in result
    assert "PRIVATE" not in json.dumps(result) and "TOKEN" not in json.dumps(result)
    assert snapshot == before


@pytest.mark.parametrize("change", ["pod", "namespace", "container", "restart", "image", "started",
    "not-ready", "missing", "mount-path", "host-path", "subpath", "duplicate", "setting-change"])
def test_replacement_restart_missing_and_mount_mismatch_withhold_the_interval(case, change):
    env, mapping, target, snapshot = case
    after = deepcopy(snapshot)
    pod = after["pod"]
    state = pod["status"]["containerStatuses"][0]
    container = pod["spec"]["containers"][0]
    if change == "pod": pod["metadata"]["uid"] = "20000000-0000-0000-0000-000000000001"
    if change == "namespace": after["namespace"]["metadata"]["uid"] = "20000000-0000-0000-0000-000000000002"
    if change == "container": state["containerID"] = "containerd://" + "9" * 64
    if change == "restart": state["restartCount"] += 1
    if change == "image": state["imageID"] = "sha256:" + "b" * 64
    if change == "started": state["state"]["running"]["startedAt"] = "2026-10-27T00:00:00Z"
    if change == "not-ready": state["ready"] = False
    if change == "missing": after = {}
    if change == "mount-path": container["volumeMounts"][0]["mountPath"] = "/other"
    if change == "host-path": pod["spec"]["volumes"][0]["hostPath"]["path"] = "/srv/other"
    if change == "subpath": container["volumeMounts"][0]["subPath"] = "model"
    if change == "duplicate": container["volumeMounts"].append(deepcopy(container["volumeMounts"][0]))
    if change == "setting-change": container["volumeMounts"][0]["readOnly"] = False
    prior = analyze_model_mount(env, target, mapping, snapshot, deepcopy(snapshot))
    original = deepcopy(prior)
    result = analyze_model_mount(env, target, mapping, snapshot, after)
    assert not result["collected"] and not result["observations"]
    assert result["diagnostic_codes"] == ["MODEL_MOUNT_SOURCE_UNVERIFIED"]
    assert prior == original and prior["collected"]


def test_foreign_environment_target_cannot_supply_mount_facts(case):
    env, mapping, target, snapshot = case
    target["environment_ref"]["sha256"] = "0" * 64
    result = analyze_model_mount(env, target, mapping, snapshot, snapshot)
    assert not result["collected"] and not result["observations"]
