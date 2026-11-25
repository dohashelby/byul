"""Collect default Python mode from an owned stock service and its workers."""

from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import re

from guard.adapters.vllm_file_read_sensor import dependency
from guard.adapters.vllm_stock_python_mode import (CONFIG_SHA256, FILE_SIZES, LOADER, MODEL, STOCK_MODEL, is_sha256,
                                                  launch_command, validate_observer_profile)
from guard.configuration import NORMAL_PYTHON_MODE_OPTION, NORMAL_PYTHON_MODE_SCOPE_ERROR
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_lifecycle_capture import supervised_source

PROFILE = "kubernetes-owned-normal-python-mode/1"
ARTIFACT = "normal_python_mode.json"
PROVENANCE_SCHEMA = "guard-sup11-stock-provenance/1"
_CHECK_PROFILE = "supply-boot-python-mode-v1"
_SETTINGS = {NORMAL_PYTHON_MODE_OPTION, "target", "provenance", "kubectl_binary", "max_duration_seconds"}
_PROOF_ERROR = "SUP11_STOCK_MODE_PROOF_UNAVAILABLE"
_UNCOLLECTED = ["supported_python_modes", "optimized_mode_startup", "mode_comparison",
                "service_processor_consumption", "dynamic_import_enforcement"]
_ASSETS = Path(__file__).resolve().parents[1] / "runtime/assets"
PRODUCER_SHA256 = sha256_hex((_ASSETS / "sup11_stock_observer.py.txt").read_bytes())
_READER = supervised_source((_ASSETS / "sup11_stock_readback.py.txt").read_text(encoding="utf-8"))
_INVENTORY = supervised_source((_ASSETS / "sup11_stock_inventory.py.txt").read_text(encoding="utf-8"))
_ROLE_ROWS = [("service", None), ("worker", 0), ("worker", 1)]
_PROCESS_FIELDS = {"role", "rank", "pid", "start_ticks", "python_optimize", "anchor_module", "anchor_sha256",
                   "module", "module_sha256", "producer_sha256"}
_OWNER_KEYS = ("namespace_uid", "pod_uid", "container_id", "started_at")
_OFFLINE = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
_OBSERVER_ENV = {"PYTHONPATH": "/guard-sup11", "VLLM_WORKER_MULTIPROC_METHOD": "spawn"}
_LAUNCH_OPTIONS = {"--served-model-name", "--host", "--port", "--max-model-len", "--max-num-seqs",
                   "--gpu-memory-utilization", "--tensor-parallel-size", "--pipeline-parallel-size",
                   "--data-parallel-size", "--distributed-executor-backend"}


def normal_python_mode_transport(target, env, *, duration, binary="kubectl", runner=None):
    observer_path = target.get("sup11_observer_host_path")
    if (target.get("lab") is not False or target.get("synthetic", False) is not False
            or (env.model.id, env.model.revision, env.model.inventory_sha256) != STOCK_MODEL
            or (env.runtime.tp_size, env.runtime.pp_size, env.runtime.dp_size, env.runtime.gpu_count) != (2, 1, 1, 2)
            or target["engine"]["command"] != launch_command(env)
            or target.get("observed_version") != env.engine.version
            or target["model_mapping"]["schema"] != "guard.model-request-mapping/1"
            or target["model_mapping"]["container_mount"] != "/models"
            or target["comparison_config"]["launch"]["offline_env"] != _OFFLINE
            or not is_sha256(target.get("sup11_provenance_sha256"))
            or type(observer_path) is not str
            or not re.fullmatch(r"(?:/[A-Za-z0-9_.-]+){1,12}/observer/(baseline|candidate)", observer_path)
            or {".", ".."}.intersection(observer_path.split("/"))):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    transport = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner,
                               profile=INPUT_VALIDATION_PROFILE)
    observer = PurePosixPath(observer_path)
    if transport.binding["run_id"] != observer.parents[1].name + "-" + observer.name:
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    args = target["comparison_config"]["launch"]["argv"]
    if type(args) is not list or not args or args[0] != "/models" or len(args) % 2 != 1:
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    keys, values = args[1::2], args[2::2]
    if (len(set(keys)) != len(keys) or set(keys) - _LAUNCH_OPTIONS
            or any(type(value) is not str or not value or value.startswith("-") for value in values)):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    options = dict(zip(keys, values))
    if (options.get("--served-model-name") != transport.api_model
            or options.get("--tensor-parallel-size") != "2" or options.get("--pipeline-parallel-size", "1") != "1"
            or options.get("--data-parallel-size", "1") != "1"
            or options.get("--distributed-executor-backend", "mp") != "mp"):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    engine = transport.binding["engine"]
    window = target.get("startup_ready_window")
    if (engine["restart_count"] != 0 or type(window) is not dict or set(window) != {"container_started_at", "ready_at"}
            or window["container_started_at"] != engine["started_at"]
            or not _timestamp(window["container_started_at"]) <= _timestamp(window["ready_at"]) <= utcnow()):
        raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)
    return transport


