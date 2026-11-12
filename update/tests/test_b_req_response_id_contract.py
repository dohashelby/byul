"""Exact default producer→consumer contract; all transport is explicitly CPU."""
from copy import deepcopy
import json
import re
import threading
from types import SimpleNamespace
import uuid

import pytest
from guard.adapters.vllm import VllmAdapter
from guard.executors import req_runner_capture as link
from guard.models import sha256_hex
from tests.req_response_id_source import DATA, response_id

PROFILE = VllmAdapter().runner_capture_profile('0.31.0')


def capture(cid):
    value = object.__new__(link.Capture)
    value.ctx = SimpleNamespace(check=SimpleNamespace(id=cid))
    value.lock, value.calls = threading.Lock(), []
    value.profile = PROFILE
    value.fingerprinter = PROFILE.fingerprinter(b'EXAMPLE-response-contract-key-01!')
    return value


def request(cid):
    body = {'max_tokens': 1, 'n': 1}
    body.update(messages=[{'role': 'user', 'content': 'CPU source contract'}]) if cid == 'REQ-08' else body.update(prompt='CPU source contract')
    return {'method':'POST','url':'http://example.invalid/v1/' + ('chat/completions' if cid=='REQ-08' else 'completions'),
            'headers':{},'body':body}


@pytest.mark.parametrize('cid', ['REQ-08', 'REQ-09', 'REQ-10'])
def test_exact_upstream_default_generator_to_unchanged_native_hmac_mapping(cid):
    rid = response_id(cid == 'REQ-08')
    assert len(rid.rsplit('-', 1)[1]) == 16
    value, original = capture(cid), request(cid)
    before = deepcopy(original)
    reply = {'outcome':200,'body':json.dumps({'id':rid})}
    called = []
    def dispatch():
        called.append(original)
        return reply
    label = 'sampling-before' if cid == 'REQ-10' else 'before'
    assert value.call(label, original, dispatch) is reply
    assert called == [original] and called[0] is original and original == before
    row, = value.calls
    assert row['response_id_sha256'] == sha256_hex(rid.encode())
    assert row['request_sha256'] == value.fingerprinter.fingerprint(rid if cid=='REQ-08' else rid+'-0')
    assert row['diagnostic'] is None
    assert DATA['source_commit'] == PROFILE.source_commit and len(PROFILE.sources) == 7


@pytest.mark.parametrize('bad', ['old32','short15','long17','uppercase','wrong-prefix','suffix','nonhex'])
def test_non_default_id_shapes_preserve_dispatch_and_unknown_diagnostic(bad):
    rid = response_id(True)
    base = rid.split('-', 1)[1]
    invalid = {'old32':'chatcmpl-'+uuid.uuid4().hex, 'short15':'chatcmpl-'+base[:15],
        'long17':rid+'a','uppercase':'CHATCMPL-'+base,'wrong-prefix':'cmpl-'+base,
        'suffix':rid+'-0','nonhex':'chatcmpl-'+base[:-1]+'g'}[bad]
    value, original = capture('REQ-08'), request('REQ-08')
    reply = {'outcome':200,'body':json.dumps({'id':invalid})}
    called = []
    def dispatch():
        called.append(True)
        return reply
    assert value.call('before',original,dispatch) is reply and called == [True]
    row, = value.calls
    assert row['request_sha256'] is None and row['response_id_sha256'] is None
    assert row['diagnostic'] == 'B_REQ_RESPONSE_ID_UNAVAILABLE'


@pytest.mark.parametrize('override', ['header','body','n2'])
def test_default_generator_scope_still_rejects_override_before_dispatch(override):
    value, original = capture('REQ-08'), request('REQ-08')
    if override=='header': original['headers']['X-Request-Id']=uuid.uuid4().hex
    if override=='body': original['body']['request_id']=uuid.uuid4().hex
    if override=='n2': original['body']['n']=2
    with pytest.raises(ValueError,match='B_REQ_NATIVE_REQUEST_OUTSIDE_SELECTED_SCOPE'):
        value.call('before',original,lambda:pytest.fail('must reject before native dispatch'))
    assert value.calls == []


@pytest.mark.parametrize('rid', ['chatcmpl-a950387d06b5f78c','cmpl-a4a134d191b5d06f'])
def test_preserved_v12_actual_http_id_shape_matches_pinned_default_source(rid):
    # Only public-format strings from the actual normal receipt; no worker,
    # authentication event, token, prompt or new live observation is created.
    assert re.fullmatch(PROFILE.response_id_pattern, rid)
