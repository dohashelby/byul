"""LIF14-only regular-file trace collision and non-destructive recovery.

The engine is already deployed with /guard-profiler-traces/<prepared session>.
No profiler or engine is started here. Recovery retains the original marker.
"""
import hashlib
import ctypes
import json
import os
from pathlib import Path
import re
import stat
import sys

SCHEMA = 'guard-owned-profiler-trace-fault/1'
ROOT = '/guard-profiler-traces'


def identity(info):
    return [info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_size]


def marker(session):
    return ('guard-owned-profiler-stop-fault/1:' + session + '\n').encode()


def apply_fixture(root, session, action, receipt=None):
    """Exact session marker only; never remove a directory or foreign file."""
    if (type(session) is not str or re.fullmatch('[0-9a-f]{32}', session) is None
            or action not in {'arm', 'recover'}):
        raise ValueError('PROFILER_TRACE_FIXTURE_INVALID')
    root = Path(root)
    if not root.is_absolute() or any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('PROFILER_TRACE_ROOT_UNVERIFIED')
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    parent = os.open(root, flags)
    try:
        owner = os.fstat(parent)
        if not stat.S_ISDIR(owner.st_mode) or owner.st_uid != os.geteuid() or owner.st_mode & 0o022:
            raise ValueError('PROFILER_TRACE_ROOT_UNVERIFIED')
        expected = marker(session)
        digest = hashlib.sha256(expected).hexdigest()
        result = {'schema': SCHEMA, 'session': session, 'action': action,
            'root_identity': identity(owner)[:4], 'marker_sha256': digest,
            'fault_activation_verified': False, 'recovery_verified': False}
        if action == 'arm':
            fd = os.open(session, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                if os.write(fd, expected) != len(expected):
                    raise ValueError('PROFILER_TRACE_MARKER_WRITE_FAILED')
                os.fsync(fd)
                result['marker_identity'] = identity(os.fstat(fd))
            finally:
                os.close(fd)
            result['fault_activation_verified'] = True
            return result
        if (type(receipt) is not dict or receipt.get('schema') != SCHEMA
                or receipt.get('action') != 'arm' or receipt.get('session') != session
                or receipt.get('root_identity') != result['root_identity']
                or receipt.get('marker_sha256') != digest or receipt.get('fault_activation_verified') is not True):
            raise ValueError('PROFILER_TRACE_RECOVERY_RECEIPT_INVALID')

        def check(name):
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or identity(before) != receipt.get('marker_identity')
                        or before.st_size != len(expected) or os.read(fd, len(expected) + 1) != expected
                        or identity(os.fstat(fd)) != identity(before)):
                    raise ValueError('PROFILER_TRACE_MARKER_REPLACED')
            finally:
                os.close(fd)
        check(session)
        # Move the actual entry without overwriting any existing destination.
        # A raced replacement is retained, never unlinked or overwritten.
        saved = session + '.fault-record'
        rename = ctypes.CDLL(None, use_errno=True).renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(parent, session.encode(), parent, saved.encode(), 1) != 0:
            raise OSError(ctypes.get_errno(), 'PROFILER_TRACE_QUARANTINE_UNAVAILABLE')
        try:
            check(saved)
        except Exception:
            try:
                os.link(saved, session, src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
            except OSError:
                pass
            raise
        os.mkdir(session, 0o700, dir_fd=parent)
        folder = os.stat(session, dir_fd=parent, follow_symlinks=False)
        result.update(marker_identity=receipt['marker_identity'], directory_identity=identity(folder)[:4],
            recovery_verified=True, original_marker_retained=True)
        return result
    finally:
        os.close(parent)


def main():
    try:
        if len(sys.argv) != 3:
            raise ValueError()
        raw = sys.stdin.buffer.read(4097)
        if len(raw) > 4096:
            raise ValueError()
        receipt = json.loads(raw) if raw else None
        result = apply_fixture(ROOT, sys.argv[1], sys.argv[2], receipt)
    except Exception:
        print(json.dumps({'schema': SCHEMA, 'available': False, 'diagnostic': 'PROFILER_TRACE_FIXTURE_UNAVAILABLE'}))
        return 1
    print(json.dumps(result, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
