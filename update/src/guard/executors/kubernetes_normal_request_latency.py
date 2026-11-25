"""LIF-03 client timing for one ordinary request on the selected owned engine."""

from pathlib import Path

from guard.adapters.engine_profiles import supports
from guard.core.select import check_policy_value
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state, validate_content_settings
from .kubernetes_normal_token_input import _normal_exchange

NORMAL_OPTION = "normal_request_latency_only"
PROFILE = "kubernetes-owned-normal-request-latency/1"
_CHECK_PROFILE = "kubernetes-stream-buffer-budget-observation-v1"
_PATH = "/v1/completions"
_LABEL = "func|POST_" + _PATH
_CLOCK = "batch_monotonic_client_connection_only"
_TIMES = ("dispatch_ms", "request_sent_ms", "headers_ms", "end_ms")
_UNCOLLECTED = ("normal_latency_policy_limit", "normal_throughput", "queue", "KV", "fairness", "concurrency",
               "process_rss", "send_wait", "request_resource_budgets", "media", "budget_recovery")


def normal_request_latency_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Admit the exact stock transport without dispatching a request."""
    if (type(target) is not dict or target.get("lab") is not False or target.get("synthetic") is True
            or not supports(env.engine.name, env.engine.version, "stream_buffer")):
        raise ValueError("LIF03_NORMAL_TARGET_UNVERIFIED")
    return KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner, profile=INPUT_VALIDATION_PROFILE)


def _client_timing(response, timeout):
    offsets = [response.get(key) for key in _TIMES]
    if (response.get("timing_basis") != _CLOCK or response.get("method") != "POST" or response.get("mode") != "json"
            or any(type(value) is not int or not 0 <= value <= timeout * 1000 for value in offsets)
            or offsets != sorted(offsets)):
        return None
    dispatch, sent, headers, finished = offsets
    return {"basis": _CLOCK, "offsets_ms": dict(zip(_TIMES, offsets, strict=True)),
            "client_elapsed_ms": finished - dispatch, "client_time_to_headers_ms": headers - dispatch,
            "client_sent_to_headers_ms": headers - sent}


class KubernetesNormalRequestLatencyExecutor:
    name = "stream_buffer"
    version = "0.1.0+k8s-normal-latency"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "LIF-03"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "request_timeout", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("LIF03_NORMAL_SCOPE_INVALID")
        validate_content_settings(self.cfg)
        if type(values) is not dict or set(values) != {param.key for param in check.policy_params}:
            raise ValueError("LIF03_NORMAL_POLICY_INVALID")
        for param in check.policy_params:
            check_policy_value(check.id, param.key, param.type, values[param.key])

    def _target_transport(self, target, env, *, duration, expected_target_sha256=None):
        target_sha = sha256_hex(canonical_json(target))
        pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
        if not pins or any(pin != target_sha for pin in pins):
            raise ValueError
        transport = normal_request_latency_transport(target, env, duration=duration,
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self.runner)
        return target_sha, transport

    def prepared_normal_request_latency(self, check, env, values, *, expected_target_sha256=None):
        """Validate the selected target and effective settings without building a request."""
        from guard.adapters.vllm_file_read_sensor import dependency

        self.validate_policy(check, values)
        try:
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            self._target_transport(target, env, duration=self.cfg.get("max_duration_seconds", 12),
                                   expected_target_sha256=expected_target_sha256)
        except (KubernetesBindingError, KeyError, OSError, OverflowError, TypeError, ValueError):
            raise ValueError("LIF03_NORMAL_TARGET_UNVERIFIED") from None

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        timeout = self.cfg.get("request_timeout", 3)
        duration = self.cfg.get("max_duration_seconds", 12)
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            if adapter is None:
                raise ValueError
            target_sha, transport = self._target_transport(target, env, duration=duration)
            body = {"model": transport.api_model, "prompt": "ping", "max_tokens": 1}
            transport.declare_input_contract({"path": _PATH, "control": body, "prompt_tokens": None, "fields": {}})
        except (KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            raise ValueError("LIF03_NORMAL_TARGET_UNVERIFIED") from None

        response, payload, capture, errors = _normal_exchange(transport, _PATH, _LABEL, body, timeout)
        cleanup = list(errors)
        observed = None
        if payload and control_state(response, transport.api_model, _PATH) == "pass":
            usage = payload["usage"]
            if (len(payload["choices"]) == 1 and usage["completion_tokens"] == 1
                    and usage["total_tokens"] == usage["prompt_tokens"] + 1):
                observed = _client_timing(response, timeout)

        complete = observed is not None and not cleanup
        step = {"scope": "ordinary_completion_client_latency", "method": "POST", "path": _PATH,
                "outcome": capture["outcome"], "client_timing": observed}
        fixture_hash = sha256_hex(canonical_json({"profile": PROFILE, "request_body_sha256": capture["request_body_sha256"]}))
        settings = {NORMAL_OPTION: True, "request_timeout": timeout, "max_duration_seconds": duration,
                    "probe_source_sha256": capture["probe_source_sha256"], "timing_basis": _CLOCK}
        conditions_hash = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                           if complete and target.get("comparison_config") else None)
        missing = list(_UNCOLLECTED) + ([] if complete else ["ordinary_completion_client_timing"])
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "capture": capture, "observation": step if complete else None, "missing": missing,
                   "conditions_sha256": conditions_hash}
        error = None if complete else "LIF03_NORMAL_OBSERVATION_INCOMPLETE"
        artifacts = []
        try:
            path = Path(workdir) / "normal_request_latency.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("LIF03_NORMAL_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": True, "succeeded": True if complete else None,
                            "detail": "One ordinary completion and its client-observed connection interval."},
            challenges=[{"step": item, "attempted": False} for item in check.challenge_steps],
            observations={"activation_steps": {"value": [step] if complete else None, "collected": complete},
                          "activation_violations": {"value": None, "collected": False},
                          "activation_missing": {"value": missing, "collected": True},
                          "activation_scope_complete": {"value": False, "collected": True}},
            comparability={"fixture_sha256": fixture_hash, "config_sha256": conditions_hash,
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu} if not error else {},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
