"""Linux owned-container Unix socket metadata/zero-payload connection probe.

This helper never sends bytes, opens SHM, parses IPC messages or deserializes data.
Paths are retained only in memory; output uses the parent-provided index.
"""
from __future__ import annotations

import argparse
import errno
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
from itertools import islice


MAX_PATHS = 8


def _stat_socket(path):
    p = Path(path)
    if not p.is_absolute() or ".." in p.parts or len(os.fsencode(path)) > 100:
        raise ValueError("unsupported socket path")
    for parent in [*reversed(p.parents), p]:
        item = parent.lstat()
        if stat.S_ISLNK(item.st_mode):
            raise ValueError("symlink path")
    item = p.lstat()
    if not stat.S_ISSOCK(item.st_mode):
        raise ValueError("not a socket")
    return {"device": item.st_dev, "inode": item.st_ino, "uid": item.st_uid,
            "gid": item.st_gid, "mode": stat.S_IMODE(item.st_mode),
            "ctime_ns": item.st_ctime_ns}


def _namespaces():
    return {name: os.readlink(f"/proc/self/ns/{name}") for name in ("mnt", "net", "pid", "user")}


def inspect_paths(paths):
    rows = []
    deadline = time.monotonic() + 10
    for index, path in enumerate(paths):
        row = {"index": index, "complete": False}
        try:
            before = _stat_socket(path)
            with Path("/proc/net/unix").open() as stream:
                table = stream.read(524289)
            if len(table) > 524288:
                raise ValueError("socket table bound")
            matches = [line.split() for line in table.splitlines()[1:]
                       if len(line.split()) == 8 and line.split()[7] == path]
            if len(matches) != 1:
                raise ValueError("ambiguous socket")
            inode = matches[0][6]
            owners = []
            processes = list(islice(Path("/proc").iterdir(), 4097))
            if len(processes) > 4096:
                raise ValueError("process bound")
            for proc in processes:
                if time.monotonic() >= deadline:
                    raise TimeoutError("metadata deadline")
                if not proc.name.isdigit():
                    continue
                try:
                    descriptors = list(islice((proc / "fd").iterdir(), 4097))
                    if len(descriptors) > 4096:
                        raise ValueError("descriptor bound")
                    if not any(fd.is_symlink() and os.readlink(fd) == f"socket:[{inode}]" for fd in descriptors):
                        continue
                    state = (proc / "stat").read_text()
                    start = state.rsplit(")", 1)[1].split()[19]
                    uid_line = next(line for line in (proc / "status").read_text().splitlines() if line.startswith("Uid:"))
                    owners.append({"pid": int(proc.name), "start": start, "uid": int(uid_line.split()[2])})
                    if len(owners) > 16:
                        raise ValueError("owner bound")
                except (FileNotFoundError, ProcessLookupError):
                    continue  # another process may exit; endpoint owners are checked again below
            if not owners or _stat_socket(path) != before:
                raise ValueError("owner unavailable or path changed")
            row.update(complete=True, identity=before, socket_inode=inode, owners=sorted(owners, key=lambda v: v["pid"]))
        except (OSError, ValueError, StopIteration, IndexError):
            row["error"] = "IPC_METADATA_UNAVAILABLE"
        rows.append(row)
    return {"schema": "guard-unix-ipc-probe/1", "mode": "inspect", "uid": os.geteuid(),
            "namespaces": _namespaces(), "rows": rows}


def connect_paths(paths, timeout):
    rows = []
    deadline = time.monotonic() + 10
    for index, path in enumerate(paths):
        row = {"index": index, "outcome": "unknown"}
        try:
            if time.monotonic() >= deadline:
                raise TimeoutError("connection deadline")
            before = _stat_socket(path)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
                peer.settimeout(min(timeout, max(0.001, deadline - time.monotonic())))
                peer.connect(path)  # zero payload: never send a command or material
                pid, uid, gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                row.update(outcome="connected", peer={"pid": pid, "uid": uid, "gid": gid})
            row["identity"] = before
            row["path_stable"] = _stat_socket(path) == before
        except OSError as error:
            row.update(outcome="denied" if error.errno in (errno.EACCES, errno.EPERM) else "unknown")
        except ValueError:
            pass
        rows.append(row)
    return {"schema": "guard-unix-ipc-probe/1", "mode": "connect", "uid": os.geteuid(),
            "groups": os.getgroups(), "namespaces": _namespaces(), "rows": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "connect"))
    parser.add_argument("paths", help="explicit JSON list; no discovery or shell expansion")
    parser.add_argument("--timeout", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        paths = json.loads(args.paths)
        if (type(paths) is not list or not 1 <= len(paths) <= MAX_PATHS
                or any(type(p) is not str or not p.startswith("/") or "\x00" in p for p in paths)
                or len(paths) != len(set(paths)) or not 0 < args.timeout <= 2):
            raise ValueError("invalid bounded input")
        result = inspect_paths(paths) if args.mode == "inspect" else connect_paths(paths, args.timeout)
    except (OSError, ValueError):
        print('{"schema":"guard-unix-ipc-probe/1","error":"IPC_PROBE_INPUT_OR_SOURCE_FAILED"}')
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
