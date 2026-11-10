"""회수 전용 연결 후보. 이 모듈은 AWS 호출이나 GPU 기동을 하지 않는다."""
import base64
import hashlib
import json
import re
import shlex
import types
import uuid
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


IID = "i-05e8da8ea99593196"
CODEC_SHA = "6de3190f37d333bd1055e6abe706addb2a606405f701cf710712150852e1591f"
COPY_SHA = "5a6d67b20412e3060fbf31a5770ff0018dae39630db07ba909267ebfb49f7080"
ENCODE_SECONDS = 60
MAX_ENVELOPE = 4 * 1024 * 1024
REFERENCE = {
    "origin": "ACTUAL_RETAINED_R8",
    "original_path": "/home/ubuntu/byul-gpu-b-tp2-20261103/GB2026111112-original-evidence.zip",
    "original_bytes": 14636464,
    "original_sha256": "2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219",
    "original_boot_id": "78fd6444-4138-4b60-8833-333e14805550",
    "original_execution_command_id": "8a5e8bdc-e5fb-48df-9f87-f5147b8cbe4c",
    "original_discovery_command_id": "dd87580f-ce50-435c-9e26-6a7cceb9a9af",
}
ENVELOPE_PATH = str(PurePosixPath(REFERENCE["original_path"]).with_name("GB2026111112-transfer.json"))
WIRE_SCHEMA = "S2-retained-R8-encoder-terminal-wire/1"
RECEIPT_FIELDS = {
    "schema", "envelope_path", "encoder_uid", "envelope_bytes", "envelope_sha256",
    "published_identity", "reference", "recovery_boot_id", "collector_request_id",
    "original_source_modified", "original_ZIP_CRC_verified",
}
IDENTITY_FIELDS = {"dev", "inode", "uid", "gid", "mode", "nlink", "bytes", "mtime_ns", "ctime_ns"}


class JoinError(ValueError):
    pass


def require(value, reason):
    if not value:
        raise JoinError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def uuid_text(value):
    require(type(value) is str, "UUID_TEXT_REQUIRED")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise JoinError("UUID_TEXT_REQUIRED") from error
    require(str(parsed) == value, "CANONICAL_UUID_REQUIRED")
    return value


