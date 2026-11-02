"""Existing /13 sourcepoints: local request/rank/output facts, no TP lineage claim.

Separate processes/sessions cannot be joined by an object token, rank or time.
Intermediate tensor contents, buffer transport and upper client receipt remain
unimplemented. No source pin, common sensor, native profile or installer changes.
"""
from __future__ import annotations

from copy import deepcopy
import os
import threading

from . import vllm_cache_callback as shared
from . import vllm_rank_failure_sensor as rank

MP = rank.MP
PROFILE = 'source-bound-distributed-callback-candidate-v1'
TARGETS = {MP: ('MultiprocExecutor.execute_model', 'WorkerProc._execute_worker_rpc',
    'WorkerProc.handle_output', 'WorkerProc.enqueue_output')}


class DistributedCallbackObserver(shared.CacheCallbackObserver):
    targets = TARGETS
    sources = rank.SOURCE_ADDITIONS
    schema = 'guard-vllm-distributed-callback/1'
    profile = PROFILE

    def _requests(self, scheduler):
        values = shared.base.attrs(scheduler).get('num_scheduled_tokens')
        if (type(values) is not dict or len(values) > 64 or any(type(v) is not str or len(v) > 512 for v in values)):
            return None
        return [self.fingerprint(v) for v in values]

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        owner = args[0] if args else None
        fields = shared.base.attrs(owner)
        active = shared._ACTIVE.get()
        active = active if active and active['observer'] is self and active['pid'] == os.getpid() and active['thread'] == threading.get_ident() else None
        scheduler = None; method = None; output_rank = None; output = None
        requests = active.get('request_sha256_list') if active else None
        if point == 'MultiprocExecutor.execute_model':
            scheduler = args[1] if len(args) > 1 else kwargs.get('scheduler_output')
            requests = self._requests(scheduler); method = 'execute_model'; output_rank = shared._uint(fields.get('output_rank'))
            if returned: output = result
        elif point == 'WorkerProc._execute_worker_rpc':
            rpc = args[1] if len(args) > 1 else kwargs.get('rpc_request')
            if type(rpc) is tuple and len(rpc) == 4 and type(rpc[0]) is str and rpc[0] == 'execute_model' and type(rpc[1]) is tuple and rpc[1]:
                scheduler = rpc[1][0]; requests = self._requests(scheduler); method = 'execute_model'
                output_rank = shared._uint(rpc[3])
            else:
                self.gaps.add('DISTRIBUTED_NON_EXECUTE_RPC_UNSUPPORTED')
                requests = None
        else:
            output = args[1] if len(args) > 1 else kwargs.get('output')
        worker = fields.get('worker'); worker_fields = shared.base.attrs(worker)
        config = worker_fields.get('model_config')
        if config is None: config = shared.base.attrs(worker_fields.get('vllm_config')).get('model_config')
        config_fields = shared.base.attrs(config)
        ids = shared.base.attrs(output).get('req_ids')
        returned_ids = ([self.fingerprint(v) for v in ids] if type(ids) is list and len(ids) <= 64
            and all(type(v) is str and len(v) <= 512 for v in ids) else None)
        # These are Python object/batch boundary facts only, not tensor hashes.
        return {'receiver_token': self.token(owner), 'worker_token': self.token(worker),
            'request_sha256': requests[0] if requests and len(requests) == 1 else None,
            'native_request_sha256': requests, 'scheduler_object_token': self.token(scheduler),
            'rank': shared._uint(fields.get('rank')), 'local_rank': shared._uint(fields.get('local_rank')),
            'selected_output_rank': output_rank, 'rpc_method': method,
            'configured_model_sha256': self.fingerprint(config_fields.get('model')),
            'configured_revision_sha256': self.fingerprint(config_fields.get('revision')),
            'output_object_token': self.token(output), 'output_request_sha256': returned_ids,
            'output_is_exception': isinstance(output, Exception),
            'intermediate_tensor_buffer_verified': False, 'cross_process_lineage_verified': False,
            'upper_client_receipt_verified': False}


def project(report, policy_values):
    rows = [deepcopy(r) for r in report['events'] if r['phase'] in {'CALL', 'RETURN', 'RAISE'}
        and r['entry_qualified_before'] is True and (r['phase'] == 'CALL' or r.get('entry_qualified_after') is True)]
    # Actual nested call tokens supply process-local association. No timestamp,
    # common rank or coincident model configuration supplies a missing edge.
    indexed = {r['call_sha256']: r for r in rows if r['phase'] == 'CALL'}
    edges = []
    for row in rows:
        parent = indexed.get(row['parent_call_sha256'])
        if parent is None or row['phase'] != 'CALL': continue
        if (row['session_sha256'], row['process_sha256']) != (parent['session_sha256'], parent['process_sha256']): continue
        edges.append({'session_sha256': row['session_sha256'], 'process_sha256': row['process_sha256'],
            'parent_call_sha256': parent['call_sha256'], 'child_call_sha256': row['call_sha256'],
            'request_sha256': row['fields']['native_request_sha256'],
            'output_object_token': row['fields']['output_object_token']})
    return {'profile': PROFILE, 'policy_values': deepcopy(policy_values), 'source_bound_events': rows,
        'same_process_call_edges': edges, 'missing': sorted(set(report['missing']) | {
            'per_rank_intermediate_tensor_buffer_transport_unimplemented', 'cross_process_sender_receiver_edge_unimplemented',
            'actual_model_generation_consumption_unverified', 'upper_output_request_and_client_receipt_unverified',
            'fault_activation_unverified', 'native_profile_installer_unimplemented'}),
        'scope_complete': False, 'native_profile_supported': False, 'producer_authenticated': False}
