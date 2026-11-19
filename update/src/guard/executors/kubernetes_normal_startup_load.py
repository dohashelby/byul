"""LIF-01's ordinary model load report from a re-read owned boot log prefix."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path, PurePosixPath
import re

from guard.adapters.engine_profiles import supports
from guard.core.select import check_policy_value
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import (
    ENGINE_CALLBACK_PROFILE, LOG_INTEGRITY_PROFILE, KubernetesBindingError, KubernetesHTTP,
)
from .base import DraftArtifact, EvidenceDraft

NORMAL_OPTION = "normal_startup_load_only"
PROFILE = "kubernetes-owned-normal-startup-load/1"
_CHECK_PROFILE = "supply-boot-stage-budget-v1"
_LIMIT = 65536
_SHA = re.compile(r"[0-9a-f]{64}")
_LOG_TIME = re.compile(rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z")
_SOURCE_FILES = frozenset({"v1/worker/gpu_model_runner.py", "utils/mem_utils.py", "platforms/cuda.py",
                           "logger.py", "logging_utils/formatter.py"})
_SOURCE_KEYS = {"source_root", "file_sha256", "logger_call_linenumber", "entrypoint", "required_env", "optional_env", "forbidden_args"}
_COST_LINE = re.compile(rb"(?:\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\) )?INFO "
                        rb"[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
                        rb"\[gpu_model_runner\.py:([0-9]{1,5})\] Model loading took "
                        rb"(-?[0-9]{1,4}\.[0-9]{1,2}) GiB memory and ([0-9]{1,6}\.[0-9]{6}) seconds")
_MISSING = ["resolve_fetch_verify_stage_costs", "compile_stage_cost", "cpu_memory_disk_and_peak_resources",
            "total_budget_and_enforcement", "stop_and_cleanup", "independent_worker_measurements"]


def _stamp(value):
    if type(value) is not str or not _LOG_TIME.fullmatch(value.encode("ascii")):
        raise ValueError("LIF01_BOOT_TIMESTAMP_INVALID")
    return datetime.fromisoformat(value)


def _transport(target, env, *, duration, binary, runner):
    if (type(target) is not dict or target.get("lab") is not False or target.get("synthetic") is True
            or not supports(env.engine.name, env.engine.version, "log_integrity")
            or (env.runtime.tp_size, env.runtime.pp_size, env.runtime.dp_size) != (1, 1, 1)):
        raise ValueError("LIF01_BOOT_TARGET_UNVERIFIED")
    return KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner, profile=LOG_INTEGRITY_PROFILE)


def normal_startup_load_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Validate a sealed selection and the bounded prefix selector without I/O."""
    transport = _transport(target, env, duration=duration, binary=binary, runner=runner)
    prefix = target.get("startup_log_prefix")
    if (type(prefix) is not dict or set(prefix) != {"byte_count", "sha256", "container_started_at", "ready_at"}
            or type(prefix["byte_count"]) is not int or not 0 < prefix["byte_count"] < _LIMIT
            or type(prefix["sha256"]) is not str or not _SHA.fullmatch(prefix["sha256"])
            or prefix["container_started_at"] != transport.binding["engine"]["started_at"]
            or _stamp(prefix["ready_at"]) < _stamp(prefix["container_started_at"])):
        raise ValueError("LIF01_BOOT_PREFIX_UNVERIFIED")
    return transport


def _ready_window(transport):
    pod = transport._verify(capture_snapshot=True)["engine"]
    engine = transport.binding["engine"]
    selected, = [row for row in pod["spec"]["containers"] if row["name"] == engine["container"]]
    condition, = [row for row in pod["status"]["conditions"] if row.get("type") == "Ready"]
    started_at, ready_at = engine["started_at"], condition["lastTransitionTime"]
    if engine["restart_count"] != 0 or not _stamp(started_at) <= _stamp(ready_at) <= utcnow():
        raise ValueError("LIF01_BOOT_WINDOW_UNVERIFIED")
    return {"container_started_at": started_at, "ready_at": ready_at}, selected


def _read_logs(transport, window, cap):
    result = transport.engine_logs(window["container_started_at"], cap)
    raw, capture = result.get("stdout_bytes"), result.get("capture") or {}
    if (result.get("returncode") != 0 or type(raw) is not bytes or not 0 < len(raw) <= cap
            or not raw.endswith(b"\n") or capture.get("complete") is not True
            or capture.get("bounded_at_source") is not True or capture.get("truncated") is not False
            or capture.get("timed_out") is not False):
        raise ValueError("LIF01_BOOT_LOG_INCOMPLETE")
    return raw


