"""SUP16 operating call sites, used only by S2's next fixed controller.

No main, resource discovery, factory, policy, source installation or model prep.
open_role/close_role are S2's original create/identity and preserve/quiesce APIs.
The supplied transport, bounded reads, exclusive writes and remaining budget are
the existing controller callbacks, not implementations of those protections.
"""
import base64
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import time

PHASES = ('normal-before', 'challenge-partial', 'normal-recovered')
RESOLVER_SHA = '9a840fb0fa61c9c11adcc3fa4121f778d4d4747f3f00eaf9b87c1a420ce9e340'
BRIDGE_SHA = '4845d897d6f456bc71d23b6b9fb2d327a6f724ab3f9604cd191603d867c08ecb'
READY_SHA = '45046718557d1d2b5a77f210f9a77c23a7d965af92c65ca1a1354b7e8b212fe4'

# Same bounded original session export, restricted to one actually selected
# file. Check the opened FD before/after read and its current directory entry.
EXPORT_ONE = r"""import os,sys,stat,pathlib,hashlib,base64,json,re
session,name=sys.argv[1:3]
assert os.geteuid()==1000 and re.fullmatch('[a-f0-9]{32}',session)
assert re.fullmatch(r'(pid-[1-9][0-9]*-[1-9][0-9]*\.jsonl|boot-record\.json)',name)
root=pathlib.Path('/guard-lifecycle/data')/session
parent=os.open(root,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
try:
 d=os.fstat(parent);assert d.st_uid==d.st_gid==1000 and stat.S_IMODE(d.st_mode)==0o700
 fd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW,dir_fd=parent)
 try:
  a=os.fstat(fd);assert stat.S_ISREG(a.st_mode) and a.st_uid==a.st_gid==1000 and a.st_nlink==1 and a.st_size<=2*1024**2
  assert stat.S_IMODE(a.st_mode)==(0o444 if name=='boot-record.json' else 0o600)
  with os.fdopen(fd,'rb',closefd=False) as f:b=f.read(2*1024**2+1)
  z=os.fstat(fd);p=os.stat(name,dir_fd=parent,follow_symlinks=False)
  identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
  assert identity(a)==identity(z)==identity(p) and len(b)==a.st_size
  assert (d.st_dev,d.st_ino)==(root.lstat().st_dev,root.lstat().st_ino)
  print(json.dumps(dict(name=name,session=session,bytes=len(b),sha256=hashlib.sha256(b).hexdigest(),raw_b64=base64.b64encode(b).decode(),fd_identity=list(identity(z)))))
 finally:os.close(fd)
finally:os.close(parent)
"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _save(c, base, path, body, owner=0):
    """Only new role output directories; final file uses original write_new."""
    base, path = Path(base), Path(path)
    require(path.is_relative_to(base), 'SUP16_JOIN_OUTPUT_SCOPE')
    current = base
    for part in path.relative_to(base).parts[:-1]:
        current /= part
        if not current.exists():
            current.mkdir(mode=0o700)
            os.chown(current, owner, owner)
        s = current.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == owner and s.st_gid == owner
                and stat.S_IMODE(s.st_mode) == 0o700, 'SUP16_JOIN_OUTPUT_DIRECTORY_IDENTITY')
    c.write_new(path, body, owner)


def _module(path, pin, read):
    path = Path(path)
    raw = read(path, 0, 2 * 1024**2)
    require(sha(raw) == pin, 'SUP16_JOIN_ASSET_CHANGED')
    module = importlib.util.module_from_spec(importlib.util.spec_from_file_location('_sup16_join_bridge', path))
    exec(compile(raw, str(path), 'exec', dont_inherit=True), module.__dict__)
    return module


def verified_pod_ready(pod):
    """Only a Pod already checked by the original owner/generation validator."""
    conditions = [row for row in pod.get('status', {}).get('conditions', [])
                  if row.get('type') == 'Ready']
    if len(conditions) != 1 or conditions[0].get('status') != 'True':
        return False
    observed = conditions[0].get('observedGeneration')
    return observed is None or observed == pod['metadata']['generation']


def stage_remaining(c, frame, state):
    bounds = [c.remaining()]
    require(type(bounds[0]) in (int, float) and math.isfinite(bounds[0]),
            'SUP16_JOIN_FINITE_ORIGINAL_PHASE_REMAINING_REQUIRED')
    if state['stage'] in ('bind', 'publish'):
        bounds.append(frame['asset_deadline_monotonic'] - time.monotonic())
    if state['stage'] in ('ready', 'normal'):
        # Existing install start/no-reset proof remains required. Its old120
        # expiry is not a new Ready or request budget.
        require(state['observation_until'] is not None,
                'SUP16_JOIN_ORIGINAL_OBSERVATION_REQUIRED')
        deadline = frame['normal_ready_deadline_monotonic']
        require(type(deadline) in (int, float) and math.isfinite(deadline),
                'SUP16_JOIN_ACTUAL_POD600_READY_DEADLINE_REQUIRED')
        bounds.append(deadline - time.monotonic())
    remaining = min(bounds)
    require(remaining > 0, 'SUP16_JOIN_ORIGINAL_DEADLINE_EXPIRED')
    return remaining


def run_role(c, frame, previous):
    """Consume one actual S2-created role frame; never create a future identity.

    c: original owner/command/kube, read_regular(path,uid,bound),
       write_new(path,bytes), remaining(), verify_preparation(label),
       preparation_argv(argv,stdin=bool). All are S2-bound callbacks.
    frame: actual input pointer, root/runtime paths, selected Python and pinned
       resolver/bridge/old Ready AST file, actual asset90 monotonic deadline.
    previous: actual successful preparation pointers from preceding roles.
    S2 close_role must preserve/quiesce on success AND failure (see run_roles).
    """
    root, runtime = Path(frame['root']), Path(frame['runtime'])
    require(root.is_absolute() and runtime.is_absolute() and not root.is_relative_to(runtime)
            and not runtime.is_relative_to(root), 'SUP16_JOIN_SEPARATE_ROOT_RUNTIME_REQUIRED')
    raw = c.read_regular(Path(frame['inputs']['path']), 1000, 3 * 1024**2)
    require(sha(raw) == frame['inputs']['file_sha256'], 'SUP16_JOIN_INPUT_CHANGED')
    values = json.loads(raw)
    phase = values['phase']
    require(phase == PHASES[len(previous)] and values['previous_roles'] == previous,
            'SUP16_JOIN_ACTUAL_PRIOR_ROLES_REQUIRED')
    require(values['schema'] == 'byul-sup16-role-command-inputs/1', 'SUP16_JOIN_INPUT_SCHEMA')
    for name in ('role_output', 'capture_output'):
        require(Path(values[name]).is_relative_to(runtime) and not Path(values[name]).exists(),
                'SUP16_JOIN_NEW_OWN_RUNTIME_REQUIRED')
    require(Path(frame['inputs']['path']).is_relative_to(runtime), 'SUP16_JOIN_INPUT_SCOPE')
    # Ready controller is DATA. Never execute/import its old installation/main.
    for key, pin in (('resolver', RESOLVER_SHA), ('ready_controller', READY_SHA)):
        # These two files are consumed by the actual owner1000 resolver, unlike
        # the root-only dispatch bridge. Use the existing runtime asset actor.
        require(sha(c.read_regular(Path(frame[key]), 1000, 2 * 1024**2)) == pin,
                'SUP16_JOIN_ASSET_CHANGED')
    bridge = _module(frame['bridge'], BRIDGE_SHA, c.read_regular)
    require(not root.exists(), 'SUP16_JOIN_EXCLUSIVE_ROLE_OUTPUT_REQUIRED')
    root.mkdir(mode=0o700)
    require(root.lstat().st_uid == 0 and stat.S_IMODE(root.lstat().st_mode) == 0o700,
            'SUP16_JOIN_ROOT700_REQUIRED')
    _save(c, root, root / 'actual-inputs.json', raw)
    state = {'stage': 'bind', 'observation_until': None, 'resolved': 0, 'identities': 0}
    partial = {'phase': phase, 'role_preparation': None, 'archive_root': str(root),
               'writer_executed': False, 'full_product_pair_connected': False}

    def left():
        return stage_remaining(c, frame, state)

    def dispatch(spec):
        def save(label, body):
            path = root / 'stdin' / (label + '.bin')
            _save(c, root, path, body)
            return path
        return bridge.dispatch_one(spec, owner=c.owner, command=c.command,
            preparation_argv=c.preparation_argv, verify_preparation=verify,
            read_input=lambda p: c.read_regular(Path(p), 1000, 3 * 1024**2),
            save_stdin=save, remaining=left, python=frame['python'])

    def verify(label):
        actual = c.verify_preparation(label)
        # Preserve what the original callback actually observed, including an
        # unexpectedly Ready challenge. No substitute own_pod/mount policy.
        state['latest_verified_pod'] = actual
        state['identities'] += 1
        _save(c, root, root / 'identity' / ('%03d.json' % state['identities']),
              (json.dumps({'label': label, 'actual': actual}, sort_keys=True) + '\n').encode())
        return actual

    role_path = Path(values['role_output']) / 'role-preparation.json'

    def resolve(stage, **extra):
        state['stage'] = stage
        state['resolved'] += 1
        output = runtime / ('resolved-%03d-%s' % (state['resolved'], stage))
        argv = [frame['python'], '-I', '-B', frame['resolver'], 'bind-commands',
                '--inputs', frame['inputs']['path'], '--inputs-sha256', frame['inputs']['file_sha256'],
                '--stage', stage, '--output', str(output)]
        if stage != 'bind':
            body = c.read_regular(role_path, 1000, 2 * 1024**2)
            argv += ['--role-preparation', str(role_path), '--role-preparation-sha256', sha(body)]
        if stage in ('ready', 'normal'):
            argv += ['--ready-controller', frame['ready_controller'],
                     '--observation-remaining-seconds', str(min(120, left()))]
        for key, value in extra.items():
            argv += ['--' + key.replace('_', '-'), str(value)]
        c.owner('sup16-resolve-%03d-%s' % (state['resolved'], stage), argv, timeout=min(45, left()))
        document = c.read_regular(output / 'commands.json', 1000, 3 * 1024**2)
        result = json.loads(document)
        require(result['schema'] == 'byul-sup16-role-resolved-commands/1'
                and result['stage'] == stage and result['phase'] == phase
                and result['required_input_pins'] == values and result['dispatched'] is False,
                'SUP16_JOIN_RESOLVED_COMMAND_BINDING')
        _save(c, root, root / 'resolved' / (output.name + '.json'), document)
        return result['commands']

    def export_one(session, name, expected_sha=None):
        raw = dispatch({'label': 'sup16-export-' + name.replace('.', '-'), 'actor': 'preparation',
            'argv': ['python3', '-I', '-S', '-B', '-c', EXPORT_ONE, session, name],
            'timeout': 45, 'stdin': None})
        result = json.loads(raw)
        body = base64.b64decode(result['raw_b64'], validate=True)
        require(result['session'] == session and result['name'] == name and len(body) == result['bytes']
                and sha(body) == result['sha256'] and (expected_sha is None or sha(body) == expected_sha),
                'SUP16_JOIN_EXPORTED_FILE_CHANGED')
        # The owner1000 resolver must be able to read the exact exported bytes;
        # a ROOT700 journal pointer would fail that actual consumption contract.
        path = runtime / 'exported-native' / name
        _save(c, runtime, path, body, 1000)
        _save(c, root, root / 'native-originals' / name, body)
        _save(c, root, root / 'native-originals' / (name + '.fd.json'), raw)
        return path, sha(body)

    try:
        verify('sup16-actual-role-before-bind')
        for spec in resolve('bind'):
            dispatch(spec)
        role_raw = c.read_regular(role_path, 1000, 2 * 1024**2)
        role = json.loads(role_raw)
        partial['role_preparation'] = {'path': str(role_path), 'file_sha256': sha(role_raw)}
        for spec in resolve('publish'):
            if '/guard-prepare/native17_install.py' in spec['argv']:
                # Original450467 starts this one120 window BEFORE installation.
                require(state['observation_until'] is None, 'SUP16_JOIN_NO_OBSERVATION_RESET')
                state['observation_until'] = time.monotonic() + 120
            dispatch(spec)
        if phase != 'challenge-partial':
            while True:
                commands = resolve('ready')
                require(len(commands) == 1, 'SUP16_JOIN_SINGLE_READY_REQUIRED')
                health = json.loads(dispatch(commands[0]))
                if health['ready'] and verified_pod_ready(state['latest_verified_pod']):
                    break
                time.sleep(min(2, left()))
            for spec in resolve('normal'):
                control = dispatch(spec)
                _save(c, root, root / 'actual-normal-request.json', control)
        verify('sup16-actual-generation-before-capture')
        for spec in resolve('capture'):
            dispatch(spec)
        capture_dir = Path(values['capture_output'])
        capture_raw = c.read_regular(capture_dir / 'capture.json', 1000, 3 * 1024**2)
        receipt_raw = c.read_regular(capture_dir / 'capture-receipt.json', 1000, 3 * 1024**2)
        analysis = json.loads(c.read_regular(capture_dir / 'native-analysis.json', 1000, 3 * 1024**2))
        # Never select candidates[0] from an ambiguous process set.
        require(len(analysis['candidates']) == 1, 'SUP16_JOIN_ONE_ACTUAL_CANDIDATE_REQUIRED')
        candidate, = analysis['candidates']
        require(type(candidate['pid']) is int and candidate['pid'] > 0
                and type(candidate['start_ticks']) is int and candidate['start_ticks'] > 0,
                'SUP16_JOIN_ACTUAL_PID_START_REQUIRED')
        name = 'pid-%s-%s.jsonl' % (candidate['pid'], candidate['start_ticks'])
        journal, journal_sha = export_one(role['session'], name)
        for spec in resolve('writer', capture=capture_dir / 'capture.json', capture_sha256=sha(capture_raw),
                capture_receipt=capture_dir / 'capture-receipt.json', capture_receipt_sha256=sha(receipt_raw),
                journal=journal, journal_sha256=journal_sha,
                journal_container_path='/guard-lifecycle/data/' + role['session'] + '/' + name):
            writer_raw = dispatch(spec)
            _save(c, root, root / 'actual-writer-result.json', writer_raw)
            writer = json.loads(writer_raw)
            require(writer['record'] == '/guard-lifecycle/data/' + role['session'] + '/boot-record.json',
                    'SUP16_JOIN_ACTUAL_WRITER_PATH_REQUIRED')
            export_one(role['session'], 'boot-record.json', writer['record_sha256'])
            partial['writer_executed'] = True
        return partial
    except BaseException as error:
        error.sup16_role_partial = partial
        raise
    finally:
        # Register actual partial files for original ROOT-rglob archive even if
        # resolver/capture/writer failed. No synthetic positive receipt/Ref.
        active = __import__('sys').exception()
        try:
            for kind, parent in (('role', Path(values['role_output'])), ('capture', Path(values['capture_output']))):
                if parent.exists():
                    for path in sorted(parent.rglob('*')):
                        require(not path.is_symlink(), 'SUP16_JOIN_ARCHIVE_SYMLINK')
                        if path.is_file():
                            body = c.read_regular(path, 1000, 64 * 1024**2)
                            _save(c, root, root / 'runtime-snapshot' / kind / path.relative_to(parent), body)
        except BaseException as later:
            if active is None:
                later.sup16_role_partial = partial
                raise
            active.add_note('SUP16_ARCHIVE_REGISTRATION_ERROR:' + type(later).__name__ + ':' + str(later))


def run_roles(c, *, open_role, close_role):
    """Three call sites inside S2's original lifecycle, with a cleanup barrier.

    open_role(phase, previous) returns an ACTUAL frame after original approved
    template/render/create/probe/FD700/ENV/target operations. close_role(frame,
    partial,error) invokes original preservation, UID/RV cleanup and archive,
    returning its actual cleanup-complete document. S2 supplies both functions;
    they are not replaced by a new resource/privilege/mount implementation here.
    """
    previous, results, first = [], [], None
    for phase in PHASES:
        try:
            frame = open_role(phase, list(previous))
        except BaseException as opening:
            # open_role owns preservation/cleanup of any resources it created
            # before returning a frame. A frame/cleanup success is never made
            # up here. Preserve its actual partial record and original cause.
            results.append({'phase': phase, 'frame_returned': False, 'error': opening,
                            'open_partial': getattr(opening, 'sup16_open_partial', None)})
            if first is not None:
                first.add_note('SUP16_NEXT_ROLE_OPEN_ERROR:' + type(opening).__name__ + ':' + str(opening))
                first.sup16_roles = results
                raise first
            opening.sup16_roles = results
            raise
        error = None
        try:
            result = run_role(c, frame, list(previous))
        except BaseException as original:
            error = original
            result = getattr(original, 'sup16_role_partial', {'phase': phase, 'role_preparation': None})
            if first is None:
                first = original
        results.append({'phase': phase, 'result': result, 'error': error})
        try:
            cleanup = close_role(frame, result, error)
            actual = json.loads(c.read_regular(Path(frame['inputs']['path']), 1000, 3 * 1024**2))
            target = json.loads(c.read_regular(Path(actual['target']['path']), 1000, 3 * 1024**2))
            require(cleanup['normal_UID_RV_delete_and_NotFound'] is True and cleanup['GPU_processes'] == 0
                    and cleanup['force'] is False
                    and cleanup['namespace_uid'] == target['kubernetes']['engine']['namespace_uid']
                    and cleanup['guard_run'] == target['run_id'], 'SUP16_JOIN_ACTUAL_CLEANUP_BARRIER')
        except BaseException as later:
            if first is not None:
                first.add_note('SUP16_CLEANUP_ERROR:' + type(later).__name__ + ':' + str(later))
                first.sup16_roles = results
                raise first
            raise
        if result['role_preparation'] is None:
            break  # Later resolver needs this actual receipt; never invent one.
        previous.append(result['role_preparation'])
    if first is not None:
        first.sup16_roles = results
        raise first
    return results
