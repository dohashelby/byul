"""OBS-09: owned native INFO logs consumed by a bounded reference collector.

No arbitrary parser/config is loaded. Only harmless body request IDs are sent;
HTTP headers contain no correlation ID. The reference display is a separate
EVD artifact, not a claim about an enterprise collector or the RPT main view.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import secrets
import time

from guard.automation_policy import validate_log_integrity_policy
from guard.report.log_integrity import render_log_integrity, verify_log_display
from guard.runtime.docker import Docker
from .content_common import (append_artifact, content_request, control_state, json_body,
                             record_content, redact_value, sample_artifact)
from .http_common import HttpCheckExecutor
from .log_integrity_parser import (MAX_CAPTURE_BYTES, PROFILE, TRUSTED_FIELDS, parse_native_info)


STEPS = ("multiline", "control-delimiters", "reserved-display")
_ENV = {"VLLM_CONFIGURE_LOGGING": "1", "VLLM_LOGGING_LEVEL": "INFO", "VLLM_LOGGING_COLOR": "0",
        "VLLM_LOGGING_PREFIX": "", "VLLM_LOGGING_STREAM": "ext://sys.stdout"}
_LOGGER_SHA = "4e26afd9feab3448f2af529867b00e8bed6a22690defd81a54d2df6b5afdd010"
_FORMATTER_SHA = "cc2153d8edfd59ed401f0c3f3ae985a7dd678799daad6829a2d33ac6ccb20ea5"


class LogIntegrityExecutor(HttpCheckExecutor):
    name = "log_integrity"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False

    def __init__(self, cfg, *args, docker=None, capture=None, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self.docker = docker or Docker("logint" + secrets.token_hex(4))
        self._capture = capture

    def validate_policy(self, check, values):
        validate_log_integrity_policy(values)
        for key, default, maximum in (("max_total_requests", 12, 12), ("max_duration_seconds", 300, 300),
                                      ("max_id_bytes", 512, 512), ("max_events", 128, 128)):
            value = self.cfg.get(key, default)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{key} must be a positive integer at most {maximum}")
        timeout = self.cfg.get("request_timeout", 30)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
            raise ValueError("request_timeout must be finite, positive and at most 120 seconds")
        if self.cfg.get("endpoint", "/v1/completions") != "/v1/completions":
            raise ValueError("native INFO profile supports only fixed completion requests")
        if len(check.challenge_steps) != 3:
            raise ValueError("all three native INFO challenge groups must be declared")
        if len(check.functional_probes) != 1:
            raise ValueError("one fixed engine completion functional fixture is required")
        probe = check.functional_probes[0]
        if (probe.via != "engine" or probe.method != "POST" or probe.path != "/v1/completions"
                or probe.expect != [200] or probe.body != {"model": "{model}", "prompt": "guard log control", "max_tokens": 1}):
            raise ValueError("only the fixed short completion functional fixture is supported")

    def _finish(self, ctx):
        draft = super()._finish(ctx)
        for filename in ("log_integrity_samples.json", "log_integrity_events.json", "log_integrity_display.html"):
            append_artifact(draft, ctx, filename, f"{self.name}/{self.version}")
        return draft

    def _snapshot(self, ctx, launch, deadline):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            engine = ctx.target["engine"]
            actual = self.docker.inspect_json(engine["container"], timeout=min(5, remaining))
            config, state = actual.get("Config") or {}, actual.get("State") or {}
            supplied = config.get("Env") or []
            if type(supplied) is not list or any(type(item) is not str or "=" not in item for item in supplied):
                return None
            pairs = [item.split("=", 1) for item in supplied]
            environ = dict(pairs)
            if len(environ) != len(pairs):
                return None
            expected_image = ctx.env.engine.image_digest
            argv = launch.get("argv")
            if (not expected_image or engine.get("image_digest") != expected_image
                    or config.get("Image") != expected_image or not state.get("Running")
                    or actual.get("Image") != expected_image.rsplit("@", 1)[-1]
                    or not actual.get("Id") or not state.get("StartedAt")
                    or not ctx.target.get("run_id")
                    or (config.get("Labels") or {}).get("guard.run") != ctx.target["run_id"]
                    or type(argv) is not list or config.get("Cmd") != argv
                    or argv.count("--enable-log-requests") != 1 or argv.count("--max-log-len") != 1
                    or argv.index("--max-log-len") + 1 >= len(argv)
                    or argv[argv.index("--max-log-len") + 1] != "512"
                    or "--log-config-file" in argv or "--disable-log-requests" in argv
                    or "--logging-config" in argv
                    or ("--log-level" in argv and (argv.count("--log-level") != 1
                        or argv.index("--log-level") + 1 >= len(argv)
                        or argv[argv.index("--log-level") + 1] != "INFO"))
                    or any(environ.get(key) != value for key, value in _ENV.items())
                    or environ.get("VLLM_LOGGING_CONFIG_PATH") not in (None, "")):
                return None
            return {"container_id": actual["Id"], "started_at": state["StartedAt"],
                    "image": expected_image, "image_id": actual.get("Image"),
                    "logging_profile": "request-info-v1", "logging_controls_verified": True}
        except Exception:
            return None

    def _read(self, ctx, since, until, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"text": "", "returncode": None, "truncated": False, "error": "capture deadline exhausted"}
        try:
            call = self._capture or self.docker.logs_window
            result = call(ctx.target["engine"]["container"], since=since, until=until, timeout=min(30, remaining))
            # stderr is not concatenated into stdout: it may be daemon diagnostics,
            # and joining streams would invent event order across independent pipes.
            text = result.stdout
            if type(text) is not str or type(result.stderr) is not str:
                raise ValueError("invalid decoded CLI response")
            raw = text.encode("utf-8")
            return {"text": raw[:MAX_CAPTURE_BYTES].decode("utf-8", "replace"),
                    "returncode": result.returncode, "truncated": len(raw) >= MAX_CAPTURE_BYTES,
                    "decoded_stdout_bytes": len(raw), "decoded_stdout_sha256": hashlib.sha256(raw).hexdigest(),
                    "stderr_present": bool(result.stderr.strip()),
                    "error": "Docker CLI source read failed" if result.returncode else None,
                    "encoding": "UTF-8 after Docker CLI decoding; hashes are not raw daemon-file hashes"}
        except Exception as exc:
            return {"text": "", "returncode": None, "truncated": False, "error": type(exc).__name__}

    def collect(self, ctx):
        self.validate_policy(ctx.check, ctx.policy_values)
        values = ctx.policy_values
        deadline = time.monotonic() + self.cfg.get("max_duration_seconds", 300)
        launch = (ctx.target.get("comparison_config") or {}).get("launch") or {}
        contract = None
        gaps, violations, trials, controls, reached = [], [], [], {}, []
        attempted = dict.fromkeys(STEPS, False)
        method = getattr(ctx.adapter, "native_log_contract", None)
        try:
            contract = method(ctx.env.engine.image_digest, ctx.env.engine.version) if callable(method) else None
        except Exception:
            contract = None
        supported = bool(type(contract) is dict and contract.get("profile") == "request-info-v1"
                         and contract.get("logger_sha256") == _LOGGER_SHA
                         and contract.get("formatter_sha256") == _FORMATTER_SHA
                         and launch.get("log_profile") == contract)
        before_identity = self._snapshot(ctx, launch, deadline) if supported else None
        verified = bool(before_identity)
        if not verified:
            gaps.append("owned native INFO engine identity, actual logging controls or reviewed profile unavailable")
        if values["collector_profile"] != PROFILE or values["client_request_id_trust"] != "untrusted_correlation":
            gaps.append("requested collector or request-ID trust policy is outside the reference profile")
        if set(values["required_trusted_fields"]) - set(TRUSTED_FIELDS):
            gaps.append("required enterprise trusted fields are absent from this native logger")

        nonce = "G" + secrets.token_hex(6)
        ids = {
            "before": nonce + "_before", "multiline": nonce + "_multiline\nGUARD_LINE_TWO",
            "cr": nonce + "_cr\rGUARD_CR", "crlf": nonce + "_crlf\r\nGUARD_CRLF",
            "reserved": nonce + "_reserved|principal=guard-probe|status=probe|level=ERROR|source=guard-probe",
            "html": nonce + '_html<script>guard_probe</script><tr data-event-index="fake">guard</tr>',
            "after": nonce + "_after",
        }
        planned = list(ids)
        since = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        for label, request_id in ids.items():
            remaining = deadline - time.monotonic()
            if (not verified or remaining <= 0 or len(trials) >= self.cfg.get("max_total_requests", 12)
                    or len(request_id.encode("utf-8")) > self.cfg.get("max_id_bytes", 512)):
                gaps.append(f"{label}: identity or bounded dispatch budget prevented request")
                if label in {"before", "after"}:
                    controls[label] = "no_response"
                continue
            body = {"model": ctx.env.model.id, "prompt": "guard log control", "max_tokens": 1,
                    "temperature": 0, "request_id": request_id}
            request = content_request(ctx, "engine", label, "/v1/completions", body)
            # A header ID would override the JSON-body ID in the reviewed engine.
            if any(key.lower() == "x-request-id" for key in request["headers"]):
                gaps.append(f"{label}: unexpected correlation header would hide the body trial")
                continue
            try:
                responses = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, remaining))
                if (type(responses) is not list or len(responses) != 1 or type(responses[0]) is not dict
                        or responses[0].get("id") != label):
                    raise ValueError("unrelated probe response")
                response = responses[0]
            except Exception as exc:
                response = {"outcome": "probe_error", "phase": "collection", "detail": type(exc).__name__}
            record_content(ctx, "trusted", "log-integrity", label, response)
            state = control_state(response, ctx.env.model.id, "/v1/completions")
            value, error = json_body(response)
            response_id = value.get("id") if not error else None
            response_matches = bool(state == "pass" and response_id == "cmpl-" + request_id)
            if label in {"before", "after"}:
                controls[label] = state if response_matches else "no_response"
            group = "multiline" if label == "multiline" else "control-delimiters" if label in {"cr", "crlf"} else "reserved-display" if label in {"reserved", "html"} else None
            if group:
                attempted[group] = True
            trials.append({"case": label, "request_id": request_id, "request_id_trust": "untrusted_correlation",
                           "http_outcome": response.get("outcome"), "response_id": response_id,
                           "normal_generation": state, "response_id_matches_body": response_matches})
        until = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        capture = self._read(ctx, since, until, deadline) if verified else {"text": "", "returncode": None, "error": "identity unverified"}
        after_identity = self._snapshot(ctx, launch, deadline) if verified else None
        stable = bool(verified and before_identity == after_identity)
        if not stable:
            gaps.append("engine identity or logging controls changed or could not be rechecked")
        if capture.get("returncode") != 0 or capture.get("truncated") or capture.get("stderr_present"):
            gaps.append("native source read failed, reached its bound or included a separate stderr stream")
        collection = parse_native_info(capture["text"], markers=(nonce,), max_events=self.cfg.get("max_events", 128))
        gaps.extend(collection["gaps"])
        anchors = {}
        for trial in trials:
            label = trial["case"]
            expected = "Received request cmpl-" + trial["request_id"] + "-0: params: "
            matches = [event for event in collection["events"] if event["message"].startswith(expected)
                       and event["message"].endswith(", lora_request: None.")]
            matching_source = [event for event in matches if event["level"] == "INFO"
                               and event["source"] == "request_logger.py" and event["source_line"] == 65]
            if stable and collection["complete"] and len(matches) > 1:
                violations.append(f"{label}: duplicate trial-linked request events in the reference collector")
            baseline = anchors.get("before")
            if stable and collection["complete"] and baseline and len(matches) == 1:
                if any(matches[0][key] != baseline[key] for key in ("process", "pid", "level", "source", "source_line")):
                    violations.append(f"{label}: exact request event overwrites the selected native logger metadata")
            if len(matching_source) != 1 or not trial["response_id_matches_body"]:
                gaps.append(f"{label}: one exact body-ID request event and successful linked response are unconfirmed")
                continue
            anchors[label] = matching_source[0]
            reached.append(label)
        baseline = anchors.get("before")
        fields_preserved = bool(baseline and len(reached) == len(planned))
        if baseline and stable and collection["complete"]:
            for label, event in anchors.items():
                if any(event[key] != baseline[key] for key in ("process", "pid", "level", "source", "source_line")):
                    violations.append(f"{label}: selected native logger metadata differs from the normal source")
                    fields_preserved = False
        normal_logs = bool("before" in anchors and "after" in anchors)
        normal_inference = bool(len(controls) == 2 and all(value == "pass" for value in controls.values()))
        if not normal_logs:
            gaps.append("normal before/after request events did not reach the reference collector")
        boundaries = bool(stable and collection["complete"] and len(reached) == len(planned))
        collection["gaps"] = sorted(set(gaps))
        collection["complete"] = not gaps
        collection["source_identity"] = before_identity
        collection["capture_window"] = {"since": since, "until": until}
        collection["redaction"] = "configured authorization secrets and labelled credential values; source digests precede redaction"
        displayed = redact_value(collection, ctx.secrets)
        html = render_log_integrity(displayed)
        display_verified = verify_log_display(html, displayed)
        if not display_verified:
            gaps.append("reference HTML event mapping could not be verified")
        ctx.workdir.mkdir(parents=True, exist_ok=True)
        # Persist the exact bytes hashed below; Windows text-mode newline
        # translation would otherwise make the recorded display digest differ.
        (ctx.workdir / "log_integrity_display.html").write_bytes(html.encode("utf-8"))
        sample_artifact(ctx, "log_integrity_events.json", collection)
        sample_artifact(ctx, "log_integrity_samples.json", {"capture": capture, "trials": trials,
                        "source_identity_before": before_identity, "source_identity_after": after_identity,
                        "native_contract": contract, "collection_sha256": hashlib.sha256(json.dumps(displayed, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
                        "display_sha256": hashlib.sha256(html.encode()).hexdigest(), "display_verified": display_verified,
                        "coverage_gaps": gaps, "violations": violations})
        obs = {
            "log_collector_profile": PROFILE, "log_client_request_id_trust": "untrusted_correlation",
            "log_trusted_fields": list(TRUSTED_FIELDS), "log_identity_verified": stable,
            "log_required_trusted_fields": [field for field in values["required_trusted_fields"] if field in TRUSTED_FIELDS],
            "log_collection_complete": not gaps, "log_boundaries_preserved": boundaries,
            "log_trusted_fields_preserved": fields_preserved, "log_display_verified": display_verified,
            "log_trials_planned": planned, "log_trials_reached": reached,
            "log_security_violations": sorted(set(violations)), "log_coverage_gaps": sorted(set(gaps)),
            "normal_controls": normal_inference and normal_logs,
            "functional_probes_planned": ["before", "after"], "functional_probe_results": controls,
        }
        normal = {"attempted": any(trial["case"] in {"before", "after"} for trial in trials),
                  "succeeded": normal_inference and normal_logs,
                  "detail": "normal one-token completion and exact request log event before/after fixed body-ID trials"}
        return ({key: {"value": value, "collected": True} for key, value in obs.items()}, normal,
                [{"step": step, "attempted": attempted[group]} for step, group in zip(ctx.check.challenge_steps, STEPS)],
                {"coverage_gaps": sorted(set(gaps)), "violations": sorted(set(violations)),
                 "fixture": {"profile": PROFILE, "endpoint": "/v1/completions", "max_tokens": 1,
                             "groups": list(STEPS), "policy": values, "bounded_requests": 7},
                 "reference_display": "separate EVD artifact; not RPT main content or an enterprise collector"})