def capture_startup_log_prefix(target, env, *, duration=15, binary="kubectl", runner=None):
    """Scope a later product read before the driver allows ordinary requests.

    This selector supplies no observations or evidence origin. Both the current
    same-owner source and fixed producer files must still verify in the EVD run.
    """
    try:
        transport = _transport(target, env, duration=duration, binary=binary, runner=runner)
        window, _ = _ready_window(transport)
        raw = _read_logs(transport, window, _LIMIT)
        after, _ = _ready_window(transport)
        if window != after or len(raw) == _LIMIT:
            raise ValueError
        return {"byte_count": len(raw), "sha256": sha256_hex(raw), **window}
    except (AttributeError, KubernetesBindingError, KeyError, TypeError, ValueError):
        raise ValueError("LIF01_BOOT_PREFIX_UNVERIFIED") from None


def _source_profile(adapter, env):
    source = adapter.startup_load_source(env)
    if (type(source) is not dict or set(source) != _SOURCE_KEYS
            or type(source["source_root"]) is not str or not source["source_root"].startswith("/")
            or ".." in PurePosixPath(source["source_root"]).parts
            or type(source["file_sha256"]) is not dict or source["file_sha256"].keys() != _SOURCE_FILES
            or any(type(value) is not str or not _SHA.fullmatch(value) for value in source["file_sha256"].values())
            or type(source["logger_call_linenumber"]) is not int or not 1 <= source["logger_call_linenumber"] <= 99999
            or type(source["entrypoint"]) is not list or not source["entrypoint"]
            or any(type(field) is not str or not field.startswith("/") for field in source["entrypoint"])
            or any(type(source[key]) is not dict for key in ("required_env", "optional_env"))
            or type(source["forbidden_args"]) is not list
            or any(type(arg) is not str or not arg.startswith("--") for arg in source["forbidden_args"])):
        raise ValueError("LIF01_BOOT_SOURCE_UNQUALIFIED")
    return source


def _verify_launch(target, container, source):
    selected_env = {}
    settings = source["required_env"] | source["optional_env"]
    for row in container.get("env", []):
        name = row["name"]
        if name in {"PYTHONHOME", "PYTHONPATH", "PATH", "LD_PRELOAD", "LD_LIBRARY_PATH"}:
            raise ValueError("LIF01_BOOT_LAUNCH_UNQUALIFIED")
        if name in settings:
            if name in selected_env or row.get("value") != settings[name] or "valueFrom" in row:
                raise ValueError("LIF01_BOOT_LAUNCH_UNQUALIFIED")
            selected_env[name] = row["value"]
    protected = [PurePosixPath(path) for path in [source["source_root"], *source["entrypoint"]]]
    args = container.get("args")
    mounts = [PurePosixPath(row["mountPath"]) for row in container.get("volumeMounts", [])]
    if (container.get("command") != source["entrypoint"]
            or type(args) is not list or args != target["comparison_config"]["launch"]["argv"]
            or any(arg.partition("=")[0] in source["forbidden_args"] for arg in args)
            or container.get("envFrom") or container.get("tty", False) is not False
            or not source["required_env"].keys() <= selected_env.keys()
            or any(mount.is_relative_to(path) or path.is_relative_to(mount) for mount in mounts for path in protected)):
        raise ValueError("LIF01_BOOT_LAUNCH_UNQUALIFIED")


def _verify_source(transport, source):
    files = [{"root": 0, "relative": filename} for filename in sorted(_SOURCE_FILES)]
    rows = transport.engine_supply_hashes([source["source_root"]], files)
    observed = {row["relative"]: row["sha256"] for row in rows if row["state"] == "hashed"
                and type(row["root"]) is int and row["root"] == 0}
    if len(observed) != len(rows) or observed != source["file_sha256"]:
        raise ValueError("LIF01_BOOT_INSTALLED_SOURCE_MISMATCH")


