"""Assemble an offline configuration for paired owned Kubernetes metadata checks."""
from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from guard.adapters.engine_profiles import supports
from guard.configuration import (GATEWAY_RECORD_SNAPSHOT_OPTION, KUBERNETES_BOUND_CHECK_OPTIONS,
                                 RECORD_SNAPSHOT_OPTIONS, bounded_metadata_scopes, merge_config, update_allowed_checks)
from guard.core.execution_recipe import validate_policy_time
from guard.core.run import RunConfig, _blocked_reason
from guard.core.select import _in_scope, select
from guard.executors import passive_api_hop, registry, run_binding, template_supply_approval as template_supply
from guard.executors.kubernetes_gateway_observation import SNAPSHOT_SOURCE, snapshot_owner
from guard.executors.kubernetes_ipc_product import KubernetesIPCProductExecutor
from guard.executors.kubernetes_log_analysis import KubernetesLogAnalysisExecutor
from guard.executors.kubernetes_normal_token_input import normal_input_transports
from guard.executors.kubernetes_observation import KubernetesRequiredEventExecutor
from guard.executors.kubernetes_template_supply import KubernetesTemplateSupplyExecutor
from guard.models import Environment, Policy, load_pack, utcnow
from guard.models.artifacts import model_closure_sha256
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import INPUT_VALIDATION_PROFILE, KubernetesBindingError, KubernetesHTTP, REQUEST_BINDING_PROFILE
from guard.update_validation import _plain_path, reserve_output
from tools import mvp_live_readiness as readiness
from tools.j02_run_config import write_config
from tools.mvp_a_input_preflight import _pairs, _read
from tools.mvp_versions import versions

CHECK_EXECUTORS = {'SUP-04': 'container_config', 'PLT-01': 'container_config', 'PLT-04': 'credential_exposure',
                   'SUP-03': 'template_validation', 'REQ-07': 'template_validation', 'OBS-03': 'log_analysis',
                   'OBS-04': 'boundary_egress', 'PLT-06': 'unix_ipc',
                   'OBS-01': 'required_event_record', 'PLT-12': 'required_event_record', 'PLT-02': 'unix_ipc',
                   'LIF-04': 'stream_buffer', 'REQ-09': 'input_validation', 'REQ-10': 'sampling_validation',
                   passive_api_hop.CHECK: passive_api_hop.EXECUTOR}
CHECKS = tuple(cid for cid in CHECK_EXECUTORS
               if cid not in {*RECORD_SNAPSHOT_OPTIONS, 'PLT-02', 'LIF-04', 'REQ-09', 'REQ-10', passive_api_hop.CHECK})
NODE_IMAGE_PROFILES = ('local-containerd-v1', 'local-k3s-containerd-v1')
METADATA_EXECUTORS = frozenset(CHECK_EXECUTORS.values())
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
    is_copy = check_id == 'PLT-12'
    fields = {'record_session', 'record_producer', 'copy_inventory' if is_copy else 'test_actors'}
    file_fields = ('record_producer', 'copy_inventory') if is_copy else ('record_producer',)
    bindings = {}
    for side, entry in value.items():
        if type(entry) is not dict or set(entry) != fields:
            raise ValueError('OBSERVATION_RUN_BINDING_INVALID')
        bindings[side] = dict(entry)
        for field in file_fields:
            if type(entry[field]) is not str or not entry[field]:
                raise ValueError('OBSERVATION_RUN_BINDING_INVALID')
            source = Path(entry[field]).absolute()
            _read(source)
            bindings[side][field] = str(source)
    return bindings


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


def staged_config(config, fresh_candidate_target):
    """Use a separate, unpublished file for A's after-baseline candidate phase."""
    new_path = _plain_path(Path(fresh_candidate_target))
    if new_path.exists() or not config['sides']['candidate']:
        raise ValueError('METADATA_FRESH_CANDIDATE_PATH_REQUIRED')
    if config.get('check_config', {}).get('OBS-01', {}).get(GATEWAY_RECORD_SNAPSHOT_OPTION) is True:
        raise ValueError('GATEWAY_RECORDS_REQUIRE_PREBOUND_PAIR')
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
    restricted_executors = {CHECK_EXECUTORS[cid] for cid in KUBERNETES_BOUND_CHECK_OPTIONS}
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
    return dict.fromkeys(executors, new.target_sha256)


