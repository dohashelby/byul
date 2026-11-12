"""Selected original helper functions; remote execution is a fake-SDK seam."""
from __future__ import annotations

import argparse

import base64

from concurrent.futures import ThreadPoolExecutor, as_completed

import hashlib

import json

import os

from pathlib import Path

import re

import shlex

import stat

import sys

import threading

import time

import uuid

CHUNK = 12_000

WORKERS = 1

GET_WORKERS = 4

GET_NAME = re.compile(r"GB[0-9]{2,}-(?:original-evidence\.zip|transfer\.json)\Z")

COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

SENSITIVE_COMPONENT = re.compile(r"(?:^|[-_.])(?:key|keys|settings|secret|credentials?|kubeconfig)(?:$|[-_.])", re.I)

TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut", "Delivery Timed Out", "Execution Timed Out", "Terminated", "Undeliverable"}

INSTANCE='EXAMPLE_NOT_A_REAL_NODE'
REMOTE_SOURCE='EXAMPLE_REMOTE_SOURCE_NOT_EXECUTED'

def put_name(relative: str) -> list[str]:
    parts = relative.split("/")
    if not parts or any(p in (".", "..") or not COMPONENT.fullmatch(p) for p in parts):
        raise ValueError("put path must be relative safe components")
    name = parts[-1].lower()
    if any(bad in name for bad in ("key", "secret", "credential", "kubeconfig", "settings", ".pem", ".env")):
        raise ValueError("sensitive file name is forbidden")
    if not (name.endswith(".py") or name.endswith(".zip") or name.endswith(".tar.gz") or name.endswith(".whl")):
        raise ValueError("put accepts prepared code or bundle files only")
    return parts

def get_name(relative: str) -> str:
    if not GET_NAME.fullmatch(relative):
        raise ValueError("get accepts only GBxx-original-evidence.zip or GBxx-transfer.json at base root")
    return relative

def local_source(path: Path):
    put_name(path.name)
    parent, name = checked_local_parent(path)
    try:
        st = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("source must be a regular file, not symlink")
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    st2 = os.fstat(fd)
    if not stat.S_ISREG(st2.st_mode) or (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) != (st2.st_dev, st2.st_ino, st2.st_size, st2.st_mtime_ns):
        os.close(fd)
        raise ValueError("source changed before opening")
    return fd, st2

def checked_local_parent(path: Path):
    """Return an open parent descriptor only for permitted non-symlink paths."""
    raw = os.fspath(path)
    if ".." in raw.split(os.sep):
        raise ValueError("parent traversal forbidden")
    # getcwd() yields the physical directory.  Joining without resolve() keeps
    # caller-supplied symlink components visible to the O_NOFOLLOW walk.
    absolute = path if path.is_absolute() else Path(os.getcwd()) / path
    parts = absolute.parts
    if not parts or any(
        part.lower() in {".aws", ".ssh", ".kube", ".env"}
        or SENSITIVE_COMPONENT.search(part)
        or part.lower().endswith((".pem", ".key"))
        for part in parts
    ):
        raise ValueError("credential or settings destination path forbidden")
    parent = parts[1:-1]
    fd = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in parent:
            next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise

def open_new_local(path: Path, *, readable: bool) -> int:
    """Create a new CloudShell file through no-follow directory descriptors."""
    fd, name = checked_local_parent(path)
    try:
        flags = (os.O_RDWR if readable else os.O_WRONLY) | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        result = os.open(name, flags, 0o600, dir_fd=fd)
        os.fchmod(result, 0o600)
        return result
    finally:
        os.close(fd)

def hash_fd(fd):
    h = hashlib.sha256()
    offset = 0
    while True:
        block = os.pread(fd, 1024 * 1024, offset)
        if not block:
            break
        h.update(block)
        offset += len(block)
    return h.hexdigest(), offset

