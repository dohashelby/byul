"""Transport-independent candidate. No AWS/session API; not operationally ready.

CLI deadlines are absolute CLOCK_MONOTONIC values in deadline-boot-id's domain.
The owner must supply its ORIGINAL deadline, created before authentication and
connection. Neither parser nor CLI creates a new 900-second interval.
"""
import argparse
import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import os
import select
import stat
import sys
import time
import uuid

PREFIX = b"GB12/1 "
CHUNK = 12000
MAX_LINE = 34000
BANNER_LIMIT = 65536
IDENTITY_KEYS = ("dev", "ino", "mode", "uid", "gid", "size", "mtime_ns", "ctime_ns", "nlink")


class StreamError(Exception):
    pass


def require(ok, code):
    if not ok:
        raise StreamError(code)


def canonical_uuid(value, version=None):
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise StreamError("UUID_INVALID") from None
    require(str(parsed) == value and parsed.int != 0 and
            (version is None or parsed.version == version), "UUID_NONCANONICAL")
    return value


def boot_id():
    with open('/proc/sys/kernel/random/boot_id', 'r', encoding='ascii') as f:
        return canonical_uuid(f.read(64).strip())


@dataclass(frozen=True)
class Reference:
    kind: str
    path: str
    size: int
    sha256: str


GB12 = Reference('ACTUAL_GB12',
    '/home/ubuntu/byul-gpu-b-tp2-20261103/GB2026111112-original-evidence.zip',
    14636464, '2681a23c1545647e4b25711cb4c570336efdc6e7a604332de452c9cc2a663219')


@dataclass(frozen=True)
class Deadline:
    end: float

    def remaining(self):
        value = self.end - time.monotonic()
        require(0 < value <= 900, 'ORIGINAL_DEADLINE_EXPIRED_OR_TOO_LONG')
        return value


def identity(fd):
    return stat_identity(os.fstat(fd))


def stat_identity(s):
    return dict(zip(IDENTITY_KEYS, (s.st_dev, s.st_ino, s.st_mode, s.st_uid,
        s.st_gid, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)))


def open_parent(path):
    require(os.name == 'posix' and hasattr(os, 'O_NOFOLLOW'), 'POSIX_REQUIRED')
    require(path.startswith('/') and path != '/', 'ABSOLUTE_PATH_REQUIRED')
    parts = path.split('/')[1:]
    require(all(p not in ('', '.', '..') for p in parts), 'CANONICAL_PATH_REQUIRED')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def named_identity(parent, name):
    return stat_identity(os.stat(name, dir_fd=parent, follow_symlinks=False))


def directory_identity(fd):
    s = os.fstat(fd)
    return (s.st_dev, s.st_ino, s.st_uid, s.st_gid, s.st_mode)


def same_named_parent(path, parent, before):
    other, _ = open_parent(path)
    try:
        require(directory_identity(parent) == directory_identity(other) == before,
                'NAMED_PARENT_IDENTITY_CHANGED')
    finally:
        os.close(other)


def jbytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def emit_json(write, kind, value, deadline):
    deadline.remaining()
    write(PREFIX + kind + b' ' + jbytes(value) + b'\n')
    deadline.remaining()


def _send_reference(ref, request, recovery_boot, deadline, write):
    """Internal engine; CPU reference lives in the separate CPU test source."""
    canonical_uuid(request, 4)
    require(boot_id() == canonical_uuid(recovery_boot), 'RECOVERY_BOOT_MISMATCH')
    deadline.remaining()
    parent, name = open_parent(ref.path)
    fd = None
    try:
        parent_before = directory_identity(parent)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = identity(fd)
        require(stat.S_ISREG(before['mode']) and before['uid'] == 1000 and
                stat.S_IMODE(before['mode']) == 0o600 and before['nlink'] == 1,
                'OWNER1000_REGULAR600_SINGLE_LINK_REQUIRED')
        require(before['size'] == ref.size and ref.size > 0, 'SOURCE_TYPE_OR_SIZE')
        require(before == named_identity(parent, name), 'NAMED_INPUT_IDENTITY_CHANGED')
        digest = hashlib.sha256()
        while True:
            deadline.remaining()
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            digest.update(chunk)
        require(digest.hexdigest() == ref.sha256, 'SOURCE_SHA_MISMATCH')
        require(identity(fd) == before == named_identity(parent, name), 'SOURCE_CHANGED_DURING_PREHASH')
        same_named_parent(ref.path, parent, parent_before)
        os.lseek(fd, 0, os.SEEK_SET)
        binding = {'kind': ref.kind, 'source_path': ref.path, 'bytes': ref.size,
            'sha256': ref.sha256, 'request': request, 'recovery_boot': recovery_boot}
        emit_json(write, b'H', {**binding, 'identity': before}, deadline)
        streamed, count, digest = 0, 0, hashlib.sha256()
        while True:
            deadline.remaining()
            chunk = os.read(fd, CHUNK)
            if not chunk:
                break
            require(streamed + len(chunk) <= ref.size, 'SOURCE_GREW')
            line = PREFIX + b'D ' + request.encode() + b' ' + recovery_boot.encode() + b' ' + \
                str(count).encode() + b' ' + str(len(chunk)).encode() + b' ' + base64.b64encode(chunk) + b'\n'
            write(line)
            deadline.remaining()
            digest.update(chunk)
            streamed += len(chunk)
            count += 1
        after = identity(fd)
        # Verify the selected directory entry still refers to this same open FD.
        require(named_identity(parent, name) == before, 'SOURCE_PATH_REPLACED')
        same_named_parent(ref.path, parent, parent_before)
        require(after == before and streamed == ref.size and digest.hexdigest() == ref.sha256,
                'SOURCE_CHANGED_DURING_STREAM')
        require(boot_id() == recovery_boot, 'RECOVERY_BOOT_CHANGED')
        emit_json(write, b'F', {**binding, 'frames': count, 'identity': after}, deadline)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def send_gb12(request, recovery_boot, deadline, write):
    return _send_reference(GB12, request, recovery_boot, deadline, write)


