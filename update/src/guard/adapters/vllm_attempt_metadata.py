"""Closed /10 invocation observation validation; no raw message admission."""
import hashlib
import re
from . import vllm_attempt_sensor as sensor

FIELDS={'asgi_request_sha256','asgi_invocation_sha256','entry_path_sha256','entry_root_path_sha256','entry_method_sha256',
    'phase','context_live','body_bytes','body_sha256','body_hash_complete','more_body','observer_code_sha256','observer_runtime'}
PHASES={'APP_ENTER','APP_RETURN','APP_RAISE','DISCONNECT_RECEIVED','BODY_SEND_RETURN','BODY_SEND_RAISE'}


def fingerprint(value):
    return type(value) is str and re.fullmatch(r'[0-9a-f]{64}',value) is not None


def validate_attempt(value):
    if type(value) is not dict or set(value)!=FIELDS or type(value['phase']) is not str or value['phase'] not in PHASES:
        raise ValueError('ATTEMPT_METADATA_INVALID')
    if (value['asgi_request_sha256'] is not None and not fingerprint(value['asgi_request_sha256'])
            or any(not fingerprint(value[k]) for k in ('asgi_invocation_sha256','entry_path_sha256','entry_root_path_sha256','entry_method_sha256'))
            or value['entry_path_sha256']!=hashlib.sha256(b'/v1/completions').hexdigest()
            or value['entry_method_sha256']!=hashlib.sha256(b'POST').hexdigest()
            or type(value['context_live']) is not bool or type(value['body_hash_complete']) is not bool
            or value['more_body'] is not None and type(value['more_body']) is not bool):
        raise ValueError('ATTEMPT_CONTEXT_INVALID')
    runtime=value['observer_runtime']
    if (type(runtime) is not str or runtime not in sensor.RUNTIME_CODE_CONTRACTS
            or not fingerprint(value['observer_code_sha256']) or value['observer_code_sha256']!=sensor.RUNTIME_CODE_CONTRACTS[runtime]):
        raise ValueError('ATTEMPT_OBSERVER_CODE_UNAVAILABLE')
    size=value['body_bytes'];digest=value['body_sha256'];hashed=value['body_hash_complete']
    if value['phase'] not in {'BODY_SEND_RETURN','BODY_SEND_RAISE'}:
        if size is not None or digest is not None or hashed or value['more_body'] is not None:
            raise ValueError('ATTEMPT_NONBODY_FIELDS_INVALID')
    elif (size is not None and (type(size) is not int or not 0<=size<2**63)
            or hashed and (size is None or size>sensor.BODY_HASH_LIMIT or not fingerprint(digest))
            or not hashed and digest is not None or size==0 and hashed and digest!=hashlib.sha256(b'').hexdigest()):
        raise ValueError('ATTEMPT_BODY_FIELDS_INVALID')
