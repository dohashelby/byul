"""정본 SUP-08의 두 판본 입력을 묶고 외부 IO 없이 사전 점검한다."""
import argparse
import json
from pathlib import Path
import re
import tomllib

import yaml

from guard.collectors.file import load_environment
from guard.executors import request_field_probe as probe
from guard.models import canonical_json, load_pack, sha256_hex
from guard.runtime.comparison import execution_conditions
from guard.runtime.kubernetes_http import KubernetesHTTP, INPUT_VALIDATION_PROFILE
from tools.gpu_a_run_inputs import http_inputs
from tools.mvp_versions import versions
from tools.prepare_sup08_phi3v_fixture import SOURCE_MODEL, SOURCE_REVISION

ROOT = Path(__file__).resolve().parents[1]
POC = ROOT / 'docs/evidence/sup08-code-selection-20261111.json'


def prepare(fixture_file, environments, targets, output, *, control_image, pack=ROOT / 'packs/PACK-BASELINE'):
    output = Path(output).resolve()
    if output.exists() or "'" in str(output):
        raise ValueError('새 입력 디렉터리를 지정해야 합니다.')
    fixture_raw = Path(fixture_file).read_bytes(); fixture = json.loads(fixture_raw)
    historical = json.loads(POC.read_bytes())['product_update']
    inventory = fixture['model_inventory']
    inventory_hash = sha256_hex(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode())
    code = fixture['code_revisions']
    if (fixture['schema'] != 'byul-sup08-phi3v-fixture/1'
            or fixture['source_model'] != SOURCE_MODEL or fixture['source_revision'] != SOURCE_REVISION
            or inventory_hash != fixture['model_inventory_sha256'] or inventory_hash != historical['model_inventory_sha256']
            or any(not re.fullmatch('[0-9a-f]{40}', code[k]) for k in ('approved', 'unapproved'))
            or code['approved'] == code['unapproved']):
        raise ValueError('PoC와 같은 고정 모델·코드 fixture가 필요합니다.')
    baseline = load_pack(pack); check = baseline.checks['SUP-08']
    if baseline.manifest.revision != 34 or check.revision != 5:
        raise ValueError('정본 팩과 SUP-08 정의가 준비되지 않았습니다.')
    pair = versions(); results = {}; files = {}; conditions = []
    same_pair = all(pair[side]['image'] == historical[side]['image_digest'] for side in pair)
    runtime = []
    for side, expected in pair.items():
        env = load_environment(Path(environments[side])); target = json.loads(Path(targets[side]).read_bytes())
        if ((env.engine.name, env.engine.version, env.engine.image_digest) !=
                (expected['engine'], expected['version'], expected['image'])
                or (env.model.id, env.model.revision, env.model.inventory_sha256) !=
                (fixture['model_id'], fixture['model_snapshot_id'], inventory_hash)):
            raise ValueError('판본 표 또는 고정 모델과 ENV가 다릅니다.')
        comparison = target.get('comparison_config', {})
        if (comparison.get('code_revision') != code['approved'] or comparison.get('trust_remote_code') is not True
                or not target.get('code_marker_root')):
            raise ValueError('승인 코드·trust·import 표식 결합이 다릅니다.')
        if env.runtime.cpu is not None or not env.runtime.gpu_count or not env.runtime.gpu_model or not env.runtime.driver:
            raise ValueError('명시적인 GPU 구성과 드라이버가 필요합니다.')
        runtime.append((env.runtime.gpu_model, env.runtime.driver, env.runtime.gpu_count,
                        env.runtime.tp_size or 1, env.runtime.pp_size or 1, env.runtime.dp_size or 1))
        observation = {'code_module_root': '/cache/hf/modules/transformers_modules',
            'code_module_repository': fixture['code_repository'], 'code_marker_root': target['code_marker_root'],
            'unapproved_revision': code['unapproved'], 'control_image': control_image}
        draft = {'pack': {'id': baseline.ref().id, 'revision': baseline.ref().revision},
            'values': {'SUP-08': {'code_selection_fields': ['mm_processor_kwargs.code_revision'],
                'code_selection_allowed': False, 'rejection_codes': ['400', '422'],
                'approved_code_revisions': [code['approved']]}}, 'code_selection_observation': observation}
        policy, config = http_inputs('SUP-08', target, draft, root=output / side)
        settings = tomllib.loads(config)['input_validation']
        probe.validate_policy(check, policy['values']['SUP-08']); probe.validate_configuration(check, settings)
        def forbidden(*args, **kwargs):
            raise AssertionError('입력 사전 점검에서 외부 IO를 허용하지 않습니다.')
        KubernetesHTTP(target, env, duration=120, runner=forbidden, profile=INPUT_VALIDATION_PROFILE)
        conditions.append(execution_conditions(target, settings, []))
        files[side + '/environment.json'] = canonical_json(env.to_dict())
        files[side + '/target.json'] = canonical_json(target)
        files[side + '/policy.yaml'] = yaml.safe_dump(policy, allow_unicode=True, sort_keys=False).encode()
        files[side + '/guard.toml'] = config.encode()
        results[side] = {'environment_ref': env.ref().model_dump(mode='json'),
            'target_sha256': sha256_hex(canonical_json(target)), 'prepared_required': False,
            'expected_state': historical[side]['challenge_state'] if same_pair else None, 'actual_result': None}
    if runtime[0] != runtime[1] or conditions[0] != conditions[1]:
        raise ValueError('기준·후보의 비교 조건이 다릅니다.')
    manifest = {'schema': 'guard-mvp-sup08-inputs/1', 'data_origin': 'DECLARED', 'pack_ref': baseline.ref().model_dump(mode='json'),
        'fixture_file_sha256': sha256_hex(fixture_raw), 'sides': results,
        'preflight': {'input_contracts_valid': True, 'comparison_conditions_equal': True,
            'cluster_observed': False, 'engine_executed': False, 'model_bytes_rechecked': False},
        'expected_comparison': historical['security_comparison'] if same_pair else None,
        'actual_comparison': None, 'official': False,
        'files': {name: sha256_hex(raw) for name, raw in sorted(files.items())}}
    output.mkdir(parents=True)
    for name, raw in files.items():
        p = output / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
    (output / 'manifest.json').write_bytes(canonical_json(manifest))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, required=True); p.add_argument('--control-image', required=True)
    for side in ('baseline', 'candidate'):
        p.add_argument('--' + side + '-env', type=Path, required=True)
        p.add_argument('--' + side + '-target', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); a = p.parse_args()
    result = prepare(a.fixture, {s: getattr(a, s + '_env') for s in ('baseline', 'candidate')},
                     {s: getattr(a, s + '_target') for s in ('baseline', 'candidate')}, a.output, control_image=a.control_image)
    print(json.dumps(result, ensure_ascii=False))
