"""OBS-03: bounded, source-attributed logs around marked normal/error trials.

Docker log success alone cannot establish completeness. Each required source
must contain trial-linked normal/error events, and exception coverage needs a
trial-linked exception/traceback/dump object. A 4xx response is not that object.
"""
from __future__ import annotations

import json
import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import PurePosixPath

from guard.runtime.docker import Docker
from .content_common import (MAX_SAMPLE_BYTES, append_artifact, content_request, control_state, leaves,
                             markers, sample_artifact, string_list, validate_content_settings, record_content, bounded_probe)
from .http_common import HttpCheckExecutor

REQUIRED_BRANCHES = frozenset({"normal", "error", "exception_dump"})
SUPPORTED_BRANCHES = REQUIRED_BRANCHES | {"debug", "trace"}
SUPPORTED_MARKER_CONTEXTS = frozenset({"caller_input", "private_system", "error_input"})

_FILE_READER = """import json,pathlib,sys
p=pathlib.Path(sys.argv[1]); limit=int(sys.argv[2]); stat=p.stat()
if not p.is_file(): raise ValueError('source is not a regular file')
with p.open('rb') as f: raw=f.read(limit+1)
print(json.dumps({'text':raw[:limit].decode('utf-8','replace'),'truncated':len(raw)>limit,
 'size':stat.st_size,'mtime_ns':stat.st_mtime_ns,'resolved_path':str(p.resolve())}))
"""


def _events(text: str) -> tuple[list[dict], bool]:
    """Support JSON objects and timestamp-prefixed Docker JSON lines only."""
    events, complete = [], True
    for line in text.splitlines():
        if not line.strip():
            continue
        raw = line
        prefix = re.match(r"^\d{4}-\d{2}-\d{2}T[^\s]+\s+(\{.*)$", raw)
        if prefix:
            raw = prefix.group(1)
        try:
            event = json.loads(raw)
        except ValueError:
            complete = False
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            complete = False
    return events, complete


