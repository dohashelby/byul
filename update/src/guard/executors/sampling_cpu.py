"""REQ-10 actual request/SamplingParams CPU fixture extension.

Four is an explicit local test cap, never a new enterprise/default policy.
"""
from __future__ import annotations
from .input_cpu import digest
from .structured_limits import observe_call
CAP = 4


def fixtures():
    rows = []
    for endpoint in ('completion', 'chat', 'batch', 'responses'):
        for count in (CAP, CAP + 1):
            rows.append({'id': f'{endpoint}-stop-{count}', 'kind': 'stop', 'endpoint': endpoint,
                         'value': count, 'expected': 'accepted' if count == CAP else 'rejected'})
    for kind in ('n', 'beam', 'chat-beam', 'bad-words'):
        for count in (CAP, CAP + 1):
            rows.append({'id': f'{kind}-{count}', 'kind': kind, 'value': count,
                         'expected': 'accepted' if count == CAP else 'rejected'})
    rows += [{'id': 'stop-token-dedup', 'kind': 'stop-token-dedup', 'expected': 'accepted'},
             {'id': 'bad-word-dedup', 'kind': 'bad-word-dedup', 'expected': 'accepted'}]
    return rows


def describe(row):
    return {'case_id': row['id'], 'stage': 'request_or_sampling_params',
            'expected': row['expected'], 'fixture_canonical_sha256': digest(row)}


def run_reference(request_classes, sampling_class, beam_class, tokenizer, rejection_type, tracked=()):
    rows = []
    for case in fixtures():
        metadata = {}
        def invoke():
            kind, value = case['kind'], case.get('value')
            if kind == 'stop':
                endpoint = case['endpoint']
                payload = {'completion': {'prompt': 'hello'}, 'chat': {'messages': [{'role': 'user', 'content': 'hello'}]},
                           'batch': {'messages': [[{'role': 'user', 'content': 'hello'}]]}, 'responses': {'input': 'hello'}}[endpoint]
                request = request_classes[endpoint](model='fixture-model', stop=[f'stop-{i}' for i in range(value)], **payload)
                metadata['stop_count'] = len(request.stop)
            elif kind == 'n':
                metadata['n'] = sampling_class(n=value, max_tokens=1).n
            elif kind == 'beam':
                metadata['beam_width'] = beam_class(beam_width=value, max_tokens=1).beam_width
            elif kind == 'chat-beam':
                request = request_classes['chat'](model='fixture-model', messages=[{'role': 'user', 'content': 'hello'}],
                                                  n=value, stream=True, use_beam_search=True, max_tokens=1)
                metadata['beam_width'] = request.to_beam_search_params(max_tokens=1, default_sampling_params={}).beam_width
            elif kind == 'stop-token-dedup':
                params = sampling_class(stop_token_ids=[4, 7, 4, 9, 7])
                metadata['normalized_count'] = len(params.stop_token_ids)
                metadata['normalized_canonical_sha256'] = digest(params.stop_token_ids)
            elif kind == 'bad-word-dedup':
                params = sampling_class(bad_words=['hello', 'world', 'hello'])
                metadata['normalized_count'] = len(params.bad_words)
                metadata['normalized_canonical_sha256'] = digest(params.bad_words)
            else:
                params = sampling_class(bad_words=[f'word{i}' for i in range(7, 7 + value)])
                params.update_from_tokenizer(tokenizer)
                metadata['tokenized_bad_word_count'] = len(params.bad_words_token_ids)
        row = {**describe(case), **observe_call(invoke, rejection_type, tracked)}
        if row['outcome'] == 'accepted': row['normalized_metadata'] = metadata
        row['after'] = observe_call(lambda: sampling_class(max_tokens=1), rejection_type, tracked)
        rows.append(row)
    return rows


def project(rows):
    specs = {r['id']: describe(r) for r in fixtures()}
    seen, violations = set(), []
    for row in rows:
        key = row.get('case_id')
        if key not in specs or key in seen: raise ValueError('unknown or duplicate sampling case')
        seen.add(key)
        if any(row.get(k) != specs[key][k] for k in ('stage', 'fixture_canonical_sha256')):
            raise ValueError('sampling fixture/boundary mismatch')
        if specs[key]['expected'] == 'rejected' and row.get('outcome') == 'accepted': violations.append(key)
    return {'sampling_cpu_confirmed_violations': {'value': violations, 'collected': bool(rows)}}


def judge_params():
    return {'fail_when_any': [{'obs': 'sampling_cpu_confirmed_violations', 'op': 'nonempty'}]}
