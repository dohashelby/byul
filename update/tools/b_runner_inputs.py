"""REQ-08/09/10의 기존 수집 wrapper를 B runner_input으로 고정한다. 실행 기능은 없다.

ENV/target/prepared/session와 사례 매핑은 별도 입력이다. record에서 기대 scope나
매핑을 만들지 않는다. 파일·계약 일치는 실제 B HTTP 요청과 callback의 대응 인증이 아니다.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from guard.collectors import gpu_common_inputs
from guard.executors import common_consumption
from guard.models import Environment, canonical_json, sha256_hex
from guard.runtime.bounded import strict_json

INPUT_SCHEMA = "guard-b-runner-inputs/1"
OUTPUT_SCHEMA = "guard-b-runner-prepare/1"
WRAPPER_SCHEMA = "byul-gpu-a-producers-collect/1"
KIND = "runner-consumption"
CHECKS = ("REQ-08", "REQ-09", "REQ-10")
SOURCE_NAMES = ("wrapper", "environment", "target", "prepared", "requests")
MAX_WRAPPER_BYTES = 16 * 1024 * 1024
_SHA = re.compile(r"[0-9a-f]{64}")
_SESSION = re.compile(r"[0-9a-f]{32}")
_WS = re.compile(r"[ \t\r\n]*")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _identity(value):
    return {key: getattr(value, "st_" + key) for key in ("dev", "ino", "size", "mtime_ns", "mode", "uid", "gid")}


def _read_pin(pointer, *, base, limit):
    if (type(pointer) is not dict or set(pointer) != {"path", "file_sha256"}
            or type(pointer["path"]) is not str or not pointer["path"].strip() or "\x00" in pointer["path"]
            or type(pointer["file_sha256"]) is not str or not _SHA.fullmatch(pointer["file_sha256"])):
        raise ValueError("B_RUNNER_SOURCE_POINTER_INVALID")
    path = Path(pointer["path"])
    path = (path if path.is_absolute() else base / path).absolute()
    before_path = path.lstat()
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError("B_RUNNER_SOURCE_NOT_REGULAR")
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
            raise ValueError("B_RUNNER_SOURCE_BYTE_BOUND")
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (not (_identity(before_path) == _identity(before) == _identity(after) == _identity(path.lstat()))
            or len(raw) != before.st_size or _sha(raw) != pointer["file_sha256"]):
        raise ValueError("B_RUNNER_SOURCE_BYTES_MISMATCH")
    return raw, {"path": str(path), "file_sha256": _sha(raw), "fd_identity": _identity(after)}


def _member(text, start, name):
    """strict_json으로 검사한 object에서 value 위치만 찾는다. 재직렬화하지 않는다."""
    decoder = json.JSONDecoder()
    cursor = _WS.match(text, start).end()
    if text[cursor] != "{":
        raise ValueError("B_RUNNER_WRAPPER_OBJECT_REQUIRED")
    cursor = _WS.match(text, cursor + 1).end()
    while text[cursor] != "}":
        key, cursor = decoder.raw_decode(text, cursor)
        cursor = _WS.match(text, cursor).end() + 1  # validated colon
        value_start = _WS.match(text, cursor).end()
        _, value_end = decoder.raw_decode(text, value_start)
        if key == name:
            return value_start, value_end
        cursor = _WS.match(text, value_end).end()
        if text[cursor] == "}":
            break
        cursor = _WS.match(text, cursor + 1).end()
    raise ValueError("B_RUNNER_WRAPPER_MEMBER_MISSING:" + name)


def extract_record(raw):
    wrapper = strict_json(raw, limit=MAX_WRAPPER_BYTES)
    if (type(wrapper) is not dict or wrapper.get("schema") != WRAPPER_SCHEMA
            or type(wrapper.get("records")) is not dict or type(wrapper.get("truncated")) is not bool):
        raise ValueError("B_RUNNER_WRAPPER_INVALID")
    entry = wrapper["records"].get(KIND)
    if type(entry) is not dict or "record" not in entry:
        raise ValueError("B_RUNNER_WRAPPER_RECORD_MISSING:" + str(entry))
    text = raw.decode("utf-8")
    start = 0
    for key in ("records", KIND, "record"):
        start, end = _member(text, start, key)
    byte_start, byte_end = len(text[:start].encode("utf-8")), len(text[:end].encode("utf-8"))
    record_raw = raw[byte_start:byte_end]
    record = strict_json(record_raw, limit=common_consumption.MAX_INPUT_BYTES)
    return wrapper, record, record_raw, {"start": byte_start, "end": byte_end}


def _validate_spec(spec):
    keys = {"schema", "check_id", "session", "required_cases", "sources", "data_origin"}
    if (type(spec) is not dict or set(spec) != keys or spec["schema"] != INPUT_SCHEMA
            or spec["check_id"] not in CHECKS or spec["data_origin"] not in {"EXAMPLE", "DECLARED"}
            or type(spec["session"]) is not str or not _SESSION.fullmatch(spec["session"])
            or type(spec["sources"]) is not dict or set(spec["sources"]) != set(SOURCE_NAMES)):
        raise ValueError("B_RUNNER_PREPARE_INPUT_INVALID")
    cases = spec["required_cases"]
    if (type(cases) is not list or not cases or len(cases) > 256
            or any(type(case) is not str or not case.strip() or len(case) > 128 for case in cases)
            or len(set(cases)) != len(cases)):
        raise ValueError("B_RUNNER_REQUIRED_CASES_INVALID")


def prepare(inputs, inputs_sha256, output):
    inputs = Path(inputs).absolute()
    input_raw, input_pin = _read_pin({"path": str(inputs), "file_sha256": inputs_sha256},
                                   base=inputs.parent, limit=65536)
    spec = strict_json(input_raw)
    _validate_spec(spec)
    sources, pins = {}, {"inputs": input_pin}
    for name in SOURCE_NAMES:
        sources[name], pins[name] = _read_pin(spec["sources"][name], base=inputs.parent,
            limit=MAX_WRAPPER_BYTES if name == "wrapper" else common_consumption.MAX_INPUT_BYTES)
    env = Environment.model_validate(strict_json(sources["environment"], limit=common_consumption.MAX_INPUT_BYTES))
    # ref() verifies the sealed object's content hash. Shorthand/record ENV refs are not an independent ENV.
    env_ref = env.ref().model_dump(mode="json")
    target = strict_json(sources["target"], limit=common_consumption.MAX_INPUT_BYTES)
    prepared = strict_json(sources["prepared"], limit=common_consumption.MAX_INPUT_BYTES)
    if (type(target) is not dict or not target or type(prepared) is not dict
            or prepared.get("schema") != "guard-engine-callback-assets/1"
            or prepared.get("session") != spec["session"] or prepared.get("runner_consumption") is not True):
        raise ValueError("B_RUNNER_INDEPENDENT_SCOPE_INVALID")
    scope = {"environment_ref": env_ref, "target_sha256": sha256_hex(canonical_json(target)),
             "prepared_sha256": sha256_hex(canonical_json(prepared)), "session": spec["session"]}
    requests = strict_json(sources["requests"], limit=common_consumption.MAX_INPUT_BYTES)
    wrapper, record, record_raw, span = extract_record(sources["wrapper"])
    if wrapper.get("session") != spec["session"]:
        raise ValueError("B_RUNNER_WRAPPER_SESSION_MISMATCH")
    projection = common_consumption.runner(record, scope, check_id=spec["check_id"],
        requests=requests, required_cases=spec["required_cases"])
    if wrapper["truncated"] and record["completeness"]["source_ended"]:
        raise ValueError("B_RUNNER_WRAPPER_COMPLETENESS_CONFLICT")
    # Cached wrapper validation/summary is deliberately ignored; preserve the original record and recompute.
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("NEW_B_RUNNER_OUTPUT_DIRECTORY_REQUIRED")
    config = {"path": str(output / "runner-record.json"), "sha256": _sha(record_raw),
              "scope": scope, "requests": requests, "data_origin": spec["data_origin"]}
    files = {**{name + ".json": raw for name, raw in sources.items()},
             "inputs.json": input_raw, "runner-record.json": record_raw,
             "runner-input.json": canonical_json({"runner_input": config})}
    update = Path(__file__).resolve().parents[1]
    names = ("tools/b_runner_inputs.py", "src/guard/executors/common_consumption.py",
             "src/guard/collectors/gpu_common_inputs.py", "src/guard/collectors/gpu_input_producers.py")
    receipt = {"schema": OUTPUT_SCHEMA, "check_id": spec["check_id"], "kind": KIND,
        "sources": pins, "record_byte_span": span, "scope": scope, "required_cases": spec["required_cases"],
        "runner_input": config, "consumer_projection": projection,
        "files": {name: {"bytes": len(raw), "file_sha256": _sha(raw)} for name, raw in files.items()},
        "source_files": {name: _sha((update / name).read_bytes()) for name in names},
        "import_origins": {module.__name__: str(Path(module.__file__).absolute())
                           for module in (gpu_common_inputs, common_consumption)},
        "input_files_fixed": True, "native_request_mapping_verified": False,
        "prepared_assets_runtime_verified": False, "runtime_link_complete": False, "gpu_ready": False,
        "limitations": ["Mapping keys/SHAs and observed event coverage are checked; native HTTP case identity is unverified.",
            "B input-validation headers={} and callback X-Request-Id constraints are unchanged.",
            "Independent files are pinned declarations; their current runtime provenance/ownership is unverified.",
            "Prepared manifest hash is checked; asset bytes, installation before engine import and Ready are unverified.",
            "Required cases are declared; the actual B consumer checks its own full selected case set again.",
            "No producer authentication, MEASURED origin, new policy, verdict or execution authority is created.",
            "FD identity covers this local read only, not runtime mount/subPath or engine process FD ownership."]}
    # Existing directories/files are never overwritten or removed. Partial outputs remain on I/O failure.
    output.mkdir(parents=True, exist_ok=False)
    for name, raw in files.items():
        with (output / name).open("xb") as stream:
            stream.write(raw)
    with (output / "receipt.json").open("xb") as stream:
        stream.write(canonical_json(receipt))
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--inputs-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = prepare(args.inputs, args.inputs_sha256, args.output)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"schema": OUTPUT_SCHEMA, "error": {"type": type(error).__name__, "message": str(error)},
                          "runtime_link_complete": False, "gpu_ready": False}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"schema": OUTPUT_SCHEMA, "check_id": receipt["check_id"], "output": str(args.output.absolute()),
                      "consumer_contract_complete": receipt["consumer_projection"]["complete"],
                      "runtime_link_complete": False, "gpu_ready": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
