"""Closed LIF12 declarations; never authentic automatic retry or cost proof."""
from datetime import datetime
import re

from guard.models import canonical_json, sha256_hex

PROFILE = 'kubernetes-automatic-retry-record-observation-v1'
SCOPE_SCHEMA = 'guard-automatic-retry-scope/1'
RECORD_SCHEMA = 'guard-automatic-retry-records/1'
MAX_SUBJECTS = 64
MAX_EVENTS = 256
POLICY_TYPES = {key: 'str' for key in (
    'retry_environment_sha256', 'retry_target_sha256', 'retry_scope_sha256',
    'retry_policy_sha256', 'retry_record_source_sha256', 'retry_session',
    'retry_validity_start', 'retry_validity_end', 'retry_policy_mode')}
POLICY_TYPES['retry_max_record_delay_ms'] = 'int'
OBSERVATIONS = {key: 'list' for key in (
    'retry_record_snapshots', 'retry_event_declarations', 'retry_attempt_candidates',
    'retry_family_candidates', 'retry_cost_candidates', 'retry_missing',
    'retry_conflicts', 'retry_collection', 'retry_unverified')}
OBSERVATIONS.update({key: 'bool' for key in (
    'retry_records_collected', 'retry_producer_authenticated', 'automatic_retry_verified',
    'retry_nonoccurrence_verified', 'retry_previous_work_termination_verified',
    'retry_cost_enforcement_verified', 'retry_scope_complete')})
MODES = frozenset({'UNRESOLVED', 'FORBID', 'ALLOW'})
SCENARIOS = frozenset({'NORMAL', 'MANUAL_CONTROL', 'AUTOMATIC_CHALLENGE'})
INITIATORS = frozenset({'INITIAL', 'MANUAL', 'AUTOMATIC', 'UNKNOWN'})
TRIGGERS = frozenset({'TIMEOUT', 'CONNECTION_CLOSED', 'SERVER_FAILURE', 'OTHER', 'UNKNOWN'})
TERMINALS = frozenset({'RETURNED', 'FAILED', 'CANCELLED', 'ABORTED', 'UNAVAILABLE'})
UNITS = frozenset({'attempt', 'token', 'gpu_ms', 'cpu_ms', 'usd_micros'})
SCOPE_FIELDS = frozenset({'schema', 'policy_sha256', 'run_id', 'producer_id',
    'producer_source_sha256', 'producer_code_sha256', 'configuration_sha256', 'native_source_sha256', 'image_digest',
    'model_revision', 'model_inventory_sha256', 'subjects', 'limits'})
SUBJECT_FIELDS = frozenset({'subject_id', 'actor_id', 'request_family_id', 'scenario',
    'pid', 'process_start_ticks', 'worker_generation', 'clock_domain_sha256'})
LIMIT_FIELDS = frozenset({'max_attempts', 'max_overlap_ms', 'max_remaining_ms',
    'remaining_anchor', 'max_return_ms', 'return_anchor', 'max_cost'})
RECORD_FIELDS = frozenset({'schema', 'environment_ref', 'target_sha256', 'session',
    'run_id', 'source_sha256', 'code_sha256', 'scope_sha256', 'policy_sha256',
    'producer_id', 'snapshot_generation', 'recorded_at', 'declared_complete', 'events'})
EVENT_FIELDS = frozenset({'event_id', 'seq', 'type', 'subject_id', 'actor_id',
    'request_family_id', 'attempt_id', 'parent_attempt_id', 'pid', 'process_start_ticks',
    'worker_generation', 'clock_domain_sha256', 'monotonic_ns', 'at', 'initiator', 'data'})
EVENT_DATA = {
    'CONFIGURATION_CLAIM': {'mode', 'config_sha256', 'trigger_kind'},
    'CALL_CLAIM': {'phase', 'outcome'},
    'ATTEMPT_START_CLAIM': {'native_event_ref'},
    'ATTEMPT_END_CLAIM': {'outcome', 'native_event_ref'},
    'TRIGGER_CLAIM': {'trigger_id', 'kind', 'native_event_ref'},
    'RETRY_DECISION_CLAIM': {'trigger_id', 'decision', 'next_attempt_id'},
    'PREVIOUS_WORK_CLAIM': {'previous_attempt_id', 'state', 'resource_scope', 'native_event_ref'},
    'COST_SAMPLE_CLAIM': {'cost_subject', 'unit', 'epoch_id', 'cumulative_amount', 'counter_source_sha256'},
    'RETURN_CLAIM': {'outcome', 'native_event_ref'},
}


