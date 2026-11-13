"""SUP-08 실제 로컬 파일을 읽어 확인하고 미입력·미관측 항목을 보고한다. 엔진은 실행하지 않는다."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import tomllib

import yaml

from guard.adapters.engine_provider import VllmProvider
from guard.collectors.file import load_environment
from guard.executors import request_field_probe as probe
from guard.models import load_pack, sha256_hex
from tools import mvp_sup08_inputs as inputs
from tools.mvp_versions import versions
from tools.prepare_sup08_phi3v_fixture import IMAGE_PROCESSOR_SHA256

ROOT = Path(__file__).resolve().parents[1]
LOCAL_FIXTURE = ROOT / 'docs/dev-1/ma8-local-fixture-manifest.json'
SIDES = ('baseline', 'candidate')
LAUNCH_FILES = {f'{side}/{name}' for side in SIDES for name in (
    'engine.yaml', 'probe.yaml', 'render.json', 'policy.yaml', 'guard.pending-target.toml')}
LAUNCH_FILES |= {'control.png', 'control-image.txt'}


def verify_file(path, expected, size=None):
    """큰 가중치는 스트림으로 읽는다. 검증 중 파일 신원·크기가 바뀌면 성공으로 기록하지 않는다."""
    path = Path(path)
    row = {'path': str(path), 'expected_sha256': expected, 'verified': False}
    try:
        if path.is_symlink():
            raise ValueError('SYMLINK')
        with path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            limit = size if size is not None else 16 * 1024**2
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                raise ValueError('FILE_BOUND')
            if size is not None and before.st_size != size:
                raise ValueError('SIZE_MISMATCH')
            digest = hashlib.sha256()
            count = 0
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                count += len(block)
                if count > limit:
                    raise ValueError('FILE_GREW')
                digest.update(block)
            after = os.fstat(stream.fileno())
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        stable = all(getattr(before, key) == getattr(after, key) for key in fields)
        row.update(bytes=count, sha256=digest.hexdigest(), stable=stable)
        row['verified'] = stable and count == before.st_size and digest.hexdigest() == expected
    except (OSError, ValueError) as error:
        row['error'] = str(error) if isinstance(error, ValueError) else type(error).__name__
    return row


def _launch_conditions(folder, manifest, fixture, pack):
    control = (folder / 'control-image.txt').read_text().strip()
    if (not control.startswith('data:image/png;base64,')
            or base64.b64decode(control.partition(',')[2], validate=True) != (folder / 'control.png').read_bytes()):
        raise ValueError('CONTROL_IMAGE_MISMATCH')
    normalized = []
    for side, expected in versions().items():
        base = folder / side
        render = json.loads((base / 'render.json').read_bytes())
        deployment, _ = list(yaml.safe_load_all((base / 'engine.yaml').read_bytes()))
        engine, = deployment['spec']['template']['spec']['containers']
        release = VllmProvider().release_profile(expected['version'])
        if (render['engine_image'] != expected['image'] or render['engine_version'] != expected['version']
                or engine['image'] != expected['image'] or engine['command'] != release['launch']['command']
                or render['model_id'] != fixture['model_id'] or render['model_revision'] != fixture['model_snapshot_id']
                or render['code_revision'] != fixture['code_revisions']['approved']
                or engine['args'] != render['launch']['argv']):
            raise ValueError('LAUNCH_IDENTITY_MISMATCH:' + side)
        args = list(engine['args'])
        if release['launch']['model_argument'] == '--model' and args.pop(0) != '--model':
            raise ValueError('LAUNCH_MODEL_ARGUMENT_MISMATCH:' + side)
        if (args[0] != '/models' or '--trust-remote-code' not in args
                or args[args.index('--code-revision') + 1] != fixture['code_revisions']['approved']
                or args[args.index('--tensor-parallel-size') + 1] != '1'
                or str(engine['resources']['requests']['nvidia.com/gpu']) != '1'
                or str(engine['resources']['limits']['nvidia.com/gpu']) != '1'):
            raise ValueError('LAUNCH_GPU_OR_CODE_MISMATCH:' + side)
        env = {row['name']: row['value'] for row in engine['env']}
        marker = env.pop('BYUL_CODE_MARKER_ROOT')
        settings = tomllib.loads((base / 'guard.pending-target.toml').read_text())['input_validation']
        policy = yaml.safe_load((base / 'policy.yaml').read_bytes())
        if (marker != render['code_marker_root'] or marker != manifest['sides'][side]['code_marker_root']
                or marker != settings['code_marker_root'] or env['HF_HUB_OFFLINE'] != '1'
                or env['TRANSFORMERS_OFFLINE'] != '1' or policy['scope']['include'] != ['SUP-08']
                or settings['control_image'] != control
                or settings['code_module_repository'] != fixture['code_repository']
                or settings['unapproved_revision'] != fixture['code_revisions']['unapproved']
                or policy['values']['SUP-08']['approved_code_revisions'] != [fixture['code_revisions']['approved']]
                or policy.get('confirmed_by') is not None):
            raise ValueError('LAUNCH_POLICY_OR_CACHE_MISMATCH:' + side)
        probe.validate_policy(pack.checks['SUP-08'], policy['values']['SUP-08'])
        probe.validate_configuration(pack.checks['SUP-08'], settings)
        normalized.append({'args': args, 'env': env, 'resources': engine['resources']})
    if normalized[0] != normalized[1]:
        raise ValueError('LAUNCH_COMPARISON_CONDITIONS_MISMATCH')


def audit(fixture_file, launch_manifest, *, environments=None, targets=None, pack=ROOT / 'packs/PACK-BASELINE'):
    raw, fixture, _, _ = inputs.validate_fixture(fixture_file)
    trusted = json.loads(LOCAL_FIXTURE.read_bytes())
    if fixture['code_processor_sha256'] != trusted['code_processor_sha256']:
        raise ValueError('LOCAL_CODE_IDENTITY_MISMATCH')
    model = Path(fixture['model_path'])
    inventory = fixture['model_inventory']
    if any(Path(name).name != name or name in {'.', '..'} for name in inventory):
        raise ValueError('MODEL_INVENTORY_PATH_INVALID')
    model_rows = [verify_file(model / name, row['sha256'], row['bytes']) for name, row in inventory.items()]
    actual_names = {path.name for path in model.iterdir() if path.is_file()} if model.is_dir() else set()
    cache = Path(fixture['hf_home']) / 'hub' / ('models--' + fixture['code_repository'].replace('/', '--'))
    code_rows = []
    for label, revision in fixture['code_revisions'].items():
        for name, expected in [('processing_phi3_v.py', trusted['code_processor_sha256'][label]),
                               ('image_processing_phi3_v.py', IMAGE_PROCESSOR_SHA256)]:
            code_rows.append(verify_file(cache / 'snapshots' / revision / name, expected))
    code_rows.append(verify_file(cache / 'refs/main', sha256_hex(fixture['code_revisions']['approved'].encode())))
    manifest_path = Path(launch_manifest)
    launch = json.loads(manifest_path.read_bytes())
    baseline = load_pack(pack)
    if (launch['schema'] != 'guard-sup08-local-launch-inputs/1' or set(launch['files_sha256']) != LAUNCH_FILES
            or launch['fixture_manifest_sha256'] != sha256_hex(raw)
            or launch['pack_ref'] != baseline.ref().model_dump(mode='json')):
        raise ValueError('LOCAL_LAUNCH_MANIFEST_MISMATCH')
    launch_rows = [verify_file(manifest_path.parent / name, digest)
                   for name, digest in launch['files_sha256'].items()]
    errors = []
    model_valid = bool(model_rows) and all(row['verified'] for row in model_rows) and actual_names == set(inventory)
    code_valid = all(row['verified'] for row in code_rows)
    launch_valid = all(row['verified'] for row in launch_rows)
    if launch_valid:
        try:
            _launch_conditions(manifest_path.parent, launch, fixture, baseline)
        except (KeyError, ValueError, TypeError, IndexError) as error:
            launch_valid = False
            errors.append(str(error))
    local_valid = model_valid and code_valid and launch_valid
    paths = {'environments': environments or {}, 'targets': targets or {}}
    pending = [f'{side}:{kind}' for kind, rows in paths.items() for side in SIDES
               if rows.get(side) is None or not Path(rows[side]).is_file()]
    contracts_valid = None
    if local_valid and not pending:
        try:
            # 임시 복사본만 만들고 바로 정리한다. 최종 실행 입력 폴더는 만들지 않는다.
            with tempfile.TemporaryDirectory(prefix='guard-sup08-contracts-') as temp:
                inputs.prepare(fixture_file, paths['environments'], paths['targets'], Path(temp) / 'inputs',
                    control_image=(manifest_path.parent / 'control-image.txt').read_text().strip(), pack=pack)
            contracts_valid = True
            for side in SIDES:
                if load_environment(Path(paths['environments'][side])).data_origin.value == 'EXAMPLE':
                    pending.append(side + ':EXAMPLE_ENV_NOT_ACTUAL')
        except (ValueError, OSError, KeyError, TypeError) as error:
            contracts_valid = False
            errors.append(str(error))
    return {
        'schema': 'guard-mvp-sup08-local-audit/1',
        'scope': '로컬 파일 bytes·입력 계약 확인. 클러스터·엔진·정상 요청을 관측하지 않음.',
        'fixture_manifest_sha256': sha256_hex(raw), 'pack_ref': baseline.ref().model_dump(mode='json'),
        'local_assets_valid': local_valid, 'model_bytes_rechecked': model_valid,
        'model_files': model_rows, 'code_cache_files': code_rows, 'launch_files': launch_rows,
        'code_cache_valid': code_valid, 'launch_plan_valid': launch_valid,
        'final_input_contracts_valid': contracts_valid, 'missing_inputs': pending, 'errors': errors,
        'ready_for_final_assembly': local_valid and contracts_valid is True and not pending,
        'input_preflight_passed': False, 'cluster_observed': False, 'engine_executed': False,
        'node_file_permissions_verified': False, 'normal_request_verified': False,
        'actual_comparison': None, 'official': False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--launch-manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    for side in SIDES:
        parser.add_argument('--' + side + '-env', type=Path)
        parser.add_argument('--' + side + '-target', type=Path)
    args = parser.parse_args(argv)
    if args.output.exists():
        raise ValueError('기존 보고서는 덮어쓰지 않습니다.')
    result = audit(args.fixture, args.launch_manifest,
        environments={s: getattr(args, s + '_env') for s in SIDES}, targets={s: getattr(args, s + '_target') for s in SIDES})
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({key: result[key] for key in ('local_assets_valid', 'model_bytes_rechecked',
        'final_input_contracts_valid', 'missing_inputs', 'ready_for_final_assembly', 'input_preflight_passed')}, ensure_ascii=False))
    return 0 if result['ready_for_final_assembly'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
