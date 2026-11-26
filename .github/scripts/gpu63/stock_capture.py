"""Capture owned stock vLLM inputs for a full PLAN with a bounded execution scope."""
from __future__ import annotations

import argparse
from copy import deepcopy
import os
from pathlib import Path
import re
import signal
import stat
import time

import yaml

from guard.adapters.engine_profiles import supports
from guard.adapters.model_inventory import inventory
from guard.adapters.vllm_release_profiles import usage_report_profile
from guard.adapters.vllm_startup_load import (compile_cache_profile, graph_capture_profile,
                                              safetensors_reader_profile, source_profile)
from guard.executors.kubernetes_normal_startup_load import capture_startup_log_prefix
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime.kubernetes_binding import verify_owned_pod
from guard.update_validation import _plain_path
from tools import gpu_a_aws_lab as lab, gpu_a_run_inputs as inputs
from tools.mvp_a_input_preflight import _read
from tools.mvp_versions import versions


RELEASES = {"baseline": "0.30.0", "candidate": "0.31.0"}
MODEL = "Qwen/Qwen2-VL-2B-Instruct"
REVISION = "895c3a49bc3fa70a340399125c650a463535e71c"
VERIFIED_RESIDENT_MODEL = Path(os.environ.get("BYUL_VERIFIED_RESIDENT_MODEL", "/unconfigured-resident-model"))
MODEL_INVENTORY = "ad73950ffd6022ad452aef105d66caf2c623a3eeb7dc9eafe983ee416168d432"
MODEL_REFERENCE = "edb49912a757df5b7d6b27a6101a4835541151e2d6d6287a774a79a60ec03c62"
CONTEXT_SHA = os.environ.get("BYUL_EXPECTED_CONTEXT_SHA256")
SOURCE_TREE = "22e6e83d4fe26074c3c6c6e5e68742863f5239fe"
MANIFEST_SHA = "d75d5da1ede5c278063659aa9e84c25306466fdf1077af8bfa72d7220ebdfd3f"
NORMAL_CHECKS = {
    "LIF-01": "normal_startup_load_only",
    "LIF-03": "normal_request_latency_only",
    "LIF-04": "normal_stream_only",
    "OBS-02": "normal_serving_readiness_only",
    "OBS-04": "local_observation_only",
    "PLT-02": "protected_path_metadata_only",
    "PLT-06": "worker_trust_metadata_only",
    "REQ-09": "normal_token_input_only",
    "REQ-10": "normal_sampling_only",
    "REQ-20": "normal_graph_capture_only",
    "SUP-07": "normal_compile_cache_only",
    "SUP-10": "installed_plugin_inventory_only",
    "SUP-12": "normal_safetensors_reader_only",
}
SERVING_FEATURES = {"LIF-03": "stream_buffer", "LIF-04": "stream_buffer", "OBS-02": "observation_state",
                    "REQ-09": "request_input_checks", "REQ-10": "sampling"}
STARTUP_PROFILES = {"LIF-01": source_profile, "REQ-20": graph_capture_profile,
                    "SUP-07": compile_cache_profile, "SUP-12": safetensors_reader_profile}
UPDATE = Path(lab.__file__).resolve().parents[1]
CAP = 256 * 1024
LABEL = "byul.lab/campaign"
GPU_QUERY = ("nvidia-smi", "--query-gpu=uuid,name,driver_version", "--format=csv,noheader")
VERSION_PROGRAM = ("from importlib.metadata import distributions; "
                   "version, = [d.version for d in distributions(path=['/usr/local/lib/python3.12/dist-packages']) "
                   "if d.metadata['Name'] == 'vllm']; print(version)")
UID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}")
ENGINE_ENTRYPOINT = ["/usr/local/bin/vllm-nonroot-entrypoint.sh"]
STARTUP_GENERATION = "v1-normal-native-startup"


def pinned(path, digest, limit=CAP):
    raw, _ = _read(_plain_path(Path(path)), max_bytes=limit)
    if sha256_hex(raw) != digest:
        raise ValueError("MAIN_PINNED_SOURCE_CHANGED")
    return raw


