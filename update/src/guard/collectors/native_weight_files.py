"""Link declared native iterator paths with independently read file bytes; no verdict."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path, PurePosixPath
import re

from guard.adapters import vllm_lifecycle_profiles as profiles
from guard.collectors.file import load_environment
from guard.collectors.lifecycle import load_prepared
from guard.collectors.model_manifest import _load_manifest
from guard.executors.lifecycle_capture import parse_recording, canonical_row, _safe
from guard.executors.owned_capture import strict_json, validate_owned_source
from guard.models import DataOrigin, canonical_json, sha256_hex

MAX_FILES=32
MAX_EVENTS=256


def _read(path,limit):
    with Path(path).open("rb") as stream:raw=stream.read(limit+1)
    return raw,strict_json(raw,limit=limit)


def validate_options(duration,max_file_bytes):
    if (type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=300
            or type(max_file_bytes) is not int or not 1<=max_file_bytes<=1024**3):
        raise ValueError("WEIGHT_FILE_OPTIONS_INVALID")


def load_inputs(env_path,target_path,prepared_path,context_path,manifest_path,native_path):
    env=load_environment(Path(env_path));_,target=_read(target_path,65536)
    prepared=load_prepared(prepared_path);_,context=_read(context_path,65536)
    manifest_raw,manifest=_read(manifest_path,4*1024**2)
    native_raw,native=_read(native_path,64*1024**2)
    fields={"schema","environment_ref","session","transport","model_root","native_session_sha256","model_manifest_sha256"}
    if (type(context) is not dict or set(context)!=fields or context["schema"]!="guard-native-weight-files-context/1"
            or not env.hash_ok() or context["environment_ref"]!=env.ref().model_dump(mode="json")
            or context["session"]!=prepared["session"] or type(context["transport"]) is not str or context["transport"] not in {"docker","kubernetes"}
            or context["native_session_sha256"]!=hashlib.sha256(native_raw).hexdigest()
            or context["model_manifest_sha256"]!=hashlib.sha256(manifest_raw).hexdigest()
            or type(target) is not dict or target.get("lab") is not True):
        raise ValueError("WEIGHT_FILE_CONTEXT_INVALID")
    root=context["model_root"]
    if type(root) is not str or root=="/" or not root.startswith("/") or PurePosixPath(root).as_posix()!=root or "\x00" in root:
        raise ValueError("WEIGHT_FILE_ROOT_INVALID")
    if context["transport"]=="kubernetes":
        from guard.executors.kubernetes_lifecycle_capture import validate_target
        binding=validate_target(target,env);container=binding["engine"]["container"];image=binding["engine"]["image"]
    else:
        if (target.get("environment_ref")!=env.ref().model_dump(mode="json") or type(target.get("engine")) is not dict
                or type(target.get("run_id")) is not str or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}",target["run_id"]) is None):
            raise ValueError("WEIGHT_FILE_TARGET_INVALID")
        container=target["engine"].get("container");image=env.engine.image_digest
    validate_owned_source(container=container,image=image,path=root)
    if (type(manifest) is not dict or manifest.get("schema")!="guard.model-manifest/1"
            or type(manifest.get("files")) is not list or len(manifest["files"])>4096
            or any(type(row) is not dict or type(row.get("bytes")) is not int for row in manifest["files"])):
        raise ValueError("WEIGHT_MODEL_MANIFEST_INVALID")
    manifest=_load_manifest(manifest,None)
    if (not env.model.inventory_sha256 or manifest["inventory_sha256"]!=env.model.inventory_sha256
            or manifest.get("model_id") not in (None,env.model.id)):
        raise ValueError("WEIGHT_MODEL_INVENTORY_MISMATCH")
    files={}
    for row in manifest["files"]:
        relative=row["path"]
        if (PurePosixPath(relative).as_posix()!=relative or "\x00" in relative or len(root+"/"+relative)>4096):
            raise ValueError("WEIGHT_MODEL_PATH_INVALID")
        if PurePosixPath(relative).suffix in {".safetensors",".bin",".pt",".gguf"}:
            digest=hashlib.sha256((root+"/"+relative).encode()).hexdigest()
            files[digest]={"relative":relative,"reference_sha256":row["sha256"],"reference_bytes":row["bytes"]}
    if (type(native) is not dict or native.get("schema")!="guard-lifecycle-session-capture/1"
            or native.get("environment_ref")!=env.ref().model_dump(mode="json") or native.get("session")!=prepared["session"]
            or type(native.get("recordings")) is not list or len(native["recordings"])>32
            or native.get("data_origin") not in {"EXAMPLE","DECLARED","MEASURED"}):
        raise ValueError("WEIGHT_NATIVE_SESSION_INVALID")
    selections=[];owners={};gap=False
    for index,recording in enumerate(native["recordings"]):
        try:
            capture=recording["capture"]
            if type(capture) is not dict:raise ValueError()
            source_rows=capture["records"]
            if type(source_rows) is not list or not 1<=len(source_rows)<=512 or any(not _safe(row) for row in source_rows):raise ValueError()
            pieces=[canonical_row(row) for row in source_rows]
            if any(len(v)>16384 for v in pieces) or sum(map(len,pieces))>262144:raise ValueError()
            rows,_=parse_recording(b"".join(pieces),prepared)
            owner=capture["provenance"]["owner"]
            if type(owner) is not dict:raise ValueError()
            owner_bytes=canonical_json(owner)
            if len(owner_bytes)>16384:raise ValueError()
            owner_sha=sha256_hex(owner_bytes);owners[owner_sha]=owner
            for row in rows:
                selected=profiles.returned_weight_selection(row)
                if selected is None:continue
                if len(selections)>=MAX_EVENTS:gap=True;break
                selections.append({**selected,"event":[index,row["seq"]],"owner_sha256":owner_sha})
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):gap=True
    return {"env":env,"target":target,"prepared":prepared,"context":context,"container":container,"image":image,
        "manifest":manifest,"files":files,"selections":selections,"owners":owners,"native_gap":gap,
        "native_origin":native["data_origin"]}


def collect(inputs,*,duration=180,max_file_bytes=1024**3,binary="kubectl",reader=None):
    validate_options(duration,max_file_bytes)
    env=inputs["env"];context=inputs["context"];selected={};gap=inputs["native_gap"];diagnostics=[]
    for event in inputs["selections"]:
        if not event["paths_complete"]:gap=True
        if not event["load_context_linked"] or event["factory_returned_generator"] is not True:
            gap=True;continue
        for path in event["paths"]:
            if path not in inputs["files"]:gap=True;diagnostics.append("NATIVE_WEIGHT_PATH_UNMAPPED");continue
            if path not in selected and len(selected)>=MAX_FILES:gap=True;diagnostics.append("WEIGHT_FILE_COUNT_BOUND");continue
            selected.setdefault(path,set()).add(event["owner_sha256"])
    injected=reader is not None
    if selected and reader is None:
        from guard.executors.weight_file_capture import DockerWeightReader, KubernetesWeightReader
        if context["transport"]=="kubernetes":
            reader=KubernetesWeightReader(inputs["target"],env,inputs["prepared"],context["model_root"],
                [inputs["files"][p]["relative"] for p in selected],max_file_bytes,binary=binary,duration=duration)
        else:
            from guard.executors.owned_capture import create_capture_client
            reader=DockerWeightReader(create_capture_client(inputs["target"]["run_id"]),inputs["target"],duration=duration)
    rows=[]
    for path,owner_hashes in selected.items():
        reference=inputs["files"][path]
        row={"path_sha256":path,"reference_sha256":reference["reference_sha256"],"reference_bytes":reference["reference_bytes"],
            "capture":None,"owner_binding_verified":False,"native_owner_match":False,"content_matches_reference":None,
            "capture_verified":False,"diagnostic_codes":[]}
        try:
            packet,owner,stable=reader.read(inputs["container"],inputs["image"],context["model_root"],reference["relative"],max_file_bytes)
            from guard.executors.weight_file_capture import validate_packet_body
            packet=validate_packet_body(packet,path,max_file_bytes)
            if type(stable) is not bool or type(owner) is not dict:raise ValueError()
            owner_sha=sha256_hex(canonical_json(owner))
            row.update(capture=packet,owner_sha256=owner_sha,owner_binding_verified=stable,
                native_owner_match=owner_sha in owner_hashes,
                content_matches_reference=packet["content_sha256"]==reference["reference_sha256"] and packet["bytes_read"]==reference["reference_bytes"])
            row["capture_verified"]=(stable and row["native_owner_match"] and packet["file_identity_stable"] and packet["logical_binding_stable"])
            if not row["capture_verified"]:row["diagnostic_codes"].append("WEIGHT_FILE_BINDING_UNVERIFIED");gap=True
        except Exception:row["diagnostic_codes"].append("WEIGHT_FILE_CAPTURE_UNAVAILABLE");gap=True
        rows.append(row)
    if not selected:gap=True;diagnostics.append("NATIVE_WEIGHT_SELECTION_UNAVAILABLE")
    origin=DataOrigin.EXAMPLE if injected or env.data_origin==DataOrigin.EXAMPLE or inputs["native_origin"]=="EXAMPLE" else DataOrigin.DECLARED
    return {"schema":"guard-native-weight-files-capture/1","environment_ref":env.ref().model_dump(mode="json"),
        "session":inputs["prepared"]["session"],"data_origin":origin.value,"model_inventory_sha256":inputs["manifest"]["inventory_sha256"],
        "native_session_sha256":context["native_session_sha256"],"model_manifest_sha256":context["model_manifest_sha256"],
        "model_root_sha256":hashlib.sha256(context["model_root"].encode()).hexdigest(),"selections":inputs["selections"],
        "files":rows,"collection_gap":gap,"diagnostic_codes":sorted(set(diagnostics)),"security":"UNKNOWN",
        "producer_authenticated":False,"final_model_verified":False,"actual_consumption_verified":False,
        "weights_revision_verified":False,"whole_run_coverage_verified":False,"engine_started":False,
        "transport":({"profile":"injected-owned-weight-read-v1"} if injected else reader.report()
            if hasattr(reader,"report") else {"profile":context["transport"]+"-owned-weight-file-read-v1"})}
