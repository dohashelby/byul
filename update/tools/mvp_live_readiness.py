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

from guard.collectors.file import load_environment, load_policy
from guard.configuration import for_check, merge_config
from guard.core.select import _in_scope, check_policy_value
from guard.executors import registry, run_binding
from guard.models import load_pack
from tools.mvp_a_input_preflight import _code, _pairs, _read
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'guard-mvp-live-input-sources/1'
SIDES = ('baseline', 'candidate')


def template(pack):
    return {'schema': SCHEMA, 'pack_ref': pack.ref().model_dump(mode='json'), 'inputs': []}


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
    config = tomllib.loads(files['configuration'].decode('utf-8'))
    config = merge_config(config, config.get('sides', {}).get(source['side'], {}))
    config = for_check(config, check.executor, check.id)
    implementation = config.get('executors', {}).get(check.executor)
    if implementation not in ('real', 'kubernetes'):
        raise ValueError('MVP_ACTIVE_EXECUTOR_REQUIRED')
    if registry.resolve_executor(check.executor, implementation, engine=env.engine.name,
                                 version=env.engine.version) is None:
        raise ValueError('MVP_EXECUTOR_UNAVAILABLE')
    cfg = config.get(check.executor, {})
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
    binding, _ = run_binding.effective_values(check, cfg, env, values,
        side=source['side'], attempt_id='mvp-offline-readiness', issued_at=issued_at,
        valid_from=policy.valid_from, valid_until=policy.valid_until,
        run_cfg=run_binding.run_config(config, check.id), policy_ref=policy.ref())
    for role, identity in identities.items():
        after, current = _read(source[role]['path'])
        if current != identity or after != files[role]:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    if target_state is not None:
        after, current = _read(target_state[0])
        if after != target_state[1] or current != target_state[2]:
            raise ValueError('MVP_INPUT_FILE_CHANGED')
    blockers = ['LIVE_EXECUTOR_PREFLIGHT_REQUIRED']
    if env.data_origin.value == 'EXAMPLE':
        blockers.append('EXAMPLE_ENV_NOT_ACTUAL')
    return {'file_binding_valid': True, 'environment_ref': env.ref().model_dump(mode='json'),
            'environment_origin': env.data_origin.value, 'implementation': implementation,
            'binding_schema': binding['schema'] if binding else None,
            'files': {role: source[role] for role in files}, 'blocking_codes': blockers}


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
        for selected_side in selected_sides:
            expected = expected_versions[selected_side]
            implementations = [impl for impl in ('real', 'kubernetes') if registry.resolve_executor(
                check.executor, impl, engine=expected['engine'], version=expected['version']) is not None]
            row = {'check_id': cid, 'side': selected_side, 'executor': check.executor,
                   'security_rules_present': security_rules, 'available_implementations': implementations,
                   'expected_engine': expected, 'required_policy_keys': [p.key for p in check.policy_params if p.required],
                   'required_observations': [o.key for o in check.required_observations],
                   'file_binding_valid': False, 'live_target_verified': False,
                   'input_preflight_passed': False, 'ready_for_execution': False,
                   'engine_executed': False, 'actual_results': None}
            if (cid, selected_side) not in sources:
                row['blocking_codes'] = ['ACTUAL_INPUT_FILES_MISSING']
            else:
                try:
                    row.update(_inspect(sources[cid, selected_side], check, pack, now))
                except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
                    row['blocking_codes'] = [_code(error)]
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
