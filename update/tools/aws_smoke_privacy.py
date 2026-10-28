"""Exact bootstrap capture/projection, not a Kubernetes execution framework.

The prepared handoff copies this and the existing bounded pipe helper. No raw
API response, Pod spec, native log or exception is written to evidence files.
"""
from __future__ import annotations

import argparse
import builtins
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import ipaddress
import io
import json
import os
from pathlib import Path
import re
import sys

try:
    from guard.runtime.bounded import capture_pipes
except ModuleNotFoundError as exc:
    if exc.name != "guard":
        raise
    from bounded_capture import capture_pipes

CONTEXT = "byul-aws-lab"
NAMESPACE = "serving-lab"
BASELINE_SHA = "2b620d109fb02faff87e39ddbfe5468514331236dc20260c9630a03a74020082"
REASONS = {"Completed", "Error", "OOMKilled", "ContainerCannotRun", "ImagePullBackOff", "ErrImagePull",
           "CrashLoopBackOff", "CreateContainerConfigError", "FailedScheduling", "FailedMount", "BackOff",
           "Pulling", "Pulled", "Created", "Started", "Killing", "Unhealthy", "DeadlineExceeded"}


class CaptureError(Exception):
    def __init__(self, code, record=None):
        super().__init__(code)
        self.record = record


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def write(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def call(args, *, json_result=True, timeout=60, cap=16 * 1024**2):
    try:
        result = capture_pipes(["kubectl", f"--context={CONTEXT}", f"--request-timeout={timeout}s", *args],
                               timeout=timeout, env_extra={}, max_stdout_bytes=cap, max_stderr_bytes=4096)
        if result["returncode"] != 0 or not result["capture"]["complete"]:
            raise CaptureError("KUBERNETES_CAPTURE_UNAVAILABLE", {"process_exit_code": result["returncode"],
                "retained_bytes": len(result["stdout_bytes"]), "retained_sha256": digest(result["stdout_bytes"]),
                "capture_complete": result["capture"]["complete"] is True})
        return json.loads(result["stdout_bytes"]) if json_result else result
    except CaptureError:
        raise
    except Exception:
        raise CaptureError("KUBERNETES_CAPTURE_UNAVAILABLE") from None


def metadata(value):
    source = value.get("metadata", {})
    result = {key: source[key] for key in ("name", "namespace", "uid", "resourceVersion") if type(source.get(key)) is str}
    labels = source.get("labels", {})
    result["labels"] = {}
    for key, valid in (("app", {"serving-engine", "serving-gateway", "engine-test-probe"}), ("lab-profile", {"dev", "reference"})):
        if labels.get(key) in valid:
            result["labels"][key] = labels[key]
    if type(labels.get("guard.run")) is str and re.fullmatch(r"guard-[a-z0-9-]{1,55}", labels["guard.run"]):
        result["labels"]["guard.run"] = labels["guard.run"]
    return result


def container(value):
    result = {key: value[key] for key in ("name", "image", "imageID", "containerID") if type(value.get(key)) is str}
    for key in ("ready", "restartCount"):
        if type(value.get(key)) in (bool, int):
            result[key] = value[key]
    for phase in ("waiting", "running", "terminated"):
        state = value.get("state", {}).get(phase)
        if type(state) is dict:
            result["state"] = phase
            if state.get("reason") in REASONS:
                result["reason"] = state["reason"]
            if type(state.get("exitCode")) is int:
                result["exitCode"] = state["exitCode"]
            if type(state.get("startedAt")) is str:
                result["startedAt"] = state["startedAt"]
    resources = {}
    for group in ("requests", "limits"):
        values = {key: str(raw) for key, raw in value.get("resources", {}).get(group, {}).items()
                  if key in {"cpu", "memory", "nvidia.com/gpu"} and type(raw) in (str, int)
                  and re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:m|Ki|Mi|Gi|Ti)?", str(raw))}
        if values:
            resources[group] = values
    if resources:
        result["resources"] = resources
    return result


def project(item):
    result = {"kind": item.get("kind"), "metadata": metadata(item), "source_canonical_sha256": digest(canonical(item))}
    spec, status = item.get("spec", {}), item.get("status", {})
    if item.get("kind") == "Node":
        result["nodeInfo"] = {k: status["nodeInfo"][k] for k in ("kernelVersion", "osImage", "containerRuntimeVersion",
                              "kubeletVersion", "architecture", "operatingSystem") if k in status.get("nodeInfo", {})}
        result["conditions"] = {x["type"]: x["status"] for x in status.get("conditions", [])
                                if x.get("type") in {"Ready", "MemoryPressure", "DiskPressure", "PIDPressure", "NetworkUnavailable"}
                                and x.get("status") in {"True", "False", "Unknown"}}
        result["gpu_allocatable"] = status.get("allocatable", {}).get("nvidia.com/gpu")
    elif item.get("kind") == "Pod":
        result["phase"] = status.get("phase") if status.get("phase") in {"Pending", "Running", "Succeeded", "Failed", "Unknown"} else "Unknown"
        result["node"] = spec.get("nodeName")
        result["containers"] = [container(x) for x in status.get("containerStatuses", [])]
        result["spec_images"] = [container(x) for x in spec.get("containers", [])]
        result["init_container_count"] = len(spec.get("initContainers", []))
    elif item.get("kind") == "Event":
        result["reason"] = item.get("reason") if item.get("reason") in REASONS else "UNRECOGNIZED_EVENT"
    elif item.get("kind") in {"Deployment", "Job"}:
        result["containers"] = [container(x) for x in spec.get("template", {}).get("spec", {}).get("containers", [])]
        result["conditions"] = [{"type": c["type"], "status": c["status"]} for c in status.get("conditions", [])
                                if c.get("type") in {"Complete", "Failed", "Available", "Progressing"} and c.get("status") in {"True", "False", "Unknown"}]
    return result


def projected(value):
    return {"schema": "byul-bootstrap-projection/1", "items": [project(x) for x in value.get("items", [value])]}


def gpu_info():
    try:
        result = capture_pipes(["nvidia-smi", "--query-gpu=uuid,name,driver_version,memory.total", "--format=csv,noheader,nounits"],
                               timeout=30, env_extra={}, max_stdout_bytes=65536, max_stderr_bytes=4096)
        if result["returncode"] or not result["capture"]["complete"]:
            raise ValueError
        rows = []
        for line in result["stdout_bytes"].decode("utf-8").splitlines():
            uid, name, driver, memory = [p.strip() for p in line.split(",")]
            if not re.fullmatch(r"GPU-[0-9a-fA-F-]+", uid) or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,100}", name) or not re.fullmatch(r"[0-9.]+", driver):
                raise ValueError
            rows.append({"uuid": uid, "name": name, "driver_version": driver, "memory_MiB": int(memory)})
        if len(rows) != 1:
            raise ValueError
        return {"schema": "byul-bootstrap-gpu-info/1", "gpus": rows}
    except Exception:
        raise CaptureError("GPU_INFO_UNAVAILABLE") from None


