"""REQ-08·SUP-08: 요청 필드 한 개의 거부·제거·소비를 정상 대조와 비교한다.

필드 값은 무해한 고정 fixture이고 본문은 해시로만 남긴다. SUP-08은 캐시 파일 목록과
시험 코드가 import 시 남긴 판본 표식을 함께 대조한다. 캐시 파일만으로 실행을 주장하지 않는다.
"""
from __future__ import annotations

from copy import deepcopy
import re

from guard.models import canonical_json, sha256_hex
from .content_common import bounded_probe, control_state, json_body, record_content
from .http_common import functional_requests

REUSED_TOKEN_PROFILE = "reused-token-input-v1"
CODE_SELECTION_PROFILE = "request-code-selection-v1"
PROFILES = frozenset({REUSED_TOKEN_PROFILE, CODE_SELECTION_PROFILE})
VERSION = "0.10.0"
UNAPPROVED_REVISION = "byul-unapproved-revision"
_FIELD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,3}")
_REVISION = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_IMAGE = re.compile(r"data:image/(png|jpeg);base64,[A-Za-z0-9+/=]{16,3000}")
MODULE_SETTINGS = ("code_module_root", "code_module_repository")
_MARKER_ROOT = re.compile(r"(/[A-Za-z0-9][A-Za-z0-9._-]{0,63}){2,12}")


def code_module_settings(cfg):
    """Optional engine-side dynamic-module listing; both keys or neither."""
    present = [key for key in MODULE_SETTINGS if key in cfg]
    marker_root = cfg.get("code_marker_root")
    if marker_root is not None and (type(marker_root) is not str or not _MARKER_ROOT.fullmatch(marker_root)):
        raise ValueError("code_marker_root must be a bounded absolute path")
    if not present:
        if marker_root is not None:
            raise ValueError("code_marker_root needs module observation")
        return None
    if len(present) != len(MODULE_SETTINGS) or any(type(cfg[key]) is not str or not cfg[key] for key in MODULE_SETTINGS):
        raise ValueError("code module observation needs code_module_root and code_module_repository")
    return {**{key: cfg[key] for key in MODULE_SETTINGS}, "code_marker_root": marker_root}


def challenge_revision(cfg):
    value = cfg.get("unapproved_revision", UNAPPROVED_REVISION)
    if type(value) is not str or _REF.fullmatch(value) is None or ".." in value.split("/"):
        raise ValueError("unapproved_revision must be a bounded revision name")
    return value


def control_body(body, cfg):
    """Attach the configured image to the control's last user message so a multimodal processor is built."""
    image = cfg.get("control_image")
    if image is None:
        return body
    if type(image) is not str or _IMAGE.fullmatch(image) is None:
        raise ValueError("control_image must be a bounded PNG/JPEG data URI")
    body = deepcopy(body)
    messages = body.get("messages")
    if type(messages) is not list or not messages or type(messages[-1]) is not dict or type(messages[-1].get("content")) is not str:
        raise ValueError("control_image needs a chat control whose last message has text content")
    text = messages[-1]["content"]
    messages[-1]["content"] = [{"type": "text", "text": text}, {"type": "image_url", "image_url": {"url": image}}]
    return body


def nested_fields(fields, value):
    """Dotted field paths as top-level request members; each path needs its own top-level member."""
    result = {}
    for field in fields:
        top, *rest = field.split(".")
        if top in result:
            raise ValueError("code_selection_fields need distinct top-level members")
        node = value
        for part in reversed(rest):
            node = {part: node}
        result[top] = node
    return result


def supported(check):
    return getattr(check, "collection_profile", None) in PROFILES


def _rejections(values):
    codes = values.get("rejection_codes")
    if type(codes) is not list or not codes or any(code not in {"400", "403", "413", "422"} for code in codes):
        raise ValueError("rejection_codes must explicitly name supported rejection responses")
    return codes


def _field(value):
    if type(value) is not str or _FIELD.fullmatch(value) is None:
        raise ValueError("request field names must be bounded dotted identifiers")
    return value


