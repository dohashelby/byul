"""B-only V1 slot-mask observer: bounded host reads, no GPU value readback."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import secrets
import threading
import types
import weakref

SCHEMA='byul-v1-slot-mask-snapshot/1'
POINTS=frozenset({'InputBatch.__init__','InputBatch.add_request','InputBatch.remove_request',
    'InputBatch.condense','InputBatch.swap_states','InputBatch.refresh_metadata','Sampler.forward'})
MAX_ROWS=8
MAX_COLUMNS=262144
MAX_OBJECTS=128
_LOCK=threading.Lock()
_OBJECTS=[]
_CALL_ROWS={}
_PID=os.getpid()
_MISSING=object()


BINDING_HASHES = ('asgi_request_id_sha256', 'native_request_id_sha256',
                  'call_id_sha256', 'session_id_sha256', 'process_start_sha256')

def _get(value, name, default=None):
    """Read fields/slots; do not invoke arbitrary properties or custom repr."""
    if type(value) is dict:
        return value.get(name, default)
    try:
        fields = object.__getattribute__(value, '__dict__')
    except (AttributeError, TypeError):
        fields = {}
    if type(fields) is dict and name in fields:
        return fields[name]
    descriptor = inspect.getattr_static(type(value), name, _MISSING)
    if descriptor is None or type(descriptor) in (bool, int, float, str):
        return descriptor
    if type(descriptor) is types.MemberDescriptorType:
        try:
            return descriptor.__get__(value, type(value))
        except (AttributeError, TypeError):
            pass
    return default

def _sha(value):
    return type(value) is str and len(value) == 64 and all(char in '0123456789abcdef' for char in value)

def _binding(value):
    if type(value) is not dict:
        return {}
    result = {key: value[key] for key in BINDING_HASHES if _sha(value.get(key))}
    if type(value.get('pid')) is int and 0 < value['pid'] < 2**63:
        result['pid'] = value['pid']
    return result

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _token(value):
    """Weak process-local object identity; never expose a pointer or retain it."""
    global _PID
    if value is None or not _LOCK.acquire(blocking=False):return None
    try:
        if _PID!=os.getpid():_OBJECTS.clear();_CALL_ROWS.clear();_PID=os.getpid()
        _OBJECTS[:]=[(ref,token) for ref,token in _OBJECTS if ref() is not None]
        for ref,token in _OBJECTS:
            if ref() is value:return token
        if len(_OBJECTS)>=MAX_OBJECTS:return None
        try:ref=weakref.ref(value)
        except TypeError:return None
        token=secrets.token_hex(32);_OBJECTS.append((ref,token));return token
    finally:_LOCK.release()


def _native_field(value,name):
    result=_get(value,name,None)
    if result is not None:return result
    descriptor=inspect.getattr_static(type(value),name,None)
    if type(descriptor) is types.GetSetDescriptorType:
        try:return descriptor.__get__(value,type(value))
        except Exception:return None
    return None


def _integer(value):return value if type(value) is int and 0<=value<2**63 else None


def tensor_descriptor(value):
    if type(value).__module__!='torch' or type(value).__name__!='Tensor':return {'status':'unavailable'}
    try:
        device=_native_field(value,'device');device_type=_native_field(device,'type')
        shape=_native_field(value,'shape');dtype=_native_field(value,'dtype')
        if type(device_type) is not str or device_type not in ('cpu','cuda'):return {'status':'unavailable'}
        if not isinstance(shape,tuple) or not 0<=len(shape)<=3 or any(type(v) is not int or v<0 for v in shape):return {'status':'unavailable'}
        if type(dtype).__module__=='torch' and type(dtype).__name__=='dtype':dtype=str(dtype)
        if dtype not in ('torch.bool','torch.float32','torch.float16','torch.bfloat16','torch.int32','torch.int64'):return {'status':'unavailable'}
        return {'status':'collected','device_type':device_type,'dtype':dtype,'shape':list(shape),'values_collected':False}
    except Exception:return {'status':'unavailable'}


def cpu_mask_row(mask,row):
    descriptor=tensor_descriptor(mask)
    if (descriptor.get('device_type')!='cpu' or descriptor.get('dtype')!='torch.bool'
            or len(descriptor.get('shape',[]))!=2 or type(row) is not int
            or not 0<=row<descriptor['shape'][0] or descriptor['shape'][1]>MAX_COLUMNS):return {'status':'unavailable'}
    try:
        values=mask[row].tolist()  # CPU-only; never .cpu(), .numpy() or GPU copy.
        if type(values) is not list or len(values)!=descriptor['shape'][1] or any(type(v) is not bool for v in values):return {'status':'unavailable'}
        return {'status':'collected','cells':len(values),'masked_count':sum(values),'mask_sha256':digest(values)}
    except Exception:return {'status':'unavailable'}


def cpu_output(value):
    descriptor=tensor_descriptor(value)
    shape=descriptor.get('shape',[])
    if descriptor.get('device_type')!='cpu' or descriptor.get('dtype') not in ('torch.int32','torch.int64') or len(shape)!=2 or shape[0]>MAX_ROWS or shape[1]!=1:return {'status':'unavailable'}
    try:
        values=value.tolist()
        if any(type(row) is not list or len(row)!=1 or type(row[0]) is not int for row in values):return {'status':'unavailable'}
        return {'status':'collected','count':len(values),'sha256':digest([row[0] for row in values]),'row_sha256':[digest(row) for row in values]}
    except Exception:return {'status':'unavailable'}


def _rid(value):
    return hashlib.sha256(value.encode()).hexdigest() if type(value) is str and 0<len(value)<=512 else None


def _argument(args,kwargs,index,name):return kwargs.get(name) if name in kwargs else args[index] if len(args)>index else None


def _metadata(metadata):
    mask=_get(metadata,'allowed_token_ids_mask',_MISSING)
    descriptor={'status':'absent'} if mask is None else tensor_descriptor(mask)
    if descriptor.get('status')=='collected':
        descriptor['cpu_rows']=None
        shape=descriptor['shape']
        if descriptor.get('device_type')=='cpu' and descriptor.get('dtype')=='torch.bool' and len(shape)==2 and shape[0]<=MAX_ROWS:
            descriptor['cpu_rows']=[{'slot_index':index,'cpu_mask':cpu_mask_row(mask,index)} for index in range(shape[0])]
            descriptor['values_collected']=all(row['cpu_mask']['status']=='collected' for row in descriptor['cpu_rows'])
    return {'sampling_metadata_token':_token(metadata),'sampling_mask_token':_token(mask) if mask is not _MISSING else None,
            'sampling_mask_present':mask is not None if mask is not _MISSING else None,'sampling_mask':descriptor}


def _transition(data,phase,binding,mask,point):
    token=data.get('batch_token');call=binding.get('call_id_sha256')
    if token is None or call is None or not data.get('slot_map_complete') or not _LOCK.acquire(blocking=False):return {'status':'unavailable'}
    try:
        key=(token,call,point)
        current=[{'request_sha256':row['request_sha256'],'slot_index':row['slot_index']} for row in data['rows']]
        if phase=='CALL':
            if key in _CALL_ROWS:
                _CALL_ROWS[key]=None;return {'status':'unavailable'}
            if len(_CALL_ROWS)>=MAX_OBJECTS:return {'status':'unavailable'}
            _CALL_ROWS[key]=current;return {'status':'pending'}
        previous=_CALL_ROWS.pop(key,None)
    finally:_LOCK.release()
    if previous is None:return {'status':'unavailable'}
    vacated=sorted({row['slot_index'] for row in previous}-{row['slot_index'] for row in current})
    if len(vacated)>MAX_ROWS:return {'status':'unavailable'}
    return {'status':'collected','previous_rows':previous,
            'vacated_rows':[{'slot_index':index,'cpu_mask':{'status':'absent'} if mask is None else cpu_mask_row(mask,index)} for index in vacated]}


def snapshot_slot_mask(point,args,kwargs,*,phase,result=None,exception=None,binding=None):
    output={'schema':SCHEMA,'point':point if type(point) is str and point in POINTS else 'unsupported',
        'phase':phase if type(phase) is str and phase in ('CALL','RETURN','RAISE') else 'unsupported',
        'status':'unavailable','metadata':{},'binding':_binding(binding)}
    try:
        if output['point']=='unsupported' or output['phase']=='unsupported' or type(args) not in (tuple,list) or type(kwargs) is not dict:return output
        owner=args[0] if args else kwargs.get('self');data=output['metadata']
        if point=='Sampler.forward':
            metadata=_argument(args,kwargs,2,'sampling_metadata');data.update(_metadata(metadata))
            data['logits']=tensor_descriptor(_argument(args,kwargs,1,'logits'))
            if phase=='RETURN':data['sampled_output']=cpu_output(_get(result,'sampled_token_ids'))
        else:
            data['batch_token']=_token(owner)
            device=_native_field(_get(owner,'device'),'type')
            data['dimensions']={'vocab_size':_integer(_get(owner,'vocab_size')),
                'max_model_len':_integer(_get(owner,'max_model_len')),'max_num_reqs':_integer(_get(owner,'max_num_reqs')),
                'device_type':device if device in ('cpu','cuda') else None,
                'is_pooling_model':_get(owner,'is_pooling_model') if type(_get(owner,'is_pooling_model')) is bool else None}
            mapping=_get(owner,'req_id_to_index');restricted=_get(owner,'has_allowed_token_ids')
            mask=_get(owner,'allowed_token_ids_mask_cpu_tensor',_MISSING)
            data['host_mask']={'status':'absent'} if mask is None else tensor_descriptor(mask);data['rows']=[];data['slot_map_complete']=False
            if type(mapping) is dict and len(mapping)<=MAX_ROWS and type(restricted) is set:
                valid=all(_rid(rid) and type(index) is int and 0<=index<(data['dimensions']['max_num_reqs'] or 0) for rid,index in mapping.items())
                if valid:
                    data['rows']=[{'request_sha256':_rid(rid),'slot_index':index,'restricted':rid in restricted,
                                   'cpu_mask':{'status':'absent'} if mask is None else cpu_mask_row(mask,index)} for rid,index in sorted(mapping.items(),key=lambda item:item[1])]
                    data['slot_map_complete']=True
            data['has_allowed_token_ids']=bool(restricted) if type(restricted) is set else None
            data.update(_metadata(_get(owner,'sampling_metadata')))
            if point in ('InputBatch.condense','InputBatch.swap_states'):
                data['slot_transition']=_transition(data,phase,output['binding'],mask,point)
            if point=='InputBatch.swap_states':
                data['swap_indices']=[_integer(_argument(args,kwargs,1,'i1')),_integer(_argument(args,kwargs,2,'i2'))]
            elif point in ('InputBatch.add_request','InputBatch.remove_request'):
                request=_argument(args,kwargs,1,'request' if point.endswith('add_request') else 'req_id')
                rid=_get(request,'req_id') if point.endswith('add_request') else request
                data['argument_request_sha256']=_rid(rid)
                if point.endswith('add_request'):
                    if phase=='RETURN':data['assigned_slot_index']=_integer(result)
                    allowed=_get(_get(request,'sampling_params'),'allowed_token_ids')
                    if allowed is None:data['argument_constraint']={'status':'collected','restricted':False,'count':0,'sha256':digest(None)}
                    elif type(allowed) is list and len(allowed)<=MAX_COLUMNS and all(type(v) is int and v>=0 for v in allowed):
                        data['argument_constraint']={'status':'collected','restricted':bool(allowed),'count':len(allowed),'sha256':digest(allowed)}
        if phase=='RAISE':data['exception_type']=type(exception).__name__ if type(exception).__name__ in ('ValueError','TypeError','RuntimeError','AssertionError') else 'Exception'
        output['status']='collected' if data else 'unavailable'
        return output
    except Exception:
        output['status']='partial' if output['metadata'] else 'unavailable';output['diagnostic']='SLOT_SNAPSHOT_UNAVAILABLE';return output