def legacy_baseline(namespace, cluster, path):
    source = Path(path).read_bytes()
    if digest(source) != BASELINE_SHA:
        raise CaptureError("UNREVIEWED_BASELINE_VALIDATOR")
    values = {"namespace.json": namespace, "cluster.json": cluster}
    def private_open(name, *args, **kwargs):
        if name not in values:
            raise CaptureError("UNEXPECTED_VALIDATOR_READ")
        return io.StringIO(json.dumps(values[name]))
    previous = sys.argv
    try:
        sys.argv = ["check_baseline.py", "namespace.json", "cluster.json"]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            exec(compile(source, "check_baseline.py", "exec"), {"__builtins__": {**vars(builtins), "open": private_open}})
    except (Exception, SystemExit):
        raise CaptureError("FOUNDATION_BASELINE_UNVERIFIED") from None
    finally:
        sys.argv = previous


def preflight(value):
    active = [p for p in value["items"] if p.get("status", {}).get("phase") not in ("Succeeded", "Failed")
              and any(int(c.get("resources", {}).get("limits", {}).get("nvidia.com/gpu", 0))
                      for c in p["spec"].get("containers", []) + p["spec"].get("initContainers", []))]
    if active:
        raise CaptureError("ACTIVE_GPU_OWNER_PRESENT")


