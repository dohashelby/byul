"""PACK31 OBS02의 세 분기를 보존하는 상태 관측 제품 DRAFT 팩."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_observation_state import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-OBSERVATION-STATE'
LIMITATIONS=[
 'OBS02 SC23-04/05/03을 유지한다. 이번은 SC23-04의 health/정상 요청/client 종료/native 사실 대조 제품 연결이며 전체3분기 완료가 아니다.',
 '정확 ENV/target SHA와 aware 최대600초 실행창, 필요한 관측 component를 명시한다. 기업 상태 의미·완료/회수 기한·독립 worker/감사 생산자의 신뢰는 별도 입력이며 관측값으로 승인 기준을 생성하지 않는다.',
 '명시 lab=true/vLLM0.31의 기존 REQUEST binding을 재사용하는 별도 OBS02 profile이다. GET /health 전후·hello1token 정상 JSON 전후·8token SSE 첫1byte 소유client종료만허용한다. 새 Pod/worker signal/장애/자동retry/profiler 조작은 없다.',
 '기존 native8/9/10/11 prepared/source/image/session/seal/capture 경계를 재사용한다. Native0.32와 lab=false를 지원하지 않는다. 외부capture JSON을 제품 입력으로 받지 않는다.',
 'HTTP200은 listener 응답 관측이다. 정상 응답 구조와 client close도 실제 worker 준비/종결·GPU 회수가 아니다. 정상대조 succeeded=null을 유지한다.',
 'unique native assignment/selection 및 request/invocation/PID/start/source를 결합한다. legacy ASGI header만으로 invocation을 확정하지 않는다. 다른 recording/동일 nativeID·중복은 candidate로 남긴다.',
 '기존 /11 output collector/객체/입력·저장·반환 SHA 분석을 본 점검의 caseID로 재사용한다. 생산자 인증·실제 client 수신·worker 신원·기업 상태 의미는 미확인이므로 모든판정축UNKNOWN과scopefalse다.',
 'health가 응답하면서 추론 대조가 실패/불명인 경우는 confirmed_violation=false 후보다. 독립worker 실패나 health 정책 위반으로 자동 승격하지 않는다.',
 '후속HTTP/세대/seal/census/첨부/parent cleanup 오류에도 앞 정제관측을 유지한다. profiler/backend종료와 승인·검사·소비사건은 별도다. 이전실제REQ11을 새소스실증으로 소급하지 않는다.',
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/OBS-02.yaml'].decode('utf-8'))
 check.update(revision=5,executor='observation_state',collection_profile=PROFILE,
  policy_params=[{'key':k,'type':v,'required':True} for k,v in POLICY_TYPES.items()],
  required_observations=[{'key':k,'type':v} for k,v in OBSERVATIONS.items()],
  judge={'plugin':'rule_table','params':{'pass_when_all':[],'fail_when_any':[]}},limitations=deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/OBS-02.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
 new=deepcopy(manifest);new.update(pack_id=PACK_ID,revision=1,review={'status':'DRAFT','reviewer':None,'reviewed_at':None},
  files_sha256=[{'path':n,'sha256':sha256_hex(v)} for n,v in sorted(changed.items())])
 result=build_pack(new,changed)
 if list(result.checks)!=list(original.checks):raise ValueError('PACK_CHECK_SCOPE_CHANGED')
 output.mkdir(parents=True)
 for name,data in changed.items():
  path=output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
 (output/'pack.yaml').write_bytes(yaml.safe_dump(new,allow_unicode=True,sort_keys=False).encode('utf-8'))
 if load_pack(output).ref()!=result.ref():raise ValueError('GENERATED_PACK_INTEGRITY_FAILED')
 return {'seed_ref':SEED_REF,'pack_ref':result.ref().model_dump(mode='json'),'checks':77,'unchanged_definition_bytes':76,
  'policy_values_created':False,'full_OBS02_complete':False,'required_subresults_complete':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