def _timestamp(value):
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError(_PROOF_ERROR)
    return stamp.astimezone(timezone.utc)


def _owned_pod(transport, target):
    pod = transport._verify(capture_snapshot=True)["engine"]
    spec = pod["spec"]
    container, = spec["containers"]
    engine = transport.binding["engine"]
    if (container["name"] != engine["container"] or container.get("command") != target["engine"]["command"]
            or container.get("args") != target["comparison_config"]["launch"]["argv"] or container.get("envFrom")
            or spec.get("hostPID", False) is not False or spec.get("shareProcessNamespace", False) is not False
            or spec.get("initContainers") or spec.get("ephemeralContainers")):
        raise ValueError(_PROOF_ERROR)
    env = container.get("env", [])
    by_name = {row["name"]: row for row in env}
    if (len(by_name) != len(env) or {"PYTHONHOME", "PYTHONOPTIMIZE", "LD_PRELOAD"} & by_name.keys()
            or any(by_name.get(key) != {"name": key, "value": value} for key, value in (_OFFLINE | _OBSERVER_ENV).items())):
        raise ValueError(_PROOF_ERROR)
    mounts = container["volumeMounts"]
    paths = {row["mountPath"]: row for row in mounts}
    if (len(paths) != len(mounts) or set(paths) - {"/models", "/cache", "/dev/shm", "/guard-sup11"}
            or not {"/models", "/cache", "/guard-sup11"} <= paths.keys()):
        raise ValueError(_PROOF_ERROR)
    cache = paths["/cache"]
    cache_volume, = (row for row in spec["volumes"] if row["name"] == cache["name"])
    owner_root = PurePosixPath(target["sup11_observer_host_path"]).parents[1]
    cache_path = str(owner_root / "cache" / transport.binding["run_id"])
    if (cache.get("readOnly", False) is not False or cache.get("subPath") or cache.get("subPathExpr")
            or cache_volume.get("hostPath") != {"path": cache_path, "type": "Directory"}):
        raise ValueError(_PROOF_ERROR)
    for path, host in {"/models": target["model_mapping"]["host_snapshot"],
                       "/guard-sup11": target["sup11_observer_host_path"]}.items():
        mount = paths[path]
        volume, = (row for row in spec["volumes"] if row["name"] == mount["name"])
        if (mount.get("readOnly") is not True or mount.get("subPath") or mount.get("subPathExpr")
                or volume.get("hostPath") != {"path": host, "type": "Directory"}):
            raise ValueError(_PROOF_ERROR)
    return pod, container


def _ready(transport, target):
    pod, container = _owned_pod(transport, target)
    state, = pod["status"]["containerStatuses"]
    condition, = (row for row in pod["status"]["conditions"] if row.get("type") == "Ready")
    readiness = container["readinessProbe"]
    probe = readiness["httpGet"]
    if ({"exec", "tcpSocket", "grpc"} & readiness.keys()
            or set(probe) - {"path", "port", "scheme", "host", "httpHeaders"}
            or probe.get("path") != "/health" or probe.get("scheme", "HTTP") != "HTTP"
            or probe.get("host", "") or probe.get("httpHeaders", []) or state.get("ready") is not True
            or condition.get("status") != "True"
            or condition.get("lastTransitionTime") != target["startup_ready_window"]["ready_at"]):
        raise ValueError(_PROOF_ERROR)
    port = probe["port"]
    if type(port) is str:
        selected, = (row for row in container.get("ports", []) if row.get("name") == port)
        if selected.get("protocol", "TCP") != "TCP":
            raise ValueError(_PROOF_ERROR)
        port = selected["containerPort"]
    if type(port) is not int or port != transport.endpoint.port:
        raise ValueError(_PROOF_ERROR)
    timing = {key: readiness[key] for key in ("initialDelaySeconds", "timeoutSeconds", "periodSeconds",
                                               "successThreshold", "failureThreshold") if key in readiness}
    if any(type(value) is not int or value < (0 if key == "initialDelaySeconds" else 1)
           for key, value in timing.items()):
        raise ValueError(_PROOF_ERROR)
    return {"observed_at": utcnow().isoformat(), "pod_ready": True, "engine_container_ready": True,
            "pod_ready_last_transition_at": _timestamp(condition["lastTransitionTime"]).isoformat(),
            "readiness_probe": {"method": "GET", "path": "/health", "scheme": "HTTP", "bound_engine_port": port,
                                "timing": timing}}


