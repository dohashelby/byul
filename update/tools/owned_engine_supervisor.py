"""Owned lab PID1 stays alive across engine exits for independent resource reads.

This harness starts an explicitly supplied Python module. It does not identify a
fault, qualify a worker, declare cleanup, or infer successful engine recovery.
Its shutdown action signals only the child retained by its own pidfd. Kubernetes
and the credit guard still own whole-container/node cleanup.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import signal
import socket
import secrets
import stat
import subprocess
import sys
import time

SCHEMA = 'guard-owned-engine-supervisor/1'
MAX_MESSAGE = 8192
MAX_PROCESSES = 4096


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('SUPERVISOR_DUPLICATE_KEY')
        value[key] = item
    return value


def document(raw):
    if not 0 < len(raw) <= MAX_MESSAGE:
        raise ValueError('SUPERVISOR_MESSAGE_BOUND')
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def private_file(path):
    """A fixed-size, owner-only, stable regular file; no symlink traversal."""
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
            or before.st_mode & 0o077 or not 0 < before.st_size <= MAX_MESSAGE):
        raise ValueError('SUPERVISOR_PRIVATE_FILE_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        raw = os.read(fd, MAX_MESSAGE + 1)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if len(raw) != before.st_size or len({identity(s) for s in (before, opened, after, path.lstat())}) != 1:
        raise ValueError('SUPERVISOR_PRIVATE_FILE_CHANGED')
    return document(raw)


@contextmanager
def control_directory(directory):
    """Pin the private directory and keep AF_UNIX names below its path bound.

    Linux resolves the proc FD alias to the held inode. No process-wide chdir,
    public temporary socket, or symlink supplied by a caller is needed.
    """
    before = directory.lstat()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        opened = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_uid, s.st_mode)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                or opened.st_mode & 0o077
                or len({identity(s) for s in (before, opened, directory.lstat())}) != 1):
            raise ValueError('SUPERVISOR_OWNER_UNVERIFIED')
        yield Path('/proc/self/fd', str(fd), 'control.sock')
    finally:
        os.close(fd)


@contextmanager
def child_adoption():
    """Adopt this standalone harness's descendants; restore its own prctl flag.

    No process-wide setting outside this supervisor process is changed. The
    class itself does not enable adoption in a caller such as a test runner.
    """
    library = ctypes.CDLL(None, use_errno=True)
    prctl = library.prctl
    prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    prctl.restype = ctypes.c_int
    previous = ctypes.c_int()
    if prctl(37, ctypes.addressof(previous), 0, 0, 0) or previous.value not in (0, 1):
        raise ValueError('SUPERVISOR_CHILD_ADOPTION_UNAVAILABLE')
    if prctl(36, 1, 0, 0, 0):
        raise ValueError('SUPERVISOR_CHILD_ADOPTION_UNAVAILABLE')
    try:
        yield
    finally:
        if prctl(36, previous.value, 0, 0, 0):
            raise ValueError('SUPERVISOR_CHILD_ADOPTION_RESTORE_FAILED')


def settings(value):
    required = {'schema', 'session', 'key', 'module', 'arguments', 'maximum_seconds', 'maximum_generations'}
    if (type(value) is not dict or set(value) != required or value['schema'] != SCHEMA
            or type(value['session']) is not str or re.fullmatch('[0-9a-f]{32}', value['session']) is None
            or type(value['key']) is not str or re.fullmatch('[0-9a-f]{64}', value['key']) is None
            or type(value['module']) is not str or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,15}', value['module']) is None
            or type(value['arguments']) is not list or len(value['arguments']) > 128
            or any(type(v) is not str or len(v) > 2048 or any(c in v for c in '\0\r\n') for v in value['arguments'])
            or type(value['maximum_seconds']) is not int or not 1 <= value['maximum_seconds'] <= 2700
            or type(value['maximum_generations']) is not int or not 1 <= value['maximum_generations'] <= 8):
        raise ValueError('SUPERVISOR_SETTINGS_INVALID')
    return value


def process(pid):
    raw = Path('/proc', str(pid), 'stat').read_bytes()
    if len(raw) > 8192:
        raise ValueError('SUPERVISOR_PROCESS_BOUND')
    fields = raw[raw.rfind(b')') + 2:].split()
    return {'pid': int(pid), 'start_ticks': int(fields[19]),
            'parent_pid': int(fields[1]), 'group': int(fields[2]), 'session': int(fields[3])}


def group_members(group):
    """Conservative stable namespace scan; unreadable/changing entries refuse restart."""
    first = sorted(int(name) for name in os.listdir('/proc') if name.isdigit())
    if len(first) > MAX_PROCESSES:
        raise ValueError('SUPERVISOR_PROCESS_BOUND')
    rows = []
    for pid in first:
        row = process(pid)
        if row['group'] == group or row['session'] == group:
            if process(pid) != row:
                raise ValueError('SUPERVISOR_PROCESS_GENERATION_CHANGED')
            rows.append(row)
    if first != sorted(int(name) for name in os.listdir('/proc') if name.isdigit()):
        raise ValueError('SUPERVISOR_PROCESS_INVENTORY_CHANGED')
    return rows


@dataclass
class Generation:
    number: int
    child: subprocess.Popen
    pidfd: int
    identity: dict
    launched_ns: int
    observed_exit_ns: int | None = None
    shutdown_requested: bool = False


class Supervisor:
    def __init__(self, config, *, clock=time.monotonic_ns):
        self.config = settings(config)
        self.clock = clock
        self.started_ns = clock()
        self.deadline_ns = self.started_ns + config['maximum_seconds'] * 10**9
        self.generations = []
        self.nonces = set()
        self.reaped = []
        self.reaping_diagnostics = set()

    def reap_exited_descendants(self):
        """Reap only an adopted, exited child in a retained owned session.

        Never signal it, wait on arbitrary PIDs, or reap the Popen-managed root.
        A still-live descendant remains visible and continues to block restart.
        """
        for generation in self.generations:
            try:
                for before in group_members(generation.identity['group']):
                    if before['pid'] == generation.identity['pid'] or before['parent_pid'] != os.getpid():
                        continue
                    if len(self.reaped) >= MAX_PROCESSES:
                        raise ValueError('SUPERVISOR_REAP_RECORD_BOUND')
                    fd = os.pidfd_open(before['pid'])
                    try:
                        if process(before['pid']) != before:
                            raise ValueError('SUPERVISOR_REAP_IDENTITY_CHANGED')
                        event = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG | os.WNOWAIT)
                        if event is None:
                            continue
                        if event.si_pid != before['pid'] or process(before['pid']) != before:
                            raise ValueError('SUPERVISOR_REAP_IDENTITY_CHANGED')
                        reaped = os.waitid(os.P_PIDFD, fd, os.WEXITED | os.WNOHANG)
                        if reaped is None or reaped.si_pid != before['pid']:
                            raise ValueError('SUPERVISOR_REAP_IDENTITY_CHANGED')
                        self.reaped.append({'generation': generation.number, 'pid': before['pid'],
                            'start_ticks': before['start_ticks'], 'group': before['group'], 'session': before['session'],
                            'exit_kind': reaped.si_code, 'exit_status': reaped.si_status, 'observed_ns': self.clock()})
                    finally:
                        os.close(fd)
            except (OSError, ValueError, KeyError, TypeError):
                self.reaping_diagnostics.add('SUPERVISOR_DESCENDANT_REAP_INCOMPLETE')

    def _latest(self):
        return self.generations[-1] if self.generations else None

    def _poll(self, generation):
        code = generation.child.poll()
        if code is not None and generation.observed_exit_ns is None:
            generation.observed_exit_ns = self.clock()
        return code

    def launch(self):
        if self.clock() >= self.deadline_ns:
            raise ValueError('SUPERVISOR_LEASE_EXPIRED')
        if len(self.generations) >= self.config['maximum_generations']:
            raise ValueError('SUPERVISOR_GENERATION_BOUND')
        previous = self._latest()
        if previous is not None:
            if self._poll(previous) is None:
                raise ValueError('SUPERVISOR_PREVIOUS_CHILD_RUNNING')
            self.reap_exited_descendants()
            if group_members(previous.identity['group']):
                raise ValueError('SUPERVISOR_PREVIOUS_GROUP_REMAINS')
        # No shell, inherited stdin, new credentials, environment override or GPU
        # argument is constructed here. Engine-specific module/args are data.
        child = subprocess.Popen([sys.executable, '-m', self.config['module'], *self.config['arguments']],
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        pidfd = None
        try:
            pidfd = os.pidfd_open(child.pid)
            identity = process(child.pid)
            if identity['parent_pid'] != os.getpid() or identity['group'] != child.pid or identity['session'] != child.pid:
                raise ValueError('SUPERVISOR_CHILD_IDENTITY_UNVERIFIED')
            self.generations.append(Generation(len(self.generations), child, pidfd, identity, self.clock()))
        except BaseException:
            # Popen's exact unreaped child only. No guessed PID or group signal.
            if pidfd is not None:
                try:
                    signal.pidfd_send_signal(pidfd, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                finally:
                    os.close(pidfd)
            else:
                child.kill()
            child.wait()
            raise
        return self.status()

    def status(self):
        rows = []
        for generation in self.generations:
            code = self._poll(generation)
            rows.append({'generation': generation.number, 'identity': generation.identity,
                         'launched_ns': generation.launched_ns, 'observed_exit_ns': generation.observed_exit_ns,
                         'returncode': code, 'shutdown_requested': generation.shutdown_requested})
        return {'schema': SCHEMA, 'session': self.config['session'], 'supervisor': process(os.getpid()),
                'observed_ns': self.clock(), 'deadline_ns': self.deadline_ns, 'generations': rows,
                'reaped_descendants': self.reaped[-8:], 'reaped_descendant_count': len(self.reaped),
                'reaped_descendants_sha256': hashlib.sha256(canonical(self.reaped)).hexdigest(),
                'descendant_reaping_diagnostics': sorted(self.reaping_diagnostics),
                'fault_causality_verified': False, 'whole_resources_released': False,
                'new_normal_inference_verified': False}

    def shutdown_child(self):
        generation = self._latest()
        if generation is not None and self._poll(generation) is None:
            generation.shutdown_requested = True
            try:
                signal.pidfd_send_signal(generation.pidfd, signal.SIGTERM)
            except ProcessLookupError:
                pass
        return self.status()

    def handle(self, envelope):
        if type(envelope) is not dict or set(envelope) != {'request', 'hmac_sha256'}:
            raise ValueError('SUPERVISOR_REQUEST_INVALID')
        value = envelope['request']
        if (type(value) is not dict or set(value) != {'session', 'action', 'nonce', 'issued_ns'}
                or value['session'] != self.config['session'] or value['action'] not in {'status', 'launch', 'shutdown-child'}
                or type(value['nonce']) is not str or re.fullmatch('[0-9a-f]{32}', value['nonce']) is None
                or value['nonce'] in self.nonces or len(self.nonces) >= 4096
                or type(value['issued_ns']) is not int or not 0 <= self.clock() - value['issued_ns'] <= 5 * 10**9
                or type(envelope['hmac_sha256']) is not str
                or not hmac.compare_digest(envelope['hmac_sha256'], hmac.new(bytes.fromhex(self.config['key']),
                    b'guard-owned-engine-supervisor-request/1\0' + canonical(value), hashlib.sha256).hexdigest())):
            raise ValueError('SUPERVISOR_REQUEST_UNVERIFIED')
        self.nonces.add(value['nonce'])
        if value['action'] == 'launch':
            return self.launch()
        return self.shutdown_child() if value['action'] == 'shutdown-child' else self.status()

    def close(self):
        self.shutdown_child()
        for generation in self.generations:
            if self._poll(generation) is None:
                try:
                    generation.child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    signal.pidfd_send_signal(generation.pidfd, signal.SIGKILL)
                    generation.child.wait(timeout=3)
            os.close(generation.pidfd)
        self.reap_exited_descendants()


def serve(config_file, directory):
    config = settings(private_file(config_file))
    directory.mkdir(mode=0o700, exist_ok=False)
    if directory.lstat().st_uid != os.geteuid():
        raise ValueError('SUPERVISOR_OWNER_UNVERIFIED')
    with control_directory(directory) as path, child_adoption():
        _serve(config, path)


def _serve(config, path):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    supervisor = Supervisor(config)
    socket_identity = None
    stopping = False
    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True
    previous_handlers = {number: signal.signal(number, stop) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        sock.bind(str(path))
        path.chmod(0o600)
        socket_identity = path.lstat()
        sock.listen(1)
        sock.settimeout(1)
        supervisor.launch()
        while not stopping and supervisor.clock() < supervisor.deadline_ns:
            supervisor.reap_exited_descendants()
            try:
                connection, _ = sock.accept()
            except TimeoutError:
                continue
            with connection:
                connection.settimeout(2)
                try:
                    raw = bytearray()
                    while len(raw) <= MAX_MESSAGE:
                        part = connection.recv(min(4096, MAX_MESSAGE + 1 - len(raw)))
                        if not part:
                            break
                        raw.extend(part)
                    envelope = document(bytes(raw))
                    result = supervisor.handle(envelope)
                    payload = {'nonce': envelope['request']['nonce'], 'result': result}
                    response = {'response': payload, 'hmac_sha256': hmac.new(bytes.fromhex(config['key']),
                        b'guard-owned-engine-supervisor-response/1\0' + canonical(payload), hashlib.sha256).hexdigest()}
                except Exception:
                    response = {'ok': False, 'diagnostic': 'SUPERVISOR_REQUEST_OR_ACTION_REFUSED'}
                connection.sendall(canonical(response))
    finally:
        sock.close()
        try:
            supervisor.close()
        finally:
            for number, handler in previous_handlers.items():
                signal.signal(number, handler)
        # Only the socket exclusively created by this process is removed.
        if socket_identity is not None:
            try:
                current = path.lstat()
            except FileNotFoundError:
                current = None
            if (current is not None and stat.S_ISSOCK(current.st_mode) and current.st_uid == os.geteuid()
                    and (current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino)):
                path.unlink()


def client(config_file, directory, action):
    config = settings(private_file(config_file))
    with control_directory(directory) as path:
        return _client(config, path, action)


def _client(config, path, action):
    value = {'session': config['session'], 'action': action, 'nonce': secrets.token_hex(16), 'issued_ns': time.monotonic_ns()}
    envelope = {'request': value, 'hmac_sha256': hmac.new(bytes.fromhex(config['key']),
        b'guard-owned-engine-supervisor-request/1\0' + canonical(value), hashlib.sha256).hexdigest()}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(3)
        sock.connect(str(path))
        sock.sendall(canonical(envelope))
        sock.shutdown(socket.SHUT_WR)
        raw = bytearray()
        while len(raw) <= MAX_MESSAGE:
            chunk = sock.recv(min(4096, MAX_MESSAGE + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
    response = document(bytes(raw))
    if (type(response) is not dict or set(response) != {'response', 'hmac_sha256'}
            or type(response['response']) is not dict or set(response['response']) != {'nonce', 'result'}
            or response['response']['nonce'] != value['nonce'] or type(response['hmac_sha256']) is not str
            or not hmac.compare_digest(response['hmac_sha256'], hmac.new(bytes.fromhex(config['key']),
                b'guard-owned-engine-supervisor-response/1\0' + canonical(response['response']), hashlib.sha256).hexdigest())):
        raise ValueError('SUPERVISOR_RESPONSE_UNVERIFIED')
    return response['response']['result']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--new-directory', type=Path, required=True)
    parser.add_argument('--action', choices=['serve', 'status', 'launch', 'shutdown-child'], default='serve')
    args = parser.parse_args()
    if args.action == 'serve':
        serve(args.config, args.new_directory)
    else:
        print(json.dumps(client(args.config, args.new_directory, args.action)))


if __name__ == '__main__':
    main()