def fingerprint(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def opaque(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{32}', value) is not None


def run_token(value):
    return type(value) is str and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', value) is not None


def uint(value, maximum=2**63 - 1):
    return type(value) is int and 0 <= value <= maximum


def stamp(value):
    if type(value) is not str or not 1 <= len(value) <= 40:
        raise ValueError('RETRY_TIME_INVALID')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('RETRY_TIME_INVALID')
    return result


def require(condition):
    if not condition:
        raise ValueError('RETRY_CONTRACT_INVALID')


def normalize_policy(values):
    try:
        require(type(values) is dict and set(values) == set(POLICY_TYPES))
        require(all(fingerprint(values[k]) for k in POLICY_TYPES if k.endswith('_sha256')))
        require(opaque(values['retry_session']) and values['retry_policy_mode'] in MODES)
        start, end = stamp(values['retry_validity_start']), stamp(values['retry_validity_end'])
        require(0 < (end - start).total_seconds() <= 600)
        require(uint(values['retry_max_record_delay_ms'], 600000))
        return start, end
    except (KeyError, TypeError, ValueError):
        raise ValueError('RETRY_POLICY_INVALID') from None


def validate_scope(value, *, policy_sha256, source_sha256, image_digest,
                   model_revision, model_inventory_sha256):
    try:
        require(type(value) is dict and set(value) == SCOPE_FIELDS and value['schema'] == SCOPE_SCHEMA)
        require(value['policy_sha256'] == policy_sha256 and fingerprint(value['policy_sha256']))
        require(run_token(value['run_id']) and opaque(value['producer_id']))
        require(value['producer_source_sha256'] == source_sha256 and fingerprint(source_sha256))
        require(fingerprint(value['producer_code_sha256']))
        require(fingerprint(value['configuration_sha256']))
        require(value['native_source_sha256'] is None or fingerprint(value['native_source_sha256']))
        require(value['image_digest'] == image_digest and type(image_digest) is str
                and image_digest.startswith('sha256:') and fingerprint(image_digest[7:]))
        require(value['model_revision'] == model_revision and type(model_revision) is str and 1 <= len(model_revision) <= 256)
        require(value['model_inventory_sha256'] == model_inventory_sha256 and fingerprint(model_inventory_sha256))
        require(type(value['subjects']) is list and len(value['subjects']) <= MAX_SUBJECTS)
        ids, families = set(), {}
        for row in value['subjects']:
            require(type(row) is dict and set(row) == SUBJECT_FIELDS)
            require(all(opaque(row[k]) for k in ('subject_id', 'actor_id', 'request_family_id', 'worker_generation')))
            require(row['subject_id'] not in ids and row['scenario'] in SCENARIOS)
            require(uint(row['pid'], 2**31 - 1) and row['pid'] > 0
                    and uint(row['process_start_ticks']) and row['process_start_ticks'] > 0
                    and fingerprint(row['clock_domain_sha256']))
            ids.add(row['subject_id'])
            family = (row['actor_id'], row['scenario'])
            require(families.setdefault(row['request_family_id'], family) == family)
        limits = value['limits']
        require(type(limits) is dict and set(limits) == LIMIT_FIELDS)
        for key in ('max_attempts', 'max_overlap_ms', 'max_remaining_ms', 'max_return_ms'):
            maximum = 64 if key == 'max_attempts' else 600000
            require(limits[key] is None or uint(limits[key], maximum))
        require(limits['max_attempts'] is None or limits['max_attempts'] > 0)
        for key, amount, allowed in (
                ('remaining_anchor', 'max_remaining_ms', {'RETRY_DECISION', 'NEW_ATTEMPT_START'}),
                ('return_anchor', 'max_return_ms', {'CALL_START', 'TRIGGER'})):
            require(limits[key] is None if limits[amount] is None else limits[key] in allowed)
        cost = limits['max_cost']
        if cost is not None:
            require(type(cost) is dict and set(cost) == {'unit', 'amount', 'counter_source_sha256', 'epoch_id'})
            require(cost['unit'] in UNITS and uint(cost['amount']) and fingerprint(cost['counter_source_sha256']) and opaque(cost['epoch_id']))
        return value
    except (KeyError, TypeError, ValueError):
        raise ValueError('RETRY_SCOPE_INVALID') from None


def native_ref(value, row):
    if value is None:
        return
    require(type(value) is dict and set(value) == {'recording_sha256', 'event_seq', 'request_id_sha256',
        'session', 'run_id', 'source_sha256', 'pid', 'process_start_ticks'})
    require(fingerprint(value['recording_sha256']) and uint(value['event_seq'], 2**31 - 1))
    require(value['request_id_sha256'] is None or fingerprint(value['request_id_sha256']))
    require(opaque(value['session']) and run_token(value['run_id']) and fingerprint(value['source_sha256']))
    require(type(value['pid']) is int and value['pid'] == row['pid'])
    require(type(value['process_start_ticks']) is int and value['process_start_ticks'] == row['process_start_ticks'])


def validate_event(row):
    require(type(row) is dict and set(row) == EVENT_FIELDS and row['type'] in EVENT_DATA)
    require(all(opaque(row[k]) for k in ('event_id', 'subject_id', 'actor_id', 'request_family_id', 'worker_generation')))
    require(uint(row['seq'], 2**31 - 1) and uint(row['pid'], 2**31 - 1) and row['pid'] > 0)
    require(uint(row['process_start_ticks']) and row['process_start_ticks'] > 0 and fingerprint(row['clock_domain_sha256']))
    require(row['monotonic_ns'] is None or uint(row['monotonic_ns']))
    require(row['initiator'] in INITIATORS)
    require(row['attempt_id'] is None or opaque(row['attempt_id']))
    require(row['parent_attempt_id'] is None or opaque(row['parent_attempt_id']))
    stamp(row['at'])
    data, kind = row['data'], row['type']
    require(type(data) is dict and set(data) == EVENT_DATA[kind])
    if 'native_event_ref' in data:
        native_ref(data['native_event_ref'], row)
    if kind == 'CONFIGURATION_CLAIM':
        require(row['attempt_id'] is None and row['parent_attempt_id'] is None)
        require(data['mode'] in MODES and fingerprint(data['config_sha256']) and data['trigger_kind'] in TRIGGERS)
    elif kind == 'CALL_CLAIM':
        require(row['attempt_id'] is None and row['parent_attempt_id'] is None)
        require(data['phase'] in {'START', 'END'})
        require(data['outcome'] == 'STARTED' if data['phase'] == 'START' else data['outcome'] in TERMINALS)
    elif kind in {'ATTEMPT_START_CLAIM', 'ATTEMPT_END_CLAIM', 'TRIGGER_CLAIM', 'RETRY_DECISION_CLAIM', 'PREVIOUS_WORK_CLAIM'}:
        require(opaque(row['attempt_id']))
        if kind == 'ATTEMPT_END_CLAIM':
            require(data['outcome'] in TERMINALS)
        elif kind == 'TRIGGER_CLAIM':
            require(opaque(data['trigger_id']) and data['kind'] in TRIGGERS)
        elif kind == 'RETRY_DECISION_CLAIM':
            require(data['trigger_id'] is None or opaque(data['trigger_id']))
            require(data['decision'] in {'ALLOWED', 'DENIED', 'UNAVAILABLE'})
            require(data['next_attempt_id'] is None or opaque(data['next_attempt_id']))
        elif kind == 'PREVIOUS_WORK_CLAIM':
            require(opaque(data['previous_attempt_id']) and data['state'] in {'REMAINING', 'TERMINATED', 'UNAVAILABLE'})
            require(data['resource_scope'] in {'ENGINE_TASK', 'WORKER_REQUEST', 'KERNEL_RESOURCE', 'UNKNOWN'})
    elif kind == 'COST_SAMPLE_CLAIM':
        require(data['cost_subject'] in {'ATTEMPT', 'FAMILY'} and data['unit'] in UNITS)
        require(opaque(data['epoch_id']) and uint(data['cumulative_amount']) and fingerprint(data['counter_source_sha256']))
        require(opaque(row['attempt_id']) if data['cost_subject'] == 'ATTEMPT' else row['attempt_id'] is None)
    elif kind == 'RETURN_CLAIM':
        require(row['attempt_id'] is None and row['parent_attempt_id'] is None and data['outcome'] in TERMINALS)
    return row


def validate_record(value, *, environment_ref, target_sha256, values, scope):
    try:
        require(type(value) is dict and set(value) == RECORD_FIELDS and value['schema'] == RECORD_SCHEMA)
        ref = value['environment_ref']
        require(type(ref) is dict and set(ref) == {'kind', 'id', 'revision', 'sha256'})
        require(ref == environment_ref and ref['kind'] == 'ENV' and type(ref['revision']) is int and ref['revision'] > 0)
        require(type(ref['id']) is str and 1 <= len(ref['id']) <= 256 and fingerprint(ref['sha256']))
        require(value['target_sha256'] == target_sha256 and value['session'] == values['retry_session'])
        require(value['run_id'] == scope['run_id'] and value['producer_id'] == scope['producer_id'])
        require(value['source_sha256'] == values['retry_record_source_sha256']
                and value['code_sha256'] == scope['producer_code_sha256'])
        require(value['scope_sha256'] == values['retry_scope_sha256'] and value['policy_sha256'] == values['retry_policy_sha256'])
        require(opaque(value['snapshot_generation']) and type(value['declared_complete']) is bool)
        stamp(value['recorded_at'])
        require(type(value['events']) is list and len(value['events']) <= MAX_EVENTS)
        for row in value['events']:
            validate_event(row)
            ref = row['data'].get('native_event_ref')
            if ref is not None:
                require(ref['session'] == values['retry_session'] and ref['run_id'] == scope['run_id']
                        and scope['native_source_sha256'] is not None and ref['source_sha256'] == scope['native_source_sha256'])
        require(len({r['attempt_id'] for r in value['events'] if r['attempt_id'] is not None}) <= 64)
        return value
    except (KeyError, TypeError, ValueError):
        raise ValueError('RETRY_RECORD_INVALID') from None


def process_key(row):
    return tuple(row[k] for k in ('subject_id', 'pid', 'process_start_ticks', 'worker_generation', 'clock_domain_sha256'))


def declaration_delta_ms(left, right):
    # Only declaration-internal arithmetic in exactly one selected process/epoch.
    # A matching digest across different PIDs never establishes a common clock.
    if (process_key(left) != process_key(right) or left['monotonic_ns'] is None
            or right['monotonic_ns'] is None or right['monotonic_ns'] < left['monotonic_ns']):
        return None
    return (right['monotonic_ns'] - left['monotonic_ns']) / 1_000_000


def declaration_overlap_ms(parent, parent_end, child, child_end):
    if any(process_key(row) != process_key(parent) for row in (parent_end, child, child_end)):
        return None
    if declaration_delta_ms(parent, parent_end) is None or declaration_delta_ms(child, child_end) is None:
        return None
    return max(0, min(parent_end['monotonic_ns'], child_end['monotonic_ns'])
               - max(parent['monotonic_ns'], child['monotonic_ns'])) / 1_000_000


def declaration_remaining_ms(anchor, terminal):
    if process_key(anchor) != process_key(terminal) or anchor['monotonic_ns'] is None or terminal['monotonic_ns'] is None:
        return None
    return max(0, terminal['monotonic_ns'] - anchor['monotonic_ns']) / 1_000_000


def project(scope, snapshots, values):
    start, end = normalize_policy(values)
    result = {'events': [], 'attempt_candidates': [], 'family_candidates': [], 'cost_candidates': [],
        'missing': [], 'conflicts': [], 'unverified': ['actual_retry_producer_trigger_work_cost_and_authority_unverified'],
        'producer_authenticated': False, 'automatic_retry_verified': False, 'nonoccurrence_verified': False,
        'previous_work_termination_verified': False, 'cost_enforcement_verified': False, 'whole_scope_complete': False}
    if values['retry_policy_mode'] == 'UNRESOLVED':
        result['unverified'].append('enterprise_retry_policy_unresolved')
    subjects = {row['subject_id']: row for row in scope['subjects']}
    unique, sequences, generations = {}, {}, set()
    for snapshot in snapshots:
        packet = snapshot['packet']
        generations.add(packet['snapshot_generation'])
        if not packet['declared_complete']:
            result['missing'].append('declared_record_partial')
        if not start <= stamp(packet['recorded_at']) <= end:
            result['missing'].append('record_outside_policy_window')
        for row in packet['events']:
            if not start <= stamp(row['at']) <= end:
                result['missing'].append('event_outside_policy_window')
            delay = (stamp(packet['recorded_at']) - stamp(row['at'])).total_seconds() * 1000
            if delay < 0 or delay > values['retry_max_record_delay_ms']:
                result['missing'].append('record_clock_or_delay_unverified')
            digest = sha256_hex(canonical_json(row))
            if row['event_id'] in unique and unique[row['event_id']][0] != digest:
                result['conflicts'].append('event_id_payload_conflict')
            unique.setdefault(row['event_id'], (digest, row))
            if row['seq'] in sequences and sequences[row['seq']] != row['event_id']:
                result['conflicts'].append('event_sequence_conflict')
            sequences[row['seq']] = row['event_id']
    if len(generations) > 1:
        result['conflicts'].append('record_snapshot_generation_changed')
    if sequences and sorted(sequences) != list(range(len(sequences))):
        result['missing'].append('record_sequence_gap')
    rows = [pair[1] for pair in sorted(unique.values(), key=lambda pair: (pair[1]['seq'], pair[1]['event_id']))]
    result['events'] = rows
    accepted = []
    for row in rows:
        subject = subjects.get(row['subject_id'])
        if subject is None or any(row[k] != subject[k] for k in SUBJECT_FIELDS - {'scenario'}):
            result['conflicts'].append('event_subject_family_actor_or_process_generation_mismatch')
        else:
            accepted.append(row)
    scenarios = {row['scenario'] for row in scope['subjects']}
    for scenario in SCENARIOS - scenarios:
        result['missing'].append('scenario_unprovided_' + scenario)
    families = sorted({row['request_family_id'] for row in scope['subjects']})
    starts_by_id = {}
    for row in accepted:
        if row['type'] == 'ATTEMPT_START_CLAIM':
            starts_by_id.setdefault(row['attempt_id'], []).append(row)
    if any(len(v) != 1 for v in starts_by_id.values()):
        result['conflicts'].append('attempt_id_start_ambiguous')
    for row in accepted:
        if row['type'] == 'ATTEMPT_START_CLAIM':
            if row['initiator'] == 'UNKNOWN':
                result['missing'].append('attempt_initiator_unavailable')
            if row['initiator'] == 'INITIAL' and row['parent_attempt_id'] is not None:
                result['conflicts'].append('initial_attempt_has_retry_parent')
            seen, current = set(), row
            while current is not None:
                if current['attempt_id'] in seen:
                    result['conflicts'].append('attempt_parent_cycle')
                    break
                seen.add(current['attempt_id'])
                parents = starts_by_id.get(current['parent_attempt_id'], [])
                current = parents[0] if len(parents) == 1 else None
        elif row['type'] == 'ATTEMPT_END_CLAIM':
            beginnings = starts_by_id.get(row['attempt_id'], [])
            if len(beginnings) != 1 or process_key(beginnings[0]) != process_key(row):
                result['missing'].append('terminal_without_unique_selected_attempt_start')
        elif row['type'] == 'RETRY_DECISION_CLAIM' and row['data']['decision'] == 'ALLOWED':
            following = starts_by_id.get(row['data']['next_attempt_id'], [])
            if (len(following) != 1 or following[0]['initiator'] != 'AUTOMATIC'
                    or following[0]['parent_attempt_id'] != row['attempt_id']
                    or following[0]['request_family_id'] != row['request_family_id']
                    or following[0]['actor_id'] != row['actor_id']):
                result['missing'].append('retry_decision_next_unique_automatic_attempt_unavailable')
    for family in families:
        family_rows = [row for row in accepted if row['request_family_id'] == family]
        family_subjects = [row for row in scope['subjects'] if row['request_family_id'] == family]
        scenario = family_subjects[0]['scenario']
        kinds = {row['type'] for row in family_rows}
        required = {'CONFIGURATION_CLAIM', 'CALL_CLAIM', 'ATTEMPT_START_CLAIM', 'ATTEMPT_END_CLAIM', 'COST_SAMPLE_CLAIM', 'RETURN_CLAIM'}
        if scenario == 'AUTOMATIC_CHALLENGE':
            required |= {'TRIGGER_CLAIM', 'RETRY_DECISION_CLAIM', 'PREVIOUS_WORK_CLAIM'}
        for kind in required - kinds:
            result['missing'].append('family_missing_' + kind)
        call_rows = {phase: [row for row in family_rows if row['type'] == 'CALL_CLAIM' and row['data']['phase'] == phase]
                     for phase in ('START', 'END')}
        calls = {phase: rows[0] for phase, rows in call_rows.items() if len(rows) == 1}
        if set(calls) != {'START', 'END'}:
            result['missing'].append('call_start_end_pair_unavailable')
        starts = [row for row in family_rows if row['type'] == 'ATTEMPT_START_CLAIM']
        manual = [row for row in starts if row['initiator'] == 'MANUAL']
        automatic = [row for row in starts if row['initiator'] == 'AUTOMATIC']
        initial = [row for row in starts if row['initiator'] == 'INITIAL']
        if (scenario == 'MANUAL_CONTROL' and not manual) or (scenario != 'MANUAL_CONTROL' and len(initial) != 1):
            result['missing'].append('scenario_attempt_baseline_unavailable')
        if any(row['data']['mode'] != values['retry_policy_mode'] for row in family_rows if row['type'] == 'CONFIGURATION_CLAIM'):
            result['conflicts'].append('declared_effective_configuration_mode_mismatch')
        if any(row['data']['config_sha256'] != scope['configuration_sha256'] for row in family_rows if row['type'] == 'CONFIGURATION_CLAIM'):
            result['conflicts'].append('declared_effective_configuration_hash_mismatch')
        family_candidate = {'request_family_id': family, 'scenario': scenario,
            'declared_initial_attempts': len(initial), 'declared_manual_attempts': len(manual),
            'declared_automatic_attempts': len(automatic), 'manual_is_automatic_proof': False,
            'declared_attempt_limit_exceeded': scope['limits']['max_attempts'] is not None
                and len(initial) + len(automatic) > scope['limits']['max_attempts'],
            'declared_forbidden_automatic_attempt': values['retry_policy_mode'] == 'FORBID' and bool(automatic),
            'actual_nonoccurrence_verified': False, 'actual_cost_or_retry_enforcement_verified': False,
            'confirmed_violation': False, 'declaration_internal_return_ms': None, 'declared_return_limit_exceeded': None}
        returns = [row for row in family_rows if row['type'] == 'RETURN_CLAIM']
        triggers = [row for row in family_rows if row['type'] == 'TRIGGER_CLAIM']
        anchor = calls.get('START') if scope['limits']['return_anchor'] == 'CALL_START' else triggers[0] if scope['limits']['return_anchor'] == 'TRIGGER' and len(triggers) == 1 else None
        if anchor is not None and len(returns) == 1:
            elapsed = declaration_delta_ms(anchor, returns[0])
            family_candidate['declaration_internal_return_ms'] = elapsed
            if elapsed is not None and scope['limits']['max_return_ms'] is not None:
                family_candidate['declared_return_limit_exceeded'] = elapsed > scope['limits']['max_return_ms']
            elif scope['limits']['max_return_ms'] is not None:
                result['unverified'].append('cross_process_or_clock_return_unverified')
        result['family_candidates'].append(family_candidate)
        for row in starts:
            ends = [r for r in family_rows if r['type'] == 'ATTEMPT_END_CLAIM'
                and r['attempt_id'] == row['attempt_id'] and process_key(r) == process_key(row)
                and r['initiator'] == row['initiator'] and r['parent_attempt_id'] == row['parent_attempt_id']]
            if len(ends) != 1:
                result['missing'].append('attempt_terminal_pair_unavailable')
            elif declaration_delta_ms(row, ends[0]) is None:
                result['unverified'].append('attempt_declaration_clock_order_unverified')
            if row['initiator'] != 'AUTOMATIC':
                continue
            parents = starts_by_id.get(row['parent_attempt_id'], [])
            parent = parents[0] if len(parents) == 1 and parents[0]['request_family_id'] == family and parents[0]['actor_id'] == row['actor_id'] else None
            decisions = [r for r in family_rows if r['type'] == 'RETRY_DECISION_CLAIM'
                and r['attempt_id'] == row['parent_attempt_id'] and r['data']['next_attempt_id'] == row['attempt_id']]
            linked_triggers = [t for t in triggers if len(decisions) == 1 and t['data']['trigger_id'] == decisions[0]['data']['trigger_id']
                and t['attempt_id'] == row['parent_attempt_id']]
            parent_ends = [r for r in family_rows if parent is not None and r['type'] == 'ATTEMPT_END_CLAIM'
                and r['attempt_id'] == parent['attempt_id'] and process_key(r) == process_key(parent)
                and r['initiator'] == parent['initiator'] and r['parent_attempt_id'] == parent['parent_attempt_id']]
            previous = [r for r in family_rows if r['type'] == 'PREVIOUS_WORK_CLAIM'
                and r['attempt_id'] == row['attempt_id'] and r['data']['previous_attempt_id'] == row['parent_attempt_id']
                and process_key(r) == process_key(row)]
            linked = bool(parent and len(decisions) == len(linked_triggers) == 1)
            if not linked:
                result['missing'].append('automatic_parent_trigger_decision_link_unavailable')
            if not previous:
                result['missing'].append('previous_work_claim_unavailable')
            overlap = declaration_overlap_ms(parent, parent_ends[0], row, ends[0]) if parent is not None and len(parent_ends) == len(ends) == 1 else None
            remaining_anchor = decisions[0] if scope['limits']['remaining_anchor'] == 'RETRY_DECISION' and len(decisions) == 1 else row if scope['limits']['remaining_anchor'] == 'NEW_ATTEMPT_START' else None
            remaining = declaration_remaining_ms(remaining_anchor, parent_ends[0]) if remaining_anchor is not None and len(parent_ends) == 1 else None
            if parent is not None and process_key(parent) != process_key(row):
                result['unverified'].append('cross_process_or_worker_generation_overlap_unverified')
            if overlap is None and scope['limits']['max_overlap_ms'] is not None:
                result['unverified'].append('overlap_clock_or_terminal_unverified')
            if remaining is None and scope['limits']['max_remaining_ms'] is not None:
                result['unverified'].append('remaining_work_clock_or_terminal_unverified')
            result['attempt_candidates'].append({'event_id': row['event_id'], 'attempt_id': row['attempt_id'],
                'parent_attempt_id': row['parent_attempt_id'], 'declared_parent_trigger_decision_link': linked,
                'declared_denied_but_attempt_started': any(r['data']['decision'] == 'DENIED' for r in decisions),
                'declared_previous_work_remaining': any(r['data']['state'] == 'REMAINING' for r in previous),
                'declaration_internal_overlap_ms': overlap, 'declaration_internal_remaining_ms': remaining,
                'declared_overlap_limit_exceeded': overlap is not None and scope['limits']['max_overlap_ms'] is not None and overlap > scope['limits']['max_overlap_ms'],
                'declared_remaining_limit_exceeded': remaining is not None and scope['limits']['max_remaining_ms'] is not None and remaining > scope['limits']['max_remaining_ms'],
                'actual_automatic_causation_verified': False, 'actual_previous_work_termination_verified': False, 'confirmed_violation': False})
        counters, counter_epochs, tainted = {}, {}, set()
        for row in family_rows:
            if row['type'] != 'COST_SAMPLE_CLAIM':
                continue
            data = row['data']
            key = (data['cost_subject'], row['attempt_id'], data['unit'], data['counter_source_sha256'], data['epoch_id'])
            counter = key[:-1]
            if counter in counter_epochs and counter_epochs[counter] != data['epoch_id']:
                result['unverified'].append('cost_counter_epoch_changed_no_sum')
            counter_epochs[counter] = data['epoch_id']
            previous = counters.get(key)
            reset = previous is not None and data['cumulative_amount'] < previous
            if reset:
                result['missing'].append('cost_counter_reset_or_order_unverified')
                tainted.add(key)
            counters[key] = data['cumulative_amount']
            limit = scope['limits']['max_cost']
            comparable = bool(limit is not None and data['cost_subject'] == 'FAMILY'
                and data['unit'] == limit['unit'] and data['counter_source_sha256'] == limit['counter_source_sha256']
                and data['epoch_id'] == limit['epoch_id'] and key not in tainted)
            if limit is not None and not comparable:
                result['unverified'].append('cost_unit_source_epoch_or_counter_not_comparable')
            result['cost_candidates'].append({'event_id': row['event_id'], 'request_family_id': family,
                'attempt_id': row['attempt_id'], **data, 'declared_counter_reset': reset,
                'declared_limit_comparable': comparable, 'declared_limit_exceeded': data['cumulative_amount'] > limit['amount'] if comparable else None,
                'summed_across_units_sources_epochs': False, 'actual_cost_verified': False, 'confirmed_violation': False})
        if values['retry_policy_mode'] == 'ALLOW' and any(value is None for value in scope['limits'].values()):
            result['unverified'].append('adopted_allow_limits_unprovided')
    for key in ('missing', 'conflicts', 'unverified'):
        result[key] = sorted(set(result[key]))
    return result
