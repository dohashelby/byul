"""OBS01 owned public probes and separately collected, untrusted audit records."""
from pathlib import Path
import re
import time
import uuid
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import capture_pipes,strict_json
from guard.runtime.kubernetes_http import KubernetesHTTP,REQUIRED_EVENT_PROFILE
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader,KubernetesCaptureError
from .owned_capture import _FILE_READER,MAX_BYTES
from .observation_contract import PROFILE,POLICY_TYPES,OBSERVATIONS,normalize_policy,validate_record,correlate


class RequiredEventReader(KubernetesLifecycleReader):
    def __init__(self,transport,target,session,*,binary,duration,runner=None):
        if target.get('lab') is not True:raise KubernetesCaptureError()
        self._initialize(transport.binding,target,{'session':session},binary=binary,duration=duration,runner=runner)

    def _program(self,container,argv,max_bytes):
        path='/guard-observation/data/'+self.session+'/required-events.json'
        if (container!=self.binding['engine']['container'] or argv!=['python3','-c',_FILE_READER,path,str(MAX_BYTES)]
                or max_bytes!=2*MAX_BYTES):raise KubernetesCaptureError()
        return 'file'

    def report(self):
        result=super().report();result['profile']=PROFILE
        result['programs']={'file':result['programs']['file']}
        result.update(producer_authenticated=False,source_code_verified=False,actor_identity_verified=False)
        return result


