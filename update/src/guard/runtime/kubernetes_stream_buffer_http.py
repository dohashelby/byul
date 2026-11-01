"""LIF04 closed existing stdlib requests; client facts are not server memory."""
from copy import deepcopy
import math,re,threading
from guard.models import canonical_json,sha256_hex
from .kubernetes_http import KubernetesHTTP,KubernetesBindingError,REQUEST_BINDING_PROFILE
from .kubernetes_stream_buffer import normalize_read_profiles

HTTP_PROFILE='kubernetes-owned-stream-buffer-http-v1'
SLOTS=('normal-before','normal','slow','slow-close','normal-after')


class KubernetesStreamBufferHTTP:
    profile=HTTP_PROFILE
    def __init__(self,target,env,*,read_profiles,runner=None,duration=120,binary='kubectl'):
        if target.get('lab') is not True or env.engine.name!='vllm' or env.engine.version!='0.31.0':raise KubernetesBindingError()
        self.read_profiles=normalize_read_profiles(read_profiles)
        self.base=KubernetesHTTP(target,env,runner=runner,duration=duration,binary=binary,profile=REQUEST_BINDING_PROFILE)
        self.binding=deepcopy(self.base.binding);self.api_model=self.base.api_model;self.model_mapping=deepcopy(self.base.model_mapping)
        self.target_sha256=sha256_hex(canonical_json(target));self.engine_url=target['engine']['trusted_url']
        self.rows=[];self.failed=False;self._token=None;self._cases=set();self._lock=threading.Lock()

    def __call__(self,network,requests,timeout):
        slot=None
        with self._lock:
            try:
                if (self.failed or network!='kubernetes-probe' or type(requests) is not list or len(requests)!=1
                        or type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0<timeout<=10):raise ValueError
                r=requests[0]
                if type(r) is not dict or set(r)-{'id','mode','method','url','headers','body','read_profile'} or type(r.get('id')) is not str:raise ValueError
                slot=r['id'].removeprefix('lif04-')
                if r['id']!='lif04-'+slot or slot not in SLOTS or slot in self._cases or 'normal-after' in self._cases:raise ValueError
                if slot!='normal-before' and 'normal-before' not in self._cases:raise ValueError
                streaming=slot not in {'normal-before','normal-after'};headers=r.get('headers')
                if type(headers) is not dict or set(headers)!={'X-Request-Id'} or type(headers['X-Request-Id']) is not str:raise ValueError
                token=re.fullmatch(r'byul-lif04-([0-9a-f]{32})-'+slot,headers['X-Request-Id'])
                if token is None or self._token is not None and self._token!=token[1]:raise ValueError
                body={'model':self.api_model,'prompt':'hello','max_tokens':8 if streaming else 1,'temperature':0,'stream':streaming}
                if (r.get('method')!='POST' or r.get('mode')!=('sse' if streaming else 'json') or r.get('url')!=self.engine_url+'/v1/completions'
                        or canonical_json(r.get('body'))!=canonical_json(body)):raise ValueError
                expected=self.read_profiles.get(slot)
                if expected is None and 'read_profile' in r or expected is not None and canonical_json(r.get('read_profile'))!=canonical_json(expected):raise ValueError
                self._cases.add(slot);self._token=token[1];self.base._verify(capture_snapshot=True)
                budget=min(timeout,self.base.deadline-self.base.clock())
                if budget<=0:raise ValueError
                p=self.binding['probe'];raw=self.base._call(['--namespace='+p['namespace'],'exec','-i',p['pod'],'--container='+p['container'],
                    '--','python3','-c',self.base.source,str(budget)],payload=canonical_json({'requests':requests,'timeout':budget}),cap=2*1024**2,budget=budget)
                self.base._verify(capture_snapshot=True)
                if type(raw) is not list or len(raw)!=1 or type(raw[0]) is not dict:raise ValueError
                value=raw[0]
                if (value.get('id'),value.get('method'),value.get('mode'))!=(r['id'],'POST',r['mode']):raise ValueError
                outcome=value.get('outcome')
                if not(type(outcome) is int and 100<=outcome<=599 or type(outcome) is str and outcome in {'refused','timeout','unreachable','dns_error','error','probe_error'}):raise ValueError
                text=value.get('body');complete=(type(text) is str and len(text.encode())<=131072 and value.get('capture_complete') is True and value.get('eof') is True
                    and value.get('utf8_valid') is True and value.get('body_truncated') is False and type(value.get('body_bytes')) is int
                    and value['body_bytes']==len(text.encode()) and value.get('body_sha256')==sha256_hex(text.encode()))
                row={'case_id':'lif04-'+slot,'outcome':outcome,'binding_verified':True,'capture_complete':complete,
                    'event_stream':value.get('content_type')=='text/event-stream',
                    'eof':value.get('eof') is True,'terminal_seen':value.get('terminal_seen') is True,'client_closed':False,
                    'client_close_ms':None,'read_count':None,'read_pause_count':None,'read_observations':[],'intentional_client_close':False,
                    'body_bytes':value.get('body_bytes') if type(value.get('body_bytes')) is int and 0<=value['body_bytes']<=131072 else None}
                if expected is not None:
                    count,pause,observations=value.get('read_count'),value.get('read_pause_count'),value.get('read_observations')
                    if (type(count) is not int or not 0<=count<=expected['close_after_reads'] or type(pause) is not int or not 0<=pause<=count
                            or type(observations) is not list or len(observations)!=count):raise ValueError
                    previous=0
                    for i,item in enumerate(observations):
                        if (type(item) is not dict or set(item)!={'seq','started_ms','finished_ms','bytes'} or type(item['seq']) is not int or item['seq']!=i
                                or any(type(item[k]) not in (int,float) or not math.isfinite(item[k]) for k in ('started_ms','finished_ms'))
                                or not previous<=item['started_ms']<=item['finished_ms'] or type(item['bytes']) is not int or not 1<=item['bytes']<=expected['chunk_bytes']):raise ValueError
                        previous=item['finished_ms']
                    close=value.get('client_close_ms');closed=value.get('client_closed')
                    if type(closed) is not bool:raise ValueError
                    if closed and (type(close) not in (int,float) or not math.isfinite(close) or close<previous or count!=expected['close_after_reads']
                            or value.get('stop_reason')!='client_close' or value.get('eof') is not False or value.get('capture_complete') is not False):raise ValueError
                    row.update(client_closed=closed,client_close_ms=close if closed else None,read_count=count,read_pause_count=pause,
                        read_observations=deepcopy(observations),intentional_client_close=closed)
                self.rows.append(row)
                return [{'id':'lif04-'+slot,**row,'body':text if complete and not streaming else None}]
            except Exception:
                self.failed=True;self.rows.append({'case_id':'lif04-'+slot if slot in SLOTS else None,'binding_verified':False,'diagnostic':'STREAM_BUFFER_HTTP_UNAVAILABLE'})
                raise KubernetesBindingError() from None

    def report(self):
        return {'profile':HTTP_PROFILE,'target_sha256':self.target_sha256,'binding':deepcopy(self.binding),'probe_source_sha256':self.base.source_sha256,
            'environment_ref':deepcopy(self.base.environment_ref),'requests':deepcopy(self.rows),'collection_gap':self.failed or bool(self.base.cleanup_errors),
            'cleanup_errors':list(self.base.cleanup_errors),'server_memory_verified':False,'producer_authenticated':False}
