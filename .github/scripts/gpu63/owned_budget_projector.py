"""Validate the per-request budget observations against the stock audit's cases."""

from collections import Counter


def first(rows, point):
    return next((r for r in rows if r['point'] == point), None)


def engine_epoch(row):
    return (row['pid'], row['process_start_ticks'], tuple(sorted(row['clock'].items())))


def interval(entered, enqueued, scheduled):
    if not all((entered, enqueued, scheduled)):
        return None
    start, end = scheduled.get('call_start_monotonic_ns'), scheduled['monotonic_ns']
    lower, upper = entered['monotonic_ns'], enqueued['monotonic_ns']
    if type(start) is not int or not lower <= upper <= start <= end:
        return None
    return {'lower_ns': start - upper, 'upper_ns': end - lower,
            'enqueued_monotonic_bounds_ns': [lower, upper], 'first_schedule_monotonic_bounds_ns': [start, end]}


def counts(values):
    if type(values) is list and values and all(type(value) is int and value >= 0 for value in values):
        return values
    return None


def kv_observations(rows):
    allocations = [r for r in rows if r['point'] == 'allocate_slots']
    allocated_by_group = [values for r in allocations if (values := counts(r.get('returned_allocation_by_group'))) is not None]
    closes = [r for r in rows if r['point'] in ('free', 'pop_blocks_for_free')]
    freed = [r for r in closes if r['point'] == 'free']
    associated = [(before, after) for r in freed
                  if (before := counts(r.get('before_group_references'))) is not None
                  and (after := counts(r.get('after_group_references'))) is not None and len(before) == len(after)]
    raw_counters = [counter for r in freed for counter in r['before_reference_counters']]
    counters = [c for c in raw_counters if all(type(c.get(k)) is int and c[k] >= 0 for k in ('before_refcount', 'after_refcount'))]
    reached_zero = [c for c in counters if c['before_refcount'] > 0 and c['after_refcount'] == 0]

    positive_allocations = {}
    for row in allocations:
        values = counts(row.get('returned_allocation_by_group'))
        returned = row.get('monotonic_ns')
        if values is not None and sum(values) > 0 and type(returned) is int:
            for token in row.get('allocated_owned_block_tokens', ()):
                positive_allocations.setdefault(token, returned)
    verified_tokens = set()
    for row in freed:
        started = row.get('call_start_monotonic_ns')
        if type(started) is not int:
            continue
        for counter in row['before_reference_counters']:
            token = counter.get('block_hmac_sha256')
            if (token in positive_allocations and positive_allocations[token] <= started
                    and type(counter.get('before_refcount')) is int and counter['before_refcount'] > 0
                    and type(counter.get('after_refcount')) is int and counter['after_refcount'] == 0):
                verified_tokens.add(token)
    return {
        'kv_calls': [r for r in rows if r['point'] in ('allocate_slots', 'free', 'pop_blocks_for_free')],
        'kv_allocation_return_observed': bool(allocated_by_group),
        'positive_kv_allocation_calls': sum(sum(values) > 0 for values in allocated_by_group) if allocated_by_group else None,
        'zero_kv_allocation_calls': sum(sum(values) == 0 for values in allocated_by_group) if allocated_by_group else None,
        'kv_association_removal_in_free_calls': sum(sum(before) > 0 and sum(after) == 0 for before, after in associated) if associated else None,
        'physical_references_reaching_zero_in_free_calls': len(reached_zero) if counters else None,
        'physical_references_decremented_but_retained_in_free_calls': sum(c['before_refcount'] > c['after_refcount'] > 0 for c in counters) if counters else None,
        'physical_references_unchanged_in_free_calls': sum(c['before_refcount'] == c['after_refcount'] for c in counters) if counters else None,
        'unverified_free_reference_comparisons': len(raw_counters) - len(counters),
        'allocated_block_tokens_reaching_zero': sorted(verified_tokens) if positive_allocations else None,
        'deferred_pops': sum(r['point']=='pop_blocks_for_free' for r in closes),
    }


