"""Bounded repeated timing/response-signal collection; no privacy verdict.

The caller supplies already authorized, prepared request callbacks. Presence,
identity, shared cache, load control and native correspondence require separate
evidence. This module never starts an engine or modifies cache/credentials.
"""
from __future__ import annotations

from copy import deepcopy
import math
import time
import uuid

from guard.models import sha256_hex
from guard.runtime.bounded import strict_json

PROFILE = 'cache-request-timing-callback-v1'
CASES = frozenset({'normal-before', 'present', 'absent', 'normal-after'})
MAX_SAMPLES = 128


def response_signal(packet):
    """Extract only exact response fields after complete bounded body capture."""
    outcome = packet.get('outcome')
    if not (type(outcome) is int and 100 <= outcome <= 599 or type(outcome) is str and outcome in {
            'refused', 'timeout', 'unreachable', 'dns_error', 'error', 'probe_error'}):
        outcome = None
    result = {'response_complete': False, 'cached_tokens': None, 'cache_read_input_tokens': None,
        'outcome': outcome, 'signal_fields_present': [], 'normal_shape_verified': False}
    text = packet.get('body')
    if (type(text) is not str or len(text.encode()) > 131072 or packet.get('capture_complete') is not True
            or packet.get('eof') is not True or packet.get('utf8_valid') is not True
            or packet.get('body_truncated') is not False or type(packet.get('body_bytes')) is not int
            or packet['body_bytes'] != len(text.encode()) or packet.get('body_sha256') != sha256_hex(text.encode())):
        return result
    try: body = strict_json(text.encode(), limit=131072)
    except (ValueError, TypeError): return result
    if type(body) is not dict: return result
    result['response_complete'] = True
    usage = body.get('usage')
    if type(usage) is dict:
        details = usage.get('prompt_tokens_details')
        pairs = [('cached_tokens', details.get('cached_tokens') if type(details) is dict else None),
            ('cache_read_input_tokens', usage.get('cache_read_input_tokens'))]
        for key, value in pairs:
            if type(value) is int and 0 <= value < 2**63:
                result[key] = value; result['signal_fields_present'].append(key)
    choices = body.get('choices')
    result['normal_shape_verified'] = bool(type(packet.get('outcome')) is int and packet['outcome'] == 200
        and type(choices) is list and 1 <= len(choices) <= 8 and all(type(c) is dict and (
            type(c.get('text')) is str or type(c.get('message')) is dict and type(c['message'].get('content')) is str)
            for c in choices))
    return result


def collect_timing_experiment(probe, plan, policy_values, *, clock=None):
    """Invoke every preselected callback once, preserving facts after failures.

    ``probe(case_id, case)`` returns an existing complete HTTP result packet.
    ``plan`` fixes order before measurement: each item has case_id and case only.
    Repetitions and admissible inference/error criteria remain policy values;
    neither client labels nor elapsed time prove prefix presence or isolation.
    """
    if (type(plan) is not list or not 4 <= len(plan) <= MAX_SAMPLES
            or any(type(p) is not dict or set(p) != {'case_id', 'case'} or type(p['case_id']) is not str
                or not p['case_id'] or len(p['case_id']) > 80 or type(p['case']) is not str or p['case'] not in CASES for p in plan)
            or len({p['case_id'] for p in plan}) != len(plan)
            or plan[0]['case'] != 'normal-before' or plan[-1]['case'] != 'normal-after'
            or not {'present', 'absent'} <= {p['case'] for p in plan}):
        raise ValueError('CACHE_TIMING_PLAN_INVALID')
    clock = clock or time.monotonic_ns
    rows = []; missing = set()
    for number, entry in enumerate(plan):
        # Do not persist caller labels: a label may itself contain private text.
        row = {'index': number, 'case_id_sha256': sha256_hex(entry['case_id'].encode()), 'case': entry['case'],
            'attempted': True, 'elapsed_ns': None, 'callback_completed': False}
        start = clock()
        try:
            packet = probe(entry['case_id'], entry['case'])
            finish = clock()
            if type(packet) is not dict: raise ValueError
            row.update(response_signal(packet), callback_completed=True)
            if type(start) is int and type(finish) is int and 0 <= start <= finish:
                row['elapsed_ns'] = finish - start
            else: missing.add('CACHE_TIMING_CLOCK_UNAVAILABLE')
        except Exception:
            missing.add('CACHE_TIMING_REQUEST_UNAVAILABLE:' + str(number))
        if not row.get('response_complete'): missing.add('CACHE_TIMING_RESPONSE_INCOMPLETE:' + str(number))
        rows.append(row)
    if not rows[0].get('normal_shape_verified') or not rows[-1].get('normal_shape_verified'):
        missing.add('CACHE_TIMING_NORMAL_CONTROLS_UNAVAILABLE')
    distributions = {case: [r['elapsed_ns'] for r in rows if r['case'] == case and r['elapsed_ns'] is not None]
        for case in ('present', 'absent')}
    # The unmodified original policy is deliberately separate from measured facts.
    return {'profile': PROFILE, 'policy_values': deepcopy(policy_values), 'trials': rows,
        'latency_distributions_ns': distributions, 'missing': sorted(missing | {
            'native_cache_presence_and_request_correspondence_unverified', 'trusted_observer_identity_unverified',
            'concurrent_load_and_noise_control_unverified', 'statistical_inference_rule_not_evaluated',
            'metrics_reader_scope_unverified', 'native_profile_installer_unimplemented'}),
        'normal_controls': bool(rows[0].get('normal_shape_verified') and rows[-1].get('normal_shape_verified')),
        'scope_complete': False, 'native_profile_supported': False, 'producer_authenticated': False}


