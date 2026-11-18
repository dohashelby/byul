"""Collect every explicit endpoint/format pair, without reducing the policy.

The historical single-token executor and its transport gates remain intact.
This profile requires its own definition and explicit allowed_input_pairs.
"""
from copy import deepcopy
from pathlib import Path
import hashlib,json,time
from guard.input_matrix_policy import PROFILE,pairs,planned
from guard.models import DataOrigin,ExecStatus,canonical_json,sha256_hex,utcnow
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE
from guard.runtime.kubernetes_input_matrix import KubernetesInputMatrixHTTP
from .base import EvidenceDraft,DraftArtifact
from .kubernetes_input_validation import _WithBody
from .kubernetes_observed_input import KubernetesObservedInputValidationExecutor
from .http_common import Ctx,HttpCheckExecutor,request_model,incomplete_artifact_capture
from .content_common import bounded_probe,control_state,sample_artifact,append_artifact,record_content
from .response_projection import log_metadata

SETTINGS={'target','kubectl_binary','request_timeout','max_duration_seconds','vocab_size','vocab_source',
    'max_probe_tokens','native_input_capture'}


def validate(check,values,cfg):
    selected=pairs(check,values)
    from guard.core.select import check_policy_value
    for param in check.policy_params:
        if param.required and param.key not in values:raise ValueError('INPUT_MATRIX_POLICY_VALUE_MISSING')
        if param.key in values:check_policy_value(check.id,param.key,param.type,values[param.key])
    if(type(cfg)is not dict or set(cfg)-SETTINGS or 'native_input_capture'not in cfg
            or type(cfg.get('vocab_size'))is not int or not 1<cfg['vocab_size']<2**31
            or type(cfg.get('vocab_source'))is not str or not cfg['vocab_source']
            or type(values.get('max_input_tokens'))is not int or not 1<=values['max_input_tokens']<=512
            or type(cfg.get('max_probe_tokens',4096))is not int
            or cfg.get('max_probe_tokens',4096)<values['max_input_tokens']+1
            or not values.get('rejection_codes')or any(s not in{'400','413','422'}for s in values['rejection_codes'])):
        raise ValueError('INPUT_MATRIX_CONFIGURATION_INVALID')
    return selected


def source_facts(ctx,draft,capture,fixture):
    from guard.collectors import native_input,native_input_rejection_qualification as qualification
    result={'projection':None,'qualification':None,'diagnostic_codes':[]}
    try:
        if draft.status!=ExecStatus.COMPLETED:raise ValueError
        reviewed=native_input.verify_capture(capture.prepared,ctx.workdir/'native-input-originals',
            requests=capture.requests,expected_uid=capture.prepared_uid)
        artifact,=[a for a in draft.artifacts if a.name=='native_input_capture.json']
        document=strict_json(artifact.path.read_bytes(),limit=8*1024**2)['capture']
        if(document.get('target_generation_stable')is not True or document.get('source_capture')!=reviewed
                or document.get('cases')!=capture.cases):raise ValueError
        result['projection']=ctx.adapter.native_input_rejection_facts(engine=ctx.env.engine.name,
            version=ctx.env.engine.version,image=ctx.env.engine.image_digest,review=reviewed)
        result['qualification']=qualification.status(ctx.env.engine.name,ctx.env.engine.version,ctx.env.engine.image_digest)
        expected={'before':fixture['control']}
        for label,body in fixture['cases'].items():expected.update({label:body,'after_'+label:fixture['control']})
        if (set(capture.requests)!=set(expected)or len(capture.cases)!=len(expected)
                or any(r['request_fixture_sha256']!=hashlib.sha256(native_input.runtime.canonical(expected[r['case']])).hexdigest()
                    or r['dispatched']is not True for r in capture.cases)):raise ValueError
        result['current_fixture_links_verified']=True
    except Exception:
        result.update(current_fixture_links_verified=False)
        result['diagnostic_codes'].append('INPUT_MATRIX_NATIVE_SOURCE_UNVERIFIED')
    return result


