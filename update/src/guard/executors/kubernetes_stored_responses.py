"""REQ06 same public CLI, isolated owned Responses and declared HTTP effects."""
from pathlib import Path
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_stored_responses import KubernetesStoredResponsesHTTP,HTTP_PROFILE
from .http_common import Ctx,HttpCheckExecutor
from .stored_response_contract import PROFILE,normalize_policy,stamp


class KubernetesStoredResponsesExecutor(HttpCheckExecutor):
    name='stored_responses';version='0.1.0';requires_roles=True;requires_route_scope=False;functional_via='gateway'

    def __init__(self,cfg,*,runner=None,environ=None,expected_target_sha256=None):
        super().__init__(cfg,environ=environ);self.runner=runner;self.expected_target=expected_target_sha256

    def validate_policy(self,check,values):
        if check.id!='REQ-06' or getattr(check,'collection_profile',None)!=PROFILE:raise ValueError('STORED_RESPONSE_PROFILE_UNSUPPORTED')
        normalize_policy(values,self.roles())

    def run(self,check,env,policy_values,adapter,workdir):
        self.validate_policy(check,policy_values)
        try:
            if (set(self.cfg)-{'target','roles','kubectl_binary','max_duration_seconds'} or adapter is None
                    or any(type(v) is not str or not v for v in (env.model.id,env.model.revision))):raise ValueError
            start,end,scope=normalize_policy(policy_values,self.roles());keys=self.keys()
            with Path(self.cfg['target']).open('rb') as stream:target=strict_json(stream.read(65537),limit=65536)
            digest=sha256_hex(canonical_json(target))
            if (digest!=policy_values['stored_target_sha256'] or env.ref().sha256!=policy_values['stored_environment_sha256']
                    or self.expected_target is not None and digest!=self.expected_target or not start<=utcnow()<=end):raise ValueError
        except (KeyError,ValueError,TypeError,OSError,RuntimeError):raise ValueError('STORED_RESPONSE_CONFIGURATION_INVALID') from None
        transport=KubernetesStoredResponsesHTTP(target,env,scope=scope,credentials={r:keys[r] for r in ('A','B')},
            duration=self.cfg.get('max_duration_seconds',300),binary=self.cfg.get('kubectl_binary','kubectl'),runner=self.runner)
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,timeout=10,allow_mutating=False,
            keys=keys,secrets=[v for v in keys.values() if v and len(v)>=8],cprobe=transport,workdir=workdir,started=utcnow(),http_probe_profile=HTTP_PROFILE)
        return self._finish(ctx)

    def collect(self,ctx):
        start,end,scope=normalize_policy(ctx.policy_values,self.roles());trials=[];missing=[];effects=[];candidates=[];cleanup=[];diagnostics=[]
        def unavailable(origin,owner,kind,stage,reason):
            missing.append({'origin':origin,'owner_claim':owner,'kind':kind,'stage':stage,'reason':reason})
        def invoke(origin,actor,owner,kind,action,stage,*,normal=False):
            row={'origin':origin,'actor':actor,'owner_claim':owner,'kind':kind,'action':action,'stage':stage,
                'attempted':False,'outcome':None,'capture_complete':False,'binding_verified':False,'object_shape_verified':False,
                'started_at':None,'finished_at':None,'policy_window_verified':False}
            trials.append(row)
            try:
                now=utcnow()
                if not start<=now<=end:
                    ctx.cprobe.scope.invalidate('window_expired');raise ValueError
                row['started_at']=now.isoformat()
                row.update(ctx.cprobe.perform(origin,actor,owner,kind,action,stage))
                if not row['capture_complete']:diagnostics.append('STORED_RESPONSE_CAPTURE_INCOMPLETE')
                if normal and not(row['outcome']==200 and row['object_shape_verified']):
                    unavailable(origin,owner,kind,stage,'normal_object_effect_unavailable');diagnostics.append('STORED_RESPONSE_NORMAL_UNAVAILABLE')
                if not normal and (row['outcome'] not in {200,401,403,404} or row['outcome']==200 and not row['object_shape_verified']):
                    unavailable(origin,owner,kind,stage,'cross_response_or_effect_unavailable');diagnostics.append('STORED_RESPONSE_CROSS_EFFECT_UNAVAILABLE')
            except Exception:
                observed=ctx.cprobe.report()['requests']
                if observed and all(observed[-1].get(k)==row[k] for k in ('origin','actor','owner_claim','kind','action','stage')):
                    row.update(observed[-1])
                row['diagnostic']='STORED_RESPONSE_OPERATION_UNAVAILABLE';diagnostics.append(row['diagnostic'])
            finally:
                row['finished_at']=utcnow().isoformat()
                row['policy_window_verified']=bool(row['started_at'] and start<=stamp(row['started_at'])<=stamp(row['finished_at'])<=end)
                if not row['policy_window_verified']:diagnostics.append('STORED_RESPONSE_POLICY_WINDOW_UNVERIFIED')
            return row
        def ready(origin,owner,kind,stage):
            if ctx.cprobe.ready(origin,owner,kind):return True
            unavailable(origin,owner,kind,stage,'owned_normal_lookup_unavailable');return False
        def compare(row,kind,**extra):
            effect={k:row.get(k) for k in ('origin','actor','owner_claim','kind','stage','outcome','status','object_shape_verified','content_sha256','previous_id_matches')}
            effect.update(kind_of_effect=kind,producer_authenticated=False,object_owner_verified=False,worker_effect_verified=False,**extra);effects.append(effect)
            return effect
        for origin in ('gateway','direct'):
            if origin not in scope['origins']:
                unavailable(origin,None,None,None,'origin_not_selected');continue
            kinds=[k for k in ('read','cancel-normal','cancel-target','previous','overwrite') if k.split('-')[0] in scope['operations']]
            for op in ('read','cancel','previous','overwrite'):
                if op not in scope['operations']:unavailable(origin,None,op,None,'operation_not_selected')
            try:
                for kind in kinds:
                    for owner in ('A','B'):
                        row=invoke(origin,owner,owner,kind,'create','create',normal=True)
                        if row['outcome']==200 and row['object_shape_verified']:
                            invoke(origin,owner,owner,kind,'get','owner-before',normal=True)
                normal_cancel={};normal_previous={};normal_overwrite={}
                if 'cancel' in scope['operations']:
                    for owner in ('A','B'):
                        if not ready(origin,owner,'cancel-normal','owner-cancel'):continue
                        if ctx.cprobe.status(origin,owner,'cancel-normal') not in {'queued','in_progress'}:
                            unavailable(origin,owner,'cancel-normal','owner-cancel','normal_object_not_cancellable');continue
                        row=invoke(origin,owner,owner,'cancel-normal','cancel','owner-cancel',normal=True)
                        after=invoke(origin,owner,owner,'cancel-normal','get','owner-after',normal=True)
                        normal_cancel[owner]=bool(row.get('status')=='cancelled' and after.get('status')=='cancelled')
                        compare(after,'own_api_cancel',cancellation_api_state_observed=normal_cancel[owner])
                if 'previous' in scope['operations']:
                    for owner in ('A','B'):
                        if ready(origin,owner,'previous','owner-previous'):
                            row=invoke(origin,owner,owner,'previous','previous','owner-previous',normal=True)
                            normal_previous[owner]=bool(row.get('object_shape_verified') and row.get('previous_id_matches'))
                            compare(row,'own_previous_reference')
                if 'overwrite' in scope['operations']:
                    for owner in ('A','B'):
                        if ready(origin,owner,'overwrite','owner-overwrite'):
                            row=invoke(origin,owner,owner,'overwrite','overwrite','owner-overwrite',normal=True)
                            after=invoke(origin,owner,owner,'overwrite','get','owner-overwrite-after',normal=True)
                            normal_overwrite[owner]=bool(row.get('object_shape_verified') and after.get('probe_marker')=='owned-self-overwrite'
                                and row.get('content_sha256')==after.get('content_sha256'))
                            compare(after,'own_overwrite_effect',overwrite_api_state_observed=normal_overwrite[owner])
                for owner,actor in (('A','B'),('B','A')):
                    if 'read' in scope['operations'] and ready(origin,owner,'read','cross-read') and ready(origin,actor,'read','cross-read'):
                        row=invoke(origin,actor,owner,'read','get','cross-read');compare(row,'cross_subject_read')
                        if row.get('object_shape_verified') and row['outcome']==200:
                            candidates.append({'origin':origin,'actor':actor,'owner_claim':owner,'kind':'cross_subject_response_returned','confirmed_violation':False})
                        invoke(origin,owner,owner,'read','get','owner-after',normal=True)
                    if 'cancel' in scope['operations'] and ready(origin,owner,'cancel-target','cross-cancel'):
                        if not normal_cancel.get(actor) or ctx.cprobe.status(origin,owner,'cancel-target') not in {'queued','in_progress'}:
                            unavailable(origin,owner,'cancel-target','cross-cancel','own_cancellation_or_target_cancellable_unverified')
                        else:
                            row=invoke(origin,actor,owner,'cancel-target','cancel','cross-cancel')
                            after=invoke(origin,owner,owner,'cancel-target','get','owner-after',normal=True)
                            compare(after,'cross_subject_cancel',cancellation_api_state_observed=after.get('status')=='cancelled')
                            if row.get('status')=='cancelled' and after.get('status')=='cancelled':
                                candidates.append({'origin':origin,'actor':actor,'owner_claim':owner,'kind':'cross_subject_api_cancellation','confirmed_violation':False})
                    if 'previous' in scope['operations'] and ready(origin,owner,'previous','cross-previous'):
                        if not normal_previous.get(actor):unavailable(origin,owner,'previous','cross-previous','own_previous_reference_unverified')
                        else:
                            row=invoke(origin,actor,owner,'previous','previous','cross-previous');compare(row,'cross_subject_previous_reference')
                            if row.get('object_shape_verified') and row.get('previous_id_matches'):
                                candidates.append({'origin':origin,'actor':actor,'owner_claim':owner,'kind':'cross_subject_previous_reference_accepted','confirmed_violation':False})
                    if 'overwrite' in scope['operations'] and ready(origin,owner,'overwrite','cross-overwrite'):
                        if not normal_overwrite.get(actor):unavailable(origin,owner,'overwrite','cross-overwrite','own_overwrite_effect_unverified')
                        else:
                            row=invoke(origin,actor,owner,'overwrite','overwrite','cross-overwrite')
                            after=invoke(origin,owner,owner,'overwrite','get','owner-after',normal=True);compare(after,'cross_subject_overwrite')
                            if row.get('object_shape_verified') and after.get('probe_marker')=='owned-cross-overwrite' and row.get('content_sha256')==after.get('content_sha256'):
                                candidates.append({'origin':origin,'actor':actor,'owner_claim':owner,'kind':'cross_subject_stored_object_changed','confirmed_violation':False})
            except Exception:diagnostics.append('STORED_RESPONSE_PROCEDURE_UNAVAILABLE')
            finally:
                if 'cancel' in scope['operations']:
                    for owner in ('A','B'):
                        row={'origin':origin,'owner_claim':owner,'api_cancel_attempted':False,'api_cancelled_observed':False,'worker_exit_verified':False}
                        if ctx.cprobe.ready(origin,owner,'cancel-target') and ctx.cprobe.status(origin,owner,'cancel-target') in {'queued','in_progress'}:
                            result=invoke(origin,owner,owner,'cancel-target','cancel','cleanup',normal=True);row['api_cancel_attempted']=result['attempted']
                            after=invoke(origin,owner,owner,'cancel-target','get','cleanup-after',normal=True);row['api_cancelled_observed']=after.get('status')=='cancelled'
                        cleanup.append(row)
        transport=ctx.cprobe.report();objects=transport['scope']['creation_claims']
        summary={'profile':PROFILE,'trials':trials,'effects':effects,'missing':missing,'conflict_candidates':candidates,'cleanup':cleanup,
            'transport':transport,'diagnostic_codes':sorted(set(diagnostics)),'fixture':{'profile':PROFILE},
            'retained_owned_lab_objects':objects,'retention_policy':scope and ctx.policy_values['stored_retention'],
            'actual_store_producer_authenticated':False,'actual_worker_exit_verified':False,'whole_scope_complete':False}
        ctx._stored_cleanup=transport['cleanup_errors'];ctx._stored_gap=bool(diagnostics or transport['collection_gap'] or any(m['reason'] not in {'origin_not_selected','operation_not_selected'} for m in missing))
        data={'stored_trials':trials,'stored_objects':objects,'stored_effects':effects,'stored_missing':missing,'stored_conflict_candidates':candidates,'stored_cleanup':cleanup,
            'stored_producer_authenticated':False,'stored_object_owner_verified':False,'stored_worker_effect_verified':False,'stored_scope_complete':False}
        return {k:{'value':v,'collected':True} for k,v in data.items()}, {'attempted':any(r['attempted'] for r in trials),
            'succeeded':None,'detail':'Owned HTTP response shapes and declared stored/cancel/chain effects; actual principal/store/worker source unverified.'},[],summary

    def _finish(self,ctx):
        draft=super()._finish(ctx);draft.data_origin=DataOrigin.EXAMPLE
        if ctx._stored_gap:draft.status=ExecStatus.ABORTED
        draft.cleanup_errors.extend(ctx._stored_cleanup)
        return draft
