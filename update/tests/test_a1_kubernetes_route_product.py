"""REQ02 실제 registry/CLI/Store 왕복. kubectl만 EXAMPLE이며 실제 엔진은 기동하지 않는다."""
import ast
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from guard.adapters.vllm import VllmAdapter
from guard.cli import app
from guard.executors import kubernetes_route_record as route
from guard.executors.kubernetes_fixed_record import FixedRecordReader
from guard.executors.kubernetes_forwarded_identity import ForwardedIdentityReader
from guard.executors.kubernetes_gateway_observation import GatewayRecordReader
from guard.executors.kubernetes_lifecycle_capture import supervised_source, KubernetesCaptureError
from guard.executors.kubernetes_native_route import KubernetesNativeRouteExecutor
from guard.executors.kubernetes_observation import RequiredEventReader
from guard.executors.kubernetes_record_access_product import InventoryReader
from guard.executors.owned_capture import MAX_BYTES, _FILE_READER
from guard.judges.rule_table import RuleTableJudge
from guard.models import canonical_json, load_pack, utcnow
from guard.models.checkdef import PolicyParam
from guard.store import Store
from tests.builders import assert_unconfirmed_security_failure, baseline_pack, evidence, policy, T0
from tests.test_kubernetes_gateway_observation import ordinary_gateway_record
from tests.test_kubernetes_request_binding import env, target
from tests.test_kubernetes_sampling import World as PodWorld, PRIVATE
from tests.test_native_observations import native_record, CaptureWorld
from tools.build_kubernetes_route_pack import derive
from tools.dev1_runtime_records import final_records, route_packet

SESSION = "ab" * 16


class World(PodWorld):
    def __init__(self, environment, mode):
        super().__init__(target(environment))
        self.target["lab"] = False
        self.mode = mode
        self.record = native_record(environment, run_id="guard-fixture",
                                   image=environment.engine.image_digest, kind="route")
        if mode == "reported-bypass":
            self.record["events"][0].update(auth_decision="deny", handler_outcome="entered")
        if mode == "malformed":
            self.record["events"][0]["secret"] = PRIVATE
        self.raw = json.dumps(self.record).encode()
        self.file = CaptureWorld(self.record, {"partial": "partial", "wrong-file-hash": "hash"}.get(mode))
        self.reads = 0

    def runner(self, argv, **kwargs):
        if "exec" in argv:
            self.reads += 1
            assert "--container=engine" in argv and "-i" not in argv and "-t" not in argv
            command = argv[argv.index("--") + 1:]
            assert command[:5] == ["python3", "-I", "-S", "-B", "-c"]
            tree = ast.parse(command[5])
            call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Name) and n.func.id == "compile")
            assert ast.literal_eval(call.args[0]) == _FILE_READER
            assert command[5] == supervised_source(_FILE_READER)
            assert command[7:] == ["/guard-route/data/" + SESSION + "/route-observations.json", "65536"]
            assert 0 < float(command[6]) <= 5 and 0 < kwargs["timeout"] <= 5
            self.after_exec = True
            if self.mode == "missing":
                raise OSError(PRIVATE)
            result = self.file.exec_bounded()
            pipe = dict(result.capture)
            if self.mode == "cleanup":
                pipe.update(pipes_closed=False, read_errors=["child_reap"])
            return {"returncode": 0, "stdout_bytes": result.stdout.encode(), "stderr_bytes": PRIVATE.encode(),
                    "capture": pipe}
        if "namespace" in argv:
            role = self.target["kubernetes"]["engine"]
            value = {"kind": "Namespace", "metadata": {"name": role["namespace"], "uid": role["namespace_uid"]}}
            if self.mode == "namespace-replaced" and self.after_exec:
                value["metadata"]["uid"] = "00000000-0000-0000-0000-000000000099"
            return {"returncode": 0, "stdout_bytes": canonical_json(value), "stderr_bytes": PRIVATE.encode(),
                    "capture": {"complete": True, "bounded_at_source": True, "timed_out": False, "truncated": False}}
        result = super().runner(argv, **kwargs)
        value = json.loads(result["stdout_bytes"])
        value["kind"] = "Pod"
        if self.after_exec:
            if self.mode == "pod-replaced":
                value["metadata"]["uid"] = "10000000-0000-0000-0000-000000000099"
            elif self.mode == "restart":
                value["status"]["containerStatuses"][0]["restartCount"] += 1
            elif self.mode == "container-replaced":
                value["status"]["containerStatuses"][0]["containerID"] = "containerd://" + "f" * 64
        result["stdout_bytes"] = canonical_json(value)
        result["capture"].update(bounded_at_source=True, timed_out=False, truncated=False)
        return result


