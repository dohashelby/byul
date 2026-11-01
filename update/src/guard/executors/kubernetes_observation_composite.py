"""OBS02 two independent, source-bound sessions; no cross-session causal claim."""
from pathlib import Path
from guard.collectors.lifecycle import load_prepared
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from .base import EvidenceDraft
from .kubernetes_observation_state import OBSERVATIONS as BASE_OBSERVATIONS
from .kubernetes_observation_identity import KubernetesObservationIdentityExecutor
from . import observation_identity_contract as identity
from . import kubernetes_profiler_observation as profiler

PROFILE='kubernetes-observation-state-composite-v1'
POLICY_TYPES={**identity.POLICY_TYPES,**profiler.POLICY_TYPES}
OBSERVATIONS={**BASE_OBSERVATIONS,**identity.OBSERVATIONS,**profiler.OBSERVATIONS,
    'state_collection_sessions':'list','state_collection_scope_complete':'bool'}


def normalize_policy(values):
    if type(values) is not dict or set(values)!=set(POLICY_TYPES):
        raise ValueError('OBSERVATION_COMPOSITE_POLICY_INVALID')
    identity.normalize_policy({k:values[k] for k in identity.POLICY_TYPES})
    profiler.normalize_policy({k:values[k] for k in profiler.POLICY_TYPES})


class KubernetesObservationCompositeExecutor:
    name='observation_state';version='0.1.0'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        self.cfg=cfg;self.runner=runner;self.environ=environ;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='OBS-02' or getattr(check,'collection_profile',None)!=PROFILE:
            raise ValueError('OBSERVATION_COMPOSITE_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def _children(self,check,env,values,adapter):
        """Validate both bindings before the first child can send a probe."""
        self.validate_policy(check,values)
        try:
            if (type(self.cfg) is not dict or set(self.cfg)-{'target','identity_prepared','profiler_prepared','kubectl_binary','max_duration_seconds'}
                    or adapter is None or not env.hash_ok() or env.engine.name!='vllm' or env.engine.version!='0.31.0'
                    or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target))
            if (digest!=values['observation_state_target_sha256'] or env.ref().sha256!=values['observation_state_environment_sha256']
                    or self.expected_target is not None and digest!=self.expected_target):raise ValueError
            children=[];sessions=[]
            for label,implementation,contract in (('identity',KubernetesObservationIdentityExecutor,identity),
                    ('profiler',profiler.KubernetesProfilerObservationExecutor,profiler)):
                prepared=load_prepared(self.cfg[label+'_prepared']);sessions.append(prepared['session'])
                if (prepared['profile'] not in implementation.supported_native_profiles
                        or sha256_hex(canonical_json(prepared))!=values[label+'_prepared_sha256']):raise ValueError
                selected={k:values[k] for k in contract.POLICY_TYPES}
                start,end=contract.normalize_policy(selected)
                if not start<=utcnow()<=end:raise ValueError
                cfg={k:v for k,v in self.cfg.items() if k in {'target','kubectl_binary','max_duration_seconds'}}
                cfg['prepared']=self.cfg[label+'_prepared']
                executor=implementation(cfg,runner=self.runner,environ=self.environ,expected_target_sha256=self.expected_target)
                child_check=check.model_copy(update={'collection_profile':contract.PROFILE})
                executor.validate_policy(child_check,selected)
                children.append((label,executor,child_check,selected,prepared))
            if len(set(sessions))!=2:raise ValueError
            return children
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):
            raise ValueError('OBSERVATION_COMPOSITE_CONFIGURATION_INVALID') from None

    def run(self,check,env,policy_values,adapter,workdir):
        children=self._children(check,env,policy_values,adapter);started=utcnow();drafts=[];sessions=[]
        for label,executor,child_check,values,prepared in children:
            try:
                draft=executor.run(child_check,env,values,adapter,Path(workdir)/label)
            except Exception:
                # Preserve previous evidence and still attempt the independent follow-up.
                draft=EvidenceDraft(status=ExecStatus.ABORTED,data_origin=DataOrigin.EXAMPLE if env.data_origin==DataOrigin.EXAMPLE else DataOrigin.DECLARED,
                    normal_control={'attempted':False,'succeeded':None},abort_reason='OBSERVATION_COMPOSITE_CHILD_UNAVAILABLE',
                    cleanup_errors=['OBSERVATION_COMPOSITE_CHILD_CLEANUP_UNVERIFIED'])
            drafts.append(draft)
            sessions.append({'component':label,'profile':child_check.collection_profile,
                'prepared_sha256':sha256_hex(canonical_json(prepared)),'session_sha256':sha256_hex(prepared['session'].encode()),
                'environment_sha256':env.ref().sha256,'target_sha256':values['observation_state_target_sha256'],
                'policy_sha256':sha256_hex(canonical_json(values)),
                'collected_observations':sorted(k for k,o in draft.observations.items() if o.collected),
                'whole_scope_complete':False,'status':draft.status.value,'abort_reason':draft.abort_reason,'cleanup_errors':list(draft.cleanup_errors)})
        observations={}
        for key in OBSERVATIONS:
            if key.startswith('state_collection_'):continue
            selected=[d.observations.get(key) for d in drafts if key in d.observations]
            if key in BASE_OBSERVATIONS and (len(selected)!=2 or any(not o.collected for o in selected)):
                observations[key]={'value':None,'collected':False}
            elif not selected or not all(o.collected for o in selected):
                observations[key]={'value':None,'collected':False}
            elif len(selected)==1:observations[key]=selected[0]
            elif BASE_OBSERVATIONS[key]=='list':
                observations[key]={'value':[{**v,'collection_component':children[index][0],
                    'collection_session_sha256':sessions[index]['session_sha256']}
                    for index,o in enumerate(selected) for v in o.value],'collected':True}
            else:observations[key]={'value':all(o.value is True for o in selected),'collected':True}
        observations['state_collection_sessions']={'value':sessions,'collected':True}
        observations['state_collection_scope_complete']={'value':False,'collected':True}
        complete=all(d.status==ExecStatus.COMPLETED and not d.cleanup_errors for d in drafts)
        artifacts=[]
        for (label,*_),draft in zip(children,drafts):
            artifacts.extend(a.model_copy(update={'name':label+'-'+a.name}) for a in draft.artifacts)
        return EvidenceDraft(status=ExecStatus.COMPLETED if complete else ExecStatus.ABORTED,
            data_origin=min((d.data_origin for d in drafts),key=lambda v:ORIGIN_STRENGTH[v]),
            normal_control={'attempted':any(d.normal_control.attempted for d in drafts),'succeeded':None},
            observations=observations,artifacts=artifacts,config={'profile':PROFILE,'sessions':sessions},
            started_at=started,finished_at=utcnow(),abort_reason=None if complete else 'OBSERVATION_COMPOSITE_COLLECTION_INCOMPLETE',
            cleanup_errors=[label+':'+error for (label,*_),draft in zip(children,drafts) for error in draft.cleanup_errors])
