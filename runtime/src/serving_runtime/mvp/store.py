"""Durable JSON Lines storage for MVP observations."""

import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
import threading
from typing import Any, Collection
from uuid import uuid4


_PATH_LOCKS: dict[str, threading.Lock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    # Several modules open their own JsonlStore on the same file. One lock per
    # path keeps a retention rewrite from racing their appends in this process.
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = _PATH_LOCKS[key] = threading.Lock()
        return lock


class RewriteCommittedError(OSError):
    """A rewrite replaced the target, but its directory sync did not finish."""

    def __init__(self, *, kept: int, removed: int, cause: OSError) -> None:
        super().__init__("rewrite committed but directory durability is uncertain")
        self.kept = kept
        self.removed = removed
        self.cause_type = type(cause).__name__


class JsonlStore:
    """Append dictionaries as durable UTF-8 JSON Lines records."""

    def __init__(self, path: str | Path, *, allowed_reader_gids: Collection[int] = ()) -> None:
        self.path = Path(path)
        self._lock = _lock_for(self.path)
        if any(type(gid) is not int or gid < 0 for gid in allowed_reader_gids):
            raise ValueError("reader groups must be non-negative integer GIDs")
        self._allowed_reader_gids = frozenset(allowed_reader_gids)

    @contextmanager
    def _open(self, flags: int, *, create: bool = False):
        """Pin the containing directory and reject unsafe existing files.

        Existing directories and files are never chmod'ed. Shared read access
        requires an explicit reader GID; write access belongs to the writer UID.
        """
        if create:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptor = -1
        try:
            parent = os.fstat(directory)
            if parent.st_uid != os.geteuid() or stat.S_IMODE(parent.st_mode) & 0o022:
                raise PermissionError("record directory owner or write permissions are unsafe")
            descriptor = os.open(
                self.path.name,
                flags | os.O_NOFOLLOW | os.O_NONBLOCK | (os.O_CREAT if create else 0),
                0o600,
                dir_fd=directory,
            )
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or mode & 0o137
                    or (mode & 0o040 and info.st_gid not in self._allowed_reader_gids)):
                raise PermissionError("record file owner or reader permissions are unsafe")
            try:
                current = os.stat(self.path.name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                raise OSError('record file disappeared after opening') from None
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise PermissionError("record file changed while opening")
            with os.fdopen(descriptor, "ab" if flags & os.O_APPEND else "rb") as stream:
                descriptor = -1
                yield stream
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            os.close(directory)

    def check_writable(self) -> None:
        """Use the append boundary even when preflighting an empty journal."""
        with self._lock, self._open(os.O_WRONLY | os.O_APPEND, create=True):
            pass

    def append(self, record: dict[str, Any]) -> None:
        encoded = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        with self._lock:
            with self._open(os.O_WRONLY | os.O_APPEND, create=True) as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())

    def read_all(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        corrupt: list[dict[str, Any]] = []
        with self._lock:
            try:
                with self._open(os.O_RDONLY) as source:
                    for line_number, raw_line in enumerate(source, start=1):
                        try:
                            document = json.loads(raw_line.decode("utf-8"))
                            if not isinstance(document, dict):
                                raise ValueError("record is not a JSON object")
                        except (
                            UnicodeDecodeError,
                            json.JSONDecodeError,
                            ValueError,
                            RecursionError,
                        ) as error:
                            corrupt.append({"line": line_number, "reason": str(error)})
                            continue
                        records.append(document)
            except FileNotFoundError:
                return [], []
        return records, corrupt

    def read_recent(self, *, max_bytes: int = 4 * 1024 * 1024, max_records: int = 4096) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        """Read only a protected stable tail; omitted history remains explicit."""
        if (type(max_bytes) is not int or not 0<max_bytes<=4*1024*1024
                or type(max_records) is not int or not 0<max_records<=4096):
            raise ValueError('bounded journal tail limits required')
        with self._lock:
            opened = False
            try:
                with self._open(os.O_RDONLY) as source:
                    opened = True
                    before=os.fstat(source.fileno());offset=max(0,before.st_size-max_bytes)
                    source.seek(offset);raw=source.read(max_bytes+1)
                    after=os.fstat(source.fileno());current=os.stat(self.path,follow_symlinks=False)
                    identity=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_uid,v.st_gid,v.st_mode)
                    if len(raw)>max_bytes or len(raw)!=before.st_size-offset or identity(before)!=identity(after) or identity(after)!=identity(current):
                        raise OSError('record journal changed during bounded read')
            except FileNotFoundError:
                if opened:
                    raise OSError('record journal disappeared during bounded read') from None
                return [],[],False
        truncated=offset>0;corrupt=[]
        if offset:
            boundary=raw.find(b'\n');raw=raw[boundary+1:] if boundary>=0 else b''
        if raw and not raw.endswith(b'\n'):
            boundary=raw.rfind(b'\n');raw=raw[:boundary+1] if boundary>=0 else b''
            truncated=True;corrupt.append({'line':None,'reason':'incomplete trailing JSONL record'})
        # Bound list allocation too: millions of tiny lines must not produce
        # millions of Python objects before the row ceiling is applied.
        parts=raw.rsplit(b'\n',max_records+1)[:-1]
        if len(parts)>max_records:parts=parts[-max_records:];truncated=True
        lines=[part+b'\n' for part in parts]
        rows=[]
        for index,line in enumerate(lines):
            try:
                value=json.loads(line.decode('utf-8'))
                if type(value) is not dict:raise ValueError('record is not a JSON object')
                rows.append(value)
            except (UnicodeDecodeError,ValueError,RecursionError) as error:
                corrupt.append({'line':None if truncated else index+1,'reason':type(error).__name__})
        return rows,corrupt,truncated

    def read_matching_request(self, request_id: str):
        """Bounded-memory reverse search of retained original request rows.

        A complete small tail is authoritative. Larger journals are searched
        newest-first within64MiB/65536rows, keeping at most128rows/8MiB for
        this UUID. Omitted/corrupt ranges cannot prove absence.
        """
        recent, corrupt, truncated = self.read_recent()
        if not truncated:
            return [r for r in recent if r.get('request_id') == request_id], corrupt, False
        rows, corrupt = [], []
        selected_bytes = 0
        scanned = 0
        limited = False
        stopped = False
        line_limit = 8 * 1024 * 1024

        def accept(raw):
            nonlocal scanned, selected_bytes, limited, stopped
            scanned += 1
            try:
                value = json.loads(raw.decode('utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('record is not an object')
            except (UnicodeError, ValueError, RecursionError):
                if len(corrupt) < 20:
                    corrupt.append({'line': None, 'reason': 'invalid record in bounded request search'})
            else:
                if value.get('request_id') == request_id:
                    if len(rows) >= 128 or selected_bytes + len(raw) > line_limit:
                        limited = True
                    else:
                        rows.append(value)
                        selected_bytes += len(raw)
            if scanned >= 65536:
                stopped = True
                limited = True

        with self._lock:
            opened = False
            try:
                with self._open(os.O_RDONLY) as stream:
                    opened = True
                    before = os.fstat(stream.fileno())
                    cursor = before.st_size
                    earliest = max(0, cursor - 64 * 1024 * 1024)
                    limited = earliest > 0
                    buffer = b''
                    end_pending = True
                    while cursor > earliest and not stopped:
                        length = min(65536, cursor - earliest)
                        cursor -= length
                        stream.seek(cursor)
                        block = stream.read(length)
                        if len(block) != length:
                            raise OSError('short request journal read')
                        parts = (block + buffer).split(b'\n')
                        buffer = parts[0]
                        for raw in reversed(parts[1:]):
                            if end_pending:
                                end_pending = False
                                if raw:
                                    limited = True
                                    corrupt.append({'line': None, 'reason': 'incomplete trailing record'})
                                continue
                            if len(raw) > line_limit:
                                limited = True
                                if len(corrupt) < 20:
                                    corrupt.append({'line': None, 'reason': 'oversized request record'})
                            else:
                                accept(raw)
                            if stopped:
                                break
                        if len(buffer) > line_limit:
                            limited = True
                            stopped = True
                    if not stopped and earliest == 0:
                        if end_pending and buffer:
                            limited = True
                            corrupt.append({'line': None, 'reason': 'incomplete trailing record'})
                        elif not end_pending:
                            accept(buffer)
                    after = os.fstat(stream.fileno())
                    current = os.stat(self.path, follow_symlinks=False)
                    identity = lambda v: (v.st_dev, v.st_ino, v.st_size, v.st_mtime_ns,
                                          v.st_ctime_ns, v.st_uid, v.st_gid, v.st_mode)
                    if identity(before) != identity(after) or identity(after) != identity(current):
                        raise OSError('journal changed during request search')
            except FileNotFoundError:
                if opened:
                    raise OSError('record journal disappeared during request search') from None
                return [], [], False
        rows.reverse()
        return rows, corrupt[:20], limited

    def remove_lines(self, line_numbers: Collection[int]) -> tuple[int, int]:
        """Rewrite the file without the given 1-based lines; return (kept, removed).

        Lines appended after the caller chose ``line_numbers`` are kept, because
        existing line numbers do not move when new lines are appended.
        """

        drop = set(line_numbers)
        with self._lock:
            if not drop:
                return -1, 0
            temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
            kept = removed = 0
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            replaced = False
            try:
                with os.fdopen(descriptor, "wb") as output, self._open(os.O_RDONLY) as source:
                    descriptor = -1
                    original = os.fstat(source.fileno())
                    if os.fstat(output.fileno()).st_gid != original.st_gid:
                        os.fchown(output.fileno(), -1, original.st_gid)
                    os.fchmod(output.fileno(), stat.S_IMODE(original.st_mode))
                    for line_number, raw_line in enumerate(source, start=1):
                        if line_number in drop:
                            removed += 1
                            continue
                        output.write(raw_line)
                        kept += 1
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
                replaced = True
                try:
                    directory = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
                except OSError as error:
                    # The visible file has already changed. Preserve the actual
                    # counts so callers do not report this as a zero-row delete.
                    raise RewriteCommittedError(
                        kept=kept, removed=removed, cause=error
                    ) from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if not replaced:
                    try:
                        temporary.unlink()
                    except FileNotFoundError:
                        pass
            return kept, removed
