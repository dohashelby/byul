"""Validation and presentation of received transport evidence."""

from copy import deepcopy

from guard.models import sha256_hex


def complete_body(record) -> bytes | None:
    text = record.get("body")
    if type(text) is not str:
        return None
    raw = text.encode()
    if (len(raw) > 128 * 1024 or record.get("capture_complete") is not True
            or record.get("eof") is not True or record.get("utf8_valid") is not True
            or record.get("body_truncated") is not False or type(record.get("body_bytes")) is not int
            or record["body_bytes"] != len(raw) or record.get("body_sha256") != sha256_hex(raw)):
        return None
    return raw


def lifecycle_http_report(profile, transport):
    return {
        "profile": profile, "target_sha256": transport.target_sha256,
        "binding": deepcopy(transport.binding), "environment_ref": deepcopy(transport.base.environment_ref),
        "probe_source_sha256": transport.base.source_sha256, "requests": deepcopy(transport.rows),
        "collection_gap": transport.failed or bool(transport.base.cleanup_errors),
        "cleanup_errors": list(transport.base.cleanup_errors),
        "faults_injected": False, "producer_authenticated": False,
    }
