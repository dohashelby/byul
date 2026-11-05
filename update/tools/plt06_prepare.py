"""Offline PLT06 assets, Docker argv and runtime-file binding; never deploys."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess

from guard.collectors.model_mapping import SCHEMA, validate_model_mapping
from guard.executors import ipc_trust_scope as scope, registry
from guard.executors.owned_capture import strict_json
from guard.models import Environment, canonical_json, load_pack
from guard.runtime.docker import Docker
from tools.lif14_stop_restart_prepare import reserve_preparation_output
from tools.verify_req10_consumption import verify

RECIPE = Path(__file__).parent / "fixtures/plt06_environment.json"
ASSETS = {"environment.recipe.json", "policy.draft.json", "process-map.py", "runtime-inputs.json"}
INPUTS = {"run_id", "uid", "gid", "gpu_devices", "tp", "model_root", "served_model_name", "host_port"}
PREPARATION_SOURCES = (
    "update/tools/plt06_prepare.py", "update/tools/fixtures/plt06_environment.json",
    "update/tools/lif14_stop_restart_prepare.py", "update/tools/verify_req10_consumption.py",
    "update/tools/build_kubernetes_profiler_control_pack.py", "update/tests/builders.py",
)


def read_bytes(path, limit=262144):
    path = Path(path)
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError("PLT06_INPUT_SYMLINK")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("PLT06_INPUT_NOT_REGULAR")
        data = os.read(fd, limit + 1)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if len(data) > limit or len(data) != before.st_size or key(before) != key(os.fstat(fd)) or key(before) != key(path.lstat()):
            raise ValueError("PLT06_INPUT_BOUND_OR_CHANGED")
        return data
    finally:
        os.close(fd)


def document(path):
    return strict_json(read_bytes(path), limit=262144)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    data = value if isinstance(value, bytes) else canonical_json(value)
    with Path(path).open("xb") as stream:
        stream.write(data)
    Path(path).chmod(0o600)
    return sha(data)


def reserve(output, *protected):
    return reserve_preparation_output(output, protected=[Path(__file__).resolve().parents[2], *map(Path, protected)])


def source_context(repo, commit, manifest_sha):
    repo = Path(repo)
    if not re.fullmatch("[0-9a-f]{40}", commit) or not re.fullmatch("[0-9a-f]{64}", manifest_sha):
        raise ValueError("PLT06_FIXED_SOURCE_REQUIRED")
    actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if actual != commit:
        raise ValueError("PLT06_FIXED_SOURCE_MISMATCH")
    paths = ["update/src", "update/release", *PREPARATION_SOURCES]
    subprocess.run(["git", "-C", str(repo), "diff", "--exit-code", "HEAD", "--", *paths], check=True)
    subprocess.run(["git", "-C", str(repo), "ls-files", "--error-unmatch", *PREPARATION_SOURCES], check=True, stdout=subprocess.DEVNULL)
    if sha(read_bytes(repo / "update/release/reviewed-source.json")) != manifest_sha:
        raise ValueError("PLT06_FIXED_MANIFEST_MISMATCH")
    verify(repo)
    return {"source_commit": actual, "source_manifest_sha256": manifest_sha, "tool_sha256": sha(read_bytes(__file__))}


def prepare(output, values, source, pack):
    if type(values) is not dict or set(values) != {"worker_isolation_requirement", "worker_trust_group_markers"}:
        raise ValueError("PLT06_EXPLICIT_POLICY_VALUES_REQUIRED")
    scope.validate_policy(values)
    check = pack.checks["PLT-06"]
    if check.executor != "unix_ipc" or check.collection_profile != scope.PROFILE:
        raise ValueError("PLT06_CHECK_PROFILE_MISMATCH")
    executor = registry.build_executor("unix_ipc", "real", {"unix_ipc": {}})
    executor.validate_policy(check, values)
    out = reserve(output)
    files = {}
    files["environment.recipe.json"] = write(out / "environment.recipe.json", document(RECIPE))
    policy = {"pack": {"id": pack.ref().id, "revision": pack.ref().revision}, "status": "DRAFT", "scope": {"include": ["PLT-06"], "exclude": []},
              "data_origin": "EXAMPLE", "values": {"PLT-06": deepcopy(values)}}
    files["policy.draft.json"] = write(out / "policy.draft.json", policy)
    files["process-map.py"] = write(out / "process-map.py", scope._MAPPER.encode())
    fields = {key: None for key in sorted(INPUTS)}
    files["runtime-inputs.json"] = write(out / "runtime-inputs.json", fields)
    receipt = {"schema": "plt06-preparation/1", **source, "check_id": "PLT-06", "backend": "real",
               "collection_profile": scope.PROFILE, "files_sha256": files, "pack_ref": pack.ref().model_dump(mode="json"),
               "factory_class": type(executor).__module__ + "." + type(executor).__name__,
               "policy_status": "DRAFT", "enterprise_policy_approved": False, "runtime_bound": False,
               "worker_role_authenticated": False, "worker_command_impact_collection_supported": False,
               "measurement_packet_ready": False, "engine_or_GPU_started": False}
    write(out / "preparation.json", receipt)
    return receipt


def load_prepared(path):
    root = Path(path)
    receipt = document(root / "preparation.json")
    if receipt.get("schema") != "plt06-preparation/1" or receipt.get("backend") != "real" or set(receipt["files_sha256"]) != ASSETS:
        raise ValueError("PLT06_PREPARATION_INVALID")
    if receipt.get("tool_sha256") != sha(read_bytes(__file__)) or receipt.get("collection_profile") != scope.PROFILE:
        raise ValueError("PLT06_PREPARATION_TOOL_MISMATCH")
    for name, expected in receipt["files_sha256"].items():
        if sha(read_bytes(root / name)) != expected:
            raise ValueError("PLT06_PREPARATION_BYTES_CHANGED")
    if read_bytes(root / "process-map.py") != scope._MAPPER.encode():
        raise ValueError("PLT06_MAPPER_VERSION_MISMATCH")
    if read_bytes(root / "environment.recipe.json") != canonical_json(document(RECIPE)):
        raise ValueError("PLT06_REVIEWED_RECIPE_MISMATCH")
    policy = document(root / "policy.draft.json")
    if policy.get("status") != "DRAFT" or policy.get("data_origin") != "EXAMPLE" or set(policy.get("values", {})) != {"PLT-06"}:
        raise ValueError("PLT06_DRAFT_POLICY_REQUIRED")
    scope.validate_policy(policy["values"]["PLT-06"])
    return receipt


def validate_inputs(values):
    if type(values) is not dict or set(values) != INPUTS:
        raise ValueError("PLT06_RUNTIME_INPUTS_REQUIRED")
    if type(values["run_id"]) is not str or re.fullmatch("[a-z0-9][a-z0-9-]{3,39}", values["run_id"]) is None:
        raise ValueError("PLT06_RUN_ID_INVALID")
    docker = Docker(values["run_id"])
    for key in ("uid", "gid"):
        if type(values[key]) is not int or not 1 <= values[key] < 2**31:
            raise ValueError("PLT06_NONROOT_UID_GID_REQUIRED")
    devices = values["gpu_devices"]
    if type(values["tp"]) is not int or values["tp"] not in (1, 2) or type(devices) is not list or len(devices) != values["tp"] or any(type(v) is not str or re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", v) is None for v in devices) or len({v.lower() for v in devices}) != len(devices):
        raise ValueError("PLT06_DISTINCT_GPU_UUIDS_FOR_TP_REQUIRED")
    model = values["model_root"]
    if type(model) is not str or not model.startswith("/") or str(PurePosixPath(model)) != model or ".." in PurePosixPath(model).parts or any(c in model for c in "\\,:\n\r\x00"):
        raise ValueError("PLT06_MODEL_PATH_INVALID")
    name = values["served_model_name"]
    if type(name) is not str or not 1 <= len(name) <= 256 or name.startswith("-") or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("PLT06_SERVED_MODEL_INVALID")
    if type(values["host_port"]) is not int or not 1024 <= values["host_port"] <= 65535:
        raise ValueError("PLT06_LOOPBACK_PORT_REQUIRED")
    return docker.name("engine"), docker.name("network")


def render(prepared, values, output):
    original = load_prepared(prepared)
    container, network = validate_inputs(values)
    recipe = document(Path(prepared) / "environment.recipe.json")
    args = [v.format(tp=values["tp"], served_model_name=values["served_model_name"]) for v in recipe["application_argv"]]
    resources = recipe["resources"]
    argv = ["docker", "run", "-d", "--pull", "never", "--name", container, "--network", network,
            "--label", "guard.run=" + values["run_id"], "--gpus", json.dumps("device=" + ",".join(values["gpu_devices"])),
            "--user", f"{values['uid']}:{values['gid']}", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--cpus", resources["cpus"], "--memory", resources["memory"], "--memory-swap", resources["memory"],
            "--pids-limit", resources["pids"], "--shm-size", resources["shm"],
            "--publish", f"127.0.0.1:{values['host_port']}:8000", "--mount", f"type=bind,src={values['model_root']},dst=/models,readonly",
            "-e", "HOME=/tmp", "-e", "HF_HUB_OFFLINE=1", "-e", "TRANSFORMERS_OFFLINE=1",
            "-e", "VLLM_USE_V1=1", "-e", "VLLM_ENABLE_V1_MULTIPROCESSING=1", "--entrypoint", "python3", recipe["image"], *args]
    out = reserve(output, prepared)
    files = {"launch.argv.json": write(out / "launch.argv.json", argv),
             "network.argv.json": write(out / "network.argv.json", ["docker", "network", "create", "--internal", "--label", "guard.run=" + values["run_id"], network]),
             "operator-inputs.json": write(out / "operator-inputs.json", values)}
    receipt = {"schema": "plt06-render/1", "preparation": str(Path(prepared).absolute()), "preparation_sha256": sha(read_bytes(Path(prepared) / "preparation.json")),
               "source_commit": original["source_commit"], "container": container, "network": network,
               "image": recipe["image"], "probe_image": recipe["probe_image"], "application_argv": args,
               "files_sha256": files, "declared_configuration_only": True, "engine_or_GPU_started": False}
    write(out / "render.json", receipt)
    return receipt


def load_render(root):
    root = Path(root)
    receipt = document(root / "render.json")
    if receipt.get("schema") != "plt06-render/1" or set(receipt["files_sha256"]) != {"launch.argv.json", "network.argv.json", "operator-inputs.json"}:
        raise ValueError("PLT06_RENDER_INVALID")
    for name, expected in receipt["files_sha256"].items():
        if sha(read_bytes(root / name)) != expected:
            raise ValueError("PLT06_RENDER_BYTES_CHANGED")
    if sha(read_bytes(Path(receipt["preparation"]) / "preparation.json")) != receipt["preparation_sha256"]:
        raise ValueError("PLT06_PREPARATION_REPLACED")
    original = load_prepared(receipt["preparation"])
    if receipt.get("source_commit") != original["source_commit"]:
        raise ValueError("PLT06_RENDER_SOURCE_CHANGED")
    return receipt, document(root / "operator-inputs.json")


def inspected(value, launch, inputs):
    if type(value) is list and len(value) == 1:
        value = value[0]
    try:
        cfg, state, host = value["Config"], value["State"], value["HostConfig"]
        mounts = value["Mounts"]
        if (value["Name"] != "/" + launch["container"] or re.fullmatch("[0-9a-f]{64}", value["Id"]) is None
                or re.fullmatch("sha256:[0-9a-f]{64}", value["Image"]) is None
                or cfg["Labels"]["guard.run"] != inputs["run_id"] or cfg["Image"] != launch["image"]
                or cfg["User"] != f"{inputs['uid']}:{inputs['gid']}" or state["Running"] is not True
                or type(state["StartedAt"]) is not str or not state["StartedAt"] or host.get("PidMode") not in (None, "")
                or type(mounts) is not list or any(m["Destination"] == "/" or m["Destination"] == "/proc" or m["Destination"].startswith("/proc/") for m in mounts)
                or not any(m.get("Type") == "bind" and m["Destination"] == "/models" and m.get("Source") == inputs["model_root"] and m.get("RW") is False for m in mounts)
                or launch["network"] not in value["NetworkSettings"]["Networks"]):
            raise ValueError
        network_id = value["NetworkSettings"]["Networks"][launch["network"]]["NetworkID"]
        if type(network_id) is not str or re.fullmatch("[0-9a-f]{64}", network_id) is None:
            raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("PLT06_INSPECT_BINDING_INVALID") from None
    return {"container_id": value["Id"], "image_id": value["Image"], "started_at": state["StartedAt"], "image": cfg["Image"], "network_id": network_id}


def bind(rendered, environment, inspection, network_inspection, output):
    launch, inputs = load_render(rendered)
    env = Environment.model_validate(document(environment))
    if not env.hash_ok() or env.engine.image_digest != launch["image"]:
        raise ValueError("PLT06_ENVIRONMENT_INVALID")
    identity = inspected(document(inspection), launch, inputs)
    network = document(network_inspection)
    if type(network) is list and len(network) == 1:
        network = network[0]
    try:
        if (network["Name"] != launch["network"] or network["Id"] != identity["network_id"] or network["Internal"] is not True
                or network["Labels"]["guard.run"] != inputs["run_id"]
                or network["Containers"][identity["container_id"]]["Name"] != launch["container"]):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("PLT06_NETWORK_BINDING_INVALID") from None
    mapping = validate_model_mapping(env, {"schema": SCHEMA, "environment_ref": env.ref().model_dump(mode="json"),
        "repository_id": env.model.id, "revision": env.model.revision, "inventory_sha256": env.model.inventory_sha256,
        "served_model_name": inputs["served_model_name"], "host_snapshot": inputs["model_root"], "container_mount": "/models"})
    target = {"lab": True, "topology": "isolated", "run_id": inputs["run_id"], "probe_image": launch["probe_image"],
              "engine": {"container": launch["container"], "trusted_url": "http://" + launch["container"] + ":8000", "ready_timeout": 60, "image_digest": launch["image"]},
              "origins": {"trusted": {"network": launch["network"]}}, "model_mapping": mapping,
              "comparison_config": {"launch": {"argv": launch["application_argv"], "tp": inputs["tp"], "pp": 1}}}
    out = reserve(output, rendered, environment, inspection, network_inspection)
    target_sha = write(out / "target.json", target)
    config = '[executors]\nunix_ipc="real"\n[adapter]\nname="vllm"\n[unix_ipc]\ntarget=' + json.dumps(str(out / "target.json")) + '\nrequest_timeout=3.0\n'
    config_sha = write(out / "guard.toml", config.encode())
    receipt = {"schema": "plt06-runtime-file-binding/1", "rendered": str(Path(rendered).absolute()), "render_sha256": sha(read_bytes(Path(rendered) / "render.json")),
               "environment": str(Path(environment).absolute()), "environment_ref": env.ref().model_dump(mode="json"),
               "inspection_sha256": sha(read_bytes(inspection)), "network_inspection_sha256": sha(read_bytes(network_inspection)), "identity": identity, "target_sha256": target_sha, "config_sha256": config_sha,
               "source_commit": launch["source_commit"], "input_files_validated_not_live_Docker_query": True,
               "model_bytes_or_physical_GPU_inventory_verified_by_this_tool": False,
               "worker_role_authenticated": False, "worker_command_impact_collection_supported": False,
               "policy_status": "DRAFT", "measurement_packet_ready": False, "factory_executed": False}
    write(out / "binding.json", receipt)
    return receipt


def verify_generation(bound, inspection):
    bound = Path(bound)
    receipt = document(bound / "binding.json")
    if receipt.get("schema") != "plt06-runtime-file-binding/1" or sha(read_bytes(bound / "target.json")) != receipt["target_sha256"] or sha(read_bytes(bound / "guard.toml")) != receipt["config_sha256"]:
        raise ValueError("PLT06_BINDING_BYTES_CHANGED")
    if sha(read_bytes(Path(receipt["rendered"]) / "render.json")) != receipt["render_sha256"]:
        raise ValueError("PLT06_RENDER_REPLACED")
    env = Environment.model_validate(document(receipt["environment"]))
    if not env.hash_ok() or env.ref().model_dump(mode="json") != receipt["environment_ref"]:
        raise ValueError("PLT06_ENVIRONMENT_REPLACED")
    launch, values = load_render(receipt["rendered"])
    if inspected(document(inspection), launch, values) != receipt["identity"]:
        raise ValueError("PLT06_CONTAINER_GENERATION_CHANGED")
    return {"same_container_generation_in_supplied_inspection": True, "live_query_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    for key in ("policy-values", "output", "source-commit", "manifest-sha256"):
        p.add_argument("--" + key, required=True)
    p = sub.add_parser("render")
    for key in ("prepared", "bindings", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p = sub.add_parser("bind")
    for key in ("rendered", "environment", "inspection", "network-inspection", "output"):
        p.add_argument("--" + key, type=Path, required=True)
    p = sub.add_parser("verify-generation")
    for key in ("bound", "inspection"):
        p.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    if args.action == "prepare":
        context = source_context(repo, args.source_commit, args.manifest_sha256)
        result = prepare(args.output, document(args.policy_values), context, load_pack(repo / "update/packs/PACK-BASELINE"))
    elif args.action == "render":
        original = load_prepared(args.prepared)
        source_context(repo, original["source_commit"], original["source_manifest_sha256"])
        result = render(args.prepared, document(args.bindings), args.output)
    elif args.action == "bind":
        launch, _ = load_render(args.rendered)
        original = load_prepared(launch["preparation"])
        source_context(repo, original["source_commit"], original["source_manifest_sha256"])
        result = bind(args.rendered, args.environment, args.inspection, args.network_inspection, args.output)
    else:
        bound = document(args.bound / "binding.json")
        launch, _ = load_render(bound["rendered"])
        original = load_prepared(launch["preparation"])
        source_context(repo, original["source_commit"], original["source_manifest_sha256"])
        result = verify_generation(args.bound, args.inspection)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
