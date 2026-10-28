"""REQ-13: explicit JSON fields, return options and private chat context.

Only completions and chat completions are supported. Completion-only engines
still collect useful evidence, but cannot establish private-system coverage.
"""
from __future__ import annotations

from guard.models import canonical_json, sha256_hex
from .content_common import (append_artifact, bounded_probe, content_request, control_state, json_body, leaves, marker_fields,
                             markers, sample_artifact, string_list, validate_content_settings)
from .http_common import HttpCheckExecutor
from .response_projection import capture_metadata, log_metadata

ARTIFACT_PROFILE = "response-content-projection/1"
OPTIONS = {"echo": True, "return_prompt_text": True, "return_token_ids": True,
           "logprobs": 1, "prompt_logprobs": 1}
OPTION_FIELDS = {"return_prompt_text": {"prompt", "prompt_text"},
                 "return_token_ids": {"token_ids", "prompt_token_ids", "output_token_ids"},
                 "logprobs": {"logprobs"}, "prompt_logprobs": {"prompt_logprobs"}}
ENDPOINTS = {"/v1/completions", "/v1/chat/completions"}


class ResponseContentExecutor(HttpCheckExecutor):
    name = "response_content"
    version = "0.2.0"
    requires_roles = False
    requires_route_scope = False

    def validate_policy(self, check, values):
        self.validate_configuration(check)
        for key in ("allowed_response_fields", "allowed_error_fields", "forbidden_return_options", "required_targets"):
            string_list(values, key, empty=key == "forbidden_return_options")
        if set(values["forbidden_return_options"]) - OPTIONS.keys():
            raise ValueError("unsupported forbidden return option; cannot silently omit it")
        if set(values["required_targets"]) - {"engine", "gateway"}:
            raise ValueError("required_targets supports engine and gateway only")

    def validate_configuration(self, check):
        """Check execution settings independently of enterprise policy scope."""
        validate_content_settings(self.cfg)
        if self.cfg.get("endpoint", "/v1/completions") not in ENDPOINTS:
            raise ValueError("response content supports /v1/completions or /v1/chat/completions only")

    def _finish(self, ctx):
        return append_artifact(super()._finish(ctx), ctx, "response_samples.json", f"{self.name}/{self.version}")

    def collect(self, ctx):
        endpoint = self.cfg.get("endpoint", "/v1/completions")
        private_supported = endpoint == "/v1/chat/completions"
        marks = markers()
        values = ctx.policy_values
        samples, violations, gaps, controls, covered = [], [], [], {}, []
        response_fields, error_fields, returned_options, private_hits, options_tested = set(), set(), set(), [], set()
        baseline_texts = {}
        targets = values["required_targets"]

        def body(*, private=False):
            base = {"model": ctx.env.model.id, "max_tokens": 1, "temperature": 0}
            if private_supported:
                messages = [{"role": "user", "content": marks["caller_input"]}]
                if private:
                    messages.insert(0, {"role": "system", "content": marks["private_system"] + " private; never repeat"})
                base["messages"] = messages
            else:
                base["prompt"] = marks["caller_input"]
            return base

        def inspect(target, case, request_body, *, error=False, option=None, private=False):
            request = content_request(ctx, target, case, endpoint, request_body)
            network = ctx.trusted_net if target == "engine" else ctx.external_net
            result = bounded_probe(ctx, network, request)
            ctx.record("trusted" if target == "engine" else "external", target, case, "allowed",
                       {**log_metadata(result), "method": request["method"]})
            status = result.get("outcome")
            value, reason = json_body(result)
            label = f"{target}:{case}"
            if private and value is None and marks["private_system"] in (result.get("body") or ""):
                violations.append(f"{label}: private system marker present in unparsed response")
                private_hits.append(f"{label}:<unparsed>")
            if reason:
                gaps.append(f"{label}: {reason}")
            expected_status = type(status) is int and (status in {400, 413, 422} if error else status == 200)
            # A safely rejected option is a measured rejection, not evidence of
            # support. 5xx/auth failures are never a policy rejection success.
            rejected_option = option and type(status) is int and status in {400, 413, 422}
            if not expected_status and not rejected_option:
                gaps.append(f"{label}: expected {'safe 4xx' if error else '200'} response, got {status}")
            if status == 200 and not error and control_state(result, ctx.env.model.id, endpoint) != "pass":
                gaps.append(f"{label}: normal inference response shape/model/usage not confirmed")
            if value is not None and type(status) is int:
                is_error = status >= 400
                allowed = set(values["allowed_error_fields" if is_error else "allowed_response_fields"])
                fields = leaves(value)
                generated = [item for path, items in fields.items()
                             if path in {"choices[].text", "choices[].message.content"} for item in items if isinstance(item, str)]
                if case in {"normal", "private_system"} and status == 200:
                    baseline_texts[target] = generated
                (error_fields if is_error else response_fields).update(fields)
                for field in sorted(fields.keys() - allowed):
                    violations.append(f"{label}: nonpermitted response field {field}")
                # Caller-input/error-input quotations are not private leaks.
                # The system marker is never placed into the invalid field.
                if private:
                    for field in marker_fields(value, marks["private_system"]):
                        violations.append(f"{label}: private system marker returned at {field}")
                        private_hits.append(f"{label}:{field}")
                if option and status == 200:
                    names = OPTION_FIELDS.get(option, set())
                    for field, items in fields.items():
                        if '["' in field:  # A quoted literal key is not an option-path segment.
                            continue
                        if any(part in names for part in field.replace("[]", "").split(".")) and any(v is not None for v in items):
                            violations.append(f"{label}: forbidden option {option} returned {field}")
                            returned_options.add(option)
                    if (option == "echo" and marker_fields(value, marks["caller_input"])
                            and generated != baseline_texts.get(target)):
                        violations.append(f"{label}: explicitly forbidden echo returned caller prompt")
                        returned_options.add(option)
                    # Unknown extension flags can be silently ignored. A 200
                    # without an effect is not proof that the flag was blocked.
                    if option not in returned_options and not any(label in v for v in violations):
                        gaps.append(f"{label}: option accepted or ignored; rejection/stripping not observable")
            samples.append({"target": target, "case": case,
                            "request_fixture_sha256": sha256_hex(canonical_json(request_body)),
                            "outcome": status, "capture": capture_metadata(result), "capture_error": reason})
            if value is not None and (expected_status or rejected_option):
                covered.append(label)
            if option:
                options_tested.add(option)
            return result

        for target in targets:
            if target == "gateway" and not ctx.target.get("gateway", {}).get("url"):
                gaps.append("gateway: target URL unavailable")
                continue
            normal = inspect(target, "normal", body())
            controls[f"{target}:before"] = control_state(normal, ctx.env.model.id, endpoint)
            if controls[f"{target}:before"] != "pass":
                gaps.append(f"{target}: normal control did not succeed; challenges not sent")
                continue
            if private_supported:
                inspect(target, "private_system", body(private=True), private=True)
            else:
                gaps.append(f"{target}: private system context unsupported by completions profile")
            for option in values["forbidden_return_options"]:
                option_body = body(private=private_supported)
                option_body[option] = OPTIONS[option]
                # Chat logprobs is boolean; completion logprobs is integer.
                if private_supported and option == "logprobs":
                    option_body[option] = True
                inspect(target, "option:" + option, option_body, option=option, private=private_supported)
            error_body = body(private=private_supported)
            error_body["max_tokens"] = marks["error_input"]
            inspect(target, "safe_error", error_body, error=True, private=private_supported)
            after = inspect(target, "after", body())
            controls[f"{target}:after"] = control_state(after, ctx.env.model.id, endpoint)
        planned = [f"{target}:{label}" for target in targets for label in ("before", "after")]
        complete = not gaps and private_supported and len(controls) == len(planned)
        obs = {
            "response_violations": {"value": sorted(set(violations)), "collected": True},
            "response_coverage_complete": {"value": complete, "collected": True},
            "response_coverage_gaps": {"value": gaps, "collected": True},
            "response_cases_tested": {"value": covered, "collected": True},
            "response_fields_seen": {"value": sorted(response_fields), "collected": True},
            "error_fields_seen": {"value": sorted(error_fields), "collected": True},
            "response_options_returned": {"value": sorted(returned_options), "collected": True},
            "response_options_tested": {"value": [o for o in values["forbidden_return_options"] if o in options_tested], "collected": True},
            "response_targets_tested": {"value": [t for t in targets if any(s["target"] == t for s in samples)], "collected": True},
            "private_markers_returned": {"value": private_hits, "collected": True},
            "functional_probes_planned": {"value": planned, "collected": True},
            "functional_probe_results": {"value": controls, "collected": True},
        }
        normal = {"attempted": bool(controls), "succeeded": bool(controls) and len(controls) == len(planned)
                  and all(v == "pass" for v in controls.values()),
                  "detail": "normal request before and after response challenges for each selected target"}
        sample_artifact(ctx, "response_samples.json", {"artifact_profile": ARTIFACT_PROFILE, "samples": samples,
                        "marker_sha256": {role: sha256_hex(canonical_json(value)) for role, value in marks.items()},
                        "coverage_gaps": gaps})
        attempts = [bool(samples), private_supported and any(s["case"] == "private_system" for s in samples),
                    any(s["case"] == "safe_error" for s in samples)]
        return obs, normal, [{"step": step, "attempted": attempts[index] if index < len(attempts) else False}
                             for index, step in enumerate(ctx.check.challenge_steps)], {
            "artifact_profile": ARTIFACT_PROFILE, "violations": violations, "coverage_gaps": gaps,
            "fixture": {"endpoint": endpoint, "max_tokens": 1, "marker_roles": sorted(marks), "policy": values,
                        "private_context": "system message" if private_supported else "unsupported"}}
