"""REQ-10 bounded reference fixtures; depth caps are engine rules, not policy.

No vLLM import, model, transport or enterprise limits are introduced here.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import sys
import time

from .structured_tools import SOURCE_COMMIT

PROFILE = 'structured-limits-cpu-v1'
DEPTH_LIMIT = 64
TAG_WRAPPER_ALLOWANCE = 16


def nested_schema(levels):
    if type(levels) is not int or not 1 <= levels <= 201:
        raise ValueError('fixture depth must be an integer from 1 to 201')
    value = {'type': 'string'}
    for _ in range(levels - 1):
        value = {'type': 'array', 'items': value}
    return value


def fixtures():
    """Finite official-depth/boundary and modest width/size examples."""
    result = []
    for levels in (63, 64, 65):
        for encoding in ('dict', 'json'):
            schema = nested_schema(levels)
            result.append({'id': f'depth-{levels}-{encoding}', 'kind': 'json',
                           'value': schema if encoding == 'dict' else json.dumps(schema),
                           'expected': 'rejected' if levels > DEPTH_LIMIT else 'accepted',
                           'rule': 'reference_depth'})
    for size in (32, 4096):
        result.append({'id': f'description-{size}', 'kind': 'json',
                       'value': {'type': 'string', 'description': 'a' * size},
                       'expected': None, 'rule': 'size_observation_only'})
    for width in (2, 16):
        result.append({'id': f'alternatives-{width}', 'kind': 'json',
                       'value': {'anyOf': [{'const': f'option-{i}'} for i in range(width)]},
                       'expected': None, 'rule': 'complexity_observation_only'})
    result.append({'id': 'unsupported-unique-items', 'kind': 'json',
                   'value': {'type': 'array', 'items': {'type': 'string'}, 'uniqueItems': True},
                   'expected': 'rejected', 'rule': 'xgrammar_support'})
    for field in ('response_format', 'structured_outputs'):
        for depth in (1, 201):
            tag = {'type': 'structural_tag', 'format': {'type': 'json_schema', 'json_schema': nested_schema(depth)}}
            result.append({'id': f'{field}-depth-{depth}', 'kind': field, 'value': tag,
                           'expected': 'accepted' if depth == 1 else 'rejected', 'rule': 'reference_tag_depth'})
    return result


def describe(case):
    encoded = json.dumps(case['value'], sort_keys=True, separators=(',', ':')).encode()
    return {'case_id': case['id'], 'kind': case['kind'], 'rule': case['rule'],
            'input_canonical_sha256': hashlib.sha256(encoded).hexdigest(),
            'input_canonical_bytes': len(encoded), 'expected': case['expected']}


def observe_call(call, rejection_type, tracked=()):
    """Observe actual Python entry points without patching the target functions.

    Absence of a traced entry is only meaningful inside this synchronous call,
    with that entry positively observed in the corresponding normal control.
    """
    if sys.getprofile() is not None:
        return {'outcome': 'execution_error', 'events': [], 'elapsed_seconds': 0.0}
    codes = {fn.__code__: label for label, fn in tracked}
    events = set()
    def profile(frame, event, arg):
        if event == 'call' and frame.f_code in codes:
            events.add(codes[frame.f_code])
    start = time.perf_counter()
    try:
        sys.setprofile(profile)
        call()
        outcome = 'accepted'
    except rejection_type:
        outcome = 'rejected'
    except Exception:
        outcome = 'execution_error'
    finally:
        sys.setprofile(None)
    return {'outcome': outcome, 'events': sorted(events), 'elapsed_seconds': time.perf_counter() - start}


def run_reference(request_class, sampling_class, structured_class, backend_validator,
                  rejection_type, tracked=()):
    """Run real objects supplied by the pinned CPU environment, never mocks here."""
    results = []
    for case in fixtures():
        entry = describe(case)
        value = deepcopy(case['value'])
        if case['kind'] == 'json':
            entry['stage'] = 'backend_validation'
            def invoke(value=value):
                backend_validator(sampling_class(structured_outputs=structured_class(json=value)))
        else:
            entry['stage'] = 'request_class'
            body = {'model': 'fixture-model', 'messages': [{'role': 'user', 'content': 'fixture'}]}
            body[case['kind']] = value if case['kind'] == 'response_format' else {'structural_tag': json.dumps(value)}
            def invoke(body=body):
                request_class.model_validate(body)
        entry.update(observe_call(invoke, rejection_type, tracked))
        entry['after'] = observe_call(lambda: backend_validator(
            sampling_class(structured_outputs=structured_class(json={'type': 'string'}))), rejection_type, tracked)
        results.append(entry)
    return results


def project(results):
    """Scoped independent violations survive errors in other cases/follow-ups."""
    allowed = {case['id']: describe(case) for case in fixtures()}
    violations, seen = [], set()
    for row in results:
        key = row.get('case_id')
        if key not in allowed or key in seen:
            raise ValueError('unknown or duplicate structured limit case')
        seen.add(key)
        spec = allowed[key]
        if row.get('input_canonical_sha256') != spec['input_canonical_sha256']:
            raise ValueError('structured limit fixture identity mismatch')
        if spec['expected'] == 'rejected' and row.get('outcome') == 'accepted':
            violations.append(key)
    return {'structured_limits_confirmed_violations': {'value': violations, 'collected': bool(results)},
            'structured_limits_cases_complete': {'value': seen == set(allowed), 'collected': True}}


def judge_params():
    return {'fail_when_any': [{'obs': 'structured_limits_confirmed_violations', 'op': 'nonempty'}]}
