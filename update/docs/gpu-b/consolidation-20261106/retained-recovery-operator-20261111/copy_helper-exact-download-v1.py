#!/usr/bin/env python3
"""Fixed-scope CloudShell <-> GB evidence host binary copy via SSM Run Command.

No command execution interface is exposed.  The only remote program is REMOTE_SOURCE.
The caller supplies a file path, never shell or Python source.  See --help.
"""

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

REGION = "ap-northeast-2"
INSTANCE = "i-05e8da8ea99593196"
BASE = "/home/ubuntu/byul-gpu-b-tp2-20261103"
CHUNK = 12_000
WORKERS = 1
GET_WORKERS = 4
GET_NAME = re.compile(r"GB[0-9]{2,}-(?:original-evidence\.zip|transfer\.json)\Z")
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
SENSITIVE_COMPONENT = re.compile(r"(?:^|[-_.])(?:key|keys|settings|secret|credentials?|kubeconfig)(?:$|[-_.])", re.I)
TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut", "Delivery Timed Out", "Execution Timed Out", "Terminated", "Undeliverable"}

# Fixed remote program.  Its base, operation set, owner, and path rules are not CLI inputs.
REMOTE_SOURCE = r'''
import base64, hashlib, json, os, re, stat, sys
with open('/sys/devices/virtual/dmi/id/board_asset_tag') as identity_file:
    if identity_file.read().strip() != 'i-05e8da8ea99593196': raise ValueError('EXACT_NEW_NODE_DMI_REQUIRED')
BASE = "/home/ubuntu/byul-gpu-b-tp2-20261103"
UID = 1000
LIMIT = 12000
PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
GET = re.compile(r"GB[0-9]{2,}-(?:original-evidence\.zip|transfer\.json)\Z")
FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW

def parts(value, get=False):
    if not isinstance(value, str) or value.startswith('/'):
        raise ValueError('relative path required')
    items = value.split('/')
    if not items or any(x in ('.', '..') or not PART.fullmatch(x) for x in items):
        raise ValueError('unsafe path component')
    if get and (len(items) != 1 or not GET.fullmatch(items[0])):
        raise ValueError('get name out of scope')
    return items

def directory(items):
    fd = os.open('/', FLAGS)
    try:
        for item in ('home', 'ubuntu', 'byul-gpu-b-tp2-20261103', *items[:-1]):
            new = os.open(item, FLAGS, dir_fd=fd)
            os.close(fd)
            fd = new
        return fd
    except BaseException:
        os.close(fd)
        raise

def sha_fd(fd):
    h = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        block = os.read(fd, 1024 * 1024)
        if not block: break
        h.update(block)
    return h.hexdigest()

def identity(st):
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]

def regular(st):
    if not stat.S_ISREG(st.st_mode): raise ValueError('not a regular file')

def temp_fd(d, name, size, write=False):
    fd = os.open(name, (os.O_RDWR if write else os.O_RDONLY) | os.O_NOFOLLOW, dir_fd=d)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != UID or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size != size:
        os.close(fd)
        raise ValueError('partial file identity/owner/mode/size mismatch')
    return fd

def run(a):
    op = a['op']
    items = parts(a['path'], op in ('stat', 'read'))
    d = directory(items)
    name = items[-1]
    try:
        if op == 'init':
            size = a['size']
            temp = a['temp']
            if not isinstance(size, int) or size < 0 or not re.fullmatch(r'\.' + re.escape(name) + r'\.[0-9a-f]{32}\.part', temp):
                raise ValueError('invalid partial')
            try: os.stat(name, dir_fd=d, follow_symlinks=False)
            except FileNotFoundError: pass
            else: raise FileExistsError('final exists')
            f = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=d)
            try:
                os.fchown(f, UID, UID)
                os.fchmod(f, 0o600)
                os.ftruncate(f, size)
                os.fsync(f)
            finally: os.close(f)
            return {'size': size}
        if op == 'write':
            size, offset = a['size'], a['offset']
            data = base64.b64decode(a['data'], validate=True)
            if not data or len(data) > LIMIT or offset < 0 or offset + len(data) > size:
                raise ValueError('invalid write range')
            f = temp_fd(d, a['temp'], size, True)
            try:
                if os.pwrite(f, data, offset) != len(data): raise OSError('short write')
            finally: os.close(f)
            return {'offset': offset, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if op == 'finish':
            size, expected = a['size'], a['sha256']
            if not re.fullmatch(r'[0-9a-f]{64}', expected): raise ValueError('bad sha256')
            f = temp_fd(d, a['temp'], size)
            try:
                actual = sha_fd(f)
                if actual != expected: raise ValueError('partial sha256 mismatch')
                os.fsync(f)
            finally: os.close(f)
            # link() has atomic create-if-absent semantics; rename() would overwrite.
            os.link(a['temp'], name, src_dir_fd=d, dst_dir_fd=d, follow_symlinks=False)
            os.fsync(d)
            os.unlink(a['temp'], dir_fd=d)
            os.fsync(d)
            return {'size': size, 'sha256': actual}
        if op in ('stat', 'read'):
            f = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=d)
            try:
                before = os.fstat(f)
                regular(before)
                if op == 'stat':
                    digest = sha_fd(f)
                    after = os.fstat(f)
                    if identity(before) != identity(after): raise ValueError('file changed during hash')
                    return {'identity': identity(after), 'sha256': digest}
                offset, length = a['offset'], a['length']
                if length < 1 or length > LIMIT or offset < 0 or offset + length > before.st_size or identity(before) != a['identity']:
                    raise ValueError('read identity/range mismatch')
                data = os.pread(f, length, offset)
                if len(data) != length or identity(before) != identity(os.fstat(f)):
                    raise ValueError('file changed during read')
                return {'offset': offset, 'data': base64.b64encode(data).decode('ascii')}
            finally: os.close(f)
        raise ValueError('unsupported operation')
    finally: os.close(d)

try:
    value = json.loads(base64.b64decode(sys.argv[1], validate=True))
    print(json.dumps(run(value), separators=(',', ':')))
except BaseException as exc:
    print('ERROR:' + type(exc).__name__ + ':' + str(exc))
    sys.exit(2)
'''


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


