"""Initial V1 attention metadata/source-call facts, never GPU memory safety.

New candidate adapter only. Common installer/collector integration is owned by
root; this module starts nothing and preserves original callback contracts.
"""
from __future__ import annotations
import os
import re
from . import vllm_cache_callback as cache

RUNNER = 'vllm.v1.worker.gpu_model_runner'
ATTENTION = 'vllm.v1.attention.backends.flash_attn'
PREPARE = 'GPUModelRunner._prepare_inputs'
FORWARD = 'FlashAttentionImpl.forward'
TARGETS = {RUNNER: (PREPARE,), ATTENTION: (FORWARD,)}
SOURCES = {RUNNER: '1ac3fb18b2adad3609dcf58a1656d6fdd4a568eea6740258e00f6a43c7d4b813',
           ATTENTION: '8fe8b9b56dfbefd5db474df21f81c9cbda11f9a49cb611e7d41a0ffc298627ee'}
POINT_MODULES = {PREPARE: RUNNER, FORWARD: ATTENTION}
SCHEMA = 'guard-vllm-attention-metadata-callback/1'
MAX_REQUESTS = 256
COUNTS = ('num_actual_tokens', 'max_query_len', 'max_seq_len', 'common_prefix_len', 'num_input_tokens')
STABLE_COUNTS = tuple(name for name in COUNTS if name != 'num_input_tokens')
TENSORS = ('query_start_loc', 'seq_lens', 'block_table', 'slot_mapping')
CAPACITIES = ('max_num_tokens', 'max_num_reqs', 'max_model_len', 'max_num_blocks_per_req', 'block_size')
TRUTH = ('native_library_identity_verified', 'native_cuda_dispatch_verified', 'tensor_shape_stride_capacity_verified',
         'buffer_initialization_verified', 'gpu_copy_completion_verified', 'gpu_execution_completion_verified',
         'kernel_memory_safety_verified', 'whole_question_complete')
GAPS = ('UNQUALIFIED_INTEGER_SCALAR', 'PREPARE_RETURN_STRUCTURE_UNAVAILABLE', 'METADATA_OBJECT_UNAVAILABLE',
        'REQUEST_COUNT_MAPPING_UNAVAILABLE', 'WEAK_OBJECT_IDENTITY_UNAVAILABLE')


def integer(value, gaps):
    """No int()/item()/NumPy import: unqualified scalar interfaces are gaps."""
    if type(value) is int and 0 <= value < 2**63:return value
    if value is not None:gaps.add('UNQUALIFIED_INTEGER_SCALAR')
    return None


