"""Candidate current-proof producer and once-only call of frozen SUP16 entry.

This is not an admitted operating packet. It has not executed on the guest.
The original health/timer, controller preflight, normal-model verifier and
frozen three-role caller remain separate sources of actual evidence.
"""
import argparse
import base64
from contextlib import contextmanager
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import stat
import sys
import time
import types

BASE = Path('/home/ubuntu/byul-gpu-b-tp2-20261103')
CODE = Path('/home/ubuntu/byul-gpu-b-tp2-20261103/SUP16-ROLES-20261111-R8-CODE')
ROOT = Path('/home/ubuntu/byul-gpu-b-tp2-20261103/SUP16-ROLES-20261111-R8-OPERATOR')
HEALTH = Path('/home/ubuntu/byul-gpu-b-tp2-20261103/SUP16-ROLES-20261111-R8-HEALTH')
ENTRY_SHA = '0bd54c6e0d1ace6cf08a6b3d05e6005b44866b692ae8c43e6ecca0056199ac10'
REQUEST_SHA = '6557bab78e0d127c1df2ef6c1ce153172cd5bcd829ef53ea0691658823795584'
PREFIX_SHA = 'd72424507d4abf34517c53c61c31946db15f60de13eb6d0ec106c04b9c052af9'
CHALLENGE_REFERENCE = '17236294c8ce581c3aca501ffd365ea660a2cd6e82689580138d510cafecfca9'
CHALLENGE_CODE_SHA = 'f535863f079a6bedccb4c57bd67f6477d38f94da38858d287011cebf222cd613'
ACTIVE_CALLER = None
ACTUAL_CHILDREN = []
PENDING_HANDOFF_CHILD = None
PENDING_HANDOFF_CLEANUP = None


def require(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_root_pin(path, digest):
    require(path.is_absolute() and not any(p.is_symlink() for p in (path, *path.parents)),
            'PREP_ROOT_PIN_NO_LINKS')
    initial = path.lstat()
    require(stat.S_ISREG(initial.st_mode) and initial.st_size < 3 * 1024**2,
            'PREP_PREOPEN_REGULAR_BOUNDED_REQUIRED')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size < 3 * 1024**2,
                'PREP_ROOT600_PIN_REQUIRED')
        raw = stream.read(3 * 1024**2)
        key = lambda s: (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
                         s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(key(initial) == key(before) == key(os.fstat(stream.fileno())) == key(path.lstat())
                and len(raw) == before.st_size and sha(raw) == digest, 'PREP_ROOT_PIN_CHANGED')
    return raw


def module(raw, name, path):
    result = types.ModuleType(name)
    result.__file__ = str(path)
    sys.modules[name] = result
    exec(compile(raw, str(path), 'exec'), result.__dict__)
    return result


"""Private first-failed-CLI evidence; no execution, resource or verdict API."""
import base64
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid
import zlib

CAPSULE_SCHEMA = 'byul-first-failed-cli-capsule/1'
RAW_LIMIT = 262144
WIRE_LIMIT = 20000  # Existing source-byte wire bound, not the API's larger cap.
CAPSULE_LIMIT = 3 * 1024**2 - 1  # Existing source-file reader bound.


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, allow_nan=False, separators=(',', ':')) + '\n').encode()


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def read_exact(path, *, owner, limit):
    path = Path(path).absolute()
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ValueError('CAPSULE_INPUT_LINK')
    before = path.lstat()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (identity(before) != identity(opened) or not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != owner or opened.st_gid != owner
                or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 1
                or not 0 <= opened.st_size <= limit):
            raise ValueError('CAPSULE_INPUT_IDENTITY_MODE_OR_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        if (len(raw) != opened.st_size or identity(opened) != identity(os.fstat(fd))
                or identity(opened) != identity(path.lstat())):
            raise ValueError('CAPSULE_INPUT_CHANGED')
        return raw
    finally:
        os.close(fd)


def publish_new(path, raw, *, owner):
    """Same exclusive temporary-file/link publication used by the existing SDK."""
    path = Path(path).absolute()
    parent = path.parent
    if any(p.is_symlink() or p.is_junction() for p in (parent, *parent.parents)):
        raise ValueError('CAPSULE_OUTPUT_LINK')
    st = parent.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != owner or st.st_gid != owner
            or stat.S_IMODE(st.st_mode) != 0o700):
        raise ValueError('CAPSULE_PRIVATE_PARENT_REQUIRED')
    temporary = parent / ('.' + path.name + '.' + uuid.uuid4().hex + '.publishing')
    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
    finally:
        temporary.unlink(missing_ok=True)


