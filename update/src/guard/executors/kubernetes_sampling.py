"""REQ-10 transport implementation; reuses its fixtures, observations and judge."""
from pathlib import Path
import json
import math

from guard.adapters.engine_profiles import supports
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.models.base import utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP, PROFILE
from guard.runtime.response_evidence import complete_body
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state
from .http_common import Ctx
from .kubernetes_observation import _read_snapshot_target
from .response_projection import capture_metadata
from .sampling_validation import SamplingValidationExecutor

NORMAL_OPTION = "normal_sampling_only"
NORMAL_PROFILE = "kubernetes-owned-normal-sampling/1"
NORMAL_PATH = "/v1/completions"
NORMAL_LABEL = "func|POST_" + NORMAL_PATH
NORMAL_OPTIONS = {"temperature": 0.5, "top_p": 0.9, "max_tokens": 1, "min_tokens": 0}
NORMAL_FIXTURE = {"model": "{model}", "prompt": "guard sampling control", "max_tokens": 1}


class KubernetesSamplingExecutor(SamplingValidationExecutor):
    version = "0.2.2"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        if self.cfg.get(NORMAL_OPTION) is True:
            return {}
        return super().keys()

    def validate_configuration(self, check):
        if NORMAL_OPTION not in self.cfg:
            return super().validate_configuration(check)
        if (self.cfg[NORMAL_OPTION] is not True or check.id != "REQ-10" or check.executor != self.name
                or getattr(check, "collection_profile", None) is not None
                or set(self.cfg) - {"target", NORMAL_OPTION, "kubectl_binary", "request_timeout", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]
                or len(check.functional_probes) != 1):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_SCOPE_INVALID")
        probe, = check.functional_probes
        timeout = self.cfg.get("request_timeout", 3)
        if (probe.via != "engine" or probe.path != NORMAL_PATH or probe.method != "POST" or probe.expect != [200]
                or canonical_json(probe.body) != canonical_json(NORMAL_FIXTURE)
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_SCOPE_INVALID")

    def validate_policy(self, check, values):
        if NORMAL_OPTION not in self.cfg:
            return super().validate_policy(check, values)
        self.validate_configuration(check)
        if type(values) is not dict or set(values) != {"allowed_sampling_options", "rejection_codes"}:
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_POLICY_INVALID")
        options, codes = values["allowed_sampling_options"], values["rejection_codes"]
        if (type(options) is not list or any(type(key) is not str or key not in NORMAL_OPTIONS for key in options)
                or len(set(options)) != len(options) or "max_tokens" not in options
                or type(codes) is not list or not codes or any(code not in ("400", "422") for code in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_POLICY_INVALID")

    def _normal_transport(self, target, env, *, expected_target_sha256=None):
        if (not supports(env.engine.name, env.engine.version, "sampling") or type(target) is not dict
                or target["lab"] is not False or target.get("synthetic") is True):
            raise ValueError
        target_sha = sha256_hex(canonical_json(target))
        pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
        if not pins or any(pin != target_sha for pin in pins):
            raise ValueError
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 120),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=INPUT_VALIDATION_PROFILE)
        return target_sha, transport

    def prepared_normal_sampling(self, check, env, values, *, expected_target_sha256=None):
        """Validate the saved normal control declaration without building or dispatching a request."""
        if self.cfg.get(NORMAL_OPTION) is not True:
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_SCOPE_INVALID")
        self.validate_policy(check, values)
        try:
            target = _read_snapshot_target(self.cfg["target"])
            self._normal_transport(target, env, expected_target_sha256=expected_target_sha256)
        except (KeyError, TypeError, ValueError, OSError, OverflowError, KubernetesBindingError):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_TARGET_UNVERIFIED") from None

    def _run_normal(self, check, env, values, adapter, workdir, *, normal_response_observer=None):
        self.validate_policy(check, values)
        started = utcnow()
        try:
            target = _read_snapshot_target(self.cfg["target"])
            if adapter is None:
                raise ValueError
            target_sha, transport = self._normal_transport(target, env)
            options = values["allowed_sampling_options"]
            body = {"model": transport.api_model, "prompt": NORMAL_FIXTURE["prompt"],
                    **{key: NORMAL_OPTIONS[key] for key in options}}
            transport.declare_input_contract({"path": NORMAL_PATH, "control": body, "prompt_tokens": None, "fields": {}})
        except (KeyError, TypeError, ValueError, OSError, KubernetesBindingError):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_TARGET_UNVERIFIED") from None
        request = {"id": NORMAL_LABEL, "mode": "json", "method": "POST",
                   "url": target["engine"]["trusted_url"] + NORMAL_PATH, "headers": {}, "body": body}
        response = {}
        try:
            response, = transport("kubernetes-probe", [request], self.cfg.get("request_timeout", 3))
        except KubernetesBindingError:
            pass
        report = transport.report()
        capture = capture_metadata(response)
        reports = report["requests"]
        bound = bool(len(reports) == 1 and reports[0]["id"] == NORMAL_LABEL
                     and reports[0]["binding_verified_before_after"] is True
                     and all(response.get(key) == request[key] for key in ("id", "method", "mode"))
                     and reports[0]["outcome"] == response.get("outcome"))
        response_bytes = complete_body(response) if bound else None
        complete = bool(response_bytes is not None and capture["original_json_state"] == "valid"
                        and capture.get("original_body_bytes") == response.get("body_bytes")
                        and capture.get("original_body_sha256") == response.get("body_sha256"))
        normal = False
        if complete and control_state(response, transport.api_model, NORMAL_PATH) == "pass":
            try:
                payload = strict_json(response_bytes, limit=131072)
                normal = len(payload["choices"]) == 1 and payload["usage"]["completion_tokens"] == NORMAL_OPTIONS["max_tokens"]
            except (KeyError, TypeError, ValueError):
                normal = False
        cleanup = list(report["cleanup_errors"])
        error = None if normal and not report["collection_gap"] and not cleanup else "SAMPLING_NORMAL_OBSERVATION_INCOMPLETE"
        observations = {
            "sampling_options_planned": (options, True), "sampling_options_tested": (None, False),
            "sampling_challenge_outcomes": (None, False), "sampling_accepted_options": (None, False),
            "functional_probes_planned": ([NORMAL_LABEL], True),
            "functional_probe_results": ({NORMAL_LABEL: "pass"} if normal else None, normal),
            "sampling_health_results": (None, False), "runner_consumption_verified": (False, True),
        }
        summary = {"profile": NORMAL_PROFILE, "target_sha256": target_sha,
                   "probe_source_sha256": report["probe_source_sha256"], "binding_sha256": report["binding_sha256"],
                   "request_body_sha256": sha256_hex(canonical_json(body)), "supplied_sampling_options": options,
                   "http_dispatch_observed": True if bound and type(response.get("outcome")) is int else None,
                   "ownership_verified_before_after": bound, "outcome": response.get("outcome") if bound else None,
                   "response_capture": capture if bound else None, "normal_completion_shape_verified": normal,
                   "uncollected": ["sampling_limits", "runner_consumption", "rejections", "structured_output", "tool_selection"]}
        artifacts = []
        summary_bytes = canonical_json(summary)
        try:
            path = Path(workdir) / "normal_sampling.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(summary_bytes)
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup.append("SAMPLING_NORMAL_ARTIFACT_UNAVAILABLE")
            error = error or "SAMPLING_NORMAL_ARTIFACT_UNAVAILABLE"
        origin = DataOrigin(transport.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        if normal_response_observer is not None and error is None:
            normal_response_observer(
                body_bytes=response_bytes,
                summary_sha256=sha256_hex(summary_bytes),
                environment_ref=env.ref().model_dump(mode="json"),
                data_origin=origin.value,
            )
        comparability = {}
        if not error and type(target.get("comparison_config")) is dict and target["comparison_config"]:
            fixture = {"profile": NORMAL_PROFILE, "request_body_sha256": summary["request_body_sha256"]}
            settings = {"profile": NORMAL_PROFILE, "request_timeout": self.cfg.get("request_timeout", 3),
                        "max_duration_seconds": self.cfg.get("max_duration_seconds", 120),
                        "probe_source_sha256": report["probe_source_sha256"]}
            comparability = {
                "fixture_sha256": sha256_hex(canonical_json(fixture)),
                "config_sha256": sha256_hex(canonical_json(execution_conditions(target, settings, []))),
                "model_inventory_sha256": env.model.inventory_sha256, "gpu": env.runtime.gpu_model,
                "driver": env.runtime.driver, "cpu": env.runtime.cpu,
            }
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": True, "succeeded": normal if bound else None,
                            "detail": "One in-policy collection attempt. HTTP dispatch is recorded only with a verified response."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations={key: {"value": value, "collected": collected} for key, (value, collected) in observations.items()},
            artifacts=artifacts, comparability=comparability, config={NORMAL_OPTION: True, "target_sha256": target_sha},
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)

    def run(self, check, env, policy_values, adapter, workdir, *, normal_response_observer=None):
        """For an owned normal trial, expose the verified response in memory only."""
        if normal_response_observer is not None and (
            self.cfg.get(NORMAL_OPTION) is not True or not callable(normal_response_observer)
        ):
            raise ValueError("KUBERNETES_SAMPLING_NORMAL_OBSERVER_INVALID")
        if NORMAL_OPTION in self.cfg:
            return self._run_normal(check, env, policy_values, adapter, workdir,
                                    normal_response_observer=normal_response_observer)
        allowed = {"target", "constraint_source", "constraint_engine_version", "max_output_tokens", "max_total_requests",
                   "max_probe_tokens", "max_duration_seconds", "request_timeout", "kubectl_binary", "roles",
                   "wrong_key_env", "denied_key_env", "allow_mutating_routes", "runner_input", "runner_capture"}
        try:
            if set(self.cfg) - allowed or self.cfg.get("allow_mutating_routes", False) is not False:
                raise ValueError
            self.validate_policy(check, policy_values)
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_SAMPLING_CONFIGURATION_INVALID") from None
        if adapter is None or env.engine.version != self.cfg["constraint_engine_version"]:
            raise ValueError("KUBERNETES_SAMPLING_ENV_UNAVAILABLE")
        transport = KubernetesHTTP(target, env, duration=self.cfg["max_duration_seconds"],
                                   binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner)
        if "runner_capture" not in self.cfg:
            target["probe_image"] = transport.binding["probe"]["image"]
        keys = self.keys()
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                  timeout=float(self.cfg.get("request_timeout", 3)), allow_mutating=False, keys=keys,
                  secrets=[v for v in keys.values() if v], cprobe=transport, workdir=workdir, started=utcnow(),
                  http_probe_profile="http-probe/1")
        draft = self._finish(ctx)
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft

    def collect(self, ctx):
        observations, normal, challenges, summary = super().collect(ctx)
        report = ctx.cprobe.report()
        verified = {row["id"] for row in report["requests"] if row["binding_verified_before_after"]}
        tested = [option for option in observations["sampling_options_tested"]["value"]
                  if f"sampling-{option}-challenge" in verified]
        observations["sampling_options_tested"]["value"] = tested
        for name in ("sampling_challenge_outcomes",):
            values = {key: value for key, value in observations[name]["value"].items() if key in tested}
            observations[name] = {"value": values, "collected": bool(values)}
        accepted = observations["sampling_accepted_options"]
        accepted["value"] = [option for option in accepted["value"] if option in tested]
        accepted["collected"] = bool(tested)
        for challenge in challenges:
            challenge["attempted"] = len(tested) == len(ctx.policy_values["allowed_sampling_options"])
        summary["request_dispatch_attempts"] = summary.pop("requests_sent")
        summary["fixture"].update(transport_profile=PROFILE,
                                  probe_source_sha256=report["probe_source_sha256"])
        summary["kubernetes"] = report
        summary["limitations"].append("Kubernetes transport changes no REQ-10 verdict meaning; security consumption remains UNKNOWN.")
        return observations, normal, challenges, summary
