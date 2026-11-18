"""Inventory all active MVP sides and verify supplied files without running engines.

This is a preparation gate, not runtime qualification or a security result.
Enterprise values, current ENV/target identities and results are never generated.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tomllib

import yaml

from guard.adapters import vllm_native_input_profiles, vllm_native_input_rejection
from guard.collectors import native_input, native_input_qualification
from guard.collectors import native_input_rejection_qualification, reused_input_qualification
from guard.collectors.file import load_environment, load_policy
from guard.configuration import (NORMAL_TOKEN_INPUT_OPTION, bounded_metadata_scopes, for_check, merge_config,
                                 normal_token_input_requested)
from guard.core.select import _in_scope, check_policy_value
from guard.executors import passive_api_hop, registry, run_binding
from guard.executors.kubernetes_normal_token_input import normal_input_transports
from guard.input_matrix_policy import PROFILE as INPUT_MATRIX
from guard.models import load_pack
from guard.reused_matrix_policy import PROFILE as REUSED_MATRIX
from guard.runtime.kubernetes_http import KubernetesBindingError, _validate as validate_target_binding
from tools.mvp_a_input_preflight import _code, _pairs, _read
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'guard-mvp-live-input-sources/1'
SIDES = ('baseline', 'candidate')


def template(pack):
    return {'schema': SCHEMA, 'pack_ref': pack.ref().model_dump(mode='json'), 'inputs': []}


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


def _inspect(source, check, pack, issued_at):
    files, identities = {}, {}
    for role in ('environment', 'policy', 'configuration'):
        pointer = source[role]
        if (type(pointer) is not dict or set(pointer) != {'path', 'sha256'}
                or type(pointer['path']) is not str or not pointer['path']
                or type(pointer['sha256']) is not str or len(pointer['sha256']) != 64
                or any(c not in '0123456789abcdef' for c in pointer['sha256'])):
            raise ValueError('MVP_INPUT_REFERENCE_INVALID')
        raw, identity = _read(pointer['path'])
        if sha256(raw).hexdigest() != pointer['sha256']:
            raise ValueError('MVP_INPUT_HASH_MISMATCH')
        files[role], identities[role] = raw, identity
    raw_env = json.loads(files['environment'], object_pairs_hook=_pairs)
    if raw_env.get('kind') != 'ENV':
        raise ValueError('MVP_SEALED_ENV_REQUIRED')
    env = load_environment(Path(source['environment']['path']))
    expected = versions()[source['side']]
    if (env.engine.name, env.engine.version, env.engine.image_digest) != (
            expected['engine'], expected['version'], expected['image']):
        raise ValueError('MVP_ENGINE_SIDE_MISMATCH')
    if not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver:
        raise ValueError('MVP_GPU_FACTS_REQUIRED')
    policy = load_policy(Path(source['policy']['path']), pack)
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
    allowed = update_allowed_checks(config, pack)
    if allowed is not None and check.id not in allowed:
        raise ValueError(UPDATE_CHECK_OUTSIDE_SCOPE)
    side_configs = {side: merge_config(config, config.get('sides', {}).get(side, {})) for side in SIDES}
    if any(normal_token_input_requested(raw) for raw in side_configs.values()):
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
    normal_token_input = check.id == 'REQ-09' and cfg.get(NORMAL_TOKEN_INPUT_OPTION) is True
    target = cfg.get('target')
    if implementation == 'kubernetes' and target is None:
        raise ValueError('MVP_CURRENT_TARGET_REQUIRED')
    target_state = None
    if target is not None:
        raw_target, identity = _read(target)
        target_data = json.loads(raw_target, object_pairs_hook=_pairs)
        if target_data.get('environment_ref') != env.ref().model_dump(mode='json'):
            raise ValueError('MVP_TARGET_ENVIRONMENT_MISMATCH')
        target_state = (target, raw_target, identity)
    if normal_token_input:
        try:
            normal_input_transports(target_data, env, duration=1)
        except (KubernetesBindingError, ValueError):
            raise ValueError('NORMAL_TOKEN_INPUT_TARGET_UNAVAILABLE') from None
    if passive_hop:
        passive_api_hop.validate_policy(check, values)
        passive_api_hop.validate_target(target_data, env)
        binding = None  # The source evidence belongs to a future run attempt.
    else:
        binding, effective = run_binding.effective_values(check, cfg, env, values,
            side=source['side'], attempt_id='mvp-offline-readiness', issued_at=issued_at,
            valid_from=policy.valid_from, valid_until=policy.valid_until,
            run_cfg=run_binding.run_config(config, check.id), policy_ref=policy.ref())
        # Use the same executor configuration and policy validation as runtime.
        try:
            executor = registry.build_executor(check.executor, implementation, config,
                engine=env.engine.name, version=env.engine.version)
            executor.validate_policy(check, effective)
        except (ValueError, TypeError, KeyError):
            raise ValueError('MVP_EXECUTOR_POLICY_INVALID') from None
    if requires_native_matrix and not normal_token_input:
        _check_native_prepared(cfg.get('native_input_capture'), target_data, env)
    for role, identity in identities.items():
        after, current = _read(source[role]['path'])
        if current != identity or after != files[role]:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    if target_state is not None:
        after, current = _read(target_state[0])
        if after != target_state[1] or current != target_state[2]:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    blockers = ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
    if passive_hop:
        blockers.append('SAME_ATTEMPT_PLT01_CONTROL_REQUIRED')
    if env.data_origin.value == 'EXAMPLE':
        blockers.append('EXAMPLE_ENV_NOT_ACTUAL')
    return {'file_binding_valid': True, 'executor_policy_valid': True, 'environment_ref': env.ref().model_dump(mode='json'),
            'environment_origin': env.data_origin.value, 'implementation': implementation,
            'binding_schema': binding['schema'] if binding else None,
            'files': {role: source[role] for role in files}, 'blocking_codes': blockers,
            **({NORMAL_TOKEN_INPUT_OPTION: True} if normal_token_input else {})}


def audit(index_file, *, pack=None, issued_at=None, side=None):
    if side is not None and (type(side) is not str or side not in SIDES):
        raise ValueError('MVP_SIDE_SELECTION_INVALID')
    selected_sides = SIDES if side is None else (side,)
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
        if (type(item) is not dict or set(item) != {'check_id', 'side', 'environment', 'policy', 'configuration'}
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
                    row.update(_inspect(sources[cid, selected_side], check, pack, now))
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
