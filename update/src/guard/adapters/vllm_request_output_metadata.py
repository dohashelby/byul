"""Closed /11 output-link contract: structural/hash metadata only."""
import hashlib
import re
from . import vllm_request_output_sensor as sensor

# Native ID comes from EngineCoreRequest/RequestState. Collector.request_id and
# RequestOutput.request_id are external IDs and stay separately named.
LINK_FIELDS=frozenset({'schema','point','phase','call_sha256','source_sha256','asgi_request_sha256','asgi_invocation_sha256',
    'asgi_context_live','collector_sha256','state_sha256','native_request_sha256','external_request_sha256',
    'provided_output_object_sha256','output_object_sha256','stored_output_object_sha256',
    'provided_text','returned_text','stored_text','output_present','observer_runtime','observer_code_sha256'})
TEXT_FIELDS=frozenset({'output_count','outputs_complete','items'})
ITEM_FIELDS=frozenset({'index','text_chars','text_bytes','text_sha256','text_hash_complete'})


def fingerprint(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def validate_text(value):
    if type(value) is not dict or set(value)!=TEXT_FIELDS:raise ValueError('OUTPUT_TEXT_INVALID')
    count=value['output_count'];items=value['items'];complete=value['outputs_complete']
    if (count is not None and (type(count) is not int or not 0<=count<2**63)
            or type(complete) is not bool or type(items) is not list or len(items)>sensor.MAX_OUTPUTS
            or count is None and (items or complete) or count is not None and len(items)!=min(count,sensor.MAX_OUTPUTS)):
        raise ValueError('OUTPUT_TEXT_BOUND_INVALID')
    for index,item in enumerate(items):
        if type(item) is not dict or set(item)!=ITEM_FIELDS or type(item['index']) is not int or item['index']!=index:
            raise ValueError('OUTPUT_TEXT_ITEM_INVALID')
        chars,size,digest,hashed=(item[k] for k in ('text_chars','text_bytes','text_sha256','text_hash_complete'))
        if (chars is not None and (type(chars) is not int or not 0<=chars<2**63)
                or size is not None and (type(size) is not int or not 0<=size<=4*sensor.TEXT_HASH_LIMIT)
                or type(hashed) is not bool or hashed and (chars is None or size is None or chars>sensor.TEXT_HASH_LIMIT
                    or size>sensor.TEXT_HASH_LIMIT or not fingerprint(digest))
                or not hashed and digest is not None
                or chars is None and (size is not None or hashed)
                or chars is not None and chars>sensor.TEXT_HASH_LIMIT and size is not None
                or chars is not None and chars<=sensor.TEXT_HASH_LIMIT and size is None
                or size is not None and not chars<=size<=4*chars
                or size==0 and (not hashed or digest!=hashlib.sha256(b'').hexdigest())):
            raise ValueError('OUTPUT_TEXT_HASH_INVALID')
    if complete!=bool(count is not None and 0<count<=sensor.MAX_OUTPUTS and all(i['text_hash_complete'] for i in items)):
        raise ValueError('OUTPUT_TEXT_COMPLETENESS_INVALID')


def validate_output_link(value):
    if (type(value) is not dict or set(value)!=LINK_FIELDS or value['schema']!=sensor.OUTPUT_SCHEMA
            or type(value['point']) is not str or value['point'] not in sensor.POINTS
            or type(value['phase']) is not str or value['phase'] not in {'CALL','RETURN','RAISE'}
            or value['source_sha256']!=sensor.SOURCE_SHA256 or not fingerprint(value['call_sha256'])):
        raise ValueError('OUTPUT_LINK_INVALID')
    hashes={k for k in LINK_FIELDS if k.endswith('_sha256')}-{'source_sha256','call_sha256','observer_code_sha256'}
    if (any(value[k] is not None and not fingerprint(value[k]) for k in hashes)
            or type(value['asgi_context_live']) is not bool or type(value['output_present']) is not bool
            or value['asgi_context_live'] and not fingerprint(value['asgi_invocation_sha256'])):
        raise ValueError('OUTPUT_LINK_BINDING_INVALID')
    runtime=value['observer_runtime']
    if type(runtime) is not str or runtime not in sensor.RUNTIME_CODE_CONTRACTS or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]:
        raise ValueError('OUTPUT_OBSERVER_CODE_UNAVAILABLE')
    for name in ('provided_text','returned_text','stored_text'):validate_text(value[name])
    returning=value['phase']=='RETURN' and value['point'] in {'RequestState.make_request_output','RequestOutputCollector.get','RequestOutputCollector.get_nowait'}
    if (not returning and (value['output_present'] or value['output_object_sha256'] is not None
            or value['returned_text']['output_count'] is not None)
            or not value['output_present'] and (value['output_object_sha256'] is not None or value['returned_text']['output_count'] is not None)
            or value['point']!='RequestOutputCollector.put' and (value['provided_output_object_sha256'] is not None
                or value['provided_text']['output_count'] is not None)
            or not value['point'].startswith('RequestOutputCollector.') and (value['stored_output_object_sha256'] is not None
                or value['stored_text']['output_count'] is not None)
            or value['point'].startswith('RequestOutputCollector.') and (value['native_request_sha256'] is not None or value['state_sha256'] is not None)):
        raise ValueError('OUTPUT_LINK_PHASE_INVALID')
