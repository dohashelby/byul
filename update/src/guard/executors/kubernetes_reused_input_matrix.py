"""Keep every declared reused field/endpoint in one existing REQ-08 result.

Child collectors preserve the historical protocol. New frontend/response facts
have separate qualification; returned IDs are never called GPU memory reads.
"""
from copy import deepcopy
import hashlib,hmac,json,time
from pathlib import Path
from guard.models import DataOrigin,ExecStatus,ORIGIN_STRENGTH,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from .base import EvidenceDraft,DraftArtifact
from .kubernetes_input_validation import KubernetesInputValidationExecutor
from .request_field_probe import _field,consumed_tokens
from .content_common import control_state

PROFILE='reused-token-input-matrix-v1'
MARKERS={'reused_fields_tested':'list[str]','reused_matrix_complete':'bool'}


def validate(check,values,cfg):
    from guard.reused_matrix_policy import selected
    selected(check,values)
    if(check.id!='REQ-08'or getattr(check,'collection_profile',None)!=PROFILE
            or {o.key:o.type for o in check.required_observations if o.key in MARKERS}!=MARKERS
            or 'native_input_capture'not in cfg):raise ValueError('REUSED_MATRIX_DEFINITION_OR_CAPTURE_INVALID')
    fields=values.get('reused_token_fields');endpoints=values.get('reused_field_endpoints')
    if(type(fields)is not list or not 1<=len(fields)<=8
            or any(type(v)is not str for v in fields)or len(set(fields))!=len(fields)or values.get('reused_token_field')not in fields
            or type(endpoints)is not dict or set(endpoints)!=set(fields)
            or any(type(p)is not str for p in endpoints.values())
            or set(endpoints.values())!={p.id for p in check.functional_probes}):raise ValueError('REUSED_MATRIX_POLICY_INVALID')
    for field in fields:_field(field)
    from . import request_field_probe
    sub=check.model_copy(update={'collection_profile':request_field_probe.REUSED_TOKEN_PROFILE,
        'functional_probes':[check.functional_probes[0]]})
    request_field_probe.validate_policy(sub,values);request_field_probe.validate_configuration(sub,cfg)
    if type(cfg.get('max_duration_seconds',120))not in(int,float)or not 0<cfg.get('max_duration_seconds',120)<=3600:
        raise ValueError('REUSED_MATRIX_BUDGET_INVALID')
    return fields,endpoints


class ResponseIdentity:
    """Read the controlled response only; return the original rows unchanged."""
    def __init__(self,ctx,cfg,original):
        self.ctx,self.cfg,self.original=ctx,cfg,original;self.records=[]
        self.capture=ctx._native_input_capture
        self.key=bytes.fromhex(self.capture.settings['key'])
    def __getattr__(self,name):return getattr(self.original,name)
    def _hmac(self,domain,value):
        from guard.collectors import native_input
        return hmac.new(self.key,native_input.runtime.canonical([domain,value]),hashlib.sha256).hexdigest()
    def __call__(self,network,requests,timeout):
        result=self.original(network,requests,timeout)
        record={'label':('before','challenge-0','after-0')[len(self.records)]if len(self.records)<3 else'outside-profile',
            'request_hmac_sha256':None,'request_fixture_sha256':None,'response_tokens_hmac_sha256':None,
            'fixture_tokens_hmac_sha256':self._hmac('tokens',self.cfg['reused_token_ids']),
            'generation_shape_valid':False,'status':None,'metadata_verified':False}
        try:
            from guard.collectors import native_input
            if len(requests)!=1 or len(result)!=1 or record['label']=='outside-profile':raise ValueError
            request=requests[0];row=result[0];identifier=request['headers']['X-Request-Id']
            tokens=consumed_tokens(row)
            if tokens is not None and len(tokens)>4096:raise ValueError
            record.update(request_hmac_sha256=self._hmac('request',identifier),
                request_fixture_sha256=hashlib.sha256(native_input.runtime.canonical(request['body'])).hexdigest(),
                response_tokens_hmac_sha256=self._hmac('tokens',tokens)if tokens is not None else None,
                generation_shape_valid=control_state(row,self.ctx.api_model,self.ctx.check.functional_probes[0].path)=='pass',
                status=row.get('outcome')if type(row.get('outcome'))is int else None,metadata_verified=True)
        except Exception:pass
        self.records.append(record)
        return result


