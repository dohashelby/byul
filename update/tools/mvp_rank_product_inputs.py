"""LIF-08 파일 소비용 실제 입력을 대조·복사한다. 엔진 실행/정책 확정/실환경 자격은 없다."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from guard.collectors.file import load_policy
from guard.collectors import gpu_common_inputs
from guard.core.select import check_policy_value
from guard.executors import registry, run_binding
from guard.models import Environment, canonical_json, load_pack, utcnow
from guard.update_validation import _plain_path, reserve_output
from tools.mvp_a_input_preflight import _read, _pairs
from tools.mvp_versions import versions
from tools.j02_run_config import write_config

ROOT = Path(__file__).resolve().parents[1]
_SIDE_SCHEMA = 'guard-ma7-rank-product-inputs/1'
PAIR_SELECTION_SCHEMA = 'guard-ma7-rank-paired-selection/1'
PAIR_SCHEMA = 'guard-ma7-rank-paired-product-inputs/1'
_NAMES = {'environment': 'environment.json', 'target': 'target.json', 'prepared': 'prepared.json',
          'common_input': 'common-input.json', 'policy': 'policy.yaml'}


def prepare(environment, target, prepared, common_input, policy, output, *, side):
    try:
        return _prepare(environment, target, prepared, common_input, policy, output, side=side)
    except (KeyError, TypeError, IndexError, AttributeError):
        raise ValueError('MA7_RANK_INPUT_INVALID') from None


def _bound_inputs(snapshots, policy_path, pack, side):
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
    pol = load_policy(policy_path, pack, raw=snapshots['policy'][0])
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
    return env, pol, scope, checked, record, target_value


def _settings(paths, scope, env, pol):
    return {'input_mode': 'common-records', 'target': str(paths['target']), 'prepared': str(paths['prepared']),
        'common_inputs': {'prepared': str(paths['prepared']), 'session': scope['session'],
            'data_origin': 'EXAMPLE' if env.data_origin.value == 'EXAMPLE' or pol.data_origin.value == 'EXAMPLE' else 'DECLARED',
            'records': {'rank-fault': str(paths['common_input'])}}}


def _prepare(environment, target, prepared, common_input, policy, output, *, side):
    paths = {k: Path(v) for k, v in {'environment': environment, 'target': target, 'prepared': prepared,
        'common_input': common_input, 'policy': policy}.items()}
    snapshots = {k: _read(p) for k, p in paths.items()}
    pack = load_pack(ROOT / 'packs/PACK-BASELINE')
    env, pol, scope, checked, _, _ = _bound_inputs(snapshots, paths['policy'], pack, side)
    values, check = pol.values['LIF-08'], pack.checks['LIF-08']
    output = Path(output).absolute()
    names = _NAMES
    cfg = _settings(paths, scope, env, pol)
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
    result = {'schema': _SIDE_SCHEMA, 'check_id': 'LIF-08', 'side': side,
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


def _reference(reference):
    if (type(reference) is not dict or reference.keys() != {'path', 'sha256'}
            or type(reference['path']) is not str or not reference['path']
            or type(reference['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}', reference['sha256'])):
        raise ValueError('MA7_RANK_PAIRED_INPUT_INVALID')
    path = _plain_path(Path(reference['path']))
    snapshot = _read(path)
    if sha256(snapshot[0]).hexdigest() != reference['sha256']:
        raise ValueError('MA7_RANK_INPUT_FILE_CHANGED')
    return path, snapshot


def _prepared_side(reference, pack, side, verified):
    manifest_path, snapshot = _reference(reference)
    verified[manifest_path] = snapshot
    manifest = json.loads(snapshot[0], object_pairs_hook=_pairs)
    if (manifest_path.name != 'manifest.json' or type(manifest) is not dict
            or manifest['schema'] != _SIDE_SCHEMA or manifest['side'] != side or manifest['check_id'] != 'LIF-08'
            or manifest['pack_ref'] != pack.ref().model_dump(mode='json') or manifest['file_binding_valid'] is not True
            or type(manifest['files']) is not dict or manifest['files'].keys() != {*_NAMES.values(), 'guard.toml'}):
        raise ValueError('MA7_RANK_PAIRED_INPUT_INVALID')
    paths, snapshots = {}, {}
    for key, name in _NAMES.items():
        paths[key], snapshots[key] = _reference({'path': str(manifest_path.parent / name), 'sha256': manifest['files'][name]})
        verified[paths[key]] = snapshots[key]
    env, pol, scope, checked, record, target = _bound_inputs(snapshots, paths['policy'], pack, side)
    cfg = _settings(paths, scope, env, pol)
    if (manifest['scope'] != scope or manifest['policy_status'] != pol.status.value
            or manifest['data_origin'] != cfg['common_inputs']['data_origin'] or manifest['record_validation'] != checked):
        raise ValueError('MA7_RANK_PAIRED_INPUT_INVALID')
    values = pol.values['LIF-08']
    expected_ranks = {int(rank) for rank in values['rank_expected_ranks']}
    if any(binding['rank'] not in expected_ranks for binding in record['bindings']):
        raise ValueError('MA7_RANK_PAIRED_RANK_SCOPE_MISMATCH')
    owners = {(binding['pod_uid'], binding['container_id'], binding['restart_count']) for binding in record['bindings']}
    declared = target.get('kubernetes')
    if declared is not None:
        owner = declared['engine']
        expected = (owner['pod_uid'], owner['container_id'].removeprefix('containerd://')[:128], owner['restart_count'])
        if owners != {expected}:
            raise ValueError('MA7_RANK_PAIRED_OWNER_SCOPE_MISMATCH')
    elif cfg['common_inputs']['data_origin'] != 'EXAMPLE':
        raise ValueError('MA7_RANK_PAIRED_OWNER_SCOPE_MISMATCH')
    check = pack.checks['LIF-08']
    _, effective = run_binding.effective_values(check, cfg, env, values, side=side,
        attempt_id='ma7-rank-paired-file-preflight', issued_at=utcnow(), valid_from=pol.valid_from, valid_until=pol.valid_until)
    registry.build_executor(check.executor, 'common-input', {check.executor: cfg},
                            engine=env.engine.name, version=env.engine.version).validate_policy(check, effective)
    return {'environment': env, 'policy': pol, 'scope': scope, 'owners': owners,
            'snapshots': snapshots, 'source_manifest_sha256': reference['sha256']}


def prepare_pair(selection_path, selection_sha256, output):
    """Compose exact sealed side manifests for the existing offline rank reader."""
    try:
        path, captured = _reference({'path': str(selection_path), 'sha256': selection_sha256})
        selection = json.loads(captured[0], object_pairs_hook=_pairs)
        if (type(selection) is not dict or selection.keys() != {'schema', 'sides'}
                or selection['schema'] != PAIR_SELECTION_SCHEMA or type(selection['sides']) is not dict
                or selection['sides'].keys() != {'baseline', 'candidate'}):
            raise ValueError('MA7_RANK_PAIRED_INPUT_INVALID')
        pack = load_pack(ROOT / 'packs/PACK-BASELINE')
        verified = {path: captured}
        sides = {side: _prepared_side(selection['sides'][side], pack, side, verified)
                 for side in ('baseline', 'candidate')}
        left, right = sides.values()
        if (left['environment'].id == right['environment'].id or left['policy'].ref() != right['policy'].ref()
                or left['snapshots']['policy'][0] != right['snapshots']['policy'][0]
                or left['scope']['session'] == right['scope']['session']
                or any(l_pod == r_pod or l_container == r_container for l_pod, l_container, _ in left['owners']
                       for r_pod, r_container, _ in right['owners'])):
            raise ValueError('MA7_RANK_PAIRED_SIDE_SCOPE_MISMATCH')
        if any(_read(file) != selected for file, selected in verified.items()):
            raise ValueError('MA7_RANK_INPUT_FILE_CHANGED')
    except (AttributeError, IndexError, KeyError, OSError, TypeError):
        raise ValueError('MA7_RANK_PAIRED_INPUT_INVALID') from None

    directory = reserve_output(Path(output), protected=list(verified))
    directory.chmod(0o700)
    config = {'executors': {'rank_failure': 'common-input'},
              'update': {'mode': 'external', 'allowed_checks': ['LIF-08']}, 'sides': {}}
    result = {'schema': PAIR_SCHEMA, 'check_id': 'LIF-08', 'selection_sha256': selection_sha256,
              'policy_ref': left['policy'].ref().model_dump(mode='json'), 'sides': {}, 'files': {},
              'engine_executed': False, 'live_target_verified': False, 'actual_results': None}
    written = []

    def write(name, data, *, pin=True):
        destination = directory / name
        with destination.open('xb') as stream:
            written.append(destination)
            if stream.write(data) != len(data):
                raise OSError('MA7_RANK_PAIRED_SHORT_WRITE')
        if pin:
            result['files'][name] = sha256(data).hexdigest()
        return destination

    try:
        write(_NAMES['policy'], left['snapshots']['policy'][0])
        for side, data in sides.items():
            paths = {key: write(f'{side}-{name}', data['snapshots'][key][0])
                     for key, name in _NAMES.items() if key != 'policy'}
            config['sides'][side] = {'rank_failure': _settings(paths, data['scope'], data['environment'], data['policy'])}
            result['sides'][side] = {'environment': str(paths['environment']),
                                    'environment_ref': data['scope']['environment_ref'],
                                    'source_manifest_sha256': data['source_manifest_sha256']}
        config_path = directory / 'guard.toml'
        written.append(config_path)
        write_config(config, config_path)
        result['files']['guard.toml'] = sha256(config_path.read_bytes()).hexdigest()
        write('manifest.json', canonical_json(result), pin=False)
    except (OSError, TypeError, ValueError):
        for path in reversed(written):
            path.unlink(missing_ok=True)
        directory.rmdir()
        raise
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('environment', 'target', 'prepared', 'common-input', 'policy', 'paired-inputs'):
        parser.add_argument('--' + name, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--side', choices=['baseline', 'candidate'])
    parser.add_argument('--paired-inputs-sha256')
    args = parser.parse_args()
    selected = (args.environment, args.target, args.prepared, args.common_input, args.policy)
    if args.paired_inputs is not None:
        if args.paired_inputs_sha256 is None or any(value is not None for value in (*selected, args.side)):
            parser.error('paired inputs require their SHA and exclude single-side arguments')
        result = prepare_pair(args.paired_inputs, args.paired_inputs_sha256, args.output)
    else:
        if args.paired_inputs_sha256 is not None or any(value is None for value in (*selected, args.side)):
            parser.error('single-side inputs require all five files and --side')
        result = prepare(*selected, args.output, side=args.side)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__': main()
