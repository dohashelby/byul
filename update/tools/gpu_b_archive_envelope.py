"""Bounded, single-XZ-stream archive envelope; no AWS, model, or ZIP execution.

encode(source, destination, reference, request_id, check_remaining) -> receipt
decode(envelope, destination, reference, expected_encode_receipt, check_remaining) -> receipt

The caller supplies its existing deadline callback. This module creates no timer.
References marked ACTUAL_RETAINED_R8 must equal the historical constants below.
EXPLICIT_SYNTHETIC_CPU references are separate test inputs, never actual evidence.
Encoding permits root0 or UID1000; root0 changes only its newly created output FD
to UID/GID1000. Decoding requires UID1000. Existing paths are never chmod/chowned.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat
import uuid

MAX_ENVELOPE = 4 * 1024**2
MAX_COMPRESSED = 3 * 1024**2
MAX_ORIGINAL = 512 * 1024**2
CHUNK = 64 * 1024
DICTIONARY = 8 * 1024**2
DECODE_MEMLIMIT = 16 * 1024**2
SCHEMA = "byul-retained-archive-xz-envelope/1"
ALGORITHM = "xz-lzma2-checkSHA256-encoderDict8MiB-decodeMem16MiB"
ACTUAL_REFERENCE = {
    "origin": "ACTUAL_RETAINED_R8",
    "original_path": "/home/ubuntu/byul-gpu-b-tp2-20261103/GB2026111112-original-evidence.zip",
    "original_bytes": 14636464,
    "original_sha256": "2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219",
    "original_boot_id": "78fd6444-4138-4b60-8833-333e14805550",
    "original_execution_command_id": "8a5e8bdc-e5fb-48df-9f87-f5147b8cbe4c",
    "original_discovery_command_id": "dd87580f-ce50-435c-9e26-6a7cceb9a9af",
}


class CodecError(ValueError):
    pass


class EnvelopeTooLarge(CodecError):
    pass


def require(value, message):
    if not value:
        raise CodecError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def uuid_text(value):
    require(type(value) is str and str(uuid.UUID(value)) == value,
            "CANONICAL_UUID_REQUIRED")
    return value


def reference_checked(value):
    require(type(value) is dict and set(value) == set(ACTUAL_REFERENCE),
            "EXACT_REFERENCE_FIELDS_REQUIRED")
    require(value["origin"] in ("ACTUAL_RETAINED_R8", "EXPLICIT_SYNTHETIC_CPU"),
            "DECLARED_REFERENCE_ORIGIN_REQUIRED")
    require(type(value["original_bytes"]) is int
            and 0 < value["original_bytes"] <= MAX_ORIGINAL,
            "BOUNDED_ORIGINAL_SIZE_REQUIRED")
    require(type(value["original_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", value["original_sha256"]),
            "SHA256_REFERENCE_REQUIRED")
    path = value["original_path"]
    require(type(path) is str and Path(path).is_absolute()
            and str(Path(path)) == path and ".." not in Path(path).parts,
            "CANONICAL_ABSOLUTE_SOURCE_REQUIRED")
    for key in ("original_boot_id", "original_execution_command_id",
                "original_discovery_command_id"):
        uuid_text(value[key])
    if value["origin"] == "ACTUAL_RETAINED_R8":
        require(value == ACTUAL_REFERENCE, "EXACT_RETAINED_R8_REFERENCE_REQUIRED")
    return dict(value)


def identity(value):
    return {"dev": value.st_dev, "inode": value.st_ino, "uid": value.st_uid,
            "gid": value.st_gid, "mode": stat.S_IMODE(value.st_mode),
            "nlink": value.st_nlink, "bytes": value.st_size,
            "mtime_ns": str(value.st_mtime_ns), "ctime_ns": str(value.st_ctime_ns)}


def parent_fd(path):
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts and path.name not in ("", "."),
            "ABSOLUTE_NO_DOTDOT_PATH_REQUIRED")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:-1]:
            following = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = following
        value = os.fstat(fd)
        # Match the retained transfer's NOFOLLOW traversal boundary. The global
        # BASE's owner/mode is not an observed private-ASSETS 0700 guarantee.
        # Source FD owner/mode/nlink, exact size/SHA and before/after identity,
        # and new-output exclusive publication remain separately mandatory.
        require(stat.S_ISDIR(value.st_mode), "DIRECTORY_FD_REQUIRED")
        return fd
    except BaseException:
        os.close(fd)
        raise


def regular_open(path, ceiling):
    directory = parent_fd(path)
    fd = None
    try:
        fd = os.open(Path(path).name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 1000
                and stat.S_IMODE(value.st_mode) == 0o600 and value.st_nlink == 1,
                "OWNER1000_REGULAR600_SINGLE_LINK_REQUIRED")
        require(0 < value.st_size <= ceiling, "INPUT_SIZE_CEILING")
        require(identity(value) == identity(os.stat(Path(path).name, dir_fd=directory,
                                                    follow_symlinks=False)),
                "NAMED_INPUT_IDENTITY_CHANGED")
        return directory, fd, value
    except BaseException:
        if fd is not None:
            os.close(fd)
        os.close(directory)
        raise


def finish_input(path, directory, fd, before):
    after = os.fstat(fd)
    require(identity(before) == identity(after)
            == identity(os.stat(Path(path).name, dir_fd=directory, follow_symlinks=False)),
            "SOURCE_FD_IDENTITY_CHANGED")
    other = parent_fd(path)
    try:
        a, b = os.fstat(directory), os.fstat(other)
        require((a.st_dev, a.st_ino, a.st_uid, a.st_gid, a.st_mode)
                == (b.st_dev, b.st_ino, b.st_uid, b.st_gid, b.st_mode),
                "NAMED_PARENT_IDENTITY_CHANGED")
    finally:
        os.close(other)
    return after


def write_all(fd, raw):
    view = memoryview(raw)
    while view:
        n = os.write(fd, view)
        require(n > 0, "SHORT_OUTPUT_WRITE")
        view = view[n:]


def publish_new(path, writer, check_remaining):
    """Only this invocation's new scratch inode may be removed on failure."""
    path = Path(path)
    directory = parent_fd(path)
    fd = None
    error = None
    scratch = "." + path.name + "." + uuid.uuid4().hex + ".part"
    try:
        try:
            os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError(str(path))
        check_remaining()
        fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        original = os.fstat(fd)
        require(stat.S_ISREG(original.st_mode) and stat.S_IMODE(original.st_mode) == 0o600
                and original.st_uid == os.geteuid() and original.st_nlink == 1,
                "NEW_OUTPUT600_REQUIRED")
        if os.geteuid() == 0:
            os.fchown(fd, 1000, 1000)
        owned = os.fstat(fd)
        require((owned.st_dev, owned.st_ino) == (original.st_dev, original.st_ino)
                and owned.st_uid == 1000 and stat.S_IMODE(owned.st_mode) == 0o600
                and owned.st_nlink == 1, "NEW_OUTPUT_OWNER1000_REQUIRED")
        writer(fd)
        os.fsync(fd)
        check_remaining()
        require(os.fstat(fd).st_nlink == 1, "NEW_SCRATCH_LINK_COUNT_CHANGED")
        os.link(scratch, path.name, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        published = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require((published.st_dev, published.st_ino)
                == (original.st_dev, original.st_ino), "PUBLISHED_IDENTITY_CHANGED")
        os.unlink(scratch, dir_fd=directory)
        final = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        require(final.st_nlink == 1 and identity(final) == identity(os.fstat(fd)),
                "PUBLISHED_FINAL_IDENTITY_CHANGED")
        return identity(final)
    except BaseException as original_error:
        error = original_error
        raise
    finally:
        cleanup_errors = []
        try:
            if fd is not None:
                try:
                    named = os.stat(scratch, dir_fd=directory, follow_symlinks=False)
                    owned = os.fstat(fd)
                    if (named.st_dev, named.st_ino) == (owned.st_dev, owned.st_ino):
                        os.unlink(scratch, dir_fd=directory)
                except FileNotFoundError:
                    pass
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
                try:
                    os.close(fd)
                except BaseException as cleanup:
                    cleanup_errors.append(cleanup)
        finally:
            try:
                os.close(directory)
            except BaseException as cleanup:
                cleanup_errors.append(cleanup)
        if cleanup_errors:
            if error is not None:
                for cleanup in cleanup_errors:
                    error.add_note("Own new scratch cleanup: " + repr(cleanup))
            else:
                raise cleanup_errors[0]


def xz_header_checked(data):
    # The standard-library decoder verifies the complete XZ structure and Check.
    # No manual Block/Index/Footer parser or single-block restriction is added.
    require(len(data) >= 12 and data[:8] == b"\xfd7zXZ\x00\x00\x0a",
            "EXACT_XZ_SHA256_HEADER_REQUIRED")


def encode(source, destination, reference, request_id, check_remaining):
    require(os.getuid() == os.geteuid() and os.geteuid() in (0, 1000),
            "ENCODER_ROOT0_OR_UID1000_REQUIRED")
    reference = reference_checked(reference)
    uuid_text(request_id)
    require(str(Path(source)) == reference["original_path"], "EXACT_SOURCE_PATH_REQUIRED")
    require(Path(source) != Path(destination), "DISTINCT_NEW_ENVELOPE_REQUIRED")
    recovery_boot = uuid_text(Path("/proc/sys/kernel/random/boot_id").read_text().strip())
    if reference["origin"] == "ACTUAL_RETAINED_R8":
        require(recovery_boot != reference["original_boot_id"], "RECOVERY_BOOT_MUST_BE_SEPARATE")
        require(str(Path(destination)) == str(Path(reference["original_path"]).with_name(
            "GB2026111112-transfer.json")), "EXACT_NEW_TRANSPORT_ENVELOPE_PATH_REQUIRED")
    check_remaining()
    directory, fd, before = regular_open(source, MAX_ORIGINAL)
    observed_before = utc()
    try:
        require(before.st_size == reference["original_bytes"], "EXACT_SOURCE_SIZE_REQUIRED")
        compressor = lzma.LZMACompressor(format=lzma.FORMAT_XZ, check=lzma.CHECK_SHA256,
                                       filters=[{"id": lzma.FILTER_LZMA2, "dict_size": DICTIONARY}])
        pieces, compressed_size, total, digest = [], 0, 0, hashlib.sha256()
        while True:
            check_remaining()
            raw = os.read(fd, CHUNK)
            if not raw:
                break
            total += len(raw)
            require(total <= reference["original_bytes"], "SOURCE_GREW_DURING_READ")
            digest.update(raw)
            part = compressor.compress(raw)
            compressed_size += len(part)
            if compressed_size > MAX_COMPRESSED:
                raise EnvelopeTooLarge("COMPRESSED_TRANSPORT_INPUT_CEILING")
            pieces.append(part)
        check_remaining()
        part = compressor.flush()
        pieces.append(part)
        compressed_size += len(part)
        if compressed_size > MAX_COMPRESSED:
            raise EnvelopeTooLarge("COMPRESSED_TRANSPORT_INPUT_CEILING")
        after = finish_input(source, directory, fd, before)
        observed_after = utc()
        require(total == reference["original_bytes"] and digest.hexdigest()
                == reference["original_sha256"], "EXACT_SOURCE_SHA256_REQUIRED")
    finally:
        os.close(fd)
        os.close(directory)
    compressed = b"".join(pieces)
    xz_header_checked(compressed)
    envelope = {"schema": SCHEMA, "hash_algorithm": "sha256", "algorithm": ALGORITHM,
                "encoder_uid": os.geteuid(),
                "reference": reference, "recovery_boot_id": recovery_boot,
                "collector_request_id": request_id,
                "source_before_observed_at": observed_before,
                "source_after_observed_at": observed_after,
                "source_before": identity(before), "source_after": identity(after),
                "compressed_bytes": len(compressed), "compressed_sha256": sha(compressed),
                "payload_base64": base64.b64encode(compressed).decode("ascii")}
    raw = canonical(envelope)
    if len(raw) > MAX_ENVELOPE:
        raise EnvelopeTooLarge("WHOLE_ENVELOPE_4MIB_CEILING")
    check_remaining()
    published = publish_new(destination, lambda out: write_all(out, raw), check_remaining)
    return {"schema": "byul-archive-envelope-encoded/1", "envelope_path": str(destination),
            "encoder_uid": os.geteuid(),
            "envelope_bytes": len(raw), "envelope_sha256": sha(raw),
            "published_identity": published, "reference": reference,
            "recovery_boot_id": recovery_boot, "collector_request_id": request_id,
            "original_source_modified": False, "original_ZIP_CRC_verified": False}


def duplicate_reject(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_JSON_FIELD")
        result[key] = value
    return result


def decode(envelope, destination, reference, expected_encode_receipt, check_remaining):
    require(os.getuid() == os.geteuid() == 1000, "UID1000_REQUIRED")
    reference = reference_checked(reference)
    receipt = expected_encode_receipt
    receipt_fields = {"schema", "envelope_path", "envelope_bytes", "envelope_sha256",
                      "published_identity", "reference", "recovery_boot_id", "collector_request_id",
                      "original_source_modified", "original_ZIP_CRC_verified", "encoder_uid"}
    require(type(receipt) is dict and set(receipt) == receipt_fields
            and receipt["schema"] == "byul-archive-envelope-encoded/1"
            and receipt["reference"] == reference
            and type(receipt["envelope_bytes"]) is int
            and 0 < receipt["envelope_bytes"] <= MAX_ENVELOPE
            and type(receipt["envelope_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", receipt["envelope_sha256"])
            and receipt["original_source_modified"] is False
            and receipt["original_ZIP_CRC_verified"] is False,
            "TRUSTED_EXACT_ENCODER_RECEIPT_REQUIRED")
    uuid_text(receipt["collector_request_id"])
    uuid_text(receipt["recovery_boot_id"])
    require(type(receipt["encoder_uid"]) is int and receipt["encoder_uid"] in (0, 1000),
            "ENCODER_UID_REQUIRED")
    require(type(receipt["envelope_path"]) is str
            and Path(receipt["envelope_path"]).is_absolute()
            and str(Path(receipt["envelope_path"])) == receipt["envelope_path"]
            and ".." not in Path(receipt["envelope_path"]).parts,
            "CANONICAL_ENCODER_OUTPUT_PATH_REQUIRED")
    published_input = receipt["published_identity"]
    require(type(published_input) is dict and set(published_input) == set(identity(os.stat("/"))),
            "ENCODER_PUBLISHED_IDENTITY_FIELDS_REQUIRED")
    for key in ("dev", "inode", "uid", "gid", "mode", "nlink", "bytes"):
        require(type(published_input[key]) is int and 0 <= published_input[key] < 2**64,
                "ENCODER_PUBLISHED_INTEGER_IDENTITY_REQUIRED")
    for key in ("mtime_ns", "ctime_ns"):
        require(type(published_input[key]) is str
                and re.fullmatch("0|[1-9][0-9]{0,29}", published_input[key]),
                "ENCODER_PUBLISHED_DECIMAL_TIME_REQUIRED")
    require(published_input["uid"] == 1000 and published_input["mode"] == 0o600
            and published_input["nlink"] == 1
            and published_input["bytes"] == receipt["envelope_bytes"],
            "ENCODER_PUBLISHED_IDENTITY_MISMATCH")
    if reference["origin"] == "ACTUAL_RETAINED_R8":
        require(receipt["envelope_path"] == str(Path(reference["original_path"]).with_name(
            "GB2026111112-transfer.json")), "EXACT_ENCODER_OUTPUT_PATH_REQUIRED")
    require(Path(envelope) != Path(destination)
            and Path(destination) != Path(reference["original_path"]), "DISTINCT_NEW_DESTINATION_REQUIRED")
    check_remaining()
    directory, fd, before = regular_open(envelope, MAX_ENVELOPE)
    try:
        pieces, count = [], 0
        while True:
            check_remaining()
            block = os.read(fd, CHUNK)
            if not block:
                break
            count += len(block)
            require(count <= MAX_ENVELOPE, "ENVELOPE_GREW_DURING_READ")
            pieces.append(block)
        finish_input(envelope, directory, fd, before)
    finally:
        os.close(fd)
        os.close(directory)
    raw = b"".join(pieces)
    require(len(raw) == receipt["envelope_bytes"] and sha(raw) == receipt["envelope_sha256"],
            "EXACT_DELIVERED_ENVELOPE_BYTES_SHA256_REQUIRED")
    value = json.loads(raw.decode("ascii"), object_pairs_hook=duplicate_reject,
                       parse_constant=lambda _: (_ for _ in ()).throw(CodecError("NONFINITE_JSON")))
    require(type(value) is dict and canonical(value) == raw, "CANONICAL_SINGLE_JSON_OBJECT_REQUIRED")
    fields = {"schema", "hash_algorithm", "algorithm", "reference", "recovery_boot_id",
              "collector_request_id", "source_before_observed_at", "source_after_observed_at",
              "source_before", "source_after", "compressed_bytes", "compressed_sha256", "payload_base64",
              "encoder_uid"}
    require(set(value) == fields and value["schema"] == SCHEMA and value["hash_algorithm"] == "sha256"
            and value["algorithm"] == ALGORITHM and value["reference"] == reference,
            "EXACT_SCHEMA_AND_REFERENCE_REQUIRED")
    uuid_text(value["collector_request_id"])
    uuid_text(value["recovery_boot_id"])
    require(value["collector_request_id"] == receipt["collector_request_id"]
            and value["recovery_boot_id"] == receipt["recovery_boot_id"]
            and type(value["encoder_uid"]) is int and value["encoder_uid"] == receipt["encoder_uid"],
            "ENCODER_RECEIPT_METADATA_MIXING")
    if reference["origin"] == "ACTUAL_RETAINED_R8":
        require(value["recovery_boot_id"] != reference["original_boot_id"], "RECOVERY_BOOT_MUST_BE_SEPARATE")
    for key in ("source_before_observed_at", "source_after_observed_at"):
        require(type(value[key]) is str and len(value[key]) <= 40
                and datetime.datetime.fromisoformat(value[key]).utcoffset() == datetime.timedelta(0),
                "OBSERVED_UTC_TIMESTAMP_REQUIRED")
    source = value["source_before"]
    require(type(source) is dict and set(source) == set(identity(before))
            and source == value["source_after"], "EXACT_SOURCE_FD_IDENTITY_FIELDS_REQUIRED")
    for key in ("dev", "inode", "uid", "gid", "mode", "nlink", "bytes"):
        require(type(source[key]) is int and 0 <= source[key] < 2**64, "INTEGER_SOURCE_IDENTITY_REQUIRED")
    for key in ("mtime_ns", "ctime_ns"):
        require(type(source[key]) is str and re.fullmatch("0|[1-9][0-9]{0,29}", source[key]),
                "DECIMAL_SOURCE_TIMESTAMP_REQUIRED")
    require(source["uid"] == 1000 and source["mode"] == 0o600 and source["nlink"] == 1
            and source["bytes"] == reference["original_bytes"], "SOURCE_IDENTITY_REFERENCE_MISMATCH")
    size = value["compressed_bytes"]
    require(type(size) is int and 0 < size <= MAX_COMPRESSED, "COMPRESSED_SIZE_CEILING")
    require(type(value["compressed_sha256"]) is str
            and re.fullmatch("[0-9a-f]{64}", value["compressed_sha256"]), "COMPRESSED_SHA256_REQUIRED")
    payload = value["payload_base64"]
    require(type(payload) is str and len(payload) <= (MAX_COMPRESSED + 2) // 3 * 4,
            "BOUNDED_BASE64_REQUIRED")
    compressed = base64.b64decode(payload, validate=True)
    require(base64.b64encode(compressed).decode("ascii") == payload and len(compressed) == size
            and sha(compressed) == value["compressed_sha256"], "COMPRESSED_BYTES_SHA256_MISMATCH")
    xz_header_checked(compressed)
    result = {"bytes": 0, "sha256": None}

    def writer(out):
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=DECODE_MEMLIMIT)
        position, total, idle, digest = 0, 0, 0, hashlib.sha256()
        while not decompressor.eof:
            check_remaining()
            if decompressor.needs_input:
                require(position < len(compressed), "TRUNCATED_XZ_STREAM")
                block = compressed[position:position + CHUNK]
                position += len(block)
            else:
                block = b""
            decoded = decompressor.decompress(block, max_length=min(CHUNK, reference["original_bytes"] - total + 1))
            total += len(decoded)
            require(total <= reference["original_bytes"], "DECOMPRESSED_OUTPUT_CEILING")
            if decoded:
                digest.update(decoded)
                write_all(out, decoded)
            idle = idle + 1 if not block and not decoded else 0
            require(idle <= 2, "XZ_NO_PROGRESS")
        require(decompressor.check == lzma.CHECK_SHA256 and not decompressor.unused_data
                and position == len(compressed), "XZ_TRAILING_OR_CONCATENATED_STREAM")
        require(total == reference["original_bytes"] and digest.hexdigest()
                == reference["original_sha256"], "RESTORED_ORIGINAL_BYTES_SHA256_REQUIRED")
        check_remaining()
        result.update(bytes=total, sha256=digest.hexdigest())

    published = publish_new(destination, writer, check_remaining)
    return {"schema": "byul-archive-envelope-decoded/1", "envelope_bytes": len(raw),
            "envelope_sha256": sha(raw), "destination": str(destination),
            "published_identity": published, "restored": result, "reference": reference,
            "original_source_modified": False, "original_ZIP_CRC_verified": False}
