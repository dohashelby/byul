"""PLT-07 policy mode reported by the selected Pod's own Cilium agent."""

from datetime import datetime
import json
from pathlib import Path
import re

from guard.adapters.engine_profiles import supports
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_binding import NAME, UID
from guard.runtime.kubernetes_http import REQUEST_BINDING_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft

CILIUM_EGRESS_STATUS_OPTION = "cilium_egress_status_only"
PROFILE = "kubernetes-owned-cilium-egress-status/1"
_CHECK_PROFILE = "explicit-forbidden-egress-v1"
_API_VERSION = "cilium.io/v2"
_RESOURCE = "ciliumendpoints.cilium.io"
_NAMESPACE = "kube-system"
_DAEMONSET = "cilium"
_CONTAINER = "cilium-agent"
_LABELS = {"k8s-app": "cilium"}
_IMAGE_DIGEST = "sha256:95cfa5c2427cc6b452c00908778d0566311f572589284af949d06a013db1a849"
CILIUM_AGENT_IMAGE = "quay.io/cilium/cilium:v1.20.3@" + _IMAGE_DIGEST
CILIUM_AGENT_IMAGE_ID = "quay.io/cilium/cilium@" + _IMAGE_DIGEST
_AGENT_KEYS = {"namespace_uid", "daemonset_uid", "pod", "pod_uid", "container_id", "image", "image_id", "node",
               "restart_count", "started_at"}
_MODES = ("none", "ingress", "egress", "both", "audit-ingress", "audit-egress", "audit-both")
_ENFORCING_EGRESS = ("egress", "both")
_STATUS_TIMEOUT = 5
_APPROVED_VALUES = {"forbidden_protocol": "tcp", "forbidden_operation": "tcp_connect"}
_LOCAL_AGENT_HOST = "--host=unix:///var/run/cilium/cilium.sock"

# The numeric endpoint GET returns one array entry. Project inside the stock
# CLI so policy rules, peer addresses and raw status never cross kubectl exec.
_ENDPOINT_FIELDS = {
    "id": ("id", False),
    "state": ("status.state", True),
    "connected": ("status.health.connected", False),
    "attachment": ("status.external-identifiers.cni-attachment-id", True),
    "ipv4": ("status.networking.addressing[0].ipv4", True),
    "mode": ("status.policy.realized.policy-enabled", True),
    "revision": ("status.policy.realized.policy-revision", False),
}


def _jsonpath_literal(text):
    return "{" + json.dumps(text) + "}"


def _endpoint_query():
    rows = []
    for key, (selector, quoted) in _ENDPOINT_FIELDS.items():
        quote = '"' if quoted else ""
        rows.append(_jsonpath_literal(json.dumps(key) + ":" + quote) + "{[0]." + selector + "}"
                    + (_jsonpath_literal(quote) if quoted else ""))
    return "jsonpath=" + _jsonpath_literal("{") + _jsonpath_literal(",").join(rows) + _jsonpath_literal("}")


_ENDPOINT_OUTPUT = _endpoint_query()


def _owned_transport(target, env, *, duration, binary, runner):
    if (type(target) is not dict or target.get("lab") is not False or target.get("synthetic") is True
            or not supports(env.engine.name, env.engine.version, "boundary_egress")):
        raise ValueError("PLT07_CILIUM_TARGET_UNVERIFIED")
    return KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner, profile=REQUEST_BINDING_PROFILE)


def _agent_pin(agent, engine):
    if (type(agent) is not dict or set(agent) != _AGENT_KEYS
            or any(type(agent[key]) is not str or not UID.fullmatch(agent[key])
                   for key in ("namespace_uid", "daemonset_uid", "pod_uid"))
            or type(agent["pod"]) is not str or not NAME.fullmatch(agent["pod"])
            or agent["node"] != engine["node"] or agent["pod_uid"] == engine["pod_uid"]
            or type(agent["container_id"]) is not str or not re.fullmatch(r"containerd://[0-9a-f]{64}", agent["container_id"])
            or agent["image"] != CILIUM_AGENT_IMAGE or agent["image_id"] != CILIUM_AGENT_IMAGE_ID
            or type(agent["restart_count"]) is not int or agent["restart_count"] < 0
            or type(agent["started_at"]) is not str
            or datetime.fromisoformat(agent["started_at"].replace("Z", "+00:00")).tzinfo is None):
        raise ValueError("PLT07_CILIUM_AGENT_PIN_INVALID")


