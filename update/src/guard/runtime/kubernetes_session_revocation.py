"""One bounded owned probe process preserves a selected HTTP connection."""
from copy import deepcopy
from pathlib import Path
import re,threading
from guard.models import canonical_json,sha256_hex
from guard.executors.persistent_session_probe import SCHEMA,SLOTS
from guard.executors.session_revocation_contract import normalize_policy,epoch_ns
from .kubernetes_http import KubernetesHTTP,KubernetesBindingError,REQUEST_BINDING_PROFILE,BINDING_PROBE_SHA256

PROFILE='kubernetes-owned-existing-http-session-v1'
PROBE_SHA256='a687988eefb3e31f7a1f8c79c41e0da3649af89a4084ef5d6e4cf47ef4d9bf26'
ROW_FIELDS=frozenset({'case_id','request_id_sha256','credential_label','attempted','outcome','normal_shape_verified','denial_response_observed',
 'capture_complete','started_wall_ns','finished_wall_ns','started_monotonic_ns','finished_monotonic_ns','clock_consistent','probe_socket_before',
 'probe_socket_after','selected_existing_socket_verified','new_distinct_probe_connection_verified','response_allows_keepalive','diagnostic',
 'issuer_event_verified','serving_session_receipt_verified'})


def validate_packet(packet,token):
    if (type(packet) is not dict or set(packet)!={'schema','rows','cleanup_errors','issuer_mutated','credential_bytes_or_hashes_recorded','probe_process_only',
            'producer_authenticated','serving_session_receipt_verified','application_session_or_handle_verified'} or packet['schema']!=SCHEMA
            or packet['probe_process_only'] is not True or any(packet[k] is not False for k in ('issuer_mutated','credential_bytes_or_hashes_recorded',
                'producer_authenticated','serving_session_receipt_verified','application_session_or_handle_verified'))
            or type(packet['rows']) is not list or len(packet['rows'])!=5 or packet['cleanup_errors'] not in ([],['SESSION_PROBE_OWN_CONNECTION_CLOSE_UNVERIFIED'])):raise ValueError
    for slot,row in zip(SLOTS,packet['rows']):
        if type(row) is not dict or set(row)!=ROW_FIELDS or row['case_id']!='plt11-'+slot or row['credential_label']!=('retired' if slot.startswith('retired-') else 'control'):raise ValueError
        if row['request_id_sha256']!=sha256_hex(('byul-plt11-'+token+'-'+slot).encode()):raise ValueError
        for field in ('attempted','normal_shape_verified','denial_response_observed','capture_complete','clock_consistent','selected_existing_socket_verified','new_distinct_probe_connection_verified','response_allows_keepalive'):
            if type(row[field]) is not bool:raise ValueError
        if row['issuer_event_verified'] is not False or row['serving_session_receipt_verified'] is not False:raise ValueError
        if row['outcome'] is not None and (type(row['outcome']) is not int or not 100<=row['outcome']<=599):raise ValueError
        if row['diagnostic'] not in (None,'SESSION_PROBE_SELECTED_ACCESS_UNAVAILABLE'):raise ValueError
        for field in ('started_wall_ns','finished_wall_ns','started_monotonic_ns','finished_monotonic_ns'):
            if row[field] is not None and (type(row[field]) is not int or not 0<row[field]<2**63):raise ValueError
        for field in ('probe_socket_before','probe_socket_after'):
            value=row[field]
            if value is not None and (type(value) is not dict or set(value)!={'probe_pid','descriptor','device','inode'}
                or any(type(v) is not int or not 0<=v<2**63 for v in value.values()) or value['probe_pid']==0 or value['inode']==0):raise ValueError
        if row['attempted']!=(row['started_wall_ns'] is not None and row['started_monotonic_ns'] is not None):raise ValueError
        if any(row['finished_'+unit] is None or row['started_'+unit] is not None and row['finished_'+unit]<row['started_'+unit] for unit in ('wall_ns','monotonic_ns')):raise ValueError
        if row['clock_consistent'] and not row['attempted']:raise ValueError
        if row['normal_shape_verified'] and (row['outcome']!=200 or not row['capture_complete']):raise ValueError
        if row['denial_response_observed'] and (row['outcome'] not in {401,403} or not row['capture_complete']):raise ValueError
        if row['selected_existing_socket_verified'] and (slot not in {'retired-session-open','retired-session-reuse'} or not row['attempted']
            or row['probe_socket_before'] is None or row['probe_socket_before']!=row['probe_socket_after'] or not row['response_allows_keepalive']):raise ValueError
        if row['new_distinct_probe_connection_verified'] and (slot!='retired-new' or not row['attempted'] or row['probe_socket_before'] is None):raise ValueError
    return deepcopy(packet)


