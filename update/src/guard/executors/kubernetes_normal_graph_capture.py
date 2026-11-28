"""REQ-20's normal CUDA graph capture baseline from an owned boot prefix."""

from pathlib import Path
import re

from guard.adapters.engine_provider import get_provider
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_normal_startup_load import (
    _read_logs, _ready_window, _source_profile, _verify_launch, _verify_source, _window_stamp,
    normal_startup_load_transport as normal_graph_capture_transport,
)

NORMAL_OPTION = "normal_graph_capture_only"
PROFILE = "kubernetes-owned-normal-graph-capture/1"
_CHECK_PROFILE = "engine-sensor-graph-lifetime-v1"
_PROGRESS_TITLE = b"Capturing CUDA graphs (decode, FULL)"
_RETURN_TITLE = b"Graph capturing finished"
_SKIP_TITLE = b"Skipping CUDA graph capture"
_ENGINE_CORE_PREFIX = re.compile(rb"\(EngineCore pid=[1-9][0-9]{0,8}\) ")
_GRAPH_OVERRIDES = {"--enforce-eager", "--compilation-config", "-cc", "--optimization-level",
                    "--cudagraph-capture-sizes", "--max-cudagraph-capture-size"}
# CR can re-render progress without opening a new CRI line. The bar can be ASCII or UTF-8.
_PROGRESS = re.compile(
    rb"Capturing CUDA graphs \(decode, FULL\): +(?P<percent>[0-9]{1,3})%\|"
    rb"[^|\r\n\x00-\x1f]{1,120}\| *(?P<completed>[0-9]{1,6})/(?P<total>[0-9]{1,6}) "
    rb"\[[0-9:<>., ?a-zA-Z/]{1,64}\] *"
)
_RETURN = re.compile(
    rb"(?P<process>\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\) )?"
    rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
    rb"\[gpu_model_runner\.py:(?P<line>[0-9]{1,5})\] Graph capturing finished in "
    rb"[0-9]{1,9} secs, took -?[0-9]{1,6}\.[0-9]{2} GiB"
)


class _CaptureReportGap(ValueError):
    """Closed parser diagnostics safe to include in the capture summary."""


def _capture_callsite(adapter, env):
    method = getattr(adapter, "graph_capture_source", None)
    profile = (method(env) if callable(method) else
               get_provider(env.engine.name).evidence_contract("graph_capture_source", env))
    key = "capture_completion_call_linenumber"
    if type(profile) is not dict or profile.keys() != {key} or type(profile[key]) is not int or not 1 <= profile[key] <= 99999:
        raise ValueError("REQ20_CAPTURE_SOURCE_UNQUALIFIED")
    return profile[key]


def _verify_graph_argv(args):
    if (type(args) is not list or any(type(arg) is not str for arg in args)
            or any(arg.partition("=")[0].partition(".")[0].replace("_", "-") in _GRAPH_OVERRIDES
                   for arg in args if arg.startswith("-"))):
        raise ValueError("REQ20_CAPTURE_LAUNCH_UNQUALIFIED")


def _verify_graph_launch(target, container, source):
    _verify_launch(target, container, source)
    _verify_graph_argv(container["args"])


