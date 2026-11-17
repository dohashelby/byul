"""EXAMPLE notification packets and real owned stdlib FDs; no GPU claims."""
from copy import deepcopy
import ctypes
import os
import subprocess
import sys

import pytest

from guard.adapters import owned_file_access_probe as probe
from guard.adapters import resource_recovery_probe as selected


def scope(path):
    return {'schema': probe.SELECTION_SCHEMA, 'anchor': selected.context(os.getpid()),
        'files': [{'id': 'a' * 64, 'path': str(path), 'identity': probe.file_identity(path.stat())}]}


def event(fd, pid, pidfd, mask=probe.ACCESS):
    return probe.META.pack(32, 3, 0, 24, mask, fd, pid) + probe.INFO.pack(4, 0, 8, pidfd)


@pytest.fixture
def owned_child():
    child = subprocess.Popen([sys.executable, '-I', '-S', '-c', 'import time;time.sleep(120)'])
    try:
        yield child
    finally:
        if child.poll() is None:
            child.terminate()
        child.wait()


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


@pytest.fixture
def fake_kernel(monkeypatch):
    read, write = os.pipe2(os.O_CLOEXEC | os.O_NONBLOCK)
    calls = []
    def initialize(*args):
        calls.append(('init', args)); return read
    def mark(*args):
        calls.append(('mark', args)); return 0
    library = type('Library', (), {})()
    library.fanotify_init = Function(initialize)
    library.fanotify_mark = Function(mark)
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: library)
    try:
        yield read, write, calls
    finally:
        for fd in (read, write):
            try:
                os.close(fd)
            except OSError:
                pass


def test_decode_never_closes_supplied_fd_numbers(tmp_path, owned_child):
    path = tmp_path / 'known-public-file'; path.write_bytes(b'public fixture')
    fd, pidfd = os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)
    try:
        assert probe._decode(event(fd, owned_child.pid, pidfd))[0]['pidfd'] == pidfd
        assert os.fstat(fd).st_ino == path.stat().st_ino
        assert probe._pidfd_pid(pidfd) == owned_child.pid and probe._alive(pidfd)
    finally:
        os.close(fd); os.close(pidfd)


@pytest.mark.parametrize('mutate', [
    lambda b: b[:4] + bytes([2]) + b[5:],
    lambda b: b[:5] + bytes([1]) + b[6:],
    lambda b: b[:6] + b'\x18\x01' + b[8:],
    lambda b: b[:24] + probe.INFO.pack(1, 0, 8, 88),
    lambda b: b[:24] + probe.INFO.pack(4, 1, 8, 88),
    lambda b: b[:24] + probe.INFO.pack(4, 0, 7, 88),
    lambda b: b[:-1], lambda b: b + b'\0',
    lambda b: probe.META.pack(24, 3, 0, 24, probe.ACCESS, 77, 22),
    lambda b: event(77, 22, 88, 0x10000),  # permission event forbidden
    lambda b: event(77, 22, 77),  # FD aliasing cannot authorize a double close
    lambda b: b + b,
])
def test_closed_kernel_layout_and_notification_only_flags(mutate):
    with pytest.raises(ValueError, match='FILE_ACCESS_'):
        probe._decode(mutate(event(77, 22, 88)))


def test_overflow_is_a_gap_and_missing_process_handles_are_not_attribution():
    raw = probe.META.pack(24, 3, 0, 24, probe.OVERFLOW, -1, 0)
    assert probe._decode(raw) == [{'mask': probe.OVERFLOW, 'fd': -1, 'pid': 0, 'pidfd': None}]
    assert probe._decode(event(77, 22, -1))[0]['pidfd'] == -1
    assert probe._decode(event(77, 22, -2))[0]['pidfd'] == -2


