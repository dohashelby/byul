"""PLT-02 전용 소비. 고정 A1 IPC v1 전송을 사용하며 공통 등록은 별도다.

원래 groups를 그대로 검증한다. 관측 actor/collector/owner는 serving worker나
기업 승인 주체의 증명이 아니며, 전체 IPC PASS를 생성하지 않는다.
"""
from datetime import datetime

from guard.models import canonical_json, sha256_hex
from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.runtime import kubernetes_ipc as common
from guard.runtime.kubernetes_ipc_contract import PROFILE, RESULT_SCHEMA, normalize_inputs
from .http_common import functional_requests, functional_results
from .unix_ipc import path_connection

VERSION = "0.1.0"
CONSUMER_PROFILE = "kubernetes-unix-ipc-consumer-v1"
PROGRAM_SHA256 = "9e593ea6f11b0dcfa32a895376244c0aa9dedae643b2a4964e9e7f07a31edd92"
SOURCE_PINS = {
    "actor_source_sha256": "5fe44e3599199324021f9691d9d048f75ead4ef340f8f24eebd190501cfaf4ea",
    "contract_source_sha256": "dcee148c16c9c97ec2031eb443daf8080c74dd481ab3621e8d3c655b2be31cec",
    "original_probe_sha256": "f096e9d95aed61315b4d129369cd273786fa9dc106ac264d48c670e004269ca2",
}
_FIELDS = {"schema", "profile", "started_at", "finished_at", "environment_ref", "target_sha256", "binding",
    "binding_verified_before_after", "program_sha256", *SOURCE_PINS, "policy_input_sha256", "path_sha256",
    "data_origin", "packet", "available", "requested_observations_complete", "diagnostic_codes", "cleanup_errors",
    "producer_authenticated", "worker_identity_verified", "whole_scope_complete", "security"}
_CLEANUP = "KUBERNETES_PROBE_PARENT_CLEANUP_UNVERIFIED"


def _require(value):
    if not value:
        raise ValueError("KUBERNETES_IPC_CONSUMER_RECORD_INVALID")


def validate_inputs(check, values, *, actor_specs, connect_timeout, total_timeout):
    if check.id != "PLT-02" or check.executor != "unix_ipc" or check.collection_profile != PROFILE:
        raise ValueError("KUBERNETES_IPC_CONSUMER_CHECK_UNSUPPORTED")
    if len(check.functional_probes) != 1:
        raise ValueError("KUBERNETES_IPC_CONSUMER_CONTROL_UNSUPPORTED")
    probe = check.functional_probes[0]
    if (probe.method != "POST" or probe.path != "/v1/completions" or probe.via != "engine"
            or probe.expect != [200] or probe.body != {"model": "{model}", "prompt": "ping", "max_tokens": 1}
            or type(probe.body.get("max_tokens")) is not int):
        raise ValueError("KUBERNETES_IPC_CONSUMER_CONTROL_UNSUPPORTED")
    return normalize_inputs(values, actor_specs=actor_specs, connect_timeout=connect_timeout, total_timeout=total_timeout)