def validate_configuration(check, cfg):
    if getattr(check, "collection_profile", None) != REUSED_TOKEN_PROFILE:
        code_module_settings(cfg)
        challenge_revision(cfg)
        if "control_image" in cfg:
            control_body({"messages": [{"role": "user", "content": "x"}]}, cfg)
        return
    ids, size = cfg.get("reused_token_ids"), cfg.get("vocab_size")
    if (type(size) is not int or size < 1 or not cfg.get("vocab_source") or type(ids) is not list
            or not 1 <= len(ids) <= 64 or any(type(v) is not int or not 0 <= v < size for v in ids)):
        raise ValueError("execution settings need reused_token_ids inside vocab_size and its source")


def validate_policy(check, values):
    if type(values) is not dict:
        raise ValueError("request field policy must be a mapping")
    _rejections(values)
    if check.collection_profile == REUSED_TOKEN_PROFILE:
        _field(values.get("reused_token_field"))
        if type(values.get("reused_token_allowed")) is not bool:
            raise ValueError("reused_token_allowed must be an explicit boolean")
        return
    fields = values.get("code_selection_fields")
    if (type(fields) is not list or not 1 <= len(fields) <= 8 or len(set(map(str, fields))) != len(fields)
            or any(_field(field) is None for field in fields)):
        raise ValueError("code_selection_fields requires 1..8 distinct field names")
    nested_fields(fields, None)
    if type(values.get("code_selection_allowed")) is not bool:
        raise ValueError("code_selection_allowed must be an explicit boolean")
    approved = values.get("approved_code_revisions")
    if approved is not None and (type(approved) is not list or not 1 <= len(approved) <= 16
            or len(set(approved)) != len(approved) or any(type(v) is not str or not _REVISION.fullmatch(v) for v in approved)):
        raise ValueError("approved_code_revisions must list 1..16 distinct commit SHA-1 values")


def _assign(body, path, value):
    node = body
    *parents, leaf = path.split(".")
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def consumed_tokens(result):
    body, error = json_body(result)
    if error:
        return None
    for holder in (body, *(body.get("choices") or [])):
        tokens = holder.get("prompt_token_ids") if isinstance(holder, dict) else None
        if type(tokens) is list and tokens and all(type(v) is int and v >= 0 for v in tokens):
            return tokens
    return None


def classify_reused(result, *, fixture, control_tokens, rejection_codes):
    outcome = result.get("outcome")
    if type(outcome) is int and str(outcome) in rejection_codes:
        return "rejected"
    if type(outcome) is not int or not 200 <= outcome < 300:
        return "unknown"
    tokens = consumed_tokens(result)
    if tokens is None or control_tokens is None:
        return "accepted_unverified"
    if tokens == fixture:
        return "consumed"
    return "ignored" if tokens == control_tokens else "accepted_unverified"


def classify_selection(result, *, rejection_codes):
    outcome = result.get("outcome")
    if type(outcome) is int and str(outcome) in rejection_codes:
        return "rejected"
    return "accepted_unverified" if type(outcome) is int and 200 <= outcome < 300 else "unknown"


