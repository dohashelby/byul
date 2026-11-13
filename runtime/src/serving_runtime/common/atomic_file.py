"""Atomic regular-file writes with the callers' existing durability choices."""

from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from typing import Any, Callable


@dataclass(frozen=True)
class AtomicWriteOptions:
    create_mode: int = 0o600
    final_mode: int | None = None
    chmod_after_write: bool = False
    fsync_dir: bool = False
    directory_flags: int = os.O_RDONLY
    mkstemp: bool = False
    text: bool = False
    cleanup: bool = True
    close_unowned_descriptor: bool = True


def write_atomic(
    path: Path,
    data: bytes | str | None = None,
    *,
    temporary: Path | None = None,
    options: AtomicWriteOptions = AtomicWriteOptions(),
    writer: Callable[[Any], object] | None = None,
) -> None:
    path = Path(path)
    if options.mkstemp:
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(temporary_name)
    else:
        if temporary is None:
            raise ValueError("an explicit temporary path is required")
        temporary = Path(temporary)
        descriptor = -1
    try:
        if not options.mkstemp:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, options.create_mode)
        if options.final_mode is not None and not options.chmod_after_write:
            os.fchmod(descriptor, options.final_mode)
        with os.fdopen(descriptor, "w" if options.text else "wb") as output:
            descriptor = -1
            if writer is None:
                output.write(data)
            else:
                writer(output)
            if options.final_mode is not None and options.chmod_after_write:
                os.fchmod(output.fileno(), options.final_mode)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        if options.fsync_dir:
            directory_fd = os.open(path.parent, options.directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0 and options.close_unowned_descriptor:
            os.close(descriptor)
        if options.cleanup:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