def _read_record(ctx, config):
    transport = ctx.cprobe
    if (not isinstance(transport, KubernetesHTTP) or transport.profile != REQUEST_BINDING_PROFILE
            or ctx.target.get("lab") is not True or transport.lab is not True
            or transport.engine_name != "vllm" or transport.engine_version != "0.31.0"
            or transport.target_sha256 != sha256_hex(canonical_json(ctx.target))
            or canonical_json(transport.environment_ref) != canonical_json(ctx.env.ref().model_dump(mode="json"))):
        raise ValueError("KUBERNETES_IPC_CONSUMER_INTERFACE_UNAVAILABLE")
    record = transport.ipc_snapshot(ctx.policy_values, actor_specs=config["actors"],
        connect_timeout=config["connect_timeout"], total_timeout=config["total_timeout"])
    _require(type(record) is dict and set(record) == _FIELDS)
    _require(record["schema"] == RESULT_SCHEMA and record["profile"] == PROFILE
        and record["data_origin"] == "EXAMPLE" and record["security"] == "UNKNOWN"
        and record["program_sha256"] == PROGRAM_SHA256
        and all(record[k] == v for k, v in SOURCE_PINS.items())
        and canonical_json(record["environment_ref"]) == canonical_json(transport.environment_ref)
        and record["target_sha256"] == transport.target_sha256
        and canonical_json(record["binding"]) == canonical_json(transport.binding)
        and record["policy_input_sha256"] == sha256_hex(canonical_json(ctx.policy_values))
        and record["path_sha256"] == [sha256_hex(p.encode()) for p in config["paths"]])
    for key in ("producer_authenticated", "worker_identity_verified", "whole_scope_complete"):
        _require(record[key] is False)
    for key in ("available", "binding_verified_before_after", "requested_observations_complete"):
        _require(type(record[key]) is bool)
    _require(record["available"] == record["binding_verified_before_after"])
    for key, allowed in (("cleanup_errors", {_CLEANUP}),
                         ("diagnostic_codes", {"KUBERNETES_IPC_CAPTURE_UNAVAILABLE"})):
        codes = record[key]
        _require(type(codes) is list and len(codes) <= 1 and all(type(c) is str and c in allowed for c in codes))
    times = []
    for key in ("started_at", "finished_at"):
        _require(type(record[key]) is str and len(record[key]) <= 40)
        when = datetime.fromisoformat(record[key].replace("Z", "+00:00"))
        _require(when.tzinfo is not None and when.utcoffset() is not None)
        times.append(when)
    _require(times[0] <= times[1])
    _require(not record["requested_observations_complete"] or record["available"] and not record["cleanup_errors"])
    if record["packet"] is not None:
        record["packet"] = common.decode(record["packet"], config)
    return record


def _actor_verified(actor, collector):
    before, after = actor["context_before"], actor["context_after"]
    if not (actor["credential_match_observed"] and actor["context_stable"]
            and before is not None and before == after and collector is not None):
        return False
    requested = actor["requested"]
    return (before["uids"] == [requested["uid"]] * 4 and before["gids"] == [requested["gid"]] * 4
        and before["groups"] == requested["groups"] and before["no_new_privs"] == 1
        and before["pid"] != collector["pid"] and before["start_ticks"] >= collector["start_ticks"] > 0
        and before["namespaces"] == collector["namespaces"]
        and before["boot_sha256"] == collector["boot_sha256"]
        and before["time_offsets_sha256"] == collector["time_offsets_sha256"]
        and all(before[k] & ~collector[k] == 0 for k in
                ("cap_effective", "cap_permitted", "cap_inheritable", "cap_ambient")))


def _analyze(record, config):
    errors, trials, violations, controls = [], [], [], set()
    packet = record["packet"]
    if packet is None:
        return trials, violations, controls, ["IPC_PACKET_UNAVAILABLE"]
    collector = packet["collector_before"]
    source = (record["binding_verified_before_after"] and collector is not None
        and (packet["collector_after"] is None or packet["collector_after"] == collector))
    inspections = [packet[k]["packet"]["rows"] if packet[k] is not None else []
                   for k in ("inspect_before", "inspect_after")]
    if not source:
        errors.append("IPC_SOURCE_OR_IDENTITY_UNVERIFIED")
    if not record["requested_observations_complete"]:
        errors.append("IPC_SELECTED_COLLECTION_INCOMPLETE")
    for actor in packet["actors"]:
        actor_ok = source and _actor_verified(actor, collector)
        if not actor_ok:
            errors.append("IPC_ACTOR_IDENTITY_UNVERIFIED")
        if not actor["child_reaped"]:
            errors.append("IPC_CHILD_CLEANUP_UNVERIFIED")
        elif not actor["child_exit_ok"]:
            errors.append("IPC_CHILD_EXIT_UNVERIFIED")
        attempt = actor["connect_packet"]
        anchors = {a["index"]: a for a in actor["path_anchors"]}
        rows = attempt["rows"] if attempt is not None else []
        for index, observed in enumerate(rows):
            prior, after = [p[index] if len(p) > index else {} for p in inspections]
            anchor = anchors.get(index, {})
            anchor_base = (anchor.get("metadata_complete") is True and anchor.get("kernel_connect_attempted") is True
                and anchor.get("path_binding_stable") is True and anchor.get("finished_monotonic_ns") is not None
                and anchor.get("identity") == prior.get("identity"))
            anchor_ok = anchor_base and (observed["outcome"] == "denied" or anchor.get("identity") == observed.get("identity"))
            stable, connected = path_connection(prior, observed, after)
            connected = bool(actor_ok and anchor_ok and connected)
            denied = actor_ok and stable and anchor_base and observed["outcome"] == "denied"
            uid = actor["requested"]["uid"]
            allowed = str(uid) in config["allowed_ipc_uids"]
            outcome = observed["outcome"] if observed["outcome"] != "denied" or denied else "unknown"
            trials.append({"path_sha256": record["path_sha256"][index], "actor_uid": uid,
                "actor_gid": actor["requested"]["gid"], "actor_groups": list(actor["requested"]["groups"]),
                "allowed": allowed, "reported_outcome": observed["outcome"], "outcome": outcome,
                "connection_verified": connected, "denial_verified": bool(denied),
                "actor_identity_verified": bool(actor_ok), "path_anchor_verified": bool(anchor_ok)})
            if connected and not allowed:
                violations.append(f"{record['path_sha256'][index]}:uid={uid}")
            if connected and allowed:
                controls.add(index)
            if not stable or not anchor_ok:
                errors.append("IPC_ENDPOINT_IDENTITY_UNVERIFIED")
    return trials, sorted(set(violations)), controls, errors


