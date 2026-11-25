"""Read one already-captured ordinary response from an exact owned Gateway."""
from datetime import datetime, timezone
from pathlib import Path
import re

from guard.adapters.vllm_file_read_sensor import dependency
from guard.models import DataOrigin, Policy, PolicyStatus, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import (
    GATEWAY_EVENT_PROFILE, GATEWAY_LOOPBACK_AUTHORITY, GATEWAY_LOOPBACK_RESPONSE_ORIGIN,
    GATEWAY_LOOPBACK_RESPONSE_RECEIPT, GATEWAY_NORMAL_RESPONSE_RECEIPT, GATEWAY_RESPONSE_IMAGE as GATEWAY_IMAGE,
    GATEWAY_RESPONSE_IMAGE_CONFIG, KubernetesBindingError, KubernetesHTTP,
)
from .content_common import control_state, leaves
from .kubernetes_gateway_observation import MAX_BYTES, final_records, snapshot_owner, snapshot_record
from .observation_contract import fingerprint
from .response_projection import capture_metadata

OPTION = "req13_gateway_response_snapshot_only"
INPUT = "gateway_response_inputs"
INVALID = "KUBERNETES_RESPONSE_SNAPSHOT_UNVERIFIED"
UNREVIEWED_FIELDS = "KUBERNETES_RESPONSE_SNAPSHOT_FIELDS_UNQUALIFIED"
PROFILE = "req13-gateway-ordinary-response-snapshot-v1"
MANIFEST_SCHEMA = "guard.req13-gateway-response-inputs/1"
RESPONSE_ONLY_SCHEMA = "guard.req13-gateway-response-only/1"
RESPONSE_ONLY_PROFILE = "req13-gateway-ordinary-response-only-v1"
GATEWAY_ENVIRONMENT = "byul-update-normal-observation"
GATEWAY_CONFIG_SHA256 = GATEWAY_RESPONSE_IMAGE_CONFIG.removeprefix("sha256:")
JOURNAL_PRODUCER_SHA256 = "444a827fa471387affa69d35893e816bd14b951b3d31b3fae6c0c19ab9aa473d"
ROUTE = "POST /v1/chat/completions"
BODY_LIMIT = 128 * 1024
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_RFC3339 = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)")
_FILES = {"body", "response_receipt", "audit_receipt", "journal"}
RESPONSE_ONLY_FILES = frozenset({"response", "transport_report", "gateway_owners", "guard_policy"})
_RESPONSE_ONLY_SELECTION = {"source_scope", "path", "sha256", "target_file_sha256", "guard_policy_sha256", "request_sha256"}
_PINS = {"sha256", "guard_policy_sha256", "actor_sha256", "runtime_policy_sha256", "request_sha256"}
_SCOPE_FIELDS = {"path", "record_session", "runtime_target_id", "valid_from", "valid_until"} | _PINS
_OWNER_FIELDS = {"environment_ref", "request_sha256", "engine_owner_sha256", "gateway_owner_sha256"}
_RESPONSE_FIELDS = _OWNER_FIELDS | {
    "body_sha256", "body_bytes", "status", "request_id", "route", "run_sha256",
    "observed_started_ns", "observed_finished_ns", "capture_complete", "eof", "utf8_valid",
    "body_truncated", "parent_cleanup_verified", "probe_source_sha256", "gateway_target_sha256",
    "request_fixture_sha256", "content_type", "probe_owner_sha256",
}
_AUDIT_FIELDS = _OWNER_FIELDS | {
    "event", "gateway_target_sha256", "owner_before_after_verified", "producer_sha256",
    "runtime_policy_sha256", "journal_bytes", "journal_sha256", "selected_row_sha256", "recorded_at",
    "actor_sha256", "session", "independent_denied_event_observed",
}
# Stock chat response fields on the reviewed Gateway route. Dynamic JSON keys
# must never be copied into EVD or interpreted as approved field names.
_PUBLIC_FIELDS = frozenset("""
    id object created model service_tier system_fingerprint
    choices[].index choices[].finish_reason choices[].stop_reason choices[].logprobs
    choices[].token_ids choices[].routed_experts choices[].message.role choices[].message.content
    choices[].message.annotations choices[].message.audio choices[].message.function_call
    choices[].message.reasoning choices[].message.refusal
    usage.prompt_tokens usage.completion_tokens usage.total_tokens usage.prompt_tokens_details
    usage.prompt_tokens_details.cached_tokens usage.completion_tokens_details
    usage.completion_tokens_details.reasoning_tokens
    prompt_logprobs prompt_text prompt_token_ids kv_transfer_params ec_transfer_params metrics
""".split())


