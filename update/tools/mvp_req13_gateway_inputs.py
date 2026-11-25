"""Assemble REQ-13 inputs from independently pinned ordinary Gateway capture files."""

import os
from pathlib import Path
import re

from guard.core.select import _in_scope
from guard.executors import kubernetes_response_snapshot as snapshot
from guard.executors.kubernetes_gateway_observation import MAX_BYTES, RECORD_PATH, snapshot_owner, snapshot_record
from guard.executors.kubernetes_lifecycle_capture import PROFILE as JOURNAL_READER_PROFILE
from guard.executors.owned_capture import _FILE_READER
from guard.executors.kubernetes_response_content import KubernetesResponseContentExecutor
from guard.models import DataOrigin, Environment, Policy, PolicyStatus, canonical_json, load_pack, sha256_hex
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import GATEWAY_EVENT_PROFILE, GATEWAY_LOOPBACK_RESPONSE_ORIGIN, KubernetesHTTP
from guard.update_validation import _plain_path, reserve_output
from tools import mvp_gateway_preparation as gateway
from tools.mvp_a_input_preflight import _read

SCHEMA = "guard.req13-gateway-retention-sources/1"
RESPONSE_ONLY_SOURCE_SCHEMA = "guard.req13-gateway-response-only-sources/1"
RESPONSE_CAPTURE_SOURCE_SCHEMA = "guard.req13-gateway-response-capture-sources/1"
_INVALID = "REQ13_GATEWAY_CAPTURE_SOURCE_INVALID"
_FILES = {
    "environment", "target", "guard_policy", "preparation", "response", "transport_report",
    "gateway_owners", "journal", "journal_receipt",
}
_CAPTURE_MEMBERS = snapshot.RESPONSE_ONLY_FILES - {"guard_policy"}
_RESPONSE_ONLY_FILES = _FILES - {"journal", "journal_receipt"}
_SOURCE_FILES = {
    SCHEMA: _FILES,
    RESPONSE_ONLY_SOURCE_SCHEMA: _RESPONSE_ONLY_FILES,
    RESPONSE_CAPTURE_SOURCE_SCHEMA: (_RESPONSE_ONLY_FILES - _CAPTURE_MEMBERS) | {"capture"},
}
_CAPTURE_LIMIT = len(_CAPTURE_MEMBERS) * gateway.FILE_LIMIT + 128
_SOURCE_LIMITS = {"journal": MAX_BYTES, "capture": _CAPTURE_LIMIT}
_SOURCE_FIELDS = {"schema", "files", "side", "actor_sha256", "request_sha256", "record_session"}
_RESPONSE_ONLY_SOURCE_FIELDS = {"schema", "files", "side", "request_sha256"}


def _require(condition):
    if not condition:
        raise ValueError(_INVALID)


def _capture_members(raw):
    """Split the native canonical capture packet without changing its member bytes."""
    packet = strict_json(raw, limit=_CAPTURE_LIMIT)
    _require(type(packet) is dict and packet.keys() == _CAPTURE_MEMBERS and raw == canonical_json(packet))
    members = {name: canonical_json(value) for name, value in packet.items()}
    _require(all(len(value) <= gateway.FILE_LIMIT for value in members.values()))
    return packet, members


def _read_pinned(pointer, *, limit):
    _require(type(pointer) is dict and set(pointer) == {"path", "sha256"}
             and type(pointer["path"]) is str and bool(pointer["path"])
             and snapshot.fingerprint(pointer["sha256"]))
    path = _plain_path(Path(pointer["path"]))
    raw, _ = _read(path, max_bytes=limit)
    _require(sha256_hex(raw) == pointer["sha256"])
    return path, raw


def _verified_gateway_journal(owners, capture, journal, binding, gateway_sha256, start, finish, origin):
    snapshot._verified_gateway_owners(owners, binding["gateway"], start, finish)
    _require(type(capture) is dict and set(capture) == {"file", "transport"})
    receipt, report = capture["file"], capture["transport"]
    read, = report["reads"]
    expected_binding = {"context": binding["context"], "run_id": binding["run_id"], "engine": binding["gateway"]}
    identity = receipt["file_identity"]
    journal_sha256 = sha256_hex(journal)
    _require(type(identity) is dict and set(identity) == {"device", "inode", "size", "mtime_ns", "ctime_ns"}
             and all(type(value) is int and value >= 0 for value in identity.values()) and identity["size"] == len(journal)
             and read == {"program": "file", "owner_before_after_verified": True,
                          "transport_complete": True, "diagnostic_codes": [],
                          "file_path_sha256": sha256_hex(RECORD_PATH.encode("utf-8")),
                          "file_bytes_sha256": journal_sha256}
             and report["programs"]["file"]["source_sha256"] == sha256_hex(_FILE_READER.encode())
             and receipt["owner_binding_verified"] is True
             and sha256_hex(canonical_json(receipt["owner"])) == gateway_sha256
             and type(receipt["bytes"]) is int and receipt["bytes"] == len(journal)
             and receipt["capture_sha256"] == journal_sha256
             and report["profile"] == JOURNAL_READER_PROFILE
             and report["target_binding_sha256"] == sha256_hex(canonical_json(expected_binding))
             and report["collection_gap"] is False and "parent_cleanup_unverified" not in report
             and report["injected_runner"] is (origin == "EXAMPLE"))
    return receipt


