"""Successful response metadata boundaries across buffered and split SSE traffic."""
from dataclasses import replace
import json
import time
from unittest.mock import patch

import pytest

from serving_runtime.mvp.auth import CHAT_ROUTE, COMPLETIONS_ROUTE
from serving_runtime.mvp.backend import BackendConfig
from serving_runtime.mvp.policy import load_policy
from serving_runtime.mvp.relay import Relay
from serving_runtime.mvp.validator import validate_request
from test_mvp_request_boundaries import BackendResponse, BackendConnection, RequestIO, POLICY, body


def example(route, *, stream=False):
    choice = ({'delta' if stream else 'message': {'content': 'private_debug stays in user text',
                'reasoning': 'normal reasoning'}} if route == CHAT_ROUTE else {'text': 'normal output'})
    return {'id': 'response-1', 'object': 'text_completion', 'choices': [{**choice,
            'prompt_token_ids': [999], 'logprobs': {'secret': 123}}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
            'vendor_extension': {'normal': True}, 'private_debug': {'secret': 456},
            'prompt_token_ids': [888], 'prompt_logprobs': [{'secret': 789}]}


def setup(route, document=None):
    request = json.loads(body()) if document is None else document
    allowed = validate_request(body(), 'application/json', load_policy(POLICY))
    if route == COMPLETIONS_ROUTE:
        request = {**request, 'prompt': 'hi'}
        request.pop('messages', None)
    return request, allowed


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
def test_buffered_success_hides_only_unrequested_protocol_metadata(route):
    value = example(route)
    document, allowed = setup(route)
    upstream = BackendConnection(BackendResponse([json.dumps(value).encode()]))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, result = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay('a' * 32, document, allowed, route=route)
    assert decision.result == 'ALLOWED'
    safe = json.loads(result)
    assert 'private_debug' not in safe and 'prompt_token_ids' not in safe and 'prompt_logprobs' not in safe
    assert 'logprobs' not in safe['choices'][0] and 'prompt_token_ids' not in safe['choices'][0]
    assert safe['usage'] == value['usage'] and safe['vendor_extension'] == value['vendor_extension']
    assert safe['choices'][0].get('message', safe['choices'][0].get('text')) == value['choices'][0].get('message', value['choices'][0].get('text'))


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'])
def test_split_sse_sanitizes_before_any_private_event_bytes_are_delivered(route, bom):
    value = example(route, stream=True)
    document, allowed = setup(route)
    event = b'id: preserved\r\nevent: message\r\n: heartbeat\r\ndata: ' + json.dumps(value).encode() + b'\r\n\r\n'
    wire = bom + event + b'data: [DONE]\r\n\r\n'
    upstream = BackendConnection(BackendResponse([bytes([byte]) for byte in wire], stream=True))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, outcome, started = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay_stream(
            client, 'a' * 32, document, allowed, time.monotonic(), lambda: None, route=route)
    assert (decision.result, outcome, started) == ('ALLOWED', 'completed', True)
    saved = b''.join(client.chunks)
    assert b'id: preserved\r\nevent: message\r\n: heartbeat\r\n' in saved
    assert b'data: [DONE]\r\n\r\n' in saved
    payload = next(line[5:].strip() for line in saved.splitlines() if line.startswith(b'data:') and b'[DONE]' not in line)
    safe = json.loads(payload)
    assert 'private_debug' not in safe and 'prompt_token_ids' not in safe and 'prompt_logprobs' not in safe
    assert safe['usage'] == value['usage'] and safe['vendor_extension'] == value['vendor_extension']
    assert b'999' not in saved and b'888' not in saved and b'789' not in saved


def test_invalid_sse_json_cannot_leak_a_partial_private_metadata_event():
    document, allowed = setup(CHAT_ROUTE)
    upstream = BackendConnection(BackendResponse([b'data: {"private_debug":"SENSITIVE",\n\n'], stream=True))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, outcome, started = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay_stream(
            client, 'a' * 32, document, allowed, time.monotonic(), lambda: None)
    assert (decision.reason_code, outcome, started) == ('ENGINE_BAD_RESPONSE', 'invalid_chat_stream', True)
    assert client.chunks == [] and upstream.closed


def test_explicit_approved_zero_prompt_logprobs_option_remains_requested():
    value = example(CHAT_ROUTE)
    document, allowed = setup(CHAT_ROUTE)
    document['prompt_logprobs'] = 0
    allowed = replace(allowed, extra_fields=(('prompt_logprobs', 'passthrough'),))
    upstream = BackendConnection(BackendResponse([json.dumps(value).encode()]))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, result = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay('a' * 32, document, allowed)
    assert json.loads(result)['prompt_logprobs'] == value['prompt_logprobs']
    assert 'private_debug' not in json.loads(result)


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize('stream', [False, True])
def test_uninspectable_metadata_depth_uses_safe_response_failure(route, stream):
    value = example(route, stream=stream)
    nested = {'private_debug': 'DEPTH-SECRET'}
    for _ in range(70):
        nested = {'nested': nested}
    value['vendor_extension'] = nested
    raw = json.dumps(value).encode()
    document, allowed = setup(route)
    upstream = BackendConnection(BackendResponse([b'data: ' + raw + b'\n\n' if stream else raw], stream=stream))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        relay = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1)
        if stream:
            decision, outcome, started = relay.relay_stream(client, 'a' * 32, document, allowed, time.monotonic(), lambda: None, route=route)
            assert outcome == ('invalid_chat_stream' if route == CHAT_ROUTE else 'invalid_completion_stream')
            assert client.chunks == []
        else:
            decision, result = relay.relay('a' * 32, document, allowed, route=route)
            assert result is None and decision.http_status == 502
    assert decision.reason_code == 'ENGINE_BAD_RESPONSE'
    assert 'DEPTH-SECRET' not in decision.message and upstream.closed