class AttentionMetadataObserver(cache.CacheCallbackObserver):
    targets, sources = TARGETS, SOURCES
    schema = SCHEMA
    profile = 'source-bound-attention-metadata-candidate-v1'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.identity_namespace = os.urandom(32)

    def identity(self, value, gaps):
        token = self.token(value)
        if value is not None and token is None:gaps.add('WEAK_OBJECT_IDENTITY_UNAVAILABLE')
        return self.fingerprint(self.identity_namespace + token.encode()) if token is not None else None

    def metadata(self, value, gaps):
        if value is None:return None
        fields = cache.base.attrs(value)
        return {'metadata_sha256': self.identity(value, gaps),
            'counts': {name: integer(fields.get(name), gaps) for name in COUNTS},
            'use_cascade': fields.get('use_cascade') if type(fields.get('use_cascade')) is bool else None,
            'tensor_sha256': {name: self.identity(fields.get(name), gaps) for name in TENSORS}}

    def requests(self, batch, scheduler, gaps):
        fields = cache.base.attrs(batch)
        mapping, ids = fields.get('req_id_to_index'), fields.get('req_ids')
        scheduled = cache.base.attrs(scheduler).get('num_scheduled_tokens')
        if type(mapping) is not dict or type(ids) is not list or type(scheduled) is not dict or len(mapping) > MAX_REQUESTS:
            gaps.add('REQUEST_COUNT_MAPPING_UNAVAILABLE');return None
        rows = []
        for request, slot in mapping.items():
            digest = self.fingerprint(request)
            if (digest is None or type(slot) is not int or not 0 <= slot < len(ids)
                    or type(ids[slot]) is not str or ids[slot] != request):
                gaps.add('REQUEST_COUNT_MAPPING_UNAVAILABLE');return None
            rows.append({'request_sha256': digest, 'slot': slot, 'scheduled_tokens': integer(scheduled.get(request), gaps)})
        return sorted(rows, key=lambda row: row['slot'])

    def _snapshot(self, point, args, kwargs, result=None, returned=False):
        gaps = set()
        owner = args[0] if args else kwargs.get('self')
        common = {'observation': point if point in POINT_MODULES else 'unsupported', **{name: False for name in TRUTH}}
        if point == PREPARE:
            fields = cache.base.attrs(owner)
            scheduler = args[1] if len(args) > 1 else kwargs.get('scheduler_output')
            batch = fields.get('input_batch')
            structure = 'tuple-two' if returned and type(result) is tuple and len(result) == 2 else None
            if returned and structure is None:gaps.add('PREPARE_RETURN_STRUCTURE_UNAVAILABLE')
            value = result[0] if structure else None
            if returned and value is None:gaps.add('METADATA_OBJECT_UNAVAILABLE')
            return {**common, 'runner_sha256': self.identity(owner, gaps), 'batch_sha256': self.identity(batch, gaps),
                'scheduled_total': integer(cache.base.attrs(scheduler).get('total_num_scheduled_tokens'), gaps),
                'requests': self.requests(batch, scheduler, gaps),
                'declared_capacities': {name: integer(fields.get(name), gaps) for name in CAPACITIES},
                'source_copy_plan': 'nonblocking-host-to-device', 'return_structure': structure,
                'metadata': self.metadata(value, gaps),
                'logits_indices_sha256': self.identity(result[1], gaps) if structure else None,
                'gaps': sorted(gaps)}
        if point == FORWARD:
            fields = cache.base.attrs(owner)
            value = args[6] if len(args) > 6 else kwargs.get('attn_metadata')
            output = args[7] if len(args) > 7 else kwargs.get('output')
            metadata = self.metadata(value, gaps)
            branch = ('profiling' if value is None else 'regular' if metadata['use_cascade'] is False
                      else 'cascade' if metadata['use_cascade'] is True else 'unsupported')
            return {**common, 'implementation_sha256': self.identity(owner, gaps), 'metadata': metadata,
                'branch': branch, 'declared_flash_version': integer(fields.get('fa_version'), gaps),
                'provided_output_sha256': self.identity(output, gaps),
                'returned_output_sha256': self.identity(result, gaps) if returned else None,
                'gaps': sorted(gaps)}
        return {**common, 'gaps': ['METADATA_OBJECT_UNAVAILABLE']}


