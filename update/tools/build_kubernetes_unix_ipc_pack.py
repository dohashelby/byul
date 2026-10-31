"""PACK31 PLT02의 원기준과 전용 A2 소비를 유지하는 정식 Kube 등록 DRAFT."""
from copy import deepcopy
from pathlib import Path
import argparse,json,yaml
from guard.models import load_pack,sha256_hex
from guard.models.checkdef import build_pack
from guard.runtime.kubernetes_ipc_contract import PROFILE
from tools.build_kubernetes_api_pack import SEED_REF

PACK_ID='PACK-KUBERNETES-UNIX-IPC'
LIMITATIONS=[
 'A2 fac0907의 고정 전용 소비를 A1 common ab84 IPC snapshot과 기존 CLI/registry/판정/증거에 연결하는 DRAFT다. 원PLT02 기준/두POL/정상표본/독립FAIL 및 다른76개 정의 bytes를 보존한다.',
 '명시 protected_unix_paths/allowed_ipc_uids와 별도 actor UID/GID/groups만 사용한다. 실제 supplementary groups를 보존하며 기존 parser/group/source gate를 완화하거나 관측에서 승인값을 생성하지 않는다.',
 '기존 KubernetesHTTP.ipc_snapshot과 정상 POST /v1/completions/ping/max_tokens1만 쓴다. 새 transport·selector·sensor·우회CLI나 CPU registry가 없다. payload/명령 소비·SHM 접근을 수행하지 않는다.',
 'actor와 collector의 서로 다른 PID·namespace/boot/time/UID/GID/groups/capabilities, 전후 socket inode/owner PID-start/UID와 peer/anchor를 충족한 경로별 결과만 소비한다. source/세대가 바뀌면 미확인이다.',
 '뒤 경로/normal/report/첨부/cleanup 실패 뒤 앞선 독립FAIL을 보존한다. 정상/전체IPC PASS는 없고 ipc_scope_complete=false·worker신뢰 미확인·기능/성능UNKNOWN을 유지한다.',
 'lab=true/vLLM0.31/CPython3.12 EXAMPLE 지원만 제공한다. 실제worker/message·동일UID 비허용process·abstract Unix/SHM/TCP/TP·전체IPC inventory·기업운영·native0.32는 미지원이다.',
 'A2 네 파일 bytes·원본probe·고정common actor/contract/program source pins·B c9 원본/과거팩/Ref를 보존하고 A1 검토 전체소비 pin만 갱신한다.',
 '부모A39/56/4·A1 19/27/2·A2 20/29/2를 유지하며 이 정식등록/CPU/설치를 실제환경·전체분기/Goal 완료로 올리지 않는다. 원GPU창종료/Start0·기존비용/EBS·CI결제 차단과 권한 경계를 유지한다.'
]


def derive(seed,output):
 seed=Path(seed).resolve();output=Path(output).resolve()
 if output.exists() or output.is_relative_to(seed):raise ValueError('NEW_EXTERNAL_PACK_DIRECTORY_REQUIRED')
 raw={p.relative_to(seed).as_posix():p.read_bytes() for p in seed.rglob('*') if p.is_file()}
 manifest=yaml.safe_load(raw.pop('pack.yaml').decode('utf-8'));original=build_pack(manifest,raw)
 if original.ref().model_dump(mode='json')!=SEED_REF or len(original.checks)!=77:raise ValueError('REVIEWED_PACK31_REQUIRED')
 check=yaml.safe_load(raw['checks/PLT-02.yaml'].decode('utf-8'))
 check.update(revision=6,collection_profile=PROFILE,limitations=deepcopy(check['limitations'])+deepcopy(LIMITATIONS))
 changed=dict(raw);changed['checks/PLT-02.yaml']=yaml.safe_dump(check,allow_unicode=True,sort_keys=False).encode('utf-8')
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
  'policy_values_created':False,'full_PLT02_complete':False}


def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--seed',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
 a=p.parse_args();print(json.dumps(derive(a.seed,a.output),ensure_ascii=False))
if __name__=='__main__':main()
