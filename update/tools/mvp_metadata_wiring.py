"""Assemble an offline configuration for paired owned Kubernetes metadata checks."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from guard.adapters.engine_profiles import supports
from guard.adapters.engine_provider import get_provider
from guard.configuration import (GATEWAY_RECORD_SNAPSHOT_OPTION, KUBERNETES_BOUND_CHECK_OPTIONS, NORMAL_FILE_READS_OPTION,
                                 RECORD_SNAPSHOT_OPTIONS, SUP05_PREPARATION_RECEIPT, bounded_metadata_scopes, merge_config,
                                 normal_file_reads_requested, update_allowed_checks)
from guard.core.execution_recipe import validate_policy_time
from guard.core.run import RunConfig, _blocked_reason
from guard.core.select import _in_scope, select
from guard.executors import passive_api_hop, registry, run_binding, template_supply_approval as template_supply
from guard.executors.retry_record_contract import TARGET_BINDING_KEY as RETRY_TARGET_BINDING
from guard.executors.kubernetes_gateway_observation import SNAPSHOT_SOURCE, snapshot_owner
from guard.executors.kubernetes_installed_plugin_inventory import installed_plugin_inventory_transport
from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
from guard.executors.kubernetes_log_analysis import KubernetesLogAnalysisExecutor
from guard.executors.kubernetes_normal_graph_capture import normal_graph_capture_transport
from guard.executors.kubernetes_normal_request_latency import normal_request_latency_transport
from guard.executors.kubernetes_normal_safetensors_reader import normal_safetensors_reader_transport
from guard.executors.kubernetes_normal_serving_readiness import normal_serving_readiness_transport
from guard.executors.kubernetes_normal_token_input import normal_input_transports
from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
from guard.executors.kubernetes_passive_egress_policy import passive_egress_policy_transport
from guard.executors.kubernetes_template_supply import KubernetesTemplateSupplyExecutor
from guard.models import Environment, Policy, load_pack, utcnow
from guard.models.artifacts import model_closure_sha256
from guard.runtime.bounded import strict_json
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.update_validation import _plain_path, reserve_output
from tools import mvp_live_readiness as readiness
from tools.j02_run_config import write_config
from tools.mvp_a_input_preflight import _code, _pairs, _read
from tools.mvp_versions import versions

PREBOUND_RECORD_EXECUTORS = {'LIF-12': 'request_termination', 'OBS-11': 'log_integrity'}
RECORD_SCOPE_FILES = {'PLT-12': 'copy_inventory', 'LIF-12': 'retry_scope'}
CHECK_EXECUTORS = {'SUP-04': 'container_config', 'PLT-01': 'container_config', 'PLT-04': 'credential_exposure',
                   'SUP-03': 'template_validation', 'SUP-07': 'supply_boot', 'SUP-10': 'supply_boot', 'SUP-12': 'supply_boot',
                   'REQ-07': 'template_validation', 'OBS-03': 'log_analysis',
                   'OBS-04': 'boundary_egress', 'PLT-07': 'boundary_egress', 'PLT-06': 'unix_ipc',
                   'OBS-01': 'required_event_record', 'OBS-02': 'observation_state',
                   'PLT-12': 'required_event_record', 'PLT-02': 'unix_ipc',
                   'LIF-01': 'supply_boot', 'LIF-03': 'stream_buffer', 'LIF-04': 'stream_buffer',
                   'REQ-09': 'input_validation', 'REQ-10': 'sampling_validation', 'REQ-20': 'engine_sensor',
                   passive_api_hop.CHECK: passive_api_hop.EXECUTOR, **PREBOUND_RECORD_EXECUTORS}
CHECKS = tuple(cid for cid in CHECK_EXECUTORS
               if cid not in {*RECORD_SNAPSHOT_OPTIONS, *PREBOUND_RECORD_EXECUTORS, 'OBS-02', 'PLT-02', 'LIF-01', 'LIF-03', 'LIF-04',
                              'PLT-07', 'REQ-09', 'REQ-10', 'REQ-20', 'SUP-07', 'SUP-10', 'SUP-12', passive_api_hop.CHECK})
NODE_IMAGE_PROFILES = ('local-containerd-v1', 'local-k3s-containerd-v1')
METADATA_EXECUTORS = frozenset(name for cid, name in CHECK_EXECUTORS.items() if cid not in PREBOUND_RECORD_EXECUTORS)
CHECK_OPTIONS = {**KUBERNETES_BOUND_CHECK_OPTIONS, 'REQ-07': 'req07_normal_only'}


def _source(path):
    path = Path(path).absolute()
    raw, _ = _read(path)
    return json.loads(raw, object_pairs_hook=_pairs), {'path': str(path), 'sha256': sha256(raw).hexdigest()}


def _detached_runner(*args, **kwargs):
    raise AssertionError('Wiring validates saved files only')


def _transport(target, env):
    try:
        transport = KubernetesHTTP(target, env, duration=1, runner=_detached_runner, profile=REQUEST_BINDING_PROFILE)
    except KubernetesBindingError:
        raise ValueError('METADATA_TARGET_BINDING_INVALID') from None
    if target['lab'] is not False:
        raise ValueError('METADATA_MEASUREMENT_TARGET_REQUIRED')
    return transport


def _paired_record_inputs(value, check_id):
    if type(value) is not dict or set(value) != set(readiness.SIDES):
        raise ValueError('OBSERVATION_PAIRED_RUN_BINDINGS_REQUIRED')
    scope_file = RECORD_SCOPE_FILES.get(check_id)
    file_fields = () if check_id == 'OBS-11' else ('record_producer',)
    if scope_file is not None:
        file_fields = (*file_fields, scope_file)
    fields = {'record_session', *file_fields}
    if check_id == 'OBS-01':
        fields.add('test_actors')
    if check_id == 'LIF-12' and any(type(entry) is dict and RETRY_TARGET_BINDING in entry for entry in value.values()):
        fields.add(RETRY_TARGET_BINDING)
    bindings = {}
    for side, entry in value.items():
        if type(entry) is not dict or set(entry) != fields:
            raise ValueError('OBSERVATION_RUN_BINDING_INVALID')
        bindings[side] = dict(entry)
        for field in file_fields:
            if type(entry[field]) is not str or not entry[field]:
                raise ValueError('OBSERVATION_RUN_BINDING_INVALID')
            source = Path(entry[field]).absolute()
            if check_id not in PREBOUND_RECORD_EXECUTORS:
                _read(source)
            bindings[side][field] = str(source)
    return bindings


def _record_scope_settings(check_id, run_cfg):
    scope_file = RECORD_SCOPE_FILES[check_id]
    settings = {scope_file: run_cfg.pop(scope_file)}
    if check_id == 'LIF-12' and RETRY_TARGET_BINDING in run_cfg:
        settings[RETRY_TARGET_BINDING] = run_cfg.pop(RETRY_TARGET_BINDING)
    return settings


def _gateway_record_inputs(value):
    from tools.mvp_gateway_preparation import GATEWAY_ENV, PRODUCER_SHA256

    fields = {'environment', 'target_id', 'runtime_policy_sha256', 'request_sha256'}
    if type(value) is not dict or set(value) != set(readiness.SIDES):
        raise ValueError('GATEWAY_PAIRED_RECORD_INPUTS_REQUIRED')
    inputs = {}
    for side, entry in value.items():
        if type(entry) is not dict or type(entry.get(SNAPSHOT_SOURCE)) is not dict:
            raise ValueError('GATEWAY_PAIRED_RECORD_INPUTS_INVALID')
        inputs[side] = dict(entry)
        source = inputs[side].pop(SNAPSHOT_SOURCE)
        if set(source) != fields or source['environment'] != GATEWAY_ENV:
            raise ValueError('GATEWAY_PAIRED_RECORD_INPUTS_INVALID')
    inputs = _paired_record_inputs(inputs, 'OBS-01')
    if any(run_binding.producer_sha256(row['record_producer']) != PRODUCER_SHA256 for row in inputs.values()):
        raise ValueError('GATEWAY_REVIEWED_PRODUCER_REQUIRED')
    first, second = (value[side][SNAPSHOT_SOURCE] for side in readiness.SIDES)
    if (any(first[key] != second[key] for key in ('target_id', 'runtime_policy_sha256'))
            or first['request_sha256'] == second['request_sha256']
            or inputs['baseline']['record_session'] == inputs['candidate']['record_session']):
        raise ValueError('GATEWAY_DISTINCT_PAIRED_RECORDS_REQUIRED')
    for side, entry in inputs.items():
        entry[SNAPSHOT_SOURCE] = value[side][SNAPSHOT_SOURCE]
    return inputs


def _sup05_normal_inputs(pointer, check, env, policy, *, side, ordinary_target):
    from guard.executors.kubernetes_sup05_normal_preparation import read_normal_preparation

    if not _in_scope(check.id, policy):
        return None, 'MVP_POLICY_SCOPE_MISMATCH'
    if check.executor != 'supply_boot':
        return None, 'METADATA_EXECUTOR_UNAVAILABLE'
    try:
        preparation = read_normal_preparation(pointer['path'], env, expected_sha256=pointer['sha256'])
        normal = preparation['normal']
        if Path(normal['target']) == Path(ordinary_target).resolve():
            raise ValueError('SUP05_DISTINCT_LIFECYCLE_TARGET_REQUIRED')
        prepared_settings = {'normal': normal, SUP05_PREPARATION_RECEIPT: pointer}
        settings = {NORMAL_FILE_READS_OPTION: True, **prepared_settings}
        expected_target = preparation['target_sha256']
        _, values = run_binding.effective_values(check, settings, env, policy.values.get(check.id) or {},
            side=side, attempt_id=None, issued_at=utcnow(), valid_from=policy.valid_from, valid_until=policy.valid_until,
            policy_ref=policy.ref(), expected_target_sha256=expected_target,
            expected_normal_file_sha256=preparation['expected_normal_file_sha256'])
        executor = registry.build_executor(check.executor, 'kubernetes',
            {check.executor: settings, '_kubernetes_target_sha256': expected_target},
            engine=env.engine.name, version=env.engine.version)
        executor.validate_policy(check, values)
        executor.normal_file_read_inputs(env, values)
    except (KeyError, OSError, TypeError, ValueError) as error:
        return None, _code(error)
    return prepared_settings, None


def staged_config(config, fresh_candidate_target):
    """Use a separate, unpublished file for A's after-baseline candidate phase."""
    new_path = _plain_path(Path(fresh_candidate_target))
    if new_path.exists() or not config['sides']['candidate']:
        raise ValueError('METADATA_FRESH_CANDIDATE_PATH_REQUIRED')
    if config.get('check_config', {}).get('OBS-01', {}).get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True:
        raise ValueError('GATEWAY_RECORDS_REQUIRE_PREBOUND_PAIR')
    if normal_file_reads_requested(config):
        raise ValueError('SUP05_NORMAL_PREPARATIONS_REQUIRE_PREBOUND_PAIR')
    if any(name in config['executors'] for name in PREBOUND_RECORD_EXECUTORS.values()):
        raise ValueError('METADATA_RECORDS_REQUIRE_PREBOUND_PAIR')
    result = deepcopy(config)
    result['update']['kubernetes_candidate_binding'] = 'after_baseline'
    for name in result['executors']:
        result['sides']['candidate'][name]['target'] = str(new_path)
    return result