def inputs(tmp_path, mode="normal"):
    environment = env()
    world = World(environment, mode)
    now = utcnow()
    values = {"route_environment_sha256": environment.ref().sha256,
        "route_target_sha256": hashlib.sha256(canonical_json(world.target)).hexdigest(),
        "route_session": SESSION, "route_file_sha256": hashlib.sha256(world.raw).hexdigest(),
        "route_validity_start": (now-timedelta(minutes=1)).isoformat(),
        "route_validity_end": (now+timedelta(minutes=1)).isoformat()}
    if mode == "wrong-pin": values["route_file_sha256"] = "0" * 64
    if mode == "wrong-target": values["route_target_sha256"] = "0" * 64
    if mode == "expired": values["route_validity_end"] = (now-timedelta(seconds=1)).isoformat()
    receipt = derive(baseline_pack().path, tmp_path / "pack")
    pack = load_pack(tmp_path / "pack")
    return environment, world, values, pack, receipt


def stored_gateway_route(world, environment, raw):
    """Use the existing bridge on ordinary Gateway writer bytes for the owned-file fixture."""
    binding = world.target["kubernetes"]
    rows = final_records(raw.decode("utf-8").split("\n"), run_id=binding["run_id"])
    world.record = route_packet(rows, run_id=binding["run_id"],
                                environment_ref=environment.ref().model_dump(mode="json"),
                                image=binding["engine"]["image"], ingress_id="fixture-gateway",
                                upstream_id=binding["engine"]["pod"])
    world.file = CaptureWorld(world.record)
    world.raw = json.dumps(world.record).encode()


