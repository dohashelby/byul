"""Closed REQ06 Responses operations; no generic mutating route relaxation."""
from copy import deepcopy
import threading
from guard.models import canonical_json,sha256_hex
from guard.runtime.bounded import strict_json
from guard.adapters.vllm_stored_responses import creation_body,response_projection,REVIEWED_SOURCES
from .kubernetes_routes import KubernetesRoutesHTTP,EXTERNAL
from .kubernetes_http import KubernetesBindingError
from .kubernetes_forwarded_identity_http import header_value
from .kubernetes_stored_response_scope import OwnedResponseScope

HTTP_PROFILE='kubernetes-owned-stored-responses-http-v1'
STAGES=frozenset({'create','owner-before','cross-read','owner-after','cleanup-after','owner-cancel','cross-cancel','cleanup',
    'owner-previous','cross-previous','owner-overwrite','cross-overwrite','owner-overwrite-after'})


class KubernetesStoredResponsesHTTP(KubernetesRoutesHTTP):
    profile=HTTP_PROFILE

    def __init__(self,target,env,*,scope,credentials,duration=300,binary='kubectl',runner=None):
        if (target.get('lab') is not True or env.engine.name!='vllm' or env.engine.version!='0.31.0'
                or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))
                or type(credentials) is not dict or set(credentials)!={'A','B'}
                or not all(header_value(v) for v in credentials.values()) or credentials['A']==credentials['B']):raise KubernetesBindingError()
        self.scope=OwnedResponseScope(scope)
        super().__init__(target,env,duration=duration,binary=binary,runner=runner)
        if 'direct' in scope['origins'] and target['direct_targets']!=[{'origin':'external','url':self.engine.geturl()}]:raise KubernetesBindingError()
        self.source,self.source_sha256=self.base.source,self.base.source_sha256
        self._credentials=dict(credentials);self._scope=deepcopy(scope);self._cases=set();self._created=set();self._controls={}
        self._normal_cancel=set();self._cancel_claim=set();self._normal_previous=set();self._normal_overwrite=set();self._overwrite_claim={}
        self._serial=threading.Lock()

    def __call__(self,*args,**kwargs):
        # Deliberately do not expose the parent's general routes interface.
        raise KubernetesBindingError()

    def ready(self,origin,owner,kind):
        return not self.failed and (origin,owner,kind) in self._controls

    def status(self,origin,owner,kind):
        return self._controls.get((origin,owner,kind),{}).get('status')

    def perform(self,origin,actor,owner,kind,action,stage):
        safe={'origin':origin if type(origin) is str and origin in {'gateway','direct'} else None,'actor':actor if type(actor) is str and actor in {'A','B'} else None,
            'owner_claim':owner if type(owner) is str and owner in {'A','B'} else None,'kind':kind if type(kind) is str and kind in {'read','cancel-normal','cancel-target','previous','overwrite'} else None,
            'action':action if type(action) is str and action in {'create','get','cancel','previous','overwrite'} else None,'stage':stage if type(stage) is str and stage in STAGES else None,
            'attempted':False,'outcome':None,'capture_complete':False,'binding_verified':False,'object_shape_verified':False,
            'object_id_matches':False,'status':None,'content_sha256':None,'previous_id_matches':None,'probe_marker':None,
            'object_owner_verified':False,'store_effect_verified':False}
        with self._serial:
            try:
                if self.failed or None in (safe['origin'],safe['actor'],safe['owner_claim'],safe['kind'],safe['action'],safe['stage']):raise ValueError
                key=(origin,owner,kind);oid=self.scope.expected_id(*key)
                case=(origin,actor,owner,kind,stage)
                if case in self._cases or len(self._cases)>=128:raise ValueError
                same=actor==owner;method='POST';path='/v1/responses';previous=None;response_id=oid
                if action=='create':
                    if not same or stage!='create' or key in self._created:raise ValueError
                    body=creation_body(self.api_model,oid,background=kind.startswith('cancel-'))
                    self._created.add(key)
                else:
                    self.scope.require_created(*key,oid)
                    if key not in self._controls and not (action=='get' and same and stage=='owner-before'):raise ValueError
                    if action=='get':
                        if stage not in {'owner-before','cross-read','owner-after','cleanup-after','owner-overwrite-after'} or (stage=='cross-read')==same:raise ValueError
                        if stage=='cross-read' and kind!='read':raise ValueError
                        if stage=='cross-read' and (origin,actor,'read') not in self._controls:raise ValueError
                        method='GET';path+='/'+oid;body=None
                    elif action=='cancel':
                        if (stage,kind,same) not in {('owner-cancel','cancel-normal',True),('cross-cancel','cancel-target',False),('cleanup','cancel-target',True)}:raise ValueError
                        if self.status(*key) not in {'queued','in_progress'}:raise ValueError
                        if stage=='cross-cancel' and (origin,actor) not in self._normal_cancel:raise ValueError
                        path+='/'+oid+'/cancel';body=None
                    elif action=='previous':
                        if kind!='previous' or (stage,same) not in {('owner-previous',True),('cross-previous',False)}:raise ValueError
                        if stage=='cross-previous' and (origin,actor) not in self._normal_previous:raise ValueError
                        previous=oid;response_id=oid+'_'+stage+'_'+actor
                        body=creation_body(self.api_model,response_id,previous=oid)
                    elif action=='overwrite':
                        if kind!='overwrite' or (stage,same) not in {('owner-overwrite',True),('cross-overwrite',False)}:raise ValueError
                        if stage=='cross-overwrite' and (origin,actor) not in self._normal_overwrite:raise ValueError
                        body=creation_body(self.api_model,oid,overwrite='owner' if same else 'cross')
                    else:raise ValueError
                self._cases.add(case)
                rid='byul-req06-'+self.scope._nonce+'-'+str(len(self._cases))
                request={'id':rid,'method':method,'mode':'json','url':(self.gateway if origin=='gateway' else self.engine).geturl()+path,
                    'headers':{'Authorization':'Bearer '+self._credentials[actor],'X-Request-Id':rid},'body':body}
                safe.update(case_id_sha256=sha256_hex(rid.encode()),object_id_sha256=sha256_hex(oid.encode()))
                self._verify();budget=min(10,self.base.deadline-self.base.clock())
                if budget<=0:raise ValueError
                safe['attempted']=True;p=self.binding['external_probe']
                raw=self.base._call(['--namespace='+p['namespace'],'exec','-i',p['pod'],'--container='+p['container'],'--',
                    'python3','-c',self.source,str(budget)],payload=canonical_json({'requests':[request],'timeout':budget}),cap=2*1024**2,budget=budget)
                self._verify();safe['binding_verified']=True
                if type(raw) is not list or len(raw)!=1 or type(raw[0]) is not dict:raise ValueError
                row=raw[0]
                if row.get('id')!=rid or row.get('method')!=method or row.get('mode')!='json':raise ValueError
                status=row.get('outcome')
                if not(type(status) is int and 100<=status<=599 or type(status) is str and status in {'refused','timeout','unreachable','dns_error','error','probe_error'}):raise ValueError
                text=row.get('body');complete=(type(text) is str and len(text.encode())<=128*1024 and row.get('capture_complete') is True
                    and row.get('eof') is True and row.get('utf8_valid') is True and row.get('body_truncated') is False
                    and type(row.get('body_bytes')) is int and row['body_bytes']==len(text.encode()) and row.get('body_sha256')==sha256_hex(text.encode()))
                safe.update(outcome=status,capture_complete=complete)
                if status==200 and complete:
                    value=strict_json(text.encode(),limit=128*1024)
                    safe.update(response_projection(value,expected_id=response_id,api_model=self.api_model,previous=previous))
                    if safe['object_shape_verified']:
                        if action=='create':self.scope.remember_creation_claim(*key,value['id'])
                        if action=='get' and same:self._controls[key]=deepcopy(safe)
                        if stage=='owner-cancel' and safe['status']=='cancelled':self._cancel_claim.add((origin,actor))
                        if stage=='owner-after' and kind=='cancel-normal' and safe['status']=='cancelled' and (origin,actor) in self._cancel_claim:
                            self._normal_cancel.add((origin,actor))
                        if stage=='owner-previous' and safe['previous_id_matches']:self._normal_previous.add((origin,actor))
                        if stage=='owner-overwrite':self._overwrite_claim[(origin,actor)]=safe['content_sha256']
                        if stage=='owner-overwrite-after' and safe['probe_marker']=='owned-self-overwrite' and safe['content_sha256']==self._overwrite_claim.get((origin,actor)):
                            self._normal_overwrite.add((origin,actor))
                self.rows.append(safe)
                if self.base.cleanup_errors:
                    self.failed=True;self.scope.invalidate('cleanup_unverified')
                return deepcopy(safe)
            except Exception:
                self.failed=True;self.scope.invalidate('binding_unverified')
                safe['diagnostic']='STORED_RESPONSE_HTTP_UNAVAILABLE';self.rows.append(safe)
                raise KubernetesBindingError() from None

    def report(self):
        return {'profile':HTTP_PROFILE,'target_sha256':self.target_sha256,'binding':deepcopy(self.binding),
            'environment_ref':deepcopy(self.base.environment_ref),'probe_source_sha256':self.source_sha256,'reviewed_source_claims':dict(REVIEWED_SOURCES),
            'requests':deepcopy(self.rows),'scope':self.scope.report(),'collection_gap':self.failed or bool(self.base.cleanup_errors),
            'cleanup_errors':list(self.base.cleanup_errors),'producer_authenticated':False,'object_owner_verified':False,'worker_effect_verified':False}
