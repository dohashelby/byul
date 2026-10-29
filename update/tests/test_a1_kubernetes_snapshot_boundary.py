"""A-1 in-memory snapshot boundary through existing HTTP/lifecycle consumers."""
from copy import deepcopy
import json

import pytest

from guard.executors.kubernetes_lifecycle_capture import KubernetesLifecycleReader
from guard.executors.model_mounts import analyze_model_mount
from guard.runtime.kubernetes_binding import project_owned_snapshot, verify_owned_pod
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from tests.test_kubernetes_lifecycle import group, target, World as LifecycleWorld
from tests.test_lifecycle_capture import case, external
from tests.test_lifecycle_sensor import writer_factory
from tests.test_model_preparation_integration import full_inputs, FullWorld


PRIVATE = "EXAMPLE_A1_SNAPSHOT_PRIVATE_f377"


def transport_and_snapshot():
    env, target = full_inputs()
    world = FullWorld(target)
    namespaces = []

    def runner(argv, **kwargs):
        packet = world.runner(argv, **kwargs)
        value = json.loads(packet["stdout_bytes"])
        if "namespaces" in argv:
            for row in value["items"]:
                row["metadata"].update(annotations={"private": PRIVATE}, managedFields=[PRIVATE])
            namespaces.extend(deepcopy(value["items"]))
        else:
            value["metadata"].update(annotations={"private": PRIVATE}, managedFields=[PRIVATE])
            value["metadata"]["labels"]["private"] = PRIVATE
            selected = value["spec"]["containers"][0]
            selected.update(env=[{"name": "PRIVATE", "value": PRIVATE}], command=[PRIVATE], args=[PRIVATE],
                envFrom=[{"secretRef": {"name": PRIVATE}}])
            value["spec"]["containers"].append({"name": "sidecar", "image": PRIVATE})
            value["spec"].update(imagePullSecrets=[{"name": PRIVATE}])
            value["spec"].setdefault("volumes", []).append({"name": "credentials", "secret": {"secretName": PRIVATE}})
            value["status"].update(message=PRIVATE, reason=PRIVATE)
            value["status"]["conditions"][0].update(message=PRIVATE, reason=PRIVATE)
            value["status"]["containerStatuses"][0].update(lastState={"terminated": {"message": PRIVATE}})
        packet["stdout_bytes"] = json.dumps(value).encode()
        return packet

    transport = KubernetesHTTP(target, env, duration=30, runner=runner, profile=REQUEST_BINDING_PROFILE)
    pod = transport._verify()["engine"]
    namespace = next(row for row in namespaces if row["metadata"]["name"] == target["kubernetes"]["engine"]["namespace"])
    return env, target, transport, pod, namespace


def lifecycle_target(target):
    return {"schema": "guard-kubernetes-lifecycle-target/1", "lab": target["lab"],
        "environment_ref": target["environment_ref"], "run_id": target["kubernetes"]["run_id"],
        "kubernetes": {"context": target["kubernetes"]["context"], "engine": target["kubernetes"]["engine"]}}


def test_http_hands_existing_a2_consumer_a_detached_allowlisted_pair():
    env, target, transport, pod, namespace = transport_and_snapshot()
    snapshot, = transport.model_snapshots
    assert PRIVATE in json.dumps(pod) and PRIVATE not in json.dumps(snapshot)
    assert set(snapshot) == {"pod", "namespace"}
    assert len(snapshot["pod"]["spec"]["containers"]) == 1
    role = transport.binding["engine"]
    owner = verify_owned_pod(pod, namespace["metadata"], role, transport.binding["run_id"])
    assert verify_owned_pod(snapshot["pod"], snapshot["namespace"]["metadata"], role, transport.binding["run_id"]) == owner
    original = {"pod": pod, "namespace": namespace}
    raw_result = analyze_model_mount(env, lifecycle_target(target), target["model_mapping"], original, original)
    assert raw_result["collected"]
    assert analyze_model_mount(env, lifecycle_target(target), target["model_mapping"], snapshot, snapshot) == raw_result
    snapshot["pod"]["spec"]["containers"][0]["volumeMounts"][-1]["readOnly"] = False
    assert pod["spec"]["containers"][0]["volumeMounts"][-1]["readOnly"] is True