class KubernetesSessionRevocationHTTP:
    profile=PROFILE
    def __init__(self,target,env,keys,*,runner=None,duration=120,binary='kubectl'):
        if target.get('lab') is not True or env.engine.name!='vllm' or env.engine.version!='0.31.0':raise KubernetesBindingError()
        if (type(keys) is not dict or set(keys)!={'retired','control'} or any(type(v) is not str or not 0<len(v)<=8192 or any(ord(c)<33 or ord(c)>126 for c in v) for v in keys.values())
                or len(set(keys.values()))!=2):raise KubernetesBindingError()
        self._keys=deepcopy(keys);self.base=KubernetesHTTP(target,env,runner=runner,duration=duration,binary=binary,profile=REQUEST_BINDING_PROFILE)
        self.binding=deepcopy(self.base.binding);self.api_model=self.base.api_model;self.model_mapping=deepcopy(self.base.model_mapping)
        self.target_sha256=sha256_hex(canonical_json(target));self.engine_url=target['engine']['trusted_url'];self._used=False;self._lock=threading.Lock()
        root=Path(__file__).parents[1]/'executors';support=(root/'stream_probe.py').read_text(encoding='utf-8');probe=(root/'persistent_session_probe.py').read_text(encoding='utf-8')
        if sha256_hex(support.encode())!=BINDING_PROBE_SHA256 or sha256_hex(probe.encode())!=PROBE_SHA256:raise KubernetesBindingError()
        self.source=("import json,math,os,sys,threading\nlimit=float(sys.argv[1])\n"
            "if not math.isfinite(limit) or not 2 < limit <= 60: sys.exit(124)\n"
            "watchdog=threading.Timer(limit,lambda:os._exit(124))\nwatchdog.daemon=True\nwatchdog.start()\n"
            "support={'__name__':'guard_stream_support'}\nexec(compile("+repr(support)+",'guard-stream-support','exec'),support)\n"
            "probe={'__name__':'guard_session_probe'}\nexec(compile("+repr(probe)+",'guard-session-probe','exec'),probe)\n"
            "raw=sys.stdin.buffer.read(65537)\nif len(raw)>65536: sys.exit(2)\n"
            "packet=probe['collect'](json.loads(raw),support['_Response'])\n"
            "sys.stdout.write(json.dumps(packet,sort_keys=True,separators=(',',':'))+'\\n')\n")
        self.source_sha256=sha256_hex(self.source.encode());self.packet=None;self.before=False;self.after=False;self.failed=False

    def __call__(self,*args,**kwargs):raise KubernetesBindingError()

    def perform(self,token,policy):
        with self._lock:
            if self._used or type(token) is not str or re.fullmatch('[0-9a-f]{32}',token) is None:raise KubernetesBindingError()
            self._used=True;normalize_policy(policy)
            try:
                self.base._verify(capture_snapshot=True);self.before=True;budget=min(60,self.base.deadline-self.base.clock())
                if budget<=2:raise ValueError
                spec={'schema':SCHEMA,'url':self.engine_url,'api_model':self.api_model,'request_token':token,'keys':self._keys,
                    'open_before_ns':epoch_ns(policy['session_open_before']),'reuse_after_ns':epoch_ns(policy['session_deny_after']),
                    'max_clock_skew_ms':policy['session_max_clock_skew_ms'],'max_wait_seconds':policy['session_max_wait_seconds'],'timeout_seconds':budget-2}
                def retained(value):self.packet=validate_packet(value,token)
                probe=self.binding['probe'];self.base._call(['--namespace='+probe['namespace'],'exec','-i',probe['pod'],'--container='+probe['container'],
                    '--','python3','-c',self.source,str(budget)],payload=canonical_json(spec),cap=262144,budget=budget,on_document=retained)
            except Exception:self.failed=True
            finally:
                if self.before:
                    try:self.base._verify(capture_snapshot=True);self.after=True
                    except Exception:self.failed=True
            return self.report()

    def report(self):
        return {'profile':PROFILE,'target_sha256':self.target_sha256,'binding':deepcopy(self.binding),'environment_ref':deepcopy(self.base.environment_ref),
            'probe_source_sha256':self.source_sha256,'helper_file_sha256':PROBE_SHA256,'bounded_framing_file_sha256':BINDING_PROBE_SHA256,
            'packet':deepcopy(self.packet),'binding_before_verified':self.before,'binding_after_verified':self.after,
            'collection_gap':self.failed or bool(self.base.cleanup_errors) or self.packet is None
                or bool(self.packet and (self.packet['cleanup_errors'] or any(row['diagnostic'] for row in self.packet['rows']))),
            'cleanup_errors':list(self.base.cleanup_errors)+([] if self.packet is None else self.packet['cleanup_errors']),
            'issuer_mutated':False,'credentials_written':False,'credential_hashes_recorded':False,'producer_authenticated':False}
