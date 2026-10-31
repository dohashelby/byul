"""Preserve full PACK31 PLT11 issuer/new-access criteria in a DRAFT observation pack."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.session_revocation_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-SESSION-REVOCATION'
LIMITATIONS=['원팩31 PLT11/SC19-02의 기존 연결/session/handle 생성·철회·동일/신규 접근·잔존 정책/기한·원질문·FAIL/UNKNOWN과 다른76개 정의 bytes를 보존하는 DRAFT다.', '첫 범위는 lab=true/vLLM0.31 고정 GET /v1/models의 probe HTTP1.1 keep-alive다. 명시 connect 뒤 auto_open=0으로 재연결을 금지하고 같은 socket 객체/device/inode와 별도 새 연결을 구분한다. 애플리케이션session/handle/SSE/Gateway/TLS/worker 권한/serving receipt가 아니다.', '기존 native8 유일 ASGI invocation/auth bool/등록 handler/반환/PID-start-session-source를 그대로 소비하며 native8~14·resource probe·기존 stream framing bytes/source/image/lab/세대 gate와 native0.32 미지원을 유지한다.', 'POL15 exactENV/target/prepared·issuer/event 라벨/시각 선언·aware 유효창/생성 기한/재사용 금지 기한/근거/전파예산/clock오차/최대10초 준비대기·명시 deny-existing-after-deadline만 지원한다. 실제 잔존 정책을 기본값으로 만들지 않는다.', '메모리의 서로 다른 두 환경변수 자격으로 control-before/session-open/session-reuse/new-session/control-after 고정5GET을 수행한다. 키 원문/hash·Authorization·오류/응답 원문을 저장하지 않고 issuer/자격/Secret/공유Pod/정책 cache를 변경하지 않는다.', '기한 전 선택 연결의 source-bound 정상과 같은 probe socket의 기한 뒤 정상 auth/등록 handler 반환을 확인한 명시 unconditional 제약만 functional 독립FAIL이다. 실제 issuer 사건 기반 정책은 미확인으로 UNKNOWN이다. 보안 우회/전파/전체PLT11 PASS나 완료가 아니다.', '소유ENV/model mapping·engine/probe namespace/Pod/container/image/restart/start와 Ready를 전후 확인한다. 소유session만seal/capture하고 후속오류가 앞선 안전 관측과 독립FAIL을 지우지 않는다. 정확Ref·자료출처·UNKNOWN을 유지한다.', 'CPU/EXAMPLE와 실제 소유 로컬 TCP 시험은 실제 issuer/serving/Kubernetes/GPU 인수가 아니다. 실제 issuer/cache/event/source·철회 전 정상·잔존 정책·session/handle 종류·시계/접근/복구/정리 책임과 정확ENV/POL을 별도 확정한다.', '부모A39/56/4·A1 19/27/2·c9·A2/B 원본·과거팩/Ref를 유지한다. 원GPU창종료/Start0/마지막확인stopped·46.66USD/보존120GiB EBS 과금·CI결제차단을 유지하며 새 기동/기간/비용/권한 승인이 아니다.']


def derive(seed,output):
    seed=Path(seed).resolve();output=Path(output).resolve()
    if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
    raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
    manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
    if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
    check=yaml.safe_load(raw['checks/PLT-11.yaml'].decode('utf-8'))
    check.update(revision=3,executor='session_revocation',collection_profile=PROFILE,
        policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
        required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
        judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[], 'axes':{'functional':{'pass_when_all':[],'fail_when_any':[{'obs':'session_existing_connection_constraint_violated','op':'eq','value':True}]}}}},limitations=deepcopy(LIMITATIONS))
    changed=dict(raw);changed['checks/PLT-11.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
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
        'unchanged_definition_bytes':76,'policy_values_created':False,'full_PLT11_complete':False,'issuer_revocation_complete':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
