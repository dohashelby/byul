"""REQ-08·09·SUP-08 transport implementation; reuses input_validation fixtures, observations and judge."""
from copy import deepcopy
from pathlib import Path
import json

from guard.adapters.engine_profiles import supports
from guard.models import DataOrigin, canonical_json, sha256_hex
from guard.models.base import utcnow
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesHTTP
from . import request_field_probe
from .http_common import Ctx, fill_model, request_model
from .input_validation import InputValidationExecutor

_SETTINGS = {"target", "kubectl_binary", "request_timeout", "max_duration_seconds", "vocab_size", "vocab_source",
             "max_probe_tokens", "reused_token_ids", "runner_input", "code_module_root", "code_module_repository",
             "code_marker_root", "unapproved_revision", "control_image"}


class _WithBody:
    """Preserve executor IDs while using the fixed probe's space-free labels."""

    def __init__(self, transport):
        self.transport = transport

    def __call__(self, network, requests, timeout):
        original_ids = {request["id"].replace(" ", "_"): request["id"] for request in requests}
        if len(original_ids) != len(requests):
            raise ValueError("KUBERNETES_INPUT_REQUEST_ID_COLLISION")
        prepared = [{**request, "id": request["id"].replace(" ", "_"), "want_body": True}
                    for request in requests]
        return [{**row, "id": original_ids[row["id"]]}
                for row in self.transport(network, prepared, timeout)]

    def __getattr__(self, name):
        return getattr(self.transport, name)


def _contract(check, values, cfg, model):
    probe, = check.functional_probes
    control = fill_model(deepcopy(probe.body), model)
    fields, prompt_tokens = {}, None
    if request_field_probe.supported(check):
        if check.collection_profile == request_field_probe.REUSED_TOKEN_PROFILE:
            control["return_token_ids"] = True
            fields[values["reused_token_field"]] = list(cfg["reused_token_ids"])
        else:
            control = request_field_probe.control_body(control, cfg)
            fields = request_field_probe.nested_fields(values["code_selection_fields"],
                                                       request_field_probe.challenge_revision(cfg))
    else:
        # The token-id case sends vocab_size itself; the length case sends one more than the policy limit.
        prompt_tokens = {"max_length": values["max_input_tokens"] + 1, "max_value": cfg["vocab_size"]}
    return {"path": probe.path, "control": control, "prompt_tokens": prompt_tokens, "fields": fields}


class KubernetesInputValidationExecutor(InputValidationExecutor):
    version = "0.7.1+k8s.1"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        super().__init__(cfg)
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def run(self, check, env, policy_values, adapter, workdir):
        try:
            if type(self.cfg) is not dict or set(self.cfg) - _SETTINGS or adapter is None:
                raise ValueError
            self.validate_policy(check, policy_values)
            # native_input opens the transport only. Token/length and reused-token checks also need a
            # release whose catalog row lists request_input_checks; code selection needs native_input alone.
            if (getattr(check, "collection_profile", None) != request_field_probe.CODE_SELECTION_PROFILE
                    and not supports(env.engine.name, env.engine.version, "request_input_checks")):
                raise ValueError
            target = json.loads(Path(self.cfg["target"]).read_text(encoding="utf-8"))
            if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target)) != self._expected_target_sha256:
                raise ValueError
        except (KeyError, TypeError, ValueError, OSError):
            raise ValueError("KUBERNETES_INPUT_CONFIGURATION_INVALID") from None
        transport = KubernetesHTTP(target, env, duration=self.cfg.get("max_duration_seconds", 120),
            binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner, profile=INPUT_VALIDATION_PROFILE)
        transport.declare_input_contract(_contract(check, policy_values, self.cfg, request_model(env, target, transport=transport)))
        ctx = Ctx(check=check, env=env, adapter=adapter, target=target, policy_values=policy_values,
                  timeout=float(self.cfg.get("request_timeout", 10)), allow_mutating=False, keys={}, secrets=[],
                  cprobe=_WithBody(transport), workdir=workdir, started=utcnow())
        draft = self._finish(ctx)
        draft.cleanup_errors.extend(transport.report()["cleanup_errors"])
        if transport.injected:
            draft.data_origin = DataOrigin.EXAMPLE
        return draft
