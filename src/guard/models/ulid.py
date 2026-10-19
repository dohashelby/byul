"""표준 라이브러리 ULID (Crockford base32, 48bit ms 시각 + 80bit 난수)."""

import os
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(ms: int | None = None) -> str:
    ms = int(time.time() * 1000) if ms is None else ms
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    out = []
    for _ in range(26):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_id(kind: str) -> str:
    return f"{kind}-{new_ulid()}"