def test_nonobject_success_body_cannot_bypass_private_metadata_filter():
    document, allowed = setup(CHAT_ROUTE)
    upstream = BackendConnection(BackendResponse([b'[{"private_debug":"ARRAY-SECRET"}]']))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, result = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay('a' * 32, document, allowed)
    assert result is None and decision.reason_code == 'ENGINE_BAD_RESPONSE'
    assert 'ARRAY-SECRET' not in decision.message


@pytest.mark.parametrize('stream', [False, True])
@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
def test_reserialized_metadata_obeys_existing_output_byte_budget(route, stream):
    # Compact exponent input grows when the JSON library serializes its float.
    raw = (b'{"object":"text_completion","choices":[{"text":"ok"}],"usage":'
           b'{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2},"private_debug":0,"vendor":['
           + b'1e10,' * 100 + b'1e10]}')
    wire = b'data: ' + raw + b'\n\ndata: [DONE]\n\n'
    document, allowed = setup(route)
    upstream = BackendConnection(BackendResponse([wire if stream else raw], stream=stream))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')), \
         patch('serving_runtime.mvp.relay._COMPLETION_BODY_LIMIT', len(raw)), \
         patch('serving_runtime.mvp.relay._COMPLETION_STREAM_BODY_LIMIT', len(wire)), \
         patch('serving_runtime.mvp.relay._CHAT_SSE_LINE_LIMIT', len(raw) + 6), \
         patch('serving_runtime.mvp.relay._CHAT_SSE_EVENT_LIMIT', len(raw) + 8):
        relay = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1)
        if stream:
            decision, _outcome, _started = relay.relay_stream(client, 'a' * 32, document, allowed, time.monotonic(), lambda: None, route=route)
            assert client.chunks == []
        else:
            decision, result = relay.relay('a' * 32, document, allowed, route=route)
            assert result is None
    assert decision.reason_code == 'ENGINE_BAD_RESPONSE'


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize('stream', [False, True])
def test_duplicate_envelope_keys_never_preserve_hidden_raw_metadata(route, stream):
    raw = (b'{"object":"text_completion","choices":[{"text":"ok"}],"usage":'
           b'{"prompt_tokens":1,"completion_tokens":1,"total_tokens":2},'
           b'"vendor":{"private_debug":"DUP-SECRET"},"vendor":{"normal":true}}')
    document, allowed = setup(route)
    upstream = BackendConnection(BackendResponse([b'data: ' + raw + b'\n\n' if stream else raw], stream=stream))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        relay = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1)
        if stream:
            decision, _outcome, _started = relay.relay_stream(client, 'a' * 32, document, allowed, time.monotonic(), lambda: None, route=route)
            assert client.chunks == []
        else:
            decision, result = relay.relay('a' * 32, document, allowed, route=route)
            assert result is None
    assert decision.reason_code == 'ENGINE_BAD_RESPONSE'
    assert 'DUP-SECRET' not in decision.message and upstream.closed


@pytest.mark.parametrize('route', [CHAT_ROUTE, COMPLETIONS_ROUTE])
@pytest.mark.parametrize('position', ['later_event', 'double_initial'])
def test_bom_outside_initial_utf8_marker_is_safely_rejected(route, position):
    value = example(route, stream=True)
    first = b'data: ' + json.dumps(value).encode() + b'\n\n'
    private = b'\xef\xbb\xbfdata: {"private_debug":"LATE-BOM-SECRET"}\n\n'
    wire = first + private if position == 'later_event' else b'\xef\xbb\xbf' + private
    document, allowed = setup(route)
    upstream = BackendConnection(BackendResponse([wire], stream=True))
    client = RequestIO(body(stream=True))
    with patch.object(BackendConfig, 'connection', return_value=(upstream, '/engine')):
        decision, outcome, started = Relay(BackendConfig.mock(('127.0.0.1', 1)), 1, 1, 1).relay_stream(
            client, 'a' * 32, document, allowed, time.monotonic(), lambda: None, route=route)
    assert decision.reason_code == 'ENGINE_BAD_RESPONSE'
    assert b'LATE-BOM-SECRET' not in b''.join(client.chunks)