def core_observations(scoped):
    enters = [r for r in scoped if r['point'] == 'add_request.enter']
    admissions = [r for r in scoped if r['point'] == 'add_request.return']
    if len(enters) != 1 or len(admissions) != 1:
        return {'admission_enter_count': len(enters), 'admission_return_count': len(admissions),
                'gap': 'missing_or_ambiguous_core_admission'}
    entered, admitted = enters[0], admissions[0]
    epoch = engine_epoch(admitted)
    if (admitted.get('registered') is not True or engine_epoch(entered) != epoch
            or entered['monotonic_ns'] > admitted['monotonic_ns']):
        return {'gap': 'unverified_core_admission_epoch'}

    start, finish = entered['monotonic_ns'], admitted['monotonic_ns']
    core = [r for r in scoped if engine_epoch(r) == epoch]
    foreign = Counter(r['point'] for r in scoped if engine_epoch(r) != epoch)
    enqueue = [r for r in core if start <= r['monotonic_ns'] <= finish]
    enqueues = [r for r in enqueue if r['point'] == '_enqueue_waiting_request.enter']
    enqueued = [r for r in enqueue if r['point'] == '_enqueue_waiting_request.return']
    later = [r for r in core if r['monotonic_ns'] >= finish]
    schedules = [r for r in later if r['point'] == 'schedule' and type(r.get('call_start_monotonic_ns')) is int
                 and finish <= r['call_start_monotonic_ns'] <= r['monotonic_ns']]
    scheduled = schedules[0] if schedules else None
    queue = interval(enqueues[0], enqueued[0], scheduled) if len(enqueues) == len(enqueued) == 1 else None
    handled = first([r for r in later if scheduled and r['monotonic_ns'] >= scheduled['monotonic_ns']], 'update_from_output')
    kv = kv_observations([r for r in later if type(r.get('call_start_monotonic_ns')) is int
                          and finish <= r['call_start_monotonic_ns'] <= r['monotonic_ns']])
    missing = []
    for value, name in ((queue, 'same_scheduler_clock_queue_interval'), (handled, 'scheduler_output_handling'),
                        (kv['kv_allocation_return_observed'] or None, 'owned_kv_allocation'),
                        (kv['kv_association_removal_in_free_calls'], 'owned_kv_association_return'),
                        (kv['physical_references_reaching_zero_in_free_calls'], 'post_return_block_reference_counter')):
        if value is None:
            missing.append(name)
    return {'admitted': admitted, 'core_epoch': {k: admitted[k] for k in ('pid', 'process_start_ticks', 'clock')},
            'first_schedule': scheduled, 'queue_residence': queue, 'first_scheduler_output_handling': handled,
            **kv, 'same_request_event_count': len(core), 'other_core_epoch_events_excluded': dict(foreign),
            'missing_requested_observations': missing}


def project(trials, points, journal, *, pod_uid, session):
    rows = sorted((r for r in journal if r['pod_uid']==pod_uid and r['session']==session), key=lambda r: r['monotonic_ns'])
    stages = Counter(row['point'] for row in rows)
    found = []
    for trial in trials:
        case = trial['case_id']
        point = points.get(case)
        assignments = [r for r in rows if r['point']=='assign_request_id' and r['case_id']==case
                       and point is not None and (r.get('product_connection_sha256'),r.get('product_call_sha256')) == tuple(point)]
        record = {'case_id': case, 'stock_http_outcome': trial['outcome'], 'request_assignment_matches': len(assignments)}
        if len(assignments) != 1:
            found.append({**record, 'gap': 'exact_product_request_to_engine_assignment_unconfirmed'})
            continue
        authority = assignments[0]
        tag = authority['native_request_hmac_sha256']
        scoped = [r for r in rows if r['point'] not in ('assign_request_id','observer_error')
                  and r.get('native_request_hmac_sha256')==tag and r.get('case_id')==case]
        found.append({**record, 'assignment': authority, **core_observations(scoped)})
    return {'cases':found,'producer_events_by_point':dict(stages),
            'observer_errors':[r for r in rows if r['point'] in ('observer_error','installed_source_mismatch')],
            'observed_installed_sources':[r for r in rows if r['point']=='installed_source_verified'],
            'raw_journal':rows,'whole_original_budget_policy_verified':False,
            'scope':'stock fixed requests, per-request scheduler intervals and reference counts from one owned Pod; no GPU pool deallocation claim'}
