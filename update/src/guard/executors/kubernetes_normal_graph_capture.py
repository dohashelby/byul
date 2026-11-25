"""REQ-20's normal CUDA graph capture baseline from an owned boot prefix."""

from decimal import Decimal
from pathlib import Path
import re

from guard.adapters.engine_provider import get_provider
from guard.adapters.vllm_file_read_sensor import dependency
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import ENGINE_CALLBACK_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .kubernetes_normal_startup_load import (
    _read_logs, _ready_window, _source_profile, _verify_launch, _verify_source, _window_stamp,
    normal_startup_load_transport,
)

NORMAL_OPTION = "normal_graph_capture_only"
PROFILE = "kubernetes-owned-normal-graph-capture/1"
TP2_PROFILE = "kubernetes-owned-normal-graph-capture-tp2/1"
_RETURN_SITE = "capture_completion_call_linenumber"
_POOL_SITE = "warmup_pool_call_linenumber"
_PROFILING_SITE = "profiling_completion_call_linenumber"
_PROFILE_HELPER_SHA = "profiling_helper_sha256"
_PROFILE_HELPER = "v1/worker/gpu/cudagraph_utils.py"
_CHECK_PROFILE = "engine-sensor-graph-lifetime-v1"
_PROGRESS_TITLE = b"Capturing CUDA graphs (decode, FULL)"
_RETURN_TITLE = b"Graph capturing finished"
_SKIP_TITLE = b"Skipping CUDA graph capture"
_GRAPH_OVERRIDES = {"--enforce-eager", "--compilation-config", "-cc", "--optimization-level",
                    "--cudagraph-capture-sizes", "--max-cudagraph-capture-size"}
# CR can re-render progress without opening a new CRI line. The bar can be ASCII or UTF-8.
_PROGRESS = re.compile(
    rb"Capturing CUDA graphs \(decode, FULL\): +(?P<percent>[0-9]{1,3})%\|"
    rb"[^|\r\n\x00-\x1f]{1,120}\| *(?P<completed>[0-9]{1,6})/(?P<total>[0-9]{1,6}) "
    rb"\[[0-9:<>., ?a-zA-Z/]{1,64}\] *"
)
_RETURN = re.compile(
    rb"(?:\([A-Za-z0-9_-]{1,40} pid=[1-9][0-9]{0,8}\) )?"
    rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
    rb"\[gpu_model_runner\.py:(?P<line>[0-9]{1,5})\] Graph capturing finished in "
    rb"[0-9]{1,9} secs, took -?[0-9]{1,6}\.[0-9]{2} GiB"
)

_RANKED_EVENT = re.compile(
    rb"\((?P<worker>[A-Za-z0-9_-]{1,40}) pid=(?P<pid>[1-9][0-9]{0,8})\) "
    rb"INFO [0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2} "
    rb"\[(?P<file>model_runner|gpu_worker)\.py:(?P<line>[0-9]{1,5})\] (?P<message>[^\r\n]*)"
)
_RANKED_RETURN = re.compile(rb"Graph capturing finished in [0-9]{1,9} secs, took "
                            rb"(?P<gib>-?[0-9]{1,6}\.[0-9]{2}) GiB")
_POOL_REPORT = re.compile(
    rb"CUDA graph pool memory: (?P<actual>-?[0-9]{1,6}\.[0-9]{1,2}) GiB \(actual\), "
    rb"[0-9]{1,6}\.[0-9]{1,2} GiB \(estimated\), difference: "
    rb"[0-9]{1,6}\.[0-9]{1,2} GiB \([0-9]{1,16}\.[0-9]%\)\."
)

_PROFILING_COMPLETE = re.compile(rb"CUDA graph memory profiling is enabled \(default since v0\.21\.0\)\. .+")


class _CaptureReportGap(ValueError):
    """Closed parser diagnostics safe to include in the capture summary."""


