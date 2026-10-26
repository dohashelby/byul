"""OBS-03: bounded, source-attributed logs around marked normal/error trials.

Docker log success alone cannot establish completeness. Each required source
must contain trial-linked normal/error events, and exception coverage needs a
trial-linked exception/traceback/dump object. A 4xx response is not that object.
"""
from __future__ import annotations

import json
import base64
import hashlib
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath

from guard.runtime.docker import Docker
from .log_analysis_contract import (STRICT_JSON_PROFILE, analyze_log_captures, events,
                                    persistable_log_text)
from .content_common import (MAX_SAMPLE_BYTES, append_artifact, content_request, control_state, leaves,
                             markers, sample_artifact, string_list, validate_content_settings, record_content, bounded_probe, redact_value)
from .http_common import HttpCheckExecutor

REQUIRED_BRANCHES = frozenset({"normal", "error", "exception_dump"})
SUPPORTED_BRANCHES = REQUIRED_BRANCHES | {"debug", "trace"}
SUPPORTED_MARKER_CONTEXTS = frozenset({"caller_input", "private_system", "error_input"})

_FILE_READER = """import base64,hashlib,json,os,stat,sys
path=sys.argv[1]; limit=int(sys.argv[2]); handles=[]
def open_source():
    parts=path.split('/')
    if not path.startswith('/') or any(p in ('.','..') for p in parts): raise ValueError('invalid path')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY); handles.append(parent)
    for part in parts[1:-1]:
        if not part: continue
        parent=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent); handles.append(parent)
    fd=os.open(parts[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=parent); handles.append(fd)
    return fd
def identity(s):
    return {'device':s.st_dev,'inode':s.st_ino,'size':s.st_size,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns}
try:
    fd=open_source(); before=os.fstat(fd)
    if not stat.S_ISREG(before.st_mode): raise ValueError('source is not a regular file')
    chunks=[]; remaining=limit+1
    while remaining:
        chunk=os.read(fd,min(4096,remaining))
        if not chunk: break
        chunks.append(chunk); remaining-=len(chunk)
    raw=b''.join(chunks); after=os.fstat(fd)
    current=os.fstat(open_source()); stable=identity(before)==identity(after)==identity(current)
    kept=raw[:limit]
    print(json.dumps({'schema':'guard-container-file/2','raw_base64':base64.b64encode(kept).decode('ascii'),
      'raw_retained_sha256':hashlib.sha256(kept).hexdigest(),'raw_retained_bytes':len(kept),
      'truncated':len(raw)>limit,'size':before.st_size,'mtime_ns':before.st_mtime_ns,
      'file_before':identity(before),'file_after':identity(after),'path_after':identity(current),
      'file_identity_stable':stable,'source_complete':stable and len(raw)<=limit,
      'nofollow_components':True,'regular_file':True,'window_basis':'exact fresh trial IDs, not file mtime'}))
except Exception as exc:
    print(json.dumps({'schema':'guard-container-file/2','error':type(exc).__name__,
      'file_identity_stable':False,'source_complete':False,'raw_base64':''}))
finally:
    for fd in reversed(handles):
        try: os.close(fd)
        except OSError: pass
"""


# Historical parser import remains available to callers.
def _events(text):
    rows, complete = events(text)
    return [row["event"] for row in rows], complete


