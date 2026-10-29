"""REQ04 closed Gateway requests over the existing four-Pod owner boundary."""
from copy import deepcopy
import re
import threading
from guard.models import canonical_json,sha256_hex
from .kubernetes_routes import KubernetesRoutesHTTP,EXTERNAL
from .kubernetes_http import KubernetesBindingError
from .kubernetes_forwarded_identity import normalize_header_contract

HTTP_PROFILE='kubernetes-owned-forwarded-identity-http-v1'
SLOTS=('normal-before','alternate','normal-after')


def header_value(value):
    return type(value) is str and 0<len(value)<=256 and all(32<=ord(c)<=126 for c in value)


class KubernetesForwardedIdentityHTTP(KubernetesRoutesHTTP):
    profile=HTTP_PROFILE

    def __init__(self,target,env,*,header_contract,credential,normal_claim,alternate_claim,duration=120,binary='kubectl',runner=None):
        if (target.get('lab') is not True or env.engine.name!='vllm' or env.engine.version!='0.31.0'
                or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise KubernetesBindingError()
        self.header_contract=normalize_header_contract(header_contract)
        if (not all(header_value(v) for v in (credential,normal_claim,alternate_claim))
                or normal_claim==alternate_claim or credential in {normal_claim,alternate_claim}):raise KubernetesBindingError()
        super().__init__(target,env,duration=duration,binary=binary,runner=runner)
        # Reuse the pinned JSON collector already validated by the base. The
        # original broad route/REQ01/03 probe and public behavior stay unchanged.
        self.source=self.base.source;self.source_sha256=self.base.source_sha256
        self._credential=credential;self._claims={'normal-before':normal_claim,'alternate':alternate_claim,'normal-after':normal_claim}
        self._token=None;self._cases=set();self._serial=threading.Lock()

    def verify_owned_generations(self):
        try:
            self._verify()
            return deepcopy(self.binding)
        except Exception:
            self.failed=True
            raise KubernetesBindingError() from None

    def __call__(self,network,requests,timeout):
        import math
        slot=None
        with self._serial:
            try:
                if (self.failed or network!=EXTERNAL or type(requests) is not list or len(requests)!=1
                        or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10):raise ValueError
                r=requests[0]
                if (type(r) is not dict or set(r)!={'id','method','mode','url','headers','body'}
                        or type(r['id']) is not str or r['id'] not in {'req04-'+s for s in SLOTS}
                        or r['method']!='POST' or r['mode']!='json' or r['url']!=self.gateway.geturl()+'/v1/completions'):raise ValueError
                slot=r['id'].removeprefix('req04-')
                token=re.fullmatch(r'byul-req04-([0-9a-f]{32})-'+slot,r['headers'].get('X-Request-Id','')) if type(r['headers']) is dict else None
                if token is None:raise ValueError
                expected={'Authorization':'Bearer '+self._credential,'X-Request-Id':r['headers']['X-Request-Id'],
                    self.header_contract['header_name']:self._claims[slot]}
                if (canonical_json(r['headers'])!=canonical_json(expected) or canonical_json(r['body'])!=canonical_json(
                        {'model':self.api_model,'prompt':'hello','max_tokens':1,'temperature':0,'stream':False})
                        or slot in self._cases or 'normal-after' in self._cases
                        or slot!='normal-before' and 'normal-before' not in self._cases
                        or self._token is not None and self._token!=token[1]):raise ValueError
                self._token=token[1];self._cases.add(slot);self._verify()
                budget=min(timeout,self.base.deadline-self.base.clock())
                if budget<=0:raise ValueError
                p=self.binding['external_probe']
                raw=self.base._call(['--namespace='+p['namespace'],'exec','-i',p['pod'],'--container='+p['container'],
                    '--','python3','-c',self.source,str(budget)],payload=canonical_json({'requests':requests,'timeout':budget}),cap=2*1024**2,budget=budget)
                self._verify()
                if (type(raw) is not list or len(raw)!=1 or type(raw[0]) is not dict
                        or raw[0].get('id')!=r['id'] or raw[0].get('method')!='POST' or raw[0].get('mode')!='json'):raise ValueError
                row=raw[0];status=row.get('outcome')
                if not(type(status) is int and 100<=status<=599 or type(status) is str and status in {'refused','timeout','unreachable','dns_error','error','probe_error'}):raise ValueError
                text=row.get('body')
                if text is not None and (type(text) is not str or len(text.encode())>128*1024):raise ValueError
                complete=(row.get('capture_complete') is True and row.get('eof') is True and row.get('utf8_valid') is True
                    and row.get('body_truncated') is False and type(text) is str and type(row.get('body_bytes')) is int
                    and row['body_bytes']==len(text.encode()) and row.get('body_sha256')==sha256_hex(text.encode()))
                self.rows.append({'case_id':'req04-'+slot,'binding_verified_before_after':True,'outcome':status,'capture_complete':complete})
                return [{'id':'req04-'+slot,'outcome':status,'capture_complete':complete,'body':text if complete else None}]
            except Exception:
                self.failed=True
                self.rows.append({'case_id':'req04-'+slot if slot in SLOTS else None,'binding_verified_before_after':False,
                    'diagnostic':'FORWARDED_IDENTITY_HTTP_UNAVAILABLE'})
                raise KubernetesBindingError() from None

    def report(self):
        return {'profile':HTTP_PROFILE,'target_sha256':self.target_sha256,'binding':deepcopy(self.binding),
            'environment_ref':deepcopy(self.base.environment_ref),'probe_source_sha256':self.source_sha256,
            'requests':deepcopy(self.rows),'collection_gap':self.failed or any(not r.get('capture_complete') for r in self.rows) or bool(self.base.cleanup_errors),
            'cleanup_errors':list(self.base.cleanup_errors),'principal_identity_verified':False,'gateway_normalization_verified':False}