class LogAnalysisExecutor(HttpCheckExecutor):
    name = "log_analysis"
    version = "0.1.0"
    requires_roles = False
    requires_route_scope = False

    def __init__(self, cfg, *args, docker=None, **kwargs):
        super().__init__(cfg, *args, **kwargs)
        self.docker = docker or Docker("log" + secrets.token_hex(4))

    def validate_policy(self, check, values):
        self.validate_configuration(check)
        for key in ("allowed_log_fields", "forbidden_log_fields", "forbidden_marker_contexts", "required_sources", "required_branches"):
            string_list(values, key, empty=key in {"forbidden_log_fields", "forbidden_marker_contexts"})
        if set(values["forbidden_marker_contexts"]) - SUPPORTED_MARKER_CONTEXTS:
            raise ValueError("unknown forbidden marker context")
        if not REQUIRED_BRANCHES <= set(values["required_branches"]):
            raise ValueError("OBS-03 requires normal, error and basic exception_dump branches")
        if set(values["required_branches"]) - SUPPORTED_BRANCHES:
            raise ValueError("unsupported required log branch")
        if not any(source["id"] in values["required_sources"] and source.get("role", "engine") == "engine"
                   for source in self.cfg["log_sources"]):
            raise ValueError("OBS-03 must include a local engine source; collector-only evidence is insufficient")

    def validate_configuration(self, check):
        """Check bounded execution/source inventory without reducing policy scope."""
        validate_content_settings(self.cfg)
        if self.cfg.get("endpoint", "/v1/completions") not in {"/v1/completions", "/v1/chat/completions"}:
            raise ValueError("log analysis supports completions/chat completions only")
        sources = self.cfg.get("log_sources")
        if not isinstance(sources, list) or not sources or len(sources) > 16:
            raise ValueError("log_sources must explicitly inventory source and copy containers")
        ids = []
        for source in sources:
            if (not isinstance(source, dict) or type(source.get("id")) is not str
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", source["id"])
                    or type(source.get("container")) is not str
                    or (source["container"] != "@engine" and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", source["container"]))):
                raise ValueError("each log source needs id and container")
            if source.get("role", "engine") not in {"engine", "copy"}:
                raise ValueError("source role must explicitly be engine or copy")
            if source.get("kind") not in {"docker_logs", "container_file"}:
                raise ValueError("log source kind must be docker_logs or container_file")
            if source["kind"] == "container_file":
                path = PurePosixPath(source.get("path", ""))
                if not path.is_absolute() or ".." in path.parts or str(path).startswith(("/proc/", "/dev/", "/sys/")):
                    raise ValueError("container_file needs an absolute regular log file path")
            ids.append(source["id"])
        if len(ids) != len(set(ids)):
            raise ValueError("log source IDs must be unique")
        if not any(source.get("role", "engine") == "engine" for source in sources):
            raise ValueError("OBS-03 must include a local engine source; collector-only evidence is insufficient")

    def _finish(self, ctx):
        return append_artifact(super()._finish(ctx), ctx, "log_samples.json", f"{self.name}/{self.version}")

    def _capture(self, source, since, until):
        container = source["container"]
        before = self.docker.inspect_json(container, timeout=30)
        config = before.get("HostConfig", {}).get("LogConfig", {})
        if source["kind"] == "docker_logs":
            result = self.docker.logs_window(container, since=since, until=until, timeout=30)
            # Preserve partial stdout on a failed read; an observed sensitive
            # marker may remain valid evidence despite incomplete collection.
            text = result.stdout + (result.stderr if result.returncode == 0 else "")
            metadata = {"truncated": len(text.encode("utf-8")) >= MAX_SAMPLE_BYTES}
            if config.get("Type") == "none":
                raise ValueError("Docker logging driver is none")
        else:
            result = self.docker.exec(container, ["python3", "-c", _FILE_READER, source["path"], str(MAX_SAMPLE_BYTES)], timeout=30)
            captured = json.loads(result.stdout) if result.returncode == 0 else {}
            text = captured.pop("text", "")
            metadata = captured
        after = self.docker.inspect_json(container, timeout=30)
        same = bool(before.get("Id")) and before.get("Id") == after.get("Id")
        if before.get("State", {}).get("StartedAt") != after.get("State", {}).get("StartedAt"):
            same = False
        captured_bytes = text.encode("utf-8")
        # Driver plugins may accept credentials in arbitrary option names.
        # Keep retention/mode facts and a digest, never arbitrary option values.
        public_options = {"max-size", "max-file", "mode", "max-buffer-size", "compress", "cache-disabled",
                          "cache-max-size", "cache-max-file", "cache-compress"}
        logging_metadata = {"Type": config.get("Type"),
                            "Config": {key: value if key in public_options else "[REDACTED]"
                                       for key, value in (config.get("Config") or {}).items()},
                            "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()}
        return {"source": source, "identity": {"container_id": before.get("Id"), "image_id": before.get("Image"),
                "started_at": before.get("State", {}).get("StartedAt"), "logging": logging_metadata},
                "window": {"since": since, "until": until}, "returncode": result.returncode,
                "error": "source reader failed" if result.returncode else None, "identity_stable": same,
                "unredacted_source_capture_sha256": hashlib.sha256(captured_bytes).hexdigest(),
                "capture_encoding": "UTF-8 after CLI decoding; digest is not a raw daemon-file digest",
                "text": captured_bytes[:MAX_SAMPLE_BYTES].decode("utf-8", "replace"), **metadata}

    def collect(self, ctx):
        endpoint = self.cfg.get("endpoint", "/v1/completions")
        marks = markers()
        token = secrets.token_hex(6)
        correlation = {"normal": "G" + token + "N", "error": "G" + token + "X"}
        values = ctx.policy_values
        # Object timestamps intentionally round to seconds; a log window must
        # retain subsecond bounds or it can exclude the most recent trial.
        since = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        controls, requests = {}, []
        gaps, violations, captures, coverage = [], [], [], {}
        fields_seen, marker_contexts_seen = set(), set()
        model_body = {"model": ctx.env.model.id, "max_tokens": 1, "temperature": 0}
        if endpoint == "/v1/chat/completions":
            model_body["messages"] = [{"role": "system", "content": marks["private_system"] + " private; never repeat"},
                                      {"role": "user", "content": marks["caller_input"]}]
        else:
            model_body["prompt"] = marks["caller_input"]
            if "private_system" in values["forbidden_marker_contexts"]:
                gaps.append("private system marker was not consumed by the completions profile")

        def send(label, body, branch):
            request = content_request(ctx, "engine", label, endpoint, body)
            request["headers"]["X-Request-Id"] = correlation[branch]
            response = bounded_probe(ctx, ctx.trusted_net, request)
            record_content(ctx, "trusted", "log-analysis", label, response)
            requests.append({"case": label, "body": body, "request_id": correlation[branch],
                             "outcome": response.get("outcome")})
            return response

        before = send("normal", model_body, "normal")
        controls["before"] = control_state(before, ctx.env.model.id, endpoint)
        error_outcome = None
        if controls["before"] == "pass":
            error_body = {**model_body, "max_tokens": marks["error_input"]}
            error_outcome = send("safe_error", error_body, "error").get("outcome")
            controls["after"] = control_state(send("after", model_body, "normal"), ctx.env.model.id, endpoint)
        else:
            gaps.append("normal inference failed; error challenge not sent")
        until = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        if type(error_outcome) is not int or error_outcome not in {400, 413, 422}:
            gaps.append(f"safe error response not confirmed: {error_outcome}")

        configured = {source["id"]: source for source in self.cfg["log_sources"]}
        for source_id in values["required_sources"]:
            source = configured.get(source_id)
            if source is None:
                gaps.append(f"{source_id}: required source not configured")
                continue
            if source["container"] == "@engine":
                source = {**source, "container": ctx.target["engine"].get("container")}
                if not source["container"]:
                    gaps.append(f"{source_id}: managed engine container is unavailable")
                    continue
            if source.get("role", "engine") == "engine" and source["container"] != ctx.target["engine"].get("container"):
                gaps.append(f"{source_id}: engine source differs from actual target container")
                continue
            try:
                capture = self._capture(source, since, until)
            except Exception as exc:
                gaps.append(f"{source_id}: log collection failed: {type(exc).__name__}")
                continue
            captures.append(capture)
            if capture["returncode"] or not capture["identity_stable"] or capture.get("truncated"):
                gaps.append(f"{source_id}: failed, truncated or changed source identity")
            if not capture["identity_stable"]:
                # A replaced source cannot establish a violation by the engine
                # used for this trial. Keep its bytes for diagnosis only.
                continue
            text = capture["text"]
            for context in values["forbidden_marker_contexts"]:
                if marks[context] in text:
                    violations.append(f"{source_id}: forbidden {context} marker present")
            for context, marker in marks.items():
                if marker in text:
                    marker_contexts_seen.add(context)
            events, structured = _events(text)
            if not structured or not events:
                gaps.append(f"{source_id}: JSON field inventory unavailable or empty; plain text absence is not PASS")
            branches = set()
            for event in events:
                event_fields = leaves(event)
                fields_seen.update(event_fields)
                for field in sorted(event_fields):
                    if field in values["forbidden_log_fields"] or field not in values["allowed_log_fields"]:
                        violations.append(f"{source_id}: nonpermitted log field {field}")
                serialized = json.dumps(event, ensure_ascii=False)
                if correlation["normal"] in serialized:
                    branches.add("normal")
                linked_error = correlation["error"] in serialized or marks["error_input"] in serialized
                if linked_error:
                    branches.add("error")
                    for field, items in event_fields.items():
                        if '["' in field:  # Literal punctuation cannot manufacture a dump/trace parent.
                            continue
                        names = {part.lower() for part in field.replace("[]", "").split(".")}
                        has_content = any(isinstance(v, str) and v.strip() for v in items)
                        if names & {"exception", "traceback", "exception_dump", "dump"} and has_content:
                            branches.add("exception_dump")
                        for name in names & {"debug", "trace"}:
                            if has_content:
                                branches.add(name)
            coverage[source_id] = sorted(branches)
            missing = set(values["required_branches"]) - branches
            if missing:
                gaps.append(f"{source_id}: no trial-linked events for {', '.join(sorted(missing))}")
        # An unlisted collector/dump is not silently assessed. This profile
        # reports exactly the policy's source inventory, not every process sink.
        obs = {"log_violations": {"value": sorted(set(violations)), "collected": True},
               "log_coverage_complete": {"value": not gaps, "collected": True},
               "log_coverage_gaps": {"value": gaps, "collected": True},
               "log_sources_tested": {"value": coverage, "collected": True},
               "log_fields_seen": {"value": sorted(fields_seen), "collected": True},
               "log_marker_contexts_seen": {"value": sorted(marker_contexts_seen), "collected": True},
               "log_source_ids_tested": {"value": [s for s in values["required_sources"] if s in coverage], "collected": True},
               "log_required_branches_tested": {"value": [branch for branch in values["required_branches"]
                  if coverage and all(branch in branches for branches in coverage.values())], "collected": True},
               "functional_probes_planned": {"value": ["before", "after"], "collected": True},
               "functional_probe_results": {"value": controls, "collected": True}}
        sample_artifact(ctx, "log_samples.json", {"captures": captures, "requests": requests, "markers": marks,
                        "correlation": correlation, "coverage_gaps": gaps})
        normal = {"attempted": True, "succeeded": len(controls) == 2 and all(v == "pass" for v in controls.values()),
                  "detail": "normal inference before/after a bounded validation-error request; HTTP 4xx is not a dump"}
        return obs, normal, [{"step": step, "attempted": bool(requests)} for step in ctx.check.challenge_steps], {
            "coverage_gaps": gaps, "violations": violations, "sources": coverage,
            "fixture": {"endpoint": endpoint, "max_tokens": 1, "marker_roles": sorted(marks), "policy": values,
                        "source_kinds": {s["id"]: s["kind"] for s in self.cfg["log_sources"]}}}
