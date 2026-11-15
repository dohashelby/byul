"""LIF-08 파일 소비용 실제 입력을 대조·복사한다. 엔진 실행/정책 확정/실환경 자격은 없다."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from guard.collectors.file import load_policy
from guard.collectors import gpu_common_inputs
from guard.core.select import check_policy_value
from guard.executors import registry, run_binding
from guard.models import Environment, canonical_json, load_pack, utcnow
from tools.mvp_a_input_preflight import _read, _pairs
from tools.mvp_versions import versions

ROOT = Path(__file__).resolve().parents[1]


def prepare(environment, target, prepared, common_input, policy, output, *, side):
    try:
        return _prepare(environment, target, prepared, common_input, policy, output, side=side)
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError('MA7_RANK_INPUT_INVALID') from None


def _prepare(environment, target, prepared, common_input, policy, output, *, side):
    paths = {k: Path(v) for k, v in {'environment': environment, 'target': target, 'prepared': prepared,
        'common_input': common_input, 'policy': policy}.items()}
    snapshots = {k: _read(p) for k, p in paths.items()}
    def document(name): return json.loads(snapshots[name][0], object_pairs_hook=_pairs)
    env = Environment.model_validate(document('environment'))
    if not env.hash_ok() or env.kind.value != 'ENV' or side not in versions():
        raise ValueError('MA7_RANK_ENVIRONMENT_INVALID')
    expected = versions()[side]
    if ((env.engine.name, env.engine.version, env.engine.image_digest) !=
            (expected['engine'], expected['version'], expected['image']) or env.runtime.tp_size != 2
            or env.runtime.gpu_count is None or env.runtime.gpu_count < 2):
        raise ValueError('MA7_RANK_ENVIRONMENT_SIDE_MISMATCH')
    target_value, manifest, record = document('target'), document('prepared'), document('common_input')
    if (type(target_value) is not dict or target_value.get('environment_ref') != env.ref().model_dump(mode='json')
            or type(manifest) is not dict or type(record) is not dict or manifest.get('image') != env.engine.image_digest
            or manifest.get('session') != record.get('session')
            or record.get('schema') not in {'guard-gpu-common-input/1', 'guard-gpu-common-input/4'}):
        raise ValueError('MA7_RANK_FILE_SCOPE_MISMATCH')
    scope = {'environment_ref': env.ref().model_dump(mode='json'), 'target_sha256': sha256(canonical_json(target_value)).hexdigest(),
        'prepared_sha256': sha256(canonical_json(manifest)).hexdigest(), 'session': manifest['session']}
    checked = gpu_common_inputs.validate(record, kind='rank-fault', **scope)
    pack = load_pack(ROOT / 'packs/PACK-BASELINE'); pol = load_policy(paths['policy'], pack)
    if pol.scope.include != ['LIF-08'] or pol.scope.exclude:
        raise ValueError('MA7_RANK_EXPLICIT_POLICY_SCOPE_REQUIRED')
    values = pol.values.get('LIF-08') or {}; check = pack.checks['LIF-08']
    if set(values) & run_binding.owned_keys(check):
        raise ValueError('MA7_RANK_BINDING_BELONGS_TO_EXECUTION')
    for field in check.policy_params:
        if field.key not in values:
            if field.required: raise ValueError('MA7_RANK_POLICY_VALUE_MISSING')
        else:
            check_policy_value(check.id, field.key, field.type, values[field.key])
    output = Path(output).absolute()
    names = {'environment': 'environment.json', 'target': 'target.json', 'prepared': 'prepared.json',
        'common_input': 'common-input.json', 'policy': 'policy.yaml'}
    cfg = {'input_mode': 'common-records', 'target': str(paths['target']), 'prepared': str(paths['prepared']),
        'common_inputs': {'prepared': str(paths['prepared']), 'session': manifest['session'],
            'data_origin': 'EXAMPLE' if env.data_origin.value == 'EXAMPLE' or pol.data_origin.value == 'EXAMPLE' else 'DECLARED',
            'records': {'rank-fault': str(paths['common_input'])}}}
    factory = registry.resolve_executor(check.executor, 'common-input', engine=env.engine.name, version=env.engine.version)
    binding = None
    if factory is not None:
        binding, effective = run_binding.effective_values(check, cfg, env, values, side=side,
            attempt_id='ma7-rank-input-file-preflight', issued_at=utcnow(), valid_from=pol.valid_from, valid_until=pol.valid_until)
        factory({'rank_failure': cfg}).validate_policy(check, effective)
    for key, path in paths.items():
        if _read(path) != snapshots[key]: raise ValueError('MA7_RANK_INPUT_FILE_CHANGED')
    cfg['target'], cfg['prepared'] = str(output / names['target']), str(output / names['prepared'])
    cfg['common_inputs']['prepared'] = cfg['prepared']
    cfg['common_inputs']['records']['rank-fault'] = str(output / names['common_input'])
    text = '[executors]\nrank_failure="common-input"\n[rank_failure]\n'
    text += ''.join(k + '=' + json.dumps(cfg[k]) + '\n' for k in ('input_mode', 'target', 'prepared'))
    text += '[rank_failure.common_inputs]\n' + ''.join(k + '=' + json.dumps(cfg['common_inputs'][k]) + '\n'
        for k in ('prepared', 'session', 'data_origin'))
    text += '[rank_failure.common_inputs.records]\nrank-fault=' + json.dumps(cfg['common_inputs']['records']['rank-fault']) + '\n'
    result = {'schema': 'guard-ma7-rank-product-inputs/1', 'check_id': 'LIF-08', 'side': side,
        'data_origin': cfg['common_inputs']['data_origin'], 'pack_ref': pack.ref().model_dump(mode='json'),
        'scope': scope, 'policy_status': pol.status.value, 'policy_file_changed': False,
        'file_binding_valid': True, 'implementation_available': factory is not None,
        'run_binding_preflight_valid': binding is not None, 'run_binding_preview': binding,
        'record_validation': checked, 'required_implementation': 'rank_failure:common-input',
        'source_files': {k: {'path': str(paths[k]), 'sha256': sha256(v[0]).hexdigest()} for k, v in snapshots.items()},
        'files': {names[k]: sha256(v[0]).hexdigest() for k, v in snapshots.items()},
        'engine_executed': False, 'live_target_verified': False, 'input_preflight_passed': False,
        'ready_for_execution': False, 'actual_results': None}
    result['files']['guard.toml'] = sha256(text.encode()).hexdigest()
    output.mkdir(mode=0o700, exist_ok=False)
    for key, name in names.items():
        with (output / name).open('xb') as stream: stream.write(snapshots[key][0])
    with (output / 'guard.toml').open('x', encoding='utf-8') as stream: stream.write(text)
    with (output / 'manifest.json').open('xb') as stream: stream.write(canonical_json(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('environment', 'target', 'prepared', 'common-input', 'policy', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--side', choices=['baseline', 'candidate'], required=True)
    args = parser.parse_args()
    result = prepare(args.environment, args.target, args.prepared, args.common_input, args.policy, args.output, side=args.side)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__': main()
