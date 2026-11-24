"""Read a bounded local file and verify its digest and file identity."""
from __future__ import annotations

import os
import stat

from guard.models import sha256_hex


def read_pinned_bytes(path, before_path, *, limit, expected_sha256, identity_fields,
                      bound_error, mismatch_error, open_file=None, match_before_read=False):
    def identity(value):
        return {field: getattr(value, 'st_' + field) for field in identity_fields}

    path_identity = identity(before_path)
    stream = path.open('rb') if open_file is None else open_file(path)
    with stream:
        before = os.fstat(stream.fileno())
        fd_identity = identity(before)
        if (not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit
                or (match_before_read and path_identity != fd_identity)):
            raise ValueError(bound_error)
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    if (not (path_identity == fd_identity == identity(after) == identity(path.lstat()))
            or len(raw) != before.st_size or sha256_hex(raw) != expected_sha256):
        raise ValueError(mismatch_error)
    return raw, fd_identity
