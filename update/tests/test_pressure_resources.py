"""EXAMPLE resource fixtures/owned OS sockets; never actual serving engines."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import time

import pytest

from guard.executors import pressure_resource_probe as probe
from guard.executors.pressure_resources import capture_resource_sample, validate_packet
from guard.runtime.docker import CmdResult
from tests.builders import environment


IMAGE = "example.invalid/engine@sha256:" + "a" * 64
IMAGE_ID = "sha256:" + "b" * 64
TARGET = {"run_id": "EXAMPLE-owned-run", "engine": {"container": "EXAMPLE-engine", "image_digest": IMAGE}}
NS = {name: 100 + index for index, name in enumerate(probe.NAMESPACES)}


def packet():
    observations = probe.empty_observations(8000)
    observations.update(collector={"pid": 999, "start_time_ticks": 9, "namespaces": dict(NS)},
        clock={"basis": probe.CLOCK_BASIS, "time_namespace_inode": 777,
               "sample_started_monotonic_ns": 1_000_000_000, "sample_finished_monotonic_ns": 1_100_000_000},
        processes=[{"pid": 1, "start_time_ticks": 10, "rss_bytes": 4096,
                    "namespaces": dict(NS), "identity_stable": True}],
        cgroup_memory_current_bytes=8192, cgroup_version="v2",
        tcp_connections=[{"family": "ipv4", "tuple_sha256": "c" * 64, "state": 1,
            "queue_kind": "established_sequence_bytes", "tx_queue_bytes": 64, "rx_queue_bytes": 8,
            "listen_pending_connections": None, "inode": 99,
            "owners": [{"pid": 1, "start_time_ticks": 10}], "identity_stable": True, "ownership_complete": True}])
    return {"schema": probe.SCHEMA, "profile": probe.PROFILE,
            "timestamp": "2026-10-26T00:00:00+00:00", "observations": observations,
            "complete": True, "diagnostic_codes": []}


class Docker:
    run_id = "EXAMPLE-different-cleanup-owner"

    def __init__(self, value=None, *, change=None, transport=None):
        self.packet = packet() if value is None else value
        self.change = change
        self.transport = transport
        self.inspect_calls = 0
        self.calls = []

    def inspect_json(self, container, *, timeout):
        self.inspect_calls += 1
        self.calls.append(("inspect", container))
        row = {"Id": "d" * 64, "Name": "/EXAMPLE-engine", "Image": IMAGE_ID,
            "Config": {"Image": IMAGE, "Labels": {"guard.run": TARGET["run_id"]}, "Env": ["NEVER_PERSIST_ENV_SECRET"]},
            "State": {"Running": True, "StartedAt": "2026-10-26T00:00:00Z", "Pid": 12345},
            "HostConfig": {"PidMode": "", "NetworkMode": "EXAMPLE-owned-net", "CgroupnsMode": "private"}, "Mounts": []}
        if self.change:
            self.change(row, self.inspect_calls)
        return row

    def image_info(self, image, *, timeout):
        self.calls.append(("image", image))
        return {"digests": [IMAGE]}

    def exec_bounded(self, container, argv, *, timeout, max_bytes):
        self.calls.append(("exec", container, argv[0], argv[-1]))
        assert argv[:2] == ["python3", "-c"] and argv[-1] == "8000"
        assert "pressure_resource_probe" not in argv[2]  # standalone source, no package import required
        raw = json.dumps(self.packet).encode()
        capture = {"complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False}
        capture.update(self.transport or {})
        return CmdResult(0, raw.decode(), "NEVER_PERSIST_DAEMON_SECRET", stdout_bytes=raw, capture=capture)


def sample(docker):
    return capture_resource_sample(docker, TARGET, environment(engine={"name": "vllm", "image_digest": IMAGE}),
                                   deadline=time.monotonic() + 30, port=8000)


def test_owned_capture_has_exact_projection_and_actual_image_lookup_without_cleanup():
    docker = Docker()
    result = sample(docker)
    assert set(result) == {"owner", "observations", "timestamp", "capture_hashes", "complete", "diagnostic_codes"}
    assert result["complete"] is True, result["diagnostic_codes"]
    assert result["owner"]["run_id"] == TARGET["run_id"] != docker.run_id
    assert ("image", IMAGE_ID) in docker.calls
    assert result["observations"]["processes"][0]["rss_bytes"] == 4096
    assert result["observations"]["memory_scope"] == probe.MEMORY_SCOPE
    assert result["observations"]["queue_scope"] == probe.QUEUE_SCOPE
    assert set(call[0] for call in docker.calls) == {"inspect", "image", "exec"}
    assert "NEVER_PERSIST" not in json.dumps(result)


@pytest.mark.parametrize("change,code", [
    (lambda row, n: row["HostConfig"].update(PidMode="host"), "RESOURCE_HOST_NAMESPACE_UNSUPPORTED"),
    (lambda row, n: row["HostConfig"].update(NetworkMode="host"), "RESOURCE_HOST_NAMESPACE_UNSUPPORTED"),
    (lambda row, n: row["HostConfig"].update(NetworkMode="container:other"), "RESOURCE_HOST_NAMESPACE_UNSUPPORTED"),
    (lambda row, n: row["HostConfig"].update(CgroupnsMode="host"), "RESOURCE_CGROUP_NAMESPACE_UNSUPPORTED"),
    (lambda row, n: row["HostConfig"].pop("CgroupnsMode"), "RESOURCE_CGROUP_NAMESPACE_UNSUPPORTED"),
    (lambda row, n: row.update(Mounts=[{"Type": "bind", "Destination": "/proc", "Source": "/NEVER_PERSIST_PATH"}]), "RESOURCE_PROC_MOUNT_UNSUPPORTED"),
    (lambda row, n: row.update(Mounts=[{"Type": "bind", "Destination": "/", "Source": "/NEVER_PERSIST_PATH"}]), "RESOURCE_PROC_MOUNT_UNSUPPORTED"),
    (lambda row, n: row.update(Mounts=[{"Type": "bind", "Destination": "/sys", "Source": "/NEVER_PERSIST_PATH"}]), "RESOURCE_PROC_MOUNT_UNSUPPORTED"),
    (lambda row, n: row.update(Mounts=[{"Type": "bind", "Destination": "/sys/fs/cgroup", "Source": "/NEVER_PERSIST_PATH"}]), "RESOURCE_PROC_MOUNT_UNSUPPORTED"),
    (lambda row, n: row["Config"]["Labels"].update({"guard.run": "EXAMPLE-other"}), "RESOURCE_OWNER_UNVERIFIED"),
    (lambda row, n: row["State"].update(Running=False), "RESOURCE_OWNER_UNVERIFIED"),
    (lambda row, n: row["State"].pop("Pid"), "RESOURCE_OWNER_UNVERIFIED"),
])
def test_unverified_host_namespace_or_proc_overlay_never_executes_helper(change, code):
    docker = Docker(change=change)
    result = sample(docker)
    assert result["complete"] is False and code in result["diagnostic_codes"]
    assert not any(call[0] == "exec" for call in docker.calls)
    assert "NEVER_PERSIST" not in json.dumps(result)


def test_owner_restart_does_not_silently_bind_sample_to_previous_instance():
    def restart(row, n):
        if n > 1:
            row["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
    result = sample(Docker(change=restart))
    assert result["complete"] is False and "RESOURCE_OWNER_CHANGED" in result["diagnostic_codes"]
    assert result["owner"] is None
    assert result["observations"]["processes"][0]["rss_bytes"] == 4096


def test_configured_image_string_without_actual_id_repodigest_is_unverified():
    docker = Docker()
    docker.image_info = lambda *args, **kwargs: {"digests": ["example.invalid/other@sha256:" + "e" * 64]}
    result = sample(docker)
    assert not result["complete"] and "RESOURCE_IMAGE_UNVERIFIED" in result["diagnostic_codes"]
    assert not any(call[0] == "exec" for call in docker.calls)


@pytest.mark.parametrize("transport", [{"truncated": True}, {"complete": False}, {"timed_out": True}])
def test_incomplete_transport_keeps_valid_known_observations(transport):
    result = sample(Docker(transport=transport))
    assert result["complete"] is False and "RESOURCE_TRANSPORT_INCOMPLETE" in result["diagnostic_codes"]
    assert result["observations"]["tcp_connections"][0]["tx_queue_bytes"] == 64
    assert result["capture_hashes"]["packet_sha256"] == hashlib.sha256(json.dumps(packet()).encode()).hexdigest()


@pytest.mark.parametrize("change", [
    lambda value: value["observations"]["processes"][0].update(rss_bytes=-1),
    lambda value: value["observations"]["tcp_connections"][0].update(tx_queue_bytes=-1),
    lambda value: value["observations"]["tcp_connections"][0].update(family=["NEVER_PERSIST_ARBITRARY"]),
    lambda value: value["observations"].update(cgroup_memory_current_bytes=-1),
    lambda value: value["observations"].update(collector={"path": "NEVER_PERSIST_ARBITRARY"}),
    lambda value: value["diagnostic_codes"].append("NEVER_PERSIST_ARBITRARY"),
    lambda value: value.update(unexpected="NEVER_PERSIST_ARBITRARY"),
])
def test_malformed_packet_is_incomplete_and_arbitrary_payload_strings_are_discarded(change):
    value = packet()
    change(value)
    result = sample(Docker(value))
    assert result["complete"] is False and result["diagnostic_codes"]
    assert "NEVER_PERSIST" not in json.dumps(result)
    assert result["timestamp"] == value["timestamp"]


def test_missing_rss_is_null_and_other_process_sample_is_preserved():
    value = packet()
    value["observations"]["processes"][0]["rss_bytes"] = None
    value["observations"]["processes"].append({"pid": 2, "start_time_ticks": 20, "rss_bytes": 2048,
                                              "namespaces": dict(NS), "identity_stable": True})
    result = sample(Docker(value))
    assert result["complete"] is False
    assert [row["rss_bytes"] for row in result["observations"]["processes"]] == [None, 2048]


@pytest.mark.parametrize("change", [
    lambda clock: clock.update(basis="NEVER_PERSIST_CLOCK_SECRET"),
    lambda clock: clock.update(time_namespace_inode=-1),
    lambda clock: clock.update(sample_started_monotonic_ns=0),
    lambda clock: clock.update(sample_finished_monotonic_ns=1),
    lambda clock: clock.update(sample_finished_monotonic_ns=1_000_000_000 + probe.MAX_SAMPLE_DURATION_NS + 1),
    lambda clock: clock.update(unexpected="NEVER_PERSIST_CLOCK_SECRET"),
])
def test_invalid_clock_cannot_establish_coverage_or_erase_known_resource_values(change):
    value = packet()
    change(value["observations"]["clock"])
    result = sample(Docker(value))
    assert result["complete"] is False and "RESOURCE_CLOCK_UNAVAILABLE" in result["diagnostic_codes"]
    assert result["observations"]["clock"] is None
    assert result["observations"]["processes"][0]["rss_bytes"] == 4096
    assert "NEVER_PERSIST" not in json.dumps(result)


def test_missing_clock_is_unknown_and_maximum_valid_clock_interval_is_accepted():
    value = packet()
    value["observations"]["clock"] = None
    missing = sample(Docker(value))
    assert missing["complete"] is False and "RESOURCE_CLOCK_UNAVAILABLE" in missing["diagnostic_codes"]
    value = packet()
    clock = value["observations"]["clock"]
    clock["sample_finished_monotonic_ns"] = clock["sample_started_monotonic_ns"] + probe.MAX_SAMPLE_DURATION_NS
    valid = sample(Docker(value))
    assert valid["complete"] is True and valid["observations"]["clock"] == clock


def test_changed_pid_cannot_retain_rss_or_socket_owner_from_other_generation():
    value = packet()
    value["observations"]["processes"][0]["identity_stable"] = False
    result = sample(Docker(value))
    assert result["complete"] is False
    assert result["observations"]["processes"][0]["rss_bytes"] is None
    assert result["observations"]["tcp_connections"][0]["owners"] == []
    assert result["observations"]["tcp_connections"][0]["tx_queue_bytes"] == 64


def test_duplicate_json_keys_and_nonfinite_numbers_are_not_normalized_into_complete_capture():
    docker = Docker()
    def malformed(container, argv, **kwargs):
        raw = b'{"schema":"guard-pressure-resource-packet/1","schema":"NEVER_PERSIST_ARBITRARY","value":NaN}'
        return CmdResult(0, raw.decode(), "NEVER_PERSIST_STDERR", stdout_bytes=raw,
                         capture={"complete": True, "bounded_at_source": True, "truncated": False, "timed_out": False})
    docker.exec_bounded = malformed
    result = sample(docker)
    assert result["complete"] is False and "RESOURCE_PACKET_INVALID" in result["diagnostic_codes"]
    assert "NEVER_PERSIST" not in json.dumps(result)


def _stat(pid, start):
    fields = ["0"] * 22
    fields[0], fields[19] = "S", str(start)
    return str(pid) + " (EXAMPLE command with ) punctuation) " + " ".join(fields)


@pytest.fixture
def files(tmp_path, monkeypatch):
    root, cgroup = tmp_path / "EXAMPLE-proc", tmp_path / "EXAMPLE-cgroup"
    links = {}
    for pid, start in ((999, 9), (1, 10)):
        folder = root / str(pid)
        (folder / "ns").mkdir(parents=True)
        (folder / "fd").mkdir()
        (folder / "stat").write_text(_stat(pid, start), encoding="ascii")
        (folder / "status").write_text("Name:\tEXAMPLE\nVmRSS:\t4 kB\n", encoding="ascii")
        for name, inode in NS.items():
            links[str(folder / "ns" / name)] = f"{name}:[{inode}]"
    (root / "1" / "fd" / "5").touch()
    links[str(root / "1" / "fd" / "5")] = "socket:[99]"
    (root / "1" / "fd" / "6").touch()
    links[str(root / "1" / "fd" / "6")] = "/NEVER_PERSIST_FD_PATH"
    (root / "net").mkdir()
    header = "  sl local_address rem_address st tx_queue rx_queue tr tm->when retrnsmt uid timeout inode\n"
    (root / "net" / "tcp").write_text(header + "0: 0100007F:1F40 0100007F:C350 01 00000040:00000008 00:0 0 1000 0 99\n", encoding="ascii")
    (root / "net" / "tcp6").write_text(header, encoding="ascii")
    (root / "self").mkdir()
    links[str(root / "self" / "ns" / "time")] = "time:[777]"
    (root / "self" / "cgroup").write_text("0::/\n", encoding="ascii")
    cgroup.mkdir()
    (cgroup / "cgroup.controllers").write_text("memory cpu\n", encoding="ascii")
    (cgroup / "memory.current").write_text("8192\n", encoding="ascii")
    real_readlink = os.readlink
    def readlink(path, *args, **kwargs):
        return links[str(path)] if str(path) in links else real_readlink(path, *args, **kwargs)
    monkeypatch.setattr(os, "readlink", readlink)
    return root, cgroup, links


def test_file_fixture_collects_rss_cgroup_and_socket_owner_without_addresses_or_paths(files):
    root, cgroup, _ = files
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    observations, _, complete, diagnostics = validate_packet(value, port=8000)
    assert complete is True, diagnostics
    assert observations["processes"] == [{"pid": 1, "start_time_ticks": 10, "rss_bytes": 4096,
                                          "namespaces": NS, "identity_stable": True}]
    assert observations["cgroup_memory_current_bytes"] == 8192
    clock = observations["clock"]
    assert clock["basis"] == probe.CLOCK_BASIS and clock["time_namespace_inode"] == 777
    assert 0 < clock["sample_started_monotonic_ns"] <= clock["sample_finished_monotonic_ns"]
    assert clock["sample_finished_monotonic_ns"] - clock["sample_started_monotonic_ns"] <= probe.MAX_SAMPLE_DURATION_NS
    assert observations["tcp_connections"][0]["owners"] == [{"pid": 1, "start_time_ticks": 10}]
    text = json.dumps(value)
    assert "127.0.0.1" not in text and "0100007F" not in text
    assert str(root) not in text and "NEVER_PERSIST" not in text


@pytest.mark.parametrize("family,table,local,remote", [
    ("ipv4", "tcp", "0100007F", "00000000"),
    ("ipv6", "tcp6", "00000000000000000000000001000000", "0" * 32),
])
@pytest.mark.parametrize("state", [1, 4, 6, 10, 12])
def test_queue_units_depend_on_socket_state_and_never_label_listen_backlog_as_bytes(
        files, family, table, local, remote, state):
    root, cgroup, _ = files
    path = root / "net" / table
    header = path.read_text(encoding="ascii").splitlines()[0] + "\n"
    path.write_text(header + f"0: {local}:1F40 {remote}:0000 {state:02X} "
                    "00000040:00000002 00:0 0 1000 0 99\n", encoding="ascii")
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    observations, _, complete, diagnostics = validate_packet(value, port=8000)
    assert complete is (state in (1, 10)), diagnostics
    assert ("RESOURCE_QUEUE_STATE_UNSUPPORTED" in diagnostics) is (state not in (1, 10))
    row = next(row for row in observations["tcp_connections"] if row["family"] == family)
    assert row["state"] == state
    assert row["tx_queue_bytes"] == (64 if state == 1 else None)
    assert row["rx_queue_bytes"] == (2 if state == 1 else None)
    assert row["listen_pending_connections"] == (2 if state == 10 else None)
    assert row["queue_kind"] == ("established_sequence_bytes" if state == 1 else
                                  "listen_backlog_connections" if state == 10 else "uninterpreted")


@pytest.mark.parametrize("change", [
    {"state": 10},  # Legacy byte labels must not be accepted for a listener.
    {"state": 10, "queue_kind": "listen_backlog_connections", "listen_pending_connections": 2},
    {"state": 1, "listen_pending_connections": 2},
    {"state": 4, "queue_kind": "uninterpreted"},
    {"queue_kind": "NEVER_PERSIST_QUEUE_KIND"},
])
def test_contradictory_queue_metadata_is_rejected_without_erasing_other_resource_values(change):
    value = packet()
    value["observations"]["tcp_connections"][0].update(change)
    result = sample(Docker(value))
    assert not result["complete"] and "RESOURCE_PACKET_INVALID" in result["diagnostic_codes"]
    assert result["observations"]["tcp_connections"] == []
    assert result["observations"]["processes"][0]["rss_bytes"] == 4096
    assert "NEVER_PERSIST" not in json.dumps(result)


def test_file_fixture_pid_reuse_clears_only_changed_process_binding(files, monkeypatch):
    root, cgroup, _ = files
    original = probe.process_start
    calls = 0
    def changed(proc_root, pid):
        nonlocal calls
        if pid == 1:
            calls += 1
            return 10 if calls == 1 else 11
        return original(proc_root, pid)
    monkeypatch.setattr(probe, "process_start", changed)
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert not value["complete"] and "RESOURCE_PROCESS_CHANGED" in value["diagnostic_codes"]
    assert value["observations"]["processes"][0]["rss_bytes"] is None
    assert value["observations"]["tcp_connections"][0]["owners"] == []
    assert value["observations"]["cgroup_memory_current_bytes"] == 8192


@pytest.mark.parametrize("problem", ["missing", "changed", "too-long"])
def test_fixture_time_namespace_or_clock_failure_keeps_known_values_without_coverage(files, monkeypatch, problem):
    root, cgroup, links = files
    if problem == "missing":
        links.pop(str(root / "self" / "ns" / "time"))
    elif problem == "changed":
        calls = 0
        def changed(proc_root):
            nonlocal calls
            calls += 1
            return 777 if calls == 1 else 778
        monkeypatch.setattr(probe, "time_namespace", changed)
    else:
        moments = iter((1, probe.MAX_SAMPLE_DURATION_NS + 2))
        monkeypatch.setattr(probe.time, "monotonic_ns", lambda: next(moments))
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert value["complete"] is False and "RESOURCE_CLOCK_UNAVAILABLE" in value["diagnostic_codes"]
    assert value["observations"]["clock"] is None
    assert value["observations"]["processes"][0]["rss_bytes"] == 4096
    assert value["observations"]["tcp_connections"][0]["tx_queue_bytes"] == 64


@pytest.mark.parametrize("missing", ["rss", "cgroup", "tcp6", "fd"])
def test_file_fixture_missing_source_cannot_be_reported_as_zero_or_complete(files, missing):
    root, cgroup, _ = files
    if missing == "rss":
        (root / "1" / "status").write_text("VmRSS:\t-1 kB\n", encoding="ascii")
    elif missing == "cgroup":
        (cgroup / "memory.current").unlink()
    elif missing == "tcp6":
        (root / "net" / "tcp6").unlink()
    else:
        (root / "1" / "fd" / "5").unlink()
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert value["complete"] is False and value["diagnostic_codes"]
    if missing == "rss":
        assert value["observations"]["processes"][0]["rss_bytes"] is None
    if missing == "cgroup":
        assert value["observations"]["cgroup_memory_current_bytes"] is None
    assert value["observations"]["tcp_connections"][0]["tx_queue_bytes"] == 64


def test_process_and_descriptor_bounds_keep_collected_metadata_but_prevent_completeness(files, monkeypatch):
    root, cgroup, _ = files
    monkeypatch.setattr(probe, "MAX_FDS", 1)
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert value["complete"] is False and "RESOURCE_FD_BOUND" in value["diagnostic_codes"]
    assert value["observations"]["processes"][0]["rss_bytes"] == 4096


def test_process_bound_preserves_the_first_complete_process(files, monkeypatch):
    root, cgroup, _ = files
    (root / "2").mkdir()
    monkeypatch.setattr(probe, "MAX_PROCESSES", 1)
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert not value["complete"] and "RESOURCE_PROCESS_BOUND" in value["diagnostic_codes"]
    assert value["observations"]["processes"][0]["rss_bytes"] == 4096


def test_truncated_cgroup_file_is_null_without_discarding_socket_observation(files):
    root, cgroup, _ = files
    (cgroup / "memory.current").write_text("1" * 70, encoding="ascii")
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert not value["complete"] and "RESOURCE_CGROUP_UNAVAILABLE" in value["diagnostic_codes"]
    assert value["observations"]["cgroup_memory_current_bytes"] is None
    assert value["observations"]["tcp_connections"][0]["tx_queue_bytes"] == 64


def test_malformed_tcp_row_does_not_erase_a_valid_neighbor(files):
    root, cgroup, _ = files
    table = root / "net" / "tcp"
    table.write_text(table.read_text(encoding="ascii") +
        "1: 0100007F:1F40 0100007F:C351 01 -1:00000000 00:0 0 1000 0 100\n", encoding="ascii")
    value = probe.collect(8000, proc_root=root, cgroup_root=cgroup, self_pid=999)
    assert not value["complete"] and "RESOURCE_TCP_ROW_INVALID" in value["diagnostic_codes"]
    assert len(value["observations"]["tcp_connections"]) == 1
    assert value["observations"]["tcp_connections"][0]["inode"] == 99


def test_expired_capture_deadline_does_not_query_or_start_anything():
    docker = Docker()
    value = capture_resource_sample(docker, TARGET, environment(engine={"name": "vllm", "image_digest": IMAGE}),
                                    deadline=time.monotonic() - 1, port=8000)
    assert value["complete"] is False and value["diagnostic_codes"] == ["RESOURCE_DEADLINE"]
    assert docker.calls == []


def test_same_namespace_reverse_endpoints_use_same_hash_but_different_namespace_does_not():
    first, second = ["127.0.0.1", 8000], ["127.0.0.1", 50000]
    assert probe.tuple_digest(first, second, 101) == probe.tuple_digest(second, first, 101)
    assert probe.tuple_digest(first, second, 101) != probe.tuple_digest(first, second, 102)


def test_own_os_process_and_owned_socket_or_explicit_platform_limitation(capsys):
    if sys.platform != "linux":
        assert probe.main(["8000"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["complete"] is False and result["diagnostic_codes"] == ["RESOURCE_PLATFORM_UNSUPPORTED"]
        return
    own = os.getpid()
    assert probe.process_start(Path("/proc"), own) > 0
    namespaces = probe.process_namespaces(Path("/proc"), own)
    assert set(namespaces) == set(probe.NAMESPACES)
    assert probe.process_rss(Path("/proc"), own) > 0
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        diagnostics = set()
        rows = probe.tcp_rows(Path("/proc"), listener.getsockname()[1], namespaces["net"], diagnostics)
        assert any(row["state"] == 10 for row in rows)
        assert all("127.0.0.1" not in json.dumps(row) for row in rows)
