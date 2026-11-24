"""Share exact submitted records after each check's execution recipe is verified."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

MAX_RECORD_BYTES = 8 * 1024**2
_active = ContextVar("guard_verified_observations", default=None)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def path_identity(path):
    try:
        return identity(path.stat())
    except OSError:
        return None


@dataclass(frozen=True)
class VerifiedRecord:
    sha256: str | None
    raw: bytes | None = None
    identity: tuple[int, ...] | None = None


@contextmanager
def bind(verified, decoded):
    retained = {path for path, entry in verified.items() if entry.raw is not None}
    for path in decoded.keys() - retained:
        del decoded[path]
    token = _active.set((verified, decoded))
    try:
        yield
    finally:
        _active.reset(token)


def recipe_record(path: Path) -> VerifiedRecord | None:
    scope = _active.get()
    return scope[0].get(path) if scope is not None else None


def load_verified(path: Path, before, source: VerifiedRecord, *, decode):
    if (source.raw is None or len(source.raw) != before.st_size
            or identity(before) != source.identity or path_identity(path) != source.identity):
        return None
    _, decoded = _active.get()
    # Cache parsing separately from admission. Every check must supply the
    # exact bytes that passed its own recipe and file identity checks.
    previous = decoded.get(path)
    if previous is None or previous[0] != source.raw or previous[1] is not decode:
        previous = (source.raw, decode, decode(source.raw, limit=MAX_RECORD_BYTES))
        decoded[path] = previous
    return deepcopy(previous[2]), source.sha256
