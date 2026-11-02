"""OBS02 closed health/request/native comparison using the existing session boundary."""
from __future__ import annotations
import re
import uuid
from guard.adapters.vllm_observation_state import COMPONENTS,project
from guard.adapters import vllm_attempt_sensor as attempt_sensor,vllm_request_output_sensor as output_sensor
from guard.models import canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import OBSERVATION_STATE_PROFILE
from .kubernetes_request_termination import KubernetesRequestTerminationExecutor,_time
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session

PROFILE='kubernetes-observation-state-v1'
POLICY_TYPES={'observation_state_environment_sha256':'str','observation_state_target_sha256':'str',
    'observation_state_validity_start':'str','observation_state_validity_end':'str',
    'observation_state_required_components':'list[str]'}
OBSERVATIONS={'state_http_trials':'list','state_native_bindings':'list','state_native_events':'list',
    'state_asgi_events':'list','state_output_events':'list','state_output_links':'list',
    'state_component_status':'list','state_conflict_candidates':'list','state_scope_complete':'bool',
    'independent_worker_state_verified':'bool','state_semantics_verified':'bool'}
SLOTS=('health-before','normal-before','disconnect','normal-after','health-after')


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        if any(type(values[k]) is not str or re.fullmatch('[0-9a-f]{64}',values[k]) is None
                for k in ('observation_state_environment_sha256','observation_state_target_sha256')):raise ValueError
        start,end=(_time(values[k]) for k in ('observation_state_validity_start','observation_state_validity_end'))
        required=values['observation_state_required_components']
        if (not 0<(end-start).total_seconds()<=600 or type(required) is not list or not 1<=len(required)<=len(COMPONENTS)
                or any(type(v) is not str or v not in COMPONENTS for v in required) or len(set(required))!=len(required)):raise ValueError
        return start,end
    except (KeyError,TypeError,ValueError):raise ValueError('OBSERVATION_STATE_POLICY_INVALID') from None