def classify_modules(state, before, after, approved):
    """Use new import markers, bound to cached module bytes, to resolve one challenge.

    An HTTP rejection can follow an import. Cache creation alone proves neither import nor execution.
    The marker directory must be present and an approved marker must calibrate the normal control.
    """
    if approved is None:
        return state, None
    if not _inventory_ok(before) or not _inventory_ok(after):
        return "accepted_unverified", {"reason": "CODE_EXECUTION_OBSERVATION_UNAVAILABLE"}
    old, new = set(before["revisions"]), set(after["revisions"])
    prior_markers, final_markers = before["markers"], after["markers"]
    if (not before["markers_present"] or not after["markers_present"]
            or not any(rev in approved and rev in old and _marker_matches(prior_markers[rev], before["revisions"][rev])
                       for rev in prior_markers)):
        return "accepted_unverified", {"reason": "APPROVED_EXECUTION_MARKER_MISSING"}
    if any(rev not in approved for rev in old | set(prior_markers)):
        return "accepted_unverified", {"reason": "UNAPPROVED_REVISION_PRESENT_BEFORE_CHALLENGE"}
    selected = sorted(rev for rev in set(final_markers) - set(prior_markers) if rev not in approved)
    if selected:
        if all(rev in new and _marker_matches(final_markers[rev], after["revisions"][rev]) for rev in selected):
            return "consumed", {"selected_revisions": selected,
                                "module_files": {rev: after["revisions"][rev] for rev in selected},
                                "execution_markers": {rev: final_markers[rev] for rev in selected}}
        return "accepted_unverified", {"reason": "UNAPPROVED_MARKER_MODULE_MISMATCH"}
    cached = sorted(rev for rev in new - old if rev not in approved)
    if cached:
        return "accepted_unverified", {"reason": "UNAPPROVED_MODULE_CACHED_WITHOUT_EXECUTION_MARKER",
                                       "cached_revisions": cached}
    if state == "rejected":
        return "rejected", {"approved_revisions_present": sorted(rev for rev in new if rev in approved)}
    if state == "accepted_unverified" and new <= set(approved):
        return "ignored", {"approved_revisions_present": sorted(rev for rev in new if rev in approved)}
    return "accepted_unverified", {"reason": "CODE_SELECTION_UNRESOLVED"}


def _inventory_ok(value):
    return (type(value) is dict and value.get("present") is True and value.get("complete") is True
            and type(value.get("revisions")) is dict and type(value.get("markers")) is dict
            and type(value.get("markers_present")) is bool
            and all(type(k) is str for k in value["revisions"])
            and all(type(k) is str for k in value["markers"]))


def _marker_matches(marker, files):
    return (type(marker) is dict and type(files) is dict and marker.get("schema") == "guard-code-import-marker/1"
            and type(marker.get("module_sha256")) is str and any(
                type(item) is dict and item.get("sha256") == marker["module_sha256"] for item in files.values()))


def _hash(value):
    return sha256_hex(canonical_json(value))


