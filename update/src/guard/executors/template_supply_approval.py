"""SUP-03의 관리 template 신원·API 입력 projection 관측 전용 부분계약.

현재 파일 bytes와 API가 보고한 입력을 독립 worker 소비 증거로 바꾸지 않는다.
승인 불일치도 관측만 기록하며, 원문 공급물 소비 FAIL/PASS는 확정하지 않는다.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import math
import re
import time

from guard.models import canonical_json, sha256_hex
from .content_common import content_request, control_state, json_body, sample_artifact
from .engine_identity import owned_template_snapshot
from .http_common import mask


PROFILE = "fixed-template-supply-v1"
POLICY_KEY = "allowed_template_sha256"
NORMAL_TEXT = "guard template control"
DEFAULTS = {"max_output_tokens": 1, "max_total_requests": 3, "max_probe_tokens": 4096,
            "max_total_probe_tokens": 16384, "max_input_bytes": 4096, "max_duration_seconds": 30}
_SHA = re.compile(r"[0-9a-f]{64}")
_PINNED = re.compile(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


def settings(cfg):
    """기업 정책값과 독립된 요청·시간·관측 상한. 한 번의 출력은 1 token이다."""
    result = {key: cfg.get(key, value) for key, value in DEFAULTS.items()}
    maxima = {"max_output_tokens": 1, "max_total_requests": 3, "max_probe_tokens": 4096,
              "max_total_probe_tokens": 16384, "max_input_bytes": 4096, "max_duration_seconds": 300}
    for key, maximum in maxima.items():
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f"{key} must be an integer in [1, {maximum}]")
    timeout = cfg.get("request_timeout", 3.0)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        raise ValueError("request_timeout must be finite, positive and at most 120 seconds")
    for key, endpoint in (("endpoint", "/v1/chat/completions"), ("tokenize_endpoint", "/tokenize")):
        if cfg.get(key, endpoint) != endpoint:
            raise ValueError("fixed-template-supply-v1 requires its pinned endpoints")
    return result


def validate_policy(check, values, cfg):
    """승인 목록은 명시 입력만 받는다. REQ-07 값이나 관측에서 채우지 않는다."""
    approved = values.get(POLICY_KEY)
    if (type(approved) is not list or any(type(value) is not str or not _SHA.fullmatch(value) for value in approved)
            or len(approved) != len(set(approved))):
        raise ValueError("allowed_template_sha256 must be an explicit unique lowercase SHA-256 list")
    limits = settings(cfg)
    if len(check.functional_probes) != 1:
        raise ValueError("one fixed engine chat fixture is required")
    probe = check.functional_probes[0]
    if (probe.via != "engine" or probe.method != "POST" or probe.path != "/v1/chat/completions"
            or probe.expect != [200] or type((probe.body or {}).get("max_tokens")) is not int
            or probe.body != {"model": "{model}", "messages": [
                {"role": "user", "content": NORMAL_TEXT}], "max_tokens": 1}):
        raise ValueError("only the fixed single-user template supply fixture is supported")
    if len(NORMAL_TEXT.encode("utf-8")) > limits["max_input_bytes"]:
        raise ValueError("fixed normal fixture exceeds input byte budget")


def _timestamp(value):
    if type(value) is not str or len(value) > 40:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def _target(ctx, *, profile_support=None, kubernetes=False):
    engine = ctx.target.get("engine")
    config = ctx.target.get("comparison_config")
    launch = config.get("launch") if type(config) is dict else None
    if type(engine) is not dict or type(launch) is not dict:
        return None
    container, image, model = engine.get("container"), engine.get("image_digest"), ctx.env.model.id
    run_id = ctx.target.get("run_id")
    api_model = model
    if kubernetes:
        from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
        if (not isinstance(ctx.cprobe, KubernetesHTTP) or ctx.cprobe.profile != REQUEST_BINDING_PROFILE
                or ctx.cprobe.target_sha256 != sha256_hex(canonical_json(ctx.target))):
            return None
        container = ctx.cprobe.binding["engine"]["container"]
        run_id = ctx.cprobe.binding["run_id"]
        api_model = ctx.api_model
    argv, identity = launch.get("argv"), launch.get("chat_template")
    if (type(container) is not str or not _NAME.fullmatch(container)
            or type(image) is not str or len(image) > 512 or not _PINNED.fullmatch(image)
            or image != ctx.env.engine.image_digest
            or type(model) is not str or not model or launch.get("model") != model
            or type(run_id) is not str or not run_id
            or not ctx.env.engine.version or ctx.target.get("observed_version") != ctx.env.engine.version
            or type(argv) is not list or not argv or len(argv) > 128
            or any(type(arg) is not str or len(arg) > 1024 for arg in argv)
            or type(identity) is not dict or argv.count("--chat-template") != 1):
        return None
    selected = {key: identity.get(key) for key in
                ("profile", "version", "sha256", "container_path", "content_format", "request_overrides")}
    index = argv.index("--chat-template") + 1
    if (index >= len(argv) or argv[index] != selected["container_path"]
            or type(selected["sha256"]) is not str or not _SHA.fullmatch(selected["sha256"])
            or type(launch.get("max_model_len")) is not int or launch["max_model_len"] < 2):
        return None
    if "--served-model-name" in argv:
        index = argv.index("--served-model-name") + 1
        if argv.count("--served-model-name") != 1 or index >= len(argv) or argv[index] != api_model:
            return None
    supported = profile_support or getattr(ctx.adapter, "supports_template_profile", None)
    try:
        if not callable(supported) or not supported(selected):
            return None
    except Exception:
        return None
    return container, image, launch, selected


def _snapshot(executor, ctx, target, deadline, *, expected_path=None, kubernetes=False):
    """공유 REQ-07 helper는 유지하고 이 프로파일의 identity 형식을 보강한다."""
    container, image, launch, identity = target
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if kubernetes:
            from .engine_identity import _READ_TEMPLATE
            snap = ctx.cprobe.template_snapshot(identity, launch["argv"], ctx.target["engine"].get("command"), _READ_TEMPLATE)
            if (type(snap) is not dict or snap.get("sha256") != identity["sha256"]
                    or snap.get("selected_path") != identity["container_path"] or snap.get("readonly") is not True):
                return None
            return snap
        raw = executor.docker.inspect_json(container, timeout=min(5, ctx.timeout, remaining))
        if type(raw) is not dict:
            return None
        state, config = raw.get("State"), raw.get("Config")
        if type(state) is not dict or type(config) is not dict:
            return None
        cid, local_image = raw.get("Id"), raw.get("Image")
        name_matches = raw.get("Name") == "/" + container
        if _SHA.fullmatch(container):
            name_matches = cid == container and type(raw.get("Name")) is str and bool(_NAME.fullmatch(raw["Name"][1:]))
        if (type(cid) is not str or not _SHA.fullmatch(cid) or not name_matches
                or type(local_image) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", local_image)
                or state.get("Running") is not True or not _timestamp(state.get("StartedAt"))
                or config.get("Image") != image or config.get("Cmd") != launch["argv"]):
            return None
        # 시간 epoch는 helper와 같고, 개별 snapshot의 남은 시간도 요청 timeout 안이다.
        snap = owned_template_snapshot(executor.docker, ctx, identity, launch["argv"],
                                       min(deadline, time.monotonic() + ctx.timeout), monotonic=time.monotonic,
                                       **({"expected_path": expected_path} if expected_path is not None else {}))
        if (type(snap) is not dict or snap.get("container_id") != cid
                or snap.get("started_at") != state["StartedAt"] or snap.get("image") != image
                or snap.get("sha256") != identity["sha256"] or snap.get("readonly") is not True):
            return None
        return {**snap, "image_id": local_image}
    except Exception:
        return None


def _tokens(value, maximum):
    return (type(value) is list and 0 < len(value) <= maximum
            and all(type(token) is int and 0 <= token < 2**31 for token in value))


def collect(executor, ctx, *, kubernetes=False):
    """최대 세 번의 무해 요청. 원문 challenge와 독립 소비는 미수집이다."""
    ctx._native_artifact_written = False
    validate_policy(ctx.check, ctx.policy_values, executor.cfg)
    limits = settings(executor.cfg)
    deadline = time.monotonic() + limits["max_duration_seconds"]
    target = _target(ctx, kubernetes=kubernetes)
    errors, requests, accepted_tokens, responses = [], 0, 0, []
    before = _snapshot(executor, ctx, target, deadline, kubernetes=kubernetes) if target is not None else None
    after = None
    tokenize_response_tokens = None
    stable, exact, normal_ok = False, False, False
    approved = None
    functional = {ctx.check.functional_probes[0].id: "no_response"}
    attempted = False
    api_model = ctx.api_model if kubernetes else ctx.env.model.id

    def send(label, endpoint, body):
        nonlocal requests
        remaining = deadline - time.monotonic()
        if remaining <= 0 or requests >= limits["max_total_requests"] or accepted_tokens >= limits["max_total_probe_tokens"]:
            errors.append("TEMPLATE_SUPPLY_BUDGET_EXHAUSTED")
            return {"outcome": "budget_exhausted"}
        request = content_request(ctx, "engine", label, endpoint, body)
        requests += 1
        try:
            rows = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, remaining))
            if (type(rows) is not list or len(rows) != 1 or type(rows[0]) is not dict
                    or rows[0].get("id") != label or type(rows[0].get("outcome")) is not int
                    or not 100 <= rows[0]["outcome"] <= 599):
                raise ValueError("invalid response correlation")
            response = rows[0]
        except Exception:
            response = {"outcome": "probe_error"}
            errors.append("TEMPLATE_SUPPLY_PROBE_UNAVAILABLE")
        # 원문 body/detail/preview·token ID·호스트 경로를 artifact에 남기지 않는다.
        projection = {"outcome": response.get("outcome")}
        ctx.record("trusted", PROFILE, label, "allowed", projection)
        responses.append({"case": label, **projection})
        return response

    if before is None:
        errors.append("TEMPLATE_SUPPLY_IDENTITY_UNVERIFIED")
    else:
        messages = deepcopy(ctx.check.functional_probes[0].body["messages"])
        token_response = send("tokenize-A" if kubernetes else "supply-tokenize", "/tokenize", {"model": api_model, "messages": messages,
                              "add_generation_prompt": True, "add_special_tokens": False, "return_token_strs": False})
        rendered, error = json_body(token_response)
        rendered_valid = bool(token_response.get("outcome") == 200 and not error
                              and _tokens(rendered.get("tokens"), limits["max_probe_tokens"])
                              and type(rendered.get("count")) is int and rendered["count"] == len(rendered["tokens"])
                              and type(rendered.get("max_model_len")) is int
                              and rendered["max_model_len"] == target[2]["max_model_len"]
                              and len(rendered["tokens"]) + 1 <= rendered["max_model_len"])
        if rendered_valid:
            tokenize_response_tokens = len(rendered["tokens"])
        if rendered_valid and tokenize_response_tokens > limits["max_total_probe_tokens"] - accepted_tokens:
            # The bounded response was received, but its tokens are not admitted as proof.
            errors.append("TEMPLATE_SUPPLY_BUDGET_EXHAUSTED")
        elif rendered_valid:
            accepted_tokens += tokenize_response_tokens
            if accepted_tokens + len(rendered["tokens"]) + 1 <= limits["max_total_probe_tokens"]:
                attempted = True
                response = send("before" if kubernetes else "supply-inference", "/v1/chat/completions", {"model": api_model,
                                "messages": messages, "max_tokens": 1, "temperature": 0,
                                "return_token_ids": True, "add_generation_prompt": True, "add_special_tokens": False})
                value, error = json_body(response)
                exact = bool(control_state(response, api_model, "/v1/chat/completions") == "pass"
                             and not error and len(value["choices"]) == 1
                             and _tokens(value.get("prompt_token_ids"), limits["max_probe_tokens"])
                             and value["prompt_token_ids"] == rendered["tokens"]
                             and value["usage"]["prompt_tokens"] == len(rendered["tokens"])
                             and value["usage"]["completion_tokens"] == 1
                             and value["usage"]["total_tokens"] == len(rendered["tokens"]) + 1)
                if exact:
                    accepted_tokens += len(rendered["tokens"]) + 1
                else:
                    errors.append("TEMPLATE_SUPPLY_INPUT_PROJECTION_UNVERIFIED")
            else:
                errors.append("TEMPLATE_SUPPLY_BUDGET_EXHAUSTED")
        else:
            errors.append("TEMPLATE_SUPPLY_TOKENIZATION_UNVERIFIED")
        after = _snapshot(executor, ctx, target, deadline, kubernetes=kubernetes)
        stable = after is not None and before == after
        if not stable:
            errors.append("TEMPLATE_SUPPLY_IDENTITY_CHANGED_OR_UNAVAILABLE")
        approved = before["sha256"] in ctx.policy_values[POLICY_KEY] if stable else None
        normal_ok = bool(stable and exact)
        functional[ctx.check.functional_probes[0].id] = "pass" if normal_ok else "no_response"

    observations = {
        "selected_template_sha256": {"value": before["sha256"] if stable else None, "collected": stable},
        "template_identity_verified": {"value": stable, "collected": True},
        "selected_template_approved": {"value": approved, "collected": stable},
        "input_projection_exact_match": {"value": exact and stable, "collected": True},
        "template_consumer_verified": {"value": False, "collected": True},
        "supply_scope_complete": {"value": False, "collected": True},
        "functional_probes_planned": {"value": list(functional), "collected": True},
        "functional_probe_results": {"value": functional, "collected": attempted},
    }
    fixture = {"profile": PROFILE, "normal": ctx.check.functional_probes[0].model_dump(mode="json"),
               "settings": limits, "endpoints": ["/tokenize", "/v1/chat/completions"]}
    if kubernetes:
        report = ctx.cprobe.report()
        fixture.update(transport_profile=report["profile"], probe_source_sha256=report["probe_source_sha256"],
                       model_mapping_sha256=sha256_hex(canonical_json(ctx.cprobe.model_mapping)))
    ctx._template_fixture_sha256 = sha256_hex(canonical_json(mask(fixture, ctx.secrets)))
    summary = {
        "profile": PROFILE, "artifact_profile": "template-response-projection/1",
        "requests_sent": requests, "accepted_tokens": accepted_tokens,
        "tokenize_response_token_count": tokenize_response_tokens,
        "identity_before": before, "identity_after": after,
        "collection_errors": sorted(set(errors)), "responses": responses,
        "not_exercised": ["independent_worker_template_read", "startup_loaded_template_digest",
                          "all_secondary_sources", "unapproved_supply_restart_challenge"],
        "limitations": [
            "Exact token correspondence is an engine-reported API input projection, not independent worker file consumption.",
            "Token admission is checked after each bounded response; over-budget replies are rejected and no further inference is dispatched.",
            "Current readonly mount bytes may differ from a template cached at startup; no actual supply violation is established.",
            "Approval membership is an operator policy comparison, not authenticated corporate approval.",
            "Neither approval membership nor API input projection establishes full security or functional PASS.",
            "Only the reviewed fixed template, full pinned ENV image, matching model and bounded normal chat are supported.",
            "Manual PASS is outside the existing REQ-07 bounded manual policy contract; declared FAIL and UNKNOWN are preserved.",
        ],
        "fixture": {"profile": PROFILE, "normal_sha256": sha256_hex(canonical_json(fixture["normal"])),
                    "settings": limits, "endpoints": fixture["endpoints"]},
    }
    if kubernetes:
        summary["kubernetes"] = report
        summary["model_mapping_sha256"] = fixture["model_mapping_sha256"]
        summary["fixture"].update({key: fixture[key] for key in
            ("transport_profile", "probe_source_sha256", "model_mapping_sha256")})
        summary["limitations"].extend([
            "Readonly is the selected Kubernetes volumeMount declaration, not observed worker mount flags.",
            "Template bytes are read by an exec helper, not independent engine-worker consumption.",
            "Pod imageID does not establish node runtime image ancestry or deployed source provenance.",
            "Before/after snapshots do not prove whole-run coverage or producer authenticity.",
        ])
    if executor.cfg.get("native_observation_path") is not None:
        # Optional actual file capture, never an implicit new sensor/trust/verdict claim.
        from .owned_capture import read_owned_json
        from .native_observations import validate_capture
        try:
            if before is None or deadline <= time.monotonic():
                raise ValueError("NATIVE_CAPTURE_TARGET_UNVERIFIED")
            packet, provenance = read_owned_json(executor.docker, ctx.target,
                container=target[0], image=target[1], path=executor.cfg["native_observation_path"],
                timeout=min(ctx.timeout, 5, deadline - time.monotonic()))
            result = validate_capture(packet, kind="template", env_ref=ctx.env.ref().model_dump(mode="json"),
                                      run_id=ctx.target["run_id"], image=target[1])
            summary["native_observations"] = {**result, "capture": provenance}
            sample_artifact(ctx, "template_native_observations.json",
                            {"capture": provenance, "record": packet, "summary": result})
            ctx._native_artifact_written = True
        except Exception:
            summary["native_observations"] = {"captured": False, "error": "NATIVE_TEMPLATE_CAPTURE_UNAVAILABLE",
                "actual_consumption_verified": False, "security": "UNKNOWN", "functional": "UNKNOWN"}
    sample_artifact(ctx, "template_samples.json", {"summary": summary})
    return observations, {"attempted": attempted, "succeeded": normal_ok,
                          "detail": "bounded normal chat and exact API input projection; independent consumer unverified"}, [
        {"step": step, "attempted": False, "observations": {"profile": PROFILE, "scope": "not exercised"}}
        for step in ctx.check.challenge_steps], summary