@pytest.mark.parametrize("ordinary_gateway_record", [True], indirect=True)
@pytest.mark.parametrize("mode,reads", [("normal", 1), ("wrong-target", 0), ("expired", 0), ("restart", 1)])
def test_pack39_retains_owned_gateway_record_when_native_binding_is_absent(
        tmp_path, monkeypatch, ordinary_gateway_record, mode, reads):
    check = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE").checks["REQ-02"]
    environment = env()
    world = World(environment, mode)
    gateway_raw, writer_row, _ = ordinary_gateway_record
    stored_gateway_route(world, environment, gateway_raw)
    selected = tmp_path / "route-target.json"
    selected.write_bytes(canonical_json(world.target))
    now = utcnow()
    values = {
        "route_environment_sha256": environment.ref().sha256,
        "route_target_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
        "route_session": SESSION,
        "route_validity_start": (now - timedelta(minutes=1)).isoformat(),
        "route_validity_end": (now + timedelta(minutes=1)).isoformat(),
    }
    if mode == "wrong-target":
        values["route_target_sha256"] = "0" * 64
    if mode == "expired":
        values["route_validity_end"] = (now - timedelta(seconds=1)).isoformat()

    def forbidden(*args, **kwargs):
        pytest.fail("Native construction and external transport are unavailable to this fixture")

    monkeypatch.setattr(KubernetesNativeRouteExecutor, "__init__", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    partial = route.KubernetesRouteRecordExecutor({
        "target": str(selected),
        "native_route": {"target": str(selected), "prepared": str(tmp_path / "missing.json")},
    }, runner=world.runner)

    draft = partial.run(check, environment, values, VllmAdapter(), tmp_path)
    assert world.reads == reads
    assert draft.status.value == "ABORTED" and draft.data_origin.value == "EXAMPLE"
    assert not draft.normal_control.attempted and draft.challenges == []
    route_observed = mode == "normal"
    assert draft.observations["route_record_collected"].value is route_observed
    assert draft.observations["route_producer_authenticated"].value is False
    assert "native_route_events" not in draft.observations
    routes = draft.observations["route_events"]
    assert routes.collected is route_observed
    assert draft.observations["composite_subcollections"].value == {
        "completed": ["route_record"] if route_observed else [],
        "incomplete": [] if route_observed else ["route_record"],
        "failed": {"native_route": "SUBCOLLECTION_UNAVAILABLE"},
    }
    log, = draft.artifacts
    log_bytes = Path(log.path).read_bytes()
    retained = json.loads(log_bytes.splitlines()[-1])["record"]
    if route_observed:
        event, = routes.value
        assert event["request_sha256"] == hashlib.sha256(writer_row["request_id"].encode()).hexdigest()
        assert event["external_path_sha256"] == hashlib.sha256(b"/v1/completions").hexdigest()
        assert event["auth_decision"] == "allow"
        assert retained["file_bytes_sha256"] == hashlib.sha256(world.raw).hexdigest()
        assert retained["file_bytes"] == len(world.raw) and retained["owner_binding_verified"] is True
        assert retained["record_sha256"] == hashlib.sha256(canonical_json(world.record)).hexdigest()
    else:
        assert routes.value is None and retained is None
    assert all(value not in log_bytes for value in (
        writer_row["request_id"].encode(), b"/v1/completions", b"EXAMPLE-PRIVATE-REQUEST-DATA",
        b"fixture-principal", PRIVATE.encode()))
    evd = evidence(status=draft.status, data_origin=draft.data_origin, check_id=check.id,
                   normal=draft.normal_control.model_dump(), observations=draft.observations, attempted=False)
    assert RuleTableJudge().judge(check, evd, check.judge.params, {}).verdicts["security"].value == "UNKNOWN"


def test_pack39_record_only_rejects_partial_native_values_and_bad_role():
    check = load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE").checks["REQ-02"]
    now = utcnow()
    values = {
        "route_environment_sha256": "a" * 64,
        "route_target_sha256": "b" * 64,
        "route_session": SESSION,
        "route_validity_start": now.isoformat(),
        "route_validity_end": (now + timedelta(minutes=1)).isoformat(),
    }
    partial = route.KubernetesRouteRecordExecutor({"native_route": {"target": "target.json"}})
    with pytest.raises(ValueError, match="^KUBERNETES_ROUTE_POLICY_INVALID$"):
        partial.validate_policy(check, {**values, "route_native_environment_sha256": "a" * 64})
    for key in ("route_environment_sha256", "route_session", "route_file_sha256"):
        legacy = check.model_copy(update={"policy_params": [PolicyParam(key=key, type="str")]})
        with pytest.raises(ValueError, match="^KUBERNETES_ROUTE_POLICY_INVALID$"):
            partial.validate_policy(legacy, values)
    with pytest.raises(ValueError, match="^KUBERNETES_ROUTE_CONFIGURATION_INVALID$"):
        partial.run(check, None, values, None, None)


@pytest.mark.parametrize("mode", ["normal", "reported-bypass", "missing", "malformed", "wrong-file-hash",
    "wrong-pin", "wrong-target", "expired", "partial", "namespace-replaced", "pod-replaced",
    "container-replaced", "restart", "attachment", "cleanup", "late-window"])
def test_product_cli_unknown_exact_ref_and_failure_boundaries(tmp_path, monkeypatch, mode):
    environment, world, values, pack, _ = inputs(tmp_path, mode)
    if mode == "late-window":
        now = utcnow(); times = iter([now, now + timedelta(minutes=2)])
        monkeypatch.setattr(route, "utcnow", lambda: next(times))
    monkeypatch.setattr("guard.runtime.kubernetes_http.capture_pipes", world.runner)
    # Reader's source transport receives the same real kubectl seam, never input JSON ingest.
    monkeypatch.setattr(route, "capture_pipes", world.runner)
    def forbidden(*args, **kwargs): raise AssertionError("unexpected external runtime")
    monkeypatch.setattr(subprocess, "Popen", forbidden); monkeypatch.setattr(socket, "socket", forbidden)
    if mode == "attachment":
        original = Path.open
        def open_file(path, *args, **kwargs):
            if path.name == "probe_log.jsonl" and args and args[0] == "w": raise OSError(PRIVATE)
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", open_file)
    pol = policy(pack.ref(), scope={"include": ["REQ-02"]}, values={"REQ-02": values})
    for name, value in (("environment", environment.to_dict()), ("policy", pol.to_dict()), ("target", world.target)):
        (tmp_path / (name + ".json")).write_bytes(canonical_json(value))
    cfg = tmp_path / "guard.toml"
    cfg.write_text('[executors]\nroute_record="kubernetes"\n[adapter]\nname="vllm"\n[route_record]\ntarget='
                   +json.dumps(str(tmp_path / "target.json"))+'\n', encoding="utf-8")
    runner = CliRunner(); common = ["--store", str(tmp_path / "store"), "--config", str(cfg)]
    def cli(args, expected=0):
        result = runner.invoke(app, list(map(str, args)))
        assert result.exit_code == expected, (args, result.output, result.exception)
        assert PRIVATE not in result.output
    cli(["select", "--pack", pack.path, "--env", tmp_path / "environment.json", "--policy", tmp_path / "policy.json", *common])
    plan, = Store.read_only(tmp_path / "store").iter_kind("PLAN")
    assert next(x for x in plan.items if x.check_id == "REQ-02").selected
    cli(["run", "--plan", plan.id, "--pack", pack.path, *common], 1 if mode == "cleanup" else 0)
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), *common])
    cli(["report", "--plan", plan.id, "--as-of", T0.isoformat(), "--json", tmp_path / "report.json", *common])
    cli(["verify", *common])
    store = Store.read_only(tmp_path / "store")
    e, = store.iter_kind("EVD"); res, = store.iter_kind("RES")
    collected = mode in {"normal", "reported-bypass", "attachment", "cleanup"}
    assert e.data_origin.value == "EXAMPLE"
    assert e.observations["route_record_collected"].value is collected
    assert not e.observations["route_producer_authenticated"].value
    assert not e.observations["route_authorization_scope_complete"].value
    assert_unconfirmed_security_failure(res)
    assert res.verdicts.functional == res.verdicts.performance == "UNKNOWN"
    assert not e.normal_control.attempted and e.normal_control.succeeded is None
    assert bool(e.execution.cleanup_errors) is (mode == "cleanup")
    assert e.execution.status.value == ("COMPLETED" if mode in {"normal", "reported-bypass", "cleanup"} else "ABORTED")
    assert world.reads == (0 if mode in {"wrong-target", "expired"} else 1)
    if collected:
        events = e.observations["route_events"].value
        assert events[0]["external_path_sha256"] == hashlib.sha256(b"/tenant/v1/completions").hexdigest()
        assert "external_path" not in events[0] and events[0]["auth_decision"] == ("deny" if mode == "reported-bypass" else "allow")
    else: assert e.observations["route_events"].value is None
    for a in e.artifacts:
        raw = (tmp_path / "store" / a.path).read_bytes()
        assert PRIVATE.encode() not in raw and b"/tenant/v1/completions" not in raw
        row = json.loads(raw.splitlines()[-1])
        if collected:
            assert row["record"]["file_bytes_sha256"] == hashlib.sha256(world.raw).hexdigest()
            assert row["record"]["record_sha256"] == hashlib.sha256(canonical_json(world.record)).hexdigest()
            assert row["record"]["file_bytes_sha256"] != row["record"]["record_sha256"]
    objects = [x for kind in ("ENV", "POL", "PLAN", "EVD", "RES", "RPT") for x in store.iter_kind(kind)]
    assert len(objects) == 6
    cli(["evidence", "export", "--store", tmp_path / "store", "--output", tmp_path / "bundle.zip"])
    cli(["evidence", "restore", "--bundle", tmp_path / "bundle.zip", "--destination", tmp_path / "restored"])
    restored = Store.read_only(tmp_path / "restored")
    assert all(restored.resolve(x.ref()).to_dict() == x.to_dict() for x in objects)
    cli(["judge", "--plan", plan.id, "--as-of", T0.isoformat(), "--store", tmp_path / "restored", "--config", cfg])
    assert [x.ref() for x in Store.read_only(tmp_path / "restored").iter_kind("RES")] == [res.ref()]
    cli(["verify", "--store", tmp_path / "restored", "--config", cfg])


