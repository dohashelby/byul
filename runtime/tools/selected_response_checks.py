"""A-close then B-new-request probes assembled from the existing stream collector."""
from __future__ import annotations

import hashlib
import json
import time
from uuid import uuid4
from urllib.parse import urlsplit

from guard.executors.stream_probe import collect
from tools.selected_runtime_check import HTTPConnection, UUID, concurrent_precondition, document, fact, origin, request
from tools.selected_request_checks import record_for


def generated_text(row):
    text, valid = [], True
    sources = ([row.get('body')] if row.get('mode') == 'json' else
               [e.get('data') for e in row.get('events', []) if e.get('data') != '[DONE]'])
    for raw in sources:
        try:
            value = json.loads(raw)
            for choice in value.get('choices', []):
                message = choice.get('message' if row.get('mode') == 'json' else 'delta') or {}
                piece = message.get('content') if 'content' in message else choice.get('text')
                if piece is not None:
                    if not isinstance(piece, str):
                        valid = False
                    else:
                        text.append(piece)
        except (TypeError, ValueError, AttributeError):
            valid = False
    return ''.join(text), valid


def assess_separation(alpha, beta, marker_a, marker_b, raw_a, raw_b, *, expected_scope=None):
    text_a, valid_a = generated_text(alpha)
    text_b, valid_b = generated_text(beta)
    ids = [(r.get('response_headers') or {}).get('x-request-id') for r in (alpha, beta)]
    unique = all(isinstance(v, str) and UUID.fullmatch(v) for v in ids) and ids[0] != ids[1]
    closed = alpha.get('client_closed') is True and alpha.get('stop_reason') == 'client_close' and alpha.get('terminal_seen') is not True
    normal = (beta.get('outcome') == 200 and beta.get('capture_complete') is True and valid_b and
              beta.get('utf8_valid') is not False and beta.get('post_terminal_events',0) == 0 and
              (beta.get('mode') == 'json' or beta.get('terminal_seen') is True))
    binding = all(r.get('state') == 'observed' and r.get('request_id') == rid and r.get('record_request_ids_match_query') is True and r.get('context_stable') is True for r, rid in zip((raw_a, raw_b),ids))
    required = ('principal_id','policy_sha256','target_id')
    populated = all(isinstance(r.get(k),str) and bool(r[k]) for r in (raw_a,raw_b) for k in required)
    same_context = binding and populated and all(raw_a.get(k) == raw_b.get(k) for k in (*required,'target_instance_id','run_id'))
    if expected_scope:
        same_context = same_context and all(raw_a.get(k)==value==raw_b.get(k) for k,value in expected_scope.items())
    authenticated = all(r.get('auth_state')=='authenticated' for r in (raw_a,raw_b))
    normal_alpha = (alpha.get('outcome')==200 and raw_a.get('started_decision_result')=='ALLOWED' and
                    raw_a.get('delivery_attempted') is True and authenticated)
    crossed = marker_a in text_b or marker_b in text_a
    echo_b = text_b.strip() == marker_b
    normal_journal = (raw_b.get('decision_result') == 'ALLOWED' and raw_b.get('delivery_attempted') is True and
                      raw_b.get('final_outcome') == 'completed')
    beta_failure = ((isinstance(beta.get('outcome'),int) and beta['outcome'] != 200) or
                    raw_b.get('state') == 'observed' and not normal_journal)
    alpha_failure = ((isinstance(alpha.get('outcome'),int) and alpha['outcome']!=200) or
                     raw_a.get('decision_result') in {'DENIED','INVALID'})
    # A body byte alone can be an SSE/JSON prefix. The required first generated
    # output witness is separate and must actually contain A's synthetic marker.
    passed = False if crossed or beta_failure or alpha_failure else True if normal_alpha and closed and normal and normal_journal and unique and same_context and echo_b and valid_a and marker_a in text_a else None
    return fact('REQ012-A-CLOSE-B-NEW', passed, {
        'request_ids': ids, 'distinct_server_uuids': bool(unique), 'client_label_reused': True,
        'alpha_close_observed': closed, 'alpha_closed_before_terminal': closed if alpha.get('mode') == 'sse' else None,
        'alpha_generated_output_observed': bool(text_a),
        'alpha_own_marker_observed': marker_a in text_a, 'alpha_capture_json_valid': valid_a,
        'alpha_bytes': len(text_a.encode()), 'alpha_generated_sha256': hashlib.sha256(text_a.encode()).hexdigest(),
        'beta_completed': normal, 'beta_exact_own_marker': echo_b, 'cross_marker_observed': crossed,
        'beta_generated_sha256': hashlib.sha256(text_b.encode()).hexdigest(),
        'same_trusted_context': same_context, 'required_context_populated':populated,
        'alpha_authenticated_generation_admitted':normal_alpha,'optional_context_expected':expected_scope or {},
        'alpha_journal': raw_a, 'beta_journal': raw_b,
        'timing': {label: {key: row.get(key) for key in ('dispatch_ms','headers_ms','first_byte_ms','client_close_ms','end_ms')}
                   for label, row in (('alpha', alpha), ('beta', beta))},
        'raw_generated_text_saved': False, 'engine_termination_verified': False, 'gpu_release_verified': False,
    }, scope='sequential client-close then new request with same untrusted client label; socket/result isolation only')


