"""회수 전용 local actor wrapper 후보. 실제 API는 final-ready child에서만 가능하다."""
from pathlib import Path
import argparse,datetime,hashlib,json,os,signal,stat,subprocess,sys,time,types
OUT=Path(__file__).parent
PACKET=None

def group_active(pgid):

    for entry in Path('/proc').iterdir():

        if not entry.name.isdigit():

            continue

        try:

            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()

            if int(fields[2]) == pgid and fields[0] != 'Z':

                return True

        except (FileNotFoundError, ProcessLookupError):

            continue

    return False

def wait_child(proc, timeout):

    result = {'timed_out': False, 'child_reaped': False, 'group_terminal': False,

        'cleanup_errors': []}

    error = None

    try:

        result['returncode'] = proc.wait(timeout=timeout)

        result['child_reaped'] = True

    except BaseException as caught:

        error = caught

        result['timed_out'] = isinstance(caught, subprocess.TimeoutExpired)

        result['wait_error'] = type(caught).__name__ + ': ' + str(caught)

    # Kill any remaining guard descendants even when the direct CLI already exited.

    try:

        if group_active(proc.pid):

            os.killpg(proc.pid, signal.SIGKILL)

    except ProcessLookupError:

        pass

    except OSError as caught:

        result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    if not result['child_reaped']:

        try:

            result['returncode'] = proc.wait(timeout=30)

            result['child_reaped'] = True

        except BaseException as caught:

            result['cleanup_errors'].append(type(caught).__name__ + ': ' + str(caught))

    until = time.monotonic() + 30

    while group_active(proc.pid) and time.monotonic() < until:

        time.sleep(.05)

    result['group_terminal'] = not group_active(proc.pid)

    if error is not None and not result['timed_out']:

        result['interrupted'] = True

    return result

def checked_file(relative, expected):
    path = OUT / relative
    assert not any(p.is_symlink() for p in [path, *path.parents])
    meta = path.lstat()
    import stat
    assert stat.S_ISREG(meta.st_mode) and meta.st_uid == 1000 and meta.st_nlink == 1
    assert stat.S_IMODE(meta.st_mode) == 0o600
    raw = path.read_bytes()
    assert len(raw) == expected['bytes'] and hashlib.sha256(raw).hexdigest() == expected['sha256']
    return raw


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def record(name, value):
    with (OUT / name).open('x', encoding='utf-8') as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write('\n')


def start_ticks(pid):
    return int((Path('/proc') / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19])


def load_packet(expected_sha):
    global PACKET
    assert os.getuid() == os.geteuid() == 1000
    assert os.getgid() == 996 and sys.version_info[:2] == (3, 13)
    assert sys.executable == '/usr/local/python3.13/bin/python3'
    assert sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode
    assert Path.home() == Path('/home/cloudshell-user')
    assert OUT.parent == Path.home() and OUT.name.startswith('BYUL-R8-ENVELOPE-')
    assert not any(path.is_symlink() for path in (OUT, *OUT.parents))
    meta = OUT.lstat()
    assert meta.st_uid == 1000 and stat.S_IMODE(meta.st_mode) == 0o700
    os.umask(0o077)
    path = OUT / 'operator-freeze.json'
    size = path.lstat().st_size
    assert 0 < size <= 1024 * 1024
    raw = checked_file(path.name, {'bytes': size, 'sha256': expected_sha})
    packet = json.loads(raw)
    assert packet['purpose'] == 'retained-R8-envelope-recovery-only'
    assert packet['private_path'] == str(OUT) and packet['cli_receipts_path'] == str(OUT / 'CLI')
    assert packet['resource_budget_confirmation_owner'] == 'user'
    assert packet['cost_as_of'] is None and packet['cost_estimate'] is None
    for name, pin in packet['local_files'].items():
        assert Path(name).name == name and '/' not in name and '\\' not in name
        checked_file(name, pin)
    assert packet['local_files'][Path(__file__).name]['sha256'] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    PACKET = packet
    return raw, packet


def loader_from_private(packet):
    name = 'private_loader.py'
    raw = checked_file(name, packet['local_files'][name])
    module = types.ModuleType('s2_verified_private_loader_entry')
    module.__file__ = str(OUT / name)
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return module