def test_pack_preserves_seed_questions_criteria_and_all_other_bytes(tmp_path):
    seed = baseline_pack()
    receipt = derive(seed.path, tmp_path / "pack")
    pack = load_pack(tmp_path / "pack")
    assert receipt["unchanged_definition_bytes"] == 76 and not receipt["full_REQ02_complete"]
    for path in seed.path.rglob("*"):
        if path.is_file() and path.name != "pack.yaml" and path.relative_to(seed.path).as_posix() != "checks/REQ-02.yaml":
            assert path.read_bytes() == (pack.path / path.relative_to(seed.path)).read_bytes()
    for field in ("question", "satisfied_if", "fail_criteria", "unknown_criteria", "challenge_steps", "legacy"):
        assert getattr(seed.checks["REQ-02"], field) == getattr(pack.checks["REQ-02"], field)
    assert pack.checks["REQ-02"].judge.params == {"pass_when_all": [], "fail_when_any": []}
    assert not pack.reviewed and len(pack.checks) == 77
    with pytest.raises(ValueError): derive(seed.path, tmp_path / "pack")


@pytest.mark.parametrize("damage", ["extra", "session", "hash", "naive", "reversed"])
def test_policy_and_fixed_program_are_closed_before_io(tmp_path, damage):
    environment, world, values, _, _ = inputs(tmp_path)
    if damage == "extra": values["verified"] = True
    elif damage == "session": values["route_session"] = "../secret"
    elif damage == "hash": values["route_file_sha256"] = "runtime-hash"
    elif damage == "naive": values["route_validity_start"] = "2026-10-29T00:00:00"
    else: values["route_validity_start"] = values["route_validity_end"]
    with pytest.raises(ValueError, match="KUBERNETES_ROUTE_POLICY_INVALID"): route.normalize_policy(values)
    reader = route.KubernetesRouteReader(world.target, environment, SESSION, runner=world.runner)
    for path in ("/etc/passwd", "/guard-lifecycle/data/" + SESSION + "/pid-1-1.jsonl",
                 "/guard-route/data/" + "cd"*16 + "/route-observations.json"):
        with pytest.raises(KubernetesCaptureError):
            reader.exec_bounded("engine", ["python3", "-c", _FILE_READER, path, "65536"], timeout=5, max_bytes=131072)
    assert not world.calls and not world.reads


