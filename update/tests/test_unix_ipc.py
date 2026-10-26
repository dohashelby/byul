"""PLT-02 partial IPC contract; Docker/engine observations here are EXAMPLE."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from guard.core.judge import judge_plan
from guard.core.run import RunConfig, run_plan
from guard.core.select import select
from guard.executors import registry
from guard.executors.unix_ipc import UnixIPCExecutor, _parse, policy_shape
from guard.models import ExecStatus, Verdict, new_id
from guard.store import Store
from tests.builders import baseline_pack, completion_response, environment, policy


PIN = "example.invalid/engine@sha256:" + "a" * 64
NS = {k: f"{k}:[123]" for k in ("mnt", "net", "pid", "user")}
SOCKET = {"device": 1, "inode": 2, "uid": 0, "gid": 0, "mode": 511, "ctime_ns": 4}
OWNER = {"pid": 9, "start": "12", "uid": 0}
VALUES = {"protected_unix_paths": ["/tmp/EXAMPLE-ipc.sock"], "allowed_ipc_uids": ["0"]}


class World:
    def __init__(self, connected=False, mutate=None, functional_error=False, source_error=False):
        self.connected, self.mutate = connected, mutate
        self.functional_error, self.source_error = functional_error, source_error
        self.calls = []

    def inspect_json(self, container, timeout):
        self.calls.append(("identity", container))
        return {"Id": "b" * 64, "Name": "/EXAMPLE-engine", "Image": "sha256:" + "c" * 64,
                "Config": {"Image": PIN, "Labels": {"guard.run": "EXAMPLE-run"}},
                "HostConfig": {"PidMode": ""},
                "State": {"Running": True, "StartedAt": "2026-10-26T00:00:00Z"}}

    def exec_as_uid(self, container, uid, argv, timeout):
        mode = argv[3]
        self.calls.append((mode, uid))
        if self.source_error:
            raise OSError("PRIVATE_DAEMON_ERROR")
        value = {"schema": "guard-unix-ipc-probe/1", "mode": mode, "uid": uid,
                 "namespaces": deepcopy(NS), "rows": []}
        if mode == "inspect":
            value["rows"] = [{"index": 0, "complete": True, "identity": deepcopy(SOCKET),
                              "socket_inode": "55", "owners": [deepcopy(OWNER)]}]
        else:
            value["groups"] = []
            connected = uid == 0 or self.connected
            value["rows"] = [{"index": 0, "outcome": "connected" if connected else "denied"}]
            if connected:
                value["rows"][0].update(identity=deepcopy(SOCKET), path_stable=True,
                                       peer={"pid": 9, "uid": 0, "gid": 0})
        if self.mutate:
            self.mutate(value)
        return SimpleNamespace(returncode=0, stdout=json.dumps(value), stderr="PRIVATE_STDERR")

    def probe(self, net, requests, timeout):
        if self.functional_error and any(r["method"] == "POST" for r in requests):
            raise OSError("PRIVATE_FUNCTIONAL_ERROR")
        return [{"id": r["id"], **completion_response(r)} for r in requests]


def run(tmp_path, monkeypatch, world=None, values=None, extra=None):
    world = world or World()
    target = {"lab": True, "run_id": "EXAMPLE-run", "probe_image": "EXAMPLE-unused",
              "origins": {"trusted": {"network": "EXAMPLE-network"}},
              "engine": {"container": "EXAMPLE-engine", "image_digest": PIN,
                         "trusted_url": "http://EXAMPLE-engine:8000", "ready_timeout": 1}}
    path = tmp_path / "target.json"
    path.write_text(json.dumps(target), encoding="utf-8")
    cfg = {"target": str(path), "probe_uids": [0, 1001], "request_timeout": 1, **(extra or {})}
    monkeypatch.setitem(registry.EXECUTORS, "unix_ipc:sim",
        lambda _: UnixIPCExecutor(cfg, docker=world, container_probe=world.probe))
    store, pack = Store(tmp_path / "store"), baseline_pack()
    env = environment(id=new_id("ENV"), engine={"name": "vllm", "version": "0.32.0", "image_digest": PIN}, model={"id": "EXAMPLE-model"})
    pol = policy(pack.ref(), id=new_id("POL"), scope={"include": ["PLT-02"], "exclude": []},
                 values={"PLT-02": deepcopy(VALUES if values is None else values)})
    store.put_many([env, pol])
    plan = select(pack, [env], pol)
    store.put(plan)
    evd, = run_plan(store, pack, plan, RunConfig(executors={"unix_ipc": "sim"}, adapter="vllm"))
    res, = judge_plan(store, pack, plan, as_of=env.created_at)
    return store, pack, plan, evd, res, world


def test_allowed_control_and_disallowed_denial_are_partial_unknown(tmp_path, monkeypatch):
    store, _, _, evd, res, world = run(tmp_path, monkeypatch)
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin.value == "EXAMPLE"
    assert evd.observations["unix_ipc_violations"].value == []
    assert evd.observations["unix_ipc_trials"].collected is True
    assert evd.normal_control.succeeded is True
    assert res.verdicts.security == res.verdicts.functional == res.verdicts.performance == Verdict.UNKNOWN
    assert any(not c.attempted for c in evd.challenges)
    assert ("connect", 0) in world.calls and ("connect", 1001) in world.calls
    raw = (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")
    assert "/tmp/EXAMPLE-ipc.sock" not in raw and "PRIVATE_" not in raw


@pytest.mark.parametrize("functional_error", [False, True])
def test_verified_unapproved_connection_fail_survives_later_control_failure(tmp_path, monkeypatch, functional_error):
    _, _, _, evd, res, _ = run(tmp_path, monkeypatch, World(connected=True, functional_error=functional_error))
    assert evd.observations["unix_ipc_violations"].value
    assert res.verdicts.security == Verdict.FAIL and res.verdicts.performance == Verdict.UNKNOWN
    assert not functional_error or evd.normal_control.succeeded is False


@pytest.mark.parametrize("change", [
    lambda v: v.update(uid=True) if v["mode"] == "connect" else None,
    lambda v: v["namespaces"].update(mnt="mnt:[999]") if v["mode"] == "connect" else None,
    lambda v: v["rows"][0].update(path_stable=False) if v["mode"] == "connect" and v["uid"] == 1001 else None,
    lambda v: v["rows"][0]["peer"].update(pid=99) if v["mode"] == "connect" and v["uid"] == 1001 else None,
    lambda v: v.update(groups=[999]) if v["mode"] == "connect" else None,
    lambda v: v["rows"][0].update(complete=False) if v["mode"] == "inspect" else None,
])
def test_mismatch_is_not_a_verified_connection_violation(tmp_path, monkeypatch, change):
    _, _, _, evd, res, _ = run(tmp_path, monkeypatch, World(connected=True, mutate=change))
    assert evd.observations["unix_ipc_violations"].value == []
    assert res.verdicts.security == Verdict.UNKNOWN


def test_source_failure_is_unknown_and_diagnostics_never_echo_raw_errors(tmp_path, monkeypatch):
    store, _, _, evd, res, _ = run(tmp_path, monkeypatch, World(source_error=True))
    assert res.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["unix_ipc_trials"].collected is False
    assert "PRIVATE_" not in (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")


@pytest.mark.parametrize("outcome", [None, True, 200, [], {}, "", "PRIVATE_DAEMON_ERROR"])
def test_untrusted_outcomes_are_rejected_without_retaining_raw_values(tmp_path, monkeypatch, outcome):
    def malformed(value):
        if value["mode"] == "connect":
            value["rows"][0]["outcome"] = outcome
    store, _, _, evd, result, _ = run(tmp_path, monkeypatch, World(connected=True, mutate=malformed))
    assert result.verdicts.security == Verdict.UNKNOWN
    assert evd.observations["unix_ipc_trials"].collected is False
    assert evd.observations["unix_ipc_violations"].value == []
    raw = (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")
    assert "IPC_SOURCE_OR_IDENTITY_UNVERIFIED" in raw
    assert "PRIVATE_" not in raw


def test_earlier_connection_violation_survives_later_untrusted_outcome(tmp_path, monkeypatch):
    def malformed(value):
        if value["mode"] == "connect" and value["uid"] == 0:
            value["rows"][0]["outcome"] = "PRIVATE_DAEMON_ERROR"
    store, _, _, evd, result, _ = run(tmp_path, monkeypatch,
        World(connected=True, mutate=malformed), extra={"probe_uids": [1001, 0]})
    assert result.verdicts.security == Verdict.FAIL
    assert len(evd.observations["unix_ipc_violations"].value) == 1
    assert "PRIVATE_" not in (store.root / evd.artifacts[0].path).read_text(encoding="utf-8")


def test_independent_first_uid_violation_survives_later_actor_metadata_gap(tmp_path, monkeypatch):
    world = World(connected=True)
    def later_gap(value):
        if value["mode"] == "inspect" and sum(call[0] == "inspect" for call in world.calls) > 2:
            value["rows"][0]["complete"] = False
    world.mutate = later_gap
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, world, extra={"probe_uids": [1001, 0]})
    assert evd.observations["unix_ipc_violations"].value and result.verdicts.security == Verdict.FAIL


@pytest.mark.parametrize("values", [
    {}, {**VALUES, "allowed_ipc_uids": []}, {**VALUES, "allowed_ipc_uids": [0]},
    {**VALUES, "allowed_ipc_uids": ["00"]}, {**VALUES, "protected_unix_paths": []},
    {**VALUES, "protected_unix_paths": ["/tmp/../private"]},
    {**VALUES, "protected_unix_paths": ["/tmp/a", "/tmp/a"]},
])
def test_policy_is_explicit_and_never_filled_from_observations(values):
    with pytest.raises(ValueError):
        policy_shape(values)


@pytest.mark.parametrize("uids", [[0], [1001, 1002], [True, 1001], [0, 0, 1001], [-1, 0]])
def test_probe_actor_configuration_is_not_an_implicit_policy_decision(uids):
    executor = UnixIPCExecutor({"probe_uids": uids}, docker=object())
    with pytest.raises(ValueError):
        executor.validate_policy(baseline_pack().checks["PLT-02"], deepcopy(VALUES))


def test_saved_pack_report_export_restore_and_rejudge_preserve_verified_failure(tmp_path, monkeypatch):
    from guard.core.report import build_report
    from guard.store.bundle import export_bundle, restore_bundle
    from guard.store.packs import get_pack
    from guard.store.packs import put_pack
    from tests.builders import PACK_DIR
    store, pack, plan, evd, result, _ = run(tmp_path, monkeypatch, World(connected=True))
    before = evd.to_dict()
    report = build_report(store, pack, plan, as_of=evd.created_at)
    store.put(report)
    assert put_pack(store, PACK_DIR) == pack.ref()
    assert report.official is False and "PLT-02" in json.dumps(report.to_dict())
    export_bundle(store.root, tmp_path / "evidence.zip")
    restore_bundle(tmp_path / "evidence.zip", tmp_path / "restored")
    restored = Store(tmp_path / "restored")
    assert store.verify() == restored.verify() == []
    saved = get_pack(restored, pack.ref())
    replay, = judge_plan(restored, saved, restored.resolve(plan.ref()), as_of=evd.created_at)
    assert replay.ref() == result.ref() and restored.resolve(evd.ref()).to_dict() == before


def test_parser_refuses_duplicate_keys_or_unbounded_output():
    assert _parse(SimpleNamespace(returncode=0, stdout='{"uid":0,"uid":1}'), "inspect", 0, 1) is None
    assert _parse(SimpleNamespace(returncode=0, stdout="x" * 65537), "inspect", 0, 1) is None


def test_docker_uid_option_is_numeric_argv_not_shell():
    from guard.runtime.docker import CmdResult, Docker
    calls = []
    def runner(argv, **kwargs):
        calls.append(argv)
        return CmdResult(0, "", "")
    docker = Docker("example-run", runner=runner)
    docker.exec_as_uid("EXAMPLE-engine", 1001, ["python3", "-V"], timeout=1)
    assert calls == [["docker", "exec", "--user", "1001:1001", "EXAMPLE-engine", "python3", "-V"]]
    with pytest.raises(ValueError):
        docker.exec_as_uid("EXAMPLE-engine", True, [], timeout=1)


def test_only_new_definition_changes_and_legacy_76_hashes_are_preserved():
    import hashlib
    from tests.builders import ROOT, PACK_DIR
    hashes = json.loads((ROOT / "tests/fixtures/pack25-check-hashes.json").read_text())
    changed = {p.stem for p in (PACK_DIR / "checks").glob("*.yaml")
               if hashlib.sha256(p.read_bytes()).hexdigest() != hashes[p.stem]}
    assert changed == {"PLT-01", "PLT-02", "PLT-07", "PLT-08"} and len(hashes) == 77
    previous = json.loads((ROOT / "tests/fixtures/pack26-check-hashes.json").read_text())
    latest_changes = {p.stem for p in (PACK_DIR / "checks").glob("*.yaml")
                      if hashlib.sha256(p.read_bytes()).hexdigest() != previous[p.stem]}
    assert latest_changes == {"PLT-01", "PLT-07", "PLT-08"} and len(previous) == 77
    predecessor = json.loads((ROOT / "tests/fixtures/pack27-check-hashes.json").read_text())
    current_changes = {p.stem for p in (PACK_DIR / "checks").glob("*.yaml")
                       if hashlib.sha256(p.read_bytes()).hexdigest() != predecessor[p.stem]}
    assert current_changes == {"PLT-07", "PLT-08"} and len(predecessor) == 77
    check = baseline_pack().checks["PLT-02"]
    assert check.revision == 3 and check.judge.params["pass_when_all"] == []
    assert check.fail_criteria == "비허용 주체가 보호해야 할 IPC 자료나 명령 접점에 접근한다."


def test_zero_payload_probe_uses_owned_linux_socket_or_explicit_transport_double(tmp_path, monkeypatch):
    import os
    import socket
    import struct
    import tempfile
    from guard.executors import ipc_probe
    if hasattr(socket, "SO_PEERCRED") and Path("/proc/self/ns/mnt").exists():
        # Linux CI exercises the actual kernel on a test-owned socket, never engine IPC.
        with tempfile.TemporaryDirectory(prefix="guard-ipc-") as folder:
            path = str(Path(folder) / "owned.sock")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                result = ipc_probe.connect_paths([path], 0.5)
                listener.settimeout(0.5)
                connection, _ = listener.accept()
                with connection:
                    connection.settimeout(0.5)
                    assert connection.recv(1) == b""  # Actual EOF: the probe sent no payload.
            row = result["rows"][0]
            assert row["outcome"] == "connected" and row["path_stable"] is True
            assert row["peer"]["pid"] == os.getpid()
    else:
        # Windows exercises the same zero-payload protocol with an explicit EXAMPLE transport.
        class Peer:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def settimeout(self, value): assert 0 < value <= 0.5
            def connect(self, path): assert path == "/EXAMPLE/socket"
            def getsockopt(self, *args): return struct.pack("3i", 9, 0, 0)
            def send(self, *args): pytest.fail("payload must never be sent")
        monkeypatch.setattr(ipc_probe, "_stat_socket", lambda _: deepcopy(SOCKET))
        monkeypatch.setattr(ipc_probe, "_namespaces", lambda: deepcopy(NS))
        monkeypatch.setattr(ipc_probe.socket, "socket", lambda *args: Peer())
        # These interfaces are Linux-only; the double never queries Windows accounts.
        monkeypatch.setattr(ipc_probe.os, "geteuid", lambda: 1001, raising=False)
        monkeypatch.setattr(ipc_probe.os, "getgroups", lambda: [], raising=False)
        monkeypatch.setattr(ipc_probe.socket, "SO_PEERCRED", 17, raising=False)
        monkeypatch.setattr(ipc_probe.socket, "AF_UNIX", 1, raising=False)
        result = ipc_probe.connect_paths(["/EXAMPLE/socket"], 0.5)
        assert result["rows"][0]["outcome"] == "connected" and result["uid"] == 1001


def test_manual_pass_cannot_bypass_partial_ipc_ceiling(tmp_path, monkeypatch):
    from guard.judges.declared import DeclaredJudge
    from guard.models import Evidence
    _, pack, _, evd, _, _ = run(tmp_path, monkeypatch)
    raw = evd.to_dict()
    raw["observations"]["declared_verdicts"] = {"collected": True, "value": {
        "security": "PASS", "functional": "UNKNOWN", "performance": "UNKNOWN"}}
    raw["observations"]["declared_observation_notes"] = {"collected": True, "value": {
        item: "EXAMPLE operator claim" for item in pack.checks["PLT-02"].required_observations_prose}}
    for item in raw["challenges"]:
        item["attempted"] = True
    result = DeclaredJudge().judge(pack.checks["PLT-02"], Evidence.model_validate(raw).seal(), {}, VALUES)
    assert result.verdicts["security"] == Verdict.UNKNOWN


def test_dynamic_endpoint_identity_is_never_carried_over_from_declared_inputs(tmp_path):
    from guard.executors.reuse import preview_inputs
    pack = baseline_pack()
    assert preview_inputs(pack.checks["PLT-02"], environment(), VALUES,
        {"executors": {"unix_ipc": "real"}, "adapter": {"name": "vllm"}, "unix_ipc": {"probe_uids": [0, 1001]}}) is None


def test_static_support_requires_explicit_probe_configuration_without_querying_docker(monkeypatch):
    from guard.readiness_support import assess_check_support
    pack = baseline_pack()
    env = environment(engine={"name": "vllm", "version": "0.32.0", "image_digest": PIN})
    pol = policy(pack.ref(), values={"PLT-02": VALUES}, scope={"include": ["PLT-02"], "exclude": []})
    cfg = {"adapter": {"name": "vllm"}, "executors": {"unix_ipc": "real"}}
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["PLT-02"])
    assert row["status"] == "UNKNOWN" and row["policy_valid"] is True
    cfg["unix_ipc"] = {"probe_uids": [0, 1001]}
    row, = assess_check_support(pack, env, pol, cfg, selected_check_ids=["PLT-02"])
    assert row["status"] == "SUPPORTED" and "no full IPC PASS" in row["scope"]


def test_budget_expiry_after_verified_violation_does_not_invent_normal_control_attempt(tmp_path, monkeypatch):
    import guard.executors.unix_ipc as module
    clock = {"now": 0, "identities": 0}
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    class ExpiringWorld(World):
        def inspect_json(self, *args, **kwargs):
            value = super().inspect_json(*args, **kwargs)
            clock["identities"] += 1
            if clock["identities"] == 3:
                clock["now"] = 61
            return value
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, ExpiringWorld(connected=True),
                                   extra={"probe_uids": [1001, 0], "max_duration_seconds": 60})
    assert result.verdicts.security == Verdict.FAIL
    assert evd.normal_control.attempted is False and evd.normal_control.succeeded is False


@pytest.mark.parametrize("invalid", ["label", "image", "container-name", "running", "started-at", "pid-mode", "proc-mount"])
def test_unverified_target_is_aborted_before_health_request_or_uid_exec(tmp_path, monkeypatch, invalid):
    class UnownedWorld(World):
        def inspect_json(self, *args, **kwargs):
            value = super().inspect_json(*args, **kwargs)
            if invalid == "label": value["Config"]["Labels"] = {}
            elif invalid == "image": value["Config"]["Image"] = "example.invalid/other@sha256:" + "d" * 64
            elif invalid == "container-name": value["Name"] = "/different"
            elif invalid == "running": value["State"]["Running"] = 1  # bool identity matters.
            elif invalid == "started-at": value["State"]["StartedAt"] = ""
            elif invalid == "pid-mode": value["HostConfig"]["PidMode"] = "host"
            elif invalid == "proc-mount": value["Mounts"] = [{"Destination": "/proc/host"}]
            return value
        def exec_as_uid(self, *args, **kwargs):
            pytest.fail("unverified target must not receive helper commands")
        def probe(self, *args, **kwargs):
            pytest.fail("ownership must be established before even /health")
    _, _, _, evd, result, world = run(tmp_path, monkeypatch, UnownedWorld())
    assert evd.execution.status == ExecStatus.ABORTED and result.verdicts.security == Verdict.UNKNOWN
    assert world.calls and all(c[0] == "identity" for c in world.calls)


def test_same_pid_with_changed_start_is_not_stable_endpoint_proof(tmp_path, monkeypatch):
    world = World(connected=True)
    def reused(value):
        if value["mode"] == "inspect":
            count = sum(c[0] == "inspect" for c in world.calls)
            value["rows"][0]["owners"][0]["start"] = str(12 + count % 2)
    world.mutate = reused
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, world)
    assert evd.observations["unix_ipc_violations"].value == [] and result.verdicts.security == Verdict.UNKNOWN


def test_verified_violation_survives_later_container_restart(tmp_path, monkeypatch):
    class RestartWorld(World):
        def inspect_json(self, *args, **kwargs):
            value = super().inspect_json(*args, **kwargs)
            if sum(c[0] == "identity" for c in self.calls) >= 4:
                value["State"]["StartedAt"] = "2026-10-26T00:00:01Z"
            return value
    _, _, _, evd, result, _ = run(tmp_path, monkeypatch, RestartWorld(connected=True),
                                 extra={"probe_uids": [1001, 0]})
    assert result.verdicts.security == Verdict.FAIL
    assert len(evd.observations["unix_ipc_violations"].value) == 1
    assert evd.observations["unix_ipc_violations"].value[0].endswith(":uid=1001")
    assert evd.normal_control.succeeded is False


@pytest.mark.parametrize("changed", [False, True])
def test_proc_metadata_parser_uses_only_explicit_owned_fixture_tree(tmp_path, monkeypatch, changed):
    from guard.executors import ipc_probe
    proc = tmp_path / "EXAMPLE-proc"
    (proc / "net").mkdir(parents=True)
    (proc / "net/unix").write_text("Header\n0000: 2 0 10000 0001 01 55 /EXAMPLE/socket\n")
    (proc / "9/fd").mkdir(parents=True)
    descriptor = proc / "9/fd/3"
    descriptor.write_bytes(b"")
    (proc / "9/stat").write_text("9 (EXAMPLE worker) S " + "0 " * 18 + "12\n")
    (proc / "9/status").write_text("Uid:\t0\t0\t0\t0\n")
    original_path, is_link, read_link = Path, Path.is_symlink, ipc_probe.os.readlink
    def fixture_path(value):
        return proc / str(value)[6:] if str(value).startswith("/proc/") else proc if value == "/proc" else original_path(value)
    monkeypatch.setattr(ipc_probe, "Path", fixture_path)
    monkeypatch.setattr(Path, "is_symlink", lambda self: True if self == descriptor else is_link(self))
    monkeypatch.setattr(ipc_probe.os, "readlink", lambda path: "socket:[55]" if Path(path) == descriptor else read_link(path))
    monkeypatch.setattr(ipc_probe, "_namespaces", lambda: deepcopy(NS))
    monkeypatch.setattr(ipc_probe.os, "geteuid", lambda: 0, raising=False)
    reads = []
    def stat_socket(_):
        reads.append(True)
        return {**SOCKET, "inode": 99 if changed and len(reads) > 1 else SOCKET["inode"]}
    monkeypatch.setattr(ipc_probe, "_stat_socket", stat_socket)
    value = ipc_probe.inspect_paths(["/EXAMPLE/socket"])
    row, = value["rows"]
    if changed:
        assert row["complete"] is False and row["error"] == "IPC_METADATA_UNAVAILABLE"
    else:
        assert row["complete"] is True and row["owners"] == [OWNER] and row["socket_inode"] == "55"
    assert "/EXAMPLE/socket" not in json.dumps(value)
