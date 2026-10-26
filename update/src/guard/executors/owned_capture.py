"""소유 컨테이너의 제한 JSON 자료를 읽는다. 자료 출처 인증과 내용 판정은 하지 않는다."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from pathlib import PurePosixPath

from .log_analysis import _FILE_READER


MAX_BYTES = 65536


def strict_json(raw, *, limit=MAX_BYTES):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise ValueError("CAPTURE_BYTE_BOUND")
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("CAPTURE_DUPLICATE_KEY")
            out[key] = value
        return out
    def constant(_):
        raise ValueError("CAPTURE_NONFINITE_NUMBER")
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError("CAPTURE_INVALID_JSON") from None


def identity(docker, target, container, image, timeout):
    row = docker.inspect_json(container, timeout=timeout)
    cfg, state = row["Config"], row["State"]
    if (not target.get("run_id") or cfg.get("Labels", {}).get("guard.run") != target["run_id"]
            or cfg.get("Image") != image or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image or "")
            or state.get("Running") is not True or type(state.get("StartedAt")) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("Id", ""))
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", row.get("Image", ""))
            or (row.get("Name") != "/" + container and row["Id"] != container)):
        raise ValueError("CAPTURE_OWNER_UNVERIFIED")
    return {"container_id": row["Id"], "image_id": row["Image"], "started_at": state["StartedAt"],
            "image": cfg["Image"]}


def validate_owned_source(*, container, image, path):
    parts = PurePosixPath(path).parts if type(path) is str else ()
    if (not parts or not path.startswith("/") or ".." in parts or "\x00" in path or len(path) > 256
            or type(container) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", container)
            or type(image) is not str or not re.fullmatch(r".+@sha256:[0-9a-f]{64}", image)):
        raise ValueError("CAPTURE_PATH_INVALID")


def _read_owned_file(docker, target, *, container, image, path, timeout=5):
    """고정 source bytes·fstat·pipe 상한·전후 소유 신원을 확인하며 생산자를 인증하지 않는다."""
    validate_owned_source(container=container, image=image, path=path)
    deadline = time.monotonic() + timeout
    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise ValueError("CAPTURE_DEADLINE")
        return value
    before = identity(docker, target, container, image, remaining())
    result = docker.exec_bounded(container, ["python3", "-c", _FILE_READER, path, str(MAX_BYTES)],
                                 timeout=remaining(), max_bytes=128 * 1024)
    capture = result.capture or {}
    if (result.returncode != 0 or capture.get("complete") is not True
            or capture.get("bounded_at_source") is not True
            or capture.get("timed_out") is not False or capture.get("truncated") is not False):
        raise ValueError("CAPTURE_TRANSPORT_INCOMPLETE")
    packet = strict_json(result.stdout.encode(), limit=128 * 1024)
    if type(packet) is not dict:
        raise ValueError("CAPTURE_PACKET_INVALID")
    if (packet.get("schema") != "guard-container-file/2" or packet.get("source_complete") is not True
            or packet.get("file_identity_stable") is not True or packet.get("truncated") is not False
            or packet.get("nofollow_components") is not True or packet.get("regular_file") is not True):
        raise ValueError("CAPTURE_FILE_INCOMPLETE")
    raw = base64.b64decode(packet.get("raw_base64", ""), validate=True)
    if (type(packet.get("raw_retained_bytes")) is not int or packet["raw_retained_bytes"] != len(raw)
            or not 0 < len(raw) <= MAX_BYTES
            or packet.get("raw_retained_sha256") != hashlib.sha256(raw).hexdigest()):
        raise ValueError("CAPTURE_BYTES_MISMATCH")
    file_identity = packet.get("file_before")
    fields = {"device", "inode", "size", "mtime_ns", "ctime_ns"}
    if (type(file_identity) is not dict or set(file_identity) != fields
            or any(type(file_identity[k]) is not int or file_identity[k] < 0 for k in fields)
            or packet.get("file_after") != file_identity or packet.get("path_after") != file_identity
            or file_identity["size"] != len(raw)):
        raise ValueError("CAPTURE_FILE_IDENTITY_INVALID")
    after = identity(docker, target, container, image, remaining())
    if before != after:
        raise ValueError("CAPTURE_INSTANCE_CHANGED")
    return raw, {"capture_sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw),
        "owner": before, "file_identity": packet.get("file_before"),
        "producer_authenticated": False, "record_authenticity_verified": False}


def read_owned_json(docker, target, *, container, image, path, timeout=5):
    raw, provenance = _read_owned_file(docker, target, container=container, image=image, path=path, timeout=timeout)
    return strict_json(raw), provenance


def read_owned_jsonl(docker, target, *, container, image, path, timeout=5):
    """같은 소유권/전송/파일 경계로 최대 64개 JSON 객체를 읽는다. 원문은 반환하지 않는다."""
    raw, provenance = _read_owned_file(docker, target, container=container, image=image, path=path, timeout=timeout)
    lines = raw.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    if not 1 <= len(lines) <= 64 or any(not line.strip() or len(line) > 16384 for line in lines):
        raise ValueError("CAPTURE_JSONL_BOUND")
    records = [strict_json(line, limit=16384) for line in lines]
    if any(type(record) is not dict for record in records):
        raise ValueError("CAPTURE_JSONL_OBJECT_REQUIRED")
    return records, provenance
