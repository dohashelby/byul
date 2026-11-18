"""Canonical REQ-09 normal observation using only owned tokenizer-issued IDs."""
from pathlib import Path
import time

from guard.adapters.engine_profiles import supports
from guard.input_matrix_policy import PROFILE as MATRIX_PROFILE, pairs, planned
from guard.models import DataOrigin, ExecStatus, canonical_json, sha256_hex
from guard.models.base import utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import (INPUT_VALIDATION_PROFILE, REQUEST_BINDING_PROFILE,
                                          KubernetesBindingError, KubernetesHTTP)
from .base import DraftArtifact, EvidenceDraft
from .content_common import control_state, validate_content_settings
from .response_projection import capture_metadata

NORMAL_OPTION = "normal_token_input_only"
NORMAL_PATH = "/v1/completions"
NORMAL_ENDPOINT = "POST " + NORMAL_PATH
NORMAL_FORMAT = "token_ids"
_NORMAL_LABEL = "func|POST_" + NORMAL_PATH
_NORMAL_CASE = NORMAL_ENDPOINT + "|" + NORMAL_FORMAT


def normal_input_transports(target, env, *, duration, binary="kubectl", runner=None):
    """Validate the declared normal-input profile and build both non-dispatching transports."""
    try:
        if (type(target) is not dict or target["lab"] is not False or target.get("synthetic") is True
                or not supports(env.engine.name, env.engine.version, "request_input_checks")):
            raise ValueError
        model_window = target["comparison_config"]["launch"]["max_model_len"]
        if type(model_window) is not int or model_window <= 1:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise ValueError("KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED") from None
    tokenizer = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner,
                               profile=REQUEST_BINDING_PROFILE)
    completion = KubernetesHTTP(target, env, duration=duration, binary=binary, runner=runner,
                                 profile=INPUT_VALIDATION_PROFILE)
    completion.deadline = tokenizer.deadline
    return model_window, tokenizer, completion


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


class KubernetesNormalTokenInputExecutor:
    name = "input_validation"
    version = "0.7.1+k8s-normal"

    def __init__(self, cfg, *, runner=None, environ=None, expected_target_sha256=None):
        self.cfg = cfg
        self._kubectl_runner = runner
        self._expected_target_sha256 = expected_target_sha256

    def keys(self):
        return {}

    def validate_configuration(self, check):
        if (type(self.cfg) is not dict or self.cfg.get(NORMAL_OPTION) is not True or check.id != "REQ-09"
                or check.executor != self.name
                or getattr(check, "collection_profile", None) != MATRIX_PROFILE
                or set(self.cfg) - {"target", NORMAL_OPTION, "kubectl_binary", "request_timeout", "max_duration_seconds"}
                or type(self.cfg.get("target")) is not str or not self.cfg["target"]):
            raise ValueError("KUBERNETES_INPUT_NORMAL_SCOPE_INVALID")
        validate_content_settings(self.cfg)

    def validate_policy(self, check, values):
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

    def run(self, check, env, values, adapter, workdir):
        self.validate_policy(check, values)
        started = utcnow()
        try:
            with Path(self.cfg["target"]).open("rb") as stream:
                target = strict_json(stream.read(65537), limit=65536)
            target_sha = sha256_hex(canonical_json(target))
            if adapter is None or self._expected_target_sha256 != target_sha:
                raise ValueError
            model_window, tokenizer, completion = normal_input_transports(
                target, env, duration=self.cfg.get("max_duration_seconds", 12),
                binary=self.cfg.get("kubectl_binary", "kubectl"), runner=self._kubectl_runner)
        except (KeyError, TypeError, ValueError, OSError, KubernetesBindingError):
            raise ValueError("KUBERNETES_INPUT_NORMAL_TARGET_UNVERIFIED") from None

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
