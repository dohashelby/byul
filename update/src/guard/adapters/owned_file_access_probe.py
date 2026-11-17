"""Selected-inode, PIDFD-attributed Linux notifications; no file content reads.

Only notification marks are installed, never permission marks. An ACCESS event
is a kernel file-read fact, not parser acceptance or complete supply coverage.
Events missed through mmap, caches, an exited process or overflow remain gaps.
The anchor must come from a separately verified owned container task.

Kernel ABI and observation limits:
https://github.com/torvalds/linux/blob/master/include/uapi/linux/fanotify.h
https://man7.org/linux/man-pages/man7/fanotify.7.html
"""
from __future__ import annotations

from copy import deepcopy
import ctypes
import json
import os
import select
import stat
import struct
import sys
import time

from . import resource_recovery_probe as selected

SELECTION_SCHEMA = 'guard-owned-file-access-selection/1'
SCHEMA = 'guard-owned-file-access-notifications/1'
MAX_FILES, MAX_EVENTS, MAX_INPUT, MAX_OUTPUT = 128, 2048, 65536, 2 * 1024**2
ACCESS, OPEN, EXEC, OVERFLOW = 1, 0x20, 0x1000, 0x4000
EVENT_MASK = ACCESS | OPEN | EXEC
INIT_FLAGS = 1 | 2 | 0x80  # CLOEXEC | NONBLOCK | REPORT_PIDFD, CLASS_NOTIF=0
META = struct.Struct('=IBBHQii')
INFO = struct.Struct('=BBHi')
FILE_FIELDS = {'device', 'inode', 'size', 'mtime_ns', 'ctime_ns', 'uid', 'mode'}
CLOCK_FIELDS = {'boot_sha256', 'time_namespace', 'time_offsets_sha256'}
DIAGNOSTICS = {
    'FILE_ACCESS_QUEUE_OVERFLOW', 'FILE_ACCESS_PROCESS_EXITED_OR_UNAVAILABLE',
    'FILE_ACCESS_FILE_CHANGED', 'FILE_ACCESS_PROCESS_OR_FILE_UNSTABLE',
    'FILE_ACCESS_EVENT_BOUND', 'FILE_ACCESS_EVENT_ATTRIBUTION_UNAVAILABLE',
    'FILE_ACCESS_ANCHOR_OR_FILE_CHANGED',
}
LIMITATIONS = [
    'FILE_ACCESS_MMAP_AND_CACHE_CONSUMPTION_UNOBSERVED',
    'FILE_ACCESS_SELECTED_INODES_ONLY',
    'FILE_ACCESS_PARSER_ACCEPTANCE_UNPROVEN',
    'FILE_ACCESS_ACTUAL_GPU_QUALIFICATION_PENDING',
]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def file_identity(value):
    return dict(zip(('device', 'inode', 'size', 'mtime_ns', 'ctime_ns', 'uid', 'mode'),
                    (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                     value.st_ctime_ns, value.st_uid, value.st_mode)))


def _valid_identity(value):
    return (type(value) is dict and set(value) == FILE_FIELDS
            and all(selected.uint(v) for v in value.values())
            and value['inode'] > 0 and stat.S_ISREG(value['mode']))


def validate_selection(value):
    try:
        if (type(value) is not dict or set(value) != {'schema', 'anchor', 'files'}
                or value['schema'] != SELECTION_SCHEMA):
            raise ValueError
        selected.validate_context(value['anchor'])
        if value['anchor']['pid'] <= 1 or type(value['files']) is not list or not 1 <= len(value['files']) <= MAX_FILES:
            raise ValueError
        ids, inodes = set(), set()
        for row in value['files']:
            if (type(row) is not dict or set(row) != {'id', 'path', 'identity'}
                    or not selected.fingerprint(row['id']) or row['id'] in ids
                    or type(row['path']) is not str or not row['path'].startswith('/')
                    or len(row['path']) > 1024 or any(p in {'', '.', '..'} for p in row['path'].split('/')[1:])
                    or '\0' in row['path'] or not _valid_identity(row['identity'])):
                raise ValueError
            inode = row['identity']['device'], row['identity']['inode']
            if inode in inodes:
                raise ValueError
            ids.add(row['id']); inodes.add(inode)
        if len(canonical(value)) > MAX_INPUT:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise ValueError('FILE_ACCESS_SELECTION_INVALID') from None


