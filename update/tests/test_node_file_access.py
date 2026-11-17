"""CPU transport examples, not actual GPU fanotify qualification."""
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from guard.adapters import owned_file_access_probe as probe
from guard.adapters import resource_recovery_probe as selected
from guard.executors import node_file_access as collector, node_image_proof
from guard.runtime import file_access_source
from tests.test_node_gpu_process import setup


def example(tmp_path, monkeypatch):
    path = tmp_path / 'known-public-file'; path.write_bytes(b'public example')
    binding, proof = setup()
    anchor = selected.context(os.getpid())
    proof._receipt['task'] = {k: anchor[k] for k in ('pid', 'start_ticks', 'pid_namespace')}
    proof._receipt['node']['boot_id_sha256'] = anchor['boot_sha256']
    files = [{'id': 'b' * 64, 'path': str(path), 'identity': probe.file_identity(path.stat())}]
    return binding, proof, anchor, files


def packet(selection):
    now = time.monotonic_ns()
    return {'schema': probe.SCHEMA, 'selection_sha256': selected.sha(probe.canonical(selection)),
        'observer': selected.context(os.getpid()), 'anchor': deepcopy(selection['anchor']),
        'ready_monotonic_ns': now, 'finished_monotonic_ns': now,
        'files': [{'id': r['id'], 'identity': r['identity']} for r in selection['files']],
        'events': [], 'ignored_unselected_events': 0, 'anchor_and_file_pins_stable': True,
        'capture_closed': True, 'diagnostic_codes': [], 'limitations': list(probe.LIMITATIONS),
        'resource_contents_read': False, 'permission_decisions_made': False, 'producer_authenticated': False,
        'whole_supply_scope_verified': False, 'parser_acceptance_verified': False, 'actual_gpu_source_qualified': False}


def test_initial_opaque_task_source_and_interval_bind_without_native_scope_promotion(tmp_path, monkeypatch):
    binding, proof, anchor, files = example(tmp_path, monkeypatch)
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[:5] == [sys.executable, '-I', '-S', '-B', '-c']
        value = json.loads(kwargs['input_bytes'])
        assert value['selection'] == {'schema': probe.SELECTION_SCHEMA, 'anchor': anchor, 'files': files}
        assert value['max_seconds'] == 5 and kwargs['timeout'] == 10
        return {'returncode': 0, 'capture': {'complete': True}, 'stderr_bytes': b'',
            'stdout_bytes': probe.canonical(packet(value['selection']))}
    result = collector.collect(binding, proof, files, runner=run)
    assert len(calls) == 1 and result.matches(binding)
    public = result.public_receipt()
    assert public['data_origin'] == 'EXAMPLE' and public['initial_local_task_proof_verified'] is True
    assert public['packet_sha256'] == sha256(probe.canonical(result.packet())).hexdigest()
    assert all(public[k] is False for k in ('root_mount_stable_through_capture_verified',
        'actual_gpu_source_qualified', 'producer_registered', 'whole_supply_scope_verified',
        'parser_acceptance_verified', 'engine_launched_by_collector'))
    assert files[0]['path'] not in probe.canonical(public).decode()
    changed = result.packet(); changed['events'].append({})
    assert result.packet()['events'] == []
    with pytest.raises(ValueError, match='FILE_ACCESS_COLLECTED_SNAPSHOT_REQUIRED'):
        collector.NodeFileAccessSnapshot({}, {}, {})


@pytest.mark.parametrize('mode', ['operator-dict', 'foreign-cid', 'expired', 'future', 'reused-task', 'other-boot'])
def test_invalid_task_is_rejected_before_source_or_process(tmp_path, monkeypatch, mode):
    binding, proof, anchor, files = example(tmp_path, monkeypatch)
    if mode == 'operator-dict': proof = {'local_task_proof_verified': True}
    elif mode == 'foreign-cid': binding['engine']['container_id'] = 'containerd://' + 'd' * 64
    elif mode == 'expired': proof._receipt['observed_monotonic_ns'] -= 31_000_000_000
    elif mode == 'future': proof._receipt['observed_monotonic_ns'] += 31_000_000_000
    elif mode == 'reused-task': proof._receipt['task']['start_ticks'] += 1
    else: proof._receipt['node']['boot_id_sha256'] = 'd' * 64
    def forbidden(*a, **kw): pytest.fail('No launch for invalid task')
    monkeypatch.setattr(file_access_source, 'source', forbidden)
    with pytest.raises(ValueError): collector.collect(binding, proof, files, runner=forbidden)


@pytest.mark.parametrize('mode', ['clock', 'stderr', 'partial', 'claim', 'other-selection', 'owner-replaced'])
def test_partial_foreign_or_unbounded_capture_does_not_make_an_opaque_snapshot(tmp_path, monkeypatch, mode):
    binding, proof, anchor, files = example(tmp_path, monkeypatch)
    real_context = selected.context
    def run(*args, **kwargs):
        value = packet(json.loads(kwargs['input_bytes'])['selection'])
        if mode == 'clock': value['ready_monotonic_ns'] = 1
        elif mode == 'claim': value['whole_supply_scope_verified'] = True
        elif mode == 'other-selection': value['selection_sha256'] = 'd' * 64
        elif mode == 'owner-replaced':
            def changed(pid):
                current = real_context(pid); current['start_ticks'] += 1; return current
            monkeypatch.setattr(selected, 'context', changed)
        return {'returncode': 0, 'capture': {'complete': mode != 'partial'},
            'stderr_bytes': b'EXAMPLE-error' if mode == 'stderr' else b'', 'stdout_bytes': probe.canonical(value)}
    with pytest.raises(ValueError): collector.collect(binding, proof, files, runner=run)