@pytest.mark.parametrize("damage", ["writable", "subpath", "subpath_expr", "host_path", "host_type", "duplicate_mount", "duplicate_volume", "missing_volume"])
def test_projection_keeps_existing_mount_declaration_and_rejection_meanings(damage):
    env, target, transport, pod, namespace = transport_and_snapshot()
    mount = pod["spec"]["containers"][0]["volumeMounts"][-1]
    volume = pod["spec"]["volumes"][0]
    if damage == "writable":
        mount["readOnly"] = False
    elif damage in {"subpath", "subpath_expr"}:
        mount["subPath" if damage == "subpath" else "subPathExpr"] = "partial"
    elif damage == "host_path":
        volume["hostPath"]["path"] = "/other"
    elif damage == "host_type":
        volume["hostPath"]["type"] = "DirectoryOrCreate"
    elif damage == "duplicate_mount":
        pod["spec"]["containers"][0]["volumeMounts"].append(deepcopy(mount))
    elif damage == "duplicate_volume":
        pod["spec"]["volumes"].append(deepcopy(volume))
    else:
        pod["spec"]["volumes"] = []
    original = {"pod": pod, "namespace": namespace}
    snapshot = project_owned_snapshot(pod, namespace, transport.binding["engine"], transport.binding["run_id"])
    raw_result = analyze_model_mount(env, lifecycle_target(target), target["model_mapping"], original, original)
    assert analyze_model_mount(env, lifecycle_target(target), target["model_mapping"], snapshot, snapshot) == raw_result
    assert raw_result["collected"] is (damage == "writable")
    if damage == "writable":
        assert raw_result["observations"]["declared_read_only"] is False
    assert raw_result["worker_access_verified"] is raw_result["producer_authenticated"] is False


@pytest.mark.parametrize("damage", ["namespace_name", "namespace_uid", "pod_kind", "pod_uid", "owner", "image", "image_id", "restart", "started_at"])
def test_unverified_original_never_becomes_a_verified_projection(damage):
    _, _, transport, pod, namespace = transport_and_snapshot()
    state = pod["status"]["containerStatuses"][0]
    if damage == "namespace_name":
        namespace["metadata"]["name"] = "other"
    elif damage == "namespace_uid":
        namespace["metadata"]["uid"] = "other"
    elif damage == "pod_kind":
        pod["kind"] = "ConfigMap"
    elif damage == "pod_uid":
        pod["metadata"]["uid"] = "other"
    elif damage == "owner":
        pod["metadata"]["labels"]["guard.run"] = "other"
    elif damage == "image":
        pod["spec"]["containers"][0]["image"] = "other"
    elif damage == "started_at":
        state["state"]["running"]["startedAt"] = "2026-10-28T00:00:00Z"
    else:
        state[{"image_id": "imageID", "restart": "restartCount"}[damage]] = "other"
    with pytest.raises(ValueError):
        project_owned_snapshot(pod, namespace, transport.binding["engine"], transport.binding["run_id"])


@pytest.mark.parametrize("ready", [True, False])
def test_lifecycle_snapshot_is_sanitized_and_keeps_readiness_separate(group, target, ready):
    world = LifecycleWorld(group, target, None if ready else "not-ready")
    reader = KubernetesLifecycleReader(target, group.env, group.prepared, runner=world.runner)
    snapshot = reader.snapshot()
    assert "EXAMPLE_KUBE_CAPTURE_PRIVATE_73CB" not in json.dumps(snapshot)
    role = reader.binding["engine"]
    owner = verify_owned_pod(snapshot["pod"], snapshot["namespace"]["metadata"], role, reader.binding["run_id"], require_ready=False)
    assert owner["reported_image_id"] == role["image_id"]
    assert owner["image_reference"] == role["image"] != owner["reported_image_id"]
    assert reader.image_check({}, {}) is False
    if not ready:
        with pytest.raises(ValueError):
            verify_owned_pod(snapshot["pod"], snapshot["namespace"]["metadata"], role, reader.binding["run_id"])
    assert reader.report()["node_runtime_image_verified"] is False
