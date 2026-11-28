import pytest

from owned_budget_projector import project


@pytest.fixture
def observation():
    def row(point, stamp, **kw):
        return {'point': point, 'pid': 1, 'process_start_ticks': 42, 'clock': {'inode': 8}, 'monotonic_ns': stamp,
                'native_request_hmac_sha256': 'owned', 'case_id': 'case', 'pod_uid': 'pod', 'session': 'live'} | kw
    trial = {'case_id': 'case', 'outcome': 200}
    rows = [row('assign_request_id', 10, pid=7, process_start_ticks=6, clock={'inode': 4},
                product_connection_sha256='connection', product_call_sha256='call'),
            row('add_request.enter', 14),
            row('_enqueue_waiting_request.enter', 15), row('_enqueue_waiting_request.return', 17),
            row('add_request.return', 18, registered=True),
            row('schedule', 27, call_start_monotonic_ns=22), row('update_from_output', 34),
            row('allocate_slots', 25, call_start_monotonic_ns=23, returned_allocation_by_group=[1], allocated_owned_block_tokens=['owned-block']),
            row('free', 40, before_group_references=[1], after_group_references=[0],
                call_start_monotonic_ns=38,
                before_reference_counters=[{'before_refcount': 1, 'after_refcount': 0, 'block_hmac_sha256': 'owned-block'},
                                           {'before_refcount': 2, 'after_refcount': 1}]),
            row('pop_blocks_for_free', 45, call_start_monotonic_ns=44, before_group_references=[1], after_group_references=[0],
                before_reference_counters=[{'before_refcount': 1, 'after_refcount': 0}])]
    def run(call='call', pod='pod'):
        result, = project([trial], {'case': ('connection', call)}, rows, pod_uid=pod, session='live')['cases']
        return result
    return rows, run


def test_owned_assigned_engine_request_is_attributed_with_conservative_queue_and_kv_counts(observation):
    rows, run = observation
    result = run()
    assert result['request_assignment_matches'] == 1
    assert result['assignment']['pid'] != result['core_epoch']['pid']
    assert result['queue_residence']['lower_ns'] == 5 and result['queue_residence']['upper_ns'] == 12
    assert result['positive_kv_allocation_calls'] == 1 and result['zero_kv_allocation_calls'] == 0
    assert result['physical_references_reaching_zero_in_free_calls'] == 1
    assert result['physical_references_decremented_but_retained_in_free_calls'] == 1
    assert result['allocated_block_tokens_reaching_zero'] == ['owned-block']
    assert result['deferred_pops'] == 1
    assert result['missing_requested_observations'] == []
    assert run(call='different')['request_assignment_matches'] == 0
    assert run(pod='other')['request_assignment_matches'] == 0


@pytest.mark.parametrize('epoch_shift', [{'pid': 264}, {'process_start_ticks': 644}, {'clock': {'inode': 9}}])
def test_different_engine_generation_contributes_no_queue_output_allocation_or_free(observation, epoch_shift):
    rows, run = observation
    for entry in rows:
        if entry['point'] in ('schedule','update_from_output','allocate_slots','free','pop_blocks_for_free'):
            entry.update(epoch_shift)
    result = run()
    assert result['queue_residence'] is None and 'same_scheduler_clock_queue_interval' in result['missing_requested_observations']
    assert result['first_scheduler_output_handling'] is None
    assert result['positive_kv_allocation_calls'] is None
    assert result['physical_references_reaching_zero_in_free_calls'] is None
    assert result['other_core_epoch_events_excluded']['free'] == 1


@pytest.mark.parametrize('epoch_shift',[{}, {'process_start_ticks': 644}])
def test_duplicate_admission_leaves_the_case_unresolved_in_a_single_or_changed_core(observation, epoch_shift):
    rows, run = observation
    admission = next(row for row in rows if row['point']=='add_request.return')
    rows.append(admission | epoch_shift | {'monotonic_ns': 19})
    result = run()
    assert result['admission_return_count'] == 2
    assert result['gap'] == 'missing_or_ambiguous_core_admission'
    assert 'kv_calls' not in result


def test_observed_zero_allocation_and_unchanged_shared_reference_do_not_prove_positive_return(observation):
    rows, run = observation
    allocated = next(row for row in rows if row['point']=='allocate_slots')
    allocated.update(returned_allocation_by_group=[0], allocated_owned_block_tokens=[])
    free = next(row for row in rows if row['point']=='free')
    free.update(before_reference_counters=[{'before_refcount': 2, 'after_refcount': 2}])
    result = run()
    assert result['positive_kv_allocation_calls'] == 0 and result['zero_kv_allocation_calls'] == 1
    assert result['kv_association_removal_in_free_calls'] == 1
    assert result['physical_references_reaching_zero_in_free_calls'] == 0
    assert result['physical_references_unchanged_in_free_calls'] == 1
    assert result['missing_requested_observations'] == []


@pytest.mark.parametrize('free_entry,expected',[(25,['owned-block']),(24,[]),(None,[])])
def test_same_block_pair_requires_positive_allocation_before_or_at_free_entry(observation, free_entry, expected):
    rows, run = observation
    free = next(row for row in rows if row['point']=='free')
    free['call_start_monotonic_ns'] = free_entry
    result = run()
    assert result['allocated_block_tokens_reaching_zero'] == expected
    if free_entry is not None:
        assert result['physical_references_reaching_zero_in_free_calls'] == 1
    else:
        assert result['physical_references_reaching_zero_in_free_calls'] is None


def test_zero_allocation_does_not_claim_a_same_token_pair(observation):
    rows, run = observation
    next(row for row in rows if row['point']=='allocate_slots')['returned_allocation_by_group']=[0]
    assert run()['allocated_block_tokens_reaching_zero'] is None