def _load(path):
    return strict_json(dependency(path, None), limit=65536)


def _processes(readback, profile, profile_digest):
    if (type(readback) is not dict or set(readback) != {"profile_sha256", "producer_sha256", "processes"}
            or readback["profile_sha256"] != profile_digest or readback["producer_sha256"] != PRODUCER_SHA256):
        raise ValueError(_PROOF_ERROR)
    rows = readback["processes"]
    if type(rows) is not list or len(rows) != len(_ROLE_ROWS):
        raise ValueError(_PROOF_ERROR)
    for row, (role, rank) in zip(rows, _ROLE_ROWS, strict=True):
        if (type(row) is not dict or set(row) != _PROCESS_FIELDS or row["role"] != role
                or type(row["rank"]) is not type(rank) or row["rank"] != rank
                or type(row["python_optimize"]) is not int or row["python_optimize"] != 0
                or type(row["pid"]) is not int or not 0 < row["pid"] < 2**23
                or type(row["start_ticks"]) is not int or row["start_ticks"] <= 0
                or row["producer_sha256"] != PRODUCER_SHA256):
            raise ValueError(_PROOF_ERROR)
        service = profile["service"]
        selected = (service["module"], service["sha256"], service["module"], service["sha256"]) if rank is None else (
            LOADER, profile["worker_loader_sha256"], MODEL, profile["model_source_sha256"])
        if selected != tuple(row[name] for name in ("anchor_module", "anchor_sha256", "module", "module_sha256")):
            raise ValueError(_PROOF_ERROR)
    if rows[0]["pid"] != 1 or len({row["pid"] for row in rows}) != len(rows):
        raise ValueError(_PROOF_ERROR)
    return rows


def prepared_provenance(document, env, binding_sha):
    if (type(document) is not dict or set(document) != {"schema", "environment_ref", "binding_sha256",
            "profile", "profile_sha256", "oci_source_qualification_sha256", "readback"}
            or document["schema"] != PROVENANCE_SCHEMA or document["environment_ref"] != env.ref().model_dump(mode="json")
            or document["binding_sha256"] != binding_sha or not is_sha256(document["oci_source_qualification_sha256"])):
        raise ValueError(_PROOF_ERROR)
    profile = document["profile"]
    validate_observer_profile(profile, env)
    digest = sha256_hex(canonical_json(profile))
    if document["profile_sha256"] != digest:
        raise ValueError(_PROOF_ERROR)
    _processes(document["readback"], profile, digest)
    return document


def _readback(transport, target, source):
    engine = transport.binding["engine"]
    _owned_pod(transport, target)
    budget = min(5, transport.deadline - transport.clock())
    if budget <= 0:
        raise ValueError(_PROOF_ERROR)
    receipt = transport._call([f"--namespace={engine['namespace']}", "exec", engine["pod"],
        f"--container={engine['container']}", "--", "python3", "-I", "-S", "-B", "-c", source, str(budget)],
        cap=8192, budget=budget)
    _owned_pod(transport, target)
    return receipt


def _validate_inventory(result):
    if (type(result) is not dict or set(result) != {"file_sizes", "config_sha256", "read_only", "auto_map_present",
                                                    "pid1_start_ticks"}
            or result["file_sizes"] != FILE_SIZES or result["config_sha256"] != CONFIG_SHA256
            or result["read_only"] is not True or result["auto_map_present"] is not False
            or type(result["pid1_start_ticks"]) is not int or result["pid1_start_ticks"] <= 0):
        raise ValueError(_PROOF_ERROR)


