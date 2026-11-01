"""검토된 PACK31에서 REQ05의 명시 probe/native 관측 DRAFT 팩을 만든다."""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_model_authorization import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID="PACK-KUBERNETES-MODEL-AUTHORIZATION"
LIMITATIONS=[
 "REQ05의 세 분기와 모델별 권한·퇴역 모델 실행 중단 두 필수 결과를 유지한다. 이 profile은 한 선정 base-model API alias의 명시 allowed/denied probe 자격과 현재 native 관측을 정식 제품 경로로 결합하는 단계다.",
 "정확 ENV/target SHA, 서로 다른 두 probe 주체 해시, 명시 principal:model:revision grant 목록, 명시 model:revision:퇴역시각 목록과 유효창을 필수 POL로 받는다. [] 퇴역 목록은 그 시험 미선정이며 퇴역 보안 PASS가 아니다.",
 "고정 /v1/chat/completions hello/max_tokens=1 JSON 요청의 before/challenge/after만 실행한다. 정상 구조 before가 없으면 challenge를 보내지 않고 after만 대조한다. 실제 승인 자격만 환경변수로 제공하고 raw token/credential/header/body/response는 저장하지 않는다.",
 "기존 prepared native session·lab=true/source/image gate와 native0.32 미지원을 그대로 사용한다. 현재 Kubernetes image_check=False이며 진단 관측을 source-qualified 최종 모델 소비로 승격하지 않는다.",
 "native assign-request/forward의 opaque object hash 및 loader configured model/revision은 실제 파일/tensor 정체·인가된 주체·최종 모델 사용 증명이 아니다. mount/API 이름/metadata claim을 승인값으로 사용하지 않는다. 발견한 configured-policy conflict와 configured-retired-use는 후보이며 확인된 FAIL이 아니다.",
 "최종 모델·주체·파일 lineage가 미확인이므로 이 판본의 판정 축은 모두 UNKNOWN이다. 실제 근거가 없는 PASS/FAIL을 만들지 않는다. alias/LoRA/대체 endpoint·퇴역 전후 매핑 변경/다른 활성 모델 정상대조·기존 요청 종료/회수는 별도 미완료다.",
 "seal 실패 뒤에도 기존 capture_session을 수집하고 후속 API/첨부/정리 실패가 앞서 관측한 hash/enum metadata를 지우지 않게 한다. CLI/증거/정확 Ref 왕복 완료는 전체 REQ05 완료가 아니다."
]
def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError("NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED")
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob("*") if p.is_file()}
 manifest=yaml.safe_load(raw.pop("pack.yaml").decode("utf-8"));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode="json")!=SEED_REF or len(original.checks)!=77:raise ValueError("REVIEWED_PACK31_REQUIRED")
 check=yaml.safe_load(raw["checks/REQ-05.yaml"].decode("utf-8"))
 check.update(revision=5,executor="model_authorization",collection_profile=PROFILE,
  policy_params=[{"key":k,"type":v,"required":True} for k,v in POLICY_TYPES.items()],
  required_observations=[{"key":k,"type":v} for k,v in OBSERVATIONS.items()],
  judge={"plugin":"rule_table","params":{"pass_when_all":[],"fail_when_any":[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed["checks/REQ-05.yaml"]=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode("utf-8")
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
  "policy_values_created":False,"full_REQ05_complete":False,"required_subresults_complete":False}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--seed",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
 args=p.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=="__main__":main()
