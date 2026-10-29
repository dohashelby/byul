"""Standalone stdlib observer for selected template calls; never changes arguments."""
from __future__ import annotations
import hashlib
import json

SCHEMA='byul-template-boundary-snapshot/1'
POINTS=frozenset({'safe_apply_chat_template','resolve_chat_template','tokenizer.apply_chat_template',
    'SandboxedEnvironment.is_safe_attribute','ImmutableSandboxedEnvironment.is_safe_attribute'})
NORMAL=[{'role':'user','content':'guard sandbox control'}]
CHALLENGE=[{'role':'user','content':'guard sandbox boundary'}]
MAX_TEXT=8192
MAX_IDS=512


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _text(value):
    if type(value) is not str or len(value)>MAX_TEXT:return {'status':'unavailable'}
    return {'status':'collected','kind':'text','count':len(value),'sha256':hashlib.sha256(value.encode()).hexdigest()}


def _summary(value):
    if type(value) is str:return _text(value)
    if type(value) in (list,tuple) and len(value)<=MAX_IDS and all(type(x) is int and x>=0 for x in value):
        return {'status':'collected','kind':'token_ids','count':len(value),'sha256':digest(list(value))}
    return {'status':'unavailable'}


def snapshot_template_boundary(point,args,kwargs,*,phase,result=None,exception=None,binding=None):
    out={'schema':SCHEMA,'point':point if type(point) is str and point in POINTS else 'unsupported',
        'phase':phase if type(phase) is str and phase in ('CALL','RETURN','RAISE') else 'unsupported',
        'status':'unavailable','metadata':{},'binding':{}}
    try:
        if out['point']=='unsupported' or out['phase']=='unsupported':return out
        if type(args) not in (list,tuple) or type(kwargs) is not dict:return out
        if type(binding) is dict:
            out['binding']={k:v for k,v in binding.items() if k in ('asgi_request_id_sha256','native_request_id_sha256','call_id_sha256','session_id_sha256','process_start_sha256','parent_call_id_sha256','invocation_id_sha256') and type(v) is str and len(v)==64 and all(c in '0123456789abcdef' for c in v)}
            if type(binding.get('pid')) is int and 0<binding['pid']<2**63:out['binding']['pid']=binding['pid']
        data=out['metadata']
        if point.endswith('.is_safe_attribute'):
            attr=kwargs.get('attr',args[2] if len(args)>2 else None)
            obj=kwargs.get('obj',args[1] if len(args)>1 else None)
            data['object_case']='integer_one' if type(obj) is int and obj==1 else 'other'
            data['attribute']='__class__' if type(attr) is str and attr=='__class__' else 'other' if type(attr) is str else 'unavailable'
            context=kwargs.get('evaluation_context')
            data['evaluation_context']=context if type(context) is str and context in ('compile_or_optimize','template_runtime','unavailable') else 'unavailable'
            if phase=='RETURN':
                data['attribute_result_status']='collected' if type(result) is bool else 'unavailable'
                data['attribute_allowed']=result if type(result) is bool else None
        else:
            # Only explicit builtin containers are inspected. Never consume an iterator.
            conversation=kwargs.get('conversation',kwargs.get('messages'))
            if conversation is None and point=='safe_apply_chat_template' and len(args)>2:conversation=args[2]
            if type(conversation) in (list,tuple) and len(conversation)==1 and type(conversation[0]) is dict:
                m=conversation[0]
                if set(m)=={'role','content'} and type(m['role']) is str and type(m['content']) is str and len(m['content'])<=MAX_TEXT:
                    msgs=[{'role':m['role'],'content':m['content']}]
                    data.update(messages_sha256=digest(msgs),messages_case='normal' if msgs==NORMAL else 'challenge' if msgs==CHALLENGE else 'other')
            template=kwargs.get('chat_template')
            data['template']=_text(template)
            if phase=='RETURN':
                data['result_summary']=_summary(result)
                if point=='resolve_chat_template':data['resolved_template']=_text(result)
        if phase=='RAISE':data['exception_type']=type(exception).__name__ if isinstance(exception,BaseException) else None
        out['status']='collected'
    except BaseException:
        out['status']='unavailable';out['metadata']={}
    return out
