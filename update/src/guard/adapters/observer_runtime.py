"""Source guards and bounded journal for the new observation release.

This release is functional/security instrumentation. Timing remains unqualified
until a separately reviewed on/off calibration receipt names the exact release.
"""
from copy import deepcopy
import functools
import hashlib
import json
import os
from pathlib import Path
import queue
import threading

from . import vllm_cache_callback as cache


def _stamp(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('OBSERVER_SOURCE_REPLACED')
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink


class QualifiedObserver:
    """Initial byte qualification, per-call code/stat guard, final byte check."""
    def _qualified_codes(self, module, targets, sources):
        name, path = vars(module)['__name__'], vars(module)['__file__']
        key = name, path, sources[name]
        with self.lock:
            if not hasattr(self, '_qualified_files'):
                self._qualified_files = {}
            current = self._qualified_files.get(key)
            if current is None:
                before = _stamp(path)
                codes = cache._codes(module, targets, sources)
                after = _stamp(path)
                if before != after:
                    raise ValueError('OBSERVER_SOURCE_REPLACED')
                current = self._qualified_files[key] = after, codes
            if _stamp(path) != current[0]:
                raise ValueError('OBSERVER_SOURCE_REPLACED')
            return dict(current[1])

    def verify_sources(self):
        for (_, path, digest), (stamp, _) in getattr(self, '_qualified_files', {}).items():
            if _stamp(path) != stamp or hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                self.gaps.add('OBSERVER_SOURCE_POSTCHECK_FAILED')

    def wrap(self, module, point):
        """Return an explicitly selected callback; no module/class mutation.

        A future approved profile installer may assign this wrapper. This module
        itself does not prepare assets, update immutable pins or claim deployment.
        """
        name = vars(module).get('__name__')
        if point not in self.targets.get(name, ()):
            raise ValueError('CACHE_CALLBACK_POINT_UNSUPPORTED')
        cls, method, original = cache._function(module, point)
        expected = self._qualified_codes(module, self.targets, self.sources).get(point)
        if expected is None or cache._digest(original.__code__, name) != expected:
            raise ValueError('CACHE_CALLBACK_ENTRY_UNSUPPORTED')

        def qualified():
            current = vars(cls).get(method)
            if os.getpid() != self.pid:
                self.gaps.add('CACHE_CALLBACK_PROCESS_CHANGED'); return False
            return (current in (original, wrapped, getattr(wrapped, 'guard_outer', None)) and original.__globals__ is vars(module)
                and cache._digest(original.__code__, name) == expected and self._qualified_codes(module, self.targets, self.sources).get(point) == expected)

        @functools.wraps(original)
        def wrapped(*args, **kwargs):
            # A fork can inherit a mutex held by a vanished parent thread.
            # Preserve the native call without touching that process's state.
            if os.getpid() != self.pid:
                self.gaps.add('CACHE_CALLBACK_PROCESS_CHANGED')
                return original(*args, **kwargs)
            with self.lock:
                self.sequence += 1; call = self.fingerprint('call:' + str(self.sequence))
            parent = cache._ACTIVE.get()
            parent = parent if parent and parent['observer'] is self and parent['pid'] == os.getpid() and parent['thread'] == threading.get_ident() else None
            started = self._now(); state = 'CALL'; result = None; before = False; snapshot = {}
            try:
                before = bool(qualified())
                if before: snapshot = self._snapshot(point, args, kwargs)
                else: self.gaps.add('CACHE_CALLBACK_ENTRY_REPLACED')
            except BaseException: self.gaps.add('CACHE_CALLBACK_ENTRY_UNAVAILABLE')
            frame = {'observer': self, 'pid': os.getpid(), 'thread': threading.get_ident(), 'call': call,
                'request_sha256': snapshot.get('request_sha256') or (parent['request_sha256'] if parent else None),
                'request_sha256_list': snapshot.get('native_request_sha256') or (parent.get('request_sha256_list') if parent else None)}
            token = cache._ACTIVE.set(frame)
            try:
                common = {'schema': self.schema, 'session_sha256': cache.base.sha(self.session.encode()), 'process_sha256': self.process_sha256,
                    'call_sha256': call, 'parent_call_sha256': parent['call'] if parent else None, 'point': point,
                    'source_file_sha256': self.sources[name], 'entry_code_sha256': expected,
                    'entry_qualified_before': before, 'runtime_context_injected': self.injected_context,
                    'authenticated_identity_verified': False, 'actual_tensor_consumption_verified': False,
                    'transitive_dependencies_verified': False, 'native_profile_installed': False}
                self._emit({**common, 'phase': state, 'operation_started_ns': started, 'operation_finished_ns': started, 'fields': snapshot})
                native_started = self._now()
                try:
                    result = original(*args, **kwargs); state = 'RETURN'; return result
                except BaseException:
                    state = 'RAISE'; raise
                finally:
                    finished = self._now(); after = False; fields = {}
                    try:
                        after = bool(qualified())
                        # Keep independent entry facts when a later observation
                        # fails; the original return/exception stays unchanged.
                        if before and after: fields = self._snapshot(point, args, kwargs, result, state == 'RETURN')
                        elif before: self.gaps.add('CACHE_CALLBACK_TERMINAL_ENTRY_REPLACED')
                    except BaseException: self.gaps.add('CACHE_CALLBACK_TERMINAL_UNAVAILABLE')
                    self._emit({**common, 'phase': state, 'entry_qualified_after': after,
                        'operation_started_ns': started, 'operation_finished_ns': finished,
                        'native_started_ns': native_started, 'native_finished_ns': finished, 'fields': fields})
            finally:
                cache._ACTIVE.reset(token)
        return wrapped


class JournalSink:
    """Bounded callback queue with independently acknowledged capture cuts."""
    MAX_BYTES = 16 * 1024**2
    SEGMENT_BYTES = 1024**2

    def __init__(self, directory):
        self.directory = Path(directory)
        self.pid = os.getpid()
        self.queue = queue.Queue(maxsize=512)
        self.lock, self.state_lock = threading.Lock(), threading.Lock()
        self.accepted = self.written = self.processed = self.dropped = self.errors = 0
        self.closed = False
        self.offsets = {}
        self.pending_checkpoint = None
        self.state = self.directory / f'callbacks-{self.pid}.state.json'
        self.thread = threading.Thread(target=self._run, name='guard-observer-journal', daemon=True)
        self._state(); self.thread.start()

    def __call__(self, row):
        if os.getpid() != self.pid:
            raise ValueError('OBSERVER_JOURNAL_PROCESS_CHANGED')
        error = None
        with self.lock:
            if self.closed or os.getpid() != self.pid:
                self.dropped += 1; error = 'OBSERVER_JOURNAL_CLOSED'
            else:
                try: self.queue.put_nowait(deepcopy(row))
                except queue.Full:
                    self.dropped += 1; error = 'OBSERVER_JOURNAL_QUEUE_FULL'
                else: self.accepted += 1
        if error:
            # Rare loss publication is explicit; normal callbacks do no I/O.
            self._state()
            raise ValueError(error)

    def _state(self):
        with self.state_lock:
            with self.lock:
                value = dict(schema='guard-observer-journal/3', accepted=self.accepted, written=self.written,
                    processed=self.processed, dropped=self.dropped, errors=self.errors, closed=self.closed,
                    measurement_scope='functional_security', timing_qualified=False)
            path = self.state.with_suffix('.tmp')
            with path.open('w') as stream: json.dump(value, stream, separators=(',', ':'))
            os.replace(path, self.state)

    def _checkpoint(self):
        request = self.directory / 'capture-request.json'
        try:
            if request.is_symlink(): raise ValueError('OBSERVER_CAPTURE_REQUEST_INVALID')
            with request.open('rb') as stream: raw = stream.read(1025)
            value = json.loads(raw)
            capture = value['capture_id']
            if len(raw) > 1024 or type(capture) is not str or len(capture) != 32 or any(c not in '0123456789abcdef' for c in capture):
                raise ValueError('OBSERVER_CAPTURE_REQUEST_INVALID')
            ack = self.directory / f'checkpoint-{self.pid}-{capture}.json'
            if not ack.exists() and (self.pending_checkpoint is None or self.pending_checkpoint[0] != capture):
                with self.lock: self.pending_checkpoint = capture, self.accepted
        except FileNotFoundError: pass
        if self.pending_checkpoint is None: return
        capture, cut = self.pending_checkpoint
        with self.lock:
            if self.processed < cut: return
            value = dict(schema='guard-observer-checkpoint/1', capture_id=capture, pid=self.pid,
                accepted=cut, processed=self.processed, written=self.written, dropped=self.dropped,
                errors=self.errors, offsets=dict(self.offsets))
        ack = self.directory / f'checkpoint-{self.pid}-{capture}.json'
        if not ack.exists():
            tmp = ack.with_suffix('.tmp')
            with tmp.open('w') as stream: json.dump(value, stream, separators=(',', ':'))
            os.replace(tmp, ack)
        self.pending_checkpoint = None

    def _run(self):
        segment, segment_bytes, total, fd = 0, 0, 0, None
        try:
            while True:
                self._checkpoint()
                try: row = self.queue.get(timeout=0.05)
                except queue.Empty: continue
                try:
                    if row is None: return
                    raw = (json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n').encode()
                    if total + len(raw) > self.MAX_BYTES or len(raw) > 256 * 1024:
                        with self.lock: self.dropped += 1
                        continue
                    if fd is None or segment_bytes + len(raw) > self.SEGMENT_BYTES:
                        if fd is not None: os.close(fd); segment += 1
                        path = self.directory / f'callbacks-{self.pid}-{segment:04d}.jsonl'
                        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                        segment_bytes = 0
                    offset = 0
                    while offset < len(raw):
                        written = os.write(fd, raw[offset:])
                        if written <= 0: raise OSError('OBSERVER_SHORT_WRITE')
                        offset += written
                    total += len(raw); segment_bytes += len(raw)
                    with self.lock:
                        self.written += 1; self.offsets[path.name] = segment_bytes
                except Exception:
                    with self.lock: self.errors += 1
                finally:
                    if row is not None:
                        with self.lock: self.processed += 1
                    self.queue.task_done(); self._state()
        except Exception:
            with self.lock: self.errors += 1
        finally:
            if fd is not None: os.close(fd)
            self._state()

    def close(self):
        if os.getpid() != self.pid: return
        with self.lock:
            if self.closed: return
            self.closed = True
        try:
            self.queue.put(None, timeout=1); self.thread.join(timeout=5)
        except queue.Full: pass
        if self.thread.is_alive():
            with self.lock: self.errors += 1
        self._state()
