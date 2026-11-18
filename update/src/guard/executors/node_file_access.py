"""Read kernel file-access facts through an existing opaque owned-node proof.

The producer is not yet GPU-qualified or registered. Public receipts expose no
selected paths or file contents. No raw JSON success flag substitutes for the
local containerd task proof or promotes these facts to a security verdict.
"""
from copy import deepcopy
import hashlib
import sys
import time

from guard.adapters import owned_file_access_probe as probe
from guard.adapters import resource_recovery_probe as selected
from guard.models import canonical_json, sha256_hex
from guard.runtime.bounded import capture_pipes, strict_json
from guard.runtime import file_access_source
from . import node_image_proof

_KEY = object()


class NodeFileAccessSnapshot:
    __slots__ = ('_scope', '_packet', '_receipt')
    def __init__(self, scope, packet, receipt, *, _key=None):
        if _key is not _KEY:
            raise ValueError('FILE_ACCESS_COLLECTED_SNAPSHOT_REQUIRED')
        self._scope, self._packet, self._receipt = deepcopy(scope), deepcopy(packet), deepcopy(receipt)

    def packet(self): return deepcopy(self._packet)
    def public_receipt(self): return deepcopy(self._receipt)
    def matches(self, binding): return self._scope == binding.get('engine')


def collect(binding, task_proof, files, *, runner=None, max_seconds=5):
    if (type(binding) is not dict or type(binding.get('engine')) is not dict
            or type(task_proof) is not node_image_proof.NodeImageProof
            or not task_proof.matches(binding['engine'])):
        raise ValueError('FILE_ACCESS_LOCAL_TASK_PROOF_REQUIRED')
    if type(max_seconds) is not int or not 1 <= max_seconds <= 20:
        raise ValueError('FILE_ACCESS_BUDGET_INVALID')
    receipt = task_proof.public_receipt()
    observed = receipt.get('observed_monotonic_ns')
    now = time.monotonic_ns()
    if type(observed) is not int or not 0 <= now - observed <= 30_000_000_000:
        raise ValueError('FILE_ACCESS_TASK_PROOF_EXPIRED')
    task = receipt['task']
    anchor = selected.context(task['pid'])
    if (anchor['start_ticks'] != task['start_ticks'] or anchor['pid_namespace'] != task['pid_namespace']
            or anchor['boot_sha256'] != receipt['node']['boot_id_sha256']):
        raise ValueError('FILE_ACCESS_TASK_IDENTITY_CHANGED')
    selection = {'schema': probe.SELECTION_SCHEMA, 'anchor': anchor, 'files': deepcopy(files)}
    probe.validate_selection(selection)
    program = file_access_source.source()
    began = time.monotonic_ns()
    result = (runner or capture_pipes)([sys.executable, '-I', '-S', '-B', '-c', program],
        timeout=max_seconds + 5, env_extra={}, max_stdout_bytes=probe.MAX_OUTPUT, max_stderr_bytes=32768,
        input_bytes=canonical_json({'selection': selection, 'max_seconds': max_seconds}))
    finished = time.monotonic_ns()
    if (result.get('returncode') != 0 or result.get('capture', {}).get('complete') is not True
            or type(result.get('stderr_bytes')) is not bytes or result['stderr_bytes']):
        raise ValueError('FILE_ACCESS_NODE_CAPTURE_INCOMPLETE')
    packet = strict_json(result['stdout_bytes'], limit=probe.MAX_OUTPUT)
    probe.validate_packet(packet, selection)
    if (not began <= packet['ready_monotonic_ns'] <= packet['finished_monotonic_ns'] <= finished
            or packet['observer']['boot_sha256'] != receipt['node']['boot_id_sha256']
            or selected.context(task['pid']) != anchor):
        raise ValueError('FILE_ACCESS_NODE_INTERVAL_OR_TASK_UNVERIFIED')
    origin = 'EXAMPLE' if runner is not None or binding.get('lab') is True else receipt['data_origin']
    public = {'schema': 'guard-local-node-file-access-capture/1', 'node': deepcopy(receipt['node']),
        'engine_binding_sha256': sha256_hex(canonical_json(binding['engine'])),
        'selection_sha256': selected.sha(probe.canonical(selection)),
        'files': deepcopy(packet['files']), 'anchor': deepcopy(anchor),
        'program_sha256': hashlib.sha256(program.encode()).hexdigest(),
        'packet_sha256': sha256_hex(canonical_json(packet)), 'data_origin': origin,
        'initial_local_task_proof_verified': True, 'root_mount_stable_through_capture_verified': False,
        'actual_gpu_source_qualified': False, 'producer_registered': False,
        'whole_supply_scope_verified': False, 'parser_acceptance_verified': False,
        'engine_launched_by_collector': False}
    return NodeFileAccessSnapshot(binding['engine'], packet, public, _key=_KEY)


