"""Shared B template evidence reduction. No dispatch, registry or policy defaults."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib
import json
import re
from guard.models import canonical_json

TYPE_TEMPLATE_SHA256='38110f6e668d55f240ecfde0d38f5fecc7c11a742d76823ffd9d0918abb9fe36'
SOURCE_COMMIT = 'db9527a46873454610df6dbedf79a36d6bf1a7f6'
NORMAL = [{'role': 'user', 'content': 'guard sandbox control'}]
CHALLENGE = [{'role': 'user', 'content': 'guard sandbox boundary'}]
NORMAL_TEXT = 'GUARD_TYPE_BOUNDARY_7f81d490c326: normal\nuser: guard sandbox control\nassistant: '
REFERENCE_TEXT = 'GUARD_TYPE_BOUNDARY_7f81d490c326: int\n'
_ATTR_POINTS = {'SandboxedEnvironment.is_safe_attribute', 'ImmutableSandboxedEnvironment.is_safe_attribute'}

def _sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def _date(value):
    try:
        value = datetime.fromisoformat(value)
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def digest(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()

def native_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()

def _text(value, text):
    return (isinstance(value, dict) and value.get('status') == 'collected' and value.get('kind') == 'text'
            and value.get('sha256') == hashlib.sha256(text.encode()).hexdigest() and value.get('count') == len(text))

def _template(value):
    return isinstance(value, dict) and value.get('status') == 'collected' and value.get('sha256') == TYPE_TEMPLATE_SHA256

def analyze_row(row, case):
    """Fixed-template reference output must belong to this exact render tree."""
    expected_messages = CHALLENGE if case == 'challenge' else NORMAL
    if (not isinstance(row, dict) or row.get('case_id') != case or row.get('fixture_sha256') != TYPE_TEMPLATE_SHA256
            or row.get('messages_sha256') != native_digest(expected_messages)
            or not _sha(row.get('invocation_id_sha256'))
            or row.get('source_validation', {}).get('pre') != 'verified'
            or row.get('source_validation', {}).get('post', {}).get('status') != 'verified'):
        return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
    snapshots = row.get('snapshots', [])
    if not isinstance(snapshots, list):
        return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
    calls, returns, pids = {}, {}, set()
    for snapshot in snapshots:
        binding = snapshot.get('binding', {})
        identity = binding.get('call_id_sha256')
        if (snapshot.get('schema') != 'byul-template-boundary-snapshot/1'
                or binding.get('invocation_id_sha256') != row['invocation_id_sha256']
                or not _sha(identity) or type(binding.get('pid')) is not int):
            return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
        pids.add(binding['pid'])
        if snapshot.get('phase') == 'CALL':
            if identity in calls:
                return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
            calls[identity] = snapshot
        elif snapshot.get('phase') in {'RETURN', 'RAISE'}:
            if identity not in calls or calls[identity].get('point') != snapshot.get('point'):
                return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
            if snapshot.get('phase') == 'RETURN':
                if identity in returns:
                    return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
                returns[identity] = snapshot
    def under(identity, parent):
        seen = set()
        while identity in calls and identity not in seen:
            if identity == parent:
                return True
            seen.add(identity)
            identity = calls[identity].get('binding', {}).get('parent_call_id_sha256')
        return False
    for identity in calls:
        cursor, seen = identity, set()
        while cursor in calls:
            if cursor in seen:
                return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
            seen.add(cursor)
            cursor = calls[cursor].get('binding', {}).get('parent_call_id_sha256')
    safe = [(key, value) for key, value in calls.items() if value.get('point') == 'safe_apply_chat_template']
    if len(pids) != 1 or len(safe) != 1:
        return {'case_id': case, 'qualified': False, 'reference_consumed': False, 'normal': False}
    root, entry = safe[0]
    selected = (_template(entry.get('metadata', {}).get('template'))
        and entry.get('metadata', {}).get('messages_sha256') == native_digest(expected_messages)
        and entry.get('metadata', {}).get('messages_case') == ('challenge' if case == 'challenge' else 'normal'))
    tokenizers = [(key, value) for key, value in calls.items() if value.get('point') == 'tokenizer.apply_chat_template'
        and under(key, root) and value.get('metadata', {}).get('messages_sha256') == native_digest(expected_messages)
        and value.get('metadata', {}).get('messages_case') == ('challenge' if case == 'challenge' else 'normal') and (_template(value.get('metadata', {}).get('template'))
            or _template(returns.get(key, {}).get('metadata', {}).get('template')))]
    selected &= bool(tokenizers)
    actual = returns.get(root, {}).get('metadata', {}).get('result_summary')
    normal = selected and any(_text(returns.get(key, {}).get('metadata', {}).get('result_summary'), NORMAL_TEXT) for key, _ in tokenizers) and row.get('outcome') == 'returned' and _text(actual, NORMAL_TEXT) and _text(row.get('result_summary'), NORMAL_TEXT)
    rendered_reference = (selected and case == 'challenge' and row.get('outcome') == 'returned'
        and _text(actual, REFERENCE_TEXT) and _text(row.get('result_summary'), REFERENCE_TEXT)
        and any(_text(returns.get(key, {}).get('metadata', {}).get('result_summary'), REFERENCE_TEXT) for key, _ in tokenizers))
    allowed, denied = False, False
    compile_allowed, compile_denied = False, False
    for identity, value in returns.items():
        metadata = value.get('metadata', {})
        if (value.get('point') not in _ATTR_POINTS or metadata.get('attribute') != '__class__'
                or metadata.get('object_case') != 'integer_one' or metadata.get('attribute_result_status') != 'collected'
                or metadata.get('evaluation_context') not in {'template_runtime', 'compile_or_optimize'}
                or not any(under(identity, parent) for parent, _ in tokenizers)):
            continue
        # The outer Jinja method's decision governs nested superclass decisions.
        parent = calls.get(identity, {}).get('binding', {}).get('parent_call_id_sha256')
        if calls.get(parent, {}).get('point') in _ATTR_POINTS:
            continue
        if metadata['evaluation_context'] == 'template_runtime':
            allowed |= metadata.get('attribute_allowed') is True
            denied |= metadata.get('attribute_allowed') is False
        else:
            compile_allowed |= metadata.get('attribute_allowed') is True
            compile_denied |= metadata.get('attribute_allowed') is False
    return {'case_id': case, 'qualified': bool(selected), 'normal': bool(normal),
        'attribute_allowed_runtime': allowed, 'attribute_denied_runtime': denied,
        'attribute_allowed_compile': compile_allowed, 'attribute_denied_compile': compile_denied,
        'reference_consumed': bool(rendered_reference),
        'invocation_id_sha256': row['invocation_id_sha256'],
        'messages_sha256': row['messages_sha256'],
        'observation_gaps': ['event_limit_exceeded'] if 'event_limit_exceeded' in row.get('observation_gaps', []) else [],
        'dropped_event_count': row.get('dropped_event_count') if type(row.get('dropped_event_count')) is int and row['dropped_event_count'] >= 0 else None,
        'template_sha256': TYPE_TEMPLATE_SHA256,
        'rendered_sha256': actual.get('sha256') if isinstance(actual, dict) and _sha(actual.get('sha256')) else None}

def _cpu_result(record, *, source_commit, collector_sha256, callback_sha256):
    if (not isinstance(record, dict) or not isinstance(record.get('collection_id'), str)
            or not record['collection_id'].startswith('sup13-template-cpu-')
            or _date(record.get('capture_started_at')) is None or _date(record.get('capture_finished_at')) is None
            or record.get('schema') != 'byul-sup13-template-cpu-capture/1'
            or record.get('source_commit') != source_commit or record.get('source_profile') != 'target031'
            or record.get('own_producer_sha256') != collector_sha256 or record.get('native_callback_sha256') != callback_sha256
            or record.get('fixture_sha256') != TYPE_TEMPLATE_SHA256 or record.get('source_validation', {}).get('pre') != 'verified'):
        return None
    rows = record.get('results', [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows) or len({r.get('case_id') for r in rows}) != len(rows):
        return None
    analyzed = [analyze_row(row, row.get('case_id')) for row in rows if row.get('case_id') in {'before', 'challenge', 'after'}]
    normal = any(r['case_id'] == 'before' and r['normal'] for r in analyzed)
    challenge = next((r for r in analyzed if r['case_id'] == 'challenge'), {})
    return {'mode': 'cpu', 'rows': analyzed, 'identity': any(r['qualified'] for r in analyzed),
        'witness': normal, 'reference_consumed': normal and challenge.get('reference_consumed') is True,
        'complete': record.get('capture_status') == 'completed' and len(analyzed) == 3 and all(r['qualified'] for r in analyzed)
            and not any(row.get('trace_truncated') is True or row.get('observer_errors') or row.get('observer_error_type') or row.get('observation_gaps')
                or type(row.get('dropped_event_count')) is int and row['dropped_event_count'] > 0 for row in rows)
            and record.get('auxiliary_write', {}).get('status', 'completed') == 'completed'
            and normal and any(r['case_id'] == 'after' and r['normal'] for r in analyzed)
            and record.get('source_validation', {}).get('post', {}).get('status') == 'verified',
        'collection_id': record.get('collection_id'), 'started_at': record.get('capture_started_at'),
        'finished_at': record.get('capture_finished_at'),
        'function_inventory': {point: {key: value.get(key) for key in ('module', 'file_sha256', 'code_sha256')}
            for point, value in record.get('function_inventory', {}).items()
            if point in {'safe_apply_chat_template', 'resolve_chat_template', 'tokenizer.apply_chat_template', *_ATTR_POINTS}
            and isinstance(value, dict) and _sha(value.get('file_sha256')) and _sha(value.get('code_sha256'))},
        'installed_target': None, 'gpu_execution_verified': False}


def analyze_cpu_capture(record, *, source_commit, collector_sha256, callback_sha256):
    """Approved caller pins are mandatory; unavailable pins cannot qualify a record."""
    if (type(source_commit) is not str or re.fullmatch('[0-9a-f]{40}',source_commit) is None
            or not _sha(collector_sha256) or not _sha(callback_sha256)):
        return None
    return _cpu_result(record,source_commit=source_commit,collector_sha256=collector_sha256,callback_sha256=callback_sha256)
