"""M-A 입력 파일·정책·실행 결합을 외부 IO 없이 대조한다. 실행 자격을 발급하지 않는다."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tomllib

from guard.collectors.file import load_environment, load_policy
from guard.configuration import merge_config
from guard.core.select import check_policy_value, _in_scope
from guard.executors import run_binding
from guard.models import load_pack
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs/dev-1/ma7-session-input-plan.json'
SCHEMA = 'guard-ma7-input-sources/1'
SIDES = ('baseline', 'candidate')


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError('MA7_DUPLICATE_JSON_KEY')
        value[key] = item
    return value


def _read(path):
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= 8 * 1024**2:
        raise ValueError('MA7_INPUT_FILE_INVALID')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError('MA7_INPUT_FILE_INVALID')
        chunks, size = [], 0
        while size <= 8 * 1024**2:
            block = os.read(fd, min(65536, 8 * 1024**2 + 1 - size))
            if not block:
                break
            chunks.append(block)
            size += len(block)
        raw = b''.join(chunks)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    current = path.lstat()
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    if (len(raw) != before.st_size
            or len({identity(s) for s in (before, opened, after, current)}) != 1):
        raise ValueError('MA7_INPUT_FILE_CHANGED')
    return raw, identity(current)


def _code(error):
    if type(error) in (ValueError, run_binding.RunBindingUnavailable) and len(error.args) == 1:
        value = error.args[0]
        if type(value) is str and re.fullmatch('[A-Z][A-Z0-9_]{0,79}', value):
            return value
    return 'MA7_INPUT_INVALID'


def inspect_side(source, check, pack, *, issued_at):
    """파일/형식/결합만 확인한다. 수집·엔진·실환경 관측과 구분한다."""
    files, identities = {}, {}
    for role in ('environment', 'policy', 'configuration'):
        item = source[role]
        if (type(item) is not dict or set(item) != {'path', 'sha256'}
                or type(item['path']) is not str or type(item['sha256']) is not str
                or re.fullmatch('[0-9a-f]{64}', item['sha256']) is None):
            raise ValueError('MA7_INPUT_REFERENCE_INVALID')
        raw, identity = _read(item['path'])
        if sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('MA7_INPUT_HASH_MISMATCH')
        files[role], identities[role] = raw, identity
    raw_env = json.loads(files['environment'], object_pairs_hook=_pairs)
    if raw_env.get('kind') != 'ENV':
        raise ValueError('MA7_SEALED_ENV_REQUIRED')
    env = load_environment(Path(source['environment']['path']))
    expected = versions()[source['side']]
    if (env.engine.name, env.engine.version, env.engine.image_digest) != (
            expected['engine'], expected['version'], expected['image']):
        raise ValueError('MA7_ENGINE_SIDE_MISMATCH')
    if not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver:
        raise ValueError('MA7_GPU_FACTS_REQUIRED')
    policy = load_policy(Path(source['policy']['path']), pack)
    if not _in_scope(check.id, policy):
        raise ValueError('MA7_POLICY_SCOPE_MISMATCH')
    values = policy.values.get(check.id) or {}
    for field in check.policy_params:
        if field.key not in values:
            if field.required:
                raise ValueError('MA7_POLICY_VALUE_MISSING')
        else:
            check_policy_value(check.id, field.key, field.type, values[field.key])
    config = tomllib.loads(files['configuration'].decode('utf-8'))
    config = merge_config(config, config.get('sides', {}).get(source['side'], {}))
    implementation = config.get('executors', {}).get(check.executor)
    if implementation != 'kubernetes':
        raise ValueError('MA7_KUBERNETES_EXECUTOR_REQUIRED')
    executor_cfg = config.get(check.executor, {})
    target_path = executor_cfg.get('target')
    referenced = {}
    binding_config = run_binding.run_config(config, check.id)
    for table in (executor_cfg, binding_config or {}):
        for key in ('target', 'prepared', 'manifest', 'scenario', 'egress_binding', 'api_binding'):
            if key in table:
                raw_file, identity = _read(table[key])
                referenced[str(table[key])] = raw_file, identity
    if target_path is not None:
        raw_target, _ = referenced[str(target_path)]
        target = json.loads(raw_target, object_pairs_hook=_pairs)
        if target.get('environment_ref') not in (None, env.ref().model_dump(mode='json')):
            raise ValueError('MA7_TARGET_ENVIRONMENT_MISMATCH')
    binding, _ = run_binding.effective_values(check, executor_cfg, env, values,
        side=source['side'], attempt_id='ma7-offline-preflight', issued_at=issued_at,
        valid_from=policy.valid_from, valid_until=policy.valid_until,
        run_cfg=binding_config)
    for role, identity in identities.items():
        after, current = _read(source[role]['path'])
        if current != identity or after != files[role]:
            raise ValueError('MA7_INPUT_FILE_CHANGED')
    for path, (raw_file, identity) in referenced.items():
        after, current = _read(path)
        if after != raw_file or current != identity:
            raise ValueError('MA7_INPUT_FILE_CHANGED')
    blockers = ['LIVE_TARGET_AND_EXECUTOR_PREFLIGHT_REQUIRED']
    if env.data_origin.value == 'EXAMPLE':
        blockers.append('EXAMPLE_ENV_NOT_ACTUAL')
    return {'environment_ref': env.ref().model_dump(mode='json'),
        'environment_origin': env.data_origin.value,
        'files': {k: {'path': source[k]['path'], 'sha256': sha256(v).hexdigest()} for k, v in files.items()},
        'binding_schema': binding['schema'] if binding else None,
        'binding_files': binding['files'] if binding else {},
        'file_binding_valid': True, 'blocking_codes': blockers,
        'live_target_verified': False, 'input_preflight_passed': False,
        'ready_for_execution': False, 'actual_results': None}


def audit(index_file, *, issued_at=None):
    raw, _ = _read(index_file)
    index = json.loads(raw, object_pairs_hook=_pairs)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    plan = json.loads(PLAN.read_bytes())
    check_ids = sorted(r['check_id'] for r in plan['checks'] if r['work_group'] == 'M-A')
    if (type(index) is not dict or set(index) != {'schema', 'pack_ref', 'inputs'}
            or index['schema'] != SCHEMA or index['pack_ref'] != pack.ref().model_dump(mode='json')
            or type(index['inputs']) is not list or len(index['inputs']) > 54):
        raise ValueError('MA7_SOURCE_INDEX_INVALID')
    sources = {}
    for item in index['inputs']:
        if (type(item) is not dict or set(item) != {'check_id', 'side', 'environment', 'policy', 'configuration'}
                or item['check_id'] not in check_ids or item['side'] not in SIDES):
            raise ValueError('MA7_SOURCE_INDEX_INVALID')
        identity = item['check_id'], item['side']
        if identity in sources:
            raise ValueError('MA7_SOURCE_DUPLICATE')
        sources[identity] = item
    rows = []
    now = issued_at or datetime.now(timezone.utc)
    for cid in check_ids:
        for side in SIDES:
            row = {'check_id': cid, 'side': side, 'file_binding_valid': False,
                'live_target_verified': False, 'input_preflight_passed': False,
                'ready_for_execution': False, 'actual_results': None}
            if (cid, side) not in sources:
                row['blocking_codes'] = ['ACTUAL_INPUT_FILES_MISSING']
            else:
                try:
                    row.update(inspect_side(sources[cid, side], pack.checks[cid], pack, issued_at=now))
                except (OSError, ValueError, TypeError, KeyError) as error:
                    row['blocking_codes'] = [_code(error)]
            rows.append(row)
    return {'schema': 'guard-ma7-file-binding-preflight/1',
        'scope': '입력 파일·정책 형식·실행 결합만 대조. 실제 수집/실행기 자격/보안 판정은 하지 않음.',
        'pack_ref': pack.ref().model_dump(mode='json'), 'source_index_sha256': sha256(raw).hexdigest(),
        'generator_source_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
        'rows': rows, 'file_binding_valid_sides': sum(r['file_binding_valid'] for r in rows),
        'live_target_verified': False, 'input_preflight_passed': False,
        'ready_for_execution': False, 'engine_executed': False, 'actual_results': None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.index)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'파일_결합_유효': result['file_binding_valid_sides'], '실환경_자격': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