def revalidate_candidate(config, env, preliminary_ref):
    """Return hashes for A's runtime-only target binding before candidate dispatch.

    The operator supplies the new target after verified baseline resource release.
    This checks saved declarations and never queries or modifies a deployment.
    """
    hashes, _ = _revalidate_candidate(config, env, preliminary_ref)
    return hashes


def bind_staged_candidate(run: RunConfig, env, preliminary_ref):
    """Bind the verified candidate and return any check-specific preflight failure."""
    config = run.requested
    candidate = run.side_configs['candidate']
    hashes, abort_reasons = _revalidate_candidate(config, env, preliminary_ref, allow_check_gaps=True)
    # Checks sharing an affected executor receive independent target pins.
    affected_executors = {CHECK_EXECUTORS[check_id] for check_id in abort_reasons}
    separated = {name: hashes.pop(name) for name in affected_executors}
    check_hashes = {check_id: None if check_id in abort_reasons else separated[CHECK_EXECUTORS[check_id]]
                    for check_id in (config['update'].get('allowed_checks') or ())
                    if CHECK_EXECUTORS[check_id] in affected_executors}
    candidate.raw['_kubernetes_target_sha256_by_executor'] = hashes
    candidate.raw['_kubernetes_target_sha256_by_check'] = check_hashes
    candidate.check_abort_reasons = abort_reasons
    return abort_reasons


