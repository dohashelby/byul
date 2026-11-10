"""Explicit user-service handover. Run as the existing observation service owner."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import subprocess
import stat
import time

FEED = 'byul-observation-feed.service'
STREAM = 'byul-tetragon-stream.service'


def command(*args):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('USER_SERVICE_COMMAND_FAILED')



def read_file(path, limit=1024*1024):
    """Reject symlinks/special files without blocking and cap every input read."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or not 0 <= before.st_size <= limit:
            raise ValueError('BOUNDED_REGULAR_INPUT_REQUIRED')
        with os.fdopen(descriptor, 'rb', closefd=False) as stream:
            raw = stream.read(limit+1)
        after = os.fstat(descriptor)
        current = path.lstat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(raw) != before.st_size or identity(before) != identity(after) or identity(after) != identity(current):
            raise ValueError('INPUT_CHANGED_DURING_READ')
        return raw
    finally:
        os.close(descriptor)


def atomic(path, value):
    temporary = path.with_name(path.name + '.next')
    with temporary.open('xb') as stream:
        os.chmod(temporary, 0o600)
        stream.write(value)
    temporary.replace(path)


def ready(heartbeat, session, prepared, started, now):
    """Freshness and exact stream scope; an old legacy heartbeat cannot qualify."""
    try:
        at = datetime.fromisoformat(heartbeat['updated_at'].replace('Z', '+00:00')).timestamp()
        began = datetime.fromisoformat(heartbeat['started_at'].replace('Z', '+00:00')).timestamp()
        processes = heartbeat['processes']
        return (heartbeat['schema'] == 'runtime-observation-feed-heartbeat/1.0'
                and heartbeat['kind'] == 'tetragon' and heartbeat['state'] == 'running'
                and heartbeat['kube_context'] == prepared['context']
                and heartbeat['feed_path'] == '/srv/serving/observation/tetragon.jsonl'
                and started <= began <= at <= now and now - at <= 10
                and heartbeat.get('open_gap') is None
                and isinstance(processes, list) and bool(processes)
                and all(p.get('running') is True and p.get('returncode') is None for p in processes)
                and session['context'] == prepared['context']
                and session['namespace'] == prepared['namespace']
                and sorted(session['policies']) == sorted(prepared['policies']))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def _rollout(prepared_dir, *, unit_dir=None, observation=Path('/srv/serving/observation'), timeout=45):
    if not 1 <= timeout <= 120:
        raise ValueError('BOUNDED_TIMEOUT_REQUIRED')
    prepared_dir = Path(prepared_dir)
    prepared = json.loads(read_file(prepared_dir / 'prepared.json'))
    stream = read_file(prepared_dir / STREAM)
    if hashlib.sha256(stream).hexdigest() != prepared['sha256'][STREAM]:
        raise ValueError('PREPARED_UNIT_HASH_MISMATCH')
    units = unit_dir or Path.home() / '.config/systemd/user'
    feed_path, stream_path = units / FEED, units / STREAM
    # Fresh handover only; preserve custom existing stream deployments for review.
    if stream_path.exists() or stream_path.is_symlink() or feed_path.is_symlink():
        raise ValueError('EXISTING_OR_SYMLINK_UNIT_REQUIRES_REVIEW')
    before = read_file(feed_path)
    if b'--hubble-only' in before or before.count(b'ExecStart=') != 1:
        raise ValueError('ACTIVE_LEGACY_FEED_REQUIRED')
    lines = before.splitlines(keepends=True)
    command_line = next(line for line in lines if line.startswith(b'ExecStart='))
    if (not command_line.startswith(b'ExecStart=/opt/byul/runtime/.venv/bin/python -B tools/k8s_observation_feed.py ')
            or b' --reader-group ' not in command_line):
        raise ValueError('LEGACY_FEED_ARGUMENT_CONTRACT_MISMATCH')
    after = b''.join(line.replace(b' --reader-group ', b' --hubble-only --reader-group ', 1)
                     if line is command_line else line for line in lines)
    command('is-active', '--quiet', FEED)
    state_path = observation / 'tetragon-rollout.json'
    started = time.time()
    def report(state):
        atomic(state_path, json.dumps({'schema':'runtime-tetragon-rollout/1', 'state':state,
               'started_at':started, 'updated_at':time.time()}, sort_keys=True).encode())
    report('handover_in_progress')
    try:
        command('stop', FEED)
        atomic(stream_path, stream)
        command('daemon-reload')
        command('start', STREAM)
        deadline = time.monotonic() + timeout
        while True:
            try:
                heartbeat = json.loads(read_file(observation / 'tetragon.heartbeat.json'))
                session = json.loads(read_file(observation / 'stream-session.json'))
            except (OSError, ValueError):
                heartbeat, session = {}, {}
            if ready(heartbeat, session, prepared, started, time.time()):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('FRESH_SCOPED_STREAM_NOT_READY')
            time.sleep(.2)
        atomic(feed_path, after)
        command('daemon-reload')
        command('start', FEED)
        command('is-active', '--quiet', FEED)
        command('is-active', '--quiet', STREAM)
        if not ready(json.loads(read_file(observation / 'tetragon.heartbeat.json')),
                     json.loads(read_file(observation / 'stream-session.json')),
                     prepared, started, time.time()):
            raise RuntimeError('STREAM_LOST_DURING_HANDOVER')
        command('enable', STREAM)
        report('hubble_only_and_scoped_stream_active')
    except BaseException:
        # Restore the exact old feed before restarting it. Fail loudly if recovery fails.
        try:
            report('rollback_in_progress')
        except OSError:
            pass
        try:
            command('disable', '--now', STREAM)
            stream_path.unlink(missing_ok=True)
            atomic(feed_path, before)
            command('daemon-reload')
            command('start', FEED)
            command('is-active', '--quiet', FEED)
            report('failed_legacy_feed_restored')
        except BaseException:
            report('failed_manual_recovery_required')
            raise RuntimeError('ROLLOUT_FAILED_MANUAL_RECOVERY_REQUIRED') from None
        raise



def rollout(prepared_dir, *, unit_dir=None, observation=Path('/srv/serving/observation'), timeout=45):
    # Check the actual observation owner before creating even a lock file.
    uid = os.geteuid()
    info = observation.lstat()
    if uid == 0 or not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
        raise PermissionError('RUN_AS_PROTECTED_OBSERVATION_DIRECTORY_OWNER')
    # One explicit handover per observation owner; never follow a lock symlink.
    descriptor = os.open(observation / '.tetragon-rollout.lock', os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise PermissionError('ROLLOUT_LOCK_OWNER_OR_MODE')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _rollout(prepared_dir, unit_dir=unit_dir, observation=observation, timeout=timeout)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-dir', required=True, type=Path)
    args = parser.parse_args()
    try:
        rollout(args.prepared_dir)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        parser.exit(1, 'Tetragon handover failed; inspect tetragon-rollout.json and restore the legacy feed if required.\n')
    print('Scoped stream and Hubble-only feed active; canonical status recovery remains a separate check.')


if __name__ == '__main__':
    main()
