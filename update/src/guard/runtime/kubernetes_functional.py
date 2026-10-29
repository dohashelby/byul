"""One existing CheckDef completion control over the pinned JSON collector.

The logical ID contains a space; the source protocol forbids spaces. Map this
one exact ID to a fixed wire ID and restore it after exact correlation. The
route, body, model and source are never substituted. Bodies remain transient.
"""
from copy import deepcopy
import json
import math
from urllib.parse import urlsplit

from guard.models import sha256_hex, utcnow
from .kubernetes_http import KubernetesBindingError, REQUEST_BINDING_PROFILE

PROFILE='kubernetes-owned-functional-control-http-v1'
LOGICAL_ID='func|POST /v1/completions'
WIRE_ID='functional-control'


def capture(transport,network,requests,timeout):
    row={'id':LOGICAL_ID,'profile':PROFILE,'binding_verified_before_after':False,
         'capture_complete':False,'started_at':utcnow().isoformat()}
    try:
        with transport.lock:
            if transport.failed:raise KubernetesBindingError()
        if (transport.profile!=REQUEST_BINDING_PROFILE or network!='kubernetes-probe'
                or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=30
                or type(requests) is not list or len(requests)!=1):
            raise KubernetesBindingError()
        request=requests[0]
        if (type(request) is not dict or set(request)!={'id','method','url','headers','body','want_body'}
                or request['id']!=LOGICAL_ID or request['method']!='POST'
                or type(request['headers']) is not dict or request['headers']
                or request['want_body'] is not True or type(request['url']) is not str):
            raise KubernetesBindingError()
        url=urlsplit(request['url']);body=request['body']
        if (url.scheme!=transport.endpoint.scheme or url.netloc!=transport.endpoint.netloc
                or url.path!='/v1/completions' or url.query or url.fragment
                or type(body) is not dict or set(body)!={'model','prompt','max_tokens'}
                or body['model']!=transport.api_model or body['prompt']!='ping'
                or type(body['max_tokens']) is not int or body['max_tokens']!=1):
            raise KubernetesBindingError()
        prepared={'id':WIRE_ID,'method':'POST','url':request['url'],'mode':'json',
                  'headers':{},'body':deepcopy(body)}
        transport._verify()
        remaining=min(float(timeout),transport.deadline-transport.clock())
        if remaining<=0:raise KubernetesBindingError()
        payload=json.dumps({'requests':[prepared],'timeout':remaining},ensure_ascii=False,allow_nan=False).encode('utf-8')
        if len(payload)>128*1024:raise KubernetesBindingError()
        probe=transport.binding['probe']
        raw=transport._call([f"--namespace={probe['namespace']}",'exec','-i',probe['pod'],
            f"--container={probe['container']}",'--','python3','-c',transport.source,str(remaining)],
            payload=payload,cap=2*1024**2,budget=remaining)
        transport._verify()
        if (type(raw) is not list or len(raw)!=1 or type(raw[0]) is not dict
                or raw[0].get('id')!=WIRE_ID or raw[0].get('method')!='POST' or raw[0].get('mode')!='json'):
            raise KubernetesBindingError()
        value=raw[0];outcome=value.get('outcome')
        if not (type(outcome) is int and 100<=outcome<=599 or type(outcome) is str
                and outcome in {'refused','timeout','unreachable','dns_error','error','probe_error'}):
            raise KubernetesBindingError()
        text=value.get('body')
        complete=(value.get('capture_complete') is True and value.get('eof') is True
            and value.get('body_truncated') is False and value.get('utf8_valid') is True
            and value.get('stop_reason')=='eof' and type(text) is str
            and len(text.encode('utf-8'))<=128*1024 and type(value.get('body_bytes')) is int
            and value['body_bytes']==len(text.encode('utf-8'))
            and value.get('body_sha256')==sha256_hex(text.encode('utf-8')))
        # A partial 200 cannot pass the existing structural validator. Retain
        # its status but withhold its body; no content enters report/log rows.
        safe={'id':LOGICAL_ID,'method':'POST','outcome':outcome,'capture_complete':complete,
              'body':text if complete else None,'body_bytes':value['body_bytes'] if complete else 0}
        if type(value.get('elapsed_ms')) is int and value['elapsed_ms']>=0:
            safe['elapsed_ms']=value['elapsed_ms']
        row.update(binding_verified_before_after=True,capture_complete=complete,outcome=outcome,
            wire_id=WIRE_ID,probe_source_sha256=transport.source_sha256)
        return [safe]
    except Exception:
        with transport.lock:transport.failed=True
        row['diagnostic']='KUBERNETES_FUNCTIONAL_CONTROL_UNAVAILABLE'
        raise KubernetesBindingError() from None
    finally:
        row['finished_at']=utcnow().isoformat()
        with transport.lock:transport.rows.append(row)
