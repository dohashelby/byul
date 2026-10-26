"""계측 이미지에서 명시적으로 호출할 제한 template 읽기 센서.

자동 monkeypatch·엔진 import·기동을 하지 않는다. 파일을 다시 열어 해시만 재는
snapshot이 아니라 반환하는 동일 buffer를 기록한다. native loader를 이 함수로
연결하려면 선정 이미지/소스 검토와 별도 계측 이미지가 필요하다.
"""
from __future__ import annotations

import hashlib
import inspect
import os
from pathlib import Path
import stat


MAX_TEMPLATE_BYTES = 8192


def observe_template_call(function, kwargs, *, stage, reviewed_source_sha256, emit):
    """검토된 동기 selection/apply 호출의 실제 인수·반환 연결만 기록한다."""
    if stage not in {"selection", "apply"} or type(kwargs) is not dict:
        raise ValueError("TEMPLATE_SENSOR_CALL_UNSUPPORTED")
    if inspect.iscoroutinefunction(function) or inspect.isgeneratorfunction(function) or inspect.isasyncgenfunction(function):
        raise ValueError("TEMPLATE_SENSOR_DEFERRED_CALL_UNSUPPORTED")
    filename = inspect.getsourcefile(function)
    if not filename or hashlib.sha256(Path(filename).read_bytes()).hexdigest() != reviewed_source_sha256:
        raise ValueError("TEMPLATE_SENSOR_SOURCE_MISMATCH")
    selected = kwargs.get("chat_template")
    if type(selected) is not str or len(selected.encode()) > MAX_TEMPLATE_BYTES:
        raise ValueError("TEMPLATE_SENSOR_TEMPLATE_UNSUPPORTED")
    event = {"kind": "template_" + stage, "argument_sha256": hashlib.sha256(selected.encode()).hexdigest(),
             "source_sha256": reviewed_source_sha256, "outcome": "error"}
    try:
        result = function(**kwargs)
    except Exception:
        try:
            emit(event)
        except Exception:
            pass  # missing telemetry stays a gap; never replace the native exception
        raise  # preserves native exception and does not persist its text
    # Async/generator invocation is not evidence that the native operation ran.
    if inspect.isawaitable(result) or inspect.isgenerator(result) or inspect.isasyncgen(result):
        event["outcome"] = "deferred"
        emit(event)
        return result
    event["outcome"] = "returned"
    if stage == "selection" and type(result) is str and len(result.encode()) <= MAX_TEMPLATE_BYTES:
        event["returned_sha256"] = hashlib.sha256(result.encode()).hexdigest()
    emit(event)
    return result


def read_template(path, *, reviewed_loader_path, reviewed_loader_sha256, emit):
    """UTF-8 고정 파일 프로파일만 지원한다. caller가 반환 문자열을 native 경로에 전달한다."""
    source = Path(reviewed_loader_path).read_bytes()
    if hashlib.sha256(source).hexdigest() != reviewed_loader_sha256:
        raise ValueError("TEMPLATE_SENSOR_SOURCE_MISMATCH")
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts:
        raise ValueError("TEMPLATE_SENSOR_PATH_UNSUPPORTED")
    for part in [*reversed(p.parents), p]:
        if part.is_symlink():
            raise ValueError("TEMPLATE_SENSOR_SYMLINK")
    path_before = p.stat()
    with p.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_TEMPLATE_BYTES:
            raise ValueError("TEMPLATE_SENSOR_FILE_UNSUPPORTED")
        raw = stream.read(MAX_TEMPLATE_BYTES + 1)
        after = os.fstat(stream.fileno())
    def identity(value):
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    path_after = p.stat()
    # Compare time fields within each API; Windows handle/path metadata may differ.
    # Keep complete before/after checks for both views and cross-bind inode/device/size.
    if (identity(before) != identity(after) or identity(path_before) != identity(path_after)
            or (after.st_dev, after.st_ino, after.st_size) != (
                path_after.st_dev, path_after.st_ino, path_after.st_size)
            or len(raw) > MAX_TEMPLATE_BYTES):
        raise ValueError("TEMPLATE_SENSOR_FILE_CHANGED")
    # Matches the reviewed UTF-8 text-open/universal-newline subset, not every native loader branch.
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    event = {"kind": "template_read", "raw_sha256": hashlib.sha256(raw).hexdigest(),
             "decoded_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
             "raw_bytes": len(raw), "decoded_bytes": len(text.encode("utf-8")),
             "loader_sha256": reviewed_loader_sha256, "process_id": os.getpid(),
             "buffer_returned": True, "selection_observed": False, "apply_observed": False,
             "generate_handoff_observed": False, "worker_consumption_verified": False}
    emit(event)  # Metadata only; no raw template, path, request or token values.
    return text