def _capture_class():
    from .native_input_link import Capture
    return Capture


class KubernetesMatrixInputValidationExecutor(KubernetesObservedInputValidationExecutor):
    def validate_policy(self,check,values):
        if check.id=='REQ-08'and getattr(check,'collection_profile',None)=='reused-token-input-matrix-v1':
            from .kubernetes_reused_input_matrix import validate as validate_reused
            validate_reused(check,values,self.cfg);return
        if check.id=='REQ-09'and getattr(check,'collection_profile',None)==PROFILE:
            validate(check,values,self.cfg);return
        return super().validate_policy(check,values)

    def run(self,check,env,policy_values,adapter,workdir):
        if check.id=='REQ-08'and getattr(check,'collection_profile',None)=='reused-token-input-matrix-v1':
            from .kubernetes_reused_input_matrix import run
            return run(self,check,env,policy_values,adapter,workdir)
        if check.id!='REQ-09'or getattr(check,'collection_profile',None)!=PROFILE:
            return super().run(check,env,policy_values,adapter,workdir)
        selected=validate(check,policy_values,self.cfg)
        if adapter is None:raise ValueError('INPUT_MATRIX_ADAPTER_REQUIRED')
        target=strict_json(Path(self.cfg['target']).read_bytes(),limit=65536)
        if self._expected_target_sha256 is not None and sha256_hex(canonical_json(target))!=self._expected_target_sha256:
            raise ValueError('INPUT_MATRIX_TARGET_CHANGED')
        duration=self.cfg.get('max_duration_seconds',120)
        self._matrix_deadline=time.monotonic()+duration
        self._matrix_artifacts=[];self._matrix_cleanup=[];self._matrix_injected=False;self._matrix_incomplete=False
        first=self._transport(target,env)
        model=request_model(env,target,transport=first)
        builder=getattr(adapter,'input_matrix_fixtures',None)
        if not callable(builder):raise ValueError('INPUT_MATRIX_ENGINE_PROFILE_UNAVAILABLE')
        fixtures=builder(engine=env.engine.name,version=env.engine.version,image=env.engine.image_digest,
            pairs=selected,model=model,vocab_size=self.cfg['vocab_size'],limit=policy_values['max_input_tokens'])
        if len(fixtures)!=len(selected):raise ValueError('INPUT_MATRIX_ENGINE_COVERAGE_INVALID')
        for fixture,pair in zip(fixtures,selected,strict=True):
            if(fixture['endpoint'],fixture['format'])!=pair:raise ValueError('INPUT_MATRIX_ENGINE_COVERAGE_INVALID')
        self.version='0.8.0+k8s-input-matrix.1'
        ctx=Ctx(check=check,env=env,adapter=adapter,target=target,policy_values=policy_values,
            timeout=float(self.cfg.get('request_timeout',10)),allow_mutating=False,keys={},secrets=[],
            cprobe=first,workdir=Path(workdir),started=utcnow())
        ctx._matrix_fixtures=fixtures;ctx._matrix_model=model;ctx._matrix_first=first
        draft=HttpCheckExecutor._finish(self,ctx)
        draft.artifacts.extend(self._matrix_artifacts);draft.cleanup_errors.extend(self._matrix_cleanup)
        if self._matrix_injected:draft.data_origin=DataOrigin.EXAMPLE
        if self._matrix_cleanup or self._matrix_incomplete:draft=incomplete_artifact_capture(draft)
        return draft

    def _transport(self,target,env):
        remaining=min(self.cfg.get('max_duration_seconds',120),self._matrix_deadline-time.monotonic())
        return KubernetesInputMatrixHTTP(target,env,duration=remaining,binary=self.cfg.get('kubectl_binary','kubectl'),
            runner=self._kubectl_runner,profile=INPUT_VALIDATION_PROFILE)

    def collect(self,ctx):
        if ctx.check.id!='REQ-09'or getattr(ctx.check,'collection_profile',None)!=PROFILE:return super().collect(ctx)
        from guard.collectors import native_input
        from . import native_input_link
        rows=[];controls={};control_plan=[];groups=[];endpoints=set();formats=set();attempted=0
        for index,fixture in enumerate(ctx._matrix_fixtures):
            prefix=fixture['endpoint']+'|'+fixture['format'];group={'pair':prefix,'source':None,'diagnostic_codes':[]}
            groups.append(group)
            child=Ctx(check=ctx.check,env=ctx.env,adapter=ctx.adapter,target=ctx.target,policy_values=ctx.policy_values,
                timeout=ctx.timeout,allow_mutating=False,keys={},secrets=[],cprobe=None,
                workdir=ctx.workdir/('matrix-'+str(index)),started=ctx.started)
            local_controls={};outcomes={};capture=None;child_draft=None;transport=None
            for label in['before',*['after_'+key for key in fixture['cases']]]:control_plan.append(prefix+'|'+label)
            try:
                transport=ctx._matrix_first if index==0 else self._transport(ctx.target,ctx.env)
                self._matrix_injected|=transport.injected
                transport.declare_matrix(fixture['path'],[fixture['control'],*fixture['cases'].values()])
                child.cprobe=_WithBody(transport)
                capture=_capture_class()(child,self.cfg['native_input_capture']);child._native_input_capture=capture
                def send(label,body):
                    nonlocal attempted
                    request={'id':'func|POST '+fixture['path'],'method':'POST','url':child.engine_url+fixture['path'],
                        'headers':{},'body':deepcopy(body)}
                    attempted+=1;endpoints.add(fixture['endpoint']);formats.add(fixture['format'])
                    value=capture.call(label,request,lambda actual:bounded_probe(child,child.trusted_net,actual))
                    record_content(child,'trusted','input-matrix',label,log_metadata(value))
                    return value
                response=send('before',fixture['control'])
                local_controls['before']=control_state(response,ctx._matrix_model,fixture['path'])
                if local_controls['before']=='pass':
                    for label,body in fixture['cases'].items():
                        response=send(label,body);status=response.get('outcome')
                        outcomes[label]=str(status)if type(status)is int else'error'
                        response=send('after_'+label,fixture['control'])
                        local_controls['after_'+label]=control_state(response,ctx._matrix_model,fixture['path'])
                child_draft=EvidenceDraft(status=ExecStatus.COMPLETED,
                    data_origin=DataOrigin.EXAMPLE if transport.injected or ctx.env.data_origin==DataOrigin.EXAMPLE else DataOrigin.MEASURED,
                    normal_control={'attempted':True,'succeeded':len(local_controls)==len(fixture['cases'])+1 and all(v=='pass'for v in local_controls.values())})
                child_draft=native_input_link.finish(child,child_draft)
                group['source']=source_facts(child,child_draft,capture,fixture)
                if child_draft.status!=ExecStatus.COMPLETED:
                    self._matrix_cleanup.extend(child_draft.cleanup_errors);self._matrix_incomplete=True
                # Original nonsecret HMAC snapshots accompany the summary in
                # EVD export, keeping the exact source bytes independently traceable.
                for artifact in child_draft.artifacts:
                    self._matrix_artifacts.append(DraftArtifact(name=f'matrix-{index}-'+artifact.name,path=artifact.path,collector=artifact.collector))
                for ordinal,path in enumerate(sorted((child.workdir/'native-input-originals').glob('pid-*.json'))):
                    self._matrix_artifacts.append(DraftArtifact(name=f'matrix-{index}-source-{ordinal:04d}.json',path=path,collector='native-input-original-hmac/1'))
            except Exception:
                group['diagnostic_codes'].append('INPUT_MATRIX_PAIR_CAPTURE_UNAVAILABLE');self._matrix_incomplete=True
            finally:
                if transport is not None:
                    report=transport.report();group['transport']=report;self._matrix_cleanup.extend(report['cleanup_errors'])
            controls.update({prefix+'|'+k:v for k,v in local_controls.items()})
            projection=(group.get('source')or{}).get('projection')or{}
            qualified=((group.get('source')or{}).get('qualification')or{}).get('registered')is True
            linked=(group.get('source')or{}).get('current_fixture_links_verified')is True
            for case in fixture['cases']:
                facts=projection.get('cases',{}).get(case,{})
                normal=local_controls.get('before')==local_controls.get('after_'+case)=='pass'
                verified=(normal and qualified and linked and outcomes.get(case)in ctx.policy_values['rejection_codes']
                    and facts.get('original_rejection_observed')is True and facts.get('normal_returns_observed')is True)
                rows.append({'case_id':prefix+'|'+case,'endpoint':fixture['endpoint'],'format':fixture['format'],'case':case,
                    'outcome':outcomes.get(case),'attempted':case in outcomes,'normal_controls_pass':normal,
                    'rejection_state':'verified'if verified else'unverified','native_original_rejection_observed':facts.get('original_rejection_observed')is True,
                    'request_fixture_sha256':hashlib.sha256(native_input.runtime.canonical(fixture['cases'][case])).hexdigest()})
        expected=planned(ctx.check,ctx.policy_values)
        complete=(len(rows)==len(expected)and [r['case_id']for r in rows]==expected
            and all(r['rejection_state']=='verified'for r in rows)and not self._matrix_cleanup)
        kinds = ('token_id', 'length')
        outcomes = {row['case_id']: row['outcome'] or 'unavailable' for row in rows}
        rejections = {row['case_id']: row['rejection_state'] for row in rows}
        # The pack requires both kind keys. Use the first planned case for
        # each key and include every case in the existing all_in rules.
        for kind in kinds:
            cases = [row for row in rows if row['case'] == kind]
            outcomes[kind] = outcomes[cases[0]['case_id']] if cases else 'unavailable'
            rejections[kind] = ('verified' if cases and all(row['rejection_state'] == 'verified' for row in cases)
                                else 'unverified')
        observations={'challenge_outcomes':outcomes,'accepted_challenges':[r['case_id']for r in rows
            if type(r['outcome'])is str and r['outcome'].isdigit()and 200<=int(r['outcome'])<300],
            'challenges_planned':list(kinds),'functional_probes_planned':control_plan,'functional_probe_results':controls,
            'input_endpoints_tested':[p for p in ctx.policy_values['allowed_endpoints']if p in endpoints],
            'input_formats_tested':[p for p in ctx.policy_values['allowed_input_formats']if p in formats],
            'challenge_rejection_results':rejections,'challenge_consumption':dict.fromkeys(kinds, 'unknown'),
            'confirmed_input_violations':[],'input_matrix_planned':expected,'input_matrix_cases':rows,'input_matrix_complete':complete}
        normal={'attempted':attempted>0,'succeeded':bool(control_plan)and set(control_plan)==set(controls)
            and all(v=='pass'for v in controls.values()),'detail':'Actual controls before and after every explicitly permitted endpoint/format boundary case.'}
        summary={'profile':PROFILE,'groups':groups,'cases':rows,'selected_input_matrix_complete':complete,
            'whole_parser_or_consumer_scope_verified':False,'fixture':{'profile':PROFILE,
                'planned_sha256':sha256_hex(canonical_json(expected)),'policy':ctx.policy_values,'vocab_source':self.cfg['vocab_source']}}
        return ({k:{'value':v,'collected':True}for k,v in observations.items()},normal,
            [{'step':step,'attempted':bool(rows)and all(r['attempted']for r in rows)}for step in ctx.check.challenge_steps],summary)
