"""검토된 PACK31에서 REQ12의 외부ID 재요청/native 관측 DRAFT 팩을 만든다."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_request_attempts import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID="PACK-KUBERNETES-REQUEST-ATTEMPTS"
LIMITATIONS=['REQ12 SC18-01/03의 취소/연결 종료·같은 외부ID 새시도·늦은 결과/자동 retry 요구를 유지한다. 이 판본은 명시 재요청 절차와 native ID 관계만 연결하고 전체분기 완료가 아니다.', '필수4 POL로 정확 ENV/target SHA·최대600초 aware 유효창을 받는다. 기업의 취소확정·늦은 결과/소비자/필드·retry 기준은 생성하지 않으며 그 입력과 신뢰/관측 미준비를 UNKNOWN으로 남긴다.', '정상 JSON2회는 같은 normal 외부ID, SSE 종료A/JSON 대체B는 같은 shared 외부ID, 마지막 JSON은 별도 after ID다. 각 session_id는 보조 구별 표식이고 native attempt ID·신원·취소권한을 대신하지 않는다.', '기존 request_termination:kubernetes 실행기에서 명시 REQ12 profile을 dispatch하며 기존 native8/9·전후 세대·seal/capture·lab/source/image gate를 유지한다. 실제 lab=false와 native0.32는 지원하지 않는다.', '선정 ASGI/request/session 표식·native assignment/selection·PID/start·유일 invocation을 결합한다. 중복·누락은 유일결합 미확인이며 내부ID/invocation 충돌은 declared 후보로만 남긴다.', '현재 ASGI BODY_SEND_RETURN/DISCONNECT에는 native attempt/invocation이 없다. 같은 외부ID나 기록순서로 소비자를 확정하지 않고 attempt_case_id=null로 보존한다. 실제 늦은 chunk 폐기/전달·자동SDK/Gateway retry는 미확인이다.', '현재 producer 미인증/image_check=False와 scope/late_delivery/automatic_retry verified=false 및 전축UNKNOWN을 유지한다. HTTP 정상구조·clientclose·source 상관으로 MEASURED/PASS/FAIL을 만들지 않는다.', '후속 오류·seal/census/첨부/parent cleanup은 이전 정제관측을 지우지 않는다. LIF10/11 실제자원회수·새 serving/worker/GPU·실제정책/소유권은 별도다. 원래 승인창 종료·추가Start0·CI결제 차단을 유지한다.']

LIMITATIONS.append('명시 native10은 별도 ATTEMPT event의 ASGI invocation·observer code·같은 PID/start 결합을 추가한다. 옛 ASGI 행의 attempt_case_id=null은 유지하며 ASGI send 반환을 실제client 수신/native출력원천·늦은chunk 폐기/전달로 올리지 않는다. native8/9 기본·기존제한과 B/A2전용10 미지원은 보존한다.')

LIMITATIONS.append('명시 native11은 실제 add_request/RequestState의 native ID와 약한 참조 collector/output nonce, put의 입력/저장 객체 및 get의 반환 객체·호출 invocation을 구분해 연결한다. 합치기/객체 수정·누락·중복·프로세스 세대가 다르면 근거 부족을 남긴다. collector 반환은 client 전달·worker/GPU·늦은 결과 정책위반의 독립 증명이 아니다.')

def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError("NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED")
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob("*") if p.is_file()}
 manifest=yaml.safe_load(raw.pop("pack.yaml").decode("utf-8"));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode="json")!=SEED_REF or len(original.checks)!=77:raise ValueError("REVIEWED_PACK31_REQUIRED")
 check=yaml.safe_load(raw["checks/REQ-12.yaml"].decode("utf-8"))
 check.update(revision=7,executor="request_termination",collection_profile=PROFILE,
  policy_params=[{"key":k,"type":v,"required":True} for k,v in POLICY_TYPES.items()],
  required_observations=[{"key":k,"type":v} for k,v in OBSERVATIONS.items()],
  judge={"plugin":"rule_table","params":{"pass_when_all":[],"fail_when_any":[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed["checks/REQ-12.yaml"]=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode("utf-8")
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=3,review={"status":"DRAFT","reviewer":None,"reviewed_at":None},
  files_sha256=[{"path":name,"sha256":sha256_hex(data)} for name,data in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError("PACK_CHECK_SCOPE_CHANGED")
 output.mkdir(parents=True)
 for name,data in changed.items():
  path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 (output/"pack.yaml").write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode("utf-8"))
 if load_pack(output).ref()!=result.ref():raise ValueError("GENERATED_PACK_INTEGRITY_FAILED")
 return {"seed_ref":SEED_REF,"pack_ref":result.ref().model_dump(mode="json"),"checks":77,"unchanged_definition_bytes":76,
  "policy_values_created":False,"full_REQ12_complete":False,"required_subresults_complete":False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--seed",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
 args=p.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=="__main__":main()
