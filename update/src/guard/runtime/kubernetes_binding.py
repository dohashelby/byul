"""Explicit Kubernetes namespace/Pod/container identity, shared by owned probes."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import ipaddress
import re

NAME = re.compile(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\Z")
UID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
PIN = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
FIELDS = ("namespace", "namespace_uid", "pod", "pod_uid", "container", "container_id",
          "image", "image_id", "restart_count", "node", "pod_ip", "started_at")


def validate_container_binding(item):
    if type(item) is not dict or set(item) != set(FIELDS):
        raise ValueError
    for field in ("namespace", "pod", "container", "node"):
        if type(item[field]) is not str or not NAME.fullmatch(item[field]):
            raise ValueError
    for field in ("namespace_uid", "pod_uid"):
        if type(item[field]) is not str or not UID.fullmatch(item[field]):
            raise ValueError
    if (not PIN.fullmatch(item["image"])
            or not re.fullmatch(r"containerd://[0-9a-f]{64}", item["container_id"])
            or type(item["image_id"]) is not str or len(item["image_id"]) > 300
            or not re.fullmatch(r"(?:sha256:[0-9a-f]{64}|(?:docker-pullable://)?[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64})", item["image_id"])
            or type(item["restart_count"]) is not int or item["restart_count"] < 0
            or datetime.fromisoformat(item["started_at"].replace("Z", "+00:00")).tzinfo is None
            or ipaddress.ip_address(item["pod_ip"]).version != 4):
        raise ValueError


def verify_owned_pod(pod, namespace, expected, run_id, *, require_ready=True, terminated=False, allow_terminating=False):
    if (type(allow_terminating) is not bool or allow_terminating
            and (require_ready is not False or terminated is not False)):
        raise ValueError
    if namespace.get("uid") != expected["namespace_uid"] or namespace.get("deletionTimestamp"):
        raise ValueError
    meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
    containers = [x for x in spec["containers"] if x["name"] == expected["container"]]
    states = [x for x in status["containerStatuses"] if x["name"] == expected["container"]]
    if len(containers) != 1 or len(states) != 1:
        raise ValueError
    container, state = containers[0], states[0]
    pending_deletion=allow_terminating and meta.get("deletionTimestamp") is not None
    if pending_deletion:
        deletion=datetime.fromisoformat(meta["deletionTimestamp"].replace("Z","+00:00"))
        if deletion.tzinfo is None:raise ValueError
    terminal_state=terminated or pending_deletion and type(state.get("state",{}).get("terminated")) is dict
    lifecycle=state.get("state",{}).get("terminated" if terminal_state else "running",{})
    phase_ok=(status.get("phase") in {"Succeeded","Failed"} if terminated else
        status.get("phase") in {"Running","Succeeded","Failed"} if pending_deletion and terminal_state else
        status.get("phase")=="Running")
    if terminal_state:
        if type(lifecycle.get("exitCode")) is not int:raise ValueError
        finished=datetime.fromisoformat(lifecycle["finishedAt"].replace("Z","+00:00"))
        started=datetime.fromisoformat(expected["started_at"].replace("Z","+00:00"))
        if finished.tzinfo is None or finished<started:raise ValueError
    if (meta.get("name") != expected["pod"] or meta.get("namespace") != expected["namespace"]
            or meta.get("uid") != expected["pod_uid"] or meta.get("deletionTimestamp") and not (terminated or pending_deletion)
            or meta.get("labels", {}).get("guard.run") != run_id
            or spec.get("nodeName") != expected["node"]
            or any(spec.get(x, False) is not False for x in ("hostNetwork", "hostPID", "hostIPC"))
            or container.get("image") != expected["image"]
            or not phase_ok or status.get("podIP") != expected["pod_ip"]
            or state.get("containerID") != expected["container_id"]
            or state.get("imageID") != expected["image_id"]
            or type(state.get("restartCount")) is not int or state["restartCount"] != expected["restart_count"]
            or lifecycle.get("startedAt") != expected["started_at"]):
        raise ValueError
    if require_ready and (not any(x.get("type") == "Ready" and x.get("status") == "True"
            for x in status.get("conditions", [])) or state.get("ready") is not True):
        raise ValueError
    # The reported image ID keeps its Kubernetes meaning; it is not a Docker config ID.
    return {"transport":"kubernetes", "namespace_uid":expected["namespace_uid"], "pod_uid":meta["uid"],
        "container":expected["container"], "container_id":state["containerID"], "node":spec["nodeName"],
        "image_reference":container["image"], "reported_image_id":state["imageID"],
        "restart_count":state["restartCount"], "started_at":lifecycle["startedAt"]}


def project_owned_snapshot(pod, namespace, expected, run_id, *, require_ready=True):
    """Existing in-memory {pod, namespace} input, restricted to identity/mount facts.

    Verify the original API objects before projecting. This is not an export
    schema or node-side image proof. Callers must not persist this input: model
    host paths remain transient declarations for the existing A-2 consumer.
    """
    if (pod.get("kind") != "Pod" or namespace.get("kind") != "Namespace"
            or namespace["metadata"].get("name") != expected["namespace"]):
        raise ValueError
    verify_owned_pod(pod, namespace["metadata"], expected, run_id, require_ready=require_ready)

    def fields(value, names):
        if type(value) is not dict:
            raise ValueError
        return {key: deepcopy(value[key]) for key in names if key in value}

    def rows(value, names):
        if type(value) is not list:
            raise ValueError
        return [fields(row, names) for row in value]

    container, = [row for row in pod["spec"]["containers"] if row["name"] == expected["container"]]
    selected = fields(container, ("name", "image"))
    if "volumeMounts" in container:
        selected["volumeMounts"] = rows(container["volumeMounts"],
            ("name", "mountPath", "readOnly", "subPath", "subPathExpr", "mountPropagation"))
    spec = fields(pod["spec"], ("nodeName", "hostNetwork", "hostPID", "hostIPC"))
    spec["containers"] = [selected]
    if "volumes" in pod["spec"]:
        volumes = pod["spec"]["volumes"]
        if type(volumes) is not list:
            raise ValueError
        spec["volumes"] = []
        for volume in volumes:
            row = fields(volume, ("name",))
            if "hostPath" in volume:
                row["hostPath"] = fields(volume["hostPath"], ("path", "type"))
            spec["volumes"].append(row)
    state, = [row for row in pod["status"]["containerStatuses"] if row["name"] == expected["container"]]
    selected_state = fields(state, ("name", "containerID", "imageID", "restartCount", "ready"))
    selected_state["state"] = {"running": fields(state["state"]["running"], ("startedAt",))}
    status = fields(pod["status"], ("phase", "podIP"))
    status["containerStatuses"] = [selected_state]
    conditions = pod["status"].get("conditions", [])
    if type(conditions) is not list or any(type(row) is not dict for row in conditions):
        raise ValueError
    status["conditions"] = rows([row for row in conditions if row.get("type") == "Ready"], ("type", "status"))
    metadata = fields(pod["metadata"], ("name", "namespace", "uid", "deletionTimestamp"))
    metadata["labels"] = {"guard.run": pod["metadata"]["labels"]["guard.run"]}
    return {"pod": {"kind": "Pod", "metadata": metadata, "spec": spec, "status": status},
            "namespace": {"kind": "Namespace", "metadata": fields(namespace["metadata"],
                ("name", "uid", "deletionTimestamp"))}}


def validate_capture_target(target, env):
    try:
        if (type(target) is not dict or set(target) != {"schema","run_id","environment_ref","lab","kubernetes"}
                or target["schema"] != "guard-kubernetes-lifecycle-target/1" or target["lab"] is not True
                or not env.hash_ok() or target["environment_ref"] != env.ref().model_dump(mode="json")
                or type(target["run_id"]) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}",target["run_id"])):
            raise ValueError
        value=target["kubernetes"]
        if (type(value) is not dict or set(value)!={"context","engine"} or type(value["context"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9._:@/-]{1,200}",value["context"])):
            raise ValueError
        validate_container_binding(value["engine"])
        if value["engine"]["image"] != env.engine.image_digest:
            raise ValueError
        return {"context":value["context"],"run_id":target["run_id"],"engine":deepcopy(value["engine"])}
    except (KeyError,TypeError,ValueError,AttributeError):
        raise ValueError("KUBERNETES_CAPTURE_TARGET_INVALID") from None
