"""One explicit startup generation, separate from every check and verdict.

The configured operation is never retried. A failed preparation is retained and
the caller's normal managed-startup failure path prevents the test from running.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time

from guard.executors.content_common import control_state, json_body, redact_value
from guard.runtime.docker import DockerError

SETTING = "generation_readiness_timeout"
FILENAME = "generation-readiness.json"
PROTOCOL = "guard-generation-readiness/1"


def profile(config: dict) -> dict | None:
    """Return only static conditions; absent configuration preserves legacy up."""
    if SETTING not in config:
        return None
    timeout = config[SETTING]
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 300:
        raise ValueError("generation_readiness_timeout must be finite and between 1 and 300 seconds")
    if config.get("launch_profile") != "cpu-bounded" or config.get("gpu", False) is not False:
        raise ValueError("generation readiness requires the managed bounded CPU profile")
    model = config.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("generation readiness requires an explicit served model")
    return {"protocol": PROTOCOL, "origin": "trusted", "target": "engine", "max_requests": 1,
            "request_timeout_seconds": float(timeout), "probe_process_timeout_seconds": float(timeout) + 30,
            "request": {"method": "POST", "path": "/v1/completions",
                        "body": {"model": model, "prompt": "ping", "max_tokens": 1, "temperature": 0}}}


def reference(folder: Path) -> dict | None:
    path = Path(folder) / FILENAME
    if not path.is_file():
        return None
    return {"path": FILENAME, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def run(folder: Path, conditions: dict, engine_url: str, network: str, probe,
        *, secret_values: list[str]) -> dict:
    """Use probe(network, requests, timeout) once and preserve its bounded reply.

    The probe process has its own finite timeout (HTTP limit plus 30 seconds of
    existing Docker probe overhead). No response, elapsed time or generated Ref
    is part of comparison conditions.
    """
    request = conditions["request"]
    wire = {"id": "generation-readiness", "method": request["method"],
            "url": engine_url + request["path"], "body": deepcopy(request["body"]), "want_body": True}
    doc = {"schema": PROTOCOL, "profile": deepcopy(conditions), "attempts": 0,
           "started_at": datetime.now(timezone.utc).isoformat(),
           "scope": "Startup preparation only; not an EVD, normal control, challenge or verdict.",
           "code": "GENERATION_READINESS_INCOMPLETE", "response": None}
    started = time.monotonic()
    path = Path(folder) / FILENAME
    # Reserve before sending. An old success or failure is never overwritten.
    with path.open("xb") as output:
        try:
            doc["attempts"] = 1
            responses = probe(network, [wire], conditions["request_timeout_seconds"])
            if not isinstance(responses, list) or len(responses) != 1 or not isinstance(responses[0], dict):
                doc["code"] = "GENERATION_READINESS_INVALID_PROBE_RESPONSE"
            else:
                response = responses[0]
                doc["response"] = redact_value(response, secret_values)
                parsed, error = json_body(response)
                # An explicit error object is never a successful startup,
                # including a conflicting object with success-shaped fields.
                valid = (error is None and "error" not in parsed
                         and control_state(response, request["body"]["model"], request["path"]) == "pass")
                doc["code"] = "GENERATION_READINESS_READY" if valid else "GENERATION_READINESS_NOT_READY"
        except Exception as exc:
            # Exceptions may include keys, URLs or daemon output. Retain type only.
            doc["code"] = "GENERATION_READINESS_PROBE_ERROR"
            doc["error_type"] = type(exc).__name__
        finally:
            doc["elapsed_seconds"] = max(0.0, time.monotonic() - started)
            payload = json.dumps(redact_value(doc, secret_values), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
            output.write(payload)
    saved = reference(folder)
    if doc["code"] != "GENERATION_READINESS_READY":
        raise DockerError(doc["code"])
    return saved
