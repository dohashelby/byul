"""REQ-08·09·SUP-08 transport implementation; reuses input_validation fixtures, observations and judge."""
from copy import deepcopy
from pathlib import Path
import json
import time

from guard.adapters.engine_profiles import supports
from guard.input_matrix_policy import PROFILE as MATRIX_PROFILE, pairs, planned
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.models.base import utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import (INPUT_VALIDATION_PROFILE, REQUEST_BINDING_PROFILE,
                                          KubernetesBindingError, KubernetesHTTP)
from . import request_field_probe
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state, validate_content_settings
from .http_common import Ctx, fill_model, request_model
from .input_validation import InputValidationExecutor
from .response_projection import capture_metadata

_SETTINGS = {"target", "kubectl_binary", "request_timeout", "max_duration_seconds", "vocab_size", "vocab_source",
             "max_probe_tokens", "reused_token_ids", "runner_input", "runner_capture", "code_module_root",
             "code_module_repository", "code_marker_root", "unapproved_revision", "control_image", "native_input_capture"}
NORMAL_OPTION = "normal_token_input_only"
NORMAL_PATH = "/v1/completions"
NORMAL_ENDPOINT = "POST " + NORMAL_PATH
NORMAL_FORMAT = "token_ids"
_NORMAL_LABEL = "func|POST_" + NORMAL_PATH
_NORMAL_CASE = NORMAL_ENDPOINT + "|" + NORMAL_FORMAT


