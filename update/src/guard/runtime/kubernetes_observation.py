"""OBS01's closed public HTTP request contract; no other check IDs admitted."""
import math
import re
from urllib.parse import urlsplit
from guard.models import canonical_json

PROFILE='kubernetes-owned-required-event-http-v1'
SLOTS={'obs01-before':'before','obs01-denied':'denied','obs01-after':'after'}


def validate_request(request,*,api_model,timeout):
    if (type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10
            or type(request) is not dict or set(request)-{'id','mode','method','url','headers','body','want_body'}
            or request.get('mode','json')!='json' or request.get('method')!='POST' or request.get('want_body',True) is not True
            or type(request.get('id')) is not str or request['id'] not in SLOTS):raise ValueError('OBSERVATION_HTTP_INVALID')
    slot=SLOTS[request['id']];headers=request.get('headers')
    if (type(headers) is not dict or set(headers)!={'X-Request-Id','Authorization'}
            or type(headers['X-Request-Id']) is not str or type(headers['Authorization']) is not str
            or re.fullmatch(r'Bearer [A-Za-z0-9._~+/-]{1,4096}={0,2}',headers['Authorization']) is None):
        raise ValueError('OBSERVATION_HTTP_INVALID')
    token=re.fullmatch(r'byul-obs01-([0-9a-f]{32})-'+slot,headers['X-Request-Id'])
    body={'model':api_model,'messages':[{'role':'user','content':'hello'}],'max_tokens':1,'stream':False}
    if (token is None or type(request.get('body')) is not dict or canonical_json(request['body'])!=canonical_json(body)
            or urlsplit(request.get('url','')).path!='/v1/chat/completions'):
        raise ValueError('OBSERVATION_HTTP_INVALID')
    return slot,token[1],headers['Authorization']
