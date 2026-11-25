"""SUP-12's normal reader branch, observed in an owned boot prefix."""

from decimal import Decimal
from pathlib import Path
import re

from guard.adapters.engine_provider import get_provider
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_normal_startup_load import (
    _read_logs, _ready_window, _source_profile, _stamp, _verify_launch, normal_startup_load_transport,
)

NORMAL_OPTION = "normal_safetensors_reader_only"
PROFILE = "kubernetes-owned-normal-safetensors-reader/1"
_CHECK_PROFILE = "supply-boot-reader-v1"
_VALUES = {"supply_allowed_readers": ["safetensors"]}
_SOURCES = {"model_executor/model_loader/weight_utils.py", "model_executor/model_loader/default_loader.py"}
_CALLS = {"filesystem_call_linenumber", "default_strategy_call_linenumber", "loader_completion_call_linenumber"}
_SHA = re.compile(r"[0-9a-f]{64}")
_LOG_LINE = re.compile(rb"(?P<time>[0-9T:.Z-]{20,30}) "
                       rb"(?:(?P<process>\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\)) )?"
                       rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
                       rb"\[(?P<module>weight_utils|default_loader)\.py:(?P<line>[0-9]{1,5})\] (?P<message>[^\r\n]+)")
_FS_LABEL = rb"(unknown|[A-Z][A-Z0-9_-]{0,23})"
_FILESYSTEM = re.compile(rb"Filesystem type for checkpoints: " + _FS_LABEL + rb"\. "
                         rb"Checkpoint size: ([0-9]{1,6}\.[0-9]{2}) GiB\. Available RAM: [0-9]{1,6}\.[0-9]{2} GiB\.")
_READER_DEFAULT = re.compile(rb"Auto-prefetch is disabled because the filesystem \(" + _FS_LABEL + rb"\) "
                              rb"is not a recognized network FS \(NFS/Lustre\)\. If you want to force prefetching, "
                              rb"start vLLM with --safetensors-load-strategy=prefetch\.")
_LOADER_RETURN = re.compile(rb"Loading weights took [0-9]{1,6}\.[0-9]{2} seconds")
_ALTERNATE = (b"Loading safetensors checkpoint shards (eager)", b"Multi-thread loading shards",
              b"Loading safetensors using Runai Model Streamer", b"Loading safetensors using InstantTensor loader",
              b"Loading pt checkpoint shards", b"Multi-thread loading pt checkpoint shards", b"Loading np_cache checkpoint shards",
              b"safetensors_load_strategy='prefetch' was explicitly specified", b"Skipping auto-prefetch", b"Network filesystem (",
              b"Prefetching checkpoint files")
_FORBIDDEN_ARGS = {"--load-format", "--safetensors-load-strategy", "--model-loader-extra-config", "--quantization"}
_MISSING = ["direct_safe_open_call_and_file_identity", "file_format_magic_and_dependency_runtime_identity",
            "alternative_reader_and_fallback", "disallowed_format_or_objects", "reader_side_effects",
            "whole_model_and_message_reader_coverage"]


