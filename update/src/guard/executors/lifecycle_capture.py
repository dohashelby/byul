"""Owned lifecycle recording capture; metadata agreement, never LIF-10 PASS."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy

from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.adapters import vllm_safe_open_mapping_sensor as mapping_sensor
from guard.adapters import vllm_safe_open_mapping_metadata as mapping_metadata
from guard.adapters import vllm_worker_initialization_sensor as joint_sensor
from guard.adapters import vllm_worker_startup_sensor as startup_sensor,vllm_worker_startup_metadata as startup_metadata,vllm_worker_startup_contract as startup_contract
from guard.adapters import vllm_worker_readiness as startup_callback
from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.adapters import vllm_online_sensor as online
from guard.adapters import vllm_online_metadata as online_metadata
from guard.adapters import vllm_attempt_sensor as attempt, vllm_attempt_metadata as attempt_metadata
from guard.adapters import vllm_request_output_sensor as output_sensor, vllm_request_output_metadata as output_metadata
from guard.adapters import vllm_stream_buffer_sensor as stream_sensor, vllm_stream_buffer_metadata as stream_metadata
from guard.adapters import vllm_rank_failure_sensor as rank_sensor
from guard.adapters import vllm_initialization_sensor as init_sensor
from guard.adapters import vllm_profiler_sensor as profiler_sensor,vllm_profiler_metadata as profiler_metadata
from guard.adapters import vllm_file_read_sensor as read_sensor,vllm_file_read_metadata as read_metadata
from guard.adapters import vllm_weight_iteration_sensor as weight_sensor,vllm_weight_iteration_metadata as weight_metadata
from guard.adapters import vllm_initialization_metadata as init_metadata, vllm_rank_failure_metadata as rank_metadata
from .owned_capture import _read_owned_file, strict_json, validate_owned_source, EXTENDED_RECORDING_PROFILE

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
external_request_sha256 asgi_request_sha256 request_present output_state_present batch_row_present status_code
selected_scheduled_sha256 selected_finished_sha256 selection_complete scheduler_sequence
scheduled_sequence processed_sequence deferred_fences deferred_complete ownership
complete groups pool_sha256 block_sha256 blocks ref_count is_null observed_request_sha256 pool call_chain module verified
code_sha256 more_body inherited observation_started_ns observation_finished_ns
model_selection owner_model_sha256 argument_model_sha256 returned_model_sha256
configured_model_sha256 configured_revision_sha256 load_format_sha256 load_dummy_weights
lora_config_present speculative_config_present
""".split())
KEYS |= profiles.WEIGHT_KEYS
KEYS |= profiles.ROUTE_KEYS
KEYS |= profiles.REQUEST_SELECTION_KEYS
KEYS |= profiles.HANDLER_KEYS
POINTS = set(profiles.point_sources(sensor.PROFILE))
WORDS = {*profiles.SCHEMAS, *profiles.SCHEMAS.values(), sensor.COMMIT, "START", "MODULE", "NATIVE", "ASGI", "END",
    "SELECTION",
    "SEALED", "PROCESS_EXIT", "BOUND_REACHED", "CONTROL_UNAVAILABLE", "CALL", "RETURN", "RAISE",
    "SAMPLE_UNAVAILABLE", "DISCONNECT_RECEIVED", "BODY_SEND_RETURN", "APP_RETURN", "APP_RAISE",
    *sensor.SOURCE_SHA, *POINTS}


def _safe(value, depth=0, *, keys=KEYS, words=WORDS):
    if depth > 12:
        return False
    if value is None or type(value) is bool:
        return True
    if type(value) is int:
        return 0 <= value < 2**63
    if type(value) is str:
        return value in words or re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{64})", value) is not None
    if type(value) is list:
        return len(value) <= 64 and all(_safe(v, depth+1, keys=keys, words=words) for v in value)
    if type(value) is dict:
        return set(value) <= keys and all(profiles.valid_selected_integer(k,v) or _safe(v, depth+1, keys=keys, words=words)
                                         for k,v in value.items())
    return False


def canonical_row(row):
    return (json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=True)+"\n").encode()


