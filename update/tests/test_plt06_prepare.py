"""PLT06 preparation contracts; CPU processes are not admitted GPU workers."""
from copy import deepcopy
import csv
import json
import os
from pathlib import Path
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest

from guard.collectors.file import load_policy
from guard.executors import ipc_trust_scope as scope, registry
from guard.models import canonical_json, load_pack
from guard.runtime.bounded import capture_pipes
from guard.runtime.docker import CmdResult
from tests.builders import environment
from tools import plt06_prepare as prep

ROOT = Path(__file__).resolve().parents[1]
VALUES = {"worker_isolation_requirement": "shared-trust-group", "worker_trust_group_markers": ["plt06-cpu-a", "plt06-cpu-b"]}


def inputs():
    return {"run_id": "plt06-cpu-test", "uid": 1000, "gid": 1000,
            "gpu_devices": ["GPU-00000000-0000-0000-0000-000000000001"], "tp": 1,
            "model_root": "/owned/EXAMPLE-model", "served_model_name": "EXAMPLE-served-model", "host_port": 18060}


def prepared(tmp_path, values=None):
    source = {"source_commit": "0" * 40, "source_manifest_sha256": "0" * 64, "tool_sha256": prep.sha(prep.read_bytes(prep.__file__))}
    output = tmp_path / "assets"
    prep.prepare(output, deepcopy(values or VALUES), source, load_pack(ROOT / "packs/PACK-BASELINE"))
    return output


def rendered(tmp_path):
    assets = prepared(tmp_path)
    output = tmp_path / "rendered"
    receipt = prep.render(assets, inputs(), output)
    return assets, output, receipt


def runtime_files(tmp_path, launch):
    env = environment(engine={"name": "vllm", "version": "0.31.0", "image_digest": launch["image"]},
                      model={"id": "Qwen/Qwen2.5-0.5B-Instruct", "revision": "a" * 40, "inventory_sha256": "b" * 64})
    envfile = tmp_path / "env.json"
    envfile.write_bytes(canonical_json(env.model_dump(mode="json")))
    inspect = {"Id": "c" * 64, "Image": "sha256:" + "d" * 64, "Name": "/" + launch["container"],
               "Config": {"Image": launch["image"], "User": "1000:1000", "Labels": {"guard.run": inputs()["run_id"]}},
               "State": {"Running": True, "StartedAt": "2026-10-19T00:00:00Z"}, "HostConfig": {"PidMode": ""},
               "Mounts": [{"Type": "bind", "Source": inputs()["model_root"], "Destination": "/models", "RW": False}],
               "NetworkSettings": {"Networks": {launch["network"]: {"NetworkID": "f" * 64}}}}
    file = tmp_path / "inspect.json"
    file.write_text(json.dumps([inspect]))
    networkfile = tmp_path / "network.json"
    networkfile.write_text(json.dumps([{"Id": "f" * 64, "Name": launch["network"], "Internal": True, "Labels": {"guard.run": inputs()["run_id"]},
        "Containers": {inspect["Id"]: {"Name": launch["container"]}}}]))
    return envfile, file, networkfile, env, inspect


def test_actual_draft_policy_and_asset_consumer_contract(tmp_path):
    output = prepared(tmp_path)
    receipt = prep.load_prepared(output)
    pack = load_pack(ROOT / "packs/PACK-BASELINE")
    policy = load_policy(output / "policy.draft.json", pack)
    assert policy.status.value == "DRAFT" and policy.values["PLT-06"] == VALUES
    assert policy.data_origin.value == "EXAMPLE"
    assert policy.scope.include == ["PLT-06"]
    assert prep.read_bytes(output / "process-map.py") == scope._MAPPER.encode()
    assert set(prep.document(output / "runtime-inputs.json").values()) == {None}
    assert receipt["measurement_packet_ready"] is False
    assert receipt["worker_command_impact_collection_supported"] is False
    assert output.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValueError):prepared(tmp_path)


def test_rendered_argv_is_closed_nonroot_readonly_and_never_invokes_docker(tmp_path, monkeypatch):
    assets = prepared(tmp_path)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("render must not run Docker or any child"))
    output = tmp_path / "rendered"
    receipt = prep.render(assets, inputs(), output)
    argv = prep.document(output / "launch.argv.json")
    assert argv[:4] == ["docker", "run", "-d", "--pull"] and argv[4] == "never"
    assert argv[argv.index("--publish") + 1] == "127.0.0.1:18060:8000"
    assert argv[argv.index("--user") + 1] == "1000:1000"
    assert argv[argv.index("--mount") + 1].endswith(",dst=/models,readonly")
    assert "--privileged" not in argv and "--pid" not in argv
    assert prep.document(output / "network.argv.json") == ["docker", "network", "create", "--internal", "--label", "guard.run=" + inputs()["run_id"], receipt["network"]]
    assert argv[argv.index("--distributed-executor-backend") + 1] == "mp"
    assert receipt["declared_configuration_only"] and not receipt["engine_or_GPU_started"]