def normal_safetensors_reader_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Validate the closed same-boot selector without contacting the cluster."""
    return normal_startup_load_transport(target, env, duration=duration, binary=binary, runner=runner)


def _reader_profile(adapter, env):
    method = getattr(adapter, "safetensors_reader_source", None)
    profile = (method(env) if callable(method) else
               get_provider(env.engine.name).evidence_contract("safetensors_reader_source", env))
    if (type(profile) is not dict or profile.keys() != {"file_sha256", *_CALLS}
            or type(profile["file_sha256"]) is not dict or profile["file_sha256"].keys() != _SOURCES
            or any(type(sha) is not str or not _SHA.fullmatch(sha) for sha in profile["file_sha256"].values())
            or any(type(profile[key]) is not int or not 1 <= profile[key] <= 99999 for key in _CALLS)):
        raise ValueError("SUP12_READER_SOURCE_UNQUALIFIED")
    return profile


def _verify_reader_args(argv, forbidden):
    if any(arg.partition("=")[0].replace("_", "-") in forbidden for arg in argv if arg.startswith("--")):
        raise ValueError("SUP12_READER_LAUNCH_UNQUALIFIED")


def _verify_reader_launch(target, container, startup):
    _verify_launch(target, container, startup)
    _verify_reader_args(container["args"], _FORBIDDEN_ARGS)


def _verify_sources(transport, startup, profile):
    expected = {name: startup["file_sha256"][name] for name in ("logger.py", "logging_utils/formatter.py")}
    expected.update(profile["file_sha256"])
    files = [{"root": 0, "relative": name} for name in sorted(expected)]
    rows = transport.engine_supply_hashes([startup["source_root"]], files)
    observed = {row["relative"]: row["sha256"] for row in rows if row["state"] == "hashed"
                and type(row["root"]) is int and row["root"] == 0}
    if len(rows) != len(observed) or observed != expected:
        raise ValueError("SUP12_READER_SOURCE_MISMATCH")
    return observed


def _reader_observation(raw, window, profile):
    started, ready = (_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    events = {}
    markers = {
        (b"weight_utils", profile["filesystem_call_linenumber"]): ("filesystem", _FILESYSTEM),
        (b"weight_utils", profile["default_strategy_call_linenumber"]): ("strategy", _READER_DEFAULT),
        (b"default_loader", profile["loader_completion_call_linenumber"]): ("loader_return", _LOADER_RETURN),
    }
    for line in raw.split(b"\n")[:-1]:
        timestamp, _, _ = line.partition(b" ")
        if any(marker in line for marker in _ALTERNATE):
            if not timestamp or started <= _stamp(timestamp.decode("ascii")) <= ready:
                raise ValueError("SUP12_READER_CONTRADICTORY_BOOT_REPORT")
        log = _LOG_LINE.fullmatch(line)
        if log is None:
            continue
        event_at = _stamp(log["time"].decode("ascii"))
        location = (log["module"], int(log["line"]))
        if not started <= event_at <= ready or location not in markers:
            continue
        label, pattern = markers[location]
        match = pattern.fullmatch(log["message"])
        if not match or label in events:
            raise ValueError("SUP12_READER_BOOT_REPORT_UNVERIFIED")
        events[label] = event_at, log["process"], match.groups()

    if tuple(events) != ("filesystem", "strategy", "loader_return"):
        raise ValueError("SUP12_READER_BOOT_REPORT_UNVERIFIED")
    (fs_at, fs_process, (filesystem, checkpoint_size)) = events["filesystem"]
    (strategy_at, strategy_process, (strategy_filesystem,)) = events["strategy"]
    (returned_at, return_process, _) = events["loader_return"]
    size = int(Decimal(checkpoint_size.decode("ascii")) * 100)
    if (not fs_at <= strategy_at <= returned_at or fs_process != strategy_process or fs_process != return_process
            or not size or filesystem != strategy_filesystem or filesystem in {b"NFS", b"NFS4", b"LUSTRE"}):
        raise ValueError("SUP12_READER_BOOT_REPORT_UNVERIFIED")
    filesystem_reported = filesystem != b"unknown"
    return {"scope": "native_ordinary_safetensors_default_iterator",
            "observed_reader_family": "safetensors",
            "load_strategy_branch": ("automatic_local_no_prefetch" if filesystem_reported
                                     else "automatic_no_prefetch_filesystem_unreported"),
            "source_indicated_iterator_call": "safe_open", "direct_safe_open_call_observed": False,
            "filesystem_type": filesystem.decode("ascii") if filesystem_reported else None,
            "reported_checkpoint_size_centi_gib": size,
            "iterator_report_at": fs_at.isoformat(), "strategy_report_at": strategy_at.isoformat(),
            "loader_return_report_at": returned_at.isoformat(), "same_process_label_observed": fs_process is not None}


class KubernetesNormalSafetensorsReaderExecutor:
    name = "supply_boot"
    version = "0.1.0+k8s-normal-safetensors-reader"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "SUP-12"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"] or type(values) is not dict or values != _VALUES):
            raise ValueError("SUP12_READER_SCOPE_INVALID")

    def _target_sha256(self, target, expected_target_sha256=None):
        target_sha = sha256_hex(canonical_json(target))
        pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
        if not pins or any(pin != target_sha for pin in pins):
            raise ValueError
        return target_sha

    def _configured_readers(self, target, env):
        duration = self.cfg.get("max_duration_seconds", 20)
        binary = self.cfg.get("kubectl_binary", "kubectl")
        logs = normal_safetensors_reader_transport(target, env, duration=duration, binary=binary, runner=self.runner)
        source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                        profile=ENGINE_CALLBACK_PROFILE, clock=logs.clock)
        source_reader.deadline = logs.deadline
        return logs, source_reader

    def prepared_normal_reader(self, check, env, values, *, expected_target_sha256=None):
        """Check saved default-reader declarations without inspecting the live launch."""
        from guard.adapters.vllm_file_read_sensor import dependency

        self.validate_policy(check, values)
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            self._target_sha256(target, expected_target_sha256)
            adapter = get_provider(env.engine.name).adapter()
            startup = _source_profile(adapter, env)
            _reader_profile(adapter, env)
            argv = target["comparison_config"]["launch"]["argv"]
            if type(argv) is not list or any(type(arg) is not str for arg in argv):
                raise ValueError
            _verify_reader_args(argv, _FORBIDDEN_ARGS | set(startup["forbidden_args"]))
            self._configured_readers(target, env)
        except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("SUP12_READER_TARGET_OR_SOURCE_UNVERIFIED") from None

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 20)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = self._target_sha256(target)
            startup = _source_profile(adapter, env)
            profile = _reader_profile(adapter, env)
            logs, source_reader = self._configured_readers(target, env)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("SUP12_READER_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = source_hashes = None
        failure = "SUP12_READER_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(logs)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_reader_launch(target, container, startup)
            failure = "SUP12_READER_INSTALLED_SOURCE_UNVERIFIED"
            source_hashes = _verify_sources(source_reader, startup, profile)
            failure = "SUP12_READER_PREFIX_UNVERIFIED"
            raw = _read_logs(logs, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "SUP12_READER_BOOT_REPORT_UNVERIFIED"
            reader = _reader_observation(raw, window, profile)
            failure = "SUP12_READER_OWNER_AFTER_UNVERIFIED"
            after, container = _ready_window(logs)
            _verify_reader_launch(target, container, startup)
            if (after == window and _verify_sources(source_reader, startup, profile) == source_hashes
                    and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"] for t in (logs, source_reader))):
                observed = {"role": "normal", "profile": PROFILE, "reader": reader}
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass

        cleanup = sorted({item for t in (logs, source_reader) for item in t.report()["cleanup_errors"]})
        missing = _MISSING + ([] if observed else ["ordinary_boot_reader_selection"])
        if observed and reader["filesystem_type"] is None:
            missing.append("checkpoint_filesystem_type")
        settings = {"profile": PROFILE, "max_duration_seconds": duration}
        conditions = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                      if observed and target.get("comparison_config") else None)
        error = None if observed else failure
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": logs.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source_hashes if observed else None,
                   "observation": observed, "conditions_sha256": conditions, "missing": missing, "gap": error}
        artifacts = []
        try:
            path = Path(workdir) / "normal_safetensors_reader.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("SUP12_READER_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(logs.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None,
                            "detail": "Observed normal safetensors iterator branch; full reader control untested."},
            challenges=[{"step": step, "attempted": index == 0 and observed is not None}
                        for index, step in enumerate(check.challenge_steps)],
            observations={"supply_boot_sessions": {"value": [observed] if observed else None, "collected": observed is not None},
                          "supply_boot_violations": {"value": None, "collected": False},
                          "supply_boot_missing": {"value": missing, "collected": True},
                          "supply_boot_complete": {"value": False, "collected": True},
                          "supply_boot_record_authenticated": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "source_scope": "ordinary_reader"})),
                           "config_sha256": conditions, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