QUERIES = {
    "preflight": ["get", "pods", "-A", "-o", "json"],
    "engine-ready": ["-n", NAMESPACE, "get", "pods", "-l", "app=serving-engine", "-o", "json"],
    "workloads": ["-n", NAMESPACE, "get", "pods", "-o", "json"],
    "workload-config": ["-n", NAMESPACE, "get", "deployments,services,networkpolicies", "-o", "json"],
    "networkpolicies": ["-n", NAMESPACE, "get", "networkpolicies", "-o", "json"],
    "events": ["-n", NAMESPACE, "get", "events", "--sort-by=.metadata.creationTimestamp", "-o", "json"],
    "final-pods": ["get", "pods", "-A", "-o", "json"],
    "gpu-pods": ["-n", NAMESPACE, "get", "pods", "-l", "job-name=gpu-smoke", "-o", "json"],
    "denied-pods": ["-n", NAMESPACE, "get", "pods", "-l", "job-name=engine-denied", "-o", "json"],
}


def capture_kind(kind):
    if kind == "gpu-info":
        return gpu_info()
    if kind == "baseline":
        ns = call(["-n", NAMESPACE, "get", "deployments,networkpolicies,ciliumnetworkpolicies.cilium.io,tracingpoliciesnamespaced.cilium.io", "-o", "json"])
        cluster = call(["get", "ciliumclusterwidenetworkpolicies.cilium.io,tracingpolicies.cilium.io", "-o", "json"])
        try:
            legacy_baseline(ns, cluster, Path(__file__).with_name("check_baseline.py"))
        except CaptureError as exc:
            exc.record = {"namespace": projected(ns), "cluster": projected(cluster)}
            raise
        # First bootstrap only. Never overwrite another/previous workload.
        existing = call(["-n", NAMESPACE, "get", "deployments,services,jobs,networkpolicies", "-o", "json"])
        if existing["items"]:
            raise CaptureError("EXISTING_BOOTSTRAP_RESOURCES_REQUIRE_REVIEW", projected(existing))
        return {"namespace": projected(ns), "cluster": projected(cluster), "fresh_named_resources": True}
    if kind == "profile":
        node = call(["get", "node", "byul-gpu-01", "-o", "json"])
        conditions = {c["type"]: c["status"] for c in node["status"]["conditions"]}
        profile = os.environ["LAB_PROFILE"]
        if profile not in ("dev", "reference") or any(conditions.get(k) != v for k, v in {"Ready": "True", "MemoryPressure": "False", "DiskPressure": "False"}.items()):
            raise CaptureError("NODE_PROFILE_UNVERIFIED")
        patch = [{"op": "test", "path": "/metadata/uid", "value": node["metadata"]["uid"]},
                 {"op": "test", "path": "/metadata/resourceVersion", "value": node["metadata"]["resourceVersion"]},
                 {"op": "add", "path": "/metadata/labels/lab-profile", "value": profile}]
        after = call(["patch", "node", "byul-gpu-01", "--type=json", "-p", json.dumps(patch), "-o", "json"])
        if after["metadata"]["uid"] != node["metadata"]["uid"]:
            raise CaptureError("NODE_GENERATION_CHANGED")
        configured = {key: os.environ[key] for key in ("ENGINE_CPU_REQUEST", "ENGINE_CPU_LIMIT", "ENGINE_MEMORY_REQUEST", "ENGINE_MEMORY_LIMIT")
                      if key in os.environ and re.fullmatch(r"[0-9]+(?:m|Ki|Mi|Gi|Ti)?", os.environ[key])}
        return {"profile": profile, "declared_resource_settings": configured, "node": project(after)}
    value = call(QUERIES[kind])
    if kind == "preflight":
        try:
            preflight(value)
        except CaptureError as exc:
            exc.record = projected(value)
            raise
    if kind == "engine-ready":
        if not value["items"] or any([c["name"] for c in p["spec"]["containers"]] != ["engine"]
                                    or p["spec"].get("initContainers") for p in value["items"]):
            raise CaptureError("ENGINE_CONTAINER_LAYOUT_UNVERIFIED", projected(value))
    return projected(value)