@pytest.mark.parametrize("change", [{"tp": 2}, {"gpu_devices": ["GPU-00000000-0000-0000-0000-000000000001"] * 2},
    {"uid": 0}, {"uid": True}, {"run_id": "UPPERCASE"}, {"run_id": "valid-run\n"}, {"host_port": 80},
    {"model_root": "/model,bad"}, {"model_root": "/a/../b"}, {"served_model_name": "bad\nname"}, {"served_model_name": "--unexpected-option"}])
def test_invalid_runtime_inputs_are_rejected_before_output(tmp_path, change):
    assets = prepared(tmp_path)
    values = inputs();values.update(change)
    with pytest.raises((ValueError, TypeError)):prep.render(assets, values, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_tp2_requires_two_distinct_device_selections_without_claiming_inventory(tmp_path):
    assets = prepared(tmp_path);values = inputs();values["tp"] = 2
    values["gpu_devices"].append("GPU-00000000-0000-0000-0000-000000000002")
    receipt = prep.render(assets, values, tmp_path / "tp2")
    argv = prep.document(tmp_path / "tp2/launch.argv.json")
    assert argv[argv.index("--tensor-parallel-size") + 1] == "2"
    assert next(csv.reader([argv[argv.index("--gpus") + 1]])) == ["device=" + ",".join(values["gpu_devices"])]
    assert receipt["declared_configuration_only"] is True


@pytest.mark.parametrize("asset", ["process-map.py", "environment.recipe.json", "policy.draft.json"])
def test_asset_changes_are_rejected_even_if_caller_rewrites_receipt_hash(tmp_path, asset):
    output = prepared(tmp_path)
    if asset == "process-map.py":changed = b"print('{}')"
    elif asset == "environment.recipe.json":
        recipe = prep.document(output / asset);recipe["image"] = "example.invalid/foreign@sha256:" + "a" * 64;changed = canonical_json(recipe)
    else:
        policy = prep.document(output / asset);policy["status"] = "APPROVED";changed = canonical_json(policy)
    (output / asset).write_bytes(changed)
    receipt = prep.document(output / "preparation.json");receipt["files_sha256"][asset] = prep.sha(changed)
    (output / "preparation.json").write_bytes(canonical_json(receipt))
    with pytest.raises(ValueError):prep.load_prepared(output)


def test_bound_files_reach_existing_factory_identity_and_policy_contract(tmp_path):
    assets, rendered_path, launch = rendered(tmp_path)
    envfile, inspectfile, networkfile, env, raw = runtime_files(tmp_path, launch)
    output = tmp_path / "bound"
    receipt = prep.bind(rendered_path, envfile, inspectfile, networkfile, output)
    config = tomllib.loads((output / "guard.toml").read_text())
    executor = registry.build_executor("unix_ipc", "real", config)
    executor.docker = SimpleNamespace(inspect_json=lambda *a, **k: deepcopy(raw))  # EXAMPLE transport only.
    import time
    target = prep.document(output / "target.json")
    assert executor._identity(SimpleNamespace(target=target, env=env), time.monotonic() + 5) == {key:receipt["identity"][key] for key in ("container_id", "image_id", "started_at", "image")}
    assert receipt["identity"]["network_id"] == "f" * 64
    executor.validate_policy(load_pack(ROOT / "packs/PACK-BASELINE").checks["PLT-06"], VALUES)
    assert receipt["environment_ref"] == env.ref().model_dump(mode="json")
    assert receipt["input_files_validated_not_live_Docker_query"] is True
    assert receipt["measurement_packet_ready"] is False and receipt["worker_role_authenticated"] is False
    assert prep.verify_generation(output, inspectfile)["live_query_performed"] is False
    raw["Id"] = "e" * 64;inspectfile.write_text(json.dumps([raw]))
    with pytest.raises(ValueError, match="CONTAINER_GENERATION_CHANGED"):prep.verify_generation(output, inspectfile)


@pytest.mark.parametrize("change", ["owner", "stopped", "host-pid", "proc-mount", "writable-model"])
def test_actual_input_binding_mismatch_never_writes_target(tmp_path, change):
    _, rendered_path, launch = rendered(tmp_path)
    envfile, file, networkfile, _, raw = runtime_files(tmp_path, launch)
    if change == "owner":raw["Config"]["Labels"]["guard.run"] = "another-owner"
    elif change == "stopped":raw["State"]["Running"] = False
    elif change == "host-pid":raw["HostConfig"]["PidMode"] = "host"
    elif change == "proc-mount":raw["Mounts"].append({"Destination": "/proc/1"})
    else:raw["Mounts"][0]["RW"] = True
    file.write_text(json.dumps([raw]))
    with pytest.raises(ValueError, match="INSPECT_BINDING_INVALID"):prep.bind(rendered_path, envfile, file, networkfile, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_network_owner_and_later_environment_replacement_are_rejected(tmp_path):
    _, rendered_path, launch = rendered(tmp_path)
    envfile, file, networkfile, env, _ = runtime_files(tmp_path, launch)
    original = networkfile.read_bytes()
    network = json.loads(original);network[0]["Labels"]["guard.run"] = "foreign"
    networkfile.write_text(json.dumps(network))
    with pytest.raises(ValueError, match="NETWORK_BINDING_INVALID"):
        prep.bind(rendered_path, envfile, file, networkfile, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()
    networkfile.write_bytes(original)
    prep.bind(rendered_path, envfile, file, networkfile, tmp_path / "bound")
    env.id = "ENV-EXAMPLE-REPLACED";env.seal()
    envfile.write_bytes(canonical_json(env.model_dump(mode="json")))
    with pytest.raises(ValueError, match="ENVIRONMENT_REPLACED"):
        prep.verify_generation(tmp_path / "bound", file)


def test_external_network_cannot_replace_original_internal_environment(tmp_path):
    _, rendered_path, launch = rendered(tmp_path)
    envfile, file, networkfile, _, _ = runtime_files(tmp_path, launch)
    network = json.loads(networkfile.read_bytes());network[0]["Internal"] = False
    networkfile.write_text(json.dumps(network))
    with pytest.raises(ValueError, match="NETWORK_BINDING_INVALID"):
        prep.bind(rendered_path, envfile, file, networkfile, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


@pytest.mark.parametrize("helper", ["update/tools/lif14_stop_restart_prepare.py", "update/tools/verify_req10_consumption.py",
    "update/tools/build_kubernetes_profiler_control_pack.py", "update/tests/builders.py"])
def test_dirty_imported_preparation_helpers_are_rejected_by_actual_git(tmp_path, monkeypatch, helper):
    repo = tmp_path / "fixed-source"
    repo.mkdir()
    for name in prep.PREPARATION_SOURCES:
        target = repo / name;target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT.parent / name).read_bytes())
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=CPU contract", "-c", "user.email=cpu@example.invalid", "commit", "-qm", "fixed helper sources"], check=True)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    target = repo / helper;target.write_bytes(target.read_bytes() + b"\n# uncommitted helper change\n")
    monkeypatch.setattr(prep, "verify", lambda *_: pytest.fail("dirty helper must fail before source-manifest verifier"))
    with pytest.raises(subprocess.CalledProcessError):
        prep.source_context(repo, commit, "0" * 64)


def test_case_variant_of_same_uuid_cannot_be_two_tp_devices():
    values = inputs();values["tp"] = 2
    values["gpu_devices"] = ["GPU-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "GPU-AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA"]
    with pytest.raises(ValueError, match="DISTINCT_GPU_UUIDS"):prep.validate_inputs(values)


def test_symlink_and_duplicate_json_inputs_are_rejected(tmp_path):
    file = tmp_path / "values.json";file.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):prep.document(file)
    link = tmp_path / "link";link.symlink_to(file)
    with pytest.raises(ValueError, match="SYMLINK"):prep.document(link)


def test_actual_exported_mapper_to_real_factory_collector_preserves_scope_limits(tmp_path):
    values = deepcopy(VALUES);values["worker_isolation_requirement"] = "independent-uid"
    assets = prepared(tmp_path, values)
    children = []
    try:
        for name in values["worker_trust_group_markers"]:
            code = "import ctypes,os,sys; assert ctypes.CDLL(None).prctl(15,sys.argv[1].encode(),0,0,0)==0;print(os.getpid(),flush=True);sys.stdin.readline()"
            child = subprocess.Popen([sys.executable, "-I", "-S", "-B", "-c", code, name], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
            children.append(child);assert int(child.stdout.readline()) == child.pid

        class LocalCPUTransport:
            def exec_bounded(self, container, argv, *, timeout, max_bytes):
                assert container == "CPU-process-contract-not-vllm"
                assert argv == ["python3", "-c", (assets / "process-map.py").read_text()]
                result = capture_pipes([sys.executable, "-I", "-S", "-B", str(assets / "process-map.py")], timeout=timeout,
                    env_extra={}, max_stdout_bytes=max_bytes, max_stderr_bytes=max_bytes)
                return CmdResult(result["returncode"], result["stdout_bytes"].decode(), result["stderr_bytes"].decode(),
                                 result["stdout_bytes"], result["stderr_bytes"], result["capture"])

        executor = registry.build_executor("unix_ipc", "real", {"unix_ipc": {}})
        executor.docker = LocalCPUTransport()
        check = load_pack(ROOT / "packs/PACK-BASELINE").checks["PLT-06"]
        executor.validate_policy(check, values)
        obs, normal, _, _ = executor.collect(SimpleNamespace(policy_values=values, target={"engine": {"container": "CPU-process-contract-not-vllm"}}, check=check))
        assert obs["worker_isolation_violations"]["value"] == [{"groups": sorted(values["worker_trust_group_markers"]), "shared_uid": [str(os.geteuid())]}]
        groups = {r["marker"]: r for r in obs["worker_trust_group_map"]["value"]}
        assert all(child.pid in groups[name]["pids"] for child, name in zip(children, values["worker_trust_group_markers"]))
        assert obs["worker_command_impact_verified"]["value"] is False
        assert normal["attempted"] is False  # CPU process names do not authenticate admitted GPU workers.
    finally:
        for child in children:
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
