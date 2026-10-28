"""SUP-10 finite local plugin loader observations, not a universal plugin policy."""
from __future__ import annotations

GROUPS = ('vllm.general_plugins', 'vllm.endpoint_plugins', 'vllm.logits_processors', 'vllm.io_processor_plugins')
NAMES = ('byul-approved', 'byul-unapproved', 'byul-task-mismatch')
PHASES = ('import', 'general_hook', 'endpoint_factory', 'attach_router', 'init_state',
          'endpoint_handler', 'io_factory', 'io_constructor', 'io_preprocess', 'io_postprocess')
CASES = (
    ('general-allowlisted', GROUPS[0], 'byul-approved'),
    ('general-disabled', GROUPS[0], ''),
    ('general-unset', GROUPS[0], None),
    ('endpoint-allowlisted', GROUPS[1], 'byul-approved'),
    ('endpoint-disabled', GROUPS[1], ''),
    ('endpoint-unset', GROUPS[1], None),
    ('endpoint-task-mismatch', GROUPS[1], 'byul-task-mismatch'),
    ('logits-allowlisted', GROUPS[2], 'byul-approved'),
    ('io-allowlisted', GROUPS[3], 'byul-approved'),
    ('io-two-allowed-one-selected', GROUPS[3], 'byul-approved,byul-unapproved'),
)


def sanitize_events(events):
    result = []
    for event in events:
        if (type(event) is not dict or set(event) != {'plugin', 'phase'}
                or event['plugin'] not in NAMES or event['phase'] not in PHASES):
            raise ValueError('invalid plugin fixture event')
        result.append(dict(event))
    return result


def project(rows):
    """Keep independent source-filter violations across other collection errors.

    The logits loader has no VLLM_PLUGINS guarantee. Task-mismatch import and
    factory initialization precede task selection by design in this source.
    Neither fact is silently turned into an enterprise-policy FAIL.
    """
    specs = {row[0]: row for row in CASES}
    phases, violations, errors = {}, [], []
    for row in rows:
        key = row.get('case_id') if isinstance(row, dict) else None
        if key not in specs or key in phases:
            errors.append('unknown_or_duplicate_case')
            continue
        if row.get('status') != 'completed':
            errors.append(key)
            if row.get('target_started') is not True:
                phases[key] = 'unknown'
                continue
        if row.get('event_collection_status', 'completed') != 'completed':
            errors.append(key)
        try:
            events = sanitize_events(row.get('events', []))
        except ValueError:
            phases[key] = 'unknown'
            errors.append(key)
            continue
        phases[key] = [event['plugin'] + ':' + event['phase'] for event in events]
        _, group, configured = specs[key]
        # Only apply an allowlist where the selected reference loader promises it.
        guarded = group != GROUPS[2] and (configured is not None or group == GROUPS[1])
        allowed = set(configured.split(',')) if configured else set()
        if guarded and any(event['plugin'] not in allowed for event in events):
            violations.append(key)
        if key == 'endpoint-task-mismatch' and any(e['phase'] in {'attach_router', 'init_state', 'endpoint_handler'} for e in events):
            violations.append(key)
    return {'plugin_cpu_phases': {'value': phases or None, 'collected': bool(phases)},
            'plugin_loading_confirmed_violations': {'value': sorted(set(violations)), 'collected': bool(rows)},
            'plugin_cpu_cases_complete': {'value': set(phases) == set(specs) and not errors and all(v != 'unknown' for v in phases.values()), 'collected': True}}


def judge_params():
    return {'fail_when_any': [{'obs': 'plugin_loading_confirmed_violations', 'op': 'nonempty'}]}