def preflight(reference):
    pinned(UPDATE / "release/reviewed-source.json", MANIFEST_SHA)
    approved = strict_json(pinned(reference, MODEL_REFERENCE), limit=CAP)
    releases = versions()
    if (approved.get("model_id") != MODEL or approved.get("inventory_sha256") != MODEL_INVENTORY
            or len(approved.get("files", ())) != 11
            or sha256_hex(canonical_json(approved["files"])) != MODEL_INVENTORY
            or {side: row["version"] for side, row in releases.items()} != RELEASES):
        raise ValueError("MAIN_APPROVED_INPUT_MISMATCH")
    approved_stock_files(approved)
    return approved, releases


def approved_stock_files(approved):
    names = [row["path"] for row in approved["files"]]
    if (len(names) != 11
            or any(type(name) is not str or name != Path(name).name
                   or name.startswith(".") or Path(name).suffix not in {".json", ".txt", ".safetensors"}
                   for name in names) or len(set(names)) != 11):
        raise ValueError("MAIN_APPROVED_STOCK_MODEL_FILES_INVALID")
    return frozenset(names)


def require_model(root, model_root, approved):
    model = _plain_path(model_root)
    if model not in (Path("/srv/serving/models") / REVISION, root / "approved-model" / REVISION, VERIFIED_RESIDENT_MODEL):
        raise ValueError("MAIN_MODEL_CUSTODY_PATH_UNVERIFIED")
    expected = approved_stock_files(approved)
    if {entry.name for entry in model.iterdir()} != expected:
        raise ValueError("MAIN_MODEL_FILE_SET_MISMATCH")
    for name in expected:
        leaf = model / name
        if not stat.S_ISREG(leaf.lstat().st_mode):
            raise ValueError("MAIN_MODEL_FILE_SET_MISMATCH")
    actual = inventory(model, "all")
    if actual["files"] != approved["files"] or actual["inventory_sha256"] != MODEL_INVENTORY:
        raise ValueError("MAIN_MODEL_DIGEST_MISMATCH")
    return actual


def command(*args, data=None, limit=CAP, timeout=28):
    result = capture_pipes(list(args), timeout=timeout, env_extra={}, max_stdout_bytes=limit + 1,
                           max_stderr_bytes=2048, input_bytes=data)
    raw = result["stdout_bytes"]
    if result["returncode"] != 0 or not result["capture"]["complete"] or len(raw) > limit:
        raise ValueError("MAIN_OWNED_PROCESS_UNAVAILABLE")
    return raw


def save(directory, name, value):
    raw = value if type(value) is bytes else canonical_json(value)
    path = directory / name
    with path.open("xb") as stream:
        if stream.write(raw) != len(raw):
            raise OSError("MAIN_ARTIFACT_WRITE_INCOMPLETE")
    return path


