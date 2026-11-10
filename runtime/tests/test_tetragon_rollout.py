"""Explicit handover failures preserve the existing service without cluster access."""
import hashlib
import importlib.util
import json
import os
import time
from pathlib import Path
from unittest.mock import patch
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/install/rollout_tetragon_stream.py'
spec = importlib.util.spec_from_file_location('tetragon_rollout', SCRIPT)
rollout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rollout)


def documents():
    prepared = {'context':'ctx', 'namespace':'serving-lab', 'policies':['policy']}
    heartbeat = {'schema':'runtime-observation-feed-heartbeat/1.0', 'kind':'tetragon',
        'state':'running', 'kube_context':'ctx', 'feed_path':'/srv/serving/observation/tetragon.jsonl',
        'started_at':'2026-11-10T00:00:01Z', 'updated_at':'2026-11-10T00:00:02Z',
        'processes':[{'running':True, 'returncode':None}], 'open_gap':None}
    return prepared, heartbeat, {'context':'ctx','namespace':'serving-lab','policies':['policy']}


@pytest.mark.parametrize('change',[{}, {'state':'reconnecting'}, {'updated_at':'2026-11-09T00:00:02Z'},
    {'started_at':'2026-11-09T00:00:01Z'}, {'kube_context':'wrong'}, {'processes':[]},
    {'processes':[{'running':False,'returncode':1}]}, {'open_gap':{'gap_id':'unresolved'}}])
def test_fresh_exact_heartbeat_required(change):
    prepared, heartbeat, session = documents()
    heartbeat.update(change)
    start = 1794268800  # 2026-11-10 UTC
    assert rollout.ready(heartbeat, session, prepared, start, start+3) is (not change)
    session['namespace'] = 'wrong'
    assert rollout.ready(heartbeat, session, prepared, start, start+3) is False


@pytest.mark.parametrize('failure',[None,'readiness','restart','fifo'])
def test_explicit_handover_or_exact_legacy_restore(tmp_path, failure):
    units = tmp_path/'units'; units.mkdir()
    observation = tmp_path/'observation'; observation.mkdir()
    prepared_dir = tmp_path/'prepared'; prepared_dir.mkdir()
    original = b'[Service]\nExecStart=/opt/byul/runtime/.venv/bin/python -B tools/k8s_observation_feed.py --reader-group 10010\n'
    (units/rollout.FEED).write_bytes(original)
    stream = b'[Service]\nExecStart=/opt/byul/runtime/.venv/bin/python stream.py\n'
    prepared, _, _ = documents()
    prepared['sha256'] = {rollout.STREAM:hashlib.sha256(stream).hexdigest()}
    (prepared_dir/'prepared.json').write_text(json.dumps(prepared))
    (prepared_dir/rollout.STREAM).write_bytes(stream)
    (observation/'tetragon.heartbeat.json').write_text('{}')
    (observation/'stream-session.json').write_text('{}')
    if failure == 'fifo':
        (observation/'tetragon.heartbeat.json').unlink()
        os.mkfifo(observation/'tetragon.heartbeat.json')
    calls = []
    def command(*args):
        calls.append(args)
        if failure == 'restart' and args == ('start', rollout.FEED) and b'--hubble-only' in (units/rollout.FEED).read_bytes():
            raise RuntimeError('restart failed')
    with patch.object(rollout,'command',side_effect=command), patch.object(rollout,'ready',return_value=failure not in {'readiness','fifo'}), patch.object(rollout.time,'monotonic',side_effect=[0,2]):
        if failure:
            with pytest.raises(RuntimeError):rollout._rollout(prepared_dir,unit_dir=units,observation=observation,timeout=1)
        else:
            rollout._rollout(prepared_dir,unit_dir=units,observation=observation,timeout=1)
    state = json.loads((observation/'tetragon-rollout.json').read_text())['state']
    if failure:
        assert (units/rollout.FEED).read_bytes() == original
        assert not (units/rollout.STREAM).exists()
        assert state == 'failed_legacy_feed_restored'
        assert calls[-1] == ('is-active','--quiet',rollout.FEED)
    else:
        assert b'--hubble-only' in (units/rollout.FEED).read_bytes()
        assert state == 'hubble_only_and_scoped_stream_active'
        assert calls.index(('start',rollout.STREAM)) < calls.index(('start',rollout.FEED))


@pytest.mark.parametrize('uid', [0, 12345])
def test_wrong_user_never_creates_a_lock_or_changes_services(tmp_path, uid):
    with patch.object(rollout.os, 'geteuid', return_value=uid), patch.object(rollout.os, 'open') as opened, patch.object(rollout, 'command') as command:
        with pytest.raises(PermissionError):
            rollout.rollout(tmp_path, observation=tmp_path)
    opened.assert_not_called()
    command.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_old_home_feed_requires_documented_migration_before_handover(tmp_path):
    units = tmp_path/'units'; units.mkdir()
    prepared = tmp_path/'prepared'; prepared.mkdir()
    observation = tmp_path/'observations'; observation.mkdir()
    original = b'ExecStart=/usr/bin/python3 -B %h/byul/runtime/tools/k8s_observation_feed.py --reader-group 10010\n'
    (units/rollout.FEED).write_bytes(original)
    stream = b'prepared stream'
    (prepared/rollout.STREAM).write_bytes(stream)
    (prepared/'prepared.json').write_text(json.dumps({'sha256':{rollout.STREAM:hashlib.sha256(stream).hexdigest()}}))
    with patch.object(rollout,'command') as command:
        with pytest.raises(ValueError, match='LEGACY_FEED_ARGUMENT_CONTRACT_MISMATCH'):
            rollout._rollout(prepared,unit_dir=units,observation=observation)
    command.assert_not_called()
    assert (units/rollout.FEED).read_bytes() == original
    assert list(observation.iterdir()) == []


@pytest.mark.parametrize('kind', ['fifo', 'symlink', 'oversize'])
def test_input_reads_are_nonblocking_regular_and_bounded(tmp_path, kind):
    path = tmp_path/'input'
    if kind == 'fifo':
        os.mkfifo(path)
    elif kind == 'symlink':
        target = tmp_path/'target'; target.write_bytes(b'{}'); path.symlink_to(target)
    else:
        path.write_bytes(b'x'*17)
    started = time.monotonic()
    with pytest.raises((OSError, ValueError)):
        rollout.read_file(path, limit=16)
    assert time.monotonic()-started < 1


def test_fifo_lock_is_rejected_without_waiting_for_a_reader(tmp_path):
    from types import SimpleNamespace
    import stat
    os.mkfifo(tmp_path/'.tetragon-rollout.lock')
    owner = SimpleNamespace(st_uid=12345, st_mode=stat.S_IFDIR | 0o750)
    started = time.monotonic()
    with patch.object(rollout.os, 'geteuid', return_value=12345), patch.object(Path, 'lstat', return_value=owner), patch.object(rollout, 'command') as command:
        with pytest.raises(OSError):
            rollout.rollout(tmp_path, observation=tmp_path)
    command.assert_not_called()
    assert time.monotonic()-started < 1