def _revalidate_candidate(config, env, preliminary_ref, *, allow_check_gaps=False):
    executors = config.get('executors', {})
    candidate = config.get('sides', {}).get('candidate', {})
    allowed = config.get('update', {}).get('allowed_checks')
    record_checks = {cid for cid in RECORD_SNAPSHOT_OPTIONS if cid in allowed} if type(allowed) is list else set()
    technical = {'run_binding'} if record_checks else set()
    if 'PLT-12' in record_checks:
        technical.add('check_config')
    if (config.get('update', {}).get('kubernetes_candidate_binding') != 'after_baseline' or not executors
            or set(executors) - METADATA_EXECUTORS or set(candidate) != set(executors) | technical
            or any(impl != 'kubernetes' for impl in executors.values())
            or any(type(candidate[name]) is not dict or set(candidate[name]) != {'target'} for name in executors)):
        raise ValueError('METADATA_STAGED_CONFIGURATION_INVALID')
    if record_checks:
        settings_by_side = {side: config['sides'].get(side, {}) for side in readiness.SIDES}
        for settings in settings_by_side.values():
            bindings = settings.get('run_binding')
            if type(bindings) is not dict or set(bindings) != record_checks or any(type(row) is not dict for row in bindings.values()):
                raise ValueError('METADATA_STAGED_CONFIGURATION_INVALID')
            if 'PLT-12' in record_checks:
                overlay = settings.get('check_config')
                if (type(overlay) is not dict or set(overlay) != {'PLT-12'} or type(overlay['PLT-12']) is not dict
                        or set(overlay['PLT-12']) != {'copy_inventory'}):
                    raise ValueError('METADATA_STAGED_CONFIGURATION_INVALID')
        for check_id in record_checks:
            _paired_record_inputs({side: {**settings['run_binding'][check_id],
                **(settings['check_config'][check_id] if check_id == 'PLT-12' else {})}
                for side, settings in settings_by_side.items()}, check_id)
    restricted_executors = {CHECK_EXECUTORS[cid] for cid in KUBERNETES_BOUND_CHECK_OPTIONS if cid in CHECK_EXECUTORS}
    if allowed is None:
        if restricted_executors.intersection(executors):
            raise ValueError('METADATA_STAGED_EXECUTION_SCOPE_INVALID')
    elif (type(allowed) is not list or any(type(cid) is not str or cid not in CHECK_EXECUTORS for cid in allowed)
          or len(set(allowed)) != len(allowed) or {CHECK_EXECUTORS[cid] for cid in allowed} != set(executors)
          or (passive_api_hop.CHECK in allowed and passive_api_hop.SOURCE_CHECK not in allowed)
          or any(config.get('check_config', {}).get(cid) != {CHECK_OPTIONS[cid]: True}
                 for cid in allowed if cid in CHECK_OPTIONS)):
        raise ValueError('METADATA_STAGED_EXECUTION_SCOPE_INVALID')
    paths = {candidate[name]['target'] for name in executors}
    if len(paths) != 1:
        raise ValueError('METADATA_FRESH_CANDIDATE_PATH_REQUIRED')
    current_path, = paths
    preliminary, pointer = _source(preliminary_ref['path'])
    if pointer != preliminary_ref or Path(current_path).resolve() == Path(pointer['path']).resolve():
        raise ValueError('METADATA_PRELIMINARY_TARGET_CHANGED')
    current, _ = _source(current_path)
    old, new = _transport(preliminary, env), _transport(current, env)
    for check_id, contract, error in (
        ('SUP-12', 'safetensors_reader_source', 'NORMAL_SAFETENSORS_READER_SOURCE_UNAVAILABLE'),
        ('REQ-20', 'graph_capture_source', 'NORMAL_GRAPH_CAPTURE_SOURCE_UNAVAILABLE'),
    ):
        if check_id not in (allowed or ()):
            continue
        try:
            if get_provider(env.engine.name).evidence_contract(contract, env) is None:
                raise ValueError
        except ValueError:
            raise ValueError(error) from None
    gaps = {}
    if 'PLT-07' in (allowed or ()):
        try:
            passive_egress_policy_transport(current, env, duration=1, runner=_detached_runner)
        except (KubernetesBindingError, ValueError):
            gaps['PLT-07'] = 'CILIUM_EGRESS_STATUS_TARGET_UNAVAILABLE'
        else:
            if current['cilium_agent'] != preliminary.get('cilium_agent'):
                gaps['PLT-07'] = 'CILIUM_AGENT_GENERATION_CHANGED'
        if 'PLT-07' in gaps and not allow_check_gaps:
            raise ValueError(gaps['PLT-07'])
    if 'SUP-10' in (allowed or ()):
        try:
            installed_plugin_inventory_transport(current, env, duration=1, runner=_detached_runner)
        except (KubernetesBindingError, ValueError):
            raise ValueError('INSTALLED_PLUGIN_INVENTORY_TARGET_UNAVAILABLE') from None
    if 'LIF-03' in (allowed or ()):
        try:
            normal_request_latency_transport(current, env, duration=1, runner=_detached_runner)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_REQUEST_LATENCY_TARGET_UNAVAILABLE') from None
    if 'observation_state' in executors:
        try:
            normal_serving_readiness_transport(current, env, duration=1, runner=_detached_runner)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_SERVING_READINESS_TARGET_UNAVAILABLE') from None
    if 'input_validation' in executors:
        try:
            normal_input_transports(current, env, duration=1, runner=_detached_runner)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_TOKEN_INPUT_TARGET_UNAVAILABLE') from None
    roles = ('engine', 'probe')
    if (old.binding['context'] != new.binding['context']
            or any(old.binding[role][key] != new.binding[role][key]
                   for role in roles for key in ('namespace', 'namespace_uid'))):
        raise ValueError('METADATA_CANDIDATE_OWNERSHIP_CHANGED')
    if any(old.binding[role][key] == new.binding[role][key]
           for role in roles for key in ('pod_uid', 'container_id')):
        raise ValueError('METADATA_CANDIDATE_GENERATION_NOT_FRESH')
    if execution_conditions(preliminary, {}, []) != execution_conditions(current, {}, []):
        raise ValueError('METADATA_CANDIDATE_CONDITIONS_CHANGED')
    if 'template_validation' in executors and (current.get('observed_version') != env.engine.version
            or current['engine'].get('command') != preliminary['engine'].get('command')):
        raise ValueError('METADATA_CANDIDATE_TEMPLATE_LAUNCH_CHANGED')
    for check_id in readiness.STARTUP_OBSERVERS:
        if check_id not in (allowed or ()):
            continue
        reason = readiness.startup_observer_reason(check_id, current, env, runner=_detached_runner)
        if reason is not None:
            if not allow_check_gaps:
                raise ValueError(reason)
            gaps[check_id] = reason
    return dict.fromkeys(executors, new.target_sha256), gaps


