"""Draft LIF08 canonical product: passive mp facts, no fault-control protocol."""
import uuid
from guard.collectors.file import load_bounded_json
from guard.adapters import vllm_rank_failure_sensor as sensor,vllm_rank_failure_metadata as metadata
from guard.adapters import vllm_release_observation_sensor as release_observation,vllm_lifecycle_profiles as profiles
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.kubernetes_rank_failure import KubernetesRankFailureHTTP,PROFILE as HTTP_PROFILE,SLOTS
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .rank_failure_contract import PROFILE,normalize_policy,stamp


ACTIVATION_OBSERVATIONS = {'activation_steps', 'activation_violations', 'activation_missing', 'activation_scope_complete'}


def _activation_observations(projection, trials, diagnostics, packet):
    calls = {(row['recording'], row['pid'], row['start_ticks'], row['call_sha256']) for row in projection['calls']
        if row['point'] == 'MultiprocExecutor.execute_model' and row['phase'] == 'RETURN'
        and row['source_process_binding_observed']}
    native = [row for row in projection['samples']
        if row['point'] == 'MultiprocExecutor.execute_model' and row['phase'] == 'RETURN'
        and row['returned_kind'] == 'value'
        and (row['recording'], row['pid'], row['start_ticks'], row['call_sha256']) in calls]
    source_recordings = {}
    for index, recording in enumerate((packet or {}).get('recordings', [])):
        capture = recording.get('capture') or {}
        summary, provenance = capture.get('summary', {}), capture.get('provenance', {})
        if (summary.get('recording_closed') is True and summary.get('discarded_tail_bytes') == 0
                and provenance.get('owner_binding_verified') is True
                and metadata.fingerprint(provenance.get('capture_sha256'))):
            source_recordings[index] = provenance['capture_sha256']
    attributed = [row for row in native if row['recording'] in source_recordings]
    controls = [row for row in trials if row['case_id'] in {'lif08-normal-before', 'lif08-normal-after'}
        and row.get('binding_verified') is True and row.get('policy_window_verified') is True
        and row.get('capture_complete') is True and row.get('normal_shape_verified') is True]
    # These are independently observed facts. The native return did not identify the client control.
    steps = [{'step': 'native-rank-executor-value-return', 'source_recording_sha256': source_recordings[row['recording']],
              **{key: row[key] for key in ('call_sha256', 'source_sha256', 'recording', 'seq',
                                         'operation_started_ns', 'operation_finished_ns')}} for row in attributed]
    steps += [{'step': 'direct-rank-normal-control', **row} for row in controls]
    missing = set(diagnostics)
    missing.update(f"{row['kind']}:{row['reason']}" if row.get('kind') is not None else row['reason']
                   for row in projection['missing'])
    if len(attributed) != len(native):
        missing.add('RANK_NATIVE_EXECUTOR_VALUE_SOURCE_UNVERIFIED')
    if not attributed:
        missing.add('RANK_NATIVE_EXECUTOR_VALUE_RETURN_UNAVAILABLE')
    if len(controls) != 2:
        missing.add('RANK_BOUNDED_NORMAL_CONTROL_PAIR_UNAVAILABLE')
    return {'activation_steps': steps, 'activation_violations': [], 'activation_missing': sorted(missing),
            'activation_scope_complete': False}


