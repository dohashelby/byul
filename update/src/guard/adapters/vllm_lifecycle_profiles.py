"""Known private recording versions; old assets are read, never executed here."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import vllm_lifecycle_sensor as sensor

LEGACY_PROFILE = "vllm-native-lifecycle-metadata/1"
LEGACY_SCHEMA = "guard-native-lifecycle-events/1"
SCHEMAS = {sensor.PROFILE: sensor.SCHEMA, LEGACY_PROFILE: LEGACY_SCHEMA}
# Exact source bytes from 5db08ab, including its known Windows checkout spelling.
LEGACY_ASSETS = {
    (31143, "50143b5cf54aa8f5bbcfef769c286f65c971e0acd2caa111c625583f4baec3de"),
    (31825, "812d512c75c15fb369e2546ef0a01c858e872ad0aaf033e146b3b36b5473d4c8"),
}


def asset_supported(profile, raw):
    if profile == LEGACY_PROFILE:
        return (len(raw), hashlib.sha256(raw).hexdigest()) in LEGACY_ASSETS
    if profile == sensor.PROFILE:
        return raw == Path(sensor.__file__).read_bytes()
    return False
