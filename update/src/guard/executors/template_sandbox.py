"""SUP-13: one supplied-template type-reference witness, never full sandbox PASS.

Reuse the template executor, owned file snapshots, bounded HTTP client and
existing judge. API errors and sandbox names do not prove enforcement. Bodies,
token IDs, source code, exception text and raw inspect data are never persisted.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import re
import secrets
import time
from urllib.parse import urlsplit

from guard.models import canonical_json, sha256_hex, utcnow
from guard.runtime.docker import Docker
from .content_common import content_request, json_body, sample_artifact
from .http_common import Ctx, docker_container_probe
from . import template_supply_approval as supply
from .stream_pressure import _network_binding

PROFILE = "supplied-template-type-boundary-v1"
POLICY = "forbidden_template_attributes"
NORMAL = "guard sandbox control"
DEFAULTS = {"max_output_tokens": 1, "max_total_requests": 6, "max_probe_tokens": 512,
            "max_total_probe_tokens": 4096, "max_duration_seconds": 60}


def validate_policy(check, values, cfg):
    allowed = {*DEFAULTS, "target", "request_timeout", "roles", "wrong_key_env", "denied_key_env", "allow_mutating_routes"}
    if set(cfg) - allowed or cfg.get("allow_mutating_routes", False) is not False:
        raise ValueError("TEMPLATE_TYPE_CONFIGURATION_UNSUPPORTED")
    items = values.get(POLICY)
    if (type(items) is not list or any(type(v) is not str or v != "__class__" for v in items)
            or len(items) != len(set(items))):
        raise ValueError("TEMPLATE_TYPE_POLICY_REQUIRED")
    for key, maximum in {"max_output_tokens": 1, "max_total_requests": 6, "max_probe_tokens": 4096,
                         "max_total_probe_tokens": 65536, "max_duration_seconds": 300}.items():
        value = cfg.get(key, DEFAULTS[key])
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError("TEMPLATE_TYPE_BUDGET_INVALID")
    timeout = cfg.get("request_timeout", 3.0)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ValueError("TEMPLATE_TYPE_TIMEOUT_INVALID")
    if len(check.functional_probes) != 1 or check.functional_probes[0].body != {
        "model": "{model}", "messages": [{"role": "user", "content": NORMAL}], "max_tokens": 1}:
        raise ValueError("TEMPLATE_TYPE_FIXTURE_UNSUPPORTED")
    fp = check.functional_probes[0]
    if fp.via != "engine" or fp.method != "POST" or fp.path != "/v1/chat/completions" or fp.expect != [200]:
        raise ValueError("TEMPLATE_TYPE_FIXTURE_UNSUPPORTED")


def run(executor, check, env, values, adapter, workdir):
    validate_policy(check, values, executor.cfg)
    if adapter is None:
        raise ValueError("TEMPLATE_TYPE_ADAPTER_REQUIRED")
    try:
        target = json.loads(Path(executor.cfg["target"]).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("TEMPLATE_TYPE_TARGET_UNAVAILABLE") from None
    if type(target) is not dict:
        raise ValueError("TEMPLATE_TYPE_TARGET_UNAVAILABLE")
    try:
        url = target["engine"]["trusted_url"]
        if type(url) is not str:
            raise ValueError
        urlsplit(url).port  # Reject malformed ports before common comparison serialization.
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ValueError("TEMPLATE_TYPE_TARGET_UNAVAILABLE") from None
    keys = executor.keys()
    holder = {}
    def probe(network, requests, timeout):
        if executor._container_probe is not None:
            return executor._container_probe(network, requests, timeout)
        if "probe" not in holder:
            image = target.get("probe_image")
            if type(image) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._:/-]*@sha256:[0-9a-f]{64}", image):
                raise ValueError("TEMPLATE_TYPE_PROBE_IMAGE_UNPINNED")
            client = Docker("type" + secrets.token_hex(8))
            if client.run_id == target.get("run_id"):
                raise ValueError("TEMPLATE_TYPE_PROBE_OWNER_COLLISION")
            holder["client"] = client
            holder["probe"] = docker_container_probe(client, image, pull_never=True, parent_deadline=ctx._type_deadline)
        return holder["probe"](network, requests, timeout)
    ctx = Ctx(check=check, env=env, adapter=adapter, target=deepcopy(target), policy_values=values,
              timeout=float(executor.cfg.get("request_timeout", 3)), allow_mutating=False, keys=keys,
              secrets=[v for v in keys.values() if v], cprobe=probe, workdir=workdir, started=utcnow())
    draft = None
    try:
        draft = executor._finish(ctx)
        if executor._container_probe is not None:
            from guard.models import DataOrigin
            draft.data_origin = DataOrigin.EXAMPLE
        elif not ctx._type_live_owner_verified:
            from guard.models import DataOrigin
            if draft.data_origin == DataOrigin.MEASURED:
                draft.data_origin = DataOrigin.DECLARED
        return draft
    finally:
        if "client" in holder:
            try:
                incomplete = bool(holder["client"].cleanup(timeout=30))
            except Exception:
                incomplete = True
            if incomplete and draft is not None:
                draft.cleanup_errors.append("TEMPLATE_TYPE_PROBE_CLEANUP_INCOMPLETE")


def collect(executor, ctx):
    validate_policy(ctx.check, ctx.policy_values, executor.cfg)
    cfg = {key: executor.cfg.get(key, value) for key, value in DEFAULTS.items()}
    deadline = time.monotonic() + cfg["max_duration_seconds"]
    ctx._type_deadline = deadline
    method = getattr(ctx.adapter, "template_type_boundary_contract", None)
    contract = None
    def supported(metadata):
        nonlocal contract
        contract = method(metadata, ctx.env.engine.image_digest, ctx.env.engine.version) if callable(method) else None
        return contract is not None
    target = supply._target(ctx, profile_support=supported)
    errors, rows, sent, tokens_used = [], [], 0, 0
    baseline, bound = None, None
    controls = {"before": "unknown", "after": "unknown"}
    witness = violation = boundary_attempted = False
    reference_normal = reference_type = None
    try:
        endpoint = urlsplit(ctx.engine_url)
        endpoint_ok = (endpoint.scheme == "http" and endpoint.port == 8000
                       and not any((endpoint.username, endpoint.password, endpoint.path, endpoint.query, endpoint.fragment)))
    except (KeyError, TypeError, ValueError, AttributeError):
        endpoint_ok = False

    def snapshot():
        if not target or not contract:
            return None
        snap = supply._snapshot(executor, ctx, target, deadline, expected_path=contract["path"])
        if not snap:
            return None
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            image = executor.docker.image_info(snap["image_id"], timeout=min(5, ctx.timeout, remaining))
            raw = executor.docker.inspect_json(target[0], timeout=min(5, ctx.timeout, deadline - time.monotonic()))
            if (type(image) is not dict or image.get("os") != "linux" or type(image.get("digests")) is not list
                    or target[1] not in image["digests"] or type(image.get("entrypoint")) is not list
                    or not image["entrypoint"] or any(type(arg) is not str for arg in image["entrypoint"])
                    or raw.get("Image") != snap["image_id"] or raw.get("Id") != snap["container_id"]
                    or raw.get("State", {}).get("StartedAt") != snap["started_at"]
                    or raw.get("Config", {}).get("Entrypoint") != image["entrypoint"]):
                return None
            return snap
        except Exception:
            return None

    if (target and contract and type(ctx.env.model.id) is str and 0 < len(ctx.env.model.id) <= 256
            and endpoint_ok):
        baseline = snapshot()
        if baseline:
            try:
                bound = _network_binding(executor.docker, ctx.target, baseline, deadline)
            except Exception:
                errors.append("TEMPLATE_TYPE_ENDPOINT_UNVERIFIED")
    if not baseline or not bound:
        errors.append("TEMPLATE_TYPE_IDENTITY_UNVERIFIED")
    ctx._type_live_owner_verified = baseline is not None and bound is not None

    def send(label, path, body):
        nonlocal sent
        if not baseline or not bound or sent >= cfg["max_total_requests"] or time.monotonic() >= deadline:
            errors.append("TEMPLATE_TYPE_INPUT_OR_BUDGET_UNVERIFIED")
            return None
        if snapshot() != baseline:
            errors.append("TEMPLATE_TYPE_GENERATION_CHANGED")
            return None
        try:
            if _network_binding(executor.docker, ctx.target, baseline, deadline) != bound:
                raise ValueError
            request = content_request(ctx, "engine", label, path, body)
            host = "[" + bound["host"] + "]" if ":" in bound["host"] else bound["host"]
            request["url"] = f"http://{host}:8000{path}"
            sent += 1
            responses = ctx.cprobe(ctx.trusted_net, [request], min(ctx.timeout, deadline - time.monotonic()))
            value, = responses
            if (type(value) is not dict or value.get("id") != label or type(value.get("outcome")) is not int
                    or not 100 <= value["outcome"] <= 599 or snapshot() != baseline
                    or _network_binding(executor.docker, ctx.target, baseline, deadline) != bound):
                raise ValueError
            outcome = value["outcome"]
            rows.append({"case": label, "outcome": outcome, "generation_bound": True})
            ctx.record("trusted", PROFILE, label, "allowed", {"outcome": outcome})
            return value
        except Exception:
            errors.append("TEMPLATE_TYPE_RESPONSE_UNVERIFIED")
            rows.append({"case": label, "generation_bound": False})
            return None

    def tokenize(label, body):
        nonlocal tokens_used
        result = send(label, "/tokenize", {"model": ctx.env.model.id, **body, "add_special_tokens": False,
                                           "return_token_strs": False, "add_generation_prompt": True})
        value, error = json_body(result or {})
        if (result is None or result.get("outcome") != 200 or error
                or not supply._tokens(value.get("tokens"), cfg["max_probe_tokens"])
                or type(value.get("count")) is not int or value["count"] != len(value["tokens"])
                or value.get("max_model_len") != target[2]["max_model_len"]
                or tokens_used + len(value["tokens"]) > cfg["max_total_probe_tokens"]):
            return None
        tokens_used += len(value["tokens"])
        return value["tokens"]

    def normal(label):
        nonlocal tokens_used
        body = {"model": ctx.env.model.id, "messages": [{"role": "user", "content": NORMAL}],
                "max_tokens": 1, "temperature": 0, "return_token_ids": True,
                "add_special_tokens": False, "add_generation_prompt": True}
        result = send(label, "/v1/chat/completions", body)
        value, error = json_body(result or {})
        try:
            choice = value["choices"][0]
            good = (result["outcome"] == 200 and not error and value["model"] == ctx.env.model.id
                    and type(value["choices"]) is list and len(value["choices"]) == 1
                    and type(choice["message"]["content"]) is str and choice["finish_reason"] in {"stop", "length"}
                    and type(value["usage"]["completion_tokens"]) is int and value["usage"]["completion_tokens"] == 1
                    and supply._tokens(value.get("prompt_token_ids"), cfg["max_probe_tokens"])
                    and value.get("prompt_token_ids") == reference_normal
                    and tokens_used + len(reference_normal) + 1 <= cfg["max_total_probe_tokens"])
            if good:
                tokens_used += len(reference_normal) + 1
            return "pass" if good else "unknown"
        except (TypeError, KeyError, IndexError, AttributeError):
            return "unknown"

    if baseline and bound:
        reference_normal = tokenize("type-reference-normal", {"prompt": contract["normal_rendered"]})
        reference_type = tokenize("type-reference-result", {"prompt": contract["reference_rendered"]})
        if reference_normal and reference_type and reference_normal != reference_type:
            actual = tokenize("type-normal-render", {"messages": [{"role": "user", "content": NORMAL}]})
            if actual == reference_normal:
                controls["before"] = normal("type-before")
                witness = controls["before"] == "pass"
            if witness:
                boundary_attempted = True
                actual = tokenize("type-boundary-render", {"messages": [{"role": "user", "content": contract["challenge_message"]}]})
                violation = bool(actual == reference_type and contract["attribute"] in ctx.policy_values[POLICY])
                controls["after"] = normal("type-after")
    observations = {"template_identity_verified": baseline is not None and bound is not None,
                    "template_render_witness_verified": witness, "forbidden_template_type_reference_verified": violation,
                    "template_sandbox_scope_complete": False,
                    "functional_probes_planned": ["before", "after"], "functional_probe_results": controls}
    summary = {"profile": PROFILE, "requests_sent": sent, "response_projections": rows, "diagnostics": sorted(set(errors)),
               "owner": baseline, "network_binding_sha256": sha256_hex(canonical_json(bound)) if bound else None,
               "fixture": {"profile": PROFILE, "settings": cfg, "attribute": "__class__",
                           "template_sha256": target[3]["sha256"] if target else None},
               "limitations": ["Only a harmless supplied fixed-template type-reference rendering branch is observed.",
                   "API errors, sandbox names and missing observations never prove enforcement.",
                   "No full sandbox PASS, resource limit, arbitrary object/call, worker file-read, output attribution, GPU or enterprise approval proof."]}
    sample_artifact(ctx, "template_samples.json", summary)
    return ({key: {"value": value, "collected": True} for key, value in observations.items()},
            {"attempted": sent > 0, "succeeded": all(v == "pass" for v in controls.values()),
             "detail": "fixed rendering witness and before/after bounded normal inference"},
            [{"step": step, "attempted": flag} for step, flag in zip(ctx.check.challenge_steps, (sent > 0, boundary_attempted, controls["after"] == "pass"))],
            summary)
