"""OBS-02 observation of owned Pod readiness during ordinary serving."""

from datetime import datetime
from pathlib import Path

from guard.adapters.engine_profiles import supports
from guard.automation_policy import string_list
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_functional import LOGICAL_ID
from guard.runtime.kubernetes_http import REQUEST_BINDING_PROFILE, KubernetesBindingError, KubernetesHTTP
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state, validate_content_settings

NORMAL_OPTION = "normal_serving_readiness_only"
PROFILE = "kubernetes-owned-normal-serving-readiness/1"
_CHECK_PROFILE = "kubernetes-observation-state-composite-v1"
_COMPONENT = "http_listener"
_CONTROL_PATH = "/v1/completions"


def normal_serving_readiness_transport(target, env, *, duration, binary="kubectl", runner=None):
    """Admit the reviewed stock transport without dispatching or asserting Ready."""
    if (type(target) is not dict or target.get("lab") is not False or target.get("synthetic") is True
            or not supports(env.engine.name, env.engine.version, "observation_state")):
        raise ValueError("OBS02_NORMAL_TARGET_UNVERIFIED")
    return KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner, profile=REQUEST_BINDING_PROFILE)


def _owned_ready(transport):
    # The existing snapshot projection omits readinessProbe. Read it from the
    # same identity-verified in-memory document and keep only the selected state.
    pod = transport._verify(capture_snapshot=True)["engine"]
    name = transport.binding["engine"]["container"]
    container, = [row for row in pod["spec"]["containers"] if row["name"] == name]
    state, = [row for row in pod["status"]["containerStatuses"] if row["name"] == name]
    condition, = [row for row in pod["status"]["conditions"] if row.get("type") == "Ready"]
    readiness = container["readinessProbe"]
    probe = readiness["httpGet"]
    if ({"exec", "tcpSocket", "grpc"} & readiness.keys()
            or type(probe) is not dict or set(probe) - {"path", "port", "scheme", "host", "httpHeaders"}
            or probe.get("path") != "/health" or probe.get("scheme", "HTTP") != "HTTP"
            or probe.get("host", "") != "" or probe.get("httpHeaders", []) != []
            or state.get("ready") is not True or condition.get("status") != "True"):
        raise ValueError("OBS02_HEALTH_READINESS_UNVERIFIED")
    port = probe["port"]
    if type(port) is str and port:
        selected, = [row for row in container.get("ports", []) if row.get("name") == port]
        if selected.get("protocol", "TCP") != "TCP":
            raise ValueError("OBS02_HEALTH_READINESS_UNVERIFIED")
        port = selected["containerPort"]
    if type(port) is not int or port != transport.endpoint.port:
        raise ValueError("OBS02_HEALTH_READINESS_UNVERIFIED")
    timing = {key: readiness[key] for key in ("initialDelaySeconds", "timeoutSeconds", "periodSeconds",
                                               "successThreshold", "failureThreshold") if key in readiness}
    if any(type(value) is not int or value < (0 if key == "initialDelaySeconds" else 1)
           for key, value in timing.items()):
        raise ValueError("OBS02_HEALTH_READINESS_UNVERIFIED")
    return {"observed_at": utcnow().isoformat(), "pod_ready": True, "engine_container_ready": True,
            "readiness_probe": {"method": "GET", "path": "/health", "scheme": "HTTP", "bound_engine_port": port,
                                "timing": timing}}


