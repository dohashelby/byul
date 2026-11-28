"""Poll only the sockets registered by the matching owned stream Pod."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from owned_tcp_diag import NO_COOKIE, DiagnosticProtocolError, ExactDiagnostic, SocketIdentity, observe, own_netns


ASSETS = Path("/guard-owned-stream")
ENGINE_DATA = Path("/guard-owned-engine")
PROBE_DATA = Path("/guard-owned-probe")
CONNECTION = re.compile("[0-9a-f]{64}")
CASE_IDS = ("lif04-normal-before", "lif04-normal", "lif04-slow", "lif04-slow-close", "lif04-normal-after")
REQUEST_CASE = re.compile(rb"byul-lif04-[0-9a-f]{32}-(normal-before|normal|slow|slow-close|normal-after)")
OPEN_CLIENT = frozenset(("ESTABLISHED", "CLOSE_WAIT"))
CLOSED_CLIENT_STATES = frozenset(("FIN_WAIT_1", "FIN_WAIT_2", "CLOSING", "LAST_ACK", "CLOSE", "TIME_WAIT"))
NS_PER_MS = 1_000_000
CLOSE_KINDS = frozenset(("client_endpoint_kernel_transition_bracket", "client_explicit_close_block", "client_fd_close_call"))


def clock_identity():
    return {
        "time_namespace": os.stat("/proc/self/ns/time").st_ino,
        "time_offsets_sha256": hashlib.sha256(Path("/proc/self/timens_offsets").read_bytes()).hexdigest(),
    }


def write_atomic(path, document):
    tmp = path.with_name("." + path.name + f".{os.getpid()}")
    tmp.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def client_origin(records, *, matching_clock):
    """Bracket the first observed local TCP close transition on the probe."""
    if not matching_clock:
        return None
    last_open = None
    saw_error_since_open = False
    for row in records:
        if row["status"] == "present" and row.get("state") in OPEN_CLIENT:
            last_open = row
            saw_error_since_open = False
        elif (last_open and (row["status"] in ("absent", "time_wait")
                            or (row["status"] == "present" and row.get("state") in CLOSED_CLIENT_STATES))):
            if saw_error_since_open:
                return None
            return {
                "kind": "client_endpoint_kernel_transition_bracket",
                "lower_ns": last_open["start_ns"],
                "upper_ns": row["end_ns"],
                "first_transition": row.get("state", row["status"]),
                "width_ns": row["end_ns"] - last_open["start_ns"],
            }
        elif last_open:
            saw_error_since_open = True
    return None


def read_client_origin(tag, config, clock, directory=PROBE_DATA):
    try:
        document = json.loads((directory / f"close-{tag}.json").read_bytes())
    except FileNotFoundError:
        return None
    origin = document["origin"]
    lower, upper = origin["lower_ns"], origin["upper_ns"]
    if (document["connection_sha256"] != tag or document["clock"] != clock
            or document["pod_uid"] != config["probe_pod_uid"] or document["session"] != config["session"]
            or origin["kind"] not in CLOSE_KINDS
            or type(lower) is not int or type(upper) is not int or not 0 < lower <= upper
            or origin["width_ns"] != upper - lower):
        return None
    return origin


def endpoint_key(local_address, local_port, peer_address, peer_port):
    packed = json.dumps([local_address, local_port, peer_address, peer_port], separators=(",", ":"))
    return hashlib.sha256(packed.encode("ascii")).hexdigest()


def case_from_header(value):
    match = REQUEST_CASE.fullmatch(value) if type(value) is bytes else None
    return "lif04-" + match[1].decode("ascii") if match is not None else None


def select_fields(row, names):
    return {key: row[key] for key in names if key in row}


def project_probe_receipt(receipt, client):
    kept = select_fields(receipt, ("session", "pod_uid", "namespace_uid", "pid", "case_id", "endpoint_key",
                                  "probe_source_sha256", "probe_observer_sha256", "stdlib_socket_sha256",
                                  "fd_close_enter_monotonic_ns"))
    kept["socket"] = client.json()
    kept["clock"] = select_fields(receipt["clock"], ("time_namespace", "time_offsets_sha256"))
    diagnostic = receipt["initial_diagnostic"]
    kept["initial_diagnostic"] = select_fields(diagnostic, ("start_ns", "end_ns", "status", "state", "expiry_ms", "errno",
        "cookie_errno", "tuple_errno", "receive_queue_bytes", "send_queue_bytes", "fd_association"))
    for key, names in (("skmem_bytes", ("rmem_alloc", "wmem_alloc", "fwd_alloc", "wmem_queued", "optmem", "backlog")),
                       ("skmem_capacity_bytes", ("rcvbuf", "sndbuf")), ("meminfo_bytes", ("rmem", "wmem", "fmem", "tmem"))):
        if key in diagnostic:
            kept["initial_diagnostic"][key] = select_fields(diagnostic[key], names) if diagnostic[key] is not None else None
    if "skmem_drops_packets" in diagnostic:
        kept["initial_diagnostic"]["skmem_drops_packets"] = diagnostic["skmem_drops_packets"]
    kept["stock_close_calls"] = select_fields(receipt.get("stock_close_calls", {}), ("shutdown_start", "tracked_close_start",
        "response_close_start", "http_connection_close_start", "close_block_end"))
    for name in ("client_explicit_close_interval", "client_fd_close_interval"):
        if name in receipt:
            kept[name] = select_fields(receipt[name], ("kind", "lower_ns", "upper_ns", "width_ns"))
    return kept


def project_registration(document, identity):
    result = select_fields(document, ("connection_sha256", "native_request_call_sha256", "case_id", "session", "namespace_uid",
        "pod_uid", "probe_pod_uid", "registered_monotonic_ns", "registration_finished_monotonic_ns", "pid", "process_start_ticks", "protocol"))
    result["socket"] = identity.json()
    result["clock"] = select_fields(document["clock"], ("time_namespace", "time_offsets_sha256"))
    result["protocol_source"] = select_fields(document.get("protocol_source", {}), ("file", "sha256", "symbol", "reviewed_protocol_attributes"))
    return result


def read_probe_receipt(registration, config, clock, netns, directory=PROBE_DATA):
    server = SocketIdentity(**registration["socket"])
    key = endpoint_key(server.peer_address, server.peer_port, server.local_address, server.local_port)
    try:
        receipt = json.loads((directory / f"client-{key}.json").read_bytes())
    except FileNotFoundError:
        return None
    if receipt.get("tuple_reuse_observed") is True:
        return None
    client = SocketIdentity(**receipt["socket"])
    if (receipt["clock"] != clock or receipt["session"] != config["session"]
            or receipt["pod_uid"] != config["probe_pod_uid"] or receipt["namespace_uid"] != config["namespace_uid"]
            or receipt["endpoint_key"] != key or receipt.get("case_id") not in CASE_IDS
            or receipt["case_id"] != registration.get("case_id")
            or receipt["probe_source_sha256"] != config["reviewed_probe_source_sha256"]
            or receipt["probe_observer_sha256"] != config["probe_observer_sha256"] or client.netns != netns
            or (client.local_address, client.local_port, client.peer_address, client.peer_port) !=
               (server.peer_address, server.peer_port, server.local_address, server.local_port)):
        return None
    return project_probe_receipt(receipt, client)


def receipt_origin(receipt):
    if receipt is None:
        return None
    intended = receipt["case_id"] == "lif04-slow-close"
    key = "client_explicit_close_interval" if intended else "client_fd_close_interval"
    origin = receipt.get(key)
    positive = receipt["initial_diagnostic"]
    return origin if (origin and positive["status"] == "present"
                      and origin["upper_ns"] >= origin["lower_ns"] >= positive["end_ns"]) else None


def sampling_end(entry, budget_ms):
    anchor = entry["client_origin"]
    if anchor is not None:
        return anchor["upper_ns"] + (budget_ms + 180) * NS_PER_MS
    if entry["local_closure_seen"] is not None:
        # A peer may remain in CLOSE_WAIT after the server has dropped its
        # file reference. The local fallback also allows the peer to publish
        # its clock before the collector stops.
        return entry["local_closure_seen"] + (budget_ms + 1300) * NS_PER_MS
    return None


def validate_registration(document, config):
    socket_id = SocketIdentity(**document["socket"])
    if (document["session"] != config["session"] or document["pod_uid"] != config["engine_pod_uid"]
            or document["namespace_uid"] != config["namespace_uid"]
            or document["probe_pod_uid"] != config["probe_pod_uid"]
            or not CONNECTION.fullmatch(document["connection_sha256"])
            or document.get("case_id") not in CASE_IDS
            or not CONNECTION.fullmatch(document.get("native_request_call_sha256", ""))
            or socket_id.local_address != config["engine_ip"] or socket_id.local_port != 8000
            or socket_id.peer_address != config["probe_ip"]):
        raise ValueError("socket registration outside the authorized Pods")
    return socket_id


def discover_client(diag, server):
    """Mirror the exact registered peer tuple while it is an open socket."""
    probe = SocketIdentity(diag.netns, server.peer_address, server.peer_port,
                           server.local_address, server.local_port, 1, 1)
    try:
        raw = diag.query(probe, NO_COOKIE)
    except (OSError, DiagnosticProtocolError) as exc:
        return None, {"status": "client_identity_discovery_error", "errno": getattr(exc, "errno", None)}
    if not (
        raw["family"] == probe.family and raw["local_address"] == probe.local_address
        and raw["local_port"] == probe.local_port and raw["peer_address"] == probe.peer_address
        and raw["peer_port"] == probe.peer_port and raw["inode"] > 0
        and 0 < raw["cookie"] < NO_COOKIE and raw["state"] in OPEN_CLIENT
    ):
        return None, {"status": "client_open_identity_unavailable"}
    return replace(probe, inode=raw["inode"], cookie=raw["cookie"], ifindex=raw["interface"]), None


def collect(role, *, config_path=ASSETS / "session.json", halt=ASSETS / "HALT"):
    if role not in ("engine", "probe"):
        raise ValueError("unsupported socket poll role")
    config = json.loads(config_path.read_bytes())
    uid = os.environ["BYUL_OWNED_STREAM_POD_UID"]
    if uid != config[f"{role}_pod_uid"] or config["budget_ms"] != 5000 or not 75000 <= config["followup_ms"] <= 90000:
        raise ValueError("socket poll role does not match deployment")
    out = ENGINE_DATA if role == "engine" else PROBE_DATA
    birth = time.monotonic_ns()
    clock = clock_identity()
    ready = {"role": role, "pid": os.getpid(), "uid": os.geteuid(), "pod_uid": uid,
             "netns": own_netns(), "session": config["session"], "clock": clock,
             "poll_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             "diag_source_sha256": hashlib.sha256((ASSETS / "owned_tcp_diag.py").read_bytes()).hexdigest()}
    try:
        diag = ExactDiagnostic()
    except OSError as exc:
        write_atomic(out / "poll-ready.json", {**ready, "netlink_ready": False, "errno": exc.errno})
        return
    write_atomic(out / "poll-ready.json", {**ready, "netlink_ready": True})
    active = {}
    try:
        with (out / "tcp-journal.jsonl").open("x", encoding="utf-8", buffering=1) as log:
            def save(event):
                log.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")

            while not halt.exists() and time.monotonic_ns() - birth < 180_000_000_000:
                tick = time.monotonic_ns()
                paths = tuple(ENGINE_DATA.glob("owned-*.json"))
                if len(paths) > 5:
                    save({"event": "collector_error", "reason": "unexpected_connection_count"})
                    break
                for path in sorted(paths):
                    tag = path.stem.removeprefix("owned-")
                    if tag in active or not CONNECTION.fullmatch(tag):
                        continue
                    reg = json.loads(path.read_bytes())
                    try:
                        server = validate_registration(reg, config)
                    except (ValueError, KeyError, TypeError):
                        save({"event": "registration_error", "connection_sha256": tag})
                        active[tag] = None
                        continue
                    if tag != reg["connection_sha256"] or type(reg.get("registered_monotonic_ns")) is not int:
                        save({"event": "registration_error", "connection_sha256": tag})
                        active[tag] = None
                        continue
                    active[tag] = {"server": server, "registration": reg, "identity": server if role == "engine" else None,
                                   "queried": 0, "next": tick, "local_closure_seen": None,
                                   "client_origin": None, "pre_anchor": [],
                                   "positive_after_window": False,
                                   "identity_posted": False, "finished": False}
                for tag, entry in active.items():
                    if entry is None or entry["finished"] or tick < entry["next"] or entry["queried"] >= 260:
                        continue
                    server = entry["server"]
                    receipt = read_probe_receipt(entry["registration"], config, clock, diag.netns) if role == "probe" else None
                    begin = time.monotonic_ns()
                    if entry["identity"] is None and receipt is not None:
                        entry["identity"] = SocketIdentity(**receipt["socket"])
                        positive = receipt["initial_diagnostic"]
                        save({"event": "sample", "connection_sha256": tag, **positive,
                              "collection_path": "inside_original_stock_probe_while_fd_open"})
                        entry["pre_anchor"].append(positive)
                        sample = observe(diag.query, entry["identity"])
                    elif entry["identity"] is None:
                        identity, error = discover_client(diag, server)
                        if error is not None:
                            sample = {"start_ns": begin, "end_ns": time.monotonic_ns(), **error}
                        else:
                            entry["identity"] = identity
                            sample = observe(diag.query, identity)
                    else:
                        sample = observe(diag.query, entry["identity"])
                    if entry["identity"] is not None and not entry["identity_posted"]:
                        save({"event": "identity", "connection_sha256": tag, "socket": entry["identity"].json()})
                        entry["identity_posted"] = True
                    save({"event": "sample", "connection_sha256": tag, **sample})
                    if entry["local_closure_seen"] is None and (sample["status"] in ("time_wait", "absent")
                            or (role == "probe" and sample.get("state") in CLOSED_CLIENT_STATES)):
                        entry["local_closure_seen"] = sample["end_ns"]
                    if role == "engine" and entry["local_closure_seen"] is None and (path := ENGINE_DATA / f"close-{tag}.json").is_file():
                        closed = json.loads(path.read_bytes())
                        if (closed["clock"] == clock and closed["session"] == config["session"]
                                and closed["pod_uid"] == config["engine_pod_uid"]
                                and closed["connection_sha256"] == tag):
                            entry["local_closure_seen"] = closed["mono_ns"]
                    if role == "probe":
                        previous = entry["client_origin"]
                        direct = (receipt_origin(receipt) if receipt is not None and entry["identity"] is not None
                                  and receipt["socket"] == entry["identity"].json() else None)
                        anchor = direct
                        if direct is None and previous is None:
                            entry["pre_anchor"].append(sample)
                            anchor = client_origin(entry["pre_anchor"], matching_clock=True)
                        if anchor is not None and anchor != previous:
                            write_atomic(PROBE_DATA / f"close-{tag}.json", {
                                "connection_sha256": tag, "pod_uid": uid, "session": config["session"],
                                "clock": clock, "origin": anchor,
                            })
                            entry["pre_anchor"].clear()
                            entry["client_origin"] = anchor
                    else:
                        entry["client_origin"] = read_client_origin(tag, config, clock)
                    entry["queried"] += 1
                    # Permit one boundary sample after the budget to resolve
                    # the measured interval rather than sampling until all requests finish.
                    end = sampling_end(entry, config["budget_ms"])
                    if end is not None and sample["start_ns"] >= end:
                        anchor = entry["client_origin"]
                        horizon = anchor["upper_ns"] + config["followup_ms"] * NS_PER_MS if anchor else None
                        status = sample["status"]
                        if status in ("present", "time_wait"):
                            entry["positive_after_window"] = True
                        reason = None
                        if anchor is None:
                            reason = "client_clock_anchor_unavailable"
                        elif status == "absent":
                            reason = "two_exact_enoent_responses_at_sample"
                        elif sample["start_ns"] >= horizon:
                            reason = "horizon_reached_with_owned_state" if status in ("present", "time_wait") else "horizon_reached_with_lookup_uncertainty"
                        elif not entry["positive_after_window"]:
                            reason = "no_confirmed_residual_at_followup_start"
                        if reason is not None:
                            save({"event": "followup_stop", "connection_sha256": tag, "end_ns": sample["end_ns"],
                                  "last_reading_status": status, "reason": reason, "horizon_ns": horizon})
                            entry["finished"] = True
                        else:
                            wait = (max(250, min(15000, sample["expiry_ms"] - 250)) if status == "time_wait" else 2500)
                            entry["next"] = min(horizon, sample["end_ns"] + wait * NS_PER_MS)
                        continue
                    if entry["local_closure_seen"] is None or tick - entry["local_closure_seen"] < 300_000_000:
                        period = 18_000_000
                    else:
                        period = 100_000_000
                    entry["next"] = sample["end_ns"] + period
                time.sleep(0.005)
            save({"event": "poll_stop", "utc_ns": time.time_ns(), "end_ns": time.monotonic_ns(),
                  "halt_seen": halt.exists(), "connections": len(active)})
    finally:
        diag.close()


if __name__ == "__main__":
    collect(sys.argv[1])
