"""Reviewed 0.31 Responses shapes; fingerprints never prove an actual owner."""
from guard.models import canonical_json,sha256_hex

REVIEWED_SOURCES={
 'api_router':'cffe4bb3cd4cfa2bfd71039c0125b68ad730f1d302a37e128f1055a335c503a3',
 'serving':'c701580373b32b57e2da6091ec3c242bbae0439974b5af088ba93cc588aaf3c8',
 'protocol':'5fa24e326726dd3e73b15314921f99dca2bf2f587e0bb2b1b286b525ed218460'}
STATUSES=frozenset({'queued','in_progress','completed','cancelled','failed','incomplete'})


def creation_body(model,object_id,*,background=False,previous=None,overwrite=False):
    value={'model':model,'input':'goodbye' if overwrite=='cross' else 'hello','max_output_tokens':1,'temperature':0,
        'stream':False,'store':previous is None,'background':background,'request_id':object_id,
        'metadata':{'guard_probe':'owned-cross-overwrite' if overwrite=='cross' else 'owned-self-overwrite' if overwrite=='owner' else 'owned-original'}}
    if previous is not None:value['previous_response_id']=previous
    return value


def response_projection(value,*,expected_id,api_model,previous=None):
    valid=(type(value) is dict and value.get('id')==expected_id and value.get('object')=='response'
        and value.get('model')==api_model and type(value.get('status')) is str and value['status'] in STATUSES
        and type(value.get('output')) is list and len(value['output'])<=16)
    metadata=value.get('metadata') if type(value) is dict else None
    marker=metadata.get('guard_probe') if type(metadata) is dict else None
    return {'object_shape_verified':valid,'object_id_matches':type(value) is dict and value.get('id')==expected_id,
        'probe_marker':marker if valid and type(marker) is str and marker in {'owned-original','owned-self-overwrite','owned-cross-overwrite'} else None,
        'status':value['status'] if valid else None,
        'content_sha256':sha256_hex(canonical_json({'output':value['output'],'metadata':value.get('metadata')})) if valid else None,
        'previous_id_matches':(type(value) is dict and value.get('previous_response_id')==previous) if previous is not None else None,
        'object_owner_verified':False,'store_effect_verified':False}