def _epoch_ns(value):
    match = _RFC3339.fullmatch(value) if type(value) is str and len(value) <= 40 else None
    if match is None:
        raise ValueError(INVALID)
    seconds, fraction, offset = match.groups()
    timestamp = datetime.fromisoformat(seconds + ("+00:00" if offset == "Z" else offset))
    return _datetime_ns(timestamp) + int((fraction or "").ljust(9, "0"))


def _datetime_ns(timestamp):
    delta = timestamp - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86400 + delta.seconds) * 10**9 + delta.microseconds * 1000


def _policy_window(policy_validity):
    if (type(policy_validity) is not tuple or len(policy_validity) != 2
            or any(bound is not None and (not isinstance(bound, datetime) or bound.utcoffset() is None)
                   for bound in policy_validity)):
        raise ValueError(INVALID)
    lower, upper = (_datetime_ns(bound) if bound is not None else None for bound in policy_validity)
    if lower is not None and upper is not None and lower >= upper:
        raise ValueError(INVALID)
    return lower, upper


def _read(path, expected=None):
    from guard.update_validation import _plain_path

    if not isinstance(path, (str, Path)) or not path:
        raise ValueError(INVALID)
    return dependency(_plain_path(Path(path)), expected)


def snapshot_manifest_bytes(path):
    """Shared raw PLAN digest and executor reader, with a bounded local file."""
    return _read(path)


def snapshot_target_bytes(path):
    """Read the raw saved three-role target under the same file contract."""
    return _read(path)


def _owner_sha256(binding):
    """Fingerprint the existing verify_owned_pod projection."""
    fields = ("namespace_uid", "pod_uid", "container", "container_id", "node", "restart_count", "started_at")
    owner = {key: binding[key] for key in fields}
    owner.update(transport="kubernetes", image_reference=binding["image"], reported_image_id=binding["image_id"])
    return sha256_hex(canonical_json(owner))


def _file_inputs(selection, expected, *, schema=MANIFEST_SCHEMA, fields=_FILES):
    raw = _read(selection["path"], expected)
    manifest = strict_json(raw)
    if type(manifest) is not dict or manifest.keys() != (fields | {"schema"}) or manifest["schema"] != schema:
        raise ValueError(INVALID)
    result = {}
    for key in fields:
        ref = manifest[key]
        if (type(ref) is not dict or ref.keys() != {"path", "sha256"}
                or type(ref["path"]) is not str or not fingerprint(ref["sha256"])):
            raise ValueError(INVALID)
        result[key] = _read(ref["path"], ref["sha256"])
    return result, raw


def _response_fields(raw, response, api_model):
    body = strict_json(raw, limit=BODY_LIMIT)
    text = raw.decode("utf-8")
    result = {"outcome": 200, "body": text, "body_bytes": len(raw)}
    if (type(body) is not dict or type(body.get("choices")) is not list or len(body["choices"]) != 1
            or control_state(result, api_model, "/v1/chat/completions") != "pass"
            or body["usage"]["completion_tokens"] != 1
            or response["body_sha256"] != sha256_hex(raw) or type(response["body_bytes"]) is not int
            or response["body_bytes"] != len(raw)):
        raise ValueError(INVALID)
    fields = leaves(body).keys()
    if not fields <= _PUBLIC_FIELDS:
        raise _UnreviewedFields()
    return sorted(fields), capture_metadata(result)


class _UnreviewedFields(ValueError):
    pass