def buffered_close(spec, timeout):
    """One byte then close, bounded by the existing runtime watchdog.

    Buffered generation may already have completed before headers. Preserve
    that detection delay instead of asserting a cancellation inside the engine.
    """
    from serving_runtime.mvp.response_timeout import DeadlineWatchdog, set_response_timeout
    parts = urlsplit(spec['url']); start = time.monotonic(); deadline = start + timeout
    conn = HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    watch = DeadlineWatchdog(conn, deadline); response = None; watch.start()
    ms = lambda: int((time.monotonic() - start) * 1000)
    try:
        watch.connect()
        if watch.expired():raise TimeoutError('SELECTED_HTTP_DEADLINE')
        conn.request('POST', parts.path, body=json.dumps(spec['body']).encode(),
                     headers={**spec['headers'],'Content-Type':'application/json','Connection':'close'})
        response = conn.getresponse(); watch.watch_response(response); headers_ms = ms()
        sock = conn.sock or getattr(getattr(getattr(response,'fp',None),'raw',None),'_sock',None)
        set_response_timeout(sock, deadline-time.monotonic())
        raw = response.read1(1)
        if watch.expired() or watch.finish():raise TimeoutError('SELECTED_HTTP_DEADLINE')
        return {'id':'alpha','mode':'json','outcome':response.status,'capture_complete':False,
                'terminal_seen':False,'client_closed':bool(raw),'stop_reason':'client_close' if raw else 'empty_body',
                'response_headers':{k.lower():v for k,v in response.getheaders()},'body':raw.decode('utf-8',errors='replace'),
                'dispatch_ms':0,'headers_ms':headers_ms,'first_byte_ms':ms(),'client_close_ms':ms(),'end_ms':ms()}
    finally:
        watch.cancel()
        if response is not None:response.close()
        conn.close()


def separation(gateway_url, model, inference, viewer, *, timeout=15):
    base = origin(gateway_url)
    status_reply = request('GET',base+'/api/status',token=viewer,timeout=timeout)
    status = document(status_reply) if status_reply.status==200 else {}
    policy_reply = request('GET', base + '/api/policy', token=viewer, timeout=timeout)
    current = document(policy_reply).get('current', {}) if policy_reply.status == 200 else {}
    expected_scope = {'policy_sha256':current.get('sha256'),'target_id':current.get('target_id'),
                      'run_id':status.get('run_id'),
                      'target_instance_id':(((status.get('target') or {}).get('instance') or {}).get('current') or {}).get('instance_id')}
    if (status_reply.status!=200 or 'GET /api/*' not in (status.get('auth') or {}).get('protected_routes',[]) or
            not expected_scope['policy_sha256'] or not expected_scope['target_id']):
        return {'execution':'not_run','cases':[fact('REQ012-PREFLIGHT',None,{'status_http':status_reply.status,'policy_http':policy_reply.status},scope='protected viewer and selected policy/target required')]}
    limit = current.get('output_max_tokens')
    if type(limit) is not int or limit < 128:
        return {'execution':'not_run','cases':[fact('REQ012-PREFLIGHT',None,{'policy_status':policy_reply.status,'output_max_tokens':limit},scope='selected output limit must permit bounded128-token control')]}
    cases = []
    for mode, delay_ms in (('sse', 0), ('sse', 250), ('json', 0)):
        ready, capacity = concurrent_precondition(base, viewer, timeout)
        if ready is not True:
            cases.append(fact('REQ012-'+mode+'-'+str(delay_ms),None,{'dispatched':False,'precondition':capacity},scope='capacity for old and new connection'))
            continue
        markers = ['ALPHA'+uuid4().hex[:12], 'BETA'+uuid4().hex[:12]]
        label = 'same-synthetic-client-id'
        headers = {'Authorization':'Bearer '+inference,'X-Request-ID':label}
        alpha = {'id':'alpha','method':'POST','mode':mode,'url':base+'/v1/chat/completions','headers':headers,
                 'body':{'model':model,'messages':[{'role':'user','content':'Start with '+markers[0]+' then list integers from 1 to 500 separated by spaces.'}],
                         'stream':mode=='sse','max_tokens':128},
                 'read_profile':{'profile':'slow-reader-v1','chunk_bytes':256,'interval_ms':50,'close_after_reads':16}}
        # Collect A alone. B is dispatched only after A's collector confirms
        # closure/EOF, so this is a new request rather than the concurrent case.
        rows_a = collect([alpha], timeout) if mode == 'sse' else [buffered_close(alpha, timeout)]
        time.sleep(delay_ms / 1000)
        beta = {'id':'beta','method':'POST','mode':mode,'url':base+'/v1/chat/completions','headers':headers,
                'body':{'model':model,'messages':[{'role':'user','content':'Repeat exactly once with no other text: '+markers[1]}],
                        'stream':mode=='sse','max_tokens':32}}
        rows_b = collect([beta], timeout)
        if len(rows_a) != 1 or len(rows_b) != 1:
            cases.append(fact('REQ012-'+mode+'-'+str(delay_ms),None,{'reason':'collector_row_missing'},scope='capture incomplete'))
            continue
        a, b = rows_a[0], rows_b[0]
        raw = [record_for(base,(row.get('response_headers') or {}).get('x-request-id'),viewer,timeout) for row in (a,b)]
        result = assess_separation(a,b,*markers,*raw,expected_scope=expected_scope)
        result['id'] = 'REQ012-'+mode+'-'+str(delay_ms)
        result['facts'].update(delay_ms=delay_ms, alpha_read_profile=alpha['read_profile'],
                               buffered_generation_may_finish_before_close=mode=='json',
                               alpha_output_recipient='observed_own_marker' if result['facts']['alpha_own_marker_observed'] else 'unknown')
        cases.append(result)
    return {'cases':cases,'security_verdict':None,'inference_post_retries':False,'engine_termination_claim':False,'gpu_release_claim':False}