class KubernetesNormalPythonModeExecutor:
    name = "supply_boot"
    version = "0.1.0+k8s-sup11-stock-python-mode"

    def __init__(self, cfg, *, runner=None, expected_target_sha256=None, expected_provenance_sha256=None):
        self.cfg = cfg
        self._runner = runner
        self._target_pin = expected_target_sha256
        self._provenance_pin = expected_provenance_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_PYTHON_MODE_OPTION) is not True
                or check.id != "SUP-11" or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - _SETTINGS
                or any(type(self.cfg.get(key)) is not str or not self.cfg[key] for key in ("target", "provenance"))
                or type(values) is not dict or values):
            raise ValueError(NORMAL_PYTHON_MODE_SCOPE_ERROR)

    def _inputs(self, env, target_pin, provenance_pin):
        try:
            target = _load(self.cfg["target"])
            provenance = _load(self.cfg["provenance"])
            target_sha, provenance_sha = (sha256_hex(canonical_json(value)) for value in (target, provenance))
            if (target_pin != target_sha or provenance_pin != provenance_sha
                    or target.get("sup11_provenance_sha256") != provenance_sha):
                raise ValueError(_PROOF_ERROR)
            transport = normal_python_mode_transport(target, env, duration=self.cfg.get("max_duration_seconds", 60),
                binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._runner)
            binding_sha = sha256_hex(canonical_json(transport.binding))
            return target, prepared_provenance(provenance, env, binding_sha), transport
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError(_PROOF_ERROR) from None

    def prepared_normal_python_mode(self, check, env, values, *, target_sha, provenance_sha):
        self.validate_policy(check, values)
        self._inputs(env, target_sha, provenance_sha)

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        target, provenance, transport = self._inputs(env, self._target_pin, self._provenance_pin)
        ready_before = ready_after = installed = inventory = rows = None
        error = None
        try:
            ready_before = _ready(transport, target)
            before = _readback(transport, target, _READER)
            inventory = _readback(transport, target, _INVENTORY)
            after = _readback(transport, target, _READER)
            ready_after = _ready(transport, target)
            if (before != after or before != provenance["readback"]
                    or any(ready_before[key] != ready_after[key] for key in ready_before.keys() - {"observed_at"})):
                raise ValueError(_PROOF_ERROR)
            _validate_inventory(inventory)
            verified = _processes(before, provenance["profile"], provenance["profile_sha256"])
            if verified[0]["start_ticks"] != inventory["pid1_start_ticks"]:
                raise ValueError(_PROOF_ERROR)
            rows = verified
            profile = provenance["profile"]
            installed = {"binding_sha256": provenance["binding_sha256"], "service": profile["service"],
                         "worker_loader_sha256": profile["worker_loader_sha256"],
                         "model_source_sha256": profile["model_source_sha256"],
                         "profile_sha256": provenance["profile_sha256"]}
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            error = _PROOF_ERROR
        cleanup = transport.report()["cleanup_errors"]
        if cleanup:
            error = error or _PROOF_ERROR
        owner = {key: transport.binding["engine"][key] for key in _OWNER_KEYS}
        summary = {"profile": PROFILE, "target_sha256": self._target_pin, "provenance_sha256": self._provenance_pin,
                   "environment_ref": transport.environment_ref, "owner": owner, "ready_before": ready_before,
                   "ready_after": ready_after, "installed_source": installed, "model_inventory": inventory,
                   "processes": rows, "default_mode_observed": error is None, "uncollected": _UNCOLLECTED}
        artifacts = []
        try:
            path = Path(workdir) / ARTIFACT
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            error = error or "SUP11_STOCK_MODE_ARTIFACT_UNAVAILABLE"
        observations = {obs.key: {"value": None, "collected": False} for obs in check.required_observations}
        observations.update({
            "supply_boot_missing": {"value": _UNCOLLECTED if error is None else [*_UNCOLLECTED, "stock_default_mode"],
                                    "collected": True},
            "supply_boot_complete": {"value": False, "collected": True},
            "supply_boot_change_applied": {"value": False, "collected": True},
            "supply_boot_record_authenticated": {"value": False, "collected": True},
        })
        if error is None:
            observations["supply_boot_sessions"] = {"value": [
                {"scope": "stock_default_python_mode", "default_mode_observed": True,
                 "binding_sha256": installed["binding_sha256"]}], "collected": True}
        comparison = {} if error else {
            "fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "python_optimize": 0})),
            "config_sha256": sha256_hex(canonical_json(execution_conditions(target, {"profile": PROFILE}, []))),
            "model_inventory_sha256": env.model.inventory_sha256,
            "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu,
        }
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED,
            data_origin=DataOrigin.EXAMPLE if transport.process_observation_origin == "EXAMPLE" else DataOrigin.DECLARED,
            normal_control={"attempted": ready_before is not None, "succeeded": None,
                            "detail": "One owned stock boot observes default mode; support across modes is unobserved."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, artifacts=artifacts, comparability=comparison,
            config={NORMAL_PYTHON_MODE_OPTION: True, "target_sha256": self._target_pin,
                    "provenance_sha256": self._provenance_pin}, started_at=started, finished_at=utcnow(),
            abort_reason=error, cleanup_errors=cleanup)