def _owned_transport(target, env, expected):
    if (type(target) is not dict or sha256_hex(canonical_json(target)) != expected or target.get("lab") is not False
            or target.get("synthetic", False) is not False or target.get("engine_kind") == "stub"
            or not {"kubernetes_routes", "gateway", "direct_targets"}.isdisjoint(target)
            or target.get("observed_version") != env.engine.version):
        raise ValueError(INVALID)
    mapping = target.get("model_mapping")
    if type(mapping) is not dict or mapping.get("schema") != "guard.model-request-mapping/1":
        raise ValueError(INVALID)
    engine_view = {name: value for name, value in target.items() if name != "kubernetes_gateway"}
    # Construction checks the declared bindings and selects the native probe source without calling it.
    transport = KubernetesHTTP(engine_view, env, duration=1, profile=GATEWAY_EVENT_PROFILE)
    gateway = snapshot_owner(target, transport.binding)
    if gateway["image"] != GATEWAY_IMAGE or gateway["image_id"] not in {
            GATEWAY_IMAGE, f"docker-pullable://{GATEWAY_IMAGE}", f"sha256:{GATEWAY_CONFIG_SHA256}"}:
        raise ValueError(INVALID)
    return transport, gateway


def _verified_gateway_owners(owners, gateway, start, finish):
    if type(owners) is not dict or set(owners) != {"before", "after"}:
        raise ValueError(INVALID)
    before, after = (owners[key] for key in ("before", "after"))
    for observation in (before, after):
        if (type(observation) is not dict or set(observation) != {"observed_at_ns", "owner"}
                or sha256_hex(canonical_json(observation["owner"])) != _owner_sha256(gateway)
                or type(observation["observed_at_ns"]) is not int):
            raise ValueError(INVALID)
    first, last = before["observed_at_ns"], after["observed_at_ns"]
    if not 0 < first <= start < finish <= last < 2**63 or _epoch_ns(gateway["started_at"]) > first:
        raise ValueError(INVALID)
    return first, last


def _verified_probe(response, report, transport, gateway, origin, *, allow_gateway_loopback=False):
    row, = report["requests"]
    receipt = row["retained_normal_response"]
    if type(receipt) is not dict:
        raise ValueError(INVALID)
    loopback = receipt.get("schema") == GATEWAY_LOOPBACK_RESPONSE_RECEIPT
    if loopback and (not allow_gateway_loopback or row["id"] != "obs01-before"):
        raise ValueError(INVALID)
    fixture = {"model": transport.api_model, "messages": [{"role": "user", "content": "hello"}],
               "max_tokens": 1, "stream": False}
    expected = {
        "schema": GATEWAY_NORMAL_RESPONSE_RECEIPT,
        "response_sha256": sha256_hex(canonical_json(response)),
        "request_fixture_sha256": sha256_hex(canonical_json(fixture)),
        "gateway_authority_sha256": sha256_hex(canonical_json(["http", gateway["pod_ip"], 8080])),
        "data_origin": origin,
    }
    if loopback:
        expected.update(schema=GATEWAY_LOOPBACK_RESPONSE_RECEIPT, transport_origin=GATEWAY_LOOPBACK_RESPONSE_ORIGIN,
                        source_owner_sha256=_owner_sha256(gateway),
                        gateway_authority_sha256=sha256_hex(canonical_json(GATEWAY_LOOPBACK_AUTHORITY)))
    if (report["profile"] != GATEWAY_EVENT_PROFILE or report["binding"] != transport.binding
            or report["binding_sha256"] != sha256_hex(canonical_json(transport.binding))
            or report["probe_source_sha256"] != transport.source_sha256
            or report["collection_gap"] is not False or report["cleanup_errors"] != []
            or row["id"] not in {"obs01-before", "obs01-after"} or row["id"] != response["id"]
            or row["binding_verified_before_after"] is not True or row["capture_complete"] is not True
            or type(row["outcome"]) is not int or row["outcome"] != 200
            or type(response["outcome"]) is not int or response["outcome"] != 200
            or response["method"] != "POST" or response["mode"] != "json"
            or set(receipt) != set(expected) | {"observed_started_ns", "observed_finished_ns"}
            or any(receipt[key] != value for key, value in expected.items())):
        raise ValueError(INVALID)
    start, finish = receipt["observed_started_ns"], receipt["observed_finished_ns"]
    if type(start) is not int or type(finish) is not int or not 0 < start < finish < 2**63:
        raise ValueError(INVALID)
    return receipt, start, finish


