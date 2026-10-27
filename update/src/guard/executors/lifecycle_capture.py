"""Owned lifecycle recording capture; metadata agreement, never LIF-10 PASS."""
from __future__ import annotations

import hashlib
import json
import re

from guard.adapters import vllm_lifecycle_sensor as sensor
from .owned_capture import _read_owned_file, strict_json, validate_owned_source

CONTEXT_READER = r"""
import hashlib,json,os,pathlib,sys
pid=int(sys.argv[1]); p=pathlib.Path("/proc")/str(pid)
def read_context():
    raw=(p/"stat").read_text(); fields=raw[raw.rfind(")")+2:].split()
    status=(p/"status").read_text().splitlines()
    uid=int(next(x for x in status if x.startswith("Uid:")).split()[2])
    return {"pid":pid,"start_ticks":int(fields[19]),"uid":uid,
      "boot_sha256":hashlib.sha256(pathlib.Path("/proc/sys/kernel/random/boot_id").read_bytes()).hexdigest(),
      "pid_namespace":os.stat(p/"ns/pid").st_ino,"time_namespace":os.stat(p/"ns/time").st_ino,
      "time_offsets_sha256":hashlib.sha256((p/"timens_offsets").read_bytes()).hexdigest()}
before=read_context()
same=os.readlink(p/"exe")==os.readlink("/proc/self/exe")
after=read_context()
print(json.dumps({"schema":"guard-lifecycle-process-context/1","context":before,
  "stable":before==after,"same_executable":same}))
"""

KEYS = set("""
seq event monotonic_ns previous_sha256 payload schema profile commit session
settings_sha256 sensor_sha256 context producer_authenticated pid start_ticks uid
boot_sha256 pid_namespace time_namespace time_offsets_sha256 reason point phase
source_sha256 requests metadata selection_observed diagnostic request_sha256
external_request_sha256 request_present output_state_present batch_row_present status_code
selected_scheduled_sha256 selected_finished_sha256 selection_complete scheduler_sequence
scheduled_sequence processed_sequence deferred_fences deferred_complete ownership
complete groups pool_sha256 block_sha256 blocks ref_count is_null observed_request_sha256 pool call_chain module verified
code_sha256 more_body inherited observation_started_ns observation_finished_ns
""".split())
POINTS = {c+"."+m for classes in sensor.POINTS.values() for c, methods in classes.items() for m in methods}
WORDS = {sensor.SCHEMA, sensor.PROFILE, sensor.COMMIT, "START", "MODULE", "NATIVE", "ASGI", "END",
    "SEALED", "PROCESS_EXIT", "BOUND_REACHED", "CONTROL_UNAVAILABLE", "CALL", "RETURN", "RAISE",
    "SAMPLE_UNAVAILABLE", "DISCONNECT_RECEIVED", "BODY_SEND_RETURN", "APP_RETURN", "APP_RAISE",
    *sensor.SOURCE_SHA, *POINTS}


def _safe(value, depth=0):
    if depth > 12:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return 0 <= value < 2**63
    if type(value) is str:
        return value in WORDS or re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", value) is not None
    if type(value) is list:
        return len(value) <= 64 and all(_safe(v, depth+1) for v in value)
    if type(value) is dict:
        return set(value) <= KEYS and all(_safe(v, depth+1) for v in value.values())
    return False


def canonical_row(row):
    return (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)+"\n").encode()


