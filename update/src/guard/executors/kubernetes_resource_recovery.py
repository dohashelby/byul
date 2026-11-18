"""LIF11 selected resources and explicit absolute deadlines; full recovery lineage separate."""
from pathlib import Path
import uuid
from guard.adapters import vllm_initialization_sensor as initialization,vllm_rank_failure_sensor as rank
from guard.adapters import vllm_initialization_release_sensor as initialization_release,vllm_release_observation_sensor as release_observation
from guard.adapters import resource_recovery_probe as resource_probe
from guard.adapters import resource_group_probe as group_probe
from guard.adapters import gpu_process_probe as gpu_probe
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_recovery_http import KubernetesRecoveryHTTP,PROFILE as HTTP_PROFILE,SLOTS
from .http_common import Ctx,HttpCheckExecutor
from guard.runtime.kubernetes_resource_recovery import KubernetesResourceRecoveryReader
from .lifecycle_session import capture_session
from .resource_recovery_contract import PROFILE,normalize_policy,stamp,validate_selected_policy,project


from .lifecycle_common_input_consumption import LifecycleCommonInputConsumer


class KubernetesResourceRecoveryExecutor(LifecycleCommonInputConsumer, HttpCheckExecutor):
    name='resource_recovery';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='LIF-11' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('RESOURCE_RECOVERY_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','prepared','selection','kubectl_binary','max_duration_seconds','namespace_resources',
                    'node_gpu_processes','node_gpu_containerd_address'} or adapter is None:raise ValueError
            if type(self.cfg.get('namespace_resources',False))is not bool:raise ValueError
            if type(self.cfg.get('node_gpu_processes',False))is not bool:raise ValueError
            if 'node_gpu_containerd_address'in self.cfg and(self.cfg.get('node_gpu_processes')is not True
                    or self.cfg['node_gpu_containerd_address']not in('/run/containerd/containerd.sock','/run/k3s/containerd/containerd.sock')):raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            target_hash=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end,deadline=normalize_policy(policy_values)
            with Path(self.cfg['selection']).open('rb') as stream:selection=strict_json(stream.read(resource_probe.MAX_INPUT+1),limit=resource_probe.MAX_INPUT)
            validate_selected_policy(selection,policy_values)
            expected_profiles=({rank.PROFILE,release_observation.RANK_PROFILE} if policy_values['recovery_branch']=='runtime-failure'
                else {initialization.PROFILE,initialization_release.PROFILE})
            if (target_hash!=policy_values['recovery_target_sha256'] or env.ref().sha256!=policy_values['recovery_environment_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target or prepared['profile'] not in expected_profiles or sha256_hex(canonical_json(selection))!=policy_values['recovery_selection_sha256']
                    or sha256_hex(canonical_json(prepared))!=policy_values['recovery_prepared_sha256'] or not start<=utcnow()<=end or type(env.model.inventory_sha256) is not str or len(env.model.inventory_sha256)!=64):raise ValueError
        except (KeyError,ValueError,TypeError,RuntimeError,OSError):raise ValueError('RESOURCE_RECOVERY_CONFIGURATION_INVALID') from None
        transport=KubernetesRecoveryHTTP(target,env,runner=self.runner,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys={'none':None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._recovery_prepared=prepared;ctx._recovery_selection=selection;return self._finish(ctx)

    def collect(self,ctx):
        start,end,deadline=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex;reader=None;packet=None;sealed=False;trials=[];diagnostics=[];censuses=[];groups=[];gpus=[]
        group_selection={'schema':group_probe.SELECTION_SCHEMA,
            'owners':[{'id':row['id'],'context':row['context']}for row in ctx._recovery_selection['owners']]}
        def collect_group():
            if self.cfg.get('namespace_resources',False):
                row=reader.group_resource_census(group_selection,max_clock_skew_ms=ctx.policy_values['recovery_max_clock_skew_ms']);groups.append(row)
                if (not row.get('collected')or not row.get('owner_binding_verified')or not row.get('clock_window_verified')
                        or (row.get('packet')or{}).get('namespace_scan_complete')is not True):
                    diagnostics.append('RESOURCE_RECOVERY_NAMESPACE_CENSUS_INCOMPLETE')
        def collect_gpu():
            if not self.cfg.get('node_gpu_processes',False):return
            from .node_gpu_process import collect_for_reader
            try:
                address=self.cfg.get('node_gpu_containerd_address')
                row=collect_for_reader(reader,group_selection['owners'],
                    containerd_address=None if address=='/run/containerd/containerd.sock'else address)
                if type(row)is not dict:raise ValueError
                if row.get('collected'):
                    from guard.runtime.gpu_process_source import source as gpu_source
                    receipt=row['receipt'];scope=receipt['selection']
                    if(receipt['engine_binding_sha256']!=sha256_hex(canonical_json(reader.binding['engine']))
                            or scope['owners']!=group_selection['owners']
                            or receipt['packet_sha256']!=sha256_hex(canonical_json(row['packet']))
                            or receipt['program_sha256']!=sha256_hex(gpu_source().encode())):raise ValueError
                    gpu_probe.validate_packet(row['packet'],scope)
            except Exception:
                row={'schema':'guard-owned-node-gpu-process-read/1','collected':False,'owner_binding_verified':False,
                    'clock_window_verified':False,'packet':None,'receipt':None,'diagnostic_codes':['GPU_PROCESS_OWNED_NODE_READ_UNVERIFIED']}
            gpus.append(row)
            if(not row.get('collected')or not row.get('owner_binding_verified')or not row.get('clock_window_verified')
                    or(row.get('packet')or{}).get('host_inventory_complete')is not True):
                diagnostics.append('RESOURCE_RECOVERY_NODE_GPU_CENSUS_INCOMPLETE')
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesResourceRecoveryReader(native_target,ctx.env,ctx._recovery_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            censuses.append(reader.resource_census(ctx._recovery_selection,max_clock_skew_ms=ctx.policy_values['recovery_max_clock_skew_ms']))
            collect_group()
            collect_gpu()
            for slot in SLOTS:
                row={'case_id':'lif11-'+slot,'request_id_sha256':sha256_hex(('byul-lif11-'+token+'-'+slot).encode()),
                    'attempted':False,'started_at':None,'finished_at':None,'policy_window_verified':False,
                    'fault_activation_verified':False,'declared_observation_kind':slot.removeprefix('observe-') if slot.startswith('observe-') else None}
                trials.append(row)
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    row.update(attempted=True,started_at=now.isoformat());row.update(ctx.cprobe.perform(slot,'byul-lif11-'+token+'-'+slot))
                    if ctx.policy_values['recovery_phase']=='restarted-normal' and not row['normal_shape_verified']:diagnostics.append('RESOURCE_RECOVERY_NORMAL_HTTP_CONTROL_UNAVAILABLE')
                except Exception:diagnostics.append('RESOURCE_RECOVERY_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:diagnostics.append('RESOURCE_RECOVERY_POLICY_WINDOW_UNVERIFIED')
        except Exception:diagnostics.append('RESOURCE_RECOVERY_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:censuses.append(reader.resource_census(ctx._recovery_selection,max_clock_skew_ms=ctx.policy_values['recovery_max_clock_skew_ms']))
                except Exception:diagnostics.append('RESOURCE_RECOVERY_FINAL_CENSUS_UNAVAILABLE')
                try:collect_group()
                except Exception:diagnostics.append('RESOURCE_RECOVERY_FINAL_NAMESPACE_CENSUS_UNAVAILABLE')
                collect_gpu()
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('RESOURCE_RECOVERY_SESSION_SEAL_UNAVAILABLE')
                try:
                    engine=reader.binding['engine'];packet=capture_session(reader,native_target,container=engine['container'],image=engine['image'],
                        prepared=ctx._recovery_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not packet.get('recorded_files_covered'):diagnostics.append('RESOURCE_RECOVERY_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('RESOURCE_RECOVERY_CAPTURE_UNAVAILABLE')
        projection=project(censuses,ctx._recovery_selection,ctx.policy_values)
        group_projection=None
        if self.cfg.get('namespace_resources',False):
            try:
                if len(groups)!=2 or any(not r['collected']or not r['owner_binding_verified']or not r['clock_window_verified']for r in groups):raise ValueError
                group_projection=group_probe.compare(groups[0]['packet'],groups[1]['packet'],group_selection)
            except Exception:diagnostics.append('RESOURCE_RECOVERY_NAMESPACE_COMPARISON_UNAVAILABLE')
        gpu_projection=None
        if self.cfg.get('node_gpu_processes',False):
            try:
                if len(gpus)!=2 or any(not r['collected']or not r['owner_binding_verified']or not r['clock_window_verified']for r in gpus):raise ValueError
                scopes=[r['receipt']['selection']for r in gpus]
                if scopes[0]!=scopes[1]or scopes[0]['owners']!=group_selection['owners']:raise ValueError
                gpu_projection=gpu_probe.compare(gpus[0]['packet'],gpus[1]['packet'],scopes[0])
            except Exception:diagnostics.append('RESOURCE_RECOVERY_NODE_GPU_COMPARISON_UNAVAILABLE')
        try:http=ctx.cprobe.report()
        except Exception:
            diagnostics.append('RESOURCE_RECOVERY_HTTP_REPORT_UNAVAILABLE')
            http={'collection_gap':True,'cleanup_errors':['RESOURCE_RECOVERY_HTTP_REPORT_UNAVAILABLE']}
        read_report=None
        if reader is not None:
            try:read_report=reader.report()
            except Exception:
                diagnostics.append('RESOURCE_RECOVERY_READER_REPORT_UNAVAILABLE')
                read_report={'collection_gap':True,'parent_cleanup_unverified':True}
        if any(not row.get('collected') or not row.get('owner_binding_verified') or not row.get('clock_window_verified') for row in censuses) or len(censuses)!=2:
            diagnostics.append('RESOURCE_RECOVERY_CENSUS_BINDING_UNAVAILABLE')
        ctx._recovery_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._recovery_cleanup.append('RESOURCE_RECOVERY_PARENT_CLEANUP_UNVERIFIED')
        ctx._recovery_gap=bool(diagnostics or http['collection_gap'] or ctx._recovery_cleanup)
        ctx._recovery_diagnostic_codes=tuple(diagnostics)
        summary={'profile':PROFILE,'declared_branch':ctx.policy_values['recovery_branch'],'declared_phase':ctx.policy_values['recovery_phase'],
            'declared_scenario_sha256':ctx.policy_values['recovery_scenario_sha256'],'trials':trials,'censuses':censuses,'native':packet,
            'projection':projection,'transport':http,'reader':read_report,'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,
            'fixture':{'profile':PROFILE},'failure_control_implemented':False,'restart_control_implemented':False,'whole_scope_complete':False}
        if self.cfg.get('namespace_resources',False):summary.update(namespace_resource_censuses=groups,namespace_resource_projection=group_projection)
        if self.cfg.get('node_gpu_processes',False):
            from guard.collectors import gpu_process_qualification
            try:qualification=gpu_process_qualification.status(ctx.env.engine.name,ctx.env.engine.version,ctx.env.engine.image_digest)
            except Exception:qualification={'registered':False,'diagnostic_codes':['GPU_PROCESS_QUALIFICATION_DATA_INVALID']}
            summary.update(node_gpu_process_censuses=gpus,node_gpu_process_projection=gpu_projection,
                node_gpu_process_qualification=qualification)
        observations={'recovery_trials':trials,'recovery_resource_censuses':censuses,'recovery_selected_resource_status':projection['rows'],
            'recovery_missing':projection['missing'],'recovery_absolute_deadline_violated':projection['absolute_deadline_violated'],
            'recovery_selected_absence_observed':projection['selected_absence_observed'],'recovery_global_resources_released':False,
            'recovery_next_normal_launch_verified':False,'recovery_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in observations.items()},{'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Explicit selected owner/PID/start and descriptor/inode census; unconditional absolute policy deadline violations separate from missing failure/restart/global release.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._recovery_gap:
            draft.status=ExecStatus.ABORTED
            if draft.abort_reason is None:
                codes=sorted(set(tuple(getattr(ctx,'_recovery_diagnostic_codes',()))+tuple(ctx._recovery_cleanup)))
                draft.abort_reason='; '.join(codes) or 'RESOURCE_RECOVERY_COLLECTION_INCOMPLETE'
        draft.cleanup_errors.extend(ctx._recovery_cleanup);return draft
