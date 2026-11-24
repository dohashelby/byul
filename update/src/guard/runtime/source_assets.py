"""Read exact pinned source text on the host."""

from hashlib import sha256
from pathlib import Path


def read_pinned_source(path: Path, expected_sha256: str | None, mismatch_code: str,
                       *, max_bytes: int | None = None) -> str:
    raw = path.read_bytes()
    if (max_bytes is not None and len(raw) > max_bytes) or sha256(raw).hexdigest() != expected_sha256:
        raise ValueError(mismatch_code)
    return raw.decode("utf-8")
