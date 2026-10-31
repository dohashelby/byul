"""Closed fresh-connection probe design; no issuer/credential mutation."""
from copy import deepcopy
import re,threading
from guard.models import canonical_json,sha256_hex
from .bounded import strict_json
from .kubernetes_http import KubernetesHTTP,KubernetesBindingError,REQUEST_BINDING_PROFILE
from guard.executors.credential_revocation_contract import SLOTS

PROFILE='kubernetes-owned-credential-revocation-http-v1'


class KubernetesCredentialRevocationHTTP:
    profile=PROFILE
    def __init__(self,target,env,keys,*,runner=None,duration=120,binary='kubectl'):
        if target.get('lab') is not True or env.engine.name!='vllm' or env.engine.version!='0.31.0':raise KubernetesBindingError()
        if (type(keys) is not dict or set(keys)!={'retired','control'} or any(type(v) is not str or not 0<len(v)<=8192 or any(ord(c)<33 or ord(c)>126 for c in v) for v in keys.values())
                or len(set(keys.values()))!=2):raise KubernetesBindingError()
        self._keys=deepcopy(keys)
        self.base=KubernetesHTTP(target,env,runner=runner,duration=duration,binary=binary,profile=REQUEST_BINDING_PROFILE)
        self.binding=deepcopy(self.base.binding);self.api_model=self.base.api_model;self.model_mapping=deepcopy(self.base.model_mapping)
        self.target_sha256=sha256_hex(canonical_json(target));self.engine_url=target['engine']['trusted_url']
        self.rows=[];self.failed=False;self._last=-1;self._token=None;self._lock=threading.Lock()

    def __call__(self,*args,**kwargs):raise KubernetesBindingError()

    def perform(self,slot,request_id):
        selected=None
        with self._lock:
            try:
                if self.failed or type(slot) is not str or slot not in SLOTS or type(request_id) is not str:raise ValueError
                selected=SLOTS.index(slot)
                if selected<=self._last or self._last<0 and selected!=0:raise ValueError
                match=re.fullmatch('byul-plt10-([0-9a-f]{32})-'+slot,request_id)
                if match is None or self._token is not None and self._token!=match[1]:raise ValueError
                self._last=selected;self._token=match[1];role='retired' if slot.startswith('retired-') else 'control'
                request={'id':'plt10-'+slot,'method':'GET','mode':'json','url':self.engine_url+'/v1/models',
                    'headers':{'X-Request-Id':request_id,'Authorization':'Bearer '+self._keys[role]}}
                self.base._verify(capture_snapshot=True);budget=min(10,self.base.deadline-self.base.clock())
                if budget<=0:raise ValueError
                probe=self.binding['probe'];packet=self.base._call(['--namespace='+probe['namespace'],'exec','-i',probe['pod'],'--container='+probe['container'],
                    '--','python3','-c',self.base.source,str(budget)],payload=canonical_json({'requests':[request],'timeout':budget}),cap=2*1024**2,budget=budget)
                self.base._verify(capture_snapshot=True)
                if type(packet) is not list or len(packet)!=1 or type(packet[0]) is not dict:raise ValueError
                value=packet[0]
                if (value.get('id'),value.get('method'),value.get('mode'))!=(request['id'],'GET','json'):raise ValueError
                status=value.get('outcome')
                if not(type(status) is int and 100<=status<=599 or type(status) is str and status in {'refused','timeout','unreachable','dns_error','error','probe_error'}):raise ValueError
                text=value.get('body');complete=bool(type(text) is str and len(text.encode())<=131072 and value.get('capture_complete') is True
                    and value.get('eof') is True and value.get('utf8_valid') is True and value.get('body_truncated') is False
                    and type(value.get('body_bytes')) is int and value['body_bytes']==len(text.encode()) and value.get('body_sha256')==sha256_hex(text.encode()))
                shape=False
                if complete and status==200:
                    body=strict_json(text.encode(),limit=131072);models=body.get('data') if type(body) is dict else None
                    shape=bool(type(body) is dict and body.get('object')=='list' and type(models) is list and 1<=len(models)<=128
                        and all(type(c) is dict and type(c.get('id')) is str for c in models) and any(c['id']==self.api_model for c in models))
                row={'case_id':'plt10-'+slot,'request_id_sha256':sha256_hex(request_id.encode()),'credential_label':role,
                    'outcome':status,'binding_verified':True,'capture_complete':complete,'normal_shape_verified':shape,
                    'denial_response_observed':bool(complete and status in {401,403}),'fresh_connection_source_design':True,
                    'new_connection_receipt_verified':False,'issuer_event_verified':False}
                self.rows.append(row);return deepcopy(row)
            except Exception:
                self.failed=True;self.rows.append({'case_id':'plt10-'+SLOTS[selected] if selected is not None else None,
                    'binding_verified':False,'diagnostic':'CREDENTIAL_REVOCATION_HTTP_UNAVAILABLE'})
                raise KubernetesBindingError() from None

    def report(self):
        return {'profile':PROFILE,'target_sha256':self.target_sha256,'binding':deepcopy(self.binding),'environment_ref':deepcopy(self.base.environment_ref),
            'probe_source_sha256':self.base.source_sha256,'requests':deepcopy(self.rows),'collection_gap':self.failed or bool(self.base.cleanup_errors),
            'cleanup_errors':list(self.base.cleanup_errors),'issuer_mutated':False,'credentials_written':False,'credential_hashes_recorded':False,
            'issuer_event_verified':False,'producer_authenticated':False}