class LogAnalysisExecutor(HttpCheckExecutor):
    name = "log_analysis"
    version = "0.2.1"
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
        duration = self.cfg.get("max_duration_seconds", 180)
        if type(duration) is not int or not 1 <= duration <= 300:
            raise ValueError("max_duration_seconds must be a positive integer at most 300")
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

    def _capture(self, source, since, until, deadline=None):
        def budget():
            remaining = 30 if deadline is None else min(30, deadline - time.monotonic())
            if remaining <= 0:
                raise TimeoutError("bounded log capture deadline exhausted")
            return remaining
        container = source["container"]
        before = self.docker.inspect_json(container, timeout=budget())
        config = before.get("HostConfig", {}).get("LogConfig", {})
        raw = None
        if source["kind"] == "docker_logs":
            result = self.docker.logs_window(container, since=since, until=until, timeout=budget())
            text = result.stdout
            # Successful Docker logs maps container stdout/stderr to separate
            # CLI pipes. Never join their partial lines or invent a chronology.
            stderr_text = result.stderr if result.returncode == 0 else ""
            raw = getattr(result, "stdout_bytes", None)
            metadata = {"truncated": max(len(text.encode("utf-8")), len(stderr_text.encode("utf-8"))) >= MAX_SAMPLE_BYTES,
                        "stderr_text": stderr_text.encode("utf-8")[:MAX_SAMPLE_BYTES].decode("utf-8", "replace"),
                        "stderr_present": bool(result.stderr),
                        "stderr_role": "container log stream" if result.returncode == 0 else "failed CLI diagnostic; text not retained",
                        "stream_order": "each pipe keeps its own order; no order across stdout/stderr"}
            if config.get("Type") == "none":
                metadata["source_complete"] = False
                metadata["driver_disabled"] = True
        else:
            call = getattr(self.docker, "exec_bounded", self.docker.exec)
            result = call(container, ["python3", "-c", _FILE_READER, source["path"], str(MAX_SAMPLE_BYTES)], timeout=budget())
            captured = json.loads(result.stdout) if result.returncode == 0 else {}
            if captured.get("schema") == "guard-container-file/2":
                raw = base64.b64decode(captured.pop("raw_base64", ""), validate=True)
                if (captured.get("raw_retained_bytes") != len(raw)
                        or captured.get("raw_retained_sha256") != hashlib.sha256(raw).hexdigest()):
                    captured["source_complete"] = False
                    captured["file_identity_stable"] = False
                text = raw.decode("utf-8", "replace")
            else:
                # Old injected fixtures remain readable; they cannot establish
                # the new nofollow/descriptor-stability guarantee.
                text = captured.pop("text", "")
                captured["source_complete"] = False
                captured["file_identity_stable"] = False
                captured["legacy_file_metadata"] = True
            metadata = captured
        after = self.docker.inspect_json(container, timeout=budget())
        same = (bool(before.get("Id")) and before.get("Id") == after.get("Id")
                and bool(before.get("State", {}).get("StartedAt"))
                and before.get("Image") == after.get("Image"))
        if before.get("State", {}).get("StartedAt") != after.get("State", {}).get("StartedAt"):
            same = False
        pipe = getattr(result, "capture", None)
        if pipe is not None:
            metadata["pipe_capture"] = pipe
            metadata["source_complete"] = metadata.get("source_complete", True) and pipe.get("complete") is True
            metadata["truncated"] = metadata.get("truncated", False) or pipe.get("truncated", False)
            metadata["timed_out"] = pipe.get("timed_out", False)
        captured_bytes = text.encode("utf-8")
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
                "raw_retained_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
                "raw_retained_bytes": len(raw) if raw is not None else None,
                "capture_encoding": "retained CLI/file bytes decoded as UTF-8; decoded and raw hashes are separate; not daemon-file digest",
                "text": captured_bytes[:MAX_SAMPLE_BYTES].decode("utf-8", "replace"), **metadata}

    def collect(self, ctx):
        deadline = time.monotonic() + self.cfg.get("max_duration_seconds", 180)
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
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {"outcome": "probe_error", "phase": "collection", "detail": "deadline exhausted"}
            ctx.timeout = min(ctx.timeout, remaining)
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
                capture = self._capture(source, since, until, deadline)
            except Exception as exc:
                gaps.append(f"{source_id}: log collection failed: {type(exc).__name__}")
                continue
            captures.append(capture)
        # An unlisted collector/dump is not silently assessed. This profile
        # reports exactly the policy's source inventory, not every process sink.
        gaps_before_analysis = list(gaps)
        analysis = analyze_log_captures(captures, values, marks, correlation, initial_gaps=gaps,
                                        profile=STRICT_JSON_PROFILE)
        gaps, violations, coverage = analysis["log_coverage_gaps"], analysis["log_violations"], analysis["log_sources_tested"]
        obs = {key: {"value": value, "collected": True} for key, value in analysis.items()}
        obs.update({"functional_probes_planned": {"value": ["before", "after"], "collected": True},
                    "functional_probe_results": {"value": controls, "collected": True}})
        replay_captures = []
        for capture in captures:
            saved = dict(capture)
            rejected = {}
            for field in ("text", "stderr_text"):
                if field in saved:
                    saved[field], rejected[field] = persistable_log_text(saved[field])
            saved["rejected_json_lines"] = rejected
            replay_captures.append(saved)
        saved_captures = redact_value(replay_captures, ctx.secrets)
        for capture in saved_captures:
            persisted = capture["text"].encode("utf-8")
            capture["persisted_decoded_sha256"] = hashlib.sha256(persisted).hexdigest()
            capture["persisted_decoded_bytes"] = len(persisted)
            if "stderr_text" in capture:
                stderr = capture["stderr_text"].encode("utf-8")
                capture["persisted_stderr_sha256"] = hashlib.sha256(stderr).hexdigest()
                capture["persisted_stderr_bytes"] = len(stderr)
        sample_artifact(ctx, "log_samples.json", {"schema": "guard-log-analysis-capture/3",
                        "analysis_profile": STRICT_JSON_PROFILE,
                        "captures": saved_captures, "requests": requests, "markers": marks,
                        "correlation": correlation, "initial_gaps": list(gaps_before_analysis),
                        "coverage_gaps": gaps, "analysis": analysis, "controls": controls,
                        "policy_values": values, "source_target": ctx.target["engine"].get("container"),
                        "window": {"since": since, "until": until}})
        normal = {"attempted": True, "succeeded": len(controls) == 2 and all(v == "pass" for v in controls.values()),
                  "detail": "normal inference before/after a bounded validation-error request; HTTP 4xx is not a dump"}
        return obs, normal, [{"step": step, "attempted": bool(requests)} for step in ctx.check.challenge_steps], {
            "coverage_gaps": gaps, "violations": violations, "sources": coverage,
            "fixture": {"endpoint": endpoint, "max_tokens": 1, "marker_roles": sorted(marks), "policy": values,
                        "source_kinds": {s["id"]: s["kind"] for s in self.cfg["log_sources"]}}}
