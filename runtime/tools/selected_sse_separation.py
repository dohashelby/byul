#!/usr/bin/env python3
"""One bounded SSE A-close/B-new acceptance using existing collector/verdict."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from guard.executors.stream_probe import collect
from tools.selected_response_checks import assess_separation
from tools.selected_request_checks import record_for
from tools.selected_runtime_check import concurrent_precondition, document, fact, origin, request


def scope(status):
    return {'policy_sha256': (status.get('policy') or {}).get('sha256'),
            'target_id': (status.get('policy') or {}).get('target_id'),
            'run_id': status.get('run_id'),
            'target_instance_id': (((status.get('target') or {}).get('instance') or {}).get('current') or {}).get('instance_id')}


def run(gateway, model, inference, viewer, *, timeout=15, delay_ms=0, chunk_bytes=128, close_after_reads=32, interval_ms=1, close_mode='semantic'):
    if type(timeout) not in (int, float) or not 0 < timeout <= 30 or delay_ms not in (0, 250):
        raise ValueError('bounded timing required')
    if type(chunk_bytes) is not int or not 32 <= chunk_bytes <= 512 or type(close_after_reads) is not int or not 1 <= close_after_reads <= 32:
        raise ValueError('bounded read profile required')
    if interval_ms == 0:
        return {'execution': 'not_run', 'cases': [fact('REQ012-SSE-BOUNDED', None,
                {'reason': 'existing_slow_reader_profile_requires_positive_interval_ms'}, scope='unsupported profile; no POST')], 'security_verdict': None}
    if type(interval_ms) is not int or not 1 <= interval_ms <= 10:
        raise ValueError('positive supported bounded interval required')
    if close_mode not in ('semantic', 'reads'):
        raise ValueError('declared close mode required')
    base = origin(gateway)
    sr = request('GET', base + '/api/status', token=viewer, timeout=timeout)
    pr = request('GET', base + '/api/policy', token=viewer, timeout=timeout)
    status = document(sr) if sr.status == 200 else {}
    current = (document(pr).get('current') or {}) if pr.status == 200 else {}
    expected = scope(status)
    if (sr.status != 200 or pr.status != 200 or not inference or not viewer or
            'GET /api/*' not in (status.get('auth') or {}).get('protected_routes', []) or
            any(not isinstance(v, str) or not v for v in expected.values()) or
            current.get('sha256') != expected['policy_sha256'] or current.get('target_id') != expected['target_id'] or
            type(current.get('output_max_tokens')) is not int or current['output_max_tokens'] < 128):
        return {'execution': 'not_run', 'cases': [fact('REQ012-SSE-BOUNDED', None,
                {'reason': 'protected_scope_or_output_limit_unconfirmed'}, scope='no POST before exact protected scope')], 'security_verdict': None}
    ready, capacity = concurrent_precondition(base, viewer, timeout)
    if ready is not True:
        return {'execution': 'not_run', 'cases': [fact('REQ012-SSE-BOUNDED', None,
                {'precondition': capacity}, scope='existing two-slot precondition')], 'security_verdict': None}
    marker_a, marker_b = 'ALPHA' + uuid4().hex[:12], 'BETA' + uuid4().hex[:12]
    headers = {'Authorization': 'Bearer ' + inference, 'X-Request-ID': 'same-synthetic-client-id'}
    profile = {'profile': 'generated-marker-close-v1' if close_mode == 'semantic' else 'slow-reader-v1', 'chunk_bytes': chunk_bytes,
               'interval_ms': interval_ms, 'close_after_reads': close_after_reads}
    if close_mode == 'semantic':
        profile['marker'] = marker_a
    alpha = {'id': 'alpha', 'mode': 'sse', 'method': 'POST', 'url': base + '/v1/chat/completions', 'headers': headers,
             'body': {'model': model, 'messages': [{'role': 'user', 'content':
                      'Repeat this exact marker 12 times separated by one space, with no other text: ' + marker_a}],
                      'stream': True, 'max_tokens': 128}, 'read_profile': profile}
    beta = {'id': 'beta', 'mode': 'sse', 'method': 'POST', 'url': base + '/v1/chat/completions', 'headers': headers,
            'body': {'model': model, 'messages': [{'role': 'user', 'content': 'Repeat exactly once with no other text: ' + marker_b}],
                     'stream': True, 'max_tokens': 32}}
    # No calibration retries or prompt/profile selection based on observed output.
    rows_a = collect([alpha], timeout)
    time.sleep(delay_ms / 1000)
    rows_b = collect([beta], timeout)
    if len(rows_a) != 1 or len(rows_b) != 1:
        return {'execution': 'incomplete', 'cases': [fact('REQ012-SSE-BOUNDED', None,
                {'reason': 'collector_row_missing'}, scope='original capture boundary')], 'security_verdict': None}
    a, b = rows_a[0], rows_b[0]
    raw = [record_for(base, (row.get('response_headers') or {}).get('x-request-id'), viewer, timeout) for row in (a, b)]
    result = assess_separation(a, b, marker_a, marker_b, *raw, expected_scope=expected)
    result['id'] = 'REQ012-SSE-BOUNDED'
    result['facts'].update(alpha_read_profile=profile, alpha_marker_repetitions_requested=12,
                           delay_ms=delay_ms, profile_fixed_before_dispatch=True,
                           close_trigger='complete generated marker before terminal, capped by fixed reads' if close_mode == 'semantic' else 'fixed read count',
                           semantic_marker_close_observed=a.get('marker_close_observed'),
                           semantic_marker_json_valid=a.get('marker_json_valid'))
    end = request('GET', base + '/api/status', token=viewer, timeout=timeout)
    stable = end.status == 200 and scope(document(end)) == expected
    cases = [result, fact('REQ012-SSE-SCOPE', stable if end.status == 200 else None,
                          {'scope_before': expected, 'scope_after': scope(document(end)) if end.status == 200 else None},
                          scope='same Gateway scope before and after both requests')]
    return {'execution': 'executed', 'cases': cases, 'security_verdict': None, 'inference_post_retries': False,
            'buffered_control': 'reuse separately recorded existing ownership result',
            'engine_termination_claim': False, 'gpu_release_claim': False, 'raw_generated_text_saved': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for key in ('gateway', 'model', 'inference-env', 'viewer-env', 'out'): p.add_argument('--' + key, required=True)
    p.add_argument('--timeout', type=float, default=15)
    p.add_argument('--delay-ms', type=int, choices=(0, 250), default=0)
    p.add_argument('--chunk-bytes', type=int, default=128)
    p.add_argument('--close-after-reads', type=int, default=32)
    p.add_argument('--interval-ms', type=int, default=1)
    p.add_argument('--close-mode', choices=('semantic', 'reads'), default='semantic')
    a = p.parse_args()
    out = Path(a.out).absolute(); out.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        result = run(a.gateway, a.model, os.environ[a.inference_env], os.environ[a.viewer_env],
                     timeout=a.timeout, delay_ms=a.delay_ms, chunk_bytes=a.chunk_bytes,
                     close_after_reads=a.close_after_reads, interval_ms=a.interval_ms, close_mode=a.close_mode)
        raw = (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
        fd = os.open(out / 'helper.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f: f.write(raw)
        print(json.dumps({'execution': result['execution'], 'cases': [{'id': c['id'], 'result': c['result']} for c in result['cases']]}))
        return 0
    except Exception as error:
        print(json.dumps({'execution': 'incomplete', 'error_type': type(error).__name__})); return 2


if __name__ == '__main__':
    raise SystemExit(main())
