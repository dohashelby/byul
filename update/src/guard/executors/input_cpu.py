"""B-1 bounded renderer/admission fixtures for REQ-07/08/09.

Actual engine objects are supplied by an isolated reference environment.
No transport, engine, common registry, policy or final-consumer claim is added.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from .structured_limits import observe_call

PROFILE = 'b1-input-cpu-v1'


def fixtures():
    rows = []
    for key, value, expected in (
        ('text', {'prompt': 'hello world'}, 'accepted'),
        ('reuse-valid', {'prompt_token_ids': [3, 4]}, 'accepted'),
        ('reuse-negative', {'prompt_token_ids': [-1]}, 'rejected'),
        ('reuse-oov', {'prompt_token_ids': [32]}, 'rejected'),
        ('reuse-long', {'prompt_token_ids': [3] * 16}, 'rejected'),
        ('reuse-and-text', {'prompt_token_ids': [3], 'prompt': 'hello world'}, None),
    ):
        rows.append({'id': key, 'check_id': 'REQ-08', 'kind': 'completion', 'value': value, 'expected': expected})
    for length in (0, 1, 2, 3, 4):
        rows.append({'id': f'mask-{length}', 'check_id': 'REQ-09', 'kind': 'mask',
                     'value': {'shape': [3, 4], 'mask_length': length},
                     'expected': 'accepted' if length == 3 else 'rejected'})
    for shape in ([3], [3, 5]):
        rows.append({'id': 'shape-' + '-'.join(map(str, shape)), 'check_id': 'REQ-09', 'kind': 'shape',
                     'value': {'shape': shape}, 'expected': None})
    for offset in (-1, 0, 3, 4, 2**127):
        rows.append({'id': f'offset-{offset}', 'check_id': 'REQ-09', 'kind': 'offset',
                     'value': offset, 'expected': 'accepted' if 0 <= offset <= 3 else 'rejected'})
    for key, template, expected in (
        ('template-default', None, 'accepted'),
        ('template-boundary', 'hello ' * 15, 'accepted'),
        ('template-overbound', 'hello ' * 16, 'rejected'),
        ('template-fixed-override', 'hello world', 'accepted'),
    ):
        rows.append({'id': key, 'check_id': 'REQ-07', 'kind': 'chat', 'value': template, 'expected': expected})
    for side in ('left', 'right'):
        rows.append({'id': f'reuse-truncate-{side}', 'check_id': 'REQ-08', 'kind': 'truncate',
                     'value': {'ids': [3, 4, 5, 6], 'side': side}, 'expected': 'accepted',
                     'expected_admitted_ids': [5, 6] if side == 'left' else [3, 4]})
    for count in (15, 16):
        rows.append({'id': f'template-kwargs-{count}', 'check_id': 'REQ-07', 'kind': 'chat-kwargs',
                     'value': 'hello ' * count, 'expected': 'accepted' if count == 15 else 'rejected'})
    return rows


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def describe(case):
    return {'case_id': case['id'], 'check_id': case['check_id'], 'stage': 'renderer_and_input_processor',
            'expected': case['expected'], 'fixture_canonical_sha256': digest(case)}


def run_reference(processor, sampling_class, chat_params_class, embeds_input, tensor_zeros,
                  rejection_type, tracked=(), request_classes=None):
    renderer = processor.renderer
    rows = []
    def admit(prompt):
        inputs = renderer.render_cmpl([prompt])
        return processor.process_inputs('b1-cpu-fixture', inputs[0], sampling_class(max_tokens=1), ('generate',))
    for case in fixtures():
        details = {}
        def invoke():
            kind, value = case['kind'], deepcopy(case['value'])
            if kind == 'completion':
                request = admit(value)
            elif kind in ('mask', 'shape'):
                kwargs = {}
                if kind == 'mask':
                    kwargs = {'prompt_token_ids': [3, 4, 5], 'is_token_ids': [False] * value['mask_length']}
                prompt = embeds_input(tensor_zeros(value['shape']), **kwargs)
                request = processor.process_inputs('b1-cpu-fixture', prompt, sampling_class(max_tokens=1), ('generate',))
            elif kind == 'truncate':
                req = request_classes['completion'](model='fixture-model', prompt=value['ids'], max_tokens=1,
                    truncate_prompt_tokens=2, truncation_side=value['side'])
                inputs = renderer.render_cmpl([{'prompt_token_ids': req.prompt}], req.build_tok_params(processor.model_config))
                request = processor.process_inputs('b1-cpu-fixture', inputs[0], sampling_class(max_tokens=1), ('generate',))
            elif kind == 'chat-kwargs':
                req = request_classes['chat'](model='fixture-model', messages=[{'role': 'user', 'content': 'hello'}],
                    chat_template='{{ suffix }}', chat_template_kwargs={'suffix': value}, max_tokens=1)
                _, inputs = renderer.render_chat([req.messages], req.build_chat_params(None, 'auto'), req.build_tok_params(processor.model_config))
                request = processor.process_inputs('b1-cpu-fixture', inputs[0], sampling_class(max_tokens=1), ('generate',))
            elif kind == 'offset':
                prompt = {'type': 'token', 'prompt_token_ids': [3, 4, 5]}
                params = sampling_class(max_tokens=1, routed_experts_prompt_start=value)
                request = processor.process_inputs('b1-cpu-fixture', prompt, params, ('generate',))
            else:
                _, inputs = renderer.render_chat([[{'role': 'user', 'content': 'hello world'}]],
                                                 chat_params_class(chat_template=value))
                request = processor.process_inputs('b1-cpu-fixture', inputs[0], sampling_class(max_tokens=1), ('generate',))
            ids = request.prompt_token_ids
            details['prompt_count'] = len(ids) if ids is not None else None
            details['token_ids_canonical_sha256'] = digest(ids) if ids is not None else None
            details['embedding_shape'] = list(request.prompt_embeds.shape) if request.prompt_embeds is not None else None
            details['mask_count'] = len(request.prompt_is_token_ids) if request.prompt_is_token_ids is not None else None
        row = {**describe(case), **observe_call(invoke, rejection_type, tracked)}
        if row['outcome'] == 'accepted':
            row['admitted_metadata'] = details
        row['after'] = observe_call(lambda: admit({'prompt': 'hello world'}), rejection_type, tracked)
        rows.append(row)
    return rows


def project(rows):
    cases = {row['id']: row for row in fixtures()}
    specs = {key: describe(row) for key, row in cases.items()}
    seen, violations = set(), {}
    for row in rows:
        key = row.get('case_id')
        if key not in specs or key in seen:
            raise ValueError('unknown or duplicate input CPU case')
        seen.add(key)
        spec = specs[key]
        if any(row.get(k) != spec[k] for k in ('check_id', 'stage', 'fixture_canonical_sha256')):
            raise ValueError('input CPU fixture or boundary mismatch')
        if spec['expected'] == 'rejected' and row.get('outcome') == 'accepted':
            violations.setdefault(spec['check_id'], []).append(key)
        expected_ids = cases[key].get('expected_admitted_ids')
        actual_hash = (row.get('admitted_metadata') or {}).get('token_ids_canonical_sha256')
        if (expected_ids is not None and row.get('outcome') == 'accepted'
                and actual_hash is not None and actual_hash != digest(expected_ids)):
            violations.setdefault(spec['check_id'], []).append(key)
    return {check: {'input_cpu_confirmed_violations': {'value': violations.get(check, []), 'collected': any(r.get('check_id') == check for r in rows)}}
            for check in ('REQ-07', 'REQ-08', 'REQ-09')}


def judge_params():
    return {'fail_when_any': [{'obs': 'input_cpu_confirmed_violations', 'op': 'nonempty'}]}
