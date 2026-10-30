"""Preserve full PACK31 PLT10 issuer/new-access criteria in a DRAFT observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.credential_revocation_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-CREDENTIAL-REVOCATION'
LIMITATIONS=[
 '원팩31 PLT10/SC19-01 원질문·철회 전 정상·실제 철회/소수 새 접근/전파 기한·FAIL/UNKNOWN과 다른76개 정의 bytes를 보존하는 DRAFT다. NetworkPolicy 회수는 credential issuer 철회가 아니다.',
 'lab=true/vLLM0.31의 기존 native8 인증/GETmodels handler 관측을 그대로 소비한다. 신규 native profile이나 issuer 제어 API를 만들지 않고 기존8~14 bytes/source/image/lab/PID 관문·native0.32 미지원을 유지한다.',
 '정확 ENV/target/prepared·모델 inventory/revision/API 별칭·선정 issuer/event 선언·기한 근거/전파 예산/clock오차·유효창을 명시한다. 실제 issuer 주체·정책 캐시·원본 사건/시계·principal은 미확인이고 선언을 실측으로 올리지 않는다.',
 '두 자격은 retired/control 환경변수에서 메모리로만 읽으며 서로 달라야 한다. 실제 자격·자격 hash·Authorization 헤더·원문 응답/오류/예외를 증거에 저장하지 않고 secret hash를 ID로 바꾸지 않는다. issuer/event는 실제 키 값이 아닌 명시 라벨이다.',
 '같은 UUID의 control-before/retired-first/retired-second/retired-third/control-after 5회만 고정 GET/v1/models를 수행한다. source는 요청마다 연결을 만들고 닫지만 독립 connection receipt·기존session/handle과 실제 재연결은 미확인이다. 일반 protected endpoint/Gateway/TLS로 확대하지 않는다.',
 'HTTP모델목록 shape와 실제 recording/PID/start/session의 유일 ASGI invocation·auth token bool·등록 handler/반환을 대조한다. 같은 requestID의 중복·다른 기록/세대·source/context 누락이면 확인되지 않는다. auth bool 자체를 trusted principal/권한/issuer 철회 증명으로 올리지 않는다.',
 '명시 absolute-unconditional deny-after 정책만, 유효창·오차를 지난 retired 자격의 정상 응답과 source/process 결합·active normal control을 확인하면 functional 독립 FAIL로 남긴다. 보안 우회/issuer 전파를 확정하지 않는다. after-verified-issuer-event는 사건 producer가 없어 UNKNOWN이다.',
 '401/403·native 거부·HTTP200만으로 PLT10 전체 PASS를 주지 않는다. 철회 전 정상·실제 issuer/cache/event/time·새 연결 receipt/전파·principal 미확인으로 security와 전체 scope는UNKNOWN/false, normal succeeded=null이다. 열린 session과 사본 처리는 PLT11/12 별도다.',
 '소유 engine/probe namespace/Pod/container/image/restart/start·ENV/run·모델 별칭·시간/출력 상한을 전후 확인한다. 정상 Ready gate를 해제하지 않고 대상 교체/restart/누락·후속 seal/capture/첨부/부모 정리 오류가 앞선 안전 관측과 독립FAIL을 지우지 않게 한다.',
 '실제 issuer 변경·키 교체/삭제·Secret 조회·공유 Pod 재기동·세션 종료를 수행하지 않는다. 외부 정책/receipt가 미확정인데 임의 로그/파일/callback을 현재 지원 계약으로 채우지 않는다.',
 'CPU의 exact auth/모델목록 함수와 동일 공개 전송/CLI/Ref fixture는 EXAMPLE이며 실제 issuer/서비스/GPU 실증이 아니다. 실제 재개에는 issuer/정책/시험 자격/철회 정상·사건/시계·새 접근/복구/정리 책임과 정확 ENV/POL이 필요하다.',
 '부모 A39/56/4·A1 19/27/2·c9·A2/B 원본·과거팩/Ref/실증을 보존한다. 원GPU창종료/추가Start0/마지막확인stopped·기존46.66USD·보존120GiB EBS 과금·CI결제 외부차단을 유지하며 새 시간/비용/권한 승인이 아니다.',
]


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/PLT-10.yaml'].decode('utf-8'))
    check.update(revision=5,executor='credential_revocation',collection_profile=PROFILE,
        policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
        required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[], 'axes':{'functional':{'pass_when_all':[],'fail_when_any':[{'obs':'revocation_absolute_denial_constraint_violated','op':'eq','value':True}]}}}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/PLT-10.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
    new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
        files_sha256=[{'path':name,'sha256':sha256_hex(data)} for name,data in sorted(changed.items())])
    result=build_pack(new,changed)
    if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name,data in changed.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
    if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,
        'unchanged_definition_bytes':76,'policy_values_created':False,'full_PLT10_complete':False,'issuer_revocation_complete':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