def _response_only(cfg, env, selection, target_sha256, manifest_sha256, guard_policy_sha256,
                   target_file_sha256, policy_validity, policy_values):
    from guard.core.select import _in_scope

    if (selection.keys() - {"transport_origin"} != _RESPONSE_ONLY_SELECTION
            or selection["source_scope"] != RESPONSE_ONLY_SCHEMA
            or "transport_origin" in selection and selection["transport_origin"] != GATEWAY_LOOPBACK_RESPONSE_ORIGIN
            or type(selection["path"]) is not str or not selection["path"]
            or not all(fingerprint(pin) for pin in (target_sha256, manifest_sha256, guard_policy_sha256,
                                                    selection["target_file_sha256"], selection["request_sha256"]))
            or selection["sha256"] != manifest_sha256 or selection["guard_policy_sha256"] != guard_policy_sha256
            or target_file_sha256 not in (None, selection["target_file_sha256"])):
        raise ValueError(INVALID)
    raw_target = _read(cfg["target"], selection["target_file_sha256"])
    transport, gateway = _owned_transport(strict_json(raw_target, limit=256 * 1024), env, target_sha256)
    files, manifest = _file_inputs(selection, manifest_sha256, schema=RESPONSE_ONLY_SCHEMA, fields=RESPONSE_ONLY_FILES)
    policy = Policy.model_validate(strict_json(files["guard_policy"]))
    if (not policy.hash_ok() or policy.ref().sha256 != guard_policy_sha256 or policy.status is not PolicyStatus.CONFIRMED
            or not _in_scope("REQ-13", policy) or policy.values.get("REQ-13") != policy_values
            or (policy.valid_from, policy.valid_until) != policy_validity):
        raise ValueError(INVALID)
    response = strict_json(files["response"], limit=256 * 1024)
    report, owners = (strict_json(files[key]) for key in ("transport_report", "gateway_owners"))
    origin = "EXAMPLE" if env.data_origin is DataOrigin.EXAMPLE else "MEASURED"
    receipt, start, finish = _verified_probe(response, report, transport, gateway, origin, allow_gateway_loopback=True)
    source_origin = receipt.get("transport_origin")
    if source_origin != selection.get("transport_origin"):
        raise ValueError(INVALID)
    first, last = _verified_gateway_owners(owners, gateway, start, finish)
    lower, upper = _policy_window(policy_validity)
    if (_datetime_ns(policy.confirmed_at) > first or lower is not None and first < lower
            or upper is not None and last >= upper
            or any(_epoch_ns(transport.binding[role]["started_at"]) > start for role in ("engine", "probe"))):
        raise ValueError(INVALID)
    request_id = response["gateway_request_id"]
    if (type(request_id) is not str or not _ID.fullmatch(request_id)
            or sha256_hex(request_id.encode()) != selection["request_sha256"]
            or response["content_type"] != "application/json" or response["body_truncated"] is not False
            or any(response[key] is not True for key in ("capture_complete", "utf8_valid", "eof"))):
        raise ValueError(INVALID)
    fields, capture = _response_fields(response["body"].encode("utf-8"), response, transport.api_model)
    source = ({"probe_owner_sha256": _owner_sha256(transport.binding["probe"])} if source_origin is None else
              {"transport_origin": source_origin, "source_owner_sha256": receipt["source_owner_sha256"]})
    return {"profile": RESPONSE_ONLY_PROFILE, "environment_sha256": env.ref().sha256,
            "target_sha256": target_sha256, "target_file_sha256": sha256_hex(raw_target),
            "guard_policy_sha256": guard_policy_sha256, "manifest_sha256": sha256_hex(manifest),
            "guard_policy_data_origin": policy.data_origin.value,
            "probe_source_sha256": transport.source_sha256, "request_sha256": selection["request_sha256"],
            "request_fixture_sha256": receipt["request_fixture_sha256"],
            "engine_owner_sha256": _owner_sha256(transport.binding["engine"]),
            **source, "gateway_owner_sha256": _owner_sha256(gateway),
            "gateway_owners_file_sha256": sha256_hex(files["gateway_owners"]),
            "transport_report_file_sha256": sha256_hex(files["transport_report"]),
            "response_file_sha256": sha256_hex(files["response"]),
            "gateway_owner_before_ns": first, "gateway_owner_after_ns": last,
            "observed_started_ns": start, "observed_finished_ns": finish,
            "guard_policy_valid_from": policy.valid_from.isoformat() if policy.valid_from is not None else None,
            "guard_policy_valid_until": policy.valid_until.isoformat() if policy.valid_until is not None else None,
            "response_fields_seen": fields, "capture": capture}


