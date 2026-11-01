"""OBS02 closed request contract; health is not engine/model readiness.

Health HTTP availability, client close and native completion are separate facts.
This module neither executes requests nor interprets enterprise status policy.
"""
import math
import re
from urllib.parse import urlsplit
from guard.models import canonical_json

PROFILE='kubernetes-owned-observation-state-http-v1'
PRODUCT_REGISTERED=True
SLOTS={
 'obs02-health-before':'health-before',
 'obs02-normal-before':'normal-before',
 'obs02-disconnect':'disconnect',
 'obs02-normal-after':'normal-after',
 'obs02-health-after':'health-after',
}


def validate_request(request,*,api_model,engine_url,timeout):
    try:
        if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10
                or type(request) is not dict or set(request)-{'id','mode','method','url','headers','body','want_body','read_profile'}
                or request.get('mode','json') not in {'json','sse'} or request.get('want_body',True) is not True
                or type(request.get('id')) is not str or request['id'] not in SLOTS
                or type(engine_url) is not str):raise ValueError
        endpoint=urlsplit(engine_url)
        if (endpoint.scheme!='http' or not endpoint.hostname or not endpoint.port or endpoint.username is not None
                or endpoint.password is not None or endpoint.path or endpoint.query or endpoint.fragment):raise ValueError
        slot=SLOTS[request['id']];headers=request.get('headers')
        if type(headers) is not dict or set(headers)!={'X-Request-Id'} or type(headers['X-Request-Id']) is not str:raise ValueError
        token=re.fullmatch(r'byul-obs02-([0-9a-f]{32})-'+slot,headers['X-Request-Id'])
        if token is None:raise ValueError
        health=slot.startswith('health-');close=slot=='disconnect'
        if (request.get('method')!=('GET' if health else 'POST')
                or request.get('url')!=engine_url+('/health' if health else '/v1/completions')
                or request.get('mode','json')!=('sse' if close else 'json')):raise ValueError
        body=None if health else {'model':api_model,'prompt':'hello','max_tokens':8 if close else 1,'temperature':0,'stream':close}
        if canonical_json(request.get('body'))!=canonical_json(body):raise ValueError
        expected_reader={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
        if close:
            if canonical_json(request.get('read_profile'))!=canonical_json(expected_reader):raise ValueError
        elif 'read_profile' in request:raise ValueError
        return slot,token[1]
    except (TypeError,KeyError,ValueError,AttributeError):
        raise ValueError('OBSERVATION_STATE_HTTP_INVALID') from None
