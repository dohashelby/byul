"""Bind independent socket and object observations to a sealed product run."""

from __future__ import annotations

import hashlib
import io
import json
import re
import sqlite3
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from owned_lifetime_assessment import CASE_IDS, full_assessment, ownership_timeline, socket_followup
from owned_stream_observer import COUNT_FIELDS, RESOURCE_NAMES
from owned_stream_poll import client_origin, project_registration, read_client_origin, read_probe_receipt, receipt_origin, validate_registration, write_atomic
from owned_tcp_diag import SKMEM_FIELDS, assess


STORE_QUERY = "SELECT id, revision, kind, content_sha256, body FROM objects ORDER BY id, revision"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def ref(obj):
    return {**{k: obj[k] for k in ("kind", "id", "revision")}, "sha256": obj["content_sha256"]}


def load_documents(root):
    export = root / "export"
    raw_zip = (export / "evidence.zip").read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw_zip)) as z:
        manifest = json.loads(z.read("manifest.json"))
        files = manifest["files"]
        if (manifest["format"] != "guard-store-bundle/1" or set(z.namelist()) != {"manifest.json", *files}
                or len(z.infolist()) != len(files) + 1 or "store.sqlite" not in files):
            raise ValueError("product evidence bundle inventory mismatch")
        for key, value in files.items():
            if (not 0 < value["bytes"] <= 16 * 1024**2
                    or (key != "store.sqlite" and not re.fullmatch("artifacts/[0-9a-f]{64}", key))):
                raise ValueError("product evidence member not permitted")
            member = z.read(key)
            if (len(member) != value["bytes"] or sha(member) != value["sha256"]
                    or (key != "store.sqlite" and key.rsplit("/", 1)[1] != value["sha256"])):
                raise ValueError("product evidence member digest mismatch")
        database = z.read("store.sqlite")
        contents = {key: z.read(key) for key in files if key != "store.sqlite"}
    with sqlite3.connect(":memory:") as db:
        db.deserialize(database)
        db.execute("pragma query_only=ON")
        if db.execute("pragma quick_check").fetchall() != [("ok",)]:
            raise ValueError("invalid evidence database")
        rows = db.execute(STORE_QUERY).fetchall()
    disk, = root.glob("stores/*/store.sqlite")
    with sqlite3.connect(disk.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        if rows != db.execute(STORE_QUERY).fetchall():
            raise ValueError("evidence bundle disagrees with emitted product store")
    stored = {}
    for id_, revision, kind, expected, body in rows:
        doc = json.loads(body)
        seal = ref(doc)
        if (seal != {"kind": kind, "id": id_, "revision": revision, "sha256": expected}
                or expected != sha(canonical({k: v for k, v in doc.items() if k != "content_sha256"}))
                or kind in stored):
            raise ValueError("product document identity or seal mismatch")
        stored[kind] = doc
    for kind, basename in (("RPT", "report.json"), ("ENV", "environment.json"), ("PLAN", "plan.json")):
        if stored[kind] != json.loads((export / basename).read_bytes()):
            raise ValueError("product export disagrees with stored document")
    return stored, contents, {"zip_sha256": sha(raw_zip), "sqlite_sha256": sha(database),
                             "report_file_sha256": sha((export / "report.json").read_bytes())}


def verify_product(root, run_metadata):
    docs, contents, seals = load_documents(root)
    report, result, evidence = (docs[key] for key in ("RPT", "RES", "EVD"))
    references = {key: ref(docs[key]) for key in ("RPT", "RES", "EVD", "ENV", "PLAN")}
    selected, = [x["single"] for x in report["by_check"] if x["check_id"] == "LIF-04"]
    if (selected["result"] != references["RES"] or selected["evidence_ref"] != references["EVD"]
            or result["evidence"] != references["EVD"] or references["EVD"] not in result["refs"]
            or result["check_id"] != evidence["check_id"] or result["check_id"] != "LIF-04"
            or evidence["environment"] != references["ENV"] or evidence["plan"] != references["PLAN"]
            or not all(references[key] in evidence["refs"] for key in ("ENV", "PLAN"))
            or references["ENV"] not in docs["PLAN"]["refs"]
            or not all(references[key] in report["refs"] for key in ("RES", "EVD", "ENV", "PLAN"))
            or report["mode"] != "audit" or report["official"] is not False):
        raise ValueError("inconsistent product report scope or references")
    target_data = (root / "export" / "target.json").read_bytes()
    target = json.loads(target_data)
    engine = target["kubernetes"]["engine"]
    probe = target["kubernetes"]["probe"]
    def digest(image):
        return image.rsplit("@", 1)[-1]
    if (target["environment_ref"] != references["ENV"]
            or target["kubernetes"]["run_id"] != root.name
            or engine["pod_uid"] != run_metadata["pod_uid"]
            or probe["pod_uid"] != run_metadata["probe_pod_uid"]
            or target["observed_version"] != run_metadata["version"]
            or any(digest(value) != digest(run_metadata["image"]) for value in
                   (engine["image"], engine["image_id"], probe["image"], probe["image_id"],
                    run_metadata["image_id"], docs["ENV"]["engine"]["image_digest"]))):
        raise ValueError("product target differs from the measured Pod generation")
    named, = [item for item in evidence["artifacts"] if item["name"] == "probe_log.jsonl"]
    raw = contents["artifacts/" + named["sha256"]]
    if len(raw) != named["bytes"] or sha(raw) != named["sha256"]:
        raise ValueError("read-only product transport artifact digest mismatch")
    packet, = [d for d in (json.loads(line) for line in raw.splitlines()) if d.get("summary")]
    if tuple(t["case_id"] for t in packet["trials"]) != CASE_IDS:
        raise ValueError("product test request order differs")
    return packet, {
        **seals, "product_references": references, "target_file_sha256": sha(target_data),
        "product_artifact_sha256": named["sha256"], "mode": report["mode"], "official": report["official"],
        "security": result["verdicts"]["security"], "execution": evidence["execution"]["status"],
        "evidence_origin": evidence["data_origin"], "verification_level": result["verification_level"],
    }


def choose_row(records, field):
    return next((row for row in records if row.get("phase") == field), None)


def original_call_points(trials, samples):
    """Consume the stock request fingerprint in memory and return only call/tag pairs."""
    points = {}
    for trial in trials:
        fingerprint = trial.get("request_id_sha256")
        selected = [(r["connection_sha256"], r["call_sha256"]) for r in samples
                    if r["phase"] == "REQUEST_ENTER" and fingerprint is not None and r.get("request_sha256") == fingerprint
                    and r.get("call_sha256") and r.get("connection_sha256")]
        if len(selected) == 1:
            points[trial["case_id"]] = selected[0]
    counts = Counter(points.values())
    return {case: point for case, point in points.items() if counts[point] == 1}


def resource_assessment(records, deadline, app_debug, nonstream):
    if not records:
        return {}
    components = {}
    for key in RESOURCE_NAMES:
        values = [r["resources"][key] for r in records]
        types = sorted({value["type"] for value in values if value["type"] is not None})
        inactive = nonstream and not types and key in ("asgi_stream_writer", "asgi_body_iterator")
        component = ownership_timeline(records, lambda r: r["resources"][key]["count"], deadline, inactive=inactive)
        if not component["supported_samples"] and any(v["reference_state"] == "weakref_unavailable" for v in values):
            component["status"] = "reference_measurement_unsupported"
        components[key] = {**component, "types": types, "basis": "same_process_weak_reference",
                           "inactive_reason": "original_http_response_was_nonstream" if inactive else None}
    measured = {}
    for key in COUNT_FIELDS:
        measured[key] = ownership_timeline(records, lambda r: r["counts_and_states"][key], deadline)
    timer = measured["protocol_keepalive_handle_count"]
    if not components["keepalive_timer"]["types"] and timer["first_empty"] and not timer["last_held"]:
        components["keepalive_timer"]["status"] = "conditional_inactive"
        components["keepalive_timer"]["inactive_reason"] = "no_registered_handle_observed_in_source_profile"
    components["response_debug_buffer"] = {
        "status": "conditional_inactive" if app_debug and app_debug["response_debug_buffer_state"] == "inactive" else "unobserved",
        "configuration": app_debug}
    intervals = {operation: [] for operation in ("SEND", "DRAIN")}
    started = {}
    for row in records:
        operation, _, phase = row["phase"].partition("_")
        if operation not in intervals or not row["product_call_sha256"]:
            continue
        key = (operation, row["product_call_sha256"])
        if phase == "ENTER":
            started[key] = row["mono_ns"]
        elif phase in ("RETURN", "RAISE") and key in started:
            intervals[operation].append({"start_ns": started.pop(key), "end_ns": row["mono_ns"], "end_phase": phase})
    return {"scope": "component samples of the selected request and connection",
            "deadline": deadline, "components": components, "counts_and_queues": measured,
            "send_and_drain_observer_intervals": intervals,
            "unfinished_send_or_drain_calls": dict(Counter(op for op, _ in started))}


def build_report(root, config, run_metadata):
    packet, product = verify_product(root, run_metadata)
    engine_dir, probe_dir = root / "owned-engine", root / "owned-probe"

    def rows(directory, pattern):
        return [json.loads(line) for path in sorted(directory.glob(pattern)) for line in path.read_bytes().splitlines()]

    readiness = {role: json.loads((path / "poll-ready.json").read_bytes())
                 for role, path in (("engine", engine_dir), ("probe", probe_dir))}
    for role, ready in readiness.items():
        if (ready["pod_uid"] != config[f"{role}_pod_uid"] or ready["session"] != config["session"]
                or ready["poll_source_sha256"] != run_metadata["build"]["owned_stream_poll.py"]
                or ready["diag_source_sha256"] != run_metadata["build"]["owned_tcp_diag.py"]):
            raise ValueError("socket diagnostic process is outside the bound run")
    same_clock = readiness["engine"]["clock"] == readiness["probe"]["clock"]
    device = {role: rows(path, "tcp-journal.jsonl")
              for role, path in (("engine", engine_dir), ("probe", probe_dir))}
    gauges = rows(engine_dir, "resources-*.jsonl")
    registries = []
    for p in sorted(engine_dir.glob("owned-*.json")):
        row = json.loads(p.read_bytes())
        identity = validate_registration(row, config)
        if identity.netns != readiness["engine"]["netns"] or row["clock"] != readiness["engine"]["clock"]:
            raise ValueError("registration clock or namespace differs from the kernel observer")
        registries.append(project_registration(row, identity))
    product_samples = (packet.get("projection") or {}).get("samples", [])
    product_points = original_call_points(packet["trials"], product_samples)
    cases = []
    for trial in packet["trials"]:
        case_id = trial["case_id"]
        http_fields = ("case_id", "outcome", "started_at", "finished_at", "client_close_ms", "read_count",
                       "read_pause_count", "read_observations", "intentional_client_close", "event_stream")
        case = {"http": {key: trial.get(key) for key in http_fields}}
        found = [row for row in registries if row["case_id"] == case_id
                 and (row["connection_sha256"], row["native_request_call_sha256"]) == product_points.get(case_id)]
        if len(found) != 1:
            cases.append({**case, "status": "request_socket_binding_unresolved", "matching_registrations": len(found)})
            continue
        reg = found[0]
        tag = reg["connection_sha256"]
        track = sorted((row for row in gauges if row.get("connection_sha256") == tag and row["event"] == "resources"),
                       key=lambda row: row["mono_ns"])
        anchor = choose_row(track, "REQUEST_ENTER")
        if not anchor or (tag, anchor["product_call_sha256"]) != product_points.get(case_id):
            cases.append({**case, "status": "product_native_call_anchor_unresolved", "socket_registration": reg})
            continue
        probe_journal = [r for r in device["probe"] if r.get("connection_sha256") == tag]
        kernel_origin = client_origin([r for r in probe_journal if r["event"] == "sample"], matching_clock=same_clock)
        probe_receipt = read_probe_receipt(reg, config, readiness["probe"]["clock"], readiness["probe"]["netns"], probe_dir)
        observed_client = next((r["socket"] for r in probe_journal if r["event"] == "identity"), None)
        if probe_receipt is not None and (probe_receipt["case_id"] != trial["case_id"] or probe_receipt["socket"] != observed_client):
            probe_receipt = None
        direct_origin = receipt_origin(probe_receipt) if same_clock else None
        origin = direct_origin or kernel_origin
        published_origin = read_client_origin(tag, config, readiness["engine"]["clock"], probe_dir) if same_clock else None
        if published_origin is not None and published_origin != origin:
            raise ValueError("client observation times do not match the published boundary")
        server_close = choose_row(track, "CONNECTION_LOST_ENTER")
        supplemental_origin = ({"kind": "server_connection_lost_observer_timestamp", "lower_ns": server_close["mono_ns"],
                                "upper_ns": server_close["mono_ns"]} if server_close else None)
        endpoints = {}
        for role, records in device.items():
            relevant = [x for x in records if x.get("connection_sha256") == tag]
            samples = [x for x in relevant if x["event"] == "sample"]
            result = assess(samples, origin, config["budget_ms"])
            if origin is None:
                result["server_anchored_observation"] = assess(samples, supplemental_origin if same_clock or role=="engine" else None,
                                                              config["budget_ms"])
            result["counts_by_status"] = dict(Counter(x["status"] for x in samples))
            stop = next((x for x in relevant if x["event"] == "followup_stop"), {})
            endpoints[role] = {"identity": next((x["socket"] for x in relevant if x["event"] == "identity"), None),
                               "window": result, "post_window": socket_followup(samples, result.get("deadline"), stop),
                               "journal": samples}
        debug = next((row for row in gauges if row["event"]=="response_logging" and row.get("connection_sha256")==tag), None)
        cases.append({**case, "status": "independent_observation_collected", "registration": reg,
                      "native_product_request_call_sha256": anchor["product_call_sha256"],
                      "five_second_window_origin": origin, "client_kernel_close_origin": kernel_origin,
                      "client_actual_close_observation": direct_origin, "probe_process_observation": probe_receipt,
                      "product_probe_join_basis": "case_and_owned_pod_uids_clock_and_reverse_tcp_tuple_plus_native_product_call_and_tag",
                      "server_close_observation": supplemental_origin,
                      "endpoints": endpoints,
                      "resource_assessment": resource_assessment(track, endpoints["engine"]["window"].get("deadline"), debug,
                                                                 trial.get("outcome") == 200 and trial.get("event_stream") is False),
                      "resource_journal": track,
                      "resource_followup_stop": next((row for row in gauges if row["event"] == "app_followup_stop"
                                                       and row.get("connection_sha256") == tag), None)})
    result = {
        "schema": "byul-owned-stream-lifetime/1", "run": root.name,
        "built_utc": datetime.now(timezone.utc).isoformat(), "deployment": run_metadata,
        "product": product, "registered_socket_count": len(registries), "role_observers": readiness,
        "matching_role_clocks": same_clock, "cases": cases,
        "reclamation_assessment": full_assessment(cases),
        "instrumentation_metadata": [r for r in gauges if r["event"] in ("loader", "writer_source", "observer_error")],
        "scope": {"original_product_flows_untouched": True, "policy_budget_ms": config["budget_ms"],
                  "closed_socket_followup_cap_ms": config["followup_ms"],
                  "skmem_field_units": SKMEM_FIELDS, "separate_overlapping_meminfo_fields": True,
                  "kernel_memory_fields_summed": False,
                  "process_rss_used_as_reclamation": False,
                  "time_wait_has_live_buffer_values": False},
    }
    write_atomic(root / "export" / "owned-stream-lifetime.json", result)
    return result
