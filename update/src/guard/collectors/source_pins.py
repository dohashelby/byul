"""Verify and identify packaged source files used for recorded evidence."""

from hashlib import sha256
from pathlib import Path

from guard.models import sha256_hex


def verify_adapter_source_pins(collector: Path, pins: dict[str, str], mismatch_code: str) -> None:
    adapters = collector.resolve().parents[1] / "adapters"
    try:
        matched = all(sha256_hex((adapters / name).read_bytes()) == expected for name, expected in pins.items())
    except OSError:
        matched = False
    if not matched:
        raise ValueError(mismatch_code)


def producer_source_sha256(*paths: str) -> str:
    """Hash ordered sources, including the implementation of this framing."""
    digest = sha256()
    for filename in (*paths, __file__):
        path = Path(filename)
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()
