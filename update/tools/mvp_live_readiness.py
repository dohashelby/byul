"""Inventory all active MVP sides and verify supplied files without running engines.

This is a preparation gate, not runtime qualification or a security result.
Enterprise values, current ENV/target identities and results are never generated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
import json
from pathlib import Path
import tomllib

import yaml

from guard.adapters import vllm_initialization_release_sensor, vllm_initialization_sensor
from guard.adapters import vllm_native_input_profiles, vllm_native_input_rejection
from guard.adapters.engine_provider import get_provider
from guard.adapters.resource_recovery_probe import MAX_INPUT as _RECOVERY_SELECTION_LIMIT
from guard.adapters.vllm_file_read_sensor import MAX_SCOPE_BYTES
from guard.application import _scoped_target_sha256
from guard.collectors import native_input, native_input_qualification
from guard.collectors import native_input_rejection_qualification, reused_input_qualification
from guard.collectors.file import load_environment, load_policy
from guard.configuration import (CILIUM_EGRESS_STATUS_OPTION, GATEWAY_RECORD_SNAPSHOT_OPTION,
                                 INSTALLED_PLUGIN_INVENTORY_OPTION, KUBERNETES_BOUND_CHECK_OPTIONS,
                                 NORMAL_COMPILE_CACHE_OPTION,
                                 NORMAL_FILE_READS_OPTION, NORMAL_GRAPH_CAPTURE_OPTION,
                                 NORMAL_REQUEST_BINDING_OPTION, NORMAL_REQUEST_BINDING_SCOPE_ERROR,
                                 NORMAL_REQUEST_LATENCY_OPTION,
                                 NORMAL_SAFETENSORS_READER_OPTION,
                                 NORMAL_SERVING_READINESS_OPTION,
                                 NORMAL_STARTUP_LOAD_OPTION,
                                 NORMAL_TOKEN_INPUT_OPTION,
                                 RESPONSE_SNAPSHOT_INPUT, RESPONSE_SNAPSHOT_OPTION, RESPONSE_SNAPSHOT_SCOPE_ERROR,
                                 SUP05_PREPARATION_RECEIPT, SUP13_NORMAL_OPTION,
                                 bounded_metadata_scopes, cilium_egress_status_requested, collection_status_snapshot_requested,
                                 for_check, merge_config,
                                 installed_plugin_inventory_requested, normal_request_binding_requested,
                                 normal_request_latency_requested,
                                 normal_compile_cache_requested, normal_file_reads_requested,
                                 normal_graph_capture_requested,
                                 normal_safetensors_reader_requested,
                                 normal_serving_readiness_requested,
                                 normal_startup_load_requested,
                                 normal_token_input_requested, response_snapshot_is_prebound, response_snapshot_requested,
                                 retry_record_snapshot_requested, sup13_normal_requested,
                                 validate_normal_request_binding, validate_requested_config, validate_sup05_preparation_receipt)
from guard.core.execution_recipe import validate_policy_time
from guard.core.select import _in_scope, check_policy_value
from guard.executors import passive_api_hop, registry, resource_recovery_contract, run_binding
from guard.executors.kubernetes_installed_plugin_inventory import installed_plugin_inventory_transport
from guard.executors.kubernetes_normal_compile_cache import normal_compile_cache_transport
from guard.executors.kubernetes_normal_graph_capture import normal_graph_capture_transport
from guard.executors.kubernetes_normal_request_latency import normal_request_latency_transport
from guard.executors.kubernetes_normal_safetensors_reader import normal_safetensors_reader_transport
from guard.executors.kubernetes_normal_serving_readiness import normal_serving_readiness_transport
from guard.executors.kubernetes_normal_startup_load import normal_startup_load_transport
from guard.executors.kubernetes_normal_token_input import normal_input_transports
from guard.executors.kubernetes_passive_egress_policy import passive_egress_policy_transport
from guard.executors.kubernetes_response_content import _TARGET_BYTE_LIMIT as _RESPONSE_TARGET_BYTE_LIMIT
from guard.executors.lifecycle_transition import (REDEPLOY_CHECKS, REDEPLOY_SIDE_REUSE, _manifest_row,
                                                   endpoint_sensor, generation_identity, generation_sensor,
                                                   redeployment_side_reuse)
from guard.executors.supply_boot_contract import ROLES as SUPPLY_ROLES
from guard.input_matrix_policy import PROFILE as INPUT_MATRIX
from guard.models import Environment, canonical_json, load_pack, sha256_hex
from guard.reused_matrix_policy import PROFILE as REUSED_MATRIX
from guard.runtime.bounded import strict_json
from guard.runtime.kubernetes_http import (
    REQUEST_BINDING_PROFILE, KubernetesBindingError, _validate as validate_target_binding,
)
from guard.update_validation import _plain_path
from tools.mvp_a_input_preflight import _code, _pairs, _read
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'guard-mvp-live-input-sources/1'
SIDES = ('baseline', 'candidate')
_INPUT_FILES = ('environment', 'policy', 'configuration')
_INPUT_ROW_FIELDS = {'check_id', 'side', *_INPUT_FILES}
_INPUT_FILE_LIMITS = {'route_target': _RESPONSE_TARGET_BYTE_LIMIT, 'initialization_prepared': MAX_SCOPE_BYTES,
                      RESPONSE_SNAPSHOT_INPUT: MAX_SCOPE_BYTES}
_INITIALIZATION_PREPARED_PROFILES = {
    ('vllm', '0.30.0'): vllm_initialization_release_sensor.PROFILE,
    ('vllm', '0.31.0'): vllm_initialization_sensor.PROFILE,
}
_COLLECTION_STATUS_DIGESTS = {'target_file_sha256', 'record_session_sha256'}
OBSERVATION_RECORD_DIGESTS = _COLLECTION_STATUS_DIGESTS | set(run_binding.KEYS_BY_CHECK_3['OBS-01'])
_OBSERVATION_DENIED_ACTOR = 'observation_denied_actor_sha256'
_GATEWAY_ALLOWED_RECORD_DIGESTS = OBSERVATION_RECORD_DIGESTS - {_OBSERVATION_DENIED_ACTOR}
_RETRY_RECORD_DIGESTS = _COLLECTION_STATUS_DIGESTS | {'retry_scope_sha256', 'retry_record_source_sha256'}
_COPY_RECORD_DIGESTS = _COLLECTION_STATUS_DIGESTS | {'copy_inventory_sha256', 'copy_record_source_sha256'}
STARTUP_OBSERVERS = {
    'LIF-01': ('startup_load_source', partial(normal_startup_load_transport, allow_ranked=True), 'NORMAL_STARTUP_LOAD'),
    'SUP-07': ('compile_cache_source', normal_compile_cache_transport, 'NORMAL_COMPILE_CACHE'),
}


def _observation_actor_digests(effective, *, gateway_snapshot=False):
    actors = {'observation_allowed_actor_sha256': effective['observation_allowed_actor_sha256']}
    if not gateway_snapshot or _OBSERVATION_DENIED_ACTOR in effective:
        actors[_OBSERVATION_DENIED_ACTOR] = effective[_OBSERVATION_DENIED_ACTOR]
    return actors


def template(pack):
    return {'schema': SCHEMA, 'pack_ref': pack.ref().model_dump(mode='json'), 'inputs': []}


def _sup05_preparation_ref(value):
    reference = validate_sup05_preparation_receipt(value)
    return {**reference, 'path': str(Path(reference['path']).expanduser().absolute())}


def _sup05_preparation_refs(value):
    if type(value) is not dict or set(value) != set(SIDES):
        raise ValueError('SUP05_PAIRED_NORMAL_PREPARATIONS_REQUIRED')
    return {side: _sup05_preparation_ref(entry) for side, entry in value.items()}


def startup_observer_reason(check_id, target, env, *, runner=None):
    """Check the selected startup source contract and its saved prefix without target I/O."""
    contract, transport, reason = STARTUP_OBSERVERS[check_id]
    try:
        if get_provider(env.engine.name).evidence_contract(contract, env) is None:
            raise ValueError
    except ValueError:
        return reason + '_SOURCE_UNAVAILABLE'
    try:
        transport(target, env, duration=1, runner=runner)
    except (KubernetesBindingError, ValueError):
        return reason + '_TARGET_UNAVAILABLE'
    return None


def _native_matrix_blockers(expected, profile):
    identity = expected['engine'], expected['version'], expected['image']
    blockers = []
    try:
        vllm_native_input_profiles.select(expected['image'])
    except ValueError:
        blockers.append('NATIVE_INPUT_PROFILE_UNAVAILABLE')
    # Both matrix collectors use the original validation source pins. Each
    # projection still requires its own receipt for the exact image and source.
    try:
        vllm_native_input_rejection.profile(*identity)
    except (OSError, ValueError, TypeError, KeyError):
        blockers.append('NATIVE_REJECTION_PROFILE_UNAVAILABLE')
    if profile == INPUT_MATRIX:
        qualifier, prefix = native_input_rejection_qualification, 'NATIVE_REJECTION_PROJECTION'
    else:
        qualifier, prefix = reused_input_qualification, 'REUSED_INPUT_PROJECTION'
    for registry_, name in ((native_input_qualification, 'NATIVE_INPUT_CAPTURE'), (qualifier, prefix)):
        try:
            if registry_.status(*identity)['registered'] is not True:
                blockers.append(name + '_GPU_UNQUALIFIED')
        except (OSError, ValueError, TypeError, KeyError):
            blockers.append(name + '_QUALIFICATION_INVALID')
    return blockers


def _check_native_prepared(spec, target, env):
    if (type(spec) is not dict or set(spec) - {'prepared_dir', 'prepared_sha256', 'prepared_uid'}
            or type(spec.get('prepared_dir')) is not str or not spec['prepared_dir']
            or not native_input._sha(spec.get('prepared_sha256'))
            or ('prepared_uid' in spec and type(spec['prepared_uid']) is not int)):
        raise ValueError('NATIVE_INPUT_CAPTURE_SETTINGS_INVALID')
    if target.get('lab') is not True:
        raise ValueError('NATIVE_INPUT_CAPTURE_TARGET_MISMATCH')
    try:
        validate_target_binding(target, env)
    except KubernetesBindingError:
        raise ValueError('NATIVE_INPUT_CAPTURE_TARGET_MISMATCH') from None
    try:
        manifest, _, _ = native_input.load_prepared(
            Path(spec['prepared_dir']).absolute(), expected_uid=spec.get('prepared_uid'))
    except FileNotFoundError:
        raise ValueError('NATIVE_INPUT_PREPARED_MISSING') from None
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError('NATIVE_INPUT_PREPARED_INVALID') from None
    if (manifest['image'] != env.engine.image_digest
            or sha256(native_input.runtime.canonical(manifest)).hexdigest() != spec['prepared_sha256']):
        raise ValueError('NATIVE_INPUT_CAPTURE_BINDING_CHANGED')


def _generation_manifest_snapshot(cfg, bound_files):
    rows = cfg['generations']
    if type(rows) is not list or not 1 <= len(rows) <= 4:
        raise ValueError('TRANSITION_CONFIGURATION_INVALID')
    manifest = []
    for row in rows:
        if (type(row) is not dict or set(row) != {'id', 'environment', 'target', 'prepared'}
                or any(type(value) is not str or not value for value in row.values())):
            raise ValueError('TRANSITION_CONFIGURATION_INVALID')
        files = {}
        for key in ('environment', 'target', 'prepared'):
            path = row[key]
            raw, identity = _read(path)
            bound_files.append((path, raw, identity))
            files[key] = raw
        try:
            declared = yaml.safe_load(files['environment'])
            if type(declared) is not dict or declared.get('kind') != 'ENV':
                raise ValueError
            env = Environment.model_validate(declared)
            target = strict_json(files['target'], limit=65536)
            prepared = strict_json(files['prepared'], limit=262144)
            if (not env.hash_ok() or type(target) is not dict or type(prepared) is not dict
                    or prepared.get('profile') not in (generation_sensor.PROFILE, endpoint_sensor.PROFILE)):
                raise ValueError
            manifest.append(_manifest_row(row['id'], env, target, prepared))
        except (RecursionError, TypeError, ValueError, yaml.YAMLError):
            raise ValueError('TRANSITION_CONFIGURATION_INVALID') from None
    return manifest


def _paired_normal_control_requested(raw):
    if sup13_normal_requested(raw):
        return True
    overlays = raw.get('check_config')
    for check_id, executor in (('REQ-10', 'sampling_validation'), ('LIF-04', 'stream_buffer')):
        overlay = overlays.get(check_id) if type(overlays) is dict else None
        if any(type(settings) is dict and KUBERNETES_BOUND_CHECK_OPTIONS[check_id] in settings
               for settings in (raw.get(executor), overlay)):
            return True
    return False


def _is_sha256(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _read_input(pointer, *, configured_target=None, **bounds):
    if (type(pointer) is not dict or set(pointer) != {'path', 'sha256'}
            or type(pointer['path']) is not str or not pointer['path']
            or not _is_sha256(pointer['sha256'])):
        raise ValueError('MVP_INPUT_REFERENCE_INVALID')
    path = pointer['path']
    if configured_target is not None:
        if _plain_path(Path(path)) != _plain_path(Path(configured_target)):
            raise ValueError('MVP_TARGET_REFERENCE_PATH_MISMATCH')
        path = configured_target
    raw, identity = _read(path, **bounds)
    if sha256(raw).hexdigest() != pointer['sha256']:
        raise ValueError('MVP_INPUT_HASH_MISMATCH')
    return raw, identity


def _inspect(source, check, pack, issued_at, redeployment_identities, *, sup05_normal_preparation=None,
             expected_record_inputs=None):
    files, identities = {}, {}
    for role in _INPUT_FILES:
        files[role], identities[role] = _read_input(source[role])
    raw_env = json.loads(files['environment'], object_pairs_hook=_pairs)
    if raw_env.get('kind') != 'ENV':
        raise ValueError('MVP_SEALED_ENV_REQUIRED')
    env = load_environment(Path(source['environment']['path']), raw=files['environment'])
    expected = versions()[source['side']]
    if (env.engine.name, env.engine.version, env.engine.image_digest) != (
            expected['engine'], expected['version'], expected['image']):
        raise ValueError('MVP_ENGINE_SIDE_MISMATCH')
    if not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver:
        raise ValueError('MVP_GPU_FACTS_REQUIRED')
    policy = load_policy(Path(source['policy']['path']), pack, raw=files['policy'])
    if not _in_scope(check.id, policy):
        raise ValueError('MVP_POLICY_SCOPE_MISMATCH')
    values = policy.values.get(check.id) or {}
    for field in check.policy_params:
        if field.key not in values:
            if field.required:
                raise ValueError('MVP_POLICY_VALUE_MISSING')
        else:
            check_policy_value(check.id, field.key, field.type, values[field.key])
    from guard.configuration import UPDATE_CHECK_OUTSIDE_SCOPE, update_allowed_checks
    config = tomllib.loads(files['configuration'].decode('utf-8'))
    requested_update = config.get('update', {})
    allowed = update_allowed_checks(config, pack)
    if allowed is not None and check.id not in allowed:
        raise ValueError(UPDATE_CHECK_OUTSIDE_SCOPE)
    side_configs = {side: merge_config(config, config.get('sides', {}).get(side, {})) for side in SIDES}
    if response_snapshot_requested(config):
        validate_requested_config(config)
        if not response_snapshot_is_prebound(config):
            raise ValueError(RESPONSE_SNAPSHOT_SCOPE_ERROR)
    if normal_request_binding_requested(config):
        validate_requested_config(config)
        validate_normal_request_binding(config, policy)
        validate_policy_time(policy, as_of=issued_at)
    if any(_paired_normal_control_requested(raw)
           or cilium_egress_status_requested(raw) or installed_plugin_inventory_requested(raw)
           or collection_status_snapshot_requested(raw)
           or normal_compile_cache_requested(raw)
           or normal_file_reads_requested(raw)
           or normal_graph_capture_requested(raw)
           or normal_safetensors_reader_requested(raw)
           or normal_request_binding_requested(raw)
           or normal_request_latency_requested(raw)
           or normal_serving_readiness_requested(raw)
           or normal_startup_load_requested(raw)
           or normal_token_input_requested(raw)
           or response_snapshot_requested(raw)
           or retry_record_snapshot_requested(raw) for raw in side_configs.values()):
        bounded_metadata_scopes(side_configs.values(), pack, allowed)
    passive_hop = check.id == passive_api_hop.CHECK and passive_api_hop.validate_scope(
        pack, side_configs.values(), allowed, explicitly_requested=passive_api_hop.requested(config))
    if passive_hop and not _in_scope(passive_api_hop.SOURCE_CHECK, policy):
        raise ValueError('PASSIVE_API_HOP_SOURCE_NOT_ADMITTED')
    config = for_check(side_configs[source['side']], check.executor, check.id)
    implementation = config.get('executors', {}).get(check.executor)
    if implementation not in ('real', 'kubernetes'):
        raise ValueError('MVP_ACTIVE_EXECUTOR_REQUIRED')
    requires_native_matrix = getattr(check, 'collection_profile', None) in (INPUT_MATRIX, REUSED_MATRIX)
    if requires_native_matrix and implementation != 'kubernetes':
        raise ValueError('NATIVE_INPUT_KUBERNETES_REQUIRED')
    if registry.resolve_executor(check.executor, implementation, engine=env.engine.name,
                                 version=env.engine.version) is None:
        raise ValueError('MVP_EXECUTOR_UNAVAILABLE')
    cfg = config.get(check.executor, {})
    gateway_snapshot = check.id == 'OBS-01' and cfg.get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True
    external_kubernetes = implementation == 'kubernetes' and requested_update.get('mode', 'external') == 'external'
    prebound_kubernetes = (external_kubernetes
                           and requested_update.get('kubernetes_candidate_binding', 'prebound') == 'prebound')
    node_image_verification = (prebound_kubernetes and check.id == 'SUP-04' and check.executor == 'container_config'
                               and 'node_image_verification' in cfg)
    prebound_record_stages = prebound_kubernetes and run_binding.stages_for(check) == (1, 2, 3, 4)
    prebound_gateway_snapshot = (gateway_snapshot and prebound_record_stages
                                 and check.executor == 'required_event_record')
    normal_sampling = (prebound_kubernetes and check.id == 'REQ-10' and check.executor == 'sampling_validation'
                       and getattr(check, 'collection_profile', None) is None
                       and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['REQ-10']) is True)
    normal_stream = (prebound_kubernetes and check.id == 'LIF-04' and check.executor == 'stream_buffer'
                     and run_binding.stages_for(check) == (1,) and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['LIF-04']) is True)
    record_access = external_kubernetes and run_binding.is_record_access_binding(check)
    record_snapshot = (prebound_record_stages and check.id == 'OBS-01' and check.executor == 'required_event_record'
                       and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['OBS-01']) is True
                       and GATEWAY_RECORD_SNAPSHOT_OPTION not in cfg and KUBERNETES_BOUND_CHECK_OPTIONS['PLT-12'] not in cfg)
    copy_snapshot = (prebound_record_stages and check.id == 'PLT-12' and check.executor == 'required_event_record'
                     and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['PLT-12']) is True)
    retry_records = (prebound_record_stages and check.id == 'LIF-12' and check.executor == 'request_termination'
                     and not config.get('lab_activation'))
    if gateway_snapshot or record_snapshot or copy_snapshot:
        bounded_metadata_scopes(side_configs.values(), pack, allowed)
    observation_record_binding = record_snapshot or (
        prebound_gateway_snapshot and ('record_inputs' in source or expected_record_inputs is not None))
    record_source_key = None
    if record_access or observation_record_binding or copy_snapshot or retry_records:
        record_source_key = {
            'OBS-01': 'observation_expected_source_sha256',
            'OBS-06': 'record_inventory_source_sha256',
            'PLT-12': 'copy_record_source_sha256',
            'LIF-12': 'retry_record_source_sha256',
        }[check.id]
    normal_file_reads = check.id == 'SUP-05' and cfg.get(NORMAL_FILE_READS_OPTION) is True
    if check.id == 'SUP-05' and SUP05_PREPARATION_RECEIPT in cfg:
        saved_preparation = _sup05_preparation_ref(cfg[SUP05_PREPARATION_RECEIPT])
        if sup05_normal_preparation is not None and saved_preparation != sup05_normal_preparation:
            raise ValueError('SUP05_NORMAL_PREPARATION_REFERENCE_MISMATCH')
        sup05_normal_preparation = saved_preparation
    if sup05_normal_preparation is not None and not (
            normal_file_reads and prebound_kubernetes and check.executor == 'supply_boot'):
        raise ValueError('SUP05_NORMAL_PREPARATION_SCOPE_INVALID')
    supply_record_binding = (implementation == 'kubernetes' and run_binding.is_supply_record_binding(check)
                             and requested_update.get('mode', 'external') == 'external'
                             and requested_update.get('kubernetes_candidate_binding', 'prebound') == 'prebound')
    supply_record_roles = implementation == 'kubernetes' and (
        supply_record_binding or check.id == 'SUP-15'
        or run_binding.is_paired_supply_role_preflight(check, cfg, requested_update))
    generation_inputs = (implementation == 'kubernetes' and run_binding.is_lifecycle_generation_binding(check)
                         and requested_update.get('mode', 'external') == 'external' and 'generations' in cfg)
    initialization = (check.executor == 'initialization' and implementation == 'kubernetes'
                      and check.id in run_binding.INITIALIZATION_CHECKS
                      and {1, 2}.issubset(run_binding.stages_for(check)))
    pinned_initialization = 'initialization_prepared' in source
    if pinned_initialization and (
            not initialization or not prebound_kubernetes or run_binding.stages_for(check) != (1, 2)
            or values.get('init_phase') != 'normal' or config.get('lab_activation') or config.get('record_attestation')
            or type(cfg.get('prepared')) is not str or not cfg['prepared']):
        raise ValueError('MVP_INITIALIZATION_PREPARED_REFERENCE_SCOPE_INVALID')
    collection_status = (check.id == 'OBS-11' and check.executor == 'log_integrity' and implementation == 'kubernetes'
                         and requested_update.get('mode', 'external') == 'external' and 2 in run_binding.stages_for(check))
    if 'record_inputs' in source:
        if observation_record_binding:
            digests = OBSERVATION_RECORD_DIGESTS
        elif copy_snapshot:
            digests = _COPY_RECORD_DIGESTS
        elif retry_records and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['LIF-12']) is True:
            digests = _RETRY_RECORD_DIGESTS
        elif (collection_status and prebound_kubernetes and run_binding.stages_for(check) == (2,)
              and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['OBS-11']) is True):
            digests = _COLLECTION_STATUS_DIGESTS
        else:
            raise ValueError('MVP_RECORD_INPUT_SCOPE_INVALID')
        record_inputs = source['record_inputs']
        if (type(record_inputs) is not dict
                or (record_inputs.keys() != digests and not (
                    prebound_gateway_snapshot and record_inputs.keys() == _GATEWAY_ALLOWED_RECORD_DIGESTS))
                or not all(_is_sha256(digest) for digest in record_inputs.values())):
            raise ValueError('MVP_RECORD_INPUT_REFERENCE_INVALID')
        if expected_record_inputs is not None and record_inputs != expected_record_inputs:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
        expected_record_inputs = record_inputs
    local_metadata = (check.id in {'OBS-04', 'PLT-06'} and implementation == 'kubernetes'
                      and requested_update.get('mode', 'external') == 'external'
                      and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS[check.id]) is True)
    protected_paths = (prebound_kubernetes and check.id == 'PLT-02' and check.executor == 'unix_ipc'
                       and cfg.get(KUBERNETES_BOUND_CHECK_OPTIONS['PLT-02']) is True
                       and not config.get('record_attestation') and not config.get('lab_activation'))
    gateway_response_snapshot = (prebound_kubernetes and check.id == 'REQ-13'
                                 and check.executor == 'response_content'
                                 and cfg.get(RESPONSE_SNAPSHOT_OPTION) is True)
    if (gateway_response_snapshot != (RESPONSE_SNAPSHOT_INPUT in source)
            or gateway_response_snapshot and 'target' not in source):
        raise ValueError('MVP_RESPONSE_SNAPSHOT_REFERENCE_SCOPE_INVALID')
    gateway_response = (not gateway_response_snapshot and check.id == 'REQ-13' and check.executor == 'response_content'
                        and implementation == 'kubernetes' and 'gateway' in values['required_targets']
                        and config.get('update', {}).get('kubernetes_candidate_binding', 'prebound') == 'prebound')
    pinned_route = 'route_target' in source
    if pinned_route and (not prebound_kubernetes or not gateway_response
                         or type(cfg.get('route_target')) is not str or not cfg['route_target']):
        raise ValueError('MVP_ROUTE_TARGET_REFERENCE_SCOPE_INVALID')
    normal_graph_capture = check.id == 'REQ-20' and cfg.get(NORMAL_GRAPH_CAPTURE_OPTION) is True
    normal_safetensors_reader = check.id == 'SUP-12' and cfg.get(NORMAL_SAFETENSORS_READER_OPTION) is True
    normal_compile_cache = check.id == 'SUP-07' and cfg.get(NORMAL_COMPILE_CACHE_OPTION) is True
    normal_startup_load = check.id == 'LIF-01' and cfg.get(NORMAL_STARTUP_LOAD_OPTION) is True
    cilium_egress_status = check.id == 'PLT-07' and cfg.get(CILIUM_EGRESS_STATUS_OPTION) is True
    installed_plugin_inventory = check.id == 'SUP-10' and cfg.get(INSTALLED_PLUGIN_INVENTORY_OPTION) is True
    normal_request_latency = check.id == 'LIF-03' and cfg.get(NORMAL_REQUEST_LATENCY_OPTION) is True
    normal_serving_readiness = check.id == 'OBS-02' and cfg.get(NORMAL_SERVING_READINESS_OPTION) is True
    normal_token_input = check.id == 'REQ-09' and cfg.get(NORMAL_TOKEN_INPUT_OPTION) is True
    normal_request_binding = check.id == 'REQ-11' and cfg.get(NORMAL_REQUEST_BINDING_OPTION) is True
    if normal_request_binding and (not prebound_kubernetes or 'target' not in source):
        raise ValueError(NORMAL_REQUEST_BINDING_SCOPE_ERROR)
    target = cfg['normal']['target'] if normal_file_reads else cfg.get('target')
    if 'target' in source:
        other_targets = {'generations', 'native_route', 'challenge', 'target' if normal_file_reads else 'normal'}
        if (not prebound_kubernetes or type(target) is not str or not target or supply_record_roles
                or not other_targets.isdisjoint(cfg) or any(key.endswith('_target') for key in cfg)):
            raise ValueError('MVP_TARGET_REFERENCE_SCOPE_INVALID')
    if implementation == 'kubernetes' and target is None and not (supply_record_roles or generation_inputs):
        raise ValueError('MVP_CURRENT_TARGET_REQUIRED')
    bound_files = []
    binding_options = {}
    role_digests = {}
    prepared_raw_digests = {}
    record_digests = {}
    record_source_file = None
    preparation = None
    if sup05_normal_preparation is not None:
        from guard.executors.kubernetes_sup05_normal_preparation import read_normal_preparation

        receipt_path = _plain_path(Path(sup05_normal_preparation['path']))
        receipt_sha256 = sup05_normal_preparation['sha256']
        raw, identity = _read(receipt_path)
        if sha256_hex(raw) != receipt_sha256:
            raise ValueError('SUP05_PREPARATION_RECEIPT_INVALID')
        bound_files.append((receipt_path, raw, identity))
        preparation = read_normal_preparation(receipt_path, env, expected_sha256=receipt_sha256)
        if preparation['normal'] != cfg['normal']:
            raise ValueError('SUP05_NORMAL_PREPARATION_INPUT_MISMATCH')
    if supply_record_roles:
        for role in SUPPLY_ROLES:
            for key in ('target', 'prepared'):
                path = cfg[role][key]
                raw, identity = _read(path)
                bound_files.append((path, raw, identity))
                document = json.loads(raw, object_pairs_hook=_pairs)
                role_digests[f'supply_{role}_{key}_sha256'] = sha256_hex(canonical_json(document))
                if key == 'prepared':
                    prepared_raw_digests[role] = sha256_hex(raw)
    elif generation_inputs:
        generation_manifest = _generation_manifest_snapshot(cfg, bound_files)
    elif target is not None:
        if 'target' in source:
            bounds = {'max_bytes': MAX_SCOPE_BYTES} if gateway_response_snapshot else {}
            if normal_request_binding:
                bounds = {'max_bytes': 65536}
            raw_target, identity = _read_input(source['target'], configured_target=target, **bounds)
            files['target'], identities['target'] = raw_target, identity
        else:
            raw_target, identity = _read(target)
        target_data = json.loads(raw_target, object_pairs_hook=_pairs)
        if type(target_data) is not dict or target_data.get('environment_ref') != env.ref().model_dump(mode='json'):
            raise ValueError('MVP_TARGET_ENVIRONMENT_MISMATCH')
        if record_snapshot and expected_record_inputs is not None and target_data.get('synthetic', False) is not False:
            raise ValueError('MVP_RECORD_INPUT_SCOPE_INVALID')
        if gateway_snapshot:
            gateway_target_sha256 = _scoped_target_sha256(
                raw_target, env, gateway_snapshot=True, scoped_check=check.id)
        if 'target' not in source or source['target']['path'] != target:
            bound_files.append((target, raw_target, identity))
    if record_access or copy_snapshot or retry_records:
        scope_key, digest_key = {
            'OBS-06': ('subjects', 'record_subject_plan_sha256'),
            'PLT-12': ('copy_inventory', 'copy_inventory_sha256'),
            'LIF-12': ('retry_scope', 'retry_scope_sha256'),
        }[check.id]
        path = cfg[scope_key]
        raw, identity = _read(path)
        bound_files.append((path, raw, identity))
        digest_bytes = canonical_json(strict_json(raw, limit=262144)) if record_access else raw
        record_digests[digest_key] = sha256_hex(digest_bytes)
    if normal_file_reads:
        target_sha256 = _scoped_target_sha256(raw_target, env, scoped_check=check.id)
        normal_hashes = {}
        for role in ('prepared', 'reference_manifest'):
            path = cfg['normal'][role]
            raw, identity = _read(path)
            bound_files.append((path, raw, identity))
            normal_hashes[role] = sha256(raw).hexdigest()
        if preparation is not None:
            if (preparation['target_sha256'] != target_sha256 or preparation['target_file_sha256'] != sha256_hex(raw_target)
                    or preparation['expected_normal_file_sha256'] != normal_hashes):
                raise ValueError('SUP05_NORMAL_PREPARATION_INPUT_MISMATCH')
            target_sha256 = preparation['target_sha256']
            normal_hashes = preparation['expected_normal_file_sha256']
        binding_options['expected_target_sha256'] = target_sha256
        binding_options['expected_normal_file_sha256'] = normal_hashes
    if gateway_response_snapshot:
        pointer = source[RESPONSE_SNAPSHOT_INPUT]
        raw, identity = _read_input(pointer, configured_target=cfg[RESPONSE_SNAPSHOT_INPUT]['path'],
                                    max_bytes=MAX_SCOPE_BYTES)
        files[RESPONSE_SNAPSHOT_INPUT], identities[RESPONSE_SNAPSHOT_INPUT] = raw, identity
        config = {**config, '_response_snapshot_target_file_sha256': sha256_hex(raw_target)}
    if gateway_response and 'route_target' in cfg:
        path = cfg['route_target']
        if pinned_route:
            pointer = source['route_target']
            raw, identity = _read_input(pointer, configured_target=path, max_bytes=_RESPONSE_TARGET_BYTE_LIMIT)
            files['route_target'], identities['route_target'] = raw, identity
            digest = pointer['sha256']
        else:
            raw, identity = _read(path)
            bound_files.append((path, raw, identity))
            digest = sha256_hex(raw)
        config = {**config, '_response_route_target_sha256': digest}
    if cilium_egress_status:
        try:
            passive_egress_policy_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('CILIUM_EGRESS_STATUS_TARGET_UNAVAILABLE') from None
    if normal_startup_load or normal_compile_cache:
        reason = startup_observer_reason(check.id, target_data, env)
        if reason is not None:
            raise ValueError(reason)
    if normal_safetensors_reader:
        try:
            if get_provider(env.engine.name).evidence_contract('safetensors_reader_source', env) is None:
                raise ValueError
        except ValueError:
            raise ValueError('NORMAL_SAFETENSORS_READER_SOURCE_UNAVAILABLE') from None
        try:
            normal_safetensors_reader_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_SAFETENSORS_READER_TARGET_UNAVAILABLE') from None
    if normal_graph_capture:
        try:
            if get_provider(env.engine.name).evidence_contract('graph_capture_source', env) is None:
                raise ValueError
        except ValueError:
            raise ValueError('NORMAL_GRAPH_CAPTURE_SOURCE_UNAVAILABLE') from None
        try:
            normal_graph_capture_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_GRAPH_CAPTURE_TARGET_UNAVAILABLE') from None
    if installed_plugin_inventory:
        try:
            installed_plugin_inventory_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('INSTALLED_PLUGIN_INVENTORY_TARGET_UNAVAILABLE') from None
    if normal_request_latency:
        try:
            normal_request_latency_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_REQUEST_LATENCY_TARGET_UNAVAILABLE') from None
    if normal_serving_readiness:
        try:
            normal_serving_readiness_transport(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_SERVING_READINESS_TARGET_UNAVAILABLE') from None
    if normal_token_input:
        try:
            normal_input_transports(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_TOKEN_INPUT_TARGET_UNAVAILABLE') from None
    route_record_only = False
    if passive_hop:
        passive_api_hop.validate_policy(check, values)
        passive_api_hop.validate_target(target_data, env)
        binding = None  # The source evidence belongs to a future run attempt.
    else:
        run_cfg = run_binding.run_config(config, check.id)
        route_record_only = prebound_kubernetes and run_binding.native_route_record_only_preflight(check, cfg, run_cfg)
        if route_record_only:
            native_target = cfg['native_route']['target']
            raw, identity = _read(native_target)
            bound_files.append((native_target, raw, identity))
            binding_options['native_route_prepared_absent'] = True
        if record_source_key is not None and type(run_cfg) is dict and set(run_cfg) == run_binding.config_keys(check):
            path = run_cfg['record_producer']
            raw, identity = _read(path, min_bytes=0, max_bytes=run_binding._MAX_PRODUCER_BYTES)
            record_source_file = (path, raw, identity)
            record_digests[record_source_key] = sha256_hex(raw)
        if supply_record_binding and type(run_cfg) is dict and 'record_producer' in run_cfg:
            producer = run_binding.producer_sha256(run_cfg['record_producer'])
            binding_options['expected_supply_record_digests'] = {**prepared_raw_digests, 'record_producer': producer}
            config = {**config, '_supply_prepared_sha256_by_role': prepared_raw_digests}
        if pinned_initialization and (type(run_cfg) is not dict or run_cfg.keys() != run_binding.config_keys(check)):
            raise ValueError('MVP_INITIALIZATION_PREPARED_REFERENCE_SCOPE_INVALID')
        if initialization and type(run_cfg) is dict and 'scenario' in run_cfg and 'prepared' in cfg:
            initialization_digests = {}
            for key, path in (('prepared', cfg['prepared']), ('scenario', run_cfg['scenario'])):
                if pinned_initialization and key == 'prepared':
                    pointer = source['initialization_prepared']
                    raw, identity = _read_input(pointer, configured_target=path, max_bytes=MAX_SCOPE_BYTES)
                    manifest = strict_json(raw, limit=MAX_SCOPE_BYTES)
                    profile = _INITIALIZATION_PREPARED_PROFILES.get((env.engine.name, env.engine.version))
                    if profile is None or type(manifest) is not dict or manifest.get('profile') != profile:
                        raise ValueError('MVP_INITIALIZATION_PREPARED_REFERENCE_SCOPE_INVALID')
                    files['initialization_prepared'], identities['initialization_prepared'] = raw, identity
                    initialization_digests[key] = pointer['sha256']
                else:
                    raw, identity = _read(path)
                    bound_files.append((path, raw, identity))
                    initialization_digests[key] = sha256_hex(raw)
            binding_options['expected_initialization_digests'] = initialization_digests
            config = {**config, '_initialization_prepared_sha256': initialization_digests['prepared']}
        binding, effective = run_binding.effective_values(check, cfg, env, values,
            side=source['side'], attempt_id='mvp-offline-readiness', issued_at=issued_at,
            valid_from=policy.valid_from, valid_until=policy.valid_until,
            run_cfg=run_cfg, policy_ref=policy.ref(), **binding_options)
        if (prebound_gateway_snapshot and _OBSERVATION_DENIED_ACTOR not in effective
                and expected_record_inputs is None):
            raise ValueError('MVP_RECORD_INPUT_REFERENCE_REQUIRED')
        if expected_record_inputs is not None:
            if observation_record_binding:
                session_key = 'observation_session'
                record_digests.update(_observation_actor_digests(effective, gateway_snapshot=prebound_gateway_snapshot))
            elif copy_snapshot:
                session_key = 'copy_record_session'
            elif check.id == 'LIF-12' and retry_records:
                session_key = 'retry_session'
            elif check.id == 'OBS-11' and collection_status:
                session_key = 'collection_session'
            else:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            actual_record_inputs = {'target_file_sha256': sha256_hex(raw_target),
                                    'record_session_sha256': sha256_hex(effective[session_key].encode('ascii')),
                                    **record_digests}
            if actual_record_inputs != expected_record_inputs:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            if prebound_gateway_snapshot:
                from tools.mvp_gateway_preparation import PRODUCER_SHA256

                if effective['observation_expected_source_sha256'] != PRODUCER_SHA256:
                    raise ValueError('GATEWAY_REVIEWED_PRODUCER_REQUIRED')
        # Use the same executor configuration and policy validation as runtime.
        if node_image_verification:
            config = {**config, '_kubernetes_target_sha256_by_executor': {
                check.executor: sha256_hex(canonical_json(target_data)),
            }}
        try:
            executor = registry.build_executor(check.executor, implementation, config,
                engine=env.engine.name, version=env.engine.version)
            executor.validate_policy(check, effective)
        except (ValueError, TypeError, KeyError):
            raise ValueError('MVP_EXECUTOR_POLICY_INVALID') from None
        if node_image_verification:
            executor.prepared_container_configuration(check, env)
        if route_record_only:
            if effective['route_target_sha256'] != sha256_hex(canonical_json(target_data)):
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            executor.prepared_route_record_only(check, env, effective)
        if prebound_gateway_snapshot:
            if effective['observation_target_sha256'] != gateway_target_sha256:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            engine_target = {key: value for key, value in target_data.items() if key != 'kubernetes_gateway'}
            try:
                executor._transport(engine_target, env, REQUEST_BINDING_PROFILE)
            except (KubernetesBindingError, OverflowError):
                raise ValueError('GATEWAY_AUDIT_TARGET_UNVERIFIED') from None
        if (prebound_kubernetes and check.id == "SUP-13" and check.executor == "template_validation"
                and cfg.get(SUP13_NORMAL_OPTION) is True):
            executor.prepared_sup13_normal(check, env, effective, get_provider(env.engine.name).adapter(),
                                           expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_file_reads:
            executor.normal_file_read_inputs(env, effective)
        if supply_record_roles:
            if any(effective.get(key) != digest for key, digest in role_digests.items()):
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            executor.prepared_supply_roles(check, env, effective)
        if generation_inputs:
            if sha256_hex(canonical_json(generation_manifest)) != effective['transition_inputs_sha256']:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            generations, verified_manifest = executor.prepared_generations(check, env, effective)
            if verified_manifest != generation_manifest:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
        if initialization:
            if effective['init_target_sha256'] != sha256_hex(canonical_json(target_data)):
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            executor.prepared_inputs(check, env, effective)
        if (prebound_kubernetes and check.id == 'LIF-11' and check.executor == 'resource_recovery'
                and run_binding.stages_for(check) == (1, 2, 4)):
            path = cfg['selection']
            raw, identity = _read(path, max_bytes=_RECOVERY_SELECTION_LIMIT)
            selection = strict_json(raw, limit=_RECOVERY_SELECTION_LIMIT)
            if sha256_hex(canonical_json(selection)) != effective['recovery_selection_sha256']:
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            resource_recovery_contract.validate_selected_policy(selection, effective)
            bound_files.append((path, raw, identity))
        if collection_status:
            executor.prepared_collection_status(check, env, effective,
                                                expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_serving_readiness and prebound_kubernetes and check.executor == 'observation_state':
            executor.prepared_normal_serving_readiness(check, env, effective,
                                                       expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_request_latency and prebound_kubernetes and check.executor == 'stream_buffer':
            executor.prepared_normal_request_latency(check, env, effective,
                                                     expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_startup_load and prebound_kubernetes and check.executor == 'supply_boot':
            executor.prepared_normal_startup_load(check, env, effective,
                                                  expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_compile_cache and prebound_kubernetes and check.executor == 'supply_boot':
            executor.prepared_normal_compile_cache(check, env, effective,
                                                   expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if cilium_egress_status and prebound_kubernetes and check.executor == 'boundary_egress':
            executor.prepared_passive_egress_policy(check, env, effective,
                                                    expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if installed_plugin_inventory and prebound_kubernetes and check.executor == 'supply_boot':
            executor.prepared_installed_plugin_inventory(check, env, effective,
                                                         expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_safetensors_reader and prebound_kubernetes and check.executor == 'supply_boot':
            executor.prepared_normal_reader(check, env, effective,
                                            expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_graph_capture and prebound_kubernetes and check.executor == 'engine_sensor':
            executor.prepared_normal_graph_capture(check, env, effective,
                                                   expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if (normal_token_input and prebound_kubernetes and check.executor == 'input_validation'
                and getattr(check, 'collection_profile', None) == INPUT_MATRIX):
            executor.prepared_normal_token_input(check, env, effective,
                                                 expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if normal_request_binding:
            executor.prepared_normal_control(check, env, effective,
                expected_target_sha256=sha256_hex(canonical_json(target_data)),
                expected_target_file_sha256=sha256_hex(raw_target))
        if normal_sampling or normal_stream:
            expected = sha256_hex(canonical_json(target_data))
            if normal_sampling:
                executor.prepared_normal_sampling(check, env, effective, expected_target_sha256=expected)
            else:
                executor.prepared_normal_stream(check, env, effective, expected_target_sha256=expected)
        if record_source_key is not None:
            if any(effective[key] != digest for key, digest in record_digests.items()):
                raise ValueError('MVP_INPUT_FILE_CHANGED')
            expected = sha256_hex(canonical_json(target_data))
            if record_access:
                executor.prepared_record_access(check, env, effective, expected_target_sha256=expected)
            elif copy_snapshot:
                executor.prepared_copy_record_snapshot(check, env, effective, expected_target_sha256=expected)
            elif record_snapshot:
                executor.prepared_record_snapshot(check, env, effective, expected_target_sha256=expected)
            elif retry_records:
                executor.prepared_retry_records(check, env, effective, expected_target_sha256=expected)
        if local_metadata:
            expected = sha256_hex(canonical_json(target_data))
            if check.id == 'OBS-04':
                executor.prepared_local_observation(check, env, effective, expected_target_sha256=expected)
            else:
                executor.prepared_worker_trust(check, env, effective, expected_target_sha256=expected)
        if protected_paths:
            executor.prepared_protected_paths(check, env, effective,
                                              expected_target_sha256=sha256_hex(canonical_json(target_data)))
        if gateway_response_snapshot:
            executor.prepared_response_snapshot(check, env, effective,
                expected_target_sha256=sha256_hex(canonical_json(target_data)),
                expected_manifest_sha256=sha256_hex(files[RESPONSE_SNAPSHOT_INPUT]),
                expected_policy_sha256=policy.ref().sha256, policy_validity=(policy.valid_from, policy.valid_until))
        elif gateway_response:
            executor.prepared_gateway(env, expected_target_sha256=sha256_hex(canonical_json(target_data)))
    if requires_native_matrix and not normal_token_input:
        _check_native_prepared(cfg.get('native_input_capture'), target_data, env)
    for role, identity in identities.items():
        path = source[role]['path']
        limit = MAX_SCOPE_BYTES if role == 'target' and gateway_response_snapshot else _INPUT_FILE_LIMITS.get(role)
        if role == 'target' and normal_request_binding:
            limit = 65536
        after, current = _read(path, max_bytes=limit) if limit is not None else _read(path)
        if current != identity or after != files[role]:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    for path, before, identity in bound_files:
        after, current = _read(path)
        if after != before or current != identity:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    if record_source_file is not None:
        path, before, identity = record_source_file
        after, current = _read(path, min_bytes=0, max_bytes=run_binding._MAX_PRODUCER_BYTES)
        if after != before or current != identity:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    if route_record_only and not run_binding.native_route_preparation_missing(cfg):
        raise ValueError('MVP_INPUT_FILE_CHANGED')
    if generation_inputs and check.id in REDEPLOY_CHECKS:
        declared_identities = {generation_identity(row.target, row.env, row.prepared) for row in generations.values()}
        if redeployment_side_reuse(redeployment_identities, declared_identities):
            raise ValueError(REDEPLOY_SIDE_REUSE)
        redeployment_identities.update(declared_identities)
    blockers = ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
    if route_record_only:
        blockers.append('NATIVE_ROUTE_PREPARATION_ABSENT')
    if passive_hop:
        blockers.append('SAME_ATTEMPT_PLT01_CONTROL_REQUIRED')
    if env.data_origin.value == 'EXAMPLE':
        blockers.append('EXAMPLE_ENV_NOT_ACTUAL')
    return {'file_binding_valid': True, 'executor_policy_valid': True, 'environment_ref': env.ref().model_dump(mode='json'),
            'environment_origin': env.data_origin.value, 'implementation': implementation,
            'binding_schema': binding['schema'] if binding else None,
            'files': {role: source[role] for role in files}, 'blocking_codes': blockers,
            **({'record_inputs': source['record_inputs']} if 'record_inputs' in source else {}),
            **({'node_image_verification': cfg['node_image_verification']} if node_image_verification else {}),
            **({NORMAL_FILE_READS_OPTION: True} if normal_file_reads else {}),
            **({'sup05_normal_preparation': sup05_normal_preparation} if sup05_normal_preparation is not None else {}),
            **({NORMAL_GRAPH_CAPTURE_OPTION: True} if normal_graph_capture else {}),
            **({NORMAL_SAFETENSORS_READER_OPTION: True} if normal_safetensors_reader else {}),
            **({NORMAL_COMPILE_CACHE_OPTION: True} if normal_compile_cache else {}),
            **({NORMAL_STARTUP_LOAD_OPTION: True} if normal_startup_load else {}),
            **({CILIUM_EGRESS_STATUS_OPTION: True} if cilium_egress_status else {}),
            **({INSTALLED_PLUGIN_INVENTORY_OPTION: True} if installed_plugin_inventory else {}),
            **({NORMAL_REQUEST_LATENCY_OPTION: True} if normal_request_latency else {}),
            **({NORMAL_SERVING_READINESS_OPTION: True} if normal_serving_readiness else {}),
            **({NORMAL_TOKEN_INPUT_OPTION: True} if normal_token_input else {}),
            **({NORMAL_REQUEST_BINDING_OPTION: True} if normal_request_binding else {})}


def audit(index_file, *, pack=None, issued_at=None, side=None, sup05_normal_preparations=None, expected_record_inputs=None):
    if side is not None and (type(side) is not str or side not in SIDES):
        raise ValueError('MVP_SIDE_SELECTION_INVALID')
    selected_sides = SIDES if side is None else (side,)
    preparations = (_sup05_preparation_refs(sup05_normal_preparations)
                    if sup05_normal_preparations is not None else {})
    record_requirements = expected_record_inputs or {}
    pack = pack or load_pack(ROOT / 'packs/PACK-BASELINE')
    active = {cid: check for cid, check in pack.checks.items() if not check.retired}
    raw, _ = _read(index_file)
    index = json.loads(raw, object_pairs_hook=_pairs)
    if (type(index) is not dict or set(index) != {'schema', 'pack_ref', 'inputs'}
            or index['schema'] != SCHEMA or index['pack_ref'] != pack.ref().model_dump(mode='json')
            or type(index['inputs']) is not list or len(index['inputs']) > len(active) * len(selected_sides)):
        raise ValueError('MVP_SOURCE_INDEX_INVALID')
    sources = {}
    for item in index['inputs']:
        if (type(item) is not dict
                or set(item) - {'target', 'route_target', 'record_inputs', 'initialization_prepared',
                                RESPONSE_SNAPSHOT_INPUT} != _INPUT_ROW_FIELDS
                or item['check_id'] not in active or item['side'] not in SIDES):
            raise ValueError('MVP_SOURCE_INDEX_INVALID')
        if item['side'] not in selected_sides:
            raise ValueError('MVP_SOURCE_INDEX_SCOPE_MISMATCH')
        key = item['check_id'], item['side']
        if key in sources:
            raise ValueError('MVP_SOURCE_DUPLICATE')
        sources[key] = item
    rows = []
    now = issued_at or datetime.now(timezone.utc)
    expected_versions = versions()
    for cid, check in sorted(active.items()):
        rules = (check.judge.params if check.judge else {})
        security_rules = bool(rules.get('pass_when_all') or rules.get('fail_when_any'))
        collection_profile = getattr(check, 'collection_profile', None)
        requires_native_matrix = collection_profile in (INPUT_MATRIX, REUSED_MATRIX)
        active_implementations = ('kubernetes',) if requires_native_matrix else ('real', 'kubernetes')
        redeployment_identities = set()
        for selected_side in selected_sides:
            expected = expected_versions[selected_side]
            implementations = [impl for impl in active_implementations if registry.resolve_executor(
                check.executor, impl, engine=expected['engine'], version=expected['version']) is not None]
            row = {'check_id': cid, 'side': selected_side, 'executor': check.executor,
                   'security_rules_present': security_rules, 'available_implementations': implementations,
                   'expected_engine': expected, 'required_policy_keys': [p.key for p in check.policy_params if p.required],
                   'required_observations': [o.key for o in check.required_observations],
                   'file_binding_valid': False, 'executor_policy_valid': False, 'live_target_verified': False,
                   'input_preflight_passed': False, 'ready_for_execution': False,
                   'engine_executed': False, 'actual_results': None}
            if (cid, selected_side) not in sources:
                row['blocking_codes'] = ['ACTUAL_INPUT_FILES_MISSING']
            else:
                try:
                    preparation = preparations.get(selected_side) if cid == 'SUP-05' else None
                    record_inputs = record_requirements.get((cid, selected_side))
                    row.update(_inspect(sources[cid, selected_side], check, pack, now, redeployment_identities,
                        sup05_normal_preparation=preparation, expected_record_inputs=record_inputs))
                except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
                    row['blocking_codes'] = [_code(error)]
            if requires_native_matrix and not row.get(NORMAL_TOKEN_INPUT_OPTION):
                row['blocking_codes'].extend(_native_matrix_blockers(expected, collection_profile))
            if not implementations:
                row['blocking_codes'].append('CURRENT_EXECUTOR_UNAVAILABLE')
            if not security_rules:
                row['blocking_codes'].append('SECURITY_RULES_EMPTY')
            rows.append(row)
    result = {'schema': 'guard-mvp-live-readiness/1', 'pack_ref': pack.ref().model_dump(mode='json'),
            'source_index_sha256': sha256(raw).hexdigest(), 'active_checks': len(active),
            'sides': len(rows), 'file_binding_valid_sides': sum(r['file_binding_valid'] for r in rows),
            'blocking_counts': dict(Counter(code for r in rows for code in r['blocking_codes'])),
            'engine_executed': False, 'ready_for_execution': False, 'actual_results': None,
            'scope': 'Offline file binding and implementation inventory only; live prepare-update is still required.',
            'rows': rows}
    if side is not None:
        result['selection'] = {'mode': 'one-declared-side', 'side': side,
                               'expected_engine': expected_versions[side]}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path)
    parser.add_argument('--write-template', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--side', choices=SIDES,
                        help='Audit one declared engine side; omitted audits both sides. No engine is started.')
    args = parser.parse_args()
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    if args.write_template:
        with args.write_template.open('x', encoding='utf-8') as stream:
            json.dump(template(pack), stream, ensure_ascii=False, indent=2)
    index = args.index or args.write_template
    if index is None:
        parser.error('--index or --write-template is required')
    result = audit(index, pack=pack, side=args.side)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
