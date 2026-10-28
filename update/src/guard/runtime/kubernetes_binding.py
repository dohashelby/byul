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


def verify_owned_pod(pod, namespace, expected, run_id, *, require_ready=True):
    if namespace.get("uid") != expected["namespace_uid"] or namespace.get("deletionTimestamp"):
        raise ValueError
    meta, spec, status = pod["metadata"], pod["spec"], pod["status"]
    containers = [x for x in spec["containers"] if x["name"] == expected["container"]]
    states = [x for x in status["containerStatuses"] if x["name"] == expected["container"]]
    if len(containers) != 1 or len(states) != 1:
        raise ValueError
    container, state = containers[0], states[0]
    if (meta.get("name") != expected["pod"] or meta.get("namespace") != expected["namespace"]
            or meta.get("uid") != expected["pod_uid"] or meta.get("deletionTimestamp")
            or meta.get("labels", {}).get("guard.run") != run_id
            or spec.get("nodeName") != expected["node"]
            or any(spec.get(x, False) is not False for x in ("hostNetwork", "hostPID", "hostIPC"))
            or container.get("image") != expected["image"]
            or status.get("phase") != "Running" or status.get("podIP") != expected["pod_ip"]
            or state.get("containerID") != expected["container_id"]
            or state.get("imageID") != expected["image_id"]
            or type(state.get("restartCount")) is not int or state["restartCount"] != expected["restart_count"]
            or state.get("state", {}).get("running", {}).get("startedAt") != expected["started_at"]):
        raise ValueError
    if require_ready and (not any(x.get("type") == "Ready" and x.get("status") == "True"
            for x in status.get("conditions", [])) or state.get("ready") is not True):
        raise ValueError
    # The reported image ID keeps its Kubernetes meaning; it is not a Docker config ID.
    return {"transport":"kubernetes", "namespace_uid":expected["namespace_uid"], "pod_uid":meta["uid"],
        "container":expected["container"], "container_id":state["containerID"], "node":spec["nodeName"],
        "image_reference":container["image"], "reported_image_id":state["imageID"],
        "restart_count":state["restartCount"], "started_at":state["state"]["running"]["startedAt"]}


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
