"""Connect original input rejection facts through the existing executor and judge.

The registered passive capture SDK remains byte-identical. This projection has
its own exact-source GPU qualification and cannot borrow whole-check status.
"""
from pathlib import Path
import hashlib
from guard.models import ExecStatus,canonical_json
from guard.runtime.bounded import strict_json
from .kubernetes_input_validation import KubernetesInputValidationExecutor
from .content_common import append_artifact,sample_artifact


def connect(ctx,draft,cfg):
    capture=getattr(ctx,'_native_input_capture',None)
    if capture is None or ctx.check.id!='REQ-09':return draft
    from guard.collectors import native_input,native_input_rejection_qualification as qualification
    result={'schema':'guard-current-original-input-rejection/1','projection':None,'qualification':None,
        'applied_cases':[],'diagnostic_codes':[],'scope_complete':False,'consumer_nonreach_verified':False}
    try:
        if draft.status!=ExecStatus.COMPLETED or 'NATIVE_INPUT_CAPTURE_INCOMPLETE'in draft.cleanup_errors:raise ValueError
        # Revalidate original private HMAC bytes. A summary dictionary, source
        # name, HTTP status or a historical capture alone supplies no proof.
        verified=native_input.verify_capture(capture.prepared,ctx.workdir/'native-input-originals',
            requests=capture.requests,expected_uid=capture.prepared_uid)
        artifact,=[a for a in draft.artifacts if a.name=='native_input_capture.json']
        envelope=strict_json(artifact.path.read_bytes(),limit=8*1024**2)
        value=envelope['capture']
        if(value.get('target_generation_stable')is not True or value.get('source_capture')!=verified
                or value.get('cases')!=capture.cases):raise ValueError
        reviewer=getattr(ctx.adapter,'native_input_rejection_facts',None)
        if not callable(reviewer):raise ValueError
        facts=reviewer(engine=ctx.env.engine.name,version=ctx.env.engine.version,image=ctx.env.engine.image_digest,review=verified)
        status=qualification.status(ctx.env.engine.name,ctx.env.engine.version,ctx.env.engine.image_digest)
        result.update(projection=facts,qualification=status)
        observations=draft.observations;outcomes=observations['challenge_outcomes'].value
        original=observations['challenge_rejection_results'].value
        if type(original)is not dict or type(outcomes)is not dict:raise ValueError
        updates=dict(original)
        # Reconstruct the unchanged bounded fixtures and compare their hashes
        # to the current source wrapper's actual dispatch records.
        control=ctx.check.functional_probes[0].body.copy()
        from .http_common import fill_model,request_model
        control=fill_model(control,request_model(ctx.env,ctx.target,transport=capture.transport))
        for case,prompt in{'token_id':[cfg['vocab_size']],'length':[0]*(ctx.policy_values['max_input_tokens']+1)}.items():
            expected={**control,'prompt':prompt}
            cases=[r for r in capture.cases if r['case']==case]
            row=facts.get('cases',{}).get(case,{})
            if(len(cases)==1 and cases[0]['dispatched']is True
                    and cases[0]['request_fixture_sha256']==hashlib.sha256(native_input.runtime.canonical(expected)).hexdigest()
                    and str(cases[0]['outcome'])==outcomes.get(case)
                    and str(cases[0]['outcome'])in ctx.policy_values['rejection_codes']
                    and row.get('original_rejection_observed')is True and row.get('normal_returns_observed')is True
                    and status['registered']is True):
                updates[case]='verified';result['applied_cases'].append(case)
        # Existing independent violations, functional controls and consumption
        # UNKNOWN remain unchanged. This only adds a verified rejection cause.
        observations['challenge_rejection_results'].value=updates
        from guard.models import Observation
        observations['native_original_rejection_facts']=Observation(value=facts,collected=True)
        if not status['registered']:result['diagnostic_codes'].append('NATIVE_REJECTION_PROJECTION_GPU_UNQUALIFIED')
    except Exception:result['diagnostic_codes'].append('NATIVE_REJECTION_CURRENT_PROOF_UNVERIFIED')
    sample_artifact(ctx,'native_original_rejection.json',result)
    return append_artifact(draft,ctx,'native_original_rejection.json','original-input-rejection/1')


class KubernetesObservedInputValidationExecutor(KubernetesInputValidationExecutor):
    def __init__(self,cfg,**kwargs):
        super().__init__(cfg,**kwargs)
        if 'native_input_capture'in cfg:self.version='0.7.2+k8s-native-rejection.1'

    def _finish(self,ctx):
        draft=super()._finish(ctx)
        return connect(ctx,draft,self.cfg)