def gpu_rows(raw):
    rows = []
    for line in raw.decode("ascii").splitlines():
        identifier, name, driver = (part.strip() for part in line.split(","))
        if (not re.fullmatch(r"GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identifier)
                or name not in {"L4", "NVIDIA L4"} or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", driver)):
            raise ValueError("MAIN_GPU_IDENTITY_UNVERIFIED")
        rows.append((identifier, name, driver))
    if not rows or len({row[0] for row in rows}) != len(rows) or len({row[1:] for row in rows}) != 1:
        raise ValueError("MAIN_GPU_IDENTITY_UNVERIFIED")
    return rows


def kube(kubeconfig, *args, data=None, limit=CAP, timeout=28):
    return command("kubectl", "--kubeconfig", str(kubeconfig), f"--request-timeout={max(1, timeout - 2)}s",
                   *args, data=data, limit=limit, timeout=timeout)


def read(kubeconfig, *args):
    return strict_json(kube(kubeconfig, *args, "-o", "json"), limit=CAP)


def create(kubeconfig, document):
    return strict_json(kube(kubeconfig, "create", "-f", "-", "-o", "json", data=canonical_json(document)), limit=CAP)


def verify_host(kubeconfig, releases):
    context = kube(kubeconfig, "config", "current-context", limit=256).decode("ascii").strip()
    if sha256_hex(context.encode()) != CONTEXT_SHA:
        raise ValueError("MAIN_OWNED_CLUSTER_CONTEXT_CHANGED")
    host_gpus = gpu_rows(command(*GPU_QUERY, limit=4096))
    if len(host_gpus) != 1:
        raise ValueError("SINGLE_L4_DEVICE_REQUIRED")
    for attempt in range(19):
        nodes = read(kubeconfig, "get", "nodes")["items"]
        if len(nodes) == 1 and nodes[0]["status"]["allocatable"].get("nvidia.com/gpu") == "1":
            break
        if attempt == 18:
            raise ValueError("SINGLE_L4_ALLOCATABLE_DEVICE_REQUIRED")
        time.sleep(5)
    used = command("nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", limit=128)
    retained = read(kubeconfig, "-n", "serving-lab", "get", "deployment", "serving-engine")
    if [line.strip() for line in used.decode("ascii").splitlines()] != ["0"] or retained["spec"]["replicas"] != 0:
        raise ValueError("MAIN_RETAINED_GPU_ALLOCATION_NOT_IDLE")
    rows = command("ctr", "-n", "k8s.io", "images", "ls", limit=CAP).decode().splitlines()[1:]
    resident = {row.split()[0]: row.split()[2] for row in rows if len(row.split()) >= 3}
    if any(resident.get(row["image"]) != row["image"].rsplit("@", 1)[1] for row in releases.values()):
        raise ValueError("MAIN_APPROVED_ENGINE_IMAGES_NOT_RESIDENT")
    return context, {row[0] for row in host_gpus}


def cleanup_namespaces(kubeconfig, namespaces):
    result = {}
    for run, uid in namespaces.items():
        try:
            owned_namespace(kubeconfig, run, uid, allow_terminating=True)
            kube(kubeconfig, "delete", "namespace", run, "--wait=true", "--timeout=130s", timeout=135)
            if kube(kubeconfig, "get", "namespace", run, "--ignore-not-found", "-o", "json", limit=4096).strip():
                raise ValueError("MAIN_OWNED_NAMESPACE_REMAINS")
            result[run] = "owned_namespace_removed"
        except (KeyError, OSError, TypeError, ValueError):
            result[run] = "owned_namespace_cleanup_unverified"
    return result


def diagnostic(error):
    value = str(error)
    return value if re.fullmatch(r"[A-Z][A-Z0-9_]{0,117}", value) else "MAIN_CAPTURE_UNAVAILABLE"


def interrupted(*_):
    raise KeyboardInterrupt


ROLE_LABELS = {"engine": "serving-engine", "probe": "engine-test-probe"}
OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
FORBIDDEN_ARGS = {"--trust-remote-code", "--code-revision", "--enable-lora", "--profiler-config",
                  "--chat-template", "--mm-processor-kwargs", "--hf-overrides"}


def _check_pod_spec(pod, role, run, model, launch=None, *, cache=None, expected_environment=None):
    spec = pod["spec"]
    labels = pod["metadata"]["labels"]
    container, = spec["containers"]
    if (labels.get("guard.run") != run or labels.get("app") != ROLE_LABELS[role]
            or any(spec.get(key, False) is not False
                   for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace"))
            or spec.get("serviceAccountName") != "default"
            or spec.get("automountServiceAccountToken") is not False or spec.get("enableServiceLinks") is not False
            or spec.get("initContainers") or spec.get("ephemeralContainers")
            or container.get("imagePullPolicy") != "Never"
            or container.get("securityContext", {}).get("privileged", False) is not False
            or container.get("securityContext", {}).get("allowPrivilegeEscalation") is not False
            or container.get("envFrom")
            or any(port.get("hostPort") for port in container.get("ports", []))):
        raise ValueError("MAIN_ORDINARY_POD_SCOPE_UNVERIFIED")
    if role == "probe":
        if (container["name"] != "probe" or container.get("command") != ["sleep", "infinity"]
                or container.get("args") or container.get("env")
                or any(volume.get("hostPath") is not None for volume in spec.get("volumes", []))):
            raise ValueError("MAIN_ORDINARY_PROBE_SCOPE_UNVERIFIED")
        return
    args = container["args"]
    model_mount, = [mount for mount in container["volumeMounts"] if mount.get("mountPath") == "/models"]
    volumes = {volume["name"]: volume for volume in spec["volumes"]}
    model_volume = volumes[model_mount["name"]]
    cache_mount, = [mount for mount in container["volumeMounts"] if mount.get("mountPath") == "/cache"]
    environment = container.get("env", [])
    resources = container["resources"]
    if (container["name"] != "engine" or launch is None or container.get("command") != ENGINE_ENTRYPOINT
            or launch["command"] != ENGINE_ENTRYPOINT
            or args != launch["argv"] or FORBIDDEN_ARGS & {argument.split("=", 1)[0] for argument in args}
            or [value for key, value in zip(args, args[1:]) if key == "--tensor-parallel-size"] != ["1"]
            or args[0] != "/models"
            or any(resources.get(kind, {}).get("nvidia.com/gpu") != "1" for kind in ("limits", "requests"))
            or environment != expected_environment
            or any([entry for entry in environment if entry.get("name") == key] != [{"name": key, "value": value}]
                   for key, value in (OFFLINE_ENV | {"VLLM_USE_V2_MODEL_RUNNER": "0"}).items())
            or any(entry.get("name") in {"PYTHONPATH", "VLLM_SERVER_DEV_MODE", "BYUL_CODE_MARKER_ROOT"}
                   for entry in environment)
            or {mount.get("mountPath") for mount in container["volumeMounts"]} != {"/models", "/cache", "/dev/shm"}
            or model_mount.get("readOnly") is not True
            or any(model_mount.get(key) for key in ("subPath", "subPathExpr", "mountPropagation"))
            or model_volume.get("hostPath") != {"path": str(model), "type": "Directory"}
            or cache_mount["name"] != "cache"
            or any(cache_mount.get(key) for key in ("subPath", "subPathExpr", "mountPropagation"))
            or volumes["cache"].get("hostPath") != {"path": str(cache), "type": "Directory"}):
        raise ValueError("MAIN_STOCK_OFFLINE_MODEL_SCOPE_UNVERIFIED")


def render_side(host_root, run, release, model):
    raw, probe_raw, metadata = lab.render(
        UPDATE / "tests/fixtures/aws-handoff-20261026", generation="v2-default", profile="reference",
        run_id=run, engine_image=release["image"], engine_version=release["version"],
        model_revision=REVISION, model_id=MODEL, model_host_snapshot=str(model), host_root=str(host_root),
        release_launch=True, max_model_len=4096,
    )
    deployment, service = yaml.safe_load_all(raw)
    probe = yaml.safe_load(probe_raw)
    template = deployment["spec"]["template"]
    for pod in (template, probe):
        spec = pod["spec"]
        pod["metadata"]["labels"]["byul.a/generation"] = STARTUP_GENERATION
        spec["serviceAccountName"] = "default"
        spec["automountServiceAccountToken"] = False
        for container in spec["containers"]:
            container["imagePullPolicy"] = "Never"
    deployment["metadata"]["namespace"] = probe["metadata"]["namespace"] = run
    if service["spec"].get("type") != "ClusterIP" or service["spec"].get("externalIPs"):
        raise ValueError("MAIN_UNEXPECTED_ENGINE_SERVICE_EXPOSURE")
    cache = host_root / "cache" / run
    engine, = template["spec"]["containers"]
    # Both exact OCI images contain the installed non-root wrapper. The native
    # LIF-01 source contract selects its ordinary V1 CUDA log on both releases.
    engine["command"] = list(ENGINE_ENTRYPOINT)
    engine["env"].append({"name": "VLLM_USE_V2_MODEL_RUNNER", "value": "0"})
    metadata["launch"].update(command=list(ENGINE_ENTRYPOINT), runner="v1")
    metadata["generation"] = STARTUP_GENERATION
    cache_volume, = [volume for volume in template["spec"]["volumes"] if volume["name"] == "cache"]
    cache_volume["hostPath"] = {"path": str(cache), "type": "Directory"}
    metadata["cache_path"] = str(cache)
    metadata["stock_environment"] = deepcopy(template["spec"]["containers"][0]["env"])
    metadata["outputs_sha256"] = {"engine.yaml": sha256_hex(canonical_json(deployment)),
                                 "probe.yaml": sha256_hex(canonical_json(probe))}
    _check_pod_spec(template, "engine", run, model, metadata["launch"],
                    cache=cache, expected_environment=metadata["stock_environment"])
    _check_pod_spec(probe, "probe", run, model)
    return metadata, deployment, probe, cache


DENY_ALL = {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}


def add_network_policy(kubeconfig, run, name, spec):
    create(kubeconfig, {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                        "metadata": {"name": name, "namespace": run,
                                     "labels": {LABEL: run, "guard.run": run}},
                        "spec": spec})


def fixed_network_policies(run, *, serving):
    policies = {"deny-all": DENY_ALL}
    if serving:
        engine = {"matchLabels": {"app": ROLE_LABELS["engine"], "guard.run": run}}
        probe = {"matchLabels": {"app": ROLE_LABELS["probe"], "guard.run": run}}
        port = [{"protocol": "TCP", "port": 8000}]
        policies["ordinary-engine-ingress"] = {
            "podSelector": engine, "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": probe}], "ports": port}]}
        policies["ordinary-probe-egress"] = {
            "podSelector": probe, "policyTypes": ["Egress"],
            "egress": [{"to": [{"podSelector": engine}], "ports": port}]}
    return policies


