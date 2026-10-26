"""Bounded LIF-04 collection, without a security verdict or executor registration.

Client reads/close and aggregate process/cgroup/socket measurements are distinct.
Neither proves per-request buffer attribution, worker cancellation or recovery.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import ipaddress
import math
import os
from pathlib import Path
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from guard.models import canonical_json, sha256_hex
from . import stream_probe
from .content_common import control_state
from .http_common import docker_stream_probe
from .pressure_resources import capture_resource_sample, owned_resource_snapshot


PROFILE = "bounded-stream-pressure-v1"
SETTINGS_SCHEMA = "guard-stream-pressure-settings/1"
DEFAULTS = {"read_profile": {"profile": "slow-reader-v1", "chunk_bytes": 64,
                           "interval_ms": 100, "close_after_reads": 8},
            "max_output_tokens": 8, "request_timeout_seconds": 15,
            "max_duration_seconds": 60, "resource_interval_ms": 250,
            "max_resource_samples": 8, "post_close_wait_ms": 500, "api_key_env": None}
_PIN = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{64}")
_OUTCOMES = {"dns_error", "refused", "unreachable", "timeout", "error", "probe_error"}
_STOPS = {"not_started", "collecting", "eof", "client_close", "incomplete_body",
          "incomplete_sse_event", "invalid_utf8", "deadline", "timeout", "transport_error",
          "body_limit", "line_limit", "events_limit", "metadata_limit", "headers_limit",
          "unsupported_content_encoding", "unsupported_transfer_encoding", "ambiguous_http_framing",
          "invalid_content_length", "unsupported_read_profile_content_type", "source_deadline",
          "source_clock_unavailable", "source_clock_mismatch"}


def normalize_settings(value):
    if (type(value) is not dict or value.get("schema") != SETTINGS_SCHEMA
            or set(value) - {"schema", *DEFAULTS}):
        raise ValueError("STREAM_PRESSURE_SETTINGS_INVALID")
    cfg = {"schema": SETTINGS_SCHEMA, **deepcopy(DEFAULTS), **deepcopy(value)}
    for key, low, high in (("max_output_tokens", 1, 16), ("resource_interval_ms", 10, 1000),
                           ("max_resource_samples", 3, 16), ("post_close_wait_ms", 0, 1000)):
        if type(cfg[key]) is not int or not low <= cfg[key] <= high:
            raise ValueError("STREAM_PRESSURE_SETTINGS_INVALID")
    for key, maximum in (("request_timeout_seconds", 30), ("max_duration_seconds", 120)):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]) or not 0 < cfg[key] <= maximum:
            raise ValueError("STREAM_PRESSURE_SETTINGS_INVALID")
    key = cfg["api_key_env"]
    if key is not None and (type(key) is not str or not re.fullmatch(r"[A-Z_][A-Z0-9_]{0,63}", key)):
        raise ValueError("STREAM_PRESSURE_SETTINGS_INVALID")
    # Reuse the real collector's exact protocol validation before any I/O.
    stream_probe._prepare([{"id": "slow", "method": "POST", "url": "http://example.invalid:8000/",
                            "mode": "sse", "read_profile": cfg["read_profile"]}], 1)
    return cfg


def validate_inputs(env, target, settings):
    normalize_settings(settings)
    if (not env.hash_ok() or type(target) is not dict or type(target.get("lab")) is not bool
            or type(target.get("run_id")) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", target["run_id"])
            or type(target.get("engine")) is not dict
            or type(env.engine.image_digest) is not str or not _PIN.fullmatch(env.engine.image_digest)
            or target["engine"].get("image_digest") != env.engine.image_digest
            or type(env.model.id) is not str or not 1 <= len(env.model.id) <= 256):
        raise ValueError("STREAM_PRESSURE_INPUT_INVALID")
    engine = target["engine"]
    container, url = engine.get("container"), engine.get("trusted_url")
    if type(container) is not str or not _NAME.fullmatch(container) or type(url) is not str:
        raise ValueError("STREAM_PRESSURE_TARGET_INVALID")
    parsed = urlsplit(url)
    try:
        ipaddress.ip_address(parsed.hostname)
        literal = True
    except ValueError:
        literal = False
    origins = target.get("origins")
    trusted = origins.get("trusted") if type(origins) is dict else None
    network = trusted.get("network") if type(trusted) is dict else None
    if (parsed.scheme != "http" or not (literal or parsed.hostname == container.lower()) or parsed.port is None
            or not 1 <= parsed.port <= 65535 or parsed.path not in ("", "/") or parsed.query
            or parsed.fragment or parsed.username is not None or parsed.password is not None
            or len(url) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in url)
            or type(network) is not str or not _NAME.fullmatch(network)
            or type(target.get("probe_image")) is not str or not _PIN.fullmatch(target["probe_image"])):
        raise ValueError("STREAM_PRESSURE_TARGET_INVALID")


def create_pressure_client():
    from guard.runtime.docker import Docker
    return Docker("pressure" + secrets.token_hex(6))


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ValueError("STREAM_PRESSURE_DEADLINE")
    return remaining


def _source_cutoff(sample, received_at, parent_deadline, owner):
    """Bound a source epoch without equating the Docker VM and parent clocks.

    Source sampling starts before its packet reaches the parent. Adding only
    the parent's remaining budget at receipt therefore produces an earlier,
    conservative source cutoff, even if transport or Docker startup is slow.
    Other incomplete resource fields do not invalidate this independent clock.
    """
    observations = sample.get("observations")
    clock = observations.get("clock") if type(observations) is dict else None
    hashes = sample.get("capture_hashes")
    remaining = parent_deadline - received_at
    if (remaining <= 0 or sample.get("owner") != owner or type(hashes) is not dict
            or hashes.get("transport_complete") is not True or type(clock) is not dict
            or set(clock) != {"basis", "time_namespace_inode", "sample_started_monotonic_ns", "sample_finished_monotonic_ns"}
            or clock["basis"] != "linux-monotonic-time-namespace-v1"
            or any(type(clock[key]) is not int or not 0 < clock[key] < 2**63
                   for key in ("time_namespace_inode", "sample_started_monotonic_ns", "sample_finished_monotonic_ns"))
            or not 0 <= clock["sample_finished_monotonic_ns"] - clock["sample_started_monotonic_ns"] <= 6_000_000_000):
        return None
    cutoff = clock["sample_started_monotonic_ns"] + math.floor(remaining * 1_000_000_000)
    if not 0 < cutoff < 2**63:
        return None
    return {"basis": clock["basis"], "time_namespace_inode": clock["time_namespace_inode"],
            "deadline_monotonic_ns": cutoff}


def _owner(docker, env, target, deadline):
    owner = owned_resource_snapshot(docker, target, env, min(5, _remaining(deadline)))
    info = docker.image_info(owner["image_id"], timeout=min(5, _remaining(deadline)))
    digests = info.get("digests") if type(info) is dict else None
    if (type(digests) is not list or not 1 <= len(digests) <= 128
            or any(type(v) is not str or not _PIN.fullmatch(v) for v in digests)
            or env.engine.image_digest not in digests or info.get("os") != "linux"):
        raise ValueError("STREAM_PRESSURE_IMAGE_UNVERIFIED")
    if owned_resource_snapshot(docker, target, env, min(5, _remaining(deadline))) != owner:
        raise ValueError("STREAM_PRESSURE_SOURCE_CHANGED")
    return owner


def _network_binding(docker, target, owner, deadline):
    """Pin requests to the inspected owned endpoint, not an unverified DNS name."""
    network = target["origins"]["trusted"]["network"]
    row = docker.network_info(network, timeout=min(5, _remaining(deadline)))
    if (type(row) is not dict or type(row.get("Id")) is not str or not _SHA.fullmatch(row["Id"])
            or row.get("Name") != network and row.get("Id") != network
            or row.get("Driver") != "bridge" or row.get("Scope") != "local"
            or type(row.get("Labels")) is not dict or row["Labels"].get("guard.run") != target["run_id"]
            or type(row.get("Containers")) is not dict or len(row["Containers"]) > 128):
        raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
    raw = docker.inspect_json(target["engine"]["container"], timeout=min(5, _remaining(deadline)))
    if (type(raw) is not dict or raw.get("Id") != owner["container_id"] or raw.get("Image") != owner["image_id"]
            or type(raw.get("State")) is not dict or raw["State"].get("StartedAt") != owner["started_at"]):
        raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
    settings = raw.get("NetworkSettings")
    nets = settings.get("Networks") if type(settings) is dict else None
    if type(nets) is not dict or len(nets) > 128:
        raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
    attached = [value for value in nets.values() if type(value) is dict and value.get("NetworkID") == row["Id"]]
    endpoint = row["Containers"].get(owner["container_id"])
    if (len(attached) != 1 or type(endpoint) is not dict or endpoint.get("Name") != target["engine"]["container"]
            or type(endpoint.get("EndpointID")) is not str or not _SHA.fullmatch(endpoint["EndpointID"])
            or attached[0].get("EndpointID") != endpoint["EndpointID"]):
        raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
    hosts = []
    for key, address_key in (("IPAddress", "IPv4Address"), ("GlobalIPv6Address", "IPv6Address")):
        value, cidr = attached[0].get(key, ""), endpoint.get(address_key, "")
        if value == cidr == "":
            continue
        if type(value) is not str or type(cidr) is not str or "%" in value or "%" in cidr:
            raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
        host, endpoint_host = ipaddress.ip_address(value), ipaddress.ip_interface(cidr).ip
        if host != endpoint_host or host.is_unspecified or host.is_multicast:
            raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
        hosts.append(str(host))
    supplied = urlsplit(target["engine"]["trusted_url"]).hostname
    try:
        supplied = str(ipaddress.ip_address(supplied))
        host = supplied if supplied in hosts else None
    except ValueError:
        host = hosts[0] if hosts and supplied == target["engine"]["container"].lower() else None
    if host is None:
        raise ValueError("STREAM_PRESSURE_NETWORK_UNVERIFIED")
    return {"network_id": row["Id"], "endpoint_id": endpoint["EndpointID"],
            "container_id": owner["container_id"], "host": host}


def _project_response(result, label, cfg, errors, *, slow=False):
    """Persist a numeric/enum/hash allowlist, never body/events/headers/detail."""
    if type(result) is not dict or result.get("id") != label:
        errors.append("PRESSURE_CLIENT_METADATA_UNVERIFIED")
        return {"id": label, "outcome": "unverified", "capture_complete": False}
    raw = result.get("outcome")
    outcome = raw if type(raw) is int and 100 <= raw <= 599 else (
        raw if type(raw) is str and raw in _OUTCOMES else "unverified")
    projected = {"id": label, "outcome": outcome}
    for key in ("capture_complete", "body_truncated", "utf8_valid", "terminal_seen", "eof", "deadline_guard_verified"):
        projected[key] = result.get(key) if type(result.get(key)) is bool else None
    reason = result.get("stop_reason")
    projected["stop_reason"] = reason if type(reason) is str and reason in _STOPS else "unverified"
    if reason in ("source_deadline", "source_clock_unavailable", "source_clock_mismatch"):
        errors.append("PRESSURE_" + reason.upper())
    count = result.get("body_bytes")
    projected["body_bytes"] = count if type(count) is int and 0 <= count <= stream_probe.MAX_BODY else None
    digest = result.get("body_sha256")
    projected["body_sha256"] = digest if type(digest) is str and _SHA.fullmatch(digest) else None
    connection = result.get("connection_id")
    projected["connection_id"] = connection if type(connection) is str and re.fullmatch(r"[0-9a-f]{24}", connection) else None
    limit = cfg["max_duration_seconds"] * 1000 + 1000
    for key in ("dispatch_ms", "request_sent_ms", "headers_ms", "first_byte_ms", "end_ms"):
        value = result.get(key)
        projected[key] = value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit else None
    if slow:
        counter = result.get("relative_clock")
        projected["relative_clock"] = counter if counter in ("monotonic", "perf_counter") else None
        clock = result.get("clock")
        if (type(clock) is dict and set(clock) == {"basis", "time_namespace_inode", "batch_started_monotonic_ns"}
                and clock["basis"] == "linux-monotonic-time-namespace-v1"
                and all(type(clock[k]) is int and 0 < clock[k] < 2**63
                        for k in ("time_namespace_inode", "batch_started_monotonic_ns"))):
            projected["clock"] = dict(clock)
        else:
            projected["clock"] = None
        rows = result.get("read_observations")
        valid = (type(rows) is list and len(rows) <= 32 and projected["relative_clock"] is not None
                 and not (projected["clock"] is not None and projected["relative_clock"] != "monotonic"))
        clean, previous = [], -1
        if valid:
            for index, row in enumerate(rows):
                if (type(row) is not dict or set(row) != {"seq", "started_ms", "finished_ms", "bytes"}
                        or type(row["seq"]) is not int or row["seq"] != index
                        or any(type(row[k]) not in (int, float) or not math.isfinite(row[k])
                               or not 0 <= row[k] <= limit for k in ("started_ms", "finished_ms"))
                        or row["started_ms"] < previous or row["finished_ms"] < row["started_ms"]
                        or type(row["bytes"]) is not int or not 1 <= row["bytes"] <= cfg["read_profile"]["chunk_bytes"]):
                    valid = False
                    break
                clean.append(dict(row))
                previous = row["finished_ms"]
        for key in ("read_count", "read_pause_count"):
            value = result.get(key)
            projected[key] = value if type(value) is int and 0 <= value <= 32 else None
        projected["client_closed"] = result.get("client_closed") if type(result.get("client_closed")) is bool else None
        value = result.get("client_close_ms")
        projected["client_close_ms"] = value if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= limit else None
        valid = (valid and projected["read_count"] == len(clean)
                 and projected["body_bytes"] == sum(row["bytes"] for row in clean))
        projected["read_observations"] = clean if valid else []
        projected["read_metadata_verified"] = bool(valid)
        if not valid:
            errors.append("PRESSURE_READ_METADATA_UNVERIFIED")
    return projected


def capture_pressure(docker, env, target, config, *, probe=None, sampler=None, environ=None):
    cfg = normalize_settings(config)
    validate_inputs(env, target, cfg)
    injected_probe = probe is not None
    example_seam = injected_probe or sampler is not None
    deadline = time.monotonic() + cfg["max_duration_seconds"]
    before = _owner(docker, env, target, deadline)  # Ownership precedes even normal HTTP.
    binding = _network_binding(docker, target, before, deadline)
    local_probe = docker.image_info(target["probe_image"], timeout=min(5, _remaining(deadline)))
    if (type(local_probe) is not dict or local_probe.get("os") != "linux"
            or type(local_probe.get("digests")) is not list or not 1 <= len(local_probe["digests"]) <= 128
            or any(type(v) is not str or not _PIN.fullmatch(v) for v in local_probe["digests"])
            or target["probe_image"] not in local_probe["digests"]):
        raise ValueError("STREAM_PRESSURE_LOCAL_PROBE_UNAVAILABLE")
    sampler = sampler or capture_resource_sample
    network = target["origins"]["trusted"]["network"]
    port = urlsplit(target["engine"]["trusted_url"]).port
    host = "[" + binding["host"] + "]" if ":" in binding["host"] else binding["host"]
    base_url = f"http://{host}:{port}"
    errors, stages, samples = [], [], []
    controls = dict.fromkeys(("before", "parallel", "after"), False)
    headers = {}
    if cfg["api_key_env"]:
        value = (os.environ if environ is None else environ).get(cfg["api_key_env"])
        if type(value) is not str or not value or len(value) > 4096:
            raise ValueError("STREAM_PRESSURE_CREDENTIAL_UNAVAILABLE")
        headers["Authorization"] = "Bearer " + value

    def request(label, *, slow=False):
        body = {"model": env.model.id, "prompt": "guard stream pressure control", "temperature": 0,
                "max_tokens": cfg["max_output_tokens"] if slow else 1, "stream": slow}
        result = {"id": label, "method": "POST", "mode": "sse" if slow else "json",
                  "url": base_url + "/v1/completions",
                  "headers": dict(headers), "body": body}
        if slow:
            result["read_profile"] = deepcopy(cfg["read_profile"])
        return result

    def send(stage, requests):
        responses = []
        try:
            if _owner(docker, env, target, deadline) != before:
                raise ValueError("source changed")
            if _network_binding(docker, target, before, deadline) != binding:
                raise ValueError("network endpoint changed")
            if probe is None:
                raise ValueError("source deadline unverified")
            responses = probe(network, requests, min(cfg["request_timeout_seconds"], _remaining(deadline)))
            if (type(responses) is not list or len(responses) != len(requests)
                    or any(type(row) is not dict for row in responses)
                    or [row.get("id") for row in responses] != [row["id"] for row in requests]):
                raise ValueError("unrelated client responses")
            if not injected_probe and any(row.get("deadline_guard_verified") is not True for row in responses):
                errors.append("PRESSURE_SOURCE_DEADLINE_UNVERIFIED")
        except Exception:
            errors.append("PRESSURE_CLIENT_COLLECTION_FAILED")
            responses = [{"id": row["id"], "outcome": "probe_error"} for row in requests]
        for req, response in zip(requests, responses):
            if req["id"] in controls:
                try:
                    controls[req["id"]] = (response.get("capture_complete") is True
                        and (injected_probe or response.get("deadline_guard_verified") is True)
                        and control_state(response, env.model.id, "/v1/completions") == "pass")
                except Exception:
                    controls[req["id"]] = False
        projection = [_project_response(row, req["id"], cfg, errors, slow=req["id"] == "slow")
                      for req, row in zip(requests, responses)]
        stages.append({"stage": stage, "responses": projection})
        return projection

    lock, stop = threading.Lock(), threading.Event()
    accepting = {"value": True}
    def sample(phase):
        try:
            value = sampler(docker, target, env, deadline=min(deadline, time.monotonic() + 5), port=port)
            received_at = time.monotonic()
            if (type(value) is not dict or set(value) != {"owner", "observations", "timestamp", "capture_hashes",
                                                         "complete", "diagnostic_codes"}):
                raise ValueError("invalid resource sample")
        except Exception:
            received_at = time.monotonic()
            value = {"owner": None, "observations": None, "timestamp": None, "capture_hashes": None,
                     "complete": False, "diagnostic_codes": ["PRESSURE_RESOURCE_SAMPLE_UNAVAILABLE"]}
        with lock:
            if accepting["value"] and len(samples) < cfg["max_resource_samples"]:
                samples.append({"phase": phase, "sample": value})
        return value, received_at

    def sampling_loop():
        for _ in range(cfg["max_resource_samples"] - 2):
            if stop.is_set() or time.monotonic() >= deadline:
                break
            sample("during")
            if stop.wait(cfg["resource_interval_ms"] / 1000):
                break

    before_sample, received_at = sample("before")
    if not injected_probe:
        guard = _source_cutoff(before_sample, received_at, deadline, before)
        if guard is None:
            errors.append("PRESSURE_SOURCE_DEADLINE_UNVERIFIED")
        else:
            probe = docker_stream_probe(docker, target["probe_image"], pull_never=True,
                                        parent_deadline=deadline, deadline_guard=guard)
    send("before", [request("before")])
    pressure = []
    if controls["before"]:
        worker = threading.Thread(target=sampling_loop, name="guard-pressure-resources", daemon=True)
        worker.start()
        try:
            pressure = send("pressure", [request("slow", slow=True), request("parallel")])
        finally:
            stop.set()
            worker.join(timeout=max(0, min(5.1, deadline - time.monotonic() + 0.1)))
            if worker.is_alive():
                errors.append("PRESSURE_RESOURCE_SAMPLER_INCOMPLETE")
        threading.Event().wait(min(cfg["post_close_wait_ms"] / 1000, max(0, deadline - time.monotonic())))
        send("after", [request("after")])
    else:
        errors.append("PRESSURE_NORMAL_CONTROL_UNVERIFIED")
    sample("after")
    with lock:
        accepting["value"] = False
        resource_samples = deepcopy(samples)
    try:
        after = _owner(docker, env, target, deadline)
        after_binding = _network_binding(docker, target, after, deadline)
    except Exception:
        after, after_binding = None, None
    stable = before == after and binding == after_binding
    if not stable:
        errors.append("PRESSURE_ENGINE_IDENTITY_CHANGED")
    slow = next((row for row in pressure if row["id"] == "slow"), {})
    read_rows = slow.get("read_observations", [])
    procedure = bool((injected_probe or slow.get("deadline_guard_verified") is True)
                     and slow.get("read_metadata_verified") and slow.get("client_closed") is True
                     and slow.get("stop_reason") == "client_close" and slow.get("eof") is False
                     and slow.get("capture_complete") is False
                     and slow.get("read_count") == cfg["read_profile"]["close_after_reads"]
                     and len(read_rows) >= 2 and slow.get("read_pause_count") == len(read_rows) - 1
                     and all(b["started_ms"] - a["finished_ms"] >= cfg["read_profile"]["interval_ms"] - 1
                             for a, b in zip(read_rows, read_rows[1:]))
                     and slow.get("client_close_ms") is not None
                     and slow["client_close_ms"] >= read_rows[-1]["finished_ms"])
    if not procedure:
        errors.append("PRESSURE_READ_PROCEDURE_UNVERIFIED")
    overlap = None
    if len(pressure) == 2 and all(type(row.get(key)) in (int, float) for row in pressure
                                  for key in ("request_sent_ms", "end_ms")):
        overlap = max(row["request_sent_ms"] for row in pressure) < min(row["end_ms"] for row in pressure)
    clock = slow.get("clock")
    for row in resource_samples:
        observations = row["sample"].get("observations")
        sample_clock = observations.get("clock") if type(observations) is dict else None
        valid = (type(clock) is dict and type(sample_clock) is dict
                 and sample_clock.get("basis") == clock["basis"]
                 and sample_clock.get("time_namespace_inode") == clock["time_namespace_inode"]
                 and all(type(sample_clock.get(k)) is int and sample_clock[k] > 0
                         for k in ("sample_started_monotonic_ns", "sample_finished_monotonic_ns"))
                 and sample_clock["sample_finished_monotonic_ns"] >= sample_clock["sample_started_monotonic_ns"]
                 and type(slow.get("request_sent_ms")) in (int, float)
                 and type(slow.get("client_close_ms")) in (int, float))
        if valid:
            start = clock["batch_started_monotonic_ns"] + slow["request_sent_ms"] * 1_000_000
            close = clock["batch_started_monotonic_ns"] + slow["client_close_ms"] * 1_000_000
            first, last = sample_clock["sample_started_monotonic_ns"], sample_clock["sample_finished_monotonic_ns"]
            # Millisecond client timestamps are floored. Use conservative edges
            # when matching a complete source-sample interval, never wall clocks.
            valid = (last <= start if row["phase"] == "before" else first >= close + 1_000_000
                     if row["phase"] == "after" else first >= start + 1_000_000 and last <= close)
        row["phase_interval_verified"] = bool(valid)
    phases = {row["phase"] for row in resource_samples if row["sample"].get("complete") is True
              and row["sample"].get("owner") == before and row["phase_interval_verified"]}
    samples_complete = bool(stable and phases == {"before", "during", "after"}
        and all(row["sample"].get("complete") is True and row["sample"].get("owner") == before
                for row in resource_samples) and "PRESSURE_RESOURCE_SAMPLER_INCOMPLETE" not in errors)
    if phases != {"before", "during", "after"}:
        errors.append("PRESSURE_RESOURCE_INTERVAL_UNVERIFIED")
    return {"schema": "guard-stream-pressure-capture/1", "profile": PROFILE, "check_id": "LIF-04",
            "environment_ref": env.ref().model_dump(mode="json"), "run_id": target["run_id"],
            "target_sha256": sha256_hex(canonical_json(target)), "settings_sha256": sha256_hex(canonical_json(cfg)),
            "procedure": {key: value for key, value in cfg.items() if key != "api_key_env"},
            "data_origin": "EXAMPLE" if example_seam or env.data_origin.value == "EXAMPLE" or target["lab"] else "DECLARED",
            "official": False, "capture_executed": True, "engine_started": False, "product_connected": False,
            "security": "UNKNOWN", "functional": "UNKNOWN", "performance": "UNKNOWN",
            "owner_before": before, "owner_after": after, "identity_stable": stable,
            "network_binding_sha256": sha256_hex(canonical_json(binding)),
            "stages": stages, "resource_samples": resource_samples, "client_intervals_overlap": overlap,
            "normal_response_schema_verified": controls, "read_procedure_verified": procedure,
            "resource_samples_complete": samples_complete,
            "worker_cancel_verified": False, "buffer_cost_attribution_verified": False,
            "resource_recovery_verified": False, "collection_errors": sorted(set(errors)),
            "probe_sha256": hashlib.sha256(Path(stream_probe.__file__).read_bytes()).hexdigest(),
            "not_exercised": ["response_debug_buffer", "per_connection_buffer_cost", "worker_cancellation",
                               "enterprise_resource_limits", "all_process_buffer_inventory"]}


def execute_pressure(env, target, config):
    client = create_pressure_client()
    if client.run_id == target["run_id"]:
        raise ValueError("STREAM_PRESSURE_PROBE_OWNER_COLLISION")
    result = None
    try:
        result = capture_pressure(client, env, target, config)
        return result
    finally:
        try:
            failed = bool(client.cleanup(timeout=30))
        except Exception:
            failed = True
        if failed and result is not None:
            result["collection_errors"] = sorted(set(result["collection_errors"]) | {"PRESSURE_PROBE_CLEANUP_INCOMPLETE"})