def collect_kubernetes_timing(transport, plan, requests, policy_values, *, timeout=10):
    """Use the existing target-bound transport for closed one-token requests.

    No credentials, engine creation or target replacement are introduced here.
    The existing transport verifies the selected Pod/probe before and after each
    call. Native cache presence/identity/consumption still need separate facts.
    """
    from guard.runtime.kubernetes_http import KubernetesHTTP, REQUEST_BINDING_PROFILE
    if (type(transport) is not KubernetesHTTP or transport.profile != REQUEST_BINDING_PROFILE
            or type(requests) is not list or type(plan) is not list or len(requests) != len(plan)
            or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30):
        raise ValueError('CACHE_TIMING_TRANSPORT_INVALID')
    endpoint = transport.endpoint.scheme + '://' + transport.endpoint.netloc + '/v1/chat/completions'
    by_case = {}
    for entry, original in zip(plan, requests):
        if type(entry) is not dict or type(entry.get('case_id')) is not str or type(original) is not dict:
            raise ValueError('CACHE_TIMING_REQUEST_INVALID')
        body = original.get('body')
        messages = body.get('messages') if type(body) is dict else None
        if (set(original) != {'method', 'url', 'body'} or original.get('method') != 'POST' or original.get('url') != endpoint
                or type(body) is not dict or set(body) != {'model', 'messages', 'max_tokens', 'temperature', 'stream'}
                or body.get('model') != transport.api_model or type(body.get('max_tokens')) is not int or body['max_tokens'] != 1
                or type(body.get('temperature')) not in (int, float) or body['temperature'] != 0 or body.get('stream') is not False
                or type(messages) is not list or not 1 <= len(messages) <= 8
                or any(type(m) is not dict or set(m) != {'role', 'content'} or m.get('role') != 'user'
                    or type(m.get('content')) is not str or not 0 < len(m['content']) <= 4096 for m in messages)):
            raise ValueError('CACHE_TIMING_REQUEST_INVALID')
        by_case[entry['case_id']] = deepcopy(original)
    request_ids = []
    def probe(case_id, case):
        request_id = 'byul-cache-timing-' + uuid.uuid4().hex
        request = {**by_case[case_id], 'id': 'before' if case == 'normal-before' else 'after', 'mode': 'json',
            'headers': {'X-Request-Id': request_id}}
        request_ids.append(sha256_hex(request_id.encode()))
        value, = transport('kubernetes-probe', [request], timeout)
        return value
    result = collect_timing_experiment(probe, plan, policy_values)
    result['request_id_sha256'] = request_ids
    result['transport'] = transport.report()
    result['data_origin'] = transport.process_observation_origin
    return result