def project(records, *, session_sha256, process_sha256):
    """Bounded typed source outcomes and object transfers, outside judge gates."""
    fingerprint = lambda value: type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None
    if (not fingerprint(session_sha256) or not fingerprint(process_sha256) or type(records) is not list or len(records) > 2048):
        raise ValueError('ATTENTION_METADATA_SCOPE_INVALID')
    grouped = {};missing = set();calls = [];chains = []
    def nullable_id(value):return value is None or fingerprint(value)
    def counts(value, names):
        return type(value) is dict and set(value) == set(names) and all(item is None or type(item) is int and 0 <= item < 2**63 for item in value.values())
    def metadata(value):
        if value is None:return True
        return (type(value) is dict and set(value) == {'metadata_sha256', 'counts', 'use_cascade', 'tensor_sha256'}
            and nullable_id(value['metadata_sha256']) and counts(value['counts'], COUNTS)
            and (value['use_cascade'] is None or type(value['use_cascade']) is bool)
            and type(value['tensor_sha256']) is dict and set(value['tensor_sha256']) == set(TENSORS)
            and all(nullable_id(item) for item in value['tensor_sha256'].values()))
    def fields_valid(point, fields):
        expected = {'observation', *TRUTH, 'metadata', 'gaps'} | ({'runner_sha256', 'batch_sha256', 'scheduled_total',
            'requests', 'declared_capacities', 'source_copy_plan', 'return_structure', 'logits_indices_sha256'}
            if point == PREPARE else {'implementation_sha256', 'branch', 'declared_flash_version',
                'provided_output_sha256', 'returned_output_sha256'})
        if (type(fields) is not dict or set(fields) != expected or fields.get('observation') != point or any(fields.get(name) is not False for name in TRUTH)
                or type(fields.get('gaps')) is not list or any(gap not in GAPS for gap in fields['gaps'])
                or len(fields['gaps']) != len(set(fields['gaps']))
                or not metadata(fields.get('metadata'))):return False
        if point == PREPARE:
            rows = fields.get('requests')
            valid_rows = rows is None or type(rows) is list and len(rows) <= MAX_REQUESTS and all(
                type(row) is dict and set(row) == {'request_sha256', 'slot', 'scheduled_tokens'}
                and fingerprint(row['request_sha256']) and type(row['slot']) is int and 0 <= row['slot'] < 2**63
                and (row['scheduled_tokens'] is None or type(row['scheduled_tokens']) is int and 0 <= row['scheduled_tokens'] < 2**63) for row in rows)
            return (valid_rows and counts(fields.get('declared_capacities'), CAPACITIES)
                and nullable_id(fields.get('runner_sha256')) and nullable_id(fields.get('batch_sha256'))
                and nullable_id(fields.get('logits_indices_sha256')) and fields.get('return_structure') in {None, 'tuple-two'}
                and fields.get('source_copy_plan') == 'nonblocking-host-to-device'
                and (fields.get('scheduled_total') is None or type(fields['scheduled_total']) is int and 0 <= fields['scheduled_total'] < 2**63))
        return (fields.get('branch') in {'profiling', 'regular', 'cascade', 'unsupported'}
            and nullable_id(fields.get('implementation_sha256')) and nullable_id(fields.get('provided_output_sha256'))
            and nullable_id(fields.get('returned_output_sha256')) and
            (fields.get('declared_flash_version') is None or type(fields['declared_flash_version']) is int and 0 <= fields['declared_flash_version'] < 2**63))
    for number, record in enumerate(records):
        point = record.get('point') if type(record) is dict else None
        if point not in POINT_MODULES:continue
        if (record.get('schema') != SCHEMA or record.get('session_sha256') != session_sha256
                or record.get('process_sha256') != process_sha256 or record.get('source_file_sha256') != SOURCES[POINT_MODULES[point]]
                or not fingerprint(record.get('entry_code_sha256')) or not fingerprint(record.get('call_sha256'))
                or record.get('entry_qualified_before') is not True or not fields_valid(point, record.get('fields'))):
            missing.add('source_scope_or_typed_observation_unavailable');continue
        grouped.setdefault(record['call_sha256'], []).append((number, record))
    for call, pair in grouped.items():
        if (len(pair) != 2 or pair[0][1].get('phase') != 'CALL' or pair[1][1].get('phase') not in {'RETURN', 'RAISE'}
                or pair[1][1].get('entry_qualified_after') is not True
                or any(pair[0][1].get(name) != pair[1][1].get(name) for name in ('point', 'source_file_sha256', 'entry_code_sha256', 'parent_call_sha256', 'operation_started_ns'))
                or any(type(row.get('operation_started_ns')) is not int or type(row.get('operation_finished_ns')) is not int
                    or not 0 <= row['operation_started_ns'] <= row['operation_finished_ns'] < 2**63 for _, row in pair)):
            missing.add('unique_qualified_call_pair_unavailable');continue
        first, last = pair[0][1], pair[1][1]
        before, after = first['fields'], last['fields']
        identities = ('runner_sha256', 'batch_sha256') if first['point'] == PREPARE else ('implementation_sha256',)
        if any(before[key] is None or before[key] != after[key] for key in identities):
            missing.add('stable_call_owner_unavailable');continue
        missing.update(after['gaps'])
        calls.append({'call_sha256': call, 'point': last['point'], 'phase': last['phase'],
            'event_ref': [pair[0][0], pair[1][0]], 'started_ns': first['operation_started_ns'], 'finished_ns': last['operation_finished_ns'],
            'before': before, 'after': after})
    prepares = [call for call in calls if call['point'] == PREPARE and call['phase'] == 'RETURN'
        and call['after']['return_structure'] == 'tuple-two' and call['after']['metadata'] is not None
        and fingerprint(call['after']['metadata']['metadata_sha256'])]
    for forward in [call for call in calls if call['point'] == FORWARD and call['phase'] == 'RETURN']:
        before, after = forward['before'], forward['after']
        returned_same = fingerprint(before['provided_output_sha256']) and before['provided_output_sha256'] == after['provided_output_sha256'] == after['returned_output_sha256']
        if before['branch'] != after['branch']:
            missing.add('stable_attention_branch_unavailable');continue
        if before['branch'] == 'profiling':
            chains.append({'branch': 'profiling', 'forward_call_sha256': forward['call_sha256'],
                'profiling_returned_provided_output_observed': bool(returned_same), 'same_metadata_object_observed': False,
                **{name: False for name in TRUTH}})
            continue
        if before['branch'] == 'unsupported':
            missing.add('qualified_attention_branch_unavailable');continue
        selected = [prepare for prepare in prepares if before['metadata'] is not None
            and prepare['after']['metadata']['metadata_sha256'] == before['metadata']['metadata_sha256']
            and prepare['event_ref'][1] < forward['event_ref'][0] and prepare['finished_ns'] <= forward['started_ns']]
        if len(selected) != 1 or after['metadata'] is None or after['metadata']['metadata_sha256'] != before['metadata']['metadata_sha256']:
            missing.add('unique_prepare_to_forward_metadata_transfer_unavailable');continue
        prepare = selected[0]
        made = prepare['after'];declared = made['declared_capacities'];meta = made['metadata']['counts']
        total = made['scheduled_total'];rows = made['requests']
        known_counts = type(total) is int and rows is not None and all(type(row['scheduled_tokens']) is int for row in rows)
        stable = (all(item['counts'][key] == meta[key] for item in (before['metadata'], after['metadata']) for key in STABLE_COUNTS)
            and made['metadata']['use_cascade'] == before['metadata']['use_cascade'] == after['metadata']['use_cascade']) if all(meta[key] is not None for key in STABLE_COUNTS) and made['metadata']['use_cascade'] is not None else None
        chains.append({'branch': before['branch'], 'prepare_call_sha256': prepare['call_sha256'], 'forward_call_sha256': forward['call_sha256'],
            'metadata_sha256': before['metadata']['metadata_sha256'], 'same_metadata_object_observed': True,
            'metadata_scalar_values_stable_observed': stable,
            'returned_provided_output_observed': bool(returned_same), 'declared_capacities': declared,
            'scheduled_total': total, 'metadata_counts_at_prepare': meta, 'metadata_counts_at_forward': before['metadata']['counts'],
            'scheduled_counts_match_declared_total': sum(row['scheduled_tokens'] for row in rows) == total if known_counts else None,
            'scheduled_total_matches_metadata': total == meta['num_actual_tokens'] if type(total) is int and meta['num_actual_tokens'] is not None else None,
            'declared_token_capacity_relation': total <= declared['max_num_tokens'] if type(total) is int and declared['max_num_tokens'] is not None else None,
            'declared_request_capacity_relation': len(rows) <= declared['max_num_reqs'] if rows is not None and declared['max_num_reqs'] is not None else None,
            **{name: False for name in TRUTH}})
    missing.update(('installed_native_cache_op_identity_unverified', 'installed_flashattention_dependency_source_unverified',
        'actual_tensor_extent_stride_slot_range_unverified', 'buffer_initialization_and_gpu_completion_unverified'))
    return {'source_calls': calls, 'metadata_chains': chains, 'missing': sorted(missing),
            'kernel_memory_safety_verified': False, 'whole_question_complete': False, 'producer_authenticated': False}