class FirstFailure:
    """Observe the existing call_cli's first exception without replacing it."""
    def __init__(self):
        self.failure = None

    def attach(self, module):
        original = module.call_cli

        def call(name, argv, stage, update, environment, receipts, **options):
            try:
                return original(name, argv, stage, update, environment, receipts, **options)
            except BaseException as error:
                if self.failure is None:
                    try:
                        row = receipts[-1]
                        if row.get('phase') != name or row.get('argv') != argv:
                            raise ValueError('CAPSULE_SAME_FAILED_COMMAND_REQUIRED')
                        self.failure = dict(stage=Path(stage), row=deepcopy(row),
                            exception={'type': type(error).__name__, 'message': str(error)})
                    except BaseException as secondary:
                        error.add_note('First failure metadata unavailable: ' + type(secondary).__name__)
                raise

        module.call_cli = call

    def write(self, destination, *, binding, owner, input_owner=None):
        if self.failure is None:
            return None
        row, stage = self.failure['row'], self.failure['stage']
        phase = row.get('phase')
        if not isinstance(phase, str) or re.fullmatch('[A-Za-z0-9_-]{1,160}', phase) is None:
            raise ValueError('CAPSULE_PHASE_REQUIRED')
        if row.get('child_reaped') is not True or row.get('group_terminal') is not True:
            raise ValueError('CAPSULE_FAILED_CHILD_NOT_TERMINAL')
        channels = {}
        for channel in ('stdout', 'stderr'):
            path = stage / (phase + '.' + channel)
            raw = read_exact(path, owner=owner, limit=RAW_LIMIT)
            pin = row.get(channel + '_sha256')
            if pin != digest(raw):
                raise ValueError('CAPSULE_ORIGINAL_CHANNEL_SHA_MISMATCH')
            channels[channel] = dict(bytes=len(raw), sha256=pin,
                raw_base64=base64.b64encode(raw).decode('ascii'), complete=True)
        joined = deepcopy(binding)
        argv = row['argv']
        joined['input_context_status'] = 'not supplied by this failed command'
        if input_owner is not None and '--inputs' in argv:
            try:
                if argv.count('--inputs') != 1 or argv.count('--inputs-sha256') != 1:
                    raise ValueError('CAPSULE_INPUT_POINTER_REQUIRED')
                pointer, pin = argv[argv.index('--inputs') + 1], argv[argv.index('--inputs-sha256') + 1]
                input_raw = read_exact(pointer, owner=input_owner, limit=RAW_LIMIT)
                if digest(input_raw) != pin:
                    raise ValueError('CAPSULE_INPUT_POINTER_SHA')
                current = json.loads(input_raw)
                joined.update(input_pointer={'path': pointer, 'file_sha256': pin},
                    context=current.get('context'), source_commit=current.get('source_commit'),
                    source_manifest_sha256=current.get('source_manifest_sha256'),
                    input_context_status='exact original failed-command input bytes')
            except BaseException as error:
                # Losing optional context must not lose the first original stderr.
                joined.update(input_context_status='unavailable', input_context_error=type(error).__name__ + ': ' + str(error))
        value = dict(schema=CAPSULE_SCHEMA, cli_row=row, original_exception=self.failure['exception'],
            binding=joined, channels=channels,
            SSM_CID_requires_original_submission_join=True, product_complete_claimed=False)
        raw = canonical(value)
        publish_new(destination, raw, owner=owner)
        return dict(bytes=len(raw), sha256=digest(raw), same_failed_phase=phase)


