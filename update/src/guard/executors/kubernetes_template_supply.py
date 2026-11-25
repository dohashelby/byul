"""Owned Kubernetes template snapshots and ordinary API input projections."""
from copy import deepcopy

from guard.adapters.engine_profiles import executor_feature, require
from guard.adapters.vllm_file_read_sensor import dependency
from guard.automation_policy import validate_template_policy
from guard.collectors.file import load_bounded_json
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from .content_common import append_artifact
from .http_common import Ctx, HttpCheckExecutor
from . import template_supply_approval as supply


SUP13_NORMAL_OPTION = "sup13_normal_only"
_SUP13_PROFILE = "supplied-template-type-boundary-v1"
_TEMPLATE_SETTINGS = {"target", "kubectl_binary", "request_timeout", "endpoint", "tokenize_endpoint", *supply.DEFAULTS}


class KubernetesTemplateSupplyExecutor(HttpCheckExecutor):
    name = "template_validation"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False
    functional_via = "engine"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.b_cpu_support_input = deepcopy(cfg.get('cpu_support_input'))
        self._has_cpu_support_input = 'cpu_support_input' in cfg
        super().__init__({k: v for k, v in cfg.items() if k != 'cpu_support_input'}, environ=environ)
        if expected_target_sha256 is not None and (type(expected_target_sha256) is not str
                or len(expected_target_sha256) != 64
                or any(c not in "0123456789abcdef" for c in expected_target_sha256)):
            raise ValueError("KUBERNETES_TEMPLATE_CONFIGURATION_INVALID")
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def preview_reuse_inputs(self, check, env, values, adapter):
        # Static declarations never prove the current selected file/Pod state.
        return None

    def _validate_sup13_settings(self):
        if (self.cfg.get(SUP13_NORMAL_OPTION) is not True or self._has_cpu_support_input
                or set(self.cfg) - (_TEMPLATE_SETTINGS | {SUP13_NORMAL_OPTION})):
            raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID")
        limits = supply.settings(self.cfg)
        # A nonempty tokenization is counted once on each leg, plus one chat output token.
        if limits["max_total_requests"] < 2 or limits["max_total_probe_tokens"] < 3:
            raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID")

    def validate_policy(self, check, values):
        if SUP13_NORMAL_OPTION in self.cfg:
            if (check.id != "SUP-13" or check.executor != self.name or check.collection_profile != _SUP13_PROFILE
                    or type(values) is not dict or set(values) != {"forbidden_template_attributes"}
                    or type(values["forbidden_template_attributes"]) is not list
                    or any(type(attr) is not str for attr in values["forbidden_template_attributes"])):
                raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID")
            self._validate_sup13_settings()
            supply.validate_policy(check, values, self.cfg, sup13_normal=True)
            return
        if check.id == "REQ-07" and self.cfg.get("req07_normal_only") is True:
            if (getattr(check, "collection_profile", None) is not None or self.b_cpu_support_input is not None
                    or "supply_reads_input" in self.cfg
                    or type(values) is not dict
                    or set(values) != {"allowed_template_sha256", "max_rendered_input_tokens", "request_template_override_allowed"}):
                raise ValueError("KUBERNETES_TEMPLATE_NORMAL_POLICY_UNSUPPORTED")
            validate_template_policy(values)
            supply.validate_policy(check, values, self.cfg)
            return
        if check.id != "SUP-03" or getattr(check, "collection_profile", None) != supply.PROFILE:
            raise ValueError("KUBERNETES_TEMPLATE_PROFILE_UNSUPPORTED")
        if "req07_normal_only" in self.cfg:
            raise ValueError("KUBERNETES_TEMPLATE_NORMAL_CONFIGURATION_INVALID")
        supply.validate_policy(check, values, self.cfg)

    def run(self, check, env, policy_values, adapter, workdir):
        self.validate_policy(check, policy_values)
        if check.id == "SUP-13":
            self.version = "0.1.0+k8s-sup13-normal"
            ctx = self._sup13_context(check, env, policy_values, adapter, workdir)
            draft = self._capture(ctx)
            if not draft.normal_control.succeeded:
                if draft.data_origin == DataOrigin.MEASURED:
                    draft.data_origin = DataOrigin.DECLARED
                draft.status = ExecStatus.ABORTED
                if draft.abort_reason is None:
                    draft.abort_reason = "KUBERNETES_SUP13_NORMAL_OBSERVATION_UNAVAILABLE"
            return draft
        self.version = "0.1.0+k8s-normal" if check.id == "REQ-07" else "0.1.0"
        try:
            allowed = _TEMPLATE_SETTINGS | {"supply_reads_input", "req07_normal_only"}
            if type(self.cfg) is not dict or set(self.cfg) - allowed or adapter is None:
                raise ValueError
            target = load_bounded_json(self.cfg["target"])
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_TEMPLATE_CONFIGURATION_INVALID") from None
        return self._capture(self._context(check, env, policy_values, adapter, target, workdir))

    def prepared_sup13_normal(self, check, env, values, adapter, *, expected_target_sha256=None):
        if check.id != "SUP-13":
            raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID")
        self.validate_policy(check, values)
        return self.prepared_sup13_target(env, adapter, expected_target_sha256=expected_target_sha256)

    def prepared_sup13_target(self, env, adapter, *, expected_target_sha256):
        """Recheck the declared stock target against an independently bound pin."""
        self._validate_sup13_settings()
        ctx = self._sup13_context(None, env, {}, adapter, None,
                                  expected_target_sha256=expected_target_sha256)
        return ctx.cprobe.target_sha256

    def _sup13_context(self, check, env, values, adapter, workdir, *, expected_target_sha256=None):
        pins = [pin for pin in (self._expected_target_sha256, expected_target_sha256) if pin is not None]
        try:
            if not pins or adapter is None:
                raise ValueError
            require(env.engine.name, env.engine.version, executor_feature(self.name))
            target = strict_json(dependency(self.cfg["target"], None), limit=65536)
            if (type(target) is not dict or target.get("lab") is not False
                    or target.get("synthetic", False) is not False
                    or target.get("engine_kind") == "stub"):
                raise ValueError
            digest = sha256_hex(canonical_json(target))
            command = target["engine"]["command"]
            if (any(pin != digest for pin in pins) or type(command) is not list or not command
                    or any(type(arg) is not str or not arg or len(arg) > 256 for arg in command)):
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID") from None
        try:
            ctx = self._context(check, env, values, adapter, target, workdir)
        except KubernetesBindingError as exc:
            raise ValueError(str(exc)) from None
        if supply._target(ctx, kubernetes=True) is None:
            raise ValueError("KUBERNETES_SUP13_NORMAL_CONFIGURATION_INVALID")
        return ctx

    def _context(self, check, env, policy_values, adapter, target, workdir):
        transport = KubernetesHTTP(target, env, duration=supply.settings(self.cfg)["max_duration_seconds"],
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=REQUEST_BINDING_PROFILE)
        return Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
            timeout=float(self.cfg.get("request_timeout", 3.0)), allow_mutating=False, keys={}, secrets=[],
            cprobe=transport, workdir=workdir, started=utcnow())

    def _capture(self, ctx):
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(ctx.cprobe.report()["cleanup_errors"])
        if ctx.cprobe.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        if getattr(ctx, "_template_fixture_sha256", None) is not None:
            draft.comparability.fixture_sha256 = ctx._template_fixture_sha256
        append_artifact(draft, ctx, "template_samples.json", f"{self.name}/{self.version}")
        from .common_consumption import attach
        from .committed_cpu_support import attach as attach_cpu_support
        return attach_cpu_support(attach(draft, ctx, self.cfg, kind="supply-reads"),
                                  ctx.check, self.b_cpu_support_input, ctx.workdir)

    def collect(self, ctx):
        if ctx.check.id == "SUP-13":
            original, normal, challenges, summary = supply.collect(self, ctx, kubernetes=True, sup13_normal=True)
            observations = {obs.key: {"value": None, "collected": False}
                            for obs in ctx.check.required_observations}
            observations["functional_probes_planned"] = original["functional_probes_planned"]
            observations["template_sandbox_scope_complete"] = {"value": False, "collected": True}
            transport = summary["kubernetes"]
            if (original["template_identity_verified"]["value"] is True
                    and "TEMPLATE_NORMAL_IDENTITY_CHANGED_BEFORE_INFERENCE" not in summary["collection_errors"]
                    and not transport["collection_gap"] and not transport["cleanup_errors"]):
                observations["template_identity_verified"] = original["template_identity_verified"]
            if normal["succeeded"]:
                observations["functional_probe_results"] = original["functional_probe_results"]
            return observations, normal, challenges, summary
        if ctx.check.id != "REQ-07":
            return supply.collect(self, ctx, kubernetes=True)

        original, normal, challenges, summary = supply.collect(
            self, ctx, kubernetes=True, normal_input_limit=ctx.policy_values["max_rendered_input_tokens"])
        count = summary["tokenize_response_token_count"]
        verified = original["template_identity_verified"]["value"]
        exact = original["input_projection_exact_match"]["value"]
        observations = {obs.key: {"value": None, "collected": False} for obs in ctx.check.required_observations}
        for key in ("selected_template_sha256", "template_identity_verified", "functional_probes_planned",
                    "functional_probe_results"):
            observations[key] = original[key]
        observations["rendered_input_token_counts"] = {
            "value": {"normal": count} if count is not None and verified else None,
            "collected": count is not None and verified,
        }
        observations["rendered_prompt_tokens_exact_match"] = {"value": True if exact else None, "collected": exact}
        observations["collection_complete"] = {"value": False, "collected": True}
        return observations, normal, challenges, summary
