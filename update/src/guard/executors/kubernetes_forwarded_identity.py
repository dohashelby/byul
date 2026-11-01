"""REQ04 exact owned Gateway probes plus separate declared role records."""
from copy import deepcopy
from pathlib import Path
import uuid
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import capture_pipes,strict_json
from guard.runtime.kubernetes_forwarded_identity import normalize_header_contract
from guard.runtime.kubernetes_forwarded_identity_http import KubernetesForwardedIdentityHTTP,HTTP_PROFILE,header_value,SLOTS
from guard.runtime.kubernetes_routes import EXTERNAL
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from .owned_capture import _FILE_READER,MAX_BYTES
from .forwarded_identity_contract import PROFILE,POLICY_TYPES,OBSERVATIONS,normalize_policy,validate_record,correlate,stamp


class ForwardedIdentityReader(KubernetesLifecycleReader):
    def __init__(self,transport,target,session,role,*,binary,duration,runner):
        if role not in {'gateway','engine'} or target.get('lab') is not True:raise KubernetesCaptureError()
        self.role=role
        # Both role bindings were independently validated by the four-Pod
        # transport. This is the fixed record reader, not a native worker profile.
        binding={k:transport.binding[k] for k in ('context','run_id')}
        binding['engine']=deepcopy(transport.binding[role])
        self._initialize(binding,target,{'session':session},binary=binary,duration=duration,runner=runner)
        self.deadline=min(self.deadline,transport.base.deadline)

    def _program(self,container,argv,max_bytes):
        path='/guard-identity/data/'+self.session+'/forwarded-identity.json'
        if container!=self.binding['engine']['container'] or argv!=['python3','-c',_FILE_READER,path,str(MAX_BYTES)] or max_bytes!=2*MAX_BYTES:
            raise KubernetesCaptureError()
        return 'file'

    def report(self):
        value=super().report();value['profile']=PROFILE;value['record_role']=self.role
        value['programs']={'file':value['programs']['file']}
        value.update(producer_authenticated=False,producer_process_binding_verified=False,producer_code_verified=False)
        return value