def _write(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
    return {"path": str(path), "sha256": sha256_hex(raw)}


def _verified_response(documents, transport, gateway_binding, origin, request_sha256, *, allow_gateway_loopback):
    response = documents["response"]
    receipt, start, finish = snapshot._verified_probe(
        response, documents["transport_report"], transport, gateway_binding, origin,
        allow_gateway_loopback=allow_gateway_loopback)
    body = response["body"].encode("utf-8")
    _require(0 < len(body) <= snapshot.BODY_LIMIT and type(response["body_bytes"]) is int
             and len(body) == response["body_bytes"] and sha256_hex(body) == response["body_sha256"]
             and all(response[key] is True for key in ("capture_complete", "utf8_valid", "eof"))
             and response["body_truncated"] is False and response["content_type"] == "application/json")
    snapshot._response_fields(body, response, transport.api_model)
    request_id = response["gateway_request_id"]
    _require(type(request_id) is str and sha256_hex(request_id.encode()) == request_sha256)
    return receipt, start, finish, body, request_id


def save_loopback_capture(capture, output, *, environment, target, guard_policy, request_sha256, pack=None):
    """Save a completed native loopback capture in a new private directory.

    The capture is the return of capture_retained_gateway_response, and the
    environment, bound target, Guard policy and request digest are supplied
    independently. The returned pointer is files.capture for retain-response.
    """
    try:
        sources = {name: _read_pinned(pointer, limit=gateway.FILE_LIMIT) for name, pointer in (
            ("environment", environment), ("target", target), ("guard_policy", guard_policy))}
        documents = {name: strict_json(raw, limit=gateway.FILE_LIMIT) for name, (_, raw) in sources.items()}
        env = Environment.model_validate(documents["environment"])
        policy = Policy.model_validate(documents["guard_policy"])
        pack = pack or load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
        _require(env.hash_ok() and policy.hash_ok() and policy.pack == pack.ref()
                 and policy.status is PolicyStatus.CONFIRMED and _in_scope("REQ-13", policy))
        values = policy.values.get("REQ-13", {})
        _require(values.keys() == {"allowed_response_fields", "allowed_error_fields",
                                  "forbidden_return_options", "required_targets"}
                 and values["required_targets"] == ["gateway"])
        KubernetesResponseContentExecutor({"target": str(sources["target"][0])}).validate_policy(
            pack.checks["REQ-13"], values)
        target_doc = documents["target"]
        transport, gateway_binding = snapshot._owned_transport(target_doc, env, sha256_hex(canonical_json(target_doc)))
        raw = canonical_json(capture)
        packet, _ = _capture_members(raw)
        origin = "EXAMPLE" if env.data_origin is DataOrigin.EXAMPLE else "MEASURED"
        receipt, start, finish, _, request_id = _verified_response(
            packet, transport, gateway_binding, origin, request_sha256, allow_gateway_loopback=True)
        _require(receipt.get("transport_origin") == GATEWAY_LOOPBACK_RESPONSE_ORIGIN
                 and snapshot._ID.fullmatch(request_id) is not None)
        first, last = snapshot._verified_gateway_owners(packet["gateway_owners"], gateway_binding, start, finish)
        lower, upper = snapshot._policy_window((policy.valid_from, policy.valid_until))
        _require(snapshot._datetime_ns(policy.confirmed_at) <= first
                 and (lower is None or lower <= first) and (upper is None or last < upper)
                 and all(snapshot._epoch_ns(transport.binding[role]["started_at"]) <= start
                         for role in ("engine", "probe")))
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(_INVALID) from None

    output = reserve_output(Path(output), protected=[path for path, _ in sources.values()])
    path = output / "capture.json"
    try:
        output.chmod(0o700)
        pointer = _write(path, raw)
        for source, expected in sources.values():
            _require(_read(_plain_path(source), max_bytes=gateway.FILE_LIMIT)[0] == expected)
    except (OSError, ValueError):
        path.unlink(missing_ok=True)
        output.rmdir()
        raise ValueError(_INVALID) from None
    return pointer


def prepare(inputs_path, inputs_sha256, output, *, pack=None):
    """Pin the new closed input envelope using already collected data from one owner.

    response and transport_report are the original bounded transport return and
    its report. gateway_owners are observed before/after verify_owned_pod
    projections with original epoch-ns observation times. The response-only
    schema requires a confirmed Guard policy and the independently selected
    request. Its capture-packet variant takes one pinned canonical serialization
    of capture_retained_gateway_response(..., loopback=True) in files.capture
    in place of the three separate capture pointers. The audited schema also
    requires actor, session and the existing GatewayRecordReader receipt.
    """
    try:
        source_path, source_raw = _read_pinned({"path": str(inputs_path), "sha256": inputs_sha256},
                                               limit=gateway.FILE_LIMIT)
        request = strict_json(source_raw, limit=gateway.FILE_LIMIT)
        _require(type(request) is dict and request.get("schema") in _SOURCE_FILES)
        schema = request["schema"]
        response_only = schema != SCHEMA
        _require(set(request) == (_RESPONSE_ONLY_SOURCE_FIELDS if response_only else _SOURCE_FIELDS)
                 and request["side"] in {"baseline", "candidate"} and type(request["files"]) is dict
                 and request["files"].keys() == _SOURCE_FILES[schema]
                 and snapshot.fingerprint(request["request_sha256"]))
        if not response_only:
            _require(snapshot.fingerprint(request["actor_sha256"]) and type(request["record_session"]) is str
                     and re.fullmatch("[0-9a-f]{32}", request["record_session"]))
        sources = {name: _read_pinned(pointer, limit=_SOURCE_LIMITS.get(name, gateway.FILE_LIMIT))
                   for name, pointer in request["files"].items()}
        documents = {name: strict_json(raw, limit=gateway.FILE_LIMIT) for name, (_, raw) in sources.items()
                     if name not in {"journal", "capture"}}
        capture_members = {}
        if schema == RESPONSE_CAPTURE_SOURCE_SCHEMA:
            packet, capture_members = _capture_members(sources["capture"][1])
            documents.update(packet)
        env = Environment.model_validate(documents["environment"])
        policy = Policy.model_validate(documents["guard_policy"])
        pack = pack or load_pack(Path(__file__).resolve().parents[1] / "packs/PACK-BASELINE")
        _require(env.hash_ok() and policy.hash_ok() and policy.pack == pack.ref() and _in_scope("REQ-13", policy))
        origin = "EXAMPLE" if env.data_origin is DataOrigin.EXAMPLE else "MEASURED"
        target = documents["target"]
        engine_target = {key: value for key, value in target.items() if key != "kubernetes_gateway"}
        transport = KubernetesHTTP(engine_target, env, duration=1, profile=GATEWAY_EVENT_PROFILE)
        gateway_binding = snapshot_owner(target, transport.binding)
        prep = documents["preparation"]
        _require(prep["image"] == gateway.GATEWAY_IMAGE and prep["archive_sha256"] == gateway.ARCHIVE_SHA256
                 and prep["producer_sha256"] == gateway.PRODUCER_SHA256 and prep["gateway_environment"] == gateway.GATEWAY_ENV)
        prepared = prep["sides"][request["side"]]
        engine_path, engine_raw = _read_pinned(prepared["engine_target"], limit=gateway.FILE_LIMIT)
        _require(prepared["environment_ref"] == env.ref().model_dump(mode="json")
                 and strict_json(engine_raw, limit=gateway.FILE_LIMIT) == engine_target)
        runtime_policy_sha = prepared["runtime_policy_sha256"]
        policy_raw, = (raw.encode("utf-8") for raw in prepared["deployment"]["configuration"]["data"].values()
                       if sha256_hex(raw.encode("utf-8")) == runtime_policy_sha)
        runtime_policy = strict_json(policy_raw, limit=gateway.FILE_LIMIT)
        _require(runtime_policy["target_id"] == prepared["runtime_target_id"])
        response = documents["response"]
        response_source, start, finish, body, request_id = _verified_response(
            documents, transport, gateway_binding, origin, request["request_sha256"],
            allow_gateway_loopback=response_only)
        if capture_members:
            _require(response_source.get("transport_origin") == GATEWAY_LOOPBACK_RESPONSE_ORIGIN)
        gateway_sha = gateway._owner_sha256(gateway_binding)
        run_id = transport.binding["run_id"]
        if response_only:
            snapshot._verified_gateway_owners(documents["gateway_owners"], gateway_binding, start, finish)
        else:
            journal = sources["journal"][1]
            capture = _verified_gateway_journal(documents["gateway_owners"], documents["journal_receipt"], journal,
                                               {**transport.binding, "gateway": gateway_binding}, gateway_sha, start, finish, origin)
            selected_source = {"environment": gateway.GATEWAY_ENV, "target_id": runtime_policy["target_id"],
                               "runtime_policy_sha256": runtime_policy_sha, "request_sha256": request["request_sha256"]}
            values = {"observation_session": request["record_session"],
                      "observation_allowed_actor_sha256": request["actor_sha256"]}
            event, row_sha, recorded_at = snapshot_record(journal, source=selected_source, values=values, run_id=run_id)
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        raise ValueError(_INVALID) from None

    response_digest = sha256_hex(canonical_json(target))
    target_path, target_raw = sources["target"]
    target_file_sha256 = sha256_hex(target_raw)
    protected = [source_path, engine_path, *(file for file, _ in sources.values())]
    directory = reserve_output(Path(output), protected=protected)
    directory.chmod(0o700)
    written = []
    try:
        def write(name, raw):
            path = directory / name
            # Register the newly reserved path before writing so partial output is removed.
            written.append(path)
            return _write(path, raw)

        selection_fields = {"target_file_sha256": target_file_sha256,
                            "guard_policy_sha256": policy.ref().sha256, "request_sha256": request["request_sha256"]}
        if response_only:
            selection_fields["source_scope"] = snapshot.RESPONSE_ONLY_SCHEMA
            if "transport_origin" in response_source:
                selection_fields["transport_origin"] = response_source["transport_origin"]
            manifest = {"schema": snapshot.RESPONSE_ONLY_SCHEMA, **{
                key: {"path": str(sources[key][0]), "sha256": sha256_hex(sources[key][1])}
                for key in sorted(snapshot.RESPONSE_ONLY_FILES - capture_members.keys())}}
            manifest.update({key: write(f"{key}.json", raw) for key, raw in capture_members.items()})
        else:
            selection_fields.update(runtime_policy_sha256=runtime_policy_sha, runtime_target_id=runtime_policy["target_id"],
                record_session=request["record_session"], actor_sha256=request["actor_sha256"],
                valid_from=runtime_policy["valid_from"], valid_until=runtime_policy["valid_until"])
            common = {"environment_ref": env.ref().model_dump(mode="json"), "gateway_target_sha256": response_digest,
                      "request_sha256": request["request_sha256"], "engine_owner_sha256": gateway._owner_sha256(transport.binding["engine"]),
                      "gateway_owner_sha256": gateway_sha}
            response_receipt = {**common, **{key: response[key] for key in (
                "body_bytes", "body_sha256", "body_truncated", "utf8_valid", "capture_complete", "eof", "content_type")},
                "status": response["outcome"], "request_id": request_id, "route": snapshot.ROUTE, "run_sha256": sha256_hex(run_id.encode()),
                "observed_started_ns": start, "observed_finished_ns": finish, "parent_cleanup_verified": True,
                "probe_source_sha256": transport.source_sha256, "request_fixture_sha256": response_source["request_fixture_sha256"],
                "probe_owner_sha256": gateway._owner_sha256(transport.binding["probe"])}
            audit_receipt = {**common, "event": event["event"], "owner_before_after_verified": capture["owner_binding_verified"],
                "producer_sha256": gateway.PRODUCER_SHA256, "runtime_policy_sha256": runtime_policy_sha,
                "journal_bytes": capture["bytes"], "journal_sha256": capture["capture_sha256"],
                "selected_row_sha256": row_sha, "recorded_at": recorded_at, "actor_sha256": event["actor_sha256"],
                "session": request["record_session"], "independent_denied_event_observed": False}
            manifest = {"schema": snapshot.MANIFEST_SCHEMA, "body": write("response.bin", body),
                        "response_receipt": write("response.json", canonical_json(response_receipt)),
                        "audit_receipt": write("audit.json", canonical_json(audit_receipt)),
                        "journal": {"path": str(sources["journal"][0]), "sha256": capture["capture_sha256"]}}
        pointer = write("manifest.json", canonical_json(manifest))
        selection = {"target": str(target_path), snapshot.OPTION: True, snapshot.INPUT: {**pointer, **selection_fields}}
        executor = KubernetesResponseContentExecutor(selection, expected_target_file_sha256=target_file_sha256)
        executor.prepared_response_snapshot(pack.checks["REQ-13"], env, policy.values.get("REQ-13", {}),
            expected_target_sha256=response_digest, expected_manifest_sha256=pointer["sha256"],
            expected_policy_sha256=policy.ref().sha256, policy_validity=(policy.valid_from, policy.valid_until))
        for name, (path, raw) in sources.items():
            limit = _SOURCE_LIMITS.get(name, gateway.FILE_LIMIT)
            if _read(path, max_bytes=limit)[0] != raw:
                raise ValueError(_INVALID)
        if _read(source_path, max_bytes=gateway.FILE_LIMIT)[0] != source_raw or _read(engine_path, max_bytes=gateway.FILE_LIMIT)[0] != engine_raw:
            raise ValueError(_INVALID)
        write("selection.json", canonical_json(selection))
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        for path in reversed(written):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise ValueError(_INVALID) from None
    return selection