def parse_recording(raw, prepared):
    is_online = prepared["profile"] == online.PROFILE
    is_output = prepared["profile"] == output_sensor.PROFILE
    is_stream = prepared["profile"] == stream_sensor.PROFILE
    is_rank = prepared["profile"] == rank_sensor.PROFILE
    is_read = prepared["profile"] == read_sensor.PROFILE
    is_worker_startup = prepared["profile"] in {startup_sensor.PROFILE,joint_sensor.PROFILE}
    is_mapping = prepared["profile"] in {mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE}
    is_weight = prepared["profile"] in {weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE}
    is_profiler = prepared["profile"] == profiler_sensor.PROFILE
    is_init = prepared["profile"] in {init_sensor.PROFILE,joint_sensor.PROFILE}
    is_attempt = prepared["profile"] in {attempt.PROFILE,output_sensor.PROFILE}
    if type(raw) is not bytes or not 0 < len(raw) <= (weight_sensor.MAX_BYTES if is_weight else profiler_sensor.MAX_BYTES if is_profiler else init_sensor.MAX_BYTES if is_init else rank_sensor.MAX_BYTES if is_rank else stream_sensor.MAX_BYTES if is_stream else attempt.MAX_BYTES if is_attempt else online.MAX_BYTES if is_online else sensor.MAX_BYTES):
        raise ValueError("LIFECYCLE_RECORDING_BYTES_INVALID")
    lines = raw.splitlines(keepends=True)
    incomplete_tail = not raw.endswith(b"\n")
    tail = lines.pop() if incomplete_tail else b""
    if not 1 <= len(lines) <= (weight_sensor.MAX_ROWS if is_weight else profiler_sensor.MAX_ROWS if is_profiler else init_sensor.MAX_ROWS if is_init else rank_sensor.MAX_ROWS if is_rank else stream_sensor.MAX_ROWS if is_stream else attempt.MAX_ROWS if is_attempt else online.MAX_ROWS if is_online else sensor.MAX_ROWS):
        raise ValueError("LIFECYCLE_RECORDING_ROW_BOUND")
    previous, clock, rows = "0"*64, -1, []
    keys = KEYS - {"asgi_request_sha256"} if prepared["profile"] == profiles.LEGACY_PROFILE else KEYS
    if prepared["profile"] in {profiles.LEGACY_PROFILE,profiles.V2_PROFILE}:
        keys = keys - {"model_selection","owner_model_sha256","argument_model_sha256","returned_model_sha256",
            "configured_model_sha256","configured_revision_sha256","load_format_sha256","load_dummy_weights",
            "lora_config_present","speculative_config_present"}
    if prepared["profile"] not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,profiles.V4_PROFILE,profiles.V5_PROFILE,profiles.V6_PROFILE,profiles.V7_PROFILE}:keys=keys-profiles.WEIGHT_KEYS
    if prepared["profile"] not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,profiles.V5_PROFILE,profiles.V6_PROFILE,profiles.V7_PROFILE}:keys=keys-(profiles.ROUTE_KEYS-{"asgi_request_sha256"})
    if prepared["profile"] not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,profiles.V6_PROFILE,profiles.V7_PROFILE}:keys=keys-profiles.REQUEST_SELECTION_KEYS
    if prepared["profile"] not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,profiles.V7_PROFILE}:keys=keys-(profiles.HANDLER_KEYS-profiles.ROUTE_KEYS)
    words = WORDS
    if is_online:
        keys = keys | {"online_input", "online_code_sha256", "online_runtime"}
        words = words | {online.PROFILE, online.SCHEMA, online.CALLBACK_MODULE, *online.SOURCE_ADDITIONS,
            *online.POINT_CONTRACTS, *online.RUNTIME_CONTRACTS}
    if is_attempt:
        keys=keys|attempt_metadata.FIELDS
        words=words|{"ATTEMPT",*attempt_metadata.PHASES,*attempt.RUNTIME_CODE_CONTRACTS}
    if is_output:
        keys=keys|output_metadata.LINK_FIELDS|output_metadata.TEXT_FIELDS|output_metadata.ITEM_FIELDS
        words=words|{"OUTPUT",output_sensor.OUTPUT_SCHEMA,*output_sensor.POINTS,*output_sensor.RUNTIME_CODE_CONTRACTS}
    if is_stream:
        keys=keys|stream_metadata.FIELDS
        words=words|{'STREAM_BUFFER',stream_sensor.STREAM_SCHEMA,*stream_sensor.PHASES,*stream_sensor.SOURCE_ADDITIONS,
            *stream_sensor.RUNTIME_CODE_CONTRACTS,'unsupported','asyncio-selector-tcp'}
    if is_rank:
        keys=keys|rank_metadata.FIELDS
        words=words|{"RANK_FAILURE",rank_sensor.EVENT_SCHEMA,*rank_sensor.POINTS,*rank_sensor.SOURCES,*rank_sensor.RUNTIME_CODE_CONTRACTS,
            "executor","future","worker","core","execute_model","PENDING","RUNNING","FINISHED","CANCELLED","CANCELLED_AND_NOTIFIED",
            "none","value","timeout","exception","base-exception","negative","zero","positive"}
    if is_profiler:
        keys=keys|profiler_metadata.FIELDS|profiler_metadata.STATE_FIELDS
        words=words|{'PROFILER',profiler_sensor.EVENT_SCHEMA,*profiler_sensor.POINTS,*profiler_sensor.SOURCE_ADDITIONS,
            *profiler_sensor.RUNTIME_CODE_CONTRACTS,'worker','torch-wrapper','torch','cuda','proton','other','exception','base-exception'}
    if is_init:
        keys=keys|init_metadata.FIELDS|{'rank'}
        words=words|{'INITIALIZATION',init_sensor.EVENT_SCHEMA,*init_sensor.POINTS,*init_sensor.SOURCE_ADDITIONS,
            *init_sensor.RUNTIME_CODE_CONTRACTS,'READY','other','timeout','exception','base-exception'}
    if is_weight:
        keys=keys|weight_metadata.FIELDS
        words=words|{'WEIGHT_ITERATION',weight_sensor.EVENT_SCHEMA,*weight_sensor.PHASES,*weight_sensor.POINTS,*weight_sensor.SOURCE_ADDITIONS,*weight_sensor.RUNTIME_CODE_CONTRACTS,'NEXT','SEND','THROW','CLOSE','exception','base-exception'}
    if is_read:
        keys=keys|read_metadata.FIELDS|read_metadata.SNAPSHOT|{'offset'}
        words=words|{'FILE_READ',read_sensor.EVENT_SCHEMA,*read_sensor.PHASES,*read_sensor.RUNTIME_CODE_CONTRACTS,'disabled','unavailable','complete','too-large','change','creation'}
    if is_mapping:
        keys=keys|mapping_metadata.FIELDS|mapping_metadata.MAP_FIELDS|mapping_metadata.SNAPSHOT_FIELDS
        words=words|{'SAFE_OPEN_MAPPING',mapping_sensor.EVENT_SCHEMA,*mapping_sensor.PHASES,*mapping_sensor.RUNTIME_CODE_CONTRACTS,*mapping_metadata.PERMISSIONS,'observed','bounded','unavailable'}
    if is_worker_startup:
        keys=keys|startup_metadata.FIELDS|startup_metadata.LINK_FIELDS|startup_metadata.CLOSE_FIELDS|startup_metadata.LIMIT_FIELDS|startup_metadata.CLOSE_RESULT_FIELDS|startup_contract.SNAPSHOT_FIELDS|startup_contract.BINDING_FIELDS|startup_contract.METADATA_FIELDS|startup_contract.TRANSITION_FIELDS|set(startup_contract.SOURCE_SHA256)
        words=words|{'WORKER_STARTUP','WORKER_STARTUP_LINK','WORKER_STARTUP_CLOSE','WORKER_STARTUP_LIMIT',startup_sensor.EVENT_SCHEMA,startup_sensor.LINK_SCHEMA,startup_sensor.CLOSE_SCHEMA,startup_sensor.LIMIT_SCHEMA,
            *startup_contract.SPAN_NAMES,*startup_contract.SOURCE_SHA256,*startup_metadata.ENTRY_KINDS,*startup_metadata.PHASES,*startup_sensor.RUNTIME_CODE_CONTRACTS,
            startup_callback.SCHEMA,*startup_callback.POINTS,*startup_callback.PHASES,*startup_callback.STATUSES,*startup_callback.GRAPH_MODES,*startup_callback.GAPS,
            'CALLBACK_UNAVAILABLE','CALLBACK_CLOSE_UNAVAILABLE','unsupported','calls'}
    source_hashes = profiles.source_hashes(prepared["profile"])
    point_sources=profiles.point_sources(prepared["profile"])
    points=set(point_sources)
    for seq, line in enumerate(lines):
        row = strict_json(line, limit=16384)
        safe_row = row
        if is_online and type(row) is dict and type(row.get("payload")) is dict:
            payload = row["payload"]
            metadata = payload.get("metadata")
            if type(metadata) is dict and "online_input" in metadata:
                if row.get("event") != "NATIVE" or set(metadata) != {"online_input"}:
                    raise ValueError("ONLINE_NATIVE_METADATA_INVALID")
                online_metadata.validate_snapshot(metadata["online_input"], payload.get("point"), payload.get("phase"))
                safe_row = deepcopy(row)
                safe_row["payload"]["metadata"]["online_input"] = None
        if (type(row) is not dict or set(row) != {"seq","event","monotonic_ns","previous_sha256","payload"}
                or not _safe(safe_row, keys=keys, words=words) or type(row["seq"]) is not int or row["seq"] != seq
                or type(row["monotonic_ns"]) is not int or row["monotonic_ns"] < clock
                or row["previous_sha256"] != previous or canonical_row(row) != line
                or row["event"] not in ({"START","MODULE","NATIVE","ASGI","END","SELECTION","WEIGHT_ITERATION","SAFE_OPEN_MAPPING","WORKER_STARTUP","WORKER_STARTUP_LINK","WORKER_STARTUP_CLOSE","WORKER_STARTUP_LIMIT"} | ({"INITIALIZATION"} if prepared["profile"]==joint_sensor.PROFILE else set()) if is_worker_startup else {"START","MODULE","NATIVE","ASGI","END","SELECTION","WEIGHT_ITERATION","SAFE_OPEN_MAPPING"} if is_mapping else {"START","MODULE","NATIVE","ASGI","END","SELECTION","WEIGHT_ITERATION","FILE_READ"} if is_read else {"START","MODULE","NATIVE","ASGI","END","SELECTION","WEIGHT_ITERATION"} if is_weight else {"START","MODULE","NATIVE","ASGI","END","SELECTION","PROFILER"} if is_profiler else {"START","MODULE","NATIVE","ASGI","END","SELECTION","INITIALIZATION"} if is_init else {"START","MODULE","NATIVE","ASGI","END","SELECTION","RANK_FAILURE"} if is_rank else {"START","MODULE","NATIVE","ASGI","END","SELECTION","STREAM_BUFFER"} if is_stream else {"START","MODULE","NATIVE","ASGI","END","SELECTION","ATTEMPT","OUTPUT"} if is_output else {"START","MODULE","NATIVE","ASGI","END","SELECTION","ATTEMPT"} if is_attempt else {"START","MODULE","NATIVE","ASGI","END","SELECTION"})
                or type(row["payload"]) is not dict):
            raise ValueError("LIFECYCLE_RECORDING_ROW_INVALID")
        previous, clock = hashlib.sha256(line).hexdigest(), row["monotonic_ns"]
        rows.append(row)
    first = rows[0]
    header = first["payload"]
    if (first["event"] != "START" or set(header) != {"schema","profile","commit","session","settings_sha256",
            "sensor_sha256","context","producer_authenticated"}
            or header["schema"] != profiles.SCHEMAS.get(prepared["profile"])
            or header["profile"] != prepared["profile"]
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
    online_calls = {}
    online_orphans = 0
    online_runtime = None
    attempt_runtime=None;attempt_calls={};attempt_events=0
    output_runtime=None;output_calls={};output_events=0;stream_runtime=None;stream_events=0;rank_runtime=None;rank_events=0;init_runtime=None;init_events=0
    profiler_runtime=None;profiler_events=0;profiler_calls={};weight_events=[]
    for row in rows[1:]:
        payload = row["payload"]
        if row["event"] == "START":
            raise ValueError("LIFECYCLE_RECORDING_RESTARTED")
        if row["event"] == "MODULE":
            callback_module = is_online and payload.get("module") == online.CALLBACK_MODULE
            module_hashes = {**source_hashes, online.CALLBACK_MODULE: online.CALLBACK_SHA256} if callback_module else source_hashes
            module_payload = dict(payload)
            selected_codes = None
            if is_online and (callback_module or payload.get("module") in online.MODULE_CONTRACTS) and payload.get("verified") is True:
                selected_codes = online.runtime_contract(module_payload.get("online_runtime", ""))
                observed_runtime = module_payload.pop("online_runtime")
                if online_runtime is not None and observed_runtime != online_runtime:
                    raise ValueError("ONLINE_CODE_RUNTIME_MISMATCH")
                online_runtime = observed_runtime
            if is_online and "online_code_sha256" in module_payload:
                contract = online.MODULE_CONTRACTS.get(module_payload.get("module"), {})
                if (selected_codes is None or not contract
                        or module_payload.pop("online_code_sha256") != selected_codes["module_codes"].get(module_payload.get("module"))):
                    raise ValueError("ONLINE_MODULE_CODE_INVALID")
            if (set(module_payload) not in ({"module","verified"}, {"module","verified","source_sha256","code_sha256"},
                    {"module","verified","source_sha256","code_sha256","inherited"})
                    or payload.get("module") not in module_hashes or type(payload.get("verified")) is not bool):
                raise ValueError("LIFECYCLE_RECORDING_MODULE_INVALID")
            if payload["verified"]:
                if (payload.get("source_sha256") != module_hashes[payload["module"]]
                        or re.fullmatch(r"[0-9a-f]{64}", payload.get("code_sha256","")) is None):
                    raise ValueError("LIFECYCLE_RECORDING_MODULE_HASH_INVALID")
                if callback_module and payload["code_sha256"] != selected_codes["callback_code_sha256"]:
                    raise ValueError("ONLINE_CALLBACK_CODE_INVALID")
                if is_online and payload["module"] in online.MODULE_CONTRACTS and "online_code_sha256" not in payload:
                    raise ValueError("ONLINE_MODULE_CODE_INVALID")
                if "inherited" in payload and payload["inherited"] is not True:
                    raise ValueError("LIFECYCLE_RECORDING_INHERITANCE_INVALID")
                ready.add(payload["module"])
            elif is_online or is_output or is_stream or is_rank or is_init or is_weight:
                ready.discard(payload["module"])
        if row['event']=='INITIALIZATION':
            init_metadata.validate_sample(payload)
            if not is_init or payload['source_verified'] and payload['source_module'] not in ready:
                raise ValueError('INITIALIZATION_SOURCE_PROVENANCE_UNAVAILABLE')
            if payload['operation_finished_ns'] is not None and payload['operation_finished_ns']>row['monotonic_ns']:
                raise ValueError('INITIALIZATION_CLOCK_INVALID')
            if init_runtime is not None and init_runtime!=payload['observer_runtime']:
                raise ValueError('INITIALIZATION_RUNTIME_MISMATCH')
            init_runtime=payload['observer_runtime'];init_events+=1
        if row['event']=='RANK_FAILURE':
            rank_metadata.validate_sample(payload)
            if not is_rank or payload['source_verified'] and payload['source_module'] not in ready:
                raise ValueError('RANK_FAILURE_SOURCE_PROVENANCE_UNAVAILABLE')
            if payload['operation_finished_ns'] is not None and payload['operation_finished_ns']>row['monotonic_ns']:
                raise ValueError('RANK_FAILURE_CLOCK_INVALID')
            if rank_runtime is not None and rank_runtime!=payload['observer_runtime']:
                raise ValueError('RANK_FAILURE_RUNTIME_MISMATCH')
            rank_runtime=payload['observer_runtime'];rank_events+=1
        if row['event']=='STREAM_BUFFER':
            stream_metadata.validate_sample(payload)
            if not is_stream or payload['source_verified'] and payload['source_module'] not in ready:
                raise ValueError('STREAM_BUFFER_SOURCE_PROVENANCE_UNAVAILABLE')
            if payload['operation_finished_ns'] is not None and payload['operation_finished_ns']>row['monotonic_ns']:
                raise ValueError('STREAM_BUFFER_CLOCK_INVALID')
            if stream_runtime is not None and stream_runtime!=payload['observer_runtime']:
                raise ValueError('STREAM_BUFFER_RUNTIME_MISMATCH')
            stream_runtime=payload['observer_runtime'];stream_events+=1
        if row["event"] in {"NATIVE","SELECTION"}:
            if row["event"]=="SELECTION" and (header["profile"] not in {sensor.PROFILE,online.PROFILE,attempt.PROFILE,output_sensor.PROFILE,stream_sensor.PROFILE,rank_sensor.PROFILE,init_sensor.PROFILE,profiler_sensor.PROFILE,weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE,profiles.V6_PROFILE,profiles.V7_PROFILE}
                    or payload.get("point")!="InputProcessor.assign_request_id" or payload.get("requests")!=[]
                    or payload.get("selection_observed") is not False
                    or type(payload.get("metadata")) is not dict
                    or payload.get("metadata")=={} and payload.get("diagnostic")!="SAMPLE_UNAVAILABLE"
                    or set(payload["metadata"]) not in (set(),{"request_selection"})):
                raise ValueError("LIFECYCLE_SELECTION_EVENT_INVALID")
            required = {"point","phase","source_sha256","requests","metadata","selection_observed",
                        "observation_started_ns","observation_finished_ns"}
            if (not required <= set(payload) or not (set(payload)-required) <= {"call_chain","diagnostic"}
                    or type(payload.get("requests")) is not list or len(payload["requests"]) > sensor.MAX_IDS
                    or type(payload.get("metadata")) is not dict or type(payload.get("selection_observed")) is not bool
                    or type(payload.get("observation_started_ns")) is not int
                    or type(payload.get("observation_finished_ns")) is not int
                    or not 0 <= payload["observation_started_ns"] <= payload["observation_finished_ns"] <= row["monotonic_ns"]
                    or payload.get("point") not in points or payload.get("phase") not in {"CALL","RETURN","RAISE"}):
                raise ValueError("LIFECYCLE_RECORDING_POINT_INVALID")
            asgi = payload["metadata"].get("asgi_request_sha256")
            if ("asgi_request_sha256" in payload["metadata"] and
                    (header["profile"] == profiles.LEGACY_PROFILE
                     or payload["point"] != "InputProcessor.assign_request_id"
                     or type(asgi) is not str or re.fullmatch(r"[0-9a-f]{64}", asgi) is None)):
                raise ValueError("LIFECYCLE_RECORDING_ASGI_ASSIGNMENT_INVALID")
            if "model_selection" in payload["metadata"]:
                profiles.validate_model_selection(payload)
            if "weight_selection" in payload["metadata"]:
                profiles.validate_weight_selection(payload)
            if "route_authentication" in payload["metadata"]:
                profiles.validate_route_authentication(payload)
            if "route_handler" in payload["metadata"]:
                profiles.validate_route_handler(payload)
            if "request_selection" in payload["metadata"]:
                if row["event"]=="NATIVE" and payload["point"]=="InputProcessor.assign_request_id":
                    raise ValueError("LIFECYCLE_SELECTION_EVENT_REQUIRED")
                profiles.validate_request_selection(payload)
            if is_online and payload["point"] in online.POINT_CONTRACTS:
                if (set(payload["metadata"]) != {"online_input"} or payload["requests"] != []
                        or payload["selection_observed"] is not False or row["event"] != "NATIVE"
                        or online.CALLBACK_MODULE not in ready or point_sources[payload["point"]] not in ready):
                    raise ValueError("ONLINE_POINT_PROVENANCE_INVALID")
                snapshot = online_metadata.validate_snapshot(payload["metadata"]["online_input"],
                    payload["point"], payload["phase"], context, prepared["session"])
                call = snapshot["binding"]["call_id_sha256"]
                if payload["phase"] == "CALL":
                    if call in online_calls:raise ValueError("ONLINE_CALL_ID_REUSED")
                    online_calls[call] = {"point":payload["point"], "binding":snapshot["binding"], "closed":False}
                elif call not in online_calls:
                    online_orphans += 1
                else:
                    first_call = online_calls[call]
                    if first_call["closed"] or first_call["point"] != payload["point"] or first_call["binding"] != snapshot["binding"]:
                        raise ValueError("ONLINE_CALL_BINDING_MISMATCH")
                    first_call["closed"] = True
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
                if (not profiles.valid_call_frame(frame,prepared["profile"])
                        or frame["point"] not in points or type(frame["request_sha256"]) is not list
                        or len(frame["request_sha256"]) > sensor.MAX_IDS
                        or any(type(v) is not str or re.fullmatch(r"[0-9a-f]{64}",v) is None for v in frame["request_sha256"])):
                    raise ValueError("LIFECYCLE_RECORDING_CHAIN_INVALID")
            if payload.get("source_sha256") != source_hashes[point_sources[payload["point"]]]:
                raise ValueError("LIFECYCLE_RECORDING_POINT_SOURCE_INVALID")
        if row['event'] in {'WORKER_STARTUP','WORKER_STARTUP_LINK','WORKER_STARTUP_CLOSE','WORKER_STARTUP_LIMIT'}:
            if not is_worker_startup:raise ValueError('WORKER_STARTUP_PROFILE_MISMATCH')
            validator={'WORKER_STARTUP':startup_metadata.validate_sample,'WORKER_STARTUP_LINK':startup_metadata.validate_link,'WORKER_STARTUP_CLOSE':startup_metadata.validate_close,'WORKER_STARTUP_LIMIT':startup_metadata.validate_limit}[row['event']]
            validator(payload)
            if row['event']=='WORKER_STARTUP':
                if payload['source_verified'] and not set(startup_contract.SOURCE_SHA256)<=set(ready):raise ValueError('WORKER_STARTUP_SOURCE_UNAVAILABLE')
                if payload['operation_finished_ns']>row['monotonic_ns']:raise ValueError('WORKER_STARTUP_CLOCK_INVALID')
        if row['event']=='SAFE_OPEN_MAPPING':
            mapping_metadata.validate_sample(payload)
            if not is_mapping or payload['source_verified'] and payload['source_module'] not in ready:raise ValueError('SAFE_OPEN_MAPPING_SOURCE_UNAVAILABLE')
            if payload['operation_finished_ns']>row['monotonic_ns']:raise ValueError('SAFE_OPEN_MAPPING_CLOCK_INVALID')
        if row['event']=='FILE_READ':
            read_metadata.validate_sample(payload)
            if not is_read or payload['source_verified'] and payload['source_module'] not in ready:raise ValueError('FILE_READ_SOURCE_UNAVAILABLE')
            if payload['operation_finished_ns']>row['monotonic_ns']:raise ValueError('FILE_READ_CLOCK_INVALID')
        if row['event']=='WEIGHT_ITERATION':
            weight_metadata.validate_sample(payload)
            if not is_weight or payload['source_verified'] and payload['source_module'] not in ready:
                raise ValueError('WEIGHT_ITERATION_SOURCE_MODULE_UNAVAILABLE')
            if any(payload[key] is not None and payload[key]>row['monotonic_ns'] for key in ('operation_started_ns','operation_finished_ns')):
                raise ValueError('WEIGHT_ITERATION_CLOCK_INVALID')
            weight_events.append(row)
        if row['event']=='PROFILER':
            profiler_metadata.validate_sample(payload)
            if payload['source_module'] not in ready:raise ValueError('PROFILER_SOURCE_MODULE_UNAVAILABLE')
            if profiler_runtime is not None and payload['observer_runtime']!=profiler_runtime:raise ValueError('PROFILER_OBSERVER_RUNTIME_MISMATCH')
            profiler_runtime=payload['observer_runtime'];profiler_events+=1
            identity={k:payload[k] for k in ('point','parent_call_sha256','owner_sha256','asgi_request_sha256','asgi_invocation_sha256','registered_handler_observed','is_start','state_before')}
            call=profiler_calls.setdefault(payload['call_sha256'],{'identity':identity,'calls':0,'ends':0})
            if call['identity']!=identity:raise ValueError('PROFILER_CALL_BINDING_MISMATCH')
            call['calls']+=payload['phase']=='CALL';call['ends']+=payload['phase'] in {'RETURN','RAISE'}
            if call['calls']>1 or call['ends']>1:raise ValueError('PROFILER_CALL_ID_REUSED')
        if row["event"]=="OUTPUT":
            output_metadata.validate_output_link(payload)
            if output_sensor.MODULE not in ready:raise ValueError("OUTPUT_SOURCE_MODULE_UNAVAILABLE")
            if output_runtime is not None and payload["observer_runtime"]!=output_runtime:raise ValueError("OUTPUT_OBSERVER_RUNTIME_MISMATCH")
            output_runtime=payload["observer_runtime"];output_events+=1
            key=payload["call_sha256"];identity=(payload["point"],payload["collector_sha256"])
            call=output_calls.setdefault(key,{"identity":identity,"calls":0,"ends":0})
            if call["identity"]!=identity:raise ValueError("OUTPUT_CALL_BINDING_MISMATCH")
            call["calls"]+=payload["phase"]=="CALL"
            call["ends"]+=payload["phase"] in {"RETURN","RAISE"}
        if row["event"]=="ATTEMPT":
            attempt_metadata.validate_attempt(payload)
            if attempt_runtime is not None and payload["observer_runtime"]!=attempt_runtime:
                raise ValueError("ATTEMPT_OBSERVER_RUNTIME_MISMATCH")
            attempt_runtime=payload["observer_runtime"];attempt_events+=1
            invocation=payload["asgi_invocation_sha256"]
            identity={k:payload[k] for k in ("asgi_request_sha256","asgi_invocation_sha256","entry_path_sha256","entry_root_path_sha256","entry_method_sha256")}
            call=attempt_calls.setdefault(invocation,{"identity":identity,"enters":0,"ends":0,"late":0,"unhashed":0})
            if call["identity"]!=identity:raise ValueError("ATTEMPT_INVOCATION_BINDING_MISMATCH")
            call["enters"]+=payload["phase"]=="APP_ENTER"
            call["ends"]+=payload["phase"] in {"APP_RETURN","APP_RAISE"}
            call["late"]+=not payload["context_live"]
            call["unhashed"]+=payload["phase"].startswith("BODY_SEND_") and not payload["body_hash_complete"]
        if row["event"] == "ASGI":
            if (set(payload) != {"request_sha256","phase","more_body"}
                    or payload["request_sha256"] is not None and (type(payload["request_sha256"]) is not str
                        or re.fullmatch(r"[0-9a-f]{64}",payload["request_sha256"]) is None)
                    or payload["phase"] not in {"DISCONNECT_RECEIVED","BODY_SEND_RETURN","APP_RETURN","APP_RAISE"}
                    or payload["more_body"] is not None and type(payload["more_body"]) is not bool):
                raise ValueError("LIFECYCLE_RECORDING_ASGI_INVALID")
        if row["event"] == "END" and row is not rows[-1]:
            raise ValueError("LIFECYCLE_RECORDING_AFTER_END")
    if is_output and output_runtime is not None and attempt_runtime is not None and output_runtime!=attempt_runtime:
        raise ValueError("OUTPUT_INVOCATION_RUNTIME_MISMATCH")
    startup_journal=startup_metadata.journal(rows,prepared,header['context']) if is_worker_startup else None
    mapping_journal=mapping_metadata.journal([row for row in rows if row['event']=='SAFE_OPEN_MAPPING'],prepared,weight_events) if is_mapping else None
    read_journal=read_metadata.journal([row for row in rows if row['event']=='FILE_READ'],prepared,weight_events) if is_read else None
    end = rows[-1]["payload"] if rows[-1]["event"] == "END" else {}
    if rows[-1]["event"] == "END" and (set(end) != {"reason"} or end["reason"] not in {"SEALED","PROCESS_EXIT","BOUND_REACHED","CONTROL_UNAVAILABLE"}):
        raise ValueError("LIFECYCLE_RECORDING_END_INVALID")
    weight_journal=weight_metadata.journal(weight_events) if is_weight else {}
    return rows, {"recording_closed": bool(end) and not incomplete_tail, "recording_end": end.get("reason"),
        "incomplete_final_row":incomplete_tail,"discarded_tail_bytes":len(tail),
        "discarded_tail_sha256":hashlib.sha256(tail).hexdigest() if tail else None,
        "source_profile_modules": sorted(ready), "whole_run_coverage_verified": False,
        **({'worker_startup_events':len(startup_journal['events']),'worker_startup_missing':startup_journal['missing']} if is_worker_startup else {}),
        **({'safe_open_mapping_events':len(mapping_journal['samples']),'safe_open_mapping_missing':mapping_journal['missing']} if is_mapping else {}),
        **({'file_read_events':sum(row['event']=='FILE_READ' for row in rows),'file_read_missing':read_journal['missing']} if is_read else {}),
        **({'weight_iteration_events':len(weight_events),'weight_iteration_missing':weight_journal['missing']} if is_weight else {}),
        **({'profiler_observer_runtime':profiler_runtime,'profiler_event_count':profiler_events,
            'profiler_incomplete_calls':sum(c['calls']!=1 or c['ends']!=1 for c in profiler_calls.values()),
            'profiler_observer_code_sha256':profiler_sensor.RUNTIME_CODE_CONTRACTS.get(profiler_runtime)} if is_profiler else {}),
        **({'initialization_observer_runtime':init_runtime,'initialization_event_count':init_events,
            'initialization_observer_code_sha256':init_sensor.RUNTIME_CODE_CONTRACTS.get(init_runtime)} if is_init else {}),
        **({'rank_observer_runtime':rank_runtime,'rank_event_count':rank_events,
            'rank_observer_code_sha256':rank_sensor.RUNTIME_CODE_CONTRACTS.get(rank_runtime)} if is_rank else {}),
        **({'stream_observer_runtime':stream_runtime,'stream_event_count':stream_events,
            'stream_observer_code_sha256':stream_sensor.RUNTIME_CODE_CONTRACTS.get(stream_runtime)} if is_stream else {}),
        **({"output_observer_runtime":output_runtime,"output_event_count":output_events,
            "output_incomplete_calls":sum(v["calls"]!=1 or v["ends"]!=1 for v in output_calls.values()),
            "output_observer_code_sha256":output_sensor.RUNTIME_CODE_CONTRACTS.get(output_runtime)} if is_output else {}),
        **({"attempt_observer_runtime":attempt_runtime,"attempt_event_count":attempt_events,
            "attempt_incomplete_invocations":sum(v["enters"]!=1 or v["ends"]!=1 for v in attempt_calls.values()),
            "attempt_late_callbacks":sum(v["late"] for v in attempt_calls.values()),
            "attempt_unhashed_bodies":sum(v["unhashed"] for v in attempt_calls.values()),
            "attempt_observer_code_sha256":attempt.RUNTIME_CODE_CONTRACTS.get(attempt_runtime)} if is_attempt else {}),
        **({"online_code_contract":online.CODE_CONTRACT,
            "online_runtime":online_runtime,
            "online_incomplete_calls":sum(not call["closed"] for call in online_calls.values()) + online_orphans,
            "online_callback_provenance":({"module":online.CALLBACK_MODULE, "bytes_sha256":online.CALLBACK_SHA256,
                "code_sha256":online.runtime_contract(online_runtime)["callback_code_sha256"],
                "code_runtime":online_runtime} if online.CALLBACK_MODULE in ready else None)} if is_online else {})}


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
    is_online = prepared["profile"] == online.PROFILE
    is_attempt=prepared["profile"] in {attempt.PROFILE,output_sensor.PROFILE}
    is_stream=prepared["profile"]==stream_sensor.PROFILE
    is_rank=prepared["profile"]==rank_sensor.PROFILE
    is_weight=prepared["profile"] in {weight_sensor.PROFILE,read_sensor.PROFILE,mapping_sensor.PROFILE,startup_sensor.PROFILE,joint_sensor.PROFILE}
    is_profiler=prepared["profile"]==profiler_sensor.PROFILE
    is_init=prepared["profile"] in {init_sensor.PROFILE,joint_sensor.PROFILE}
    limit = weight_sensor.MAX_BYTES if is_weight else profiler_sensor.MAX_BYTES if is_profiler else init_sensor.MAX_BYTES if is_init else rank_sensor.MAX_BYTES if is_rank else stream_sensor.MAX_BYTES if is_stream else attempt.MAX_BYTES if is_attempt else online.MAX_BYTES if is_online else sensor.MAX_BYTES
    read_options = {"read_profile":EXTENDED_RECORDING_PROFILE} if (is_online or is_attempt or is_stream or is_rank or is_init or is_profiler or is_weight) and read_file is None else {}
    raw, provenance = read(docker, target, **source, max_bytes=limit, **read_options)
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
        later, second = read(docker, target, **source, max_bytes=limit, **read_options)
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
        **({"online_callback_provenance":summary["online_callback_provenance"],
            "online_code_contract":online.CODE_CONTRACT} if is_online else {}),
        "provenance":safe_provenance, "process_context_verified":verified, "image_digest_verified":image_verified,
        "diagnostic_codes":diagnostic, "product_connected":False, "security":"UNKNOWN",
        "producer_authenticated":False, "record_authenticity_verified":False,
        "scope":"one supplied process recording; not whole-run coverage"}
