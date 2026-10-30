"""PLT11 selected existing probe connection; issuer/application session separate."""
from pathlib import Path
import uuid,re
from datetime import datetime,timezone
from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_session_revocation import KubernetesSessionRevocationHTTP,PROFILE as HTTP_PROFILE,SLOTS
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .session_revocation_contract import PROFILE,normalize_policy,epoch_ns,project


class KubernetesSessionRevocationExecutor(HttpCheckExecutor):
    name='session_revocation';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='PLT-11' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('SESSION_REVOCATION_PROFILE_UNSUPPORTED')
        normalize_policy(values)

    def run(self,check,env,policy_values,adapter,workdir):
        from guard.collectors.lifecycle import load_prepared
        self.validate_policy(check,policy_values)
        try:
            if set(self.cfg)-{'target','prepared','kubectl_binary','max_duration_seconds','retired_key_env','control_key_env'} or adapter is None:raise ValueError
            if any(type(v) is not str or not v for v in (env.model.id,env.model.revision)):raise ValueError
            keys={}
            for role in ('retired','control'):
                name=self.cfg[role+'_key_env']
                if type(name) is not str or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',name) is None:raise ValueError
                keys[role]=self._environ.get(name)
            if any(type(v) is not str or not v for v in keys.values()) or len(set(keys.values()))!=2:raise ValueError
            if any(secret in str(policy_values[key]) for secret in keys.values() for key in ('session_issuer_id','session_event_id')):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            target_hash=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end,event,opening,deadline=normalize_policy(policy_values)
            if (target_hash!=policy_values['session_target_sha256'] or env.ref().sha256!=policy_values['session_environment_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target or prepared['profile']!=sensor.PROFILE
                    or sha256_hex(canonical_json(prepared))!=policy_values['session_prepared_sha256'] or not start<=utcnow()<=end or type(env.model.inventory_sha256) is not str or len(env.model.inventory_sha256)!=64):raise ValueError
        except (KeyError,ValueError,TypeError,RuntimeError,OSError):raise ValueError('SESSION_REVOCATION_CONFIGURATION_INVALID') from None
        transport=KubernetesSessionRevocationHTTP(target,env,keys,runner=self.runner,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys=keys,secrets=list(keys.values()),cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._session_prepared=prepared;return self._finish(ctx)

    def collect(self,ctx):
        start,end,event,opening,deadline=normalize_policy(ctx.policy_values);reader=None;native=None;sealed=False;trials=[];diagnostics=[]
        target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        transport=None
        try:
            reader=KubernetesLifecycleReader(target,ctx.env,ctx._session_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            transport=ctx.cprobe.perform(uuid.uuid4().hex,ctx.policy_values)
            for row in (transport.get('packet') or {}).get('rows',[]):
                row=dict(row);row['policy_window_verified']=bool(row['started_wall_ns'] and epoch_ns(ctx.policy_values['session_validity_start'])
                    <=row['started_wall_ns']<=row['finished_wall_ns']<=epoch_ns(ctx.policy_values['session_validity_end']))
                trials.append(row)
            if transport['collection_gap']:diagnostics.append('SESSION_REVOCATION_PROBE_UNAVAILABLE')
        except Exception:diagnostics.append('SESSION_REVOCATION_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('SESSION_REVOCATION_SESSION_SEAL_UNAVAILABLE')
                try:
                    engine=reader.binding['engine'];native=capture_session(reader,target,container=engine['container'],image=engine['image'],
                        prepared=ctx._session_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not native.get('recorded_files_covered'):diagnostics.append('SESSION_REVOCATION_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('SESSION_REVOCATION_CAPTURE_UNAVAILABLE')
        transport=ctx.cprobe.report();projection=project(native,trials,ctx.policy_values,transport);read_report=reader.report() if reader else None
        if not trials or any(not row['unique_source_process_invocation_observed'] for row in projection['bindings']):diagnostics.append('SESSION_REVOCATION_NATIVE_AUTH_BINDING_UNAVAILABLE')
        ctx._session_cleanup=list(transport['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._session_cleanup.append('SESSION_REVOCATION_PARENT_CLEANUP_UNVERIFIED')
        ctx._session_gap=bool(diagnostics or transport['collection_gap'] or ctx._session_cleanup)
        summary={'profile':PROFILE,'trials':trials,'native':native,'projection':projection,'transport':transport,'reader':read_report,
            'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,'fixture':{'profile':PROFILE},
            'issuer_event_verified':False,'provider_propagation_verified':False,'whole_scope_complete':False}
        observations={'session_trials':trials,'session_native_bindings':projection['bindings'],'session_conflict_candidates':projection['candidates'],
            'session_missing':projection['missing'],'session_existing_connection_constraint_violated':projection['existing_connection_constraint_violated'],
            'session_selected_probe_connection_reuse_observed':projection['selected_probe_connection_reuse_observed'],
            'session_distinct_new_probe_connection_observed':projection['distinct_new_probe_connection_observed'],
            'session_issuer_event_verified':False,'session_provider_propagation_verified':False,'session_application_session_or_handle_verified':False,
            'session_serving_session_receipt_verified':False,'session_scope_complete':False}
        return {key:{'value':value,'collected':True} for key,value in observations.items()},{'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Selected probe HTTP keep-alive and native auth facts only; actual issuer/principal/application session/handle/serving receipt unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._session_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._session_cleanup);return draft
