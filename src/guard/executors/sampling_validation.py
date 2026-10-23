"""REQ-10 bounded sampling API observations; runner/count consumption stays unverified.

Only an engine POST /v1/completions text fixture is supported. The supplied
constraint source is DECLARED context, never proof that the running processor
uses those limits. No V2-sized arrays, NaN JSON or mutable admin requests are sent.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import math
import threading
import time

from .http_common import HttpCheckExecutor, functional_requests


SUPPORTED_OPTIONS = ("temperature", "top_p", "min_tokens")
PROFILE = "sampling-api-v1"


def _strings(value, label):
    if (type(value) is not list or not value or any(type(v) is not str or not v.strip() for v in value)
            or len(set(value)) != len(value)):
        raise ValueError(f"{label} must be a nonempty unique string list")
    return value


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


class SamplingValidationExecutor(HttpCheckExecutor):
    name = "sampling_validation"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False

    def validate_policy(self, check, values):
        options = _strings(values.get("allowed_sampling_options"), "allowed_sampling_options")
        if set(options) - set(SUPPORTED_OPTIONS):
            raise ValueError("sampling-api-v1 supports only temperature, top_p and min_tokens; policy is not reduced")
        codes = _strings(values.get("rejection_codes"), "rejection_codes")
        if set(codes) - {"400", "422"}:
            raise ValueError("rejection_codes must explicitly use 400 and/or 422")
        for name in ("constraint_source", "constraint_engine_version"):
            value = self.cfg.get(name)
            if type(value) is not str or not value.strip() or value.strip().lower() in {"unknown", "null", "none"}:
                raise ValueError(f"sampling execution needs explicit {name}; it remains declared context")
        _integer(self.cfg.get("max_output_tokens"), "max_output_tokens", 1, 32)
        _integer(self.cfg.get("max_total_requests"), "max_total_requests", 1 + 5 * len(options), 64)
        _integer(self.cfg.get("max_probe_tokens"), "max_probe_tokens", 1, 4096)
        _integer(self.cfg.get("max_duration_seconds"), "max_duration_seconds", 1, 300)
        timeout = self.cfg.get("request_timeout", 3.0)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError("request_timeout must be finite, positive and at most 30 seconds")
        if len(check.functional_probes) != 1:
            raise ValueError("sampling-api-v1 needs exactly one engine text completion fixture")
        fp = check.functional_probes[0]
        body = fp.body or {}
        if (fp.via != "engine" or fp.method != "POST" or fp.path != "/v1/completions"
                or type(body.get("prompt")) is not str or not body["prompt"].strip()
                or set(body) != {"model", "prompt", "max_tokens"}
                or body.get("max_tokens") != 1 or type(body.get("max_tokens")) is not int
                or fp.expect != [200]):
            raise ValueError("sampling-api-v1 supports the pinned engine POST /v1/completions text fixture only")
        # Character count is a conservative input-byte fixture budget, not a
        # claim that tokenization or the active model vocabulary was observed.
        if len(body["prompt"].encode("utf-8")) > self.cfg["max_probe_tokens"]:
            raise ValueError("text fixture exceeds bounded UTF-8 input budget")

    def collect(self, ctx):
        # This method may also be called directly by contract tests.
        self.validate_policy(ctx.check, ctx.policy_values)
        if ctx.env.engine.version != self.cfg["constraint_engine_version"]:
            raise ValueError("constraint_engine_version must match selected ENV; source authenticity is unverified")
        requests, probes = functional_requests(ctx, via="engine")
        template = requests[0]
        fp = next(iter(probes.values()))
        template["want_body"] = True
        template["body"]["max_tokens"] = self.cfg["max_output_tokens"]
        options = ctx.policy_values["allowed_sampling_options"]
        boundaries = {"temperature": (0.0, -1.0), "top_p": (1.0, 0.0),
                      "min_tokens": (0, self.cfg["max_output_tokens"] + 1)}
        controls, outcomes, health, pairs, attempts = {}, {}, {}, {}, []
        started = time.monotonic()
        deadline = started + self.cfg["max_duration_seconds"]
        sent = 0
        dispatch_lock = threading.Lock()

        def send(request, label):
            nonlocal sent
            remaining = deadline - time.monotonic()
            with dispatch_lock:
                if remaining <= 0 or sent >= self.cfg["max_total_requests"]:
                    return {"id": label, "outcome": "budget_exhausted", "dispatched": False}
                sent += 1
            request = deepcopy(request)
            request["id"] = label
            try:
                result = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, remaining))
                if len(result) != 1 or not isinstance(result[0], dict) or result[0].get("id") != label:
                    return {"id": label, "outcome": "incomplete_probe_result", "dispatched": True}
                return {**result[0], "dispatched": True}
            except Exception:
                # Do not persist dependency exception text or credentials.
                return {"id": label, "outcome": "probe_error", "dispatched": True}

        def record(label, response):
            ctx.record("trusted", "sampling-validation", label, fp.as_role, response)

        def inference(response):
            if response.get("outcome") != 200 or type(response.get("outcome")) is not int:
                return "unknown"
            try:
                body = json.loads(response.get("body") or "")
                choices = body["choices"]
                usage = body["usage"]
                if (type(choices) is list and len(choices) == 1 and type(choices[0]) is dict
                        and type(choices[0].get("text")) is str
                        and choices[0].get("finish_reason") in {"stop", "length"}
                        and body.get("model") == ctx.env.model.id
                        and type(usage.get("completion_tokens")) is int
                        and 0 < usage["completion_tokens"] <= self.cfg["max_output_tokens"]):
                    return "pass"
            except (ValueError, TypeError, KeyError, AttributeError):
                pass
            return "unknown"

        initial = send(template, "sampling-before")
        record("before", initial)
        controls["before"] = inference(initial)
        for option in options:
            if controls["before"] != "pass" or (controls and list(controls.values())[-1] != "pass"):
                break
            if time.monotonic() >= deadline or sent + 5 > self.cfg["max_total_requests"]:
                break
            valid, invalid = boundaries[option]
            boundary = deepcopy(template)
            boundary["body"][option] = valid
            response = send(boundary, f"sampling-{option}-boundary")
            record(f"{option}:boundary", response)
            controls[f"{option}:boundary"] = inference(response)
            if controls[f"{option}:boundary"] != "pass":
                break
            challenge = deepcopy(boundary)
            challenge["body"][option] = invalid
            # Dispatch two probe invocations concurrently; a serial list sent to
            # the existing HTTP probe is not a concurrent request test. This
            # does not establish overlap inside the scheduler/worker.
            with ThreadPoolExecutor(max_workers=2) as pool:
                bad_future = pool.submit(send, challenge, f"sampling-{option}-challenge")
                good_future = pool.submit(send, template, f"sampling-{option}-concurrent")
                bad, good = bad_future.result(), good_future.result()
            if bad.get("dispatched"):
                attempts.append(option)
            record(f"{option}:challenge", bad)
            record(f"{option}:concurrent", good)
            value = bad.get("outcome")
            outcomes[option] = str(value) if type(value) is int else value or "no_response"
            controls[f"{option}:concurrent"] = inference(good)
            pairs[option] = {"challenge_id": f"sampling-{option}-challenge",
                             "control_id": f"sampling-{option}-concurrent", "dispatch": "two_threads",
                             "engine_execution_overlap_verified": False}
            follow = send(template, f"sampling-{option}-after")
            record(f"{option}:after", follow)
            controls[f"{option}:after"] = inference(follow)
            check = {"method": "GET", "url": f"{ctx.engine_url}/health"}
            alive = send(check, f"sampling-{option}-health")
            record(f"{option}:health", alive)
            health[option] = str(alive.get("outcome"))
            if controls[f"{option}:concurrent"] != "pass" or controls[f"{option}:after"] != "pass":
                break

        planned_controls = ["before", *(f"{o}:{phase}" for o in options for phase in ("boundary", "concurrent", "after"))]
        complete = set(controls) == set(planned_controls) and all(v == "pass" for v in controls.values())
        obs = {
            "sampling_options_planned": {"value": options, "collected": True},
            "sampling_options_tested": {"value": attempts, "collected": True},
            "sampling_challenge_outcomes": {"value": outcomes, "collected": bool(outcomes)},
            "sampling_accepted_options": {"value": [k for k, v in outcomes.items() if type(v) is str and v.isdigit()
                                                      and 200 <= int(v) < 300], "collected": bool(outcomes)},
            "functional_probes_planned": {"value": planned_controls, "collected": True},
            "functional_probe_results": {"value": controls, "collected": True},
            "sampling_health_results": {"value": health, "collected": bool(health)},
            "runner_consumption_verified": {"value": False, "collected": True},
        }
        normal = {"attempted": True, "succeeded": complete,
                  "detail": "text completions with structured output before, concurrent with, and after bounded challenges; health alone is insufficient"}
        challenge_log = [{"step": step, "attempted": len(attempts) == len(options)} for step in ctx.check.challenge_steps]
        return obs, normal, challenge_log, {
            "profile": PROFILE, "requests_sent": sent, "elapsed_seconds": time.monotonic() - started,
            "concurrent_pairs": pairs, "health": health,
            "limitations": ["Constraint source/version are DECLARED and not independently authenticated.",
                            "Only bounded API option filtering and text completion responses are observed.",
                            "V2 activation, effective runner/worker values, count limits and actual consumer are not measured; security remains UNKNOWN.",
                            "Two probes are dispatched concurrently; scheduler/worker execution overlap is not measured.",
                            "NaN/infinity, allowed-token/logit-bias/stop-count boundaries, other endpoints and formats are untested.",
                            "5xx, timeout, malformed replies and incomplete probes do not prove shared-engine damage."],
            "fixture": {"profile": PROFILE, "control": template["body"], "options": options,
                        "boundaries": {o: {"valid": boundaries[o][0], "invalid": boundaries[o][1]} for o in options},
                        "constraint_source": self.cfg["constraint_source"], "constraint_origin": "DECLARED",
                        "constraint_engine_version": self.cfg["constraint_engine_version"],
                        "endpoint": f"{template['method']} {fp.path}", "input_format": "text"},
        }