def consume(raw, *, expected_binding=None, expected_error=None):
    """Validate exact bytes from a source-file wire entry, never a new verdict."""
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError('CAPSULE_DUPLICATE_KEY')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('CAPSULE_NONFINITE')))
    if (type(value) is not dict or value.get('schema') != CAPSULE_SCHEMA
            or value.get('product_complete_claimed') is not False
            or value.get('SSM_CID_requires_original_submission_join') is not True):
        raise ValueError('CAPSULE_SCHEMA_OR_SCOPE')
    row = value['cli_row']
    if row.get('child_reaped') is not True or row.get('group_terminal') is not True:
        raise ValueError('CAPSULE_FAILED_CHILD_NOT_TERMINAL')
    if not isinstance(row.get('argv'), list) or any(type(a) is not str for a in row['argv']):
        raise ValueError('CAPSULE_ORIGINAL_ARGV')
    if set(value['channels']) != {'stdout', 'stderr'}:
        raise ValueError('CAPSULE_CHANNELS')
    for name, channel in value['channels'].items():
        original = base64.b64decode(channel['raw_base64'], validate=True)
        if (channel.get('complete') is not True or type(channel['bytes']) is not int
                or len(original) != channel['bytes'] or len(original) > RAW_LIMIT
                or digest(original) != channel['sha256']
                or channel['sha256'] != row.get(name + '_sha256')):
            raise ValueError('CAPSULE_ORIGINAL_CHANNEL_BINDING')
    if expected_binding is not None and any(value['binding'].get(k) != v for k, v in expected_binding.items()):
        raise ValueError('CAPSULE_CURRENT_WRAPPER_BINDING')
    if expected_error is not None and (value['original_exception']['type'] != expected_error['type']
            or value['original_exception']['message'][:1600] != expected_error['message']):
        raise ValueError('CAPSULE_SAME_WRAPPER_EXCEPTION')
    return value


def pack_wire(original_pack, result_raw, sources, *, capsule_raw=None, capsule_path=None, diagnostic=None):
    """Keep the entire original wire as-is, adding a separately pinned capsule."""
    original_wire = original_pack(result_raw, sources)
    if capsule_raw is None:
        return original_wire
    try:
        if not 0 < len(capsule_raw) <= CAPSULE_LIMIT or not isinstance(capsule_path, str):
            raise ValueError('CAPSULE_SOURCE_BOUND_OR_PATH')
        consume(capsule_raw)
        value = dict(schema='byul-original-wire-with-first-failure/1', original_wire=original_wire,
            capsule_path=capsule_path, capsule_bytes=len(capsule_raw), capsule_sha256=digest(capsule_raw),
            capsule_zlib_base64=base64.b64encode(zlib.compress(capsule_raw, 9)).decode('ascii'))
        wire = canonical(value).decode().rstrip('\n')
        if len(wire.encode()) > WIRE_LIMIT:
            raise ValueError('CAPSULE_COMBINED_WIRE_BOUND')
        return wire
    except (ValueError, TypeError, KeyError) as error:
        if diagnostic is not None:
            diagnostic(error)
        return original_wire  # Optional diagnostic failure adds no stop gate.


def unpack_wire(original_unpack, wire, *, capsule_path):
    if type(wire) is not str or len(wire.encode()) > WIRE_LIMIT:
        raise ValueError('CAPSULE_COMBINED_WIRE_BOUND')
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise ValueError('CAPSULE_DUPLICATE_KEY')
            value[key] = item
        return value
    value = json.loads(wire, object_pairs_hook=pairs)
    if value.get('schema') != 'byul-original-wire-with-first-failure/1':
        return original_unpack(wire), None
    bundle = original_unpack(value['original_wire'])  # Original terminal guards are authoritative.
    try:
        return bundle, _consume_wire_capsule(value, bundle, capsule_path)
    except (ValueError, TypeError, KeyError, zlib.error) as error:
        return bundle, {'validated': False, 'error': type(error).__name__ + ': ' + str(error),
                        'original_terminal_bundle_validated': True}


def _consume_wire_capsule(value, bundle, capsule_path):
    if (set(value) != {'schema', 'original_wire', 'capsule_path', 'capsule_bytes',
            'capsule_sha256', 'capsule_zlib_base64'} or value['capsule_path'] != capsule_path
            or type(value['capsule_bytes']) is not int or not 0 < value['capsule_bytes'] <= CAPSULE_LIMIT):
        raise ValueError('CAPSULE_WIRE_SCHEMA_OR_SOURCE_PATH')
    compressed = base64.b64decode(value['capsule_zlib_base64'], validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, value['capsule_bytes'] + 1)
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(raw) != value['capsule_bytes'] or digest(raw) != value['capsule_sha256']):
        raise ValueError('CAPSULE_EXACT_ORIGINAL_SOURCE_BYTES')
    result = json.loads(base64.b64decode(bundle['result_json_base64'], validate=True))
    wrapper = result['wrapper']
    capsule = consume(raw, expected_binding={k: wrapper.get(k) for k in ('boot_id', 'configuration_sha256')},
        expected_error={'type': wrapper.get('exception_type'), 'message': wrapper.get('exception_message')})
    return dict(validated=True, path=capsule_path, bytes=len(raw), sha256=digest(raw),
        raw_base64=base64.b64encode(raw).decode('ascii'), value=capsule)


