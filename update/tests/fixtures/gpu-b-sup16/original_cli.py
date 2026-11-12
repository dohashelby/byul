"""Actual selected B call_cli/wait/group functions; only own CPU children run."""
from datetime import datetime,timezone

import hashlib,json,os,re,selectors,signal,stat,subprocess,sys,time

from pathlib import Path

def require(value,code):
    if not value:raise ValueError(code)

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

def call_cli(name, argv, stage, update, environment, receipts, *, allowed=(0,), timeout=900):

    started = datetime.now(timezone.utc).isoformat()

    began = time.monotonic()

    row = {'phase': name, 'argv': argv, 'started_at': started,

        'child_reaped': False, 'group_terminal': False}

    receipts.append(row)

    def persist():

        (stage / 'cli-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')

    persist()

    proc = None

    try:

        with ((stage / (name + '.stdout')).open('xb', buffering=0) as stdout,

                (stage / (name + '.stderr')).open('xb', buffering=0) as stderr):

            try:

                proc = subprocess.Popen(argv, cwd=update, env=environment, stdin=subprocess.DEVNULL,

                    stdout=stdout, stderr=stderr, start_new_session=True)

            except OSError as error:

                row.update(no_child_created=True, child_reaped=True, group_terminal=True,

                    launch_error=type(error).__name__ + ': ' + str(error))

                raise

            row['pid'] = proc.pid

            try:

                persist()

            except BaseException:

                row.update(wait_child(proc, 0))

                raise

            row.update(wait_child(proc, timeout))

    except BaseException as error:

        if proc is None:

            row.update(no_child_created=True, child_reaped=True, group_terminal=True,

                launch_error=type(error).__name__ + ': ' + str(error))

        elif row.get('child_reaped') is not True or row.get('group_terminal') is not True:

            row.update(wait_child(proc, 0))

        raise

    finally:

        row.update(ended_at=datetime.now(timezone.utc).isoformat(),

            elapsed_seconds=time.monotonic() - began)

        for channel in ('stdout', 'stderr'):

            path = stage / (name + '.' + channel)

            if path.is_file():

                row[channel + '_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()

        persist()

        print(json.dumps(row), flush=True)

    require(row.get('child_reaped') is True and row.get('group_terminal') is True

        and not row.get('timed_out') and not row.get('interrupted')

        and row.get('returncode') in allowed, 'GB49_CLI_FAILED:' + name)

    return (stage / (name + '.stdout')).read_text()