class KubernetesObservationStateExecutor(KubernetesRequestTerminationExecutor):
    name='observation_state';version='0.1.0';check_id='OBS-02';collection_profile=PROFILE;policy_prefix='observation_state'
    policy_normalizer=staticmethod(normalize_policy);transport_profile=OBSERVATION_STATE_PROFILE
    supported_native_profiles=KubernetesRequestTerminationExecutor.supported_native_profiles|{attempt_sensor.PROFILE,output_sensor.PROFILE}

    def _consumer(self,check):
        if type(self) is KubernetesObservationStateExecutor:
            from .kubernetes_observation_composite import PROFILE as COMPOSITE_PROFILE,KubernetesObservationCompositeExecutor
            if check.id=='OBS-02' and getattr(check,'collection_profile',None)==COMPOSITE_PROFILE:
                return KubernetesObservationCompositeExecutor(self.cfg,runner=self.runner,environ=self._environ,
                    expected_target_sha256=self.expected_target_sha256)
        return super()._consumer(check)

    def project_native(self,ctx,packet,trials):
        """Project the validated packet before its redacted artifact summary."""
        return project(packet,trials,ctx.policy_values['observation_state_required_components'])

    def after_trial(self,ctx,row):
        """Optional bounded follow-up collection; the base state procedure has none."""
        return None

    def collect(self,ctx):
        start,end=self.policy_normalizer(ctx.policy_values);token=uuid.uuid4().hex
        summary={'profile':PROFILE,'trials':[],'native':None,'diagnostic_codes':[],'fixture':{'profile':PROFILE},
            'producer_authenticated':False,'worker_identity_verified':False,'state_semantics_verified':False,'whole_scope_complete':False,
            'scope':'HTTP availability, normal output shape, client close and source-correlated native facts; no independent worker/state truth'}
        target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),
            'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        reader=None;packet=None;bycase={};sealed=False
        try:
            reader=KubernetesLifecycleReader(target,ctx.env,ctx._termination_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120))
            reader.snapshot()
            for slot in SLOTS:
                if slot=='disconnect' and not bycase.get('normal-before',{}).get('normal_shape_verified'):continue
                health=slot.startswith('health-');close=slot=='disconnect';rid='byul-obs02-'+token+'-'+slot
                body=None if health else {'model':ctx.api_model,'prompt':'hello','max_tokens':8 if close else 1,'temperature':0,'stream':close}
                row={'case_id':'obs02-'+slot,'method':'GET' if health else 'POST','request_id_sha256':sha256_hex(rid.encode()),
                    'request_body_sha256':sha256_hex(canonical_json(body)),'attempted':False,'outcome':None,
                    'binding_verified':False,'capture_complete':False,'normal_shape_verified':False,'http_available':None,
                    'expected_client_close':False,'started_at':None,'finished_at':None,'policy_window_verified':False}
                summary['trials'].append(row);bycase[slot]=row
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    request={'id':row['case_id'],'method':row['method'],'mode':'sse' if close else 'json',
                        'url':ctx.engine_url+('/health' if health else '/v1/completions'),'headers':{'X-Request-Id':rid},'body':body}
                    if close:request['read_profile']={'profile':'slow-reader-v1','chunk_bytes':1,'interval_ms':1,'close_after_reads':1}
                    row.update(attempted=True,started_at=now.isoformat())
                    response,=ctx.cprobe('kubernetes-probe',[request],ctx.timeout)
                    text=response.get('body');complete=(response.get('capture_complete') is True and response.get('eof') is True
                        and response.get('utf8_valid') is True and response.get('body_truncated') is False
                        and type(text) is str and type(response.get('body_bytes')) is int
                        and response['body_bytes']==len(text.encode()) and response.get('body_sha256')==sha256_hex(text.encode()))
                    row.update(outcome=response.get('outcome'),capture_complete=complete,binding_verified=True)
                    if health:
                        row['http_available']=bool(complete and type(response.get('outcome')) is int and response['outcome']==200)
                        if not complete:summary['diagnostic_codes'].append('OBSERVATION_STATE_HEALTH_CAPTURE_UNVERIFIED')
                    elif close:
                        row['expected_client_close']=any(r.get('id')==row['case_id'] and r.get('expected_client_close') is True for r in ctx.cprobe.report()['requests'])
                        if not row['expected_client_close']:summary['diagnostic_codes'].append('OBSERVATION_STATE_CLIENT_CLOSE_UNVERIFIED')
                    else:
                        if response.get('outcome')==200 and complete:
                            value=strict_json(text.encode(),limit=128*1024);choices=value.get('choices') if type(value) is dict else None
                            row['normal_shape_verified']=(type(value) is dict and value.get('model')==ctx.api_model
                                and type(choices) is list and 1<=len(choices)<=8
                                and all(type(c) is dict and type(c.get('text')) is str for c in choices))
                        if not row['normal_shape_verified']:summary['diagnostic_codes'].append('OBSERVATION_STATE_NORMAL_CONTROL_UNVERIFIED')
                except Exception:summary['diagnostic_codes'].append('OBSERVATION_STATE_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=_time(row['started_at'])<=_time(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:summary['diagnostic_codes'].append('OBSERVATION_STATE_POLICY_WINDOW_UNVERIFIED')
                    try:self.after_trial(ctx,row)
                    except Exception:summary['diagnostic_codes'].append('OBSERVATION_STATE_FOLLOWUP_UNAVAILABLE')
        except Exception:summary['diagnostic_codes'].append('OBSERVATION_STATE_NATIVE_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session()['sealed']
                except Exception:sealed=False
                if not sealed:summary['diagnostic_codes'].append('OBSERVATION_STATE_SEAL_UNVERIFIED')
                try:
                    engine=reader.binding['engine']
                    packet=capture_session(reader,target,container=engine['container'],image=engine['image'],prepared=ctx._termination_prepared,
                        owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                except Exception:summary['diagnostic_codes'].append('OBSERVATION_STATE_CAPTURE_UNAVAILABLE')
                summary['native']={'sealed':sealed,'session_sha256':sha256_hex(ctx._termination_prepared['session'].encode()),
                    'recorded_files_covered':bool(packet and packet['recorded_files_covered']),
                    'live_census_stable':bool(packet and packet['live_census_stable']),'transport':reader.report(),
                    'recordings':[{'pid':r['pid'],'start_ticks':r['start_ticks'],'captured':r['capture'] is not None,
                        'capture_sha256':r['capture']['provenance']['capture_sha256'] if r['capture'] else None,
                        'image_digest_verified':r['capture']['image_digest_verified'] if r['capture'] else False}
                        for r in packet['recordings']] if packet else []}
        projected=self.project_native(ctx,packet,summary['trials'])
        for stage,normal in (('health-before','normal-before'),('health-after','normal-after')):
            h=bycase.get(stage,{});n=bycase.get(normal,{})
            if h.get('http_available') is True and n.get('attempted') and not n.get('normal_shape_verified'):
                projected['conflict_candidates'].append({'kind':'health_available_inference_control_unverified',
                    'health_case_id':h['case_id'],'inference_case_id':n['case_id'],'inference_outcome':n['outcome'],
                    'independent_worker_failure_verified':False,'confirmed_violation':False})
        summary['projection']=projected;summary['http_transport']=ctx.cprobe.report()
        summary['diagnostic_codes']=sorted(set(summary['diagnostic_codes']+projected['diagnostic_codes']))
        if packet and not packet['recorded_files_covered']:summary['diagnostic_codes'].append('OBSERVATION_STATE_NATIVE_COVERAGE_UNVERIFIED')
        gaps={'OBSERVATION_STATE_NATIVE_PREFLIGHT_UNAVAILABLE','OBSERVATION_STATE_PROBE_UNAVAILABLE','OBSERVATION_STATE_SEAL_UNVERIFIED',
            'OBSERVATION_STATE_CAPTURE_UNAVAILABLE','OBSERVATION_STATE_POLICY_WINDOW_UNVERIFIED','OBSERVATION_STATE_CLIENT_CLOSE_UNVERIFIED',
            'OBSERVATION_STATE_HEALTH_CAPTURE_UNVERIFIED','OBSERVATION_STATE_NORMAL_CONTROL_UNVERIFIED',
            'OBSERVATION_STATE_NATIVE_COVERAGE_UNVERIFIED','OBSERVATION_STATE_NATIVE_UNAVAILABLE','OBSERVATION_STATE_EVENT_BOUND_REACHED',
            'OBSERVATION_STATE_FOLLOWUP_UNAVAILABLE'}
        ctx._termination_gap=bool(gaps.intersection(summary['diagnostic_codes']) or summary['http_transport']['collection_gap'])
        ctx._termination_cleanup=list(summary['http_transport']['cleanup_errors'])
        if reader is not None and reader.report().get('parent_cleanup_unverified'):
            ctx._termination_cleanup.append('OBSERVATION_STATE_NATIVE_PARENT_CLEANUP_UNVERIFIED');ctx._termination_gap=True
        values={'state_http_trials':summary['trials'],'state_native_bindings':projected['bindings'],'state_native_events':projected['events'],
            'state_asgi_events':projected['asgi_events'],'state_output_events':projected['output_events'],'state_output_links':projected['output_links'],
            'state_component_status':projected['component_status'],'state_conflict_candidates':projected['conflict_candidates'],
            'state_scope_complete':False,'independent_worker_state_verified':False,'state_semantics_verified':False}
        normal={'attempted':bool(bycase.get('normal-before',{}).get('attempted')),'succeeded':None,
            'detail':'HTTP/normal shape and client close remain separate; independent worker and enterprise state semantics unavailable.'}
        return {k:{'value':v,'collected':True} for k,v in values.items()},normal,[],summary