def _prebound_record_binding(check, env, values, *, policy, side, target_pointer, target_sha256, record_inputs):
    settings = {'target': target_pointer['path'], CHECK_OPTIONS[check.id]: True}
    run_cfg = dict(record_inputs)
    if check.id == 'LIF-12':
        settings.update(_record_scope_settings(check.id, run_cfg))
    elif run_binding.stages_for(check) != (2,):
        raise ValueError('METADATA_COLLECTION_STATUS_PROFILE_UNAVAILABLE')
    _, effective = run_binding.effective_values(check, settings, env, values,
        side=side, attempt_id=None, issued_at=utcnow(), valid_from=policy.valid_from, valid_until=policy.valid_until,
        run_cfg=run_cfg, policy_ref=policy.ref())
    executor = registry.build_executor(check.executor, 'kubernetes', {check.executor: settings},
                                       engine=env.engine.name, version=env.engine.version)
    digests = {'target_file_sha256': target_pointer['sha256'],
               'record_session_sha256': sha256(run_cfg['record_session'].encode('ascii')).hexdigest()}
    if check.id == 'LIF-12':
        executor.prepared_retry_records(check, env, effective, expected_target_sha256=target_sha256)
        digests.update({key: effective[key] for key in ('retry_scope_sha256', 'retry_record_source_sha256')})
    else:
        executor.prepared_collection_status(check, env, effective, expected_target_sha256=target_sha256)
    return digests