def validate_result(raw):
    try:
        value = json.loads(raw)
        normal, stream = value["normal"], value["stream"]
        if (value["schema"] != "byul-bootstrap-response/1" or value["health_200"] is not True
                or any(normal[k] is not True for k in ("model_verified", "text_nonempty", "finish_seen", "positive_completion_usage"))
                or any(stream[k] is not True for k in ("all_models_verified", "text_seen", "finish_seen", "done_last"))
                or type(stream["data_frames"]) is not int or not 0 < stream["data_frames"] <= 4096):
            raise ValueError
        return {"schema": value["schema"], "health_200": True,
                "normal": {k: True for k in ("model_verified", "text_nonempty", "finish_seen", "positive_completion_usage")},
                "stream": {**{k: True for k in ("all_models_verified", "text_seen", "finish_seen", "done_last")}, "data_frames": stream["data_frames"]}}
    except Exception:
        raise CaptureError("BOOTSTRAP_RESPONSE_UNVERIFIED") from None


def logs(target, mode):
    if target not in {"job/gpu-smoke", "job/engine-smoke", "job/engine-denied", "deployment/serving-engine"}:
        raise CaptureError("UNSUPPORTED_LOG_TARGET")
    result = call(["-n", NAMESPACE, "logs", target], json_result=False, cap=1024**2)
    raw = result["stdout_bytes"]
    record = {"schema": "byul-bootstrap-log-summary/1", "retained_bytes": len(raw), "retained_sha256": digest(raw), "capture_complete": True}
    if mode == "normal":
        record["response_validation"] = validate_result(raw)
    elif mode == "gpu":
        if b"Test PASSED" not in raw:
            raise CaptureError("GPU_COMPUTE_UNVERIFIED")
        record["cuda_vector_add_passed"] = True
    elif mode == "denied":
        if raw.strip() != b"DENIED_BY_TIMEOUT":
            raise CaptureError("DIRECT_ACCESS_REVOKE_UNVERIFIED")
        record["denied_by_timeout"] = True
    return record


def owned_mutation(kind, run_id):
    if not re.fullmatch(r"guard-[a-z0-9-]{1,55}", run_id):
        raise CaptureError("OWNERSHIP_CONFIGURATION_INVALID")
    resource = "networkpolicy" if kind == "revoke" else "job"
    name = "engine-direct-test" if kind == "revoke" else kind
    if name not in {"engine-direct-test", "gpu-smoke", "engine-smoke", "engine-denied"}:
        raise CaptureError("UNSUPPORTED_OWNED_TARGET")
    captured = call(["-n", NAMESPACE, "get", resource, name, "--ignore-not-found", "-o", "json"], json_result=False)
    if not captured["stdout_bytes"].strip():
        return {"schema": "byul-bootstrap-owned-cleanup/1", "target_absent": True,
                "temporary_access_revoked": kind == "revoke", "mutation_executed": False}
    value = json.loads(captured["stdout_bytes"])
    meta = value["metadata"]
    if meta.get("labels", {}).get("guard.run") != run_id or not meta.get("uid") or not meta.get("resourceVersion"):
        raise CaptureError("OWNERSHIP_UNVERIFIED", projected(value))
    patch = [{"op": "test", "path": "/metadata/uid", "value": meta["uid"]},
             {"op": "test", "path": "/metadata/resourceVersion", "value": meta["resourceVersion"]},
             {"op": "test", "path": "/metadata/labels/guard.run", "value": run_id}]
    if kind == "revoke":
        patch.append({"op": "add", "path": "/spec/ingress", "value": []})
    else:
        if not any(c.get("type") == "Complete" and c.get("status") == "True" for c in value.get("status", {}).get("conditions", [])):
            raise CaptureError("JOB_COMPLETION_UNVERIFIED")
        patch.append({"op": "add", "path": "/spec/ttlSecondsAfterFinished", "value": 0})
    after = call(["-n", NAMESPACE, "patch", resource, name, "--type=json", "-p", json.dumps(patch), "-o", "json"])
    if after["metadata"]["uid"] != meta["uid"] or kind == "revoke" and after["spec"].get("ingress") != []:
        raise CaptureError("OWNED_MUTATION_UNVERIFIED")
    if kind != "revoke":
        call(["-n", NAMESPACE, "wait", "--for=delete", "job/" + name, "--timeout=30s"], json_result=False, timeout=35)
    return {"schema": "byul-bootstrap-owned-cleanup/1", "uid": meta["uid"], "run_id": run_id,
            "temporary_access_revoked": kind == "revoke", "deny_only_policy_retained": kind == "revoke",
            "completed_job_TTL_zero_requested": kind != "revoke"}


