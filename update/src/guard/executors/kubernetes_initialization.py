"""LIF09 canonical single-stage observation; full experiment lineage remains separate."""
from pathlib import Path
import uuid
from guard.adapters import vllm_initialization_sensor as sensor,vllm_initialization_metadata as metadata
from guard.adapters import vllm_worker_generation_sensor as generation_sensor,vllm_worker_generation_metadata as generation_metadata
from guard.adapters import vllm_worker_initialization_sensor as joint_sensor,vllm_worker_initialization_metadata as joint_metadata
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_initialization import KubernetesInitializationHTTP,PROFILE as HTTP_PROFILE,SLOTS
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .initialization_contract import PROFILE,normalize_policy,stamp


class KubernetesInitializationExecutor(HttpCheckExecutor):
    name='initialization';version='0.3.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='LIF-09' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('INITIALIZATION_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','prepared','kubectl_binary','max_duration_seconds'} or adapter is None:raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            target_hash=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end=normalize_policy(policy_values)
            if prepared['profile']==generation_sensor.PROFILE:
                required={item.key for item in check.required_observations}
                if not {'init_worker_generation_events','init_worker_generation_candidates','init_ready_send_observations'}<=required:raise ValueError
            if (target_hash!=policy_values['init_target_sha256'] or env.ref().sha256!=policy_values['init_environment_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target or prepared['profile'] not in {sensor.PROFILE,joint_sensor.PROFILE,generation_sensor.PROFILE}
                    or sha256_hex(canonical_json(prepared))!=policy_values['init_prepared_sha256'] or not start<=utcnow()<=end or type(env.model.inventory_sha256) is not str or len(env.model.inventory_sha256)!=64):raise ValueError
        except (KeyError,ValueError,TypeError,RuntimeError,OSError):raise ValueError('INITIALIZATION_CONFIGURATION_INVALID') from None
        transport=KubernetesInitializationHTTP(target,env,runner=self.runner,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys={'none':None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._init_prepared=prepared;return self._finish(ctx)

    def collect(self,ctx):
        start,end=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex;reader=None;packet=None;sealed=False;trials=[];diagnostics=[]
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesLifecycleReader(native_target,ctx.env,ctx._init_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            for slot in SLOTS:
                row={'case_id':'lif09-'+slot,'request_id_sha256':sha256_hex(('byul-lif09-'+token+'-'+slot).encode()),
                    'attempted':False,'started_at':None,'finished_at':None,'policy_window_verified':False,
                    'fault_activation_verified':False,'declared_observation_kind':slot.removeprefix('observe-') if slot.startswith('observe-') else None}
                trials.append(row)
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    row.update(attempted=True,started_at=now.isoformat());row.update(ctx.cprobe.perform(slot,'byul-lif09-'+token+'-'+slot))
                    if ctx.policy_values['init_phase'] in {'normal','recovered-normal'} and not row['normal_shape_verified']:diagnostics.append('INITIALIZATION_NORMAL_HTTP_CONTROL_UNAVAILABLE')
                except Exception:diagnostics.append('INITIALIZATION_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:diagnostics.append('INITIALIZATION_POLICY_WINDOW_UNVERIFIED')
        except Exception:diagnostics.append('INITIALIZATION_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('INITIALIZATION_SESSION_SEAL_UNAVAILABLE')
                try:
                    engine=reader.binding['engine'];packet=capture_session(reader,native_target,container=engine['container'],image=engine['image'],
                        prepared=ctx._init_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not packet.get('recorded_files_covered'):diagnostics.append('INITIALIZATION_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('INITIALIZATION_CAPTURE_UNAVAILABLE')
        projection=metadata.project(packet,ctx.policy_values);http=ctx.cprobe.report();read_report=reader.report() if reader else None
        if not projection['correspondence']:
            diagnostics.append('INITIALIZATION_AGGREGATE_CORRESPONDENCE_UNAVAILABLE')
        if any(not row['source_process_binding_observed'] for row in projection['calls']):
            diagnostics.append('INITIALIZATION_RECORDING_PROCESS_BINDING_UNAVAILABLE')
        worker={'events':[],'model_candidates':[],'missing':['worker_startup_profile_not_selected']}
        if ctx._init_prepared['profile']==generation_sensor.PROFILE:
            try:worker=generation_metadata.project(packet,ctx._init_prepared)
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                worker['missing']=['worker_generation_projection_unavailable'];diagnostics.append('INITIALIZATION_WORKER_GENERATION_UNAVAILABLE')
            if not worker.get('generation_events'):diagnostics.append('INITIALIZATION_WORKER_GENERATION_OBSERVATION_UNAVAILABLE')
            if any(code in worker['missing'] for code in ('worker_generation_unique_call_pair_unavailable','worker_ready_pipe_unique_call_pair_unavailable','worker_generation_calls-bound')):
                diagnostics.append('INITIALIZATION_WORKER_GENERATION_PARTIAL')
        if ctx._init_prepared['profile']==joint_sensor.PROFILE:
            try:worker=joint_metadata.project(packet,ctx._init_prepared)
            except (ValueError,KeyError,TypeError,OSError,RuntimeError):
                worker['missing']=['worker_startup_projection_unavailable'];diagnostics.append('INITIALIZATION_WORKER_PROJECTION_UNAVAILABLE')
            if not worker['events']:diagnostics.append('INITIALIZATION_WORKER_OBSERVATION_UNAVAILABLE')
            if any(code in worker['missing'] for code in ('worker_startup_unique_call_pair_unavailable','worker_startup_observer_close_incomplete','worker_startup_observer_call_bound')):
                diagnostics.append('INITIALIZATION_WORKER_PARTIAL_OBSERVATION')
        ctx._init_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._init_cleanup.append('INITIALIZATION_PARENT_CLEANUP_UNVERIFIED')
        ctx._init_gap=bool(diagnostics or http['collection_gap'] or ctx._init_cleanup)
        summary={'profile':PROFILE,'declared_phase':ctx.policy_values['init_phase'],
            'declared_scenario_sha256':ctx.policy_values['init_scenario_sha256'],'trials':trials,'native':packet,'projection':projection,
            'transport':http,'reader':read_report,'worker_startup':worker,'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,
            'fixture':{'profile':PROFILE},'failure_control_implemented':False,'failure_activation_verified':False,
            'cross_phase_lineage_verified':False,'whole_scope_complete':False,'actual_worker_gpu_release_verified':False}
        observations={'init_worker_generation_events':worker.get('generation_events',[]),'init_worker_generation_candidates':worker.get('generation_candidates',[]),
            'init_ready_send_observations':worker.get('ready_sends',[]),'init_worker_startup_events':worker['events'],'init_worker_startup_candidates':worker['model_candidates'],
            'init_worker_startup_missing':worker['missing'],'init_observation_trials':trials,'init_native_samples':projection['samples'],'init_native_calls':projection['calls'],
            'init_correspondence':projection['correspondence'],'init_missing':projection['missing'],'init_producer_authenticated':False,
            'init_all_preparation_verified':False,'init_service_gate_verified':False,'init_cross_phase_lineage_verified':False,'init_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in observations.items()},{'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Single declared startup stage: same-process readiness/aggregate facts, bounded HTTP admission samples; independent failure, full model readiness and repaired launch lineage unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._init_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._init_cleanup);return draft
