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