def child_outcome(packet, packet_sha, original, result):
    diagnostics = []
    stopped = False
    observed_at = None
    try:
        raw = (OUT / 'recovery-selected-stopped.json').read_bytes()
        assert len(raw) <= 1024 * 1024
        value = json.loads(raw)
        stopped = (value.get('InstanceId') == 'i-05e8da8ea99593196'
                   and value.get('collector_request_id') == packet['collector_request_id']
                   and value.get('state') == 'stopped')
        observed_at = value.get('at') if stopped else None
    except BaseException as error:
        diagnostics.append('Selected-stop evidence: ' + repr(error))
        if original is not None:
            original.add_note(diagnostics[-1])
    return {
        'schema': 'S2-data-only-recovery-child-outcome/1', 'at': now(),
        'packet_sha256': packet_sha, 'collector_request_id': packet['collector_request_id'],
        'selected_stopped_observed': stopped, 'selected_stopped_observed_at': observed_at,
        'exact_off_transition_UTC': None,
        'remaining_resource_stop_verification_required': not stopped,
        'remaining_selected_IP_SSM_observation_required': True,
        'resource_cleanup_or_cost_ledger_complete_claimed': False,
        'original_error': None if original is None else {'type': type(original).__name__,
            'message': str(original), 'notes': list(getattr(original, '__notes__', []))},
        'controller_result': result, 'secondary_diagnostics': diagnostics,
        'current_CLI_reconciliation_pointer': str(OUT / 'CLI'),
        'historical_R8_UNKNOWN_resolved': False,
        'original_archive_restored_or_product_success_claimed': False,
    }


def run_child(runtime, packet_sha):
    parent = json.loads((OUT / 'collect-invocation-once-v1.json').read_bytes())
    assert parent['packet_sha256'] == packet_sha and parent['PID'] == os.getppid()
    assert parent['parent_start_ticks'] == start_ticks(os.getppid())
    original = None
    result = None
    try:
        runtime.adapter.assert_identity()
        runtime.driver.uncertainty_barrier('before-recovery-only-child')
        controller = runtime.controller_module.Controller(
            runtime.driver, runtime.glue, runtime.packet, runtime.codec_raw)
        result = controller.run_once()
    except BaseException as error:
        original = error
    try:
        value = child_outcome(runtime.packet, packet_sha, original, result)
        record('recovery-child-outcome-v1.json', value)
    except BaseException as secondary:
        if original is None:
            raise
        original.add_note('Child outcome preservation failed: ' + repr(secondary))
    if original is not None:
        raise original.with_traceback(original.__traceback__)
    return result