def verified_snapshot(cfg, env, *, target_sha256, manifest_sha256, guard_policy_sha256, target_file_sha256=None,
                      policy_validity=None, policy_values=None):
    """Return only qualified response paths and non-content provenance."""
    try:
        policy_from, policy_until = _policy_window(policy_validity)
        selection = cfg[INPUT]
        if type(selection) is dict and "source_scope" in selection:
            return _response_only(cfg, env, selection, target_sha256, manifest_sha256, guard_policy_sha256,
                                  target_file_sha256, policy_validity, policy_values)
        if (type(selection) is not dict or selection.keys() - {"target_file_sha256"} != _SCOPE_FIELDS
                or type(selection["path"]) is not str or not selection["path"]
                or not all(fingerprint(selection[key]) for key in _PINS)
                or not all(fingerprint(pin) for pin in (target_sha256, manifest_sha256, guard_policy_sha256))
                or target_file_sha256 is not None and not fingerprint(target_file_sha256)
                or selection["sha256"] != manifest_sha256 or selection["guard_policy_sha256"] != guard_policy_sha256
                or type(selection["runtime_target_id"]) is not str or not _ID.fullmatch(selection["runtime_target_id"])
                or type(selection["record_session"]) is not str
                or not re.fullmatch(r"[0-9a-f]{32}", selection["record_session"])):
            raise ValueError(INVALID)
        valid_from, valid_until = (_epoch_ns(selection[key]) for key in ("valid_from", "valid_until"))
        if "target_file_sha256" in selection:
            declared_target_sha256 = selection["target_file_sha256"]
            if (not fingerprint(declared_target_sha256)
                    or target_file_sha256 not in (None, declared_target_sha256)):
                raise ValueError(INVALID)
            target_file_sha256 = declared_target_sha256
        target_raw = _read(cfg["target"], target_file_sha256)
        target = strict_json(target_raw, limit=256 * 1024)
        transport, gateway = _owned_transport(target, env, target_sha256)
        files, manifest_raw = _file_inputs(selection, manifest_sha256)
        response, audit = (strict_json(files[key]) for key in ("response_receipt", "audit_receipt"))
        request_sha = selection["request_sha256"]
        session = selection["record_session"]
        run_id = transport.binding["run_id"]
        engine = transport.binding["engine"]
        probe = transport.binding["probe"]
        fixture = {"model": transport.api_model, "messages": [{"role": "user", "content": "hello"}],
                   "max_tokens": 1, "stream": False}
        request_fixture_sha = sha256_hex(canonical_json(fixture))
        common = {"environment_ref": env.ref().model_dump(mode="json"), "gateway_target_sha256": target_sha256,
                  "request_sha256": request_sha, "engine_owner_sha256": _owner_sha256(engine),
                  "gateway_owner_sha256": _owner_sha256(gateway)}
        if (type(response) is not dict or response.keys() != _RESPONSE_FIELDS
                or type(audit) is not dict or audit.keys() != _AUDIT_FIELDS
                or any(response[key] != value or audit[key] != value for key, value in common.items())
                or response["route"] != ROUTE or type(response["status"]) is not int or response["status"] != 200
                or response["run_sha256"] != sha256_hex(run_id.encode())
                or type(response["request_id"]) is not str or not _ID.fullmatch(response["request_id"])
                or sha256_hex(response["request_id"].encode()) != request_sha
                or response["request_fixture_sha256"] != request_fixture_sha
                or response["probe_owner_sha256"] != _owner_sha256(probe)
                # A receipt from a different client cannot inherit this reviewed source digest.
                or response["probe_source_sha256"] != transport.source_sha256
                or type(response["content_type"]) is not str or len(response["content_type"]) > 128
                or response["content_type"].partition(";")[0].strip().lower() != "application/json"
                or any(response[key] is not True for key in ("capture_complete", "eof", "utf8_valid", "parent_cleanup_verified"))
                or response["body_truncated"] is not False
                or audit["event"] != "ACCESS_ALLOWED" or audit["owner_before_after_verified"] is not True
                or audit["independent_denied_event_observed"] is not False or audit["session"] != session
                or audit["actor_sha256"] != selection["actor_sha256"]
                or audit["runtime_policy_sha256"] != selection["runtime_policy_sha256"]
                or audit["producer_sha256"] != JOURNAL_PRODUCER_SHA256):
            raise ValueError(INVALID)
        journal = files["journal"]
        if (not 0 < len(journal) <= MAX_BYTES or not journal.endswith(b"\n")
                or type(audit["journal_bytes"]) is not int or audit["journal_bytes"] != len(journal)
                or audit["journal_sha256"] != sha256_hex(journal)):
            raise ValueError(INVALID)
        rows = final_records(journal.decode("utf-8").split("\n"), run_id=run_id, session=session)
        selected = [row for row in rows if row.get("request_id") == response["request_id"]]
        if len(selected) != 1:
            raise ValueError(INVALID)
        final_record, = selected
        request = final_record.get("request")
        # The Runtime writer records the selected dimensions separately from the probe receipt.
        if (final_record.get("route") != ROUTE or type(request) is not dict
                or request.get("model") != fixture["model"]
                or type(request.get("max_tokens")) is not int or request["max_tokens"] != fixture["max_tokens"]
                or "n" not in request or request["n"] is not None
                or final_record.get("stream") is not fixture["stream"]):
            raise ValueError(INVALID)
        source = {"environment": GATEWAY_ENVIRONMENT, "target_id": selection["runtime_target_id"],
                  "runtime_policy_sha256": selection["runtime_policy_sha256"], "request_sha256": request_sha}
        _, record_sha, recorded_at = snapshot_record(canonical_json(final_record) + b"\n", source=source, run_id=run_id,
            values={"observation_session": session, "observation_allowed_actor_sha256": selection["actor_sha256"]})
        start, finish = (response[key] for key in ("observed_started_ns", "observed_finished_ns"))
        # The audit receipt carries the normalized display time. Qualify the original journal time.
        final = _epoch_ns(final_record["recorded_at"])
        if (record_sha != audit["selected_row_sha256"] or recorded_at != audit["recorded_at"]
                or type(start) is not int or type(finish) is not int
                or not 0 < valid_from <= start < finish < valid_until < 2**63
                or not start <= final < valid_until
                or max(_epoch_ns(binding["started_at"]) for binding in (engine, probe, gateway)) > start):
            raise ValueError(INVALID)
        if (policy_from is not None and min(start, finish, final) < policy_from
                or policy_until is not None and max(start, finish, final) >= policy_until):
            raise ValueError(INVALID)
        fields, capture = _response_fields(files["body"], response, transport.api_model)
        return {"profile": PROFILE, "environment_sha256": env.ref().sha256, "target_sha256": target_sha256,
                "target_file_sha256": sha256_hex(target_raw),
                "guard_policy_sha256": guard_policy_sha256, "runtime_policy_sha256": source["runtime_policy_sha256"],
                "manifest_sha256": sha256_hex(manifest_raw), "probe_source_sha256": transport.source_sha256,
                "request_fixture_sha256": request_fixture_sha, "request_sha256": request_sha,
                "engine_owner_sha256": common["engine_owner_sha256"], "gateway_owner_sha256": common["gateway_owner_sha256"],
                "probe_owner_sha256": response["probe_owner_sha256"],
                "journal_producer_sha256": JOURNAL_PRODUCER_SHA256, "journal_sha256": audit["journal_sha256"],
                "actor_sha256": selection["actor_sha256"], "session_sha256": sha256_hex(session.encode("ascii")),
                "observed_started_ns": start, "observed_finished_ns": finish, "recorded_at": recorded_at,
                "valid_from": selection["valid_from"], "valid_until": selection["valid_until"],
                "guard_policy_valid_from": policy_validity[0].isoformat() if policy_validity[0] is not None else None,
                "guard_policy_valid_until": policy_validity[1].isoformat() if policy_validity[1] is not None else None,
                "selected_row_sha256": record_sha, "response_fields_seen": fields, "capture": capture}
    except _UnreviewedFields:
        raise ValueError(UNREVIEWED_FIELDS) from None
    except (AttributeError, KeyError, KubernetesBindingError, OSError, RecursionError, TypeError, ValueError):
        raise ValueError(INVALID) from None