"""Bound the existing SSM evidence getter; no new API or remote program."""
import base64
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import hashlib
import json
import math
import os
from pathlib import Path
import shlex
import threading
import time
import uuid

# Existing CLI adapter's subprocess.run timeout, not a larger allowance.
SDK_CLI_BOUND = 65


def publish(path, raw):
    path = Path(path)
    temporary = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.publishing')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


def encode(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False) + '\n').encode()


def bind_transfer(original):
    """Use the selected helper's original FD/name/hash guards and REMOTE_SOURCE."""
    class Transfer(original.Transfer):
        def __init__(self, sdk, log, *, deadline, clock=time.monotonic, sleep=time.sleep, **options):
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise ValueError('ORIGINAL_TOTAL_DEADLINE_REQUIRED')
            super().__init__(sdk, log, **options)
            self.deadline, self.clock, self.sleep = deadline, clock, sleep
            self.records = Path(log).with_name(Path(log).name + '.records')
            try:
                self.records.mkdir(mode=0o700, exist_ok=False)
            except BaseException:
                self.close()
                raise
            self._record_lock, self._serial = threading.Lock(), 0

        def record(self, name, value):
            publish(self.records / name, encode(value))

        def issue_allowed(self):
            # Preserve the existing send CLI bound + original per-call watch.
            # Existing in-flight calls may only observe, never create a new send.
            return self.deadline - self.clock() >= SDK_CLI_BOUND + self.timeout

        def call(self, args):
            with self._record_lock:
                self._serial += 1
                tag = '%06d' % self._serial
            if not self.issue_allowed():
                self.record(tag + '.not-issued.json', {'op': args['op'], 'issued': False,
                    'reason': 'original total deadline admission', 'deadline': self.deadline})
                raise TimeoutError('ORIGINAL_TOTAL_DEADLINE_NO_NEW_SEND')
            encoded = base64.b64encode(json.dumps(args, separators=(',', ':')).encode()).decode()
            command = 'python3 -I -S -B -c ' + shlex.quote(original.REMOTE_SOURCE) + ' ' + shlex.quote(encoded)
            request = dict(InstanceIds=[original.INSTANCE], DocumentName='AWS-RunShellScript',
                Parameters={'commands': [command]}, TimeoutSeconds=120, Comment='GB binary file copy')
            self.record(tag + '.intent.json', {'op': args['op'], 'arguments': args,
                'request': request, 'deadline': self.deadline, 'issued': 'not yet known'})
            command_id, result = None, None
            try:
                reply = self.sdk.send_command(**request)
                command_id = reply['Command']['CommandId']
                # SDK accepted receipt already exists separately. Keep the exact
                # helper-bound reply before the first get_command_invocation too.
                self.record(tag + '.accepted.json', {'op': args['op'], 'command_id': command_id,
                    'response': reply, 'server_terminal': False})
                start = self.clock()
                while True:
                    if self.deadline - self.clock() < SDK_CLI_BOUND:
                        raise TimeoutError('ORIGINAL_TOTAL_DEADLINE_ACCEPTED_REQUIRES_RECONCILIATION:' + command_id)
                    try:
                        result = self.sdk.get_command_invocation(CommandId=command_id, InstanceId=original.INSTANCE)
                    except Exception as error:
                        if type(error).__name__ != 'InvocationDoesNotExist' or self.clock() - start > self.timeout:
                            self.event({'op': args['op'], 'command_id': command_id, 'api_error': repr(error)})
                            raise
                        self.sleep(min(self.poll, max(0, self.deadline - self.clock() - SDK_CLI_BOUND)))
                        continue
                    status = result.get('StatusDetails', result.get('Status'))
                    if status in original.TERMINAL:
                        break
                    if self.clock() - start > self.timeout:
                        self.event({'op': args['op'], 'command_id': command_id, 'timeout': True, 'status': status})
                        raise TimeoutError(command_id)
                    self.sleep(min(self.poll, max(0, self.deadline - self.clock() - SDK_CLI_BOUND)))
                self._preserve_response(tag, result)
                stdout, stderr = result.get('StandardOutputContent', ''), result.get('StandardErrorContent', '')
                event = {'op': args['op'], 'command_id': command_id, 'status': status,
                    'response_code': result.get('ResponseCode'), 'path': args['path']}
                if 'offset' in args:
                    event['offset'] = args['offset']
                if status != 'Success' or result.get('ResponseCode') != 0:
                    event.update(failure_stdout=stdout, failure_stderr=stderr)
                    self.event(event)
                    raise RuntimeError('SSM command failed; see private metadata log: ' + command_id)
                self.event(event)
                try:
                    return json.loads(stdout)
                except (ValueError, TypeError):
                    self.event({'command_id': command_id, 'unparseable_stdout_size': len(stdout),
                        'unparseable_stdout_sha256': hashlib.sha256(stdout.encode()).hexdigest()})
                    raise
            except BaseException as error:
                try:
                    if result is not None and not (self.records / (tag + '.response.json')).exists():
                        self._preserve_response(tag, result)
                    partial = {}
                    for channel in ('stdout', 'stderr'):
                        raw = getattr(error, channel, None)
                        if raw is not None:
                            raw = raw.encode() if isinstance(raw, str) else raw
                            publish(self.records / (tag + '.exception.' + channel), raw)
                            partial[channel] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                    self.record(tag + '.exception.json', {'type': type(error).__name__, 'message': str(error),
                        'command_id': command_id, 'sdk_local_mutation_tag': getattr(error, 'mutation_tag', None),
                        'partial_exception_channels': partial, 'API_response_observed': result is not None,
                        'missing_raw_response_is_not_empty_response': result is None and not partial})
                except BaseException as secondary:
                    error.add_note('Transfer evidence preservation failed: ' + type(secondary).__name__)
                raise

        def _preserve_response(self, tag, result):
            self.record(tag + '.response.json', result)
            for channel, field in (('stdout', 'StandardOutputContent'), ('stderr', 'StandardErrorContent')):
                publish(self.records / (tag + '.' + channel), result.get(field, '').encode())

        def get(self, remote, destination, *, expected_size, expected_sha256):
            original.get_name(remote)
            parent, _ = original.checked_local_parent(destination)
            os.close(parent)
            before = self.call({'op': 'stat', 'path': remote})
            remote_identity, digest = before['identity'], before['sha256']
            size = remote_identity[2]
            if size != expected_size or digest != expected_sha256:
                raise ValueError('EXACT_EXPECTED_REMOTE_BYTES_AND_SHA256_REQUIRED_BEFORE_COPY')
            if type(size) is not int or size < 0 or original.re.fullmatch('[0-9a-f]{64}', digest) is None:
                raise ValueError('invalid remote stat')
            fd = original.open_new_local(destination, readable=True)
            written, lock = {}, threading.Lock()
            stopped = threading.Event()
            failure_lock = threading.Lock()
            first_failure = []
            def remember_failure(exc):
                with failure_lock:
                    if not first_failure:
                        first_failure.append((exc, exc.__traceback__))
                stopped.set()
            try:
                os.fchmod(fd, 0o600)
                os.ftruncate(fd, size)
                offsets = iter(range(0, size, original.CHUNK))

                def one(offset):
                    if stopped.is_set():
                        return
                    try:
                        length = min(original.CHUNK, size - offset)
                        result = self.call({'op': 'read', 'path': remote, 'offset': offset,
                            'length': length, 'identity': remote_identity})
                        data = base64.b64decode(result['data'], validate=True)
                        if result['offset'] != offset or len(data) != length or os.pwrite(fd, data, offset) != length:
                            raise ValueError('read response/range mismatch')
                        row = {'offset': offset, 'length': length, 'pwrite_succeeded': True,
                               'bytes_sha256': hashlib.sha256(data).hexdigest()}
                        self.record('range-%012d.json' % offset, row)
                        with lock:
                            written[offset] = row

                    except BaseException as exc:
                        remember_failure(exc)
                        raise

                with ThreadPoolExecutor(max_workers=original.GET_WORKERS) as pool:
                    pending = set()
                    def enqueue():
                        if not self.issue_allowed():
                            raise TimeoutError('ORIGINAL_TOTAL_DEADLINE_NO_NEW_RANGE')
                        try:
                            offset = next(offsets)
                        except StopIteration:
                            return False
                        pending.add(pool.submit(one, offset))
                        return True
                    try:
                        for _ in range(original.GET_WORKERS):
                            if not enqueue():
                                break
                        while pending:
                            done, pending = wait(pending, return_when=FIRST_COMPLETED)
                            for future in done:
                                future.result()
                            for _ in done:
                                if not enqueue():
                                    break
                    except BaseException as error:
                        remember_failure(error)
                        for future in pending:
                            future.cancel()
                if first_failure:
                    original_error, traceback = first_failure[0]
                    raise original_error.with_traceback(traceback)
                if sorted(written) != list(range(0, size, original.CHUNK)):
                    raise ValueError('TRANSFER_WRITTEN_RANGE_COVERAGE_REQUIRED')
                os.fsync(fd)
                local_digest, local_size = original.hash_fd(fd)
                after = self.call({'op': 'stat', 'path': remote})
                if after != before or local_digest != digest or local_size != size:
                    self.event({'op': 'get', 'path': remote, 'mismatch': True,
                        'destination': str(destination), 'before': before, 'after': after, 'local_sha256': local_digest})
                    raise ValueError('source changed or copied hash mismatch; raw local file retained')
                self.record('get-complete.json', {'complete': True, 'size': size, 'sha256': digest,
                    'written_ranges': [written[k] for k in sorted(written)], 'remote_identity_rechecked': True})
                return {'local': str(destination), 'size': size, 'sha256': digest, 'log': str(self.log)}
            except BaseException as error:
                try:
                    self.record('get-incomplete.json', {'complete': False,
                        'type': type(error).__name__, 'message': str(error), 'expected_size': size,
                        'written_ranges': [written[k] for k in sorted(written)],
                        'allocated_file_size_is_not_completed_bytes': True, 'raw_partial_retained': True})
                except BaseException as secondary:
                    error.add_note('Incomplete transfer evidence preservation failed: ' + type(secondary).__name__)
                raise
            finally:
                os.close(fd)
    return Transfer


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="operation", required=True)
    put = sub.add_parser("put")
    put.add_argument("source", type=Path)
    put.add_argument("remote_relative")
    get = sub.add_parser("get")
    get.add_argument("remote_name")
    get.add_argument("destination", type=Path)
    get.add_argument("--expected-size", type=int, required=True)
    get.add_argument("--expected-sha256", required=True)
    for p in (put, get):
        p.add_argument("--log", type=Path, required=True, help="new private JSONL metadata log")
    get.add_argument('--deadline-monotonic', type=float, required=True)
    a = parser.parse_args(argv)
    # Import only on actual use; CPU fake-SDK contracts never touch AWS credentials.
    import aws_cli_adapter_v2
    sdk = aws_cli_adapter_v2.client("ssm", region_name=REGION)
    transfer = Transfer(sdk, a.log) if a.operation == 'put' else bind_transfer(sys.modules[__name__])(sdk, a.log, deadline=a.deadline_monotonic)
    try:
        result = transfer.put(a.source, a.remote_relative) if a.operation == "put" else transfer.get(a.remote_name, a.destination, expected_size=a.expected_size, expected_sha256=a.expected_sha256)
    finally:
        transfer.close()
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