def parse_recording(raw, prepared):
    if type(raw) is not bytes or not 0 < len(raw) <= sensor.MAX_BYTES:
        raise ValueError("LIFECYCLE_RECORDING_BYTES_INVALID")
    lines = raw.splitlines(keepends=True)
    incomplete_tail = not raw.endswith(b"\n")
    tail = lines.pop() if incomplete_tail else b""
    if not 1 <= len(lines) <= sensor.MAX_ROWS:
        raise ValueError("LIFECYCLE_RECORDING_ROW_BOUND")
    previous, clock, rows = "0"*64, -1, []
    for seq, line in enumerate(lines):
        row = strict_json(line, limit=16384)
        if (type(row) is not dict or set(row) != {"seq","event","monotonic_ns","previous_sha256","payload"}
                or not _safe(row) or type(row["seq"]) is not int or row["seq"] != seq
                or type(row["monotonic_ns"]) is not int or row["monotonic_ns"] < clock
                or row["previous_sha256"] != previous or canonical_row(row) != line
                or row["event"] not in {"START","MODULE","NATIVE","ASGI","END"}
                or type(row["payload"]) is not dict):
            raise ValueError("LIFECYCLE_RECORDING_ROW_INVALID")
        previous, clock = hashlib.sha256(line).hexdigest(), row["monotonic_ns"]
        rows.append(row)
    first = rows[0]
    header = first["payload"]
    if (first["event"] != "START" or set(header) != {"schema","profile","commit","session","settings_sha256",
            "sensor_sha256","context","producer_authenticated"}
            or header["schema"] != sensor.SCHEMA or header["profile"] != sensor.PROFILE
            or header["commit"] != sensor.COMMIT or header["session"] != prepared["session"]
            or header["settings_sha256"] != prepared["assets"]["settings.json"]["sha256"]
            or header["sensor_sha256"] != prepared["assets"]["guard_lifecycle_sensor.py"]["sha256"]
            or header["producer_authenticated"] is not False):
        raise ValueError("LIFECYCLE_RECORDING_PREPARED_BINDING_INVALID")
    context = header["context"]
    if (type(context) is not dict or set(context) != {"pid","start_ticks","uid","boot_sha256",
            "pid_namespace","time_namespace","time_offsets_sha256"}
            or any(type(context[k]) is not int or context[k] < (0 if k=="uid" else 1)
                for k in ("pid","start_ticks","uid","pid_namespace","time_namespace"))
            or any(type(context[k]) is not str or re.fullmatch(r"[0-9a-f]{64}", context[k]) is None
                for k in ("boot_sha256","time_offsets_sha256"))):
        raise ValueError("LIFECYCLE_RECORDING_CONTEXT_INVALID")
    ready = set()
    for row in rows[1:]:
        payload = row["payload"]
        if row["event"] == "START":
            raise ValueError("LIFECYCLE_RECORDING_RESTARTED")
        if row["event"] == "MODULE":
            if (set(payload) not in ({"module","verified"}, {"module","verified","source_sha256","code_sha256"},
                    {"module","verified","source_sha256","code_sha256","inherited"})
                    or payload.get("module") not in sensor.SOURCE_SHA or type(payload.get("verified")) is not bool):
                raise ValueError("LIFECYCLE_RECORDING_MODULE_INVALID")
            if payload["verified"]:
                if (payload.get("source_sha256") != sensor.SOURCE_SHA[payload["module"]]
                        or re.fullmatch(r"[0-9a-f]{64}", payload.get("code_sha256","")) is None):
                    raise ValueError("LIFECYCLE_RECORDING_MODULE_HASH_INVALID")
                if "inherited" in payload and payload["inherited"] is not True:
                    raise ValueError("LIFECYCLE_RECORDING_INHERITANCE_INVALID")
                ready.add(payload["module"])
        if row["event"] == "NATIVE":
            required = {"point","phase","source_sha256","requests","metadata","selection_observed",
                        "observation_started_ns","observation_finished_ns"}
            if (not required <= set(payload) or not (set(payload)-required) <= {"call_chain","diagnostic"}
                    or type(payload.get("requests")) is not list or len(payload["requests"]) > sensor.MAX_IDS
                    or type(payload.get("metadata")) is not dict or type(payload.get("selection_observed")) is not bool
                    or type(payload.get("observation_started_ns")) is not int
                    or type(payload.get("observation_finished_ns")) is not int
                    or not 0 <= payload["observation_started_ns"] <= payload["observation_finished_ns"] <= row["monotonic_ns"]
                    or payload.get("point") not in POINTS or payload.get("phase") not in {"CALL","RETURN","RAISE"}):
                raise ValueError("LIFECYCLE_RECORDING_POINT_INVALID")
            for request in payload["requests"]:
                if (type(request) is not dict or set(request) != {"request_sha256","request_present",
                        "output_state_present","batch_row_present","status_code"}
                        or type(request["request_sha256"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}",request["request_sha256"]) is None
                        or any(request[k] is not None and type(request[k]) is not bool
                            for k in ("request_present","output_state_present","batch_row_present"))
                        or request["status_code"] is not None and
                            (type(request["status_code"]) is not int or not 0 <= request["status_code"] <= 32)):
                    raise ValueError("LIFECYCLE_RECORDING_REQUEST_METADATA_INVALID")
            chain = payload.get("call_chain",[])
            if type(chain) is not list or len(chain) > 4:
                raise ValueError("LIFECYCLE_RECORDING_CHAIN_INVALID")
            for frame in chain:
                if (type(frame) is not dict or set(frame) != {"point","request_sha256"}
                        or frame["point"] not in POINTS or type(frame["request_sha256"]) is not list
                        or len(frame["request_sha256"]) > sensor.MAX_IDS
                        or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}",v) is None for v in frame["request_sha256"])):
                    raise ValueError("LIFECYCLE_RECORDING_CHAIN_INVALID")
            expected = [m for m, classes in sensor.POINTS.items() if payload["point"].split(".")[0] in classes]
            if len(expected) != 1 or payload.get("source_sha256") != sensor.SOURCE_SHA[expected[0]]:
                raise ValueError("LIFECYCLE_RECORDING_POINT_SOURCE_INVALID")
        if row["event"] == "ASGI":
            if (set(payload) != {"request_sha256","phase","more_body"}
                    or payload["request_sha256"] is not None and (type(payload["request_sha256"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}",payload["request_sha256"]) is None)
                    or payload["phase"] not in {"DISCONNECT_RECEIVED","BODY_SEND_RETURN","APP_RETURN","APP_RAISE"}
                    or payload["more_body"] is not None and type(payload["more_body"]) is not bool):
                raise ValueError("LIFECYCLE_RECORDING_ASGI_INVALID")
        if row["event"] == "END" and row is not rows[-1]:
            raise ValueError("LIFECYCLE_RECORDING_AFTER_END")
    end = rows[-1]["payload"] if rows[-1]["event"] == "END" else {}
    if rows[-1]["event"] == "END" and (set(end) != {"reason"} or end["reason"] not in {"SEALED","PROCESS_EXIT","BOUND_REACHED","CONTROL_UNAVAILABLE"}):
        raise ValueError("LIFECYCLE_RECORDING_END_INVALID")
    return rows, {"recording_closed": bool(end) and not incomplete_tail, "recording_end": end.get("reason"),
        "incomplete_final_row":incomplete_tail,"discarded_tail_bytes":len(tail),
        "discarded_tail_sha256":hashlib.sha256(tail).hexdigest() if tail else None,
        "source_profile_modules": sorted(ready), "whole_run_coverage_verified": False}


def process_context(docker, container, pid):
    result = docker.exec_bounded(container, ["python3","-c",CONTEXT_READER,str(pid)], timeout=5, max_bytes=4096)
    pipe = result.capture or {}
    if (result.returncode != 0 or pipe.get("complete") is not True
            or pipe.get("bounded_at_source") is not True or pipe.get("timed_out") is not False
            or pipe.get("truncated") is not False):
        raise ValueError("LIFECYCLE_PROCESS_TRANSPORT_INVALID")
    if pipe.get("owner_binding_verified",True) is not True:
        raise ValueError("LIFECYCLE_PROCESS_SOURCE_OWNER_UNVERIFIED")
    value = strict_json(result.stdout.encode(), limit=4096)
    if (type(value) is not dict or set(value) != {"schema","context","stable","same_executable"}
            or value["schema"] != "guard-lifecycle-process-context/1"
            or value["stable"] is not True or value["same_executable"] is not True):
        raise ValueError("LIFECYCLE_PROCESS_CONTEXT_UNVERIFIED")
    return value["context"]


def capture(docker, target, *, source, prepared, read_file=None, image_check=None):
    validate_owned_source(**source)
    read = _read_owned_file if read_file is None else read_file
    raw, provenance = read(docker, target, **source, max_bytes=sensor.MAX_BYTES)
    records, summary = parse_recording(raw, prepared)
    context = records[0]["payload"]["context"]
    expected_path = f"/guard-lifecycle/data/{prepared['session']}/pid-{context['pid']}-{context['start_ticks']}.jsonl"
    if source["path"] != expected_path:
        raise ValueError("LIFECYCLE_RECORDING_PROCESS_PATH_INVALID")
    verified, image_verified, diagnostic = False, False, []
    # Keep the first safe recording if a later recheck fails.
    try:
        if image_check is not None:
            image_verified = image_check(provenance,source) is True
        else:
            image = docker.image_info(provenance["owner"]["image_id"],timeout=5)
            digests = image.get("digests") if type(image) is dict else None
            image_verified = (type(digests) is list and 1 <= len(digests) <= 128
                and all(type(v) is str and re.fullmatch(r".+@sha256:[0-9a-f]{64}",v) is not None for v in digests)
                and source["image"] in digests)
    except Exception:
        diagnostic.append("LIFECYCLE_IMAGE_RECHECK_UNAVAILABLE")
    try:
        before = process_context(docker, source["container"], context["pid"])
        later, second = read(docker, target, **source, max_bytes=sensor.MAX_BYTES)
        after = process_context(docker, source["container"], context["pid"])
        verified = (before == context == after and raw == later
                    and provenance["owner"] == second["owner"]
                    and provenance["file_identity"] == second["file_identity"]
                    and provenance.get("owner_binding_verified",True) is True
                    and second.get("owner_binding_verified",True) is True)
    except Exception:
        diagnostic.append("LIFECYCLE_RECHECK_UNAVAILABLE")
    if not image_verified:
        diagnostic.append("LIFECYCLE_IMAGE_BINDING_UNVERIFIED")
    if not verified:
        diagnostic.append("LIFECYCLE_PROCESS_BINDING_UNVERIFIED")
    if provenance.get("owner_binding_verified",True) is not True:
        diagnostic.append("LIFECYCLE_INITIAL_OWNER_RECHECK_UNAVAILABLE")
    owner = dict(provenance["owner"])
    started = owner.pop("started_at")
    owner["started_at_sha256"] = hashlib.sha256(started.encode("utf-8","surrogatepass")).hexdigest()
    safe_provenance = {**provenance,"owner":owner}
    return {"schema":"guard-lifecycle-capture/1", "records":records, "summary":summary,
        "provenance":safe_provenance, "process_context_verified":verified, "image_digest_verified":image_verified,
        "diagnostic_codes":diagnostic, "product_connected":False, "security":"UNKNOWN",
        "producer_authenticated":False, "record_authenticity_verified":False,
        "scope":"one supplied process recording; not whole-run coverage"}