def _decode(raw):
    """Decode metadata only. This function never closes any supplied FD number.

    Only NotificationReader passes bytes read directly from its kernel queue to
    the FD-consuming path. Public/caller JSON is never an FD authority.
    """
    if type(raw) is not bytes or not raw or len(raw) > 65536:
        raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
    rows, offset = [], 0
    while offset < len(raw):
        if len(raw) - offset < META.size:
            raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
        length, version, reserved, metadata, mask, fd, pid = META.unpack_from(raw, offset)
        if (version != 3 or reserved or metadata != META.size
                or length > len(raw) - offset or length not in {META.size, META.size + INFO.size}):
            raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
        pidfd = None
        if length == META.size + INFO.size:
            kind, pad, size, pidfd = INFO.unpack_from(raw, offset + metadata)
            if kind != 4 or pad or size != INFO.size:
                raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
        if mask == OVERFLOW:
            if fd != -1 or pid != 0 or pidfd is not None:
                raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
        elif not mask or mask & ~EVENT_MASK or fd < 0 or pid <= 0 or pidfd is None or pidfd < -2:
            raise ValueError('FILE_ACCESS_EVENT_FORMAT_INVALID')
        rows.append({'mask': mask, 'fd': fd, 'pid': pid, 'pidfd': pidfd})
        offset += length
    descriptors = [r[k] for r in rows for k in ('fd', 'pidfd') if r[k] is not None and r[k] >= 0]
    if len(set(descriptors)) != len(descriptors):
        raise ValueError('FILE_ACCESS_DUPLICATE_DESCRIPTOR')
    return rows


def _alive(pidfd):
    poller = select.poll()
    poller.register(pidfd, select.POLLIN | select.POLLERR | select.POLLHUP | select.POLLNVAL)
    return not poller.poll(0)


def _pidfd_pid(pidfd):
    raw = selected.read_small('/proc/self/fdinfo/' + str(pidfd), 4096)
    rows = [r.split(b':', 1)[1].strip() for r in raw.splitlines() if r.startswith(b'Pid:')]
    if len(rows) != 1 or not rows[0].isdigit():
        raise ValueError('FILE_ACCESS_PROCESS_EXITED_OR_UNAVAILABLE')
    return int(rows[0])


