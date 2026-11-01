"""REQ-11: bounded JSON/SSE input projection and connection checks.

The fixture uses consumer-side prompt IDs and rendered text. Generated words,
an HTTP status, and an echoed caller-controlled request ID prove no ownership.
There is no independent oracle for the generated output's originating request.
The current collector can therefore establish subproperties, never security PASS.
No abort, stored-result, retry, child-request or tenant authorization is tested.
"""
from __future__ import annotations

import hashlib
import math
import secrets
import time

from guard.automation_policy import validate_request_binding_policy
from guard.models import canonical_json, sha256_hex
from guard.runtime.docker import Docker
from .content_common import append_artifact, content_request, json_body, sample_artifact
from .engine_identity import owned_template_snapshot
from .http_common import HttpCheckExecutor
from .owned_capture import strict_json
from .response_projection import capture_metadata, log_metadata


PROFILE = "request-connection-binding-v1"
ARTIFACT_PROFILE = "request-binding-response-projection/1"
ID_CASES = ("generated", "distinct_body", "collision_body", "collision_header")
CASES = tuple(f"{mode}:{case}" for mode in ("json", "sse") for case in ID_CASES)
SUPPORTED_POLICY = {"result_binding": "originating_connection",
                    "client_request_id_trust": "untrusted_correlation"}
DEFAULTS = {"max_total_requests": 24, "max_output_tokens": 8,
            "max_duration_seconds": 300, "read_delay_ms": 250}


def _ids(value, maximum=128, *, empty=False):
    return (type(value) is list and (empty or bool(value)) and len(value) <= maximum
            and all(type(token) is int and token >= 0 for token in value))


def rendered_text(text):
    """The fixed fixture's exact bytes; never execute arbitrary Jinja."""
    return "user: " + text + "\nassistant: "


def _load_json(text):
    # Transport/body helpers already enforce the existing capture bounds.
    # Do not add a smaller limit when validating an individual SSE frame.
    raw=text.encode("utf-8")
    return strict_json(raw,limit=len(raw))


def _response_object(response):
    """Keep existing body bounds and reject ambiguous consumer/tokenizer JSON."""
    try:
        value,error=json_body(response)
        if error:return value,error
        return _load_json(response["body"]),None
    except (ValueError,TypeError,UnicodeError,RecursionError):
        return None,"response is not unambiguous JSON"


def _analysis_projection(value):
    """Storage only: keep the complete analyzer result transient and unchanged."""
    result = {key: value[key] for key in ("verified", "gaps", "violations", "output_tokens",
                                         "finish_signals", "terminal_signals") if key in value}
    for key in ("api_ids", "consumer_ids", "consumer_texts"):
        if key in value:
            result[key + "_sha256"] = sha256_hex(canonical_json(value[key]))
    return result


def _timing_projection(value):
    """Serialize existing measurements only; never change collection/overlap math."""
    connection = value.get("connection_id")
    result = {"id": value["id"], "connection_id_sha256": sha256_hex(canonical_json(connection))
              if type(connection) is str else None}
    for key in ("dispatch_ms", "request_sent_ms", "headers_ms", "end_ms"):
        item = value.get(key)
        result[key] = item if (type(item) in (int, float) and 0 <= item <= 86_400_000
                               and math.isfinite(item)) else None
    return result


