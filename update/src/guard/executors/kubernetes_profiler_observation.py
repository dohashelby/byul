"""OBS02 profiler facts and existing closed normal HTTP controls, no profiler control."""
import re
from guard.adapters import vllm_profiler_sensor as sensor,vllm_profiler_metadata as metadata
from guard.models import canonical_json,sha256_hex
from .kubernetes_observation_state import KubernetesObservationStateExecutor,normalize_policy as normalize_base,POLICY_TYPES as BASE_POLICY,OBSERVATIONS as BASE_OBSERVATIONS

PROFILE='kubernetes-profiler-state-observation-v1'
POLICY_TYPES={**BASE_POLICY,'profiler_prepared_sha256':'str','profiler_backend_kind':'str','profiler_required_ranks':'list[str]'}
OBSERVATIONS={**BASE_OBSERVATIONS,'profiler_native_samples':'list','profiler_worker_calls':'list','profiler_rank_status':'list',
 'profiler_conflict_candidates':'list','profiler_missing':'list','profiler_backend_collection_stopped_verified':'bool',
 'profiler_trace_output_completed_verified':'bool','profiler_cross_process_rpc_verified':'bool','profiler_scope_complete':'bool'}


def normalize_policy(values):
    try:
        if type(values) is not dict or set(values)!=set(POLICY_TYPES):raise ValueError
        start,end=normalize_base({key:values[key] for key in BASE_POLICY})
        if type(values['profiler_prepared_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',values['profiler_prepared_sha256']) is None or values['profiler_backend_kind']!='torch':raise ValueError
        ranks=values['profiler_required_ranks']
        if type(ranks) is not list or not 1<=len(ranks)<=64 or any(type(r) is not str or re.fullmatch('0|[1-9][0-9]?',r) is None or int(r)>63 for r in ranks) or len(set(ranks))!=len(ranks):raise ValueError
        return start,end
    except (KeyError,ValueError,TypeError):raise ValueError('PROFILER_OBSERVATION_POLICY_INVALID') from None


class KubernetesProfilerObservationExecutor(KubernetesObservationStateExecutor):
    name='profiler_observation';version='0.1.0';collection_profile=PROFILE
    supported_native_profiles=frozenset({sensor.PROFILE});policy_normalizer=staticmethod(normalize_policy)

    def validate_policy(self,check,values):
        from .profiler_lifecycle_cpu import PROFILE as CPU_PROFILE
        if check.id=='LIF-14' and getattr(check,'collection_profile',None)==CPU_PROFILE:
            if values:raise ValueError('PROFILER_CPU_POLICY_UNSUPPORTED')
            return
        return super().validate_policy(check,values)

    def run(self,check,env,policy_values,adapter,workdir):
        from .profiler_lifecycle_cpu import PROFILE as CPU_PROFILE,ProfilerLifecycleCPUExecutor
        if check.id=='LIF-14' and getattr(check,'collection_profile',None)==CPU_PROFILE:
            self.validate_policy(check,policy_values)
            return ProfilerLifecycleCPUExecutor(self.cfg).run(check,env,policy_values,adapter,workdir)
        self.validate_policy(check,policy_values)
        from guard.collectors.lifecycle import load_prepared
        try:
            prepared=load_prepared(self.cfg['prepared'])
            if sha256_hex(canonical_json(prepared))!=policy_values['profiler_prepared_sha256']:raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('PROFILER_OBSERVATION_PREPARED_UNAVAILABLE') from None
        return super().run(check,env,policy_values,adapter,workdir)

    def project_native(self,ctx,packet,trials):
        result=super().project_native(ctx,packet,trials)
        result['profiler_projection']=metadata.project(packet,ctx.policy_values['profiler_required_ranks'])
        return result

    def collect(self,ctx):
        observations,normal,challenges,summary=super().collect(ctx)
        projection=summary['projection'].pop('profiler_projection')
        summary['profile']=PROFILE;summary['profiler_projection']=projection
        summary['scope']='Existing closed HTTP normal controls and same-process source-bound profiler adapter/flag facts; no profiler start/stop requests, independent backend/trace transfer or cross-process API/worker causality.'
        if (not projection['samples'] or 'required_profiler_worker_rank_unavailable' in projection['missing']
            or any(reason in projection['missing'] for reason in ('profiler_call_pair_unavailable','profiler_source_process_call_binding_unavailable'))):
            summary['diagnostic_codes'].append('PROFILER_OBSERVATION_REQUIRED_FACTS_UNAVAILABLE');ctx._termination_gap=True
        values={'profiler_native_samples':projection['samples'],'profiler_worker_calls':projection['worker_calls'],'profiler_rank_status':projection['rank_status'],
            'profiler_conflict_candidates':projection['conflict_candidates'],'profiler_missing':projection['missing'],
            'profiler_backend_collection_stopped_verified':False,'profiler_trace_output_completed_verified':False,
            'profiler_cross_process_rpc_verified':False,'profiler_scope_complete':False}
        observations.update({key:{'value':value,'collected':True} for key,value in values.items()})
        normal['succeeded']=None;normal['detail']='HTTP controls and vLLM profiler adapter/flag facts; backend collection/output/next-start and state semantics remain independently unverified.'
        return observations,normal,challenges,summary
