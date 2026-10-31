"""PACK31 OBS02의 세 분기를 보존하는 상태 관측 제품 DRAFT 팩."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.executors.kubernetes_profiler_observation import PROFILE,POLICY_TYPES,OBSERVATIONS
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-PROFILER-STATE'
LIMITATIONS=['원팩31 OBS02 SC23-04/05/03의 원질문/정상/FAIL/UNKNOWN·backend 독립 관측과 다른76개 정의 bytes를 보존하는 DRAFT다. 이 단계는 SC23-05의 selected vLLM profiler adapter/flag 관측 제품 연결이며 전체 분기 완료가 아니다.', 'native15는 immutable native8을 기반으로 고정0.31의 Worker.profile,WorkerProfiler start/stop/call/step/shutdown,TorchProfilerWrapper adapter,실제 등록 API callback의 순서/반환/예외/전후 제한 flag와 call/parent/object/source/code/PID-start-session만 관측한다. native8~14 bytes/source/image/lab/native0.32 미지원은 유지한다.', 'POL8은 기존 exact ENV/target/aware<=600초/required components5와 native15 prepared SHA/torch backend/명시required ranks3을 함께 요구한다. 관측시 읽은 값을 승인값으로 삼거나 정책을 생성하지 않는다.', '기존 닫힌 GET health 전후/1token 정상 전후/8token SSE 첫byte 소유client 종료만 재사용한다. 이 실행기는 start_profile/stop_profile·backend·Pod/worker/Secret/설정/등록/장애 주입을 수행하지 않는다. 실제 service 시험은 B의 backend/접근/정상/실패/산출·다음시작 요구 확정이 별도다.', 'WorkerProfiler는 _stop 예외 뒤에도 running=false를 설정한다. Torch adapter는 실제 stop 뒤 출력 처리도 수행한다. 같은 source-bound call/parent/wrapper의 adapter RAISE와 flagfalse는 후보이며 backend stop 실패나 실제 수집 지속을 각각 확정하지 않는다.', 'wrapper/source 반환·내부 flag·API200·경고 문구는 독립 backend 활동/trace callback/산출·전송 종료/다음 정상시작의 증명이 아니다. 다른 PID/hash/가까운 시각으로 API-RPC-worker를 결합하지 않는다. 모든 판정축UNKNOWN/normal succeeded=null과 scopefalse를 유지한다.', 'prefix/path/trace/요청·응답 본문/환경·임의 예외 원문은 관측하지 않는다. 실제 registered endpoint/dependant source/code를 검사하며 callback 결과·예외·순서·정리를 유지한다. profiler가 있는 serving hook을 별도 Pod exec Python으로 대신하지 않는다.', 'source·process·recording/세대·객체·등록·parent·기한·필수rank의 중복/누락/교체는 미확인이다. 후속 수집/seal/첨부/정리 오류가 기존 정제 관측과 독립 FAIL을 지우지 않게 기존 CLI/정확Ref를 사용한다.', 'CPU exact native-body/EXAMPLE는 실제 Torch/backend/GPU가 아니다. actual source/image/producer/principal/POL/backend·정상시작/수집/정지·실패/산출·다음시작/복구/정리 책임은 별도 실증이다. 이전 실측·wheel을 새 source 검증으로 소급하지 않는다.', '부모A39/56/4·A1 19/27/2·c9·A2/B원본·과거팩/Ref·원GPU창종료/Start0/마지막확인stopped·46.66USD/보존120GiB EBS과금·CI결제차단을 유지한다. 새 기동/기간/비용/권한 승인이 아니다.']


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/OBS-02.yaml'].decode('utf-8'))
 check.update(revision=6,executor='profiler_observation',collection_profile=PROFILE,
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
