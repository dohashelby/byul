"""PACK31 PLT07의 원기준과 전용 A2 소비를 유지하는 정식 Kube 등록 DRAFT."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.runtime.kubernetes_egress_contract import PROFILE
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-BOUNDARY-EGRESS'
LIMITATIONS=['A2 확정9482a75의3원본 bytes와공통2596337 egress v1을 기존 boundary_egress 논리실행기/CLI/rule_table/EVD-RES-RPT에 연결한다. 다른76개 정의/원PACK31/Docker POL11·판정은 유지한다.', '명시13POL(기존11의이름+source_target_sha256/receiver_target_sha256), 전체 containerd 신원·UID/netns·소유 수신IPv4/port/tcp_connect/유효창과 별도 actor UID/GID/groups/시간만 쓴다. 관측에서금지정책/허용값을만들지않는다.', '이미기동한 같은context/run 소유receiver·정확원본source명령/PID-start/FD-listener/수신파일전후와source/probe/receiver API세대만소비한다. 새Pod/server기동·UID전환·worker/SDK재현은없다.', '전후collector/actor UID-GID-groups/PID-start/namespace/boot/time·kernel양끝·32byte상관/수신/시간의필수근거가확인된명시금지TCP한사건만 원래독립securityFAIL이다. ACK/후속clock/pipe/report/cleanup/첨부오류로확인된FAIL을지우지않는다.', '거부/timeout/누락/필수근거교체는UNKNOWN이며전체차단PASS가아니다. normal attempted=false/succeeded=null, functional/performanceUNKNOWN·egress_scope_complete=false를유지한다.', 'labtrue/vLLM0.31/CPython3.12 EXAMPLE제품검증이다. 실제worker/SDK/취득/cache/usage/telemetry/DNS/redirect/TLS/proxy/전체5분기·기업인가/native032는미완료다.', '원본sender/receiver/A2전용소비/source gates와 B원본c9pin을보존한다. A1소유검토전체snapshot만고정하며실행시hash자동승인·경로제외·groups변조·gate완화는없다.', '부모A39/56/4·A1 19/27/2·A2 20/29/2와c9/sources/worktree/팩/Ref/실증·GPU창종료/Start0/기존46.66USD/EBS/CI결제외부차단을보존한다.']


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/PLT-07.yaml'].decode('utf-8'))
 check.update(revision=6,collection_profile=PROFILE,policy_params=deepcopy(check['policy_params'])+[{'key':k,'type':'str','required':True} for k in ('source_target_sha256','receiver_target_sha256')],limitations=deepcopy(check['limitations'])+deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/PLT-07.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
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
  'policy_values_created':False,'full_PLT07_complete':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
