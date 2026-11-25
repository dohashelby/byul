"""엔진 센서 점검(REQ-14·15·16·18·19·21)의 두 판본 `guard update` 입력을 쪽마다 결합한다.

`mvp_a_input_materialize`가 만든 결합 전 자산 다음 단계다. 쪽마다 아래 세 가지를 받는다.

- ENV: `gpu_a_run_inputs env`
- target: `gpu_a_run_inputs target` 또는 J02 `--discover`
- 그 쪽 엔진에 실제로 설치한 콜백 준비 자산 하나: `guard prepare-engine-callbacks`

한 쪽의 엔진 Pod에는 콜백 세션이 하나만 들어가므로, 이 도구가 고른 점검들은 그 쪽의 준비 자산 하나를 함께 쓴다.
두 쪽에는 같은 사례 bytes를 쓴다. 사례 manifest는 쪽마다 그 쪽의 세션·target·ENV에 묶는다.
결과물은 두 가지다.

- J02 선언 조각: `executors`와 `sides.<side>.checks.<점검>.executor`. `tools/j02_run_config.py`의 spec에 합친다.
- 점검·쪽별 정적 보고서

kubectl·엔진·Docker를 실행하지 않는다. 빠진 입력은 채우지 않고 `MISSING_INPUT`과 고정 코드로 남긴다.
`READY_STATIC`은 실행기가 읽을 파일의 형식과 실행 결합(`run_binding.effective_values`, `guard update`와 같은 호출)이
맞는다는 뜻이다. 실행·노드 설치·관측 지점 자격·보안 PASS를 뜻하지 않는다.

REQ-15는 공유 집단 주체를 Gateway가 정하므로 Gateway target·주체 토큰·주체 사례가 필요하다.
이 도구는 Gateway 입력을 만들지 않고 `REQ15_GATEWAY_INPUTS_REQUIRED`로 남긴다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path

import yaml

from guard.adapters import engine_profiles
from guard.collectors import engine_callbacks
from guard.collectors.file import load_environment
from guard.executors import engine_callback_projection as projection
from guard.executors import run_binding
from guard.models import canonical_json, load_pack, sha256_hex, utcnow
from guard.runtime.bounded import strict_json
from tools import gpu_a_run_inputs

SCHEMA = 'byul-engine-sensor-pair-inputs/1'
ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / 'packs/PACK-BASELINE'
SIDES = ('baseline', 'candidate')
CHECKS = ('REQ-14', 'REQ-15', 'REQ-16', 'REQ-18', 'REQ-19', 'REQ-21')
# 기존 사례 생성기(gpu_a_run_inputs.cases)를 그대로 쓴다. REQ-16은 lab 문맥이 없는 salt 묶음이다.
PRESETS = {'REQ-14': 'req14-normal', 'REQ-16': 'req16-salt', 'REQ-18': 'req18-slot'}
# 엔진 상태를 바꾸거나(REQ-21 재기동) GPU 동기화 대조(REQ-19)를 하는 점검은 소유 lab 대상에서만 돈다.
LAB_CHECKS = frozenset({'REQ-19', 'REQ-21'})
KERNEL_CHECKS = frozenset({'REQ-19'})
# 실행 안에서 엔진 컨테이너를 다시 띄운다. 실행기·입력 도구의 900초 상한과 같다.
LONG_CHECKS = frozenset({'REQ-21'})
TARGET_LIMIT = 262144


def case_list(check_id):
    """두 쪽이 같이 쓰는 사례. 실행기는 앞에 32자 nonce와 공백을 붙인다."""
    if check_id in PRESETS:
        return gpu_a_run_inputs.cases(PRESETS[check_id])
    if check_id == 'REQ-19':
        # project_kernel_bounds는 KERNEL_BLOCK(1024 token) 이하와 초과 길이의 prefill을 모두 봐야 범위가 찬다.
        # ' a' 1500번은 BPE 토크나이저에서 대략 token 1500개라 경계를 넘는다(3014자, 4096자 상한 안).
        # 실제 길이는 수집된 query_len으로 판단하며, 넘지 못하면 실행기가 block_boundary_not_crossed로 남긴다.
        return [{'case_id': 'short', 'role': 'boundary', 'prompt': 'boundary short ' + gpu_a_run_inputs.TEXT, 'max_tokens': 2},
                {'case_id': 'long', 'role': 'boundary', 'prompt': 'boundary long' + ' a' * 1500, 'max_tokens': 2}]
    if check_id == 'REQ-21':
        return [{'case_id': 'before', 'role': 'before', 'prompt': 'before ' + gpu_a_run_inputs.TEXT, 'max_tokens': 4},
                {'case_id': 'restart', 'role': 'switch', 'action': 'engine_restart', 'variant': 'once'},
                {'case_id': 'after', 'role': 'after', 'prompt': 'after ' + gpu_a_run_inputs.TEXT, 'max_tokens': 4}]
    raise ValueError('ENGINE_SENSOR_PAIR_CASES_UNAVAILABLE')


def lab_contexts(check_id):
    return deepcopy({'engine_restart': projection.ENGINE_RESTART_SPEC}) if check_id == 'REQ-21' else None


def _read_target(path):
    with Path(path).open('rb') as stream:
        return strict_json(stream.read(TARGET_LIMIT + 1), limit=TARGET_LIMIT)


def _load_side(env_path, target_path, prepared_path):
    """쪽 공통 입력. 읽지 못한 항목은 코드로 남기고 값을 만들지 않는다."""
    codes, env, target, prepared = [], None, None, None
    try:
        env = load_environment(Path(env_path))
        if not env.hash_ok():
            codes.append('ENV_HASH_INVALID'); env = None
    except (OSError, ValueError, TypeError):
        codes.append('ENV_UNAVAILABLE')
    try:
        target = _read_target(target_path)
        if type(target) is not dict:
            raise ValueError
    except (OSError, ValueError, TypeError):
        codes.append('TARGET_UNAVAILABLE'); target = None
    try:
        prepared = engine_callbacks.load_prepared(prepared_path)
    except (OSError, ValueError, TypeError, KeyError):
        codes.append('PREPARED_INVALID')
    if env is not None:
        if not engine_profiles.supports(env.engine.name, env.engine.version, 'native_lifecycle'):
            codes.append('ENGINE_SENSOR_PROFILE_UNREVIEWED')
        if target is not None and target.get('environment_ref') != env.ref().model_dump(mode='json'):
            codes.append('TARGET_ENVIRONMENT_MISMATCH')
    return env, target, prepared, codes


def side_inputs(side, env_path, target_path, prepared_path, out_dir, *, checks=CHECKS, policy_values=None, pack=None):
    """한 쪽의 점검별 manifest와 실행기 설정. 파일은 새로 만든 폴더에만 쓴다."""
    pack = pack or load_pack(PACK)
    policy_values = policy_values or {}
    env, target, prepared, common = _load_side(env_path, target_path, prepared_path)
    prepared_manifest = Path(prepared_path)
    prepared_manifest = (prepared_manifest if prepared_manifest.is_dir() else prepared_manifest.parent).absolute() / 'manifest.json'
    rows = {}
    for check_id in checks:
        codes = list(common)
        if check_id == 'REQ-15':
            codes.append('REQ15_GATEWAY_INPUTS_REQUIRED')
        if check_id in LAB_CHECKS and target is not None and target.get('lab') is not True:
            codes.append('LAB_TARGET_REQUIRED')
        if check_id in KERNEL_CHECKS and prepared is not None and prepared.get('lab_kernel_checks') is not True:
            codes.append('LAB_KERNEL_CHECKS_REQUIRED')
        row = {'check_id': check_id, 'side': side, 'status': 'MISSING_INPUT', 'codes': codes, 'executor': None,
               'manifest_sha256': None, 'run_binding_computed': False}
        rows[check_id] = row
        if codes:
            continue
        cases = case_list(check_id)
        projection.validate_cases(check_id, cases)
        lab = lab_contexts(check_id)
        if lab is not None:
            projection.validate_lab_contexts(lab, cases)
        manifest = {'schema': projection.SCHEMA, 'session': prepared['session'],
                    'target_sha256': sha256_hex(canonical_json(target)),
                    'environment_ref': env.ref().model_dump(mode='json'), 'cases': cases,
                    **({'lab_contexts': lab} if lab is not None else {})}
        path = Path(out_dir).absolute() / side / check_id / 'manifest.json'
        path.parent.mkdir(parents=True, exist_ok=False)
        with path.open('xb') as stream:
            stream.write(canonical_json(manifest))
        executor = {'target': str(Path(target_path).absolute()), 'prepared': str(prepared_manifest),
                    'manifest': str(path), 'max_duration_seconds': 900 if check_id in LONG_CHECKS else 300}
        row.update(executor=executor, manifest_sha256=sha256_hex(canonical_json(manifest)),
                   cases_sha256=sha256_hex(canonical_json(cases)))
        # guard update가 실행 직전에 하는 같은 결합 계산. 정책 값은 기업 입력이라 받은 것만 쓴다.
        values = {k: v for k, v in (policy_values.get(check_id) or {}).items()}
        try:
            run_binding.effective_values(pack.checks[check_id], executor, env, values,
                                         side=side, attempt_id=None, issued_at=utcnow())
        except ValueError as error:
            row['codes'] = [str(error) or 'RUN_BINDING_UNAVAILABLE']
            continue
        row['run_binding_computed'] = True
        missing = [p.key for p in pack.checks[check_id].policy_params if p.required and p.key not in values]
        if missing:
            row['codes'] = ['POLICY_VALUES_MISSING']; row['missing_policy_keys'] = missing
            continue
        row['status'] = 'READY_STATIC'
    return rows, env, prepared


def pair(sides, out_dir, *, checks=CHECKS, policy_file=None):
    """sides: {side: {'env', 'target', 'prepared'}}. 두 쪽 모두 READY_STATIC인 점검만 J02 조각에 넣는다."""
    out_dir = Path(out_dir).absolute()
    if out_dir.exists():
        raise ValueError('ENGINE_SENSOR_PAIR_OUTPUT_EXISTS')
    if set(sides) != set(SIDES) or any(set(v) != {'env', 'target', 'prepared'} for v in sides.values()):
        raise ValueError('ENGINE_SENSOR_PAIR_SIDES_INVALID')
    if not checks or set(checks) - set(CHECKS) or len(set(checks)) != len(checks):
        raise ValueError('ENGINE_SENSOR_PAIR_CHECKS_INVALID')
    policy_values = {}
    if policy_file is not None:
        document = yaml.safe_load(Path(policy_file).read_text(encoding='utf-8'))
        if type(document) is not dict or type(document.get('values', {})) is not dict:
            raise ValueError('ENGINE_SENSOR_PAIR_POLICY_INVALID')
        policy_values = document.get('values') or {}
    pack = load_pack(PACK)
    out_dir.mkdir(parents=True)
    rows, envs, sessions = {}, {}, {}
    for side in SIDES:
        rows[side], envs[side], prepared = side_inputs(side, sides[side]['env'], sides[side]['target'], sides[side]['prepared'],
            out_dir, checks=checks, policy_values=policy_values, pack=pack)
        sessions[side] = prepared['session'] if prepared else None
    shared_session = sessions['baseline'] is not None and sessions['baseline'] == sessions['candidate']
    if shared_session:
        # 두 판본이 같은 세션을 쓰면 한쪽 기록이 다른 쪽 시험 근거로 섞일 수 있다.
        for side in SIDES:
            for row in rows[side].values():
                row['status'] = 'MISSING_INPUT'; row['codes'] = sorted({*row['codes'], 'PREPARED_SESSION_SHARED_ACROSS_SIDES'})
    ready = [c for c in checks if all(rows[s][c]['status'] == 'READY_STATIC' for s in SIDES)]
    spec = {'executors': {'engine_sensor': 'kubernetes'},
            'sides': {side: {'checks': {c: {'executor': deepcopy(rows[side][c]['executor'])} for c in ready}} for side in SIDES}}
    spec_path = out_dir / 'j02-engine-sensor.yaml'
    spec_path.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding='utf-8')
    same_inventory = (None if None in envs.values()
                      else envs['baseline'].model.inventory_sha256 == envs['candidate'].model.inventory_sha256)
    report = {'schema': SCHEMA, 'pack_ref': pack.ref().model_dump(mode='json'), 'checks': list(checks),
              'ready_static_both_sides': ready, 'rows': {s: rows[s] for s in SIDES},
              'sessions': sessions, 'session_shared_across_sides': shared_session,
              'model_inventory_equal': same_inventory, 'j02_spec_fragment': str(spec_path),
              'j02_spec_fragment_sha256': sha256_hex(spec_path.read_bytes()),
              'generator_source_sha256': sha256_hex(Path(__file__).read_bytes()),
              'engine_restarting_checks': sorted(LONG_CHECKS & set(ready)),
              'limitations': ['READY_STATIC는 입력 형식·실행 결합 계산만 확인한다. 노드 설치·관측 지점 자격·실행·판정이 아니다.',
                              'REQ-21은 실행 중에 엔진 컨테이너를 재기동한다. 같은 쪽의 다른 엔진 센서 점검과 실행 순서를 운영자가 확인한다.',
                              'REQ-19는 준비 자산의 lab_kernel_checks(GPU 동기화)를 켠다. 같은 세션의 다른 점검 지연 시간이 달라질 수 있다.'],
              'engine_executed': False, 'cluster_observed': False, 'ready_for_execution': False}
    with (out_dir / 'report.json').open('xb') as stream:
        stream.write(canonical_json(report))
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for side in SIDES:
        for name in ('env', 'target', 'prepared'):
            parser.add_argument(f'--{side}-{name}', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='새 출력 폴더')
    parser.add_argument('--checks', default=','.join(CHECKS), help='쉼표로 구분. 기본은 6개 전부')
    parser.add_argument('--policy', type=Path, help='REQ-15·16 정책 값을 담은 guard 정책 입력(읽기만 한다)')
    args = parser.parse_args(argv)
    sides = {side: {name: getattr(args, f'{side}_{name}') for name in ('env', 'target', 'prepared')} for side in SIDES}
    report = pair(sides, args.out, checks=tuple(c for c in args.checks.split(',') if c), policy_file=args.policy)
    print(json.dumps({'ready_static_both_sides': report['ready_static_both_sides'],
                      'report': str(Path(args.out).absolute() / 'report.json'),
                      'ready_for_execution': False}, ensure_ascii=False))


if __name__ == '__main__':
    main()
