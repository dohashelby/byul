"""Validate and project source-qualified, same-loader normal file reads."""

from guard.adapters import vllm_approved_file_read_composition as source
from guard.adapters import vllm_file_read_metadata as legacy
from guard.adapters import vllm_file_read_sensor as read
from guard.adapters import vllm_lifecycle_catalog as catalog


LOADER_FIELDS = frozenset({"loader_iterator_observation_sha256", "loader_source_sha256",
                           "loader_method_code_sha256", "loader_iterator_code_sha256"})
FIELDS = legacy.FIELDS - {"root_load_sha256"} | LOADER_FIELDS
IDENTITY = tuple(key for key in legacy.IDENTITY if key != "root_load_sha256") + (
    "source_sha256", *sorted(LOADER_FIELDS))
HASH_FIELDS = frozenset({"handle_sha256", "call_sha256", "model_object_sha256", "loader_call_sha256",
                        "loader_model_sha256", "normalized_path_sha256", "cwd_sha256", "scope_sha256",
                        "source_method_code_sha256"}) | LOADER_FIELDS


def _nonnegative(value):
    return type(value) is int and 0 <= value < 2**63


def validate_sample(value, prepared):
    if type(value) is not dict or value.keys() != FIELDS or not catalog.approved_file_read(prepared["profile"]):
        raise ValueError("APPROVED_FILE_READ_SAMPLE_INVALID")
    pins = catalog.row(prepared["profile"])["sources"]
    phase = value["phase"]
    returned = phase == "READ_RETURN"
    digest = value["returned_bytes_sha256"]
    status = value["hash_status"]
    if (type(phase) is not str or type(status) is not str
            or value["schema"] != source.EVENT_SCHEMA or value["point"] != read.POINT
            or value["source_module"] != source.WEIGHTS or value["source_sha256"] != pins[source.WEIGHTS]["sha256"]
            or value["loader_source_sha256"] != pins[source.LOADER]["sha256"] or value["source_verified"] is not True
            or value["observer_runtime"] != source.RUNTIME or value["observer_code_sha256"] != source.PRIMITIVE_CODE_SHA256
            or value["scope_sha256"] != prepared["assets"]["model-read-scope.json"]["sha256"]
            or any(not read.fingerprint(value[key]) for key in HASH_FIELDS)
            or value["model_object_sha256"] != value["loader_model_sha256"]
            or value["operation_index"] != 1 or type(value["operation_index"]) is not int
            or phase not in read.PHASES or any(value[key] is not False for key in legacy.TRUTH)
            or value["source_fd_read_return_observed"] is not returned
            or type(value["fd_closed_observed"]) is not bool
            or value["fd_closed_observed"] is not (phase == "CLOSE_RETURN") and phase != "CLOSE_RAISE"
            or value["exception_kind"] not in ({"exception", "base-exception"} if phase.endswith("RAISE") else {None})
            or not all(_nonnegative(value[key]) for key in ("operation_started_ns", "operation_finished_ns"))
            or value["operation_started_ns"] > value["operation_finished_ns"]
            or returned and (not _nonnegative(value["returned_bytes"]) or status not in {"complete", "too-large"}
                             or (not read.fingerprint(digest) if status == "complete" else digest is not None))
            or not returned and (value["returned_bytes"] is not None or digest is not None or status != "disabled")):
        raise ValueError("APPROVED_FILE_READ_SAMPLE_INVALID")
    for key in ("fd_before", "fd_after", "path_before", "path_after"):
        row = value[key]
        if row is None:
            continue
        fields = legacy.SNAPSHOT | ({"offset"} if key.startswith("fd_") else set())
        hashes = {"device_sha256", "inode_sha256"}
        if (type(row) is not dict or row.keys() != fields or row["ctime_basis"] != "change"
                or any(not read.fingerprint(row[name]) for name in hashes) or type(row["regular"]) is not bool
                or any(not _nonnegative(row[name]) for name in fields - hashes - {"ctime_basis", "regular"})):
            raise ValueError("APPROVED_FILE_READ_SNAPSHOT_INVALID")


def journal(rows, prepared):
    scope = prepared["model_read_scope"]
    allowed = {row["normalized_path_sha256"]: row for row in scope["files"]}
    handles = {}
    for row in rows:
        value = row["payload"]
        validate_sample(value, prepared)
        path, count, status = (value[key] for key in ("normalized_path_sha256", "returned_bytes", "hash_status"))
        if (path not in allowed or scope["hash_model_bytes"] is not True
                or status == "complete" and count > scope["max_hash_bytes"]
                or status == "too-large" and count <= scope["max_hash_bytes"]):
            raise ValueError("APPROVED_FILE_READ_SCOPE_MISMATCH")
        handle = value["handle_sha256"]
        history = handles.setdefault(handle, [])
        if history:
            previous = history[-1]
            prior = previous["payload"]
            if (previous["seq"] >= row["seq"] or prior["operation_finished_ns"] > value["operation_started_ns"]
                    or any(value[key] != prior[key] for key in IDENTITY)
                    or prior["phase"] not in {"OPEN_RETURN", "READ_RETURN", "READ_RAISE"}
                    or (prior["phase"] == "OPEN_RETURN" and not value["phase"].startswith(("READ_", "CLOSE_")))
                    or (prior["phase"].startswith("READ_") and not value["phase"].startswith("CLOSE_"))):
                raise ValueError("APPROVED_FILE_READ_ORDER_INVALID")
        elif value["phase"] not in {"OPEN_RETURN", "OPEN_RAISE"}:
            # A missing initial event is a coverage gap, never a standalone receipt.
            raise ValueError("APPROVED_FILE_READ_ORDER_INVALID")
        history.append(row)
    return handles


