"""Replay bounded original metadata; this test is not a new GPU qualification."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from guard.adapters import vllm_rank_rpc_actor as actor
from guard.adapters import vllm_rank_ipc_sources as sources

FIXTURE = Path(__file__).parent / 'fixtures/rank-normal-rpc-j01'


def case():
    provenance = json.loads((FIXTURE / 'PROVENANCE.json').read_bytes())
    for name, digest in provenance['files'].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest
    capture = json.loads((FIXTURE / 'capture-review.json').read_bytes())
    calibration = json.loads((FIXTURE / 'calibration-envelope.json').read_bytes())['payload']
    catalog = json.loads(sources.PROFILES.read_bytes())
    profile, = [p for p in catalog['profiles'] if p['image'] == capture['image']]
    records = [(row, str(number), {}) for number, row in enumerate(capture['rows'])]
    return profile, records, calibration['proof'], calibration['normal_client']


def test_actual_metadata_contains_original_rpc_returns_for_both_declared_workers():
    value = actor.normal_rpc_returns(*case())
    assert value['diagnostic_codes'] == []
    assert value['all_declared_member_returns_observed'] is True
    assert [row['rank'] for row in value['members']] == [0, 1]
    assert [row['completed_rpc_calls'] for row in value['members']] == [1, 1]
    assert len({row['context']['pid'] for row in value['members']}) == 2
    for key in ('scope_complete', 'gpu_completion_verified', 'engine_group_verified',
                'source_authenticated', 'producer_registered'):
        assert value[key] is False


@pytest.mark.parametrize('damage', [
    'missing-return', 'missing-call', 'duplicate-return', 'original-not-invoked',
    'wrong-parent', 'wrong-worker-object', 'wrong-rank', 'wrong-tp-size',
    'before-client', 'after-client', 'raise', 'foreign-member', 'source-gap',
    'wrong-uid', 'wrong-namespace', 'wrong-clock', 'missing-initial-worker',
    'duplicate-request-call', 'foreign-parent-call', 'parent-source-drift', 'parent-rank-mismatch',
])
def test_partial_foreign_or_ambiguous_rpc_records_do_not_complete_the_member_scope(damage):
    profile, records, proof, client = deepcopy(case())
    selected = next(row for row, _, _ in records if row.get('event') == 'RETURN'
        and row.get('point') == proof['point'] and row.get('scalars', {}).get('gpu_rank') == 1)
    call = next(row for row, _, _ in records if row.get('event') == 'CALL'
        and row.get('call_hmac_sha256') == selected['call_hmac_sha256'])
    if damage in {'missing-return', 'missing-call'}:
        target = selected if damage == 'missing-return' else call
        records = [r for r in records if r[0] is not target]
    elif damage == 'duplicate-return': records.append((deepcopy(selected), 'EXAMPLE-duplicate', {}))
    elif damage == 'original-not-invoked': selected['original_invoked'] = False
    elif damage == 'wrong-parent': selected['rpc_parent_call_hmac_sha256'] = 'f' * 64
    elif damage == 'wrong-worker-object': selected['rpc_parent_object_hmac_sha256'] = 'f' * 64
    elif damage == 'wrong-rank': selected['scalars']['gpu_rank'] = 0
    elif damage == 'wrong-tp-size': selected['scalars']['configured_tp_size'] = 1
    elif damage == 'before-client': call['observation_ns'] = client['request_started_ns'] - 1
    elif damage == 'after-client': selected['observation_ns'] = client['response_finished_ns'] + 1
    elif damage == 'raise': selected['event'] = 'RAISE'
    elif damage == 'foreign-member':
        row = deepcopy(selected); row['context']['pid'] += 10000
        records.append((row, 'EXAMPLE-foreign', {}))
    elif damage == 'source-gap': selected['gaps'] = ['RANK_RPC_SOURCE_DRIFT']
    elif damage in {'wrong-uid', 'wrong-namespace', 'wrong-clock'}:
        key = {'wrong-uid': 'uid', 'wrong-namespace': 'pid_namespace', 'wrong-clock': 'time_namespace'}[damage]
        client['context'][key] += 1
    elif damage == 'missing-initial-worker':
        records = [r for r in records if not (r[0]['point'] == profile['ipc_topology']['worker_point']
            and r[0].get('scalars', {}).get('rank') == 1)]
    elif damage == 'duplicate-request-call': records.append((deepcopy(call), 'EXAMPLE-duplicate-call', {}))
    elif damage == 'foreign-parent-call':
        parent = next(row for row, _, _ in records if row.get('event') == 'CALL'
            and row.get('call_hmac_sha256') == call['rpc_parent_call_hmac_sha256'])
        row = deepcopy(parent); row['call_hmac_sha256'] = 'f' * 64
        records.append((row, 'EXAMPLE-unpaired-parent', {}))
    elif damage in {'parent-source-drift', 'parent-rank-mismatch'}:
        parent = next(row for row, _, _ in records if row.get('event') == 'RETURN'
            and row.get('call_hmac_sha256') == call['rpc_parent_call_hmac_sha256'])
        if damage == 'parent-source-drift': parent['source_sha256'] = 'f' * 64
        else: parent['scalars']['rpc_rank'] = 0
    value = actor.normal_rpc_returns(profile, records, proof, client)
    assert value['all_declared_member_returns_observed'] is False
    assert value['diagnostic_codes'] and all(c.startswith('RANK_NORMAL_RPC_') for c in value['diagnostic_codes'])
    assert value['scope_complete'] is value['gpu_completion_verified'] is value['producer_registered'] is False


def test_other_topology_is_not_silently_interpreted_as_broadcast_workers():
    profile, records, proof, client = case()
    profile['ipc_topology']['kind'] = 'task-pipe-driver'
    value = actor.normal_rpc_returns(profile, records, proof, client)
    assert value['all_declared_member_returns_observed'] is False
    assert value['diagnostic_codes'] == ['RANK_NORMAL_RPC_TOPOLOGY_UNSUPPORTED']
