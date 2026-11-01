"""Preserve full PLT12 criteria in a closed declared-copy observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.credential_copy_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-CREDENTIAL-COPY'
LIMITATIONS=[
 '팩31 PLT12/SC19-03의 위치·수신자별 실제 사본 inventory, 교체·회수·삭제·제한 보존, 실제 재사용 금지와 issuer 결과를 각각 확인하는 원질문/기준을 보존한다. 다른76개 정의 bytes는 바꾸지 않는다.',
 '기존 required_event_record:kubernetes 실행기의 명시 PLT12 profile과 OBS01 RequiredEventReader를 재사용한다. OBS01 /1 schema·역할·요청의 의미는 유지하며 copy schema를 model/approval 필드에 끼워 넣지 않는다.',
 '정확 ENV/target/session/source·inventory 원 bytes와 정책 hash/유효창을 고정하고 허용 사본 UUID·세대·위치/수신자 UUID·처리 기한/방법·보존 예외를 입력한다. 기업 값과 실제 위치/수신자·신뢰 주체를 임의로 채우지 않는다.',
 '읽는 데이터는 선정 소유 session의 비민감 JSON 선언이다. 자격 값/자격 hash·실제 자격 파일/환경·Secret·backup 내용은 수집하지 않는다. credential-family/copy/location/recipient/actor/enforcer ID는 자격 값과 무관한 명시 UUID 라벨이어야 한다.',
 'before/after의 실제 파일 수집·Pod/container/image/restart/start 결합과 선언된 copy inventory/treatment/reuse/issuer 사건을 분리한다. 파일 readable/absent·mount/Secret 등록·issuer deny만으로 실제 사본 멤버십/폐기·재사용 금지를 증명하지 않는다.',
 '사건 ID/seq 중복·상충·다른 copy 세대/위치/수신자·선언 시계/지연·누락/부분 수집·교체 및 후속 수집/첨부/정리 오류는 앞선 유효 자료와 별도로 보존한다.',
 '실제 producer/권한·사본 발견/멤버십·처리·issuer·reuse 집행점은 미연결이다. 선언된 위반 후보를 확인된 실제 FAIL로 올리지 않는다. 정상대조 succeeded=null·모든 판정 축UNKNOWN·전체 분기false를 유지한다.',
 '이 profile은 실제 사본 교체/회수/삭제·자격 변경·issuer 호출·재사용 요청을 수행하지 않는 읽기 전용 입력 소비다. 해당 실제 운영 절차와 허용 주체/범위·책임은 별도로 확정해야 한다.',
 '원 source/image/lab gate와 native0.32 미지원 의미를 보존한다. 새 native profile이나 실제 serving/worker/GPU 증거를 제공하는 것이 아니다.',
 '부모A39/56/4·A1 19/27/2·c9·A2/B 원본·기존팩/Ref/실증을 유지한다. 원GPU창종료/추가Start0·기존46.66USD/보존120GiB EBS 비용·CI 결제 외부차단을 초기화하지 않는다.'
]


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/PLT-12.yaml'].decode('utf-8'))
    check.update(revision=4,executor='required_event_record',collection_profile=PROFILE,
        policy_params=[{'key':key,'type':value,'required':True} for key,value in POLICY_TYPES.items()],
        required_observations=[{'key':key,'type':value} for key,value in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/PLT-12.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
    new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
        files_sha256=[{'path':name,'sha256':sha256_hex(data)} for name,data in sorted(changed.items())])
    result=build_pack(new,changed)
    if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name,data in changed.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
    if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,
            'policy_values_created':False,'full_PLT12_complete':False,'actual_copy_membership_treatment_reuse_verified':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
