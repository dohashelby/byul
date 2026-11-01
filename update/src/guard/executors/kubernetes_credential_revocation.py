"""PLT10 selected credential samples; issuer propagation and whole revocation separate."""
from pathlib import Path
import uuid,re
from datetime import datetime,timezone
from guard.adapters import vllm_lifecycle_sensor as sensor
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_credential_revocation import KubernetesCredentialRevocationHTTP,PROFILE as HTTP_PROFILE,SLOTS
from .http_common import Ctx,HttpCheckExecutor
from .kubernetes_lifecycle_capture import KubernetesLifecycleReader
from .lifecycle_session import capture_session
from .credential_revocation_contract import PROFILE,normalize_policy,stamp,project


class KubernetesCredentialRevocationExecutor(HttpCheckExecutor):
    name='credential_revocation';version='0.1.0';requires_roles=False;requires_route_scope=False;functional_via='engine'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='PLT-10' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('CREDENTIAL_REVOCATION_PROFILE_UNSUPPORTED')
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
            if any(secret in str(policy_values[key]) for secret in keys.values() for key in ('revocation_issuer_id','revocation_event_id')):raise ValueError
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            target_hash=sha256_hex(canonical_json(target));prepared=load_prepared(self.cfg['prepared']);start,end,event,deadline=normalize_policy(policy_values)
            if (target_hash!=policy_values['revocation_target_sha256'] or env.ref().sha256!=policy_values['revocation_environment_sha256']
                    or self.expected_target is not None and target_hash!=self.expected_target or prepared['profile']!=sensor.PROFILE
                    or sha256_hex(canonical_json(prepared))!=policy_values['revocation_prepared_sha256'] or not start<=utcnow()<=end or type(env.model.inventory_sha256) is not str or len(env.model.inventory_sha256)!=64):raise ValueError
        except (KeyError,ValueError,TypeError,RuntimeError,OSError):raise ValueError('CREDENTIAL_REVOCATION_CONFIGURATION_INVALID') from None
        transport=KubernetesCredentialRevocationHTTP(target,env,keys,runner=self.runner,duration=self.cfg.get('max_duration_seconds',120),binary=self.cfg.get('kubectl_binary','kubectl'))
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys=keys,secrets=list(keys.values()),cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        ctx._revocation_prepared=prepared;return self._finish(ctx)

    def collect(self,ctx):
        start,end,event,deadline=normalize_policy(ctx.policy_values);token=uuid.uuid4().hex;reader=None;packet=None;sealed=False;trials=[];diagnostics=[]
        native_target={'schema':'guard-kubernetes-lifecycle-target/1','lab':ctx.target['lab'],'run_id':ctx.cprobe.binding['run_id'],
            'environment_ref':ctx.env.ref().model_dump(mode='json'),'kubernetes':{'context':ctx.cprobe.binding['context'],'engine':ctx.cprobe.binding['engine']}}
        try:
            reader=KubernetesLifecycleReader(native_target,ctx.env,ctx._revocation_prepared,runner=self.runner,
                binary=self.cfg.get('kubectl_binary','kubectl'),duration=self.cfg.get('max_duration_seconds',120));reader.snapshot()
            for slot in SLOTS:
                if slot.startswith('retired-') and not trials[0].get('normal_shape_verified'):continue
                row={'case_id':'plt10-'+slot,'request_id_sha256':sha256_hex(('byul-plt10-'+token+'-'+slot).encode()),
                    'attempted':False,'started_at':None,'finished_at':None,'policy_window_verified':False,
                    'issuer_event_verified':False,'credential_label':'retired' if slot.startswith('retired-') else 'control'}
                trials.append(row)
                try:
                    now=datetime.now(timezone.utc)
                    if not start<=now<=end:raise ValueError
                    row.update(attempted=True,started_at=now.isoformat());row.update(ctx.cprobe.perform(slot,'byul-plt10-'+token+'-'+slot))
                    if slot.startswith('control-') and not row['normal_shape_verified']:diagnostics.append('CREDENTIAL_REVOCATION_NORMAL_HTTP_CONTROL_UNAVAILABLE')
                except Exception:diagnostics.append('CREDENTIAL_REVOCATION_PROBE_UNAVAILABLE')
                finally:
                    if row['attempted']:row['finished_at']=datetime.now(timezone.utc).isoformat()
                    row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                    if row['attempted'] and not row['policy_window_verified']:diagnostics.append('CREDENTIAL_REVOCATION_POLICY_WINDOW_UNVERIFIED')
        except Exception:diagnostics.append('CREDENTIAL_REVOCATION_PREFLIGHT_UNAVAILABLE')
        finally:
            if reader is not None:
                try:sealed=reader.seal_session().get('sealed') is True
                except Exception:pass
                if not sealed:diagnostics.append('CREDENTIAL_REVOCATION_SESSION_SEAL_UNAVAILABLE')
                try:
                    engine=reader.binding['engine'];packet=capture_session(reader,native_target,container=engine['container'],image=engine['image'],
                        prepared=ctx._revocation_prepared,owner_identity=reader.owner_identity,read_file=reader.read_file,image_check=reader.image_check)
                    if not packet.get('recorded_files_covered'):diagnostics.append('CREDENTIAL_REVOCATION_NATIVE_COVERAGE_UNAVAILABLE')
                except Exception:diagnostics.append('CREDENTIAL_REVOCATION_CAPTURE_UNAVAILABLE')
        projection=project(packet,trials,ctx.policy_values);http=ctx.cprobe.report();read_report=reader.report() if reader else None
        if not any(r['unique_source_process_invocation_observed'] for r in projection['bindings']):
            diagnostics.append('CREDENTIAL_REVOCATION_NATIVE_AUTH_BINDING_UNAVAILABLE')
        ctx._revocation_cleanup=list(http['cleanup_errors'])
        if read_report and read_report.get('parent_cleanup_unverified'):ctx._revocation_cleanup.append('CREDENTIAL_REVOCATION_PARENT_CLEANUP_UNVERIFIED')
        ctx._revocation_gap=bool(diagnostics or http['collection_gap'] or ctx._revocation_cleanup)
        summary={'profile':PROFILE,'declared_issuer_id':ctx.policy_values['revocation_issuer_id'],'declared_event_id':ctx.policy_values['revocation_event_id'],
            'declared_event_at':ctx.policy_values['revocation_event_at'],'declared_deadline_basis':ctx.policy_values['revocation_deadline_basis'],
            'trials':trials,'native':packet,'projection':projection,'transport':http,'reader':read_report,
            'sealed_selected_session':sealed,'diagnostic_codes':diagnostics,'fixture':{'profile':PROFILE},
            'issuer_event_verified':False,'provider_propagation_verified':False,'whole_scope_complete':False}
        observations={'revocation_trials':trials,'revocation_auth_bindings':projection['bindings'],'revocation_conflict_candidates':projection['candidates'],
            'revocation_missing':projection['missing'],'revocation_absolute_denial_constraint_violated':projection['absolute_denial_constraint_violated'],
            'revocation_issuer_event_verified':False,'revocation_provider_propagation_verified':False,'revocation_normal_before_issuer_verified':False,
            'revocation_new_connection_receipt_verified':False,'revocation_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in observations.items()},{'attempted':bool(trials and trials[0]['attempted']),
            'succeeded':None,'detail':'Closed selected credential samples and native auth/handler facts; declared issuer event/provider propagation/pre-event normal/connection receipt/principal unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._revocation_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._revocation_cleanup);return draft