def _check_reason(check, env, target, transport, values, *, policy, side, target_path, record_inputs):
    if not _in_scope(check.id, policy):
        return 'MVP_POLICY_SCOPE_MISMATCH'
    if any(key.required and key.key not in values for key in check.policy_params):
        return 'MVP_POLICY_VALUE_MISSING'
    if check.executor != CHECK_EXECUTORS[check.id]:
        return 'METADATA_EXECUTOR_UNAVAILABLE'
    if check.id == passive_api_hop.CHECK:
        try:
            passive_api_hop.validate_policy(check, values)
        except passive_api_hop.PassiveApiHopUnavailable as exc:
            return str(exc)
        return None
    try:
        config = {check.executor: {CHECK_OPTIONS[check.id]: True} if check.id in CHECK_OPTIONS else {}}
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
             gateway_record_inputs=None, normal_stream_buffer=False, normal_sampling=False, normal_token_input=False):
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
               (('OBS-01', observation_run_bindings), ('PLT-12', copy_record_inputs)) if value is not None}
    if gateway_record_inputs is not None:
        if 'OBS-01' in records:
            raise ValueError('GATEWAY_RECORD_INPUT_MODE_CONFLICT')
        records['OBS-01'] = _gateway_record_inputs(gateway_record_inputs)
    optional = {passive_api_hop.CHECK: passive_plt08, 'PLT-02': protected_ipc_metadata,
                'LIF-04': normal_stream_buffer, 'REQ-10': normal_sampling, 'REQ-09': normal_token_input,
                **dict.fromkeys(records, True)}
    check_ids = (*CHECKS, *(cid for cid, requested in optional.items() if requested))
    for check_id in check_ids:
        check = pack.checks[check_id]
        values = policy.values.get(check_id) or {}
        reasons = {side: _check_reason(check, env, target, transport, values, policy=policy, side=side,
                                       target_path=target_pointer['path'], record_inputs=records.get(check_id, {}).get(side))
                   for side, (env, target, transport, _, target_pointer) in sides.items()}
        candidates[check_id] = {'configuration_enabled': not any(reasons.values()), 'sides': reasons}
    container_checks = ('SUP-04', 'PLT-01')
    if any(not candidates[cid]['configuration_enabled'] and _in_scope(cid, policy) for cid in container_checks):
        for cid in container_checks:
            entry = candidates[cid]
            if entry['configuration_enabled']:
                entry['configuration_enabled'] = False
                entry['sides'] = dict.fromkeys(sides, 'SHARED_CONTAINER_CHECK_INPUT_UNAVAILABLE')
    passive = candidates.get(passive_api_hop.CHECK)
    if passive and not candidates[passive_api_hop.SOURCE_CHECK]['configuration_enabled']:
        passive['configuration_enabled'] = False
        passive['sides'] = {side: reason or 'PASSIVE_API_HOP_SOURCE_NOT_ADMITTED'
                            for side, reason in passive['sides'].items()}
    bindings = []
    for check_id, decision in candidates.items():
        if not decision['configuration_enabled']:
            continue
        check = pack.checks[check_id]
        config['executors'][check.executor] = 'kubernetes'
        for side, (_, _, _, env_pointer, target_pointer) in sides.items():
            settings = config['sides'][side]
            settings[check.executor] = {'target': target_pointer['path']}
            if check_id in records:
                run_cfg = dict(records[check_id][side])
                if check_id == 'PLT-12':
                    settings.setdefault('check_config', {})[check_id] = {'copy_inventory': run_cfg.pop('copy_inventory')}
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
    shared_checks = ('SUP-03', 'OBS-04', 'PLT-06', 'LIF-04', 'REQ-09', *RECORD_SNAPSHOT_OPTIONS, passive_api_hop.CHECK)
    anchors = [cid for cid in shared_checks if candidates.get(cid, {}).get('configuration_enabled')]
    if anchors:
        plan = select(pack, [base, candidate], policy, mode='update')
        run = RunConfig(executors=config['executors'])
        for anchor in anchors:
            siblings = {}
            for check in pack.active_checks():
                if check.executor != CHECK_EXECUTORS[anchor] or check.id in allowed:
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
    parser.add_argument('--normal-sampling', action='store_true',
                        help='Observe one ordinary completion using the approved REQ-10 sampling options')
    parser.add_argument('--normal-token-input', action='store_true',
                        help='Observe an ordinary REQ-09 completion using the bound tokenizer result')
    parser.add_argument('--copy-record-inputs', type=Path,
                        help='Paired PLT-12 consumer inputs with an existing producer, session and sealed inventory')
    parser.add_argument('--candidate-target-after-baseline', type=Path,
                        help='Unpublished target path for the operator-managed second phase')
    args = parser.parse_args()
    inputs = {side: {role: getattr(args, f'{side}_{role}') for role in ('environment', 'target')}
              for side in readiness.SIDES}
    pack = load_pack(readiness.ROOT / 'packs/PACK-BASELINE')
    observation = _source(args.observation_run_bindings)[0] if args.observation_run_bindings else None
    gateway = _source(args.gateway_record_inputs)[0] if args.gateway_record_inputs else None
    copies = _source(args.copy_record_inputs)[0] if args.copy_record_inputs else None
    config, bindings, checks = assemble(inputs, args.policy, pack=pack, store=args.store,
                                        node_image_verification=args.node_image_verification, passive_plt08=args.passive_plt08,
                                        protected_ipc_metadata=args.protected_ipc_metadata, observation_run_bindings=observation,
                                        copy_record_inputs=copies, gateway_record_inputs=gateway,
                                        normal_stream_buffer=args.normal_stream_buffer, normal_sampling=args.normal_sampling,
                                        normal_token_input=args.normal_token_input)
    staged = staged_config(config, args.candidate_target_after_baseline) if args.candidate_target_after_baseline else None
    preliminary = _source(inputs['candidate']['target'])[1] if staged is not None else None
    protected = [args.policy, args.store, *(path for side in inputs.values() for path in side.values())]
    if staged is not None:
        protected.append(args.candidate_target_after_baseline)
    if observation is not None:
        protected.extend([args.observation_run_bindings, *(row['record_producer'] for row in observation.values())])
    if gateway is not None:
        protected.extend([args.gateway_record_inputs, *(row['record_producer'] for row in gateway.values())])
    if copies is not None:
        protected.extend([args.copy_record_inputs, *(row[key] for row in copies.values()
                         for key in ('record_producer', 'copy_inventory'))])
    output = reserve_output(args.output, protected=protected)
    config_path = output / ('preliminary-guard.toml' if staged is not None else 'guard.toml')
    write_config(config, config_path)
    cfg_ref = {'path': str(config_path), 'sha256': sha256(config_path.read_bytes()).hexdigest()}
    index = output / 'input-sources.json'
    index.write_text(json.dumps({**readiness.template(pack),
        'inputs': [{**entry, 'configuration': cfg_ref} for entry in bindings]}, indent=2), encoding='utf-8')
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