def collect_kubernetes_unix_ipc(ctx, *, actor_specs, connect_timeout, total_timeout):
    """확정 A1 method만 사용. 공통 wrapper는 명시 actor/config를 이 함수에 전달한다."""
    config = validate_inputs(ctx.check, ctx.policy_values, actor_specs=actor_specs,
        connect_timeout=connect_timeout, total_timeout=total_timeout)
    errors, record, trials, violations, controls = [], None, [], [], set()
    try:
        record = _read_record(ctx, config)
        trials, violations, controls, errors = _analyze(record, config)
    except Exception:
        errors.append("IPC_COMMON_OBSERVATION_UNAVAILABLE")
    # Validate all normal responses before using any; never store their content.
    planned, functional, normal_attempted, normal_ok = [], {}, False, False
    try:
        requests, probes = functional_requests(ctx, via="engine")
        planned = list(probes)
        if record is None:
            raise ValueError("IPC_OBSERVATION_SOURCE_UNAVAILABLE")
        normal_attempted = bool(requests)
        responses = ctx.cprobe.normal_requests(ctx.trusted_net, requests, ctx.timeout)
        if (type(responses) is not list or len(responses) != len(requests)
                or any(type(r) is not dict for r in responses)
                or [r.get("id") for r in responses] != [r["id"] for r in requests]):
            responses = []
            errors.append("IPC_NORMAL_CONTROL_RESPONSE_UNCORRELATED")
        functional, _, _ = functional_results(probes, dict(zip(probes, responses)), ctx.env.model.id)
        normal_ok = bool(len(controls) == len(config["paths"]) and functional
            and all(v == "pass" for v in functional.values()))
    except Exception:
        errors.append("IPC_NORMAL_CONTROL_UNVERIFIED")
    try:
        transport_report = ctx.cprobe.report()
    except Exception:
        transport_report = None
        errors.append("IPC_TRANSPORT_REPORT_UNAVAILABLE")
    observations = {
        "unix_ipc_violations": {"value": violations, "collected": True},
        "unix_ipc_trials": {"value": trials or None, "collected": bool(trials)},
        "ipc_scope_complete": {"value": False, "collected": True},
        "functional_probes_planned": {"value": planned, "collected": True},
        "functional_probe_results": {"value": functional or None, "collected": bool(functional)},
    }
    ctx._kubernetes_ipc_cleanup_errors = list(record["cleanup_errors"]) if record is not None else []
    if record is not None and record["packet"] is not None and any(
            not actor["child_reaped"] for actor in record["packet"]["actors"]):
        ctx._kubernetes_ipc_cleanup_errors.append("IPC_CHILD_CLEANUP_UNVERIFIED")
    return observations, {"attempted": normal_attempted, "succeeded": normal_ok,
        "detail": "Selected explicit actor/path connections and correlated normal completion; whole IPC scope unverified"}, [
            {"step": step, "attempted": False} for step in ctx.check.challenge_steps], {
        "profile": CONSUMER_PROFILE, "common_observation": record, "kubernetes": transport_report,
        "collection_errors": sorted(set(errors)), "worker_subject_verified": False, "ipc_scope_complete": False,
        "fixture": {"common_profile": PROFILE, "program_sha256": PROGRAM_SHA256, "consumer_version": VERSION},
        "not_exercised": ["worker_message_consumption", "same_uid_unapproved_process", "abstract_unix", "shared_memory",
                          "tcp_ipc", "tensor_parallel", "whole_ipc_inventory", "enterprise_authorization"]}
