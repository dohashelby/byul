"""PLT07 전용 소비: 명시 UID/netns TCP 사건과 독립 수신 관측의 결합.

고정 A1 전송만 사용한다. 관측 helper는 serving worker가 아니며 거부/불완전
관측으로 전체 egress PASS나 정상 업무 성공을 만들지 않는다.
"""
from copy import deepcopy
from datetime import datetime, timezone

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.runtime import kubernetes_egress as common
from guard.runtime.kubernetes_egress_contract import PROFILE, SCHEMA, normalize, integer, digest
from .boundary_egress import _sender, _records, _when, _require, _CODES

VERSION = "0.1.0"
CONSUMER_PROFILE = "kubernetes-boundary-egress-consumer-v1"
PROGRAM_SHA256 = "2314e60244997eb641f7d2a64292f39b4c9bcb24a79502b0ee716c02b83ccf0a"
SOURCE_PINS = {
    "executors/egress_probe.py": "cd5b395f08dc141074964b0804b37f5f9cd3aafbe3db4271b0128dae57d23687",
    "executors/egress_receiver.py": "f16f8544908e038ddd90d66516eafc3a52cc3bc8b673cccdf4b8e05254aae599",
    "executors/boundary_egress.py": "21b135d6c80be03c99a3df540fb035d991e21985557be7aec63a5a5a60caae79",
    "runtime/kubernetes_ipc_actor.py": "5fe44e3599199324021f9691d9d048f75ead4ef340f8f24eebd190501cfaf4ea",
}
SOURCE_FIELDS = {
    "helper_source_sha256": "9e1d71ade479e3b6ac9f51e968f47996ddda05026c7fea8a15ed081754f3539c",
    "contract_source_sha256": "09b21321d0d0af87163492118a4407ad69e3beeff6fe188bb2f6d1334fc93975",
    "file_reader_sha256": "f8de10f8a6eaaeeb21a31e177e2db617e94dc6a31bdccb8e2c47cc739d38de13",
}
FIELDS = {"schema", "profile", "environment_ref", "target_sha256", "receiver_target_sha256",
    "policy_input_sha256", "actor_requested", "binding", "model_mapping", "receiver_binding", "program_sha256",
    "source_pins", *SOURCE_FIELDS, "correlation_sha256", "started_at", "finished_at", "receiver_before", "sender",
    "receiver_after", "binding_verified_before_after", "requested_observations_complete", "diagnostic_codes",
    "cleanup_errors", "data_origin", "producer_authenticated", "worker_identity_verified", "node_runtime_image_verified",
    "whole_scope_complete", "security", "normal_control"}
CLEANUP = "KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"
DIAGNOSTICS = {"KUBERNETES_EGRESS_CAPTURE_UNAVAILABLE", "KUBERNETES_EGRESS_SENDER_INCOMPLETE",
    "KUBERNETES_EGRESS_RECEIVER_AFTER_INCOMPLETE"}


def _valid(value):
    _require(value, "EGRESS_COMMON_RECORD_INVALID")


def validate_inputs(check, values, *, actor_spec, connect_timeout, total_timeout):
    if check.id != "PLT-07" or check.executor != "boundary_egress" or check.collection_profile != PROFILE:
        raise ValueError("KUBERNETES_EGRESS_CONSUMER_CHECK_UNSUPPORTED")
    if check.functional_probes:
        raise ValueError("KUBERNETES_EGRESS_CONSUMER_CONTROL_UNSUPPORTED")
    return normalize(values, actor_spec=actor_spec, connect_timeout=connect_timeout, total_timeout=total_timeout)