def run_parent(packet, packet_sha):
    record('collect-invocation-once-v1.json', {
        'at': now(), 'PID': os.getpid(), 'PGID': os.getpgid(0),
        'parent_start_ticks': start_ticks(os.getpid()), 'packet_sha256': packet_sha,
        'scope': 'one new data-only recovery child; no model/whole/source installation',
    })
    argv = [sys.executable, '-I', '-S', '-B', str(Path(__file__)), 'child', '--packet-sha256', packet_sha]
    with (OUT / 'collect.stdout').open('xb') as stdout, (OUT / 'collect.stderr').open('xb') as stderr:
        proc = subprocess.Popen(argv, stdout=stdout, stderr=stderr, start_new_session=True)
        try:
            value = {'at': now(), 'PID': proc.pid, 'PGID': proc.pid, 'argv': argv,
                     'packet_sha256': packet_sha}
            try:
                value['local_start_ticks'] = start_ticks(proc.pid)
            except FileNotFoundError:
                if proc.poll() is None:
                    raise
                value.update(local_start_ticks=None, start_ticks_unobserved_child_already_exited=True)
            record('collect-local-PID-v1.json', value)
        except BaseException as original:
            cleanup = wait_child(proc, 0)
            cleanup.update(at=now(), PID=proc.pid, PGID=proc.pid, argv=argv, packet_sha256=packet_sha,
                           local_metadata_error=repr(original), resource_cleanup_complete_claimed=False,
                           remaining_resource_stop_verification_required=True)
            try:
                record('collect-local-terminal-v1.json', cleanup)
            except BaseException as secondary:
                original.add_note('Local terminal preservation failed: ' + repr(secondary))
            raise
        print(json.dumps(value), flush=True)
        terminal = wait_child(proc, None)
    terminal.update(at=now(), PID=proc.pid, PGID=proc.pid, argv=argv, packet_sha256=packet_sha,
                    resource_cleanup_complete_claimed=False,
                    remaining_resource_stop_verification_required=True,
                    original_archive_restored_or_product_success_claimed=False)
    outcome_verified = False
    try:
        raw = (OUT / 'recovery-child-outcome-v1.json').read_bytes()
        assert len(raw) <= 1024 * 1024
        child = json.loads(raw)
        assert child['schema'] == 'S2-data-only-recovery-child-outcome/1'
        assert child['packet_sha256'] == packet_sha
        assert child['collector_request_id'] == packet['collector_request_id']
        assert type(child['selected_stopped_observed']) is bool
        assert child['remaining_resource_stop_verification_required'] is (not child['selected_stopped_observed'])
        error_details = child['original_error']
        assert error_details is None or (
            type(error_details) is dict and set(error_details) == {'type', 'message', 'notes'}
            and type(error_details['type']) is str and type(error_details['message']) is str
            and type(error_details['notes']) is list and all(type(note) is str for note in error_details['notes']))
        observed_fields = {
            'selected_stopped_observed': child['selected_stopped_observed'],
            'remaining_resource_stop_verification_required': child['remaining_resource_stop_verification_required'],
            'remaining_selected_IP_SSM_observation_required': True,
            'original_child_error': error_details,
        }
        terminal.update(observed_fields)
        outcome_verified = True
    except BaseException as error:
        for field in ('selected_stopped_observed', 'remaining_selected_IP_SSM_observation_required', 'original_child_error'):
            terminal.pop(field, None)
        terminal['remaining_resource_stop_verification_required'] = True
        terminal['child_outcome_unverified'] = repr(error)
    record('collect-local-terminal-v1.json', terminal)
    print(json.dumps(terminal), flush=True)
    fully_observed = (terminal['child_reaped'] and terminal['group_terminal']
                      and not terminal['cleanup_errors'] and not terminal.get('interrupted')
                      and not terminal.get('timed_out') and outcome_verified)
    if fully_observed and (terminal['returncode'] != 0 or
                          (terminal.get('selected_stopped_observed') is True
                           and terminal.get('original_child_error') is None)):
        return terminal['returncode']
    return terminal['returncode'] if terminal['returncode'] != 0 else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('check', 'collect', 'child'))
    parser.add_argument('--packet-sha256', required=True)
    args = parser.parse_args()
    packet_raw, packet = load_packet(args.packet_sha256)
    try:
        loader = loader_from_private(packet)
        with loader.load_private(OUT, packet_raw, args.packet_sha256) as runtime:
            if args.mode == 'check':
                print(json.dumps({'source_only_check': True, 'packet_not_ready': packet['not_ready'],
                    'driver_OUT': str(runtime.driver.OUT), 'adapter_OUT': str(runtime.adapter.OUT),
                    'source_sha256': runtime.source_sha256, 'AWS_or_actor_started': False}), flush=True)
                return 0
            assert packet['not_ready'] is False and packet['execution_ready'] is True
            runtime.glue.uuid_text(packet['collector_request_id'])
            assert packet['remote_health_output'] is not None
            if args.mode == 'child':
                run_child(runtime, args.packet_sha256)
                return 0
            return run_parent(packet, args.packet_sha256)
    except BaseException as original:
        try:
            record('recovery-' + args.mode + '-wrapper-error-v1.json', {
                'at': now(), 'packet_sha256': args.packet_sha256,
                'original_error_type': type(original).__name__, 'original_error': str(original),
                'original_notes': list(getattr(original, '__notes__', [])),
                'resource_cleanup_complete_claimed': False,
                'remaining_resource_stop_verification_required': True,
                'current_CLI_reconciliation_pointer': str(OUT / 'CLI'),
            })
        except BaseException as secondary:
            original.add_note('Wrapper failure preservation failed: ' + repr(secondary))
        raise


if __name__ == '__main__':
    raise SystemExit(main())