def collect(ctx, cfg):
    values = ctx.policy_values
    requests, probes = functional_requests(ctx, via="engine")
    if len(requests) != 1:
        raise ValueError("request field checks need one engine control")
    template, probe = requests[0], next(iter(probes.values()))
    endpoint = probe.path
    expected_model = ctx.api_model or ctx.env.model.id
    reused = ctx.check.collection_profile == REUSED_TOKEN_PROFILE
    if reused:
        template = deepcopy(template)
        template["body"]["return_token_ids"] = True
    else:
        template = deepcopy(template)
        template["body"] = control_body(template["body"], cfg)
    modules = None if reused else code_module_settings(cfg)
    observer = getattr(getattr(ctx, "cprobe", None), "engine_code_modules", None) if modules else None
    approved = None if reused else values.get("approved_code_revisions")
    module_rows = []

    def inventory(label):
        if observer is None:
            return None
        try:
            value = observer(modules["code_module_root"], modules["code_module_repository"],
                             modules["code_marker_root"])
        except Exception:
            module_rows.append({"label": label, "state": "unavailable"})
            return None
        module_rows.append({"label": label, "state": "collected", "present": value.get("present"),
                            "complete": value.get("complete"), "revisions": value.get("revisions"),
                            "markers_present": value.get("markers_present"), "markers": value.get("markers")})
        return value

    def send(request, label):
        from .req_runner_capture import case_call
        result = case_call(ctx, label, request, lambda: bounded_probe(ctx, ctx.trusted_net, request))
        record_content(ctx, "trusted", "request-field", label, {key: result.get(key) for key in ("outcome", "body_bytes", "body_sha256")})
        return result

    controls, trials, rejections = {}, [], values["rejection_codes"]
    first = send(deepcopy(template), "before")
    controls["before"] = control_state(first, expected_model, endpoint)
    if controls["before"] == "pass":
        control_tokens = consumed_tokens(first) if reused else None
        cases = ([(values["reused_token_field"], cfg["reused_token_ids"])] if reused
                 else [(field, challenge_revision(cfg)) for field in values["code_selection_fields"]])
        for index, (field, fixture) in enumerate(cases):
            request = deepcopy(template)
            _assign(request["body"], field, fixture)
            before = None if reused else inventory(f"before-challenge-{index}")
            result = send(request, f"challenge-{index}")
            module_evidence = None
            if reused:
                state = classify_reused(result, fixture=fixture, control_tokens=control_tokens, rejection_codes=rejections)
            else:
                state = classify_selection(result, rejection_codes=rejections)
                after = inventory(f"after-challenge-{index}")
                state, module_evidence = classify_modules(state, before, after, approved)
            trials.append({"field": field, "outcome": str(result.get("outcome")), "state": state,
                           "request_fixture_sha256": _hash(request["body"]),
                           **({"module_evidence": module_evidence} if module_evidence is not None else {})})
            label = f"after-{index}"
            controls[label] = control_state(send(deepcopy(template), label), expected_model, endpoint)
    normal = {"attempted": True, "succeeded": len(controls) > 1 and all(v == "pass" for v in controls.values()),
              "detail": "challenge 전후 같은 정상 요청을 대조"}
    planned = list(controls) if len(controls) > 1 else ["before"]
    obs = {"functional_probes_planned": {"value": planned, "collected": True},
           "functional_probe_results": {"value": controls, "collected": True}}
    summary = {"profile": ctx.check.collection_profile, "controls": controls, "trials": trials,
               "fixture": {"profile": ctx.check.collection_profile, "control_sha256": _hash(probe.model_dump(mode="json")),
                           "fields_sha256": _hash([row["field"] for row in trials])}}
    done = bool(trials)
    if reused:
        allowed = values["reused_token_allowed"]
        state = trials[0]["state"] if done else None
        obs.update({"reused_input_trials": {"value": trials, "collected": done},
                    "reused_input_state": {"value": state, "collected": done},
                    "reused_input_forbidden": {"value": not allowed, "collected": True},
                    "reused_input_violations": {"value": [row["field"] for row in trials if row["state"] == "consumed" and not allowed],
                                                "collected": done}})
    else:
        fields = values["code_selection_fields"]
        forbidden = not values["code_selection_allowed"]
        resolved = {"rejected", "ignored", "consumed"}
        violations = [{"field": r["field"], "submitted_revision": challenge_revision(cfg),
                       "selected_revisions": r["module_evidence"]["selected_revisions"],
                       "module_files": r["module_evidence"]["module_files"],
                       "execution_markers": r["module_evidence"]["execution_markers"],
                       "basis": "new import marker matches the cached module SHA-256 and was absent before the challenge"}
                      for r in trials if r["state"] == "consumed"]
        obs.update({"code_selection_trials": {"value": trials, "collected": done},
                    "code_selection_forbidden": {"value": forbidden, "collected": True},
                    "code_selection_fields_planned": {"value": fields, "collected": True},
                    "code_selection_rejected_fields": {"value": [r["field"] for r in trials if r["state"] == "rejected"], "collected": done},
                    "code_selection_ignored_fields": {"value": [r["field"] for r in trials if r["state"] == "ignored"], "collected": done},
                    "code_selection_resolved_fields": {"value": [r["field"] for r in trials if r["state"] in resolved], "collected": done},
                    "code_selection_unverified_fields": {"value": [r["field"] for r in trials if r["state"] not in resolved], "collected": done},
                    "code_selection_confirmed_violations": {"value": violations, "collected": done},
                    # True only when the engine's module listing actually showed the final revision for every trial.
                    "code_selection_final_code_verified": {"value": done and all(r["state"] in {"ignored", "consumed"} for r in trials),
                                                           "collected": True}})
        summary["code_module_inventory"] = module_rows
    return obs, normal, [{"step": step, "attempted": done} for step in ctx.check.challenge_steps], summary