def _helper(value, mode):
    # Common decode accepts raw file documents. Its public envelope already
    # contains typed file rows: validate that shape without inventing raw bytes.
    if value is None:
        return None
    candidate = deepcopy(value)
    file = candidate.get("file") if type(candidate) is dict else None
    if mode == "receiver" and type(candidate) is dict:
        candidate["file"] = None
    result = common.decode(candidate, mode)
    if mode == "receiver" and file is not None:
        _valid(type(file) is dict and set(file) == {"rows", "file_identity", "capture_sha256", "bytes"}
            and digest(file["capture_sha256"]) and integer(file["bytes"], 1, 16384))
        identity = file["file_identity"]
        _valid(type(identity) is dict and set(identity) == {"device", "inode", "size", "mtime_ns", "ctime_ns"}
            and all(integer(v) for v in identity.values()) and identity["size"] == file["bytes"])
        _records(file["rows"])
        for index, row in enumerate(file["rows"]):
            common._identity(row["identity"])
            common._endpoint(row["local"], listening=index == 0)
            _valid(row["received_bytes"] <= 32 and len(canonical_json(row)) <= 2048)
            if index:
                common._endpoint(row["peer"])
                _valid(digest(row["correlation_sha256"]))
        result["file"] = file
    return result


def _read_record(ctx, config, receiver_target):
    t = ctx.cprobe
    if (not isinstance(t, KubernetesHTTP) or t.profile != REQUEST_BINDING_PROFILE
            or ctx.target.get("lab") is not True or t.lab is not True
            or t.engine_name != "vllm" or t.engine_version != "0.31.0"
            or t.target_sha256 != sha256_hex(canonical_json(ctx.target))
            or canonical_json(t.environment_ref) != canonical_json(ctx.env.ref().model_dump(mode="json"))):
        raise ValueError("EGRESS_COMMON_INTERFACE_UNAVAILABLE")
    start = datetime.now(timezone.utc)
    record = t.egress_snapshot(ctx.policy_values, receiver_target=receiver_target,
        actor_spec=config["actor_spec"], connect_timeout=config["connect_timeout"], total_timeout=config["total_timeout"])
    finish = datetime.now(timezone.utc)
    _valid(type(record) is dict and set(record) == FIELDS and len(canonical_json(record)) <= 196608)
    _valid(record["schema"] == SCHEMA and record["profile"] == PROFILE
        and record["program_sha256"] == PROGRAM_SHA256 and record["source_pins"] == SOURCE_PINS
        and all(record[k] == v for k, v in SOURCE_FIELDS.items())
        and record["data_origin"] == "EXAMPLE" and record["security"] == "UNKNOWN"
        and type(record["normal_control"]) is dict and set(record["normal_control"]) == {"attempted", "succeeded"}
        and record["normal_control"]["attempted"] is False and record["normal_control"]["succeeded"] is None
        and canonical_json(record["environment_ref"]) == canonical_json(t.environment_ref)
        and record["target_sha256"] == t.target_sha256 == ctx.policy_values["source_target_sha256"]
        and record["receiver_target_sha256"] == sha256_hex(canonical_json(receiver_target)) == ctx.policy_values["receiver_target_sha256"]
        and record["policy_input_sha256"] == sha256_hex(canonical_json(ctx.policy_values))
        and canonical_json(record["binding"]) == canonical_json(t.binding)
        and canonical_json(record["model_mapping"]) == canonical_json(t.model_mapping)
        and canonical_json(record["receiver_binding"]) == canonical_json(common._receiver(t, receiver_target, ctx.policy_values))
        and canonical_json(record["actor_requested"]) == canonical_json(config["actor_spec"]) and digest(record["correlation_sha256"]))
    for key in ("producer_authenticated", "worker_identity_verified", "node_runtime_image_verified", "whole_scope_complete"):
        _valid(record[key] is False)
    for key in ("binding_verified_before_after", "requested_observations_complete"):
        _valid(type(record[key]) is bool)
    for key, allowed in (("cleanup_errors", {CLEANUP}), ("diagnostic_codes", DIAGNOSTICS)):
        codes = record[key]
        _valid(type(codes) is list and len(codes) <= len(allowed)
            and all(type(c) is str and c in allowed for c in codes) and len(codes) == len(set(codes)))
    _valid(_when(record["started_at"]) <= _when(record["finished_at"]))
    _valid(not record["requested_observations_complete"] or record["binding_verified_before_after"]
        and not record["cleanup_errors"] and not record["diagnostic_codes"])
    record = deepcopy(record)
    for key, mode in (("sender", "sender"), ("receiver_before", "receiver"), ("receiver_after", "receiver")):
        record[key] = _helper(record[key], mode)
    return record, (start, finish)


def _stable_context(helper):
    return (helper is not None and helper["context_before"] is not None
        and helper["context_before"] == helper["context_after"])