def analyze_response(response, *, expected, foreign, model, max_output, api_id=None, mode="json"):
    """Project collector facts without using generated prose as an oracle.

    Keep independently observed foreign consumer data even if a later frame or
    transport fails. Missing or unknown consumer data are collection gaps.
    """
    gaps, violations = [], []
    if response.get("outcome") != 200:
        return {"verified": False, "gaps": ["inference status is not 200"], "violations": []}
    if (response.get("capture_complete") is not True or response.get("body_truncated") is not False
            or response.get("utf8_valid") is not True or response.get("eof") is not True):
        gaps.append("response capture was not complete, bounded and valid UTF-8")
    raw = response.get("body")
    if (type(raw) is not str or response.get("body_bytes") != len(raw.encode("utf-8"))
            or response.get("body_sha256") != hashlib.sha256(raw.encode("utf-8")).hexdigest()):
        gaps.append("captured body bytes and digest do not correspond")
    if response.get("content_type") != ("application/json" if mode == "json" else "text/event-stream"):
        gaps.append("response media type differs from the selected JSON/SSE mode")
    response_ids, consumer_ids, consumer_texts, output_ids = [], [], [], []
    finish_count, done_count, usage = 0, 0, None
    frames = []
    if mode == "json":
        value, error = _response_object(response)
        if error:
            gaps.append(error)
        else:
            frames.append(value)
    else:
        events = response.get("events")
        if type(events) is not list or not events:
            gaps.append("SSE frames are unavailable")
            events = []
        for index, event in enumerate(events):
            if (type(event) is not dict or type(event.get("seq")) is not int
                    or event["seq"] != index or type(event.get("data")) is not str):
                gaps.append("SSE frame order or data shape is unavailable")
                continue
            data = event["data"]
            if data == "[DONE]":
                done_count += 1
                if done_count > 1:
                    gaps.append("duplicate stream terminal signal on one connection")
                continue
            if done_count:
                gaps.append("a data frame followed the stream terminal signal")
            try:
                frame = _load_json(data)
            except (TypeError, ValueError, UnicodeError, RecursionError):
                gaps.append("an SSE data frame is not JSON")
                continue
            if type(frame) is not dict:
                gaps.append("an SSE data frame is not an object")
                continue
            frames.append(frame)
        if done_count != 1 or response.get("terminal_seen") is not True:
            gaps.append("one complete stream terminal signal was not observed")

    for frame in frames:
        rid = frame.get("id")
        if type(rid) is not str or not rid.startswith("chatcmpl-") or len(rid) <= len("chatcmpl-"):
            gaps.append("the source-qualified API request ID is unavailable")
        else:
            response_ids.append(rid)
            if api_id is not None and rid != api_id:
                gaps.append("API ID does not follow the inspected caller ID precedence")
        if frame.get("model") != model:
            gaps.append("response model identity is not the selected model")
        if frame.get("object") != ("chat.completion" if mode == "json" else "chat.completion.chunk"):
            gaps.append("response object does not match the inspected generation schema")
        if frame.get("prompt_token_ids") is not None:
            ids = frame["prompt_token_ids"]
            if not _ids(ids):
                gaps.append("consumer prompt token IDs have an unsupported shape")
            else:
                consumer_ids.append(ids)
                if ids == foreign["tokens"]:
                    violations.append("the other connection's exact prompt token IDs were consumed")
                elif ids != expected["tokens"]:
                    gaps.append("consumer prompt token IDs do not match the independently tokenized fixture")
        if frame.get("prompt_text") is not None:
            text = frame["prompt_text"]
            if type(text) is not str:
                gaps.append("consumer prompt text has an unsupported shape")
            else:
                consumer_texts.append(text)
                if text == foreign["text"]:
                    violations.append("the other connection's exact rendered prompt was returned")
                elif text != expected["text"]:
                    gaps.append("consumer prompt text differs from the fixed rendered fixture")
        choices = frame.get("choices")
        if type(choices) is not list or len(choices) > 1:
            gaps.append("exactly one generated choice was not observed")
            continue
        if not choices:
            if mode != "sse" or type(frame.get("usage")) is not dict:
                gaps.append("empty choices without the supported stream usage trailer")
        for choice in choices:
            if type(choice) is not dict or type(choice.get("index")) is not int or choice["index"] != 0:
                gaps.append("generated choice index is unavailable")
                continue
            if mode == "json":
                message = choice.get("message")
                if (type(message) is not dict or message.get("role") != "assistant"
                        or type(message.get("content")) is not str):
                    gaps.append("normal chat message shape is unavailable")
            elif type(choice.get("delta")) is not dict:
                gaps.append("stream delta shape is unavailable")
            tokens = choice.get("token_ids")
            if tokens is not None:
                if _ids(tokens, max_output, empty=True):
                    if finish_count and tokens:
                        gaps.append("generated tokens followed the finish signal")
                    output_ids.extend(tokens)
                else:
                    gaps.append("generated token IDs exceed the bounded output shape")
            finish = choice.get("finish_reason")
            if finish is not None:
                if type(finish) is not str or finish not in {"stop", "length"}:
                    gaps.append("finish signal is unsupported")
                finish_count += 1
                if finish_count > 1:
                    gaps.append("more than one finish signal was returned for one choice")
        if frame.get("usage") is not None:
            if type(frame["usage"]) is not dict:
                gaps.append("usage shape is unavailable")
            elif usage is not None and frame["usage"] != usage:
                gaps.append("usage summaries differ within the same response")
            else:
                usage = frame["usage"]
    if len(set(response_ids)) > 1:
        violations.append("API request ID changed within one response stream")
    if not response_ids or not consumer_ids or not consumer_texts:
        gaps.append("source-qualified API and consumer-side request correspondence is incomplete")
    if (any(ids != expected["tokens"] for ids in consumer_ids)
            or any(text != expected["text"] for text in consumer_texts)):
        gaps.append("not every consumer-side prompt projection matches the originating connection")
    if finish_count != 1 or not 1 <= len(output_ids) <= max_output:
        gaps.append("one finish and bounded nonempty generated token IDs were not observed")
    if (type(usage) is not dict or any(type(usage.get(key)) is not int for key in
                                     ("prompt_tokens", "completion_tokens", "total_tokens"))
            or usage.get("prompt_tokens") != len(expected["tokens"])
            or usage.get("completion_tokens") != len(output_ids)
            or usage.get("total_tokens") != len(expected["tokens"]) + len(output_ids)):
        gaps.append("consumer/output token counts and complete usage do not correspond")
    return {"verified": not gaps and not violations, "gaps": sorted(set(gaps)),
            "violations": sorted(set(violations)), "api_ids": sorted(set(response_ids)),
            "consumer_ids": consumer_ids, "consumer_texts": consumer_texts,
            "output_tokens": len(output_ids), "finish_signals": finish_count, "terminal_signals": done_count}