def test_selected_inode_actual_pidfd_and_request_window_do_not_become_supply_pass(tmp_path, owned_child, fake_kernel):
    path = tmp_path / 'known-public-file'; path.write_bytes(b'fixture only')
    selection = scope(path)
    read, write, calls = fake_kernel
    reader = probe.NotificationReader(selection).open()
    fd, pidfd = os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)
    try:
        os.write(write, event(fd, owned_child.pid, pidfd, probe.ACCESS | probe.OPEN))
        reader.drain()
        for closed in (fd, pidfd):
            with pytest.raises(OSError):
                os.fstat(closed)
        packet = reader.finish(capture_closed=True)
        assert packet['events'][0]['process'] == selected.context(owned_child.pid)
        assert packet['events'][0]['namespace_pids'] == probe._namespace_pids(
            selected.read_small('/proc/' + str(owned_child.pid) + '/status', 16384))
        assert packet['events'][0]['file_identity'] == selection['files'][0]['identity']
        assert packet['events'][0]['accessed'] is True and packet['events'][0]['opened'] is True
        assert packet['events'][0]['opened_for_exec'] is False
        assert packet['anchor_and_file_pins_stable'] is True and packet['diagnostic_codes'] == []
        assert all(packet[k] is False for k in ('resource_contents_read', 'permission_decisions_made',
            'producer_authenticated', 'whole_supply_scope_verified', 'parser_acceptance_verified', 'actual_gpu_source_qualified'))
        assert str(path) not in probe.canonical(packet).decode()
        assert calls[0] == ('init', (0x83, os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK))
        mark = calls[1][1]
        assert mark[:4] == (read, 1, probe.EVENT_MASK, -100)
        assert mark[4].startswith(b'/proc/self/fd/') and str(path).encode() not in mark[4]
        assert probe.validate_packet(packet, selection) is packet
    finally:
        reader.close(); reader.close()
    assert owned_child.poll() is None


