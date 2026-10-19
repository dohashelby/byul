"""SPEC §2.3 해시 규칙."""

import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> bytes:
    """정렬된 키·UTF-8·공백 없는 JSON."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_sha256(obj: dict) -> str:
    """`content_sha256` 필드를 뺀 객체의 해시."""
    body = {k: v for k, v in obj.items() if k != "content_sha256"}
    return sha256_hex(canonical_json(body))