def duplicate_reject(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def verified_codec_module(raw):
    require(type(raw) is bytes and len(raw) == 22363 and digest(raw) == CODEC_SHA,
            "EXACT_FROZEN_CODEC_BYTES_REQUIRED")
    module = types.ModuleType("s2_retained_archive_codec")
    module.__file__ = "<fixed-codec-6de3190f>"
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module


def build_encode_argv(codec_raw, request_id):
    """고정 source만 명령에 넣는다. 명령을 전송·실행하지 않는다."""
    uuid_text(request_id)
    require(type(codec_raw) is bytes and len(codec_raw) == 22363 and digest(codec_raw) == CODEC_SHA,
            "EXACT_FROZEN_CODEC_BYTES_REQUIRED")
    payload = base64.b64encode(zlib.compress(codec_raw, 9)).decode("ascii")
    source = f'''import base64,hashlib,json,os,time,zlib
deadline=time.monotonic()+{ENCODE_SECONDS}
def check_remaining():
    if time.monotonic()>=deadline:raise TimeoutError('ENCODER_ORIGINAL_PHASE60_EXPIRED')
check_remaining()
if os.getuid()!=0 or os.geteuid()!=0:raise ValueError('PRODUCTION_ENCODER_UID0_REQUIRED')
raw=zlib.decompress(base64.b64decode({payload!r},validate=True))
if len(raw)!=22363 or hashlib.sha256(raw).hexdigest()!={CODEC_SHA!r}:raise ValueError('FIXED_CODEC_BYTES_CHANGED')
ns={{'__name__':'s2_retained_archive_codec','__file__':'<fixed-codec-6de3190f>'}}
exec(compile(raw,ns['__file__'],'exec'),ns)
reference=json.loads({canonical(REFERENCE).decode('ascii')!r})
check_remaining()
receipt=ns['encode'](reference['original_path'],{ENVELOPE_PATH!r},reference,{request_id!r},check_remaining)
check_remaining()
wire={{'schema':{WIRE_SCHEMA!r},'codec_sha256':{CODEC_SHA!r},'receipt':receipt}}
print(json.dumps(wire,sort_keys=True,separators=(',',':'),ensure_ascii=True),flush=True)
'''
    compile(source, "<retained-R8-encoder-wrapper>", "exec")
    return ["/usr/bin/python3.12", "-I", "-S", "-B", "-c", source]


def build_encode_request(codec_raw, request_id):
    argv = build_encode_argv(codec_raw, request_id)
    return {
        "InstanceIds": [IID], "DocumentName": "AWS-RunShellScript",
        "Comment": "BYUL retained-R8 envelope " + request_id,
        "Parameters": {"commands": [shlex.join(argv)], "executionTimeout": [str(ENCODE_SECONDS)]},
        "TimeoutSeconds": 120, "MaxConcurrency": "1", "MaxErrors": "0",
    }


def request_sha(value):
    # 기존 submit 원문과 동일한 request 지문 표기다.
    return digest(json.dumps(value, sort_keys=True).encode())


def validate_receipt(receipt, request_id, recovery_boot):
    require(type(receipt) is dict and set(receipt) == RECEIPT_FIELDS, "EXACT_ENCODER_RECEIPT_FIELDS_REQUIRED")
    require(receipt["schema"] == "byul-archive-envelope-encoded/1", "ENCODER_RECEIPT_SCHEMA_REQUIRED")
    require(receipt["reference"] == REFERENCE, "EXACT_RETAINED_REFERENCE_REQUIRED")
    require(receipt["envelope_path"] == ENVELOPE_PATH, "EXACT_ENVELOPE_PATH_REQUIRED")
    require(type(receipt["encoder_uid"]) is int and receipt["encoder_uid"] == 0,
            "ACTUAL_ENCODER_UID0_REQUIRED")
    require(receipt["collector_request_id"] == uuid_text(request_id), "ENCODER_REQUEST_ID_MISMATCH")
    require(receipt["recovery_boot_id"] == uuid_text(recovery_boot)
            and recovery_boot != REFERENCE["original_boot_id"], "CURRENT_RECOVERY_BOOT_REQUIRED")
    require(type(receipt["envelope_bytes"]) is int and 0 < receipt["envelope_bytes"] <= MAX_ENVELOPE,
            "ENCODER_WHOLE_ENVELOPE_CAP_REQUIRED")
    require(type(receipt["envelope_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", receipt["envelope_sha256"]), "ENVELOPE_SHA256_REQUIRED")
    require(receipt["original_source_modified"] is False
            and receipt["original_ZIP_CRC_verified"] is False, "ORIGINAL_SCOPE_FLAGS_REQUIRED")
    identity = receipt["published_identity"]
    require(type(identity) is dict and set(identity) == IDENTITY_FIELDS, "EXACT_PUBLISHED_IDENTITY_REQUIRED")
    for key in ("dev", "inode", "uid", "gid", "mode", "nlink", "bytes"):
        require(type(identity[key]) is int and 0 <= identity[key] < 2**64, "INTEGER_IDENTITY_REQUIRED")
    for key in ("mtime_ns", "ctime_ns"):
        require(type(identity[key]) is str and re.fullmatch("0|[1-9][0-9]{0,29}", identity[key]),
                "DECIMAL_IDENTITY_TIME_REQUIRED")
    require(identity["uid"] == 1000 and identity["gid"] == 1000 and identity["mode"] == 0o600
            and identity["nlink"] == 1 and identity["bytes"] == receipt["envelope_bytes"],
            "PRIVATE_ENVELOPE_PUBLICATION_REQUIRED")


@dataclass(frozen=True)
class ValidatedEncoding:
    command_id: str
    request_sha256: str
    stdout_sha256: str
    receipt_json: bytes

    def receipt(self):
        # getter가 새 객체를 돌려 주므로 소비자의 수정이 검증 객체를 바꾸지 않는다.
        return json.loads(self.receipt_json)


def join_encode_terminal(codec_raw, expected_request_id, expected_boot, submission, terminal):
    """원 request/수락 CID/서버 terminal을 exact 결합한 뒤에만 GET 입력을 낸다."""
    expected_request = build_encode_request(codec_raw, expected_request_id)
    uuid_text(expected_boot)
    require(expected_boot != REFERENCE["original_boot_id"], "CURRENT_RECOVERY_BOOT_REQUIRED")
    require(type(submission) is dict and submission.get("request") == expected_request,
            "EXACT_ORIGINAL_REQUEST_REQUIRED")
    expected_sha = request_sha(expected_request)
    require(submission.get("request_sha256") == expected_sha, "ORIGINAL_REQUEST_SHA_REQUIRED")
    response = submission.get("response")
    require(type(response) is dict and type(response.get("Command")) is dict,
            "POSITIVE_ORIGINAL_COMMAND_ACCEPTANCE_REQUIRED")
    command = response["Command"]
    cid = uuid_text(command.get("CommandId"))
    require(command.get("DocumentName") == expected_request["DocumentName"]
            and command.get("Parameters") == expected_request["Parameters"],
            "EXACT_ACCEPTED_DOCUMENT_PARAMETERS_REQUIRED")
    require(command.get("InstanceIds") == [IID], "ORIGINAL_ACCEPTED_INSTANCE_REQUIRED")
    require(type(terminal) is dict and terminal.get("CommandId") == cid
            and terminal.get("InstanceId") == IID, "SAME_COMMAND_INSTANCE_TERMINAL_REQUIRED")
    require(terminal.get("Status") == "Success" and type(terminal.get("ResponseCode")) is int
            and terminal["ResponseCode"] == 0, "POSITIVE_SUCCESS0_TERMINAL_REQUIRED")
    require(terminal.get("StandardErrorContent") == "", "ENCODER_STDERR_NOT_EMPTY")
    text = terminal.get("StandardOutputContent")
    require(type(text) is str and 0 < len(text) < 8192, "COMPLETE_BOUNDED_ENCODER_STDOUT_REQUIRED")
    try:
        raw = text.encode("ascii")
        wire = json.loads(raw, object_pairs_hook=duplicate_reject,
                          parse_constant=lambda _: (_ for _ in ()).throw(JoinError("NONFINITE_JSON")))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise JoinError("ENCODER_SINGLE_COMPLETE_JSON_REQUIRED") from error
    require(type(wire) is dict and canonical(wire) + b"\n" == raw, "ENCODER_CANONICAL_SINGLE_LINE_REQUIRED")
    require(set(wire) == {"schema", "codec_sha256", "receipt"}
            and wire["schema"] == WIRE_SCHEMA and wire["codec_sha256"] == CODEC_SHA,
            "EXACT_ENCODER_WIRE_SOURCE_REQUIRED")
    receipt = wire["receipt"]
    validate_receipt(receipt, expected_request_id, expected_boot)
    return ValidatedEncoding(cid, expected_sha, digest(raw), canonical(receipt))


def existing_get_argv(validated, selected_python, helper, new_destination, new_log):
    require(type(validated) is ValidatedEncoding, "JOINED_ENCODER_TERMINAL_REQUIRED")
    receipt = validated.receipt()
    validate_receipt(receipt, receipt["collector_request_id"], receipt["recovery_boot_id"])
    require(Path(new_destination).is_absolute() and Path(new_log).is_absolute(), "ABSOLUTE_NEW_LOCAL_PATH_REQUIRED")
    require(Path(new_destination) != Path(new_log), "DISTINCT_NEW_LOG_REQUIRED")
    return [str(selected_python), str(helper), "get", "GB2026111112-transfer.json", str(new_destination),
            "--expected-size", str(receipt["envelope_bytes"]),
            "--expected-sha256", receipt["envelope_sha256"], "--log", str(new_log)]


def invoke_existing_get_once(validated, run_transfer_group, selected_python, helper, destination, log):
    """기존 run_transfer_group가900초 deadline을1회 추가하고 group 정리를 맡는다."""
    require(digest(Path(helper).read_bytes()) == COPY_SHA, "UNCHANGED_COPY_HELPER_REQUIRED")
    argv = existing_get_argv(validated, selected_python, helper, destination, log)
    result = run_transfer_group("export-retained-R8-envelope", argv)
    require(result.returncode == 0, "ORIGINAL_GET_GROUP_FAILED")
    # 원 helper가FD/name/size/hash를 검증한다. 여기서 새 deadline이나 재시도를 만들지 않는다.
    return result


def delegate_decode_once(codec_raw, incoming_envelope, new_destination, validated, check_remaining):
    require(type(validated) is ValidatedEncoding, "JOINED_ENCODER_TERMINAL_REQUIRED")
    receipt = validated.receipt()
    validate_receipt(receipt, receipt["collector_request_id"], receipt["recovery_boot_id"])
    module = verified_codec_module(codec_raw)
    # 실제 native UID1000/REG600 입력 및 새 목적지 조건은 원 codec가 검사한다.
    return module.decode(incoming_envelope, new_destination, dict(REFERENCE), receipt, check_remaining)