def verify_network(kubeconfig, run, *, serving):
    expected = fixed_network_policies(run, serving=serving)
    policies = read(kubeconfig, "-n", run, "get", "networkpolicy")["items"]
    actual = {}
    for policy in policies:
        meta = policy["metadata"]
        name = meta["name"]
        if (name in actual or meta.get("namespace") != run
                or meta.get("labels", {}).get(LABEL) != run or meta.get("labels", {}).get("guard.run") != run
                or meta.get("deletionTimestamp")):
            raise ValueError("MAIN_ORDINARY_NETWORK_SCOPE_CHANGED")
        actual[name] = {key: value for key, value in policy["spec"].items()
                        if key not in {"ingress", "egress"} or value != []}
    if (actual != expected or read(kubeconfig, "-n", run, "get", "service")["items"]
            or read(kubeconfig, "-n", run, "get", "ingress")["items"]):
        raise ValueError("MAIN_ORDINARY_NETWORK_SCOPE_CHANGED")
    return {"network_policy_spec_sha256": sha256_hex(canonical_json(actual)),
            "deny_all_verified": True, "namespace_local_probe_to_engine_tcp_port": 8000 if serving else None,
            "public_service_or_ingress_present": False, "policy_count": len(actual)}


def owned_namespace(kubeconfig, run, namespace_uid, *, allow_terminating=False):
    namespace = read(kubeconfig, "get", "namespace", run)
    ns = namespace["metadata"]
    labels = ns.get("labels", {})
    if (type(namespace_uid) is not str or UID.fullmatch(namespace_uid) is None
            or ns.get("name") != run or ns.get("uid") != namespace_uid
            or labels.get("byul.lab/owned") != "true" or labels.get(LABEL) != run or labels.get("guard.run") != run
            or ns.get("deletionTimestamp") and not allow_terminating):
        raise ValueError("MAIN_OWNED_NAMESPACE_CHANGED")
    return namespace


