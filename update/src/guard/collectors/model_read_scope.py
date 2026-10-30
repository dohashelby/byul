"""Build an explicit nonsecret safetensors reference scope without model I/O."""
from copy import deepcopy
from pathlib import Path,PurePosixPath
import json
from guard.adapters import vllm_file_read_sensor as sensor
from guard.collectors.model_manifest import _load_manifest
from guard.models import canonical_json,sha256_hex
from guard.executors.owned_capture import strict_json


def build_scope(env,target,manifest_path,model_root,*,nonsecret_model_weights,hash_model_bytes=False,max_hash_bytes=sensor.MAX_HASH_BYTES):
    from guard.executors.kubernetes_lifecycle_capture import validate_target
    validate_target(target,env)
    if (type(env.model.id) is not str or not env.model.id or type(env.model.revision) is not str or not env.model.revision
            or not sensor.fingerprint(env.model.inventory_sha256)):raise ValueError('MODEL_READ_SCOPE_MODEL_BINDING_REQUIRED')
    if (nonsecret_model_weights is not True or type(model_root) is not str or not model_root.startswith('/')
            or str(PurePosixPath(model_root))!=model_root or '..' in PurePosixPath(model_root).parts
            or '\\' in model_root or '\x00' in model_root or len(model_root)>4096):raise ValueError('MODEL_READ_SCOPE_ROOT_INVALID')
    # A file-only reference input: do not accept a directory and scan its bytes.
    raw=Path(manifest_path).read_bytes()
    manifest=_load_manifest(strict_json(raw,limit=16*1024**2),None)
    if (manifest.get('schema')!='guard.model-manifest/1' or manifest.get('model_id')!=env.model.id
            or manifest['inventory_sha256']!=env.model.inventory_sha256):raise ValueError('MODEL_READ_SCOPE_MANIFEST_BINDING_INVALID')
    rows=[]
    for row in manifest['files']:
        if row['path'].endswith('.safetensors'):
            rows.append({'normalized_path_sha256':sha256_hex(str(PurePosixPath(model_root)/row['path']).encode()),
                'reference_sha256':row['sha256'],'bytes':row['bytes']})
    result={'schema':sensor.SCOPE_SCHEMA,'session':None,'input_scope_sha256':None,
        'environment_ref':env.ref().model_dump(mode='json'),'target_sha256':sha256_hex(canonical_json(target)),
        'model_sha256':sha256_hex(env.model.id.encode()),'revision_sha256':sha256_hex(env.model.revision.encode()),
        'inventory_sha256':manifest['inventory_sha256'],'manifest_bytes_sha256':sha256_hex(raw),
        'data_origin':'EXAMPLE' if env.data_origin.value=='EXAMPLE' else 'DECLARED','nonsecret_model_weights':nonsecret_model_weights,'hash_model_bytes':hash_model_bytes,
        'max_hash_bytes':max_hash_bytes,'files':rows}
    return sensor.validate_scope(result,session=None)


def load_scope(path):
    with Path(path).open('rb') as stream:raw=stream.read(sensor.MAX_SCOPE_BYTES+1)
    return sensor.validate_scope(strict_json(raw,limit=sensor.MAX_SCOPE_BYTES),session=None),raw


def deploy_scope(value,raw,session):
    result=deepcopy(value);result.update(session=session,input_scope_sha256=sha256_hex(raw))
    sensor.validate_scope(result,session=session)
    return (json.dumps(result,sort_keys=True,indent=2)+'\n').encode()


def scope_summary(value):
    return {key:value[key] for key in ('environment_ref','target_sha256','model_sha256','revision_sha256','inventory_sha256',
        'manifest_bytes_sha256','data_origin','nonsecret_model_weights','hash_model_bytes','max_hash_bytes','files')}


def validate_binding(prepared,env,target):
    if prepared.get('profile')!=sensor.PROFILE:return
    value=prepared.get('model_read_scope',{})
    if type(env.model.id) is not str or type(env.model.revision) is not str:raise ValueError('MODEL_READ_SCOPE_MODEL_BINDING_REQUIRED')
    if (value.get('environment_ref')!=env.ref().model_dump(mode='json') or value.get('target_sha256')!=sha256_hex(canonical_json(target))
            or value.get('model_sha256')!=sha256_hex(env.model.id.encode()) or value.get('revision_sha256')!=sha256_hex(env.model.revision.encode())
            or value.get('inventory_sha256')!=env.model.inventory_sha256):raise ValueError('MODEL_READ_SCOPE_TARGET_MISMATCH')
