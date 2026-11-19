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
    _read_logs, _ready_window, _source_profile, _stamp, _verify_launch, _verify_source,
    normal_startup_load_transport as normal_graph_capture_transport,
)

NORMAL_OPTION = "normal_graph_capture_only"
PROFILE = "kubernetes-owned-normal-graph-capture/1"
_CHECK_PROFILE = "engine-sensor-graph-lifetime-v1"
_PROGRESS_TITLE = b"Capturing CUDA graphs (decode, FULL)"
_RETURN_TITLE = b"Graph capturing finished"
_SKIP_TITLE = b"Skipping CUDA graph capture"
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


def _capture_callsite(adapter, env):
    method = getattr(adapter, "graph_capture_source", None)
    profile = (method(env) if callable(method) else
               get_provider(env.engine.name).evidence_contract("graph_capture_source", env))
    key = "capture_completion_call_linenumber"
    if type(profile) is not dict or profile.keys() != {key} or type(profile[key]) is not int or not 1 <= profile[key] <= 99999:
        raise ValueError("REQ20_CAPTURE_SOURCE_UNQUALIFIED")
    return profile[key]


def _capture_observation(raw, window, callsite):
    started, ready = (_stamp(window[key]) for key in ("container_started_at", "ready_at"))
    last_progress = previous = expected_total = None
    returned = False
    for row in raw.split(b"\n")[:-1]:
        if not any(marker in row for marker in (_PROGRESS_TITLE, _RETURN_TITLE, _SKIP_TITLE)):
            continue
        timestamp, _, message = row.partition(b" ")
        at = _stamp(timestamp.decode("ascii"))
        if not started <= at <= ready or returned or _SKIP_TITLE in message:
            raise ValueError("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
        if _RETURN_TITLE in message:
            match = _RETURN.fullmatch(message)
            if (not match or int(match["line"]) != callsite or last_progress is None
                    or at < last_progress or previous != expected_total):
                raise ValueError("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
            returned = True
            continue
        for frame in message.split(b"\r"):
            if not frame:
                continue
            progress = _PROGRESS.fullmatch(frame)
            if not progress:
                raise ValueError("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            completed, total, percent = (int(progress[key]) for key in ("completed", "total", "percent"))
            if not (0 <= completed <= total and total > 0 and 0 <= percent <= 100
                    and (percent == 100) == (completed == total)):
                raise ValueError("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            if previous is None:
                if completed != 0 or percent != 0:
                    raise ValueError("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
                expected_total = total
            elif total != expected_total or completed == 0 or completed < previous or at < last_progress:
                raise ValueError("REQ20_CAPTURE_PROGRESS_UNVERIFIED")
            previous, last_progress = completed, at
    if not returned:
        raise ValueError("REQ20_CAPTURE_LOG_SEQUENCE_UNVERIFIED")
    return {"mode": "decode_full", "reported_completed_descriptor_iterations": expected_total,
            "native_capture_return_report_observed": True}


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
            callsite = _capture_callsite(adapter, env)
            logs = normal_graph_capture_transport(target, env, duration=duration, binary=binary, runner=self.runner)
            source_reader = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=self.runner,
                                            profile=ENGINE_CALLBACK_PROFILE, clock=logs.clock)
            source_reader.deadline = logs.deadline
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            raise ValueError("REQ20_CAPTURE_TARGET_OR_SOURCE_UNVERIFIED") from None

        prefix = target["startup_log_prefix"]
        observed = None
        failure = "REQ20_CAPTURE_OWNER_BEFORE_UNVERIFIED"
        try:
            window, container = _ready_window(logs)
            if any(prefix[key] != value for key, value in window.items()):
                raise ValueError
            _verify_launch(target, container, source)
            failure = "REQ20_CAPTURE_INSTALLED_SOURCE_UNVERIFIED"
            _verify_source(source_reader, source)
            failure = "REQ20_CAPTURE_PREFIX_UNVERIFIED"
            raw = _read_logs(logs, window, prefix["byte_count"])
            if len(raw) != prefix["byte_count"] or sha256_hex(raw) != prefix["sha256"]:
                raise ValueError
            failure = "REQ20_CAPTURE_NATIVE_REPORT_UNVERIFIED"
            capture = _capture_observation(raw, window, callsite)
            failure = "REQ20_CAPTURE_OWNER_AFTER_UNVERIFIED"
            after, container = _ready_window(logs)
            _verify_launch(target, container, source)
            _verify_source(source_reader, source)
            if after == window and not any(t.report()["cleanup_errors"] or t.report()["collection_gap"]
                                           for t in (logs, source_reader)):
                observed = capture
        except (AttributeError, KeyError, OSError, TypeError, ValueError, KubernetesBindingError):
            pass

        cleanup = sorted({item for t in (logs, source_reader) for item in t.report()["cleanup_errors"]})
        settings = {"profile": PROFILE, "max_duration_seconds": duration, "baseline_mode": "decode_full"}
        conditions = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                      if observed and target.get("comparison_config") else None)
        error = None if observed else failure
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": logs.report()["binding_sha256"], "fixed_boot_prefix": prefix,
                   "installed_source_sha256": source["file_sha256"] if observed else None,
                   "capture_completion_call_linenumber": callsite if observed else None,
                   "observation": observed, "conditions_sha256": conditions, "gap": error,
                   "missing": ["actual_graph_handle_and_descriptor_contents", "current_input_refresh_and_replay",
                               "output_consumption_and_buffer_lifetime", "replay_functional_control"]}
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