class KubernetesForwardedIdentityExecutor(HttpCheckExecutor):
    name='forwarded_identity';version='0.1.0';requires_roles=True;requires_route_scope=False;functional_via='gateway'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='REQ-04' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('FORWARDED_IDENTITY_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            if (set(self.cfg)-{'target','roles','header','kubectl_binary','max_duration_seconds'} or adapter is None
                    or set(self.roles())!={'allowed'} or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise ValueError
            header=normalize_header_contract(self.cfg['header']);keys=self.keys()
            normal=self._environ.get(header['normal_value_env']);alternate=self._environ.get(header['alternate_value_env'])
            if not header_value(normal) or not header_value(alternate) or normal==alternate or keys['allowed'] in {normal,alternate}:raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target));start,end=normalize_policy(policy_values)
            if (digest!=policy_values['forwarded_target_sha256'] or env.ref().sha256!=policy_values['forwarded_environment_sha256']
                    or self.expected_target is not None and digest!=self.expected_target or not start<=utcnow()<=end):raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('FORWARDED_IDENTITY_CONFIGURATION_INVALID') from None
        transport=KubernetesForwardedIdentityHTTP(target,env,header_contract=header,credential=keys['allowed'],normal_claim=normal,
            alternate_claim=alternate,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'),runner=self.runner)
        secrets=[keys['allowed'],normal,alternate]
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys=keys,secrets=[v for v in secrets if len(v)>=8],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._forwarded_header=header;ctx._forwarded_claims={'normal-before':normal,'alternate':alternate,'normal-after':normal}
        ctx._forwarded_secrets=secrets
        return self._finish(ctx)

    def collect(self,ctx):
        values=ctx.policy_values;start,end=normalize_policy(values);token=uuid.uuid4().hex
        summary={'profile':PROFILE,'trials':[],'snapshots':[],'collection':[],'diagnostic_codes':[],'fixture':{'profile':PROFILE},
            'producer_authenticated':False,'principal_identity_verified':False,'whole_scope_complete':False}
        readers={};replies={}
        def read_records(stage):
            for role in ('gateway','engine'):
                row={'stage':stage,'role':role,'collected':False,'file_bytes_sha256':None,'file_bytes':None,'owner_binding_verified':False}
                try:
                    if not start<=utcnow()<=end:raise ValueError
                    # Reuse all four selected identities around each role read.
                    ctx.cprobe.verify_owned_generations();reader=readers[role];b=reader.binding['engine']
                    raw,provenance=reader.read_file(reader,ctx.target,container=b['container'],image=b['image'],
                        path='/guard-identity/data/'+values['forwarded_session']+'/forwarded-identity.json')
                    packet=validate_record(strict_json(raw,limit=MAX_BYTES),role=role,environment_ref=ctx.env.ref().model_dump(mode='json'),
                        target_sha256=values['forwarded_target_sha256'],run_sha256=sha256_hex(ctx.cprobe.binding['run_id'].encode()),
                        session=values['forwarded_session'],source_sha256=values['forwarded_'+role+'_source_sha256'],
                        policy_sha256=values['forwarded_identity_policy_sha256'],header_contract=ctx._forwarded_header,forbidden_values=ctx._forwarded_secrets)
                    ctx.cprobe.verify_owned_generations()
                    if provenance.get('owner_binding_verified') is not True or not start<=utcnow()<=end:raise ValueError
                    row.update(collected=True,file_bytes_sha256=provenance['capture_sha256'],file_bytes=provenance['bytes'],
                        file_identity=provenance['file_identity'],owner_binding_verified=True)
                    summary['snapshots'].append({'stage':stage,'role':role,'packet':packet})
                    if not packet['source_ended'] or packet['dropped_events']:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_SOURCE_INCOMPLETE')
                except Exception:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_RECORD_UNAVAILABLE')
                summary['collection'].append(row)
        try:
            for role in ('gateway','engine'):
                readers[role]=ForwardedIdentityReader(ctx.cprobe,ctx.target,values['forwarded_session'],role,
                    binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120),runner=self.runner or capture_pipes)
            read_records('initial')
            for slot in SLOTS:
                if slot=='alternate' and not replies.get('normal-before',{}).get('normal_shape_verified'):continue
                rid='byul-req04-'+token+'-'+slot;claim=ctx._forwarded_claims[slot]
                body={'model':ctx.api_model,'prompt':'hello','max_tokens':1,'temperature':0,'stream':False}
                row={'case_id':'req04-'+slot,'request_id_sha256':sha256_hex(rid.encode()),'claim_sha256':sha256_hex(claim.encode()),
                    'field_kind':ctx._forwarded_header['field_kind'],'operation_sha256':sha256_hex(canonical_json({'method':'POST','path':'/v1/completions','body':body})),
                    'credential_sha256':sha256_hex(ctx.keys['allowed'].encode()),'attempted':False,'outcome':None,
                    'capture_complete':False,'normal_shape_verified':False,'binding_verified':False,
                    'started_at':None,'finished_at':None,'policy_window_verified':False}
                summary['trials'].append(row);replies[slot]=row
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    row.update(attempted=True,started_at=now.isoformat())
                    response,=ctx.cprobe(EXTERNAL,[{'id':row['case_id'],'method':'POST','mode':'json','url':ctx.cprobe.gateway.geturl()+'/v1/completions',
                        'headers':{'Authorization':'Bearer '+ctx.keys['allowed'],'X-Request-Id':rid,ctx._forwarded_header['header_name']:claim},'body':body}],10)
                    row.update(outcome=response['outcome'],capture_complete=response['capture_complete'],binding_verified=True)
                    if response['outcome']==200 and response['capture_complete']:
                        v=strict_json(response['body'].encode(),limit=128*1024);choices=v.get('choices') if type(v) is dict else None
                        row['normal_shape_verified']=(type(v) is dict and v.get('model')==ctx.api_model and type(choices) is list and 1<=len(choices)<=8
                            and all(type(c) is dict and type(c.get('text')) is str for c in choices))
                    if slot!='alternate' and not row['normal_shape_verified']:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_NORMAL_CONTROL_UNVERIFIED')
                except Exception:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_POLICY_WINDOW_UNVERIFIED')
                read_records(slot)
        except Exception:summary['diagnostic_codes'].append('FORWARDED_IDENTITY_PREFLIGHT_UNAVAILABLE')
        projected=correlate(summary['snapshots'],summary['trials']);summary['projection']=projected
        summary['transport']=ctx.cprobe.report();summary['record_readers']={role:r.report() for role,r in readers.items()}
        ctx._forwarded_cleanup=list(summary['transport']['cleanup_errors'])
        for reader in readers.values():
            if reader.report().get('parent_cleanup_unverified'):ctx._forwarded_cleanup.append('FORWARDED_IDENTITY_RECORD_PARENT_CLEANUP_UNVERIFIED')
        ctx._forwarded_gap=bool(summary['diagnostic_codes'] or summary['transport']['collection_gap'] or ctx._forwarded_cleanup)
        data={'forwarded_trials':summary['trials'],'forwarded_records':projected['records'],'forwarded_matches':projected['matches'],
            'forwarded_missing':projected['missing'],'forwarded_conflict_candidates':projected['conflict_candidates'],'forwarded_collection':summary['collection'],
            'forwarded_producer_authenticated':False,'forwarded_principal_identity_verified':False,'forwarded_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in data.items()}, {'attempted':bool(replies.get('normal-before',{}).get('attempted')),
            'succeeded':None,'detail':'HTTP normal shape and declared normalization/consumption claims; actual trusted principal/authorization unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._forwarded_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._forwarded_cleanup)
        return draft
