"""Preserve LIF12 criteria while connecting closed read-only declarations."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml

from guard.models import load_pack, sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.retry_record_contract import PROFILE, POLICY_TYPES, OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID = 'PACK-KUBERNETES-AUTO-RETRY'
LIMITATIONS = [
    '원 LIF12/SC18-03의 실제 자동 retry 주체·발동/허용 정책·원/새 attempt·중복/잔존/반환/누적 비용 요구와 정상/수동대조/도전 기준을 보존한다. 다른76정의 bytes는 바꾸지 않는다.',
    '기존 request_termination:kubernetes의 명시 LIF12 profile과 선정 소유 OBS01 session 파일 읽기를 재사용한다. 기존 LIF10 종료·REQ12 수동 재호출 절차와 자료 출처·독립 FAIL 의미를 유지한다.',
    '정확 ENV/target/run/session/source/code/image/model revision/inventory/선정 PID-start·worker 세대 및 비민감 scope/policy 원 bytes를 고정한다. UUID 라벨·해시·읽힌 파일은 실제 producer/정책/worker 인증이 아니다.',
    '정상·수동대조·자동도전 가족과 원/새 attempt/parent/trigger/decision을 명시 ID로 구분한다. 수동 재호출·이벤트 부재·동시각·PID·경로만으로 자동 retry 또는 비사용을 증명하지 않는다.',
    '시간 계산은 동일 선정 subject/PID-start/worker/domain의 선언 내부 정합성에 한정한다. 다른 프로세스/세대의 시간값이나 자기 clock hash로 실제 인과·시계 동등성을 만들지 않는다. 실제 관측 시간의 주장과 구분한다.',
    '비용은 단위·발생원·주체·epoch를 나누고 서로 더하지 않는다. 누락/상충·counter reset/교체·다른 귀속·부분/후속 수집·첨부/정리 오류와 앞선 유효 기록을 각각 보존한다.',
    '실제 retry 생산자/trigger/비발생/원 작업 종결/반환/비용 집행은 미연결이다. 선언된 한도 초과 후보를 실제 FAIL로 올리지 않고 정상 attempted=false/succeeded=null·모든 축UNKNOWN·전체분기false를 유지한다.',
    '새 retry·HTTP 재호출·실패 주입·Pod 변경·광범위 process/resource 조사·GPU/daemon/모델 실행을 하지 않는다. 기업 허용/금지·상한·복구 책임은 임의로 채우지 않는다.',
    '원 source/image/lab gate와 native0.32 미지원을 유지하며 새 native profile을 제공하지 않는다. 부모A39/56/4·A1 19/27/2·B/A2 원본·과거팩/Ref/실증을 보존한다.',
    '원GPU창 종료/추가Start0·기존46.66USD 상한/과거사용량/120GiB EBS 비용과 CI 외부 차단·의존성 질문 대기를 유지한다.',
]


def derive(seed, output):
    seed, output = Path(seed).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):
        raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw = {p.relative_to(seed).as_posix(): p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest = yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'))
    original = build_pack(manifest, raw)
    if original.ref().model_dump(mode='json') != SEED_REF or len(original.checks) != 77:
        raise ValueError('REVIEWED_PACK31_REQUIRED')
    check = yaml.safe_load(raw['checks/LIF-12.yaml'].decode('utf-8'))
    check.update(revision=4, executor='request_termination', collection_profile=PROFILE,
        policy_params=[{'key': key, 'type': value, 'required': True} for key, value in POLICY_TYPES.items()],
        required_observations=[{'key': key, 'type': value} for key, value in OBSERVATIONS.items()],
        judge={'plugin': 'rule_table', 'params': {'pass_when_all': [], 'fail_when_any': []}},
        limitations=deepcopy(LIMITATIONS))
    changed = dict(raw)
    changed['checks/LIF-12.yaml'] = yaml.safe_dump(check, allow_unicode=True, sort_keys=False).encode('utf-8')
    new = deepcopy(manifest)
    new.update(pack_id=PACK_ID, revision=1, review={'status': 'DRAFT', 'reviewer': None, 'reviewed_at': None},
        files_sha256=[{'path': name, 'sha256': sha256_hex(data)} for name, data in sorted(changed.items())])
    result = build_pack(new, changed)
    if list(result.checks) != list(original.checks):
        raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name, data in changed.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (output / 'pack.yaml').write_bytes(yaml.safe_dump(new, allow_unicode=True, sort_keys=False).encode('utf-8'))
    if load_pack(output).ref() != result.ref():
        raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref': SEED_REF, 'pack_ref': result.ref().model_dump(mode='json'), 'checks': 77,
        'unchanged_definition_bytes': 76, 'policy_values_created': False, 'full_LIF12_complete': False,
        'actual_retry_producer_trigger_work_cost_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(derive(args.seed, args.output), ensure_ascii=False))


if __name__ == '__main__':
    main()
