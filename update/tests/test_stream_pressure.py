"""LIF-04 product observations with EXAMPLE transports and owned test sockets.

These tests never start Docker or a serving engine. Aggregate resource and
client transport observations do not prove buffer attribution or cancellation.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import threading
import time

import pytest

from guard.collectors.stream_pressure import normalize_settings, validate_inputs
from guard.executors.stream_pressure import capture_pressure, execute_pressure
from guard.models import DataOrigin
from tests.builders import baseline_pack, environment


ENGINE_PIN = "example.invalid/engine@sha256:" + "a" * 64
PROBE_PIN = "example.invalid/probe@sha256:" + "b" * 64
ENGINE_ID = "sha256:" + "c" * 64
CONTAINER_ID = "d" * 64
STARTED_AT = "2026-10-26T00:00:00Z"
NAMESPACES = {name: index for index, name in enumerate(("pid", "net", "mnt", "user", "cgroup"), 1)}


def inputs(url="http://127.0.0.1:8000", *, origin=DataOrigin.EXAMPLE):
    env = environment(origin=origin, engine={"name": "vllm", "version": "0.31.0", "image_digest": ENGINE_PIN},
                      model={"id": "EXAMPLE-model"})
    target = {"lab": True, "run_id": "EXAMPLE-run", "probe_image": PROBE_PIN,
              "engine": {"container": "EXAMPLE-engine", "image_digest": ENGINE_PIN, "trusted_url": url},
              "origins": {"trusted": {"network": "EXAMPLE-network"}}}
    cfg = normalize_settings({"schema": "guard-stream-pressure-settings/1",
        "read_profile": {"profile": "slow-reader-v1", "chunk_bytes": 32, "interval_ms": 10, "close_after_reads": 3},
        "request_timeout_seconds": 2, "max_duration_seconds": 20, "resource_interval_ms": 10,
        "max_resource_samples": 4, "post_close_wait_ms": 0})
    return env, target, cfg


def owner():
    return {"container_id": CONTAINER_ID, "image_id": ENGINE_ID, "started_at": STARTED_AT,
            "image": ENGINE_PIN, "run_id": "EXAMPLE-run", "host_pid": 123}


class PressureWorld:
    def __init__(self, *, change=None, sample_complete=True, clock_change=None, missing_sample=None,
                 expected_during_samples=2):
        self.change, self.sample_complete = change, sample_complete
        self.clock_change, self.missing_sample = clock_change, missing_sample
        self.expected_during_samples = expected_during_samples
        self.inspections, self.image_queries, self.probe_calls, self.sample_calls, self.cleanups = [], [], [], [], []
        self.network_queries = []
        self.run_id = "pressure-example-owned-probes"
        self.sample_started = threading.Event()
        self.actual_during_sample = threading.Event()
        self.pressure_window_open = threading.Event()
        self.probe_times = []

    def inspect_json(self, container, *, timeout):
        assert container == "EXAMPLE-engine" and timeout > 0
        self.inspections.append(container)
        value = {"Id": CONTAINER_ID, "Name": "/EXAMPLE-engine", "Image": ENGINE_ID,
            "Config": {"Image": ENGINE_PIN, "Labels": {"guard.run": "EXAMPLE-run"}},
            "State": {"Running": True, "StartedAt": STARTED_AT, "Pid": 123},
            "HostConfig": {"PidMode": "", "NetworkMode": "EXAMPLE-network", "CgroupnsMode": "private"}, "Mounts": [],
            "NetworkSettings": {"Networks": {"EXAMPLE-network": {
                "NetworkID": "1" * 64, "EndpointID": "2" * 64, "IPAddress": "127.0.0.1", "GlobalIPv6Address": ""}}}}
        if self.change == "unowned":
            value["Config"]["Labels"] = {}
        if self.change == "restart" and self.probe_calls:
            value["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
        if self.change == "proc-mount":
            value["Mounts"] = [{"Type": "bind", "Destination": "/proc"}]
        if self.change == "container-network-id":
            value["NetworkSettings"]["Networks"]["EXAMPLE-network"]["NetworkID"] = "3" * 64
        return value

    def network_info(self, network, *, timeout):
        assert network == "EXAMPLE-network" and timeout > 0
        self.network_queries.append(network)
        value = {"Id": "1" * 64, "Name": "EXAMPLE-network", "Driver": "bridge", "Scope": "local",
                 "Labels": {"guard.run": "EXAMPLE-run"},
                 "Containers": {CONTAINER_ID: {"Name": "EXAMPLE-engine", "EndpointID": "2" * 64,
                                               "IPv4Address": "127.0.0.1/8", "IPv6Address": ""}}}
        if self.change == "network-owner":
            value["Labels"] = {}
        elif self.change == "network-address":
            value["Containers"][CONTAINER_ID]["IPv4Address"] = "127.0.0.2/8"
        elif self.change == "network-container":
            value["Containers"] = {"f" * 64: value["Containers"][CONTAINER_ID]}
        elif self.change == "network-endpoint":
            value["Containers"][CONTAINER_ID]["EndpointID"] = "3" * 64
        elif self.change == "network-id":
            value["Id"] = "3" * 64
        return value

    def image_info(self, image, *, timeout):
        assert timeout > 0 and image in {ENGINE_ID, PROBE_PIN}
        self.image_queries.append(image)
        return {"os": "linux", "digests": [] if self.change == "unlinked-image" else
                [ENGINE_PIN if image == ENGINE_ID else PROBE_PIN]}

    def probe(self, network, requests, timeout):
        assert network == "EXAMPLE-network" and timeout > 0
        self.probe_calls.append(deepcopy(requests))
        self.probe_times.append((tuple(row["id"] for row in requests), time.monotonic()))
        if len(requests) == 2:
            assert self.sample_started.wait(1), "pressure capture did not start its bounded resource sampler"
        batch_started = time.monotonic_ns()
        if len(requests) == 2:
            # Keep the synthetic client interval open while the second resource
            # sample runs. The first 'during' sample deliberately precedes HTTP.
            time.sleep(0.05)
            # A loaded test host may let both sampler iterations run before
            # this fake client starts. Admit the second sample only after the
            # existing synthetic client interval is genuinely open.
            self.pressure_window_open.set()
            if self.expected_during_samples == 2:
                assert self.actual_during_sample.wait(1), "the actual client interval has no second source sample"
            # Client metadata uses floored milliseconds, so leave a margin
            # after the complete sample before recording the synthetic close.
            time.sleep(0.003)
        rows = []
        for request in requests:
            profile = request.get("read_profile")
            if profile is None:
                body = json.dumps({"model": "EXAMPLE-model", "choices": [{"text": "PRIVATE_RESPONSE", "finish_reason": "length"}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}})
            else:
                body = ("PRIVATE_RESPONSE" * 100)[:profile["chunk_bytes"] * profile["close_after_reads"]]
            duration = max(30, (time.monotonic_ns() - batch_started) // 1_000_000) if profile else 5
            value = {"id": request["id"], "mode": request.get("mode", "json"), "method": "POST",
                "connection_id": hashlib.sha256((str(len(self.probe_calls)) + request["id"]).encode()).hexdigest()[:24],
                "outcome": 200, "phase": "body", "capture_complete": profile is None,
                "body_truncated": False, "body_bytes": len(body.encode()), "body": body,
                "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "utf8_valid": True,
                "terminal_seen": False, "eof": profile is None,
                "events": [{"seq": 0, "data": "PRIVATE_EVENT"}],
                "stop_reason": "client_close" if profile else "eof",
                "response_headers": {"x-request-id": "PRIVATE_HEADER"}, "detail": "PRIVATE_RAW_ERROR",
                "dispatch_ms": 0, "request_sent_ms": 1, "headers_ms": 1, "first_byte_ms": 2, "end_ms": duration,
                "timing_basis": "batch_monotonic_client_connection_only",
                "clock": None if self.clock_change == "client-missing" else {
                    "basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 101,
                    "batch_started_monotonic_ns": batch_started}}
            if profile:
                value.update(relative_clock="monotonic", read_count=profile["close_after_reads"], read_pause_count=profile["close_after_reads"] - 1,
                    client_closed=True, client_close_ms=duration,
                    read_observations=[{"seq": index, "started_ms": index * (profile["interval_ms"] + 1) + 2,
                                        "finished_ms": index * (profile["interval_ms"] + 1) + 3,
                                        "bytes": profile["chunk_bytes"]} for index in range(profile["close_after_reads"])])
            rows.append(value)
        if len(requests) == 2:
            # Model result serialization after close, leaving the next source
            # sample beyond the conservative upper millisecond edge. Python
            # 3.12 Windows monotonic may use a 15.625 ms GetTickCount64 tick.
            time.sleep(0.02)
        return rows

    def sample(self, docker, target, env, *, deadline, port):
        assert docker is self and target["engine"]["image_digest"] == env.engine.image_digest
        assert deadline > 0 and 1 <= port <= 65535
        if len(self.sample_calls) == 2 and self.expected_during_samples == 2:
            assert self.pressure_window_open.wait(1), "synthetic client interval did not open"
        started = time.monotonic_ns()
        self.sample_calls.append(port)
        count = len(self.sample_calls)
        finished = time.monotonic_ns()
        clock = {"basis": "linux-monotonic-time-namespace-v1",
                 "time_namespace_inode": 102 if self.clock_change == "namespace" else 101,
                 "sample_started_monotonic_ns": 1 if self.clock_change == "sample-outside" else started,
                 "sample_finished_monotonic_ns": 2 if self.clock_change == "sample-outside" else finished}
        if self.clock_change == "sample-missing" or count == self.missing_sample:
            clock = None
        if len(self.sample_calls) >= 2:
            self.sample_started.set()
        if len(self.sample_calls) == 3:
            self.actual_during_sample.set()
        return {"owner": owner(), "observations": {
            "clock": clock,
            "collector": {"pid": 2, "start_time_ticks": 10, "namespaces": deepcopy(NAMESPACES)},
            "processes": [{"pid": 1, "start_time_ticks": 9, "rss_bytes": 4096,
                           "namespaces": deepcopy(NAMESPACES), "identity_stable": True}],
            "cgroup_memory_current_bytes": 8192, "cgroup_version": "v2", "tcp_port": port,
            "tcp_connections": [], "memory_scope": "process_rss_and_container_cgroup_aggregate",
            "queue_scope": "kernel_per_socket_queues_at_sample_time",
            "tuple_hash_scheme": "sha256-unordered-normalized-ip-port-pair-with-netns-v1"},
            "timestamp": datetime.now(timezone.utc).isoformat(), "capture_hashes": {
                "helper_sha256": "e" * 64, "packet_sha256": "f" * 64, "retained_bytes": 128,
                "transport_complete": True},
            "complete": self.sample_complete and clock is not None,
            "diagnostic_codes": (["RESOURCE_CLOCK_UNAVAILABLE"] if clock is None else
                                 [] if self.sample_complete else ["RESOURCE_PACKET_INCOMPLETE"])}

    def cleanup(self, *, timeout):
        self.cleanups.append(timeout)
        return []


def test_early_sampler_waits_for_the_actual_synthetic_client_window():
    class EarlySampler(PressureWorld):
        def __init__(self):
            super().__init__()
            self.second_during_requested = threading.Event()

        def sample(self, *args, **kwargs):
            if len(self.sample_calls) == 2:
                self.second_during_requested.set()
            return super().sample(*args, **kwargs)

        def probe(self, network, requests, timeout):
            if len(requests) == 2:
                assert self.second_during_requested.wait(1), "second sampler iteration was not reached"
            return super().probe(network, requests, timeout)

    env, target, cfg = inputs()
    world = EarlySampler()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    during = [row for row in result["resource_samples"] if row["phase"] == "during"]
    assert len(during) == 2
    assert during[0]["phase_interval_verified"] is False
    assert during[1]["phase_interval_verified"] is True
    assert result["resource_samples_complete"] is True
    assert result["client_intervals_overlap"] is True and result["read_procedure_verified"] is True
    assert result["data_origin"] == "EXAMPLE"


def test_capture_preserves_partial_scope_exact_inputs_and_drops_response_content():
    env, target, cfg = inputs()
    world = PressureWorld()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["schema"] == "guard-stream-pressure-capture/1" and result["check_id"] == "LIF-04"
    assert result["environment_ref"] == env.ref().model_dump(mode="json")
    assert result["run_id"] == target["run_id"] and result["data_origin"] == "EXAMPLE"
    assert result["capture_executed"] is True and result["engine_started"] is result["product_connected"] is result["official"] is False
    assert {result[axis] for axis in ("security", "functional", "performance")} == {"UNKNOWN"}
    assert {stage["stage"] for stage in result["stages"]} == {"before", "pressure", "after"}
    assert result["normal_response_schema_verified"] == {"before": True, "parallel": True, "after": True}
    assert result["read_procedure_verified"] is True and result["client_intervals_overlap"] is True
    assert result["identity_stable"] is True
    assert result["resource_samples_complete"] is True
    assert {row["phase"] for row in result["resource_samples"] if row["phase_interval_verified"]} == {"before", "during", "after"}
    during = [row for row in result["resource_samples"] if row["phase"] == "during"]
    assert any(not row["phase_interval_verified"] for row in during)
    assert any(row["phase_interval_verified"] for row in during)
    assert {row["phase"] for row in result["resource_samples"]} >= {"before", "after"}
    assert len(result["resource_samples"]) <= cfg["max_resource_samples"]
    assert world.probe_calls and world.sample_calls and not world.cleanups
    assert "PRIVATE_" not in json.dumps(result)
    assert result["worker_cancel_verified"] is result["buffer_cost_attribution_verified"] is result["resource_recovery_verified"] is False
    assert baseline_pack().checks["LIF-04"].executor is baseline_pack().checks["LIF-04"].judge is None


def test_partial_resource_capture_preserves_samples_without_making_resource_claims():
    env, target, cfg = inputs()
    world = PressureWorld(sample_complete=False)
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["resource_samples_complete"] is False and result["read_procedure_verified"] is True
    assert result["resource_samples"]
    assert all(row["sample"]["observations"]["cgroup_memory_current_bytes"] == 8192 for row in result["resource_samples"])
    assert result["worker_cancel_verified"] is result["resource_recovery_verified"] is False
    assert {result[axis] for axis in ("security", "functional", "performance")} == {"UNKNOWN"}


def test_pre_http_sample_does_not_establish_actual_pressure_interval_coverage():
    env, target, cfg = inputs()
    cfg["max_resource_samples"] = 3
    world = PressureWorld(expected_during_samples=1)
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["read_procedure_verified"] is True and result["identity_stable"] is True
    assert result["resource_samples_complete"] is False
    during = [row for row in result["resource_samples"] if row["phase"] == "during"]
    assert len(during) == 1 and during[0]["phase_interval_verified"] is False
    assert "PRESSURE_RESOURCE_INTERVAL_UNVERIFIED" in result["collection_errors"]


@pytest.mark.parametrize("clock_change", ["client-missing", "sample-missing", "namespace", "sample-outside"])
def test_resource_clock_gaps_do_not_erase_observations_or_invent_interval_coverage(clock_change):
    env, target, cfg = inputs()
    world = PressureWorld(clock_change=clock_change)
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["read_procedure_verified"] is True
    assert result["resource_samples_complete"] is False
    assert "PRESSURE_RESOURCE_INTERVAL_UNVERIFIED" in result["collection_errors"]
    assert result["resource_samples"]
    assert all(row["sample"]["observations"]["cgroup_memory_current_bytes"] == 8192 for row in result["resource_samples"])
    assert result["worker_cancel_verified"] is result["resource_recovery_verified"] is False


def test_missing_actual_during_sample_cannot_be_filled_by_complete_before_or_after_samples():
    env, target, cfg = inputs()
    world = PressureWorld(missing_sample=3)
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["resource_samples_complete"] is False and result["read_procedure_verified"] is True
    assert any(not row["sample"]["complete"] for row in result["resource_samples"])
    assert "PRESSURE_RESOURCE_INTERVAL_UNVERIFIED" in result["collection_errors"]
    assert result["worker_cancel_verified"] is result["resource_recovery_verified"] is False


def test_configured_post_close_wait_precedes_after_control():
    env, target, cfg = inputs()
    cfg["post_close_wait_ms"] = 200
    world = PressureWorld()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    times = {labels: point for labels, point in world.probe_times}
    assert times[("after",)] - times[("slow", "parallel")] >= 0.18
    assert result["normal_response_schema_verified"]["after"] is True


def test_api_secret_is_transported_in_headers_without_entering_attachment():
    env, target, cfg = inputs()
    cfg["api_key_env"] = "GUARD_PRESSURE_EXAMPLE_KEY"
    world = PressureWorld()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample,
                              environ={"GUARD_PRESSURE_EXAMPLE_KEY": "PRIVATE_AUTH"})
    assert all(request["headers"]["Authorization"] == "Bearer PRIVATE_AUTH"
               for batch in world.probe_calls for request in batch)
    assert "PRIVATE_" not in json.dumps(result) and "GUARD_PRESSURE_EXAMPLE_KEY" not in json.dumps(result)


def test_missing_api_secret_prevents_requests_and_sampling():
    env, target, cfg = inputs()
    cfg["api_key_env"] = "GUARD_PRESSURE_EXAMPLE_KEY"
    world = PressureWorld()
    with pytest.raises(ValueError, match="CREDENTIAL_UNAVAILABLE"):
        capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample, environ={})
    assert not world.probe_calls and not world.sample_calls


@pytest.mark.parametrize("change", ["unowned", "unlinked-image", "proc-mount"])
def test_unverified_initial_source_does_not_dispatch_even_normal_requests(change):
    env, target, cfg = inputs()
    world = PressureWorld(change=change)
    with pytest.raises(ValueError):
        capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert not world.probe_calls and not world.sample_calls


def test_later_restart_is_preserved_as_an_identity_gap():
    env, target, cfg = inputs()
    world = PressureWorld(change="restart")
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert result["identity_stable"] is False
    assert "PRESSURE_ENGINE_IDENTITY_CHANGED" in result["collection_errors"]
    assert result["security"] == result["functional"] == "UNKNOWN"


@pytest.mark.parametrize("change", ["network-owner", "network-address", "network-container", "network-endpoint",
                                     "network-id", "container-network-id"])
def test_unbound_network_identity_prevents_every_http_request(change):
    env, target, cfg = inputs()
    world = PressureWorld(change=change)
    with pytest.raises(ValueError):
        capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert not world.probe_calls


def test_literal_target_address_must_match_the_owned_container_endpoint():
    env, target, cfg = inputs("http://127.0.0.2:8000")
    world = PressureWorld()
    with pytest.raises(ValueError):
        capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert not world.probe_calls


def test_container_dns_target_is_replaced_with_independently_bound_engine_address():
    from urllib.parse import urlsplit
    env, target, cfg = inputs("http://EXAMPLE-engine:8000")
    world = PressureWorld()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    assert world.network_queries
    assert all(urlsplit(request["url"]).hostname == "127.0.0.1" for batch in world.probe_calls for request in batch)
    assert result["normal_response_schema_verified"] == {"before": True, "parallel": True, "after": True}


@pytest.mark.parametrize("lab,origin", [
    (True, DataOrigin.DECLARED),
    (False, DataOrigin.DECLARED),
    (False, DataOrigin.EXAMPLE),
    (False, DataOrigin.MEASURED),
])
def test_injected_probe_and_sampler_remain_example_even_for_managed_or_measured_inputs(lab, origin):
    env, target, cfg = inputs(origin=origin)
    target["lab"] = lab
    validate_inputs(env, target, cfg)
    world = PressureWorld()
    result = capture_pressure(world, env, target, cfg, probe=world.probe, sampler=world.sample)
    # An injected EXAMPLE producer cannot acquire a real source from the ENV.
    assert result["data_origin"] == "EXAMPLE"
    assert result["engine_started"] is result["product_connected"] is False
    assert {result[axis] for axis in ("security", "functional", "performance")} == {"UNKNOWN"}


def test_product_uses_real_reader_on_test_owned_socket_and_keeps_raw_output_private():
    from guard.executors.stream_probe import collect
    from tests.test_stream_probe import _response, _server

    def handler(conn, request, index, stop):
        body = json.loads(request.partition(b"\r\n\r\n")[2])
        assert body["model"] == "EXAMPLE-model" and 1 <= body["max_tokens"] <= 16
        if body.get("stream"):
            payload = (b'data: {"choices":[{"text":"PRIVATE_STREAM_OUTPUT"}]}\n\n' * 24
                       + b"data: [DONE]\n\n")
            _response(conn, payload, content_type="text/event-stream")
        else:
            _response(conn, json.dumps({"model": body["model"], "choices": [{"text": "PRIVATE_RESPONSE", "finish_reason": "length"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}).encode())
    with _server(handler) as (url, accepted):
        env, target, cfg = inputs(url)
        world = PressureWorld()
        result = capture_pressure(world, env, target, cfg, probe=lambda _network, requests, timeout: collect(requests, timeout),
                                  sampler=world.sample)
    assert len(accepted) == 4
    assert result["normal_response_schema_verified"] == {"before": True, "parallel": True, "after": True}
    assert result["read_procedure_verified"] is True
    slow = next(row for stage in result["stages"] for row in stage["responses"] if row["id"] == "slow")
    assert slow["read_count"] == 3 and slow["read_pause_count"] == 2
    assert slow["stop_reason"] == "client_close" and slow["capture_complete"] is False and slow["eof"] is False
    assert slow["body_bytes"] == sum(row["bytes"] for row in slow["read_observations"])
    assert slow["body_bytes"] <= cfg["read_profile"]["chunk_bytes"] * cfg["read_profile"]["close_after_reads"]
    assert result["worker_cancel_verified"] is result["resource_recovery_verified"] is False
    assert "PRIVATE_" not in json.dumps(result)


def test_default_probe_factory_requires_no_implicit_pull(monkeypatch):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world, factories = PressureWorld(), []
    def factory(client, image, **options):
        factories.append((client, image, options))
        def guarded(network, requests, timeout):
            return [{**row, "deadline_guard_verified": True} for row in world.probe(network, requests, timeout)]
        return guarded
    monkeypatch.setattr(module, "docker_stream_probe", factory)
    monkeypatch.setattr(module, "capture_resource_sample", world.sample)
    result = capture_pressure(world, env, target, cfg)
    assert len(factories) == 1 and factories[0][:2] == (world, PROBE_PIN)
    options = factories[0][2]
    assert set(options) == {"pull_never", "parent_deadline", "deadline_guard"}
    assert options["pull_never"] is True
    guard = options["deadline_guard"]
    assert guard["basis"] == "linux-monotonic-time-namespace-v1" and guard["time_namespace_inode"] == 101
    assert 0 < guard["deadline_monotonic_ns"] < 2**63
    assert time.monotonic() < options["parent_deadline"] <= time.monotonic() + cfg["max_duration_seconds"]
    assert PROBE_PIN in world.image_queries and result["read_procedure_verified"] is True


def test_source_cutoff_uses_receipt_budget_without_equating_parent_and_vm_epochs():
    from guard.executors.stream_pressure import _source_cutoff
    sample = {"owner": owner(), "capture_hashes": {"transport_complete": True}, "complete": False,
              "observations": {"clock": {"basis": "linux-monotonic-time-namespace-v1",
                "time_namespace_inode": 101, "sample_started_monotonic_ns": 1_000_000,
                "sample_finished_monotonic_ns": 4_001_000_000}}}
    guard = _source_cutoff(sample, 90_003.0, 90_005.0, owner())
    assert guard == {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 101,
                     "deadline_monotonic_ns": 2_001_000_000}
    # The source cutoff is intentionally already past if sampling/transport
    # consumed more than the remaining budget. It is never rebased to receipt.
    assert guard["deadline_monotonic_ns"] < sample["observations"]["clock"]["sample_finished_monotonic_ns"]


@pytest.mark.parametrize("problem", ["clock-missing", "transport-incomplete", "owner-changed"])
def test_real_docker_probe_is_not_created_without_a_verified_source_cutoff(monkeypatch, problem):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world = PressureWorld()
    def sample(*args, **kwargs):
        value = world.sample(*args, **kwargs)
        if problem == "clock-missing":
            value["observations"]["clock"] = None
        elif problem == "transport-incomplete":
            value["capture_hashes"]["transport_complete"] = False
        else:
            value["owner"] = None
        value["complete"] = False
        return value
    monkeypatch.setattr(module, "capture_resource_sample", sample)
    monkeypatch.setattr(module, "docker_stream_probe", lambda *a, **kw: pytest.fail("unverified source started a probe"))
    result = capture_pressure(world, env, target, cfg)
    assert world.probe_calls == [] and len(world.sample_calls) == 2
    assert result["read_procedure_verified"] is result["resource_samples_complete"] is False
    assert result["normal_response_schema_verified"] == dict.fromkeys(("before", "parallel", "after"), False)
    assert "PRESSURE_SOURCE_DEADLINE_UNVERIFIED" in result["collection_errors"]
    assert all(row["sample"]["observations"]["cgroup_memory_current_bytes"] == 8192 for row in result["resource_samples"])


def test_missing_source_guard_acknowledgement_cannot_verify_a_normal_control(monkeypatch):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world = PressureWorld()
    monkeypatch.setattr(module, "capture_resource_sample", world.sample)
    monkeypatch.setattr(module, "docker_stream_probe", lambda *a, **kw: world.probe)
    result = capture_pressure(world, env, target, cfg)
    assert len(world.probe_calls) == 1
    assert result["normal_response_schema_verified"] == dict.fromkeys(("before", "parallel", "after"), False)
    assert result["read_procedure_verified"] is False
    assert "PRESSURE_SOURCE_DEADLINE_UNVERIFIED" in result["collection_errors"]
    assert result["stages"][0]["responses"][0]["body_bytes"] > 0
    assert "PRIVATE_" not in json.dumps(result)


@pytest.mark.parametrize("origin,expected", [(DataOrigin.DECLARED, "DECLARED"),
                                             (DataOrigin.MEASURED, "DECLARED"),
                                             (DataOrigin.EXAMPLE, "EXAMPLE")])
def test_default_producers_keep_managed_origin_ceiling(monkeypatch, origin, expected):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs(origin=origin)
    target["lab"] = False
    world = PressureWorld()
    def factory(*args, **kwargs):
        def guarded(network, requests, timeout):
            return [{**row, "deadline_guard_verified": True} for row in world.probe(network, requests, timeout)]
        return guarded
    monkeypatch.setattr(module, "capture_resource_sample", world.sample)
    monkeypatch.setattr(module, "docker_stream_probe", factory)
    result = capture_pressure(world, env, target, cfg)
    assert result["data_origin"] == expected
    assert result["read_procedure_verified"] is True


@pytest.mark.parametrize("change", [
    {"schema": "unknown"}, {"unknown": 1}, {"max_output_tokens": 0}, {"max_output_tokens": 17},
    {"max_output_tokens": True}, {"request_timeout_seconds": 0}, {"request_timeout_seconds": 31},
    {"max_duration_seconds": float("nan")}, {"max_resource_samples": 2}, {"max_resource_samples": 17},
    {"resource_interval_ms": 0}, {"resource_interval_ms": 1001}, {"post_close_wait_ms": -1},
    {"post_close_wait_ms": 1001}, {"api_key_env": "private-lowercase"},
])
def test_invalid_execution_budget_or_schema_is_rejected_before_collection(change):
    with pytest.raises(ValueError):
        normalize_settings({"schema": "guard-stream-pressure-settings/1", **change})


@pytest.mark.parametrize("change", [
    lambda e, t: t.update(lab=None),
    lambda e, t: t.update(lab="true"),
    lambda e, t: t["engine"].update(image_digest="unpinned:latest"),
    lambda e, t: t["engine"].update(trusted_url="http://other-host:8000"),
    lambda e, t: t["engine"].update(trusted_url="http://127.0.0.1:8000/private"),
    lambda e, t: t.update(probe_image="unapproved:latest"),
])
def test_target_validation_does_not_fill_missing_ownership_or_scope(change):
    env, target, cfg = inputs()
    change(env, target)
    with pytest.raises(ValueError):
        validate_inputs(env, target, cfg)


def test_sealed_environment_is_required():
    env, target, cfg = inputs()
    corrupted = env.model_copy(update={"content_sha256": "0" * 64})
    assert corrupted.hash_ok() is False
    with pytest.raises(ValueError):
        validate_inputs(corrupted, target, cfg)


def test_execute_cleans_only_its_fresh_probe_owner_when_collection_raises(monkeypatch):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world, error = PressureWorld(), RuntimeError("EXAMPLE programming failure")
    assert world.run_id != target["run_id"]
    monkeypatch.setattr(module, "create_pressure_client", lambda: world)
    def fail(*args, **kwargs):
        assert args == (world, env, target, cfg)
        raise error
    monkeypatch.setattr(module, "capture_pressure", fail)
    with pytest.raises(RuntimeError) as caught:
        execute_pressure(env, target, cfg)
    assert caught.value is error and len(world.cleanups) == 1


def test_execute_returns_capture_and_records_cleanup_failure_without_raw_diagnostic(monkeypatch):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world, attachment = PressureWorld(), {"collection_errors": [], "engine_started": False}
    monkeypatch.setattr(module, "create_pressure_client", lambda: world)
    monkeypatch.setattr(module, "capture_pressure", lambda *args, **kwargs: attachment)
    def failed_cleanup(*, timeout):
        world.cleanups.append(timeout)
        return ["PRIVATE_CLEANUP_DIAGNOSTIC"]
    monkeypatch.setattr(world, "cleanup", failed_cleanup)
    result = execute_pressure(env, target, cfg)
    assert result["collection_errors"] == ["PRESSURE_PROBE_CLEANUP_INCOMPLETE"]
    assert len(world.cleanups) == 1 and "PRIVATE_" not in json.dumps(result)


def test_cleanup_owner_collision_is_rejected_before_capture_or_cleanup(monkeypatch):
    import guard.executors.stream_pressure as module
    env, target, cfg = inputs()
    world = PressureWorld()
    world.run_id = target["run_id"]
    monkeypatch.setattr(module, "create_pressure_client", lambda: world)
    monkeypatch.setattr(module, "capture_pressure", lambda *args: pytest.fail("unexpected collection"))
    with pytest.raises(ValueError, match="OWNER_COLLISION"):
        execute_pressure(env, target, cfg)
    assert not world.cleanups