class Receiver:
    """Feed arbitrary fragments, then call finish ONLY on transport EOF.

    Caller must abort on transport error. A footer alone never publishes output.
    Non-frame session banners are bounded and allowed only outside the stream.
    """
    def __init__(self, output, request, recovery_boot, deadline, *, reference=GB12):
        canonical_uuid(request, 4)
        canonical_uuid(recovery_boot)
        deadline.remaining()
        self.ref, self.request, self.boot, self.deadline = reference, request, recovery_boot, deadline
        self.output = output
        self.parent, self.name = open_parent(output)
        self.temp, self.fd, self.temp_identity = None, None, None
        self.pending, self.banner = bytearray(), 0
        self.state, self.total, self.sequence = 'before', 0, 0
        self.digest, self.source_identity = hashlib.sha256(), None
        self.closed = False
        try:
            self.parent_identity = directory_identity(self.parent)
            p = os.fstat(self.parent)
            require(p.st_uid == os.geteuid() and p.st_mode & 0o077 == 0, 'OUTPUT_PARENT_NOT_PRIVATE_OWNED')
            try:
                os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise StreamError('OUTPUT_EXISTS')
            self.temp = '.gb12-' + uuid.uuid4().hex + '.partial'
            self.fd = os.open(self.temp, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                              0o600, dir_fd=self.parent)
            original = identity(self.fd)
            require(stat.S_ISREG(original['mode']) and stat.S_IMODE(original['mode']) == 0o600 and
                    original['uid'] == os.geteuid() and original['nlink'] == 1, 'NEW_OUTPUT600_REQUIRED')
            self.temp_identity = original
            if os.geteuid() == 0:
                os.fchown(self.fd, 1000, 1000)  # Only our newly created FD.
            owned = identity(self.fd)
            require((owned['dev'], owned['ino']) == (original['dev'], original['ino']) and
                    owned['uid'] == 1000 and stat.S_IMODE(owned['mode']) == 0o600 and
                    owned['nlink'] == 1, 'NEW_OUTPUT_OWNER1000_REQUIRED')
            self.temp_identity = owned
            same_named_parent(self.output, self.parent, self.parent_identity)
        except BaseException:
            self.abort()
            raise

    def _binding(self):
        return {'kind': self.ref.kind, 'source_path': self.ref.path, 'bytes': self.ref.size,
            'sha256': self.ref.sha256, 'request': self.request, 'recovery_boot': self.boot}

    def _json(self, raw):
        try:
            obj = json.loads(raw)
        except (ValueError, UnicodeError):
            raise StreamError('FRAME_JSON_INVALID') from None
        require(type(obj) is dict and jbytes(obj) == raw, 'FRAME_JSON_NOT_CANONICAL')
        return obj

    def _line(self, line):
        if line.endswith(b'\r'):
            line = line[:-1]  # PTY LF -> CRLF translation only.
        if not line.startswith(PREFIX):
            require(self.state in ('before', 'after') and b'GB12/1' not in line, 'UNFRAMED_DATA_OR_INJECTION')
            require(all(c == 9 or 32 <= c < 127 for c in line), 'BANNER_NOT_ASCII_TEXT')
            self.banner += len(line) + 1
            require(self.banner <= BANNER_LIMIT, 'BANNER_LIMIT')
            return
        require(self.state != 'after', 'FRAME_AFTER_FOOTER')
        tag, sep, body = line[len(PREFIX):].partition(b' ')
        require(sep == b' ', 'FRAME_TAG_INVALID')
        if tag == b'H':
            require(self.state == 'before', 'DUPLICATE_HEADER')
            obj = self._json(body)
            ident = obj.get('identity')
            require(type(ident) is dict and set(ident) == set(IDENTITY_KEYS) and
                    all(type(v) is int and v >= 0 for v in ident.values()) and
                    stat.S_ISREG(ident['mode']) and ident['size'] == self.ref.size and
                    ident['uid'] == 1000 and stat.S_IMODE(ident['mode']) == 0o600 and
                    ident['nlink'] == 1, 'SOURCE_IDENTITY_INVALID')
            require(obj == {**self._binding(), 'identity': ident}, 'HEADER_BINDING_MISMATCH')
            self.source_identity, self.state = ident, 'data'
        elif tag == b'D':
            require(self.state == 'data', 'DATA_BEFORE_HEADER')
            fields = body.split(b' ')
            require(len(fields) == 5, 'DATA_FIELDS_INVALID')
            req, boot, seq, length, encoded = fields
            require(req == self.request.encode() and boot == self.boot.encode(), 'DATA_BINDING_MISMATCH')
            require(seq == str(self.sequence).encode(), 'SEQUENCE_MISMATCH')
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise StreamError('BASE64_INVALID') from None
            require(base64.b64encode(raw) == encoded and 0 < len(raw) <= CHUNK and
                    length == str(len(raw)).encode(), 'DATA_LENGTH_OR_ENCODING')
            require(self.total + len(raw) <= self.ref.size, 'TOTAL_BYTES_EXCEEDED')
            view = memoryview(raw)
            while view:
                self.deadline.remaining()
                n = os.write(self.fd, view)
                require(n > 0, 'OUTPUT_SHORT_WRITE')
                view = view[n:]
            self.digest.update(raw)
            self.total += len(raw)
            self.sequence += 1
        elif tag == b'F':
            require(self.state == 'data', 'FOOTER_BEFORE_HEADER')
            obj = self._json(body)
            require(type(obj.get('frames')) is int and obj == {**self._binding(), 'frames': self.sequence, 'identity': self.source_identity},
                    'FOOTER_BINDING_MISMATCH')
            require(self.total == self.ref.size and self.digest.hexdigest() == self.ref.sha256,
                    'FOOTER_TOTAL_OR_SHA_MISMATCH')
            self.state = 'after'
        else:
            raise StreamError('UNKNOWN_FRAME_TAG')

    def feed(self, chunk):
        require(not self.closed and isinstance(chunk, bytes), 'RECEIVER_CLOSED_OR_INPUT_TYPE')
        self.deadline.remaining()
        # Bounded even if the caller gives one very large fragment.
        for start in range(0, len(chunk), 4096):
            self.pending.extend(chunk[start:start + 4096])
            while b'\n' in self.pending:
                i = self.pending.index(10)
                require(i <= MAX_LINE, 'LINE_LIMIT')
                line = bytes(self.pending[:i])
                del self.pending[:i + 1]
                self._line(line)
                self.deadline.remaining()
            require(len(self.pending) <= MAX_LINE, 'LINE_LIMIT')

    def finish(self):
        self.deadline.remaining()
        require(not self.closed and self.state == 'after' and not self.pending, 'INCOMPLETE_STREAM')
        os.fsync(self.fd)
        self.deadline.remaining()
        current = identity(self.fd)
        entry = os.stat(self.temp, dir_fd=self.parent, follow_symlinks=False)
        require((entry.st_dev, entry.st_ino) == (current['dev'], current['ino']) ==
                (self.temp_identity['dev'], self.temp_identity['ino']) and
                current['size'] == self.ref.size and stat.S_IMODE(current['mode']) == 0o600 and
                current['uid'] == 1000 and current['nlink'] == 1 and
                named_identity(self.parent, self.temp) == current, 'OUTPUT_IDENTITY_CHANGED')
        # Verify the exact output FD, not only the previously decoded stream.
        os.lseek(self.fd, 0, os.SEEK_SET)
        stored = hashlib.sha256()
        count = 0
        while True:
            self.deadline.remaining()
            chunk = os.read(self.fd, CHUNK)
            if not chunk:
                break
            count += len(chunk)
            stored.update(chunk)
        require(count == self.ref.size and stored.hexdigest() == self.ref.sha256 and
                identity(self.fd) == current, 'OUTPUT_BYTES_OR_IDENTITY_CHANGED')
        self.deadline.remaining()
        same_named_parent(self.output, self.parent, self.parent_identity)
        require(identity(self.fd)['nlink'] == 1, 'NEW_SCRATCH_LINK_COUNT_CHANGED')
        # Atomic no-overwrite publication; destination symlinks also cause EEXIST.
        os.link(self.temp, self.name, src_dir_fd=self.parent, dst_dir_fd=self.parent, follow_symlinks=False)
        published = named_identity(self.parent, self.name)
        require((published['dev'], published['ino']) == (current['dev'], current['ino']) and
                published['nlink'] == 2 and published == identity(self.fd), 'PUBLISHED_IDENTITY_CHANGED')
        os.unlink(self.temp, dir_fd=self.parent)
        self.temp = None
        output_identity = identity(self.fd)
        require(output_identity['nlink'] == 1 and
                output_identity == named_identity(self.parent, self.name), 'PUBLISHED_FINAL_IDENTITY_CHANGED')
        same_named_parent(self.output, self.parent, self.parent_identity)
        self.closed = True
        os.close(self.fd)
        self.fd = None
        os.close(self.parent)
        self.parent = None
        return {'status': 'received', 'kind': self.ref.kind, 'bytes': self.total,
            'sha256': self.digest.hexdigest(), 'frames': self.sequence,
            'request': self.request, 'recovery_boot': self.boot,
            'source_identity': self.source_identity, 'output_identity': output_identity,
            'product_complete': False, 'producer_authenticated': False}

    def abort(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.temp is not None and self.parent is not None and self.temp_identity is not None:
            try:
                s = os.stat(self.temp, dir_fd=self.parent, follow_symlinks=False)
                if (s.st_dev, s.st_ino) == (self.temp_identity['dev'], self.temp_identity['ino']):
                    os.unlink(self.temp, dir_fd=self.parent)
            except FileNotFoundError:
                pass
        if self.parent is not None:
            os.close(self.parent)
            self.parent = None
        self.closed = True


def receive_fd(fd, receiver):
    original = os.get_blocking(fd)
    try:
        os.set_blocking(fd, False)
        while True:
            ready, _, _ = select.select([fd], [], [], receiver.deadline.remaining())
            require(bool(ready), 'ORIGINAL_DEADLINE_EXPIRED')
            try:
                chunk = os.read(fd, 4096)
            except BlockingIOError:
                continue
            receiver.deadline.remaining()
            if not chunk:
                return receiver.finish()
            receiver.feed(chunk)
    except BaseException:
        receiver.abort()
        raise
    finally:
        os.set_blocking(fd, original)


def write_fd(fd, deadline, data):
    view = memoryview(data)
    while view:
        _, ready, _ = select.select([], [fd], [], deadline.remaining())
        require(bool(ready), 'ORIGINAL_DEADLINE_EXPIRED')
        try:
            n = os.write(fd, view[:4096])
        except BlockingIOError:
            continue
        require(n > 0, 'TRANSPORT_SHORT_WRITE')
        view = view[n:]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=['send', 'receive'])
    p.add_argument('--request', required=True)
    p.add_argument('--recovery-boot', required=True)
    p.add_argument('--deadline-monotonic', type=float, required=True)
    p.add_argument('--deadline-boot-id', required=True)
    p.add_argument('--output')
    a = p.parse_args()
    try:
        require(boot_id() == canonical_uuid(a.deadline_boot_id), 'DEADLINE_CLOCK_BOOT_MISMATCH')
        deadline = Deadline(a.deadline_monotonic)
        require(deadline.remaining() <= 900, 'DEADLINE_EXCEEDS_ORIGINAL_TOTAL')
        if a.mode == 'send':
            require(a.output is None, 'SENDER_OUTPUT_ARGUMENT_FORBIDDEN')
            fd = sys.stdout.fileno()
            original = os.get_blocking(fd)
            try:
                os.set_blocking(fd, False)
                send_gb12(a.request, a.recovery_boot, deadline,
                          lambda data: write_fd(fd, deadline, data))
            finally:
                os.set_blocking(fd, original)
        else:
            require(a.output is not None, 'OUTPUT_REQUIRED')
            result = receive_fd(sys.stdin.fileno(), Receiver(a.output, a.request, a.recovery_boot, deadline))
            sys.stdout.buffer.write(jbytes(result) + b'\n')
        return 0
    except Exception as exc:
        # No captured banner/frame/path/credential values in diagnostics.
        code = str(exc) if isinstance(exc, StreamError) else 'OS_OR_RUNTIME_ERROR'
        sys.stderr.buffer.write(jbytes({'status': 'failed', 'code': code,
            'exception_type': type(exc).__name__, 'errno': getattr(exc, 'errno', None)}) + b'\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