@pytest.mark.parametrize('route',[CHAT_ROUTE,COMPLETIONS_ROUTE])
@pytest.mark.parametrize('position',['first','after_normal','after_done'])
@pytest.mark.parametrize('split',[False,True])
def test_upstream_sse_error_envelope_never_becomes_completed_or_public_detail(route,position,split):
    document,allowed=setup(route)
    normal=b'data: '+json.dumps(example(route,stream=True)).encode()+b'\n\n'
    failure=b'data: {"error":{"message":"SYNTHETIC-UPSTREAM-SECRET","type":"EngineDeadError"}}\n\n'
    done=b'data: [DONE]\n\n'
    wire=(failure+done if position=='first' else normal+failure+done if position=='after_normal' else normal+done+failure)
    chunks=[bytes([x])for x in wire]if split else[wire]
    upstream=BackendConnection(BackendResponse(chunks,stream=True));client=RequestIO(body(stream=True))
    with patch.object(BackendConfig,'connection',return_value=(upstream,'/engine')):
        decision,outcome,started=Relay(BackendConfig.mock(('127.0.0.1',1)),1,1,1).relay_stream(client,'a'*32,document,allowed,time.monotonic(),lambda:None,route=route)
    assert decision.result=='ERROR' and decision.reason_code=='ENGINE_BAD_RESPONSE' and decision.source=='engine'
    assert outcome!='completed' and upstream.closed
    assert b'SYNTHETIC-UPSTREAM-SECRET'not in b''.join(client.chunks)
    assert 'SYNTHETIC-UPSTREAM-SECRET'not in decision.message


def test_upstream_sse_error_is_final_error_record_and_releases_slot(tmp_path,caplog):
    from types import SimpleNamespace
    from serving_runtime.mvp.request_records import RequestRecords
    from serving_runtime.mvp.request_flow import RequestFlow,ConcurrencyLimiter
    from serving_runtime.mvp.policy_state import load_revocations
    policy=load_policy(POLICY);backend=BackendConfig.mock(('127.0.0.1',1))
    wire=b'data: {"error":{"message":"SYNTHETIC-UPSTREAM-SECRET"}}\n\ndata: [DONE]\n\n'
    upstream=BackendConnection(BackendResponse([wire],stream=True));client=RequestIO(body(stream=True))
    engine=SimpleNamespace(receipt_not_queried=lambda:'not_queried',backend_receipt=lambda _: 'not_queried',mock_cancel_evidence=lambda _,**kw:'not_observed')
    limiter=ConcurrencyLimiter(1);records=RequestRecords(tmp_path,'session',lambda:policy,'mock')
    with patch.object(BackendConfig,'connection',return_value=(upstream,'/v1/chat/completions')):
        RequestFlow(backend,lambda:policy,lambda:load_revocations(None),records,engine,Relay(backend,1,1,1),limiter).chat(client,'a'*32)
    rows,corrupt=records.store.read_all();final=rows[-1]
    assert not corrupt and final['phase']=='final' and final['decision']['result']=='ERROR'
    assert final['decision']['reason_code']=='ENGINE_BAD_RESPONSE' and final['final_outcome']=='invalid_chat_stream'
    assert final['response_started']is True and final['http_start_status']==200
    assert limiter.status()['in_flight']==0 and upstream.closed
    assert 'SYNTHETIC-UPSTREAM-SECRET'not in json.dumps(rows)+caplog.text
    assert b'SYNTHETIC-UPSTREAM-SECRET'not in b''.join(client.chunks)


@pytest.mark.parametrize('value',[None,'SYNTHETIC-UPSTREAM-SECRET',[],{'code':500}])
def test_top_level_sse_error_remains_failed_even_with_valid_choices(value):
    document,allowed=setup(CHAT_ROUTE);payload=example(CHAT_ROUTE,stream=True);payload['error']=value
    wire=b'data: '+json.dumps(payload).encode()+b'\n\ndata: [DONE]\n\n'
    upstream=BackendConnection(BackendResponse([wire],stream=True));client=RequestIO(body(stream=True))
    with patch.object(BackendConfig,'connection',return_value=(upstream,'/engine')):
        decision,outcome,_=Relay(BackendConfig.mock(('127.0.0.1',1)),1,1,1).relay_stream(client,'a'*32,document,allowed,time.monotonic(),lambda:None)
    assert decision.result=='ERROR' and outcome=='invalid_chat_stream' and client.chunks==[]


def test_error_text_in_normal_request_and_generated_content_is_not_an_error_envelope():
    request=json.loads(body());request['messages'][0]['content']='Explain the word error, including {"error": "example"}.'
    document,allowed=setup(CHAT_ROUTE,request);payload=example(CHAT_ROUTE,stream=True)
    payload['choices'][0]['delta']['content']='The user text contains {"error":"example"}.'
    wire=b'data: '+json.dumps(payload).encode()+b'\n\ndata: [DONE]\n\n'
    upstream=BackendConnection(BackendResponse([wire],stream=True));client=RequestIO(body(stream=True))
    with patch.object(BackendConfig,'connection',return_value=(upstream,'/engine')):
        decision,outcome,_=Relay(BackendConfig.mock(('127.0.0.1',1)),1,1,1).relay_stream(client,'a'*32,document,allowed,time.monotonic(),lambda:None)
    assert decision.result=='ALLOWED' and outcome=='completed'
    assert b'[DONE]' in b''.join(client.chunks)