def _closed_read(history, reference):
    opened, read_row, closed = history
    observed = read_row["payload"]
    payloads = [entry["payload"] for entry in history]
    fds = [legacy.identity(value[key]) for value in payloads for key in ("fd_before", "fd_after")
           if value[key] is not None]
    before, after = observed["fd_before"], observed["fd_after"]
    count = observed["returned_bytes"]
    full = bool(before is not None and after is not None and opened["payload"]["fd_before"] is not None
                and closed["payload"]["fd_before"] is not None and before["offset"] == 0
                and after["offset"] == count == after["bytes"] and all(fd["regular"] and fd == fds[0] for fd in fds))
    paths_match = bool(full and all(legacy.identity(value[key]) == fds[0]
                                   for value in payloads for key in ("path_before", "path_after")))
    compared = full and observed["hash_status"] == "complete"
    return {
        "root_event": [opened["seq"]], "source_events": [entry["seq"] for entry in history],
        "loader_iterator_observation_sha256": observed["loader_iterator_observation_sha256"],
        "source_sha256": observed["source_sha256"], "source_method_code_sha256": observed["source_method_code_sha256"],
        "loader_source_sha256": observed["loader_source_sha256"],
        "loader_method_code_sha256": observed["loader_method_code_sha256"],
        "loader_iterator_code_sha256": observed["loader_iterator_code_sha256"],
        "loader_call_sha256": observed["loader_call_sha256"], "model_object_sha256": observed["model_object_sha256"],
        "normalized_path_sha256": observed["normalized_path_sha256"], "reference_sha256": reference["reference_sha256"],
        "read_return_bytes": count, "read_return_bytes_sha256": observed["returned_bytes_sha256"],
        "hash_status": observed["hash_status"], "close_observed": True, "full_fd_read_return_observed": full,
        "path_fd_metadata_match_observed": paths_match, "declared_reference_comparison_observed": compared,
        "declared_reference_bytes_match_observed": bool(compared and count == reference["bytes"]
            and observed["returned_bytes_sha256"] == reference["reference_sha256"]),
    }


def analyze(packet, prepared):
    if (not catalog.approved_file_read(prepared["profile"]) or type(packet) is not dict
            or packet.get("session") != prepared["session"]
            or type(packet.get("recordings")) is not list or len(packet["recordings"]) > 32):
        raise ValueError("APPROVED_FILE_READ_SESSION_INVALID")
    scope = prepared["model_read_scope"]
    allowed = {row["normalized_path_sha256"]: row for row in scope["files"]}
    candidates, paths = [], set()
    unjoined = open_failures = 0
    bounded = False
    for number, recording in enumerate(packet["recordings"]):
        capture = recording.get("capture")
        if capture is None:
            continue
        rows = capture["records"]
        if (not rows or rows[0]["event"] != "START" or rows[0]["payload"]["profile"] != prepared["profile"]
                or rows[0]["payload"]["session"] != prepared["session"]):
            raise ValueError("APPROVED_FILE_READ_SESSION_INVALID")
        context = rows[0]["payload"]["context"]
        owner_bound = (capture.get("process_context_verified") is True and recording.get("session_owner_match") is True
                       and recording.get("listed_file_identity_match") is True)
        for history in journal([row for row in rows if row["event"] == source.EVENT], prepared).values():
            phases = [row["payload"]["phase"] for row in history]
            if phases == ["OPEN_RAISE"]:
                if (owner_bound and (capture.get("provenance") or {}).get("owner_binding_verified") is True
                        and capture["summary"]["recording_closed"] is True):
                    open_failures += 1
                continue
            if not owner_bound or phases != ["OPEN_RETURN", "READ_RETURN", "CLOSE_RETURN"]:
                unjoined += 1
                continue
            if len(candidates) >= 256:
                bounded = True
                continue
            path = history[1]["payload"]["normalized_path_sha256"]
            row = _closed_read(history, allowed[path])
            row["root_event"] = [number, *row["root_event"]]
            row["source_events"] = [[number, seq] for seq in row["source_events"]]
            candidates.append({**row, "recording": number, "pid": context["pid"], "start_ticks": context["start_ticks"],
                               "recording_closed": capture["summary"]["recording_closed"] is True,
                               "declared_reference_mismatch_observed": row["declared_reference_comparison_observed"]
                                   and not row["declared_reference_bytes_match_observed"]})
            paths.add(path)
    missing = ["tensor_storage_and_backend_apply_completion_unverified",
               "worker_assignment_and_rank_shard_inventory_unverified", "approval_and_inspection_authority_unverified"]
    if not candidates:
        missing.append("same_loader_model_file_read_candidate_unavailable")
    if paths != allowed.keys():
        missing.append("declared_scope_file_read_candidates_unavailable")
    if bounded:
        missing.append("model_file_candidate_output_bound")
    return {
        "schema": "guard-approved-model-file-read-candidates/1", "profile": prepared["profile"],
        "candidates": candidates, "groups": [{"declared_paths_without_candidate_count": len(allowed.keys() - paths)}],
        "unjoined_read_count": unjoined, "open_failure_count": open_failures, "missing": missing,
        "declared_scope": {"scope_sha256": prepared["assets"]["model-read-scope.json"]["sha256"],
                           "declared_model_id_sha256": scope["model_sha256"],
                           "declared_revision_sha256": scope["revision_sha256"],
                           "declared_inventory_sha256": scope["inventory_sha256"],
                           "manifest_bytes_sha256": scope["manifest_bytes_sha256"], "reference_origin": scope["data_origin"],
                           "declared_path_count": len(allowed)},
        **{key: False for key in legacy.TRUTH}, "scope_complete": False,
    }