def default_service_account(kubeconfig, run, uid, *, wait):
    owned_namespace(kubeconfig, run, uid)
    deadline = time.monotonic() + (30 if wait else 8)
    while time.monotonic() < deadline:
        raw = kube(kubeconfig, "-n", run, "get", "serviceaccount", "default",
                   "--ignore-not-found", "-o", "json", limit=4096, timeout=6)
        if raw.strip():
            account = strict_json(raw, limit=4096)
            meta = account["metadata"]
            if (account.get("apiVersion") != "v1" or account.get("kind") != "ServiceAccount"
                    or meta.get("namespace") != run or meta.get("name") != "default"
                    or type(meta.get("uid")) is not str or UID.fullmatch(meta["uid"]) is None
                    or meta.get("deletionTimestamp")):
                raise ValueError("MAIN_DEFAULT_SERVICE_ACCOUNT_UNVERIFIED")
            owned_namespace(kubeconfig, run, uid)
            return meta["uid"]
        if not wait:
            break
        time.sleep(0.5)
    raise ValueError("MAIN_DEFAULT_SERVICE_ACCOUNT_NOT_READY")


def ready_snapshot(kubeconfig, run, namespace_uid, metadata, probe_name):
    namespace = owned_namespace(kubeconfig, run, namespace_uid)
    ns = namespace["metadata"]
    pods = read(kubeconfig, "-n", run, "get", "pods")["items"]
    if len(pods) != 2:
        raise ValueError("MAIN_ORDINARY_POD_ROSTER_CHANGED")
    engine, = [pod for pod in pods if pod["metadata"]["labels"].get("app") == ROLE_LABELS["engine"]]
    probe, = [pod for pod in pods if pod["metadata"]["name"] == probe_name]
    _check_pod_spec(engine, "engine", run, metadata["model_host_snapshot"], metadata["launch"],
                    cache=metadata["cache_path"], expected_environment=metadata["stock_environment"])
    _check_pod_spec(probe, "probe", run, metadata["model_host_snapshot"])
    bindings = {role: inputs._binding(pod, namespace, role, ROLE_LABELS[role])
                for role, pod in (("engine", engine), ("probe", probe))}
    identity = {role: verify_owned_pod(pod, ns, bindings[role], run) for role, pod in
                (("engine", engine), ("probe", probe))}
    if any(row["restart_count"] != 0 or row["image_reference"] != metadata["engine_image"]
           for row in identity.values()):
        raise ValueError("MAIN_ORDINARY_POD_IDENTITY_UNVERIFIED")
    return namespace, engine, probe, bindings, identity


