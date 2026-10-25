"""REQ-07: bounded fixed-template rendering and actual consumed prompt IDs.

Token counts alone never establish equivalence. The engine adapter must identify
the supported pre-generation rejection; arbitrary 400 replies are inconclusive.
Only a fixed literal override is sent, never user-supplied Jinja or a large load.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
import secrets
import time

from guard.automation_policy import validate_template_policy
from guard.runtime.docker import Docker
from .content_common import (append_artifact, content_request, control_state, json_body,
                             record_content, sample_artifact)
from .http_common import HttpCheckExecutor
from .engine_identity import owned_template_snapshot
from . import template_supply_approval


PROFILE = "fixed-template-input-v1"
SAFE_OVERRIDE = "GUARD_SAFE_OVERRIDE_ONLY\n"
STEPS = ("normal", "boundary", "overbound", "override")
DEFAULTS = {"max_output_tokens": 1, "max_total_requests": 48,
            "max_probe_tokens": 4096, "max_total_probe_tokens": 16384,
            "max_input_bytes": 4096, "max_duration_seconds": 300}


def _positive(value, label, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [1, {maximum}]")
    return value


def _token_ids(value, maximum):
    return (type(value) is list and 0 < len(value) <= maximum
            and all(type(token) is int and token >= 0 for token in value))


class TemplateValidationExecutor(HttpCheckExecutor):
    name = "template_validation"
    version = "0.2.0"
    requires_roles = False
    requires_route_scope = False

    def __init__(self, cfg, *args, docker=None, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self.docker = docker or Docker("template" + secrets.token_hex(4))

    def _identity_snapshot(self, ctx, identity, argv, deadline):
        """Read only the owned running engine and selected readonly template.

        Inspect may contain secrets in memory; persist only this explicit
        projection. Host mount paths, raw daemon replies and errors stay out.
        """
        return owned_template_snapshot(self.docker, ctx, identity, argv, deadline, monotonic=time.monotonic)

    def validate_policy(self, check, values):
        if getattr(check, "collection_profile", None) == template_supply_approval.PROFILE:
            template_supply_approval.validate_policy(check, values, self.cfg)
            return
        validate_template_policy(values)
        limits = {"max_output_tokens": 32, "max_total_requests": 64, "max_probe_tokens": 4096,
                  "max_total_probe_tokens": 65536, "max_input_bytes": 4096, "max_duration_seconds": 300}
        for key, maximum in limits.items():
            _positive(self.cfg.get(key, DEFAULTS[key]), key, maximum)
        timeout = self.cfg.get("request_timeout", 3.0)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("request_timeout must be finite, positive and at most 120 seconds")
        if self.cfg.get("endpoint", "/v1/chat/completions") != "/v1/chat/completions":
            raise ValueError("fixed-template-input-v1 requires the chat completions endpoint")
        if self.cfg.get("tokenize_endpoint", "/tokenize") != "/tokenize":
            raise ValueError("fixed-template-input-v1 requires the pinned tokenize endpoint")
        if len(check.functional_probes) != 1:
            raise ValueError("one fixed engine chat functional fixture is required")
        fp = check.functional_probes[0]
        body = fp.body or {}
        if (fp.via != "engine" or fp.method != "POST" or fp.path != "/v1/chat/completions"
                or fp.expect != [200] or set(body) != {"model", "messages", "max_tokens"}
                or body.get("model") != "{model}" or type(body.get("max_tokens")) is not int
                or body["max_tokens"] != 1 or type(body.get("messages")) is not list
                or len(body["messages"]) != 1 or type(body["messages"][0]) is not dict
                or set(body["messages"][0]) != {"role", "content"}
                or body["messages"][0]["role"] != "user"
                or type(body["messages"][0]["content"]) is not str
                or not body["messages"][0]["content"].strip()):
            raise ValueError("only the pinned single-user chat fixture is supported")
        if len(body["messages"][0]["content"].encode()) > self.cfg.get("max_input_bytes", DEFAULTS["max_input_bytes"]):
            raise ValueError("normal fixture exceeds input byte budget")
        if len(check.challenge_steps) != len(STEPS):
            raise ValueError("fixed-template-input-v1 needs the four declared challenge steps")

    def _finish(self, ctx):
        return append_artifact(super()._finish(ctx), ctx, "template_samples.json", f"{self.name}/{self.version}")

    def collect(self, ctx):
        if getattr(ctx.check, "collection_profile", None) == template_supply_approval.PROFILE:
            return template_supply_approval.collect(self, ctx)
        self.validate_policy(ctx.check, ctx.policy_values)
        settings = {key: self.cfg.get(key, value) for key, value in DEFAULTS.items()}
        values = ctx.policy_values
        started = time.monotonic()
        deadline = started + settings["max_duration_seconds"]
        samples, gaps, violations, counts, controls, consumption = [], [], [], {}, {}, {}
        consumed_counts = {}
        attempted = dict.fromkeys(STEPS, False)
        sent = 0
        token_budget_used = 0
        fp = ctx.check.functional_probes[0]
        template = deepcopy(fp.body["messages"])
        target_launch = (ctx.target.get("comparison_config") or {}).get("launch", {})
        supplied_identity = target_launch.get("chat_template") or {}
        identity = {key: supplied_identity.get(key) for key in
                    ("profile", "version", "sha256", "container_path", "content_format", "request_overrides")}
        template_sha = identity.get("sha256")
        selected = type(template_sha) is str and bool(re.fullmatch(r"[0-9a-f]{64}", template_sha))
        argv = target_launch.get("argv") or []
        container_path = identity.get("container_path")
        argv_selects = (type(argv) is list and "--chat-template" in argv
                        and argv.index("--chat-template") + 1 < len(argv)
                        and argv[argv.index("--chat-template") + 1] == container_path)
        image = ctx.target.get("engine", {}).get("image_digest")
        image_matches = bool(image) and image == ctx.env.engine.image_digest
        verified_identity = bool(selected and argv_selects and image_matches
                                 and identity.get("profile") == "guard-text-chat-v1"
                                 and identity.get("version") == "1"
                                 and identity.get("content_format") == "string"
                                 and identity.get("request_overrides") is False)
        profile_check = getattr(ctx.adapter, "supports_template_profile", None)
        try:
            verified_identity = bool(verified_identity and callable(profile_check) and profile_check(identity))
        except Exception:
            verified_identity = False
        identity_before = self._identity_snapshot(ctx, identity, argv, deadline) if verified_identity else None
        verified_identity = bool(verified_identity and identity_before
                                 and identity_before["sha256"] == template_sha)
        if not verified_identity:
            gaps.append("managed fixed template selection, launch identity or pinned image is unverified")
        if verified_identity and template_sha not in values["allowed_template_sha256"]:
            violations.append("the selected template digest is outside the explicit policy allowlist")
        if values["request_template_override_allowed"]:
            gaps.append("request-template overrides allowed by policy are outside this restricted profile")

        def send(label, endpoint, body):
            nonlocal sent
            if not verified_identity:
                return {"outcome": "identity_unverified", "dispatched": False}
            remaining = deadline - time.monotonic()
            if remaining <= 0 or sent >= settings["max_total_requests"] or token_budget_used >= settings["max_total_probe_tokens"]:
                gaps.append(f"{label}: bounded request, time or observed-token budget exhausted")
                return {"outcome": "budget_exhausted", "dispatched": False}
            request = content_request(ctx, "engine", label, endpoint, body)
            sent += 1
            try:
                results = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, remaining))
                if (type(results) is not list or len(results) != 1 or type(results[0]) is not dict
                        or results[0].get("id") != label):
                    raise ValueError("invalid or unrelated probe response")
                result = {**results[0], "dispatched": True}
            except Exception as exc:
                result = {"outcome": "probe_error", "phase": "collection",
                          "detail": type(exc).__name__, "dispatched": True}
            record_content(ctx, "trusted", "template-validation", label, result)
            samples.append({"case": label, "request": body, "response": result})
            return result

        def tokenize(label, messages, extra=None, *, raw=False):
            nonlocal token_budget_used
            body = {"model": ctx.env.model.id, "add_special_tokens": False, "return_token_strs": False}
            if raw:
                body["prompt"] = messages[0]["content"]
            else:
                body.update(messages=messages, add_generation_prompt=True)
            body.update(extra or {})
            response = send(label + "-tokenize", "/tokenize", body)
            value, error = json_body(response)
            if (response.get("outcome") != 200 or error or not _token_ids(value.get("tokens"), settings["max_probe_tokens"])
                    or type(value.get("count")) is not int or value["count"] != len(value["tokens"])
                    or type(value.get("max_model_len")) is not int or value["max_model_len"] <= 0
                    or value["max_model_len"] != target_launch.get("max_model_len")):
                gaps.append(f"{label}: complete tokenized input and exact token count unavailable")
                return None, response
            token_budget_used += len(value["tokens"])
            if token_budget_used > settings["max_total_probe_tokens"]:
                gaps.append(f"{label}: observed token count exceeds collection budget")
                return None, response
            counts[label] = len(value["tokens"])
            return value, response

        def inference(label, messages, rendered=None, extra=None):
            nonlocal token_budget_used
            body = {"model": ctx.env.model.id, "messages": messages,
                    "max_tokens": settings["max_output_tokens"], "temperature": 0,
                    "return_token_ids": True, "add_generation_prompt": True, "add_special_tokens": False}
            body.update(extra or {})
            response = send(label, "/v1/chat/completions", body)
            state = control_state(response, ctx.env.model.id, "/v1/chat/completions")
            value, error = json_body(response)
            ids = value.get("prompt_token_ids") if not error else None
            if state == "pass":
                usage = value.get("usage", {})
                if (len(value["choices"]) != 1 or usage.get("completion_tokens", 0) > settings["max_output_tokens"]
                        or _token_ids(ids, settings["max_probe_tokens"]) and usage.get("prompt_tokens") != len(ids)):
                    state = "no_response"
            matches = bool(state == "pass" and rendered is not None
                           and _token_ids(ids, settings["max_probe_tokens"]) and ids == rendered["tokens"])
            if state == "pass":
                consumption[label] = matches
                if _token_ids(ids, settings["max_probe_tokens"]):
                    token_budget_used += len(ids)
                if not matches:
                    gaps.append(f"{label}: actual prompt_token_ids are absent or differ from the selected render")
                else:
                    consumed_counts[label] = len(ids)
                    if len(ids) > values["max_rendered_input_tokens"]:
                        violations.append(f"{label}: rendered input above the policy token limit was actually consumed")
            if label in {"before", "boundary", "after"}:
                controls[label] = state if matches else "no_response"
            return response, matches

        def rejection(response, kind, rendered=None):
            method = getattr(ctx.adapter, "template_rejection_evidence", None)
            if not callable(method):
                return None
            try:
                _, error = json_body(response)
                if error:
                    return None
                proof = method(response, kind=kind,
                               prompt_tokens=len(rendered["tokens"]) if rendered else None,
                               output_tokens=settings["max_output_tokens"],
                               max_model_len=rendered.get("max_model_len") if rendered else None)
                if (type(proof) is dict and proof.get("engine_version") == ctx.env.engine.version
                        and proof["engine_version"] == ctx.target.get("observed_version")
                        and proof.get("image_digest") == image):
                    return proof
                return None
            except Exception:
                return None

        before, before_tokens_response = tokenize("before", template)
        before_response, _ = inference("before", template, before)
        attempted["normal"] = before_tokens_response.get("dispatched", False) and before_response.get("dispatched", False)
        boundary_messages = overbound_messages = None
        boundary = overbound = None
        policy_limit = values["max_rendered_input_tokens"]
        # No assumption about BPE monotonicity becomes evidence. Binary search
        # is only a bounded fixture finder; exact measured counts are required.
        cache = {}
        max_repetitions = min(settings["max_input_bytes"] // 2, settings["max_probe_tokens"])

        def measure(repetitions):
            if repetitions not in cache:
                message = [{"role": "user", "content": "x " * repetitions}]
                rendered, _ = tokenize(f"search-{repetitions}", message)
                cache[repetitions] = (message, rendered)
            return cache[repetitions]

        if before is not None and controls.get("before") == "pass":
            if policy_limit + 1 > settings["max_probe_tokens"]:
                gaps.append("policy boundary exceeds bounded token-fixture support; policy was not reduced")
            else:
                low, high = 1, max_repetitions
                while low <= high and sent < settings["max_total_requests"] - 8 and time.monotonic() < deadline:
                    mid = (low + high) // 2
                    message, rendered = measure(mid)
                    if rendered is None:
                        break
                    count = len(rendered["tokens"])
                    if count == policy_limit:
                        boundary_messages, boundary = message, rendered
                    if count == policy_limit + 1:
                        overbound_messages, overbound = message, rendered
                    if count < policy_limit + 1:
                        low = mid + 1
                    else:
                        high = mid - 1
                # A bounded local neighbourhood handles token merges without
                # silently substituting a different policy boundary.
                for repetitions in range(max(1, high - 2), min(max_repetitions, low + 2) + 1):
                    if boundary is not None and overbound is not None:
                        break
                    if sent >= settings["max_total_requests"] - 8 or time.monotonic() >= deadline:
                        break
                    message, rendered = measure(repetitions)
                    if rendered is not None and len(rendered["tokens"]) == policy_limit:
                        boundary_messages, boundary = message, rendered
                    if rendered is not None and len(rendered["tokens"]) == policy_limit + 1:
                        overbound_messages, overbound = message, rendered
        if boundary is not None:
            counts["boundary"] = len(boundary["tokens"])
            response, _ = inference("boundary", boundary_messages, boundary)
            attempted["boundary"] = response.get("dispatched", False)
        else:
            gaps.append("an exact inside-boundary render was not obtained")

        enforcement = False
        enforcement_proof = None
        if overbound is not None:
            counts["overbound"] = len(overbound["tokens"])
            raw, _ = tokenize("overbound-raw", overbound_messages, raw=True)
            short_before_render = bool(raw is not None and len(raw["tokens"]) <= policy_limit)
            if not short_before_render:
                gaps.append("raw input within policy but expanded render outside policy was not confirmed")
            response, consumed = inference("overbound", overbound_messages, overbound)
            attempted["overbound"] = response.get("dispatched", False)
            enforcement_proof = rejection(response, "input_limit", overbound)
            enforcement = bool(enforcement_proof and short_before_render)
            if not consumed and not enforcement:
                gaps.append("overbound response does not establish the supported pre-generation input rejection")
        else:
            gaps.append("an exact rendered policy-boundary-plus-one fixture was not obtained")

        blocked = []
        override_proofs = []
        for name, extra in (("template", {"chat_template": SAFE_OVERRIDE}),
                            ("kwargs", {"chat_template_kwargs": {"chat_template": SAFE_OVERRIDE}})):
            # Server-template tokenization is the independent expected render.
            response, consumed_server = inference("override-" + name, template, before, extra)
            override_proof = rejection(response, "request_override", before)
            applied = False
            if response.get("outcome") == 200 and not consumed_server:
                changed, _ = tokenize("override-" + name, template, extra)
                value, error = json_body(response)
                applied = bool(not error and changed is not None and before is not None
                               and changed["tokens"] != before["tokens"]
                               and value.get("prompt_token_ids") == changed["tokens"]
                               and control_state(response, ctx.env.model.id, "/v1/chat/completions") == "pass")
            if applied and values["request_template_override_allowed"] is False:
                violations.append(f"request {name} override changed the actually consumed prompt tokens")
            blocked.append(bool(override_proof or consumed_server))
            override_proofs.append({"case": name, "dispatched": response.get("dispatched", False),
                                    "applied": applied, "consumed_server_render": consumed_server,
                                    "rejection": override_proof})
            if not blocked[-1] and not applied:
                gaps.append(f"override-{name}: rejection, ignoring or actual selection could not be established")
        attempted["override"] = all(item["dispatched"] for item in override_proofs)
        after, _ = tokenize("after", template)
        inference("after", template, after)
        planned = ["before", "boundary", "after"]
        normal_ok = set(controls) == set(planned) and all(value == "pass" for value in controls.values())
        if not normal_ok:
            gaps.append("normal before, boundary and after inference/consumed-token controls are incomplete")
        identity_after = self._identity_snapshot(ctx, identity, argv, deadline) if verified_identity else None
        if not identity_after or identity_after != identity_before:
            verified_identity = False
            gaps.append("owned engine and readonly template identity were not stable across the trials")
        complete = (not gaps and all(attempted.values()) and enforcement and all(blocked)
                    and verified_identity and normal_ok and all(consumption.values()))
        tested_limit = (len(boundary["tokens"]) if boundary is not None and overbound is not None
                        and len(overbound["tokens"]) == len(boundary["tokens"]) + 1
                        and attempted["boundary"] and attempted["overbound"] else None)
        obs = {
            "selected_template_sha256": {"value": template_sha, "collected": selected},
            "template_identity_verified": {"value": verified_identity, "collected": True},
            "rendered_prompt_tokens_exact_match": {"value": bool(consumption) and all(consumption.values()), "collected": True},
            "rendered_input_token_counts": {"value": counts, "collected": bool(counts)},
            "max_consumed_prompt_tokens": {"value": max(consumed_counts.values()) if consumed_counts else None,
                                           "collected": bool(consumed_counts)},
            "tested_rendered_input_limit": {"value": tested_limit, "collected": tested_limit is not None},
            "enforcement_observed": {"value": enforcement, "collected": True},
            "request_override_rejected": {"value": all(blocked), "collected": True},
            "security_violations": {"value": violations, "collected": True},
            "collection_complete": {"value": bool(complete), "collected": True},
            "normal_controls": {"value": normal_ok, "collected": True},
            "functional_probes_planned": {"value": planned, "collected": True},
            "functional_probe_results": {"value": controls, "collected": True},
        }
        summary = {"profile": PROFILE, "requests_sent": sent, "observed_tokens": token_budget_used,
                   "elapsed_seconds": time.monotonic() - started, "gaps": gaps,
                   "consumed_prompt_token_counts": consumed_counts,
                   "violations": violations, "selected_template": identity,
                   "template_identity_before": identity_before, "template_identity_after": identity_after,
                   "enforcement_proof": enforcement_proof, "override_proofs": override_proofs,
                   "fixture": {"profile": PROFILE, "normal": template, "settings": settings,
                               "override": SAFE_OVERRIDE, "endpoints": ["/tokenize", "/v1/chat/completions"]},
                   "limitations": ["Only the selected fixed text-chat profile and bounded fixtures were exercised.",
                                   "A token count or 2xx/4xx alone does not prove rendering, consumption or rejection location.",
                                   "Allowlist policy values are supplied decisions, not independently authenticated approval.",
                                   "Pinned image and observed API version do not independently verify an engine source commit.",
                                   "Request overrides allowed by policy and other processors/templates remain unsupported.",
                                   "Observed-token bounds apply after responses; input bytes, request count and time are bounded before dispatch."]}
        sample_artifact(ctx, "template_samples.json", {"samples": samples, "summary": summary})
        return obs, {"attempted": attempted["normal"], "succeeded": normal_ok,
                     "detail": "before, exact boundary and after chat inference with exact consumed prompt-token correspondence"}, [
                         {"step": step, "attempted": attempted[key]} for step, key in zip(ctx.check.challenge_steps, STEPS)], summary