class KubernetesNormalServingReadinessExecutor:
    name = "observation_state"
    version = "0.1.0+k8s-normal-ready"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self.runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_policy(self, check, values):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "OBS-02"
                or check.executor != self.name or check.collection_profile != _CHECK_PROFILE
                or set(self.cfg) - {NORMAL_OPTION, "target", "kubectl_binary", "request_timeout", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("OBS02_NORMAL_SCOPE_INVALID")
        validate_content_settings(self.cfg)
        if (type(values) is not dict or set(values) != {"observation_state_required_components", "profiler_backend_kind",
                                                        "profiler_required_ranks"}
                or type(values["profiler_backend_kind"]) is not str or not values["profiler_backend_kind"]):
            raise ValueError("OBS02_NORMAL_POLICY_INVALID")
        if _COMPONENT not in string_list(values, "observation_state_required_components"):
            raise ValueError("OBS02_NORMAL_POLICY_INVALID")
        string_list(values, "profiler_required_ranks")

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            if adapter is None or target_sha != self._expected_target_sha256:
                raise ValueError
            transport = normal_serving_readiness_transport(target, env, duration=self.cfg.get("max_duration_seconds", 12),
                binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self.runner)
        except (KubernetesBindingError, KeyError, OSError, TypeError, ValueError):
            raise ValueError("OBS02_NORMAL_TARGET_UNVERIFIED") from None

        body = {"model": transport.api_model, "prompt": "ping", "max_tokens": 1}
        before = after = completion = None
        attempted = matched = False
        failure = "OBS02_NORMAL_BEFORE_READY_UNVERIFIED"
        try:
            before = _owned_ready(transport)
            failure = "OBS02_NORMAL_COMPLETION_UNVERIFIED"
            request = {"id": LOGICAL_ID, "method": "POST", "headers": {}, "body": body, "want_body": True,
                       "url": transport.endpoint.geturl() + _CONTROL_PATH}
            attempted = True
            response, = transport.normal_requests("kubernetes-probe", [request], self.cfg.get("request_timeout", 3))
            row, = transport.report()["requests"]
            if (row["id"] != LOGICAL_ID or row["binding_verified_before_after"] is not True
                    or row["capture_complete"] is not True or response.get("capture_complete") is not True
                    or type(response.get("outcome")) is not int or response["outcome"] != row["outcome"]):
                raise ValueError
            raw = response["body"].encode()
            payload = strict_json(raw, limit=131072)
            completion = {"route": _CONTROL_PATH, "outcome": row["outcome"], "response_bytes": len(raw),
                          "response_sha256": sha256_hex(raw), "started_at": row["started_at"], "finished_at": row["finished_at"]}
            if (control_state(response, transport.api_model, _CONTROL_PATH) != "pass" or len(payload["choices"]) != 1
                    or payload["usage"]["completion_tokens"] != 1
                    or payload["usage"]["total_tokens"] != payload["usage"]["prompt_tokens"] + 1):
                raise ValueError
            failure = "OBS02_NORMAL_AFTER_READY_UNVERIFIED"
            after = _owned_ready(transport)
            times = [before["observed_at"], completion["started_at"], completion["finished_at"], after["observed_at"]]
            instants = [datetime.fromisoformat(value) for value in times]
            matched = before["readiness_probe"] == after["readiness_probe"] and instants == sorted(instants)
        except (AttributeError, KubernetesBindingError, KeyError, TypeError, ValueError):
            pass

        report = transport.report()
        cleanup = list(report["cleanup_errors"])
        matched = matched and not report["collection_gap"] and not cleanup
        missing = [component for component in values["observation_state_required_components"]
                   if component != _COMPONENT or not matched]
        missing += ["startup_transition", "failure", "termination", "profiler"]
        step = {"component": _COMPONENT, "scope": "normal_serving_readiness_correspondence",
                "readiness_before": before, "ordinary_completion": completion, "readiness_after": after}
        fixture = {"profile": PROFILE, "request": {"method": "POST", "path": _CONTROL_PATH,
                    "body": {**body, "model": "{model}"}}}
        settings = {NORMAL_OPTION: True, "request_timeout": self.cfg.get("request_timeout", 3),
                    "max_duration_seconds": self.cfg.get("max_duration_seconds", 12),
                    "readiness_probe": before["readiness_probe"] if matched else None}
        conditions_sha = (sha256_hex(canonical_json(execution_conditions(target, settings, [])))
                          if matched and target.get("comparison_config") else None)
        summary = {"profile": PROFILE, "target_sha256": target_sha, "environment_sha256": env.ref().sha256,
                   "binding_sha256": report["binding_sha256"], "probe_source_sha256": report["probe_source_sha256"],
                   "request_body_sha256": sha256_hex(canonical_json(body)), "observed_scope": step,
                   "readiness_correspondence_observed": matched, "conditions_sha256": conditions_sha, "missing": missing}
        error = None if matched else failure
        artifacts = []
        try:
            path = Path(workdir) / "normal_serving_readiness.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("OBS02_NORMAL_ARTIFACT_UNAVAILABLE")
            error = error or cleanup[-1]
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": attempted, "succeeded": True if matched else None,
                            "detail": "Correspondence of Ready and ordinary serving on one stable Pod generation."},
            challenges=[{"step": item, "attempted": False} for item in check.challenge_steps],
            observations={"activation_steps": {"value": [step] if matched else None, "collected": matched},
                          "activation_violations": {"value": None, "collected": False},
                          "activation_missing": {"value": missing, "collected": True},
                          "activation_scope_complete": {"value": False, "collected": True}},
            comparability={"fixture_sha256": sha256_hex(canonical_json(fixture)), "config_sha256": conditions_sha,
                           "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                           "driver": env.runtime.driver, "cpu": env.runtime.cpu},
            config={NORMAL_OPTION: True, "target_sha256": target_sha}, artifacts=artifacts,
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)