def test_standalone_source_rejects_caller_fd_authority_before_any_kernel_probe(tmp_path, monkeypatch):
    binding, proof, anchor, files = example(tmp_path, monkeypatch)
    program = file_access_source.source()
    request = {'selection': {'schema': probe.SELECTION_SCHEMA, 'anchor': anchor, 'files': files},
        'max_seconds': 1, 'fanotify_fd': 7}
    child = subprocess.run([sys.executable, '-I', '-S', '-c', program], input=json.dumps(request).encode(), capture_output=True)
    assert child.returncode != 0 and b'FILE_ACCESS_INPUT_INVALID' in child.stderr and not child.stdout


@pytest.mark.parametrize('name', ['owned_file_access_probe.py', 'resource_recovery_probe.py', 'bounded.py'])
def test_each_standalone_dependency_is_pinned(monkeypatch, name):
    read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + (b'\n' if p.name == name else b''))
    with pytest.raises(ValueError, match='FILE_ACCESS_SOURCE_UNREVIEWED'): file_access_source.source()


def test_new_file_access_path_leaves_registered_native_sdk_identity_unchanged():
    from guard.collectors.native_input import producer
    assert producer()['source_sha256'] == '5c9e6a5c956b8f2ec0873e71941776eb7027b16b4fd185c4606dd2125938b5d6'


@pytest.mark.parametrize('mode', ['normal', 'owner-replaced', 'node-replaced', 'root-mount-replaced',
    'task-replaced', 'missing-chain-proof', 'proof-error', 'capture-error', 'cleanup-error'])
def test_live_reader_rechecks_both_mount_proofs_and_preserves_earlier_facts(tmp_path, monkeypatch, mode):
    binding, first, anchor, files = example(tmp_path, monkeypatch)
    for key in ('image_chain_verified', 'snapshot_chain_verified', 'live_root_mount_verified'):
        first._receipt[key] = True
    first._receipt['root_mount_identity_sha256'] = 'a' * 64
    second = node_image_proof.NodeImageProof(binding['engine'], first.public_receipt(), _key=node_image_proof._PROOF_KEY)
    second._receipt['observed_monotonic_ns'] += 100
    second._receipt['reads'] = [{'operation': 'EXAMPLE-different-read-timing'}]
    if mode == 'node-replaced': second._receipt['node']['node_uid'] = 'EXAMPLE-other-node'
    elif mode == 'root-mount-replaced': second._receipt['root_mount_identity_sha256'] = 'c' * 64
    elif mode == 'task-replaced': second._receipt['task']['start_ticks'] += 1
    elif mode == 'missing-chain-proof': second._receipt['snapshot_chain_verified'] = False
    class Reader:
        lab = True; injected = True; deadline = time.monotonic() + 120
        _parent_cleanup_failures = 0
        def __init__(self): self.binding = binding; self.nodes = 0; self.owners = 0; self.runner = lambda *a, **k: None
        def _remaining(self, deadline): return deadline - time.monotonic()
        def _owner(self, timeout):
            self.owners += 1
            return {'owner': 'other' if mode == 'owner-replaced' and self.owners == 2 else 'EXAMPLE'}
        def _request(self, argv, **kwargs):
            assert argv == ['get', 'node', binding['engine']['node'], '-o', 'json']
            self.nodes += 1
            return b'{}'
    reader = Reader(); proofs = [first, second]
    def proof(*a, **kwargs):
        value = proofs.pop(0)
        if value is second and mode == 'proof-error': raise OSError('PRIVATE-ERROR')
        return value
    monkeypatch.setattr(node_image_proof, 'collect_local_node_image_proof', proof)
    def capture(*a, **kwargs):
        if mode == 'capture-error': raise OSError('PRIVATE-CAPTURE-ERROR')
        if mode == 'cleanup-error': reader._parent_cleanup_failures += 1
        selection = {'schema': probe.SELECTION_SCHEMA, 'anchor': anchor, 'files': files}
        return collector.NodeFileAccessSnapshot(binding['engine'], packet(selection), {'data_origin': 'EXAMPLE'}, _key=collector._KEY)
    monkeypatch.setattr(collector, 'collect', capture)
    result = collector.collect_for_reader(reader, files, containerd_address='/run/k3s/containerd/containerd.sock')
    assert result['collected'] is result['owner_binding_verified'] is result['clock_window_verified'] is result['root_mount_before_after_verified'] is (mode == 'normal')
    assert (result['packet'] is not None) is (mode != 'capture-error')
    assert b'PRIVATE' not in probe.canonical(result)
    assert (result['task_proof_after'] is not None) is (mode not in {'proof-error', 'capture-error'})


def test_live_reader_rejects_another_runtime_endpoint_before_any_io():
    class Reader:
        def _owner(self, *a): pytest.fail('No IO for unsupported endpoint')
    result = collector.collect_for_reader(Reader(), [], containerd_address='/private/other.sock')
    assert result['collected'] is False and result['packet'] is None
    assert result['diagnostic_codes'] == ['FILE_ACCESS_OWNED_NODE_READ_UNVERIFIED']
