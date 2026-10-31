"""Preserve full PACK31 LIF11 startup criteria in a DRAFT observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.resource_recovery_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-MP-RESOURCE-RECOVERY'
LIMITATIONS=[
 '원팩31 LIF11/SC18-02 실행 중 실패·SC17-01 초기화 부분 실패 두 분기와 정상 종료/회수/같은 환경의 후속 기동·정상 요청 및 원FAIL/UNKNOWN을 유지하는 DRAFT다. 다른76개 정의 bytes를 보존한다.',
 'lab=true/vLLM0.31/mp에서 정확 ENV/target/prepared/선정 자원 manifest/POL·시나리오·필수rank·기한·관측 단계·정확 모델 inventory/revision/별칭을 결합한다. 실행 중 hash/PID/inode를 승인 입력으로 자동 채우지 않는다.',
 'runtime-failure는 기존 native13, partial-initialization은 native14를 명시 소비한다. source/image/lab·소유 running container와 PID/start 관문 및 native0.32 미지원을 유지하고 기존 native8~14 bytes를 변경하지 않는다.',
 '공개 resource_census는 명시 선정64개 이하 process/PID/start/uid/namespace/boot·128개 이하 FD/device/inode만 고정 read-only Linux helper로 확인한다. 전체 process/FD scan·신호·poll/wait/join·파일 본문/환경/argv·IPC 메시지/주소 저장을 하지 않는다.',
 '각 선정 process/descriptor의 match·absent·replaced·unstable·unavailable을 구분한다. fd 부재는 global IPC/shared memory/GPU/공유 참조 반환이 아니며 새 PID·FD 재사용을 원소유자에 합치지 않는다. 컨테이너 종료로 exec 불가 또는 권한/안정성/clock 누락이면 UNKNOWN이다.',
 'POL의 absolute-unconditional이 해당 선정 주체의 무조건 절대 회수 기한을 명시한 경우만, 전후 소유/세대/시계/자원 결합을 확인하고 기한 뒤 동일 process 또는 FD/inode 잔존을 독립 FAIL 관측으로 제공한다. after-verified-failure는 독립 실패 trigger/시계가 없어 UNKNOWN이며 phase/scenario 이름을 활성 근거로 쓰지 않는다.',
 '선정 descriptor나 PID 부재 관측은 기록하지만 전체 PASS는 주지 않는다. 전체 자원/공유 참조·실제 실패·기한/회수 인과·전후 기동 lineage·정상 추론은 별도다. 모든 normal succeeded=null·전체 범위false이고 근거 없는 축은 UNKNOWN이다. 확인한 절대 기한 위반은 후속 seal/capture/첨부/부모 정리 실패가 지우지 않는다.',
 '두 고정 before/after POST/v1/completions hello1token/temperature0/streamfalse만 같은 UUID로 보낸다. 새 프로필의 running engine Ready=false 관측과 probe Ready=true를 분리하며 전후 namespace/Pod/container/image/restart/start·시간/출력 한도를 유지한다. HTTP200은 회수나 후속 기동 증명이 아니다.',
 '초기화 unready worker 정리와 일반 ready worker shutdown을 혼동하지 않는다. shutdown·terminate/kill 호출이나 반환만으로 실제 종료/IPC/GPU 반환을 완료 처리하지 않는다. 센서가 원정리 순서/신호·기동을 바꾸지 않는다.',
 '제어 API/장애 유발·재기동·공유 worker 변경은 수행하지 않는다. 별도 Python helper는 kernel metadata 수집 주체이며 serving native hook이 아니다. 기존 native session만 seal/capture하고 원기록 producer 인증을 승격하지 않는다.',
 'CPU 소유 child/pipe/socket/shm/FD 시험·주입 응답·설치본 CLI/정확Ref는 EXAMPLE이다. 실제 TP/worker/GPU·부모A39/56/4·A1 19/27/2 완료 수로 올리지 않는다. 기존실증/배포wheel04f7856·A2/B 원본/c9 pin·과거팩/Ref를 보존한다.',
 '원GPU창종료·추가Start0·마지막확인stopped·기존46.66USD·보존120GiB EBS 과금·CI외부차단을 유지하며 새 기동/시간/비용/권한 승인이 아니다. 실제 재개는 준비된 소비자·정확 ENV/POL·소유권/활성작업/quota/잔여총비용·새시간 승인·복구/정리 책임을 요구한다.',
]


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/LIF-11.yaml'].decode('utf-8'))
    check.update(revision=5,executor='resource_recovery',collection_profile=PROFILE,
        policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
        required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[], 'axes':{'functional':{'pass_when_all':[],'fail_when_any':[{'obs':'recovery_absolute_deadline_violated','op':'eq','value':True}]}}}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/LIF-11.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
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
        'unchanged_definition_bytes':76,'policy_values_created':False,'full_LIF11_complete':False,'recovery_two_branches_complete':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
