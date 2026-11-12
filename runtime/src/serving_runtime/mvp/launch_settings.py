"""Allowlisted declared launch settings; never retain the original argv."""
from __future__ import annotations

import ipaddress
import re
from typing import Any

BOOL_KEYS = {'trust_remote_code', 'trust_request_mm_kwargs', 'enable_multimodal'}
KEYS = ('trust_remote_code', 'trust_request_mm_kwargs', 'code_revision',
        'revision', 'tokenizer_revision', 'enable_multimodal')
# Defaults for vLLM 0.31.0 and SGLang 0.5.22, only for applicable flags.
DEFAULTS = {
    'vllm': {'trust_remote_code': False, 'trust_request_mm_kwargs': False,
             'code_revision': None, 'revision': None, 'tokenizer_revision': None},
    'sglang': {'trust_remote_code': False, 'revision': None, 'enable_multimodal': False},
}


def validate_settings(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.keys() - set(KEYS):
        raise ValueError('launch_settings must contain only allowlisted keys')
    for key, item in value.items():
        if item is None:
            continue
        valid = type(item) is bool if key in BOOL_KEYS else isinstance(item, str) and len(item) <= 128
        if not valid:
            raise ValueError('launch_settings value has invalid type or length')
    return dict(value)


def _revision(value: str) -> str | None:
    # Do not retain substitutions, URLs, control characters or unbounded values.
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,127}', value):
        return None
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    return None


def observe(container_spec: Any, engine: str | None) -> dict[str, Any]:
    defaults = DEFAULTS.get(engine, {})
    if isinstance(container_spec, dict) and 'launch_settings' in container_spec:
        # Host snapshot transport contains normalized allowlisted values only.
        reduced = container_spec.get('launch_settings')
        candidate = reduced.get(engine) if isinstance(reduced, dict) else None
        if (isinstance(candidate, dict) and set(candidate) == {'source', 'settings'}
                and candidate.get('source') == 'kubernetes_spec'):
            settings = candidate.get('settings')
            if isinstance(settings, dict) and set(settings) == set(KEYS):
                safe = True
                for key, row in settings.items():
                    if not isinstance(row, dict) or set(row) != {'state', 'value'}:
                        safe = False
                        break
                    state, value = row['state'], row['value']
                    if state == 'observed' and key in defaults:
                        if key in BOOL_KEYS:
                            safe = safe and type(value) is bool
                        else:
                            safe = safe and (value is None or isinstance(value, str) and _revision(value) == value)
                    elif state in {'unknown', 'not_applicable'} and value is None:
                        safe = safe and (state != 'not_applicable' or key not in defaults)
                    else:
                        safe = False
                if safe:
                    return candidate
        container_spec = None
    values = dict(defaults)
    unknown = not defaults or not isinstance(container_spec, dict)
    argv: list[str] = []
    if not unknown:
        unknown = not any(k in container_spec for k in ('command', 'args'))
        for key in ('command', 'args'):
            part = container_spec.get(key, [])
            if not isinstance(part, list) or not all(isinstance(v, str) for v in part):
                unknown = True
                break
            argv.extend(part)
    # Shell wrappers and external config can override or hide the declaration.
    if any(v in {'sh', 'bash', '/bin/sh', '/bin/bash', '-c', '-lc'} for v in argv):
        unknown = True
    invalid: set[str] = set()
    spellings = {key.replace('_', '-') for key in defaults}
    if engine == 'vllm':
        spellings.update(f'no-{key.replace("_", "-")}' for key in defaults if key in BOOL_KEYS)
    spellings.add('config')
    i = 0
    while i < len(argv) and not unknown:
        token = argv[i]
        i += 1
        if not token.startswith('--'):
            continue
        flag, sep, inline = token[2:].partition('=')
        if engine == 'vllm':
            flag = flag.replace('_', '-')
        elif '_' in flag:
            continue
        if flag not in spellings and any(option.startswith(flag) for option in spellings):
            # argparse may accept a proper prefix, but its full option table
            # determines ambiguity and value consumption. Do not guess either
            # the abbreviated value or the meaning of subsequent argv tokens.
            unknown = True
            break
        if flag == 'config':
            unknown = True
            break
        negative = flag.startswith('no-')
        key = (flag[3:] if negative else flag).replace('-', '_')
        if key not in defaults:
            continue
        if key in BOOL_KEYS:
            if negative and engine != 'vllm':
                invalid.add(key)
            elif sep and (negative or inline.lower() not in {'true', 'false'}):
                invalid.add(key)
            else:
                values[key] = inline.lower() == 'true' if sep else not negative
        elif negative:
            invalid.add(key)
        else:
            if not sep:
                if i >= len(argv) or argv[i].startswith('-'):
                    invalid.add(key)
                    continue
                inline = argv[i]
                i += 1
            normalized = _revision(inline)
            if normalized is None:
                invalid.add(key)
            else:
                values[key] = normalized
    return {
        'source': 'kubernetes_spec',
        'settings': {key: {
            'value': None if unknown or key in invalid or key not in defaults else values[key],
            'state': ('not_applicable' if defaults and key not in defaults else
                      'unknown' if unknown or key in invalid else 'observed'),
        } for key in KEYS},
    }


def comparison_rows(approved: dict[str, Any] | None, pods: Any) -> list[dict[str, Any]]:
    """Use only fresh, unambiguous observations. Null approval is unconfirmed."""
    snapshot = pods.get('last_value') if isinstance(pods, dict) and pods.get('state') == 'ok' else None
    observation = snapshot.get('launch_settings') if isinstance(snapshot, dict) else None
    settings = observation.get('settings', {}) if isinstance(observation, dict) else {}
    rows = []
    for key in KEYS:
        current = settings.get(key, {})
        expected = (approved or {}).get(key)
        state = current.get('state', 'unknown')
        observed = current.get('value') if state == 'observed' else None
        if state == 'observed':
            state = ('unapproved' if key not in (approved or {}) else 'unknown' if expected is None else
                     'match' if expected == observed else 'drift')
        note = None
        if state == 'unknown':
            note = '승인값 확인 안 됨' if current.get('state') == 'observed' else '관측 불가 (unknown)'
        rows.append({'field': f'launch_settings.{key}', 'expected': expected,
                     'observed': observed, 'source': 'kubernetes_spec', 'state': state,
                     'note': note,
                     'observed_at': (snapshot.get('collected_at') or pods.get('last_success_at')) if isinstance(snapshot, dict) else None,
                     'data_origin': pods.get('data_origin') if isinstance(pods, dict) else None})
    return rows