class KubernetesRequiredEventExecutor(HttpCheckExecutor):
    name='required_event_record';version='0.1.0';requires_roles=True;requires_route_scope=False;functional_via='engine'
    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='OBS-01' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('OBSERVATION_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            if (set(self.cfg)-{'target','roles','kubectl_binary','max_duration_seconds'} or adapter is None
                    or set(self.roles())!={'allowed','denied'}
                    or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise ValueError
            keys=self.keys()
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target));start,end=normalize_policy(policy_values)
            if (target.get('lab') is not True or digest!=policy_values['observation_target_sha256']
                    or env.ref().sha256!=policy_values['observation_environment_sha256']
                    or self.expected_target is not None and self.expected_target!=digest or not start<=utcnow()<=end):raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('OBSERVATION_CONFIGURATION_INVALID') from None
        transport=KubernetesHTTP(target,env,duration=self.cfg.get('max_duration_seconds',120),
            binary=self.cfg.get('kubectl_binary','kubectl'),runner=self.runner,profile=REQUIRED_EVENT_PROFILE)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,
            allow_mutating=False,keys=keys,secrets=[v for v in keys.values() if v],cprobe=transport,workdir=workdir,started=utcnow())
        return self._finish(ctx)

    def collect(self,ctx):
        values=ctx.policy_values;start,end=normalize_policy(values)
        summary={'profile':PROFILE,'trials':[],'snapshots':[],'collection':[],'diagnostic_codes':[],
            'fixture':{'profile':PROFILE},'producer_authenticated':False,'actor_identity_verified':False,
            'independent_event_source_verified':False,'approval_or_deployment_source_verified':False,'whole_scope_complete':False}
        reader=None;token=uuid.uuid4().hex
        def read_records(stage):
            row={'stage':stage,'collected':False,'file_bytes_sha256':None,'file_bytes':None,'owner_binding_verified':False,
                'collected_monotonic_ns':time.monotonic_ns()}
            try:
                if not start<=utcnow()<=end:raise ValueError
                role=reader.binding['engine']
                raw,provenance=reader.read_file(reader,ctx.target,container=role['container'],image=role['image'],
                    path='/guard-observation/data/'+values['observation_session']+'/required-events.json')
                packet=validate_record(strict_json(raw,limit=MAX_BYTES),environment_ref=ctx.env.ref().model_dump(mode='json'),
                    target_sha256=values['observation_target_sha256'],session=values['observation_session'],
                    expected_source_sha256=values['observation_expected_source_sha256'],forbidden_values=ctx.secrets)
                if provenance.get('owner_binding_verified') is not True or not start<=utcnow()<=end:raise ValueError
                row.update(collected=True,file_bytes_sha256=provenance['capture_sha256'],file_bytes=provenance['bytes'],
                    file_identity=provenance['file_identity'],owner_binding_verified=True,collected_monotonic_ns=time.monotonic_ns())
                summary['snapshots'].append({'stage':stage,'packet':packet,'collected_monotonic_ns':row['collected_monotonic_ns']})
            except Exception:summary['diagnostic_codes'].append('OBSERVATION_RECORD_UNAVAILABLE')
            summary['collection'].append(row)
        try:
            reader=RequiredEventReader(ctx.cprobe,ctx.target,values['observation_session'],
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120),runner=self.runner or capture_pipes)
            reader.snapshot()
            for slot,role in (('before','allowed'),('denied','denied'),('after','allowed')):
                rid='byul-obs01-'+token+'-'+slot
                body={'model':ctx.cprobe.api_model,'messages':[{'role':'user','content':'hello'}],'max_tokens':1,'stream':False}
                row={'case_id':'obs01-'+slot,'role':role,'request_id_sha256':sha256_hex(rid.encode()),
                    'body_sha256':sha256_hex(canonical_json(body)),'attempted':False,'outcome':None,
                    'binding_verified':False,'expected_response_observed':False,'normal_shape_verified':False,
                    'started_at':None,'finished_at':None,'finished_monotonic_ns':time.monotonic_ns()}
                summary['trials'].append(row)
                if slot=='denied' and not summary['trials'][0]['normal_shape_verified']:continue
                try:
                    if not start<=utcnow()<=end:raise ValueError
                    row.update(attempted=True,started_at=utcnow().isoformat())
                    response,=ctx.cprobe('kubernetes-probe',[{'id':row['case_id'],'mode':'json','method':'POST',
                        'url':ctx.engine_url+'/v1/chat/completions','headers':{'X-Request-Id':rid,'Authorization':'Bearer '+ctx.keys[role]},'body':body}],10)
                    row.update(outcome=response.get('outcome'),binding_verified=True)
                    complete=response.get('capture_complete') is True and response.get('eof') is True and response.get('utf8_valid') is True
                    if role=='allowed' and response.get('outcome')==200 and complete:
                        value=strict_json(response.get('body','').encode(),limit=128*1024)
                        choices=value.get('choices') if type(value) is dict else None
                        row['normal_shape_verified']=(value.get('model')==ctx.cprobe.api_model and type(choices) is list and len(choices)==1
                            and type(choices[0]) is dict and type(choices[0].get('message')) is dict
                            and type(choices[0]['message'].get('content')) is str and bool(choices[0]['message']['content']))
                    row['expected_response_observed']=bool(row['normal_shape_verified'] if role=='allowed' else complete and response.get('outcome') in (401,403))
                    if not row['expected_response_observed']:summary['diagnostic_codes'].append('OBSERVATION_PROBE_CONTROL_UNVERIFIED')
                except Exception:summary['diagnostic_codes'].append('OBSERVATION_PROBE_UNAVAILABLE')
                finally:
                    row['finished_monotonic_ns']=time.monotonic_ns()
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    if not start<=utcnow()<=end:summary['diagnostic_codes'].append('OBSERVATION_POLICY_WINDOW_UNVERIFIED')
                if slot=='before':read_records('initial')
            read_records('final')
        except Exception:summary['diagnostic_codes'].append('OBSERVATION_PREFLIGHT_UNAVAILABLE')
        projected=correlate(summary['snapshots'],summary['trials'],values,run_sha256=sha256_hex(ctx.cprobe.binding['run_id'].encode()))
        summary['projection']=projected;summary['transport']=ctx.cprobe.report()
        if reader is not None:summary['record_transport']=reader.report()
        closed=bool(summary['collection'] and summary['collection'][-1]['collected'] and summary['snapshots']
            and summary['snapshots'][-1]['packet']['source_ended'] and summary['snapshots'][-1]['packet']['dropped_events']==0)
        summary['collection_recovered_after_initial_gap']=bool(len(summary['collection'])==2
            and not summary['collection'][0]['collected'] and summary['collection'][1]['collected'])
        hard={'OBSERVATION_PROBE_UNAVAILABLE','OBSERVATION_PROBE_CONTROL_UNVERIFIED','OBSERVATION_POLICY_WINDOW_UNVERIFIED','OBSERVATION_PREFLIGHT_UNAVAILABLE'}
        ctx._obs_gap=bool(projected['missing'] or not closed or hard.intersection(summary['diagnostic_codes'])
            or summary['transport'].get('collection_gap') or reader is not None and reader.gap)
        ctx._obs_cleanup=list(summary['transport'].get('cleanup_errors',[]))
        if reader is not None and reader._parent_cleanup_failures:ctx._obs_cleanup.append('OBSERVATION_RECORD_PARENT_CLEANUP_UNVERIFIED')
        observations={'required_event_trials':summary['trials'],'required_event_records':projected['records'],
            'required_event_matches':projected['matches'],'required_event_missing':projected['missing'],
            'required_event_conflict_candidates':projected['conflict_candidates'],'required_event_collection':summary['collection'],
            'required_event_records_collected':bool(summary['snapshots']),'required_event_producer_authenticated':False,
            'required_event_independent_trigger_verified':False,'required_event_scope_complete':False}
        normal_rows=[r for r in summary['trials'] if r['role']=='allowed']
        normal={'attempted':any(r['attempted'] for r in normal_rows),'succeeded':bool(len(normal_rows)==2 and all(r['normal_shape_verified'] for r in normal_rows)),
            'detail':'소유 probe의 전후 정상 응답 구조이며 실제 신뢰 주체·승인·worker 실행 완료 증명이 아니다.'}
        summary['diagnostic_codes']=sorted(set(summary['diagnostic_codes']))
        challenges=[{'step':step,'attempted':any(r['attempted'] for r in summary['trials']),
            'observations':{'required_event_scope_complete':False}} for step in ctx.check.challenge_steps]
        return {k:{'value':v,'collected':True} for k,v in observations.items()},normal,challenges,summary

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        ceiling=DataOrigin.EXAMPLE if self.runner is not None or ctx.target['lab'] or ctx.env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED
        draft.data_origin=min(draft.data_origin,ceiling,key=lambda origin:ORIGIN_STRENGTH[origin])
        if ctx._obs_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._obs_cleanup)
        return draft