def _analyze(record, config, receiver_target, window):
    p, actor = config["policy_values"], config["actor_spec"]
    trial = {"profile": PROFILE, "policy_rule_sha256": sha256_hex(canonical_json(p)), "verified": False}
    errors, successes = [], []
    if not record["requested_observations_complete"]:
        errors.append("EGRESS_SELECTED_COLLECTION_INCOMPLETE")
    errors.extend(record["diagnostic_codes"])
    errors.extend(record["cleanup_errors"])
    try:
        _require(record["binding_verified_before_after"], "EGRESS_INSTANCE_CHANGED")
        helpers = [record[k] for k in ("receiver_before", "sender", "receiver_after")]
        _require(all(_stable_context(h) for h in helpers), "EGRESS_SOURCE_UNVERIFIED")
        before, sending, after = helpers
        for h in helpers:
            errors.extend(h["diagnostic_codes"])
        sc = sending["context_before"]
        _require(sc["uids"] == [actor["uid"]] * 4 and sc["gids"] == [actor["gid"]] * 4
            and sc["groups"] == actor["groups"] and sc["namespaces"]["net"] == f"net:[{p['subject_net_namespace_inode']}]",
            "EGRESS_SOURCE_UNVERIFIED")
        sender = _sender(sending["sender"])
        trial.update(sender_outcome=sender["outcome"], sender_ack_verified=sender["ack_verified"],
            final_deadline_guard_verified=sender["deadline_guard_verified"], sender_received_bytes=sender["received_bytes"])
        _require(before["file"] is not None and after["file"] is not None, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        prior, final = before["file"], after["file"]
        initial, rows = prior["rows"], final["rows"]
        _require(rows[:len(initial)] == initial and all(prior["file_identity"][k] == final["file_identity"][k]
            for k in ("device", "inode")), "EGRESS_FILE_CHANGED")
        startup = initial[0]
        # Each collector is stable within its own invocation. The later reader
        # is a new process; compare namespace/boot/time domain, not its PID.
        bc, ac = before["context_before"], after["context_before"]
        _require(all(bc[k] == ac[k] for k in ("namespaces", "boot_sha256", "time_offsets_sha256")),
            "EGRESS_RECEIVER_PROCESS_UNVERIFIED")
        command = ["python3", "-u", "-c", common.source()[1], str(p["forbidden_receiver_port"])]
        for h in (before, after):
            process, collector = h["receiver_process"], h["context_before"]
            _require(process is not None and process["complete"] and process["identity"] == startup["identity"]
                and process["identity"]["pid"] == receiver_target["receiver_pid"]
                and process["command_sha256"] == sha256_hex(canonical_json(command))
                and startup["socket_inode"] in process["socket_inodes"]
                and collector["pid"] != process["identity"]["pid"]
                and collector["namespaces"]["net"] == f"net:[{process['identity']['net_namespace_inode']}]",
                "EGRESS_RECEIVER_PROCESS_UNVERIFIED")
        _require(startup["identity"]["net_namespace_inode"] != p["subject_net_namespace_inode"]
            and startup["local"]["port"] == p["forbidden_receiver_port"]
            and startup["local"]["ip"] in {"0.0.0.0", p["forbidden_receiver_ip"]}, "EGRESS_RECEIVER_UNVERIFIED")
        expected_identity = {"pid": sc["pid"], "start_time_ticks": sc["start_ticks"], "uid": actor["uid"],
            "net_namespace_inode": p["subject_net_namespace_inode"]}
        _require(sender["connected"] and sender["marker_sent"] and sender["process_identity_stable"]
            and sender["connection_guard_verified"] and sender["identity_before"] == sender["identity_after"] == expected_identity
            and sender["socket_inode"] is not None and sender["sent_bytes"] == 32
            and sender["correlation_sha256"] == record["correlation_sha256"]
            and sender["peer"] == {"ip": p["forbidden_receiver_ip"], "port": p["forbidden_receiver_port"]}
            and sender["local"] is not None and sender["local"]["port"] > 0
            and sender["local"]["ip"] == record["binding"]["engine"]["pod_ip"], "EGRESS_OPERATION_UNVERIFIED")
        matches = [r for r in rows[len(initial):] if r["correlation_sha256"] == record["correlation_sha256"]]
        _require(len(matches) == 1, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        receipt = matches[0]
        _require(receipt["peer"] == sender["local"] and receipt["local"] == sender["peer"]
            and receipt["received_bytes"] == 32, "EGRESS_RECEIVER_RECORD_UNVERIFIED")
        connected, sent, received = _when(sender["connected_at"]), _when(sender["marker_sent_at"]), _when(receipt["observed_at"])
        # Common envelope timestamps have second precision. The consumer's
        # precise invocation window bounds source events without rounding them
        # or broadening the explicit policy validity. Clock authority stays UNKNOWN.
        _require(window[0] <= connected <= sent <= received <= window[1]
            and _when(p["validity_start"]) <= connected <= sent <= received <= _when(p["validity_end"]),
            "EGRESS_CLOCK_WINDOW_UNVERIFIED")
        connection = sha256_hex(canonical_json({"source": sender["local"], "destination": sender["peer"]}))
        trial.update(verified=True, connection_sha256=connection, correlation_sha256=record["correlation_sha256"],
            sender_identity=sender["identity_before"], receiver_identity=startup["identity"],
            source_binding=record["binding"]["engine"], receiver_binding=record["receiver_binding"],
            actor_gid=actor["gid"], actor_groups=list(actor["groups"]),
            connection_guard_verified=True, receiver_ack_sent=receipt["ack_sent"],
            operation_timestamps={"connected_at": connected.isoformat(), "marker_sent_at": sent.isoformat(), "received_at": received.isoformat()},
            receiver_record_sha256=sha256_hex(canonical_json(receipt)),
            receiver_capture_before_sha256=prior["capture_sha256"], receiver_capture_after_sha256=final["capture_sha256"])
        successes.append(sha256_hex(canonical_json({"rule": trial["policy_rule_sha256"],
            "connection": connection, "correlation": record["correlation_sha256"]})))
    except Exception as exc:
        code = str(exc) if type(exc) is ValueError else ""
        errors.append(code if code in _CODES else "EGRESS_OPERATION_UNVERIFIED")
        return trial, successes, errors
    return trial, successes, errors


def collect_kubernetes_boundary_egress(ctx, *, receiver_target, actor_spec, connect_timeout, total_timeout):
    config = validate_inputs(ctx.check, ctx.policy_values, actor_spec=actor_spec,
        connect_timeout=connect_timeout, total_timeout=total_timeout)
    record, window, trials, successes, errors = None, None, [], [], []
    try:
        record, window = _read_record(ctx, config, receiver_target)
        trial, successes, errors = _analyze(record, config, receiver_target, window)
        trials.append(trial)
    except Exception:
        errors.append("EGRESS_COMMON_OBSERVATION_UNAVAILABLE")
    try:
        report = ctx.cprobe.report()
    except Exception:
        report = None
        errors.append("EGRESS_TRANSPORT_REPORT_UNAVAILABLE")
    ctx._kubernetes_egress_cleanup_errors = list(record["cleanup_errors"]) if record is not None else []
    return {
        "forbidden_egress_successes": {"value": successes, "collected": True},
        "egress_trials": {"value": trials or None, "collected": bool(trials)},
        "egress_scope_complete": {"value": False, "collected": True},
    }, {"attempted": False, "succeeded": None, "detail": "EGRESS_NORMAL_CONTROL_UNSUPPORTED"}, [
        {"step": step, "attempted": False} for step in ctx.check.challenge_steps], {
        "profile": CONSUMER_PROFILE, "common_observation": record, "kubernetes": report,
        "operator_window": {"started_at": window[0].isoformat(), "finished_at": window[1].isoformat()} if window else None,
        "collection_errors": sorted(set(errors)), "worker_subject_verified": False, "egress_scope_complete": False,
        "fixture": {"common_profile": PROFILE, "program_sha256": PROGRAM_SHA256, "consumer_version": VERSION},
        "not_exercised": ["worker_only_subject", "SDK_acquisition", "cache", "usage", "telemetry", "other_destinations",
            "other_operations", "dns_redirect_tls_proxy", "all_egress_inventory", "denial_enforcement", "functional_control",
            "clock_authority", "enterprise_authorization"]}