def bind_wire_consumer(original_unpack, wrapper_path):
    def linked(wire):
        bundle, linked.first_failure_capsule = unpack_wire(original_unpack, wire,
            capsule_path=str(Path(wrapper_path).with_name('first-failure-capsule.json')))
        return bundle
    linked.first_failure_capsule = None
    return linked


FIRST_FAILURE = FirstFailure()

def track_actual_children(rc):
    original = rc.subprocess
    proxy = types.SimpleNamespace(**vars(original))
    def popen(*args, **kwargs):
        global PENDING_HANDOFF_CHILD, PENDING_HANDOFF_CLEANUP
        child = original.Popen(*args, **kwargs)
        # Retain the actual handle before the potentially failing list handoff.
        # A failed handoff terminates this caller; finally must still see it.
        PENDING_HANDOFF_CHILD = child
        PENDING_HANDOFF_CLEANUP = None
        try:
            ACTUAL_CHILDREN.append(child)
        except BaseException as primary:
            try:
                PENDING_HANDOFF_CLEANUP = rc.wait_child(child, 0)
            except BaseException as secondary:
                primary.add_note('Actual child handoff cleanup failed: ' + type(secondary).__name__)
            raise
        PENDING_HANDOFF_CHILD = None
        return child
    proxy.Popen = popen
    rc.subprocess = proxy
    FIRST_FAILURE.attach(rc)


def total_from_health(health):
    require(health['health_complete'] is True and health['timer_reset'] is False
            and health['physical_GPU_count'] == 1 and health['prior_GPU_processes'] == 0,
            'PREP_ACTUAL_ORIGINAL_HEALTH_REQUIRED')
    rows = dict(row.split('=', 1) for row in health['timer'].splitlines() if '=' in row)
    require(rows['ActiveState'] == 'active' and rows['Unit'] == 'byul-tp2-resume-budget.service'
            and 'OnActiveUSec=2h' in rows['TimersMonotonic'], 'PREP_ORIGINAL_TIMER_POLICY')
    require(rows['ActiveEnterTimestampMonotonic'].isdigit(), 'PREP_TIMER_MONOTONIC_ANCHOR')
    deadline = int(rows['ActiveEnterTimestampMonotonic']) / 1000000 + 7200
    require(0 < deadline - time.monotonic() <= 7200, 'PREP_ORIGINAL_TIMER_EXPIRED')
    return deadline


class PreparationWindow:
    """Only the original prepare1800, additionally capped by active total2h.

    Each preflight CLI is further capped by original preflight420 below.
    There is no replacement of an existing signal timer or guest systemd unit.
    Original rc CLI exception handling still reaps its actual child group.
    """
    def __init__(self, write_new, total):
        self.write_new, self.total = write_new, total

    def __enter__(self):
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0), 'PREP_PRESERVE_EXISTING_ALARM')
        start = time.monotonic()
        self.deadline = min(start + 1800, self.total)
        require(self.deadline > start, 'PREP_ORIGINAL_WINDOW_EXPIRED')
        self.write_new(ROOT / 'current-proof-preparation-intent.json', {
            'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'phase': 'prepare', 'phase_started_monotonic': start, 'budget_seconds': 1800,
            'total_deadline_monotonic': self.total, 'effective_deadline_monotonic': self.deadline,
            'timer_reset': False}, 0)
        self.saved = signal.getsignal(signal.SIGALRM)
        self.local_deadline = self.deadline
        try:
            signal.signal(signal.SIGALRM, self.expired)
            signal.setitimer(signal.ITIMER_REAL, self.left())
        except BaseException as primary:
            try:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, self.saved)
            except BaseException as secondary:
                primary.add_note('Preparation alarm restoration failed: ' + type(secondary).__name__)
            raise
        return self

    def expired(self, signum, frame):
        raise TimeoutError('PREP_ORIGINAL_PREPARATION_WINDOW_EXPIRED')

    def left(self):
        value = min(self.local_deadline, self.deadline) - time.monotonic()
        require(math.isfinite(value) and value > 0, 'PREP_ORIGINAL_WINDOW_EXPIRED')
        return value

    @contextmanager
    def original_preflight_window(self):
        start = time.monotonic()
        previous = self.local_deadline
        self.local_deadline = min(start + 420, self.deadline)
        self.write_new(ROOT / 'global-preflight-intent.json', {
            'phase': 'preflight', 'phase_started_monotonic': start, 'budget_seconds': 420,
            'effective_deadline_monotonic': self.local_deadline,
            'original_prepare_deadline_monotonic': self.deadline, 'deadline_reset': False}, 0)
        signal.setitimer(signal.ITIMER_REAL, self.left())
        try:
            yield
            self.left()
        finally:
            self.local_deadline = previous
            # Continue the unchanged parent prepare anchor, never now()+1800.
            remaining = self.deadline - time.monotonic()
            if remaining > 0:
                signal.setitimer(signal.ITIMER_REAL, remaining)

    def __exit__(self, kind, error, trace):
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, self.saved)
        return False