def render_job(image, ip, run_id, *, denied=False):
    if not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image) or not re.fullmatch(r"guard-[a-z0-9-]{1,55}", run_id):
        raise CaptureError("JOB_CONFIGURATION_INVALID")
    ip = str(ipaddress.IPv4Address(ip))
    code = Path(__file__).with_name("aws_smoke_client.py").read_text(encoding="utf-8")
    command = ["python3", "-c", code, ip]
    if denied:
        command = ["/bin/sh", "-ec", "set +e; curl -s -o /dev/null --connect-timeout 5 --max-time 8 http://" + ip
                   + ":8000/health 2>/dev/null; result=$?; set -e; test \"$result\" -eq 28; echo DENIED_BY_TIMEOUT"]
    labels = {"app": "engine-test-probe", "guard.run": run_id}
    return {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "engine-denied" if denied else "engine-smoke",
            "namespace": NAMESPACE, "labels": labels}, "spec": {"backoffLimit": 0, "activeDeadlineSeconds": 300,
            "ttlSecondsAfterFinished": 300, "template": {"metadata": {"labels": labels}, "spec": {
                "serviceAccountName": "probe", "automountServiceAccountToken": False, "restartPolicy": "Never",
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "client", "image": image, "command": command,
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}}}]}}}}


def render_policy(run_id):
    if not re.fullmatch(r"guard-[a-z0-9-]{1,55}", run_id):
        raise CaptureError("POLICY_CONFIGURATION_INVALID")
    return {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "engine-direct-test",
        "namespace": NAMESPACE, "labels": {"guard.run": run_id}}, "spec": {"podSelector": {"matchLabels": {"app": "serving-engine", "guard.run": run_id}},
        "policyTypes": ["Ingress"], "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "engine-test-probe", "guard.run": run_id}}}],
                                                 "ports": [{"protocol": "TCP", "port": 8000}]}]}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["capture", "logs", "revoke", "finish-job", "manifest-hashes", "render-client", "render-denied", "render-policy"])
    parser.add_argument("--kind")
    parser.add_argument("--target")
    parser.add_argument("--run-id")
    parser.add_argument("--image")
    parser.add_argument("--engine-ip")
    parser.add_argument("--output", required=True)
    parser.add_argument("files", nargs="*")
    args = parser.parse_args(argv)
    try:
        if args.mode == "capture": value = capture_kind(args.kind)
        elif args.mode == "logs": value = logs(args.target, args.kind)
        elif args.mode in {"revoke", "finish-job"}: value = owned_mutation("revoke" if args.mode == "revoke" else args.target, args.run_id)
        elif args.mode in {"render-client", "render-denied"}: value = render_job(args.image, args.engine_ip, args.run_id, denied=args.mode == "render-denied")
        elif args.mode == "render-policy": value = render_policy(args.run_id)
        else: value = {"schema": "byul-bootstrap-manifest-hashes/1", "file_bytes_sha256": {Path(p).name: digest(Path(p).read_bytes()) for p in args.files}}
        write(args.output, value)
        return 0
    except Exception as exc:
        code = str(exc) if isinstance(exc, CaptureError) else "BOOTSTRAP_CAPTURE_UNAVAILABLE"
        if isinstance(exc, CaptureError) and exc.record is not None:
            try:
                write(args.output, {"schema": "byul-bootstrap-partial/1", "complete": False,
                                    "diagnostic": code, "observed": exc.record})
            except OSError:
                pass
        print(json.dumps({"schema": "byul-bootstrap-diagnostic/1", "complete": False, "diagnostic": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