class NotificationReader:
    """Owns the queue and all event descriptors; explicitly bounded and read-only."""
    def __init__(self, selection):
        self.selection = deepcopy(validate_selection(selection))
        self.fd, self.pins, self.events, self.gaps = None, [], [], set()
        self.ignored = 0
        self.ready_ns = None
        self.closed = False

    def _stable_pins(self):
        return (len(self.pins) == len(self.selection['files'])
                and all(file_identity(os.fstat(pin)) == row['identity']
                    and file_identity(os.stat(row['path'], follow_symlinks=False)) == row['identity']
                    for pin, row in zip(self.pins, self.selection['files'])))

    def open(self):
        if self.closed or self.fd is not None or sys.platform != 'linux':
            raise ValueError('FILE_ACCESS_READER_STATE_INVALID')
        try:
            if selected.context(self.selection['anchor']['pid']) != self.selection['anchor']:
                raise ValueError('FILE_ACCESS_ANCHOR_CHANGED')
            observer = selected.context(os.getpid())
            if any(observer[k] != self.selection['anchor'][k] for k in CLOCK_FIELDS):
                raise ValueError('FILE_ACCESS_CLOCK_NAMESPACE_UNVERIFIED')
            library = ctypes.CDLL('libc.so.6', use_errno=True)
            initialize, mark = library.fanotify_init, library.fanotify_mark
            initialize.argtypes, initialize.restype = [ctypes.c_uint, ctypes.c_uint], ctypes.c_int
            mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int, ctypes.c_char_p]
            mark.restype = ctypes.c_int
            self.fd = initialize(INIT_FLAGS, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK)
            if self.fd < 0:
                self.fd = None
                raise ValueError('FILE_ACCESS_KERNEL_NOTIFICATIONS_UNAVAILABLE')
            for row in self.selection['files']:
                pin = os.open(row['path'], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
                self.pins.append(pin)
                if file_identity(os.fstat(pin)) != row['identity']:
                    raise ValueError('FILE_ACCESS_FILE_CHANGED')
                # Mark the retained, verified inode, not a subsequently resolved path.
                if mark(self.fd, 1, EVENT_MASK, -100, ('/proc/self/fd/' + str(pin)).encode()) < 0:
                    raise ValueError('FILE_ACCESS_MARK_UNAVAILABLE')
                if file_identity(os.fstat(pin)) != row['identity']:
                    raise ValueError('FILE_ACCESS_FILE_CHANGED')
            if selected.context(self.selection['anchor']['pid']) != self.selection['anchor']:
                raise ValueError('FILE_ACCESS_ANCHOR_CHANGED')
            if not self._stable_pins():
                raise ValueError('FILE_ACCESS_FILE_CHANGED')
            self.ready_ns = time.monotonic_ns()
            return self
        except BaseException:
            self.close()
            raise

    def _event(self, row):
        if row['mask'] == OVERFLOW:
            self.gaps.add('FILE_ACCESS_QUEUE_OVERFLOW'); return
        pidfd = row['pidfd']
        if pidfd < 0:
            self.gaps.add('FILE_ACCESS_PROCESS_EXITED_OR_UNAVAILABLE'); return
        if not _alive(pidfd) or _pidfd_pid(pidfd) != row['pid']:
            self.gaps.add('FILE_ACCESS_PROCESS_EXITED_OR_UNAVAILABLE'); return
        before = selected.context(row['pid'])
        anchor = self.selection['anchor']
        # A shared host file can also be read by another Pod or the observer.
        # Such a read is never assigned to this owned PID namespace.
        if (row['pid'] == os.getpid()
                or any(before[k] != anchor[k] for k in selected.NAMESPACE_FIELDS)
                or before['uid'] != anchor['uid']):
            self.ignored += 1; return
        identity = file_identity(os.fstat(row['fd']))
        files = [r for r in self.selection['files'] if r['identity'] == identity]
        if len(files) != 1:
            self.gaps.add('FILE_ACCESS_FILE_CHANGED'); return
        after = selected.context(row['pid'])
        if (before != after or not _alive(pidfd) or _pidfd_pid(pidfd) != row['pid']
                or identity != file_identity(os.fstat(row['fd']))):
            self.gaps.add('FILE_ACCESS_PROCESS_OR_FILE_UNSTABLE'); return
        if len(self.events) >= MAX_EVENTS:
            self.gaps.add('FILE_ACCESS_EVENT_BOUND'); return
        self.events.append({'seq': len(self.events), 'file_id': files[0]['id'], 'file_identity': identity,
            'process': before, 'received_monotonic_ns': time.monotonic_ns(),
            'opened': bool(row['mask'] & OPEN), 'accessed': bool(row['mask'] & ACCESS),
            'opened_for_exec': bool(row['mask'] & EXEC), 'kernel_pidfd_verified': True})

    def drain(self):
        if self.closed or self.fd is None or self.ready_ns is None:
            raise ValueError('FILE_ACCESS_READER_STATE_INVALID')
        try:
            # Exactly one bounded read per turn; caller's deadline cannot be
            # starved by a busy file or an unselected reader.
            raw = os.read(self.fd, 65536)
        except BlockingIOError:
            return
        rows = _decode(raw)
        try:
            for row in rows:
                try:
                    self._event(row)
                except (OSError, ValueError):
                    self.gaps.add('FILE_ACCESS_EVENT_ATTRIBUTION_UNAVAILABLE')
        finally:
            for row in rows:
                for key in ('fd', 'pidfd'):
                    if row[key] is not None and row[key] >= 0:
                        os.close(row[key])

    def finish(self, *, capture_closed):
        if type(capture_closed) is not bool or self.closed or self.ready_ns is None:
            raise ValueError('FILE_ACCESS_READER_STATE_INVALID')
        stable = False
        try:
            stable = (selected.context(self.selection['anchor']['pid']) == self.selection['anchor']
                and self._stable_pins())
        except (OSError, ValueError):
            pass
        if not stable:
            self.gaps.add('FILE_ACCESS_ANCHOR_OR_FILE_CHANGED')
        packet = {'schema': SCHEMA, 'selection_sha256': selected.sha(canonical(self.selection)),
            'observer': selected.context(os.getpid()), 'anchor': deepcopy(self.selection['anchor']),
            'ready_monotonic_ns': self.ready_ns, 'finished_monotonic_ns': time.monotonic_ns(),
            'files': [{'id': r['id'], 'identity': dict(r['identity'])} for r in self.selection['files']],
            'events': deepcopy(self.events), 'ignored_unselected_events': self.ignored,
            'anchor_and_file_pins_stable': stable, 'capture_closed': capture_closed,
            'diagnostic_codes': sorted(self.gaps), 'limitations': list(LIMITATIONS),
            'resource_contents_read': False, 'permission_decisions_made': False,
            'producer_authenticated': False, 'whole_supply_scope_verified': False,
            'parser_acceptance_verified': False, 'actual_gpu_source_qualified': False}
        validate_packet(packet, self.selection)
        return packet

    def close(self):
        if self.closed:
            return
        self.closed = True
        for fd in ([self.fd] if self.fd is not None else []) + self.pins:
            os.close(fd)
        self.fd, self.pins = None, []


def observe(selection, *, max_seconds=5, on_ready=None):
    if type(max_seconds) is not int or not 1 <= max_seconds <= 60:
        raise ValueError('FILE_ACCESS_BUDGET_INVALID')
    reader = NotificationReader(selection)
    try:
        reader.open()
        deadline = time.monotonic_ns() + max_seconds * 1_000_000_000
        if on_ready is not None:
            on_ready(reader.ready_ns)
        while time.monotonic_ns() < deadline:
            remaining = (deadline - time.monotonic_ns()) / 1_000_000_000
            if select.select([reader.fd], [], [], min(0.2, max(0, remaining)))[0]:
                reader.drain()
        reader.drain()
        return reader.finish(capture_closed=True)
    finally:
        reader.close()


def validate_packet(value, selection):
    try:
        validate_selection(selection)
        false_fields = {'resource_contents_read', 'permission_decisions_made', 'producer_authenticated',
            'whole_supply_scope_verified', 'parser_acceptance_verified', 'actual_gpu_source_qualified'}
        keys = {'schema', 'selection_sha256', 'observer', 'anchor', 'ready_monotonic_ns', 'finished_monotonic_ns',
            'files', 'events', 'ignored_unselected_events', 'anchor_and_file_pins_stable', 'capture_closed',
            'diagnostic_codes', 'limitations'} | false_fields
        if (type(value) is not dict or set(value) != keys or value['schema'] != SCHEMA
                or value['selection_sha256'] != selected.sha(canonical(selection))
                or value['anchor'] != selection['anchor'] or any(value[k] is not False for k in false_fields)
                or value['limitations'] != LIMITATIONS
                or any(type(value[k]) is not bool for k in ('anchor_and_file_pins_stable', 'capture_closed'))
                or not selected.uint(value['ignored_unselected_events'])
                or not selected.uint(value['ready_monotonic_ns'], 1)
                or not selected.uint(value['finished_monotonic_ns'], value['ready_monotonic_ns'])
                or value['files'] != [{'id': r['id'], 'identity': r['identity']} for r in selection['files']]
                or type(value['events']) is not list or len(value['events']) > MAX_EVENTS
                or type(value['diagnostic_codes']) is not list
                or value['diagnostic_codes'] != sorted(set(value['diagnostic_codes']))
                or any(type(c) is not str or c not in DIAGNOSTICS for c in value['diagnostic_codes'])):
            raise ValueError
        selected.validate_context(value['observer'])
        selected.validate_context(value['anchor'])
        if (any(value['observer'][k] != selection['anchor'][k] for k in CLOCK_FIELDS)
                or type(value['files']) is not list
                or any(type(r) is not dict or set(r) != {'id', 'identity'}
                    or not _valid_identity(r['identity']) for r in value['files'])):
            raise ValueError
        files = {r['id']: r['identity'] for r in selection['files']}
        previous = value['ready_monotonic_ns']
        for seq, row in enumerate(value['events']):
            if (type(row) is not dict or set(row) != {'seq', 'file_id', 'file_identity', 'process',
                    'received_monotonic_ns', 'opened', 'accessed', 'opened_for_exec', 'kernel_pidfd_verified'}
                    or type(row['seq']) is not int or row['seq'] != seq or row['file_id'] not in files
                    or not _valid_identity(row['file_identity'])
                    or row['file_identity'] != files[row['file_id']] or row['kernel_pidfd_verified'] is not True
                    or any(type(row[k]) is not bool for k in ('opened', 'accessed', 'opened_for_exec'))
                    or not any(row[k] for k in ('opened', 'accessed', 'opened_for_exec'))
                    or not selected.uint(row['received_monotonic_ns'], previous, value['finished_monotonic_ns'])):
                raise ValueError
            selected.validate_context(row['process'])
            if (any(row['process'][k] != selection['anchor'][k] for k in selected.NAMESPACE_FIELDS)
                    or row['process']['uid'] != selection['anchor']['uid']
                    or row['process']['pid'] == value['observer']['pid']):
                raise ValueError
            previous = row['received_monotonic_ns']
        if len(canonical(value)) > MAX_OUTPUT:
            raise ValueError
        return value
    except (KeyError, TypeError, ValueError):
        raise ValueError('FILE_ACCESS_PACKET_INVALID') from None
