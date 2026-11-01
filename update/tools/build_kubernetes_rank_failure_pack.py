"""Preserve full PACK31 LIF08/four fault criteria in a DRAFT observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.rank_failure_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-MP-RANK-FAILURE'
LIMITATIONS=[
 'LIF08/SC18-02의 rank 예외·사망·지연·통신 정지 네 조건과 원질문/정상/FAIL/UNKNOWN 기준을 유지한다. HTTP timeout·한 예외·EngineDead 표식으로 네 fault를 대신하지 않는다.',
 '현재는 lab=true/vLLM0.31의 선정 mp 실제 메서드 관측을 기존 CLI에서 소비하는 DRAFT다. native13을 명시 선택하며 기본8/9~12 bytes와 source/lab/image 관문·native0.32 미지원을 유지한다.',
 '정확 ENV/target/prepared·모델/revision/API 별칭·mp/rank 집합/output rank·aware600초 이하 창·네 종류 각각 감지/전달 예산을 명시한다. 관측 hash·rank·기한을 승인값으로 자동 사용하지 않는다.',
 '같은 UUID의 normal-before·네 observe 단계·normal-after는 고정 hello1token/temperature0/streamfalse POST/v1/completions만 수행한다. observe 이름은 관측 구간 선언이며 장애를 활성화하거나 실제 발생을 증명하지 않는다.',
 'worker 실제 RPC 호출과 예외 객체/응답, executor collective RPC/Future 반환·예외, source 확인된 worker monitor의 shutdown 진입, core EXECUTOR_FAILED 처리/ENGINE_CORE_DEAD 송신 호출을 구분한다. source/code·call/부모·객체·PID/start/session을 기록하며 원문 예외/traceback/RPC/출력·callback을 저장하거나 교체하지 않는다.',
 '선정 output rank와 필수 rank 계산/통신 완료는 다르다. 최종 결과가 한 rank/집계 경로로 반환될 수 있다. Future 반환·API200·worker enqueue 표식을 모든 rank 완료나 client 수신으로 올리지 않는다.',
 '서로 다른 프로세스의 rank/group/요청 연결·실제 fault 활성 생산자/발생 시계·감지·상위 접수·영향 요청 결과는 독립 근거가 필요하다. 같은 native ID·선언 종류·근접한 시간만으로 결합하지 않는다.',
 'timeout은 관측한 호출의 timeout일 뿐 delay/통신 정지의 식별이 아니다. 실패 표식·예외 후보는 confirmed_violation=false이며 네 fault 활성/기한 검증과 전체 범위는 false다. 모든 판정 UNKNOWN·normal_control.succeeded=null이다. 구성 관문 거부는 기존 미수집 null을 유지한다.',
 'fault 제어 API/파일/payload, 공유 worker 종료·callback 대체·RPC 변조·임의 profiler 제어는 구현하지 않는다. 비동기 worker 출력의 context가 끊기거나 세대/소스/호출·부모 구간이 유일하지 않으면 누락이며 안전 PASS로 채우지 않는다.',
 '선택 native session만 seal/capture한다. 누락/교체/restart·후속 수집/첨부·부모 정리 실패는 앞선 정제 증거와 독립 FAIL을 지우지 않는다. 초기화 부분 실패는 LIF09, 실제 회수/다음 기동은 LIF11, 상태 표현 대조는 OBS02의 별도 범위다.',
 '실제 준비에는 선정 executor/TP·필수 rank의 정상 계산/통신과 네 안전 fault 각각의 발생/감지/전달·시계·기한·복구 책임이 필요하다. CPU 메서드/소유 child 시험을 실제 serving/TP/GPU 실증으로 올리지 않는다.',
 '부모 A39/56/4·A1 19/27/2와 공동c9·A2/B 원본·과거 팩/Ref·실제wheel 근거를 유지한다. 원GPU창종료/추가Start0/마지막확인stopped·기존비용/보존EBS과금·CI외부차단은 유지하며 이 묶음은 새 기동/시간/비용 승인이 아니다.',
]


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/LIF-08.yaml'].decode('utf-8'))
    check.update(revision=5,executor='rank_failure',collection_profile=PROFILE,
        policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
        required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/LIF-08.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
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
        'unchanged_definition_bytes':76,'policy_values_created':False,'full_LIF08_complete':False,'four_faults_completed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