def test_fixed_record_readers_keep_profile_paths_separate():
    environment = env()
    request_target = {**target(environment), "lab": False}

    def unexpected_io(*args, **kwargs):
        pytest.fail("The fixed-program check must complete before any transport call")

    reader_options = {"binary": "kubectl", "duration": 30, "runner": unexpected_io}
    route_reader = route.KubernetesRouteReader(request_target, environment, SESSION, **reader_options)
    binding = route_reader.binding
    transport = SimpleNamespace(binding={**binding, "gateway": binding["engine"]}, base=route_reader)
    lab_target = {**request_target, "lab": True}
    inventory_target = {
        "schema": "guard-kubernetes-lifecycle-target/1",
        "run_id": binding["run_id"],
        "environment_ref": lab_target["environment_ref"],
        "lab": True,
        "kubernetes": {"context": binding["context"], "engine": binding["engine"]},
    }
    readers = [
        (RequiredEventReader(transport, lab_target, SESSION, **reader_options),
         f"/guard-observation/data/{SESSION}/required-events.json"),
        (GatewayRecordReader(transport, lab_target, SESSION, **reader_options),
         "/srv/serving/runtime/data/gateway_records.jsonl"),
        (ForwardedIdentityReader(transport, lab_target, SESSION, "engine", **reader_options),
         f"/guard-identity/data/{SESSION}/forwarded-identity.json"),
        (InventoryReader(inventory_target, environment, SESSION, **reader_options),
         f"/guard-record-access/data/{SESSION}/copy-inventory.json"),
        (route_reader, f"/guard-route/data/{SESSION}/route-observations.json"),
    ]
    required_reader = readers[0][0]
    inventory_reader = readers[3][0]
    assert type(required_reader) is RequiredEventReader
    assert all(type(reader) is FixedRecordReader for reader, _ in readers[1:])
    for reader, own_path in readers:
        own_command = ["python3", "-c", _FILE_READER, own_path, str(MAX_BYTES)]
        assert reader._program("engine", own_command, 2 * MAX_BYTES) == "file"

        error = ValueError if reader is inventory_reader else KubernetesCaptureError
        error_code = ("RECORD_ACCESS_INVENTORY_PROGRAM_INVALID" if reader is inventory_reader
                      else "KUBERNETES_CAPTURE_BINDING_UNAVAILABLE")
        for _, other_path in readers:
            if other_path == own_path:
                continue
            other_command = ["python3", "-c", _FILE_READER, other_path, str(MAX_BYTES)]
            with pytest.raises(error, match=error_code):
                reader.exec_bounded("engine", other_command, timeout=5, max_bytes=2 * MAX_BYTES)