def scope_capabilities(env, target, metadata):
    """Qualify exact scope inputs without claiming the runtime observations."""
    result = {}
    engine = env.engine
    owned_target = target["lab"] is False and target["environment_ref"] == env.ref().model_dump(mode="json")
    if not owned_target:
        raise ValueError("MAIN_STOCK_TARGET_UNVERIFIED")
    for check, feature in SERVING_FEATURES.items():
        blocker = None if supports(engine.name, engine.version, feature) else "MAIN_NORMAL_RELEASE_FEATURE_UNAVAILABLE"
        if check == "REQ-09" and target["comparison_config"]["launch"]["max_model_len"] <= 1:
            blocker = "MAIN_TOKEN_INPUT_WINDOW_UNAVAILABLE"
        result[check] = blocker
    result["SUP-10"] = (None if engine.name == "vllm" and supports(engine.name, engine.version)
                        else "MAIN_INVENTORY_RELEASE_FEATURE_UNAVAILABLE")
    try:
        startup = source_profile(env)
    except ValueError:
        startup = None
    container_env = {row["name"]: row.get("value") for row in metadata["stock_environment"]}
    launch = target["comparison_config"]["launch"]
    stock_boot = (startup is not None and startup["entrypoint"] == target["engine"]["command"]
                  and all(container_env.get(name) == value for name, value in startup["required_env"].items())
                  and (env.runtime.tp_size, env.runtime.pp_size, env.runtime.dp_size) == (1, 1, 1))
    local_usage = (
        stock_boot and usage_report_profile(env) is not None
        and container_env.get("VLLM_NO_USAGE_STATS") == container_env.get("DO_NOT_TRACK") == "1"
        and container_env.get("XDG_CONFIG_HOME") == "/cache/config"
    )
    result["OBS-04"] = None if local_usage else "MAIN_LOCAL_USAGE_INPUTS_UNVERIFIED"
    ipc_scope = supports(engine.name, engine.version, "unix_ipc")
    for check in ("PLT-02", "PLT-06"):
        result[check] = None if ipc_scope else "MAIN_OWNED_IPC_METADATA_SCOPE_UNAVAILABLE"
    for check, reader in STARTUP_PROFILES.items():
        if "startup_log_prefix" not in target:
            result[check] = "MAIN_STARTUP_PREFIX_UNAVAILABLE"
        elif not stock_boot:
            result[check] = "MAIN_STOCK_BOOT_PROFILE_UNVERIFIED"
        else:
            try:
                profile = reader(env)
                if check == "SUP-07" and container_env.get(profile["cache_env"]) != profile["cache_root"]:
                    raise ValueError
                if any(arg.partition("=")[0].replace("_", "-") in startup["forbidden_args"]
                       for arg in launch["argv"] if arg.startswith("--")):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                result[check] = "MAIN_EXACT_RELEASE_OBSERVER_UNAVAILABLE"
            else:
                result[check] = None
    return {check: {"mode": mode, "input_scope_ready": result[check] is None, "blocker": result[check]}
            for check, mode in NORMAL_CHECKS.items()}