class _Child(KubernetesInputValidationExecutor):
    def collect(self,ctx):
        self.current=ctx;original=ctx.cprobe;self.response_identity=ResponseIdentity(ctx,self.cfg,original)
        ctx.cprobe=self.response_identity
        try:
            observations,normal,challenges,summary=super().collect(ctx)
            summary['reused_response_identity']=self.response_identity.records
            return observations,normal,challenges,summary
        finally:ctx.cprobe=original


def facts(child,draft):
    from guard.collectors import native_input,reused_input_qualification as qualification
    ctx=child.current;capture=ctx._native_input_capture
    result={'schema':'guard-current-reused-field-identity/1','current_links_verified':False,
        'response_identity':child.response_identity.records,'source':None,'qualification':None,
        'state':'accepted_unverified','diagnostic_codes':[],
        'native_gpu_token_consumption_verified':False,'whole_question_complete':False}
    try:
        if draft.status!=ExecStatus.COMPLETED:raise ValueError
        reviewed=native_input.verify_capture(capture.prepared,ctx.workdir/'native-input-originals',
            requests=capture.requests,expected_uid=capture.prepared_uid)
        artifact,=[a for a in draft.artifacts if a.name=='native_input_capture.json']
        document=strict_json(artifact.path.read_bytes(),limit=8*1024**2)['capture']
        if(document.get('target_generation_stable')is not True or document.get('source_capture')!=reviewed
                or document.get('cases')!=capture.cases):raise ValueError
        records=child.response_identity.records
        if len(records)!=3 or [r['label']for r in records]!=['before','challenge-0','after-0']or any(r['metadata_verified']is not True for r in records):raise ValueError
        by_label={r['label']:r for r in records}
        if(set(capture.requests)!=set(by_label)or len(capture.cases)!=3
                or {r['case']for r in capture.cases}!=set(by_label)
                or any(r['request_fixture_sha256']!=by_label[r['case']]['request_fixture_sha256']
                    or r['dispatched']is not True or r['outcome']!=by_label[r['case']]['status']for r in capture.cases)):raise ValueError
        source=ctx.adapter.native_reused_field_facts(engine=ctx.env.engine.name,version=ctx.env.engine.version,
            image=ctx.env.engine.image_digest,review=reviewed,requests={k:r['request_hmac_sha256']for k,r in by_label.items()})
        status=qualification.status(ctx.env.engine.name,ctx.env.engine.version,ctx.env.engine.image_digest)
        result.update(source=source,qualification=status,current_links_verified=source['complete'])
        before,challenge,after=records
        distinct=(before['response_tokens_hmac_sha256']is not None
            and before['response_tokens_hmac_sha256']!=challenge['fixture_tokens_hmac_sha256'])
        result['control_fixture_distinct']=distinct
        if not distinct:result['diagnostic_codes'].append('REUSED_FIXTURE_EQUALS_OR_LACKS_NORMAL_IDENTITY')
        normal=(before['generation_shape_valid']and after['generation_shape_valid']
            and before['response_tokens_hmac_sha256']is not None
            and before['response_tokens_hmac_sha256']==after['response_tokens_hmac_sha256'])
        if not(normal and source['complete']and status['registered']):return result
        if str(challenge['status'])in ctx.policy_values['rejection_codes']:
            result['state']='rejected'
        elif(distinct and challenge['generation_shape_valid']
                and all(all(r[key]for key in ('frontend_validation_return_observed','renderer_reuse_return_observed',
                    'chat_renderer_return_observed','input_processing_return_observed'))for r in source['cases'].values())):
            value=challenge['response_tokens_hmac_sha256']
            if value is not None and value==challenge['fixture_tokens_hmac_sha256']:result['state']='consumed'
            elif value is not None and value==before['response_tokens_hmac_sha256']:result['state']='ignored'
        return result
    except Exception:
        result['diagnostic_codes'].append('REUSED_CURRENT_SOURCE_OR_IDENTITY_UNVERIFIED');return result


