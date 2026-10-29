"""PLT-08: explicit plaintext prohibition on one owned direct API test hop.

Reuse the owned LIF-04 capture in this invocation, never an imported capture.
The HTTP-only client and owned bridge binding establish this limited hop; no
certificate, TLS termination, IPC, TP or deployed caller identity is inferred.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from .base import EvidenceDraft
from . import stream_pressure, stream_probe


PROFILE = "explicit-owned-api-plaintext-v1"
_SHA = re.compile(r"[0-9a-f]{64}")
POLICY_TYPES = {"api_hop_scope": "str", "api_container_id": "str", "api_network_id": "str",
    "api_endpoint_id": "str", "api_ip": "str", "api_port": "int", "plaintext_forbidden": "bool",
    "validity_start": "str", "validity_end": "str"}


def _require(value):
    if not value:
        raise ValueError("API_TRANSPORT_BINDING_UNVERIFIED")


def _when(value):
    _require(type(value) is str and 1 <= len(value) <= 40)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        _require(parsed.tzinfo is not None and parsed.utcoffset() is not None)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise ValueError("API_TRANSPORT_BINDING_UNVERIFIED") from None


def normalize_policy(values):
    """No default prohibition, target identity, exception or validity window."""
    _require(type(values) is dict and set(values) == set(POLICY_TYPES))
    _require(values["api_hop_scope"] == "guard_probe_to_owned_direct_api"
        and all(type(values[k]) is str and _SHA.fullmatch(values[k])
                for k in ("api_container_id", "api_network_id", "api_endpoint_id"))
        and type(values["api_port"]) is int and 1 <= values["api_port"] <= 65535
        and type(values["plaintext_forbidden"]) is bool)
    try:
        ip = ipaddress.ip_address(values["api_ip"])
        _require(type(values["api_ip"]) is str and str(ip) == values["api_ip"]
                 and not ip.is_unspecified and not ip.is_multicast and not ip.is_loopback)
    except (ValueError, TypeError):
        raise ValueError("API_TRANSPORT_BINDING_UNVERIFIED") from None
    _require(_when(values["validity_start"]) < _when(values["validity_end"]))
    return dict(values)


def _window(policy, first, last):
    return _when(policy["validity_start"]) <= first <= last <= _when(policy["validity_end"])


def _capture(env, target, settings, policy):
    """Check the exact policy endpoint before even the first normal request."""
    client = stream_pressure.create_pressure_client()
    _require(client.run_id != target["run_id"])  # Never clean the target's owner ID.
    deadline = time.monotonic() + settings["max_duration_seconds"]
    result = None
    try:
        owner = stream_pressure._owner(client, env, target, deadline)
        binding = stream_pressure._network_binding(client, target, owner, deadline)
        _require(binding == {"network_id": policy["api_network_id"], "endpoint_id": policy["api_endpoint_id"],
            "container_id": policy["api_container_id"], "host": policy["api_ip"]})
        effective = dict(settings, max_duration_seconds=stream_pressure._remaining(deadline))
        result = stream_pressure.capture_pressure(client, env, target, effective)
        return result, effective, datetime.now(timezone.utc)
    finally:
        try:
            incomplete = bool(client.cleanup(timeout=30))
        except Exception:
            incomplete = True
        if incomplete and result is not None:
            result["collection_errors"] = sorted(set(result["collection_errors"]) | {"PRESSURE_PROBE_CLEANUP_INCOMPLETE"})


def project_capture(capture, env, target, settings, policy, started, finished):
    """Internal live-capture projection; no CLI/file import entry point."""
    _require(type(capture) is dict and capture.get("schema") == "guard-stream-pressure-capture/1"
        and capture.get("profile") == stream_pressure.PROFILE
        and capture.get("capture_executed") is True and capture.get("engine_started") is False
        and capture.get("environment_ref") == env.ref().model_dump(mode="json")
        and capture.get("run_id") == target["run_id"]
        and capture.get("target_sha256") == sha256_hex(canonical_json(target))
        and capture.get("settings_sha256") == sha256_hex(canonical_json(settings))
        and capture.get("probe_sha256") == hashlib.sha256(Path(stream_probe.__file__).read_bytes()).hexdigest()
        and capture.get("identity_stable") is True and _window(policy, started, finished))
    owner = capture.get("owner_before")
    _require(type(owner) is dict and owner == capture.get("owner_after")
             and owner.get("container_id") == policy["api_container_id"]
             and owner.get("image") == env.engine.image_digest and owner.get("run_id") == target["run_id"]
             and type(owner.get("image_id")) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", owner["image_id"])
             and type(owner.get("host_pid")) is int and owner["host_pid"] > 0)
    _when(owner.get("started_at"))
    binding = {"network_id": policy["api_network_id"], "endpoint_id": policy["api_endpoint_id"],
               "container_id": policy["api_container_id"], "host": policy["api_ip"]}
    _require(capture.get("network_binding_sha256") == sha256_hex(canonical_json(binding)))
    # Source samples bracket the same capture. Clock and owner are independent
    # of missing aggregate memory/queue fields; those gaps cannot erase a known
    # API response. Never equate host and Docker-VM monotonic epochs.
    edges = {}
    for phase in ("before", "after"):
        rows = [r for r in capture.get("resource_samples", []) if r.get("phase") == phase]
        _require(len(rows) == 1)
        sample = rows[0]["sample"]
        clock = (sample.get("observations") or {}).get("clock")
        _require(sample.get("owner") == owner and type(clock) is dict
            and clock.get("basis") == "linux-monotonic-time-namespace-v1"
            and all(type(clock.get(k)) is int and 0 < clock[k] < 2**63
                    for k in ("time_namespace_inode", "sample_started_monotonic_ns", "sample_finished_monotonic_ns"))
            and clock["sample_started_monotonic_ns"] <= clock["sample_finished_monotonic_ns"]
            and sample.get("capture_hashes", {}).get("transport_complete") is True)
        edges[phase] = (_when(sample.get("timestamp")), clock)
    a, b = edges["before"], edges["after"]
    _require(_window(policy, a[0], b[0]) and a[1]["time_namespace_inode"] == b[1]["time_namespace_inode"]
             and a[1]["sample_finished_monotonic_ns"] <= b[1]["sample_started_monotonic_ns"])
    # Only the first, complete normal response is used. Slow-reader outcomes,
    # resource completeness and later normal/stream failures are independent.
    stages = [r for r in capture.get("stages", []) if r.get("stage") == "before"]
    _require(len(stages) == 1 and len(stages[0].get("responses", [])) == 1)
    response = stages[0]["responses"][0]
    success = (capture.get("normal_response_schema_verified", {}).get("before") is True
        and response.get("id") == "before" and response.get("outcome") == 200
        and response.get("capture_complete") is True and response.get("deadline_guard_verified") is True
        and response.get("stop_reason") == "eof" and response.get("utf8_valid") is True
        and response.get("body_truncated") is False)
    _require(capture.get("data_origin") in ("EXAMPLE", "DECLARED"))
    trial = {"profile": PROFILE, "policy_sha256": sha256_hex(canonical_json(policy)),
        "hop_scope": policy["api_hop_scope"], "binding": binding, "port": policy["api_port"],
        "method": "POST", "route": "/v1/completions", "transport": "plain_http",
        "normal_response_verified": bool(success), "plaintext_forbidden": policy["plaintext_forbidden"],
        "capture_sha256": sha256_hex(canonical_json(capture)),
        "probe_sha256": capture["probe_sha256"], "source_interval": [a[0].isoformat(), b[0].isoformat()],
        "source_owner": {k: owner[k] for k in ("container_id", "image_id", "image", "run_id", "host_pid", "started_at")},
        "source_clock_edges": [dict(a[1]), dict(b[1])],
        # Reuse the producer's enum/numeric/hash projection. Never retain raw
        # response bodies, headers, arbitrary detail or exception strings.
        "initial_response": stream_pressure._project_response(response, "before", settings, []),
        "cryptographic_peer_identity_verified": False, "deployed_caller_path_verified": False}
    return trial, bool(success and policy["plaintext_forbidden"]), DataOrigin(capture["data_origin"])


def collect_kubernetes_api_transport(ctx):
    """Consume one live shared hop, never a caller-supplied capture dictionary."""
    from guard.runtime.kubernetes_api_hop import PROFILE as KUBE_PROFILE, normalize_policy as kube_policy
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    if (not isinstance(ctx.cprobe, KubernetesHTTP) or ctx.cprobe.profile != REQUEST_BINDING_PROFILE
            or ctx.cprobe.target_sha256 != sha256_hex(canonical_json(ctx.target))
            or not callable(getattr(ctx.cprobe, "api_hop_snapshot", None))):
        raise ValueError("KUBERNETES_API_TRANSPORT_COMMON_INTERFACE_UNAVAILABLE")
    policy = kube_policy(ctx.policy_values)
    record = ctx.cprobe.api_hop_snapshot(policy, ctx.timeout)
    if (type(record) is not dict or record.get("schema") != "guard-kubernetes-api-hop-capture/1"
            or record.get("profile") != KUBE_PROFILE or record.get("target_sha256") != ctx.cprobe.target_sha256
            or record.get("environment_ref") != ctx.env.ref().model_dump(mode="json")
            or record.get("policy_binding_verified") is not True):
        raise ValueError("KUBERNETES_API_TRANSPORT_BINDING_UNVERIFIED")
    ctx._kubernetes_api_hop_data_origin = DataOrigin(record["data_origin"])
    normal_success = all(record.get(key) is True for key in (
        "available", "policy_binding_verified", "policy_window_verified", "capture_executed",
        "binding_verified_before_after", "normal_response_schema_verified"))
    violated = normal_success and record.get("transport") == "plain_http" and policy["plaintext_forbidden"]
    observations = {
        "forbidden_plaintext_successes": {"value": ["owned_kubernetes_direct_api_plaintext"] if violated else [],
                                          "collected": True},
        "api_transport_trials": {"value": [record], "collected": True},
        "api_transport_scope_complete": {"value": False, "collected": True},
    }
    # This is an attempted public normal-control call, not proof of an HTTP
    # dispatch when an owner/transport gap prevents a bound response.
    normal = {"attempted": True, "succeeded": normal_success,
              "detail": "one policy-bound owned Kubernetes API hop; full API and IPC/TP scope unverified"}
    summary = {"profile": KUBE_PROFILE, "one_hop_normal_response_verified": normal_success,
        "kubernetes": ctx.cprobe.report(), "api_transport_scope_complete": False,
        "internal_IPC_TP_scope_complete": False,
        "fixture": {"profile": KUBE_PROFILE, "policy_sha256": record["policy_sha256"],
                    "probe_source_sha256": record["probe_source_sha256"],
                    "normal_control_source_sha256": record["normal_control_source_sha256"]},
        "limitations": ["One fixed HTTP probe-to-owned-Pod hop is distinct from the deployed caller path.",
            "No TLS/CA/peer, Service/Gateway, worker identity or IPC/TP protection is established.",
            "Observer UTC/source hashes are not remote clock or producer authentication.",
            "Explicit allowed plaintext or a missing/denied/partial response cannot establish whole protection."]}
    return observations, normal, [{"step": step, "attempted": False} for step in ctx.check.challenge_steps], summary


class APITransportExecutor:
    name, version = "api_transport", "0.1.0"

    def __init__(self, cfg):
        if type(cfg) is not dict:
            raise ValueError("API_TRANSPORT_CONFIGURATION_INVALID")
        self.cfg = cfg

    def run(self, check, env, policy_values, adapter, workdir):
        started = datetime.now(timezone.utc)
        policy = normalize_policy(policy_values)  # Before any file/runtime/HTTP.
        try:
            _require(type(self.cfg) is dict and set(self.cfg) <= {"target", "settings"})
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
            settings = stream_pressure.normalize_settings(self.cfg.get("settings", {
                "schema": stream_pressure.SETTINGS_SCHEMA}))
            stream_pressure.validate_inputs(env, target, settings)
            supplied = urlsplit(target["engine"]["trusted_url"])
            _require(supplied.port == policy["api_port"] and _window(policy, started, started))
            # Reject expiry before the bounded existing producer finishes.
            _require(_window(policy, started, started + timedelta(seconds=settings["max_duration_seconds"])))
            capture, settings, finished = _capture(env, target, settings, policy)
            trial, violated, origin = project_capture(capture, env, target, settings, policy, started, finished)
        except Exception:
            return EvidenceDraft(status=ExecStatus.ABORTED, data_origin=(DataOrigin.EXAMPLE
                if env.data_origin == DataOrigin.EXAMPLE else DataOrigin.DECLARED),
                normal_control={"attempted": False, "succeeded": None}, config=self.cfg,
                abort_reason="API_TRANSPORT_BINDING_UNVERIFIED", started_at=started,
                finished_at=datetime.now(timezone.utc))
        # The producer's ceiling is retained even for an actual owned capture.
        if env.data_origin == DataOrigin.EXAMPLE or target["lab"]:
            origin = DataOrigin.EXAMPLE
        return EvidenceDraft(status=ExecStatus.COMPLETED, data_origin=origin, config=self.cfg,
            started_at=started, finished_at=datetime.now(timezone.utc),
            cleanup_errors=["API_TRANSPORT_PROBE_CLEANUP_INCOMPLETE"]
                if "PRESSURE_PROBE_CLEANUP_INCOMPLETE" in capture.get("collection_errors", []) else [],
            normal_control={"attempted": True, "succeeded": trial["normal_response_verified"]},
            challenges=[{"step": s, "attempted": False} for s in check.challenge_steps],
            observations={"forbidden_plaintext_successes": {"collected": True,
                "value": ["owned_direct_api_plaintext"] if violated else []},
                "api_transport_trials": {"collected": True, "value": [trial]},
                "api_transport_scope_complete": {"collected": True, "value": False}})