@pytest.mark.parametrize('mode', ['outside-pod', 'other-uid', 'observer', 'wrong-pidfd', 'exited', 'changed-file', 'missing-pidfd'])
def test_unbound_reader_or_changed_inode_cannot_prove_native_read(mode, tmp_path, owned_child, fake_kernel, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    selection = scope(path); reader = probe.NotificationReader(selection).open()
    original = probe.selected.context
    pid = owned_child.pid
    fd, pidfd = os.open(path, os.O_RDONLY), os.pidfd_open(pid)
    if mode == 'outside-pod':
        def altered(p):
            value = original(p)
            if p == pid: value['pid_namespace'] += 1
            return value
        monkeypatch.setattr(probe.selected, 'context', altered)
    elif mode == 'other-uid':
        def altered(p):
            value = original(p)
            if p == pid: value['uid'] += 1
            return value
        monkeypatch.setattr(probe.selected, 'context', altered)
    elif mode == 'observer':
        pid = os.getpid(); os.close(pidfd); pidfd = os.pidfd_open(pid)
    elif mode == 'wrong-pidfd':
        os.close(pidfd); pidfd = os.pidfd_open(os.getpid())
    elif mode == 'exited':
        owned_child.terminate(); owned_child.wait()
    elif mode == 'changed-file':
        path.write_bytes(b'changed public file')
    elif mode == 'missing-pidfd':
        os.close(pidfd); pidfd = -1
    try:
        os.write(fake_kernel[1], event(fd, pid, pidfd))
        reader.drain()
        packet = reader.finish(capture_closed=True)
        assert packet['events'] == []
        assert packet['ignored_unselected_events'] == int(mode in {'outside-pod', 'other-uid', 'observer'})
        assert bool(packet['diagnostic_codes']) is (mode not in {'outside-pod', 'other-uid', 'observer'})
    finally:
        reader.close()


def test_all_fds_are_closed_even_if_recording_is_interrupted(tmp_path, owned_child, fake_kernel, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    reader = probe.NotificationReader(scope(path)).open()
    descriptors = [(os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)) for _ in range(2)]
    def stop(row): raise KeyboardInterrupt('test interruption')
    monkeypatch.setattr(reader, '_event', stop)
    try:
        os.write(fake_kernel[1], b''.join(event(fd, owned_child.pid, pidfd) for fd, pidfd in descriptors))
        with pytest.raises(KeyboardInterrupt): reader.drain()
        for pair in descriptors:
            for fd in pair:
                with pytest.raises(OSError): os.fstat(fd)
    finally:
        reader.close()


@pytest.mark.parametrize('field', ['pid', 'start_ticks', 'uid', 'pid_namespace', 'mount_namespace', 'ipc_namespace', 'time_namespace'])
def test_stale_anchor_is_rejected_before_kernel_setup(tmp_path, field, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    selection = scope(path); selection['anchor'][field] += 1
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: pytest.fail('No kernel initialization for stale anchor'))
    reader = probe.NotificationReader(selection)
    with pytest.raises((ValueError, OSError)): reader.open()
    assert reader.closed and reader.fd is None and reader.pins == []


@pytest.mark.parametrize('mutation', [
    lambda s: s.update(fd=77),
    lambda s: s['files'][0].update(path='/tmp/../private'),
    lambda s: s['files'].append(deepcopy(s['files'][0])),
    lambda s: s['files'][0]['identity'].update(mode=0o40700),
    lambda s: s['anchor'].update(pid=True),
    lambda s: s['files'][0]['identity'].update(size=-1),
])
def test_no_caller_fd_directory_ambiguous_path_or_bool_identifier(tmp_path, mutation):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    selection = scope(path); mutation(selection)
    with pytest.raises(ValueError, match='FILE_ACCESS_SELECTION_INVALID'): probe.validate_selection(selection)


def test_changed_inode_before_mark_never_silently_rebinds(tmp_path, fake_kernel):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    selection = scope(path); path.rename(tmp_path / 'old'); path.write_bytes(b'public')
    reader = probe.NotificationReader(selection)
    with pytest.raises(ValueError, match='FILE_ACCESS_FILE_CHANGED'): reader.open()
    assert reader.closed and reader.fd is None and reader.pins == []
    assert [kind for kind, args in fake_kernel[2]] == ['init']


def test_failed_permission_or_unsupported_kernel_is_not_a_capture(tmp_path, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    library = type('Library', (), {})()
    library.fanotify_init = Function(lambda *args: -1)
    library.fanotify_mark = Function(lambda *args: pytest.fail('No marks after failed init'))
    monkeypatch.setattr(ctypes, 'CDLL', lambda *a, **kw: library)
    with pytest.raises(ValueError, match='FILE_ACCESS_KERNEL_NOTIFICATIONS_UNAVAILABLE'):
        probe.observe(scope(path))


def test_path_replacement_after_mark_keeps_old_inode_distinct(tmp_path, fake_kernel):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    reader = probe.NotificationReader(scope(path)).open()
    try:
        path.rename(tmp_path / 'old'); path.write_bytes(b'public')
        packet = reader.finish(capture_closed=True)
        assert packet['anchor_and_file_pins_stable'] is False
        assert packet['diagnostic_codes'] == ['FILE_ACCESS_ANCHOR_OR_FILE_CHANGED']
    finally:
        reader.close()


@pytest.mark.parametrize('damage', ['selected-file', 'process', 'clock', 'sequence', 'future', 'bool-size',
    'bool-uid', 'claim', 'path-leak', 'not-event', 'unclosed', 'namespace-map', 'bool-namespace-pid'])
def test_public_packet_is_closed_and_does_not_accept_added_authority(damage, tmp_path, owned_child, fake_kernel):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    selection = scope(path); reader = probe.NotificationReader(selection).open()
    fd, pidfd = os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)
    try:
        os.write(fake_kernel[1], event(fd, owned_child.pid, pidfd))
        reader.drain(); packet = reader.finish(capture_closed=True)
    finally:
        reader.close()
    if damage == 'selected-file': packet['events'][0]['file_id'] = 'b' * 64
    elif damage == 'process': packet['events'][0]['process']['mount_namespace'] += 1
    elif damage == 'clock': packet['observer']['time_namespace'] += 1
    elif damage == 'sequence': packet['events'][0]['seq'] = 1
    elif damage == 'future': packet['events'][0]['received_monotonic_ns'] = packet['finished_monotonic_ns'] + 1
    elif damage == 'bool-size': packet['events'][0]['file_identity']['uid'] = bool(packet['events'][0]['file_identity']['uid'])
    elif damage == 'bool-uid': packet['anchor']['uid'] = bool(packet['anchor']['uid'])
    elif damage == 'claim': packet['actual_gpu_source_qualified'] = True
    elif damage == 'path-leak': packet['files'][0]['path'] = str(path)
    elif damage == 'not-event': packet['events'][0]['accessed'] = False
    elif damage == 'unclosed': packet['capture_closed'] = 1
    elif damage == 'namespace-map': packet['events'][0]['namespace_pids'][0] += 1
    elif damage == 'bool-namespace-pid': packet['events'][0]['namespace_pids'][-1] = True
    with pytest.raises(ValueError, match='FILE_ACCESS_PACKET_INVALID'): probe.validate_packet(packet, selection)


def test_event_bound_and_overflow_preserve_partial_facts_without_complete_supply(tmp_path, owned_child, fake_kernel, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    reader = probe.NotificationReader(scope(path)).open()
    monkeypatch.setattr(probe, 'MAX_EVENTS', 1)
    fds = [(os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)) for _ in range(2)]
    try:
        raw = b''.join(event(fd, owned_child.pid, pidfd) for fd, pidfd in fds)
        raw += probe.META.pack(24, 3, 0, 24, probe.OVERFLOW, -1, 0)
        os.write(fake_kernel[1], raw); reader.drain()
        packet = reader.finish(capture_closed=True)
        assert len(packet['events']) == 1
        assert packet['diagnostic_codes'] == ['FILE_ACCESS_EVENT_BOUND', 'FILE_ACCESS_QUEUE_OVERFLOW']
        assert packet['whole_supply_scope_verified'] is False
    finally:
        reader.close()


def test_single_drain_is_bounded_and_empty_queue_does_not_block(tmp_path, fake_kernel):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    reader = probe.NotificationReader(scope(path)).open()
    try:
        reader.drain()
        assert reader.events == [] and reader.gaps == set()
    finally:
        reader.close()


@pytest.mark.parametrize('raw', [b'', b'NSpid:\t1\nNSpid:\t1\n', b'NSpid:\t-1\n',
    b'NSpid:\t0\n', b'NSpid:\t1 invalid\n', b'NSpid:\t' + b'1 ' * 33 + b'\n'])
def test_missing_or_ambiguous_namespace_pid_chain_is_not_a_mapping(raw):
    with pytest.raises(ValueError, match='FILE_ACCESS_PROCESS_NAMESPACE_UNAVAILABLE'): probe._namespace_pids(raw)


def test_pidfd_namespace_pid_and_live_proc_must_agree(tmp_path, owned_child, fake_kernel, monkeypatch):
    path = tmp_path / 'known-file'; path.write_bytes(b'public')
    reader = probe.NotificationReader(scope(path)).open()
    fd, pidfd = os.open(path, os.O_RDONLY), os.pidfd_open(owned_child.pid)
    original = probe._pidfd_identity
    def wrong_namespace(fd):
        value = original(fd); value['namespace_pids'].append(9999); return value
    monkeypatch.setattr(probe, '_pidfd_identity', wrong_namespace)
    try:
        os.write(fake_kernel[1], event(fd, owned_child.pid, pidfd)); reader.drain()
        packet = reader.finish(capture_closed=True)
        assert packet['events'] == []
        assert packet['diagnostic_codes'] == ['FILE_ACCESS_PROCESS_OR_FILE_UNSTABLE']
    finally:
        reader.close()