def _check_reason(check, env, target, transport, values, *, policy, side, target_pointer, record_inputs, record_pins):
    if not _in_scope(check.id, policy):
        return 'MVP_POLICY_SCOPE_MISMATCH'
    if any(key.required and key.key not in values for key in check.policy_params):
        return 'MVP_POLICY_VALUE_MISSING'
    if check.executor != CHECK_EXECUTORS[check.id]:
        return 'METADATA_EXECUTOR_UNAVAILABLE'
    if check.id in PREBOUND_RECORD_EXECUTORS:
        try:
            record_pins[side] = _prebound_record_binding(check, env, values, policy=policy, side=side,
                target_pointer=target_pointer, target_sha256=transport.target_sha256, record_inputs=record_inputs)
        except (KeyError, OSError, TypeError, ValueError) as error:
            return _code(error)
        return None
    if check.id == passive_api_hop.CHECK:
        try:
            passive_api_hop.validate_policy(check, values)
        except passive_api_hop.PassiveApiHopUnavailable as exc:
            return str(exc)
        return None
    try:
        target_path = target_pointer['path']
        config = {check.executor: {CHECK_OPTIONS[check.id]: True} if check.id in CHECK_OPTIONS else {}}
        if check.id in readiness.STARTUP_OBSERVERS:
            config[check.executor]['target'] = target_path
            reason = readiness.startup_observer_reason(check.id, target, env, runner=_detached_runner)
            if reason is not None:
                return reason
        if check.id == 'REQ-20':
            config[check.executor]['target'] = target_path
            try:
                if get_provider(env.engine.name).evidence_contract('graph_capture_source', env) is None:
                    raise ValueError
            except ValueError:
                return 'NORMAL_GRAPH_CAPTURE_SOURCE_UNAVAILABLE'
            try:
                normal_graph_capture_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'NORMAL_GRAPH_CAPTURE_TARGET_UNAVAILABLE'
        if check.id == 'PLT-07':
            config[check.executor]['target'] = target_path
            try:
                passive_egress_policy_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'CILIUM_EGRESS_STATUS_TARGET_UNAVAILABLE'
        if check.id == 'SUP-10':
            config[check.executor]['target'] = target_path
            try:
                installed_plugin_inventory_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'INSTALLED_PLUGIN_INVENTORY_TARGET_UNAVAILABLE'
        if check.id == 'SUP-12':
            config[check.executor]['target'] = target_path
            try:
                if get_provider(env.engine.name).evidence_contract('safetensors_reader_source', env) is None:
                    raise ValueError
            except ValueError:
                return 'NORMAL_SAFETENSORS_READER_SOURCE_UNAVAILABLE'
            try:
                normal_safetensors_reader_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'NORMAL_SAFETENSORS_READER_TARGET_UNAVAILABLE'
        if check.id == 'LIF-03':
            config[check.executor]['target'] = target_path
            try:
                normal_request_latency_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'NORMAL_REQUEST_LATENCY_TARGET_UNAVAILABLE'
        if check.id == 'OBS-02':
            config[check.executor]['target'] = target_path
            try:
                normal_serving_readiness_transport(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'NORMAL_SERVING_READINESS_TARGET_UNAVAILABLE'
        if check.id == 'REQ-09':
            config[check.executor]['target'] = target_path
            try:
                normal_input_transports(target, env, duration=1, runner=_detached_runner)
            except (KubernetesBindingError, ValueError):
                return 'NORMAL_TOKEN_INPUT_TARGET_UNAVAILABLE'
        if check.id == 'REQ-10':
            config[check.executor]['target'] = target_path
            try:
                KubernetesHTTP(target, env, duration=1, runner=_detached_runner, profile=INPUT_VALIDATION_PROFILE)
            except KubernetesBindingError:
                return 'NORMAL_SAMPLING_TARGET_UNAVAILABLE'
        if check.id in RECORD_SNAPSHOT_OPTIONS or check.id == 'LIF-04':
            config[check.executor]['target'] = target_path
            run_cfg = dict(record_inputs) if record_inputs is not None else None
            if check.id == 'PLT-12':
                config[check.executor]['copy_inventory'] = run_cfg.pop('copy_inventory')
            if check.id == 'OBS-01' and SNAPSHOT_SOURCE in run_cfg:
                from tools.mvp_gateway_preparation import GATEWAY_IMAGE, IMAGE_CONFIG_SHA256

                gateway = snapshot_owner(target, transport.binding)
                if (gateway['image'] != GATEWAY_IMAGE
                        or gateway['image_id'] not in {GATEWAY_IMAGE, f'docker-pullable://{GATEWAY_IMAGE}',
                                                       f'sha256:{IMAGE_CONFIG_SHA256}'}):
                    return 'GATEWAY_PAIRED_TARGET_REQUIRED'
                config[check.executor].update({
                    GATEWAY_RECORD_SNAPSHOT_OPTION: True, SNAPSHOT_SOURCE: run_cfg.pop(SNAPSHOT_SOURCE)})
            _, values = run_binding.effective_values(check, config[check.executor], env, values, side=side,
                attempt_id=None, issued_at=utcnow(), valid_from=policy.valid_from, valid_until=policy.valid_until,
                run_cfg=run_cfg, policy_ref=policy.ref())
        executor = registry.build_executor(check.executor, 'kubernetes', config,
                                           engine=env.engine.name, version=env.engine.version)
        executor.validate_policy(check, values)
    except (KeyError, TypeError, ValueError):
        return 'METADATA_EXECUTOR_OR_POLICY_UNAVAILABLE'
    if check.id == 'OBS-03' and type(executor) is not KubernetesLogAnalysisExecutor:
        return 'NORMAL_LOG_EXECUTOR_UNAVAILABLE'
    if check.id in RECORD_SNAPSHOT_OPTIONS and type(executor) is not KubernetesRequiredEventExecutor:
        return 'OBSERVATION_RECORD_SNAPSHOT_EXECUTOR_UNAVAILABLE'
    if check.id == 'OBS-04' and registry.resolve_executor(check.executor, 'kubernetes', engine=env.engine.name,
            version=env.engine.version) is not registry.EXECUTORS.get('boundary_egress:kubernetes'):
        return 'LOCAL_USAGE_EXECUTOR_UNAVAILABLE'
    if check.id in {'PLT-02', 'PLT-06'} and (type(executor) is not KubernetesIPCProductExecutor
                                          or not supports(env.engine.name, env.engine.version, 'unix_ipc')):
        return 'IPC_PATH_METADATA_EXECUTOR_UNAVAILABLE' if check.id == 'PLT-02' else 'WORKER_TRUST_EXECUTOR_UNAVAILABLE'
    if check.id in ('SUP-03', 'REQ-07'):
        if type(executor) is not KubernetesTemplateSupplyExecutor:
            return 'CONFIGURED_TEMPLATE_EXECUTOR_UNAVAILABLE'
        factory = registry.adapter_factory(env.engine.name)
        adapter = factory() if factory is not None else None
        ctx = SimpleNamespace(env=env, target=target, adapter=adapter, cprobe=transport, api_model=transport.api_model)
        command = target['engine'].get('command')
        approved_target = template_supply._target(ctx, kubernetes=True)
        if (type(command) is not list or not command or any(type(arg) is not str or not arg for arg in command)
                or approved_target is None):
            return 'CONFIGURED_TEMPLATE_TARGET_REQUIRED'
        if approved_target[3]['sha256'] not in values[template_supply.POLICY_KEY]:
            return 'CONFIGURED_TEMPLATE_POLICY_APPROVAL_MISSING'
    return None


def assemble(inputs, policy_path, *, pack, store, node_image_verification=None, expected=None, passive_plt08=False,
             protected_ipc_metadata=False, observation_run_bindings=None, copy_record_inputs=None,
             gateway_record_inputs=None, normal_stream_buffer=False, normal_sampling=False, normal_token_input=False,
             normal_serving_readiness=False, normal_request_latency=False, installed_plugin_inventory=False,
             cilium_egress_status=False, normal_startup_load=False, normal_compile_cache=False,
             normal_safetensors_reader=False, normal_graph_capture=False, sup05_normal_preparations=None,
             retry_record_inputs=None, collection_status_bindings=None):
    """Return product TOML settings, hash-pinned index rows and the paired input decisions."""
    if node_image_verification is not None and node_image_verification not in NODE_IMAGE_PROFILES:
        raise ValueError('METADATA_NODE_IMAGE_PROFILE_INVALID')
    expected = versions() if expected is None else expected
    document, policy_pointer = _source(policy_path)
    policy = Policy.model_validate(document)
    if not policy.hash_ok() or policy.pack != pack.ref() or policy.status != 'CONFIRMED' or policy.data_origin == 'EXAMPLE':
        raise ValueError('METADATA_CONFIRMED_POLICY_REQUIRED')
    validate_policy_time(policy)

    sides = {}
    for side in readiness.SIDES:
        env_doc, env_pointer = _source(inputs[side]['environment'])
        env = Environment.model_validate(env_doc)
        engine = expected[side]
        if (not env.hash_ok() or env.data_origin == 'EXAMPLE' or (env.engine.name, env.engine.version, env.engine.image_digest)
                != (engine['engine'], engine['version'], engine['image'])):
            raise ValueError('METADATA_ENV_IDENTITY_MISMATCH')
        if not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver or not env.model.inventory_sha256:
            raise ValueError('METADATA_GPU_AND_MODEL_FACTS_REQUIRED')
        target, target_pointer = _source(inputs[side]['target'])
        transport = _transport(target, env)
        sides[side] = env, target, transport, env_pointer, target_pointer

    base, candidate = (sides[side][0] for side in readiness.SIDES)
    if (base.runtime != candidate.runtime or model_closure_sha256(base.model) != model_closure_sha256(candidate.model)
            or base.model.id != candidate.model.id or base.model.revision != candidate.model.revision):
        raise ValueError('METADATA_COMPARISON_CONDITIONS_MISMATCH')
    config = {'store': str(Path(store).absolute()), 'adapter': {'name': base.engine.name},
              'update': {'mode': 'external'}, 'executors': {}, 'sides': {side: {} for side in sides}}
    candidates = {}
    records = {check_id: _paired_record_inputs(value, check_id) for check_id, value in
               (('OBS-01', observation_run_bindings), ('PLT-12', copy_record_inputs),
                ('LIF-12', retry_record_inputs), ('OBS-11', collection_status_bindings)) if value is not None}
    if gateway_record_inputs is not None:
        if 'OBS-01' in records:
            raise ValueError('GATEWAY_RECORD_INPUT_MODE_CONFLICT')
        records['OBS-01'] = _gateway_record_inputs(gateway_record_inputs)
    optional = {passive_api_hop.CHECK: passive_plt08, 'PLT-02': protected_ipc_metadata,
                'LIF-01': normal_startup_load,
                'LIF-03': normal_request_latency, 'LIF-04': normal_stream_buffer,
                'REQ-10': normal_sampling, 'REQ-09': normal_token_input,
                'REQ-20': normal_graph_capture,
                'OBS-02': normal_serving_readiness,
                'SUP-07': normal_compile_cache,
                'SUP-10': installed_plugin_inventory,
                'SUP-12': normal_safetensors_reader,
                'PLT-07': cilium_egress_status,
                **dict.fromkeys(records, True)}
    check_ids = (*CHECKS, *(cid for cid, requested in optional.items() if requested))
    for check_id in check_ids:
        check = pack.checks[check_id]
        values = policy.values.get(check_id) or {}
        record_pins = {}
        reasons = {side: _check_reason(check, env, target, transport, values, policy=policy, side=side,
                                       target_pointer=target_pointer, record_inputs=records.get(check_id, {}).get(side),
                                       record_pins=record_pins)
                   for side, (env, target, transport, _, target_pointer) in sides.items()}
        candidates[check_id] = {'configuration_enabled': not any(reasons.values()), 'sides': reasons}
        if record_pins.keys() == set(readiness.SIDES):
            candidates[check_id]['prepared_record_input_sha256_by_side'] = record_pins
    normal_file_read_settings = {}
    if sup05_normal_preparations is not None:
        references = readiness._sup05_preparation_refs(sup05_normal_preparations)
        reasons = {}
        for side, (env, _, _, _, target_pointer) in sides.items():
            normal_file_read_settings[side], reasons[side] = _sup05_normal_inputs(
                references[side], pack.checks['SUP-05'], env, policy, side=side, ordinary_target=target_pointer['path'])
        candidates['SUP-05'] = {'configuration_enabled': not any(reasons.values()), 'sides': reasons}
    passive = candidates.get(passive_api_hop.CHECK)
    if passive and not candidates[passive_api_hop.SOURCE_CHECK]['configuration_enabled']:
        passive['configuration_enabled'] = False
        passive['sides'] = {side: reason or 'PASSIVE_API_HOP_SOURCE_NOT_ADMITTED'
                            for side, reason in passive['sides'].items()}
    bindings = []
    sup05_enabled = candidates.get('SUP-05', {}).get('configuration_enabled')
    for check_id, decision in candidates.items():
        if not decision['configuration_enabled']:
            continue
        check = pack.checks[check_id]
        config['executors'][check.executor] = 'kubernetes'
        for side, (_, _, _, env_pointer, target_pointer) in sides.items():
            settings = config['sides'][side]
            if sup05_enabled and check.executor == 'supply_boot':
                source = normal_file_read_settings[side] if check_id == 'SUP-05' else {'target': target_pointer['path']}
                settings.setdefault('check_config', {})[check_id] = source
            else:
                settings[check.executor] = {'target': target_pointer['path']}
            if check_id in records:
                run_cfg = dict(records[check_id][side])
                if check_id in RECORD_SCOPE_FILES:
                    settings.setdefault('check_config', {})[check_id] = _record_scope_settings(check_id, run_cfg)
                if check_id == 'OBS-01' and SNAPSHOT_SOURCE in run_cfg:
                    settings.setdefault('check_config', {})[check_id] = {SNAPSHOT_SOURCE: run_cfg.pop(SNAPSHOT_SOURCE)}
                settings.setdefault('run_binding', {})[check_id] = run_cfg
            bindings.append({'check_id': check_id, 'side': side, 'environment': env_pointer, 'policy': policy_pointer})
        if check_id == 'SUP-04' and node_image_verification is not None:
            config.setdefault('check_config', {})[check_id] = {'node_image_verification': node_image_verification}
        if check_id in CHECK_OPTIONS:
            config.setdefault('check_config', {})[check_id] = {CHECK_OPTIONS[check_id]: True}
        if check_id == 'OBS-01' and gateway_record_inputs is not None:
            config['check_config'][check_id][GATEWAY_RECORD_SNAPSHOT_OPTION] = True
    config['update']['allowed_checks'] = [cid for cid, decision in candidates.items() if decision['configuration_enabled']]
    allowed = update_allowed_checks(config, pack)
    bounded_metadata_scopes((merge_config(config, settings) for settings in config['sides'].values()), pack, allowed)
    shared_checks = ('SUP-03', 'SUP-05', 'SUP-07', 'SUP-10', 'SUP-12', 'OBS-04', 'PLT-06', 'PLT-07', 'LIF-01', 'LIF-03',
                     'LIF-04', 'REQ-09', 'REQ-20',
                     *RECORD_SNAPSHOT_OPTIONS, *PREBOUND_RECORD_EXECUTORS, passive_api_hop.CHECK)
    anchors = [cid for cid in shared_checks if candidates.get(cid, {}).get('configuration_enabled')]
    if anchors:
        plan = select(pack, [base, candidate], policy, mode='update')
        run = RunConfig(executors=config['executors'])
        for anchor in anchors:
            siblings = {}
            for check in pack.active_checks():
                if check.executor != pack.checks[anchor].executor or check.id in allowed:
                    continue
                row = plan.item(check.id)
                siblings[check.id] = {
                    side: {'expected_if_invoked': 'NOT_RUN' if row.selected else 'NOT_SELECTED',
                           'reason': _blocked_reason(row, run, env, allowed_checks=allowed) if row.selected
                                     else row.not_selected_reason.value,
                           'transport_dispatched': False}
                    for side, (env, _, _, _, _) in sides.items()
                }
            candidates[anchor]['shared_executor_checks'] = siblings
    return config, bindings, candidates


def _write_input_sources(index, pack, bindings, config_ref):
    index.write_text(json.dumps({**readiness.template(pack),
        'inputs': [{**entry, 'configuration': config_ref} for entry in bindings]}, indent=2), encoding='utf-8')


def _publish_prebound(output, *, config, bindings, checks, pack, preparations, record_checks):
    with TemporaryDirectory(dir=output, prefix='.prebound-preflight-') as directory:
        staging = Path(directory)
        config_path = staging / 'guard.toml'
        index = staging / 'input-sources.json'
        write_config(config, config_path)
        config_sha256 = sha256(config_path.read_bytes()).hexdigest()
        _write_input_sources(index, pack, bindings, {'path': str(config_path), 'sha256': config_sha256})
        record_pins = {(check_id, side): checks[check_id]['prepared_record_input_sha256_by_side'][side]
                       for check_id in record_checks for side in readiness.SIDES}
        result = readiness.audit(index, pack=pack, sup05_normal_preparations=preparations,
                                 expected_record_inputs=record_pins)
        verified = {(row['check_id'], row['side']) for row in result['rows'] if row['file_binding_valid']}
        if record_pins.keys() - verified:
            raise ValueError('METADATA_RECORD_INPUT_PUBLICATION_UNVERIFIED')
        if preparations is not None:
            verified_sup05 = {row['side'] for row in result['rows'] if row['check_id'] == 'SUP-05'
                              and row['file_binding_valid'] and row.get('sup05_normal_preparation') == preparations[row['side']]}
            if verified_sup05 != set(readiness.SIDES):
                raise ValueError('SUP05_NORMAL_PREPARATION_PUBLICATION_UNVERIFIED')
        if sha256(config_path.read_bytes()).hexdigest() != config_sha256:
            raise ValueError('MVP_INPUT_FILE_CHANGED')

        # Publish the audited config bytes at their final path and bind that path in the emitted index.
        config_ref = {'path': str(output / config_path.name), 'sha256': config_sha256}
        _write_input_sources(index, pack, bindings, config_ref)
        for row in result['rows']:
            if 'files' in row:
                row['files']['configuration'] = config_ref
        result['source_index_sha256'] = sha256(index.read_bytes()).hexdigest()
        result['metadata_wiring'] = checks
        report = staging / 'readiness.json'
        report.write_text(json.dumps(result, indent=2), encoding='utf-8')
        for path in (index, report, config_path):
            (output / path.name).hardlink_to(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for side in readiness.SIDES:
        parser.add_argument(f'--{side}-environment', type=Path, required=True)
        parser.add_argument(f'--{side}-target', type=Path, required=True)
    parser.add_argument('--policy', type=Path, required=True)
    parser.add_argument('--store', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node-image-verification', choices=NODE_IMAGE_PROFILES)
    parser.add_argument('--passive-plt08', action='store_true',
                        help='Project PLT-08 from the exact same-attempt PLT-01 normal control')
    parser.add_argument('--protected-ipc-metadata', action='store_true',
                        help='Read only the approved PLT-02 path metadata')
    parser.add_argument('--observation-run-bindings', type=Path,
                        help='Paired existing OBS-01 RunBinding inputs for read-only record collection')
    parser.add_argument('--gateway-record-inputs', type=Path,
                        help='Paired prebound OBS-01 inputs for one selected, already recorded normal Gateway request')
    parser.add_argument('--normal-stream-buffer', action='store_true',
                        help='Observe only ordinary SSE framing and fixed owned container memory for LIF-04')
    parser.add_argument('--normal-request-latency', action='store_true',
                        help='Observe client timing for one ordinary owned LIF-03 completion')
    parser.add_argument('--normal-sampling', action='store_true',
                        help='Observe one ordinary completion using the approved REQ-10 sampling options')
    parser.add_argument('--normal-token-input', action='store_true',
                        help='Observe an ordinary REQ-09 completion using the bound tokenizer result')
    parser.add_argument('--normal-serving-readiness', action='store_true',
                        help='Observe the owned Pod readiness and ordinary serving correspondence for OBS-02')
    parser.add_argument('--installed-plugin-inventory', action='store_true',
                        help='Read installed plugin entry-point metadata in the owned SUP-10 container')
    parser.add_argument('--sup05-normal-preparations', type=Path,
                        help='Paired SUP-05 preparation receipt pointers with independent SHA-256 pins')
    parser.add_argument('--cilium-egress-status', action='store_true',
                        help='Read reported egress policy state for the owned PLT-07 endpoint')
    parser.add_argument('--normal-startup-load', action='store_true',
                        help='Read the source-checked model load timing for LIF-01')
    parser.add_argument('--normal-compile-cache', action='store_true',
                        help='Read the owned normal startup compile record and cache file metadata for SUP-07')
    parser.add_argument('--normal-safetensors-reader', action='store_true',
                        help='Verify the default Safetensors loader branch during owned normal startup for SUP-12')
    parser.add_argument('--normal-graph-capture', action='store_true',
                        help='Read the source-checked ordinary CUDA graph capture completion for REQ-20')
    parser.add_argument('--copy-record-inputs', type=Path,
                        help='Paired PLT-12 consumer inputs with an existing producer, session and sealed inventory')
    parser.add_argument('--retry-record-inputs', type=Path,
                        help='Paired LIF-12 inputs with an existing producer, session and sealed retry scope')
    parser.add_argument('--collection-status-bindings', type=Path,
                        help='Paired OBS-11 session declarations for existing owned collection status records')
    parser.add_argument('--candidate-target-after-baseline', type=Path,
                        help='Unpublished target path for the operator-managed second phase')
    args = parser.parse_args()
    inputs = {side: {role: getattr(args, f'{side}_{role}') for role in ('environment', 'target')}
              for side in readiness.SIDES}
    pack = load_pack(readiness.ROOT / 'packs/PACK-BASELINE')
    observation = _source(args.observation_run_bindings)[0] if args.observation_run_bindings else None
    gateway = _source(args.gateway_record_inputs)[0] if args.gateway_record_inputs else None
    copies = _source(args.copy_record_inputs)[0] if args.copy_record_inputs else None
    retries = _source(args.retry_record_inputs)[0] if args.retry_record_inputs else None
    collection_status = _source(args.collection_status_bindings)[0] if args.collection_status_bindings else None
    sup05_preparations = None
    if args.sup05_normal_preparations is not None:
        raw, _ = _read(_plain_path(args.sup05_normal_preparations), max_bytes=65536)
        sup05_preparations = readiness._sup05_preparation_refs(strict_json(raw, limit=65536))
    config, bindings, checks = assemble(inputs, args.policy, pack=pack, store=args.store,
                                        node_image_verification=args.node_image_verification, passive_plt08=args.passive_plt08,
                                        protected_ipc_metadata=args.protected_ipc_metadata, observation_run_bindings=observation,
                                        copy_record_inputs=copies, gateway_record_inputs=gateway,
                                        normal_stream_buffer=args.normal_stream_buffer, normal_sampling=args.normal_sampling,
                                        normal_token_input=args.normal_token_input,
                                        normal_serving_readiness=args.normal_serving_readiness,
                                        normal_request_latency=args.normal_request_latency,
                                        installed_plugin_inventory=args.installed_plugin_inventory,
                                        cilium_egress_status=args.cilium_egress_status,
                                        normal_startup_load=args.normal_startup_load,
                                        normal_compile_cache=args.normal_compile_cache,
                                        normal_safetensors_reader=args.normal_safetensors_reader,
                                        normal_graph_capture=args.normal_graph_capture,
                                        sup05_normal_preparations=sup05_preparations, retry_record_inputs=retries,
                                        collection_status_bindings=collection_status)
    staged = staged_config(config, args.candidate_target_after_baseline) if args.candidate_target_after_baseline else None
    preliminary = _source(inputs['candidate']['target'])[1] if staged is not None else None
    protected = [args.policy, args.store, *(path for side in inputs.values() for path in side.values())]
    if staged is not None:
        protected.append(args.candidate_target_after_baseline)
    if observation is not None:
        protected.extend([args.observation_run_bindings, *(Path(row['record_producer']) for row in observation.values())])
    if gateway is not None:
        protected.extend([args.gateway_record_inputs, *(Path(row['record_producer']) for row in gateway.values())])
    if copies is not None:
        protected.extend([args.copy_record_inputs, *(Path(row[key]) for row in copies.values()
                         for key in ('record_producer', 'copy_inventory'))])
    if retries is not None:
        protected.extend([args.retry_record_inputs, *(Path(row[key]) for row in retries.values()
                         for key in ('record_producer', 'retry_scope'))])
    if collection_status is not None:
        protected.append(args.collection_status_bindings)
    if sup05_preparations is not None:
        # Each receipt's directory contains all files emitted by prepare-sup05-normal.
        protected.extend([args.sup05_normal_preparations, *(Path(row['path']).parent for row in sup05_preparations.values())])
    output = reserve_output(args.output, protected=protected)
    bound_records = {cid for cid in PREBOUND_RECORD_EXECUTORS if checks.get(cid, {}).get('configuration_enabled')}
    enabled_preparations = (sup05_preparations if checks.get('SUP-05', {}).get('configuration_enabled') else None)
    if bound_records or enabled_preparations is not None:
        _publish_prebound(output, config=config, bindings=bindings, checks=checks, pack=pack,
                          preparations=enabled_preparations, record_checks=bound_records)
        return
    config_path = output / ('preliminary-guard.toml' if staged is not None else 'guard.toml')
    write_config(config, config_path)
    cfg_ref = {'path': str(config_path), 'sha256': sha256(config_path.read_bytes()).hexdigest()}
    index = output / 'input-sources.json'
    _write_input_sources(index, pack, bindings, cfg_ref)
    result = readiness.audit(index, pack=pack)
    result['metadata_wiring'] = checks
    if staged is not None:
        write_config(staged, output / 'guard.toml')
        result['candidate_handoff'] = {'preliminary_target': preliminary,
            'candidate_target_after_baseline': str(_plain_path(args.candidate_target_after_baseline)),
            'fresh_generation_verified': False, 'revalidation_required': True}
    (output / 'readiness.json').write_text(json.dumps(result, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
