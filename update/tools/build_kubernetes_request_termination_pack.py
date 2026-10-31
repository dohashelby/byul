"""검토된 PACK31에서 LIF10의 연결 종료/native 관측 DRAFT 팩을 만든다."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_request_termination import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID="PACK-KUBERNETES-REQUEST-TERMINATION"
LIMITATIONS=['LIF10의 일반/stream·대기/계산/출력·취소/연결 종료/timeout/stop과 Core/worker/전송/상태/참조/자원 기한을 유지한다. 이 판본은 선정된 한 SSE client의 연결 종료 절차만 제품으로 연결하며 전체 분기 완료가 아니다.', '필수6 POL로 exact ENV/target SHA, aware 유효창, 정수 cleanup deadline과 명시 scope 목록을 받는다. []는 명시적인 요구 scope 없음이며 회수 PASS가 아니다. 기업 기준을 관측값으로 채우지 않는다.', '고정 POST /v1/completions hello JSON before/after와8token SSE의 첫1byte 후 소유 client handle close를 사용한다. 별도 cancel/admin route·worker signal·Pod/EC2 조작을 하지 않는다. before 정상 구조가 없으면 disconnect를 보내지 않는다.', 'client_closed와 불완전 SSE body는 실제 서버 취소/작업 종료/자원 회수와 다르다. reader 세대·원문 비저장·시간/출력 한도와 native8/9 prepared session·기존 lab/source/image gate를 재사용한다. native0.32는 미지원이다.', 'live selection과 같은 recording의 ASGI disconnect/native ID를 결합하고 /proc context로 확인한 같은 native clock domain에서만 경과 후보를 계산한다. probe의 상대 clock과 serving clock을 직접 빼지 않는다.', 'source-correlated active state/empty mapping은 독립 worker/GPU/transfer 완료의 증명이 아니다. 현재 image_check=False·producer 미인증·whole-run coverage 미확인을 유지하며 deadline conflict는 후보이고 confirmed_violation=False다. 판정 축은 모두 UNKNOWN이다.', 'Core/worker/전송/참조 범위를 따로 표시하며 공유 weights, pool refcount와 요청별 정리는 같지 않다. REQ12의 같은 외부 ID 새 시도·늦은 출력·실제 retry, LIF11 장애 그룹 정리, profiler/decoder/loader 서비스 시험은 별도다.', 'followup/세대/seal/census/첨부/parent cleanup 오류는 이전 관측을 지우지 않는다. 기존 CLI·EVD/RES/RPT·정확Ref 반출/복원·재판정 완료를 실제 serving/GPU 또는 부모A39 완료로 올리지 않는다.']

def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError("NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED")
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob("*") if p.is_file()}
 manifest=yaml.safe_load(raw.pop("pack.yaml").decode("utf-8"));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode="json")!=SEED_REF or len(original.checks)!=77:raise ValueError("REVIEWED_PACK31_REQUIRED")
 check=yaml.safe_load(raw["checks/LIF-10.yaml"].decode("utf-8"))
 check.update(revision=4,executor="request_termination",collection_profile=PROFILE,
  policy_params=[{"key":k,"type":v,"required":True} for k,v in POLICY_TYPES.items()],
  required_observations=[{"key":k,"type":v} for k,v in OBSERVATIONS.items()],
  judge={"plugin":"rule_table","params":{"pass_when_all":[],"fail_when_any":[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed["checks/LIF-10.yaml"]=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode("utf-8")
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={"status":"DRAFT","reviewer":None,"reviewed_at":None},
  files_sha256=[{"path":name,"sha256":sha256_hex(data)} for name,data in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError("PACK_CHECK_SCOPE_CHANGED")
 output.mkdir(parents=True)
 for name,data in changed.items():
  path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 (output/"pack.yaml").write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode("utf-8"))
 if load_pack(output).ref()!=result.ref():raise ValueError("GENERATED_PACK_INTEGRITY_FAILED")
 return {"seed_ref":SEED_REF,"pack_ref":result.ref().model_dump(mode="json"),"checks":77,"unchanged_definition_bytes":76,
  "policy_values_created":False,"full_LIF10_complete":False,"required_subresults_complete":False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--seed",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
 args=p.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=="__main__":main()
