"""One request, one root-owned kubectl session; no resident service.

The original bounded collector is loaded unchanged in a private namespace.
Its Popen/Thread facades add ownership and clamp cleanup to the SAME deadline.
This is a new injected B runner, not an unchanged five-second implementation.
The production entry also creates a transient PID/mount namespace. Its PID1
receives SIGKILL on death of the exact outer root parent; kernel PID-namespace
teardown then covers children outside the created client process group too.
No namespace is persisted. Kernel-uninterruptible tasks remain a limitation.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import re
import select
import signal
import stat
import subprocess
import sys
import threading
import time
import types
import uuid

BOUNDED_SHA = "25c81666a17e13651c0b5b3bd67c9f310c6feee8311fcac408404d895dc67290"
KUBECTL = "/usr/local/bin/kubectl"
KUBECONFIG = "/etc/rancher/k3s/k3s.yaml"
CLEANUP_RESERVE = 0.65
WIRE_RESERVE = 0.35
REQUEST_CAP = 65536
WIRE_METADATA_CAP = 32768
_CALLER_PROC_FD = None
_NAMESPACE_PARENT = None

# Root Python starts with this fixed stdlib-only program, not a pathname that
# Python would read before our FD guards. The verified FD bytes themselves are
# compiled. No shell, import-path change, replacement .pth or copied config.
ROOT_BOOTSTRAP = r'''
import hashlib,os,pathlib,stat,sys
path,pin,encoded=sys.argv[1:]
p=pathlib.PurePosixPath(path)
if not p.is_absolute():raise RuntimeError("BOOTSTRAP_ABSOLUTE_SOURCE")
directory=os.open("/",os.O_RDONLY|os.O_DIRECTORY)
try:
 for part in p.parts[1:-1]:
  new=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=directory)
  os.close(directory);directory=new
 d=os.fstat(directory)
 if d.st_uid!=1000 or stat.S_IMODE(d.st_mode)!=0o700:raise RuntimeError("BOOTSTRAP_PRIVATE_DIRECTORY")
 fd=os.open(p.name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=directory)
 try:
  a=os.fstat(fd)
  if not stat.S_ISREG(a.st_mode) or a.st_uid!=1000 or a.st_nlink!=1 or stat.S_IMODE(a.st_mode)!=0o600 or a.st_size>65536:raise RuntimeError("BOOTSTRAP_SOURCE_METADATA")
  raw=os.read(fd,65537);b=os.fstat(fd)
  ident=lambda s:(s.st_dev,s.st_ino,s.st_uid,s.st_gid,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  if ident(a)!=ident(b) or len(raw)!=a.st_size or hashlib.sha256(raw).hexdigest()!=pin:raise RuntimeError("BOOTSTRAP_SOURCE_BYTES")
 finally:os.close(fd)
finally:os.close(directory)
sys.argv=[path,"--one-request",encoded]
exec(compile(raw,path,"exec"),{"__name__":"__main__","__file__":path})
'''


class RootClientError(RuntimeError):
    def __init__(self, code, *, result=None, transport=None):
        super().__init__(code)
        self.result = result
        self.transport = transport


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _proc(pid, *, proc_fd=None):
    def read(name):
        if proc_fd is None:
            return Path(f"/proc/{pid}/{name}").read_text()
        fd = os.open(f"{pid}/{name}", os.O_RDONLY, dir_fd=proc_fd)
        try:
            return os.read(fd, 65536).decode()
        finally:
            os.close(fd)
    try:
        raw = read("stat")
        fields = raw[raw.rfind(")") + 2:].split()
        info = {"pid": pid, "state": fields[0], "ppid": int(fields[1]),
                "pgid": int(fields[2]), "sid": int(fields[3]), "start": int(fields[19])}
        status = read("status")
        info["uid"] = int(re.search(r"^Uid:\s+(\d+)", status, re.M)[1])
        return info
    except (FileNotFoundError, ProcessLookupError):
        return None


def _same_caller(expected):
    now = _proc(expected["pid"], proc_fd=_CALLER_PROC_FD)
    return bool(now and now["state"] not in ("Z", "X")
                and (now["start"], now["uid"]) == (expected["start"], 1000))


def _group(pgid):
    rows = []
    for name in os.listdir("/proc"):
        if name.isdecimal():
            info = _proc(int(name))
            if info and info["pgid"] == pgid and info["sid"] == pgid:
                rows.append(info)
    return rows


def _private_dir(path):
    path = Path(path)
    if not path.is_absolute():
        raise RootClientError("PRIVATE_ABSOLUTE_DIRECTORY_REQUIRED")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        s = os.fstat(fd)
        if s.st_uid != 1000 or stat.S_IMODE(s.st_mode) != 0o700:
            raise RootClientError("PRIVATE_UID1000_MODE700_REQUIRED")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _source(path, expected):
    path = Path(path)
    parent = _private_dir(str(path.parent))
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 1000
                    or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                    or before.st_size > 65536):
                raise RootClientError("PRIVATE_SOURCE_METADATA")
            raw = os.read(fd, 65537)
            after = os.fstat(fd)
            identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode,
                                  s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
            if (identity(before) != identity(after) or len(raw) != before.st_size
                    or hashlib.sha256(raw).hexdigest() != expected):
                raise RootClientError("PRIVATE_SOURCE_BYTES")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)


def _original(raw):
    if hashlib.sha256(raw).hexdigest() != BOUNDED_SHA:
        raise RootClientError("ORIGINAL_BOUNDED_SHA")
    namespace = {"__name__": "_private_original_bounded"}
    exec(compile(raw, "<exact-original-bounded-25c8>", "exec"), namespace)
    return namespace


def _subreaper():
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "PR_SET_CHILD_SUBREAPER")


def _drop_to_caller(gid):
    os.setgroups([])
    os.setgid(gid)
    os.setuid(1000)
    # setuid clears PDEATHSIG. Restore it after dropping privilege and close
    # that race by checking the exact parent through the retained host proc FD.
    if _NAMESPACE_PARENT is not None:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "post-drop PR_SET_PDEATHSIG")
        observed = _proc(_NAMESPACE_PARENT["pid"], proc_fd=_CALLER_PROC_FD)
        if (not observed or observed["start"] != _NAMESPACE_PARENT["start"]
                or observed["state"] in ("Z", "X")):
            raise RootClientError("POST_DROP_NAMESPACE_PARENT_CHANGED")


def _namespace_once(callback, absolute_deadline):
    """Root one-shot. No fallback when existing namespace capability is absent.

    callback runs as PID1 in a new PID namespace with a private /proc mount.
    The retained old /proc directory FD is used ONLY to read caller identity.
    The outer parent's PID/start is rechecked after setting PDEATHSIG, closing
    the fork-to-prctl parent-death race. No signal targets caller/other groups.
    """
    global _CALLER_PROC_FD, _NAMESPACE_PARENT
    if os.geteuid() != 0 or time.monotonic() >= absolute_deadline - WIRE_RESERVE:
        raise RootClientError("NAMESPACE_ROOT_AND_ORIGINAL_REMAINING_REQUIRED")
    parent = _proc(os.getpid())
    old_proc = os.open("/proc", os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.unshare(os.CLONE_NEWPID)
        pid = os.fork()
        if pid == 0:
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
                    raise OSError(ctypes.get_errno(), "PR_SET_PDEATHSIG")
                observed = _proc(parent["pid"], proc_fd=old_proc)
                if not observed or observed["start"] != parent["start"] or observed["state"] in ("Z", "X"):
                    raise RootClientError("NAMESPACE_OUTER_PARENT_CHANGED")
                _CALLER_PROC_FD = old_proc
                _NAMESPACE_PARENT = parent
                os.unshare(os.CLONE_NEWNS)
                # Mount propagation and /proc are changed in this child's new
                # mount namespace only. No host mount/config file is rewritten.
                if libc.mount(None, b"/", None, 16384 | (1 << 18), None) != 0:
                    raise OSError(ctypes.get_errno(), "private mount propagation")
                if libc.mount(b"proc", b"/proc", b"proc", 2 | 4 | 8, None) != 0:
                    raise OSError(ctypes.get_errno(), "request /proc mount")
                if os.getpid() != 1:
                    raise RootClientError("REQUEST_NAMESPACE_PID1_REQUIRED")
                callback()
                os._exit(0)
            except BaseException as exc:
                # A failed namespace PID1 exits; kernel teardown owns all of
                # its descendants. This exception is not a successful wire.
                os.write(2, (type(exc).__name__ + ":" + str(exc)[:512] + "\n").encode())
                os._exit(1)
        while time.monotonic() < absolute_deadline:
            ended, status = os.waitpid(pid, os.WNOHANG)
            if ended:
                code = os.waitstatus_to_exitcode(status)
                if code != 0:
                    raise RootClientError("REQUEST_NAMESPACE_CHILD_EXIT_" + str(code))
                return
            time.sleep(min(0.005, max(0, absolute_deadline - time.monotonic())))
        # Only the exact child returned by this fork; an unreaped PID cannot
        # be reused. SIGKILL of this PID1 triggers its namespace's teardown.
        os.kill(pid, signal.SIGKILL)
        ended, status = os.waitpid(pid, os.WNOHANG)
        raise RootClientError("REQUEST_NAMESPACE_ORIGINAL_DEADLINE_INCOMPLETE")
    finally:
        os.close(old_proc)


def capture_owned(argv, *, absolute_deadline, caller, bounded_source,
                  max_stdout_bytes, max_stderr_bytes, input_bytes=None):
    """Root-only core. Returns original collector values plus observed ownership.

    Tests call this core with harmless children. Production entry permits only
    the selected kubectl grammar. There is no CLI flag to bypass that grammar.
    """
    started = time.monotonic()
    if os.geteuid() != 0 or not _same_caller(caller):
        raise RootClientError("ROOT_AND_LIVE_EXACT_UID1000_CALLER_REQUIRED")
    if (type(absolute_deadline) not in (int, float) or not math.isfinite(absolute_deadline)
            or absolute_deadline - started <= CLEANUP_RESERVE + WIRE_RESERVE + 0.02):
        raise RootClientError("ORIGINAL_REMAINING_INSUFFICIENT")
    namespace = _original(bounded_source)
    _subreaper()
    capture_deadline = absolute_deadline - CLEANUP_RESERVE - WIRE_RESERVE
    cleanup_deadline = absolute_deadline - WIRE_RESERVE
    state = {"schema": "byul-root-client-supervision/1", "caller": caller,
             "root_pid": os.getpid(), "root_uid": os.geteuid(), "started": started,
             "absolute_deadline": absolute_deadline, "capture_deadline": capture_deadline,
             "cleanup_deadline": cleanup_deadline, "cleanup_reserve": CLEANUP_RESERVE,
             "wire_reserve": WIRE_RESERVE, "termination_reason": None,
             "group_kill_sent": False, "reaped_adopted_pids": [], "cleanup_errors": []}
    state.update({"pid_scope": "transient_request_namespace" if _NAMESPACE_PARENT else "current_namespace",
                  "namespace_outer_parent": _NAMESPACE_PARENT,
                  "namespace_inode": os.readlink("/proc/self/ns/pid")})
    owned = []

    class OwnedProcess:
        def __init__(self, args, **kwargs):
            if owned or not _same_caller(caller) or time.monotonic() >= capture_deadline:
                raise RootClientError("CHILD_START_OWNERSHIP_OR_DEADLINE")
            self.child = subprocess.Popen(args, start_new_session=True, **kwargs)
            self.pid = self.child.pid
            self.identity = _proc(self.pid)
            self.terminal_seen = False
            self.stdout, self.stderr, self.stdin = self.child.stdout, self.child.stderr, self.child.stdin
            state["child_identity"] = self.identity
            state["pgid"] = self.pid
            owned.append(self)

        @property
        def returncode(self):
            return self.child.returncode

        def reap(self):
            self.child.poll()  # reap the direct child through its Popen owner
            for row in _group(self.pid):
                if row["pid"] == self.pid:
                    continue
                try:
                    pid, _ = os.waitpid(row["pid"], os.WNOHANG)
                except ChildProcessError:
                    continue
                if pid:
                    state["reaped_adopted_pids"].append(pid)

        def kill(self):
            if self.terminal_seen:
                return
            # This group/session was created here and is never reused after
            # terminal observation. No caller, other group or daemon is killed.
            try:
                os.killpg(self.pid, signal.SIGKILL)
                state["group_kill_sent"] = True
            except ProcessLookupError:
                pass

        def poll(self):
            self.reap()
            rows = _group(self.pid)
            if not rows and self.child.returncode is not None:
                self.terminal_seen = True
                return self.child.returncode
            if not _same_caller(caller):
                state["termination_reason"] = "caller_identity_gone_or_changed"
                self.kill()
            return None

        def wait(self, timeout=None):
            end = min(cleanup_deadline, time.monotonic() + (timeout if timeout is not None else 0))
            while time.monotonic() < end:
                if self.poll() is not None:
                    return self.child.returncode
                time.sleep(min(0.005, max(0, end - time.monotonic())))
            if self.poll() is None:
                raise subprocess.TimeoutExpired(argv, timeout)
            return self.child.returncode

    class BudgetThread(threading.Thread):
        def join(self, timeout=None):
            return super().join(min(timeout if timeout is not None else 0,
                                    max(0, cleanup_deadline - time.monotonic())))

    namespace["subprocess"] = types.SimpleNamespace(
        Popen=OwnedProcess, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired)
    namespace["threading"] = types.SimpleNamespace(Thread=BudgetThread, Event=threading.Event)
    result = None
    try:
        result = namespace["capture_pipes"](
            argv, timeout=max(0.001, capture_deadline - time.monotonic()), env_extra={},
            max_stdout_bytes=max_stdout_bytes, max_stderr_bytes=max_stderr_bytes,
            input_bytes=input_bytes)
        return result
    finally:
        if owned:
            child = owned[0]
            if child.poll() is None:
                child.kill()
                try:
                    child.wait(timeout=max(0, cleanup_deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    state["cleanup_errors"].append("owned_group_not_terminal_by_cleanup_deadline")
            child.reap()
            state["remaining_group"] = _group(child.pid)
            state["direct_child_reaped"] = child.child.returncode is not None
            state["group_terminal"] = not state["remaining_group"] and state["direct_child_reaped"]
        else:
            state["remaining_group"] = []
            state["group_terminal"] = None
            state["direct_child_reaped"] = None
        state["finished"] = time.monotonic()
        state["cleanup_deadline_exceeded"] = state["finished"] > cleanup_deadline
        if result is not None:
            if state["termination_reason"] is None:
                state["termination_reason"] = ("capture_timeout" if result["capture"]["timed_out"]
                    else "capture_overflow" if result["capture"]["truncated"] else "child_terminal")
            result["root_supervision"] = state


def _selected_argv(argv, binding, active_budget):
    if (type(argv) is not list or any(type(v) is not str for v in argv)
            or len(argv) < 5 or argv[0] != KUBECTL
            or argv[1] != "--context=" + binding["context"]
            or not re.fullmatch(r"--request-timeout=[0-9]+(?:\.[0-9]+)?s", argv[2])):
        raise RootClientError("SELECTED_CLIENT_ARGV_REQUIRED")
    requested = float(argv[2].split("=", 1)[1][:-1])
    budget = min(requested, active_budget)
    if not math.isfinite(budget) or budget <= 0:
        raise RootClientError("SELECTED_CLIENT_BUDGET")
    args = argv[3:]
    namespace = binding["namespace"]
    pod = binding["pod"]
    if args == ["get", "namespace", namespace, "-o", "json"]:
        pass
    elif args == ["--namespace=" + namespace, "get", "pod", pod, "-o", "json"]:
        pass
    elif (len(args) >= 12 and args[:5] == ["--namespace=" + namespace, "exec", pod,
          "--container=" + binding["container"], "--"]
          and args[5:10] == ["python3", "-I", "-S", "-B", "-c"]
          and hashlib.sha256(args[10].encode()).hexdigest() in binding["program_sha256"]):
        remote_budget = float(args[11])
        if not math.isfinite(remote_budget) or remote_budget <= 0:
            raise RootClientError("REMOTE_PROGRAM_BUDGET")
        args = [*args[:11], repr(min(remote_budget, budget)), *args[12:]]
    else:
        raise RootClientError("SELECTED_READER_OPERATION_REQUIRED")
    return [KUBECTL, "--kubeconfig=" + KUBECONFIG, argv[1],
            f"--request-timeout={budget:.6f}s", *args]


def _wire_result(result):
    return {**result, "stdout_bytes": base64.b64encode(result["stdout_bytes"]).decode(),
            "stderr_bytes": base64.b64encode(result["stderr_bytes"]).decode()}


def _entry(encoded):
    if len(encoded) > 4 * ((REQUEST_CAP + 2) // 3):
        raise RootClientError("REQUEST_BOUND")
    request_raw = base64.b64decode(encoded, validate=True)
    if len(request_raw) > REQUEST_CAP:
        raise RootClientError("REQUEST_BOUND")
    request = json.loads(request_raw)
    # Read source by protected FD before any command. Parent verifies this pin too.
    _source(__file__, request["component_sha256"])
    raw = _source(request["bounded_path"], BOUNDED_SHA)
    if os.getuid() != 0 or not _same_caller(request["caller"]):
        raise RootClientError("ROOT_ENTRY_CALLER")
    active = request["absolute_deadline"] - time.monotonic() - CLEANUP_RESERVE - WIRE_RESERVE
    effective = _selected_argv(request["argv"], request["binding"], active)
    # Existing selected config/binary, no chmod, install, copied config or new privilege.
    config = os.stat(KUBECONFIG, follow_symlinks=False)
    binary = os.stat(KUBECTL)
    if (not stat.S_ISREG(config.st_mode) or config.st_uid != 0
            or stat.S_IMODE(config.st_mode) != 0o600 or config.st_nlink != 1
            or not stat.S_ISREG(binary.st_mode) or binary.st_uid != 0
            or binary.st_mode & 0o022 or not binary.st_mode & 0o111):
        raise RootClientError("SELECTED_EXISTING_CLIENT_METADATA")
    output_fd = _private_dir(request["output_dir"])
    try:
        result = capture_owned(effective, absolute_deadline=request["absolute_deadline"],
            caller=request["caller"], bounded_source=raw,
            max_stdout_bytes=request["stdout_cap"], max_stderr_bytes=request["stderr_cap"])
        result["root_supervision"].update({"requested_argv_sha256": hashlib.sha256(_json(request["argv"])).hexdigest(),
            "effective_argv_sha256": hashlib.sha256(_json(effective)).hexdigest(),
            "deadline_reduced_argv": effective != request["argv"], "injected": True})
        wire = _json({"schema": "byul-root-client-wire/1", "nonce": request["nonce"],
                      "result": _wire_result(result)})
        cap = 4 * ((request["stdout_cap"] + 2) // 3) + 4 * ((request["stderr_cap"] + 2) // 3) + WIRE_METADATA_CAP
        if len(wire) > cap:
            raise RootClientError("WIRE_BOUND", result=result)
        # Child cleanup is finished before dropping to the original helper owner.
        _drop_to_caller(request["caller_gid"])
        fd = os.open(request["nonce"] + ".json", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=output_fd)
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(wire)
        finally:
            os.close(fd)
        os.set_blocking(1, False)
        remaining = memoryview(wire)
        while remaining:
            budget = request["absolute_deadline"] - time.monotonic()
            if budget <= 0:
                raise RootClientError("WIRE_ORIGINAL_DEADLINE")
            if not select.select([], [1], [], budget)[1]:
                raise RootClientError("WIRE_ORIGINAL_DEADLINE")
            try:
                remaining = remaining[os.write(1, remaining[:65536]):]
            except BlockingIOError:
                continue
    finally:
        os.close(output_fd)


def selected_kubeconfig_runner(binary, kubeconfig, *, existing_root_client,
                               component_path, component_sha256, bounded_path,
                               output_dir, binding):
    """Factory for S5 injection; existing runner kwargs and actual result retained.

    binding={context,namespace,pod,container,program_sha256:[exact wrapper SHA...]}
    The helper must run as UID/EUID1000. No model or API runs at factory creation.
    """
    if (binary != KUBECTL or str(kubeconfig) != KUBECONFIG or existing_root_client is not True
            or os.getuid() != 1000 or os.geteuid() != 1000):
        raise RootClientError("SELECTED_UID1000_HELPER_REQUIRED")
    _source(component_path, component_sha256)
    bounded_source = _source(bounded_path, BOUNDED_SHA)
    fd = _private_dir(output_dir)
    os.close(fd)
    # JSON round trip makes a private binding snapshot; does not rebind A source.
    binding = json.loads(_json(binding))
    for key in ("context", "namespace", "pod", "container"):
        if type(binding.get(key)) is not str or not binding[key] or len(binding[key]) > 253:
            raise RootClientError("FIXED_READER_BINDING")
    if (type(binding.get("program_sha256")) is not list or len(binding["program_sha256"]) != 4
            or any(not re.fullmatch("[0-9a-f]{64}", v) for v in binding["program_sha256"])):
        raise RootClientError("FIXED_FOUR_PROGRAM_PINS")

    broken = False

    def runner(argv, *, timeout, env_extra, max_stdout_bytes, max_stderr_bytes, input_bytes=None):
        nonlocal broken
        entered = time.monotonic()
        if broken:
            raise RootClientError("PRIOR_ROOT_WIRE_UNRESOLVED_NO_NEXT_REQUEST")
        if (type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 5
                or env_extra != {} or input_bytes is not None
                or type(max_stdout_bytes) is not int or not 1 <= max_stdout_bytes <= 4 * 1024**2
                or type(max_stderr_bytes) is not int or not 1 <= max_stderr_bytes <= 4096):
            raise RootClientError("ORIGINAL_REQUEST_BOUND")
        deadline = entered + timeout
        _selected_argv(argv, binding, timeout - CLEANUP_RESERVE - WIRE_RESERVE)
        nonce = uuid.uuid4().hex
        request = {"component_sha256": component_sha256, "bounded_path": str(bounded_path),
            "output_dir": str(output_dir), "binding": binding, "argv": argv,
            "absolute_deadline": deadline, "caller": _proc(os.getpid()), "caller_gid": os.getgid(),
            "stdout_cap": max_stdout_bytes, "stderr_cap": max_stderr_bytes, "nonce": nonce}
        encoded = base64.b64encode(_json(request)).decode()
        command = ["/usr/bin/sudo", "-n", "-u", "root", "--", "/usr/bin/python3.12", "-I", "-S", "-B",
                   "-c", ROOT_BOOTSTRAP, str(component_path), component_sha256, encoded]
        cap = 4 * ((max_stdout_bytes + 2) // 3) + 4 * ((max_stderr_bytes + 2) // 3) + WIRE_METADATA_CAP
        remaining = deadline - time.monotonic()
        if remaining <= CLEANUP_RESERVE + WIRE_RESERVE:
            raise RootClientError("ORIGINAL_REMAINING_INSUFFICIENT")
        # UID1000 owns only the transport Popen. It must never claim that
        # killing sudo also kills its root child. The root component owns that
        # cleanup. A failed wire latches this runner until external resolution.
        transport_errors = []

        class TransportProcess:
            def __init__(self, args, **kwargs):
                self.child = subprocess.Popen(args, **kwargs)
                self.stdout, self.stderr, self.stdin = self.child.stdout, self.child.stderr, self.child.stdin

            @property
            def returncode(self):
                return self.child.returncode

            def poll(self):
                return self.child.poll()

            def kill(self):
                try:
                    self.child.kill()
                except PermissionError:
                    transport_errors.append("uid1000_cannot_signal_elevated_transport")

            def wait(self, timeout=None):
                return self.child.wait(timeout=min(timeout if timeout is not None else 0,
                                                   max(0, deadline - time.monotonic())))

        class TransportThread(threading.Thread):
            def join(self, timeout=None):
                return super().join(min(timeout if timeout is not None else 0,
                                        max(0, deadline - time.monotonic())))

        namespace = _original(bounded_source)
        namespace["subprocess"] = types.SimpleNamespace(Popen=TransportProcess,
            PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired)
        namespace["threading"] = types.SimpleNamespace(Thread=TransportThread, Event=threading.Event)
        broken = True
        transport = namespace["capture_pipes"](command,
            timeout=max(0.001, deadline - time.monotonic()), env_extra={},
            max_stdout_bytes=cap, max_stderr_bytes=4096)
        transport["transport_cleanup_errors"] = transport_errors
        if transport["returncode"] != 0 or transport["capture"]["complete"] is not True:
            raise RootClientError("ROOT_WIRE_TRANSPORT_INCOMPLETE", transport=transport)
        try:
            wire = json.loads(transport["stdout_bytes"])
            if wire["schema"] != "byul-root-client-wire/1" or wire["nonce"] != nonce:
                raise ValueError("wire identity")
            result = wire["result"]
            for name, limit in (("stdout", max_stdout_bytes), ("stderr", max_stderr_bytes)):
                raw = base64.b64decode(result[name + "_bytes"], validate=True)
                actual = result["capture"]["streams"][name]
                if (len(raw) > limit or actual["limit"] != limit or actual["retained_bytes"] != len(raw)
                        or actual["retained_sha256"] != hashlib.sha256(raw).hexdigest()):
                    raise ValueError("retained stream identity")
                result[name + "_bytes"] = raw
            state = result["root_supervision"]
            if (state["group_terminal"] is not True or state["cleanup_errors"]
                    or state["cleanup_deadline_exceeded"] or time.monotonic() > deadline):
                raise RootClientError("ROOT_CLEANUP_OR_ORIGINAL_DEADLINE_INCOMPLETE", result=result)
            broken = False
            return result
        except RootClientError:
            raise
        except Exception as exc:
            raise RootClientError("ROOT_WIRE_INVALID", transport=transport) from exc

    return runner


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--one-request":
        raise SystemExit("one selected request required")
    encoded = sys.argv[2]
    if len(encoded) > 4 * ((REQUEST_CAP + 2) // 3):
        raise SystemExit("request bound")
    request = json.loads(base64.b64decode(encoded, validate=True))
    _namespace_once(lambda: _entry(encoded), request["absolute_deadline"])