def passive_egress_policy_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Verify the static stock target, ENV and exact agent pin with no I/O."""
    transport = _owned_transport(target, env, duration=duration, binary=binary, runner=runner)
    _agent_pin(target.get("cilium_agent"), transport.binding["engine"])
    return transport


def _agent_readback(transport, pod_name):
    namespace = transport._call(["get", "namespace", _NAMESPACE, "-o", "json"], budget=_STATUS_TIMEOUT)
    namespace_flag = f"--namespace={_NAMESPACE}"
    daemonset = transport._call([namespace_flag, "get", "daemonset", _DAEMONSET, "-o", "json"], budget=_STATUS_TIMEOUT)
    pod = transport._call([namespace_flag, "get", "pod", pod_name, "-o", "json"], budget=_STATUS_TIMEOUT)
    ns_meta, ds_meta, metadata = namespace["metadata"], daemonset["metadata"], pod["metadata"]
    spec, status = pod["spec"], pod["status"]
    owner, = metadata["ownerReferences"]
    agent_container, = [row for row in spec["containers"] if row["name"] == _CONTAINER]
    daemonset_container, = [row for row in daemonset["spec"]["template"]["spec"]["containers"] if row["name"] == _CONTAINER]
    agent_status, = [row for row in status["containerStatuses"] if row["name"] == _CONTAINER]
    if (namespace["kind"] != "Namespace" or namespace["apiVersion"] != "v1" or ns_meta["name"] != _NAMESPACE
            or daemonset["kind"] != "DaemonSet" or daemonset["apiVersion"] != "apps/v1"
            or ds_meta["name"] != _DAEMONSET or ds_meta["namespace"] != _NAMESPACE
            or daemonset["spec"]["selector"]["matchLabels"] != _LABELS
            or daemonset_container["image"] != CILIUM_AGENT_IMAGE
            or pod["kind"] != "Pod" or pod["apiVersion"] != "v1"
            or metadata["name"] != pod_name or metadata["namespace"] != _NAMESPACE
            or any(meta.get("deletionTimestamp") is not None for meta in (ns_meta, ds_meta, metadata))
            or type(metadata["ownerReferences"]) is not list
            or owner["apiVersion"] != "apps/v1" or owner["kind"] != "DaemonSet" or owner["name"] != _DAEMONSET
            or owner["uid"] != ds_meta["uid"] or owner.get("controller") is not True
            or any(metadata["labels"].get(key) != value for key, value in _LABELS.items())
            or spec.get("hostNetwork") is not True or status["phase"] != "Running"
            or not any(row.get("type") == "Ready" and row.get("status") == "True" for row in status["conditions"])
            or agent_status.get("ready") is not True):
        raise ValueError("PLT07_CILIUM_AGENT_OWNER_UNVERIFIED")
    result = {"namespace_uid": ns_meta["uid"], "daemonset_uid": ds_meta["uid"], "pod": pod_name,
              "pod_uid": metadata["uid"], "container_id": agent_status["containerID"], "image": agent_container["image"],
              "image_id": agent_status["imageID"], "node": spec["nodeName"], "restart_count": agent_status["restartCount"],
              "started_at": agent_status["state"]["running"]["startedAt"]}
    _agent_pin(result, transport.binding["engine"])
    return result


def capture_cilium_agent_binding(target, env, *, pod, duration, binary="kubectl", runner=None):
    """Read one explicitly selected agent Pod for the A-side target recipe."""
    if type(pod) is not str or not NAME.fullmatch(pod):
        raise ValueError("PLT07_CILIUM_AGENT_PIN_INVALID")
    try:
        transport = _owned_transport(target, env, duration=duration, binary=binary, runner=runner)
        before = transport.snapshot()
        pin = _agent_readback(transport, pod)
        if (pin != _agent_readback(transport, pod) or before != transport.snapshot()
                or transport.report()["cleanup_errors"]):
            raise ValueError
        return pin
    except (AttributeError, KubernetesBindingError, KeyError, TypeError, ValueError):
        raise ValueError("PLT07_CILIUM_AGENT_OWNER_UNVERIFIED") from None


def _endpoint_binding(document, engine):
    if document["apiVersion"] != _API_VERSION or document["kind"] != "CiliumEndpoint":
        raise ValueError
    metadata, status = document["metadata"], document["status"]
    owner, = metadata["ownerReferences"]
    if (type(metadata["ownerReferences"]) is not list or metadata["name"] != engine["pod"]
            or metadata["namespace"] != engine["namespace"] or metadata.get("deletionTimestamp") is not None
            or type(metadata["uid"]) is not str or not UID.fullmatch(metadata["uid"])
            or type(metadata["resourceVersion"]) is not str or not metadata["resourceVersion"].strip()
            or owner["apiVersion"] != "v1" or owner["kind"] != "Pod"
            or owner["name"] != engine["pod"] or owner["uid"] != engine["pod_uid"]):
        raise ValueError

    addressing = status["networking"]["addressing"]
    if type(addressing) is not list or any(type(row) is not dict for row in addressing):
        raise ValueError
    ipv4 = [row["ipv4"] for row in addressing if "ipv4" in row]
    attachment = status["external-identifiers"]["cni-attachment-id"]
    endpoint_id = status["id"]
    # CEP and the debugger use the same producer for this generation identity.
    # The sandbox attachment ID differs from the engine container runtime ID.
    if (status["state"] != "ready" or ipv4 != [engine["pod_ip"]]
            or type(endpoint_id) is not int or not 1 <= endpoint_id <= 65535
            or type(status["identity"]["id"]) is not int or status["identity"]["id"] <= 0
            or type(attachment) is not str or not re.fullmatch(r"[0-9a-f]{64}:[A-Za-z0-9_.-]{1,15}", attachment)):
        raise ValueError
    return {"id": endpoint_id, "ipv4": ipv4[0], "attachment": attachment, "endpoint_uid": metadata["uid"],
            "resource_version": metadata["resourceVersion"], "pod_uid": owner["uid"]}


def _reported_state(transport, agent, binding):
    output = transport._call([f"--namespace={_NAMESPACE}", "exec", agent["pod"], f"--container={_CONTAINER}", "--",
                              "cilium-dbg", _LOCAL_AGENT_HOST, "endpoint", "get", str(binding["id"]),
                              "--output", _ENDPOINT_OUTPUT], cap=1024, budget=_STATUS_TIMEOUT)
    if (type(output) is not dict or output.keys() != _ENDPOINT_FIELDS.keys()
            or type(output["id"]) is not int or output["id"] != binding["id"]
            or output["attachment"] != binding["attachment"] or output["ipv4"] != binding["ipv4"]
            or output["state"] != "ready" or output["connected"] is not True
            or output["mode"] not in _MODES or type(output["revision"]) is not int or output["revision"] <= 0):
        raise ValueError
    return {"endpoint_state": output["state"], "reported_policy_mode": output["mode"],
            "reported_egress_enforcing": output["mode"] in _ENFORCING_EGRESS,
            "reported_policy_revision": output["revision"],
            "endpoint_binding_sha256": sha256_hex(canonical_json(binding)),
            "agent_binding_sha256": sha256_hex(canonical_json(agent))}


class KubernetesPassiveEgressPolicyExecutor:
    name = "boundary_egress"
    version = "0.1.0+k8s-cilium-status"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(CILIUM_EGRESS_STATUS_OPTION) is not True
                or check.id != "PLT-07" or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {CILIUM_EGRESS_STATUS_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("PLT07_CILIUM_SCOPE_INVALID")
        if type(values) is not dict or values != _APPROVED_VALUES:
            raise ValueError("PLT07_CILIUM_POLICY_INVALID")

    def _target_transport(self, target, env, *, duration, expected_target_sha256):
        target_sha = sha256_hex(canonical_json(target))
        if target_sha != expected_target_sha256 or self._expected_target_sha256 not in (None, target_sha):
            raise ValueError
        transport = passive_egress_policy_transport(target, env, duration=duration,
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self.runner)
        return target_sha, transport

    def prepared_passive_egress_policy(self, check, env, values, *, expected_target_sha256):
        """Validate the saved engine and agent declaration without reading status."""
        from guard.adapters.vllm_file_read_sensor import dependency

        self.validate_policy(check, values)
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            self._target_transport(target, env, duration=self.cfg.get("max_duration_seconds", 30),
                                   expected_target_sha256=expected_target_sha256)
        except (KubernetesBindingError, KeyError, OSError, OverflowError, TypeError, ValueError):
            raise ValueError("PLT07_CILIUM_TARGET_UNVERIFIED") from None

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 30)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            if adapter is None:
                raise ValueError
            target_sha, transport = self._target_transport(
                target, env, duration=duration, expected_target_sha256=self._expected_target_sha256)
            agent = target["cilium_agent"]
        except (KubernetesBindingError, OSError, TypeError, ValueError):
            raise ValueError("PLT07_CILIUM_TARGET_UNVERIFIED") from None

        observed = None
        failure = "PLT07_CILIUM_OWNER_BEFORE_UNVERIFIED"
        try:
            transport.snapshot()
            if agent != _agent_readback(transport, agent["pod"]):
                raise ValueError
            failure = "PLT07_CILIUM_ENDPOINT_BINDING_UNVERIFIED"
            engine = transport.binding["engine"]
            document = transport._call([f"--namespace={engine['namespace']}", "get", _RESOURCE, engine["pod"], "-o", "json"],
                                       cap=262144, budget=_STATUS_TIMEOUT)
            binding = _endpoint_binding(document, engine)
            failure = "PLT07_CILIUM_AGENT_STATUS_UNVERIFIED"
            read_started = utcnow()
            state = _reported_state(transport, agent, binding)
            read_finished = utcnow()
            failure = "PLT07_CILIUM_OWNER_AFTER_UNVERIFIED"
            if agent != _agent_readback(transport, agent["pod"]):
                raise ValueError
            transport.snapshot()
            report = transport.report()
            before, after = report["snapshot_reads"]
            if (not report["collection_gap"] and not report["cleanup_errors"]
                    and before["snapshot_sha256"] == after["snapshot_sha256"]
                    and before["source_file_sha256"] == after["source_file_sha256"]):
                observed = {**state, "read_started_at": read_started.isoformat(), "read_finished_at": read_finished.isoformat(),
                            "owned_snapshot_sha256": before["snapshot_sha256"],
                            "ownership_source_sha256": before["source_file_sha256"]}
        except (AttributeError, KubernetesBindingError, KeyError, TypeError, ValueError):
            pass

        report = transport.report()
        cleanup = list(report["cleanup_errors"])
        fixture = {"profile": PROFILE, "resource": _RESOURCE, "api_version": _API_VERSION,
                   "agent_image": agent["image"], "selection": _ENDPOINT_FIELDS}
        settings = {CILIUM_EGRESS_STATUS_OPTION: True, "agent_image_id": agent["image_id"],
                    "max_duration_seconds": duration, "status_timeout_seconds": _STATUS_TIMEOUT}
        conditions_sha = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                          if observed and target.get("comparison_config") else None)
        summary = {"profile": PROFILE, "scope": "agent_reported_policy_state_for_selected_engine_pod",
                   "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": report["binding_sha256"], "conditions_sha256": conditions_sha,
                   "observation": observed, "gap": None if observed else failure,
                   "missing": ["direct_agent_version_readback", "applied_policy_rules", "datapath_decision",
                               "traffic_trials", "acquisition_and_observation_egress"]}
        error = None if observed else failure
        artifacts = []
        try:
            path = Path(workdir) / "cilium_egress_status.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("PLT07_CILIUM_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None, "detail": "Passive egress policy state readback."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations={"forbidden_egress_successes": {"value": None, "collected": False},
                          "egress_trials": {"value": None, "collected": False},
                          "egress_scope_complete": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json(fixture)),
                           "config_sha256": conditions_sha, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={CILIUM_EGRESS_STATUS_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
