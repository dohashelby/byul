"""REQ-09 real serialized-embedding loader boundary, not model execution."""
from __future__ import annotations
import hashlib
from .input_cpu import digest
from .structured_limits import observe_call


def fixtures():
    return [{'id': key, 'shape': shape, 'dtype': dtype, 'enabled': enabled, 'expected': expected}
            for key, shape, dtype, enabled, expected in (
                ('matrix-f32', [3, 4], 'float32', True, 'accepted'),
                ('batch-f32', [1, 3, 4], 'float32', True, 'accepted'),
                ('hidden-mismatch', [3, 5], 'float32', True, 'rejected'),
                ('flat', [3], 'float32', True, 'rejected'),
                ('four-dim', [1, 1, 3, 4], 'float32', True, 'rejected'),
                ('integer', [3, 4], 'int64', True, 'rejected'),
                ('boolean', [3, 4], 'bool', True, 'rejected'),
                ('complex', [3, 4], 'complex64', True, 'rejected'),
                ('floating-cast', [3, 4], 'float64', True, 'accepted'),
                ('feature-disabled', [3, 4], 'float32', False, 'rejected'))]


def describe(case):
    return {'case_id': case['id'], 'stage': 'safe_load_prompt_embeds', 'expected': case['expected'],
            'fixture_canonical_sha256': digest(case)}


def run_reference(loader, enabled_config, disabled_config, make_payload, rejection_type, tracked=()):
    rows = []
    normal = fixtures()[0]
    for case in fixtures():
        payload = make_payload(case['shape'], case['dtype'])
        metadata = {}
        def invoke():
            tensor = loader(enabled_config if case['enabled'] else disabled_config, payload)
            metadata.update(shape=list(tensor.shape), dtype=str(tensor.dtype))
        row = {**describe(case), 'payload_bytes_sha256': hashlib.sha256(payload).hexdigest(),
               'payload_bytes': len(payload), **observe_call(invoke, rejection_type, tracked)}
        if row['outcome'] == 'accepted': row['normalized_metadata'] = metadata
        row['after'] = observe_call(lambda: loader(enabled_config, make_payload(normal['shape'], normal['dtype'])), rejection_type, tracked)
        rows.append(row)
    return rows


def project(rows):
    specs = {r['id']: describe(r) for r in fixtures()}
    seen, violations = set(), []
    for row in rows:
        key = row.get('case_id')
        if key not in specs or key in seen: raise ValueError('unknown or duplicate embedding case')
        seen.add(key)
        if any(row.get(k) != specs[key][k] for k in ('stage', 'fixture_canonical_sha256')):
            raise ValueError('embedding fixture/boundary mismatch')
        if specs[key]['expected'] == 'rejected' and row.get('outcome') == 'accepted': violations.append(key)
    return {'embedding_cpu_confirmed_violations': {'value': violations, 'collected': bool(rows)}}


def judge_params():
    return {'fail_when_any': [{'obs': 'embedding_cpu_confirmed_violations', 'op': 'nonempty'}]}