def _capture_observation(raw, window, callsite):
    started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    last_progress = previous = expected_total = None
    progress_process = None
    returned = False
    for row in raw.split(b"\n")[:-1]:
        if not any(marker in row for marker in (_PROGRESS_TITLE, _RETURN_TITLE, _SKIP_TITLE)):
            continue
        timestamp, _, message = row.partition(b" ")
        try:
            at = _window_stamp(timestamp.decode("ascii"))
        except ValueError:
            raise _CaptureReportGap("REQ20_CAPTURE_LOG_TIMESTAMP_UNVERIFIED") from None
        if not started <= at <= ready or returned or _SKIP_TITLE in message:
            raise _CaptureReportGap("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
        if _RETURN_TITLE in message:
            match = _RETURN.fullmatch(message)
            if (not match or int(match["line"]) != callsite or last_progress is None
                    or (progress_process is not None and match["process"] != progress_process)
                    or at < last_progress or previous != expected_total):
                raise _CaptureReportGap("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
            returned = True
            continue
        frames = message.split(b"\r")
        # EngineCore can label the physical line once, before tqdm's first CR.
        process = frames.pop(0) if _ENGINE_CORE_PREFIX.fullmatch(frames[0]) else None
        if previous is not None and process != progress_process:
            raise _CaptureReportGap("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
        progress_process = process
        for frame in frames:
            if not frame:
                continue
            progress = _PROGRESS.fullmatch(frame)
            if not progress:
                raise _CaptureReportGap("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            completed, total, percent = (int(progress[key]) for key in ("completed", "total", "percent"))
            if not (0 <= completed <= total and total > 0 and 0 <= percent <= 100
                    and (percent == 100) == (completed == total)):
                raise _CaptureReportGap("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            if previous is None:
                if completed != 0 or percent != 0:
                    raise _CaptureReportGap("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
                expected_total = total
            elif total != expected_total or completed == 0 or completed < previous or at < last_progress:
                raise _CaptureReportGap("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            previous, last_progress = completed, at
    if not returned:
        raise _CaptureReportGap("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
    return {"mode": "decode_full", "reported_completed_descriptor_iterations": expected_total,
            "native_capture_return_report_observed": True}


def _capture_return_report(raw, window, callsite):
    """Retain a unique reviewed return line independently of the progress display."""
    rows = raw.split(b"\n")[:-1]
    if any(_SKIP_TITLE in row for row in rows):
        return None
    try:
        row, = (line for line in rows if _RETURN_TITLE in line)
        timestamp, _, message = row.partition(b" ")
        time_text = timestamp.decode("ascii")
        at = _window_stamp(time_text)
        started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    except ValueError:
        return None
    matched = _RETURN.fullmatch(message)
    if not matched or int(matched["line"]) != callsite or not started <= at <= ready:
        return None
    reported_at = time_text.removesuffix("Z") + "+00:00" if at[1] else at[0].isoformat()
    return {"reported_at": reported_at, "log_line_sha256": sha256_hex(row)}


class KubernetesNormalGraphCaptureExecutor:
    name = "engine_sensor"
    version = "0.1.1+k8s-normal-graph-capture"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "REQ-20"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]
                or type(values) is not dict or values or check.policy_params):
            raise ValueError("REQ20_CAPTURE_SCOPE_INVALID")

    def _target_inputs(self, target, env, adapter, *, duration, expected_target_sha256=None):
        target_sha = sha256_hex(canonical_json(target))
        pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
        if not pins or any(pin != target_sha for pin in pins):
            raise ValueError
        source = _source_profile(adapter, env)
        callsite = _capture_callsite(adapter, env)
        binary = self.cfg.get("kubectl_binary", "kubectl")
        logs = normal_graph_capture_transport(target, env, duration=duration, binary=binary, runner=self.runner)
        source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                        profile=ENGINE_CALLBACK_PROFILE, clock=logs.clock)
        source_reader.deadline = logs.deadline
        return target_sha, source, callsite, logs, source_reader

    def prepared_normal_graph_capture(self, check, env, values, *, expected_target_sha256=None):
        """Validate the selected boot-prefix declaration and reviewed source without collection."""
        from guard.adapters.vllm_file_read_sensor import dependency

        self.validate_policy(check, values)
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            adapter = get_provider(env.engine.name).adapter()
            _, source, _, _, _ = self._target_inputs(
                target, env, adapter, duration=self.cfg.get("max_duration_seconds", 20),
                expected_target_sha256=expected_target_sha256,
            )
            args = target["comparison_config"]["launch"]["argv"]
            _verify_graph_argv(args)
            if any(arg.partition("=")[0].replace("_", "-") in source["forbidden_args"]
                   for arg in args if arg.startswith("--")):
                raise ValueError("LIF01_BOOT_LAUNCH_UNQUALIFIED")
        except (AttributeError, KeyError, OSError, OverflowError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED") from None

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        duration = self.cfg.get("max_duration_seconds", 20)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha, source, callsite, logs, source_reader = self._target_inputs(target, env, adapter, duration=duration)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = None
        report_diagnostic = retained_return = None
        failure = "REQ20_CAPTURE_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(logs)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_graph_launch(target, container, source)
            failure = "REQ20_CAPTURE_INSTALLED_SOURCE_UNVERIFIED"
            _verify_source(source_reader, source)
            failure = "REQ20_CAPTURE_PREFIX_UNVERIFIED"
            raw = _read_logs(logs, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "REQ20_CAPTURE_NATIVE_REPORT_UNVERIFIED"
            capture = diagnostic = return_report = None
            try:
                capture = _capture_observation(raw, window, callsite)
                failure = "REQ20_CAPTURE_OWNER_AFTER_UNVERIFIED"
            except _CaptureReportGap as exc:
                diagnostic = str(exc)
                return_report = _capture_return_report(raw, window, callsite)
            after, container = _ready_window(logs)
            _verify_graph_launch(target, container, source)
            _verify_source(source_reader, source)
            if after == window and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                           for t in (logs, source_reader)):
                observed = capture
                report_diagnostic = diagnostic
                retained_return = return_report
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass

        cleanup = sorted({item for t in (logs, source_reader) for item in t.report()["cleanup_errors"]})
        settings = {"profile": PROFILE, "max_duration_seconds": duration, "baseline_mode": "decode_full"}
        conditions = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                      if observed and target.get("comparison_config") else None)
        error = None if observed else failure
        source_verified_report = observed is not None or retained_return is not None
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": logs.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source["file_sha256"] if source_verified_report else None,
                   "capture_completion_call_linenumber": callsite if source_verified_report else None,
                   "observation": observed, "conditions_sha256": conditions, "gap": error,
                   "missing": ["actual_graph_handle_and_descriptor_contents", "current_input_refresh_and_replay",
                               "output_consumption_and_buffer_lifetime", "replay_functional_control"]}
        if report_diagnostic is not None:
            summary["native_report_diagnostic"] = report_diagnostic
        if retained_return is not None:
            summary["native_capture_return_report"] = retained_return
        artifacts = []
        try:
            path = Path(workdir) / "normal_graph_capture.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("REQ20_CAPTURE_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(logs.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": False, "succeeded": None, "detail": "Normal replay control was not performed."},
            challenges=[{"step": step, "attempted": index == 0 and error is None}
                        for index, step in enumerate(check.challenge_steps)],
            observations={"engine_sensor_violation": {"value": None, "collected": False},
                          "engine_sensor_full_scope": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": PROFILE, "scope": "decode_full"})),
                           "config_sha256": conditions, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