def global_preflight(caller, window):
    m = caller.modules
    rc, current = m['root_contract'], m['controller']
    role = caller.outer.roles['normal-before']
    frame = caller.request['aggregate_frame']
    namespace = dict(current.__dict__)
    namespace.update({k: Path(v) if k not in ('RUN',) else v for k, v in frame.items()})
    namespace.update(NODE=role['node'], NS=role['namespace'],
                     K=role['frame']['K'], REPO=Path(role['frame']['REPO']), PY=role['frame']['PY'])
    started = time.monotonic()
    deadline = min(started + 420, window.local_deadline, window.deadline)
    state = rc.preflight(namespace['REPO'], ROOT / 'global-preflight-commands', require_kube=True)
    state['environment']['KUBECONFIG'] = namespace['K'][2]
    context = types.SimpleNamespace(config=caller.static['aggregate_config']['fixed_config'],
        configuration_sha256=caller.static['aggregate_config']['configuration_sha256'],
        serial=0, deadline=deadline, rc=rc, state=state)
    # Reuse the exact original methods, including default45, explicit timeout,
    # allowed=(0,), serial/PG/error guards and owner env-i argv. Only frame
    # globals and the already-active effective deadline are current inputs.
    for name in ('command', 'kube', 'obj', 'owner'):
        original_method = getattr(current.Controller, name)
        rebound = types.FunctionType(original_method.__code__, namespace, name,
                                     original_method.__defaults__)
        rebound.__kwdefaults__ = original_method.__kwdefaults__
        setattr(context, name, types.MethodType(rebound, context))
    original = types.FunctionType(current.Controller.preflight.__code__, namespace, 'original_preflight')
    original(context)
    require(time.monotonic() < deadline, 'PREP_ORIGINAL_PREFLIGHT420_EXPIRED')
    proof_path = ROOT / 'preflight-complete.json'
    proof_raw = current.read_regular(proof_path, 0, 3 * 1024**2)
    value = json.loads(proof_raw)
    require(value['source'] == caller.static['aggregate_config']['fixed_config']['source'],
            'PREP_CURRENT_SOURCE_MISMATCH')
    return context, {'path': str(proof_path), 'owner': 0, 'sha256': sha(proof_raw)}, value


def challenge_model(caller, context, window, code, historical_reference):
    base = json.loads(caller.modules['controller'].read_regular(
        Path(caller.request['base_config']['path']), 0, 3 * 1024**2))
    role = caller.outer.roles['challenge-partial']
    reference = base64.b64decode(historical_reference, validate=True)
    require(sha(reference) == CHALLENGE_REFERENCE, 'PREP_HISTORICAL_CHALLENGE_REFERENCE_CHANGED')
    inputs = {'source': base['model_root'], 'destination': role['model_root'],
              'model_reference': base['model_reference'],
              'reference_manifest_file': {'bytes': 1002, 'sha256': base['model_manifest_sha256']}}
    program = ('import base64,hashlib;raw=base64.b64decode(' + repr(base64.b64encode(code).decode())
               + ');assert hashlib.sha256(raw).hexdigest()==' + repr(sha(code))
               + ";exec(compile(raw,'fixed-challenge-current-copy','exec'))")
    argv = [str(caller.modules['controller'].SYSTEM_PY),
            '-I', '-S', '-B', '-c', program, json.dumps(inputs), historical_reference]
    raw = context.owner('fixed-challenge-current-copy', argv, timeout=min(45, window.left()))
    value = json.loads(raw)
    require(value['source_modified'] is False
            and value['destination'] == role['model_root']
            and value['original_challenge_receipt_sha256'] == CHALLENGE_REFERENCE,
            'PREP_CURRENT_CHALLENGE_COPY_REQUIRED')
    row, = [row for row in value['files'] if row['name'] == 'model.safetensors']
    require(row['bytes'] == 10485760 and row['sha256'] == PREFIX_SHA,
            'PREP_CURRENT_FIXED_PARTIAL_BYTES_REQUIRED')
    path = ROOT / 'challenge-model-current.json'
    caller.modules['controller'].write_new(path, raw, 0)
    return {'path': str(path), 'owner': 0, 'sha256': sha(raw)}


