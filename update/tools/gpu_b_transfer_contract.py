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
