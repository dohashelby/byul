"""Read a local regular file's byte count and SHA-256 without changing it."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat


@dataclass(frozen=True)
class FileInfo:
    size_bytes: int
    sha256: str


def inspect_file(path: str | Path) -> FileInfo:
    # Nonblocking open lets us reject FIFOs without waiting for a writer.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("일반 파일만 확인할 수 있습니다.")
        digest = hashlib.sha256()
        size = 0
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or size != after.st_size
        ):
            raise OSError("읽는 동안 파일이 변경됐습니다. 파일을 고정한 뒤 다시 확인하세요.")
    finally:
        os.close(descriptor)
    return FileInfo(size_bytes=size, sha256=digest.hexdigest())