class RequestBindingExecutor(HttpCheckExecutor):
    name = "request_binding"
    version = "0.3.5"
    requires_roles = False
    requires_route_scope = False
    _uses_docker_identity = True

    def __init__(self, cfg, *args, docker=None, **kwargs):
        super().__init__({"request_timeout": 120.0, **cfg}, *args, **kwargs)
        self.docker = (docker or Docker("binding" + secrets.token_hex(4))) if self._uses_docker_identity else None

    def _api_model(self, ctx):
        return ctx.api_model

    def _template_snapshot(self, ctx, identity, argv, deadline):
        return owned_template_snapshot(self.docker, ctx, identity, argv, deadline)

    def validate_policy(self, check, values):
        validate_request_binding_policy(values)
        limits = {"max_total_requests": 32, "max_output_tokens": 16, "max_duration_seconds": 300,
                  "read_delay_ms": 250}
        for key, maximum in limits.items():
            value = self.cfg.get(key, DEFAULTS[key])
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be an integer in [1, {maximum}]")
        timeout = self.cfg.get("request_timeout", 120.0)
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("request_timeout must be finite, positive and at most 120 seconds")
        if self.cfg.get("endpoint", "/v1/chat/completions") != "/v1/chat/completions":
            raise ValueError("request attribution supports only the fixed chat endpoint")
        if self.cfg.get("tokenize_endpoint", "/tokenize") != "/tokenize":
            raise ValueError("request attribution supports only the inspected tokenize endpoint")
        if len(check.challenge_steps) != len(CASES):
            raise ValueError("the eight fixed JSON/SSE ID challenge steps are required")
        if len(check.functional_probes) != 1:
            raise ValueError("one fixed engine chat functional fixture is required")
        fp = check.functional_probes[0]
        if (fp.method != "POST" or fp.path != "/v1/chat/completions" or fp.via != "engine"
                or fp.expect != [200] or fp.body != {"model": "{model}",
                    "messages": [{"role": "user", "content": "guard request binding control"}], "max_tokens": 1}):
            raise ValueError("only the pinned harmless single-user functional fixture is supported")

    def _finish(self, ctx):
        return append_artifact(super()._finish(ctx), ctx, "request_binding_samples.json", f"{self.name}/{self.version}")

    def collect(self, ctx):
        self.validate_policy(ctx.check, ctx.policy_values)
        model = self._api_model(ctx)
        settings = {key: self.cfg.get(key, value) for key, value in DEFAULTS.items()}
        started = time.monotonic()
        deadline = started + settings["max_duration_seconds"]
        gaps, violations, samples, cases_attempted, cases_verified = [], [], [], [], []
        pair_observations, controls = [], {}
        sent = 0
        policy_supported = all(ctx.policy_values[key] == value for key, value in SUPPORTED_POLICY.items())
        launch = (ctx.target.get("comparison_config") or {}).get("launch", {})
        supplied = launch.get("chat_template") or {}
        identity = {key: supplied.get(key) for key in
                    ("profile", "version", "sha256", "container_path", "content_format", "request_overrides")}
        argv, image = launch.get("argv") or [], ctx.target.get("engine", {}).get("image_digest")
        contract = None
        try:
            method = getattr(ctx.adapter, "request_binding_contract", None)
            contract = method(image, ctx.env.engine.version) if callable(method) else None
        except Exception:
            pass
        source_verified = bool(type(contract) is dict and contract.get("profile") == PROFILE
            and contract.get("image_digest") == image == ctx.env.engine.image_digest
            and contract.get("engine_version") == ctx.env.engine.version == ctx.target.get("observed_version")
            and contract.get("consumer_prompt_ids") is True and contract.get("consumer_prompt_text") is True
            and contract.get("header_precedence") is True and contract.get("id_prefix") == "chatcmpl-")
        template_supported = False
        try:
            template_supported = (launch.get("profile") == (contract or {}).get("launch_profile", "cpu-bounded-v1")
                and ctx.adapter.supports_template_profile(identity) and type(argv) is list
                and "--chat-template" in argv and argv.index("--chat-template") + 1 < len(argv)
                and argv[argv.index("--chat-template") + 1] == identity["container_path"])
        except Exception:
            pass
        before = self._template_snapshot(ctx, identity, argv, deadline) if source_verified and template_supported else None
        identity_verified = bool(before and before.get("sha256") == identity.get("sha256")
            and all(type(before.get(key)) is str and before[key] for key in ("container_id", "started_at")))
        profile_supported = bool(policy_supported and source_verified and identity_verified and callable(ctx.sprobe))
        if not profile_supported:
            gaps.append("policy, pinned consumer-source contract, managed template identity or collector is unsupported")
        nonce = secrets.token_hex(6)
        texts = {"A": f"Guard attribution A {nonce} amber", "B": f"Guard attribution B {nonce} violet"}
        expected = {side: {"text": rendered_text(text)} for side, text in texts.items()}

        def send(requests, *, stream=False):
            nonlocal sent
            remaining = deadline - time.monotonic()
            if not profile_supported or remaining <= 0 or sent + len(requests) > settings["max_total_requests"]:
                gaps.append("bounded request/time budget or supported profile is unavailable")
                return []
            sent += len(requests)
            try:
                fn = ctx.sprobe if stream else ctx.cprobe
                results = fn(ctx.trusted_net, requests, min(float(self.cfg.get("request_timeout", 120.0)), remaining))
                if (type(results) is not list or len(results) != len(requests)
                        or any(type(result) is not dict for result in results)
                        or len({result.get("id") for result in results}) != len(requests)
                        or {result.get("id") for result in results} != {request["id"] for request in requests}):
                    raise ValueError("unrelated or duplicate collector response labels")
                by_id = {result["id"]: result for result in results}
                results = [by_id[request["id"]] for request in requests]
            except Exception:
                results = [{"id": request["id"], "outcome": "probe_error", "detail": "BINDING_PROBE_UNAVAILABLE"}
                           for request in requests]
            for index, (request, response) in enumerate(zip(requests, results)):
                outcome = response.get("outcome")
                if not (type(outcome) is int and 100 <= outcome <= 599
                        or type(outcome) is str and outcome in {
                            "refused", "timeout", "unreachable", "dns_error", "error", "probe_error"}):
                    response = {"id": request["id"], "outcome": "probe_error", "detail": "BINDING_PROBE_UNAVAILABLE"}
                    results[index] = response
                ctx.record("trusted", "request-binding", request["id"], "allowed", log_metadata(response))
                samples.append({"case": request["id"], "request_fixture_sha256": sha256_hex(canonical_json(request["body"])),
                                "mode": request.get("mode", "json"), "outcome": response["outcome"],
                                "capture": capture_metadata(response)})
            return results

        for side in texts:
            body = {"model": model, "messages": [{"role": "user", "content": texts[side]}],
                    "add_generation_prompt": True, "add_special_tokens": False, "return_token_strs": False}
            request = content_request(ctx, "engine", "tokenize-" + side, "/tokenize", body)
            results = send([request])
            value, error = _response_object(results[0]) if results else (None, "not dispatched")
            if (not error and results[0].get("outcome") == 200 and _ids(value.get("tokens"))
                    and type(value.get("count")) is int and value["count"] == len(value["tokens"])
                    and type(value.get("max_model_len")) is int and value["max_model_len"] == launch.get("max_model_len")
                    and len(value["tokens"]) + settings["max_output_tokens"] <= value["max_model_len"]):
                expected[side]["tokens"] = value["tokens"]
            else:
                gaps.append(f"{side}: complete independently tokenized input is unavailable")
        fixtures_ready = (all("tokens" in fixture for fixture in expected.values())
                          and expected["A"].get("tokens") != expected["B"].get("tokens"))
        if not fixtures_ready:
            gaps.append("two distinct independently tokenized text fixtures were not obtained")

        def inference(side, label, mode, case="generated", *, delay=0):
            body = {"model": model, "messages": [{"role": "user", "content": texts[side]}],
                    "max_tokens": settings["max_output_tokens"], "temperature": 0,
                    "return_token_ids": True, "return_prompt_text": True,
                    "add_generation_prompt": True, "add_special_tokens": False, "stream": mode == "sse"}
            rid = None
            if case == "distinct_body":
                rid = f"guard-{nonce}-{side}"
                body["request_id"] = rid
            elif case == "collision_body":
                rid = f"guard-{nonce}-shared-body"
                body["request_id"] = rid
            elif case == "collision_header":
                body["request_id"] = f"guard-{nonce}-{side}"
                rid = f"guard-{nonce}-shared-header"
            if mode == "sse":
                body["stream_options"] = {"include_usage": True}
            request = content_request(ctx, "engine", label, "/v1/chat/completions", body)
            request.pop("want_body", None)  # the bounded collector always captures the selected response
            request.update(mode=mode, read_delay_ms=delay)
            if case == "collision_header":
                request["headers"]["X-Request-Id"] = rid
            return request, "chatcmpl-" + rid if rid is not None else None

        def analyze(response, side, mode, api_id):
            result = analyze_response(response, expected=expected[side], foreign=expected["B" if side == "A" else "A"],
                                      model=model, max_output=settings["max_output_tokens"],
                                      api_id=api_id, mode=mode)
            violations.extend(f"{response.get('id')}: {reason}" for reason in result["violations"])
            gaps.extend(f"{response.get('id')}: {reason}" for reason in result["gaps"])
            return result

        if fixtures_ready:
            request, rid = inference("A", "before", "json")
            results = send([request], stream=True)
            controls["before"] = "pass" if results and analyze(results[0], "A", "json", rid)["verified"] else "no_response"
            for case_key in CASES:
                mode, case = case_key.split(":")
                pending = [inference(side, f"{mode}-{case}-{side}", mode, case,
                                     delay=settings["read_delay_ms"] if side == "A" else 0) for side in texts]
                results = send([item[0] for item in pending], stream=True)
                if results:
                    cases_attempted.append(case_key)
                analyses = [analyze(result, side, mode, pending[index][1])
                            for index, (side, result) in enumerate(zip(texts, results))]
                timing_fields = ("request_sent_ms", "end_ms")
                overlap = bool(len(results) == 2 and all(type(result.get(key)) in {int, float}
                    and math.isfinite(result[key]) and result[key] >= 0 for result in results for key in timing_fields)
                    and max(result["request_sent_ms"] for result in results) < min(result["end_ms"] for result in results)
                    and all(result["end_ms"] >= result["request_sent_ms"] for result in results))
                connections = bool(len(results) == 2 and all(type(result.get("connection_id")) is str
                    and result["connection_id"] for result in results)
                    and results[0]["connection_id"] != results[1]["connection_id"])
                if not overlap or not connections:
                    gaps.append(f"{case_key}: two distinct overlapping HTTP collection intervals were not observed")
                verified = bool(len(analyses) == 2 and all(item["verified"] for item in analyses) and overlap and connections)
                if verified:
                    cases_verified.append(case_key)
                pair_observations.append({"case": case_key, "overlap": overlap, "distinct_connections": connections,
                    "verified": verified, "responses": [_analysis_projection(item) for item in analyses],
                    "timings": [_timing_projection(result) for result in results]})
            request, rid = inference("A", "after", "json")
            results = send([request], stream=True)
            controls["after"] = "pass" if results and analyze(results[0], "A", "json", rid)["verified"] else "no_response"
        controls = {label: controls.get(label, "no_response") for label in ("before", "after")}
        normal_ok = all(state == "pass" for state in controls.values())
        after = self._template_snapshot(ctx, identity, argv, deadline) if identity_verified else None
        identity_verified = bool(after and before == after)
        if not identity_verified:
            gaps.append("owned engine and fixed template identity were not stable across collection")
        complete = bool(profile_supported and identity_verified and not gaps and not violations
                        and cases_verified == list(CASES) and normal_ok)
        # This verifies returned consumer-input projections and response framing,
        # not which request produced the generated content/token IDs. In
        # particular, an output-only swap can preserve every checked projection.
        input_projection_verified = complete
        output_binding_gap = ("Generated output attribution is not independently verified: "
            "matching prompt projections, API IDs and token counts are insufficient.")
        obs_values = {"profile_supported": profile_supported, "source_contract_verified": source_verified,
            "template_identity_verified": identity_verified,
            "connection_binding_verified": cases_verified == list(CASES),  # legacy case-coverage observation
            "input_projection_verified": input_projection_verified,
            "output_binding_verified": False,
            "output_binding_gap": output_binding_gap,
            "connection_overlap_verified": len(pair_observations) == len(CASES) and all(item["overlap"] for item in pair_observations),
            "tested_result_binding": ctx.policy_values["result_binding"],
            "tested_client_request_id_trust": ctx.policy_values["client_request_id_trust"],
            "cases_planned": list(CASES), "cases_attempted": cases_attempted, "cases_verified": cases_verified,
            "security_violations": sorted(set(violations)), "collection_complete": complete,
            "normal_controls": normal_ok, "functional_probes_planned": ["before", "after"],
            "functional_probe_results": controls}
        observations = {key: {"value": value, "collected": True} for key, value in obs_values.items()}
        summary = {"profile": PROFILE, "artifact_profile": ARTIFACT_PROFILE,
            "requests_sent": sent, "settings": settings,
            "verification_scope": {"input_projection_verified": input_projection_verified,
                "output_binding_verified": False, "output_binding_gap": output_binding_gap,
                "cases_verified_meaning": "input projection, connection overlap and response framing only",
                "collection_complete_meaning": "bounded collector protocol complete; not generated-output ownership proof"},
            "source_contract": contract, "gaps": sorted(set(gaps)), "violations": sorted(set(violations)),
            "template_identity_before": before, "template_identity_after": after,
            "pairs": pair_observations,
            "fixtures": {side: {key + "_sha256": sha256_hex(canonical_json(value)) for key, value in fixture.items()}
                         for side, fixture in expected.items()},
            "fixture": {"profile": PROFILE, "settings": settings, "case_order": list(CASES),
                        "body_shape": {"user": "per-run synthetic A/B text", "output": settings["max_output_tokens"]}},
            "elapsed_seconds": time.monotonic() - started,
            "limitations": ["HTTP request/collection intervals overlap; worker-side simultaneous execution is not proved.",
                output_binding_gap,
                "connection_binding_verified and cases_verified describe input-projection subproperties only; CheckDef rev3 withholds security PASS.",
                "Caller-controlled API IDs may intentionally collide; a collision alone is not a foreign-result finding.",
                "Only the pinned CPU text fixture and two synthetic direct-engine connections are covered.",
                "Enterprise principals, gateway authorization, child IDs, randomization modes, cancellation, retries and stored results remain untested.",
                "Generated prose is never an attribution oracle. Token IDs and rendered prompts are returned consumer-side projections.",
                "Timeouts bound each dispatch batch and the shared deadline; Docker inspection and cleanup have their own bounds."]}
        sample_artifact(ctx, "request_binding_samples.json", {"artifact_profile": ARTIFACT_PROFILE,
                                                            "samples": samples, "summary": summary})
        return observations, {"attempted": "before" in {sample["case"] for sample in samples},
            "succeeded": normal_ok, "detail": "before/after fixed chat inference with exact consumer IDs and rendered input"}, [
                {"step": step, "attempted": case in cases_attempted} for step, case in zip(ctx.check.challenge_steps, CASES)], summary
