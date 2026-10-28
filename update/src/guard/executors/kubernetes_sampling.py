"""REQ-10 transport implementation; reuses its fixtures, observations and judge."""
from pathlib import Path
import json

from guard.models import DataOrigin
from guard.models.base import utcnow
from guard.runtime.kubernetes_http import KubernetesHTTP, PROFILE
from .http_common import Ctx
from .sampling_validation import SamplingValidationExecutor


class KubernetesSamplingExecutor(SamplingValidationExecutor):
    version = "0.2.1"

    def __init__(self, cfg, *, runner=None, environ=None):
        super().__init__(cfg, environ=environ)
        self._kubectl_runner = runner

    def run(self, check, env, policy_values, adapter, workdir):
        allowed = {"target", "constraint_source", "constraint_engine_version", "max_output_tokens", "max_total_requests",
                   "max_probe_tokens", "max_duration_seconds", "request_timeout", "kubectl_binary", "roles",
                   "wrong_key_env", "denied_key_env", "allow_mutating_routes"}
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