def _load_cost(raw, window, line_number):
    started, ready = (_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    matches = []
    for row in raw.split(b"\n")[:-1]:
        if b"Model loading took " not in row:
            continue
        timestamp, separator, message = row.partition(b" ")
        if not separator or not _LOG_TIME.fullmatch(timestamp):
            raise ValueError("LIF01_BOOT_LOG_TIMESTAMP_UNAVAILABLE")
        event = _stamp(timestamp.decode("ascii"))
        if not started <= event <= ready:
            continue
        match = _COST_LINE.fullmatch(message)
        if match and int(match[1]) == line_number:
            matches.append((event, match))
    (event, match), = matches
    return {"stage": "load", "observed_at": event.isoformat(),
            "elapsed_us": int(Decimal(match[3].decode("ascii")) * 1000000),
            "reported_allocator_delta_centi_gib": int(Decimal(match[2].decode("ascii")) * 100),
            "allocator_delta_scope": "reported_cuda_torch_allocated_after_minus_before"}


class KubernetesNormalStartupLoadExecutor:
    name = "supply_boot"
    version = "0.1.0+k8s-normal-startup-load"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "LIF-01"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("LIF01_BOOT_SCOPE_INVALID")
        if type(values) is not dict or set(values) != {param.key for param in check.policy_params}:
            raise ValueError("LIF01_BOOT_POLICY_INVALID")
        for param in check.policy_params:
            check_policy_value(check.id, param.key, param.type, values[param.key])
        if ("load" not in values["supply_stage_budget_ms"]
                or values["supply_stage_budget_ms"].keys() != values["supply_stage_budget_bytes"].keys()):
            raise ValueError("LIF01_BOOT_POLICY_INVALID")

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 20)
        binary = self.cfg.get("kubectl_binary", "kubectl")
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            if target_sha != self._expected_target_sha256:
                raise ValueError
            source = _source_profile(adapter, env)
            transport = normal_startup_load_transport(target, env, duration=duration, binary=binary, runner=self.runner)
            source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                profile=ENGINE_CALLBACK_PROFILE, clock=transport.clock)
            source_reader.deadline = transport.deadline
        except (AttributeError, KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            raise ValueError("LIF01_BOOT_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = None
        failure = "LIF01_BOOT_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(transport)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_launch(target, container, source)
            failure = "LIF01_BOOT_INSTALLED_SOURCE_UNVERIFIED"
            _verify_source(source_reader, source)
            failure = "LIF01_BOOT_PREFIX_READ_UNVERIFIED"
            raw = _read_logs(transport, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "LIF01_BOOT_LOAD_REPORT_UNAVAILABLE"
            cost = _load_cost(raw, window, source["logger_call_linenumber"])
            failure = "LIF01_BOOT_OWNER_AFTER_UNVERIFIED"
            after, container = _ready_window(transport)
            _verify_launch(target, container, source)
            _verify_source(source_reader, source)
            if after == window and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                           for t in (transport, source_reader)):
                observed = {"role": "normal", "profile": PROFILE, "engine_reported_load_cost": cost}
        except (AttributeError, KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            pass

        cleanup = sorted(set(transport.report()["cleanup_errors"] + source_reader.report()["cleanup_errors"]))
        missing = _MISSING + ([] if observed else ["ordinary_model_load_report"])
        settings = {NORMAL_OPTION: True, "max_duration_seconds": duration, "boot_log_cap": _LIMIT,
                    "source_file_roles": sorted(_SOURCE_FILES)}
        conditions_sha = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                          if observed and target.get("comparison_config") else None)
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": transport.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source["file_sha256"] if observed else None,
                   "source_log_call_linenumber": source["logger_call_linenumber"] if observed else None,
                   "observation": observed, "conditions_sha256": conditions_sha, "missing": missing}
        error = None if observed else failure
        artifacts = []
        try:
            path = Path(workdir) / "startup_load_cost.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("LIF01_BOOT_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None,
                            "detail": "Ordinary load report only; complete startup resource budget not established."},
            challenges=[{"step": step, "attempted": index == 0 and observed is not None}
                        for index, step in enumerate(check.challenge_steps)],
            observations={"supply_boot_sessions": {"value": [observed] if observed else None, "collected": observed is not None},
                          "supply_boot_violations": {"value": None, "collected": False},
                          "supply_boot_missing": {"value": missing, "collected": True},
                          "supply_boot_complete": {"value": False, "collected": True},
                          "supply_boot_record_authenticated": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "source_scope": "ordinary_load"})),
                           "config_sha256": conditions_sha, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
