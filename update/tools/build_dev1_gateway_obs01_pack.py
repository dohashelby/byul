"""PACK32에서 OBS-01만 BYUL Gateway 경로 수집 프로필로 바꾼 파생 DRAFT 팩(개발 담당, 1단계).

1단계 동안 PACK-BASELINE은 바꾸지 않는다(GPU 담당 측정 기준 유지). 판정 규칙·관측 이름·다른 76개 정의는 그대로다.
바뀌는 것은 OBS-01의 수집 프로필, 정책 키 하나(`observation_gateway_target_id`), revision, 한계 문장뿐이다.
1단계 뒤 정본 팩 개정 여부는 현황 정리에서 정한다.

  uv run python -m tools.build_dev1_gateway_obs01_pack --output <새 폴더>
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json

import yaml

from guard.models import load_pack, sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_gateway_observation import PROFILE

PACK_ID = 'PACK-DEV1-GATEWAY-OBS01'
SEED_REF = {'kind': 'PACK', 'id': 'PACK-BASELINE', 'revision': 32,
            'sha256': '1c4c723001eb3fcccb43e79c63e944663601e4d8b2939a84bff59730a8a3b8bd'}
LIMITATIONS = [
    'BYUL 구성: 감사 기록 생산자는 runtime Gateway다. 시도는 Gateway로 보내고, Gateway가 응답 X-Request-ID로 돌려준 요청 ID로 Gateway 기록과 대조한다.',
    'Gateway 기록은 끝 표식이 없는 추가 전용 파일이라 source_ended=false로 남고 실행은 ABORTED다. 생산자 봉인·인증은 runtime 합의 항목이다.',
    '대상 지문은 Gateway target_id 기준(observation_gateway_target_id)이다. 승인·출처 값은 Gateway가 남기지 않아 None이다.',
]


def derive(output, seed=None):
    seed = Path(seed or Path(__file__).parents[1] / 'packs/PACK-BASELINE').resolve()
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):
        raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw = {p.relative_to(seed).as_posix(): p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest = yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'))
    original = build_pack(manifest, raw)
    if original.ref().model_dump(mode='json') != SEED_REF or len(original.checks) != 77:
        raise ValueError('REVIEWED_PACK32_REQUIRED')
    check = yaml.safe_load(raw['checks/OBS-01.yaml'].decode('utf-8'))
    check.update(revision=check['revision'] + 1, collection_profile=PROFILE,
                 policy_params=deepcopy(check['policy_params']) + [{'key': 'observation_gateway_target_id', 'type': 'str', 'required': True}],
                 limitations=deepcopy(check.get('limitations') or []) + LIMITATIONS)
    changed = dict(raw)
    changed['checks/OBS-01.yaml'] = yaml.safe_dump(check, allow_unicode=True, sort_keys=False).encode('utf-8')
    new = deepcopy(manifest)
    new.update(pack_id=PACK_ID, revision=1, review={'status': 'DRAFT', 'reviewer': None, 'reviewed_at': None},
               files_sha256=[{'path': n, 'sha256': sha256_hex(v)} for n, v in sorted(changed.items())])
    result = build_pack(new, changed)
    if list(result.checks) != list(original.checks):
        raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name, data in changed.items():
        path = output / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
    (output / 'pack.yaml').write_bytes(yaml.safe_dump(new, allow_unicode=True, sort_keys=False).encode('utf-8'))
    if load_pack(output).ref() != result.ref():
        raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref': SEED_REF, 'pack_ref': result.ref().model_dump(mode='json'), 'checks': 77,
            'unchanged_definition_bytes': 76, 'policy_values_created': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True); parser.add_argument('--seed', type=Path)
    args = parser.parse_args()
    print(json.dumps(derive(args.output, args.seed), ensure_ascii=False))


if __name__ == '__main__':
    main()