def observe(kubeconfig, context, run, uid, metadata, probe_name, output):
    _, _, _, bindings, before = ready_snapshot(kubeconfig, run, uid, metadata, probe_name)
    args = ("-n", run, "exec", bindings["engine"]["pod"], "-c", "engine", "--")
    gpu = gpu_rows(kube(kubeconfig, *args, *GPU_QUERY, limit=2048))
    observed = kube(kubeconfig, *args, "python3", "-I", "-S", "-B", "-c", VERSION_PROGRAM,
                    limit=128).decode("ascii").strip()
    namespace, engine, probe, current, after = ready_snapshot(kubeconfig, run, uid, metadata, probe_name)
    if bindings != current or before != after or len(gpu) != 1 or observed != metadata["engine_version"]:
        raise ValueError("MAIN_OWNED_TP1_STARTUP_UNVERIFIED")
    device, gpu_model, driver = gpu[0]
    env = inputs.environment(metadata, inventory=MODEL_INVENTORY, gpu_model=gpu_model, driver=driver,
                             observed_engine_version=observed, external_platform="Linux x86_64")
    topology = env.runtime.model_copy(update={"tp_size": 1, "pp_size": 1, "dp_size": 1})
    env = env.model_copy(update={"runtime": topology}).seal()
    target = inputs.target(env, metadata, namespace, engine, probe, context=context, observed_engine_version=observed)
    if target["lab"] is not False:
        raise ValueError("MAIN_STOCK_TARGET_UNVERIFIED")
    target["engine"]["command"] = metadata["launch"]["command"].copy()
    target["comparison_config"]["launch"] = {
        "argv": metadata["launch"]["argv"].copy(), "max_model_len": metadata["max_model_len"],
        "offline_env": OFFLINE_ENV.copy()}
    try:
        target["startup_log_prefix"] = capture_startup_log_prefix(target, env)
    except ValueError as error:
        startup_prefix_error = diagnostic(error)
    else:
        startup_prefix_error = None
    _, _, _, final_bindings, final_identity = ready_snapshot(kubeconfig, run, uid, metadata, probe_name)
    if bindings != final_bindings or before != final_identity:
        raise ValueError("MAIN_OWNED_POD_GENERATION_CHANGED")
    capabilities = scope_capabilities(env, target, metadata)
    safe_ready_checks = [check for check, item in capabilities.items() if item["input_scope_ready"]]
    env_path = save(output, "environment.json", env.model_dump(mode="json"))
    target_path = save(output, "target.json", target)
    save(output, "normal-scope-capabilities.json", {
        "schema": "byul-owned-main-normal-capabilities/1", "environment_ref": env.ref().model_dump(mode="json"),
        "target_sha256": sha256_hex(target_path.read_bytes()), "checks": capabilities,
        "startup_log_prefix_captured": startup_prefix_error is None,
        "producer_observations_pending": True})
    save(output, "startup-owner.json", {
        "environment_ref": env.ref().model_dump(mode="json"), "version": observed,
        "target_file_sha256": sha256_hex(target_path.read_bytes()),
        "owner_sha256": sha256_hex(canonical_json(before["engine"])),
        "probe_owner_sha256": sha256_hex(canonical_json(before["probe"])),
        "same_owner_before_after": True, "gpu_device_sha256": sha256_hex(device.encode()),
        "startup_log_prefix_captured": startup_prefix_error is None, "startup_log_prefix_error": startup_prefix_error})
    capture = {"environment": env, "target": target, "environment_path": env_path, "target_path": target_path,
               "gpu_devices": [device], "safe_ready_checks": safe_ready_checks}
    return capture, before, startup_prefix_error


def verify_generation(kubeconfig, run, uid, metadata, probe_name, account_uid, capture, observed_identity):
    _, _, _, bindings, identity = ready_snapshot(kubeconfig, run, uid, metadata, probe_name)
    if (observed_identity != identity
            or any(capture["target"]["kubernetes"][role] != bindings[role] for role in ROLE_LABELS)
            or default_service_account(kubeconfig, run, uid, wait=False) != account_uid):
        raise ValueError("MAIN_OWNED_POD_GENERATION_CHANGED")