def normal_model(caller, context, window):
    base = json.loads(caller.modules['controller'].read_regular(
        Path(caller.request['base_config']['path']), 0, 3 * 1024**2))
    role = caller.outer.roles['normal-before']
    require(caller.outer.roles['normal-recovered']['model_root'] == role['model_root'] == base['model_root'],
            'PREP_SAME_SELECTED_NORMAL_MODEL_ROOT')
    argv = [role['frame']['PY'], '-I', '-B', '-c',
            base['model_reuse_check'], role['model_root'], json.dumps(base['model_reference']),
            base['model_manifest_sha256'], base['staging_receipt_sha256'], base['materialization_receipt_sha256']]
    raw = context.owner('normal-model-current', argv, timeout=min(90, window.left()))
    value = json.loads(raw)
    require(value['existing_exact_model_reused'] is True and value['model_input_modified'] is False,
            'PREP_CURRENT_MODEL_VERIFIER_REQUIRED')
    path = ROOT / 'normal-model-current.json'
    caller.modules['controller'].write_new(path, raw, 0)
    return {'path': str(path), 'owner': 0, 'sha256': sha(raw)}


def main():
    global ACTIVE_CALLER
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--health-sha256', required=True)
    parser.add_argument('--challenge-code-base64', required=True)
    parser.add_argument('--challenge-code-sha256', required=True)
    parser.add_argument('--challenge-reference-base64', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    require(os.getuid() == os.geteuid() == 0 and sys.version_info[:2] == (3, 12)
            and sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode,
            'PREP_ORIGINAL_ROOT_SYSTEM_PYTHON_FLAGS')
    require(Path('/sys/devices/virtual/dmi/id/board_asset_tag').read_text().strip()
            == 'i-05e8da8ea99593196', 'PREP_EXACT_SELECTED_INSTANCE_REQUIRED')
    os.umask(0o077)
    entry_path = CODE / 'whole_entry.py'
    entry = module(read_root_pin(entry_path, ENTRY_SHA), 'sup16_frozen_entry', entry_path)
    request = json.loads(read_root_pin(CODE / 'whole-fixed-request.json', REQUEST_SHA))
    caller = entry.FrozenCaller(request)
    ACTIVE_CALLER = caller
    track_actual_children(caller.modules['root_contract'])
    challenge_code = base64.b64decode(args.challenge_code_base64, validate=True)
    require(sha(challenge_code) == args.challenge_code_sha256 == CHALLENGE_CODE_SHA,
            'PREP_FROZEN_CHALLENGE_HELPER_CHANGED')
    health_path = HEALTH / 'health-receipt.json'
    health = json.loads(read_root_pin(health_path, args.health_sha256))
    total = total_from_health(health)
    with PreparationWindow(caller.modules['controller'].write_new, total) as window:
        with window.original_preflight_window():
            context, preflight, observed = global_preflight(caller, window)
        require(health['boot_id'] == observed['boot'], 'PREP_CURRENT_BOOT_MISMATCH')
        # The preflight420 subphase ended; continue the original parent
        # prepare anchor, without calculating a replacement deadline.
        context.deadline = window.deadline
        normal = normal_model(caller, context, window)
        challenge = challenge_model(caller, context, window, challenge_code,
                                    args.challenge_reference_base64)
        current = {'health': {'path': str(health_path), 'owner': 0, 'sha256': args.health_sha256},
                   'positive_preflight': preflight,
                   'model_proofs': {'normal-before': normal, 'challenge-partial': challenge,
                                    'normal-recovered': normal},
                   'original_additional_deadlines': {
                       phase: {kind: [] for kind in (('aggregate',) if phase == 'aggregate'
                                                    else ('normal', 'preserve', 'quiesce', 'archive'))}
                       for phase in ('normal-before', 'challenge-partial', 'normal-recovered', 'aggregate')}}
        # Empty lists explicitly mean no additional original window has been
        # configured. Original total, action and actual Pod600 limits remain.
        raw = (json.dumps(current, sort_keys=True, indent=2) + '\n').encode()
        caller.modules['controller'].write_new(ROOT / 'current-inputs.json', raw, 0)
    if args.execute:
        result = caller.execute(current)
        caller.modules['controller'].write_new(ROOT / 'whole-entry-return.json', result, 0)
        print(json.dumps(result), flush=True)
    else:
        print(json.dumps({'current_inputs_path': str(ROOT / 'current-inputs.json'),
                          'current_inputs_sha256': sha(raw), 'roles_started': False}), flush=True)


def record_wrapper_finally(primary):
    require(os.getuid() == os.geteuid() == 0, 'PREP_TERMINAL_ROOT_REQUIRED')
    require(not any(p.is_symlink() for p in (ROOT, *ROOT.parents)), 'PREP_TERMINAL_NO_PATH_LINKS')
    meta = ROOT.lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0
            and stat.S_IMODE(meta.st_mode) == 0o700, 'PREP_TERMINAL_OWN_ROOT700')
    caller = ACTIVE_CALLER
    children = []
    if caller is not None:
        rc = caller.modules['root_contract']
        children = [{'pid': child.pid, 'returncode': child.poll(),
                     'group_terminal': not rc.group_active(child.pid)} for child in ACTUAL_CHILDREN]
        if PENDING_HANDOFF_CHILD is not None and not any(
                row['pid'] == PENDING_HANDOFF_CHILD.pid for row in children):
            children.append({'pid': PENDING_HANDOFF_CHILD.pid,
                'returncode': PENDING_HANDOFF_CHILD.poll(),
                'group_terminal': not rc.group_active(PENDING_HANDOFF_CHILD.pid)})
    handoff_closed = PENDING_HANDOFF_CHILD is None or (
        isinstance(PENDING_HANDOFF_CLEANUP, dict)
        and PENDING_HANDOFF_CLEANUP.get('child_reaped') is True
        and PENDING_HANDOFF_CLEANUP.get('group_terminal') is True
        and PENDING_HANDOFF_CLEANUP.get('cleanup_errors') == [])
    closed = handoff_closed and all(
        row['returncode'] is not None and row['group_terminal'] for row in children)
    phase_blocked = bool(caller is not None and (caller.supervisor.blocked or caller.outer.blocked
                                                or caller.supervisor.active is not None))
    value = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
             'wrapper_pid': os.getpid(), 'actual_children': children,
             'tracked_children_terminal': closed, 'phase_or_outer_blocked': phase_blocked,
             'pending_handoff_child_pid': None if PENDING_HANDOFF_CHILD is None else PENDING_HANDOFF_CHILD.pid,
             'pending_handoff_cleanup': PENDING_HANDOFF_CLEANUP,
             'caller_constructed': caller is not None,
             'configuration_sha256': None if caller is None else caller.static['aggregate_config']['configuration_sha256'],
             'exception_type': None if primary is None else type(primary).__name__,
             'exception_message': None if primary is None else str(primary)[:1600],
             'external_SSM_terminal_still_required': True,
             'product_PASS_or_GPU_measurement_inferred': False}
    try:
        value['first_failure_capsule'] = FIRST_FAILURE.write(
            ROOT / 'first-failure-capsule.json',
            binding={k: value[k] for k in ('boot_id', 'configuration_sha256')},
            owner=0, input_owner=1000)
    except BaseException as diagnostic_error:
        value['first_failure_capsule_error'] = {'type': type(diagnostic_error).__name__, 'message': str(diagnostic_error)}
        if primary is not None:
            primary.add_note('First failure capsule unavailable: ' + type(diagnostic_error).__name__)
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(ROOT / 'current-wrapper-finally.json',
                 os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'wrapper_finally_saved': True, 'tracked_children_terminal': closed,
                      'phase_or_outer_blocked': phase_blocked}), flush=True)


if __name__ == '__main__':
    primary = None
    try:
        main()
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            record_wrapper_finally(primary)
        except BaseException as secondary:
            if primary is None:
                raise
            primary.add_note('Wrapper terminal observation preservation failed: ' + type(secondary).__name__)
