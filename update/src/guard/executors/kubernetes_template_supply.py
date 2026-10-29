"""SUP03 consumes existing Kubernetes HTTP/template reads; no new transport."""
from pathlib import Path

from guard.models import DataOrigin, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from .content_common import append_artifact
from .http_common import Ctx, HttpCheckExecutor
from . import template_supply_approval as supply


class KubernetesTemplateSupplyExecutor(HttpCheckExecutor):
    name = "template_validation"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg, environ=environ)
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str
                or len(expected_target_sha256) != 64
                or any(c not in "0123456789abcdef" for c in expected_target_sha256)):
            raise ValueError("KUBERNETES_TEMPLATE_CONFIGURATION_INVALID")
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def preview_reuse_inputs(self, check, env, values, adapter):
        # Static declarations never prove the current selected file/Pod state.
        return None

    def validate_policy(self, check, values):
        if check.id != "SUP-03" or getattr(check, "collection_profile", None) != supply.PROFILE:
            raise ValueError("KUBERNETES_TEMPLATE_PROFILE_UNSUPPORTED")
        supply.validate_policy(check, values, self.cfg)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        try:
            allowed = {"target", "kubectl_binary", "request_timeout", "endpoint", "tokenize_endpoint", *supply.DEFAULTS}
            if type(self.cfg) is not dict or set(self.cfg) - allowed or adapter is None:
                raise ValueError
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_TEMPLATE_CONFIGURATION_INVALID") from None
        transport = KubernetesHTTP(target, env, duration=supply.settings(self.cfg)["max_duration_seconds"],
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=REQUEST_BINDING_PROFILE)
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
            timeout=float(self.cfg.get("request_timeout", 3.0)), allow_mutating=False, keys={}, secrets=[],
            cprobe=transport, workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        if getattr(ctx, "_template_fixture_sha256", None) is not None:
            draft.comparability.fixture_sha256 = ctx._template_fixture_sha256
        append_artifact(draft, ctx, "template_samples.json", f"{self.name}/{self.version}")
        return draft

    def collect(self, ctx):
        return supply.collect(self, ctx, kubernetes=True)