class Transfer:
    def __init__(self, sdk, log: Path, *, poll=0.5, timeout=120):
        self.sdk, self.log, self.poll, self.timeout = sdk, log, poll, timeout
        self.events = []
        self._log_lock = threading.Lock()
        self._log_fd = open_new_local(self.log, readable=False)

    def event(self, data):
        with self._log_lock:
            self.events.append(data)
            line = (json.dumps(data, sort_keys=True) + "\n").encode()
            if os.write(self._log_fd, line) != len(line):
                raise OSError("short metadata log write")

    def close(self):
        fd = getattr(self, "_log_fd", None)
        if fd is not None:
            self._log_fd = None
            os.close(fd)

    def __del__(self):
        self.close()

    def call(self, args):
        # shlex quoting is only for fixed Python source and strict JSON data.
        encoded = base64.b64encode(json.dumps(args, separators=(",", ":")).encode()).decode()
        command = "python3 -I -S -B -c " + shlex.quote(REMOTE_SOURCE) + " " + shlex.quote(encoded)
        reply = self.sdk.send_command(InstanceIds=[INSTANCE], DocumentName="AWS-RunShellScript", Parameters={"commands": [command]}, TimeoutSeconds=120, Comment="GB binary file copy")
        command_id = reply["Command"]["CommandId"]
        start = time.monotonic()
        while True:
            try:
                result = self.sdk.get_command_invocation(CommandId=command_id, InstanceId=INSTANCE)
            except Exception as exc:
                # SendCommand is eventually consistent.  All other API failures remain visible.
                if type(exc).__name__ != "InvocationDoesNotExist" or time.monotonic() - start > self.timeout:
                    self.event({"op": args["op"], "command_id": command_id, "api_error": repr(exc)})
                    raise
                time.sleep(self.poll)
                continue
            status = result.get("StatusDetails", result.get("Status"))
            if status in TERMINAL:
                break
            if time.monotonic() - start > self.timeout:
                self.event({"op": args["op"], "command_id": command_id, "timeout": True, "status": status})
                raise TimeoutError(command_id)
            time.sleep(self.poll)
        stdout = result.get("StandardOutputContent", "")
        stderr = result.get("StandardErrorContent", "")
        event = {"op": args["op"], "command_id": command_id, "status": status, "response_code": result.get("ResponseCode"), "path": args["path"]}
        if "offset" in args:
            event["offset"] = args["offset"]
        if status != "Success" or result.get("ResponseCode") != 0:
            event["failure_stdout"] = stdout
            event["failure_stderr"] = stderr
            self.event(event)
            raise RuntimeError("SSM command failed; see private metadata log: " + command_id)
        self.event(event)
        try:
            return json.loads(stdout)
        except (ValueError, TypeError):
            self.event({"command_id": command_id, "unparseable_stdout_size": len(stdout), "unparseable_stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest()})
            raise

    def put(self, source: Path, remote: str):
        put_name(remote)
        fd, st = local_source(source)
        temp = "." + remote.split("/")[-1] + "." + uuid.uuid4().hex + ".part"
        try:
            digest, size = hash_fd(fd)
            if size != st.st_size or os.fstat(fd).st_mtime_ns != st.st_mtime_ns:
                raise ValueError("source changed during hash")
            self.call({"op": "init", "path": remote, "temp": temp, "size": size})
            def one(offset):
                data = os.pread(fd, min(CHUNK, size - offset), offset)
                if not data:
                    raise ValueError("source changed during transfer")
                out = self.call({"op": "write", "path": remote, "temp": temp, "size": size, "offset": offset, "data": base64.b64encode(data).decode()})
                if out != {"offset": offset, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
                    raise ValueError("write receipt mismatch")
            with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                for future in as_completed([pool.submit(one, x) for x in range(0, size, CHUNK)]):
                    future.result()
            if hash_fd(fd) != (digest, size) or os.fstat(fd).st_mtime_ns != st.st_mtime_ns:
                raise ValueError("source changed after transfer")
            now = source.lstat()
            if (now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns) != (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns):
                raise ValueError("source path changed after transfer")
            out = self.call({"op": "finish", "path": remote, "temp": temp, "size": size, "sha256": digest})
            if out != {"size": size, "sha256": digest}:
                raise ValueError("finish receipt mismatch")
            return {"remote": BASE + "/" + remote, "size": size, "sha256": digest, "log": str(self.log)}
        finally:
            os.close(fd)

    def get(self, remote: str, destination: Path, *, expected_size: int, expected_sha256: str):
        get_name(remote)
        parent, _ = checked_local_parent(destination)
        os.close(parent)
        before = self.call({"op": "stat", "path": remote})
        identity, digest = before["identity"], before["sha256"]
        size = identity[2]
        if size != expected_size or digest != expected_sha256:
            raise ValueError('EXACT_EXPECTED_REMOTE_BYTES_AND_SHA256_REQUIRED_BEFORE_COPY')
        if not isinstance(size, int) or size < 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid remote stat")
        # Direct new file, preserving even a hash-mismatched original for audit.
        fd = open_new_local(destination, readable=True)
        try:
            os.fchmod(fd, 0o600)
            os.ftruncate(fd, size)
            stopped = threading.Event()
            failure_lock = threading.Lock()
            first_failure = []
            def remember_failure(exc):
                with failure_lock:
                    if not first_failure:
                        first_failure.append((exc, exc.__traceback__))
                stopped.set()
            def one(offset):
                if stopped.is_set():
                    return
                try:
                    length = min(CHUNK, size - offset)
                    out = self.call({"op": "read", "path": remote, "offset": offset, "length": length, "identity": identity})
                    data = base64.b64decode(out["data"], validate=True)
                    if out["offset"] != offset or len(data) != length or os.pwrite(fd, data, offset) != length:
                        raise ValueError("read response/range mismatch")
                except BaseException as exc:
                    remember_failure(exc)
                    raise
            with ThreadPoolExecutor(max_workers=GET_WORKERS) as pool:
                futures = [pool.submit(one, x) for x in range(0, size, CHUNK)]
                try:
                    for future in as_completed(futures):
                        future.result()
                except BaseException as exc:
                    remember_failure(exc)
                    for future in futures:
                        future.cancel()
            if first_failure:
                original, traceback = first_failure[0]
                raise original.with_traceback(traceback)
            os.fsync(fd)
            local_digest, local_size = hash_fd(fd)
            after = self.call({"op": "stat", "path": remote})
            if after != before or local_digest != digest or local_size != size:
                self.event({"op": "get", "path": remote, "mismatch": True, "destination": str(destination), "before": before, "after": after, "local_sha256": local_digest})
                raise ValueError("source changed or copied hash mismatch; raw local file retained")
            return {"local": str(destination), "size": size, "sha256": digest, "log": str(self.log)}
        finally:
            os.close(fd)