def _normal_exchange(transport, path, label, body, timeout):
    response = {}
    if timeout > 0:
        request = {"id": label, "mode": "json", "method": "POST", "headers": {}, "body": body,
                   "url": transport.endpoint.geturl().rstrip("/") + path}
        try:
            response, = transport("kubernetes-probe", [request], timeout)
        except KubernetesBindingError:
            pass
    report = transport.report()
    requests = report["requests"]
    bound = (len(requests) == 1 and requests[0]["id"] == label
             and requests[0]["binding_verified_before_after"] is True
             and requests[0]["outcome"] == response.get("outcome"))
    capture = capture_metadata(response) if bound else None
    summary = {"binding_sha256": report["binding_sha256"], "probe_source_sha256": report["probe_source_sha256"],
               "ownership_verified_before_after": bound, "request_body_sha256": sha256_hex(canonical_json(body)),
               "outcome": response.get("outcome") if bound else None, "response_capture": capture}
    try:
        if (not bound or report["collection_gap"] or report["cleanup_errors"] or response.get("outcome") != 200
                or response.get("capture_complete") is not True or response.get("eof") is not True
                or response.get("utf8_valid") is not True or response.get("body_truncated") is not False):
            raise ValueError
        raw = response["body"].encode()
        if (type(response.get("body_bytes")) is not int or response["body_bytes"] != len(raw)
                or response.get("body_sha256") != sha256_hex(raw)):
            raise ValueError
        document = strict_json(raw, limit=131072)
        if type(document) is not dict:
            raise ValueError
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        return response, None, summary, report["cleanup_errors"]
    return response, document, summary, report["cleanup_errors"]


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
            fields = request_field_probe.nested_fields([values["reused_token_field"]], list(cfg["reused_token_ids"]))
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

    def validate_configuration(self, check):
        if NORMAL_OPTION not in self.cfg:
            return super().validate_configuration(check)
        if (self.cfg[NORMAL_OPTION] is not True or check.id != "REQ-09" or check.executor != self.name
                or getattr(check, "collection_profile", None) != MATRIX_PROFILE
                or set(self.cfg) - {"target", NORMAL_OPTION, "kubectl_binary", "request_timeout", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("KUBERNETES_INPUT_NORMAL_SCOPE_INVALID")
        validate_content_settings(self.cfg)

    def validate_policy(self, check, values):
        if NORMAL_OPTION not in self.cfg:
            return super().validate_policy(check, values)
        self.validate_configuration(check)
        if (type(values) is not dict or set(values) != {"max_input_tokens", "rejection_codes", "allowed_endpoints",
                                                        "allowed_input_formats", "allowed_input_pairs"}
                or (NORMAL_ENDPOINT, NORMAL_FORMAT) not in pairs(check, values)
                or type(values["max_input_tokens"]) is not int or not 1 <= values["max_input_tokens"] <= 8192):
            raise ValueError("KUBERNETES_INPUT_NORMAL_POLICY_INVALID")
        codes = values["rejection_codes"]
        if (type(codes) is not list or not codes or any(code not in ("400", "413", "422") for code in codes)
                or len(set(codes)) != len(codes)):
            raise ValueError("KUBERNETES_INPUT_NORMAL_POLICY_INVALID")

    def _run_normal(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            if (adapter is None or not supports(env.engine.name, env.engine.version, "request_input_checks")
                    or type(target) is not dict or target["lab"] is not False or target.get("synthetic") is True
                    or self._expected_target_sha256 != target_sha):
                raise ValueError
            model_window = target["comparison_config"]["launch"]["max_model_len"]
            if type(model_window) is not int or model_window <= 1:
                raise ValueError
            transport_options = {"duration": self.cfg.get("max_duration_seconds", 12),
                                 "binary": self.cfg.get("kubectl_binary", "kubectl"), "runner": self._kubectl_runner}
            tokenizer = KubernetesHTTP(target, env, profile=REQUEST_BINDING_PROFILE, **transport_options)
            completion = KubernetesHTTP(target, env, profile=INPUT_VALIDATION_PROFILE, **transport_options)
            completion.deadline = tokenizer.deadline
        except (KeyError, TypeError, ValueError, OSError, KubernetesBindingError):
            raise ValueError("KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED") from None

        self.version = "0.7.1+k8s-normal"
        def remaining():
            return min(self.cfg.get("request_timeout", 3), tokenizer.deadline - time.monotonic())

        tokenize_body = {"model": tokenizer.api_model, "prompt": "ping", "add_special_tokens": False,
                         "return_token_strs": False}
        _, document, tokenize, cleanup = _normal_exchange(tokenizer, "/tokenize", "tokenize-A", tokenize_body, remaining())
        token_ids = document.get("tokens") if document else None
        count = len(token_ids) if type(token_ids) is list else None
        tokenized = (count is not None and 1 <= count <= min(values["max_input_tokens"], 64)
                     and type(document.get("count")) is int and document["count"] == count
                     and type(document.get("max_model_len")) is int and document["max_model_len"] == model_window
                     and count + 1 <= model_window
                     and all(type(value) is int and 0 <= value < 2**31 for value in token_ids))
        result = None
        prompt_count = None
        normal = False
        completion_timeout = remaining()
        if tokenized and completion_timeout > 0:
            body = {"model": completion.api_model, "prompt": token_ids, "max_tokens": 1}
            completion.declare_input_contract({"path": NORMAL_PATH, "control": body, "prompt_tokens": None, "fields": {}})
            response, document, result, errors = _normal_exchange(completion, NORMAL_PATH, _NORMAL_LABEL, body, completion_timeout)
            cleanup = [*cleanup, *errors]
            if document and control_state(response, completion.api_model, NORMAL_PATH) == "pass":
                usage = document["usage"]
                prompt_count = usage["prompt_tokens"]
                normal = (len(document["choices"]) == 1 and prompt_count == count
                          and usage["completion_tokens"] == 1 and usage["total_tokens"] == count + 1)

        completed_scope = result is not None and result["ownership_verified_before_after"] and type(result["outcome"]) is int
        observations = {obs.key: {"value": None, "collected": False} for obs in check.required_observations}
        observations["functional_probes_planned"] = {"value": [_NORMAL_CASE], "collected": True}
        observations["input_matrix_planned"] = {"value": planned(check, values), "collected": True}
        observations["input_matrix_complete"] = {"value": False, "collected": True}
        if completed_scope:
            observations["input_endpoints_tested"] = {"value": [NORMAL_ENDPOINT], "collected": True}
            observations["input_formats_tested"] = {"value": [NORMAL_FORMAT], "collected": True}
        if normal:
            observations["functional_probe_results"] = {"value": {_NORMAL_CASE: "pass"}, "collected": True}

        summary = {"profile": "kubernetes-owned-normal-token-input/1", "target_sha256": target_sha,
                   "tokenizer": tokenize, "completion": result, "tokenizer_count": count if tokenized else None,
                   "tokenizer_tokens_sha256": sha256_hex(canonical_json(token_ids)) if tokenized else None,
                   "api_reported_prompt_tokens": prompt_count, "normal_completion_shape_verified": normal,
                   "uncollected": ["other_policy_pairs", "input_limits", "rejections", "embedding_mask_offset", "worker_consumption"]}
        error = None if normal and not cleanup else "KUBERNETES_INPUT_NORMAL_OBSERVATION_INCOMPLETE"
        artifacts = []
        try:
            path = Path(workdir) / "normal_token_input.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(canonical_json(summary))
            artifacts.append(DraftArtifact(name=path.name, path=path, collector=f"{self.name}/{self.version}"))
        except OSError:
            cleanup = [*cleanup, "KUBERNETES_INPUT_NORMAL_ARTIFACT_UNAVAILABLE"]
            error = error or "KUBERNETES_INPUT_NORMAL_ARTIFACT_UNAVAILABLE"
        origin = DataOrigin(tokenizer.process_observation_origin)
        if error and origin == DataOrigin.MEASURED:
            origin = DataOrigin.DECLARED
        return EvidenceDraft(status=ExecStatus.ABORTED if error else ExecStatus.COMPLETED, data_origin=origin,
            normal_control={"attempted": result is not None, "succeeded": True if normal else None,
                            "detail": "One approved token-array control; API response counts do not verify worker consumption."},
            challenges=[{"step": step, "attempted": False} for step in check.challenge_steps],
            observations=observations, artifacts=artifacts, config={NORMAL_OPTION: True, "target_sha256": target_sha},
            started_at=started, finished_at=utcnow(), abort_reason=error, cleanup_errors=cleanup)

    def run(self, check, env, policy_values, adapter, workdir):
        if type(self.cfg) is dict and NORMAL_OPTION in self.cfg:
            return self._run_normal(check, env, policy_values, adapter, workdir)
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
        from guard.runtime.kubernetes_native_input import KubernetesNativeInputHTTP
        transport_type = KubernetesNativeInputHTTP if "native_input_capture" in self.cfg else KubernetesHTTP
        transport = transport_type(target, env, duration=self.cfg.get("max_duration_seconds", 120),
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
