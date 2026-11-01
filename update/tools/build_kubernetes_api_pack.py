"""검토된 PACK31에서 PLT08 한 구간의 versioned 제품 팩을 생성한다.

기업 POL이나 관측값은 만들지 않는다. 기존 guard CLI가 읽는 같은 Pack이다.
"""
from pathlib import Path
import argparse
from copy import deepcopy
import json
import yaml

from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.runtime.kubernetes_api_hop import PROFILE,POLICY_TYPES

SEED_REF={'kind':'PACK','id':'PACK-BASELINE','revision':31,
    'sha256':'54f4f2357533a8b201ad4397d4b182b08b89ee4821ffe67763e3fbecd8f369ac'}
PACK_ID='PACK-KUBERNETES-API-HOP'
LIMITATIONS=[
    'explicit-owned-kubernetes-api-plaintext-v1은 고정 POST /v1/completions의 명시 guard_probe_to_owned_kubernetes_direct_api 한 구간만 관측한다. 전체 PLT08의 API 및 내부 IPC/TP 필수 결과는 그대로 남는다.',
    '정확 ENV Ref 문서 해시·전체 target·context/run/engine+probe 전후 세대 binding 해시·IP·포트·명시 평문 금지 여부·유효창9개는 필수 POL이다. 관측한 값을 승인하거나 Docker ID로 대체하지 않는다.',
    '고정 source의 logical/wire 요청과 선택 probe/engine 컨테이너 세대는 실제 배포 caller 또는 source-bound model worker와 구분한다. timeout/refusal은 요청 전달 증명이 아니다.',
    'HTTP-only 고정 수집 source·정책 결합·전후 양쪽 세대·완전한 구조 정상 응답·정책 유효창이 맞는 명시 금지 평문 성공만 기존 독립 FAIL이다. 후속 정리/첨부 실패는 알려진 사실을 지우지 않는다.',
    '허용 평문·거부·누락·교체·불완전·만료는 전체 PASS를 만들지 않는다. TLS/CA/암호학적 peer·Service/Gateway·worker·IPC/TP·무결성 변조 및 전체 배포 경로는 미검증이다.',
    '수집 출처의 EXAMPLE/DECLARED/MEASURED 상한과 기존 INV11/rule_table을 유지한다. 이 팩은 DRAFT이며 기업 보호 요구/예외를 자동 승인하거나 과거 PACK31/Ref를 변경하지 않는다.'
]

def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    # Pin a verified in-memory snapshot, so copying cannot accept a different
    # file read after validation. The allowed seed hash is a reviewed constant.
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'))
    original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:
        raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/PLT-08.yaml'].decode('utf-8'))
    check.update(revision=6,collection_profile=PROFILE,
        policy_params=[{'key':key,'type':kind,'required':True} for key,kind in POLICY_TYPES.items()],
        limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/PLT-08.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
    new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
        files_sha256=[{'path':name,'sha256':sha256_hex(data)} for name,data in sorted(changed.items())])
    result=build_pack(new,changed)
    if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name,data in changed.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
    if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),
        'checks':len(result.checks),'unchanged_definition_bytes':76,'only_changed_definition':'PLT-08',
        'policy_values_created':False,'review_status':'DRAFT'}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