def collect_for_reader(reader, files, *, max_seconds=5, containerd_address=None):
    """Recheck the live API owner and containerd/root-mount proof on both sides.

    These are two sampled mount proofs, not continuous mount surveillance.
    Preserve an earlier packet when a later owner or proof check fails.
    """
    record = {'schema': 'guard-owned-node-file-access-read/1', 'collected': False,
        'owner_binding_verified': False, 'clock_window_verified': False,
        'root_mount_before_after_verified': False, 'packet': None, 'receipt': None,
        'task_proof_before': None, 'task_proof_after': None, 'diagnostic_codes': []}
    try:
        if (containerd_address not in (None, '/run/k3s/containerd/containerd.sock')
                or type(max_seconds) is not int or not 1 <= max_seconds <= 20):
            raise ValueError
        before = reader._owner(min(5, reader._remaining(reader.deadline)))
        engine = reader.binding['engine']
        cleanup = getattr(reader, '_parent_cleanup_failures', 0)

        def proof():
            node = strict_json(reader._request(['get', 'node', engine['node'], '-o', 'json'],
                deadline=reader.deadline, cap=262144), limit=262144)
            return node_image_proof.collect_local_node_image_proof(
                {**reader.binding, 'lab': reader.lab}, node,
                runner=reader.runner if reader.injected else None,
                timeout=min(30, reader._remaining(reader.deadline)),
                **({'containerd_address': containerd_address} if containerd_address is not None else {}))

        first = proof()
        record['task_proof_before'] = first.public_receipt()
        if max_seconds + 5 >= reader._remaining(reader.deadline):
            raise ValueError
        snapshot = collect({**reader.binding, 'lab': reader.lab}, first, files,
            runner=reader.runner if reader.injected else None, max_seconds=max_seconds)
        record.update(packet=snapshot.packet(), receipt=snapshot.public_receipt())
        second = proof()
        record['task_proof_after'] = second.public_receipt()
        identity = lambda r: {k: v for k, v in r.items() if k not in {'reads', 'observed_monotonic_ns', 'data_origin'}}
        if (type(first) is not node_image_proof.NodeImageProof or type(second) is not node_image_proof.NodeImageProof
                or not first.matches(engine) or not second.matches(engine)
                or any(r.get(k) is not True for r in (record['task_proof_before'], record['task_proof_after'])
                    for k in ('image_chain_verified', 'snapshot_chain_verified', 'live_root_mount_verified'))
                or identity(record['task_proof_before']) != identity(record['task_proof_after'])
                or reader._owner(min(5, reader._remaining(reader.deadline))) != before
                or getattr(reader, '_parent_cleanup_failures', 0) != cleanup):
            raise ValueError
        record.update(collected=True, owner_binding_verified=True, clock_window_verified=True,
            root_mount_before_after_verified=True)
    except Exception:
        record['diagnostic_codes'].append('FILE_ACCESS_OWNED_NODE_READ_UNVERIFIED')
    return record
