"""Prepare the existing native model reference scope from a saved generation target."""

from pathlib import Path

from guard.adapters.vllm_file_read_sensor import MAX_HASH_BYTES, MAX_SCOPE_BYTES, dependency, fingerprint
from guard.collectors.model_read_scope import build_scope
from guard.models import DataOrigin, Environment
from guard.runtime.bounded import strict_json


def prepare_scope(environment, target_file, model_manifest, model_root, *, expected_target_sha256,
                  approved_manifest_sha256, nonsecret_model_weights, hash_model_bytes=False,
                  max_hash_bytes=MAX_HASH_BYTES):
    """Bind the approved reference bytes to the saved non-lab generation owner."""
    from guard.update_validation import _plain_path
    from .lifecycle_transition import generation_native_target, validate_generation_http_target

    if not fingerprint(expected_target_sha256) or not fingerprint(approved_manifest_sha256):
        raise ValueError('TRANSITION_SCOPE_INPUT_DIGEST_INVALID')

    env_path = _plain_path(Path(environment))
    raw_env = strict_json(dependency(env_path, None), limit=MAX_SCOPE_BYTES)
    if type(raw_env) is not dict or raw_env.get('kind') != 'ENV':
        raise ValueError('TRANSITION_SCOPE_SEALED_ENV_REQUIRED')
    env = Environment.model_validate(raw_env)
    if not env.hash_ok() or env.data_origin == DataOrigin.EXAMPLE:
        raise ValueError('TRANSITION_SCOPE_SEALED_ENV_REQUIRED')

    path = _plain_path(Path(target_file))
    target = strict_json(dependency(path, expected_target_sha256), limit=65536)
    if (type(target) is not dict or target.get('lab') is not False or target.get('synthetic', False) is not False
            or target.get('engine_kind') != env.engine.name or target.get('observed_version') != env.engine.version
            or target.get('environment_ref') != env.ref().model_dump(mode='json')):
        raise ValueError('TRANSITION_SCOPE_GENERATION_TARGET_INVALID')
    validate_generation_http_target(target, env)
    native = generation_native_target(target, env, require_lab=False)
    reference = _plain_path(Path(model_manifest))
    return build_scope(env, native, reference, model_root, nonsecret_model_weights=nonsecret_model_weights,
                       hash_model_bytes=hash_model_bytes, max_hash_bytes=max_hash_bytes,
                       approved_manifest_sha256=approved_manifest_sha256)