class KubernetesRankFailureExecutor(HttpCheckExecutor):
    name='rank_failure';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None,common_records=False,
                 expected_common_files=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256
        self.common_records=common_records
        self.expected_common_files=expected_common_files
        if common_records:self.version='0.1.0-common-input'

    def validate_policy(self,check,values):
        if check.id!='LIF-08' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('RANK_FAILURE_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        if self.common_records:
            from .rank_common_input import run_records
            self.validate_policy(check,policy_values)
            return run_records(check,env,policy_values,self.cfg,workdir,expected_target_sha256=self.expected_target,
                               expected_files=self.expected_common_files)
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','prepared','kubectl_binary','max_duration_seconds'} or adapter is None:raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            target=load_bounded_json(self.cfg['target'])
            target_hash=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end=normalize_policy(policy_values)
            if (target_hash!=policy_values['rank_target_sha256'] or env.ref().sha256!=policy_values['rank_environment_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target or prepared['profile'] not in {sensor.PROFILE,release_observation.RANK_PROFILE}
                    or sha256_hex(canonical_json(prepared))!=policy_values['rank_prepared_sha256'] or not start<=utcnow()<=end):raise ValueError
        except (KeyError,ValueError,TypeError,RuntimeError,OSError):raise ValueError('RANK_FAILURE_CONFIGURATION_INVALID') from None
        transport=KubernetesRankFailureHTTP(target,env,runner=self.runner,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys={'none':None},secrets=[],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._rank_prepared=prepared;return self._finish(ctx)

    def collect(self,ctx):
        start,end=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex;reader=None;packet=None;sealed=False;trials=[];diagnostics=[]
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesLifecycleReader(native_target,ctx.env,ctx._rank_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            for slot in SLOTS:
                if slot.startswith('observe-') and not trials[0].get('normal_shape_verified'):continue
                row={'case_id':'lif08-'+slot,'request_id_sha256':sha256_hex(('byul-lif08-'+token+'-'+slot).encode()),
                    'attempted':False,'started_at':None,'finished_at':None,'policy_window_verified':False,
                    'fault_activation_verified':False,'declared_observation_kind':slot.removeprefix('observe-') if slot.startswith('observe-') else None}
                trials.append(row)
                try:
                    now=utcnow()
                    if not start<=now<=end:raise ValueError
                    row.update(attempted=True,started_at=now.isoformat());row.update(ctx.cprobe.perform(slot,'byul-lif08-'+token+'-'+slot))
                    if slot in {'normal-before','normal-after'} and not row['normal_shape_verified']:diagnostics.append('RANK_NORMAL_HTTP_CONTROL_UNAVAILABLE')
                except Exception:diagnostics.append('RANK_FAILURE_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=utcnow().isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:diagnostics.append('RANK_FAILURE_POLICY_WINDOW_UNVERIFIED')
        except Exception:diagnostics.append('RANK_FAILURE_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('RANK_FAILURE_SESSION_SEAL_UNAVAILABLE')
                try:
                    engine=reader.binding['engine'];packet=capture_session(reader,native_target,container=engine['container'],image=engine['image'],
                        prepared=ctx._rank_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not packet.get('recorded_files_covered'):diagnostics.append('RANK_FAILURE_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('RANK_FAILURE_CAPTURE_UNAVAILABLE')
        projection=metadata.project(packet,ctx.policy_values,source_contracts=profiles.source_hashes(ctx._rank_prepared['profile']),
            runtime_contracts=release_observation.RANK_RUNTIME_CODE_CONTRACTS
                if ctx._rank_prepared['profile']==release_observation.RANK_PROFILE else None);http=ctx.cprobe.report();read_report=reader.report() if reader else None
        if not any(c['point']=='MultiprocExecutor.execute_model' and c['source_process_binding_observed'] for c in projection['calls']):
            diagnostics.append('SELECTED_MP_NATIVE_EXECUTION_UNOBSERVED')
        ctx._rank_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._rank_cleanup.append('RANK_FAILURE_PARENT_CLEANUP_UNVERIFIED')
        ctx._rank_gap=bool(diagnostics or http['collection_gap'] or ctx._rank_cleanup)
        summary={'profile':PROFILE,'trials':trials,'native':packet,'projection':projection,'transport':http,'reader':read_report,
            'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,'fixture':{'profile':PROFILE},'fault_control_implemented':False,
            'faults_injected':False,'four_fault_activation_verified':False,'whole_scope_complete':False,'actual_worker_gpu_release_verified':False}
        observations={'rank_observation_trials':trials,'rank_native_samples':projection['samples'],'rank_native_calls':projection['calls'],
            'rank_failure_candidates':projection['source_failure_candidates'],'rank_fault_cases':projection['fault_cases'],'rank_missing':projection['missing'],
            'rank_producer_authenticated':False,'rank_normal_all_ranks_verified':False,'rank_upper_client_receipt_verified':False,'rank_scope_complete':False}
        if ACTIVATION_OBSERVATIONS <= {item.key for item in ctx.check.required_observations}:
            observations.update(_activation_observations(projection, trials, diagnostics, packet))
        return {k:{'value':v,'collected':True} for k,v in observations.items()},{'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Closed normal probes and selected mp source facts; four independent fault activations, all-rank normal computation and upper receipt unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._rank_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._rank_cleanup);return draft
