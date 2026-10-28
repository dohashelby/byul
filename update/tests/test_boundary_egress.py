"""Explicit EXAMPLE transports; no Docker daemon, receiver or engine is run."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors import boundary_egress as module
from guard.executors.boundary_egress import BoundaryEgressExecutor, normalize_policy
from guard.models import DataOrigin, ExecStatus, Verdict, canonical_json, new_id, sha256_hex
from guard.store import Store
from tests.builders import baseline_pack, environment, policy


SOURCE_PIN = "example.invalid/engine@sha256:" + "a" * 64
RECEIVER_PIN = "example.invalid/receiver@sha256:" + "b" * 64
SOURCE_CID, RECEIVER_CID = "c" * 64, "d" * 64
SOURCE_IMAGE_ID, RECEIVER_IMAGE_ID = "sha256:" + "e" * 64, "sha256:" + "f" * 64
NETWORK_ID = "1" * 64
SOURCE_IP, RECEIVER_IP = "10.42.0.2", "10.42.0.3"
SOURCE_NS, RECEIVER_NS = 101, 202
PORT, UID = 9000, 1001
STARTED = "2026-10-26T00:00:00Z"


def values():
    now = datetime.now(timezone.utc)
    return {"subject_scope": "all_processes_in_container_uid_netns", "subject_container_id": SOURCE_CID,
            "subject_uid": UID, "subject_net_namespace_inode": SOURCE_NS,
            "forbidden_receiver_container_id": RECEIVER_CID, "forbidden_receiver_ip": RECEIVER_IP,
            "forbidden_receiver_port": PORT, "forbidden_protocol": "tcp", "forbidden_operation": "tcp_connect",
            "validity_start": (now - timedelta(minutes=5)).isoformat(),
            "validity_end": (now + timedelta(minutes=5)).isoformat()}


def target(receiver=False, lab=True):
    name, pin, port = ("EXAMPLE-receiver", RECEIVER_PIN, PORT) if receiver else ("EXAMPLE-engine", SOURCE_PIN, 8000)
    return {"lab": lab, "run_id": "EXAMPLE-run", "engine": {"container": name, "image_digest": pin,
            "trusted_url": f"http://{name}:{port}"}, "origins": {"trusted": {"network": "EXAMPLE-network"}}}


def receiver_command():
    return ["python3", "-u", "-c", Path(module.egress_receiver.__file__).read_text(encoding="utf-8"), str(PORT)]


def capture_result(value, *, incomplete=False, raw=None):
    raw = json.dumps(value).encode() if raw is None else raw
    return SimpleNamespace(returncode=0, stdout=raw.decode(), stdout_bytes=raw, stderr="PRIVATE_RAW_STDERR",
        capture={"complete": not incomplete, "bounded_at_source": True, "truncated": False, "timed_out": False})


class World:
    def __init__(self, change=None, outcome="completed", event_time=None):
        self.change, self.outcome = change, outcome
        self.event_time = event_time
        self.calls, self.sent, self.file_reads = [], False, 0
        self.receiver_identity = {"pid": 1, "start_time_ticks": 20, "uid": 0, "net_namespace_inode": RECEIVER_NS}
        if change == "same-netns":
            self.receiver_identity["net_namespace_inode"] = SOURCE_NS
        self.rows = [{"schema": "guard-owned-egress-receiver/1", "kind": "startup",
            "identity": deepcopy(self.receiver_identity), "local": {"ip": "0.0.0.0", "port": PORT},
            "peer": None, "socket_inode": 55, "observed_at": ((event_time or datetime.now(timezone.utc)) - timedelta(seconds=1)).isoformat(),
            "correlation_sha256": None, "received_bytes": 0, "ack_sent": False}]

    def inspect_json(self, container, *, timeout):
        assert timeout > 0 and container in {"EXAMPLE-engine", "EXAMPLE-receiver"}
        self.calls.append(("inspect", container))
        receiver = container == "EXAMPLE-receiver"
        cid, image, pin, ip, endpoint = ((RECEIVER_CID, RECEIVER_IMAGE_ID, RECEIVER_PIN, RECEIVER_IP, "3" * 64)
                                      if receiver else (SOURCE_CID, SOURCE_IMAGE_ID, SOURCE_PIN, SOURCE_IP, "2" * 64))
        row = {"Id": cid, "Name": "/" + container, "Image": image,
            "Config": {"Image": pin, "Labels": {"guard.run": "EXAMPLE-run"},
                       "Cmd": receiver_command() if receiver else ["EXAMPLE-engine"], "Entrypoint": None},
            "State": {"Running": True, "StartedAt": STARTED, "Pid": 234 if receiver else 123},
            "HostConfig": {"PidMode": "", "NetworkMode": "EXAMPLE-network", "CgroupnsMode": "private", "Privileged": False},
            "Mounts": [], "NetworkSettings": {"Networks": {"EXAMPLE-network": {"NetworkID": NETWORK_ID,
                "EndpointID": endpoint, "IPAddress": ip, "GlobalIPv6Address": ""}}}}
        if self.change == "receiver-command" and receiver:
            row["Config"]["Cmd"] = ["PRIVATE_UNRELATED_COMMAND"]
        if self.change == "entrypoint" and receiver:
            row["Config"]["Entrypoint"] = ["PRIVATE_ENTRYPOINT"]
        if self.change == "source-restart" and self.sent and not receiver:
            row["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
        if self.change == "receiver-restart" and self.sent and receiver:
            row["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
        if self.change == "host-network":
            row["HostConfig"]["NetworkMode"] = "host"
        if self.change == "host-cgroup":
            row["HostConfig"]["CgroupnsMode"] = "host"
        if self.change == "proc-mount":
            row["Mounts"] = [{"Type": "bind", "Destination": "/proc"}]
        return row

    def image_info(self, image, *, timeout):
        assert timeout > 0 and image in {SOURCE_IMAGE_ID, RECEIVER_IMAGE_ID}
        self.calls.append(("image", image))
        return {"os": "linux", "digests": [] if self.change == "unlinked-image" else
                [SOURCE_PIN if image == SOURCE_IMAGE_ID else RECEIVER_PIN]}

    def network_info(self, network, *, timeout):
        assert timeout > 0 and network == "EXAMPLE-network"
        return {"Id": NETWORK_ID, "Name": network, "Driver": "bridge", "Scope": "local",
            "Labels": {"guard.run": "EXAMPLE-run"}, "Containers": {
                SOURCE_CID: {"Name": "EXAMPLE-engine", "EndpointID": "2" * 64, "IPv4Address": SOURCE_IP + "/24", "IPv6Address": ""},
                RECEIVER_CID: {"Name": "EXAMPLE-receiver", "EndpointID": "3" * 64,
                    "IPv4Address": ("10.42.0.4" if self.change == "endpoint" else RECEIVER_IP) + "/24", "IPv6Address": ""}}}

    def source_sample(self, docker, source, env, *, deadline, port):
        assert docker is self and source["engine"]["container"] == "EXAMPLE-engine" and port == PORT
        self.calls.append(("source-clock", False))
        stamp = self.event_time + timedelta(microseconds=200) if self.event_time else datetime.now(timezone.utc)
        now = time.monotonic_ns()
        clock = {"basis": "linux-monotonic-time-namespace-v1", "time_namespace_inode": 777,
                 "sample_started_monotonic_ns": now, "sample_finished_monotonic_ns": now}
        if self.change == "source-clock-missing":
            clock = None
        if self.change == "source-clock-skew":
            stamp -= timedelta(days=1)
        return {"owner": {"container_id": SOURCE_CID, "image_id": SOURCE_IMAGE_ID, "started_at": STARTED,
                "image": SOURCE_PIN, "run_id": "EXAMPLE-run", "host_pid": 123},
                "observations": {"clock": clock}, "timestamp": stamp.isoformat(),
                "capture_hashes": {"transport_complete": self.change != "source-clock-transport"},
                # Other independent resource fields are intentionally incomplete.
                "complete": False, "diagnostic_codes": ["RESOURCE_RSS_UNAVAILABLE"]}

    def exec_bounded(self, container, argv, *, timeout, max_bytes, uid=None):
        assert timeout > 0 and max_bytes in {65536, 128 * 1024}
        if argv[2] == module._RECEIVER_PROCESS:
            assert container == "EXAMPLE-receiver" and uid is None
            self.calls.append(("receiver-process", self.sent))
            packet = {"schema": "guard-egress-receiver-process/1", "identity": deepcopy(self.receiver_identity),
                "command_sha256": sha256_hex(canonical_json(receiver_command())), "socket_inodes": [55], "complete": True}
            if self.change == "writer-pid":
                packet["identity"]["start_time_ticks"] += 1
            if self.change == "writer-command":
                packet["command_sha256"] = "9" * 64
            if self.change == "listener-fd":
                packet["socket_inodes"] = [56]
            return capture_result(packet)
        if container == "EXAMPLE-receiver":
            assert uid is None and argv[3] == "/guard-observations/egress.jsonl"
            self.calls.append(("file", self.sent))
            self.file_reads += 1
            rows = deepcopy(self.rows)
            if self.change == "file-prefix" and self.sent:
                rows[0]["socket_inode"] = 99
            raw = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
            identity = {"device": 1, "inode": 3 if self.change == "file-inode" and self.sent else 2,
                        "size": len(raw), "mtime_ns": 4, "ctime_ns": 5}
            packet = {"schema": "guard-container-file/2", "source_complete": True, "file_identity_stable": True,
                "truncated": False, "nofollow_components": True, "regular_file": True,
                "raw_base64": base64.b64encode(raw).decode(), "raw_retained_bytes": len(raw),
                "raw_retained_sha256": hashlib.sha256(raw).hexdigest(),
                "file_before": identity, "file_after": identity, "path_after": identity}
            return capture_result(packet)
        assert container == "EXAMPLE-engine" and uid == UID
        assert argv[:3] == ["python3", "-c", Path(module.egress_probe.__file__).read_text(encoding="utf-8")]
        assert argv[3:5] == [RECEIVER_IP, str(PORT)] and argv[7:10] == [str(UID), str(SOURCE_NS), "777"]
        assert len(argv) == 11 and 0 < int(argv[10]) < 2**63
        assert 0 < float(argv[6]) <= 2
        self.calls.append(("sender", uid))
        self.sent = True
        self.marker = argv[5]
        marker_hash = hashlib.sha256(argv[5].encode("ascii")).hexdigest()
        identity = {"pid": 77, "start_time_ticks": 100, "uid": UID, "net_namespace_inode": SOURCE_NS}
        local, peer = {"ip": SOURCE_IP, "port": 45000}, {"ip": RECEIVER_IP, "port": PORT}
        connected_at = (self.event_time + timedelta(microseconds=450) if self.event_time
                        else datetime.now(timezone.utc)).isoformat()
        marker_sent_at = (self.event_time + timedelta(microseconds=475) if self.event_time
                          else datetime.now(timezone.utc)).isoformat()
        receipt = {"schema": "guard-owned-egress-receiver/1", "kind": "receipt", "identity": deepcopy(self.receiver_identity),
            "local": deepcopy(peer), "peer": deepcopy(local), "socket_inode": 66,
            "observed_at": (self.event_time + timedelta(microseconds=500) if self.event_time
                            else datetime.now(timezone.utc)).isoformat(), "correlation_sha256": marker_hash,
            "received_bytes": 32, "ack_sent": True}
        if self.outcome == "completed":
            self.rows.append(receipt)
        packet = {"schema": "guard-owned-egress-probe/1", "identity_before": deepcopy(identity), "identity_after": deepcopy(identity),
            "local": local, "peer": peer, "socket_inode": 88, "correlation_sha256": marker_hash,
            "connected": self.outcome == "completed", "marker_sent": self.outcome == "completed", "ack_verified": self.outcome == "completed",
            "sent_bytes": 32 if self.outcome == "completed" else 0, "received_bytes": 65 if self.outcome == "completed" else 0,
            "outcome": self.outcome, "observed_at": (self.event_time + timedelta(microseconds=600) if self.event_time
                else datetime.now(timezone.utc)).isoformat(), "process_identity_stable": True,
            "deadline_guard_verified": not self.outcome.startswith("source_"),
            "connected_at": connected_at if self.outcome == "completed" else None,
            "marker_sent_at": marker_sent_at if self.outcome == "completed" else None,
            "connection_guard_verified": self.outcome == "completed"}
        if self.change in {"ack-timeout", "ack-mismatch", "receiver-ack-error", "source-guard-ack"}:
            packet.update(ack_verified=False, received_bytes=66 if self.change == "ack-mismatch" else 0,
                          outcome="ack_mismatch" if self.change in {"ack-mismatch", "receiver-ack-error"} else "timeout")
        if self.change == "receiver-ack-error":
            receipt["ack_sent"] = False
        if self.change == "source-guard-ack":
            packet.update(deadline_guard_verified=False, outcome="source_deadline")
        if self.change == "final-clock-missing":
            packet["observed_at"] = None
        if self.change == "post-send-expiry":
            packet["observed_at"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        if self.change == "connection-guard-missing":
            packet["connection_guard_verified"] = False
        if self.change == "source-uid":
            packet["identity_after"]["uid"] += 1
        if self.change == "source-netns":
            packet["identity_before"]["net_namespace_inode"] += 1
            packet["identity_after"]["net_namespace_inode"] += 1
        if self.change == "source-pid":
            packet["identity_after"]["start_time_ticks"] += 1
        if self.change == "socket-missing":
            packet["socket_inode"] = None
        if self.change == "tuple":
            receipt["peer"]["port"] += 1
        if self.change == "marker":
            receipt["correlation_sha256"] = "9" * 64
        if self.change == "duplicate-receipt":
            self.rows.append(deepcopy(receipt))
        if self.change == "clock":
            receipt["observed_at"] = "2000-01-01T00:00:00Z"
        if self.change == "clock-missing":
            packet["connected_at"] = None
        if self.change == "marker-clock-missing":
            packet["marker_sent_at"] = None
        if self.change == "reversed-event-clock":
            packet["marker_sent_at"] = "2000-01-01T00:00:00Z"
        if self.change == "raw-error":
            raise OSError("PRIVATE_DAEMON_ERROR " + argv[5])
        if self.change == "duplicate-sender":
            raw = json.dumps(packet).replace('"ack_verified": true', '"ack_verified": false, "ack_verified": true').encode()
            return capture_result(packet, raw=raw)
        return capture_result(packet, incomplete=self.change == "transport")


def run(tmp_path, monkeypatch, world=None, policy_values=None, extra=None, *, origin=DataOrigin.EXAMPLE, lab=True):
    world = World() if world is None else world
    source, receiver = target(lab=lab), target(True, lab=lab)
    paths = []
    for name, value in (("target", source), ("receiver", receiver)):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value), encoding="utf-8")
        paths.append(str(path))
    cfg = {"target": paths[0], "receiver_target": paths[1], "connect_timeout": 0.5, "max_duration_seconds": 20, **(extra or {})}
    monkeypatch.setitem(registry.EXECUTORS, "boundary_egress:sim", lambda _: BoundaryEgressExecutor(cfg, docker=world))
    monkeypatch.setattr(module, "capture_resource_sample", world.source_sample)
    store, pack = Store(tmp_path / "store"), baseline_pack()
    env = environment(id=new_id("ENV"), origin=origin, engine={"name": "vllm", "version": "0.32.0", "image_digest": SOURCE_PIN},
                      model={"id": "EXAMPLE-model"})
    pol = policy(pack.ref(), id=new_id("POL"), scope={"include": ["PLT-07"], "exclude": []},
                 values={"PLT-07": deepcopy(values() if policy_values is None else policy_values)})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"boundary_egress": "sim"}, adapter="vllm"))
    res, = judge_plan(store, pack, plan, as_of=env.created_at)
    return store, pack, plan, evd, res, world


def work_summary(store, plan, evd):
    return store.root / "work" / plan.id / "PLT-07" / str(evd.side) / "boundary_egress.json"


def test_explicit_forbidden_connect_requires_both_owned_sources_and_remains_partial(tmp_path, monkeypatch):
    store, _, plan, evd, result, world = run(tmp_path, monkeypatch)
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin == DataOrigin.EXAMPLE
    assert len(evd.observations["forbidden_egress_successes"].value) == 1
    trial, = evd.observations["egress_trials"].value
    assert trial["verified"] is True and trial["sender_identity"]["uid"] == UID
    assert trial["receiver_identity"]["net_namespace_inode"] != trial["sender_identity"]["net_namespace_inode"]
    assert evd.observations["egress_scope_complete"].value is False
    assert result.verdicts.security == Verdict.FAIL
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert evd.normal_control.attempted is False and evd.normal_control.succeeded is None
    assert evd.challenges and all(row.attempted is False for row in evd.challenges)
    assert world.calls.count(("sender", UID)) == 1
    assert evd.artifacts == []
    raw = work_summary(store, plan, evd).read_text(encoding="utf-8")
    canonical = json.dumps({key: value.model_dump() for key, value in evd.observations.items()})
    for private in (SOURCE_IP, RECEIVER_IP, "PRIVATE_", world.marker, "python3", "/guard-observations/egress.jsonl"):
        assert private not in raw and private not in canonical
    assert '"ip":' not in raw and '"port":' not in raw and '"ip":' not in canonical and '"port":' not in canonical


@pytest.mark.parametrize("outcome", ["connect_refused", "timeout", "transport_error", "identity_mismatch",
                                     "source_deadline", "source_clock_unavailable", "source_clock_mismatch"])
def test_denial_or_unavailable_evidence_never_claims_egress_pass(tmp_path, monkeypatch, outcome):
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, World(outcome=outcome))
    assert evd.observations["forbidden_egress_successes"].value == []
    assert evd.observations["egress_scope_complete"].value is False
    assert result.verdicts.security == result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN


@pytest.mark.parametrize("change", ["receiver-command", "entrypoint", "host-network", "host-cgroup", "proc-mount",
    "unlinked-image", "endpoint", "writer-pid", "writer-command", "listener-fd", "same-netns",
    "source-clock-missing", "source-clock-skew", "source-clock-transport"])
def test_unverified_receiver_or_ownership_blocks_the_active_attempt(tmp_path, monkeypatch, change):
    _, _, _, evd, result, world = run(tmp_path, monkeypatch, World(change))
    assert ("sender", UID) not in world.calls
    assert evd.observations["forbidden_egress_successes"].value == [] and result.verdicts.security == Verdict.UNKNOWN


@pytest.mark.parametrize("change", ["source-restart", "receiver-restart", "source-uid", "source-netns", "source-pid",
    "socket-missing", "tuple", "marker", "duplicate-receipt", "clock", "clock-missing", "file-prefix", "file-inode",
    "transport", "raw-error", "duplicate-sender", "connection-guard-missing", "marker-clock-missing", "reversed-event-clock"])
def test_mismatched_or_partial_two_sided_evidence_is_unknown_without_raw_payload(tmp_path, monkeypatch, change):
    store, _, plan, evd, result, _ = run(tmp_path, monkeypatch, World(change))
    assert evd.observations["forbidden_egress_successes"].value == [] and result.verdicts.security == Verdict.UNKNOWN
    trial, = evd.observations["egress_trials"].value
    assert trial["verified"] is False and trial["diagnostic_codes"]
    assert "PRIVATE_" not in json.dumps(evd.observations["egress_trials"].model_dump())
    assert evd.artifacts == []
    assert "PRIVATE_" not in work_summary(store, plan, evd).read_text(encoding="utf-8")


@pytest.mark.parametrize("change", ["ack-timeout", "ack-mismatch", "receiver-ack-error", "source-guard-ack",
                                     "final-clock-missing", "post-send-expiry"])
def test_auxiliary_ack_or_final_collection_failure_cannot_erase_verified_forbidden_tcp_connect(tmp_path, monkeypatch, change):
    # tcp_connect is the forbidden operation. Historical connection/marker
    # timestamps and the independent receipt remain proof when only the later
    # ACK or final collection metadata fails; the complete-unknown cases above
    # still have no connection guard/event clocks and remain UNKNOWN.
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, World(change))
    assert result.verdicts.security == Verdict.FAIL and result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    assert len(evd.observations["forbidden_egress_successes"].value) == 1
    trial, = evd.observations["egress_trials"].value
    assert trial["verified"] is True and trial["connection_guard_verified"] is True
    assert set(trial["operation_timestamps"]) == {"connected_at", "marker_sent_at", "received_at"}
    if change in {"ack-timeout", "ack-mismatch", "receiver-ack-error", "source-guard-ack"}:
        assert trial["sender_ack_verified"] is False
    if change == "receiver-ack-error":
        assert trial["receiver_ack_sent"] is False
    if change == "source-guard-ack":
        assert trial["final_deadline_guard_verified"] is False
    if change == "final-clock-missing":
        assert trial["sender_observed_at"] is None


@pytest.mark.parametrize("change", [
    lambda v: v.pop("subject_uid"), lambda v: v.update(subject_scope="worker_only"),
    lambda v: v.update(subject_uid=True), lambda v: v.update(subject_net_namespace_inode=0),
    lambda v: v.update(forbidden_receiver_container_id=SOURCE_CID),
    lambda v: v.update(forbidden_receiver_ip="receiver.example.invalid"),
    lambda v: v.update(forbidden_receiver_port=0), lambda v: v.update(forbidden_protocol="udp"),
    lambda v: v.update(forbidden_operation="any"), lambda v: v.update(validity_start="2026-01-01T00:00:00"),
    lambda v: v.update(validity_end=v["validity_start"]), lambda v: v.update(unknown="PRIVATE_POLICY"),
])
def test_policy_has_no_inferred_rule_or_default_and_rejects_unsupported_scope(change):
    declared = values()
    change(declared)
    with pytest.raises(ValueError):
        normalize_policy(declared)


@pytest.mark.parametrize("key,value", [("connect_timeout", 0), ("connect_timeout", 2.1),
    ("max_duration_seconds", 31), ("max_duration_seconds", True), ("unknown", 1)])
def test_invalid_execution_budget_is_rejected_before_any_io(tmp_path, key, value):
    world = World()
    executor = BoundaryEgressExecutor({key: value}, docker=world)
    with pytest.raises(ValueError):
        executor.run(baseline_pack().checks["PLT-07"], environment(), values(), None, tmp_path)
    assert world.calls == []


def test_verified_violation_survives_later_artifact_persistence_failure(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("PRIVATE_ARTIFACT_ERROR")
    monkeypatch.setattr(module, "_write_summary", fail)
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch)
    assert result.verdicts.security == Verdict.FAIL and evd.observations["forbidden_egress_successes"].value
    assert evd.artifacts == []
    assert "EGRESS_ARTIFACT_UNAVAILABLE" in evd.observations["egress_trials"].value[0]["diagnostic_codes"]


def test_guard_run_canonical_observations_do_not_depend_on_duplicate_artifact_storage(tmp_path, monkeypatch):
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(True)
        raise OSError("PRIVATE_ARTIFACT_STORE_ERROR")
    monkeypatch.setattr(Store, "put_artifact", unavailable)
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch)
    assert calls == [] and evd.artifacts == []
    assert evd.execution.status == ExecStatus.COMPLETED and result.verdicts.security == Verdict.FAIL
    assert evd.observations["forbidden_egress_successes"].value
    assert evd.observations["egress_trials"].value[0]["verified"] is True


def test_later_collection_after_policy_expiry_does_not_erase_an_in_window_operation(tmp_path, monkeypatch):
    world, original, advanced = World(), module._wall_now, []
    def collection_clock():
        # The parent finishes receiving the already committed source record
        # after policy expiry. Both source operation timestamps remain in-window.
        if world.file_reads >= 2 and not advanced:
            advanced.append(True)
            return original() + timedelta(days=1)
        return original()
    monkeypatch.setattr(module, "_wall_now", collection_clock)
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, world)
    assert advanced and result.verdicts.security == Verdict.FAIL
    assert evd.observations["forbidden_egress_successes"].value


def test_injected_transport_cannot_promote_managed_measured_environment_to_real_measurement(tmp_path, monkeypatch):
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, origin=DataOrigin.MEASURED, lab=False)
    assert result.verdicts.security == Verdict.FAIL and evd.data_origin == DataOrigin.EXAMPLE


def test_subsecond_source_events_use_a_precise_collector_window(tmp_path, monkeypatch):
    base = datetime(2030, 1, 1, tzinfo=timezone.utc)
    world = World(event_time=base)
    declared = values()
    declared.update(validity_start=(base - timedelta(seconds=1)).isoformat(),
                    validity_end=(base + timedelta(seconds=1)).isoformat())
    points = iter(base + timedelta(microseconds=n) for n in (0, 100, 300, 400, 700))
    monkeypatch.setattr(module, "_wall_now", lambda: next(points))
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, world, declared)
    assert result.verdicts.security == Verdict.FAIL and evd.observations["forbidden_egress_successes"].value


@pytest.mark.parametrize("future", [False, True])
def test_policy_window_excludes_the_attempt_without_sending(tmp_path, monkeypatch, future):
    declared = values()
    now = datetime.now(timezone.utc)
    declared["validity_start"] = (now + timedelta(days=1) if future else now - timedelta(days=2)).isoformat()
    declared["validity_end"] = (now + timedelta(days=2) if future else now - timedelta(days=1)).isoformat()
    _, _, _, evd, result, world = run(tmp_path, monkeypatch, policy_values=declared)
    assert ("sender", UID) not in world.calls
    assert not evd.observations["forbidden_egress_successes"].value and result.verdicts.security == Verdict.UNKNOWN


@pytest.mark.parametrize("change", [None, "receiver-ack-error", "final-clock-missing"])
def test_saved_export_restore_and_rejudge_preserve_the_same_partial_failure(tmp_path, monkeypatch, change):
    from guard.core.report import build_report
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack, put_pack
    from tests.builders import PACK_DIR
    store, pack, plan, evd, result, _ = run(tmp_path, monkeypatch, World(change))
    before = evd.to_dict()
    report = build_report(store, pack, plan, as_of=evd.created_at)
    store.put(report)
    assert put_pack(store, PACK_DIR) == pack.ref() and report.official is False
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert store.verify() == restored.verify() == []
    saved = get_pack(restored, pack.ref())
    replay, = judge_plan(restored, saved, restored.resolve(plan.ref()), as_of=evd.created_at)
    assert replay.ref() == result.ref() and restored.resolve(evd.ref()).to_dict() == before
