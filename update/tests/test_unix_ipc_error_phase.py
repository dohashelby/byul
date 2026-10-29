"""PLT02 zero-payload connect outcomes; metadata failures are not denials."""
from copy import deepcopy
import errno
import json
import struct

import pytest

from guard.executors import ipc_probe
from guard.executors.unix_ipc import _parse
from guard.models import ExecStatus, Verdict
from tests.test_unix_ipc import World, run, NS, SOCKET, OWNER, VALUES

PATHS = ['/tmp/EXAMPLE-ipc-first.sock', '/tmp/EXAMPLE-ipc-later.sock']
PRIVATE = 'EXAMPLE_PRIVATE_IPC_PHASE'


def endpoint(index):
    return {**SOCKET, 'inode': SOCKET['inode']+index}


def install_probe_double(monkeypatch, stage, actor=None):
    state = {'uid': 1001, 'stats': {}, 'connects': []} if actor is None else actor
    class Peer:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def settimeout(self, value):
            assert 0 < value <= 0.5
        def connect(self, path):
            self.path = path
            state['connects'].append((state['uid'], path))
            if state['uid'] == 1001 and path == PATHS[-1] and stage in {'connect-permission', 'connect-refused'}:
                raise OSError(errno.EACCES if stage == 'connect-permission' else errno.ECONNREFUSED, PRIVATE)
        def getsockopt(self, *args):
            if state['uid'] == 1001 and self.path == PATHS[-1] and stage == 'peer-permission':
                raise PermissionError(errno.EPERM, PRIVATE)
            return struct.pack('3i', OWNER['pid'], OWNER['uid'], 0)
        def send(self, *args):
            pytest.fail('probe must not send a payload')
    def stat(path):
        key = (state['uid'], path)
        state['stats'][key] = state['stats'].get(key, 0)+1
        count = state['stats'][key]
        if state['uid'] == 1001 and path == PATHS[-1]:
            if stage == 'pre-permission' and count == 1:
                raise PermissionError(errno.EACCES, PRIVATE)
            if count == 2:
                if stage == 'post-permission':
                    raise PermissionError(errno.EACCES, PRIVATE)
                if stage == 'post-value':
                    raise ValueError(PRIVATE)
                if stage == 'replacement':
                    return {**endpoint(1), 'inode': 999}
        return endpoint(PATHS.index(path))
    monkeypatch.setattr(ipc_probe, '_stat_socket', stat)
    monkeypatch.setattr(ipc_probe, '_namespaces', lambda: deepcopy(NS))
    monkeypatch.setattr(ipc_probe.os, 'geteuid', lambda: state['uid'], raising=False)
    monkeypatch.setattr(ipc_probe.os, 'getgroups', lambda: [], raising=False)
    monkeypatch.setattr(ipc_probe.socket, 'SO_PEERCRED', 17, raising=False)
    monkeypatch.setattr(ipc_probe.socket, 'AF_UNIX', 1, raising=False)
    monkeypatch.setattr(ipc_probe.socket, 'socket', lambda *a, **kw: Peer())
    return state


@pytest.mark.parametrize('stage,expected', [
    ('pre-permission', 'unknown'), ('connect-permission', 'denied'), ('connect-refused', 'unknown'),
    ('peer-permission', 'unknown'), ('post-permission', 'unknown'), ('post-value', 'unknown'),
])
def test_only_connect_permission_errors_are_denials_and_other_rows_remain_parseable(monkeypatch, stage, expected):
    install_probe_double(monkeypatch, stage)
    packet = ipc_probe.connect_paths(PATHS, 0.5)
    assert packet['rows'][0]['outcome'] == 'connected'
    assert packet['rows'][1]['outcome'] == expected
    parsed = _parse(type('Result', (), {'returncode': 0, 'stdout': json.dumps(packet)})(), 'connect', 1001, 2)
    assert parsed is not None and parsed['rows'][0]['path_stable'] is True
    assert PRIVATE not in json.dumps(packet)


class ProbeWorld(World):
    def __init__(self):
        super().__init__(connected=True)
        self.actor = {'uid': 1001, 'stats': {}, 'connects': []}
    def exec_as_uid(self, container, uid, argv, timeout):
        mode = argv[3]
        self.calls.append((mode, uid))
        if mode == 'connect':
            self.actor['uid'] = uid
            value = ipc_probe.connect_paths(json.loads(argv[4]), 0.5)
        else:
            value = {'schema': 'guard-unix-ipc-probe/1', 'mode': 'inspect', 'uid': uid,
                'namespaces': deepcopy(NS), 'rows': [{'index': i, 'complete': True, 'identity': endpoint(i),
                    'socket_inode': str(55+i), 'owners': [deepcopy(OWNER)]} for i in range(2)]}
        return type('Result', (), {'returncode': 0, 'stdout': json.dumps(value), 'stderr': PRIVATE})()


@pytest.mark.parametrize('stage', ['peer-permission', 'post-permission', 'post-value', 'replacement'])
def test_product_retains_first_path_violation_after_later_metadata_failure(tmp_path, monkeypatch, stage):
    world = ProbeWorld()
    install_probe_double(monkeypatch, stage, world.actor)
    values = {**VALUES, 'protected_unix_paths': PATHS}
    store, _, _, evd, result, _ = run(tmp_path, monkeypatch, world, values=values, extra={'probe_uids': [1001, 0]})
    assert evd.execution.status == ExecStatus.COMPLETED and evd.data_origin.value == 'EXAMPLE'
    assert result.verdicts.security == Verdict.FAIL and len(evd.observations['unix_ipc_violations'].value) == 1
    assert evd.normal_control.succeeded and not evd.observations['ipc_scope_complete'].value
    trials = evd.observations['unix_ipc_trials'].value
    assert len(trials) == 4 and trials[0]['connection_verified'] is True
    assert trials[1]['outcome'] == ('connected' if stage == 'replacement' else 'unknown')
    assert trials[1]['connection_verified'] is False
    assert result.verdicts.functional == result.verdicts.performance == Verdict.UNKNOWN
    artifact = (store.root/evd.artifacts[0].path).read_text(encoding='utf-8')
    assert PRIVATE not in artifact and not any(path in artifact for path in PATHS)