def _capture_profile(adapter, env):
    method = getattr(adapter, "graph_capture_source", None)
    profile = (method(env) if callable(method) else
               get_provider(env.engine.name).evidence_contract("graph_capture_source", env))
    ranked = env.runtime.tp_size == 2
    sites = {_RETURN_SITE, _POOL_SITE, _PROFILING_SITE} if ranked else {_RETURN_SITE}
    keys = sites | {_PROFILE_HELPER_SHA} if ranked else sites
    if (type(profile) is not dict or profile.keys() != keys
            or any(type(profile[key]) is not int or not 1 <= profile[key] <= 99999 for key in sites)
            or ranked and (type(profile[_PROFILE_HELPER_SHA]) is not str
                           or not re.fullmatch(r"[0-9a-f]{64}", profile[_PROFILE_HELPER_SHA]))):
        raise ValueError("REQ20_CAPTURE_SOURCE_UNQUALIFIED")
    return profile


def normal_graph_capture_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Validate the existing single-worker or reviewed default TP2 boot declaration."""
    if env.runtime.tp_size == 2:
        _capture_profile(None, env)
    return normal_startup_load_transport(target, env, duration=duration, binary=binary,
                                         runner=runner, allow_ranked=True)


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
                    or at < last_progress or previous != expected_total):
                raise _CaptureReportGap("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
            returned = True
            continue
        for frame in message.split(b"\r"):
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
        at = _window_stamp(timestamp.decode("ascii"))
        started, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    except ValueError:
        return None
    matched = _RETURN.fullmatch(message)
    if not matched or int(matched["line"]) != callsite or not started <= at <= ready:
        return None
    return {"reported_at": at[0].isoformat(), "log_line_sha256": sha256_hex(row)}


def _ranked_capture_observation(raw, window, callsites, names):
    """Pair profiling and serving warmup returns with the worker pool report."""
    start, ready = (_window_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    return_site = (b"model_runner", callsites[_RETURN_SITE])
    pool_site = (b"gpu_worker", callsites[_POOL_SITE])
    profiling_site = (b"gpu_worker", callsites[_PROFILING_SITE])
    patterns = {return_site: _RANKED_RETURN, profiling_site: _PROFILING_COMPLETE, pool_site: _POOL_REPORT}
    by_worker = {worker.encode("ascii"): [] for worker in names}
    for row in raw.split(b"\n")[:-1]:
        stamp, _, message = row.partition(b" ")
        event = _RANKED_EVENT.fullmatch(message)
        if not event:
            continue
        site = event["file"], int(event["line"])
        if site not in patterns:
            continue
        match = patterns[site].fullmatch(event["message"])
        try:
            at = _window_stamp(stamp.decode("ascii"))
        except ValueError:
            raise _CaptureReportGap("REQ20_CAPTURE_LOG_TIMESTAMP_UNVERIFIED") from None
        if match is None or event["worker"] not in by_worker or not start <= at <= ready:
            raise _CaptureReportGap("REQ20_CAPTURE_RANK_REPORT_UNVERIFIED")
        receipt = {"log_timestamp": stamp.decode("ascii"), "log_line_sha256": sha256_hex(row)}
        by_worker[event["worker"]].append((site, at, event["pid"], match, receipt))

    ranks, pids = [], set()
    for rank, events in enumerate(by_worker.values()):
        if [row[0] for row in events] != [return_site, profiling_site, return_site, pool_site]:
            raise _CaptureReportGap("REQ20_CAPTURE_RANK_REPORT_UNVERIFIED")
        sample, profiled, warmup, pool = events
        if (not sample[1] < profiled[1] < warmup[1] < pool[1]
                or not sample[2] == profiled[2] == warmup[2] == pool[2] or pool[2] in pids
                # The profiling estimate can extrapolate beyond its initial capture sample.
                or Decimal(warmup[3]["gib"].decode("ascii")) != Decimal(pool[3]["actual"].decode("ascii"))):
            raise _CaptureReportGap("REQ20_CAPTURE_RANK_REPORT_UNVERIFIED")
        pids.add(pool[2])
        ranks.append({"reported_rank": rank, "profile_capture": sample[4], "profiling_completion": profiled[4],
                      "warmup_capture": warmup[4], "warmup_pool_report": pool[4]})
    return {"mode": "ranked_profile_and_warmup", "native_capture_return_report_observed": True,
            "reported_rank_capture": ranks, "worker_identity_verified": False}


class KubernetesNormalGraphCaptureExecutor:
    name = "engine_sensor"
    version = "0.1.0+k8s-normal-graph-capture"

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
        callsites = _capture_profile(adapter, env)
        if _PROFILE_HELPER_SHA in callsites:
            source = {**source, "file_sha256": {**source["file_sha256"],
                                               _PROFILE_HELPER: callsites[_PROFILE_HELPER_SHA]}}
        binary = self.cfg.get("kubectl_binary", "kubectl")
        logs = normal_graph_capture_transport(target, env, duration=duration, binary=binary, runner=self.runner)
        source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                        profile=ENGINE_CALLBACK_PROFILE, clock=logs.clock)
        source_reader.deadline = logs.deadline
        return target_sha, source, callsites, logs, source_reader

    def prepared_normal_graph_capture(self, check, env, values, *, expected_target_sha256=None):
        """Validate the selected boot-prefix declaration and reviewed source without collection."""
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
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            target_sha, source, callsites, logs, source_reader = self._target_inputs(target, env, adapter, duration=duration)
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        ranked = _POOL_SITE in callsites
        profile = TP2_PROFILE if ranked else PROFILE
        callsite = callsites[_RETURN_SITE]
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
                if ranked:
                    capture = _ranked_capture_observation(raw, window, callsites, source["ranked_load"]["process_names"])
                else:
                    capture = _capture_observation(raw, window, callsite)
            except _CaptureReportGap as exc:
                diagnostic = str(exc)
                if not ranked:
                    return_report = _capture_return_report(raw, window, callsite)
            failure = "REQ20_CAPTURE_OWNER_AFTER_UNVERIFIED"
            after, container = _ready_window(logs)
            _verify_graph_launch(target, container, source)
            _verify_source(source_reader, source)
            if after == window and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                           for t in (logs, source_reader)):
                if capture is None:
                    failure = "REQ20_CAPTURE_NATIVE_REPORT_UNVERIFIED"
                observed = capture
                report_diagnostic = diagnostic
                retained_return = return_report
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass

        cleanup = sorted({item for t in (logs, source_reader) for item in t.report()["cleanup_errors"]})
        mode = "ranked_profile_and_warmup" if ranked else "decode_full"
        settings = {"profile": profile, "max_duration_seconds": duration, "baseline_mode": mode}
        conditions = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                      if observed and target.get("comparison_config") else None)
        error = None if observed else failure
        source_verified_report = observed is not None or retained_return is not None
        summary = {"profile": profile, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": logs.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source["file_sha256"] if source_verified_report else None,
                   "capture_completion_call_linenumber": callsite if source_verified_report else None,
                   "observation": observed, "conditions_sha256": conditions, "gap": error,
                   "missing": ["actual_graph_handle_and_descriptor_contents", "current_input_refresh_and_replay",
                               "output_consumption_and_buffer_lifetime", "replay_functional_control"]}
        if ranked and source_verified_report:
            summary[_POOL_SITE] = callsites[_POOL_SITE]
            summary[_PROFILING_SITE] = callsites[_PROFILING_SITE]
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
            comparability={"fixture_sha256": sha256_hex(canonical_json({"profile": profile, "scope": mode})),
                           "config_sha256": conditions, "model_inventory_sha256": env.model.inventory_sha256,
                           "gpu": env.runtime.gpu_model, "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
