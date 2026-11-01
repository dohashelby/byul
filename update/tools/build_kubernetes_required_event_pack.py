"""PACK31의 원래 OBS01 두분기·다른76개를 보존하는 제품 수집 DRAFT 팩."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.observation_contract import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-REQUIRED-EVENT-RECORD'
LIMITATIONS=[
 'OBS01 SC23-03/SC23-02의 필수 접근·관리·승인·소비·배포 사건과 기한·귀속 요구를 유지한다. 이번 제품 연결은 닫힌 접근 허용/거부/후속 정상 probe와 별도 소유 기록의 정제 수집·대조이며 전체 분기 완료가 아니다.',
 '정확 ENV/target SHA·session·사전 검토 producer source SHA 주장·기업 event policy/두 actor SHA·aware 최대600초 창·수집 상한·필수 사건/필드를 명시한다. 실행시 읽은 source/hash나 key fingerprint를 승인값으로 채택하지 않는다.',
 '정식 OBS01 HTTP profile은 전후 같은 allowed credential과 별도 denied credential, 같은 모델의 고정 chat 1token hello 요청만 보낸다. 정상 응답 구조와401/403은 probe 응답 관측이며 실제 principal/worker 실행·독립 audit 생성원의 증명이 아니다.',
 '고정 경로 /guard-observation/data/<session>/required-events.json의 nofollow/file identity·전후 namespace/Pod/container/image/restart/start·출력/시간 상한을 기존 reader로 확인한다. 외부 capture JSON 제품 실행은 없다.',
 '필수 actor/target/run/policy·추가 필드와 request/result/source SHA·도착 상한·누락/중복/교체/후속 수집·첨부/정리 오류를 분리한다. 뒤 수집 실패가 앞 정제 기록을 삭제하지 않는다. 처음 수집 실패 후 다시 확보한 경우도 이전 reader gap을 보존한다.',
 'expected producer source hash 일치는 미인증 기록의 주장 대조다. producer_authenticated/independent_trigger_verified/scope_complete와 확인된 위반은 모두false이며 모든 판정 축UNKNOWN이다. 선언된 불일치/중복은 후보이고 독립FAIL을 대신하지 않는다.',
 '관리·승인·공급물 소비·배포의 독립 발생·trusted principal/기록 생산자·보존/기한 조건은 별도 실제 입력이다. 선택된 항목이 미준비이면 missing에 남긴다. OBS02 상태 표현·OBS11 경보도 완료로 간주하지 않는다.',
 'native source/image/lab gate와0.32 미지원을 유지한다. 이번 record profile은 lab=true/vllm0.31에 한정되고 actual serving/worker/GPU 성과가 아니다. 원래GPU창 종료·추가Start0·CI결제차단을 보존한다.',
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/OBS-01.yaml'].decode('utf-8'))
 check.update(revision=4,executor='required_event_record',collection_profile=PROFILE,
  policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
  required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
  judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/OBS-01.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
  files_sha256=[{'path':n,'sha256':sha256_hex(v)} for n,v in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
 output.mkdir(parents=True)
 for name,raw_bytes in changed.items():
  path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw_bytes)
 (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
 if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
 return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,
  'policy_values_created':False,'full_OBS01_complete':False,'required_subresults_complete':False}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--seed',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
 args=parser.parse_args();print(json.dumps(derive(args.seed,args.output),ensure_ascii=False))
if __name__=='__main__':main()