def run(parent,check,env,values,adapter,workdir):
    fields,endpoints=validate(check,values,parent.cfg);started=utcnow();began=time.monotonic()
    parent.version='0.8.0+k8s-reused-matrix.1'
    drafts=[];artifacts=[];trials=[];tested=[];controls={};control_plan=[];details=[];cleanup=[]
    for index,field in enumerate(fields):
        detail={'field':field,'endpoint':endpoints[field],'proof':None,'diagnostic_codes':[]};details.append(detail)
        remaining=parent.cfg.get('max_duration_seconds',120)-(time.monotonic()-began)
        selected=[p for p in check.functional_probes if p.id==endpoints[field]]
        if len(selected)!=1:raise ValueError('REUSED_MATRIX_NORMAL_PROFILE_UNVERIFIED')
        subcheck=check.model_copy(update={'collection_profile':'reused-token-input-v1','functional_probes':selected})
        policy={**values,'reused_token_field':field};cfg={**parent.cfg,'max_duration_seconds':remaining}
        child=_Child(cfg,runner=parent._kubectl_runner,
            expected_target_sha256=parent._expected_target_sha256)
        try:
            draft=child.run(subcheck,env,policy,adapter,Path(workdir)/('field-'+str(index)));drafts.append(draft)
            for item in draft.artifacts:artifacts.append(DraftArtifact(name=f'field-{index}-'+item.name,path=item.path,collector=item.collector))
            for ordinal,path in enumerate(sorted((Path(workdir)/('field-'+str(index))/'native-input-originals').glob('pid-*.json'))):
                artifacts.append(DraftArtifact(name=f'field-{index}-source-{ordinal:04d}.json',path=path,collector='native-input-original-hmac/1'))
            cleanup.extend(draft.cleanup_errors);proof=facts(child,draft);detail['proof']=proof
            source=draft.observations.get('reused_input_trials')
            if source is not None and source.collected and source.value:
                tested.append(field);trials.extend({**r,'endpoint':endpoints[field],'raw_classification':r['state'],
                    'state':proof['state'],'qualified_frontend_response_identity':proof['current_links_verified']
                    and(proof.get('qualification')or{}).get('registered')is True}for r in source.value)
            result=draft.observations.get('functional_probe_results')
            if result is not None and result.collected:controls.update({field+'|'+k:v for k,v in result.value.items()})
        except Exception:detail['diagnostic_codes'].append('REUSED_MATRIX_FIELD_CAPTURE_UNAVAILABLE')
        control_plan.extend([field+'|before',field+'|after-0'])
    states=[r['state']for r in trials]
    state=('consumed'if'consumed'in states else'rejected'if len(states)==len(fields)and all(s=='rejected'for s in states)
        else'ignored'if len(states)==len(fields)and all(s in{'ignored','rejected'}for s in states)else'accepted_unverified')
    complete=(tested==fields and len(trials)==len(fields)and all(s in{'consumed','ignored','rejected'}for s in states)
        and set(controls)==set(control_plan)and all(s=='pass'for s in controls.values())and not cleanup)
    observations={'reused_input_trials':trials,'reused_input_state':state,'reused_input_forbidden':not values['reused_token_allowed'],
        'reused_input_violations':[r['field']for r in trials if r['state']=='consumed'and not values['reused_token_allowed']],
        'reused_fields_tested':tested,'reused_matrix_complete':complete,
        'functional_probes_planned':control_plan,'functional_probe_results':controls}
    origin=min([env.data_origin,*[d.data_origin for d in drafts]],key=lambda v:ORIGIN_STRENGTH[v])
    status=ExecStatus.COMPLETED if len(drafts)==len(fields)and all(d.status==ExecStatus.COMPLETED for d in drafts)else ExecStatus.ABORTED
    if status!=ExecStatus.COMPLETED:origin=min(origin,DataOrigin.DECLARED,key=lambda v:ORIGIN_STRENGTH[v])
    summary={'profile':PROFILE,'fields':details,'selected_matrix_complete':complete,
        'native_gpu_token_consumption_verified':False,'fixture':{'fields_sha256':sha256_hex(canonical_json(endpoints))}}
    path=Path(workdir)/'reused_field_matrix.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(canonical_json(summary))
    artifacts.append(DraftArtifact(name=path.name,path=path,collector=PROFILE))
    return EvidenceDraft(status=status,data_origin=origin,started_at=started,finished_at=utcnow(),
        normal_control={'attempted':any(d.normal_control.attempted for d in drafts),
            'succeeded':len(drafts)==len(fields)and all(d.normal_control.succeeded is True for d in drafts),
            'detail':'Original before/after control for every explicit reused field/endpoint.'},
        observations={k:{'value':v,'collected':True}for k,v in observations.items()},artifacts=artifacts,
        challenges=[{'step':step,'attempted':len(trials)==len(fields)}for step in check.challenge_steps],
        cleanup_errors=cleanup,config={'profile':PROFILE,'field_endpoints_sha256':sha256_hex(canonical_json(endpoints))})
