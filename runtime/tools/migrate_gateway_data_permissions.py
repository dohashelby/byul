"""Audit Gateway JSONL permissions; change files only with --fix-permissions.

Run with the Gateway stopped. Only immediate *.jsonl children are inspected;
no contents, absolute paths, symlink targets or exception text are printed.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys


def migrate(data_dir: Path, *, owner_uid: int = 10000, fix: bool = False) -> bool:
    """Return whether all journals are safe; never change ownership/directories."""
    directory = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        parent = os.fstat(directory)
        if parent.st_uid != owner_uid or stat.S_IMODE(parent.st_mode) & 0o022:
            raise PermissionError("unsafe data directory")
        safe = True
        for name in sorted(os.listdir(directory)):
            if not name.endswith(".jsonl"):
                continue
            # JSON quoting prevents control characters in names becoming output.
            label = json.dumps(name, ensure_ascii=True)
            descriptor = -1
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                info = os.fstat(descriptor)
                current = os.stat(name, dir_fd=directory, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or info.st_nlink != 1
                        or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
                    raise PermissionError("unsafe journal identity")
                if stat.S_IMODE(info.st_mode) & ~0o600:
                    if fix:
                        os.fchmod(descriptor, 0o600)
                        os.fsync(descriptor)
                        print(f"fixed: {label}")
                    else:
                        print(f"excess_permissions: {label}")
                        safe = False
            except OSError:
                print(f"manual_review: {label}")
                safe = False
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
        return safe
    finally:
        os.close(directory)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--owner-uid", type=int, default=10000)
    parser.add_argument("--fix-permissions", action="store_true")
    args = parser.parse_args(argv)
    if args.owner_uid < 0:
        parser.error("owner UID must be non-negative")
    try:
        return 0 if migrate(args.data_dir, owner_uid=args.owner_uid, fix=args.fix_permissions) else 1
    except OSError:
        print("data_directory_unavailable: 소유자·쓰기 권한·접근 권한을 확인하세요", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
