"""Actual OnlineRenderer core request gates; no engine/client/HTTP identity claim."""
from __future__ import annotations
import asyncio
from .input_cpu import digest
from .structured_limits import observe_call


class NativeRequestRejected(Exception):
    """Internal control flow only after an actual typed 400/422 ErrorResponse."""


def fixtures():
    rows=[]
    for key,ids,expected in [('reuse-valid',[3,4],'accepted'),('reuse-empty',[],'rejected'),
                             ('reuse-bool',[True],'rejected'),('reuse-negative',[-1],'rejected'),
                             ('reuse-oov',[32],'rejected'),('reuse-long',[3]*16,'rejected')]:
        rows.append({'id':key,'check_id':'REQ-08','fields':{'kv_transfer_params':{'prompt_token_ids':ids}},
                     'expected':expected,'trust_template':False,'trust_mm':False})
    rows.append({'id':'reuse-echo-ignores-ids','check_id':'REQ-08',
                 'fields':{'echo':True,'kv_transfer_params':{'prompt_token_ids':[-1]}},
                 'expected':'accepted','trust_template':False,'trust_mm':False})
    for key,fields,trust,expected in (
        ('template-untrusted',{'chat_template':'hello world'},False,'rejected'),
        ('template-nested-untrusted',{'chat_template_kwargs':{'chat_template':'hello world'}},False,'rejected'),
        ('template-trusted',{'chat_template':'hello world'},True,'accepted'),
        ('kwargs-trusted-boundary',{'chat_template':'{{ suffix }}','chat_template_kwargs':{'suffix':'hello '*15}},True,'accepted'),
        ('kwargs-trusted-overbound',{'chat_template':'{{ suffix }}','chat_template_kwargs':{'suffix':'hello '*16}},True,'rejected')):
        rows.append({'id':key,'check_id':'REQ-07','fields':fields,'expected':expected,'trust_template':trust,'trust_mm':False})
    for key,fields,trust,expected in (
        ('no-mm-overrides',{},False,'accepted'),
        ('code-revision-default-deny',{'mm_processor_kwargs':{'code_revision':'b1-local-fixture'}},False,'rejected'),
        ('media-default-deny',{'media_io_kwargs':{'video':{'num_frames':1}}},False,'rejected'),
        ('code-revision-opt-in-text',{'mm_processor_kwargs':{'code_revision':'b1-local-fixture'}},True,None)):
        rows.append({'id':key,'check_id':'SUP-08','fields':fields,'expected':expected,'trust_template':False,'trust_mm':trust})
    return rows


def describe(case):
    return {'case_id':case['id'],'check_id':case['check_id'],'stage':'OnlineRenderer.render_chat_then_admission',
            'fixture_canonical_sha256':digest(case),'expected':case['expected']}


def run_reference(processor, online_class, request_class, error_response_class, rejection_type, tracked=()):
    rows=[]
    def request(fields):
        return request_class(model='fixture-model',messages=[{'role':'user','content':'hello world'}],max_tokens=1,**fields)
    for case in fixtures():
        online=online_class(processor.model_config,processor.renderer,request_logger=None,chat_template=None,
                            chat_template_content_format='auto',trust_request_chat_template=case['trust_template'],
                            trust_request_mm_kwargs=case['trust_mm'])
        metadata={}
        def invoke(fields):
            obj=request(fields)
            result=asyncio.run(online.render_chat(obj))
            if isinstance(result,error_response_class):
                code=result.error.code
                if type(code) is int and code in (400,422):
                    metadata['rejection_basis']='native_typed_error_response'
                    metadata['native_error_code']=code
                    raise NativeRequestRejected()
                raise RuntimeError('Unexpected native error category')
            _,inputs=result
            params=obj.to_sampling_params(max_tokens=1,default_sampling_params={})
            admitted=processor.process_inputs('b1-online-fixture',inputs[0],params,('generate',))
            metadata.update(prompt_count=len(admitted.prompt_token_ids),
                            admitted_token_ids_canonical_sha256=digest(admitted.prompt_token_ids),
                            reuse_field_removed='prompt_token_ids' not in (obj.kv_transfer_params or {}),
                            sampling_reuse_field_present='prompt_token_ids' in ((params.extra_args or {}).get('kv_transfer_params') or {}))
        primary=observe_call(lambda:invoke(case['fields']),(rejection_type,NativeRequestRejected),tracked)
        row={**describe(case),**primary,'safe_metadata':dict(metadata)}
        row['after']=observe_call(lambda:invoke({}),(rejection_type,NativeRequestRejected),tracked)
        rows.append(row)
    return rows


def project(rows):
    specs={row['id']:describe(row) for row in fixtures()};seen=set();violations={}
    for row in rows:
        key=row.get('case_id')
        if key not in specs or key in seen:raise ValueError('unknown or duplicate online input case')
        seen.add(key);spec=specs[key]
        if any(row.get(k)!=spec[k] for k in ('check_id','stage','fixture_canonical_sha256')):
            raise ValueError('online input attribution mismatch')
        if spec['expected']=='rejected' and row.get('outcome')=='accepted':
            violations.setdefault(spec['check_id'],[]).append(key)
    return {check:{'online_input_confirmed_violations':{'value':violations.get(check,[]),'collected':any(r.get('check_id')==check for r in rows)}}
            for check in ('REQ-07','REQ-08','SUP-08')}


def judge_params():
    return {'fail_when_any':[{'obs':'online_input_confirmed_violations','op':'nonempty'}]}
