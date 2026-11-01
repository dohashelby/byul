"""기존 OBS02 세 분기를 보존하는 선언 기록/native 결합 후보 DRAFT 팩."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_observation_identity import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-OBSERVATION-IDENTITY'
LIMITATIONS=[
 'OBS02 SC23-03의 기존 OBS01 /1 선언 기록과 native8 모델/프로세스 결합 후보만 제공하는 DRAFT다. 원3분기/정상/FAIL/UNKNOWN 및 다른76개 정의 bytes를 보존한다.',
 '실제 승인·검사 생산자와 정책은 미확정이다. 생산자 신뢰·허용 모델 범위·검사·최종 tensor 소비·전체범위는 false, 모든 판정축UNKNOWN/normal succeeded=null을 유지한다.',
 'POL8은 기존 상태 관측5값과 명시 prepared SHA/record session/expected source claim3값이다. 이 source claim 일치는 생산자 인증이나 승인값 채택이 아니다. 기업 허용표/정책/기한은 생성하지 않는다.',
 'OBS01 /1의 closed schema/enum/64사건/65536bytes와 기존 소유 고정 읽기·Pod/context/image/restart/start 전후 검증을 그대로 소비한다. 새 inspection enum·임의 raw field를 추가하지 않는다.',
 '기존 OBS02 닫힌 health/정상/client-close 전송과 per-check 요청 ID만 재사용하며 승인·검사·배포·모델 적재·Secret·권한·Pod 변경 요청을 보내지 않는다.',
 'native8 source/code/session/physical process/clock/weak-object와 loader return/model-forward를 대조하되 다른 PID/hash/가까운 시각으로 RPC나 실제 소비를 증명하지 않는다. source 코드 기록과 생산자 인증은 별도다.',
 '기록의 target/run/source와 approval fingerprint·policy 주장 일치 여부만 후보로 남긴다. OBS01 /1에 approved model inventory가 없으며 ENV의 inventory를 실제 소비 inventory로 승격하지 않는다.',
 '앞선 정제 기록/후보는 후속 수집/seal/세대교체/첨부·정리 실패 뒤에도 보존한다. 데이터 부족과 선언 불일치는 미확인이고 기존 다른 점검의 독립FAIL을 지우지 않는다.',
 'lab=true/vLLM0.31/native8만 지원한다. native0.32·실제 producer/principal·A2 tensor/rank 소비·B 전용 구현은 이번 지원이 아니다. 기존 native8..15/A2/B 원본·외부 contracts·core/models/store/judges를 보존한다.',
 '부모A39/56/4·A1 19/27/2·c9·과거팩/Ref/실증 및 GPU창종료/Start0·기존비용/EBS·CI결제차단을 유지한다. 이 제품 연결을 원 분기·전체 Goal 완료로 집계하지 않는다.'
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/OBS-02.yaml'].decode('utf-8'))
 check.update(revision=7,executor='observation_identity',collection_profile=PROFILE,
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
