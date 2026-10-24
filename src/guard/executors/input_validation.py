"""REQ-09 bounded token-array checks using declared policy limits and a valid control."""
from copy import deepcopy
from guard.input_policy import contract_state, policy_context
from .http_common import HttpCheckExecutor, functional_requests
from .content_common import bounded_probe, control_state, record_content


class InputValidationExecutor(HttpCheckExecutor):
    name = "input_validation"
    version = "0.4.0"
    requires_roles = False
    requires_route_scope = False

    def validate_configuration(self, check):
        """Validate execution bounds even when policy scope is unsupported."""
        if type(self.cfg.get("vocab_size")) is not int or self.cfg["vocab_size"] < 1 or not self.cfg.get("vocab_source"):
            raise ValueError("execution settings need vocab_size and its model/fixture source")
        budget = self.cfg.get("max_probe_tokens", 4096)
        if type(budget) is not int or budget < 2:
            raise ValueError("max_probe_tokens must be an integer of at least two tokens")

    def validate_policy(self, check, values):
        context = policy_context(check, values)
        if context is not None and context.blocked:
            raise ValueError("; ".join(context.reasons))
        self.validate_configuration(check)
        if type(values.get("max_input_tokens")) is not int or values["max_input_tokens"] < 1:
            raise ValueError("max_input_tokens must be a positive policy limit")
        budget = self.cfg.get("max_probe_tokens", 4096)
        if values["max_input_tokens"] + 1 > budget:
            raise ValueError("input limit exceeds the configured bounded probe budget")
        if not values.get("rejection_codes") or any(s not in {"400", "413", "422"} for s in values["rejection_codes"]):
            raise ValueError("rejection_codes must explicitly name supported input rejection responses")

    def collect(self, ctx):
        requests, probes = functional_requests(ctx, via="engine")
        if len(requests) != 1 or requests[0].get("body", {}).get("prompt") != [0]:
            raise ValueError("input validation needs one engine token-array control with prompt=[0]")
        template, fp = requests[0], next(iter(probes.values()))
        outcomes, controls = {}, {}
        cases = {"token_id": [self.cfg["vocab_size"]],
                 "length": [0] * (ctx.policy_values["max_input_tokens"] + 1)}
        def send(req, label):
            r = bounded_probe(ctx, ctx.trusted_net, req)
            record_content(ctx, "trusted", "input-validation", label, r)
            return r

        def control(label):
            response = send(deepcopy(template), label)
            controls[label] = control_state(response, ctx.env.model.id, "/v1/completions")

        control("before")
        if controls["before"] == "pass":
            for name, prompt in cases.items():
                request = deepcopy(template)
                request["body"]["prompt"] = prompt
                value = send(request, name).get("outcome")
                outcomes[name] = str(value) if value is not None else "error"
                control("after_" + name)
        normal = {"attempted": True, "succeeded": len(controls) == 3 and all(v == "pass" for v in controls.values()),
                  "detail": "bounded token-array control before and after each challenge"}
        planned = ["before", "after_token_id", "after_length"]
        obs = {
            "challenge_outcomes": {"value": outcomes, "collected": bool(outcomes)},
            "accepted_challenges": {"value": [k for k, v in outcomes.items() if v.isdigit() and 200 <= int(v) < 300],
                                    "collected": bool(outcomes)},
            "challenges_planned": {"value": list(cases), "collected": True},
            "functional_probes_planned": {"value": planned, "collected": True},
            "functional_probe_results": {"value": controls, "collected": True},
        }
        scope = {}
        if contract_state(ctx.check) != "legacy":
            # The request was actually attempted above. Derive scope from that
            # prepared request/probe, never from the policy's expected lists.
            scope = {"input_endpoints_tested": [f"{template['method']} {fp.path}"],
                     "input_formats_tested": ["token_ids"] if type(template["body"]["prompt"]) is list
                     and all(type(v) is int for v in template["body"]["prompt"]) else []}
            obs.update({key: {"value": value, "collected": True} for key, value in scope.items()})
        return obs, normal, [{"step": s, "attempted": len(outcomes) == len(cases)} for s in ctx.check.challenge_steps], {
            "challenge_outcomes": outcomes, "controls": controls,
            "fixture": {"cases": cases, "control": fp.model_dump(mode="json"), "policy": ctx.policy_values,
                        "vocab_source": self.cfg["vocab_source"], **({"submitted_scope": scope} if scope else {})}}
