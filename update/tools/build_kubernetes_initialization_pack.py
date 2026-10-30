"""Preserve full PACK31 LIF09 startup criteria in a DRAFT observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.initialization_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-MP-INITIALIZATION'
LIMITATIONS=[
 '원팩31 LIF09/SC17-01 원질문·정상·부분 실패·수정 후 정상 기동과 FAIL/UNKNOWN 기준 및 다른76개 bytes를 유지하는 DRAFT다.',
 'native14 또는 명시 native20/21/vLLM0.31/mp/lab=true의 한 선언 단계 관측을 지원한다. native20은 원 Worker19·MP14를 조합하고 Worker 사실을 별도 관측으로 소비하며 cross-process 결합을 인증하지 않는다. normal·partial-failure·recovered-normal 이름/시나리오 hash는 실제 대응이나 장애 활성 증명이 아니다.',
 '정확 ENV/target/prepared·전체 모델 inventory/revision·API 별칭·필수 rank·준비 단계worker-ready/model-load/admission·실패 예정 rank/단계·준비/복귀 기한과 aware600초 이하 창을 명시한다. 기업 정책을 채우거나 읽은 hash를 자동 승인하지 않는다.',
 'MultiprocExecutor._init_executor와 WorkerProc 두 static 준비 메서드의 원반환/예외/인자·descriptor·정리를 보존한다. 실제 소스/code·session/PID/start/call/부모/객체·필수 rank 입력/반환과 상위 반환/예외의 같은 프로세스 구간을 대조한다.',
 'WorkerProc의 READY 응답·필수 rank 목록 반환·상위 기동 반환은 전 worker의 모델 적재/통신/실제 serving 완료가 아니다. 기존native8의 모델 관측을 보존하지만 cross-process root failure/전체 model readiness를 자동 결합하지 않는다.',
 '선정 running engine은 이 새 HTTP 공개 프로필에서만 readiness=false 상태를 관측할 수 있다. 소유 probe는 Ready=true를 별도로 요구하며 UID/container/image/restart/start와 전후 결합은 그대로다. 기존 HTTP 및 lifecycle 프로필의 의미를 변경하지 않는다.',
 '같은 UUID의 before/after 고정 POST/v1/completions hello1token/temperature0/streamfalse만 보낸다. 정제 outcome·정상 shape·전후 readiness만 남기며 원문 오류/응답/예외·주소·자격은 저장하지 않는다. HTTP200/timeout/Ready=false로 실제 접수 gate·inflight·실패 원인을 판정하지 않는다.',
 '소유 안전 파일의 종료 기록 주장은 유지하지만 PID/start 현재 결합 실패를 숨기거나 process_context_verified/producer/image/whole_run을 올리지 않는다. 컨테이너 종료/교체로 exec가 불가능하면 누락이며 다른 Pod 로그/과거 UID/미래 세대를 대신 사용하지 않는다.',
 '같은 프로세스의 wait_ready RAISE와 상위 RAISE·반환의 대응 후보는 정제한다. 인증되지 않은 원기록·별도 프로세스/모델 실패·독립 실제 서비스 gate·정상/실패/수정 후 기동의 물리 lineage가 없으므로 전체 판정UNKNOWN·normal_control.succeeded=null·full scopefalse다.',
 '한 구성요소의 실제 안전 실패 활성·복구/재기동·공유 worker 변경·파일 변조·fault API는 수행하지 않는다. LIF08 네 실행 중 fault와 LIF11 부분 회수/재기동, B LIF07 readiness 판정은 별도다.',
 '선정 session만 seal/capture하며 후속 누락·수집/첨부/부모 정리 실패는 기존 안전 관측/독립 FAIL을 지우지 않는다. CPU 소유 임시 파일/child·정확 메서드 fixture를 실제 serving/TP/GPU 완료로 올리지 않는다.',
 '부모A39/56/4·A1 19/27/2·c9·A2/B 원본·과거팩/Ref/실증을 유지한다. 원GPU창종료·추가Start0·마지막확인stopped·기존비용/보존EBS과금·CI결제 차단을 유지하며 새 시간/비용/기동 승인이 아니다.',
]


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/LIF-09.yaml'].decode('utf-8'))
    check.update(revision=7,executor='initialization',collection_profile=PROFILE,
        policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
        required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/LIF-09.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
    new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=3,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
        files_sha256=[{'path':name,'sha256':sha256_hex(data)} for name,data in sorted(changed.items())])
    result=build_pack(new,changed)
    if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
    output.mkdir(parents=True)
    for name,data in changed.items():
        path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
    if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
    return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,
        'unchanged_definition_bytes':76,'policy_values_created':False,'full_LIF09_complete':False,'three_startup_phases_completed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
